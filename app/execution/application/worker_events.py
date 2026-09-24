from __future__ import annotations

from typing import Any

from app import repositories
from app.execution.application.worker_result_projection import int_payload_value
from app.execution.application.worker_step_projection import (
    multi_agent_result_summary,
    step_key_from_event,
)


AGENT_STEP_EVENT_STATUS = {
    "agent_step_started": "running",
    "agent_step_reused": "succeeded",
    "agent_step_completed": "succeeded",
    "agent_step_blocked": "failed",
    "agent_step_failed": "failed",
}


async def append_user_event(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    latency_ms: int | None = None,
    input_token_count: int | None = None,
    output_token_count: int | None = None,
    total_token_count: int | None = None,
    estimated_cost_minor: int | None = None,
) -> None:
    # Terminal transitions own their durable terminal event and audit.
    if event_type in {"run_failed", "run_cancelled"}:
        return
    merged = {"visible_to_user": True, "severity": "info"}
    if payload:
        merged.update(payload)
    event_kwargs: dict[str, Any] = {}
    if trace_id is not None:
        event_kwargs["trace_id"] = trace_id
    if latency_ms is not None:
        event_kwargs["latency_ms"] = latency_ms
    if input_token_count is not None:
        event_kwargs["input_token_count"] = input_token_count
    if output_token_count is not None:
        event_kwargs["output_token_count"] = output_token_count
    if total_token_count is not None:
        event_kwargs["total_token_count"] = total_token_count
    if estimated_cost_minor is not None:
        event_kwargs["estimated_cost_minor"] = estimated_cost_minor
    await repositories.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type=event_type,
        stage=stage,
        message=message,
        payload=merged,
        **event_kwargs,
    )


async def attach_multi_agent_result_summary(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    result_capabilities: dict[str, bool],
    result_payload: dict[str, Any],
) -> None:
    if not result_capabilities.get("multi_agent"):
        return
    steps = await repositories.list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    result_payload["multi_agent"] = multi_agent_result_summary(steps)


async def record_run_step_from_event(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    message: str,
    payload: dict[str, Any] | None,
) -> None:
    status = AGENT_STEP_EVENT_STATUS.get(event_type)
    if status is None:
        return
    event_payload = dict(payload or {})
    if status != "pending":
        event_payload["checkpoint_reuse_pending"] = False
    role = str(event_payload.get("role") or "agent")
    step_id = await repositories.upsert_run_step(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        step_key=step_key_from_event(event_payload),
        step_kind=str(event_payload.get("step_kind") or "agent"),
        status=status,
        title=str(event_payload.get("title") or message or role),
        role=role,
        sequence=int_payload_value(event_payload, "step_index", 0),
        payload_json=event_payload,
    )
    if (
        status == "succeeded"
        and event_payload.get("output") is not None
        and event_payload.get("checkpoint_id")
        and not event_payload.get("source_step_id")
    ):
        await repositories.upsert_run_step(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key_from_event(event_payload),
            step_kind=str(event_payload.get("step_kind") or "agent"),
            status=status,
            title=str(event_payload.get("title") or message or role),
            role=role,
            sequence=int_payload_value(event_payload, "step_index", 0),
            payload_json={"source_step_id": step_id},
        )
