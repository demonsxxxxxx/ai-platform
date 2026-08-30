import asyncio
import json

import pytest

from app.streaming.api import LivePublication, LiveSubscriptionClosed, RunStreamHub
from app.streaming.infrastructure import redis_live
from app.streaming.infrastructure.redis_live import RedisLiveFanoutSource


class FakeLiveSource:
    def __init__(self, *, publication_during_subscribe=None):
        self.publication_during_subscribe = publication_during_subscribe
        self.subscribes = []
        self.unsubscribes = []
        self.on_publication = None
        self.on_channel_failure = None
        self.on_failure = None
        self.closed = False

    async def start(self, *, on_publication, on_channel_failure, on_failure):
        self.on_publication = on_publication
        self.on_channel_failure = on_channel_failure
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

    async def fail_channel(self, channel):
        await self.on_channel_failure(channel, "live_publication_invalid")


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
        self.after_subscribe_ack = {}
        self.delayed_subscribe_ack = set()
        self.drop_unsubscribe_ack = set()
        self.block_unsubscribe = False
        self.unsubscribe_started = asyncio.Event()
        self.unsubscribe_release = asyncio.Event()
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)
        if channel not in self.delayed_subscribe_ack:
            await self.messages.put({"type": "subscribe", "channel": channel})
        message = self.after_subscribe_ack.pop(channel, None)
        if message is not None:
            await self.messages.put(message)

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)
        if self.block_unsubscribe:
            self.unsubscribe_started.set()
            await self.unsubscribe_release.wait()
        if channel in self.delayed_subscribe_ack:
            self.delayed_subscribe_ack.remove(channel)
            await self.messages.put({"type": "subscribe", "channel": channel})
        if channel not in self.drop_unsubscribe_ack:
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
    channel_failures = []
    await source.start(
        on_publication=lambda value: _append(received, value),
        on_channel_failure=lambda channel, reason: _append(
            channel_failures, (channel, reason)
        ),
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
    assert channel_failures == []
    assert failures == []
    await source.aclose()
    assert client.pubsub_instance.closed is True


@pytest.mark.asyncio
async def test_malformed_publication_closes_only_its_channel_and_allows_resubscribe():
    client = FakeRedisClient()
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)
    failed = await hub.subscribe("channel-a")
    unaffected = await hub.subscribe("channel-b")

    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "channel-a", "data": "not-json"}
    )
    with pytest.raises(LiveSubscriptionClosed, match="live_publication_invalid"):
        await failed.next(timeout_seconds=1)

    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-b",
            "data": json.dumps({"redis_id": "3-0", "envelope": "{}"}),
        }
    )
    assert await unaffected.next(timeout_seconds=1) == LivePublication(
        "channel-b", "3-0", "{}"
    )
    assert client.pubsub_instance.unsubscribed == ["channel-a"]

    reconnected = await hub.subscribe("channel-a")
    assert client.pubsub_instance.subscribed == [
        "channel-a",
        "channel-b",
        "channel-a",
    ]
    await reconnected.aclose()
    await unaffected.aclose()
    await hub.aclose()


@pytest.mark.asyncio
async def test_unattributable_malformed_publication_closes_all_channels():
    client = FakeRedisClient()
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)
    first = await hub.subscribe("channel-a")
    second = await hub.subscribe("channel-b")

    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "unknown-channel", "data": "not-json"}
    )

    for subscription in (first, second):
        with pytest.raises(
            LiveSubscriptionClosed, match="live_transport_unavailable"
        ):
            await subscription.next(timeout_seconds=1)
    await hub.aclose()


@pytest.mark.asyncio
async def test_malformed_publication_after_subscribe_ack_stays_channel_scoped():
    client = FakeRedisClient()
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)
    unaffected = await hub.subscribe("channel-b")
    client.pubsub_instance.after_subscribe_ack["channel-a"] = {
        "type": "message",
        "channel": "channel-a",
        "data": "not-json",
    }

    try:
        failed = await hub.subscribe("channel-a")
    except LiveSubscriptionClosed as exc:
        assert exc.reason == "live_publication_invalid"
    else:
        with pytest.raises(LiveSubscriptionClosed, match="live_publication_invalid"):
            await failed.next(timeout_seconds=1)

    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-b",
            "data": json.dumps({"redis_id": "4-0", "envelope": "{}"}),
        }
    )
    assert await unaffected.next(timeout_seconds=1) == LivePublication(
        "channel-b", "4-0", "{}"
    )
    await unaffected.aclose()
    await hub.aclose()


@pytest.mark.asyncio
async def test_concurrent_resubscribe_survives_old_channel_invalidation():
    client = FakeRedisClient()
    client.pubsub_instance.block_unsubscribe = True
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)
    failed = await hub.subscribe("channel-a")

    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "channel-a", "data": "not-json"}
    )
    await client.pubsub_instance.unsubscribe_started.wait()
    with pytest.raises(LiveSubscriptionClosed, match="live_publication_invalid"):
        await failed.next(timeout_seconds=1)

    reconnecting = asyncio.create_task(hub.subscribe("channel-a"))
    await asyncio.sleep(0)
    assert reconnecting.done() is False
    client.pubsub_instance.unsubscribe_release.set()
    reconnected = await asyncio.wait_for(reconnecting, timeout=1)
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-a",
            "data": json.dumps({"redis_id": "5-0", "envelope": "{}"}),
        }
    )

    assert await reconnected.next(timeout_seconds=1) == LivePublication(
        "channel-a", "5-0", "{}"
    )
    await reconnected.aclose()
    await hub.aclose()


@pytest.mark.asyncio
async def test_subscribe_timeout_drains_old_ack_before_same_channel_retry(monkeypatch):
    monkeypatch.setattr(redis_live, "LIVE_SUBSCRIBE_TIMEOUT_SECONDS", 0.01)
    client = FakeRedisClient()
    client.pubsub_instance.delayed_subscribe_ack.add("channel-a")
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)

    with pytest.raises(LiveSubscriptionClosed, match="live_subscribe_failed"):
        await hub.subscribe("channel-a")

    reconnected = await hub.subscribe("channel-a")
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-a",
            "data": json.dumps({"redis_id": "6-0", "envelope": "{}"}),
        }
    )
    assert await reconnected.next(timeout_seconds=1) == LivePublication(
        "channel-a", "6-0", "{}"
    )
    assert client.pubsub_instance.subscribed == ["channel-a", "channel-a"]
    assert client.pubsub_instance.unsubscribed == ["channel-a"]
    await reconnected.aclose()
    await hub.aclose()


@pytest.mark.asyncio
async def test_unsolicited_unsubscribe_ack_does_not_drop_live_channel():
    client = FakeRedisClient()
    source = RedisLiveFanoutSource(client=client)
    received = asyncio.Event()

    async def on_publication(_value):
        received.set()

    await source.start(
        on_publication=on_publication,
        on_channel_failure=lambda channel, reason: _append([], (channel, reason)),
        on_failure=lambda reason: _append([], reason),
    )
    await source.subscribe("channel-a")
    await client.pubsub_instance.messages.put(
        {"type": "unsubscribe", "channel": "channel-a"}
    )
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-a",
            "data": json.dumps({"redis_id": "10-0", "envelope": "{}"}),
        }
    )
    await asyncio.wait_for(received.wait(), timeout=1)

    await source.subscribe("channel-a")

    assert client.pubsub_instance.subscribed == ["channel-a"]
    await source.aclose()


@pytest.mark.asyncio
async def test_unsolicited_subscribe_ack_does_not_create_live_channel():
    client = FakeRedisClient()
    source = RedisLiveFanoutSource(client=client)
    received = asyncio.Event()

    async def on_publication(_value):
        received.set()

    await source.start(
        on_publication=on_publication,
        on_channel_failure=lambda channel, reason: _append([], (channel, reason)),
        on_failure=lambda reason: _append([], reason),
    )
    await source.subscribe("channel-a")
    await source.unsubscribe("channel-a")
    await client.pubsub_instance.messages.put(
        {"type": "subscribe", "channel": "channel-a"}
    )
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-a",
            "data": json.dumps({"redis_id": "11-0", "envelope": "{}"}),
        }
    )
    await asyncio.wait_for(received.wait(), timeout=1)

    await source.subscribe("channel-a")

    assert client.pubsub_instance.subscribed == ["channel-a", "channel-a"]
    await source.aclose()


@pytest.mark.asyncio
async def test_control_acknowledgements_are_generation_fenced():
    source = RedisLiveFanoutSource(client=FakeRedisClient())
    expired = source._register_control_acknowledgement("subscribe", "channel-a")
    source._retire_control_acknowledgement("subscribe", "channel-a", expired)
    current = source._register_control_acknowledgement("subscribe", "channel-a")

    source._accept_control_acknowledgement("subscribe", "channel-a")

    assert current.generation > expired.generation
    assert current.future.done() is False
    assert "channel-a" not in source._channels

    source._accept_control_acknowledgement("subscribe", "channel-a")

    assert current.future.result() is None
    assert "channel-a" in source._channels


@pytest.mark.asyncio
async def test_malformed_cleanup_ack_timeout_rebuilds_transport(monkeypatch):
    monkeypatch.setattr(redis_live, "LIVE_SUBSCRIBE_TIMEOUT_SECONDS", 0.01)
    client = FakeRedisClient()
    client.pubsub_instance.drop_unsubscribe_ack.add("channel-a")
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)
    failed = await hub.subscribe("channel-a")
    collateral = await hub.subscribe("channel-b")

    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "channel-a", "data": "not-json"}
    )
    with pytest.raises(LiveSubscriptionClosed, match="live_publication_invalid"):
        await failed.next(timeout_seconds=1)
    with pytest.raises(LiveSubscriptionClosed, match="live_transport_unavailable"):
        await collateral.next(timeout_seconds=1)

    reconnected = await hub.subscribe("channel-b")
    assert client.pubsub_calls == 2
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-b",
            "data": json.dumps({"redis_id": "7-0", "envelope": "{}"}),
        }
    )
    assert await reconnected.next(timeout_seconds=1) == LivePublication(
        "channel-b", "7-0", "{}"
    )
    await reconnected.aclose()
    await hub.aclose()


@pytest.mark.asyncio
async def test_duplicate_malformed_during_invalidation_stays_channel_scoped():
    client = FakeRedisClient()
    client.pubsub_instance.drop_unsubscribe_ack.add("channel-a")
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)
    failed = await hub.subscribe("channel-a")
    unaffected = await hub.subscribe("channel-b")

    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "channel-a", "data": "not-json"}
    )
    with pytest.raises(LiveSubscriptionClosed, match="live_publication_invalid"):
        await failed.next(timeout_seconds=1)
    while "channel-a" not in client.pubsub_instance.unsubscribed:
        await asyncio.sleep(0)
    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "channel-a", "data": "still-not-json"}
    )
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-b",
            "data": json.dumps({"redis_id": "9-0", "envelope": "{}"}),
        }
    )

    assert await unaffected.next(timeout_seconds=1) == LivePublication(
        "channel-b", "9-0", "{}"
    )
    await client.pubsub_instance.messages.put(
        {"type": "unsubscribe", "channel": "channel-a"}
    )
    await unaffected.aclose()
    await hub.aclose()


@pytest.mark.asyncio
async def test_subscribe_cleanup_command_timeout_fails_bounded(monkeypatch):
    monkeypatch.setattr(redis_live, "LIVE_SUBSCRIBE_TIMEOUT_SECONDS", 0.01)
    client = FakeRedisClient()
    client.pubsub_instance.delayed_subscribe_ack.add("channel-a")
    client.pubsub_instance.block_unsubscribe = True
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)

    with pytest.raises(LiveSubscriptionClosed, match="live_subscribe_failed"):
        await asyncio.wait_for(hub.subscribe("channel-a"), timeout=1)

    assert client.pubsub_instance.unsubscribe_started.is_set()
    await hub.aclose()


@pytest.mark.asyncio
async def test_transport_rebuild_waits_for_old_command_cleanup(monkeypatch):
    monkeypatch.setattr(redis_live, "LIVE_SUBSCRIBE_TIMEOUT_SECONDS", 0.02)
    client = FakeRedisClient()
    client.pubsub_instance.delayed_subscribe_ack.add("channel-a")
    client.pubsub_instance.block_unsubscribe = True
    source = RedisLiveFanoutSource(client=client)
    hub = RunStreamHub(source=source)
    first = asyncio.create_task(hub.subscribe("channel-a"))
    while not client.pubsub_instance.subscribed:
        await asyncio.sleep(0)
    await client.pubsub_instance.messages.put(
        {"type": "message", "channel": "unknown-channel", "data": "not-json"}
    )
    await client.pubsub_instance.unsubscribe_started.wait()

    rebuilding = asyncio.create_task(hub.subscribe("channel-b"))
    await asyncio.sleep(0)
    assert rebuilding.done() is False
    assert client.pubsub_instance.closed is False
    client.pubsub_instance.unsubscribe_release.set()

    with pytest.raises(LiveSubscriptionClosed, match="live_subscribe_failed"):
        await asyncio.wait_for(first, timeout=1)
    reconnected = await asyncio.wait_for(rebuilding, timeout=1)
    assert client.pubsub_calls == 2
    assert client.pubsub_instance.closed is True
    await client.pubsub_instance.messages.put(
        {
            "type": "message",
            "channel": "channel-b",
            "data": json.dumps({"redis_id": "8-0", "envelope": "{}"}),
        }
    )
    assert await reconnected.next(timeout_seconds=1) == LivePublication(
        "channel-b", "8-0", "{}"
    )
    await reconnected.aclose()
    await hub.aclose()


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
