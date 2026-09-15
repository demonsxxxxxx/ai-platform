from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.streaming.api import (
    V4ProjectionError,
    build_v4_control,
    opaque_message_id,
    project_public_envelope_v4,
    stream_end_event_id,
    stream_key,
)
from app.streaming import api as streaming_api
from app.streaming.infrastructure import v4 as streaming_v4
from app.streaming.domain import public_events_v4
from tests.support.db_transactions import event_loop_policy as event_loop_policy

from app.streaming.api import canonical_json_bytes
from app.streaming.application.worker_publication_v4 import publish_run_event
from app.streaming.redis import (
    RedisStreamBridge,
    SseAuthorityConflictError,
    StreamAuthority,
    StreamContractError,
    create_or_get_stream_admission_v4,
)
from app.streaming.infrastructure.v4 import V4RedisStreamBridge
from tests.test_streaming_v4_postgres_integration import (
    _connection_factory as _pg_connection_factory,
    _callback_capabilities as _pg_capabilities,
    _redis_stream as _pg_redis_stream,
    _schema as _pg_schema,
)


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.rows: list[tuple[str, dict[str, str]]] = []
        self.published: list[tuple[object, object]] = []

    async def eval(self, *args: object) -> str:
        self.calls.append(args)
        redis_id = f"1-{len(self.rows)}"
        self.rows.append((redis_id, {"envelope": str(args[6])}))
        return redis_id

    async def xrange(
        self,
        _key: object,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ):
        rows = self.rows
        if min.startswith("("):
            rows = [item for item in rows if item[0] > min[1:]]
        elif min not in {"-", "+"}:
            rows = [item for item in rows if item[0] == min]
        if max not in {"+", "-"}:
            rows = [item for item in rows if item[0] <= max]
        return rows[:count] if count is not None else rows

    async def xread(self, streams, *, count, block):
        self.calls.append(("xread", streams, count, block))
        key, after = next(iter(streams.items()))
        rows = await self.xrange(key, min=f"({after}", max="+", count=count)
        return [(key, rows)] if rows else []

    async def xrevrange(
        self,
        _key: object,
        max: str = "+",
        min: str = "-",
        count: int | None = None,
    ):
        rows = list(reversed(self.rows))
        return rows[:count] if count is not None else rows

    async def publish(self, *args: object) -> int:
        self.published.append(args)
        return 1


class Result:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self.rows = rows

    async def fetchall(self) -> tuple[dict[str, object], ...]:
        return self.rows


class Connection:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self._page_returned = False

    async def execute(self, statement: str, _params: object) -> Result:
        self.statements.append(statement)
        if self._page_returned:
            return Result(())
        self._page_returned = True
        return Result(self.rows)


def _open_payload() -> str:
    return canonical_json_bytes(
        build_v4_control(
            event_id="open-a",
            tenant_scope="scope-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
            event_type="stream.open",
            payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
            source={"kind": "stream_authority", "authority_id": "open-a"},
            emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ).decode()


def authority() -> StreamAuthority:
    payload = _open_payload()
    return StreamAuthority(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=2,
        state="confirmed",
        open_event_id="open-a",
        open_payload_bytes=payload,
        open_payload_digest=hashlib.sha256(payload.encode()).hexdigest(),
        authorization_epoch=3,
        revocation_state="active",
    )


def control(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return build_v4_control(
        event_id=f"evt-{event_type.replace('.', '-')}",
        tenant_scope="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        event_type=event_type,
        payload=payload,
        source={"kind": "stream_authority", "authority_id": "open-a"},
        emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def row() -> dict[str, object]:
    return {
        "id": "evt4_delta",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "sequence": 1,
        "event_type": "message.delta",
        "visible_to_user": True,
        "payload_json": {
            "delta": "hello",
            "__stream_v4": {
                "attempt_id": "attempt-a",
                "version": 1,
                "stream_incarnation": 2,
                "authorization_epoch": 3,
                "message_id": opaque_message_id("tenant-a", "run-a"),
                "publication_state": "published",
            },
        },
        "stream_publication_state": "published",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def test_v4_same_incarnation_recovery_capabilities_are_absent() -> None:
    assert not hasattr(streaming_v4, "rebind_v4_incarnation")
    assert not hasattr(streaming_api, "_row_for_current_authority")
    assert not hasattr(public_events_v4, "_row_for_current_authority")


def test_v4_controls_are_strict_and_public_projection_preserves_replayability() -> None:
    controls = (
        control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}),
        control("stream.heartbeat", {"status": "running"}),
        control(
            "stream.gap",
            {
                "reason": "retained_history_unavailable",
                "recovery": "reload_durable_state",
                "requested_event_id": "2-0",
                "requested_stream_incarnation": 2,
                "current_stream_incarnation": 2,
                "earliest_available_event_id": "3-0",
                "latest_available_event_id": "9-0",
            },
        ),
        control("stream.end", {"terminal_event_id": "terminal-a"}),
    )
    assert [project_public_envelope_v4(item)["replayable"] for item in controls] == [True, False, False, True]
    assert project_public_envelope_v4(controls[0])["schema"] == "ai-platform.public-run-stream-control.v4"


def test_v4_controls_reject_malformed_expanded_and_private_payloads() -> None:
    valid = control("stream.heartbeat", {"status": "running"})
    for payload in (
        {"status": "running", "private": "secret"},
        {"status": "invalid"},
        {"status": "running", "raw_command": "rm -rf"},
    ):
        invalid = dict(valid)
        invalid["payload"] = payload
        assert project_public_envelope_v4(invalid) is None


def test_v4_decode_normalizes_mapping_and_lua_field_value_list() -> None:
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=FakeRedis()))
    envelope = control(
        "stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
    )
    raw = canonical_json_bytes(envelope).decode()

    for fields in (
        {"envelope": raw},
        ["envelope", raw],
        ("envelope", raw),
    ):
        decoded = bridge._decode(
            ("1-0", fields),
            tenant_scope_value="scope-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
        )
        assert decoded.envelope == envelope


@pytest.mark.asyncio
async def test_v4_read_stream_uses_exclusive_xread_and_shared_authority_decode() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    envelope = control(
        "stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
    )
    client.rows.append(("1-0", {"envelope": canonical_json_bytes(envelope).decode()}))

    entries = await bridge.read_stream(
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        after_redis_id="0-0",
        count=7,
        block_ms=4500,
    )

    key = stream_key(
        tenant_scope_value="scope-a", run_id="run-a", stream_incarnation=2
    )
    assert [entry.cursor.redis_id for entry in entries] == ["1-0"]
    assert entries[0].envelope == envelope
    assert client.calls[-1] == ("xread", {key: "0-0"}, 7, 4500)


@pytest.mark.asyncio
async def test_v4_read_stream_rejects_foreign_entry_authority() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    envelope = control(
        "stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
    )
    foreign = dict(envelope)
    foreign["attempt_id"] = "attempt-foreign"
    client.rows.append(("1-0", {"envelope": canonical_json_bytes(foreign).decode()}))

    with pytest.raises(StreamContractError, match="v4_stream_authority_mismatch"):
        await bridge.read_stream(
            tenant_scope_value="scope-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
            after_redis_id="0-0",
        )


def test_v4_decode_rejects_malformed_redis_rows_and_fields() -> None:
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=FakeRedis()))
    envelope = control(
        "stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
    )
    raw = canonical_json_bytes(envelope).decode()
    invalid_fields = (
        (["envelope"], "v4_stream_fields_invalid"),
        (["envelope", raw, "envelope", raw], "v4_stream_fields_duplicate"),
        (["envelope", raw, 1, raw], "v4_stream_fields_invalid"),
        ({"envelope": b"not-text"}, "v4_stream_fields_invalid"),
        (["other", raw], "v4_stream_envelope_missing"),
    )

    for fields, error in invalid_fields:
        with pytest.raises(StreamContractError, match=error):
            bridge._decode(
                ("1-0", fields),
                tenant_scope_value="scope-a",
                run_id="run-a",
                attempt_id="attempt-a",
                stream_incarnation=2,
            )

    for malformed_row in ((), ("1-0",), ("1-0", {}, "extra"), "1-0"):
        with pytest.raises(StreamContractError, match="v4_stream_row_invalid"):
            bridge._decode(
                malformed_row,
                tenant_scope_value="scope-a",
                run_id="run-a",
                attempt_id="attempt-a",
                stream_incarnation=2,
            )


def test_v4_stream_decode_rejects_corrupt_cross_version_and_cursor_identity() -> None:
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=FakeRedis()))
    valid = control(
        "stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
    )
    cases = [
        ("not-json", "1-0"),
        (json.dumps({**valid, "schema": "ai-platform.public-run-stream-event.v3"}), "1-0"),
        (json.dumps(valid), "bad-cursor"),
    ]
    for envelope_json, redis_id in cases:
        with pytest.raises((StreamContractError, V4ProjectionError)):
            bridge._decode(
                (redis_id, {"envelope": envelope_json}),
                tenant_scope_value="scope-a",
                run_id="run-a",
                attempt_id="attempt-a",
                stream_incarnation=2,
            )


@pytest.mark.asyncio
async def test_v4_append_uses_stream_only_lua_authority() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}))
    script, key_count, *_ = client.calls[0]
    assert key_count == 2
    assert "PUBLISH" not in script.upper()
    assert "KEYS[3]" not in script


@pytest.mark.asyncio
async def test_v4_dot_controls_use_existing_lua_authority_and_heartbeat_has_no_cursor() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}))
    assert client.calls[-1][8] == "stream_open"
    with pytest.raises(StreamContractError, match="v4_control_not_replayable"):
        await bridge.append(control("stream.heartbeat", {"status": "running"}))
    assert not hasattr(bridge, "publish_non_replayable")
    assert not hasattr(bridge, "publish_gap")


@pytest.mark.asyncio
async def test_real_v4_admission_persists_and_revalidates_canonical_open() -> None:
    async with _pg_schema() as (dsn, schema_name, (tenant, run, attempt)):
        tenant_scope = f"scope_{tenant[2:]}"
        async with _pg_connection_factory(dsn, schema_name) as conn:
            await conn.execute(
                "delete from sse_stream_authorities where tenant_id = %s and run_id = %s",
                (tenant, run),
            )
            first = await create_or_get_stream_admission_v4(
                conn,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                tenant_scope=tenant_scope,
            )
            second = await create_or_get_stream_admission_v4(
                conn,
                tenant_id=tenant,
                run_id=run,
                attempt_id=attempt,
                tenant_scope=tenant_scope,
            )
        assert second.open_event_id == first.open_event_id
        assert second.open_payload_bytes == first.open_payload_bytes
        async with _pg_connection_factory(dsn, schema_name) as conn:
            result = await conn.execute(
                """
                select design_id, projection_version, tenant_scope,
                       stream_incarnation, state, open_event_id,
                       open_payload_bytes, open_payload_digest
                from sse_stream_authorities
                where tenant_id = %s and run_id = %s
                """,
                (tenant, run),
            )
            persisted = await result.fetchone()
        assert persisted is not None
        assert persisted["design_id"] == "ai-platform.redis-streams-sse-event-channel.v4"
        assert persisted["projection_version"] == "public-stream-v4"
        assert persisted["tenant_scope"] == tenant_scope
        assert persisted["stream_incarnation"] == 1
        assert persisted["state"] == "admission_pending"
        assert persisted["open_event_id"] == first.open_event_id
        assert persisted["open_payload_bytes"] == first.open_payload_bytes
        assert persisted["open_payload_digest"] == hashlib.sha256(
            first.open_payload_bytes.encode()
        ).hexdigest()


@pytest.mark.asyncio
async def test_real_terminal_end_partial_retry_preserves_facts_and_receipts():
    async with _pg_schema() as (dsn, schema_name, (tenant, run, _)):
        async with _pg_connection_factory(dsn, schema_name) as conn:
            await conn.execute("update runs set status = 'succeeded' where id = %s", (run,))
        client, key, _ = await _pg_redis_stream(tenant, run)

        class FailEndRedis:
            def __init__(self, delegate):
                self.delegate = delegate
                self.fail_end_once = True
                self.calls = []

            def __getattr__(self, name):
                return getattr(self.delegate, name)

            async def eval(self, *args):
                kind, event_id = str(args[8]), str(args[5])
                self.calls.append((kind, event_id))
                if kind == "end" and self.fail_end_once:
                    self.fail_end_once = False
                    raise RuntimeError("deterministic end unavailable")
                return await self.delegate.eval(*args)

        failing = FailEndRedis(client)
        bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=failing))
        capabilities = _pg_capabilities(dsn, schema_name, bridge=bridge)
        try:
            payload = await capabilities.event_persistence.load_latest_run_event(tenant_id=tenant, run_id=run)
            terminal_id = json.loads(payload)["event_id"]
            async with _pg_connection_factory(dsn, schema_name) as conn:
                before = await (await conn.execute("select * from run_events order by sequence")).fetchall()
            assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is False
            partial = await client.xrange(key)
            assert [json.loads(fields["envelope"])["event_type"] for _, fields in partial] == ["stream.open", "run.succeeded"]
            assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is True
            async with _pg_connection_factory(dsn, schema_name) as conn:
                assert await (await conn.execute("select * from run_events order by sequence")).fetchall() == before
            entries = await client.xrange(key)
            assert entries[:2] == partial
            assert json.loads(entries[-1][1]["envelope"])["event_id"] == stream_end_event_id(terminal_id)
            assert len(entries) == 3
            assert failing.calls == [("terminal", terminal_id), ("end", stream_end_event_id(terminal_id))] * 2
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_real_expired_terminal_stream_preserves_visible_business_facts():
    async with _pg_schema() as (dsn, schema_name, (tenant, run, _)):
        async with _pg_connection_factory(dsn, schema_name) as conn:
            await conn.execute("update runs set status = 'failed' where id = %s", (run,))
            await conn.execute("update sandbox_leases set status = 'released', expires_at = now() where run_id = %s", (run,))
            assert await (await conn.execute("select id from run_attempts")).fetchone() is None
        client, key, bridge = await _pg_redis_stream(tenant, run)
        try:
            await client.delete(key, f"{key}:state")
            capabilities = _pg_capabilities(dsn, schema_name, bridge=bridge)
            assert await publish_run_event(capabilities, tenant_id=tenant, run_id=run) is False
            async with _pg_connection_factory(dsn, schema_name) as conn:
                facts = await (await conn.execute("select * from run_events where event_type = 'run.failed'")).fetchall()
                assert await (await conn.execute("select status from runs where id = %s", (run,))).fetchone() == {"status": "failed"}
            assert len(facts) == 1 and facts[0]["visible_to_user"] is True
            assert "stream_publication_state" not in facts[0]
            assert "suppression_reason" not in facts[0]["payload_json"]["__stream_v4"]
            assert await client.exists(key, f"{key}:state") == 0
        finally:
            await client.delete(key, f"{key}:state")
            await client.aclose()


@pytest.mark.asyncio
async def test_terminal_append_requires_the_deterministic_end_pair_before_success() -> None:
    class PairRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.fail_end_once = True
            self.attempts: list[tuple[object, ...]] = []

        async def eval(self, *args: object) -> str:
            self.attempts.append(args)
            if args[8] == "end" and self.fail_end_once:
                self.fail_end_once = False
                raise RuntimeError("end unavailable")
            return await super().eval(*args)

    client = PairRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(
        control(
            "stream.open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        )
    )
    terminal = {
        "schema": "ai-platform.stream-event.v4",
        "event_id": "evt4_terminal_pair",
        "tenant_scope": "scope-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "message_id": None,
        "seq": 2,
        "event_type": "run.succeeded",
        "stream_incarnation": 2,
        "replayable": True,
        "trace_ref": None,
        "causation_event_id": None,
        "emitted_at": "2026-01-01T00:00:00Z",
        "projection_version": "public-stream-v4",
        "payload": {"terminal_event_id": "evt4_terminal_pair", "hydrate_required": True},
        "source": {"kind": "run_event", "run_event_id": "evt4_terminal_pair", "sequence": 2},
    }
    with pytest.raises(Exception, match="stream_append_unavailable"):
        await bridge.append(terminal)
    result = await bridge.append(terminal)
    assert result == "1-3"
    assert [call[8] for call in client.calls] == ["stream_open", "terminal", "terminal", "end"]
    assert [call[5] for call in client.attempts] == [
        "evt-stream-open",
        "evt4_terminal_pair",
        stream_end_event_id("evt4_terminal_pair"),
        "evt4_terminal_pair",
        stream_end_event_id("evt4_terminal_pair"),
    ]


def test_v4_decode_rejects_foreign_tenant_and_attempt() -> None:
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=FakeRedis()))
    envelope = control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"})
    for field, value in (("tenant_scope", "scope-foreign"), ("attempt_id", "attempt-foreign")):
        foreign = dict(envelope)
        foreign[field] = value
        with pytest.raises(StreamContractError, match="v4_stream_authority_mismatch"):
            bridge._decode(
                ("1-0", {"envelope": json.dumps(foreign)}),
                tenant_scope_value="scope-a",
                run_id="run-a",
                attempt_id="attempt-a",
                stream_incarnation=2,
            )


@pytest.mark.asyncio
async def test_v4_heartbeat_and_gap_use_real_retained_cursor_bounds() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(
        control(
            "stream.open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        )
    )
    heartbeat, heartbeat_cursor = await bridge.build_heartbeat(
        event_id="hb4_a",
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        status="running",
    )
    gap, gap_cursor = await bridge.build_gap(
        event_id="gap4_a",
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        requested_event_id="run-a:2:1-0",
        requested_stream_incarnation=2,
        current_stream_incarnation=2,
        reason="retained_history_unavailable",
    )
    assert heartbeat_cursor == "run-a:2:1-0"
    assert gap_cursor == heartbeat_cursor
    assert heartbeat["payload"] == {"status": "running"}
    assert gap["payload"]["earliest_available_event_id"] == "1-0"
    assert gap["payload"]["latest_available_event_id"] == "1-0"
    assert gap["payload"]["requested_event_id"] == "1-0"


@pytest.mark.asyncio
async def test_v4_missing_stream_gap_uses_start_cursor_and_null_bounds() -> None:
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=FakeRedis()))

    gap, gap_cursor = await bridge.build_gap(
        event_id="gap4_missing",
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        requested_event_id=None,
        requested_stream_incarnation=None,
        current_stream_incarnation=2,
        reason="stream_missing",
    )

    assert gap_cursor == "run-a:2:0-0"
    assert gap["event_type"] == "stream.gap"
    assert gap["payload"] == {
        "reason": "stream_missing",
        "recovery": "reload_durable_state",
        "requested_event_id": None,
        "requested_stream_incarnation": None,
        "current_stream_incarnation": 2,
        "earliest_available_event_id": None,
        "latest_available_event_id": None,
    }


@pytest.mark.asyncio
async def test_existing_v4_authority_requires_canonical_digest_and_identity() -> None:
    payload = _open_payload()
    base = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "tenant_scope": "scope-a",
        "stream_incarnation": 2,
        "state": "confirmed",
        "open_event_id": "open-a",
        "open_payload_bytes": payload,
        "open_payload_digest": hashlib.sha256(payload.encode()).hexdigest(),
        "authorization_epoch": 3,
        "revocation_state": "active",
        "design_id": "ai-platform.redis-streams-sse-event-channel.v4",
        "projection_version": "public-stream-v4",
    }

    class Result:
        async def fetchone(self):
            return dict(base)

    class Connection:
        async def execute(self, *_args: object):
            return Result()

    assert (await create_or_get_stream_admission_v4(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
    )).open_event_id == "open-a"

    with pytest.raises(SseAuthorityConflictError, match="sse_stream_attempt_conflict"):
        await create_or_get_stream_admission_v4(
            Connection(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-b",
            tenant_scope="scope-a",
        )

    for override in ({"open_payload_digest": "wrong"}, {"open_event_id": "other"}):
        base.update(override)
        with pytest.raises(SseAuthorityConflictError, match="sse_stream_protocol_conflict"):
            await create_or_get_stream_admission_v4(
                Connection(),
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                tenant_scope="scope-a",
            )
        base.update({key: value for key, value in {
            "open_payload_digest": hashlib.sha256(payload.encode()).hexdigest(),
            "open_event_id": "open-a",
        }.items()})


@pytest.mark.asyncio
async def test_v4_replay_page_fails_closed_when_atomic_predecessor_is_trimmed():
    class TrimmedRedis:
        calls: list[tuple[object, ...]] = []

        async def eval(self, *args: object):
            self.calls.append(args)
            return [0, []]

    client = TrimmedRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))

    with pytest.raises(StreamContractError, match="stream_replay_continuity_unproven"):
        await bridge.replay_page(
            tenant_scope_value="scope-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
            after_redis_id="1-0",
            through_redis_id="2-0",
        )

    assert client.calls[0][6] == "1-0"
