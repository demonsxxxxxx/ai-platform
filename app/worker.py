import asyncio
import time as _time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app import repositories
from app.control_plane_contracts import artifact_lineage_contract, artifact_manifest_contract, standard_trace_id
from app.context_builder import record_initial_context_snapshot
from app.db import transaction
from app.executors.base import ExecutorResult, RunPayload
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.settings import get_settings
from app.tool_policy import evaluate_tool_policy


class _WorkerClock:
    @staticmethod
    def monotonic() -> float:
        return _time.monotonic()


time = _WorkerClock()


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str | None
    error_code: str | None = None
    error_message: str | None = None


class WorkerRunCancelled(asyncio.CancelledError):
    """Raised inside the worker when a running run observes a platform cancel request."""


def parse_queue_payload(raw: dict[str, Any]) -> QueueRunPayload:
    return QueueRunPayload.model_validate(raw)


def _strip_local_output_paths(message: str) -> str:
    lines = []
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith(("详细报告:", "批注文档:")) and "/tmp/" in stripped:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _artifact_download_url(artifact_id: str) -> str:
    return f"/api/ai/artifacts/{artifact_id}/download"


FORBIDDEN_ARTIFACT_MARKERS = ("/tmp/", "tenants/", "workspaces/", ":\\", ":/")
FORBIDDEN_ARTIFACT_KEYS = {
    "storage_key",
    "local_path",
    "review_result",
    "artifact_path",
    "output_path",
    "runner",
    "runner_path",
    "executable_path",
    "cwd",
}
NATIVE_USED_SKILL_SOURCES = {"executor_hook", "executor_native"}
RAGFLOW_AUDIT_PAYLOAD_KEYS = {"dataset_ids", "reference_ids"}


AGENT_STEP_EVENT_STATUS = {
    "agent_step_started": "running",
    "agent_step_reused": "succeeded",
    "agent_step_completed": "succeeded",
    "agent_step_blocked": "failed",
    "agent_step_failed": "failed",
}


def _sanitize_artifact_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in FORBIDDEN_ARTIFACT_KEYS:
                continue
            sanitized = _sanitize_artifact_manifest(item)
            if sanitized is not None:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = [_sanitize_artifact_manifest(item) for item in value]
        return [item for item in cleaned_items if item is not None]
    if isinstance(value, str) and any(marker in value for marker in FORBIDDEN_ARTIFACT_MARKERS):
        return None
    return value


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


def _append_artifact_links(message: str, artifact_records: list[dict[str, Any]]) -> str:
    base = _strip_local_output_paths(message)
    if not artifact_records:
        return base
    links = [f"- {item['label']}: {item['download_url']}" for item in artifact_records]
    suffix = "输出文件:\n" + "\n".join(links)
    return f"{base}\n\n{suffix}" if base else suffix


def _int_payload_value(payload: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _executor_observability(
    executor_payload: dict[str, Any],
    *,
    latency_ms: int,
) -> dict[str, Any]:
    input_tokens = _int_payload_value(executor_payload, "input_token_count")
    output_tokens = _int_payload_value(executor_payload, "output_token_count")
    total_tokens = _int_payload_value(executor_payload, "total_token_count", input_tokens + output_tokens)
    return {
        "latency_ms": latency_ms,
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {
            "estimated_cost_minor": _int_payload_value(executor_payload, "estimated_cost_minor"),
        },
    }


def _event_observability_kwargs(observability: dict[str, Any], executor_payload: dict[str, Any]) -> dict[str, Any]:
    metric_keys = {
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "estimated_cost_minor",
    }
    if not any(key in executor_payload for key in metric_keys):
        return {}
    token_counts = observability["token_counts"]
    return {
        "latency_ms": observability["latency_ms"],
        "input_token_count": token_counts["input"],
        "output_token_count": token_counts["output"],
        "total_token_count": token_counts["total"],
        "estimated_cost_minor": observability["cost"]["estimated_cost_minor"],
    }


def _ragflow_audit_payload(executor_payload: dict[str, Any]) -> dict[str, Any]:
    return {key: executor_payload[key] for key in RAGFLOW_AUDIT_PAYLOAD_KEYS if key in executor_payload}


def _step_key_from_event(payload: dict[str, Any]) -> str:
    explicit = payload.get("step_key")
    if explicit:
        return str(explicit)
    role = str(payload.get("role") or "agent").strip() or "agent"
    step_index = _int_payload_value(payload, "step_index", 1)
    return f"{role}-{step_index}"


def _normalize_step_status(status: object) -> str:
    value = str(status or "")
    return "cancelled" if value == "canceled" else value


def _multi_agent_result_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    summary_steps = []
    reused_step_keys = []
    completed_step_outputs = {}
    for row in steps:
        payload = row.get("payload_json") or {}
        if not isinstance(payload, dict):
            payload = {}
        step_key = str(row["step_key"])
        output = payload.get("output")
        checkpoint_reused = bool(payload.get("checkpoint_reused"))
        status = _normalize_step_status(row.get("status"))
        if checkpoint_reused:
            reused_step_keys.append(step_key)
        if output is not None and status == "succeeded":
            completed_step_outputs[step_key] = str(output)
        summary_step = {
            "step_key": step_key,
            "status": status,
            "role": row.get("role"),
            "sequence": _int_payload_value(row, "sequence", 0),
            "depends_on": list(payload.get("depends_on") or []),
            "checkpoint_reused": checkpoint_reused,
            "output": str(output) if output is not None else None,
            "error_code": str(payload["error_code"]) if payload.get("error_code") is not None else None,
            "error": str(payload["error"]) if payload.get("error") is not None else None,
            "missing_dependencies": [str(item) for item in payload.get("missing_dependencies") or []],
        }
        if isinstance(payload.get("skill_ids"), list):
            summary_step["skill_ids"] = [str(item) for item in payload["skill_ids"]]
        if isinstance(payload.get("mcp_tool_ids"), list):
            summary_step["mcp_tool_ids"] = [str(item) for item in payload["mcp_tool_ids"]]
        if isinstance(payload.get("resource_limits"), dict):
            summary_step["resource_limits"] = dict(payload["resource_limits"])
        if payload.get("sandbox_mode") is not None:
            summary_step["sandbox_mode"] = str(payload["sandbox_mode"])
        if isinstance(payload.get("browser_enabled"), bool):
            summary_step["browser_enabled"] = payload["browser_enabled"]
        summary_steps.append(summary_step)
    counts = {
        "total": len(summary_steps),
        "pending": sum(1 for item in summary_steps if item["status"] == "pending"),
        "succeeded": sum(1 for item in summary_steps if item["status"] == "succeeded"),
        "failed": sum(1 for item in summary_steps if item["status"] == "failed"),
        "running": sum(1 for item in summary_steps if item["status"] == "running"),
        "cancelled": sum(1 for item in summary_steps if item["status"] == "cancelled"),
        "reused": sum(1 for item in summary_steps if item["checkpoint_reused"]),
        "blocked": sum(1 for item in summary_steps if item["missing_dependencies"]),
    }
    return {
        "steps": summary_steps,
        "reused_step_keys": reused_step_keys,
        "completed_step_outputs": completed_step_outputs,
        "counts": counts,
    }


async def _attach_multi_agent_result_summary(
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
    result_payload["multi_agent"] = _multi_agent_result_summary(steps)


async def _record_run_step_from_event(
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
    await repositories.upsert_run_step(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        step_key=_step_key_from_event(event_payload),
        step_kind=str(event_payload.get("step_kind") or "agent"),
        status=status,
        title=str(event_payload.get("title") or message or role),
        role=role,
        sequence=_int_payload_value(event_payload, "step_index", 0),
        payload_json=event_payload,
    )


def _sdk_import_status() -> str:
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception as exc:
        return f"unavailable:{exc.__class__.__name__}"
    return "ok"


def _worker_runtime_evidence(*, worker_id: str | None, executor_type: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "worker_id": worker_id,
        "executor_type": executor_type,
        "claude_agent_sdk_enabled": bool(settings.claude_agent_sdk_enabled),
        "claude_agent_model": settings.claude_agent_model,
        "claude_agent_sdk_import": _sdk_import_status(),
    }


async def _fail_policy_denied_run(
    payload: QueueRunPayload,
    *,
    error_code: str,
    error_message: str,
    event_type: str,
    event_stage: str,
    event_payload: dict[str, Any],
) -> WorkerOutcome:
    async with transaction() as conn:
        await repositories.fail_run(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            error_code=error_code,
            error_message=error_message,
        )
        await repositories.append_event(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            event_type=event_type,
            stage=event_stage,
            message=error_message,
            payload=event_payload,
        )
    return WorkerOutcome("failed", payload.run_id, error_code, error_message)


def _skill_snapshot_from_result(result: ExecutorResult) -> dict[str, list[str]]:
    source = {**result.executor_payload, **result.result}
    snapshot: dict[str, list[str]] = {
        "allowed_skills": [],
        "staged_skills": [],
        "used_skills": [],
    }
    for key in ("allowed_skills", "staged_skills"):
        value = source.get(key)
        if isinstance(value, list):
            snapshot[key] = [str(item) for item in value]
    snapshot["used_skills"] = _native_used_skills_from_result(result)
    return snapshot


def _native_used_skills_from_result(result: ExecutorResult) -> list[str]:
    source = str(result.executor_payload.get("used_skills_source") or "").strip()
    if source not in NATIVE_USED_SKILL_SOURCES:
        return []
    raw = result.executor_payload.get("used_skills")
    if not isinstance(raw, list):
        return []
    used: list[str] = []
    for item in raw:
        skill_name = str(item).strip()
        if skill_name and skill_name not in used:
            used.append(skill_name)
    return used


def _inferred_used_skills_from_result(result: ExecutorResult) -> list[str]:
    source = {**result.result, **result.executor_payload}
    raw = source.get("inferred_used_skills")
    if not isinstance(raw, list):
        return []
    inferred: list[str] = []
    for item in raw:
        skill_name = str(item).strip()
        if skill_name and skill_name not in inferred:
            inferred.append(skill_name)
    return inferred


def _skill_manifests_from_result(result: ExecutorResult) -> list[dict[str, Any]]:
    source = {**result.executor_payload, **result.result}
    raw = source.get("skill_manifests")
    if not isinstance(raw, list):
        return []
    used_skills = set(_native_used_skills_from_result(result))
    inferred_used_skills = set(_inferred_used_skills_from_result(result))
    used_skills_source = str(result.executor_payload.get("used_skills_source") or "").strip()
    manifests: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        manifest = dict(item)
        skill_id = str(manifest.get("skill_id") or "").strip()
        manifest["used"] = bool(skill_id and skill_id in used_skills)
        if manifest["used"]:
            manifest["used_skills_source"] = used_skills_source
            manifest["inferred_used"] = False
        elif skill_id and skill_id in inferred_used_skills:
            manifest["used_skills_source"] = "inferred"
            manifest["inferred_used"] = True
        manifests.append(manifest)
    return manifests


def _skill_manifests_for_persistence(result: ExecutorResult, payload: QueueRunPayload) -> list[dict[str, Any]]:
    manifests = _skill_manifests_from_result(result)
    if manifests or payload.executor_type != "ragflow":
        return manifests
    persisted: list[dict[str, Any]] = []
    for item in payload.skill_manifests:
        if not isinstance(item, dict):
            continue
        manifest = dict(item)
        skill_id = str(manifest.get("skill_id") or "").strip()
        if skill_id == payload.skill_id:
            succeeded = result.status == "succeeded"
            manifest["allowed"] = bool(manifest.get("allowed", True))
            manifest["staged"] = True
            manifest["used"] = succeeded
            manifest["used_skills_source"] = "executor_native" if succeeded else ""
            manifest["inferred_used"] = False
        persisted.append(manifest)
    return persisted


def _dependency_ids_from_manifest(item: dict[str, Any]) -> list[str]:
    raw = item.get("dependency_ids")
    if not isinstance(raw, list):
        return []
    return [str(value) for value in raw]


def _has_context_snapshot(payload: QueueRunPayload) -> bool:
    return bool(payload.context_snapshot_id and payload.context_snapshot)


async def _ensure_worker_context_snapshot(conn, payload: QueueRunPayload, *, trace_id: str) -> dict[str, Any]:
    if _has_context_snapshot(payload):
        return {
            "context_snapshot_id": payload.context_snapshot_id or "",
            "context_snapshot": payload.context_snapshot,
        }
    context_ref = await record_initial_context_snapshot(
        conn,
        tenant_id=payload.tenant_id,
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        session_id=payload.session_id,
        run_id=payload.run_id,
        trace_id=trace_id,
        agent_id=payload.agent_id,
        skill_id=payload.skill_id,
        input_payload=payload.input,
        message_ids=[],
        file_ids=payload.file_ids,
        source="worker_refresh",
    )
    return {
        "context_snapshot_id": str(context_ref["context_snapshot_id"]),
        "context_snapshot": context_ref,
    }


async def process_run_payload(
    raw: dict[str, Any],
    registry: AdapterRegistry | None = None,
    *,
    worker_id: str | None = None,
) -> WorkerOutcome:
    try:
        payload = parse_queue_payload(raw)
    except ValidationError as exc:
        return WorkerOutcome(
            status="dead_letter",
            run_id=None,
            error_code="invalid_queue_payload",
            error_message=str(exc),
        )
    trace_id = standard_trace_id(payload.run_id)

    adapter_registry = registry if registry is not None else AdapterRegistry()
    adapter = None

    async with transaction() as conn:
        locked = await repositories.mark_run_running(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
        if not locked:
            existing_run = await repositories.get_run(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
            if existing_run is None:
                return WorkerOutcome(
                    "skipped",
                    payload.run_id,
                    "stale_queue_payload",
                    "Run no longer exists for leased queue payload",
                )
            await repositories.append_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="skip",
                stage="worker",
                message="Run is not queued; skipping duplicate or stale payload",
            )
            return WorkerOutcome("skipped", payload.run_id)
        await append_user_event(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            event_type="worker_started",
            stage="worker",
            message="Run started",
            payload=_worker_runtime_evidence(worker_id=worker_id, executor_type=payload.executor_type),
        )
        if await repositories.is_cancel_requested(conn, tenant_id=payload.tenant_id, run_id=payload.run_id):
            await repositories.cancel_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                result_json={"message": "任务已取消"},
            )
            await append_user_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="run_cancelled",
                stage="control",
                message="任务已取消",
                payload={"severity": "warning"},
            )
            return WorkerOutcome("cancelled", payload.run_id)
        if payload.executor_type == "runtime211":
            await repositories.fail_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                error_code="legacy_runtime211_direct_executor_disabled",
                error_message="Direct runtime211 queue execution is disabled; use Claude worker legacy fallback only.",
            )
            await repositories.append_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="legacy_runtime211_direct_executor_denied",
                stage="policy",
                message="Direct runtime211 queue execution is disabled; use Claude worker legacy fallback only.",
                payload={
                    "executor_type": payload.executor_type,
                    "visible_to_user": False,
                    "severity": "error",
                },
            )
            return WorkerOutcome(
                "failed",
                payload.run_id,
                "legacy_runtime211_direct_executor_disabled",
                "Direct runtime211 queue execution is disabled; use Claude worker legacy fallback only.",
            )
        if payload.executor_type == "ragflow":
            try:
                tool = await repositories.ensure_mcp_tool_active(
                    conn,
                    tenant_id=payload.tenant_id,
                    tool_id=payload.skill_id,
                )
                tool_gate = evaluate_tool_policy(tool=tool)
                if not tool_gate.allowed and tool_gate.reason == "tool_permission_required":
                    permission_decision = await repositories.get_latest_tool_permission_decision(
                        conn,
                        tenant_id=payload.tenant_id,
                        user_id=payload.user_id,
                        run_id=payload.run_id,
                        tool_id=payload.skill_id,
                    )
                    tool_gate = evaluate_tool_policy(tool=tool, permission_decision=permission_decision)
                if not tool_gate.allowed:
                    await repositories.fail_run(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=tool_gate.reason,
                        error_message="MCP tool denied by policy",
                    )
                    await repositories.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="mcp_tool_denied",
                        stage="tool_policy",
                        message="MCP tool denied by policy",
                        payload={
                            "mcp_tool_id": payload.skill_id,
                            "policy": "tool_permission_gate",
                            "reason": tool_gate.reason,
                            "risk_level": tool_gate.risk_level,
                            "write_capable": tool_gate.write_capable,
                            "decision": tool_gate.decision,
                            "permission_request_id": tool_gate.permission_request_id,
                            "visible_to_user": True,
                            "severity": "error",
                        },
                    )
                    await repositories.append_audit_log(
                        conn,
                        tenant_id=payload.tenant_id,
                        user_id=payload.user_id,
                        action="mcp_tool_policy_denied",
                        target_type="mcp_tool",
                        target_id=payload.skill_id,
                        trace_id=trace_id,
                        payload_json={
                            "run_id": payload.run_id,
                            "session_id": payload.session_id,
                            "agent_id": payload.agent_id,
                            "skill_id": payload.skill_id,
                            "reason": tool_gate.reason,
                            "risk_level": tool_gate.risk_level,
                            "write_capable": tool_gate.write_capable,
                            "decision": tool_gate.decision,
                            "permission_request_id": tool_gate.permission_request_id,
                        },
                    )
                    return WorkerOutcome("failed", payload.run_id, tool_gate.reason, "MCP tool denied by policy")
                if tool_gate.decision == "allow_once":
                    consumed_decision = await repositories.consume_tool_permission_decision(
                        conn,
                        tenant_id=payload.tenant_id,
                        user_id=payload.user_id,
                        run_id=payload.run_id,
                        request_id=tool_gate.permission_request_id,
                    )
                    if consumed_decision is None:
                        error_code = "tool_permission_consumed_or_expired"
                        error_message = "MCP tool permission decision was already consumed or expired"
                        await repositories.fail_run(
                            conn,
                            tenant_id=payload.tenant_id,
                            run_id=payload.run_id,
                            error_code=error_code,
                            error_message=error_message,
                        )
                        await repositories.append_event(
                            conn,
                            tenant_id=payload.tenant_id,
                            run_id=payload.run_id,
                            event_type="mcp_tool_denied",
                            stage="tool_policy",
                            message="MCP tool denied by policy",
                            payload={
                                "mcp_tool_id": payload.skill_id,
                                "policy": "tool_permission_gate",
                                "reason": error_code,
                                "risk_level": tool_gate.risk_level,
                                "write_capable": tool_gate.write_capable,
                                "decision": tool_gate.decision,
                                "permission_request_id": tool_gate.permission_request_id,
                                "visible_to_user": True,
                                "severity": "error",
                            },
                        )
                        await repositories.append_audit_log(
                            conn,
                            tenant_id=payload.tenant_id,
                            user_id=payload.user_id,
                            action="mcp_tool_policy_denied",
                            target_type="mcp_tool",
                            target_id=payload.skill_id,
                            trace_id=trace_id,
                            payload_json={
                                "run_id": payload.run_id,
                                "session_id": payload.session_id,
                                "agent_id": payload.agent_id,
                                "skill_id": payload.skill_id,
                                "reason": error_code,
                                "risk_level": tool_gate.risk_level,
                                "write_capable": tool_gate.write_capable,
                                "decision": tool_gate.decision,
                                "permission_request_id": tool_gate.permission_request_id,
                                "auto_allowed": tool_gate.auto_allowed,
                            },
                        )
                        return WorkerOutcome("failed", payload.run_id, error_code, error_message)
                await repositories.append_audit_log(
                    conn,
                    tenant_id=payload.tenant_id,
                    user_id=payload.user_id,
                    action="mcp_tool_policy_allowed",
                    target_type="mcp_tool",
                    target_id=payload.skill_id,
                    trace_id=trace_id,
                    payload_json={
                        "run_id": payload.run_id,
                        "session_id": payload.session_id,
                        "agent_id": payload.agent_id,
                        "skill_id": payload.skill_id,
                        "reason": tool_gate.reason,
                        "risk_level": tool_gate.risk_level,
                        "write_capable": tool_gate.write_capable,
                        "decision": tool_gate.decision,
                        "permission_request_id": tool_gate.permission_request_id,
                        "auto_allowed": tool_gate.auto_allowed,
                    },
                )
            except repositories.RepositoryConflictError as exc:
                await repositories.fail_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code=str(exc),
                    error_message="MCP tool denied by policy",
                )
                await repositories.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="mcp_tool_denied",
                    stage="tool_policy",
                    message="MCP tool denied by policy",
                    payload={
                        "mcp_tool_id": payload.skill_id,
                        "policy": "deny_by_default",
                        "reason": str(exc),
                        "visible_to_user": True,
                        "severity": "error",
                    },
                )
                return WorkerOutcome("failed", payload.run_id, str(exc), "MCP tool denied by policy")
            except repositories.RepositoryNotFoundError as exc:
                await repositories.fail_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code=str(exc),
                    error_message="MCP tool denied by policy",
                )
                await repositories.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="mcp_tool_denied",
                    stage="tool_policy",
                    message="MCP tool denied by policy",
                    payload={
                        "mcp_tool_id": payload.skill_id,
                        "policy": "deny_by_default",
                        "reason": str(exc),
                        "visible_to_user": True,
                        "severity": "error",
                    },
                )
                return WorkerOutcome("failed", payload.run_id, str(exc), "MCP tool denied by policy")
        try:
            adapter = adapter_registry.get(payload.executor_type)
        except KeyError as exc:
            await repositories.fail_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                error_code="unknown_executor_type",
                error_message=str(exc),
            )
            await repositories.append_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="error",
                stage="worker",
                message="Unknown executor type",
                payload={"executor_type": payload.executor_type},
            )
            return WorkerOutcome("failed", payload.run_id, "unknown_executor_type", str(exc))
        context_ref = await _ensure_worker_context_snapshot(conn, payload, trace_id=trace_id)

    run_payload = RunPayload(
        tenant_id=payload.tenant_id,
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        session_id=payload.session_id,
        run_id=payload.run_id,
        agent_id=payload.agent_id,
        skill_id=payload.skill_id,
        file_ids=payload.file_ids,
        input=payload.input,
        trace_id=trace_id,
        skill_version=payload.skill_version or "",
        release_decision=payload.release_decision,
        skill_manifests=payload.skill_manifests,
        context_snapshot_id=str(context_ref["context_snapshot_id"]),
        context_snapshot=context_ref["context_snapshot"],
    )

    async def event_sink(
        *,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_payload = payload
        async with transaction() as conn:
            await append_user_event(
                conn,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
                event_type=event_type,
                stage=stage,
                message=message,
                payload=event_payload,
            )
            await _record_run_step_from_event(
                conn,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
                event_type=event_type,
                message=message,
                payload=event_payload,
            )
            if await repositories.is_cancel_requested(
                conn,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
            ):
                raise WorkerRunCancelled

    try:
        if payload.executor_type == "ragflow":
            async with transaction() as conn:
                await append_user_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="mcp_tool_call_started",
                    stage="tool",
                    message="正在检索知识库",
                    payload={"mcp_tool_id": payload.skill_id, "write_capable": False},
                )
        if adapter is None:
            raise RuntimeError("executor_adapter_not_resolved")
        started_at = time.monotonic()
        result = await adapter.submit_run(run_payload, event_sink=event_sink)
        latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
        result.validate()
        if payload.executor_type == "ragflow":
            async with transaction() as conn:
                await append_user_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="mcp_tool_call_completed",
                    stage="tool",
                    message="知识库检索完成",
                    payload={"mcp_tool_id": payload.skill_id, "write_capable": False},
                )
                await repositories.append_audit_log(
                    conn,
                    tenant_id=payload.tenant_id,
                    user_id=None,
                    action="mcp_tool_call_completed",
                    target_type="mcp_tool",
                    target_id=payload.skill_id,
                    trace_id=trace_id,
                    payload_json={
                        "run_id": payload.run_id,
                        "session_id": payload.session_id,
                        "agent_id": payload.agent_id,
                        "skill_id": payload.skill_id,
                        "write_capable": False,
                        **_ragflow_audit_payload(result.executor_payload),
                    },
                )
    except WorkerRunCancelled:
        async with transaction() as conn:
            await repositories.cancel_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                result_json={"message": "任务已取消"},
            )
            await append_user_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="run_cancelled",
                stage="control",
                message="任务已取消",
                payload={"severity": "warning"},
            )
        return WorkerOutcome("cancelled", payload.run_id)
    except Exception as exc:
        async with transaction() as conn:
            await repositories.fail_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                error_code="executor_failure",
                error_message=str(exc),
            )
            await append_user_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="run_failed",
                stage="executor",
                message="Executor failed",
                payload={"error": str(exc), "executor_type": payload.executor_type, "severity": "error"},
            )
            await repositories.append_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="error",
                stage="executor",
                message="Executor failed",
                payload={"error": str(exc), "executor_type": payload.executor_type, "visible_to_user": False},
            )
        return WorkerOutcome("failed", payload.run_id, "executor_failure", str(exc))

    observability = _executor_observability(result.executor_payload, latency_ms=latency_ms)
    event_observability_kwargs = _event_observability_kwargs(observability, result.executor_payload)
    terminal_event_kwargs = {"trace_id": trace_id, **event_observability_kwargs} if event_observability_kwargs else {}

    artifact_records = []
    for artifact in result.artifacts:
        artifact_id = repositories.new_id("art")
        artifact_records.append(
            {
                "id": artifact_id,
                "artifact_type": artifact.artifact_type,
                "label": artifact.label,
                "content_type": artifact.content_type,
                "storage_key": artifact.storage_key,
                "size_bytes": artifact.size_bytes,
                "download_url": _artifact_download_url(artifact_id),
                "manifest_json": artifact.manifest,
            }
        )
    skill_snapshot = _skill_snapshot_from_result(result)
    public_result = {
        key: value
        for key, value in result.result.items()
        if key not in {"skill_manifests", "used_skills", "used_skills_source", "inferred_used_skills"}
    }
    if "used_skills" in result.result or "used_skills" in result.executor_payload:
        public_result["used_skills"] = skill_snapshot["used_skills"]
    result_payload = {
        **public_result,
        **observability,
        "message": _append_artifact_links(str(result.result.get("message") or ""), artifact_records),
        "artifacts": [
            {
                "id": item["id"],
                "artifact_type": item["artifact_type"],
                "label": item["label"],
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "download_url": item["download_url"],
            }
            for item in artifact_records
        ],
        "executor": {
            "schema_version": result.schema_version,
            "adapter_version": result.adapter_version,
            "executor_type": result.executor_type,
            "executor_version": result.executor_version,
            "capabilities": result.capabilities,
        },
    }
    if skill_snapshot:
        result_payload["skills"] = skill_snapshot
    async with transaction() as conn:
        cancel_requested = await repositories.is_cancel_requested(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
        if result.status == "succeeded" and cancel_requested:
            result_payload = {
                **result_payload,
                "cancel_status": "cancel_requested_but_completed",
            }
        for artifact in artifact_records:
            manifest_json = artifact_manifest_contract(
                artifact_type=artifact["artifact_type"],
                manifest=_sanitize_artifact_manifest(artifact["manifest_json"]),
            )
            lineage = artifact_lineage_contract(manifest_json, source_run_id=payload.run_id)
            await repositories.create_artifact(
                conn,
                artifact_id=artifact["id"],
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                artifact_type=artifact["artifact_type"],
                label=artifact["label"],
                content_type=artifact["content_type"],
                storage_key=artifact["storage_key"],
                size_bytes=artifact["size_bytes"],
                trace_id=trace_id,
                manifest_json=manifest_json,
            )
            await append_user_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="artifact_created",
                stage="artifact",
                message=f"Artifact created: {artifact['label']}",
                payload={
                    "artifact_id": artifact["id"],
                    "artifact_type": artifact["artifact_type"],
                    "download_url": artifact["download_url"],
                    "lineage": lineage,
                },
            )
        for item in _skill_manifests_for_persistence(result, payload):
            skill_id = str(item.get("skill_id") or "").strip()
            if not skill_id:
                continue
            await repositories.upsert_run_skill_snapshot(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                skill_id=skill_id,
                skill_version=str(item.get("version") or item.get("skill_version") or ""),
                content_hash=str(item.get("content_hash") or item.get("version") or ""),
                source_json=item.get("source") if isinstance(item.get("source"), dict) else {},
                dependency_ids=_dependency_ids_from_manifest(item),
                allowed=bool(item.get("allowed")),
                staged=bool(item.get("staged")),
                used=bool(item.get("used")),
                used_skills_source=str(item.get("used_skills_source") or "").strip(),
                inferred_used=bool(item.get("inferred_used")),
            )
        if result.status == "succeeded":
            await _attach_multi_agent_result_summary(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                result_capabilities=result.capabilities,
                result_payload=result_payload,
            )
            await repositories.append_message(
                conn,
                tenant_id=payload.tenant_id,
                session_id=payload.session_id,
                run_id=payload.run_id,
                role="assistant",
                content=str(result_payload.get("message") or ""),
                metadata_json={
                    "artifact_count": len(result.artifacts),
                    "executor_type": result.executor_type,
                    "adapter_version": result.adapter_version,
                    "skills": skill_snapshot,
                },
            )
            await append_user_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="assistant_message_created",
                stage="message",
                message="Assistant response is ready",
                payload={"artifact_count": len(result.artifacts), "skills": skill_snapshot},
            )
            if cancel_requested:
                await append_user_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="cancel_requested_but_completed",
                    stage="control",
                    message="取消请求已记录，但任务已完成",
                    payload={"severity": "warning"},
                )
            await repositories.complete_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                result_json=result_payload,
            )
            await append_user_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="run_succeeded",
                stage="worker",
                message="Run succeeded",
                payload={"artifact_count": len(result.artifacts), "skills": skill_snapshot},
                **terminal_event_kwargs,
            )
            await repositories.append_event(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                event_type="status",
                stage="worker",
                message="Run succeeded",
                payload={"artifact_count": len(result.artifacts), "visible_to_user": False},
            )
            return WorkerOutcome("succeeded", payload.run_id)

        reported_error_code = str(result.result.get("error_code") or "executor_reported_failure")
        reported_error_message = str(result.result.get("message") or "Executor reported failure")
        await _attach_multi_agent_result_summary(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            result_capabilities=result.capabilities,
            result_payload=result_payload,
        )
        await repositories.fail_run(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            error_code=reported_error_code,
            error_message=reported_error_message,
            result_json=result_payload,
        )
        await append_user_event(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            event_type="run_failed",
            stage="worker",
            message="Run failed",
            payload={"artifact_count": len(result.artifacts), "severity": "error"},
            **terminal_event_kwargs,
        )
        await repositories.append_event(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            event_type="error",
            stage="worker",
            message="Run failed",
            payload={"artifact_count": len(result.artifacts), "visible_to_user": False},
        )
        return WorkerOutcome("failed", payload.run_id, reported_error_code, reported_error_message)
