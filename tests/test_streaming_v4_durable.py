from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.streaming.api import (
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
)
from app.streaming.authority import RunCursor
from app.streaming.postgres import EventReceipt
from app.streaming.redis import StreamAuthority
from app.streaming.v4 import (
    V4RedisStreamBridge,
    recover_v4_and_resume,
    list_pending_v4_rows,
)


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
            if "set stream_publication_attempts" in normalized:
                if row is not None:
                    metadata = row["payload_json"]["__stream_v4"]
                    metadata["publication_attempts"] = int(metadata.get("publication_attempts", 0)) + 1
                return Cursor({"id": event_id})
            if "set stream_publication_state = 'published'" in normalized:
                if row is not None and row["stream_publication_state"] == "pending":
                    row["stream_publication_state"] = "published"
                    row["payload_json"]["__stream_v4"]["publication_state"] = "published"
                    return Cursor({"id": event_id})
                return Cursor(None)
            if "set stream_publication_state = 'suppressed'" in normalized:
                if row is not None and row["stream_publication_state"] == "pending":
                    row["stream_publication_state"] = "suppressed"
                    row["payload_json"]["__stream_v4"]["publication_state"] = "suppressed"
                    row["payload_json"]["__stream_v4"]["suppression_reason"] = params[-2]
                    return Cursor({"id": event_id})
                return Cursor(None)
            if normalized.startswith("update run_events"):
                return Cursor({"id": event_id})
            raise AssertionError(statement)

    return Connection()


@pytest.mark.asyncio
async def test_callback_v4_rows_are_atomic_and_idempotent_per_batch_item(monkeypatch):
    from app.streaming import v4

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
            "stream_publication_state": "pending",
            "stream_publication_attempts": 0,
            "stream_publication_next_attempt_at": None,
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
    assert sum("update run_events" in statement.lower() for statement, _ in conn.statements) == 1
    await v4.mark_v4_attempt(conn, event_id=first[0]["id"])
    third = await exercise()
    assert third[0][0]["id"] == first[0]["id"]
    assert conn.rows[first[0]["id"]]["payload_json"]["__stream_v4"]["publication_attempts"] == 1
    assert len(append_calls) == 1

    assert await v4.mark_v4_published(
        conn,
        event_id=first[0]["id"],
        redis_id="17-0",
    )
    fourth = await exercise()
    assert fourth[0][0]["id"] == first[0]["id"]
    assert len(append_calls) == 1

    suppressed_item = replace(item, batch_index=2)
    suppressed_rows = await v4.append_callback_v4_rows(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-suppressed",
        items=(suppressed_item,),
        authority=authority,
        execution_lease_id="lease-a",
    )
    assert await v4.suppress_v4_event(
        conn,
        event_id=suppressed_rows[0]["id"],
        reason="authority_revoked",
    )
    suppressed_retry = await v4.append_callback_v4_rows(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-suppressed",
        items=(suppressed_item,),
        authority=authority,
        execution_lease_id="lease-a",
    )
    assert suppressed_retry[0]["id"] == suppressed_rows[0]["id"]
    assert len(append_calls) == 2

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
    assert len(append_calls) == 2


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
async def test_callback_v4_existing_row_rejects_each_immutable_callback_fact(field, value, monkeypatch):
    from app.streaming import v4

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
            "stream_publication_state": "pending",
            "stream_publication_attempts": 0,
            "stream_publication_next_attempt_at": None,
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
    from app.streaming import v4

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


@pytest.mark.asyncio
async def test_v4_publisher_retries_missing_authority_without_starving_later_rows(monkeypatch):
    from app.streaming import worker_projection

    missing = _row({"delta": "first"}, id="evt4_missing")
    ready = _row({"delta": "second"}, id="evt4_ready")
    ready["run_id"] = "run-b"
    ready["payload_json"]["__stream_v4"]["attempt_id"] = "attempt-b"
    ready["payload_json"]["__stream_v4"]["message_id"] = opaque_message_id("tenant-a", "run-b")
    ready["payload_json"]["__stream_v4"]["execution_lease_id"] = "lease-b"
    ready_authority = replace(_authority(), run_id="run-b", attempt_id="attempt-b")
    missing["payload_json"]["__stream_v4"]["execution_lease_id"] = "lease-a"
    retries = []
    published = []
    attempts = []

    remaining = [missing, ready]

    async def pending(_conn, *, limit):
        assert limit == 1
        return (remaining.pop(0),) if remaining else ()

    async def mark_attempt(_conn, *, event_id):
        attempts.append(event_id)

    async def retry(_conn, *, event_id, error):
        retries.append((event_id, error))

    async def suppress(*_args, **_kwargs):
        raise AssertionError("neither row should be suppressed")

    async def identity(_conn, *, run_id):
        return {"tenant_id": "tenant-a", "run_id": run_id, "status": "running"}

    async def authority(_conn, *, tenant_id, run_id):
        return None if run_id == "run-a" else ready_authority

    async def cancel_requested(*_args, **_kwargs):
        return False

    async def published_row(_conn, *, event_id, redis_id):
        published.append((event_id, redis_id))
        return True

    class Cursor:
        async def fetchone(self):
            return {"id": "lease"}

    class Connection:
        async def execute(self, _statement, _params):
            return Cursor()

    class Transaction:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return None

    class Bridge:
        async def append(self, envelope):
            published.append((envelope["event_id"], "redis-1"))
            return "redis-1"

        async def aclose(self):
            return None

    monkeypatch.setattr(worker_projection, "list_pending_v4_rows", pending)
    monkeypatch.setattr(worker_projection, "mark_v4_attempt", mark_attempt)
    monkeypatch.setattr(worker_projection, "mark_v4_retry_error", retry)
    monkeypatch.setattr(worker_projection, "suppress_v4_event", suppress)
    monkeypatch.setattr(worker_projection, "mark_v4_published", published_row)
    monkeypatch.setattr(worker_projection.repositories, "get_run_identity", identity)
    monkeypatch.setattr(worker_projection, "get_stream_authority", authority)
    monkeypatch.setattr(worker_projection.repositories, "is_cancel_requested", cancel_requested)

    result = await worker_projection.publish_pending_v4_events(
        lambda: Transaction(), limit=2, bridge=Bridge()
    )
    assert result == 1
    assert attempts == ["evt4_missing", "evt4_ready"]
    assert retries == [("evt4_missing", "stream_authority_missing")]
    assert published[-1] == ("evt4_ready", "redis-1")


@pytest.mark.asyncio
async def test_v4_retry_keeps_permanently_stuck_authority_rows_pending(monkeypatch):
    from app.streaming import worker_projection

    retries = []
    suppressed = []

    async def suppress(_conn, *, event_id, reason):
        suppressed.append((event_id, reason))

    async def retry(_conn, *, event_id, error):
        retries.append((event_id, error))

    monkeypatch.setattr(worker_projection, "suppress_v4_event", suppress)
    monkeypatch.setattr(worker_projection, "mark_v4_retry_error", retry)

    await worker_projection._retry_or_suppress_v4_event(
        object(),
        event_id="evt4_stuck",
        attempts=worker_projection.V4_MAX_PUBLICATION_ATTEMPTS,
        reason="stream_authority_missing",
    )
    assert retries == [("evt4_stuck", "stream_authority_missing")]
    assert suppressed == []


@pytest.mark.asyncio
async def test_v4_route_recovery_keeps_pg_gap_and_redis_resume_in_one_seam(monkeypatch):
    from app.routes import lambchat_compat
    expected = lambchat_compat.V4Recovery(({"id": "evt4_gap"},), "12-0")
    calls = []

    async def recover(conn, **kwargs):
        calls.append((conn, kwargs))
        return expected

    monkeypatch.setattr(lambchat_compat, "recover_v4_and_resume", recover)
    bridge = object()
    result = await lambchat_compat._recover_v4_attach_gap(
        "conn",
        bridge=bridge,
        tenant_id="tenant-a",
        run_id="run-a",
        authority=_authority(),
        after_sequence=7,
        limit=16,
    )
    assert result == expected
    assert calls[0][0] == "conn"
    assert calls[0][1]["tenant_id"] == "tenant-a"
    assert calls[0][1]["after_sequence"] == 7
    assert isinstance(calls[0][1]["bridge"], lambchat_compat.V4RedisStreamBridge)




@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "status", "payload"),
    [
        (
            "run.succeeded",
            "succeeded",
            {"terminal_event_id": "evt4_terminal", "hydrate_required": True},
        ),
        (
            "run.cancelled",
            "cancelled",
            {
                "terminal_event_id": "evt4_terminal",
                "hydrate_required": True,
                "reason_code": "user_cancelled",
            },
        ),
    ],
)
async def test_terminal_events_bypass_cancellation_fence(
    monkeypatch, event_type, status, payload
):
    from app.streaming import worker_projection

    row = _row(payload, id="evt4_terminal", event_type=event_type, sequence=1)
    published = []
    suppressed = []

    async def pending(_conn, *, limit):
        assert limit == 1
        return (row,)

    async def identity(_conn, *, run_id):
        return {"tenant_id": "tenant-a", "run_id": run_id, "status": status}

    async def authority(_conn, *, tenant_id, run_id):
        return replace(_authority(), state="terminal")

    async def cancel_requested(*_args, **_kwargs):
        return True

    async def mark_attempt(_conn, *, event_id):
        return None

    async def mark_published(_conn, *, event_id, redis_id):
        published.append((event_id, redis_id))
        return True

    async def suppress(_conn, *, event_id, reason):
        suppressed.append((event_id, reason))
        return True

    class Cursor:
        def __init__(self, value):
            self.value = value

        async def fetchone(self):
            return self.value

    class Connection:
        async def execute(self, statement, _params):
            if "min(sequence)" in statement:
                return Cursor({"sequence": 1})
            return Cursor({"id": "lease"})

    class Transaction:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return None

    class Bridge:
        async def append(self, envelope):
            return "12-0"

        async def aclose(self):
            return None

    monkeypatch.setattr(worker_projection, "list_pending_v4_rows", pending)
    monkeypatch.setattr(worker_projection, "mark_v4_attempt", mark_attempt)
    monkeypatch.setattr(worker_projection, "mark_v4_published", mark_published)
    monkeypatch.setattr(worker_projection, "suppress_v4_event", suppress)
    monkeypatch.setattr(worker_projection.repositories, "get_run_identity", identity)
    monkeypatch.setattr(worker_projection, "get_stream_authority", authority)
    monkeypatch.setattr(worker_projection.repositories, "is_cancel_requested", cancel_requested)

    result = await worker_projection.publish_pending_v4_events(
        lambda: Transaction(), limit=1, bridge=Bridge()
    )

    assert result == 1
    assert published == [("evt4_terminal", "12-0")]
    assert suppressed == []


@pytest.mark.asyncio
async def test_recovery_returns_public_rows_with_one_transport_cursor_per_frame():
    rows = (
        _row({"delta": "first"}, id="evt4_first", sequence=1),
        _row({"delta": "second"}, id="evt4_second", sequence=2),
    )

    class Result:
        async def fetchall(self):
            return rows

    class Connection:
        async def execute(self, _statement, _params):
            return Result()

    class Bridge:
        def __init__(self):
            self.cursors = iter(("13-0", "13-1"))

        async def append(self, envelope):
            assert envelope["schema"] == "ai-platform.stream-event.v4"
            return next(self.cursors)

    recovery = await recover_v4_and_resume(
        Connection(),
        bridge=Bridge(),
        tenant_id="tenant-a",
        run_id="run-a",
        authority=_authority(),
        after_sequence=0,
        limit=2,
    )

    assert [row["event_id"] for row in recovery.rows] == ["evt4_first", "evt4_second"]
    assert recovery.transport_cursors == ("13-0", "13-1")
    assert recovery.transport_cursor == "13-1"
    for row in recovery.rows:
        assert row["schema"] == "ai-platform.public-run-stream-event.v4"
        assert "tenant_scope" not in row
        assert "attempt_id" not in row
        assert "projection_version" not in row
        assert "source" not in row


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
                "publication_state": "pending",
            },
        },
        "stream_publication_state": "pending",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_v4_projection_is_internal_and_public_projection_strips_authority_fields() -> None:
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


def test_v4_projection_rejects_unknown_payload_keys() -> None:
    assert project_public_v4(
        _row({"delta": "hello", "raw_output": "secret"}), authority=_authority()
    ) is None


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


@pytest.mark.asyncio
async def test_v4_pending_query_is_exact_visible_due_ordered_skip_locked() -> None:
    class FakeCursor:
        async def fetchall(self) -> list[dict[str, object]]:
            return []

    class FakeConnection:
        statement = ""
        params: tuple[object, ...] | None = None

        async def execute(self, statement: str, params: tuple[object, ...]) -> FakeCursor:
            self.statement = statement
            self.params = params
            return FakeCursor()

    conn = FakeConnection()
    assert await list_pending_v4_rows(conn, limit=3) == ()
    normalized = " ".join(conn.statement.lower().split())
    assert "visible_to_user = true" in normalized
    assert "stream_publication_state = 'pending'" in normalized
    assert "stream_publication_next_attempt_at <= now()" in normalized
    assert "order by run_id asc, sequence asc limit %s" in normalized
    assert "not exists" in normalized
    assert "limit %s for update skip locked" in normalized
    assert conn.params == (3,)


def test_v4_projection_rejects_event_specific_code_combinations() -> None:
    invalid = (
        ("tool.failed", {"operation_id": "op", "category": "read", "display_name": "Read", "duration_ms": 1, "failure_category": "subagent_failed"}),
        ("policy.allowed", {"decision_id": "decision", "category": "read", "display_name": "Read", "decision_code": "policy_denied"}),
        ("subagent.cancelled", {"subagent_id": "subagent", "display_name": "Worker", "duration_ms": 1, "reason_code": "policy_cancelled"}),
    )
    for event_type, payload in invalid:
        row = _row(payload, event_type=event_type)
        row["payload_json"]["__stream_v4"]["message_id"] = opaque_message_id("tenant-a", "run-a")
        assert project_public_v4(row, authority=_authority()) is None


def test_v4_projection_rejects_authority_mismatch() -> None:
    assert project_public_v4(
        _row({"delta": "hello"}, stream_publication_state="published"),
        authority=replace(_authority(), authorization_epoch=5),
    ) is None


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
async def test_run_v4_terminal_row_reuses_terminal_intent_and_has_no_live_lease(monkeypatch):
    from dataclasses import replace
    from app.streaming import v4

    conn = _callback_conn()
    captured: list[str] = []
    terminal_authority = replace(_authority(), state="terminal")

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
            "stream_publication_state": "pending",
            "stream_publication_attempts": 0,
            "stream_publication_next_attempt_at": None,
            "created_at": "2026-01-01T00:00:00Z",
        }
        return EventReceipt(event_id, RunCursor(run_id, 21), "2026-01-01T00:00:00Z")

    monkeypatch.setattr(v4, "get_stream_authority", authority)
    monkeypatch.setattr(v4.postgres, "append_event", append_event)
    terminal_id = f"sev_{'a' * 64}"
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
    assert metadata["terminal_intent_id"] == terminal_id
    assert metadata["execution_lease_id"] is None
    assert metadata["lease_fence"] == "not_required"
    assert project_public_v4(row, authority=terminal_authority)["event_type"] == "run.failed"


@pytest.mark.asyncio
async def test_run_v4_terminal_row_rejects_a_second_terminal_identity(monkeypatch):
    from dataclasses import replace
    from app.streaming import v4

    async def authority(*_args, **_kwargs):
        return replace(_authority(), state="terminal")

    monkeypatch.setattr(v4, "get_stream_authority", authority)
    first_terminal_id = f"sev_{'a' * 64}"
    second_terminal_id = f"sev_{'b' * 64}"
    with pytest.raises(v4.V4ProjectionError, match="terminal_intent_identity"):
        await v4.append_run_v4_row(
            _callback_conn(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            event_type="run.succeeded",
            payload={"terminal_event_id": first_terminal_id, "hydrate_required": True},
            batch_id=first_terminal_id,
            event_id=first_terminal_id,
            terminal_intent_id=second_terminal_id,
        )
