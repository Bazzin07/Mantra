from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import AsyncIterator, List, Protocol, Tuple

import httpx


def _sigmoid(x: float) -> float:
    # Numerically stable; maps a rerank logit to (0, 1) without overflow.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)

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

    def generate_answer_stream(
        self,
        question: str,
        citations: List[Citation],
        model_name: str,
        query_type: str,
    ) -> AsyncIterator[str]: ...


class RerankerProvider(Protocol):
    async def rerank(
        self,
        question: str,
        candidates: List[Tuple[float, DocumentResponse, DocumentChunk]],
    ) -> List[Tuple[float, DocumentResponse, DocumentChunk]]: ...


class SafetyProvider(Protocol):
    async def is_allowed(self, text: str) -> bool: ...


@dataclass
class NvidiaClient:
    settings: Settings

    async def post(self, path: str, payload: dict) -> dict:
        if not self.settings.nvidia_api_key:
            raise RuntimeError("NVIDIA API key is not configured")
        base_url = self.settings.nvidia_base_url.rstrip("/")
        url = path if path.startswith("http") else f"{base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.settings.nvidia_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            # The shared integrate endpoint 503s / times out intermittently under
            # load; one backoff retry recovers most of them before the caller's
            # deterministic fallback kicks in.
            # ponytail: fixed 2 attempts + 0.5s backoff; use tenacity if a real
            # retry policy (jitter, budgets) is ever needed.
            for attempt in range(2):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    retryable = status is None or status >= 500
                    if attempt == 0 and retryable:
                        await asyncio.sleep(0.5)
                        continue
                    raise
            raise RuntimeError("unreachable")

    async def stream_post(self, path: str, payload: dict) -> AsyncIterator[str]:
        # ponytail: no retry here, unlike post() — a partially-consumed SSE
        # stream can't be safely retried without either duplicating already-
        # yielded tokens or discarding real partial output. A mid-stream
        # failure propagates and the caller (CopilotService.stream) falls
        # back to the deterministic template, same as the non-streaming path.
        if not self.settings.nvidia_api_key:
            raise RuntimeError("NVIDIA API key is not configured")
        base_url = self.settings.nvidia_base_url.rstrip("/")
        url = path if path.startswith("http") else f"{base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.settings.nvidia_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", url, headers=headers, json={**payload, "stream": True}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        yield text


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

    async def generate_answer_stream(
        self,
        question: str,
        citations: List[Citation],
        model_name: str,
        query_type: str,
    ) -> AsyncIterator[str]:
        # The deterministic template has the whole answer instantly — one
        # chunk is honest here, not a fabricated incremental delay.
        yield await self.generate_answer(question, citations, model_name, query_type)


def _build_chat_payload(question: str, citations: List[Citation], model_name: str, query_type: str) -> dict:
    evidence = "\n\n".join(
        f"[{index + 1}] {citation.filename} page {citation.page_number}: {citation.excerpt}"
        for index, citation in enumerate(citations)
    )
    return {
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
        payload = _build_chat_payload(question, citations, model_name, query_type)
        data = await self.client.post("/chat/completions", payload)
        return data["choices"][0]["message"]["content"].strip()

    async def generate_answer_stream(
        self,
        question: str,
        citations: List[Citation],
        model_name: str,
        query_type: str,
    ) -> AsyncIterator[str]:
        payload = _build_chat_payload(question, citations, model_name, query_type)
        async for piece in self.client.stream_post("/chat/completions", payload):
            yield piece


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
        data = await self.client.post(
            self.settings.rerank_url,
            {
                "model": self.settings.rerank_model,
                "query": {"text": question},
                "passages": [{"text": chunk.content} for _, _, chunk in candidates],
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
            raw = item.get("logit")
            # Normalize logits to (0,1) so citation scores stay on the same scale
            # as the other retrieval paths; other response shapes are already 0–1.
            score = _sigmoid(float(raw)) if raw is not None else float(
                item.get("score", item.get("relevance_score", candidates[index][0]))
            )
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


def create_model_providers(settings: Settings) -> tuple[LLMProvider, RerankerProvider, SafetyProvider]:
    if settings.llm_provider == "nvidia":
        return NvidiaLLMProvider(settings), NvidiaRerankerProvider(settings), NvidiaSafetyProvider(settings)
    return DeterministicLLMProvider(), NoOpRerankerProvider(), AllowAllSafetyProvider()
