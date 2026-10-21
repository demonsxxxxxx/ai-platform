"""Redis cache and single-flight claim for settled follow-up suggestions."""

from __future__ import annotations

import json
from typing import Any, Sequence

from redis.exceptions import RedisError


class RedisFollowUpSuggestionCache:
    """Keep one bounded, owner-scoped answer and one in-flight claim per Run."""

    def __init__(self, redis_provider: Any) -> None:
        self._redis_provider = redis_provider

    async def _client(self) -> Any:
        client = self._redis_provider()
        if hasattr(client, "__await__"):
            client = await client
        return client

    async def read(self, key: str) -> list[str] | None:
        try:
            client = await self._client()
            raw = await client.get(key)
        except RedisError:
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, list) or not all(
            isinstance(item, str) for item in payload
        ):
            return None
        return payload

    async def claim(self, key: str, *, ttl_seconds: int) -> bool:
        try:
            client = await self._client()
            return bool(await client.set(f"{key}:claim", "1", nx=True, ex=ttl_seconds))
        except RedisError:
            # An unavailable cache must not remove the bounded model budget.
            return False

    async def release(self, key: str) -> None:
        try:
            client = await self._client()
            await client.delete(f"{key}:claim")
        except RedisError:
            return None

    async def store(
        self, key: str, suggestions: Sequence[str], *, ttl_seconds: int
    ) -> None:
        try:
            client = await self._client()
            await client.set(
                key,
                json.dumps(list(suggestions), ensure_ascii=False),
                ex=ttl_seconds,
            )
        except RedisError:
            return None
        finally:
            await self.release(key)
