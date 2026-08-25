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
