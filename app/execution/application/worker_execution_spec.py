"""Execution-spec preparation for worker dispatch and reconciliation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.runs.api import ExecutionSpec, compile_execution_spec_for_dispatch


async def prepare_worker_execution_spec(
    conn: Any,
    *,
    payload: Any,
    run_identity: dict[str, str],
    trace_id: str,
    attempt_id: str,
    attempt_lifecycle: Any,
    context_loader: Callable[[], Awaitable[dict[str, Any] | None]],
    context_pack_builder: Callable[[dict[str, Any]], dict[str, Any]],
    project_run_payload: Callable[..., Any],
    mcp_attacher: Callable[..., Awaitable[Any]],
    principal: Any,
) -> tuple[ExecutionSpec | None, Any | None, dict[str, Any] | None]:
    """Prepare one immutable spec without rereading mutable context on replay."""

    if attempt_lifecycle.is_reconciliation:
        execution_spec = await attempt_lifecycle.restore_reconciliation_execution_spec(
            conn
        )
        run_payload = project_run_payload(
            execution_spec,
            attempt_id=attempt_id,
        )
        run_payload = await mcp_attacher(
            conn,
            principal=principal,
            run_payload=run_payload,
        )
        return execution_spec, run_payload, None

    context_ref = await context_loader()
    if context_ref is None:
        return None, None, None
    execution_spec = compile_execution_spec_for_dispatch(
        run_identity=run_identity,
        queue_payload=payload,
        trace_id=trace_id,
        context_snapshot_id=str(context_ref["context_snapshot_id"]),
        context_snapshot=context_ref["context_snapshot"],
        context_pack={
            **context_pack_builder(context_ref["context_snapshot"]),
            "conversation_context": context_ref["conversation_context"],
        },
    )
    run_payload = project_run_payload(
        execution_spec,
        attempt_id=attempt_id,
    )

    run_payload = await mcp_attacher(
        conn,
        principal=principal,
        run_payload=run_payload,
    )
    return execution_spec, run_payload, context_ref
