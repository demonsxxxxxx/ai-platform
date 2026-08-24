from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import pytest
from redis.asyncio import Redis

from app.streaming.api import (
    V4ProjectionError,
    build_v4_control,
    opaque_message_id,
    project_public_envelope_v4,
    stream_end_event_id,
    stream_key,
)
from app.streaming.infrastructure.redis_live import RedisLiveFanoutSource

from app.streaming.contracts import STREAM_DESIGN_ID, canonical_json_bytes
from app.streaming.redis import (
    RedisStreamBridge,
    SseAuthorityConflictError,
    StreamAuthority,
    StreamContractError,
    StreamEnvelope,
    create_or_get_stream_admission_v4,
)
from app.streaming.v4 import (
    V4RedisStreamBridge,
    recover_v4_and_resume,
)
from app.streaming.worker_projection import publish_pending_v4_events
from tests.test_streaming_v4_postgres_integration import (
    _connection_factory as _pg_connection_factory,
    _insert_v4_row as _pg_insert_v4_row,
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
        self.rows.append((redis_id, {"envelope": str(args[7])}))
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


def test_v4_live_decode_rejects_corrupt_cross_version_and_cursor_identity() -> None:
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
            bridge.decode_live_publication(
                redis_id=redis_id,
                envelope_json=envelope_json,
                tenant_scope_value="scope-a",
                run_id="run-a",
                attempt_id="attempt-a",
                stream_incarnation=2,
            )


@pytest.mark.asyncio
async def test_v4_dot_controls_use_existing_lua_authority_and_heartbeat_has_no_cursor() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(control("stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}))
    assert client.calls[-1][9] == "stream_open"
    with pytest.raises(StreamContractError, match="v4_control_not_replayable"):
        await bridge.append(control("stream.heartbeat", {"status": "running"}))
    await bridge.publish_non_replayable(control("stream.heartbeat", {"status": "running"}))


@pytest.mark.asyncio
async def test_v4_nonreplayable_publication_uses_the_shared_live_parser_shape() -> None:
    client = FakeRedis()
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    await bridge.append(
        control(
            "stream.open",
            {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        )
    )
    envelope = control("stream.heartbeat", {"status": "running"})
    cursor = await bridge.publish_non_replayable(envelope)
    channel, publication = client.published[-1]
    parsed = RedisLiveFanoutSource._parse_publication(
        channel=str(channel), value=publication
    )
    assert parsed.redis_id == "1-0"
    assert cursor == "run-a:2:1-0"
    decoded = bridge.decode_live_publication(
        redis_id=parsed.redis_id,
        envelope_json=parsed.envelope_json,
        tenant_scope_value="scope-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
    )
    assert decoded.envelope["event_type"] == "stream.heartbeat"


@pytest.mark.asyncio
async def test_real_redis_v3_terminal_phase_does_not_reopen_after_stream_loss():
    redis_url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not redis_url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    client = Redis.from_url(redis_url, decode_responses=True)
    bridge = RedisStreamBridge(publish_client=client)
    key = stream_key(
        tenant_scope_value="scope-v3-no-reopen",
        run_id="run-v3-no-reopen",
        stream_incarnation=1,
    )
    opening = StreamEnvelope(
        event_id="open-v3-no-reopen",
        tenant_scope="scope-v3-no-reopen",
        run_id="run-v3-no-reopen",
        attempt_id="attempt-v3-no-reopen",
        stream_incarnation=1,
        event_type="stream_open",
        emitted_at="2026-01-01T00:00:00Z",
        payload={"design_id": STREAM_DESIGN_ID},
    )
    terminal = StreamEnvelope(
        event_id="terminal-v3-no-reopen",
        tenant_scope="scope-v3-no-reopen",
        run_id="run-v3-no-reopen",
        attempt_id="attempt-v3-no-reopen",
        stream_incarnation=1,
        event_type="terminal",
        emitted_at="2026-01-01T00:00:01Z",
        payload={
            "event_id": "terminal-v3-no-reopen",
            "hydrate_required": True,
            "status": "succeeded",
        },
    )
    try:
        await client.delete(key, f"{key}:state")
        opened = await bridge.append(opening)
        await bridge.append(terminal, terminal=True)
        await client.delete(key)
        assert await bridge.append(opening) == opened
        assert await client.xlen(key) == 0
        assert await client.hget(f"{key}:state", "phase") == "terminal"
    finally:
        await client.delete(key, f"{key}:state")
        await client.aclose()


@pytest.mark.asyncio
async def test_real_redis_v4_restore_rebuilds_all_rows_before_requested_window():
    redis_url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not redis_url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    client = Redis.from_url(redis_url, decode_responses=True)
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    key = stream_key(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=2,
    )

    class PagedConnection:
        def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
            self.rows = rows
            self.after_sequences: list[int] = []

        async def execute(self, _statement: str, params: tuple[object, ...]) -> Result:
            after = int(params[2])
            limit = int(params[3])
            self.after_sequences.append(after)
            return Result(
                tuple(
                    item
                    for item in self.rows
                    if int(item["sequence"]) > after
                )[:limit]
            )

    first = row()
    second = dict(first)
    second["id"] = "evt4_delta_second"
    second["sequence"] = 2
    second["payload_json"] = dict(first["payload_json"])
    second["payload_json"]["delta"] = "second"
    terminal = dict(first)
    terminal["id"] = "evt4_terminal_rebuild"
    terminal["sequence"] = 3
    terminal["event_type"] = "run.succeeded"
    terminal["payload_json"] = {
        "terminal_event_id": "evt4_terminal_rebuild",
        "hydrate_required": True,
        "__stream_v4": dict(first["payload_json"]["__stream_v4"]),
    }
    terminal["payload_json"]["__stream_v4"]["message_id"] = None
    terminal["payload_json"]["__stream_v4"]["attempt_id"] = "attempt-a"
    terminal["payload_json"]["__stream_v4"]["stream_incarnation"] = 2
    terminal["payload_json"]["__stream_v4"]["publication_state"] = "published"
    terminal["stream_publication_state"] = "published"
    rejected_rows: list[dict[str, object]] = []
    for sequence in range(3, 259):
        rejected = dict(first)
        rejected["id"] = f"evt4_stale_{sequence}"
        rejected["sequence"] = sequence
        rejected_payload = dict(first["payload_json"])
        rejected_metadata = dict(rejected_payload["__stream_v4"])
        rejected_metadata["attempt_id"] = "attempt-stale"
        rejected_payload["__stream_v4"] = rejected_metadata
        rejected["payload_json"] = rejected_payload
        rejected_rows.append(rejected)
    terminal["sequence"] = 259
    connection = PagedConnection((first, second, *rejected_rows, terminal))
    opening = control(
        "stream.open", {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
    )
    try:
        await client.delete(key, f"{key}:state")
        await bridge.append(opening)
        await client.delete(key)
        recovered = await recover_v4_and_resume(
            connection,
            bridge=bridge,
            tenant_id="tenant-a",
            run_id="run-a",
            authority=authority(),
            after_sequence=2,
            limit=1,
        )
        assert connection.after_sequences == [0, 256]
        assert [item["event_id"] for item in recovered.rows] == [
            "evt4_terminal_rebuild"
        ]
        rows = await client.xrange(key, min="-", max="+")
        assert len(rows) == 5
        assert json.loads(rows[1][1]["envelope"])["event_id"] == "evt4_delta"
        assert json.loads(rows[2][1]["envelope"])["event_id"] == "evt4_delta_second"
        assert json.loads(rows[-1][1]["envelope"])["event_type"] == "stream.end"
        assert await client.hget(f"{key}:state", "phase") == "terminal"
    finally:
        await client.delete(key, f"{key}:state")
        await client.aclose()


@pytest.mark.asyncio
async def test_v4_recovery_reads_published_rows_only() -> None:
    conn = Connection((row(),))
    published: list[dict[str, object]] = []

    class Bridge:
        async def ensure_open(self, _payload: str) -> str:
            return "6-0"

        async def append(self, envelope: dict[str, object]) -> str:
            published.append(envelope)
            return "7-0"

    recovery = await recover_v4_and_resume(
        conn,
        bridge=Bridge(),
        tenant_id="tenant-a",
        run_id="run-a",
        authority=authority(),
        after_sequence=0,
        limit=8,
    )
    assert "stream_publication_state = 'published'" in conn.statements[0]
    assert len(published) == 1
    assert recovery.transport_cursors == ("7-0",)
    assert project_public_envelope_v4(published[0]) is not None


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
async def test_real_pending_terminal_end_partial_retry_keeps_row_pending() -> None:
    async with _pg_schema() as (dsn, schema_name, (tenant, run, attempt)):
        terminal_id = "evt4_terminal_end_retry"
        async with _pg_connection_factory(dsn, schema_name) as conn:
            await conn.execute("update runs set status = 'succeeded' where id = %s", (run,))
            await conn.execute(
                "update sse_stream_authorities set state = 'terminal' where tenant_id = %s and run_id = %s",
                (tenant, run),
            )
            await _pg_insert_v4_row(
                conn,
                tenant=tenant,
                run=run,
                attempt=attempt,
                sequence=1,
                event_id=terminal_id,
                event_type="run.succeeded",
                payload={"terminal_event_id": terminal_id, "hydrate_required": True},
            )
        client, key, _ = await _pg_redis_stream(tenant, run)

        class FailEndRedis:
            def __init__(self, delegate) -> None:
                self.delegate = delegate
                self.fail_end_once = True
                self.calls: list[tuple[str, str]] = []

            def __getattr__(self, name: str):
                return getattr(self.delegate, name)

            async def eval(self, *args: object):
                transport_type = str(args[9])
                event_id = str(args[6])
                self.calls.append((transport_type, event_id))
                if transport_type == "end" and self.fail_end_once:
                    self.fail_end_once = False
                    raise RuntimeError("deterministic end unavailable")
                return await self.delegate.eval(*args)

        failing_client = FailEndRedis(client)
        bridge = V4RedisStreamBridge(
            RedisStreamBridge(publish_client=failing_client)
        )
        try:
            assert await publish_pending_v4_events(
                lambda: _pg_connection_factory(dsn, schema_name),
                limit=1,
                bridge=bridge,
            ) == 0
            async with _pg_connection_factory(dsn, schema_name) as conn:
                result = await conn.execute(
                    """
                    select stream_publication_state, stream_publication_redis_id
                    from run_events where id = %s
                    """,
                    (terminal_id,),
                )
                pending = await result.fetchone()
            assert pending == {
                "stream_publication_state": "pending",
                "stream_publication_redis_id": None,
            }
            partial_rows = await client.xrange(key, min="-", max="+")
            assert [json.loads(fields["envelope"])["event_id"] for _, fields in partial_rows] == [
                terminal_id
            ]

            async with _pg_connection_factory(dsn, schema_name) as conn:
                await conn.execute(
                    "update run_events set stream_publication_next_attempt_at = now() where id = %s",
                    (terminal_id,),
                )
            assert await publish_pending_v4_events(
                lambda: _pg_connection_factory(dsn, schema_name),
                limit=1,
                bridge=bridge,
            ) == 1
            async with _pg_connection_factory(dsn, schema_name) as conn:
                result = await conn.execute(
                    """
                    select stream_publication_state, stream_publication_redis_id
                    from run_events where id = %s
                    """,
                    (terminal_id,),
                )
                published_row = await result.fetchone()
            rows = await client.xrange(key, min="-", max="+")
            envelopes = [json.loads(fields["envelope"]) for _, fields in rows]
            end_id = stream_end_event_id(terminal_id)
            assert [item["event_id"] for item in envelopes] == [terminal_id, end_id]
            assert [item["event_type"] for item in envelopes] == [
                "run.succeeded",
                "stream.end",
            ]
            assert failing_client.calls == [
                ("terminal", terminal_id),
                ("end", end_id),
                ("terminal", terminal_id),
                ("end", end_id),
            ]
            assert published_row == {
                "stream_publication_state": "published",
                "stream_publication_redis_id": rows[-1][0],
            }
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
            if args[9] == "end" and self.fail_end_once:
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
        "source": {"kind": "terminal_intent", "terminal_event_id": "evt4_terminal_pair"},
    }
    with pytest.raises(Exception, match="stream_append_unavailable"):
        await bridge.append(terminal)
    result = await bridge.append(terminal)
    assert result == "1-3"
    assert [call[9] for call in client.calls] == ["stream_open", "terminal", "terminal", "end"]
    assert [call[6] for call in client.attempts] == [
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
            bridge.decode_live_publication(
                redis_id="1-0",
                envelope_json=json.dumps(foreign),
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
async def test_real_redis_rebuilds_open_after_event_stream_loss() -> None:
    redis_url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not redis_url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    client = Redis.from_url(redis_url, decode_responses=True)
    key = stream_key(
        tenant_scope_value="scope-a",
        run_id="run-a",
        stream_incarnation=2,
    )
    bridge = V4RedisStreamBridge(RedisStreamBridge(publish_client=client))
    opening = control(
        "stream.open",
        {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
    )
    try:
        await client.delete(key, f"{key}:state")
        await bridge.append(opening)
        await client.delete(key)
        await bridge.ensure_open(canonical_json_bytes(opening))
        rows = await client.xrange(key, min="-", max="+")
        assert len(rows) == 1
        assert json.loads(rows[0][1]["envelope"])["event_type"] == "stream.open"
    finally:
        await client.delete(key, f"{key}:state")
        await client.aclose()
