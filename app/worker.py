import asyncio
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import time as _time
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, normalize_roles
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityAccessDecision,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    resolve_capability_access,
)
from app.control_plane_contracts import (
    CONTEXT_SNAPSHOT_SCHEMA_VERSION,
    artifact_lineage_contract,
    artifact_manifest_contract,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.context_builder import (
    ensure_public_context_provenance,
    executor_context_pack_from_snapshot,
    record_initial_context_snapshot,
)
from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION, sanitize_context_manifest_payload
from app.db import transaction
from app.execution_boundary import decide_execution_boundary
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


class _WorkerAllowOnceConsumptionFailed(Exception):
    def __init__(self, denial: "_WorkerCapabilityDecision") -> None:
        super().__init__(denial.decision.decision_reason)
        self.denial = denial


@dataclass(frozen=True)
class _WorkerTerminalAfterTransaction:
    outcome: WorkerOutcome
    payload: QueueRunPayload
    reconciled_parent: dict[str, Any] | None


@dataclass(frozen=True)
class _WorkerRuntimeSandboxLease:
    lease_id: str
    tenant_id: str
    user_id: str
    run_id: str


@dataclass(frozen=True)
class _WorkerCapabilityDecision:
    capability_kind: str
    capability_id: str
    decision: CapabilityAccessDecision


@dataclass(frozen=True)
class _WorkerAllowOnceGrant:
    tool_id: str
    request_id: str
    distribution_decision: CapabilityAccessDecision


@dataclass(frozen=True)
class _WorkerToolPolicyAudit:
    tool_id: str
    allowed: bool
    reason: str
    risk_level: str
    write_capable: bool
    decision: str
    permission_request_id: str
    auto_allowed: bool


@dataclass(frozen=True)
class _WorkerCapabilityAuthorization:
    payload: QueueRunPayload
    principal: AuthPrincipal
    decisions: tuple[_WorkerCapabilityDecision, ...]
    denial: _WorkerCapabilityDecision | None = None
    allow_once_grants: tuple[_WorkerAllowOnceGrant, ...] = ()
    tool_policy_audits: tuple[_WorkerToolPolicyAudit, ...] = ()


@dataclass(frozen=True)
class _WorkerAdminBypassAudit:
    tenant_id: str
    user_id: str
    target_type: str
    target_id: str
    trace_id: str
    payload_json: dict[str, Any]


_PARENT_ROLLUP_RETRY_ATTEMPTS = 3
_PARENT_ROLLUP_RETRY_DELAY_SECONDS = 0.05
_EXECUTOR_ERROR_REQUEST_ID_RE = re.compile(
    r"\brequest[_ -]?id\s*[:=]\s*[A-Za-z0-9._~+/=-]+\b",
    re.IGNORECASE,
)


def _public_executor_failure_message(result: ExecutorResult) -> str:
    generic_message = "Executor reported failure"
    for candidate in (
        result.result.get("message"),
        result.result.get("sdk_error"),
        result.executor_payload.get("sdk_error"),
    ):
        raw_text = _EXECUTOR_ERROR_REQUEST_ID_RE.sub(
            "request id: [redacted-id]",
            str(candidate or ""),
        )
        safe_text = sanitize_public_text(raw_text)
        if safe_text and safe_text != generic_message:
            return safe_text
    return generic_message


def parse_queue_payload(raw: dict[str, Any]) -> QueueRunPayload:
    return QueueRunPayload.model_validate(raw)


async def _reconcile_multi_agent_child_terminal_state(
    conn,
    *,
    payload: QueueRunPayload,
    child_status: str,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    return await repositories.reconcile_multi_agent_child_run_terminal_state(
        conn,
        tenant_id=payload.tenant_id,
        child_run_id=payload.run_id,
        child_status=child_status,
        result_json=result_json,
        error_code=error_code,
        error_message=error_message,
    )


def _parent_rollup_retry_args(
    payload: QueueRunPayload,
    reconciled: dict[str, Any] | None,
) -> dict[str, str] | None:
    if not isinstance(reconciled, dict):
        return None
    parent_run_id = str(reconciled.get("parent_run_id") or "").strip()
    if not parent_run_id:
        return None
    return {
        "tenant_id": payload.tenant_id,
        "parent_run_id": parent_run_id,
        "triggered_by_child_run_id": payload.run_id,
    }


async def _finalize_multi_agent_parent_after_child_commit(
    payload: QueueRunPayload,
    reconciled: dict[str, Any] | None,
) -> dict[str, Any] | None:
    retry_args = _parent_rollup_retry_args(payload, reconciled)
    if retry_args is None:
        return None
    for attempt in range(_PARENT_ROLLUP_RETRY_ATTEMPTS):
        async with transaction() as conn:
            finalized = await repositories.finalize_multi_agent_parent_run_if_ready(conn, **retry_args)
        if finalized is not None:
            return finalized
        if attempt + 1 < _PARENT_ROLLUP_RETRY_ATTEMPTS:
            await asyncio.sleep(_PARENT_ROLLUP_RETRY_DELAY_SECONDS)
    return None


async def _fail_run_and_reconcile(
    conn,
    *,
    payload: QueueRunPayload,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    result_json: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    terminal_written, reconciled = await _fail_run_and_reconcile_with_write(
        conn,
        payload=payload,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=error_code,
        error_message=error_message,
        result_json=result_json,
    )
    return reconciled if terminal_written else None


async def _fail_run_and_reconcile_with_write(
    conn,
    *,
    payload: QueueRunPayload,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    result_json: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    terminal_written = await repositories.fail_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=error_code,
        error_message=error_message,
        result_json=result_json,
    )
    if terminal_written is False:
        return False, None
    if tenant_id == payload.tenant_id and run_id == payload.run_id:
        return True, await _reconcile_multi_agent_child_terminal_state(
            conn,
            payload=payload,
            child_status="failed",
            result_json=result_json,
            error_code=error_code,
            error_message=error_message,
        )
    return True, None


def _mcp_tool_request_payload(payload: QueueRunPayload) -> dict[str, str]:
    serialized = json.dumps(payload.input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"input_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest()}


def _mcp_tool_call_id(
    payload: QueueRunPayload,
    request_payload: dict[str, str],
    *,
    tool_id: str | None = None,
) -> str:
    raw = "|".join(
        [
            payload.tenant_id,
            payload.user_id,
            payload.run_id,
            tool_id or payload.skill_id,
            request_payload.get("input_sha256", ""),
        ]
    )
    return f"mcp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


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
NATIVE_USED_SKILL_SOURCES = {"executor_hook", "executor_native", "platform_controlled_runner"}
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


def _int_mapping_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            value = payload.get(key)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _usd_cost_to_minor_units(value: Any) -> int:
    try:
        minor_units = (Decimal(str(value)) * Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return max(int(minor_units), 0)


def _sdk_usage_observability(executor_payload: dict[str, Any]) -> dict[str, Any]:
    usage = executor_payload.get("sdk_usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _int_mapping_value(usage, "input_tokens", "input")
    input_tokens += _int_mapping_value(usage, "cache_creation_input_tokens")
    input_tokens += _int_mapping_value(usage, "cache_read_input_tokens")
    output_tokens = _int_mapping_value(usage, "output_tokens", "output")
    total_tokens = _int_mapping_value(usage, "total_tokens", "total")
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    estimated_cost_minor = _int_mapping_value(usage, "estimated_cost_minor", "cost_minor")
    if estimated_cost_minor <= 0:
        estimated_cost_minor = _usd_cost_to_minor_units(
            usage.get("total_cost_usd") or usage.get("cost_usd") or usage.get("estimated_cost_usd")
        )
    return {
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {"estimated_cost_minor": estimated_cost_minor},
    }


def _has_sdk_observability(executor_payload: dict[str, Any]) -> bool:
    sdk_observability = _sdk_usage_observability(executor_payload)
    token_counts = sdk_observability["token_counts"]
    return (
        token_counts["input"] > 0
        or token_counts["output"] > 0
        or token_counts["total"] > 0
        or sdk_observability["cost"]["estimated_cost_minor"] > 0
    )


def _executor_observability(
    executor_payload: dict[str, Any],
    *,
    latency_ms: int,
) -> dict[str, Any]:
    sdk_observability = _sdk_usage_observability(executor_payload)
    sdk_token_counts = sdk_observability["token_counts"]
    input_tokens = _int_payload_value(executor_payload, "input_token_count", sdk_token_counts["input"])
    output_tokens = _int_payload_value(executor_payload, "output_token_count", sdk_token_counts["output"])
    total_default = sdk_token_counts["total"] or (input_tokens + output_tokens)
    total_tokens = _int_payload_value(executor_payload, "total_token_count", total_default)
    return {
        "latency_ms": latency_ms,
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {
            "estimated_cost_minor": _int_payload_value(
                executor_payload,
                "estimated_cost_minor",
                sdk_observability["cost"]["estimated_cost_minor"],
            ),
        },
    }


def _event_observability_kwargs(observability: dict[str, Any], executor_payload: dict[str, Any]) -> dict[str, Any]:
    metric_keys = {
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "estimated_cost_minor",
    }
    if not any(key in executor_payload for key in metric_keys) and not _has_sdk_observability(
        executor_payload
    ):
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
    step_id = await repositories.upsert_run_step(
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
            step_key=_step_key_from_event(event_payload),
            step_kind=str(event_payload.get("step_kind") or "agent"),
            status=status,
            title=str(event_payload.get("title") or message or role),
            role=role,
            sequence=_int_payload_value(event_payload, "step_index", 0),
            payload_json={"source_step_id": step_id},
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
        terminal_written, _ = await _fail_run_and_reconcile_with_write(
            conn,
            payload=payload,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            error_code=error_code,
            error_message=error_message,
        )
        if terminal_written is False:
            return WorkerOutcome(
                "skipped",
                payload.run_id,
                "stale_terminal_state",
                "Run already reached a terminal state",
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
        return _attach_payload_snapshot_governance(manifests, payload)
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


def _payload_snapshot_governance_by_skill(payload: QueueRunPayload) -> dict[str, dict[str, Any]]:
    by_skill: dict[str, dict[str, Any]] = {}
    for item in payload.skill_manifests:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip()
        governance = item.get("snapshot_governance")
        if skill_id and isinstance(governance, dict):
            by_skill[skill_id] = governance
    return by_skill


def _payload_skill_manifest_by_skill(payload: QueueRunPayload) -> dict[str, dict[str, Any]]:
    by_skill: dict[str, dict[str, Any]] = {}
    for item in payload.skill_manifests:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip()
        if skill_id:
            by_skill[skill_id] = item
    return by_skill


def _attach_payload_snapshot_governance(
    manifests: list[dict[str, Any]],
    payload: QueueRunPayload,
) -> list[dict[str, Any]]:
    payload_manifests_by_skill = _payload_skill_manifest_by_skill(payload)
    governance_by_skill = _payload_snapshot_governance_by_skill(payload)
    attached: list[dict[str, Any]] = []
    for item in manifests:
        manifest = dict(item)
        skill_id = str(manifest.get("skill_id") or "").strip()
        payload_manifest = payload_manifests_by_skill.get(skill_id)
        if not isinstance(payload_manifest, dict):
            continue
        for key in ("version", "skill_version", "content_hash"):
            manifest.pop(key, None)
        payload_version = ""
        payload_hash = ""
        if isinstance(payload_manifest, dict):
            payload_version = str(
                payload_manifest.get("version")
                or payload_manifest.get("skill_version")
                or payload_manifest.get("content_hash")
                or ""
            ).strip()
            payload_hash = str(payload_manifest.get("content_hash") or payload_version).strip()
        if payload_version:
            manifest["version"] = payload_version
            manifest["skill_version"] = payload_version
        if payload_hash:
            manifest["content_hash"] = payload_hash
        for field in ("source", "files", "dependency_ids", "mcp_tool_ids"):
            payload_value = payload_manifest.get(field)
            if isinstance(payload_value, (dict, list)):
                manifest[field] = payload_value
            else:
                manifest.pop(field, None)
        payload_governance = governance_by_skill.get(skill_id)
        if isinstance(payload_governance, dict):
            manifest["snapshot_governance"] = payload_governance
        else:
            manifest.pop("snapshot_governance", None)
        attached.append(manifest)
    return attached


def _source_json_from_skill_manifest(
    item: dict[str, Any],
    *,
    release_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return repositories.run_skill_snapshot_source_json(item, release_decision=release_decision)


def _without_skill_snapshot_files(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_skill_snapshot_files(item)
            for key, item in value.items()
            if str(key) != "files"
        }
    if isinstance(value, list):
        return [_without_skill_snapshot_files(item) for item in value]
    return value


def _dependency_ids_from_manifest(item: dict[str, Any]) -> list[str]:
    raw = item.get("dependency_ids")
    if not isinstance(raw, list):
        return []
    return [str(value) for value in raw]


RUN_IDENTITY_FIELDS = ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "agent_id", "skill_id")


def _payload_identity(payload: QueueRunPayload) -> dict[str, str]:
    return {
        "tenant_id": payload.tenant_id,
        "workspace_id": payload.workspace_id,
        "user_id": payload.user_id,
        "session_id": payload.session_id,
        "run_id": payload.run_id,
        "agent_id": payload.agent_id,
        "skill_id": payload.skill_id,
    }


def _locked_run_identity(payload: QueueRunPayload, locked_run: object) -> dict[str, str]:
    if not isinstance(locked_run, dict):
        return _payload_identity(payload)
    identity: dict[str, str] = {}
    for field in RUN_IDENTITY_FIELDS:
        value = locked_run.get("id") if field == "run_id" else locked_run.get(field)
        identity[field] = str(value) if value else ""
    return identity


def _identity_mismatch_fields(payload: QueueRunPayload, identity: dict[str, str]) -> list[str]:
    payload_identity = _payload_identity(payload)
    return [field for field in RUN_IDENTITY_FIELDS if str(payload_identity[field]) != str(identity[field])]


LOCKED_RUN_SNAPSHOT_FIELDS = (
    "file_ids",
    "input",
    "executor_type",
    "skill_version",
    "release_decision",
    "skill_manifests",
    "context_snapshot_id",
    "context_snapshot",
    "model_id",
    "model_value",
    "schema_version",
)


def _payload_from_locked_run(
    locked_run: object,
    *,
    run_identity: dict[str, str],
) -> QueueRunPayload | None:
    if not isinstance(locked_run, dict):
        return None
    input_json = locked_run.get("input_json")
    if not isinstance(input_json, dict) or not isinstance(input_json.get("input"), dict):
        return None
    candidate = {
        **run_identity,
        **{field: input_json[field] for field in LOCKED_RUN_SNAPSHOT_FIELDS if field in input_json},
    }
    try:
        return QueueRunPayload.model_validate(candidate)
    except ValidationError:
        return None


def _locked_run_trace_id(payload: QueueRunPayload, locked_run: object) -> str:
    if isinstance(locked_run, dict) and locked_run.get("trace_id"):
        return str(locked_run["trace_id"])
    return standard_trace_id(payload.run_id)


def _locked_run_principal(locked_run: object, run_identity: dict[str, str]) -> AuthPrincipal:
    locked = locked_run if isinstance(locked_run, dict) else {}
    raw_roles = locked.get("principal_roles")
    roles = normalize_roles(raw_roles if isinstance(raw_roles, (list, tuple, set)) else [])
    return AuthPrincipal(
        user_id=run_identity["user_id"],
        display_name=run_identity["user_id"],
        tenant_id=run_identity["tenant_id"],
        department_id=str(locked.get("principal_department_id") or ""),
        roles=roles,
        permissions=[],
        source=str(locked.get("auth_source") or ""),
    )


def _worker_capability_context(principal: AuthPrincipal) -> CapabilityAccessContext:
    return CapabilityAccessContext(
        tenant_id=principal.tenant_id,
        department_id=principal.department_id,
        roles=principal.roles,
        is_admin=is_ai_admin(principal),
        permissions=principal.permissions,
    )


def _denied_capability_decision(
    reason: str,
    *,
    source: CapabilityAccessDecision | None = None,
) -> CapabilityAccessDecision:
    return CapabilityAccessDecision(
        visible=False,
        usable=False,
        manageable=False,
        admin_bypass=False,
        decision_reason=reason,
        department_scope_ids=list(source.department_scope_ids) if source is not None else [],
        role_scope_ids=list(source.role_scope_ids) if source is not None else [],
        scope_mode=source.scope_mode if source is not None else "allowlist",
    )


def _worker_capability_record(
    capability_kind: str,
    capability_id: str,
    decision: CapabilityAccessDecision,
) -> _WorkerCapabilityDecision:
    return _WorkerCapabilityDecision(
        capability_kind=capability_kind,
        capability_id=capability_id,
        decision=decision,
    )


def _mcp_tool_lifecycle_status(tool: dict[str, Any]) -> str:
    if (
        str(tool.get("effective_status") or "disabled") == "active"
        and str(tool.get("server_status") or "disabled") == "active"
        and bool(tool.get("visible_to_user", True))
    ):
        return "active"
    return "disabled"


def _canonical_authorized_mcp_scope(
    container: dict[str, Any],
    *,
    allowed_tool_ids: set[str],
) -> dict[str, Any]:
    rebuilt = dict(container)
    requested: list[str] = []
    selector_present = False
    for key in ("mcp_tool_ids", "mcpToolIds"):
        if key not in container:
            continue
        selector_present = True
        for value in container[key]:
            tool_id = str(value).strip()
            if tool_id and tool_id in allowed_tool_ids and tool_id not in requested:
                requested.append(tool_id)
        rebuilt.pop(key, None)
    if selector_present:
        rebuilt["mcp_tool_ids"] = requested
    return rebuilt


def _payload_with_authorized_mcp_registration(
    payload: QueueRunPayload,
    *,
    allowed_entries: list[dict[str, Any]],
) -> QueueRunPayload:
    allowed_tool_ids = {
        str(entry.get("tool_id") or "").strip()
        for entry in allowed_entries
        if str(entry.get("tool_id") or "").strip()
    }
    rebuilt_input = _canonical_authorized_mcp_scope(payload.input, allowed_tool_ids=allowed_tool_ids)
    steps = rebuilt_input.get("multi_agent_steps")
    if isinstance(steps, list):
        rebuilt_input["multi_agent_steps"] = [
            _canonical_authorized_mcp_scope(step, allowed_tool_ids=allowed_tool_ids)
            if isinstance(step, dict)
            else step
            for step in steps
        ]
    return payload.model_copy(update={"input": rebuilt_input})


async def _reauthorize_worker_capabilities(
    conn,
    *,
    payload: QueueRunPayload,
    locked_run: object,
    run_identity: dict[str, str],
) -> _WorkerCapabilityAuthorization:
    principal = _locked_run_principal(locked_run, run_identity)
    context = _worker_capability_context(principal)
    decisions: list[_WorkerCapabilityDecision] = []

    try:
        await repositories.validate_run_skill_snapshots_for_dispatch(
            conn,
            tenant_id=run_identity["tenant_id"],
            run_id=run_identity["run_id"],
            skill_manifests=payload.skill_manifests,
            release_decision=payload.release_decision,
        )
    except repositories.RepositoryConflictError:
        denial = _worker_capability_record(
            "skill",
            run_identity["skill_id"],
            _denied_capability_decision("skill_snapshot_identity_mismatch"),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
    try:
        pinned_mcp_tool_ids = await repositories.validate_replay_skill_manifests(
            conn,
            skill_id=run_identity["skill_id"],
            pinned_version=str(payload.skill_version or ""),
            pinned_executor_type=payload.executor_type,
            skill_manifests=payload.skill_manifests,
        )
    except (repositories.RepositoryAuthorizationError, repositories.RepositoryConflictError):
        denial = _worker_capability_record(
            "skill",
            run_identity["skill_id"],
            _denied_capability_decision("skill_historical_pin_revoked"),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

    skill: dict[str, Any] = {}
    skill_lifecycle_status = "disabled"
    try:
        skill = await repositories.resolve_selected_skill(
            conn,
            tenant_id=run_identity["tenant_id"],
            agent_id=run_identity["agent_id"],
            skill_id=run_identity["skill_id"],
        )
        skill_lifecycle_status = str(skill.get("skill_status") or "disabled")
    except (repositories.RepositoryNotFoundError, repositories.RepositoryConflictError):
        pass
    try:
        skill_distribution = await repositories.get_capability_distribution_row(
            conn,
            tenant_id=run_identity["tenant_id"],
            capability_kind="skill",
            capability_id=run_identity["skill_id"],
        )
    except repositories.RepositoryConflictError:
        denial = _worker_capability_record(
            "skill",
            run_identity["skill_id"],
            _denied_capability_decision("distribution_scope_invalid"),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
    skill_decision = resolve_capability_access(
        context,
        CapabilityDistributionSubject(
            capability_kind="skill",
            capability_id=run_identity["skill_id"],
            lifecycle_status=skill_lifecycle_status,
            distribution=skill_distribution,
        ),
        intent="use",
    )
    skill_record = _worker_capability_record("skill", run_identity["skill_id"], skill_decision)
    decisions.append(skill_record)
    if not skill_decision.usable:
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), skill_record)

    try:
        requested_tool_ids = repositories.run_mcp_tool_ids_for_skill(skill, payload.input)
        for tool_id in pinned_mcp_tool_ids or []:
            if tool_id not in requested_tool_ids:
                requested_tool_ids.append(tool_id)
    except repositories.RepositoryAuthorizationError:
        denial = _worker_capability_record(
            "mcp_tool",
            "mcp_tool_ids",
            _denied_capability_decision("invalid_capability_selector"),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

    allowed_entries: list[dict[str, Any]] = []
    allow_once_grants: list[_WorkerAllowOnceGrant] = []
    allow_once_identities: set[tuple[str, str]] = set()
    tool_policy_audits: list[_WorkerToolPolicyAudit] = []
    request_payload = _mcp_tool_request_payload(payload)
    for tool_id in requested_tool_ids:
        tool = await repositories.get_mcp_tool_registry_entry(
            conn,
            tenant_id=run_identity["tenant_id"],
            tool_id=tool_id,
        )
        if tool is None or str(tool.get("tool_id") or "").strip() != tool_id:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision("distribution_missing"),
            )
            return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        server_id = str(tool.get("server_id") or "").strip()
        if not server_id:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision("distribution_inheritance_missing"),
            )
            return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        try:
            server_distribution = await repositories.get_capability_distribution_row(
                conn,
                tenant_id=run_identity["tenant_id"],
                capability_kind="mcp_server",
                capability_id=server_id,
            )
        except repositories.RepositoryConflictError:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision("distribution_scope_invalid"),
            )
            return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        distribution_decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="mcp_tool",
                capability_id=tool_id,
                lifecycle_status=_mcp_tool_lifecycle_status(tool),
                distribution=server_distribution,
                inherited_distribution_source=f"mcp_server:{server_id}",
            ),
            intent="use",
        )
        tool_record = _worker_capability_record("mcp_tool", tool_id, distribution_decision)
        decisions.append(tool_record)
        if not distribution_decision.usable:
            return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), tool_record)

        tool_gate = evaluate_tool_policy(tool=tool)
        if not tool_gate.allowed and tool_gate.reason == "tool_permission_required":
            permission_decision = await repositories.get_exact_tool_permission_decision(
                conn,
                tenant_id=run_identity["tenant_id"],
                user_id=run_identity["user_id"],
                run_id=run_identity["run_id"],
                tool_id=tool_id,
                tool_call_id=_mcp_tool_call_id(payload, request_payload, tool_id=tool_id),
                request_payload_json=request_payload,
            )
            tool_gate = evaluate_tool_policy(tool=tool, permission_decision=permission_decision)
        tool_policy_audits.append(
            _WorkerToolPolicyAudit(
                tool_id=tool_id,
                allowed=tool_gate.allowed,
                reason=tool_gate.reason,
                risk_level=tool_gate.risk_level,
                write_capable=tool_gate.write_capable,
                decision=tool_gate.decision,
                permission_request_id=tool_gate.permission_request_id,
                auto_allowed=tool_gate.auto_allowed,
            )
        )
        if not tool_gate.allowed:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision(tool_gate.reason, source=distribution_decision),
            )
            return _WorkerCapabilityAuthorization(
                payload,
                principal,
                tuple(decisions),
                denial,
                tool_policy_audits=tuple(tool_policy_audits),
            )
        if tool_gate.decision == "allow_once":
            grant_identity = (tool_id, tool_gate.permission_request_id)
            if grant_identity not in allow_once_identities:
                allow_once_identities.add(grant_identity)
                allow_once_grants.append(
                    _WorkerAllowOnceGrant(
                        tool_id=tool_id,
                        request_id=tool_gate.permission_request_id,
                        distribution_decision=distribution_decision,
                    )
                )
        allowed_entries.append(tool)

    authorized_payload = _payload_with_authorized_mcp_registration(
        payload,
        allowed_entries=allowed_entries,
    )
    return _WorkerCapabilityAuthorization(
        authorized_payload,
        principal,
        tuple(decisions),
        allow_once_grants=tuple(allow_once_grants),
        tool_policy_audits=tuple(tool_policy_audits),
    )


async def _consume_worker_allow_once_grants(
    conn,
    *,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
) -> None:
    for grant in authorization.allow_once_grants:
        consumed = await repositories.consume_tool_permission_decision(
            conn,
            tenant_id=run_identity["tenant_id"],
            user_id=run_identity["user_id"],
            run_id=run_identity["run_id"],
            request_id=grant.request_id,
        )
        if consumed is not None:
            continue
        raise _WorkerAllowOnceConsumptionFailed(
            _worker_capability_record(
                "mcp_tool",
                grant.tool_id,
                _denied_capability_decision(
                    "tool_permission_consumed_or_expired",
                    source=grant.distribution_decision,
                ),
            )
        )


def _worker_capability_audit_payload(
    record: _WorkerCapabilityDecision,
    *,
    principal: AuthPrincipal,
    run_identity: dict[str, str],
) -> dict[str, Any]:
    return {
        **capability_distribution_audit_payload(
            decision=record.decision,
            actor_department_id=principal.department_id,
            actor_roles=principal.roles,
            capability_kind=record.capability_kind,
            capability_id=record.capability_id,
        ),
        "run_id": run_identity["run_id"],
        "session_id": run_identity["session_id"],
        "agent_id": run_identity["agent_id"],
        "skill_id": run_identity["skill_id"],
    }


def _worker_admin_bypass_audits(
    *,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
) -> tuple[_WorkerAdminBypassAudit, ...]:
    audits: list[_WorkerAdminBypassAudit] = []
    for record in authorization.decisions:
        if not record.decision.admin_bypass:
            continue
        audits.append(
            _WorkerAdminBypassAudit(
                tenant_id=run_identity["tenant_id"],
                user_id=run_identity["user_id"],
                target_type=record.capability_kind,
                target_id=record.capability_id,
                trace_id=trace_id,
                payload_json=_worker_capability_audit_payload(
                    record,
                    principal=authorization.principal,
                    run_identity=run_identity,
                ),
            )
        )
    return tuple(audits)


async def _append_worker_admin_bypass_audits(
    conn,
    *,
    audits: tuple[_WorkerAdminBypassAudit, ...],
) -> None:
    for audit in audits:
        await repositories.append_audit_log(
            conn,
            tenant_id=audit.tenant_id,
            user_id=audit.user_id,
            action="capability_distribution.admin_bypass",
            target_type=audit.target_type,
            target_id=audit.target_id,
            trace_id=audit.trace_id,
            payload_json=audit.payload_json,
        )


async def _append_worker_tool_policy_audits(
    conn,
    *,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
) -> None:
    for audit in authorization.tool_policy_audits:
        await repositories.append_audit_log(
            conn,
            tenant_id=run_identity["tenant_id"],
            user_id=run_identity["user_id"],
            action="mcp_tool_policy_allowed" if audit.allowed else "mcp_tool_policy_denied",
            target_type="mcp_tool",
            target_id=audit.tool_id,
            trace_id=trace_id,
            payload_json={
                "run_id": run_identity["run_id"],
                "session_id": run_identity["session_id"],
                "agent_id": run_identity["agent_id"],
                "skill_id": run_identity["skill_id"],
                "reason": audit.reason,
                "risk_level": audit.risk_level,
                "write_capable": audit.write_capable,
                "decision": audit.decision,
                "permission_request_id": audit.permission_request_id,
                "auto_allowed": audit.auto_allowed,
            },
        )


async def _append_worker_capability_denial_evidence(
    conn,
    *,
    denial: _WorkerCapabilityDecision,
    principal: AuthPrincipal,
    run_identity: dict[str, str],
    trace_id: str,
    policy: str,
    error_message: str,
) -> None:
    await repositories.append_event(
        conn,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        event_type="capability_not_authorized",
        stage="authorization",
        message=error_message,
        payload={
            "capability_kind": denial.capability_kind,
            "capability_id": denial.capability_id,
            "policy": policy,
            "reason": denial.decision.decision_reason,
            "visible_to_user": True,
            "severity": "error",
        },
    )
    await repositories.append_audit_log(
        conn,
        tenant_id=run_identity["tenant_id"],
        user_id=run_identity["user_id"],
        action="capability_distribution.denied",
        target_type=denial.capability_kind,
        target_id=denial.capability_id,
        trace_id=trace_id,
        payload_json=_worker_capability_audit_payload(
            denial,
            principal=principal,
            run_identity=run_identity,
        ),
    )


async def _fail_locked_run_snapshot(
    conn,
    *,
    payload: QueueRunPayload,
    locked_run: object,
    run_identity: dict[str, str],
    trace_id: str,
) -> _WorkerTerminalAfterTransaction:
    error_code = "capability_not_authorized"
    error_message = "Capability is not authorized for this run"
    principal = _locked_run_principal(locked_run, run_identity)
    denial = _worker_capability_record(
        "skill",
        run_identity["skill_id"],
        _denied_capability_decision("locked_snapshot_invalid"),
    )
    terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
    )
    if terminal_written is False:
        return _WorkerTerminalAfterTransaction(
            WorkerOutcome(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
            None,
        )
    await _append_worker_capability_denial_evidence(
        conn,
        denial=denial,
        principal=principal,
        run_identity=run_identity,
        trace_id=trace_id,
        policy="locked_run_snapshot",
        error_message=error_message,
    )
    return _WorkerTerminalAfterTransaction(
        WorkerOutcome("failed", run_identity["run_id"], error_code, error_message),
        payload,
        reconciled_parent,
    )


async def _fail_worker_capability_authorization(
    conn,
    *,
    payload: QueueRunPayload,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
) -> _WorkerTerminalAfterTransaction:
    denial = authorization.denial
    if denial is None:
        raise RuntimeError("worker_capability_denial_missing")
    error_code = "capability_not_authorized"
    error_message = "Capability is not authorized for this run"
    terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
    )
    if terminal_written is False:
        return _WorkerTerminalAfterTransaction(
            WorkerOutcome(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
            None,
        )
    await _append_worker_capability_denial_evidence(
        conn,
        denial=denial,
        principal=authorization.principal,
        run_identity=run_identity,
        trace_id=trace_id,
        policy="capability_distribution",
        error_message=error_message,
    )
    return _WorkerTerminalAfterTransaction(
        WorkerOutcome("failed", run_identity["run_id"], error_code, error_message),
        payload,
        reconciled_parent,
    )


def _runtime_sandbox_workspace_payload() -> dict[str, str]:
    return {
        "workspace": "/workspace",
        "inputs": "/workspace/inputs",
    }


def _context_execution_tier(context_snapshot: dict[str, Any]) -> str:
    value = context_snapshot.get("execution_tier")
    return value.strip() if isinstance(value, str) else ""


def _ordinary_run_uses_runtime_sandbox(
    payload: QueueRunPayload,
    *,
    context_snapshot: dict[str, Any],
) -> bool:
    return decide_execution_boundary(
        executor_type=payload.executor_type,
        execution_mode=str(payload.input.get("execution_mode") or ""),
        execution_tier=_context_execution_tier(context_snapshot),
    ).requires_real_sandbox


def _result_prefers_cancelled_after_failure(result: ExecutorResult) -> bool:
    sandbox_provider = str(result.executor_payload.get("sandbox_provider") or "").strip()
    runtime_terminal_status = str(result.executor_payload.get("runtime_terminal_status") or "").strip().lower()
    return sandbox_provider in {"docker", "opensandbox"} and runtime_terminal_status in {"cancelled", "canceled"}


async def _create_worker_runtime_sandbox_lease(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    trace_id: str,
    worker_id: str | None,
) -> _WorkerRuntimeSandboxLease:
    lease_payload = {
        "source": "sdk_only_lifecycle_placeholder",
        "evidence_class": "sdk_only_lifecycle_placeholder",
        "executor_type": payload.executor_type,
    }
    if worker_id:
        lease_payload["worker_id"] = worker_id
    row = await repositories.create_sandbox_lease(
        conn,
        tenant_id=run_identity["tenant_id"],
        workspace_id=run_identity["workspace_id"],
        user_id=run_identity["user_id"],
        session_id=run_identity["session_id"],
        run_id=run_identity["run_id"],
        trace_id=trace_id,
        sandbox_mode="ephemeral",
        provider="fake",
        browser_enabled=False,
        ttl_seconds=1800,
        resource_limits_json={},
        user_visible_payload_json=_runtime_sandbox_workspace_payload(),
        lease_payload_json=lease_payload,
    )
    return _WorkerRuntimeSandboxLease(
        lease_id=str(row["id"]),
        tenant_id=run_identity["tenant_id"],
        user_id=run_identity["user_id"],
        run_id=run_identity["run_id"],
    )


async def _release_worker_runtime_sandbox_lease(
    conn,
    lease: _WorkerRuntimeSandboxLease | None,
    *,
    reason: str,
) -> None:
    if lease is None:
        return
    await repositories.release_sandbox_lease(
        conn,
        tenant_id=lease.tenant_id,
        user_id=lease.user_id,
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        reason=reason,
    )


def _is_top_level_multi_agent_parent_for_worker_dispatch(payload: QueueRunPayload) -> bool:
    if not bool(get_settings().multi_agent_dispatch_worker_enabled):
        return False
    if str(payload.input.get("execution_mode") or "") != "multi_agent":
        return False
    if payload.input.get("copied_from_run_id"):
        return False
    if isinstance(payload.input.get("multi_agent_dispatch"), dict):
        return False
    return True


def _has_context_snapshot(payload: QueueRunPayload) -> bool:
    return bool(payload.context_snapshot_id)


def _included_count(row: dict[str, Any], field: str, payload: dict[str, Any], payload_field: str) -> int:
    raw = row.get(field)
    if isinstance(raw, list):
        return len(raw)
    try:
        return int(payload.get(payload_field) or 0)
    except (TypeError, ValueError):
        return 0


def _safe_context_memory_policy(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "default").strip()
    if source not in {"default", "stored", "not_recorded"}:
        source = "stored"
    try:
        retention_days = int(raw.get("retention_days") or 90)
    except (TypeError, ValueError):
        retention_days = 90
    if retention_days <= 0:
        retention_days = 90
    return {
        "source": source,
        "memory_enabled": bool(raw.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": retention_days,
    }


def _context_snapshot_ref_from_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    public_payload = ensure_public_context_provenance(
        payload,
        source="stored_context_snapshot",
        message_count=_included_count(row, "included_message_ids", payload, "message_count"),
        file_count=_included_count(row, "included_file_ids", payload, "file_count"),
        artifact_count=_included_count(row, "included_artifact_ids", payload, "artifact_count"),
        memory_record_count=_included_count(row, "included_memory_record_ids", payload, "memory_record_count"),
        memory_policy_source="not_recorded",
        long_term_memory_read=False,
        preserve_stored_input_keys=True,
    )
    context_ref: dict[str, Any] = {
        "schema_version": str(row.get("schema_version") or payload.get("schema_version") or CONTEXT_SNAPSHOT_SCHEMA_VERSION),
        "context_snapshot_id": str(row["id"]),
        "source": public_payload["used_context_summary"]["source"],
        "message_count": public_payload["referenced_materials"]["message_count"],
        "file_count": public_payload["referenced_materials"]["file_count"],
        "memory_record_count": public_payload["referenced_materials"]["memory_record_count"],
        "referenced_materials": public_payload["referenced_materials"],
        "used_context_summary": public_payload["used_context_summary"],
        "latest_artifact_version": public_payload["latest_artifact_version"],
        "execution_tier": public_payload["execution_tier"],
        "context_pack_version": public_payload["context_pack_version"],
        "context_pack_generated_at": public_payload["context_pack_generated_at"],
    }
    memory_policy = _safe_context_memory_policy(payload.get("memory_policy"))
    if memory_policy is not None:
        context_ref["memory_policy"] = memory_policy
    context_manifest = payload.get("context_manifest")
    if isinstance(context_manifest, dict) and context_manifest.get("schema_version") == CONTEXT_MANIFEST_SCHEMA_VERSION:
        context_ref["context_manifest"] = sanitize_context_manifest_payload(context_manifest)
    return context_ref


async def _ensure_worker_context_snapshot(
    conn,
    payload: QueueRunPayload,
    *,
    trace_id: str,
    run_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    identity = run_identity or _payload_identity(payload)
    if _has_context_snapshot(payload):
        scoped_snapshot = await repositories.get_context_snapshot_for_worker(
            conn,
            tenant_id=identity["tenant_id"],
            workspace_id=identity["workspace_id"],
            user_id=identity["user_id"],
            session_id=identity["session_id"],
            run_id=identity["run_id"],
            context_snapshot_id=str(payload.context_snapshot_id),
        )
        if scoped_snapshot is not None:
            context_ref = _context_snapshot_ref_from_row(scoped_snapshot)
            return {
                "context_snapshot_id": str(context_ref["context_snapshot_id"]),
                "context_snapshot": context_ref,
            }
    context_ref = await record_initial_context_snapshot(
        conn,
        tenant_id=identity["tenant_id"],
        workspace_id=identity["workspace_id"],
        user_id=identity["user_id"],
        session_id=identity["session_id"],
        run_id=identity["run_id"],
        trace_id=trace_id,
        agent_id=identity["agent_id"],
        skill_id=identity["skill_id"],
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
    run_identity = _payload_identity(payload)
    runtime_sandbox_lease: _WorkerRuntimeSandboxLease | None = None
    runtime_sandbox_lease_released = False

    terminal_after_transaction: _WorkerTerminalAfterTransaction | None = None
    capability_authorization: _WorkerCapabilityAuthorization | None = None
    admin_bypass_audits: tuple[_WorkerAdminBypassAudit, ...] = ()
    try:
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
                if str(existing_run.get("status") or "") == "queued":
                    error_code = "queue_payload_identity_mismatch"
                    error_message = "Queued run identity is invalid"
                    terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                        conn,
                        payload=payload,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=error_code,
                        error_message=error_message,
                    )
                    if terminal_written is False:
                        terminal_after_transaction = _WorkerTerminalAfterTransaction(
                            WorkerOutcome(
                                "skipped",
                                payload.run_id,
                                "stale_terminal_state",
                                "Run already reached a terminal state",
                            ),
                            payload,
                            None,
                        )
                        return terminal_after_transaction.outcome
                    await repositories.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="error",
                        stage="worker",
                        message=error_message,
                        payload={"visible_to_user": False, "severity": "error", "reason": "scope_guard_rejected_lock"},
                    )
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome("failed", payload.run_id, error_code, error_message),
                        payload,
                        reconciled_parent,
                    )
                    return terminal_after_transaction.outcome
                await repositories.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="skip",
                    stage="worker",
                    message="Run is not queued; skipping duplicate or stale payload",
                )
                return WorkerOutcome("skipped", payload.run_id)
            run_identity = _locked_run_identity(payload, locked)
            mismatch_fields = _identity_mismatch_fields(payload, run_identity)
            if mismatch_fields:
                error_code = "queue_payload_identity_mismatch"
                error_message = "Queue payload identity does not match run record"
                terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                    conn,
                    payload=payload,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    error_code=error_code,
                    error_message=error_message,
                )
                if terminal_written is False:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            run_identity["run_id"],
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                        None,
                    )
                    return terminal_after_transaction.outcome
                await repositories.append_event(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    event_type="error",
                    stage="worker",
                    message=error_message,
                    payload={
                        "visible_to_user": False,
                        "severity": "error",
                        "mismatch_fields": mismatch_fields,
                    },
                )
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("failed", run_identity["run_id"], error_code, error_message),
                    payload,
                    reconciled_parent,
                )
                return terminal_after_transaction.outcome
            trace_id = _locked_run_trace_id(payload, locked)
            locked_payload = _payload_from_locked_run(
                locked,
                run_identity=run_identity,
            )
            if locked_payload is None:
                terminal_after_transaction = await _fail_locked_run_snapshot(
                    conn,
                    payload=payload,
                    locked_run=locked,
                    run_identity=run_identity,
                    trace_id=trace_id,
                )
                return terminal_after_transaction.outcome
            payload = locked_payload
            capability_authorization = await _reauthorize_worker_capabilities(
                conn,
                payload=payload,
                locked_run=locked,
                run_identity=run_identity,
            )
            admin_bypass_audits = _worker_admin_bypass_audits(
                authorization=capability_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
            await _append_worker_admin_bypass_audits(conn, audits=admin_bypass_audits)
            await _append_worker_tool_policy_audits(
                conn,
                authorization=capability_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
            if capability_authorization.denial is not None:
                terminal_after_transaction = await _fail_worker_capability_authorization(
                    conn,
                    payload=payload,
                    authorization=capability_authorization,
                    run_identity=run_identity,
                    trace_id=trace_id,
                )
                return terminal_after_transaction.outcome
            payload = capability_authorization.payload
            await append_user_event(
                conn,
                tenant_id=run_identity["tenant_id"],
                run_id=run_identity["run_id"],
                event_type="worker_started",
                stage="worker",
                message="Run started",
                payload=_worker_runtime_evidence(worker_id=worker_id, executor_type=payload.executor_type),
            )
            if await repositories.is_cancel_requested(conn, tenant_id=run_identity["tenant_id"], run_id=run_identity["run_id"]):
                cancel_result = {"message": "任务已取消"}
                terminal_written = await repositories.cancel_run(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    result_json=cancel_result,
                )
                if terminal_written is False:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            run_identity["run_id"],
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                        None,
                    )
                    return terminal_after_transaction.outcome
                reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                    conn,
                    payload=payload,
                    child_status="cancelled",
                    result_json=cancel_result,
                )
                await append_user_event(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    event_type="run_cancelled",
                    stage="control",
                    message="任务已取消",
                    payload={"severity": "warning"},
                )
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("cancelled", run_identity["run_id"]),
                    payload,
                    reconciled_parent,
                )
                return terminal_after_transaction.outcome
            if _is_top_level_multi_agent_parent_for_worker_dispatch(payload):
                parked = await repositories.mark_multi_agent_dispatch_parent_awaiting_dispatch(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    worker_id=worker_id,
                )
                if parked:
                    await repositories.append_event(
                        conn,
                        tenant_id=run_identity["tenant_id"],
                        run_id=run_identity["run_id"],
                        event_type="multi_agent_dispatch_parent_parked",
                        stage="control",
                        message="Multi-agent parent parked for dispatcher",
                        visible_to_user=False,
                        payload={
                            "visible_to_user": False,
                            "orchestration_state": "awaiting_dispatch",
                            "source": "worker",
                        },
                    )
                    return WorkerOutcome(
                        "skipped",
                        run_identity["run_id"],
                        "multi_agent_dispatch_parent_parked",
                        "Multi-agent parent parked for dispatcher",
                    )
            if payload.executor_type == "runtime211":
                terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                    conn,
                    payload=payload,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    error_code="legacy_runtime211_direct_executor_disabled",
                    error_message="Direct runtime211 queue execution is disabled; use Claude worker legacy fallback only.",
                )
                if terminal_written is False:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            run_identity["run_id"],
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                        None,
                    )
                    return terminal_after_transaction.outcome
                await repositories.append_event(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    event_type="legacy_runtime211_direct_executor_denied",
                    stage="policy",
                    message="Direct runtime211 queue execution is disabled; use Claude worker legacy fallback only.",
                    payload={
                        "executor_type": payload.executor_type,
                        "visible_to_user": False,
                        "severity": "error",
                    },
                )
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome(
                        "failed",
                        run_identity["run_id"],
                        "legacy_runtime211_direct_executor_disabled",
                        "Direct runtime211 queue execution is disabled; use Claude worker legacy fallback only.",
                    ),
                    payload,
                    reconciled_parent,
                )
                return terminal_after_transaction.outcome
            await _consume_worker_allow_once_grants(
                conn,
                authorization=capability_authorization,
                run_identity=run_identity,
            )
            try:
                adapter = adapter_registry.get(payload.executor_type)
            except KeyError as exc:
                terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                    conn,
                    payload=payload,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code="unknown_executor_type",
                    error_message=str(exc),
                )
                if terminal_written is False:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                        None,
                    )
                    return terminal_after_transaction.outcome
                await repositories.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="error",
                    stage="worker",
                    message="Unknown executor type",
                    payload={"executor_type": payload.executor_type},
                )
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("failed", payload.run_id, "unknown_executor_type", str(exc)),
                    payload,
                    reconciled_parent,
                )
                return terminal_after_transaction.outcome
            context_ref = await _ensure_worker_context_snapshot(conn, payload, trace_id=trace_id, run_identity=run_identity)
            if not _ordinary_run_uses_runtime_sandbox(
                payload,
                context_snapshot=context_ref["context_snapshot"],
            ):
                runtime_sandbox_lease = await _create_worker_runtime_sandbox_lease(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    worker_id=worker_id,
                )
    except _WorkerAllowOnceConsumptionFailed as exc:
        if capability_authorization is None:
            raise
        failed_authorization = _WorkerCapabilityAuthorization(
            payload=capability_authorization.payload,
            principal=capability_authorization.principal,
            decisions=capability_authorization.decisions,
            denial=exc.denial,
            allow_once_grants=capability_authorization.allow_once_grants,
            tool_policy_audits=capability_authorization.tool_policy_audits,
        )
        async with transaction() as conn:
            await _append_worker_admin_bypass_audits(conn, audits=admin_bypass_audits)
            await _append_worker_tool_policy_audits(
                conn,
                authorization=failed_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
            terminal_after_transaction = await _fail_worker_capability_authorization(
                conn,
                payload=failed_authorization.payload,
                authorization=failed_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
        return terminal_after_transaction.outcome
    finally:
        if terminal_after_transaction is not None:
            await _finalize_multi_agent_parent_after_child_commit(
                terminal_after_transaction.payload,
                terminal_after_transaction.reconciled_parent,
            )

    run_payload = RunPayload(
        tenant_id=run_identity["tenant_id"],
        workspace_id=run_identity["workspace_id"],
        user_id=run_identity["user_id"],
        session_id=run_identity["session_id"],
        run_id=run_identity["run_id"],
        agent_id=run_identity["agent_id"],
        skill_id=run_identity["skill_id"],
        file_ids=payload.file_ids,
        input=payload.input,
        trace_id=trace_id,
        skill_version=payload.skill_version or "",
        release_decision=payload.release_decision,
        skill_manifests=payload.skill_manifests,
        context_snapshot_id=str(context_ref["context_snapshot_id"]),
        context_snapshot=context_ref["context_snapshot"],
        context_pack=executor_context_pack_from_snapshot(context_ref["context_snapshot"]),
        model_id=payload.model_id or "",
        model_value=payload.model_value or "",
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

    async def release_runtime_sandbox_lease(conn, *, reason: str) -> None:
        nonlocal runtime_sandbox_lease_released
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        await _release_worker_runtime_sandbox_lease(conn, runtime_sandbox_lease, reason=reason)
        runtime_sandbox_lease_released = True

    async def cleanup_runtime_sandbox_lease_after_interruption() -> None:
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        try:
            async with transaction() as conn:
                await release_runtime_sandbox_lease(conn, reason="run_terminal_interrupted")
        except Exception:
            return

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
        reconciled_parent = None
        async with transaction() as conn:
            cancel_result = {"message": "任务已取消"}
            terminal_written = await repositories.cancel_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                result_json=cancel_result,
            )
            if terminal_written is False:
                return WorkerOutcome(
                    "skipped",
                    payload.run_id,
                    "stale_terminal_state",
                    "Run already reached a terminal state",
                )
            reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                conn,
                payload=payload,
                child_status="cancelled",
                result_json=cancel_result,
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
            await release_runtime_sandbox_lease(conn, reason="run_cancelled")
        await _finalize_multi_agent_parent_after_child_commit(payload, reconciled_parent)
        return WorkerOutcome("cancelled", payload.run_id)
    except Exception as exc:
        reconciled_parent = None
        outcome_after_exception = WorkerOutcome("failed", payload.run_id, "executor_failure", str(exc))
        async with transaction() as conn:
            if await repositories.is_cancel_requested(conn, tenant_id=payload.tenant_id, run_id=payload.run_id):
                cancel_result = {"message": "任务已取消"}
                terminal_written = await repositories.cancel_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    result_json=cancel_result,
                )
                if terminal_written is False:
                    outcome_after_exception = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
                    reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                        conn,
                        payload=payload,
                        child_status="cancelled",
                        result_json=cancel_result,
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
                    await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                    outcome_after_exception = WorkerOutcome("cancelled", payload.run_id)
            else:
                terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                    conn,
                    payload=payload,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code="executor_failure",
                    error_message=str(exc),
                )
                if terminal_written is False:
                    outcome_after_exception = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
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
                    await release_runtime_sandbox_lease(conn, reason="run_failed")
        await _finalize_multi_agent_parent_after_child_commit(payload, reconciled_parent)
        return outcome_after_exception

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
    reconciled_parent = None
    try:
        async with transaction() as conn:
            pending_permission_blocks_success = (
                result.status == "succeeded"
                and await repositories.has_pending_tool_permission_requests(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                )
            )
            missing_required_artifact = (
                result.status == "succeeded"
                and result.executor_payload.get("artifact_contract_required") is True
                and not result.artifacts
            )
            if pending_permission_blocks_success or missing_required_artifact:
                error_code = (
                    "tool_permission_pending"
                    if pending_permission_blocks_success
                    else "required_artifact_missing"
                )
                error_message = (
                    "A pending tool-permission request blocks successful completion."
                    if pending_permission_blocks_success
                    else "The file-required Skill produced no user-visible artifact."
                )
                result = replace(
                    result,
                    status="failed",
                    artifacts=[],
                    result={
                        **result.result,
                        "message": error_message,
                        "error_code": error_code,
                    },
                )
                artifact_records = []
                result_payload = {
                    **result_payload,
                    "message": error_message,
                    "error_code": error_code,
                    "artifacts": [],
                }
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
                    source_json=_source_json_from_skill_manifest(
                        item,
                        release_decision=payload.release_decision,
                    ),
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
                terminal_written = await repositories.complete_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    result_json=result_payload,
                )
                if terminal_written is False:
                    terminal_outcome = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
                    reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                        conn,
                        payload=payload,
                        child_status="succeeded",
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
                    await release_runtime_sandbox_lease(conn, reason="run_succeeded")
                    terminal_outcome = WorkerOutcome("succeeded", payload.run_id)
            else:
                reported_error_code = str(result.result.get("error_code") or "executor_reported_failure")
                reported_error_message = _public_executor_failure_message(result)
                await _attach_multi_agent_result_summary(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    result_capabilities=result.capabilities,
                    result_payload=result_payload,
                )
                if cancel_requested and _result_prefers_cancelled_after_failure(result):
                    cancel_result = {"message": "任务已取消"}
                    terminal_written = await repositories.cancel_run(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        result_json=cancel_result,
                    )
                    if terminal_written is False:
                        terminal_outcome = WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        )
                    else:
                        reconciled_parent = await _reconcile_multi_agent_child_terminal_state(
                            conn,
                            payload=payload,
                            child_status="cancelled",
                            result_json=cancel_result,
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
                        await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                        terminal_outcome = WorkerOutcome("cancelled", payload.run_id)
                else:
                    terminal_written, reconciled_parent = await _fail_run_and_reconcile_with_write(
                        conn,
                        payload=payload,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=reported_error_code,
                        error_message=reported_error_message,
                        result_json=result_payload,
                    )
                    if terminal_written is False:
                        terminal_outcome = WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        )
                    else:
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
                        await release_runtime_sandbox_lease(conn, reason="run_failed")
                        terminal_outcome = WorkerOutcome("failed", payload.run_id, reported_error_code, reported_error_message)
    finally:
        await cleanup_runtime_sandbox_lease_after_interruption()
    await _finalize_multi_agent_parent_after_child_commit(payload, reconciled_parent)
    return terminal_outcome
