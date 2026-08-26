from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.runs.api import RunTerminalEventFact
from app.streaming.application.durable_v4 import (
    V4PublicationClaim,
    V4PublicationTransportUnavailable,
    V4PendingAdmission,
    V4PublicationScope,
    publish_claimed_v4_events,
    publish_due_v4_events,
)

from app.streaming.application.worker_publication_v4 import (
    WorkerV4Capabilities,
    admit_v4_stream,
    finalize_parent_and_publish,
    publish_pending_admissions,
)

from app.streaming.application.recovery_v4 import (
    V4ReadySuccessorRebuild,
    V4SuccessorActivation,
    V4SuccessorRebuildClaim,
    V4SuccessorRebuildItem,
    V4SuccessorRebuildReceipt,
    activate_v4_successor_rebuild,
    build_v4_successor_rebuild,
    successor_receipt_digest,
)
from app.streaming.api import (
    V4ProjectionError,
    build_v4_control,
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
    project_public_v4_successor,
    successor_stream_open_event_id,
    stream_end_event_id,
    stream_key,
    validate_public_application_payload_v4,
)
from app.streaming.authority import RunCursor
from app.streaming.postgres import EventReceipt
from app.streaming.redis import StreamAuthority
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.infrastructure import run_v4_events, worker_v4
from app.streaming.infrastructure.postgres_v4 import (
    PostgresV4PublicationClaims,
    V4PublicationAuthorityError,
)
from app.streaming.infrastructure.v4 import (
    V4RedisStreamBridge,
    list_pending_v4_rows,
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
    assert runtime_callbacks.callback_item_to_v4 is callback_events_v4.callback_item_to_v4


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
async def test_terminal_row_uses_streaming_intent_attempt_identity(monkeypatch):
    calls: list[tuple[str, object]] = []
    conn = object()
    terminal_event_id = f"sev_{'a' * 64}"

    async def load_terminal_fact(observed_conn, *, tenant_id, run_id):
        assert observed_conn is conn
        assert (tenant_id, run_id) == ("tenant-a", "run-a")
        calls.append(("run_fact", observed_conn))
        return RunTerminalEventFact(
            status="failed",
            terminal_reason="queue_enqueue_failed",
            error_code="queue_enqueue_failed",
            trace_ref="trace-run-a",
        )

    async def ensure_terminal_intent(observed_conn, *, tenant_id, run_id, status):
        assert observed_conn is conn
        assert (tenant_id, run_id, status) == ("tenant-a", "run-a", "failed")
        calls.append(("stream_intent", observed_conn))
        return type(
            "Intent",
            (),
            {"attempt_id": "attempt-active", "terminal_event_id": terminal_event_id},
        )()

    async def append_terminal_row(observed_conn, **kwargs):
        assert observed_conn is conn
        assert kwargs["attempt_id"] == "attempt-active"
        assert kwargs["terminal_event_id"] == terminal_event_id
        calls.append(("terminal_row", observed_conn))
        return "row-a"

    monkeypatch.setattr(
        run_v4_events,
        "ensure_run_terminal_intent",
        ensure_terminal_intent,
    )
    monkeypatch.setattr(
        run_v4_events._v4,
        "append_run_terminal_v4_row",
        append_terminal_row,
    )

    row_id = await run_v4_events.append_current_run_terminal_v4_row(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        load_terminal_event_fact=load_terminal_fact,
    )

    assert row_id == "row-a"
    assert calls == [
        ("run_fact", conn),
        ("stream_intent", conn),
        ("terminal_row", conn),
    ]


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


def _publication_claim(*, event_id: str = "evt4_a", sequence: int = 7) -> V4PublicationClaim:
    envelope = project_public_v4(
        _row({"delta": "hello"}, id=event_id, sequence=sequence),
        authority=_authority(),
    )
    assert envelope is not None
    return V4PublicationClaim(
        event_id=event_id,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="tenant-a",
        stream_incarnation=2,
        authorization_epoch=4,
        sequence=sequence,
        canonical_envelope_bytes=canonical_json_bytes(envelope),
        claim_token=f"claim-{event_id}",
        claim_expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )


class _RecordingPublicationClaims:
    def __init__(
        self,
        claims: list[V4PublicationClaim],
        *,
        mark_published_result: bool = True,
    ) -> None:
        self.claims = claims
        self.mark_published_result = mark_published_result
        self.calls: list[str] = []

    async def claim_next(self, **_kwargs):
        self.calls.append("claim:committed")
        return self.claims.pop(0) if self.claims else None

    async def mark_published(self, claim, *, redis_id):
        self.calls.append(f"published:{claim.event_id}:{redis_id}")
        return self.mark_published_result

    async def schedule_retry(self, claim, *, error, delay):
        self.calls.append(f"retry:{claim.event_id}:{error}:{delay.total_seconds():g}")
        return True

    async def release(self, claim):
        self.calls.append(f"release:{claim.event_id}")
        return True


@pytest.mark.asyncio
async def test_v4_application_publishes_only_after_claim_commit_and_fences_disposition() -> None:
    claims = _RecordingPublicationClaims([_publication_claim()])

    class Transport:
        async def publish(self, canonical_envelope_bytes):
            assert claims.calls == ["claim:committed"]
            assert json.loads(canonical_envelope_bytes)["event_id"] == "evt4_a"
            claims.calls.append("transport:evt4_a")
            return "12-0"

    result = await publish_claimed_v4_events(
        claims,
        Transport(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        limit=2,
        claim_ttl=timedelta(seconds=30),
        retry_delay=timedelta(seconds=5),
    )

    assert result == 1
    assert claims.calls == [
        "claim:committed",
        "transport:evt4_a",
        "published:evt4_a:12-0",
        "claim:committed",
    ]


@pytest.mark.asyncio
async def test_v4_application_lost_disposition_is_not_counted_as_published() -> None:
    claims = _RecordingPublicationClaims(
        [_publication_claim()],
        mark_published_result=False,
    )

    class Transport:
        async def publish(self, _canonical_envelope_bytes):
            return "12-0"

    assert await publish_claimed_v4_events(
        claims,
        Transport(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        limit=1,
        claim_ttl=timedelta(seconds=30),
        retry_delay=timedelta(seconds=5),
    ) == 0
    assert claims.calls == ["claim:committed", "published:evt4_a:12-0"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", " tenant-a"),
        ("run_id", ""),
        ("attempt_id", object()),
        ("stream_incarnation", True),
        ("limit", 257),
        ("claim_ttl", timedelta(0)),
        ("retry_delay", timedelta(microseconds=-1)),
    ],
)
async def test_v4_application_rejects_invalid_publication_scope_before_claim(
    field, value
) -> None:
    claims = _RecordingPublicationClaims([])

    class Transport:
        async def publish(self, _canonical_envelope_bytes):
            raise AssertionError("transport must not run")

    kwargs = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "stream_incarnation": 2,
        "limit": 1,
        "claim_ttl": timedelta(seconds=30),
        "retry_delay": timedelta(seconds=5),
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match="v4_publication_"):
        await publish_claimed_v4_events(claims, Transport(), **kwargs)
    assert claims.calls == []


@pytest.mark.asyncio
async def test_v4_application_retries_transport_outage_without_release() -> None:
    claims = _RecordingPublicationClaims([_publication_claim()])

    class Transport:
        async def publish(self, _canonical_envelope_bytes):
            raise V4PublicationTransportUnavailable("StreamTransportUnavailable")

    assert await publish_claimed_v4_events(
        claims,
        Transport(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        limit=1,
        claim_ttl=timedelta(seconds=30),
        retry_delay=timedelta(seconds=5),
    ) == 0
    assert claims.calls == [
        "claim:committed",
        "retry:evt4_a:StreamTransportUnavailable:5",
    ]


@pytest.mark.asyncio
async def test_v4_application_releases_claim_on_unexpected_transport_error() -> None:
    claims = _RecordingPublicationClaims([_publication_claim()])

    class Transport:
        async def publish(self, _canonical_envelope_bytes):
            raise RuntimeError("invalid transport result")

    with pytest.raises(RuntimeError, match="invalid transport result"):
        await publish_claimed_v4_events(
            claims,
            Transport(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
            limit=1,
            claim_ttl=timedelta(seconds=30),
            retry_delay=timedelta(seconds=5),
        )
    assert claims.calls == ["claim:committed", "release:evt4_a"]


@pytest.mark.asyncio
async def test_v4_redis_publication_transport_decodes_claimed_canonical_bytes() -> None:
    from app.streaming.infrastructure.worker_v4 import RedisV4PublicationTransport
    from app.streaming.redis import StreamTransportUnavailable

    claim = _publication_claim()
    calls = []

    class Bridge:
        async def append(self, envelope):
            calls.append(envelope)
            return "12-0"

    transport = RedisV4PublicationTransport(Bridge())
    assert await transport.publish(claim.canonical_envelope_bytes) == "12-0"
    assert calls[0]["event_id"] == claim.event_id

    class FailingBridge:
        async def append(self, _envelope):
            raise StreamTransportUnavailable("redis unavailable")

    with pytest.raises(V4PublicationTransportUnavailable) as exc_info:
        await RedisV4PublicationTransport(FailingBridge()).publish(
            claim.canonical_envelope_bytes
        )
    assert exc_info.value.error_code == "StreamTransportUnavailable"


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


def _successor_claim_with_terminal() -> V4SuccessorRebuildClaim:
    authority = _authority()
    rows = (
        _row({"delta": "hello"}, sequence=7, id="evt4_delta"),
        _row(
            {"terminal_event_id": "evt4_terminal", "hydrate_required": True},
            sequence=8,
            id="evt4_terminal",
            event_type="run.succeeded",
        ),
    )
    items = []
    for source_row in rows:
        envelope = project_public_v4_successor(
            source_row,
            source_authority=authority,
            successor_incarnation=3,
            successor_authorization_epoch=5,
        )
        assert envelope is not None
        envelope_bytes = canonical_json_bytes(envelope)
        items.append(
            V4SuccessorRebuildItem(
                event_id=source_row["id"],
                sequence=source_row["sequence"],
                event_type=source_row["event_type"],
                canonical_envelope_bytes=envelope_bytes,
                envelope_digest=hashlib.sha256(envelope_bytes).hexdigest(),
            )
        )
    open_event_id = successor_stream_open_event_id(
        tenant_scope="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=3,
    )
    opening = build_v4_control(
        event_id=open_event_id,
        tenant_scope="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=3,
        event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": open_event_id},
        emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    open_bytes = canonical_json_bytes(opening)
    return V4SuccessorRebuildClaim(
        rebuild_id="srb_test",
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="tenant-a",
        source_incarnation=2,
        source_authorization_epoch=4,
        origin_incarnation=2,
        origin_authorization_epoch=4,
        successor_incarnation=3,
        successor_authorization_epoch=5,
        source_authority_fingerprint="a" * 64,
        source_cursor_sequence=8,
        source_through_sequence=8,
        successor_open_event_id=open_event_id,
        successor_open_bytes=open_bytes,
        successor_open_digest=hashlib.sha256(open_bytes).hexdigest(),
        items=tuple(items),
        claim_token="claim-test",
        claim_expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )


def _successor_end_bytes(claim: V4SuccessorRebuildClaim) -> bytes:
    terminal = json.loads(claim.items[-1].canonical_envelope_bytes)
    return canonical_json_bytes(
        build_v4_control(
            event_id=stream_end_event_id(claim.items[-1].event_id),
            tenant_scope=claim.tenant_scope,
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            stream_incarnation=claim.successor_incarnation,
            event_type="stream.end",
            payload={"terminal_event_id": claim.items[-1].event_id},
            source={"kind": "terminal_intent", "terminal_event_id": claim.items[-1].event_id},
            causation_event_id=claim.items[-1].event_id,
            emitted_at=terminal["emitted_at"],
        )
    )


def _successor_item_redis_ids(
    claim: V4SuccessorRebuildClaim,
) -> tuple[str, ...]:
    return tuple(f"1-{index}" for index in range(1, len(claim.items) + 1))


def test_successor_rebuild_claim_binds_exact_canonical_snapshot() -> None:
    envelope = project_public_v4_successor(
        _row({"delta": "hello"}),
        source_authority=_authority(),
        successor_incarnation=3,
        successor_authorization_epoch=5,
    )
    envelope_bytes = canonical_json_bytes(envelope)
    item = V4SuccessorRebuildItem(
        event_id="evt4_a",
        sequence=7,
        event_type="message.delta",
        canonical_envelope_bytes=envelope_bytes,
        envelope_digest=hashlib.sha256(envelope_bytes).hexdigest(),
    )
    open_id = successor_stream_open_event_id(
        tenant_scope="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=3,
    )
    opening = build_v4_control(
        event_id=open_id,
        tenant_scope="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=3,
        event_type="stream.open",
        payload={"design_id": "ai-platform.redis-streams-sse-event-channel.v4"},
        source={"kind": "stream_authority", "authority_id": open_id},
        emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    open_bytes = canonical_json_bytes(opening)
    claim = V4SuccessorRebuildClaim(
        rebuild_id="srb_a",
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="tenant-a",
        source_incarnation=2,
        source_authorization_epoch=4,
        origin_incarnation=2,
        origin_authorization_epoch=4,
        successor_incarnation=3,
        successor_authorization_epoch=5,
        source_authority_fingerprint="a" * 64,
        source_cursor_sequence=8,
        source_through_sequence=7,
        successor_open_event_id=open_id,
        successor_open_bytes=open_bytes,
        successor_open_digest=hashlib.sha256(open_bytes).hexdigest(),
        items=(item,),
        claim_token="claim-a",
        claim_expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )

    assert json.loads(claim.items[0].canonical_envelope_bytes)[
        "stream_incarnation"
    ] == 3
    assert "claim-a" not in repr(claim)
    with pytest.raises(ValueError, match="successor_incarnation_invalid"):
        replace(claim, successor_incarnation=2)
    with pytest.raises(ValueError, match="envelope_mismatch"):
        replace(item, sequence=8)
    with pytest.raises(ValueError, match="source_cursor_invalid"):
        replace(claim, source_cursor_sequence=6)
    with pytest.raises(ValueError, match="source_fingerprint_invalid"):
        replace(claim, source_authority_fingerprint="A" * 64)

    foreign_envelope = json.loads(envelope_bytes)
    foreign_envelope["run_id"] = "run-b"
    foreign_bytes = canonical_json_bytes(foreign_envelope)
    foreign_item = replace(
        item,
        canonical_envelope_bytes=foreign_bytes,
        envelope_digest=hashlib.sha256(foreign_bytes).hexdigest(),
    )
    with pytest.raises(ValueError, match="item_mismatch"):
        replace(claim, items=(foreign_item,))

    wrong_source_envelope = json.loads(envelope_bytes)
    wrong_source_envelope["source"]["run_event_id"] = "evt4_other"
    wrong_source_bytes = canonical_json_bytes(wrong_source_envelope)
    wrong_source_item = replace(
        item,
        canonical_envelope_bytes=wrong_source_bytes,
        envelope_digest=hashlib.sha256(wrong_source_bytes).hexdigest(),
    )
    with pytest.raises(ValueError, match="item_mismatch"):
        replace(claim, items=(wrong_source_item,))

    duplicate_envelope = json.loads(envelope_bytes)
    duplicate_envelope["seq"] = 8
    duplicate_envelope["source"]["sequence"] = 8
    duplicate_bytes = canonical_json_bytes(duplicate_envelope)
    duplicate_item = replace(
        item,
        sequence=8,
        canonical_envelope_bytes=duplicate_bytes,
        envelope_digest=hashlib.sha256(duplicate_bytes).hexdigest(),
    )
    with pytest.raises(ValueError, match="item_mismatch"):
        replace(
            claim,
            source_cursor_sequence=8,
            source_through_sequence=8,
            items=(item, duplicate_item),
        )

    wrong_opening = dict(opening)
    wrong_opening["source"] = {
        "kind": "stream_authority",
        "authority_id": "other-open",
    }
    wrong_open_bytes = canonical_json_bytes(wrong_opening)
    with pytest.raises(ValueError, match="open_mismatch"):
        replace(
            claim,
            successor_open_bytes=wrong_open_bytes,
            successor_open_digest=hashlib.sha256(wrong_open_bytes).hexdigest(),
        )


@pytest.mark.asyncio
async def test_successor_rebuild_application_orders_commit_transport_and_ready_cas() -> None:
    claim = _successor_claim_with_terminal()
    calls: list[str] = []

    class Rebuilds:
        async def prepare(self, **_kwargs):
            calls.append("prepare")
            return claim

        async def mark_ready(self, _claim, *, receipt):
            calls.append("mark_ready")
            assert receipt.entry_count == len(claim.items) + 2
            return True

    from app.streaming.domain.public_events_v4 import stream_end_event_id

    class Transport:
        async def build(self, _claim):
            calls.append("transport")
            terminal_id = claim.items[-1].event_id
            return V4SuccessorRebuildReceipt(
                stream_key=stream_key(
                    tenant_scope_value=claim.tenant_scope,
                    run_id=claim.run_id,
                    stream_incarnation=claim.successor_incarnation,
                ),
                stream_incarnation=claim.successor_incarnation,
                entry_count=len(claim.items) + 2,
                open_event_id=claim.successor_open_event_id,
                terminal_event_id=terminal_id,
                end_event_id=stream_end_event_id(terminal_id),
                last_redis_id="1-3",
                item_redis_ids=_successor_item_redis_ids(claim),
                last_envelope_bytes=_successor_end_bytes(claim),
            )
    ready = await build_v4_successor_rebuild(
        Rebuilds(),
        Transport(),
        tenant_id=claim.tenant_id,
        run_id=claim.run_id,
        attempt_id=claim.attempt_id,
        source_incarnation=claim.source_incarnation,
        claim_ttl=timedelta(seconds=30),
    )
    assert isinstance(ready, V4ReadySuccessorRebuild)
    assert ready.last_envelope_bytes == _successor_end_bytes(claim)
    assert calls == ["prepare", "transport", "mark_ready"]
    assert "claim-test" not in repr(ready)
    with pytest.raises(ValueError, match="claim_token_invalid"):
        replace(ready, claim_token="")


@pytest.mark.asyncio
async def test_successor_activation_application_passes_only_valid_ready_receipt():
    claim = _successor_claim_with_terminal()

    class Rebuilds:
        async def prepare(self, **_kwargs):
            return claim

        async def mark_ready(self, _claim, *, receipt):
            return True

    class Transport:
        async def build(self, _claim):
            terminal_id = claim.items[-1].event_id
            return V4SuccessorRebuildReceipt(
                stream_key=stream_key(
                    tenant_scope_value=claim.tenant_scope,
                    run_id=claim.run_id,
                    stream_incarnation=claim.successor_incarnation,
                ),
                stream_incarnation=claim.successor_incarnation,
                entry_count=len(claim.items) + 2,
                open_event_id=claim.successor_open_event_id,
                terminal_event_id=terminal_id,
                end_event_id=stream_end_event_id(terminal_id),
                last_redis_id="1-3",
                item_redis_ids=_successor_item_redis_ids(claim),
                last_envelope_bytes=_successor_end_bytes(claim),
            )

    ready = await build_v4_successor_rebuild(
        Rebuilds(),
        Transport(),
        tenant_id=claim.tenant_id,
        run_id=claim.run_id,
        attempt_id=claim.attempt_id,
        source_incarnation=claim.source_incarnation,
        claim_ttl=timedelta(seconds=30),
    )
    assert ready is not None
    calls = []

    class Activations:
        async def activate(self, candidate):
            calls.append(candidate)
            return V4SuccessorActivation(
                rebuild_id=candidate.rebuild_id,
                tenant_id=candidate.tenant_id,
                run_id=candidate.run_id,
                attempt_id=candidate.attempt_id,
                source_incarnation=candidate.source_incarnation,
                source_authorization_epoch=candidate.source_authorization_epoch,
                successor_incarnation=candidate.successor_incarnation,
                successor_authorization_epoch=candidate.successor_authorization_epoch,
                successor_open_event_id=candidate.successor_open_event_id,
                end_event_id=candidate.end_event_id,
                last_redis_id=candidate.last_redis_id,
            )

    activated = await activate_v4_successor_rebuild(Activations(), ready)
    assert activated is not None
    assert calls == [ready]
    bad_end_event_id = "wrong-end"
    bad_receipt_digest = successor_receipt_digest(
        stream_key=ready.stream_key,
        stream_incarnation=ready.successor_incarnation,
        entry_count=ready.entry_count,
        open_event_id=ready.open_event_id,
        terminal_event_id=ready.terminal_event_id,
        end_event_id=bad_end_event_id,
        last_redis_id=ready.last_redis_id,
        item_redis_ids=ready.item_redis_ids,
        last_envelope_digest=ready.last_envelope_digest,
    )
    with pytest.raises(ValueError, match="terminal_receipt"):
        await activate_v4_successor_rebuild(
            Activations(),
            replace(
                ready,
                end_event_id=bad_end_event_id,
                receipt_digest=bad_receipt_digest,
            ),
        )


@pytest.mark.parametrize(
    "field",
    (
        "stream_key",
        "stream_incarnation",
        "entry_count",
        "open_event_id",
        "end_event_id",
        "terminal_event_id",
        "last_envelope_bytes",
    ),
)
async def test_successor_rebuild_application_binds_every_receipt_field(field: str) -> None:
    claim = _successor_claim_with_terminal()
    terminal_id = claim.items[-1].event_id
    receipt = V4SuccessorRebuildReceipt(
        stream_key=stream_key(
            tenant_scope_value=claim.tenant_scope,
            run_id=claim.run_id,
            stream_incarnation=claim.successor_incarnation,
        ),
        stream_incarnation=claim.successor_incarnation,
        entry_count=len(claim.items) + 2,
        open_event_id=claim.successor_open_event_id,
        terminal_event_id=terminal_id,
        end_event_id=stream_end_event_id(terminal_id),
        last_redis_id="1-3",
        item_redis_ids=_successor_item_redis_ids(claim),
        last_envelope_bytes=_successor_end_bytes(claim),
    )
    wrong_values = {
        "stream_key": "wrong-key",
        "stream_incarnation": claim.successor_incarnation + 1,
        "entry_count": receipt.entry_count + 1,
        "open_event_id": "wrong-open",
        "end_event_id": "wrong-end",
        "terminal_event_id": "wrong-terminal",
        "last_envelope_bytes": claim.items[0].canonical_envelope_bytes,
    }
    replacement = {field: wrong_values[field], "receipt_digest": ""}
    if field == "entry_count":
        replacement["item_redis_ids"] = receipt.item_redis_ids + ("1-9",)
    if field == "last_envelope_bytes":
        replacement["last_envelope_digest"] = ""
    bad_receipt = replace(receipt, **replacement)

    class Rebuilds:
        async def prepare(self, **_kwargs):
            return claim

        async def mark_ready(self, *_args, **_kwargs):
            raise AssertionError("mark_ready received a mismatched receipt")

    class Transport:
        async def build(self, _claim):
            return bad_receipt

    with pytest.raises(ValueError, match="receipt_mismatch"):
        await build_v4_successor_rebuild(
            Rebuilds(),
            Transport(),
            tenant_id=claim.tenant_id,
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            source_incarnation=claim.source_incarnation,
            claim_ttl=timedelta(seconds=30),
        )

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("transport", "receipt", "cas"))
async def test_successor_rebuild_application_never_returns_ready_after_failure(
    failure: str,
) -> None:
    claim = _successor_claim_with_terminal()
    calls: list[str] = []

    class Rebuilds:
        async def prepare(self, **_kwargs):
            calls.append("prepare")
            return claim

        async def mark_ready(self, _claim, *, receipt):
            calls.append("mark_ready")
            if failure == "cas":
                return False
            return True

    class Transport:
        async def build(self, _claim):
            calls.append("transport")
            if failure == "transport":
                raise RuntimeError("transport failed")
            terminal_id = claim.items[-1].event_id
            from app.streaming.domain.public_events_v4 import stream_end_event_id

            receipt = V4SuccessorRebuildReceipt(
                stream_key=stream_key(
                    tenant_scope_value=claim.tenant_scope,
                    run_id=claim.run_id,
                    stream_incarnation=claim.successor_incarnation,
                ),
                stream_incarnation=claim.successor_incarnation,
                entry_count=len(claim.items) + 2,
                open_event_id=claim.successor_open_event_id,
                terminal_event_id=terminal_id,
                end_event_id=stream_end_event_id(terminal_id),
                last_redis_id="1-3",
                item_redis_ids=_successor_item_redis_ids(claim),
                last_envelope_bytes=_successor_end_bytes(claim),
            )
            if failure == "receipt":
                return replace(receipt, terminal_event_id="wrong", receipt_digest="")
            return receipt

    if failure == "transport":
        with pytest.raises(RuntimeError, match="transport failed"):
            await build_v4_successor_rebuild(
                Rebuilds(),
                Transport(),
                tenant_id=claim.tenant_id,
                run_id=claim.run_id,
                attempt_id=claim.attempt_id,
                source_incarnation=claim.source_incarnation,
                claim_ttl=timedelta(seconds=30),
            )
    elif failure == "receipt":
        with pytest.raises(ValueError, match="receipt_mismatch"):
            await build_v4_successor_rebuild(
                Rebuilds(),
                Transport(),
                tenant_id=claim.tenant_id,
                run_id=claim.run_id,
                attempt_id=claim.attempt_id,
                source_incarnation=claim.source_incarnation,
                claim_ttl=timedelta(seconds=30),
            )
    else:
        result = await build_v4_successor_rebuild(
            Rebuilds(),
            Transport(),
            tenant_id=claim.tenant_id,
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            source_incarnation=claim.source_incarnation,
            claim_ttl=timedelta(seconds=30),
        )
        assert result is None
    assert (
        "mark_ready" not in calls
        if failure in {"transport", "receipt"}
        else calls[-1] == "mark_ready"
    )


def test_publication_claim_keeps_only_validated_canonical_envelope_bytes() -> None:
    envelope = project_public_v4(_row({"delta": "hello"}), authority=_authority())
    assert envelope is not None
    claim = V4PublicationClaim(
        event_id="evt4_a",
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="tenant-a",
        stream_incarnation=2,
        authorization_epoch=4,
        sequence=7,
        canonical_envelope_bytes=canonical_json_bytes(envelope),
        claim_token="claim-a",
        claim_expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )

    decoded = json.loads(claim.canonical_envelope_bytes)
    decoded["payload"]["delta"] = "mutated"

    assert json.loads(claim.canonical_envelope_bytes)["payload"] == {"delta": "hello"}
    assert b"__stream_v4" not in claim.canonical_envelope_bytes
    with pytest.raises(ValueError, match="envelope_mismatch"):
        replace(claim, run_id="other-run")


def test_publication_claim_rejects_noncanonical_or_invalid_envelope_bytes() -> None:
    envelope = project_public_v4(_row({"delta": "hello"}), authority=_authority())
    assert envelope is not None
    noncanonical = json.dumps(envelope, ensure_ascii=False).encode()

    with pytest.raises(ValueError, match="envelope_mismatch"):
        V4PublicationClaim(
            event_id="evt4_a",
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            tenant_scope="tenant-a",
            stream_incarnation=2,
            authorization_epoch=4,
            sequence=7,
            canonical_envelope_bytes=noncanonical,
            claim_token="claim-a",
            claim_expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        )


class _PublicationClaimCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _PublicationClaimConnection:
    def __init__(self, *, malformed_event: bool = False):
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.authority = {
            "tenant_id": "tenant-a",
            "tenant_scope": "tenant-a",
            "run_id": "run-a",
            "attempt_id": "attempt-a",
            "stream_incarnation": 2,
            "authorization_epoch": 4,
            "design_id": "ai-platform.redis-streams-sse-event-channel.v4",
            "projection_version": "public-stream-v4",
            "state": "confirmed",
            "revocation_state": "active",
        }
        self.event = _row({"delta": "" if malformed_event else "hello"})
        self.event["stream_publication_claim_expires_at"] = datetime(
            2026, 1, 1, 0, 1, tzinfo=timezone.utc
        )

    async def execute(self, statement: str, params: object):
        normalized = " ".join(statement.lower().split())
        self.statements.append((normalized, params))
        if normalized.startswith("select id, tenant_id from runs"):
            return _PublicationClaimCursor({"id": "run-a", "tenant_id": "tenant-a"})
        if normalized.startswith("select tenant_id, tenant_scope"):
            return _PublicationClaimCursor(dict(self.authority))
        if normalized.startswith("select event.id from run_events"):
            return _PublicationClaimCursor({"id": "evt4_a"})
        if normalized.startswith("update run_events as event set stream_publication_claim_token = %s"):
            return _PublicationClaimCursor(dict(self.event))
        if normalized.startswith("update run_events as event"):
            return _PublicationClaimCursor({"id": "evt4_a"})
        raise AssertionError(statement)


def _publication_claim_transaction_factory(conn: _PublicationClaimConnection):
    @asynccontextmanager
    async def transaction():
        try:
            yield conn
        except Exception:
            conn.rollbacks += 1
            raise
        else:
            conn.commits += 1

    return transaction


@pytest.mark.asyncio
async def test_publication_claim_locks_run_then_authority_and_validates_before_commit() -> None:
    conn = _PublicationClaimConnection()
    adapter = PostgresV4PublicationClaims(
        _publication_claim_transaction_factory(conn),
        claim_token_factory=lambda: "claim-a",
    )

    claim = await adapter.claim_next(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
        claim_ttl=timedelta(seconds=30),
    )

    assert claim is not None
    assert claim.tenant_scope == "tenant-a"
    assert claim.authorization_epoch == 4
    assert json.loads(claim.canonical_envelope_bytes)["payload"] == {"delta": "hello"}
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert [statement.split()[0:3] for statement, _params in conn.statements[:4]] == [
        ["select", "id,", "tenant_id"],
        ["select", "tenant_id,", "tenant_scope,"],
        ["select", "event.id", "from"],
        ["update", "run_events", "as"],
    ]


@pytest.mark.asyncio
async def test_publication_claim_rolls_back_malformed_row_and_rejects_stale_authority() -> None:
    malformed = _PublicationClaimConnection(malformed_event=True)
    malformed_adapter = PostgresV4PublicationClaims(
        _publication_claim_transaction_factory(malformed),
        claim_token_factory=lambda: "claim-a",
    )
    with pytest.raises(RuntimeError, match="projection_invalid"):
        await malformed_adapter.claim_next(
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
        )
    assert malformed.commits == 0
    assert malformed.rollbacks == 1

    stale = _PublicationClaimConnection()
    stale.authority["attempt_id"] = "attempt-new"
    stale_adapter = PostgresV4PublicationClaims(
        _publication_claim_transaction_factory(stale),
        claim_token_factory=lambda: "claim-a",
    )
    with pytest.raises(V4PublicationAuthorityError, match="authority_conflict"):
        await stale_adapter.claim_next(
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            stream_incarnation=2,
        )
    assert not any(statement.startswith("select event.id") for statement, _ in stale.statements)


@pytest.mark.asyncio
async def test_publication_disposition_sql_locks_authority_and_counts_only_transport_attempts() -> None:
    published_conn = _PublicationClaimConnection()
    published_adapter = PostgresV4PublicationClaims(
        _publication_claim_transaction_factory(published_conn),
        claim_token_factory=lambda: "claim-published",
    )
    published_claim = await published_adapter.claim_next(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
    )
    assert published_claim is not None
    published_conn.statements.clear()
    assert await published_adapter.mark_published(published_claim, redis_id="1-0") is True
    published_sql = published_conn.statements[-1][0]
    assert [statement.split()[0:3] for statement, _ in published_conn.statements] == [
        ["select", "id,", "tenant_id"],
        ["select", "tenant_id,", "tenant_scope,"],
        ["update", "run_events", "as"],
    ]
    assert "stream_publication_attempts = coalesce" in published_sql
    assert "'publication_attempts'" in published_sql
    assert "'authorization_epoch' = %s" in published_sql

    retry_conn = _PublicationClaimConnection()
    retry_adapter = PostgresV4PublicationClaims(
        _publication_claim_transaction_factory(retry_conn),
        claim_token_factory=lambda: "claim-retry",
    )
    retry_claim = await retry_adapter.claim_next(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
    )
    assert retry_claim is not None
    retry_conn.statements.clear()
    assert await retry_adapter.schedule_retry(
        retry_claim,
        error="redis_unavailable",
        delay=timedelta(seconds=5),
    ) is True
    assert "stream_publication_attempts = coalesce" in retry_conn.statements[-1][0]

    release_conn = _PublicationClaimConnection()
    release_adapter = PostgresV4PublicationClaims(
        _publication_claim_transaction_factory(release_conn),
        claim_token_factory=lambda: "claim-release",
    )
    release_claim = await release_adapter.claim_next(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        stream_incarnation=2,
    )
    assert release_claim is not None
    release_conn.statements.clear()
    assert await release_adapter.release(release_claim) is True
    assert "stream_publication_attempts" not in release_conn.statements[-1][0]


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
    from app.streaming.infrastructure import v4

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
    from app.streaming.infrastructure import v4

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
        authority=object(),
        pending_admissions=Pending(),
        event_persistence=object(),
        publication_claims=object(),
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
async def test_parent_finalization_publishes_child_and_distinct_parent(monkeypatch):
    from app.streaming.application import worker_publication_v4

    calls: list[object] = []

    async def finalize(transaction_factory, payload, reconciled_parent):
        calls.append(("finalize", transaction_factory, payload.run_id, reconciled_parent))
        return {"parent_run_id": "run-parent"}

    async def publish(_capabilities, *, tenant_id, run_id):
        calls.append(("publish", tenant_id, run_id))
        return True

    monkeypatch.setattr(worker_publication_v4, "publish_pending_run_terminal", publish)
    @asynccontextmanager
    async def transaction_factory():
        yield object()

    capabilities = WorkerV4Capabilities(
        authority=object(),
        pending_admissions=object(),
        event_persistence=object(),
        publication_claims=object(),
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

        async def list_pending_admissions(self, *, limit):
            return (pending,)

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
        authority=object(),
        pending_admissions=pending_store,
        event_persistence=object(),
        publication_claims=object(),
        publication_transport=transport,
    )
    assert await publish_pending_admissions(capabilities, limit=1) == 0
    assert pending_store.confirmed == 0
    transport.outage = False
    assert await publish_pending_admissions(capabilities, limit=1) == 1
    assert pending_store.confirmed == 1


@pytest.mark.asyncio
async def test_due_publication_bounds_scopes_and_drains_delayed_remainder():
    claims = [_publication_claim(event_id=f"evt4_{index}", sequence=7 + index) for index in range(3)]

    class ClaimStore:
        def __init__(self):
            self.remaining = list(claims)
            self.published = []
            self.scopes = 0

        async def list_due_scopes(self, *, limit):
            self.scopes += 1
            if not self.remaining:
                return ()
            return (V4PublicationScope("tenant-a", "run-a", "attempt-a", 2),)

        async def claim_next(self, **kwargs):
            return self.remaining.pop(0) if self.remaining else None

        async def mark_published(self, claim, *, redis_id):
            self.published.append((claim.event_id, redis_id))
            return True

        async def schedule_retry(self, claim, *, error, delay):
            self.remaining.insert(0, claim)
            return True

        async def release(self, claim):
            self.remaining.insert(0, claim)
            return True

    class Transport:
        async def publish(self, canonical_envelope_bytes):
            return f"redis-{len(canonical_envelope_bytes)}"

    store = ClaimStore()
    assert await publish_due_v4_events(store, Transport(), scope_limit=1, event_limit=2) == 2
    assert len(store.remaining) == 1
    assert await publish_due_v4_events(store, Transport(), scope_limit=1, event_limit=2) == 1
    assert store.remaining == []
