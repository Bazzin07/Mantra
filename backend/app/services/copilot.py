from __future__ import annotations

import hashlib
import json
import re
from typing import AsyncIterator, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import END, StateGraph

from ..config import Settings
from ..models import Citation, CopilotResponse, DocumentChunk, DocumentResponse, SemanticCacheEntry
from ..repository import Repository
from .embeddings import EmbeddingProvider
from .entities import IndustrialEntityExtractor
from .exact_cache import ExactCacheProvider, NoOpExactCache
from .model_providers import (
    AllowAllSafetyProvider,
    DeterministicLLMProvider,
    LLMProvider,
    NoOpRerankerProvider,
    RerankerProvider,
    SafetyProvider,
)
from .scoring import clamp01, confidence_from_score


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9-]+")

# Entity types that name a specific thing the question is about. If a question
# names one of these and no cited document contains it, there is no real evidence.
SPECIFIC_IDENTIFIER_TYPES = {"EQUIPMENT_TAG", "PROCEDURE_ID", "PART_NUMBER"}


class CopilotState(TypedDict, total=False):
    question: str
    limit: int
    normalized_query: str
    query_tokens: List[str]
    query_embedding: List[float]
    query_type: str
    corpus_version: int
    model_name: str
    scored_chunks: List[Tuple[float, DocumentResponse, DocumentChunk]]
    retrieval_confidence_score: float
    citations: List[Citation]
    response: CopilotResponse
    cache_key: str
    cache_status: str
    generation_status: str
    rerank_status: str


class CopilotService:
    def __init__(
        self,
        repository: Repository,
        embedding_provider: EmbeddingProvider,
        settings: Optional[Settings] = None,
        llm_provider: Optional[LLMProvider] = None,
        reranker_provider: Optional[RerankerProvider] = None,
        safety_provider: Optional[SafetyProvider] = None,
        exact_cache: Optional[ExactCacheProvider] = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.entity_extractor = IndustrialEntityExtractor()
        self.settings = settings or Settings()
        self.llm_provider = llm_provider or DeterministicLLMProvider()
        self.reranker_provider = reranker_provider or NoOpRerankerProvider()
        self.safety_provider = safety_provider or AllowAllSafetyProvider()
        self.exact_cache = exact_cache or NoOpExactCache()
        self.graph = self._build_graph()

    async def answer(self, question: str, limit: int = 5) -> CopilotResponse:
        state = await self.graph.ainvoke({"question": question, "limit": limit})
        return state["response"]

    async def stream(self, question: str, limit: int = 5) -> AsyncIterator[Dict]:
        """FR-16, SSE-friendly: calls the same prepare/check_cache/retrieve/
        rerank node functions the non-streaming graph uses directly (not via
        the compiled graph), so retrieval logic isn't duplicated — only
        generation diverges (incremental vs. one blocking call). Yields
        {"type": "token", "text": ...} as text arrives, then one final
        {"type": "done", ...} frame with citations/confidence.

        Known limitation, by necessity: the non-streaming path can run a
        safety check on the *complete* answer and swap in a safe fallback
        before the caller ever sees it. A token stream can't retroactively
        un-send tokens already delivered to the client, so this path skips
        the post-hoc safety gate — the same fundamental constraint every
        token-streaming LLM integration has. Documented, not silently
        dropped: see ENGINEERING_AUDIT.md.
        """
        state: CopilotState = {"question": question, "limit": limit}
        state = await self._prepare(state)
        state = await self._check_cache(state)
        if "response" in state:
            response = state["response"]
            if response.answer:
                yield {"type": "token", "text": response.answer}
            yield {
                "type": "done",
                "citations": [c.model_dump() for c in response.citations],
                "confidence": response.confidence,
                "model_used": response.model_used,
                "generation_status": response.generation_status,
                "cache_status": response.cache_status,
            }
            return

        state = await self._retrieve(state)
        state = await self._rerank(state)

        scored_chunks = state.get("scored_chunks", [])
        confidence = confidence_from_score(state.get("retrieval_confidence_score", 0.0))
        if not scored_chunks or confidence == "none":
            yield {"type": "token", "text": "I could not find indexed evidence for that question yet."}
            yield {
                "type": "done", "citations": [], "confidence": "none", "model_used": None,
                "generation_status": "skipped_no_evidence", "cache_status": state["cache_status"],
            }
            return

        citations = build_citations(scored_chunks)
        model_name = select_model(state["query_type"], self.settings, confidence)

        pieces: List[str] = []
        try:
            async for piece in self.llm_provider.generate_answer_stream(question, citations, model_name, state["query_type"]):
                pieces.append(piece)
                yield {"type": "token", "text": piece}
            if not pieces:
                # Reasoning models (e.g. deepseek-v4-flash) stream internal
                # chain-of-thought via a separate `reasoning_content` field
                # before `content` — observed live, intermittently, on the
                # shared endpoint: a response can exhaust its budget on
                # reasoning and complete with zero real content. That's not
                # an exception, so without this check it would silently
                # report generation_status="llm" with a blank answer. Route
                # it into the same fallback as a real provider failure.
                raise RuntimeError("stream completed with no content")
            generation_status = "llm"
        except Exception:
            fallback = build_grounded_answer(question, citations, state["query_type"])
            pieces = [fallback]
            yield {"type": "token", "text": fallback}
            generation_status = "fallback"

        response = CopilotResponse(
            answer="".join(pieces),
            confidence=confidence,
            citations=citations,
            retrieved_chunk_count=len(citations),
            model_used=model_name,
            cache_status=state["cache_status"],
            query_type=state["query_type"],
            generation_status=generation_status,
            rerank_status=state.get("rerank_status", "not_used"),
        )
        await self._write_cache({**state, "response": response, "model_name": model_name})

        yield {
            "type": "done",
            "citations": [c.model_dump() for c in citations],
            "confidence": confidence,
            "model_used": model_name,
            "generation_status": generation_status,
            "cache_status": state["cache_status"],
        }

    def _build_graph(self):
        graph = StateGraph(CopilotState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("check_cache", self._check_cache)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("rerank", self._rerank)
        graph.add_node("generate", self._generate)
        graph.add_node("write_cache", self._write_cache)
        graph.set_entry_point("prepare")
        graph.add_edge("prepare", "check_cache")
        graph.add_conditional_edges(
            "check_cache",
            lambda state: "cached" if "response" in state else "miss",
            {"cached": END, "miss": "retrieve"},
        )
        graph.add_edge("retrieve", "rerank")
        graph.add_edge("rerank", "generate")
        graph.add_edge("generate", "write_cache")
        graph.add_edge("write_cache", END)
        return graph.compile()

    async def _prepare(self, state: CopilotState) -> CopilotState:
        question = state["question"]
        normalized_query = normalize_query(question)
        query_tokens = tokenize(question)
        query_embedding = self.embedding_provider.embed(question)
        query_type = classify_query(question)
        corpus_version = await self.repository.get_corpus_version()
        model_name = select_model(query_type, self.settings, confidence="unknown")
        cache_key = exact_cache_key(normalized_query, model_name, self.settings.prompt_version, corpus_version)
        return {
            **state,
            "normalized_query": normalized_query,
            "query_tokens": query_tokens,
            "query_embedding": query_embedding,
            "query_type": query_type,
            "corpus_version": corpus_version,
            "model_name": model_name,
            "cache_key": cache_key,
            "cache_status": "miss",
            "generation_status": "not_started",
            "rerank_status": "not_used",
        }

    async def _check_cache(self, state: CopilotState) -> CopilotState:
        cached = await self.exact_cache.get(state["cache_key"])
        if cached:
            return {**state, "response": cached.model_copy(update={"cache_status": "exact_hit"})}

        exact = await self.repository.get_exact_semantic_cache(
            state["normalized_query"],
            state["model_name"],
            self.settings.prompt_version,
            state["corpus_version"],
        )
        if exact:
            return {**state, "response": response_from_cache_entry(exact, "postgres_exact_hit")}

        semantic = await self.repository.find_semantic_cache(
            state["query_embedding"],
            state["model_name"],
            self.settings.prompt_version,
            state["corpus_version"],
            self.settings.semantic_cache_threshold,
        )
        if semantic:
            return {**state, "response": response_from_cache_entry(semantic, "semantic_hit")}
        return state

    async def _retrieve(self, state: CopilotState) -> CopilotState:
        query_tokens = state["query_tokens"]
        if not query_tokens:
            return {**state, "scored_chunks": []}

        query_entities = self.entity_extractor.extract_from_text(state["question"], document_id="query", chunk_id="query")
        query_entity_keys = {(entity.entity_type, entity.normalized_text) for entity in query_entities}
        scored_chunks = await self.repository.search_chunks(
            state["question"],
            query_tokens,
            state["query_embedding"],
            query_entity_keys,
            limit=100,
        )
        if state["query_type"] == "relational_query" and query_entities:
            scored_chunks = boost_relational_matches(scored_chunks, query_entity_keys)
        if state["query_type"] == "sequential_query":
            scored_chunks = boost_sequential_matches(scored_chunks)
        # Grounding gate: if the question names a specific identifier (equipment
        # tag, procedure, or part) and none of the top cited chunks actually
        # contain it, treat it as no-evidence. Prevents answering an asset-specific
        # question from documents about other assets on generic word overlap
        # (e.g. "maintenance due on Reactor RX-900" matching an unrelated manual).
        scored_chunks = await self._enforce_specific_entity_grounding(
            scored_chunks, query_entity_keys, state["limit"]
        )
        # Confidence is anchored to the strongest unified retrieval score, captured
        # before reranking so it stays on the shared [0, 1] scale regardless of
        # whether an external reranker (which uses its own score scale) runs next.
        retrieval_confidence_score = clamp01(scored_chunks[0][0]) if scored_chunks else 0.0
        return {**state, "scored_chunks": scored_chunks, "retrieval_confidence_score": retrieval_confidence_score}

    async def _enforce_specific_entity_grounding(
        self,
        scored_chunks: List[Tuple[float, DocumentResponse, DocumentChunk]],
        query_entity_keys,
        limit: int,
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
        specific_ids = {key for key in query_entity_keys if key[0] in SPECIFIC_IDENTIFIER_TYPES}
        if not specific_ids or not scored_chunks:
            return scored_chunks
        for _, _, chunk in scored_chunks[:limit]:
            chunk_entities = await self.repository.entities_for_chunk(chunk.id)
            if specific_ids & {(entity.entity_type, entity.normalized_text) for entity in chunk_entities}:
                return scored_chunks
        return []

    async def _rerank(self, state: CopilotState) -> CopilotState:
        scored_chunks = state.get("scored_chunks", [])
        if not scored_chunks:
            return {**state, "rerank_status": "skipped_no_candidates"}
        try:
            reranked = await self.reranker_provider.rerank(state["question"], scored_chunks[:25])
            rerank_status = "reranked"
        except Exception:
            reranked = scored_chunks
            rerank_status = "fallback"
        return {**state, "scored_chunks": reranked[: state["limit"]], "rerank_status": rerank_status}

    async def _generate(self, state: CopilotState) -> CopilotState:
        scored_chunks = state.get("scored_chunks", [])
        confidence = confidence_from_score(state.get("retrieval_confidence_score", 0.0))
        # "none" confidence means only weak/noise matches were retrieved; do not
        # present them as citations or spend an LLM call on them. This keeps the
        # no-evidence contract identical whether the corpus is empty or the query
        # simply has no real match, and avoids citations that contradict a
        # "none" confidence label.
        if not scored_chunks or confidence == "none":
            response = CopilotResponse(
                answer="I could not find indexed evidence for that question yet.",
                confidence="none",
                citations=[],
                retrieved_chunk_count=0,
                model_used=None,
                cache_status=state["cache_status"],
                query_type=state["query_type"],
                generation_status="skipped_no_evidence",
                rerank_status=state.get("rerank_status", "not_used"),
            )
            return {**state, "response": response}

        citations = build_citations(scored_chunks)
        model_name = select_model(state["query_type"], self.settings, confidence)
        try:
            answer = await self.llm_provider.generate_answer(state["question"], citations, model_name, state["query_type"])
            generation_status = "llm"
            if not await self.safety_provider.is_allowed(answer):
                answer = build_grounded_answer(state["question"], citations, state["query_type"])
                generation_status = "safety_fallback"
        except Exception:
            answer = build_grounded_answer(state["question"], citations, state["query_type"])
            generation_status = "fallback"

        response = CopilotResponse(
            answer=answer,
            confidence=confidence,
            citations=citations,
            retrieved_chunk_count=len(citations),
            model_used=model_name,
            cache_status=state["cache_status"],
            query_type=state["query_type"],
            generation_status=generation_status,
            rerank_status=state.get("rerank_status", "not_used"),
        )
        return {
            **state,
            "response": response,
            "citations": citations,
            "model_name": model_name,
            "generation_status": generation_status,
        }

    async def _write_cache(self, state: CopilotState) -> CopilotState:
        response = state["response"]
        # Only cache real LLM answers. Degraded fallbacks (NVIDIA 5xx, safety
        # block) still carry citations but must not stick in the cache and be
        # re-served as if they were the model's answer.
        if not response.citations or response.generation_status != "llm":
            return state
        await self.exact_cache.set(state["cache_key"], response)
        entry = SemanticCacheEntry(
            normalized_query=state["normalized_query"],
            query_embedding=state["query_embedding"],
            model_name=state["model_name"],
            prompt_version=self.settings.prompt_version,
            corpus_version=state["corpus_version"],
            citation_hash=citation_hash(response.citations),
            answer=response.answer,
            confidence=response.confidence,
            citations=response.citations,
            retrieved_chunk_count=response.retrieved_chunk_count,
            query_type=response.query_type,
            cache_metadata={
                "cache_status": "stored",
                "generation_status": response.generation_status,
                "rerank_status": response.rerank_status,
            },
        )
        await self.repository.upsert_semantic_cache(entry)
        return state


def tokenize(text: str) -> List[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def normalize_query(text: str) -> str:
    return " ".join(tokenize(text))


def classify_query(question: str) -> str:
    lowered = question.lower()
    if any(term in lowered for term in ["step", "sequence", "before", "after", "timeline", "then"]):
        return "sequential_query"
    if any(term in lowered for term in ["related", "relationship", "connected", "between", "depends on", "linked"]):
        return "relational_query"
    if any(term in lowered for term in ["all documents", "entire manual", "whole manual", "across every"]):
        return "long_context_query"
    return "simple_evidence_query"


def build_citations(scored_chunks: List[Tuple[float, DocumentResponse, DocumentChunk]]) -> List[Citation]:
    return [
        Citation(
            document_id=document.id,
            filename=document.metadata.filename,
            chunk_id=chunk.id,
            page_number=chunk.page_number,
            relevance_score=round(score, 4),
            excerpt=chunk.content[:320],
        )
        for score, document, chunk in scored_chunks
    ]


def select_model(query_type: str, settings: Settings, confidence: str) -> str:
    if query_type == "long_context_query":
        return settings.long_context_model
    if confidence == "weak" or query_type == "sequential_query":
        return settings.escalation_model
    return settings.default_answer_model


def build_grounded_answer(question: str, citations: List[Citation], query_type: str = "simple_evidence_query") -> str:
    top = citations[0]
    return (
        "Based on the indexed document evidence, the strongest matching source is "
        f"`{top.filename}` on page {top.page_number}. "
        "Use the citations below to inspect the supporting chunks before making an operational decision. "
        f"Query type: {query_type}. Question: {question}"
    )


def boost_relational_matches(
    scored_chunks: List[Tuple[float, DocumentResponse, DocumentChunk]],
    query_entity_keys,
) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
    boosted = []
    for score, document, chunk in scored_chunks:
        document_entity_keys = {(entity.entity_type, entity.normalized_text) for entity in document.entities}
        boosted.append((score + 0.25 * len(query_entity_keys.intersection(document_entity_keys)), document, chunk))
    return sorted(boosted, key=lambda item: item[0], reverse=True)


def boost_sequential_matches(
    scored_chunks: List[Tuple[float, DocumentResponse, DocumentChunk]]
) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
    sequential_terms = {"step", "then", "before", "after", "sequence", "procedure", "timeline"}
    boosted = []
    for score, document, chunk in scored_chunks:
        bonus = 0.15 if any(term in chunk.content.lower() for term in sequential_terms) else 0.0
        boosted.append((score + bonus, document, chunk))
    return sorted(boosted, key=lambda item: item[0], reverse=True)


def citation_hash(citations: List[Citation]) -> str:
    payload = json.dumps(
        [
            {
                "document_id": citation.document_id,
                "chunk_id": citation.chunk_id,
                "page_number": citation.page_number,
            }
            for citation in citations
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def exact_cache_key(normalized_query: str, model_name: str, prompt_version: str, corpus_version: int) -> str:
    raw = f"{normalized_query}|{model_name}|{prompt_version}|{corpus_version}"
    return "copilot:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def response_from_cache_entry(entry: SemanticCacheEntry, cache_status: str) -> CopilotResponse:
    return CopilotResponse(
        answer=entry.answer,
        confidence=entry.confidence,
        citations=entry.citations,
        retrieved_chunk_count=entry.retrieved_chunk_count,
        model_used=entry.model_name,
        cache_status=cache_status,
        query_type=entry.query_type,
        generation_status=entry.cache_metadata.get("generation_status", "cached"),
        rerank_status=entry.cache_metadata.get("rerank_status", "cached"),
    )
