"""Redis Pub/Sub adapter for the SSE v3 process-wide live plane."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.streaming.domain.live import LivePublication

LIVE_REDIS_MAX_CONNECTIONS = 2
LIVE_SUBSCRIBE_TIMEOUT_SECONDS = 2.0
LIVE_MESSAGE_MAX_BYTES = 262_656
_REDIS_ID_RE = re.compile(r"^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$")


@dataclass(slots=True)
class _ControlAcknowledgement:
    generation: int
    future: asyncio.Future[None]
    active: bool = True


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
        self._on_channel_failure: (
            Callable[[str, str], Awaitable[None]] | None
        ) = None
        self._on_failure: Callable[[str], Awaitable[None]] | None = None
        self._pending: dict[tuple[str, str], _ControlAcknowledgement] = {}
        self._acknowledgements: dict[
            tuple[str, str], deque[_ControlAcknowledgement]
        ] = {}
        self._control_generation = 0
        self._invalidation_tasks: dict[str, asyncio.Task[None]] = {}
        self._channels: set[str] = set()
        self._command_lock = asyncio.Lock()
        self._transport_failed = False
        self._closing = False

    async def start(
        self,
        *,
        on_publication: Callable[[LivePublication], Awaitable[None]],
        on_channel_failure: Callable[[str, str], Awaitable[None]],
        on_failure: Callable[[str], Awaitable[None]],
    ) -> None:
        if self._closing:
            raise RuntimeError("live_source_closed")
        self._on_publication = on_publication
        self._on_channel_failure = on_channel_failure
        self._on_failure = on_failure
        async with self._command_lock:
            if (
                self._reader_task is not None
                and not self._reader_task.done()
                and not self._transport_failed
            ):
                return
            task = self._reader_task
            self._reader_task = None
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            invalidations = tuple(self._invalidation_tasks.values())
            self._invalidation_tasks.clear()
            for invalidation in invalidations:
                invalidation.cancel()
            if invalidations:
                await asyncio.gather(*invalidations, return_exceptions=True)
            if self._pubsub is not None:
                await self._pubsub.aclose()
            self._pubsub = self._client.pubsub(ignore_subscribe_messages=False)
            self._channels.clear()
            self._pending.clear()
            self._acknowledgements.clear()
            self._transport_failed = False

    async def subscribe(self, channel: str) -> None:
        async with self._command_lock:
            invalidation = self._invalidation_tasks.get(channel)
            if invalidation is not None:
                await asyncio.shield(invalidation)
            if self._transport_failed:
                raise RuntimeError("live_transport_unavailable")
            if channel in self._channels:
                return
            pubsub = self._require_pubsub()
            acknowledgement = self._register_control_acknowledgement(
                "subscribe", channel
            )
            try:
                await asyncio.wait_for(
                    pubsub.subscribe(channel),
                    timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
                )
                if self._reader_task is None or self._reader_task.done():
                    self._reader_task = asyncio.create_task(self._read_messages())
                await asyncio.wait_for(
                    asyncio.shield(acknowledgement.future),
                    timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
                )
            except BaseException:
                self._retire_control_acknowledgement(
                    "subscribe", channel, acknowledgement
                )
                cleanup = self._register_control_acknowledgement(
                    "unsubscribe", channel
                )
                cleanup_failed = False
                cleanup_confirmed = False
                try:
                    await asyncio.wait_for(
                        pubsub.unsubscribe(channel),
                        timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
                    )
                    await asyncio.wait_for(
                        asyncio.shield(cleanup.future),
                        timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
                    )
                    cleanup_confirmed = True
                except Exception:
                    cleanup_failed = True
                finally:
                    self._retire_control_acknowledgement(
                        "unsubscribe", channel, cleanup
                    )
                    if cleanup_confirmed:
                        self._discard_retired_acknowledgements(
                            "subscribe", channel
                        )
                    self._channels.discard(channel)
                if cleanup_failed:
                    await self._fail_transport()
                raise
            self._channels.add(channel)

    async def unsubscribe(self, channel: str) -> None:
        async with self._command_lock:
            if self._transport_failed:
                self._channels.discard(channel)
                return
            if channel not in self._channels or self._pubsub is None:
                return
            acknowledgement = self._register_control_acknowledgement(
                "unsubscribe", channel
            )
            try:
                await asyncio.wait_for(
                    self._pubsub.unsubscribe(channel),
                    timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
                )
                await asyncio.wait_for(
                    asyncio.shield(acknowledgement.future),
                    timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
                )
            except Exception:
                await self._fail_transport()
                raise
            finally:
                self._retire_control_acknowledgement(
                    "unsubscribe", channel, acknowledgement
                )
                self._channels.discard(channel)

    async def aclose(self) -> None:
        self._closing = True
        task = self._reader_task
        self._reader_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        invalidations = tuple(self._invalidation_tasks.values())
        self._invalidation_tasks.clear()
        for invalidation in invalidations:
            invalidation.cancel()
        if invalidations:
            await asyncio.gather(*invalidations, return_exceptions=True)
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._owns_client:
            await self._client.aclose()

    async def _read_messages(self) -> None:
        try:
            while not self._closing and not self._transport_failed:
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
                    self._accept_control_acknowledgement(message_type, channel)
                    continue
                if message_type != "message":
                    continue
                try:
                    publication = self._parse_publication(
                        channel=channel,
                        value=message.get("data"),
                    )
                except ValueError:
                    if channel and ("unsubscribe", channel) in self._pending:
                        continue
                    if not channel or channel not in self._channels:
                        raise RuntimeError("live_publication_channel_unattributable")
                    await self._invalidate_channel(channel)
                    continue
                if self._on_publication is None:
                    raise RuntimeError("live_source_listener_missing")
                await self._on_publication(publication)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._fail_transport()

    async def _invalidate_channel(self, channel: str) -> None:
        async with self._command_lock:
            if channel not in self._channels:
                return
            self._channels.discard(channel)
            if self._on_channel_failure is None:
                raise RuntimeError("live_source_channel_listener_missing")
            await self._on_channel_failure(channel, "live_publication_invalid")
            acknowledgement = self._register_control_acknowledgement(
                "unsubscribe", channel
            )
            try:
                await asyncio.wait_for(
                    self._require_pubsub().unsubscribe(channel),
                    timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
                )
            except Exception:
                self._retire_control_acknowledgement(
                    "unsubscribe", channel, acknowledgement
                )
                await self._fail_transport()
                return
            invalidation = asyncio.create_task(
                self._await_invalidation_ack(channel, acknowledgement)
            )
            self._invalidation_tasks[channel] = invalidation

    async def _await_invalidation_ack(
        self,
        channel: str,
        acknowledgement: _ControlAcknowledgement,
    ) -> None:
        current = asyncio.current_task()
        try:
            await asyncio.wait_for(
                asyncio.shield(acknowledgement.future),
                timeout=LIVE_SUBSCRIBE_TIMEOUT_SECONDS,
            )
        except Exception:
            await self._fail_transport()
        finally:
            self._retire_control_acknowledgement(
                "unsubscribe", channel, acknowledgement
            )
            if self._invalidation_tasks.get(channel) is current:
                self._invalidation_tasks.pop(channel, None)

    async def _fail_transport(self) -> None:
        if self._transport_failed:
            return
        self._transport_failed = True
        for acknowledgement in self._pending.values():
            acknowledgement.active = False
            if not acknowledgement.future.done():
                acknowledgement.future.set_exception(
                    RuntimeError("live_transport_unavailable")
                )
        self._pending.clear()
        self._acknowledgements.clear()
        self._channels.clear()
        if not self._closing and self._on_failure is not None:
            await self._on_failure("live_transport_unavailable")

    def _register_control_acknowledgement(
        self, message_type: str, channel: str
    ) -> _ControlAcknowledgement:
        key = (message_type, channel)
        if key in self._pending:
            raise RuntimeError("live_control_acknowledgement_overlap")
        self._control_generation += 1
        acknowledgement = _ControlAcknowledgement(
            generation=self._control_generation,
            future=asyncio.get_running_loop().create_future(),
        )
        self._pending[key] = acknowledgement
        self._acknowledgements.setdefault(key, deque()).append(acknowledgement)
        return acknowledgement

    def _retire_control_acknowledgement(
        self,
        message_type: str,
        channel: str,
        acknowledgement: _ControlAcknowledgement,
    ) -> None:
        key = (message_type, channel)
        if self._pending.get(key) is acknowledgement:
            self._pending.pop(key, None)
        acknowledgement.active = False
        if not acknowledgement.future.done():
            acknowledgement.future.cancel()

    def _accept_control_acknowledgement(
        self, message_type: str, channel: str
    ) -> None:
        key = (message_type, channel)
        queued = self._acknowledgements.get(key)
        if not queued:
            return
        acknowledgement = queued.popleft()
        if not queued:
            self._acknowledgements.pop(key, None)
        if (
            not acknowledgement.active
            or self._pending.get(key) is not acknowledgement
        ):
            return
        self._pending.pop(key, None)
        acknowledgement.active = False
        if message_type == "subscribe":
            self._channels.add(channel)
        else:
            self._channels.discard(channel)
        if not acknowledgement.future.done():
            acknowledgement.future.set_result(None)

    def _discard_retired_acknowledgements(
        self, message_type: str, channel: str
    ) -> None:
        key = (message_type, channel)
        queued = self._acknowledgements.get(key)
        if not queued:
            return
        active = deque(item for item in queued if item.active)
        if active:
            self._acknowledgements[key] = active
        else:
            self._acknowledgements.pop(key, None)

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
