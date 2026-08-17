import asyncio
import json

import pytest

from app.streaming.api import LivePublication, LiveSubscriptionClosed, RunStreamHub
from app.streaming.infrastructure.redis_live import RedisLiveFanoutSource


class FakeLiveSource:
    def __init__(self, *, publication_during_subscribe=None):
        self.publication_during_subscribe = publication_during_subscribe
        self.subscribes = []
        self.unsubscribes = []
        self.on_publication = None
        self.on_failure = None
        self.closed = False

    async def start(self, *, on_publication, on_failure):
        self.on_publication = on_publication
        self.on_failure = on_failure

    async def subscribe(self, channel):
        self.subscribes.append(channel)
        if self.publication_during_subscribe is not None:
            await self.on_publication(self.publication_during_subscribe)

    async def unsubscribe(self, channel):
        self.unsubscribes.append(channel)

    async def aclose(self):
        self.closed = True

    async def publish(self, value):
        await self.on_publication(value)

    async def disconnect(self):
        await self.on_failure("live_transport_unavailable")


class BlockingLiveSource(FakeLiveSource):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def subscribe(self, channel):
        self.subscribes.append(channel)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.unsubscribes.append(channel)
            raise


class FakePubSub:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)
        await self.messages.put({"type": "subscribe", "channel": channel})

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)
        await self.messages.put({"type": "unsubscribe", "channel": channel})

    async def get_message(self, *, ignore_subscribe_messages, timeout):
        try:
            return await asyncio.wait_for(self.messages.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def aclose(self):
        self.closed = True


class FakeRedisClient:
    def __init__(self):
        self.pubsub_instance = FakePubSub()
        self.pubsub_calls = 0

    def pubsub(self, *, ignore_subscribe_messages):
        self.pubsub_calls += 1
        return self.pubsub_instance


def publication(redis_id="1-0", envelope_json="{}"):
    return LivePublication("sse-live:scope-a:run-a:1", redis_id, envelope_json)


@pytest.mark.asyncio
async def test_hub_reuses_one_source_subscription_for_multiple_browsers():
    source = FakeLiveSource()
    hub = RunStreamHub(source=source)

    first = await hub.subscribe(publication().channel)
    second = await hub.subscribe(publication().channel)
    await source.publish(publication())

    assert source.subscribes == [publication().channel]
    assert await first.next(timeout_seconds=1) == publication()
    assert await second.next(timeout_seconds=1) == publication()

    await first.aclose()
    assert source.unsubscribes == []
    await second.aclose()
    assert source.unsubscribes == [publication().channel]
    await hub.aclose()
    assert source.closed is True


@pytest.mark.asyncio
async def test_subscribe_ack_buffers_publication_before_replay_snapshot():
    early = publication("2-0", '{"event_type":"assistant_text_delta"}')
    source = FakeLiveSource(publication_during_subscribe=early)
    hub = RunStreamHub(source=source)

    subscription = await hub.subscribe(early.channel)

    assert await subscription.next(timeout_seconds=1) == early
    await subscription.aclose()


@pytest.mark.asyncio
async def test_redis_source_multiplexes_channels_on_one_pubsub_connection():
    client = FakeRedisClient()
    source = RedisLiveFanoutSource(client=client)
    received = []
    failures = []
    await source.start(
        on_publication=lambda value: _append(received, value),
        on_failure=lambda reason: _append(failures, reason),
    )

    await source.subscribe("channel-a")
    await source.subscribe("channel-b")
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-a",
            "data": json.dumps({"redis_id": "2-0", "envelope": "{}"}),
        }
    )
    for _ in range(10):
        if received:
            break
        await asyncio.sleep(0)

    assert client.pubsub_calls == 1
    assert client.pubsub_instance.subscribed == ["channel-a", "channel-b"]
    assert received == [LivePublication("channel-a", "2-0", "{}")]
    assert failures == []
    await source.aclose()
    assert client.pubsub_instance.closed is True


@pytest.mark.asyncio
async def test_redis_source_skips_malformed_publication_without_failing_transport():
    client = FakeRedisClient()
    source = RedisLiveFanoutSource(client=client)
    received = []
    failures = []
    await source.start(
        on_publication=lambda value: _append(received, value),
        on_failure=lambda reason: _append(failures, reason),
    )

    await source.subscribe("channel-a")
    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "channel-a", "data": "not-json"}
    )
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-a",
            "data": json.dumps({"redis_id": "3-0", "envelope": "{}"}),
        }
    )
    for _ in range(10):
        if received:
            break
        await asyncio.sleep(0)

    assert received == [LivePublication("channel-a", "3-0", "{}")]
    assert failures == []
    await source.aclose()


async def _append(values, value):
    values.append(value)


@pytest.mark.asyncio
async def test_cancelled_first_subscriber_fails_channel_without_leaking_waiters():
    source = BlockingLiveSource()
    hub = RunStreamHub(source=source)

    first = asyncio.create_task(hub.subscribe(publication().channel))
    await source.started.wait()
    second = asyncio.create_task(hub.subscribe(publication().channel))
    await asyncio.sleep(0)
    first.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(LiveSubscriptionClosed, match="live_subscribe_failed"):
        await second
    assert source.unsubscribes == [publication().channel]
    await hub.aclose()


@pytest.mark.asyncio
async def test_queue_event_or_byte_overflow_closes_and_detaches_browser():
    source = FakeLiveSource()
    hub = RunStreamHub(source=source, max_events=1, max_bytes=80)
    subscription = await hub.subscribe(publication().channel)

    await source.publish(publication("1-0", "x" * 40))
    await source.publish(publication("2-0", "x"))

    with pytest.raises(LiveSubscriptionClosed, match="live_subscriber_overflow"):
        await subscription.next(timeout_seconds=1)
    for _ in range(3):
        if source.unsubscribes:
            break
        await asyncio.sleep(0)
    assert source.unsubscribes == [publication().channel]


@pytest.mark.asyncio
async def test_source_disconnect_closes_all_subscribers_for_replay_reconnect():
    source = FakeLiveSource()
    hub = RunStreamHub(source=source)
    first = await hub.subscribe(publication().channel)
    second = await hub.subscribe(publication().channel)

    await source.disconnect()

    for subscription in (first, second):
        with pytest.raises(
            LiveSubscriptionClosed, match="live_transport_unavailable"
        ):
            await subscription.next(timeout_seconds=1)
