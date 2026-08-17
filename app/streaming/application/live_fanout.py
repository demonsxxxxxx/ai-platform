"""Process-wide SSE v3 live fan-out with bounded browser queues."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.streaming.domain.live import LivePublication

LIVE_SUBSCRIBER_MAX_EVENTS = 256
LIVE_SUBSCRIBER_MAX_BYTES = 1024 * 1024


class LiveFanoutSource(Protocol):
    async def start(
        self,
        *,
        on_publication: Callable[[LivePublication], Awaitable[None]],
        on_failure: Callable[[str], Awaitable[None]],
    ) -> None: ...

    async def subscribe(self, channel: str) -> None: ...

    async def unsubscribe(self, channel: str) -> None: ...

    async def aclose(self) -> None: ...


class LiveSubscriptionClosed(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _CloseNotice:
    reason: str


class LiveSubscription:
    __slots__ = (
        "_bytes",
        "_channel",
        "_closed",
        "_detach",
        "_max_bytes",
        "_queue",
    )

    def __init__(
        self,
        *,
        channel: str,
        detach: Callable[[LiveSubscription], Awaitable[None]],
        max_events: int,
        max_bytes: int,
    ) -> None:
        if max_events < 1 or max_bytes < 1:
            raise ValueError("live_subscription_limits_invalid")
        self._channel = channel
        self._detach = detach
        self._max_bytes = max_bytes
        self._queue: asyncio.Queue[LivePublication | _CloseNotice] = asyncio.Queue(
            maxsize=max_events
        )
        self._bytes = 0
        self._closed = False

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def closed(self) -> bool:
        return self._closed

    async def next(self, *, timeout_seconds: float) -> LivePublication:
        if timeout_seconds <= 0:
            raise ValueError("live_subscription_timeout_invalid")
        item = await asyncio.wait_for(self._queue.get(), timeout=timeout_seconds)
        if isinstance(item, _CloseNotice):
            raise LiveSubscriptionClosed(item.reason)
        self._bytes = max(0, self._bytes - item.byte_size)
        return item

    async def aclose(self) -> None:
        await self._detach(self)

    def offer(self, publication: LivePublication) -> None:
        if self._closed:
            return
        if (
            self._queue.full()
            or publication.byte_size > self._max_bytes
            or self._bytes + publication.byte_size > self._max_bytes
        ):
            self.close_from_hub("live_subscriber_overflow")
            return
        self._bytes += publication.byte_size
        self._queue.put_nowait(publication)

    def close_from_hub(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if isinstance(item, LivePublication):
                self._bytes = max(0, self._bytes - item.byte_size)
        self._queue.put_nowait(_CloseNotice(reason))


class RunStreamHub:
    def __init__(
        self,
        *,
        source: LiveFanoutSource,
        max_events: int = LIVE_SUBSCRIBER_MAX_EVENTS,
        max_bytes: int = LIVE_SUBSCRIBER_MAX_BYTES,
    ) -> None:
        self._source = source
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._subscriptions: dict[str, set[LiveSubscription]] = {}
        self._channel_ready: dict[str, asyncio.Future[None]] = {}
        self._detach_tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._started = False
        self._closed = False

    async def subscribe(self, channel: str) -> LiveSubscription:
        if not isinstance(channel, str) or not channel:
            raise ValueError("live_channel_invalid")
        subscription = LiveSubscription(
            channel=channel,
            detach=self._detach,
            max_events=self._max_events,
            max_bytes=self._max_bytes,
        )
        first = False
        async with self._lock:
            if self._closed:
                raise LiveSubscriptionClosed("live_hub_closed")
            if not self._started:
                await self._source.start(
                    on_publication=self._deliver,
                    on_failure=self._source_failed,
                )
                self._started = True
            channel_subscriptions = self._subscriptions.get(channel)
            if channel_subscriptions is None:
                channel_subscriptions = set()
                self._subscriptions[channel] = channel_subscriptions
                self._channel_ready[channel] = asyncio.get_running_loop().create_future()
                first = True
            channel_subscriptions.add(subscription)
            ready = self._channel_ready[channel]
        if first:
            try:
                await self._source.subscribe(channel)
            except BaseException as exc:
                await asyncio.shield(
                    self._fail_channel(channel, ready, "live_subscribe_failed")
                )
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise LiveSubscriptionClosed("live_subscribe_failed") from exc
            else:
                if not ready.done():
                    ready.set_result(None)
        try:
            await asyncio.shield(ready)
        except asyncio.CancelledError:
            await asyncio.shield(self._detach(subscription))
            raise
        return subscription

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = tuple(
                subscription
                for values in self._subscriptions.values()
                for subscription in values
            )
            self._subscriptions.clear()
            ready = tuple(self._channel_ready.values())
            self._channel_ready.clear()
        for future in ready:
            if not future.done():
                future.set_exception(LiveSubscriptionClosed("live_hub_closed"))
        for subscription in subscriptions:
            subscription.close_from_hub("live_hub_closed")
        if self._detach_tasks:
            await asyncio.gather(*tuple(self._detach_tasks), return_exceptions=True)
        await self._source.aclose()

    async def _fail_channel(
        self,
        channel: str,
        ready: asyncio.Future[None],
        reason: str,
    ) -> None:
        async with self._lock:
            failed = tuple(self._subscriptions.pop(channel, ()))
            self._channel_ready.pop(channel, None)
        for subscription in failed:
            subscription.close_from_hub(reason)
        if not ready.done():
            ready.set_exception(LiveSubscriptionClosed(reason))
        try:
            await asyncio.shield(ready)
        except LiveSubscriptionClosed:
            pass

    async def _detach(self, subscription: LiveSubscription) -> None:
        unsubscribe = False
        async with self._lock:
            values = self._subscriptions.get(subscription.channel)
            if values is not None:
                values.discard(subscription)
                if not values:
                    self._subscriptions.pop(subscription.channel, None)
                    self._channel_ready.pop(subscription.channel, None)
                    unsubscribe = True
        subscription.close_from_hub("live_subscription_closed")
        if unsubscribe:
            await self._source.unsubscribe(subscription.channel)

    async def _deliver(self, publication: LivePublication) -> None:
        async with self._lock:
            subscriptions = tuple(
                self._subscriptions.get(publication.channel, ())
            )
        for subscription in subscriptions:
            subscription.offer(publication)
            if subscription.closed:
                task = asyncio.create_task(self._detach(subscription))
                self._detach_tasks.add(task)
                task.add_done_callback(self._detach_finished)

    def _detach_finished(self, task: asyncio.Task[None]) -> None:
        self._detach_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _source_failed(self, reason: str) -> None:
        async with self._lock:
            subscriptions = tuple(
                subscription
                for values in self._subscriptions.values()
                for subscription in values
            )
            self._subscriptions.clear()
            ready = tuple(self._channel_ready.values())
            self._channel_ready.clear()
            self._started = False
        for future in ready:
            if not future.done():
                future.set_exception(LiveSubscriptionClosed(reason))
        for subscription in subscriptions:
            subscription.close_from_hub(reason)
