"""Runs-owned early Worker dispatch terminalization on one locked connection."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerEarlyTerminalOutcome:
    status: str
    run_id: str
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class WorkerEarlyTerminal:
    outcome: WorkerEarlyTerminalOutcome
    payload: Any


class WorkerEarlyFailureService:
    def __init__(
        self, *, fail_result: Callable[..., dict[str, Any]],
        append_event: Callable[..., Awaitable[Any]],
        record_denial_audit: Callable[..., Awaitable[None]],
        locked_principal: Callable[..., Any],
        denied_capability: Callable[..., Any],
    ) -> None:
        self._fail_result = fail_result
        self._append_event = append_event
        self._record_denial_audit = record_denial_audit
        self._locked_principal = locked_principal
        self._denied_capability = denied_capability

    async def fail_run(
        self, conn: Any, *, payload: Any, tenant_id: str,
        run_id: str, error_code: str, error_message: str,
        capabilities: Any, attempt_lifecycle: Any,
        result_json: dict[str, Any] | None = None,
    ) -> bool:
        result_json = result_json or self._fail_result(error_code, "worker", reason=error_code)
        return await attempt_lifecycle.fail(
            conn, capabilities=capabilities, error_code=error_code,
            error_message=error_message, result_json=result_json,
        )

    @staticmethod
    def _stale(payload: Any, run_id: str) -> WorkerEarlyTerminal:
        return WorkerEarlyTerminal(
            WorkerEarlyTerminalOutcome(
                "skipped", run_id, "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
        )

    async def pre_dispatch_error(
        self, conn: Any, *, payload: Any, run_identity: dict[str, str],
        error_code: str, error_message: str, event_stage: str,
        event_payload: dict[str, Any], v4_capabilities: Any, attempt_lifecycle: Any,
    ) -> WorkerEarlyTerminal:
        written = await self.fail_run(
            conn, payload=payload, tenant_id=run_identity["tenant_id"],
            run_id=run_identity["run_id"], error_code=error_code,
            error_message=error_message, capabilities=v4_capabilities,
            attempt_lifecycle=attempt_lifecycle,
        )
        if not written:
            return self._stale(payload, run_identity["run_id"])
        await self._append_event(
            conn, tenant_id=run_identity["tenant_id"], run_id=run_identity["run_id"],
            event_type="error", stage=event_stage,
            message=error_message, payload=event_payload,
        )
        return WorkerEarlyTerminal(
            WorkerEarlyTerminalOutcome("failed", run_identity["run_id"], error_code, error_message),
            payload,
        )

    async def _denial_evidence(
        self, conn: Any, *, denial: Any, principal: Any,
        run_identity: dict[str, str], trace_id: str,
        policy: str, error_message: str,
    ) -> None:
        await self._append_event(
            conn, tenant_id=run_identity["tenant_id"], run_id=run_identity["run_id"],
            event_type="capability_not_authorized", stage="authorization",
            message=error_message,
            payload={
                "capability_kind": denial.capability_kind,
                "capability_id": denial.capability_id,
                "policy": policy, "reason": denial.decision.decision_reason,
                "visible_to_user": True, "severity": "error",
            },
        )
        await self._record_denial_audit(
            conn, denial=denial, principal=principal,
            run_identity=run_identity, trace_id=trace_id,
        )

    async def invalid_snapshot(
        self, conn: Any, *, payload: Any, locked_run: Any,
        run_identity: dict[str, str], trace_id: str,
        v4_capabilities: Any, attempt_lifecycle: Any,
    ) -> WorkerEarlyTerminal:
        code, message = "capability_not_authorized", "Capability is not authorized for this run"
        principal = self._locked_principal(locked_run, run_identity)
        denial = self._denied_capability("skill", run_identity["skill_id"], "locked_snapshot_invalid")
        written = await self.fail_run(
            conn, payload=payload, tenant_id=run_identity["tenant_id"],
            run_id=run_identity["run_id"], error_code=code, error_message=message,
            capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
        )
        if not written:
            return self._stale(payload, run_identity["run_id"])
        await self._denial_evidence(
            conn, denial=denial, principal=principal, run_identity=run_identity,
            trace_id=trace_id, policy="locked_run_snapshot", error_message=message,
        )
        return WorkerEarlyTerminal(
            WorkerEarlyTerminalOutcome("failed", run_identity["run_id"], code, message), payload,
        )

    async def capability_denial(
        self, conn: Any, *, payload: Any, authorization: Any,
        run_identity: dict[str, str], trace_id: str,
        v4_capabilities: Any, attempt_lifecycle: Any,
        policy: str = "capability_distribution",
    ) -> WorkerEarlyTerminal:
        denial = authorization.denial
        if denial is None:
            raise RuntimeError("worker_capability_denial_missing")
        required_tool_denial = denial.decision.decision_reason.startswith("required_tool_")
        code = "required_tool_unavailable" if required_tool_denial else "capability_not_authorized"
        message = "Capability is not authorized for this run"
        written = await self.fail_run(
            conn, payload=payload, tenant_id=run_identity["tenant_id"],
            run_id=run_identity["run_id"], error_code=code, error_message=message,
            capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
        )
        if not written:
            return self._stale(payload, run_identity["run_id"])
        await self._denial_evidence(
            conn, denial=denial, principal=authorization.principal,
            run_identity=run_identity, trace_id=trace_id,
            policy=policy, error_message=message,
        )
        return WorkerEarlyTerminal(
            WorkerEarlyTerminalOutcome("failed", run_identity["run_id"], code, message), payload,
        )
