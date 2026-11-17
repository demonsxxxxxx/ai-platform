"""Sandbox executor terminal receipt persistence and durable handoff reads."""

from __future__ import annotations

import json
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size
from app.sandbox.domain.lease_errors import (
    SandboxExecutorTerminalConflictError,
    SandboxLeaseReleaseScopeMismatchError,
)


def _validate_terminal_observation(
    *, run_id: str, executor_status: str, terminal_result: dict[str, Any]
) -> None:
    ensure_json_size(
        terminal_result,
        max_bytes=RUN_RESULT_MAX_BYTES,
        code="executor_terminal_receipt_too_large",
    )
    if executor_status not in {"completed", "failed", "cancelled"}:
        raise ValueError("sandbox_executor_terminal_status_invalid")
    normalized_result_status = str(terminal_result.get("status") or "").strip().lower()
    if str(terminal_result.get("run_id") or "") != run_id:
        raise ValueError("sandbox_executor_terminal_run_id_invalid")
    allowed_result_statuses = {
        "completed": {"completed", "succeeded"},
        "failed": {"failed"},
        "cancelled": {"cancelled", "canceled"},
    }
    if normalized_result_status not in allowed_result_statuses[executor_status]:
        raise ValueError("sandbox_executor_terminal_result_status_invalid")




async def record_sandbox_executor_terminal_observation(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    executor_status: str,
    terminal_result: dict[str, Any],
    claim_token: str | None = None,
) -> dict[str, Any]:
    """Record Sandbox execution status while Runs owns the terminal receipt."""
    _validate_terminal_observation(
        run_id=run_id, executor_status=executor_status, terminal_result=terminal_result
    )
    cursor = await connection.execute(
        """
        select id, tenant_id, run_id, attempt_id, status, lease_payload_json,
               executor_status, executor_terminal_received_at,
               executor_probe_claim_token, executor_probe_claimed_at,
               executor_cleanup_status, executor_cleanup_claim_token,
               executor_cleanup_claimed_at, executor_cleanup_retry_not_before,
               executor_heartbeat_at, heartbeat_at, expires_at, updated_at
        from sandbox_leases
        where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
          and status = 'active'
        for update
        """,
        (lease_id, tenant_id, run_id, attempt_id),
    )
    current = await cursor.fetchone()
    if current is None:
        raise SandboxLeaseReleaseScopeMismatchError("sandbox_executor_terminal_scope_mismatch")
    current_status = str(current.get("executor_status") or "")
    if claim_token is not None and str(current.get("executor_probe_claim_token") or "") != claim_token:
        raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
    if current_status in {"completed", "failed", "cancelled"}:
        if current_status != executor_status:
            raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
        return dict(current)
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_status = %s,
            executor_heartbeat_at = now(),
            executor_terminal_received_at = coalesce(executor_terminal_received_at, now()),
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            executor_cleanup_status = 'waiting_settlement',
            heartbeat_at = now(), updated_at = now()
        where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
          and status = 'active'
          and executor_status not in ('completed', 'failed', 'cancelled')
          and (%s::text is null or executor_probe_claim_token = %s)
        returning id, tenant_id, run_id, attempt_id, status, lease_payload_json,
                  executor_status, executor_terminal_received_at,
                  executor_probe_claim_token, executor_probe_claimed_at,
                  executor_cleanup_status, executor_cleanup_claim_token,
                  executor_cleanup_claimed_at, executor_cleanup_retry_not_before,
                  executor_heartbeat_at, heartbeat_at, expires_at, updated_at
        """,
        (executor_status, lease_id, tenant_id, run_id, attempt_id, claim_token, claim_token),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
    return dict(row)




async def get_sandbox_executor_terminal_handoff(
    connection: Any, *, tenant_id: str, workspace_id: str, user_id: str,
    session_id: str, run_id: str, attempt_id: str, lease_id: str,
    owner_generation: int,
) -> dict[str, Any] | None:
    """Observe existing settlement responsibility; this grants no execution authority."""
    cursor = await connection.execute(
        """
        select lease.* from sandbox_leases lease
        join run_attempt_settlements detail
          on detail.tenant_id=lease.tenant_id and detail.run_id=lease.run_id
         and detail.attempt_id=lease.attempt_id and detail.source_lease_id=lease.id
        where lease.id=%s and lease.tenant_id=%s and lease.workspace_id=%s and lease.user_id=%s
          and lease.session_id=%s and lease.run_id=%s and lease.attempt_id=%s
          and lease.lease_payload_json->'owner_generation'=%s::jsonb
          and lease.lease_payload_json->>'attempt_id'=lease.attempt_id
          and detail.receipt_json is not null and lease.executor_terminal_received_at is not null
          and lease.executor_status in ('completed','failed','cancelled')
          and detail.accepted_context_json->>'adapter_name'<>''
          and ((lease.status='active' and lease.released_at is null
                and detail.settlement_phase in ('pending','claimed','retry','failed'))
            or (lease.status='released' and lease.released_at is not null
                and detail.settlement_phase='settled' and lease.executor_reconciled_at is not null))
        """,
        (lease_id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
         json.dumps(owner_generation)),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None
