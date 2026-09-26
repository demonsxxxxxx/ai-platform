"""Application orchestration for durable Run lifecycle transitions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.runs.domain.terminalization import (
    RunTerminalizationProgress,
    progress_for_requested_status,
)


def _result_observability_values(result_json: dict[str, Any] | None) -> tuple[int | None, int, int, int, int]:
    result = result_json or {}
    token_counts = result.get("token_counts") if isinstance(result.get("token_counts"), dict) else {}
    cost = result.get("cost") if isinstance(result.get("cost"), dict) else {}
    try:
        latency_ms = int(result.get("latency_ms")) if result.get("latency_ms") is not None else None
    except (TypeError, ValueError):
        latency_ms = None
    def integer(value: Any, default: int = 0) -> int:
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default
    input_tokens = integer(token_counts.get("input"))
    output_tokens = integer(token_counts.get("output"))
    total_tokens = integer(token_counts.get("total"), input_tokens + output_tokens)
    estimated_cost_minor = integer(cost.get("estimated_cost_minor"))
    return latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor


class RunLifecyclePersistence(Protocol):
    """Transaction-scoped PostgreSQL capabilities used by the lifecycle service."""

    async def stage_run_terminalization(self, conn: object, **kwargs: Any) -> dict[str, Any] | None: ...
    async def load_staged_terminalization(self, conn: object, **kwargs: Any) -> dict[str, Any] | None: ...
    async def clear_cancel_requested_terminalization(self, conn: object, **kwargs: Any) -> dict[str, Any] | None: ...
    async def finalize_staged_terminalization(self, conn: object, **kwargs: Any) -> dict[str, Any] | None: ...
    async def complete_run(self, conn: object, **kwargs: Any) -> bool: ...
    async def mark_run_running(self, conn: object, **kwargs: Any) -> dict[str, Any] | None: ...
    async def is_cancel_requested(self, conn: object, **kwargs: Any) -> bool: ...
    async def classify_success_commit_block(self, conn: object, **kwargs: Any) -> str: ...
    async def list_stale_run_reconciliation_candidates(self, conn: object, **kwargs: Any) -> list[dict[str, Any]]: ...


class RunEventWriter(Protocol):
    async def __call__(self, conn: object, **kwargs: Any) -> Any: ...


class RunAuditWriter(Protocol):
    async def __call__(self, conn: object, **kwargs: Any) -> Any: ...


class RunLifecycleService:
    """Coordinate Run lifecycle policy using an explicit connection and ports."""

    def __init__(
        self,
        *,
        persistence: RunLifecyclePersistence,
        append_event: RunEventWriter,
        append_audit_log: RunAuditWriter,
        validate_result_size: Callable[[dict[str, Any] | None], None],
        sanitize_payload: Callable[[Any], Any],
        sanitize_text: Callable[[object], str],
        make_trace_id: Callable[[str], str],
    ) -> None:
        self._persistence = persistence
        self._append_event = append_event
        self._append_audit_log = append_audit_log
        self._validate_result_size = validate_result_size
        self._sanitize_payload = sanitize_payload
        self._sanitize_text = sanitize_text
        self._make_trace_id = make_trace_id

    async def complete_run(
        self, conn: object, *, tenant_id: str, run_id: str, result_json: dict[str, Any]
    ) -> bool:
        self._validate_result_size(result_json)
        return await self._persistence.complete_run(
            conn, tenant_id=tenant_id, run_id=run_id, result_json=result_json,
            observability=_result_observability_values(result_json),
        )

    async def fail_run(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
        error_code: str,
        error_message: str,
        result_json: dict[str, Any] | None = None,
        terminal_reason: str = "run_failed",
    ) -> RunTerminalizationProgress:
        self._validate_result_size(result_json)
        staged = await self._persistence.stage_run_terminalization(
            conn, tenant_id=tenant_id, run_id=run_id, target_status="failed",
            terminal_reason=terminal_reason, result_json=result_json,
            error_code=error_code, error_message=error_message,
        )
        if staged is None:
            return RunTerminalizationProgress(completed=False, status=None)
        if str(staged.get("terminalization_target") or "") != "failed":
            return RunTerminalizationProgress(
                completed=False, status=str(staged.get("terminalization_target") or "") or None
            )
        return progress_for_requested_status(
            await self.progress_run_terminalization(conn, tenant_id=tenant_id, run_id=run_id),
            requested_status="failed",
        )

    async def cancel_run(
        self, conn: object, *, tenant_id: str, run_id: str,
        result_json: dict[str, Any] | None = None,
    ) -> RunTerminalizationProgress:
        self._validate_result_size(result_json)
        staged = await self._persistence.stage_run_terminalization(
            conn, tenant_id=tenant_id, run_id=run_id, target_status="cancelled",
            terminal_reason="run_cancelled", result_json=result_json,
        )
        if staged is None:
            return RunTerminalizationProgress(completed=False, status=None)
        if str(staged.get("terminalization_target") or "") != "cancelled":
            return RunTerminalizationProgress(
                completed=False, status=str(staged.get("terminalization_target") or "") or None
            )
        return progress_for_requested_status(
            await self.progress_run_terminalization(conn, tenant_id=tenant_id, run_id=run_id),
            requested_status="cancelled",
        )

    async def progress_run_terminalization(
        self, conn: object, *, tenant_id: str, run_id: str,
    ) -> RunTerminalizationProgress | None:
        staged = await self._persistence.load_staged_terminalization(
            conn, tenant_id=tenant_id, run_id=run_id
        )
        if staged is None:
            return None
        run_status = str(staged.get("status") or "")
        if run_status in {"succeeded", "failed", "cancelled"}:
            return RunTerminalizationProgress(
                completed=True, status=run_status, did_transition=False
            )
        target_status = str(staged.get("terminalization_target") or "")
        if target_status == "cancel_requested":
            await self._persistence.clear_cancel_requested_terminalization(
                conn, tenant_id=tenant_id, run_id=run_id
            )
            return RunTerminalizationProgress(completed=False, status="running")
        if target_status not in {"failed", "cancelled"}:
            return RunTerminalizationProgress(completed=False, status=target_status or None)
        terminal_reason = str(staged.get("terminalization_reason") or "run_terminalized")
        result_payload = staged.get("terminalization_result_json")
        if not isinstance(result_payload, dict):
            result_payload = {}
        result_observability = _result_observability_values(result_payload)
        prior_observability = (
            staged.get("latency_ms"), int(staged.get("input_token_count") or 0),
            int(staged.get("output_token_count") or 0), int(staged.get("total_token_count") or 0),
            int(staged.get("estimated_cost_minor") or 0),
        )
        observability = tuple(
            incoming if incoming not in (None, 0) else prior
            for incoming, prior in zip(result_observability, prior_observability)
        )
        final = await self._persistence.finalize_staged_terminalization(
            conn, tenant_id=tenant_id, run_id=run_id, target_status=target_status,
            latency_ms=observability[0], input_token_count=observability[1],
            output_token_count=observability[2], total_token_count=observability[3],
            estimated_cost_minor=observability[4],
        )
        if final is None:
            return RunTerminalizationProgress(completed=False, status=target_status)
        if bool(final.get("already_terminal")):
            return RunTerminalizationProgress(completed=True, status=target_status, did_transition=False)
        artifact_count = int(final.get("artifact_count") or 0)
        latency_ms = final.get("latency_ms")
        input_tokens = int(final.get("input_token_count") or 0)
        output_tokens = int(final.get("output_token_count") or 0)
        total_tokens = int(final.get("total_token_count") or 0)
        estimated_cost_minor = int(final.get("estimated_cost_minor") or 0)
        message = "Run failed" if target_status == "failed" else "任务已取消"
        event_type = "run_failed" if target_status == "failed" else "run_cancelled"
        stage = "worker" if target_status == "failed" else "control"
        event_payload: dict[str, Any] = {
            "visible_to_user": True,
            "severity": "error" if target_status == "failed" else "warning",
            "artifact_count": artifact_count,
            "result_status": target_status,
            "result": self._sanitize_payload(result_payload),
        }
        if target_status == "failed" and staged.get("terminalization_error_code"):
            event_payload["error_code"] = str(staged["terminalization_error_code"])
            safe_message = self._sanitize_text(staged.get("terminalization_error_message"))
            if safe_message:
                event_payload["error_message"] = safe_message
        await self._append_event(
            conn, tenant_id=tenant_id, run_id=run_id,
            trace_id=staged.get("trace_id"), event_type=event_type, stage=stage,
            message=message, payload=event_payload, visible_to_user=True,
            latency_ms=latency_ms, input_token_count=input_tokens,
            output_token_count=output_tokens, total_token_count=total_tokens,
            estimated_cost_minor=estimated_cost_minor,
        )
        await self._append_audit_log(
            conn, tenant_id=tenant_id, user_id=None,
            action=f"run.{target_status}", target_type="run", target_id=run_id,
            trace_id=staged.get("trace_id"), payload_json={
                "reason": terminal_reason, "artifact_count": artifact_count,
                "latency_ms": latency_ms, "input_token_count": input_tokens,
                "output_token_count": output_tokens, "total_token_count": total_tokens,
                "estimated_cost_minor": estimated_cost_minor,
                "error_code": staged.get("terminalization_error_code"),
            },
        )
        return RunTerminalizationProgress(completed=True, status=target_status, did_transition=True)

    async def mark_run_enqueue_failed(
        self, conn: object, *, tenant_id: str, user_id: str | None,
        run_id: str, trace_id: str | None = None,
    ) -> RunTerminalizationProgress:
        error_code = "queue_enqueue_failed"
        error_message = "Queue admission failed; retry this run."
        progress = await self.fail_run(
            conn, tenant_id=tenant_id, run_id=run_id, error_code=error_code,
            error_message=error_message,
            result_json={"message": error_message, "retryable": True},
        )
        if progress.did_transition:
            await self._append_event(
                conn, tenant_id=tenant_id, run_id=run_id, trace_id=trace_id,
                event_type="queue_enqueue_failed", stage="queue",
                message="Queue admission failed; the run was marked failed.",
                payload={"visible_to_user": False, "error_code": error_code, "retryable": True},
            )
            await self._append_audit_log(
                conn, tenant_id=tenant_id, user_id=user_id,
                action="run.queue.enqueue_failed", target_type="run", target_id=run_id,
                trace_id=trace_id or self._make_trace_id(run_id),
                payload_json={"error_code": error_code, "retryable": True},
            )
        return progress

    async def classify_success_commit_block(self, conn: object, *, tenant_id: str, run_id: str) -> str:
        return await self._persistence.classify_success_commit_block(conn, tenant_id=tenant_id, run_id=run_id)

    async def is_cancel_requested(self, conn: object, *, tenant_id: str, run_id: str) -> bool:
        return await self._persistence.is_cancel_requested(conn, tenant_id=tenant_id, run_id=run_id)

    async def mark_run_running(self, conn: object, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return await self._persistence.mark_run_running(conn, tenant_id=tenant_id, run_id=run_id)

    async def list_stale_run_reconciliation_candidates(
        self,
        conn: object,
        *,
        stale_after_seconds: int,
        cancel_requested_after_seconds: int | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self._persistence.list_stale_run_reconciliation_candidates(
            conn,
            stale_after_seconds=stale_after_seconds,
            cancel_requested_after_seconds=cancel_requested_after_seconds,
            limit=limit,
        )

    async def progress_for_requested_status(
        self, conn: object, *, tenant_id: str, run_id: str, requested_status: str,
    ) -> RunTerminalizationProgress:
        return progress_for_requested_status(
            await self.progress_run_terminalization(conn, tenant_id=tenant_id, run_id=run_id),
            requested_status=requested_status,
        )
