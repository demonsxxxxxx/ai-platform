"""Runs terminal composition for assistant persistence and provider coverage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.context.api import commit_provider_turn, release_provider_lineage
from app.runs.domain.terminalization import RunTerminalizationProgress


async def complete_run_with_context(
    conn: Any, *, complete_run: Callable[..., Awaitable[bool]],
    tenant_id: str, run_id: str, result_json: dict[str, Any],
) -> bool:
    completed = await complete_run(
        conn, tenant_id=tenant_id, run_id=run_id, result_json=result_json,
    )
    if completed:
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    return completed


async def fail_run_with_context(
    conn: Any, *, fail_run: Callable[..., Awaitable[RunTerminalizationProgress]],
    tenant_id: str, run_id: str, error_code: str, error_message: str,
    result_json: dict[str, Any] | None = None,
    terminal_reason: str = "run_failed",
) -> RunTerminalizationProgress:
    progress = await fail_run(
        conn, tenant_id=tenant_id, run_id=run_id, error_code=error_code,
        error_message=error_message, result_json=result_json,
        **({"terminal_reason": terminal_reason} if terminal_reason != "run_failed" else {}),
    )
    if progress.is_terminal():
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    return progress


async def cancel_run_with_context(
    conn: Any, *, cancel_run: Callable[..., Awaitable[RunTerminalizationProgress]],
    tenant_id: str, run_id: str, result_json: dict[str, Any] | None = None,
) -> RunTerminalizationProgress:
    progress = await cancel_run(
        conn, tenant_id=tenant_id, run_id=run_id,
        result_json=result_json,
    )
    if progress.is_terminal():
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    return progress


async def progress_run_terminalization_with_context(
    conn: Any, *, progress_terminalization: Callable[..., Awaitable[RunTerminalizationProgress | None]],
    tenant_id: str, run_id: str,
) -> RunTerminalizationProgress | None:
    progress = await progress_terminalization(
        conn, tenant_id=tenant_id, run_id=run_id,
    )
    if progress is not None and progress.is_terminal():
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    return progress


async def mark_run_enqueue_failed_with_context(
    conn: Any, *, mark_run_enqueue_failed: Callable[..., Awaitable[RunTerminalizationProgress]],
    tenant_id: str, user_id: str | None, run_id: str, trace_id: str | None,
) -> RunTerminalizationProgress:
    progress = await mark_run_enqueue_failed(
        conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id, trace_id=trace_id,
    )
    if progress.is_terminal():
        await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)
    return progress


async def converge_terminal_provider_lineage(
    conn: Any, *, tenant_id: str, run_id: str,
) -> None:
    await release_provider_lineage(conn, tenant_id=tenant_id, run_id=run_id)


async def persist_assistant_with_provider_coverage(
    conn: Any, *, append_message: Callable[..., Awaitable[str]],
    tenant_id: str, session_id: str, run_id: str, attempt_id: str,
    executor_type: str, content: str, metadata_json: dict[str, Any],
    provider_final_sequence: int | None,
) -> str:
    message_id = await append_message(
        conn, tenant_id=tenant_id, session_id=session_id, run_id=run_id,
        role="assistant", content=content, metadata_json=metadata_json,
    )
    if executor_type == "claude-agent-worker":
        await commit_provider_turn(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            assistant_message_id=message_id, final_sequence=provider_final_sequence,
        )
    return message_id
