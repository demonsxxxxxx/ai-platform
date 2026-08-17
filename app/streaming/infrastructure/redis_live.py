"""Redis Pub/Sub adapter for the SSE v3 process-wide live plane."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from app.streaming.domain.live import LivePublication

LIVE_REDIS_MAX_CONNECTIONS = 2
LIVE_SUBSCRIBE_TIMEOUT_SECONDS = 2.0
LIVE_MESSAGE_MAX_BYTES = 262_656
_REDIS_ID_RE = re.compile(r"^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$")


class RedisLiveFanoutSource:
    def __init__(
        self, *, redis_url: str | None = None, client: Any | None = None
    ) -> None:
        if client is None and redis_url is None:
            raise ValueError("live_source_redis_url_required")
        self._client = client if client is not None else Redis.from_url(
            redis_url,
            decode_responses=True,
            max_connections=LIVE_REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=2,
            socket_timeout=None,
        )
        self._owns_client = client is None
        self._pubsub: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._on_publication: Callable[[LivePublication], Awaitable[None]] | None = None
        self._on_failure: Callable[[str], Awaitable[None]] | None = None
        self._pending: dict[tuple[str, str], asyncio.Future[None]] = {}
        self._channels: set[str] = set()
        self._command_lock = asyncio.Lock()
        self._closing = False

    async def start(
        self,
        *,
        on_publication: Callable[[LivePublication], Awaitable[None]],
        on_failure: Callable[[str], Awaitable[None]],
    ) -> None:
        if self._closing:
            raise RuntimeError("live_source_closed")
        self._on_publication = on_publication
        self._on_failure = on_failure
        if self._reader_task is not None and not self._reader_task.done():
            return
        if self._pubsub is not None:
            await self._pubsub.aclose()
        self._pubsub = self._client.pubsub(ignore_subscribe_messages=False)
        self._channels.clear()
        self._pending.clear()

    async def subscribe(self, channel: str) -> None:
        async with self._command_lock:
            if channel in self._channels:
                return
            pubsub = self._require_pubsub()
            future = asyncio.get_running_loop().create_future()
            self._pending[("subscribe", channel)] = future
            try:
                await pubsub.subscribe(channel)
                if self._reader_task is None or self._reader_task.done():
                    self._reader_task = asyncio.create_task(self._read_messages())
                await asyncio.wait_for(
                    asyncio.shield(future), timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS
                )
            except BaseException:
                self._pending.pop(("subscribe", channel), None)
                if not future.done():
                    future.cancel()
                with contextlib.suppress(Exception):
                    await pubsub.unsubscribe(channel)
                raise
            self._channels.add(channel)

    async def unsubscribe(self, channel: str) -> None:
        async with self._command_lock:
            if channel not in self._channels or self._pubsub is None:
                return
            future = asyncio.get_running_loop().create_future()
            self._pending[("unsubscribe", channel)] = future
            try:
                await self._pubsub.unsubscribe(channel)
                await asyncio.wait_for(
                    asyncio.shield(future), timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS
                )
            finally:
                self._pending.pop(("unsubscribe", channel), None)
                self._channels.discard(channel)

    async def aclose(self) -> None:
        self._closing = True
        task = self._reader_task
        self._reader_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._owns_client:
            await self._client.aclose()

    async def _read_messages(self) -> None:
        try:
            while not self._closing:
                message = await self._require_pubsub().get_message(
                    ignore_subscribe_messages=False,
                    timeout=1.0,
                )
                if message is None:
                    await asyncio.sleep(0.01)
                    continue
                message_type = self._text(message.get("type"))
                channel = self._text(message.get("channel"))
                if message_type in {"subscribe", "unsubscribe"}:
                    future = self._pending.pop((message_type, channel), None)
                    if future is not None and not future.done():
                        future.set_result(None)
                    continue
                if message_type != "message":
                    continue
                try:
                    publication = self._parse_publication(
                        channel=channel,
                        value=message.get("data"),
                    )
                except ValueError:
                    continue
                if self._on_publication is None:
                    raise RuntimeError("live_source_listener_missing")
                await self._on_publication(publication)
        except asyncio.CancelledError:
            raise
        except Exception:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("live_transport_unavailable"))
            self._pending.clear()
            self._channels.clear()
            if not self._closing and self._on_failure is not None:
                await self._on_failure("live_transport_unavailable")

    def _require_pubsub(self) -> Any:
        if self._pubsub is None:
            raise RuntimeError("live_source_not_started")
        return self._pubsub

    @staticmethod
    def _parse_publication(*, channel: str, value: object) -> LivePublication:
        text = RedisLiveFanoutSource._text(value)
        if not text or len(text.encode("utf-8")) > LIVE_MESSAGE_MAX_BYTES:
            raise ValueError("live_publication_size_invalid")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("live_publication_json_invalid") from exc
        if not isinstance(raw, dict) or set(raw) != {"redis_id", "envelope"}:
            raise ValueError("live_publication_shape_invalid")
        redis_id = raw.get("redis_id")
        envelope_json = raw.get("envelope")
        if (
            not isinstance(redis_id, str)
            or not _REDIS_ID_RE.fullmatch(redis_id)
            or not isinstance(envelope_json, str)
            or not envelope_json
        ):
            raise ValueError("live_publication_value_invalid")
        return LivePublication(channel, redis_id, envelope_json)

    @staticmethod
    def _text(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value if isinstance(value, str) else ""
