import json
import types

import pytest

from app.streaming import redis as stream_redis


def _settings():
    return types.SimpleNamespace(
        sse_stream_maxlen=10000,
        sse_stream_active_idle_ttl_ms=7200000,
        sse_stream_terminal_ttl_ms=10800000,
        sse_stream_read_count=128,
        sse_stream_block_ms=15000,
        sse_authority_lease_seconds=15,
    )


def _envelope(*, event_id="sev_open", incarnation=1, event_type="stream_open", payload=None):
    return stream_redis.StreamEnvelope(
        event_id=event_id,
        tenant_scope="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=incarnation,
        event_type=event_type,
        emitted_at="2026-08-08T00:00:00Z",
        payload=payload or {"design_id": stream_redis.STREAM_DESIGN_ID},
    )


class FakeRedis:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.eval_calls = []
        self.xread_calls = []
        self.close_calls = 0
        self.event_ids = {}

    async def eval(self, script, key_count, key, dedupe_key, maxlen, event_id, envelope, ttl_ms):
        self.eval_calls.append((script, key_count, key, dedupe_key, maxlen, event_id, envelope, ttl_ms))
        if event_id in self.event_ids:
            return self.event_ids[event_id]
        redis_id = f"1700000000000-{len(self.rows)}"
        self.rows.append((redis_id, {"envelope": envelope}))
        self.event_ids[event_id] = redis_id
        return redis_id

    async def xrange(self, _key, min="-", max="+", count=None):
        if min == max and min not in {"-", "+"}:
            return [row for row in self.rows if row[0] == min][: count or None]
        return self.rows[: count or None]

    async def xrevrange(self, _key, max="+", min="-", count=None):
        return list(reversed(self.rows))[: count or None]

    async def xread(self, streams, count, block):
        self.xread_calls.append((streams, count, block))
        key, after = next(iter(streams.items()))
        later = [row for row in self.rows if stream_redis._redis_id_tuple(row[0]) > stream_redis._redis_id_tuple(after)]
        return [(key, later[:count])] if later else []

    async def aclose(self):
        self.close_calls += 1


def test_cursor_is_run_and_incarnation_bound_and_future_or_foreign_forms_fail_closed():
    cursor = stream_redis.StreamCursor.parse("run-a:7:1700000000000-0", run_id="run-a")
    assert cursor.event_id == "run-a:7:1700000000000-0"

    for value in (
        "run-b:7:1700000000000-0",
        "run-a:07:1700000000000-0",
        "run-a:0:1700000000000-0",
        "run-a:7:$",
        " run-a:7:1700000000000-0",
    ):
        with pytest.raises(stream_redis.StreamContractError):
            stream_redis.StreamCursor.parse(value, run_id="run-a")


def test_projection_rejects_hidden_reasoning_commands_credentials_and_unknown_events():
    with pytest.raises(stream_redis.StreamProjectionError, match="stream_event_type_not_public"):
        _envelope(event_type="assistant_reasoning_delta", payload={"delta": "private"})
    for payload in ({"command": "rm"}, {"callback_token": "secret"}, {"local_path": "/tmp/a"}):
        with pytest.raises(stream_redis.StreamProjectionError, match="stream_payload_forbidden_key"):
            _envelope(event_type="semantic_stage", payload=payload)


def test_stable_event_id_is_deterministic_per_batch_item():
    kwargs = dict(
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-a",
        item_index=0,
    )
    assert stream_redis.stable_event_id(**kwargs) == stream_redis.stable_event_id(**kwargs)
    assert stream_redis.stable_event_id(**kwargs) != stream_redis.stable_event_id(**{**kwargs, "item_index": 1})


@pytest.mark.asyncio
async def test_append_atomically_combines_xadd_maxlen_and_ttl_refresh(monkeypatch):
    monkeypatch.setattr(stream_redis, "SSE_STREAM_TERMINAL_TTL_MS", 10800000)
    publisher = FakeRedis()
    blocking = FakeRedis()
    bridge = stream_redis.RedisStreamBridge(publish_client=publisher, blocking_client=blocking)

    active = await bridge.append(_envelope())
    terminal = await bridge.append(
        _envelope(event_id="sev_terminal", event_type="terminal", payload={"status": "succeeded"}),
        terminal=True,
    )

    assert active.redis_id == "1700000000000-0"
    assert terminal.redis_id == "1700000000000-1"
    assert "XADD" in publisher.eval_calls[0][0]
    assert "PEXPIRE" in publisher.eval_calls[0][0]
    assert publisher.eval_calls[0][-1] == 7200000
    assert publisher.eval_calls[1][-1] == 10800000
    assert json.loads(publisher.eval_calls[0][6])["event_type"] == "stream_open"

    duplicate = await bridge.append(_envelope())
    assert duplicate == active
    assert len(publisher.rows) == 2


@pytest.mark.asyncio
async def test_default_bridge_uses_independent_bounded_publish_and_blocking_pools(monkeypatch):
    created = []

    def from_url(url, **kwargs):
        client = FakeRedis()
        created.append((url, kwargs["max_connections"], client))
        return client

    monkeypatch.setattr(stream_redis.Redis, "from_url", from_url)
    monkeypatch.setattr(stream_redis, "get_settings", lambda: types.SimpleNamespace(redis_url="redis://redis:6379/0"))

    bridge = stream_redis.RedisStreamBridge()
    assert [(url, maximum) for url, maximum, _ in created] == [
        ("redis://redis:6379/0", stream_redis.SSE_PUBLISH_MAX_CONNECTIONS),
        ("redis://redis:6379/0", stream_redis.SSE_BLOCKING_MAX_CONNECTIONS),
    ]
    assert created[0][2] is not created[1][2]
    await bridge.aclose()
    assert [client.close_calls for _, _, client in created] == [1, 1]


@pytest.mark.asyncio
async def test_trim_missing_and_rebuild_return_idless_gap_without_xread(monkeypatch):
    monkeypatch.setattr(stream_redis, "get_settings", _settings)
    first = _envelope().canonical_bytes.decode()
    delta = _envelope(
        event_id="sev_delta",
        event_type="assistant_text_delta",
        payload={"delta": "hello"},
    ).canonical_bytes.decode()
    publisher = FakeRedis(
        [
            ("1700000000100-0", {"envelope": first}),
            ("1700000000200-0", {"envelope": delta}),
        ]
    )
    blocking = FakeRedis(publisher.rows)
    bridge = stream_redis.RedisStreamBridge(publish_client=publisher, blocking_client=blocking)

    trimmed = await bridge.resolve_resume(
        tenant_scope_value="scope-a",
        run_id="run-a",
        current_stream_incarnation=1,
        last_event_id="run-a:1:1700000000000-0",
    )
    rebuilt = await bridge.resolve_resume(
        tenant_scope_value="scope-a",
        run_id="run-a",
        current_stream_incarnation=2,
        last_event_id="run-a:1:1700000000100-0",
    )

    assert trimmed.after_redis_id is None
    assert trimmed.gap.reason == "retained_history_unavailable"
    assert rebuilt.after_redis_id is None
    assert rebuilt.gap.reason == "stream_incarnation_mismatch"
    assert blocking.xread_calls == []


@pytest.mark.asyncio
async def test_two_readers_receive_same_entries_and_cleanup_handles(monkeypatch):
    monkeypatch.setattr(stream_redis, "get_settings", _settings)
    rows = [
        ("1700000000000-0", {"envelope": _envelope().canonical_bytes.decode()}),
        (
            "1700000000040-0",
            {
                "envelope": _envelope(
                    event_id="sev_delta",
                    event_type="assistant_text_delta",
                    payload={"delta": "hello"},
                ).canonical_bytes.decode()
            },
        ),
    ]
    publisher = FakeRedis(rows)
    first_reader = FakeRedis(rows)
    second_reader = FakeRedis(rows)
    first_bridge = stream_redis.RedisStreamBridge(publish_client=publisher, blocking_client=first_reader)
    second_bridge = stream_redis.RedisStreamBridge(publish_client=publisher, blocking_client=second_reader)

    first_entries = await first_bridge.read(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=1,
        after_redis_id="0-0",
    )
    second_entries = await second_bridge.read(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=1,
        after_redis_id="0-0",
    )

    assert [entry.cursor.event_id for entry in first_entries] == [
        entry.cursor.event_id for entry in second_entries
    ]
    await first_bridge.aclose()
    assert first_reader.close_calls == 1
