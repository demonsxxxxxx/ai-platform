"""Compose Run terminal transactions with provider and v4 terminal facts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.context.api import commit_provider_turn, release_provider_lineage
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.domain.terminalization import RunTerminalizationProgress
from app.streaming.api import (
    WorkerV4Capabilities,
    append_run_terminal_v4_row,
)


async def _record_terminalization_facts(
    conn: Any,
    *,
    capabilities: WorkerV4Capabilities | None,
    tenant_id: str,
    run_id: str,
    is_terminal: bool,
    did_transition: bool,
) -> Any | None:
    if is_terminal:
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    if capabilities is None:
        return None
    return await append_run_terminal_v4_row(
        capabilities,
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        did_transition=did_transition,
    )


async def record_run_terminalization(
    conn: Any,
    *,
    capabilities: WorkerV4Capabilities | None,
    tenant_id: str,
    run_id: str,
    progress: RunTerminalizationProgress,
) -> Any | None:
    """Record provider lineage and the v4 row for one Run transition."""

    return await _record_terminalization_facts(
        conn,
        capabilities=capabilities,
        tenant_id=tenant_id,
        run_id=run_id,
        is_terminal=progress.is_terminal(),
        did_transition=progress.did_transition,
    )


async def complete_run_terminalization(
    conn: Any,
    *,
    lifecycle: RunLifecycleService,
    capabilities: WorkerV4Capabilities,
    tenant_id: str,
    run_id: str,
    result_json: dict[str, Any],
) -> bool:
    completed = await lifecycle.complete_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        result_json=result_json,
    )
    await _record_terminalization_facts(
        conn,
        capabilities=capabilities,
        tenant_id=tenant_id,
        run_id=run_id,
        is_terminal=completed,
        did_transition=completed,
    )
    return completed


async def fail_run_terminalization(
    conn: Any,
    *,
    lifecycle: RunLifecycleService,
    capabilities: WorkerV4Capabilities,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    result_json: dict[str, Any] | None = None,
    terminal_reason: str = "run_failed",
) -> RunTerminalizationProgress:
    progress = await lifecycle.fail_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=error_code,
        error_message=error_message,
        result_json=result_json,
        terminal_reason=terminal_reason,
    )
    await record_run_terminalization(
        conn,
        capabilities=capabilities,
        tenant_id=tenant_id,
        run_id=run_id,
        progress=progress,
    )
    return progress


async def cancel_run_terminalization(
    conn: Any,
    *,
    lifecycle: RunLifecycleService,
    capabilities: WorkerV4Capabilities,
    tenant_id: str,
    run_id: str,
    result_json: dict[str, Any] | None = None,
) -> RunTerminalizationProgress:
    progress = await lifecycle.cancel_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        result_json=result_json,
    )
    await record_run_terminalization(
        conn,
        capabilities=capabilities,
        tenant_id=tenant_id,
        run_id=run_id,
        progress=progress,
    )
    return progress


async def progress_run_terminalization(
    conn: Any,
    *,
    lifecycle: RunLifecycleService,
    tenant_id: str,
    run_id: str,
    capabilities: WorkerV4Capabilities | None = None,
) -> RunTerminalizationProgress | None:
    progress = await lifecycle.progress_run_terminalization(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if progress is not None:
        await record_run_terminalization(
            conn,
            capabilities=capabilities,
            tenant_id=tenant_id,
            run_id=run_id,
            progress=progress,
        )
    return progress


async def finish_run_terminalization(
    *,
    lifecycle: RunLifecycleService,
    tenant_id: str,
    run_id: str,
    transaction_factory: Callable[[], Any],
    capabilities: WorkerV4Capabilities,
    attempt_lifecycle: RunAttemptLifecycleService,
    attempt_id: str | None = None,
    attempt_error_code: str | None = None,
) -> RunTerminalizationProgress | None:
    async with transaction_factory() as conn:
        progress = await progress_run_terminalization(
            conn,
            lifecycle=lifecycle,
            capabilities=capabilities,
            tenant_id=tenant_id,
            run_id=run_id,
        )
        if attempt_id is not None and progress is not None and progress.is_terminal():
            await attempt_lifecycle.terminalize(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                status=str(progress.status),
                terminal_reason=f"run_{progress.status}",
                error_code=attempt_error_code if progress.status == "failed" else None,
            )
        return progress


async def persist_assistant_with_provider_coverage(
    conn: Any,
    *,
    append_message: Callable[..., Awaitable[str]],
    tenant_id: str,
    session_id: str,
    run_id: str,
    attempt_id: str,
    executor_type: str,
    content: str,
    metadata_json: dict[str, Any],
    provider_final_sequence: int | None,
) -> str:
    message_id = await append_message(
        conn,
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
        role="assistant",
        content=content,
        metadata_json=metadata_json,
    )
    if executor_type == "claude-agent-worker":
        await commit_provider_turn(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            assistant_message_id=message_id,
            final_sequence=provider_final_sequence,
        )
    return message_id
