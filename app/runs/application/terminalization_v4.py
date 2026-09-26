"""Run lifecycle entrypoints that preserve provider and committed-v4 behavior."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.context.api import release_provider_lineage
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.domain.terminalization import RunTerminalizationProgress
from app.streaming.api import WorkerV4Capabilities, append_run_terminal_v4_row


async def complete_run_with_v4(
    conn: Any, *, lifecycle: RunLifecycleService, capabilities: WorkerV4Capabilities,
    tenant_id: str, run_id: str, result_json: dict[str, Any],
) -> bool:
    completed = await lifecycle.complete_run(conn, tenant_id=tenant_id, run_id=run_id, result_json=result_json)
    if completed:
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    await append_run_terminal_v4_row(capabilities, conn, tenant_id=tenant_id, run_id=run_id, did_transition=completed)
    return completed


async def fail_run_with_v4(
    conn: Any, *, lifecycle: RunLifecycleService, capabilities: WorkerV4Capabilities,
    tenant_id: str, run_id: str, error_code: str, error_message: str,
    result_json: dict[str, Any] | None = None, terminal_reason: str = "run_failed",
) -> RunTerminalizationProgress:
    progress = await lifecycle.fail_run(
        conn, tenant_id=tenant_id, run_id=run_id, error_code=error_code,
        error_message=error_message, result_json=result_json, terminal_reason=terminal_reason,
    )
    if progress.is_terminal():
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    await append_run_terminal_v4_row(capabilities, conn, tenant_id=tenant_id, run_id=run_id, did_transition=progress.did_transition)
    return progress


async def cancel_run_with_v4(
    conn: Any, *, lifecycle: RunLifecycleService, capabilities: WorkerV4Capabilities,
    tenant_id: str, run_id: str, result_json: dict[str, Any] | None = None,
) -> RunTerminalizationProgress:
    progress = await lifecycle.cancel_run(conn, tenant_id=tenant_id, run_id=run_id, result_json=result_json)
    if progress.is_terminal():
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    await append_run_terminal_v4_row(capabilities, conn, tenant_id=tenant_id, run_id=run_id, did_transition=progress.did_transition)
    return progress


async def progress_run_terminalization_with_v4(
    conn: Any, *, lifecycle: RunLifecycleService, capabilities: WorkerV4Capabilities,
    tenant_id: str, run_id: str,
) -> RunTerminalizationProgress | None:
    progress = await lifecycle.progress_run_terminalization(conn, tenant_id=tenant_id, run_id=run_id)
    if progress is not None and progress.is_terminal():
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    if progress is not None:
        await append_run_terminal_v4_row(capabilities, conn, tenant_id=tenant_id, run_id=run_id, did_transition=progress.did_transition)
    return progress


async def finish_run_terminalization(
    *, lifecycle: RunLifecycleService, tenant_id: str, run_id: str,
    transaction_factory: Callable[[], Any], capabilities: WorkerV4Capabilities,
    attempt_lifecycle: RunAttemptLifecycleService, attempt_id: str | None = None,
    attempt_error_code: str | None = None,
) -> RunTerminalizationProgress | None:
    async with transaction_factory() as conn:
        progress = await progress_run_terminalization_with_v4(
            conn, lifecycle=lifecycle, capabilities=capabilities,
            tenant_id=tenant_id, run_id=run_id,
        )
        if attempt_id is not None and progress is not None and progress.is_terminal():
            await attempt_lifecycle.terminalize(
                conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
                status=str(progress.status), terminal_reason=f"run_{progress.status}",
                error_code=attempt_error_code if progress.status == "failed" else None,
            )
        return progress
