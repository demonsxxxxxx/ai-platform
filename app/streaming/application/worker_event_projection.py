"""Project worker-owned user events into the durable Streaming ledger."""

from collections.abc import Awaitable, Callable
from typing import Any


async def append_worker_user_event(
    conn: Any,
    *,
    append_event: Callable[..., Awaitable[Any]],
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
    # Runs terminalization writes its own authoritative failed/cancelled facts.
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
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type=event_type,
        stage=stage,
        message=message,
        payload=merged,
        **event_kwargs,
    )
