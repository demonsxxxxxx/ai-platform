"""Process-local, event-loop-bound Redis client lifecycle authority."""

import asyncio
from dataclasses import dataclass
from inspect import isawaitable
from threading import RLock
from typing import Any

from redis.asyncio import Redis

from app.settings import get_settings


class RedisClientUnavailable(RuntimeError):
    """The current loop cannot safely use its Redis client."""


class RedisClientCloseError(RuntimeError):
    """The current loop's Redis client did not close cleanly."""


@dataclass
class _RedisClientEntry:
    loop: asyncio.AbstractEventLoop
    client: Redis
    redis_url: str
    max_connections: int
    state: str = "active"


class RedisClientHandle:
    """Operation-scoped handle backed by the current loop's shared pool."""

    def __init__(self, entry: _RedisClientEntry) -> None:
        self._entry = entry
        self._released = False

    def _assert_available(self) -> Redis:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RedisClientUnavailable("redis_client_unavailable") from exc
        if self._released or loop is not self._entry.loop or self._entry.state != "active":
            raise RedisClientUnavailable("redis_client_unavailable")
        return self._entry.client

    async def aclose(self) -> None:
        self._released = True

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._assert_available(), name)
        if not callable(value):
            return value

        async def invoke(*args: Any, **kwargs: Any) -> Any:
            method = getattr(self._assert_available(), name)
            result = method(*args, **kwargs)
            return await result if isawaitable(result) else result

        return invoke


_clients: dict[asyncio.AbstractEventLoop, _RedisClientEntry] = {}
_clients_lock = RLock()


def get_redis_client() -> RedisClientHandle:
    """Return a handle to one bounded client shared only within the current loop."""

    loop = asyncio.get_running_loop()
    settings = get_settings()
    redis_url = str(settings.redis_url)
    max_connections = int(settings.redis_max_connections)
    with _clients_lock:
        entry = _clients.get(loop)
        if entry is not None:
            if (
                entry.state != "active"
                or entry.redis_url != redis_url
                or entry.max_connections != max_connections
            ):
                raise RedisClientUnavailable("redis_client_unavailable")
        else:
            client = Redis.from_url(
                redis_url,
                decode_responses=True,
                max_connections=max_connections,
            )
            entry = _RedisClientEntry(
                loop=loop,
                client=client,
                redis_url=redis_url,
                max_connections=max_connections,
            )
            _clients[loop] = entry
    return RedisClientHandle(entry)


async def close_redis_client() -> None:
    """Close and forget the current loop's shared client, allowing later rebuild."""

    loop = asyncio.get_running_loop()
    with _clients_lock:
        entry = _clients.get(loop)
        if entry is None:
            return
        if entry.state == "closing":
            raise RedisClientUnavailable("redis_client_unavailable")
        entry.state = "closing"
    try:
        await entry.client.aclose()
    except Exception as exc:
        with _clients_lock:
            if _clients.get(loop) is entry:
                entry.state = "close_failed"
        raise RedisClientCloseError("redis_client_close_failed") from exc
    with _clients_lock:
        if _clients.get(loop) is entry:
            del _clients[loop]
