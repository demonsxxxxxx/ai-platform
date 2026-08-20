"""Receipt-aware stale-run terminalization application service."""

from __future__ import annotations

import json
from typing import Any


async def stage_stale_run_reconciliation(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None,
    run_id: str,
    expected_status: str,
    stale_before: Any,
    cancel_requested_before: Any | None = None,
    terminal_status: str,
    error_code: str | None,
    error_message: str | None,
    append_event: Any,
    append_audit_log: Any,
) -> dict[str, Any] | None:
    """CAS an ownerless stale run only when no executor terminal receipt exists."""

    if expected_status not in {"queued", "running"}:
        raise ValueError("invalid_stale_run_expected_status")
    if terminal_status not in {"failed", "cancelled"}:
        raise ValueError("invalid_stale_run_terminal_status")

    target_result = (
        {"message": "任务已取消", "reconciliation_reason": "stale_run_no_owner"}
        if terminal_status == "cancelled"
        else {
            "message": "Run interrupted because no live execution owner remains.",
            "retryable": True,
            "reconciliation_reason": "stale_run_no_owner",
        }
    )
    cursor = await conn.execute(
        """
        update runs
        set permission_terminalization_target = %s,
            permission_terminalization_reason = 'stale_run_no_owner',
            permission_terminalization_result_json = %s::jsonb,
            permission_terminalization_error_code = %s,
            permission_terminalization_error_message = %s
        where tenant_id = %s
          and workspace_id = %s
          and user_id is not distinct from %s
          and id = %s
          and status = %s
          and permission_terminalization_target is null
          and (%s <> 'cancelled' or cancel_requested_at is not null)
          and (%s <> 'failed' or cancel_requested_at is null)
          and not exists (
            select 1 from sandbox_leases
            where sandbox_leases.tenant_id = runs.tenant_id
              and sandbox_leases.run_id = runs.id
              and sandbox_leases.status = 'active'
          )
          and not exists (
            select 1 from sandbox_leases
            where sandbox_leases.tenant_id = runs.tenant_id
              and sandbox_leases.run_id = runs.id
              and sandbox_leases.executor_terminal_json is not null
              and sandbox_leases.executor_reconciliation_status is distinct from 'finalized'
          )
          and (
            (%s = 'cancelled' and cancel_requested_at <= %s::timestamptz)
            or
            (%s = 'failed' and greatest(
                  coalesce((select max(created_at) from run_events
                            where run_events.tenant_id = runs.tenant_id
                              and run_events.run_id = runs.id), '-infinity'::timestamptz),
                  coalesce(started_at, '-infinity'::timestamptz),
                  coalesce(queued_at, '-infinity'::timestamptz),
                  created_at
                ) <= %s::timestamptz)
          )
        returning id, trace_id, permission_terminalization_target
        """,
        (
            terminal_status,
            json.dumps(target_result, ensure_ascii=False, separators=(",", ":")),
            error_code,
            error_message,
            tenant_id,
            workspace_id,
            user_id,
            run_id,
            expected_status,
            terminal_status,
            terminal_status,
            terminal_status,
            cancel_requested_before,
            terminal_status,
            stale_before,
        ),
    )
    staged = await cursor.fetchone()
    if staged is None:
        return None

    event_payload: dict[str, Any] = {
        "visible_to_user": True,
        "severity": "warning" if terminal_status == "cancelled" else "error",
        "result_status": terminal_status,
        "reason": "stale_run_no_owner",
    }
    if error_code:
        event_payload["error_code"] = error_code
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=staged.get("trace_id"),
        event_type="stale_run_reconciled",
        stage="worker_maintenance",
        message=(
            "任务取消请求已在执行器丢失后收口"
            if terminal_status == "cancelled"
            else "任务因执行器丢失而中断"
        ),
        payload=event_payload,
        error_code=error_code,
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=None,
        action="run.stale.reconcile",
        target_type="run",
        target_id=run_id,
        trace_id=staged.get("trace_id"),
        payload_json={
            "workspace_id": workspace_id,
            "target_user_id": user_id,
            "expected_status": expected_status,
            "result_status": terminal_status,
            "reason": "stale_run_no_owner",
            "error_code": error_code,
        },
    )
    return dict(staged)
