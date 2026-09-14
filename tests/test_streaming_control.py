from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from app.streaming import redis as control
import json
from app.streaming.api import validate_internal_envelope_v4


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
        "open_event_id": control._semantic_id("ai-platform-stream-open-v4", "scope-a", "run-a", "attempt-a", 1),
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

    authority = await control.create_or_get_stream_admission_v4(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
    )

    insert_params = conn.calls[1][1]
    frozen_envelope = validate_internal_envelope_v4(json.loads(insert_params[8]))
    assert authority.state == "admission_pending"
    assert frozen_envelope["event_id"] == insert_params[7]
    assert frozen_envelope["event_type"] == "stream.open"
    assert frozen_envelope["stream_incarnation"] == 1
    assert control._sha256(insert_params[8]) == insert_params[9]

    confirmed_row = authority_row(
        state="confirmed", open_payload_digest=insert_params[9]
    )
    confirmation = ScriptedConnection([confirmed_row])
    confirmed = await control.confirm_stream_admission(
        confirmation,
        authority=replace(
            authority,
            open_payload_bytes=insert_params[8],
            open_payload_digest=insert_params[9],
        ),
    )
    assert confirmed.state == "confirmed"
    assert "terminal_publication_intents" not in confirmation.calls[0][0]


@pytest.mark.asyncio
async def test_admission_confirmation_preserves_an_existing_terminal_authority():
    terminal = control._authority(authority_row(state="terminal"))
    conn = ScriptedConnection([authority_row(state="terminal")])

    confirmed = await control.confirm_stream_admission(conn, authority=terminal)

    statement = " ".join(conn.calls[0][0].split()).lower()
    assert "when state = 'terminal' then 'terminal'" in statement
    assert "state in ('admission_pending','confirmed','terminal')" in statement
    assert confirmed.state == "terminal"


@pytest.mark.asyncio
async def test_stream_admission_rejects_a_different_attempt_instead_of_creating_parallel_authority():
    conn = ScriptedConnection([authority_row()])
    with pytest.raises(
        control.SseAuthorityConflictError, match="sse_stream_attempt_conflict"
    ):
        await control.create_or_get_stream_admission_v4(
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
