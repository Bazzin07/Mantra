from __future__ import annotations

import json
from typing import Optional, Protocol

from ..models import Citation, CopilotResponse


class ExactCacheProvider(Protocol):
    async def get(self, key: str) -> Optional[CopilotResponse]: ...

    async def set(self, key: str, value: CopilotResponse, ttl_seconds: int = 3600) -> None: ...


class NoOpExactCache:
    async def get(self, key: str) -> Optional[CopilotResponse]:
        return None

    async def set(self, key: str, value: CopilotResponse, ttl_seconds: int = 3600) -> None:
        return None


class RedisExactCache:
    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as redis

        self.client = redis.from_url(redis_url, decode_responses=True)

    async def get(self, key: str) -> Optional[CopilotResponse]:
        try:
            payload = await self.client.get(key)
        except Exception:
            return None
        if not payload:
            return None
        data = json.loads(payload)
        data["citations"] = [Citation(**citation) for citation in data.get("citations", [])]
        return CopilotResponse(**data)

    async def set(self, key: str, value: CopilotResponse, ttl_seconds: int = 3600) -> None:
        try:
            await self.client.set(key, value.model_dump_json(), ex=ttl_seconds)
        except Exception:
            return None


def create_exact_cache(redis_url: str) -> ExactCacheProvider:
    if redis_url:
        return RedisExactCache(redis_url)
    return NoOpExactCache()
