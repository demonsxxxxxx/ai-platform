from fastapi import HTTPException

from app.artifact_preview import artifact_preview_allowed, artifact_preview_url
from app.auth import AuthPrincipal, is_ai_admin
from app.file_preview_contracts import xlsx_preview_identity_from_metadata
from app.control_plane_contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EXECUTOR_RESULT_SCHEMA_VERSION,
    RUN_CONTRACT_VERSION,
    artifact_lineage_contract,
    artifact_manifest_contract,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.projection_redaction import redact_raw_skill_references, sanitize_user_control_input
from app.tool_permission_projection import tool_permission_public_event_payload


def normalize_run_status(status: str) -> str:
    return "cancelled" if status == "canceled" else status


def normalize_step_status(status: object) -> str:
    return normalize_run_status(str(status or ""))


def progress_for_status(status: str) -> int:
    status = normalize_run_status(status)
    if status == "queued":
        return 10
    if status == "running":
        return 55
    if status in {"succeeded", "failed", "cancelled"}:
        return 100
    return 0


def artifact_download_url(artifact_id: str) -> str:
    return f"/api/ai/artifacts/{artifact_id}/download"


def public_text_or_fallback(value: object, fallback: object = "") -> str:
    text = sanitize_public_text(value)
    if text:
        return text
    fallback_text = sanitize_public_text(fallback)
    return fallback_text or ""


PUBLIC_TERMINAL_DETAIL_MESSAGES = {
    "run_failed": "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    "run_timeout": "任务执行超时。请缩小任务范围后重试。",
    "model_service_unavailable": "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
    "execution_service_unavailable": "AI 执行服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
    "dependent_service_unavailable": "任务依赖的服务暂时不可用。请稍后重试。",
    "capability_not_authorized": "当前账号不能使用所选能力。请重新选择或联系管理员。",
    "tool_permission_denied": "任务所需工具未获授权。请调整请求或联系管理员。",
    "skill_sandbox_admission_failed": "所选 Skill 未能通过隔离沙箱准入。请调整 Skill 或联系管理员。",
    "run_cancelled": "任务已取消。取消前已产生的公开内容仍会保留。",
}

PUBLIC_TERMINAL_ERROR_CODE_ALIASES = {
    "native_tool_admission_failed": "skill_sandbox_admission_failed",
    "executor_deadline_exceeded": "run_timeout",
    "executor_cleanup_timeout": "run_timeout",
    "claude_agent_sdk_runtime_error": "model_service_unavailable",
    "claude_agent_sdk_disabled": "execution_service_unavailable",
    "claude_agent_sdk_import_failed": "execution_service_unavailable",
    "claude_agent_sdk_unavailable": "execution_service_unavailable",
    "docker_unavailable": "execution_service_unavailable",
    "executor_health_timeout": "execution_service_unavailable",
    "executor_runner_failed": "execution_service_unavailable",
    "ragflow_api_error": "dependent_service_unavailable",
    "capability_not_authorized": "capability_not_authorized",
    "model_not_allowed": "capability_not_authorized",
    "tool_denied": "tool_permission_denied",
    "mcp_tool_denied": "tool_permission_denied",
    "tool_permission_denied": "tool_permission_denied",
}

PUBLIC_FAILED_ACTIVITY_EVENT_DETAILS = {
    "agent_step_failed": {
        "message": "当前计划步骤未完成。请调整请求后重试。",
        "stage": "agent",
        "error_code": "step_failed",
        "severity": "error",
    },
    "agent_step_blocked": {
        "message": "当前计划步骤正在等待前置条件。",
        "stage": "agent",
        "error_code": "step_blocked",
        "severity": "warning",
    },
    "subagent_failed": {
        "message": "协同处理步骤未完成。请调整请求后重试。",
        "stage": "subagent",
        "error_code": "subagent_failed",
        "severity": "error",
    },
}


def public_terminal_projection(
    status: object,
    error_code: object = None,
    *,
    run_id: str | None = None,
    step_rows: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    """Build the sole ordinary-user projection for failed or cancelled terminals.

    The optional raw step rows are reprojected internally.  Callers cannot
    assert that an arbitrary ``multi_agent`` dictionary is public.
    """
    normalized_status = normalize_run_status(str(status or ""))
    if normalized_status == "cancelled":
        detail_code = "run_cancelled"
        detail_kind = "cancelled"
    elif normalized_status == "failed":
        raw_error_code = str(error_code or "").strip()
        detail_code = PUBLIC_TERMINAL_ERROR_CODE_ALIASES.get(raw_error_code, "run_failed")
        detail_kind = "failed"
    else:
        return None
    message = PUBLIC_TERMINAL_DETAIL_MESSAGES[detail_code]
    result: dict[str, object] = {"message": message}
    if run_id and step_rows:
        multi_agent = _ordinary_multi_agent_snapshot(run_id, step_rows)
        if multi_agent is not None:
            result["multi_agent"] = multi_agent
    return {
        "detail_kind": detail_kind,
        "detail_code": detail_code,
        "message": message,
        "error_code": detail_code if detail_kind == "failed" else None,
        "result": result,
        "event_payload": {},
    }


def public_terminal_detail(status: object, error_code: object = None) -> dict[str, str] | None:
    """Return the stable public terminal taxonomy used by compatibility clients."""
    projection = public_terminal_projection(status, error_code)
    if projection is None:
        return None
    return {
        "detail_kind": str(projection["detail_kind"]),
        "detail_code": str(projection["detail_code"]),
        "message": str(projection["message"]),
    }


def artifact_card(row: dict[str, object], principal: AuthPrincipal | None = None) -> dict[str, object]:
    artifact_id = str(row["id"])
    artifact_type = str(row["artifact_type"])
    content_type = str(row.get("content_type") or "application/octet-stream")
    xlsx_identity = xlsx_preview_identity_from_metadata(row)
    manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
    if principal is not None and not is_ai_admin(principal):
        manifest = redact_raw_skill_references(manifest)
        label = public_text_or_fallback(row.get("label"), artifact_type)
    else:
        label = public_text_or_fallback(row.get("label"), artifact_type)
    lineage = artifact_lineage_contract(manifest, row=row)
    lineage.pop("source_run_id", None)
    return {
        "id": artifact_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "label": label,
        "content_type": content_type,
        "size_bytes": int(row.get("size_bytes") or 0),
        "download_url": artifact_download_url(artifact_id),
        "preview_url": (
            artifact_preview_url(artifact_id)
            if artifact_preview_allowed(content_type)
            and (not xlsx_identity.has_xlsx_content_type or xlsx_identity.eligible)
            else None
        ),
        "status": "available",
        "lineage": lineage,
        "manifest": artifact_manifest_contract(
            artifact_type=artifact_type,
            manifest=manifest,
            schema_version=str(row.get("manifest_version") or ARTIFACT_MANIFEST_SCHEMA_VERSION),
        ),
        "created_at": row.get("created_at"),
    }


PUBLIC_EVENT_TYPE_ALIASES = {
    "legacy_runtime211_direct_executor_denied": "status",
    "mcp_tool_call_completed": "tool_call_completed",
    "mcp_tool_call_started": "tool_call_started",
    "mcp_tool_denied": "tool_denied",
    "run_multi_agent_child_created": "run_child_created",
    "skill_selected": "capability_selected",
    "tool_permission_decided": "tool_permission_card",
    "tool_permission_requested": "tool_permission_card",
    "tool_permission_terminalized": "tool_permission_card",
    "worker_started": "run_started",
}

PUBLIC_STAGE_ALIASES = {
    "download": "artifact",
    "executor": "agent",
    "skills": "capability",
    "tool_policy": "policy",
    "upload": "artifact",
    "worker": "status",
}


def public_event_type(value: object, principal: AuthPrincipal | None = None) -> str:
    raw = str(value or "status")
    if principal is None or is_ai_admin(principal):
        return raw
    public_value = PUBLIC_EVENT_TYPE_ALIASES.get(raw, raw)
    sanitized = sanitize_public_text(public_value)
    return sanitized or "status"


def public_event_stage(value: object, principal: AuthPrincipal | None = None) -> str:
    raw = str(value or "")
    if principal is None or is_ai_admin(principal):
        return raw
    public_value = PUBLIC_STAGE_ALIASES.get(raw, raw)
    sanitized = sanitize_public_text(public_value)
    return sanitized or "status"


def required_schema_version(row: dict[str, object], field: str, expected: str, detail: str) -> str:
    value = row.get(field)
    if value != expected:
        raise HTTPException(status_code=500, detail=detail)
    return str(value)


def run_contract_version(run: dict[str, object]) -> str:
    return required_schema_version(run, "schema_version", RUN_CONTRACT_VERSION, "invalid_run_contract")


def executor_result_schema_version(run: dict[str, object]) -> str:
    return required_schema_version(
        run,
        "executor_schema_version",
        EXECUTOR_RESULT_SCHEMA_VERSION,
        "invalid_executor_result_schema_version",
    )


def run_event_response(run_id: str, row: dict[str, object], principal: AuthPrincipal | None = None) -> dict[str, object]:
    raw_event_type = str(row["event_type"])
    payload = row.get("payload_json") or {}
    if not isinstance(payload, dict):
        payload = {}
    payload = sanitize_public_payload(payload)
    if not isinstance(payload, dict):
        payload = {}
    if principal is not None and not is_ai_admin(principal):
        payload = sanitize_user_control_input(payload)
        if raw_event_type in {
            "tool_permission_requested",
            "tool_permission_decided",
            "tool_permission_terminalized",
        }:
            payload = tool_permission_public_event_payload(
                run_id=run_id,
                event_type=raw_event_type,
                payload=payload,
            )
    severity = str(row.get("severity") or payload.get("severity") or ("error" if row.get("event_type") == "error" else "info"))
    if severity not in {"info", "warning", "error"}:
        severity = "info"
    event_type = public_event_type(raw_event_type, principal)
    stage = public_event_stage(row.get("stage"), principal)
    visible_to_user = bool(row.get("visible_to_user", payload.get("visible_to_user", True)))
    message = sanitize_public_text(row.get("message"))
    sanitized_error_code = sanitize_public_text(row.get("error_code"))
    error_code = sanitized_error_code or (None if not row.get("error_code") else "run_failed")
    if principal is not None and not is_ai_admin(principal):
        terminal_projection = None
        if raw_event_type in {"error", "run_failed"}:
            terminal_projection = public_terminal_projection("failed", row.get("error_code"))
        elif raw_event_type in {"run_cancelled", "run_canceled"}:
            terminal_projection = public_terminal_projection("cancelled")
        if terminal_projection is not None:
            # Replace, rather than selectively redact, nested executor failure
            # fields such as result/message/error/sdk_error.
            payload = dict(terminal_projection["event_payload"])
            message = str(terminal_projection["message"])
            error_code = terminal_projection["error_code"]
        elif failed_activity := PUBLIC_FAILED_ACTIVITY_EVENT_DETAILS.get(raw_event_type):
            # A failed step is useful progress, not evidence that the whole run
            # is terminal.  Its executor text and payload are never public.
            payload = {}
            message = str(failed_activity["message"])
            error_code = str(failed_activity["error_code"])
            stage = str(failed_activity["stage"])
            severity = str(failed_activity["severity"])
    return {
        "id": str(row["id"]),
        "schema_version": required_schema_version(
            row,
            "schema_version",
            EVENT_ENVELOPE_SCHEMA_VERSION,
            "invalid_event_schema_version",
        ),
        "event_id": str(row["id"]),
        "sequence": int(row.get("sequence") or 0),
        "run_id": run_id,
        "trace_id": str(row.get("trace_id") or standard_trace_id(run_id)),
        "event_type": event_type,
        "type": event_type,
        "stage": stage,
        "message": message,
        "severity": severity,
        "visible_to_user": visible_to_user,
        "error_code": error_code,
        "latency_ms": row.get("latency_ms"),
        "token_counts": {
            "input": int(row.get("input_token_count") or 0),
            "output": int(row.get("output_token_count") or 0),
            "total": int(row.get("total_token_count") or 0),
        },
        "cost": {"estimated_cost_minor": int(row.get("estimated_cost_minor") or 0)},
        "payload": payload,
        "created_at": row.get("created_at"),
    }


PUBLIC_STEP_KINDS = frozenset({"agent", "artifact", "checkpoint", "subagent", "tool", "worker"})
PUBLIC_STEP_STATUSES = frozenset({"pending", "running", "succeeded", "failed", "cancelled"})
PUBLIC_STEP_TITLES = {
    "pending": "等待执行",
    "running": "正在执行",
    "succeeded": "步骤已完成",
    "failed": "步骤未完成",
    "cancelled": "步骤已取消",
}


def _durable_public_step_id(row: dict[str, object]) -> str:
    """Return the persisted step row identity used by ordinary-user clients."""
    return str(row["id"])


def _ordinary_step_id_mapping(rows: list[dict[str, object]]) -> dict[str, str]:
    """Map only unambiguous persisted-row references to their public identities.

    Step keys come from the executor's plan and may name Skills, providers, or
    subagents.  They are therefore lookup input only.  The projected identity
    is always the durable ``run_steps.id`` that the server created.
    """
    candidates: dict[str, set[str]] = {}
    for row in rows:
        public_id = _durable_public_step_id(row)
        references = {public_id}
        raw_step_key = row.get("step_key")
        if isinstance(raw_step_key, str) and raw_step_key.strip():
            references.add(raw_step_key.strip())
        for reference in references:
            candidates.setdefault(reference, set()).add(public_id)
    return {
        reference: next(iter(public_ids))
        for reference, public_ids in candidates.items()
        if len(public_ids) == 1
    }


def _bounded_reference_count(value: object) -> int | None:
    if not isinstance(value, list):
        return None
    return min(len(value), 100_000)


def _mapped_dependencies(value: object, step_id_mapping: dict[str, str] | None) -> list[str] | None:
    if step_id_mapping is None or not isinstance(value, list):
        return None
    dependencies: list[str] = []
    for dependency in value:
        if not isinstance(dependency, str):
            return None
        public_id = step_id_mapping.get(dependency.strip())
        if public_id is None:
            return None
        dependencies.append(public_id)
    return dependencies


def _public_step_payload(
    raw_payload: dict[str, object],
    status: str,
    *,
    step_id_mapping: dict[str, str] | None = None,
) -> dict[str, object]:
    """Allowlist only structured ordinary-user step progress from persisted rows."""
    payload: dict[str, object] = {}
    dependency_count = _bounded_reference_count(raw_payload.get("depends_on"))
    if dependency_count is not None:
        payload["dependency_count"] = dependency_count
    mapped_dependencies = _mapped_dependencies(raw_payload.get("depends_on"), step_id_mapping)
    if mapped_dependencies:
        payload["depends_on"] = mapped_dependencies
    missing_dependency_count = _bounded_reference_count(raw_payload.get("missing_dependencies"))
    if missing_dependency_count is not None:
        payload["missing_dependency_count"] = missing_dependency_count
    for key in ("checkpoint_reused", "checkpoint_reuse_pending"):
        if isinstance(raw_payload.get(key), bool):
            payload[key] = raw_payload[key]
    for key in ("artifact_count", "progress"):
        value = raw_payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100_000:
            payload[key] = value
    # Existing public progress is retained only for nonfailed steps.  Failed
    # steps never copy an executor message, output, error, or metadata scalar.
    if status != "failed":
        public_note = sanitize_public_text(raw_payload.get("public_note"))
        if public_note:
            payload["public_note"] = public_note
    return payload


def _ordinary_run_step_response(
    row: dict[str, object],
    *,
    step_id_mapping: dict[str, str] | None = None,
) -> dict[str, object]:
    raw_payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    raw_status = normalize_step_status(row.get("status"))
    status = raw_status if raw_status in PUBLIC_STEP_STATUSES else "pending"
    raw_step_kind = str(row.get("step_kind") or "")
    step_kind = raw_step_kind if raw_step_kind in PUBLIC_STEP_KINDS else "agent"
    public_step_id = _durable_public_step_id(row)
    return {
        "id": public_step_id,
        "step_id": public_step_id,
        "run_id": str(row["run_id"]),
        "step_key": public_step_id,
        "step_kind": step_kind,
        "status": status,
        "title": PUBLIC_STEP_TITLES[status],
        "role": None,
        "sequence": int(row.get("sequence") or 0),
        "payload": _public_step_payload(raw_payload, status, step_id_mapping=step_id_mapping),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def run_step_response(row: dict[str, object], principal: AuthPrincipal | None = None) -> dict[str, object]:
    if principal is not None and not is_ai_admin(principal):
        return _ordinary_run_step_response(row)
    raw_payload = row.get("payload_json") or {}
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    payload = sanitize_public_payload(raw_payload)
    if not isinstance(payload, dict):
        payload = {}
    show_runtime_controls = principal is None or is_ai_admin(principal)
    skill_ids = raw_payload.get("skill_ids")
    mcp_tool_ids = raw_payload.get("mcp_tool_ids")
    resource_limits = raw_payload.get("resource_limits")
    sandbox_mode = raw_payload.get("sandbox_mode")
    browser_enabled = raw_payload.get("browser_enabled")
    title = sanitize_public_text(row.get("title"))
    role = sanitize_public_text(row.get("role")) if row.get("role") is not None else None
    response = {
        "id": str(row["id"]),
        "step_id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "step_key": str(row["step_key"]),
        "step_kind": str(row["step_kind"]),
        "status": normalize_step_status(row["status"]),
        "title": title,
        "role": role,
        "sequence": int(row.get("sequence") or 0),
        "payload": payload,
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if show_runtime_controls:
        response["skill_ids"] = [str(item) for item in skill_ids] if isinstance(skill_ids, list) else []
        response["mcp_tool_ids"] = [str(item) for item in mcp_tool_ids] if isinstance(mcp_tool_ids, list) else []
        response["resource_limits"] = dict(resource_limits) if isinstance(resource_limits, dict) else {}
        response["sandbox_mode"] = str(sandbox_mode) if sandbox_mode is not None else None
        response["browser_enabled"] = browser_enabled if isinstance(browser_enabled, bool) else None
    return response


def _multi_agent_snapshot_from_step_responses(
    run_id: str,
    steps: list[dict[str, object]],
) -> dict[str, object] | None:
    if not steps:
        return None
    counts = {
        "total": len(steps),
        "pending": sum(1 for item in steps if item["status"] == "pending"),
        "succeeded": sum(1 for item in steps if item["status"] == "succeeded"),
        "failed": sum(1 for item in steps if item["status"] == "failed"),
        "running": sum(1 for item in steps if item["status"] == "running"),
        "cancelled": sum(1 for item in steps if item["status"] == "cancelled"),
        "reused": sum(1 for item in steps if bool(item["payload"].get("checkpoint_reused"))),
        "blocked": sum(1 for item in steps if bool(item["payload"].get("missing_dependency_count"))),
    }
    return {"run_id": run_id, "steps": steps, "counts": counts}


def _ordinary_multi_agent_snapshot(
    run_id: str,
    rows: list[dict[str, object]],
) -> dict[str, object] | None:
    step_id_mapping = _ordinary_step_id_mapping(rows)
    return _multi_agent_snapshot_from_step_responses(
        run_id,
        [_ordinary_run_step_response(row, step_id_mapping=step_id_mapping) for row in rows],
    )


def multi_agent_snapshot_from_steps(
    run_id: str,
    rows: list[dict[str, object]],
    principal: AuthPrincipal | None = None,
) -> dict[str, object] | None:
    if principal is not None and not is_ai_admin(principal):
        return _ordinary_multi_agent_snapshot(run_id, rows)
    return _multi_agent_snapshot_from_step_responses(
        run_id,
        [run_step_response(row, principal=principal) for row in rows],
    )
