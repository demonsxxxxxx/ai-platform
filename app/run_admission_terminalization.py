"""Durable terminalization for committed runs rejected at queue admission."""

from __future__ import annotations

from psycopg import AsyncConnection

from app import repositories
from app.control_plane_contracts import standard_trace_id
from app.run_admission_policy import PLATFORM_MULTI_AGENT_NOT_SUPPORTED


async def terminalize_retired_platform_multi_agent_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    run_id: str,
    trace_id: str | None = None,
) -> repositories.ToolPermissionTerminalizationProgress:
    """Fail one committed run and record the retired-control admission decision."""

    error_message = "Platform multi-agent orchestration is no longer supported."
    progress = await repositories.fail_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
        error_message=error_message,
        result_json={"message": error_message, "retryable": False},
    )
    if progress.did_transition:
        await repositories.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="run_failed",
            stage="control",
            message="Run rejected because its persisted input uses retired platform orchestration.",
            severity="error",
            visible_to_user=False,
            error_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
            payload={
                "visible_to_user": False,
                "error_code": PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
                "retryable": False,
            },
        )
        await repositories.append_audit_log(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="run.admission.rejected",
            target_type="run",
            target_id=run_id,
            trace_id=trace_id or standard_trace_id(run_id),
            payload_json={
                "error_code": PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
                "reason": "retired_platform_multi_agent_control",
                "retryable": False,
            },
        )
    return progress
