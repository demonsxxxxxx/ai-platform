"""PostgreSQL persistence for one provider dispatch per durable Attempt."""

from __future__ import annotations

import json
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.sandbox.application.dispatch_intent import DispatchOutcome, SandboxDispatchIdentity


async def persist_intent(
    conn: AsyncConnection,
    *,
    identity: SandboxDispatchIdentity,
    reconciliation_context: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Commit the exact identity and recovery context before provider I/O."""
    context = {key: value for key, value in reconciliation_context.items()}
    lease_cursor = await conn.execute(
        """
        update sandbox_leases lease
        set executor_reconciliation_context_json = coalesce(
              executor_reconciliation_context_json, %s::jsonb
            ),
            executor_reconciliation_intent_context_json = coalesce(
              executor_reconciliation_intent_context_json, %s::jsonb
            ),
            executor_reconciliation_status = case
              when executor_terminal_json is not null
                and executor_reconciliation_status = 'waiting_terminal' then 'pending'
              else executor_reconciliation_status
            end,
            updated_at = now()
        from run_attempts attempt, runs
        where lease.id = %s
          and lease.tenant_id = %s and lease.run_id = %s and lease.attempt_id = %s
          and lease.status = 'active' and lease.runtime_handle_verified_at is not null
          and lease.lease_payload_json->>'attempt_id' = %s
          and lease.lease_payload_json->>'owner_generation' = %s
          and lease.provider = %s
          and attempt.tenant_id = lease.tenant_id and attempt.run_id = lease.run_id
          and attempt.id = lease.attempt_id and attempt.owner_generation = %s
          and attempt.status = 'running'
          and runs.tenant_id = lease.tenant_id and runs.id = lease.run_id
          and runs.status = 'running' and runs.cancel_requested_at is null
        returning lease.id
        """,
        (
            json.dumps(context, ensure_ascii=False),
            json.dumps(context, ensure_ascii=False),
            identity.lease_id,
            identity.tenant_id,
            identity.run_id,
            identity.attempt_id,
            identity.attempt_id,
            str(identity.owner_generation),
            identity.provider,
            identity.owner_generation,
        ),
    )
    if await lease_cursor.fetchone() is None:
        raise RuntimeError("sandbox_dispatch_attempt_scope_invalid")

    dispatch_cursor = await conn.execute(
        """
        insert into sandbox_execution_dispatches (
          dispatch_id, tenant_id, run_id, attempt_id, owner_generation,
          lease_id, provider, provider_task_id, status
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, 'intent')
        on conflict (tenant_id, run_id, attempt_id) do nothing
        returning *
        """,
        (
            uuid.uuid4().hex,
            identity.tenant_id,
            identity.run_id,
            identity.attempt_id,
            identity.owner_generation,
            identity.lease_id,
            identity.provider,
            identity.provider_task_id,
        ),
    )
    row = await dispatch_cursor.fetchone()
    if row is not None:
        return dict(row), True
    existing_cursor = await conn.execute(
        """
        select * from sandbox_execution_dispatches
        where tenant_id = %s and run_id = %s and attempt_id = %s
        for update
        """,
        (identity.tenant_id, identity.run_id, identity.attempt_id),
    )
    existing = await existing_cursor.fetchone()
    if existing is None:
        raise RuntimeError("sandbox_dispatch_intent_unavailable")
    existing = dict(existing)
    expected = {
        "owner_generation": identity.owner_generation,
        "lease_id": identity.lease_id,
        "provider": identity.provider,
        "provider_task_id": identity.provider_task_id,
    }
    if any(existing.get(key) != value for key, value in expected.items()):
        raise RuntimeError("sandbox_dispatch_identity_conflict")
    return existing, False


async def record_outcome(
    conn: AsyncConnection,
    *,
    identity: SandboxDispatchIdentity,
    outcome: DispatchOutcome,
) -> dict[str, Any] | None:
    """Record a monotonic provider observation; accepted evidence wins over unknown."""
    cursor = await conn.execute(
        """
        update sandbox_execution_dispatches
        set status = case
              when status = 'intent' then %s
              when status = 'unknown' and %s = 'accepted' then 'accepted'
              else status
            end,
            updated_at = now()
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and owner_generation = %s and lease_id = %s
          and provider = %s and provider_task_id = %s
        returning *
        """,
        (
            outcome,
            outcome,
            identity.tenant_id,
            identity.run_id,
            identity.attempt_id,
            identity.owner_generation,
            identity.lease_id,
            identity.provider,
            identity.provider_task_id,
        ),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None
