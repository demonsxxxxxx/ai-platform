"""Durable terminalization for committed runs rejected at queue admission."""

from __future__ import annotations

from psycopg import AsyncConnection

from app import repositories
from app.runs.api import RunTerminalizationProgress
from app.streaming.api import WorkerV4Capabilities
from app.tool_permission_lifecycle import fail_run_with_v4
from app.run_admission_policy import (
    PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON,
    contains_persisted_platform_multi_agent_control,
)


async def terminalize_enqueue_failure_with_v4(
    v4_capabilities: WorkerV4Capabilities,
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    run_id: str,
    trace_id: str,
) -> RunTerminalizationProgress:
    """Compensate deterministic queue rejection with its durable v4 terminal row."""

    await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=f"enqueue_failure_{run_id}",
    )
    progress = await repositories.mark_run_enqueue_failed(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        trace_id=trace_id,
    )
    if not progress.did_transition:
        raise RuntimeError("enqueue_failure_terminal_transition_missing")
    terminal_row = await v4_capabilities.event_persistence.append_terminal_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if terminal_row is None:
        raise RuntimeError("enqueue_failure_v4_terminal_row_missing")
    return progress


async def terminalize_retired_platform_multi_agent_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    v4_capabilities: WorkerV4Capabilities,
) -> RunTerminalizationProgress:
    """Stage a retired-control failure through the durable terminalization lifecycle."""

    error_message = "Platform multi-agent orchestration is no longer supported."
    progress = await fail_run_with_v4(
        conn,
        capabilities=v4_capabilities,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
        error_message=error_message,
        result_json={"message": error_message, "retryable": False},
        terminal_reason=RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON,
    )
    return progress


async def reject_chat_submission_for_retired_platform_multi_agent(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    submission_id: str,
    run_id: str,
    run: dict[str, object],
    execution_snapshot: dict[str, object] | None,
    v4_capabilities: WorkerV4Capabilities,
) -> bool:
    rejected = str(run.get("error_code") or "") == PLATFORM_MULTI_AGENT_NOT_SUPPORTED or (
        execution_snapshot is not None
        and contains_persisted_platform_multi_agent_control(run.get("input_json"))
    )
    if not rejected:
        return False
    if str(run.get("error_code") or "") != PLATFORM_MULTI_AGENT_NOT_SUPPORTED:
        await terminalize_retired_platform_multi_agent_run(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            v4_capabilities=v4_capabilities,
        )
    await repositories.finalize_chat_submission(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        state="admission_rejected",
        rejection_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    )
    return True
