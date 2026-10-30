"""Sandbox executor terminal receipt PostgreSQL operations."""

from __future__ import annotations

import json
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size
from app.sandbox.domain.lease_errors import (
    SandboxExecutorTerminalConflictError,
    SandboxLeaseReleaseScopeMismatchError,
)
from app.sandbox.api import ExecutorTerminalReceiptIdentity

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


async def _lock_cancelling_executor_terminal_scope(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str | None,
    user_id: str | None,
    session_id: str | None,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    identity: ExecutorTerminalReceiptIdentity,
) -> None:
    """Recheck the original live lease under Run -> lease -> Attempt locks."""
    if (
        not isinstance(identity, ExecutorTerminalReceiptIdentity)
        or identity.run_id != run_id
        or identity.attempt_id != attempt_id
        or not identity.callback_token_id
        or identity.session_id != session_id
        or not all((workspace_id, user_id, session_id))
    ):
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_terminal_scope_mismatch"
        )
    run = await (
        await connection.execute(
            """
            select id from runs
            where tenant_id = %s and id = %s
              and workspace_id = %s and user_id = %s and session_id = %s
              and status = 'running' and cancel_requested_at is not null
            for update
            """,
            (tenant_id, run_id, workspace_id, user_id, session_id),
        )
    ).fetchone()
    if run is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_terminal_scope_mismatch"
        )

    query = """
        select lease.* from sandbox_leases lease
        join run_attempts attempt on attempt.tenant_id = lease.tenant_id
          and attempt.run_id = lease.run_id and attempt.id = lease.attempt_id
        join runs on runs.tenant_id = attempt.tenant_id and runs.id = attempt.run_id
        where lease.tenant_id = %s and lease.workspace_id = %s and lease.user_id = %s
          and lease.session_id = %s and lease.run_id = %s and lease.attempt_id = %s
          and lease.lease_payload_json->>'attempt_id' = lease.attempt_id
          and lease.lease_payload_json->>'callback_token_id' = %s
          and lease.lease_payload_json->>'owner_generation' = (attempt.owner_generation - 1)::text
          and attempt.status = 'cancel_requested' and attempt.owner_kind = 'queue_worker'
          and runs.workspace_id = lease.workspace_id and runs.user_id = lease.user_id
          and runs.session_id = lease.session_id
          and runs.status = 'running' and runs.cancel_requested_at is not null
          and lease.status = 'active' and lease.released_at is null
          and lease.expires_at is not null and lease.expires_at > clock_timestamp()
        order by lease.id asc
    """
    params = (
        tenant_id,
        workspace_id,
        user_id,
        session_id,
        run_id,
        attempt_id,
        identity.callback_token_id,
    )
    leases = list(
        await (
            await connection.execute(query + " for update of lease", params)
        ).fetchall()
    )
    if len(leases) != 1 or str(leases[0].get("id") or "") != lease_id:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_terminal_scope_mismatch"
        )

    attempt = await (
        await connection.execute(
            """
            select id from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
              and status = 'cancel_requested' and owner_kind = 'queue_worker'
            for update
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    if attempt is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_terminal_scope_mismatch"
        )
    current_leases = list(
        await (await connection.execute(query, params)).fetchall()
    )
    if len(current_leases) != 1 or str(current_leases[0].get("id") or "") != lease_id:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_terminal_scope_mismatch"
        )

async def record_sandbox_executor_terminal(
    connection: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    lease_id: str,
    executor_status: str,
    terminal_result: dict[str, Any],
    claim_token: str | None = None,
    cancelling_terminal_identity: ExecutorTerminalReceiptIdentity | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    _validate_terminal_observation(
        run_id=run_id, executor_status=executor_status, terminal_result=terminal_result
    )
    if cancelling_terminal_identity is not None:
        await _lock_cancelling_executor_terminal_scope(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            attempt_id=attempt_id,
            lease_id=lease_id,
            identity=cancelling_terminal_identity,
        )
    cursor = await connection.execute(
        """
        select *
        from sandbox_leases
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and status = 'active'
        for update
        """,
        (lease_id, tenant_id, run_id, attempt_id),
    )
    current = await cursor.fetchone()
    if current is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_executor_terminal_scope_mismatch"
        )
    existing = current.get("executor_terminal_json")
    if existing is not None:
        if (
            existing == terminal_result
            and str(current.get("executor_status") or "") == executor_status
            and (
                claim_token is None
                or str(current.get("executor_reconciliation_claim_token") or "")
                == claim_token
            )
        ):
            return dict(current)
        raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_status = %s,
            executor_heartbeat_at = now(),
            executor_terminal_json = %s::jsonb,
            executor_terminal_received_at = now(),
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            executor_cleanup_status = 'waiting_settlement',
            executor_reconciliation_status = case
              when executor_reconciliation_context_json is null then 'waiting_terminal'
              else 'pending'
            end,
            heartbeat_at = now(),
            updated_at = now()
        where id = %s
          and tenant_id = %s
          and run_id = %s
          and attempt_id = %s
          and status = 'active'
          and executor_terminal_json is null
          and (
            %s::text is null
            or (
              executor_reconciliation_status = 'claimed'
              and executor_reconciliation_claim_token = %s
            )
          )
        returning *
        """,
        (
            executor_status,
            json.dumps(terminal_result, ensure_ascii=False),
            lease_id,
            tenant_id,
            run_id,
            attempt_id,
            claim_token,
            claim_token,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SandboxExecutorTerminalConflictError("sandbox_executor_terminal_conflict")
    return dict(row)

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
    legacy_settlement_compat: bool = False,
    cancelling_terminal_identity: ExecutorTerminalReceiptIdentity | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Record Sandbox execution status while Runs owns the terminal receipt."""
    _validate_terminal_observation(
        run_id=run_id, executor_status=executor_status, terminal_result=terminal_result
    )
    if cancelling_terminal_identity is not None:
        await _lock_cancelling_executor_terminal_scope(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            attempt_id=attempt_id,
            lease_id=lease_id,
            identity=cancelling_terminal_identity,
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
    compatibility_assignments = ""
    if legacy_settlement_compat:
        compatibility_assignments = """
            executor_reconciliation_status = case
              when executor_reconciliation_context_json is null then 'waiting_terminal'
              else 'pending'
            end,
            executor_reconciliation_claim_token = null,
            executor_reconciliation_claimed_at = null,
        """
    cursor = await connection.execute(
        f"""
        update sandbox_leases
        set executor_status = %s,
            executor_heartbeat_at = now(),
            executor_terminal_received_at = coalesce(executor_terminal_received_at, now()),
            executor_probe_claim_token = null,
            executor_probe_claimed_at = null,
            executor_cleanup_status = 'waiting_settlement',
            {compatibility_assignments}
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

async def record_sandbox_executor_terminal_diagnostics(
    connection: Any,
    *,
    lease_id: str,
    claim_token: str,
    diagnostics: list[str],
) -> bool:
    """Durably retain safe reconciliation diagnostics for the active claim."""

    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_terminal_json = jsonb_set(
              executor_terminal_json,
              '{diagnostics}',
              %s::jsonb,
              true
            ),
            updated_at = now()
        where id = %s
          and status in ('active', 'released')
          and executor_terminal_json is not null
          and executor_reconciliation_status = 'claimed'
          and executor_reconciliation_claim_token = %s
        returning id
        """,
        (json.dumps(diagnostics, ensure_ascii=False), lease_id, claim_token),
    )
    return await cursor.fetchone() is not None
