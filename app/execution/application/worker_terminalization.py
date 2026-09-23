from __future__ import annotations

from typing import Any, Awaitable, Callable


async def fail_worker_pre_dispatch_error(
    conn,
    *,
    payload: Any,
    run_identity: dict[str, str],
    error_code: str,
    error_message: str,
    event_stage: str,
    event_payload: dict[str, Any],
    v4_capabilities: Any,
    attempt_lifecycle: Any,
    fail_run_and_reconcile: Callable[..., Awaitable[tuple[bool, Any]]],
    append_event: Callable[..., Awaitable[Any]],
    outcome_factory: Callable[..., Any],
    terminal_factory: Callable[..., Any],
    is_multi_agent_child: bool | None = None,
) -> Any:
    terminal_written, reconciled_parent = await fail_run_and_reconcile(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
        is_multi_agent_child=is_multi_agent_child,
        v4_capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
    )
    if not terminal_written:
        return terminal_factory(
            outcome_factory(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
            None,
        )
    await append_event(
        conn,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        event_type="error",
        stage=event_stage,
        message=error_message,
        payload=event_payload,
    )
    return terminal_factory(
        outcome_factory("failed", run_identity["run_id"], error_code, error_message),
        payload,
        reconciled_parent,
    )


async def fail_locked_run_snapshot(
    conn,
    *,
    payload: Any,
    locked_run: object,
    run_identity: dict[str, str],
    trace_id: str,
    v4_capabilities: Any,
    attempt_lifecycle: Any,
    fail_run_and_reconcile: Callable[..., Awaitable[tuple[bool, Any]]],
    append_denial_evidence: Callable[..., Awaitable[Any]],
    locked_run_principal: Callable[..., Any],
    locked_run_is_multi_agent_child: Callable[..., bool],
    capability_record: Callable[..., Any],
    denied_capability_decision: Callable[..., Any],
    outcome_factory: Callable[..., Any],
    terminal_factory: Callable[..., Any],
) -> Any:
    error_code = "capability_not_authorized"
    error_message = "Capability is not authorized for this run"
    principal = locked_run_principal(locked_run, run_identity)
    denial = capability_record(
        "skill",
        run_identity["skill_id"],
        denied_capability_decision("locked_snapshot_invalid"),
    )
    terminal_written, reconciled_parent = await fail_run_and_reconcile(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
        is_multi_agent_child=locked_run_is_multi_agent_child(locked_run),
        v4_capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
    )
    if not terminal_written:
        return terminal_factory(
            outcome_factory(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
            None,
        )
    await append_denial_evidence(
        conn,
        denial=denial,
        principal=principal,
        run_identity=run_identity,
        trace_id=trace_id,
        policy="locked_run_snapshot",
        error_message=error_message,
    )
    return terminal_factory(
        outcome_factory("failed", run_identity["run_id"], error_code, error_message),
        payload,
        reconciled_parent,
    )


async def fail_worker_capability_authorization(
    conn,
    *,
    payload: Any,
    authorization: Any,
    run_identity: dict[str, str],
    trace_id: str,
    v4_capabilities: Any,
    attempt_lifecycle: Any,
    fail_run_and_reconcile: Callable[..., Awaitable[tuple[bool, Any]]],
    append_denial_evidence: Callable[..., Awaitable[Any]],
    outcome_factory: Callable[..., Any],
    terminal_factory: Callable[..., Any],
    policy: str = "capability_distribution",
) -> Any:
    denial = authorization.denial
    if denial is None:
        raise RuntimeError("worker_capability_denial_missing")
    required_tool_denial = denial.decision.decision_reason.startswith("required_tool_")
    error_code = "required_tool_unavailable" if required_tool_denial else "capability_not_authorized"
    error_message = "Capability is not authorized for this run"
    terminal_written, reconciled_parent = await fail_run_and_reconcile(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
        v4_capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
    )
    if not terminal_written:
        return terminal_factory(
            outcome_factory(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
            None,
        )
    await append_denial_evidence(
        conn,
        denial=denial,
        principal=authorization.principal,
        run_identity=run_identity,
        trace_id=trace_id,
        policy=policy,
        error_message=error_message,
    )
    return terminal_factory(
        outcome_factory("failed", run_identity["run_id"], error_code, error_message),
        payload,
        reconciled_parent,
    )


__all__ = [
    "fail_locked_run_snapshot",
    "fail_worker_capability_authorization",
    "fail_worker_pre_dispatch_error",
]
