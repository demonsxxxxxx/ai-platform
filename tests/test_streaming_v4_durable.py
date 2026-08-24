from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.streaming.application.durable_v4 import (
    V4PublicationClaim,
    V4PublicationTransportUnavailable,
    publish_claimed_v4_events,
)

from app.streaming.application.recovery_v4 import (
    V4SuccessorRebuildClaim,
    V4SuccessorRebuildItem,
)
from app.streaming.api import (
    build_v4_control,
    opaque_message_id,
    project_public_envelope_v4,
    project_public_v4,
    project_public_v4_successor,
    successor_stream_open_event_id,
)
from app.streaming.authority import RunCursor
from app.streaming.postgres import EventReceipt
from app.streaming.redis import StreamAuthority
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.infrastructure.postgres_v4 import (
    PostgresV4PublicationClaims,
    V4PublicationAuthorityError,
)
from app.streaming.v4 import (
    V4RedisStreamBridge,
    recover_v4_and_resume,
    list_pending_v4_rows,
)


def test_callback_v4_values_have_one_application_owner():
    from app.routes import runtime_callbacks
    from app.streaming import api, v4
    from app.streaming.application import callback_events_v4

    assert api.V4CallbackItem is callback_events_v4.V4CallbackItem
    assert v4.V4CallbackItem is callback_events_v4.V4CallbackItem
    assert api.callback_item_to_v4 is callback_events_v4.callback_item_to_v4
    assert v4.callback_item_to_v4 is callback_events_v4.callback_item_to_v4
    assert runtime_callbacks.callback_item_to_v4 is callback_events_v4.callback_item_to_v4


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
async def test_v4_redis_publication_transport_decodes_claimed_canonical_bytes() -> None:
    from app.streaming import worker_projection
    from app.streaming.redis import StreamTransportUnavailable

    claim = _publication_claim()
    calls = []

    class Bridge:
        async def append(self, envelope):
            calls.append(envelope)
            return "12-0"

    transport = worker_projection.V4RedisPublicationTransport(Bridge())
    assert await transport.publish(claim.canonical_envelope_bytes) == "12-0"
    assert calls[0]["event_id"] == claim.event_id

    class FailingBridge:
        async def append(self, _envelope):
            raise StreamTransportUnavailable("redis unavailable")

    with pytest.raises(V4PublicationTransportUnavailable) as exc_info:
        await worker_projection.V4RedisPublicationTransport(FailingBridge()).publish(
            claim.canonical_envelope_bytes
        )
    assert exc_info.value.error_code == "StreamTransportUnavailable"


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
    with pytest.raises(ValueError, match="successor_incarnation_invalid"):
        replace(claim, successor_incarnation=2)
    with pytest.raises(ValueError, match="envelope_mismatch"):
        replace(item, sequence=8)


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
