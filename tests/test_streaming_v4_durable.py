from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest

from app.runs.api import RunTerminalEventFact
from app.streaming.application.durable_v4 import (
    V4PublicationStreamExpired,
    V4PublicationTransportUnavailable,
    V4PendingAdmission,
)

from app.streaming.application.worker_publication_v4 import (
    WorkerV4Capabilities,
    admit_v4_stream,
    finalize_parent_and_publish,
)

from app.streaming.api import (
    V4ProjectionError,
    build_v4_control,
    opaque_message_id,
    project_persisted_message_delta_v4,
    project_public_envelope_v4,
    project_public_v4,
    validate_public_application_payload_v4,
)
from app.streaming.authority import RunCursor
from app.streaming.postgres import EventReceipt
from app.streaming.redis import StreamAuthority
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.infrastructure import run_v4_events, worker_v4
from app.streaming.infrastructure.v4 import (
    V4RedisStreamBridge,
)


def test_callback_v4_values_have_one_application_owner():
    from app.routes import runtime_callbacks
    from app.streaming import api
    from app.streaming.application import callback_events_v4
    from app.streaming.infrastructure import v4

    assert api.V4CallbackItem is callback_events_v4.V4CallbackItem
    assert v4.V4CallbackItem is callback_events_v4.V4CallbackItem
    assert api.callback_item_to_v4 is callback_events_v4.callback_item_to_v4
    assert v4.callback_item_to_v4 is callback_events_v4.callback_item_to_v4
    assert (
        runtime_callbacks.callback_item_to_v4 is callback_events_v4.callback_item_to_v4
    )


@pytest.mark.asyncio
async def test_pending_admission_locks_run_before_stream_authority(monkeypatch):
    calls: list[str] = []

    class Cursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

    class Transaction:
        async def execute(self, statement, params):
            normalized = " ".join(statement.split()).lower()
            if "from runs" in normalized:
                assert "for update" in normalized
                assert params == ("tenant-a", "run-a")
                calls.append("run_lock")
                return Cursor({"id": "run-a", "status": "queued"})
            if normalized.startswith("select * from sse_stream_authorities"):
                assert "for update" in normalized
                assert params == ("tenant-a", "run-a")
                calls.append("stream_authority_lock")
                return Cursor(None)
            assert normalized.startswith("insert into sse_stream_authorities")
            calls.append("stream_authority_insert")
            return Cursor(
                {
                    "tenant_id": params[0],
                    "run_id": params[1],
                    "attempt_id": params[2],
                    "tenant_scope": params[5],
                    "stream_incarnation": params[6],
                    "state": "admission_pending",
                    "open_event_id": params[7],
                    "open_payload_bytes": params[8],
                    "open_payload_digest": params[9],
                    "authorization_epoch": 1,
                    "revocation_state": "active",
                }
            )

    monkeypatch.setattr(worker_v4, "tenant_scope", lambda *_args, **_kwargs: "scope-a")
    adapter = worker_v4.PostgresV4PendingAdmissions(
        object(),
        authority_secret="test-v4-authority-secret",
    )

    pending = await adapter.prepare_pending_authority_in_transaction(
        Transaction(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
    )

    assert pending.open_event_id
    assert calls == ["run_lock", "stream_authority_lock", "stream_authority_insert"]


@pytest.mark.asyncio
async def test_terminal_row_uses_scoped_authority_after_locking_the_run_fact(monkeypatch):
    calls = []
    identities = []
    conn = object()

    async def load_terminal_fact(observed_conn, *, tenant_id, run_id):
        assert observed_conn is conn
        assert (tenant_id, run_id) == ("tenant-a", "run-a")
        calls.append("run_fact")
        return RunTerminalEventFact(status="failed", terminal_reason="queue_enqueue_failed", error_code="queue_enqueue_failed", trace_ref="trace-run-a")

    async def get_authority(observed_conn, *, tenant_id, run_id, for_update):
        assert observed_conn is conn and for_update is True
        calls.append("stream_authority")
        return replace(_authority(), attempt_id="attempt-active")

    async def append_terminal_row(observed_conn, **kwargs):
        assert observed_conn is conn
        assert kwargs["attempt_id"] == "attempt-active"
        assert kwargs["terminal_event_id"].startswith("evt4_run_")
        identities.append(kwargs["terminal_event_id"])
        calls.append("terminal_row")
        return {"id": kwargs["terminal_event_id"]}

    monkeypatch.setattr(run_v4_events, "get_stream_authority", get_authority)
    monkeypatch.setattr(run_v4_events._v4, "append_run_terminal_v4_row", append_terminal_row)
    for _ in range(2):
        await run_v4_events.append_current_run_terminal_v4_row(conn, tenant_id="tenant-a", run_id="run-a", load_terminal_event_fact=load_terminal_fact)
    assert calls == ["run_fact", "stream_authority", "terminal_row"] * 2
    assert identities[0] == identities[1]


def _callback_conn():
    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.rows = {}
            self.statements = []

        async def execute(self, statement, params):
            self.statements.append((statement, params))
            normalized = " ".join(statement.lower().split())
            event_id = params[-1]
            row = self.rows.get(event_id)
            if normalized.startswith("select id"):
                return Cursor(row)
            raise AssertionError(statement)

    return Connection()


@pytest.mark.asyncio
async def test_callback_rows_do_not_enqueue_or_notify_a_publisher(monkeypatch):
    from app.streaming.infrastructure import v4

    conn = _callback_conn()
    authority = _authority()
    item = v4.V4CallbackItem(
        callback_index=0, batch_index=0, event_type="message.delta",
        payload={"delta": "hello"}, message_id=opaque_message_id("tenant-a", "run-a"),
    )

    async def append_event(conn, *, tenant_id, run_id, event, event_id):
        return EventReceipt(event_id, RunCursor(run_id, 9), "2026-01-01T00:00:00Z")

    monkeypatch.setattr(v4.postgres, "append_event", append_event)
    rows = await v4.append_callback_v4_rows(
        conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a",
        batch_id="batch-direct", items=(item,), authority=authority,
        execution_lease_id="lease-a",
    )
    assert "stream_publication_state" not in rows[0]
    assert not any("pg_notify" in sql or "update run_events" in sql.lower() for sql, _ in conn.statements)


@pytest.mark.asyncio
async def test_callback_v4_rows_are_atomic_and_idempotent_per_batch_item(monkeypatch):
    from app.streaming.infrastructure import v4

    conn = _callback_conn()
    append_calls = []

    async def append_event(conn, *, tenant_id, run_id, event, event_id):
        append_calls.append(event_id)
        conn.rows[event_id] = {
            "id": event_id,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "sequence": 9,
            "event_type": event.event_type,
            "visible_to_user": True,
            "payload_json": dict(event.payload),
            "created_at": "2026-01-01T00:00:00Z",
        }
        return EventReceipt(event_id, RunCursor(run_id, 9), "2026-01-01T00:00:00Z")

    monkeypatch.setattr(v4.postgres, "append_event", append_event)
    authority = _authority()
    item = v4.V4CallbackItem(
        callback_index=0,
        batch_index=1,
        event_type="message.delta",
        payload={"delta": "hello"},
        message_id=opaque_message_id("tenant-a", "run-a"),
    )

    async def exercise():
        first = await v4.append_callback_v4_rows(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id="batch-a",
            items=(item,),
            authority=authority,
            execution_lease_id="lease-a",
        )
        second = await v4.append_callback_v4_rows(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id="batch-a",
            items=(item,),
            authority=authority,
            execution_lease_id="lease-a",
        )
        return first, second

    first, second = await exercise()
    assert first[0]["id"] == second[0]["id"]
    assert len(append_calls) == 1
    assert not any("pg_notify" in sql or "update run_events" in sql.lower() for sql, _ in conn.statements)
    assert "stream_publication_state" not in first[0]

    conn.rows[first[0]["id"]]["payload_json"]["delta"] = "tampered"
    with pytest.raises(v4.V4ProjectionError, match="existing_row_conflict"):
        await v4.append_callback_v4_rows(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id="batch-a",
            items=(item,),
            authority=authority,
            execution_lease_id="lease-a",
        )
    assert len(append_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("callback_batch_id", "batch-tampered"),
        ("callback_index", 9),
        ("batch_index", 9),
        ("attempt_id", "attempt-tampered"),
        ("execution_lease_id", "lease-tampered"),
        ("message_id", "msg_tampered"),
        ("source_event_id", "source-event-tampered"),
        ("source_run_id", "run-tampered"),
        ("trace_ref", "trace-tampered"),
        ("causation_event_id", "cause-tampered"),
    ],
)
async def test_callback_v4_existing_row_rejects_each_immutable_callback_fact(
    field, value, monkeypatch
):
    from app.streaming.infrastructure import v4

    conn = _callback_conn()
    append_calls = []

    async def append_event(conn, *, tenant_id, run_id, event, event_id):
        append_calls.append(event_id)
        conn.rows[event_id] = {
            "id": event_id,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "sequence": 9,
            "event_type": event.event_type,
            "visible_to_user": True,
            "payload_json": dict(event.payload),
            "created_at": "2026-01-01T00:00:00Z",
        }
        return EventReceipt(event_id, RunCursor(run_id, 9), "2026-01-01T00:00:00Z")

    monkeypatch.setattr(v4.postgres, "append_event", append_event)
    authority = _authority()
    item = v4.V4CallbackItem(
        callback_index=0,
        batch_index=1,
        event_type="message.delta",
        payload={"delta": "hello"},
        message_id=opaque_message_id("tenant-a", "run-a"),
    )
    rows = await v4.append_callback_v4_rows(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-a",
        items=(item,),
        authority=authority,
        execution_lease_id="lease-a",
    )
    metadata = conn.rows[rows[0]["id"]]["payload_json"]["__stream_v4"]
    metadata[field] = value

    with pytest.raises(v4.V4ProjectionError, match="existing_row_conflict"):
        await v4.append_callback_v4_rows(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id="batch-a",
            items=(item,),
            authority=authority,
            execution_lease_id="lease-a",
        )
    assert append_calls == [rows[0]["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("tenant_id", "tenant-b", "authority_scope_mismatch"),
        ("run_id", "run-b", "authority_scope_mismatch"),
        ("attempt_id", "attempt-b", "authority_scope_mismatch"),
        ("batch_id", "", "authority_scope_mismatch"),
        ("execution_lease_id", "", "execution_lease_id_invalid"),
    ],
)
async def test_callback_v4_rows_keep_batch_attempt_lease_and_authority_fences(
    field, value, error
):
    from app.streaming.infrastructure import v4

    conn = _callback_conn()
    item = v4.V4CallbackItem(
        callback_index=0,
        batch_index=1,
        event_type="message.delta",
        payload={"delta": "hello"},
        message_id=opaque_message_id("tenant-a", "run-a"),
    )
    values = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "batch_id": "batch-a",
        "execution_lease_id": "lease-a",
    }
    values[field] = value
    with pytest.raises((v4.V4ProjectionError, ValueError), match=error):
        await v4.append_callback_v4_rows(
            conn,
            **values,
            items=(item,),
            authority=_authority(),
        )


def _terminal_payload(event_id: str, event_type: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "terminal_event_id": event_id,
        "hydrate_required": True,
    }
    if event_type == "run.failed":
        payload.update(
            {
                "projection_version": "ai-platform.chat-public-projection.v1",
                "code": "failed",
                "default_message": "Run failed",
                "detail": None,
            }
        )
    elif event_type == "run.cancelled":
        payload["reason_code"] = "user_cancelled"
    return payload


@pytest.mark.asyncio
async def test_v4_redis_publication_transport_decodes_canonical_bytes() -> None:
    from app.streaming.infrastructure.worker_v4 import RedisV4PublicationTransport
    from app.streaming.redis import StreamContractError, StreamTransportUnavailable

    envelope = project_public_v4(_row({"delta": "hello"}), authority=_authority())
    assert envelope is not None
    payload = canonical_json_bytes(envelope)
    calls = []

    class Bridge:
        async def append(self, envelope):
            calls.append(envelope)
            return "12-0"

    assert await RedisV4PublicationTransport(Bridge()).publish(payload) == "12-0"
    assert calls == [envelope]

    class FailingBridge:
        async def append(self, _envelope):
            raise StreamTransportUnavailable("redis unavailable")

    with pytest.raises(V4PublicationTransportUnavailable) as exc_info:
        await RedisV4PublicationTransport(FailingBridge()).publish(payload)
    assert exc_info.value.error_code == "StreamTransportUnavailable"

    class MissingTerminalBridge:
        async def append(self, _envelope):
            raise StreamContractError("stream_missing")

    terminal = project_public_v4(_row(_terminal_payload("evt4_a", "run.succeeded"), event_type="run.succeeded"), authority=_authority())
    assert terminal is not None
    with pytest.raises(V4PublicationStreamExpired):
        await RedisV4PublicationTransport(MissingTerminalBridge()).publish(canonical_json_bytes(terminal))
    with pytest.raises(StreamContractError, match="stream_missing"):
        await RedisV4PublicationTransport(MissingTerminalBridge()).publish(payload)


@pytest.mark.parametrize("message_id", [None, "safe-message"])
@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "artifact.created",
            {
                "artifact_id": "artifact",
                "filename": "report.txt",
                "media_type": "text/plain",
                "size_bytes": 3,
                "status": "created",
            },
        ),
        (
            "policy.allowed",
            {
                "decision_id": "decision",
                "category": "read",
                "display_name": "Read",
                "decision_code": "allowed",
            },
        ),
        (
            "run.cancelled",
            {
                "terminal_event_id": "evt4_terminal",
                "hydrate_required": True,
                "reason_code": "user_cancelled",
            },
        ),
    ],
)
def test_nullable_and_safe_nonnull_message_ids_for_nonmessage_events(
    event_type, payload, message_id
):
    row = _row(payload, event_type=event_type)
    row["payload_json"]["__stream_v4"]["message_id"] = message_id

    internal = project_public_v4(row, authority=_authority())

    assert internal is not None
    assert internal["message_id"] == message_id


def test_invalid_nonmessage_message_id_is_rejected() -> None:
    row = _row(
        {
            "artifact_id": "artifact",
            "filename": "report.txt",
            "media_type": "text/plain",
            "size_bytes": 3,
            "status": "created",
        },
        event_type="artifact.created",
    )
    row["payload_json"]["__stream_v4"]["message_id"] = r"C:\\private\\secret"

    assert project_public_v4(row, authority=_authority()) is None


def _authority() -> StreamAuthority:
    return StreamAuthority(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="tenant-a",
        stream_incarnation=2,
        state="confirmed",
        open_event_id="open-a",
        open_payload_bytes="{}",
        open_payload_digest="digest",
        authorization_epoch=4,
        revocation_state="active",
    )


def _row(payload: dict[str, object], **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "evt4_a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "sequence": 7,
        "event_type": "message.delta",
        "visible_to_user": True,
        "payload_json": {
            **payload,
            "__stream_v4": {
                "attempt_id": "attempt-a",
                "version": 1,
                "stream_incarnation": 2,
                "authorization_epoch": 4,
                "message_id": opaque_message_id("tenant-a", "run-a"),
            },
        },
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_persisted_message_delta_projection_requires_managed_attempt_authority() -> (
    None
):
    row = _row({"delta": "hello"}, v4_attempt_authorized=True)

    projected = project_persisted_message_delta_v4(
        row, tenant_id="tenant-a", run_id="run-a"
    )

    assert projected is not None
    assert projected["payload"] == {"delta": "hello"}
    assert "__stream_v4" not in projected["payload"]
    assert "attempt_id" not in projected
    assert "authorization_epoch" not in projected
    assert "tenant_scope" not in projected
    assert (
        project_persisted_message_delta_v4(
            {**row, "v4_attempt_authorized": False},
            tenant_id="tenant-a",
            run_id="run-a",
        )
        is None
    )
    assert (
        project_persisted_message_delta_v4(
            {**row, "visible_to_user": False},
            tenant_id="tenant-a",
            run_id="run-a",
        )
        is None
    )
    assert (
        project_persisted_message_delta_v4(row, tenant_id="tenant-b", run_id="run-a")
        is None
    )


def test_v4_projection_is_internal_and_public_projection_strips_authority_fields() -> (
    None
):
    internal = project_public_v4(_row({"delta": "hello"}), authority=_authority())

    assert internal is not None
    assert internal["schema"] == "ai-platform.stream-event.v4"
    assert internal["tenant_scope"] == "tenant-a"
    assert internal["attempt_id"] == "attempt-a"
    public = project_public_envelope_v4(internal)
    assert public is not None
    assert public["schema"] == "ai-platform.public-run-stream-event.v4"
    assert "tenant_scope" not in public
    assert "attempt_id" not in public
    assert public["message_id"] == opaque_message_id("tenant-a", "run-a")


def test_v4_projection_preserves_legacy_empty_thinking_payloads() -> None:
    legacy = project_public_v4(
        _row({}, event_type="thinking.started"), authority=_authority()
    )
    current = project_public_v4(
        _row(
            {"public_summary": "Analyzing the request"},
            event_type="thinking.started",
        ),
        authority=_authority(),
    )

    assert legacy is not None
    assert legacy["payload"] == {}
    assert current is not None
    assert current["payload"] == {"public_summary": "Analyzing the request"}


def test_v4_projection_preserves_model_reasoning_delta_and_rejects_sdk_signature() -> (
    None
):
    thinking_id = "thinking_public_1"
    projected = [
        project_public_v4(
            _row({"thinking_id": thinking_id}, event_type="thinking.started"),
            authority=_authority(),
        ),
        project_public_v4(
            _row(
                {
                    "thinking_id": thinking_id,
                    "delta": "Compare the public evidence before answering.",
                },
                event_type="thinking.delta",
            ),
            authority=_authority(),
        ),
        project_public_v4(
            _row({"thinking_id": thinking_id}, event_type="thinking.completed"),
            authority=_authority(),
        ),
    ]

    assert [event["event_type"] for event in projected if event is not None] == [
        "thinking.started",
        "thinking.delta",
        "thinking.completed",
    ]
    assert projected[1] is not None
    assert projected[1]["payload"]["delta"] == (
        "Compare the public evidence before answering."
    )
    assert (
        project_public_v4(
            _row(
                {
                    "thinking_id": thinking_id,
                    "delta": "Compare the public evidence before answering.",
                    "signature": "private-sdk-signature",
                },
                event_type="thinking.delta",
            ),
            authority=_authority(),
        )
        is None
    )
    with pytest.raises(V4ProjectionError):
        validate_public_application_payload_v4(
            "thinking.delta", {"thinking_id": thinking_id, "delta": ""}
        )


@pytest.mark.parametrize(
    "forged_fields",
    [
        {"step_id": "phase_model_wait_forged"},
        {"message": "Reading hidden prompt"},
        {"raw_command": "cat /private/input"},
    ],
)
def test_v4_agent_progress_rejects_forged_authority_fields(
    forged_fields: dict[str, str],
) -> None:
    payload = {
        "schema_version": "ai-platform.public-agent-progress.v1",
        "step_id": "phase_model_wait",
        "phase": "model_wait",
        "lifecycle": "started",
        "message": "Waiting for the model response",
        **forged_fields,
    }

    with pytest.raises(V4ProjectionError):
        validate_public_application_payload_v4("agent.progress", payload)


def test_v4_projection_rejects_unknown_payload_keys() -> None:
    assert (
        project_public_v4(
            _row({"delta": "hello", "raw_output": "secret"}), authority=_authority()
        )
        is None
    )


def test_v4_projection_accepts_adapter_message_ids() -> None:
    assert project_public_v4(_row({"delta": 3}), authority=_authority()) is None
    row = _row({"delta": "hello"})
    row["payload_json"]["__stream_v4"]["message_id"] = "msg_run_a_attempt_a"
    projected = project_public_v4(row, authority=_authority())
    assert projected is not None
    assert projected["message_id"] == "msg_run_a_attempt_a"

    row["payload_json"]["__stream_v4"]["message_id"] = r"C:\private\message"
    assert project_public_v4(row, authority=_authority()) is None


def test_v4_gateway_rejects_internal_envelope_extensions() -> None:
    internal = project_public_v4(_row({"delta": "hello"}), authority=_authority())
    assert internal is not None
    extended = {**internal, "executor_private": "secret"}
    assert project_public_envelope_v4(extended) is None


def test_v4_projection_rejects_event_specific_code_combinations() -> None:
    invalid = (
        (
            "tool.failed",
            {
                "operation_id": "op",
                "category": "read",
                "display_name": "Read",
                "duration_ms": 1,
                "failure_category": "subagent_failed",
            },
        ),
        (
            "policy.allowed",
            {
                "decision_id": "decision",
                "category": "read",
                "display_name": "Read",
                "decision_code": "policy_denied",
            },
        ),
        (
            "subagent.cancelled",
            {
                "subagent_id": "subagent",
                "display_name": "Worker",
                "duration_ms": 1,
                "reason_code": "policy_cancelled",
            },
        ),
    )
    for event_type, payload in invalid:
        row = _row(payload, event_type=event_type)
        row["payload_json"]["__stream_v4"]["message_id"] = opaque_message_id(
            "tenant-a", "run-a"
        )
        assert project_public_v4(row, authority=_authority()) is None

    with pytest.raises(V4ProjectionError, match="v4_payload_keys_invalid"):
        validate_public_application_payload_v4(
            "run.failed",
            {
                "terminal_event_id": "terminal-1",
                "hydrate_required": True,
                "projection_version": "ai-platform.chat-public-projection.v1",
                "code": "failed",
                "default_message": "Run failed",
                "detail": None,
                "projection_failure_reason": "retired_projection_reason",
            },
        )


def test_v4_projection_rejects_authority_mismatch() -> None:
    assert (
        project_public_v4(
            _row({"delta": "hello"}),
            authority=replace(_authority(), authorization_epoch=5),
        )
        is None
    )


@pytest.mark.asyncio
async def test_v4_bridge_uses_existing_atomic_append_boundary() -> None:
    calls: list[dict[str, object]] = []

    class FakeBridge:
        async def append_canonical(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return "11-3"

        async def aclose(self) -> None:
            return None

    internal = project_public_v4(_row({"delta": "hello"}), authority=_authority())
    assert internal is not None
    bridge = V4RedisStreamBridge(FakeBridge())
    assert await bridge.append(internal) == "11-3"
    assert calls[0]["event_type"] == "message.delta"
    assert b"attempt-a" in calls[0]["envelope_bytes"]
    assert b"tenant-a" in calls[0]["envelope_bytes"]


@pytest.mark.asyncio
async def test_run_terminal_fact_needs_no_execution_lease_or_publication_state(
    monkeypatch,
):
    from app.streaming.infrastructure import v4

    conn = _callback_conn()
    captured: list[str] = []
    terminal_authority = _authority()

    async def authority(*_args, **_kwargs):
        return terminal_authority

    async def append_event(conn, *, tenant_id, run_id, event, event_id):
        captured.append(event_id)
        conn.rows[event_id] = {
            "id": event_id,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "sequence": 21,
            "event_type": event.event_type,
            "visible_to_user": True,
            "payload_json": dict(event.payload),
            "created_at": "2026-01-01T00:00:00Z",
        }
        return EventReceipt(event_id, RunCursor(run_id, 21), "2026-01-01T00:00:00Z")

    monkeypatch.setattr(v4, "get_stream_authority", authority)
    monkeypatch.setattr(v4.postgres, "append_event", append_event)
    terminal_id = f"evt4_run_{'a' * 64}"
    row = await v4.append_run_terminal_v4_row(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        status="failed",
        terminal_event_id=terminal_id,
        error_code="executor_private_exception",
        trace_ref="trace-a",
    )

    assert row is not None
    assert captured == [terminal_id]
    assert row["payload_json"]["code"] == "run_failed"
    assert row["payload_json"]["detail"] is None
    assert "executor_private_exception" not in str(row["payload_json"])
    metadata = row["payload_json"]["__stream_v4"]
    assert metadata["source_event_id"] == terminal_id
    assert "terminal_intent_id" not in metadata
    assert "publication_state" not in metadata
    assert metadata["execution_lease_id"] is None
    assert metadata["lease_fence"] == "not_required"
    assert (
        project_public_v4(row, authority=terminal_authority)["event_type"]
        == "run.failed"
    )


@pytest.mark.parametrize("event_id", ("evt4_a", "sev_retired"))
def test_run_terminal_live_projection_rejects_retired_identities(event_id):
    row = _row(_terminal_payload(event_id, "run.succeeded"), event_type="run.succeeded")
    row["id"] = event_id
    assert (project_public_v4(row, authority=_authority()) is not None) is event_id.startswith("evt4_")


@pytest.mark.asyncio
async def test_run_terminal_payload_cannot_name_a_different_event_identity(monkeypatch):
    from app.streaming.infrastructure import v4

    async def authority(*_args, **_kwargs):
        return _authority()

    monkeypatch.setattr(v4, "get_stream_authority", authority)
    with pytest.raises(v4.V4ProjectionError, match="terminal_event_id_mismatch"):
        await v4.append_run_v4_row(
            _callback_conn(), tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a",
            event_type="run.succeeded", payload={"terminal_event_id": "evt4_first", "hydrate_required": True},
            batch_id="terminal", event_id="evt4_second",
        )


@pytest.mark.asyncio
async def test_worker_v4_admission_prepares_before_transport_and_confirms_receipt():
    envelope = build_v4_control(
        event_id="open-a",
        tenant_scope="a" * 64,
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=1,
        event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": "open-a"},
    )
    payload = canonical_json_bytes(envelope)
    pending = V4PendingAdmission(
        tenant_id="tenant-a",
        tenant_scope="a" * 64,
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=1,
        open_event_id="open-a",
        open_payload_bytes=payload,
        open_payload_digest=hashlib.sha256(payload).hexdigest(),
    )
    calls: list[object] = []

    class Pending:
        async def prepare_pending_authority(self, **identity):
            calls.append(("prepare", identity))
            return pending

        async def confirm_pending_admission(self, admission, *, redis_id):
            calls.append(("confirm", admission, redis_id))
            return "confirmed"

    class Transport:
        async def publish(self, canonical_envelope_bytes):
            calls.append(("publish", canonical_envelope_bytes))
            return "1-0"

    capabilities = WorkerV4Capabilities(
        pending_admissions=Pending(),
        event_persistence=object(),
        publication_transport=Transport(),
    )
    result = await admit_v4_stream(
        capabilities,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
    )

    assert result == "confirmed"
    assert calls == [
        (
            "prepare",
            {"tenant_id": "tenant-a", "run_id": "run-a", "attempt_id": "attempt-a"},
        ),
        ("publish", payload),
        ("confirm", pending, "1-0"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ok", "empty", "outage", "expired", "commit_failure", "invalid_receipt"])
async def test_run_event_publishes_once_after_fact_loading_without_claims(failure):
    from app.streaming.application.worker_publication_v4 import publish_run_event

    envelope = project_public_v4(_row(_terminal_payload("evt4_a", "run.succeeded"), event_type="run.succeeded"), authority=_authority())
    assert envelope is not None
    payload = canonical_json_bytes(envelope)
    calls = []

    class Persistence:
        async def load_latest_run_event(self, *, tenant_id, run_id):
            assert (tenant_id, run_id) == ("tenant-a", "run-a")
            calls.append("fact")
            if failure == "commit_failure":
                raise RuntimeError("commit failed")
            return None if failure == "empty" else payload

    class Transport:
        async def publish(self, received):
            assert received == payload
            calls.append("stream")
            if failure == "outage":
                raise V4PublicationTransportUnavailable("redis_unavailable")
            if failure == "expired":
                raise V4PublicationStreamExpired
            return " " if failure == "invalid_receipt" else "1-0"

    capabilities = WorkerV4Capabilities(pending_admissions=object(), event_persistence=Persistence(), publication_transport=Transport())
    if failure in {"commit_failure", "invalid_receipt"}:
        with pytest.raises(RuntimeError):
            await publish_run_event(capabilities, tenant_id="tenant-a", run_id="run-a")
    else:
        assert await publish_run_event(capabilities, tenant_id="tenant-a", run_id="run-a") is (failure == "ok")
    assert calls == (["fact"] if failure in {"empty", "commit_failure"} else ["fact", "stream"])


@pytest.mark.asyncio
async def test_parent_finalization_publishes_child_and_distinct_parent(monkeypatch):
    from app.streaming.application import worker_publication_v4

    calls: list[object] = []

    async def finalize(transaction_factory, payload, reconciled_parent):
        calls.append(
            ("finalize", transaction_factory, payload.run_id, reconciled_parent)
        )
        return {"parent_run_id": "run-parent"}

    async def publish(_capabilities, *, tenant_id, run_id):
        calls.append(("publish", tenant_id, run_id))
        return True

    monkeypatch.setattr(worker_publication_v4, "publish_run_event", publish)

    @asynccontextmanager
    async def transaction_factory():
        yield object()

    capabilities = WorkerV4Capabilities(
        pending_admissions=object(),
        event_persistence=object(),
        publication_transport=object(),
    )
    payload = type("Payload", (), {"tenant_id": "tenant-a", "run_id": "run-child"})()

    await finalize_parent_and_publish(
        transaction_factory,
        capabilities,
        finalize,
        payload,
        "reconciled",
    )

    assert calls == [
        ("finalize", transaction_factory, "run-child", "reconciled"),
        ("publish", "tenant-a", "run-child"),
        ("publish", "tenant-a", "run-parent"),
    ]


@pytest.mark.asyncio
async def test_pending_admission_transport_outage_leaves_authority_retryable():
    envelope = build_v4_control(
        event_id="open-a",
        tenant_scope="a" * 64,
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=1,
        event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": "open-a"},
    )
    payload = canonical_json_bytes(envelope)
    pending = V4PendingAdmission(
        tenant_id="tenant-a",
        tenant_scope="a" * 64,
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=1,
        open_event_id="open-a",
        open_payload_bytes=payload,
        open_payload_digest=hashlib.sha256(payload).hexdigest(),
    )

    class Pending:
        confirmed = 0

        async def prepare_pending_authority(self, **identity):
            assert identity == {"tenant_id": "tenant-a", "run_id": "run-a", "attempt_id": "attempt-a"}
            return pending

        async def confirm_pending_admission(self, admission, *, redis_id):
            assert redis_id == "1-0"
            self.confirmed += 1

    class Transport:
        def __init__(self):
            self.outage = True

        async def publish(self, canonical_envelope_bytes):
            if self.outage:
                raise V4PublicationTransportUnavailable("redis_unavailable")
            return "1-0"

    pending_store = Pending()
    transport = Transport()
    capabilities = WorkerV4Capabilities(
        pending_admissions=pending_store,
        event_persistence=object(),
        publication_transport=transport,
    )
    with pytest.raises(V4PublicationTransportUnavailable):
        await admit_v4_stream(capabilities, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a")
    assert pending_store.confirmed == 0
    transport.outage = False
    await admit_v4_stream(capabilities, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a")
    assert pending_store.confirmed == 1
