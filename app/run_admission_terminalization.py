"""Durable terminalization for committed runs rejected at queue admission."""

from __future__ import annotations

from psycopg import AsyncConnection

from app.persistence import chat_submissions as persistence_chat_submissions

from app.bootstrap.run_diagnostics import build_run_diagnostics_service
from app.runs.api import (
    RunDiagnosticsService,
    RunLifecycleService,
    RunTerminalizationProgress,
)
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    exception_chain_from_error,
)
from app.streaming.api import WorkerV4Capabilities
from app.run_admission_policy import (
    PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    contains_persisted_platform_multi_agent_control,
)


async def terminalize_enqueue_failure_with_v4(
    v4_capabilities: WorkerV4Capabilities,
    conn: AsyncConnection,
    *,
    lifecycle: RunLifecycleService,
    tenant_id: str,
    user_id: str | None,
    run_id: str,
    trace_id: str,
    diagnostic_error: BaseException | None = None,
    run_diagnostics: RunDiagnosticsService | None = None,
) -> RunTerminalizationProgress:
    """Compensate deterministic queue rejection with its durable v4 terminal row."""

    await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=f"enqueue_failure_{run_id}",
    )
    progress = await lifecycle.mark_run_enqueue_failed(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        trace_id=trace_id,
    )
    if not progress.did_transition:
        raise RuntimeError("enqueue_failure_terminal_transition_missing")
    if diagnostic_error is not None:
        run_diagnostics = run_diagnostics or build_run_diagnostics_service()
        await run_diagnostics.capture_failure_result(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=None,
            source="run_admission",
            stage="queue_enqueue",
            error_code="queue_enqueue_failed",
            result_json={
                "runtime_diagnostics": {
                    "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
                    "error_code": "queue_enqueue_failed",
                    "failure_source": "run_admission",
                    "failure_stage": "queue_enqueue",
                    "sdk": {
                        "exception_type": type(diagnostic_error).__name__,
                        "exception_message": str(diagnostic_error),
                        "exception_chain": exception_chain_from_error(
                            diagnostic_error
                        ),
                    },
                }
            },
        )
    terminal_row = await v4_capabilities.event_persistence.append_terminal_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if terminal_row is None:
        raise RuntimeError("enqueue_failure_v4_terminal_row_missing")
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
) -> bool:
    rejected = str(run.get("error_code") or "") == PLATFORM_MULTI_AGENT_NOT_SUPPORTED or (
        execution_snapshot is not None
        and contains_persisted_platform_multi_agent_control(run.get("input_json"))
    )
    if not rejected:
        return False
    await persistence_chat_submissions.finalize_chat_submission(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        submission_id=submission_id,
        state="admission_rejected",
        rejection_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    )
    return True
