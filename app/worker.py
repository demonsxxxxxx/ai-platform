import asyncio
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import time as _time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app import repositories
from app.control_plane_contracts import (
    CONTEXT_SNAPSHOT_SCHEMA_VERSION,
    artifact_lineage_contract,
    artifact_manifest_contract,
    sanitize_public_text,
    standard_trace_id,
)
from app.context_builder import ensure_public_context_provenance, record_initial_context_snapshot
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


def _multi_agent_dispatch_from_payload(payload: QueueRunPayload) -> dict[str, Any] | None:
    dispatch = payload.input.get("multi_agent_dispatch")
    return dispatch if isinstance(dispatch, dict) else None


async def _reconcile_multi_agent_child_terminal_state(
    conn,
    *,
    payload: QueueRunPayload,
    child_status: str,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    if _multi_agent_dispatch_from_payload(payload) is None:
        return None
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
    if _multi_agent_dispatch_from_payload(payload) is None:
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
    await repositories.fail_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=error_code,
        error_message=error_message,
        result_json=result_json,
    )
    if tenant_id == payload.tenant_id and run_id == payload.run_id:
        return await _reconcile_multi_agent_child_terminal_state(
            conn,
            payload=payload,
            child_status="failed",
            result_json=result_json,
            error_code=error_code,
            error_message=error_message,
        )
    return None


def _mcp_tool_request_payload(payload: QueueRunPayload) -> dict[str, str]:
    serialized = json.dumps(payload.input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"input_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest()}


def _mcp_tool_call_id(payload: QueueRunPayload, request_payload: dict[str, str]) -> str:
    raw = "|".join(
        [
            payload.tenant_id,
            payload.user_id,
            payload.run_id,
            payload.skill_id,
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
        await _fail_run_and_reconcile(
            conn,
            payload=payload,
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


def _payload_with_locked_run_input(payload: QueueRunPayload, locked_run: object) -> QueueRunPayload:
    if not isinstance(locked_run, dict):
        return payload
    input_json = locked_run.get("input_json")
    if not isinstance(input_json, dict):
        return payload

    updates: dict[str, Any] = {}
    run_input = input_json.get("input")
    if isinstance(run_input, dict):
        updates["input"] = run_input
    file_ids = input_json.get("file_ids")
    if isinstance(file_ids, list):
        updates["file_ids"] = [str(item) for item in file_ids if isinstance(item, str) and item]
    executor_type = input_json.get("executor_type")
    if isinstance(executor_type, str) and executor_type:
        updates["executor_type"] = executor_type
    skill_version = input_json.get("skill_version")
    if isinstance(skill_version, str) and skill_version:
        updates["skill_version"] = skill_version
    model_id = input_json.get("model_id")
    if isinstance(model_id, str) and model_id:
        updates["model_id"] = model_id
    model_value = input_json.get("model_value")
    if isinstance(model_value, str) and model_value:
        updates["model_value"] = model_value
    release_decision = input_json.get("release_decision")
    if isinstance(release_decision, dict):
        updates["release_decision"] = release_decision

    if not updates:
        return payload
    return payload.model_copy(update=updates)


def _locked_run_trace_id(payload: QueueRunPayload, locked_run: object) -> str:
    if isinstance(locked_run, dict) and locked_run.get("trace_id"):
        return str(locked_run["trace_id"])
    return standard_trace_id(payload.run_id)


def _runtime_sandbox_workspace_payload() -> dict[str, str]:
    return {
        "workspace": "/workspace",
        "inputs": "/workspace/inputs",
    }


async def _create_worker_runtime_sandbox_lease(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    trace_id: str,
    worker_id: str | None,
) -> _WorkerRuntimeSandboxLease:
    lease_payload = {
        "source": "worker_run_lifecycle",
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
                    reconciled_parent = await _fail_run_and_reconcile(
                        conn,
                        payload=payload,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=error_code,
                        error_message=error_message,
                    )
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
                reconciled_parent = await _fail_run_and_reconcile(
                    conn,
                    payload=payload,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    error_code=error_code,
                    error_message=error_message,
                )
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
            payload = _payload_with_locked_run_input(payload, locked)
            trace_id = _locked_run_trace_id(payload, locked)
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
                await repositories.cancel_run(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    result_json=cancel_result,
                )
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
                reconciled_parent = await _fail_run_and_reconcile(
                    conn,
                    payload=payload,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    error_code="legacy_runtime211_direct_executor_disabled",
                    error_message="Direct runtime211 queue execution is disabled; use Claude worker legacy fallback only.",
                )
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
            if payload.executor_type == "ragflow":
                try:
                    tool_request_payload = _mcp_tool_request_payload(payload)
                    tool_call_id = _mcp_tool_call_id(payload, tool_request_payload)
                    tool = await repositories.ensure_mcp_tool_active(
                        conn,
                        tenant_id=payload.tenant_id,
                        tool_id=payload.skill_id,
                    )
                    tool_gate = evaluate_tool_policy(tool=tool)
                    if not tool_gate.allowed and tool_gate.reason == "tool_permission_required":
                        permission_decision = await repositories.get_exact_tool_permission_decision(
                            conn,
                            tenant_id=payload.tenant_id,
                            user_id=payload.user_id,
                            run_id=payload.run_id,
                            tool_id=payload.skill_id,
                            tool_call_id=tool_call_id,
                            request_payload_json=tool_request_payload,
                        )
                        tool_gate = evaluate_tool_policy(tool=tool, permission_decision=permission_decision)
                    if not tool_gate.allowed:
                        reconciled_parent = await _fail_run_and_reconcile(
                            conn,
                            payload=payload,
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
                        terminal_after_transaction = _WorkerTerminalAfterTransaction(
                            WorkerOutcome("failed", payload.run_id, tool_gate.reason, "MCP tool denied by policy"),
                            payload,
                            reconciled_parent,
                        )
                        return terminal_after_transaction.outcome
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
                            reconciled_parent = await _fail_run_and_reconcile(
                                conn,
                                payload=payload,
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
                            terminal_after_transaction = _WorkerTerminalAfterTransaction(
                                WorkerOutcome("failed", payload.run_id, error_code, error_message),
                                payload,
                                reconciled_parent,
                            )
                            return terminal_after_transaction.outcome
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
                    reconciled_parent = await _fail_run_and_reconcile(
                        conn,
                        payload=payload,
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
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome("failed", payload.run_id, str(exc), "MCP tool denied by policy"),
                        payload,
                        reconciled_parent,
                    )
                    return terminal_after_transaction.outcome
                except repositories.RepositoryNotFoundError as exc:
                    reconciled_parent = await _fail_run_and_reconcile(
                        conn,
                        payload=payload,
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
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome("failed", payload.run_id, str(exc), "MCP tool denied by policy"),
                        payload,
                        reconciled_parent,
                    )
                    return terminal_after_transaction.outcome
            try:
                adapter = adapter_registry.get(payload.executor_type)
            except KeyError as exc:
                reconciled_parent = await _fail_run_and_reconcile(
                    conn,
                    payload=payload,
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
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("failed", payload.run_id, "unknown_executor_type", str(exc)),
                    payload,
                    reconciled_parent,
                )
                return terminal_after_transaction.outcome
            context_ref = await _ensure_worker_context_snapshot(conn, payload, trace_id=trace_id, run_identity=run_identity)
            runtime_sandbox_lease = await _create_worker_runtime_sandbox_lease(
                conn,
                payload=payload,
                run_identity=run_identity,
                trace_id=trace_id,
                worker_id=worker_id,
            )
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
            await repositories.cancel_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                result_json=cancel_result,
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
        async with transaction() as conn:
            reconciled_parent = await _fail_run_and_reconcile(
                conn,
                payload=payload,
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
            await release_runtime_sandbox_lease(conn, reason="run_failed")
        await _finalize_multi_agent_parent_after_child_commit(payload, reconciled_parent)
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
    reconciled_parent = None
    try:
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
                reconciled_parent = await _fail_run_and_reconcile(
                    conn,
                    payload=payload,
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
                await release_runtime_sandbox_lease(conn, reason="run_failed")
                terminal_outcome = WorkerOutcome("failed", payload.run_id, reported_error_code, reported_error_message)
    finally:
        await cleanup_runtime_sandbox_lease_after_interruption()
    await _finalize_multi_agent_parent_after_child_commit(payload, reconciled_parent)
    return terminal_outcome
