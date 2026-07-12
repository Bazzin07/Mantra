from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, Tuple

import httpx

from ..config import Settings
from ..models import Citation, DocumentChunk, DocumentResponse


class LLMProvider(Protocol):
    async def generate_answer(
        self,
        question: str,
        citations: List[Citation],
        model_name: str,
        query_type: str,
    ) -> str: ...


class RerankerProvider(Protocol):
    async def rerank(
        self,
        question: str,
        candidates: List[Tuple[float, DocumentResponse, DocumentChunk]],
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]: ...


class SafetyProvider(Protocol):
    async def is_allowed(self, text: str) -> bool: ...


class DocumentParseProvider(Protocol):
    async def parse(self, filename: str, payload: bytes) -> str: ...


@dataclass
class NvidiaClient:
    settings: Settings

    async def post(self, path: str, payload: dict) -> dict:
        if not self.settings.nvidia_api_key:
            raise RuntimeError("NVIDIA API key is not configured")
        base_url = self.settings.nvidia_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url}/{path.lstrip('/')}",
                headers={
                    "Authorization": f"Bearer {self.settings.nvidia_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()


class DeterministicLLMProvider:
    async def generate_answer(
        self,
        question: str,
        citations: List[Citation],
        model_name: str,
        query_type: str,
    ) -> str:
        top = citations[0]
        return (
            "Based on the indexed document evidence, the strongest matching source is "
            f"`{top.filename}` on page {top.page_number}. "
            "Use the citations below to inspect the supporting chunks before making an operational decision. "
            f"Query type: {query_type}. Question: {question}"
        )


class NvidiaLLMProvider:
    def __init__(self, settings: Settings) -> None:
        self.client = NvidiaClient(settings)

    async def generate_answer(
        self,
        question: str,
        citations: List[Citation],
        model_name: str,
        query_type: str,
    ) -> str:
        evidence = "\n\n".join(
            f"[{index + 1}] {citation.filename} page {citation.page_number}: {citation.excerpt}"
            for index, citation in enumerate(citations)
        )
        payload = {
            "model": model_name,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an industrial knowledge copilot. Answer only from the provided evidence. "
                        "If evidence is insufficient, say what is missing. Do not invent RCA, compliance, or safety claims."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Query type: {query_type}\n"
                        f"Question: {question}\n\n"
                        f"Evidence:\n{evidence}\n\n"
                        "Write a concise answer and refer to evidence numbers where relevant."
                    ),
                },
            ],
        }
        data = await self.client.post("/chat/completions", payload)
        return data["choices"][0]["message"]["content"].strip()


class NoOpRerankerProvider:
    async def rerank(
        self,
        question: str,
        candidates: List[Tuple[float, DocumentResponse, DocumentChunk]],
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
        return candidates


class NvidiaRerankerProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = NvidiaClient(settings)

    async def rerank(
        self,
        question: str,
        candidates: List[Tuple[float, DocumentResponse, DocumentChunk]],
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]:
        if not candidates:
            return candidates
        passages = [chunk.content for _, _, chunk in candidates]
        data = await self.client.post(
            "/ranking",
            {
                "model": self.settings.rerank_model,
                "query": question,
                "passages": passages,
            },
        )
        rankings = data.get("rankings") or data.get("data") or []
        if not rankings:
            return candidates
        scored = []
        for item in rankings:
            index = int(item.get("index", item.get("passage_index", 0)))
            if index < 0 or index >= len(candidates):
                continue
            score = float(item.get("score", item.get("relevance_score", candidates[index][0])))
            _, document, chunk = candidates[index]
            scored.append((score, document, chunk))
        return scored or candidates


class AllowAllSafetyProvider:
    async def is_allowed(self, text: str) -> bool:
        return True


class NvidiaSafetyProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = NvidiaClient(settings)

    async def is_allowed(self, text: str) -> bool:
        data = await self.client.post(
            "/chat/completions",
            {
                "model": self.settings.safety_model,
                "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": "Return only SAFE or UNSAFE for this industrial assistant text."},
                    {"role": "user", "content": text},
                ],
            },
        )
        content = data["choices"][0]["message"]["content"].strip().upper()
        return "UNSAFE" not in content


class NvidiaDocumentParseProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = NvidiaClient(settings)

    async def parse(self, filename: str, payload: bytes) -> str:
        raise NotImplementedError("nemotron-parse integration is reserved for parser fallback wiring")


def create_model_providers(settings: Settings) -> tuple[LLMProvider, RerankerProvider, SafetyProvider]:
    if settings.llm_provider == "nvidia":
        return NvidiaLLMProvider(settings), NvidiaRerankerProvider(settings), NvidiaSafetyProvider(settings)
    return DeterministicLLMProvider(), NoOpRerankerProvider(), AllowAllSafetyProvider()
