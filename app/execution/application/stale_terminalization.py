"""Execution-owned event and audit composition for stale Run terminalization."""

from __future__ import annotations

from typing import Any

from app.runs.api import RunLifecycleService


async def stage_stale_run_reconciliation(
    lifecycle: RunLifecycleService,
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
    claim_token: str | None = None,
    expected_attempt_id: str | None = None,
    expected_owner_generation: int = 0,
    append_event: Any,
    append_audit_log: Any,
) -> dict[str, Any] | None:
    """Stage via the Runs port, then compose the Execution-owned facts once."""

    if expected_status not in {"queued", "running"}:
        raise ValueError("invalid_stale_run_expected_status")
    if terminal_status not in {"failed", "cancelled"}:
        raise ValueError("invalid_stale_run_terminal_status")

    staged = await lifecycle.stage_stale_run_reconciliation(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        run_id=run_id,
        expected_status=expected_status,
        stale_before=stale_before,
        cancel_requested_before=cancel_requested_before,
        terminal_status=terminal_status,
        error_code=error_code,
        error_message=error_message,
        claim_token=claim_token,
        expected_attempt_id=expected_attempt_id,
        expected_owner_generation=expected_owner_generation,
    )
    if staged is None or staged.get("already_staged"):
        return dict(staged) if staged is not None else None

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
