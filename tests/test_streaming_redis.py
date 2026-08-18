import json
import os
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


def _envelope(
    *, event_id="sev_open", incarnation=1, event_type="stream_open", payload=None
):
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
        self.published = []
        self.close_calls = 0
        self.event_ids = {}

    async def eval(
        self,
        script,
        key_count,
        key,
        state_key,
        live_channel,
        maxlen,
        event_id,
        envelope,
        ttl_ms,
        event_type,
        digest,
        terminal_event_id,
    ):
        self.eval_calls.append(
            (
                script,
                key_count,
                key,
                state_key,
                live_channel,
                maxlen,
                event_id,
                envelope,
                ttl_ms,
                event_type,
                digest,
                terminal_event_id,
            )
        )
        if (
            event_type in {"stream_open", "terminal", "end"}
            and event_id in self.event_ids
        ):
            return self.event_ids[event_id]
        redis_id = f"1700000000000-{len(self.rows)}"
        self.rows.append((redis_id, {"envelope": envelope}))
        self.published.append(
            (live_channel, json.dumps({"redis_id": redis_id, "envelope": envelope}))
        )
        self.event_ids[event_id] = redis_id
        return redis_id

    async def xrange(self, _key, min="-", max="+", count=None):
        if min == max and min not in {"-", "+"}:
            return [row for row in self.rows if row[0] == min][: count or None]
        rows = list(self.rows)
        if min.startswith("("):
            after = min[1:]
            rows = [
                row
                for row in rows
                if stream_redis._redis_id_tuple(row[0])
                > stream_redis._redis_id_tuple(after)
            ]
        if max != "+":
            rows = [
                row
                for row in rows
                if stream_redis._redis_id_tuple(row[0])
                <= stream_redis._redis_id_tuple(max)
            ]
        return rows[: count or None]

    async def xrevrange(self, _key, max="+", min="-", count=None):
        return list(reversed(self.rows))[: count or None]

    async def aclose(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_real_redis_lua_phase_ttl_idempotency_and_bounded_stream(monkeypatch):
    redis_url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL")
    if not redis_url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")

    publish = stream_redis.Redis.from_url(redis_url, decode_responses=True)
    bridge = stream_redis.RedisStreamBridge(publish_client=publish)
    key = stream_redis.stream_key(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=1,
    )
    monkeypatch.setattr(stream_redis, "SSE_STREAM_MAXLEN", 8)
    monkeypatch.setattr(stream_redis, "SSE_STREAM_TERMINAL_TTL_MS", 10_800_000)
    await publish.delete(key, f"{key}:state")
    try:
        opened = await bridge.append(_envelope())
        active_ttl = await publish.pttl(key)
        state_ttl = await publish.pttl(f"{key}:state")
        assert 0 < active_ttl <= stream_redis.SSE_STREAM_ACTIVE_IDLE_TTL_MS
        assert 0 < state_ttl <= stream_redis.SSE_STREAM_ACTIVE_IDLE_TTL_MS

        for index in range(200):
            await bridge.append(
                _envelope(
                    event_id=f"sev_active_{index}",
                    event_type="assistant_text_delta",
                    payload={"delta": "x"},
                )
            )
        duplicate = _envelope(
            event_id="sev_active_duplicate",
            event_type="assistant_text_delta",
            payload={"delta": "same"},
        )
        first_duplicate = await bridge.append(duplicate)
        second_duplicate = await bridge.append(duplicate)
        assert first_duplicate.redis_id != second_duplicate.redis_id
        assert await publish.xlen(key) < 204

        terminal = _envelope(
            event_id="sev_terminal",
            event_type="terminal",
            payload={
                "event_id": "sev_terminal",
                "hydrate_required": True,
                "status": "succeeded",
            },
        )
        terminal_cursor = await bridge.append(terminal, terminal=True)
        terminal_ttl = await publish.pttl(key)
        assert 7_200_000 < terminal_ttl <= 10_800_000

        assert await bridge.append(_envelope()) == opened
        assert await publish.pttl(key) > 7_200_000
        with pytest.raises(
            stream_redis.StreamContractError, match="stream_end_without_terminal"
        ):
            await bridge.append(
                _envelope(
                    event_id="sev_wrong_end",
                    event_type="end",
                    payload={"terminal_event_id": "sev_other_terminal"},
                ),
                terminal=True,
            )

        ended = await bridge.append(
            _envelope(
                event_id="sev_end",
                event_type="end",
                payload={"terminal_event_id": "sev_terminal"},
            ),
            terminal=True,
        )
        assert await bridge.append(terminal, terminal=True) == terminal_cursor
        assert (
            await bridge.append(
                _envelope(
                    event_id="sev_end",
                    event_type="end",
                    payload={"terminal_event_id": "sev_terminal"},
                ),
                terminal=True,
            )
            == ended
        )
        with pytest.raises(
            stream_redis.StreamContractError, match="stream_terminal_closed"
        ):
            await bridge.append(
                _envelope(
                    event_id="sev_late",
                    event_type="assistant_text_delta",
                    payload={"delta": "late"},
                )
            )
    finally:
        await publish.delete(key, f"{key}:state")
        await bridge.aclose()
        await publish.aclose()


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
    with pytest.raises(
        stream_redis.StreamProjectionError, match="stream_event_type_not_public"
    ):
        _envelope(event_type="assistant_reasoning_delta", payload={"delta": "private"})
    for payload in (
        {"command": "rm"},
        {"callback_token": "secret"},
        {"local_path": "/tmp/a"},
    ):
        with pytest.raises(
            stream_redis.StreamProjectionError, match="stream_payload_forbidden_key"
        ):
            _envelope(event_type="semantic_stage", payload=payload)


def test_committed_execution_projection_uses_only_pg_identity_sequence_and_created_at():
    row = {
        "id": "evt-execution-1",
        "run_id": "run-a",
        "sequence": 17,
        "event_type": "execution_step",
        "visible_to_user": True,
        "created_at": "2026-08-09T01:02:03Z",
        "payload_json": {
            "step_id": "pex_execution_1",
            "kind": "processing",
            "stage": "execution",
            "status": "running",
            "title": "Process request",
            "summary": "Running controlled processing",
            "progress": {"current": 0, "total": 1},
        },
    }

    projected = stream_redis.committed_public_stream_event(row)

    assert projected is not None
    envelope_type, payload = projected
    assert envelope_type == "semantic_progress"
    assert payload["event"] == "execution_step"
    assert payload["data"]["event_id"] == "evt-execution-1"
    assert payload["data"]["sequence"] == 17
    assert payload["data"]["created_at"] == "2026-08-09T01:02:03Z"
    assert "command" not in json.dumps(payload)


def test_v21_public_event_contract_excludes_unimplemented_raw_lifecycle_channels():
    assert stream_redis.PUBLIC_EVENT_TYPES == {
        "stream_open",
        "assistant_text_delta",
        "semantic_stage",
        "semantic_progress",
        "terminal",
        "end",
    }
    for event_type in (
        "tool_lifecycle",
        "approval_required",
        "artifact_ready",
        "run_status",
    ):
        with pytest.raises(
            stream_redis.StreamProjectionError, match="stream_event_type_not_public"
        ):
            _envelope(event_type=event_type, payload={"event": "run_event", "data": {}})


def test_terminal_and_end_payloads_are_exact_and_cross_linked():
    terminal = _envelope(
        event_id="sev_terminal",
        event_type="terminal",
        payload={
            "event_id": "sev_terminal",
            "hydrate_required": True,
            "status": "succeeded",
        },
    )
    end = _envelope(
        event_id="sev_end",
        event_type="end",
        payload={"terminal_event_id": "sev_terminal"},
    )

    assert terminal.payload["event_id"] == end.payload["terminal_event_id"]
    with pytest.raises(
        stream_redis.StreamProjectionError, match="stream_terminal_payload_invalid"
    ):
        _envelope(
            event_id="sev_terminal",
            event_type="terminal",
            payload={"status": "succeeded"},
        )
    with pytest.raises(
        stream_redis.StreamProjectionError, match="stream_end_payload_invalid"
    ):
        _envelope(event_id="sev_end", event_type="end", payload={"done": True})


@pytest.mark.asyncio
async def test_run_publisher_appends_committed_safe_projection_with_pg_semantic_id():
    appended = []

    class Bridge:
        async def append(self, envelope, *, terminal=False):
            appended.append((envelope, terminal))
            return stream_redis.StreamCursor("run-a", 1, "1-0")

        async def aclose(self):
            return None

    authority = stream_redis.StreamAuthority(
        "tenant-a",
        "run-a",
        "attempt-a",
        "scope-a",
        1,
        "confirmed",
        "sev-open",
        _envelope().canonical_bytes.decode(),
        "digest",
        1,
        "active",
    )
    publisher = stream_redis.RunStreamPublisher(
        "tenant-a",
        "run-a",
        "attempt-a",
        "secret",
        bridge=Bridge(),
        authority=authority,
    )

    published = await publisher.publish_committed_event(
        {
            "id": "evt-execution-1",
            "run_id": "run-a",
            "sequence": 17,
            "event_type": "execution_step",
            "visible_to_user": True,
            "created_at": "2026-08-09T01:02:03Z",
            "payload_json": {
                "step_id": "pex_execution_1",
                "kind": "processing",
                "stage": "execution",
                "status": "running",
                "title": "Process request",
                "summary": "Running controlled processing",
                "progress": {"current": 0, "total": 1},
            },
        }
    )

    assert published is True
    assert appended[0][0].event_id == "evt-execution-1"
    assert appended[0][0].event_type == "semantic_progress"
    assert appended[0][0].emitted_at == "2026-08-09T01:02:03Z"
    assert appended[0][1] is False


def test_stable_event_id_is_deterministic_per_batch_item():
    kwargs = dict(
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-a",
        item_index=0,
    )
    assert stream_redis.stable_event_id(**kwargs) == stream_redis.stable_event_id(
        **kwargs
    )
    assert stream_redis.stable_event_id(**kwargs) != stream_redis.stable_event_id(
        **{**kwargs, "item_index": 1}
    )


@pytest.mark.asyncio
async def test_append_atomically_combines_xadd_maxlen_and_ttl_refresh(monkeypatch):
    monkeypatch.setattr(stream_redis, "SSE_STREAM_TERMINAL_TTL_MS", 10800000)
    publisher = FakeRedis()
    bridge = stream_redis.RedisStreamBridge(publish_client=publisher)

    active = await bridge.append(_envelope())
    terminal = await bridge.append(
        _envelope(
            event_id="sev_terminal",
            event_type="terminal",
            payload={
                "event_id": "sev_terminal",
                "hydrate_required": True,
                "status": "succeeded",
            },
        ),
        terminal=True,
    )

    assert active.redis_id == "1700000000000-0"
    assert terminal.redis_id == "1700000000000-1"
    assert "XADD" in publisher.eval_calls[0][0]
    assert "PEXPIRE" in publisher.eval_calls[0][0]
    assert publisher.eval_calls[0][8] == 7200000
    assert publisher.eval_calls[1][8] == 10800000
    assert json.loads(publisher.eval_calls[0][7])["event_type"] == "stream_open"
    assert publisher.eval_calls[0][4] == stream_redis.stream_live_channel(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=1,
    )
    assert json.loads(publisher.published[0][1])["redis_id"] == active.redis_id

    duplicate = await bridge.append(_envelope())
    assert duplicate == active
    assert len(publisher.rows) == 2

    active_duplicate = await bridge.append(
        _envelope(
            event_id="sev_active",
            event_type="assistant_text_delta",
            payload={"delta": "same semantic identity"},
        )
    )
    active_duplicate_retry = await bridge.append(
        _envelope(
            event_id="sev_active",
            event_type="assistant_text_delta",
            payload={"delta": "same semantic identity"},
        )
    )
    assert active_duplicate_retry.redis_id != active_duplicate.redis_id


@pytest.mark.asyncio
async def test_default_bridge_uses_one_bounded_publish_pool(monkeypatch):
    created = []
    options = []

    def from_url(url, **kwargs):
        client = FakeRedis()
        created.append((url, kwargs["max_connections"], client))
        options.append(kwargs)
        return client

    monkeypatch.setattr(stream_redis.Redis, "from_url", from_url)
    monkeypatch.setattr(
        stream_redis,
        "get_settings",
        lambda: types.SimpleNamespace(redis_url="redis://redis:6379/0"),
    )

    bridge = stream_redis.RedisStreamBridge()
    assert [(url, maximum) for url, maximum, _ in created] == [
        ("redis://redis:6379/0", stream_redis.SSE_PUBLISH_MAX_CONNECTIONS),
    ]
    assert options[0]["socket_timeout"] == stream_redis._REDIS_PUBLISH_TIMEOUT_SECONDS
    await bridge.aclose()
    assert [client.close_calls for _, _, client in created] == [1]


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
    bridge = stream_redis.RedisStreamBridge(publish_client=publisher)

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
    assert not hasattr(stream_redis.RedisStreamBridge, "read")


@pytest.mark.asyncio
async def test_two_finite_replay_readers_receive_same_entries(monkeypatch):
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
    first_bridge = stream_redis.RedisStreamBridge(publish_client=publisher)
    second_bridge = stream_redis.RedisStreamBridge(publish_client=publisher)

    first_entries = await first_bridge.replay_page(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=1,
        after_redis_id="0-0",
        through_redis_id="1700000000040-0",
    )
    second_entries = await second_bridge.replay_page(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=1,
        after_redis_id="0-0",
        through_redis_id="1700000000040-0",
    )

    assert [entry.cursor.event_id for entry in first_entries] == [
        entry.cursor.event_id for entry in second_entries
    ]
    await first_bridge.aclose()
    await second_bridge.aclose()
    assert publisher.close_calls == 0
