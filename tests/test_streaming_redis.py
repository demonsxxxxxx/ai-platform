import hashlib
import os
import uuid

import pytest
from redis.exceptions import ResponseError

from app.streaming import api
from app.streaming import redis as stream_redis


class FakeRedis:
    def __init__(self):
        self.calls = []
        self.close_calls = 0

    async def eval(self, *args):
        self.calls.append(args)
        return b"1-0"

    async def aclose(self):
        self.close_calls += 1


def test_cursor_is_run_and_incarnation_bound_and_foreign_forms_fail_closed():
    cursor = api.StreamCursor.parse("run-a:7:1700000000000-0", run_id="run-a")
    assert cursor.event_id == "run-a:7:1700000000000-0"
    for value in ("run-b:7:1-0", "run-a:07:1-0", "run-a:0:1-0", "run-a:7:$", " run-a:7:1-0"):
        with pytest.raises(api.StreamContractError):
            api.StreamCursor.parse(value, run_id="run-a")


def test_append_lua_protocol_guard_is_closed_and_precedes_mutation():
    script = stream_redis._APPEND_WITH_TTL_LUA
    xadd = script.index("local id=redis.call('XADD'")
    assert script.index("stream_protocol_invalid") < xadd
    assert script.index("stream_protocol_conflict") < xadd
    assert script.index("last_event_redis_id") < xadd
    assert "redis.call('PUBLISH'" not in script
    assert "request_protocol='v3'" not in script
    assert "stored_protocol='v3'" not in script


@pytest.mark.asyncio
async def test_append_passes_only_v4_and_uses_the_atomic_ttl_boundary():
    client = FakeRedis()
    bridge = stream_redis.RedisStreamBridge(publish_client=client)
    identity = dict(tenant_scope_value="scope-a", run_id="run-a", stream_incarnation=1)
    for event_type in ("stream.open", "run.succeeded"):
        assert await bridge.append_canonical(**identity, event_id="evt4_test", event_type=event_type, envelope_bytes=b"{}") == "1-0"
    assert [call[1] for call in client.calls] == [2, 2]
    assert [call[-1] for call in client.calls] == ["v4", "v4"]
    assert [call[7] for call in client.calls] == [stream_redis.SSE_STREAM_ACTIVE_IDLE_TTL_MS, stream_redis.SSE_STREAM_TERMINAL_TTL_MS]
    assert [call[8] for call in client.calls] == ["stream_open", "terminal"]
    await bridge.aclose()
    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_default_bridge_uses_bounded_publish_and_read_pools(monkeypatch):
    created = []

    def from_url(url, **kwargs):
        client = FakeRedis()
        created.append((url, kwargs, client))
        return client

    monkeypatch.setattr(stream_redis.Redis, "from_url", from_url)
    bridge = stream_redis.RedisStreamBridge()
    assert [options["max_connections"] for _, options, _ in created] == [stream_redis.SSE_PUBLISH_MAX_CONNECTIONS, stream_redis.SSE_READ_MAX_CONNECTIONS]
    assert [options["socket_timeout"] for _, options, _ in created] == [5, 10]
    await bridge.aclose()
    assert [client.close_calls for _, _, client in created] == [1, 1]


@pytest.mark.asyncio
async def test_real_redis_v4_phase_ttl_receipts_and_bounded_stream(monkeypatch):
    redis_url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL")
    if not redis_url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    client = stream_redis.Redis.from_url(redis_url, decode_responses=True)
    bridge = stream_redis.RedisStreamBridge(publish_client=client)
    identity = dict(tenant_scope_value="scope-test", run_id=f"run-{uuid.uuid4().hex}", stream_incarnation=1)
    key = api.stream_key(**identity)
    monkeypatch.setattr(stream_redis, "SSE_STREAM_MAXLEN", 8)
    monkeypatch.setattr(stream_redis, "SSE_STREAM_TERMINAL_TTL_MS", 10_800_000)

    async def append(event_id, event_type, *, content="same", terminal_event_id=""):
        envelope = {"event_id": event_id, "content": content}
        if event_type.startswith("run."):
            envelope["source"] = {"kind": "run_event", "callback_sequence": 0}
        return await bridge.append_canonical(
            **identity, event_id=event_id, event_type=event_type,
            envelope_bytes=api.canonical_json_bytes(envelope),
            terminal_event_id=terminal_event_id,
        )

    try:
        for protocol in ("v2.1", "v3", ""):
            with pytest.raises(ResponseError, match="stream_protocol_invalid"):
                await client.eval(stream_redis._APPEND_WITH_TTL_LUA, 2, key, f"{key}:state", 8, "open", "{}", 5000, "stream_open", hashlib.sha256(b"{}").hexdigest(), "", protocol)
        assert await client.exists(key, f"{key}:state") == 0
        opened = await append("evt4_open", "stream.open")
        for name in (key, f"{key}:state"):
            assert 0 < await client.pttl(name) <= stream_redis.SSE_STREAM_ACTIVE_IDLE_TTL_MS
        for index in range(200):
            await append(f"evt4_delta_{index}", "message.delta")
        first = await append("evt4_last", "message.delta")
        assert await append("evt4_last", "message.delta") == first
        with pytest.raises(api.StreamContractError, match="stream_event_receipt_conflict"):
            await append("evt4_last", "message.delta", content="changed")
        assert await client.xlen(key) < 202
        terminal = await append("evt4_terminal", "run.succeeded")
        assert 7_200_000 < await client.pttl(key) <= 10_800_000
        assert await append("evt4_open", "stream.open") == opened
        assert await client.pttl(key) > 7_200_000
        with pytest.raises(api.StreamContractError, match="stream_end_without_terminal"):
            await append("evt4_wrong_end", "stream.end", terminal_event_id="evt4_other")
        ended = await append("evt4_end", "stream.end", terminal_event_id="evt4_terminal")
        assert await append("evt4_terminal", "run.succeeded") == terminal
        assert await append("evt4_end", "stream.end", terminal_event_id="evt4_terminal") == ended
        with pytest.raises(api.StreamContractError, match="stream_terminal_closed"):
            await append("evt4_late", "message.delta")
        await client.delete(key)
        with pytest.raises(api.StreamContractError, match="stream_missing"):
            await append("evt4_open", "stream.open")
        assert await client.xlen(key) == 0
    finally:
        await client.delete(key, f"{key}:state")
        await bridge.aclose()
        await client.aclose()
