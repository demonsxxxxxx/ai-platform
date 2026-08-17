from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from app.streaming import redis as control
from app.streaming.redis import StreamEnvelope


class Result:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class ScriptedConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def execute(self, statement, params=()):
        self.calls.append((statement, params))
        return Result(self.rows.pop(0) if self.rows else None)


def authority_row(**overrides):
    row = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "tenant_scope": "scope-a",
        "stream_incarnation": 1,
        "state": "admission_pending",
        "open_event_id": control.stream_open_event_id(
            tenant_scope="scope-a",
            run_id="run-a",
            attempt_id="attempt-a",
            incarnation=1,
        ),
        "open_payload_bytes": "{}",
        "open_payload_digest": "digest-a",
        "authorization_epoch": 1,
        "revocation_state": "active",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_stream_admission_freezes_open_envelope_before_confirmation():
    inserted_row = authority_row()
    conn = ScriptedConnection([None, inserted_row])

    authority = await control.create_or_get_stream_admission(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
    )

    insert_params = conn.calls[1][1]
    frozen_envelope = StreamEnvelope.from_json(insert_params[8])
    assert authority.state == "admission_pending"
    assert frozen_envelope.event_id == insert_params[7]
    assert frozen_envelope.event_type == "stream_open"
    assert frozen_envelope.stream_incarnation == 1
    assert control._sha256(insert_params[8]) == insert_params[9]

    confirmed_row = authority_row(
        state="confirmed", open_payload_digest=insert_params[9]
    )
    confirmed = await control.confirm_stream_admission(
        ScriptedConnection([confirmed_row]),
        authority=replace(
            authority,
            open_payload_bytes=insert_params[8],
            open_payload_digest=insert_params[9],
        ),
    )
    assert confirmed.state == "confirmed"


@pytest.mark.asyncio
async def test_stream_admission_rejects_a_different_attempt_instead_of_creating_parallel_authority():
    conn = ScriptedConnection([authority_row()])
    with pytest.raises(
        control.SseAuthorityConflictError, match="sse_stream_attempt_conflict"
    ):
        await control.create_or_get_stream_admission(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-b",
            tenant_scope="scope-a",
        )
    assert len(conn.calls) == 1


def test_authority_lease_checks_authority_deadline_without_database_io():
    now = datetime.now(timezone.utc)
    lease = control.SseAuthorityLease(
        lease_id="lease-a",
        tenant_id="tenant-a",
        run_id="run-a",
        api_instance_id="api-a",
        connection_id="connection-a",
        authorization_epoch=4,
        lease_not_after=now + timedelta(seconds=15),
    )

    assert lease.allows_frame(now=now)
    assert not lease.allows_frame(now=now + timedelta(seconds=15))


@pytest.mark.asyncio
async def test_authority_lease_is_bounded_to_fifteen_seconds_and_revocation_fences_renewal():
    confirmed = authority_row(state="confirmed")
    lease_deadline = datetime.now(timezone.utc) + timedelta(seconds=15)
    lease_row = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "api_instance_id": "api-a",
        "connection_id": "connection-a",
        "authorization_epoch": 1,
        "lease_not_after": lease_deadline,
    }
    conn = ScriptedConnection([confirmed, lease_row])
    lease = await control.acquire_sse_authority_lease(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        api_instance_id="api-a",
        connection_id="connection-a",
        lease_seconds=15,
    )
    assert lease.authorization_epoch == 1
    assert conn.calls[1][1][-1] == 15
    assert "id=excluded.id" in conn.calls[1][0].replace(" ", "").lower()

    with pytest.raises(ValueError, match="sse_authority_lease_seconds_invalid"):
        await control.acquire_sse_authority_lease(
            ScriptedConnection([]),
            tenant_id="tenant-a",
            run_id="run-a",
            api_instance_id="api-a",
            connection_id="connection-a",
            lease_seconds=16,
        )


@pytest.mark.asyncio
async def test_stale_lease_generation_cannot_close_a_renewed_connection_lease():
    first_deadline = datetime.now(timezone.utc) + timedelta(seconds=15)
    second_deadline = first_deadline + timedelta(seconds=1)
    first_row = {
        "id": "lease-generation-a",
        "authorization_epoch": 1,
        "lease_not_after": first_deadline,
    }
    second_row = {
        "id": "lease-generation-b",
        "authorization_epoch": 1,
        "lease_not_after": second_deadline,
    }
    conn = ScriptedConnection(
        [
            authority_row(state="confirmed"),
            first_row,
            authority_row(state="confirmed"),
            second_row,
            None,
        ]
    )

    first = await control.acquire_sse_authority_lease(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        api_instance_id="api-a",
        connection_id="connection-a",
        lease_seconds=15,
    )
    second = await control.acquire_sse_authority_lease(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        api_instance_id="api-a",
        connection_id="connection-a",
        lease_seconds=15,
    )
    stale_close = await control.close_sse_authority_lease(
        conn,
        lease_id=first.lease_id,
        reason="stale_connection_closed",
    )

    assert first.lease_id != second.lease_id
    assert stale_close is False

    revoked_conn = ScriptedConnection(
        [authority_row(state="confirmed", revocation_state="committed")]
    )
    with pytest.raises(
        control.SseAuthorityConflictError, match="sse_authority_revoked"
    ):
        await control.acquire_sse_authority_lease(
            revoked_conn,
            tenant_id="tenant-a",
            run_id="run-a",
            api_instance_id="api-a",
            connection_id="connection-b",
            lease_seconds=15,
        )


def test_terminal_intent_freezes_stable_semantic_ids_exact_payload_bytes_and_hashes():
    first = control.freeze_terminal_intent(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=2,
        status="succeeded",
    )
    retry = control.freeze_terminal_intent(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=2,
        status="succeeded",
    )

    assert retry.terminal_event_id == first.terminal_event_id
    assert retry.end_event_id == first.end_event_id
    assert retry.terminal_payload_bytes == first.terminal_payload_bytes
    assert retry.terminal_payload_digest == first.terminal_payload_digest
    assert (
        control._sha256(first.terminal_payload_bytes) == first.terminal_payload_digest
    )
    assert control._sha256(first.end_payload_bytes) == first.end_payload_digest


@pytest.mark.asyncio
async def test_terminal_intent_duplicate_with_different_payload_hash_fails_closed():
    intent = control.freeze_terminal_intent(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=1,
        status="failed",
    )
    conflicting_row = {
        "id": "sti-existing",
        "tenant_id": intent.tenant_id,
        "run_id": intent.run_id,
        "attempt_id": intent.attempt_id,
        "stream_incarnation": intent.stream_incarnation,
        "terminal_event_id": intent.terminal_event_id,
        "end_event_id": intent.end_event_id,
        "terminal_payload_bytes": intent.terminal_payload_bytes,
        "terminal_payload_digest": "different",
        "end_payload_bytes": intent.end_payload_bytes,
        "end_payload_digest": intent.end_payload_digest,
        "emitted_at": intent.emitted_at,
        "state": "pending",
    }
    conn = ScriptedConnection([None, conflicting_row])

    with pytest.raises(
        control.SseAuthorityConflictError, match="sse_terminal_intent_conflict"
    ):
        await control.persist_terminal_intent(conn, intent=intent)


@pytest.mark.asyncio
async def test_terminal_publisher_emits_terminal_then_end_with_frozen_timestamp_and_bytes():
    intent = control.freeze_terminal_intent(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=1,
        status="succeeded",
    )
    authority = control._authority(authority_row(state="terminal"))

    class RecordingBridge:
        def __init__(self):
            self.envelopes = []

        async def append(self, envelope, *, terminal=False):
            self.envelopes.append((envelope, terminal))
            return control.StreamCursor("run-a", 1, f"1-{len(self.envelopes)}")

    bridge = RecordingBridge()
    await control.publish_terminal_intent(bridge, authority=authority, intent=intent)

    assert [item.event_type for item, _ in bridge.envelopes] == ["terminal", "end"]
    assert all(terminal for _, terminal in bridge.envelopes)
    assert [item.emitted_at for item, _ in bridge.envelopes] == [
        intent.emitted_at,
        intent.emitted_at,
    ]
    assert bridge.envelopes[0][0].payload["event_id"] == intent.terminal_event_id
    assert (
        bridge.envelopes[1][0].payload["terminal_event_id"] == intent.terminal_event_id
    )
