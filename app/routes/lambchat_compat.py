# ruff: noqa: B008

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from app import repositories, session_actions
from app.auth import (
    AuthPrincipal,
    is_ai_admin,
    require_principal,
    sign_principal_session,
    verify_principal_session,
)
from app.control_plane_contracts import (
    EVENT_ENVELOPE_SCHEMA_VERSION,
    standard_trace_id,
)
from app.db import transaction
from app.execution.api import list_public_models
from app.models import LoginRequest, SessionRenameRequest
from app.projection_redaction import (
    capability_id_from_skill,
    public_agent_id_for_projection,
    public_skill_display_label,
)
from app.public_execution import (
    PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
    PUBLIC_EXECUTION_EVENT_TYPES,
    public_execution_event_from_row,
    validate_public_agent_progress_payload,
)
from app.routes.auth import _login_principal
from app.routes.files import MAX_UPLOAD_BYTES, upload_file as upload_platform_file
from app.routes.runs import (
    artifact_card,
    event_visible_to_principal,
    run_event_response,
)
from app.runs import api as runs_api
from app.run_projection import (
    CHAT_PUBLIC_PROJECTION_VERSION,
    PublicChatAnswerStreamProjector,
    public_chat_answer_text,
    public_chat_terminal_projection,
    public_terminal_detail,
)
from app.settings import get_settings
from app.streaming.api import (
    LiveSubscriptionClosed,
    V4ProjectionError,
    V4StreamEntry,
    live_redis_id_is_after,
    project_public_envelope_v4,
    recover_v4_missing_terminal_stream,
    stream_live_channel,
    validate_public_application_payload_v4,
)
from app.streaming.authority import RunCursor, event_page
from app.streaming.redis import (
    SSE_AUTHORITY_LEASE_SECONDS,
    SseAuthorityConflictError,
    StreamContractError,
    StreamTransportUnavailable,
    acquire_sse_authority_lease,
    close_sse_authority_lease,
    get_stream_authority,
)
from app.tool_permission_projection import tool_permission_public_event_payload


class _V4ReplayBridge(Protocol):
    async def replay_page(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
        after_redis_id: str,
        through_redis_id: str,
    ) -> tuple[V4StreamEntry, ...]: ...


router = APIRouter()
logger = logging.getLogger(__name__)
_SSE_API_INSTANCE_ID = f"api_{uuid.uuid4().hex}"
_SSE_RETRYABLE_STARTUP_CODES = frozenset(
    {"sse_stream_not_admitted", "sse_stream_not_confirmed"}
)
_SSE_EXIT_REASONS = frozenset(
    {
        "terminal_completed",
        "client_disconnected",
        "live_source_closed",
        "transport_failure",
        "stream_contract_failure",
        "stream_setup_failure",
        "stream_cleanup_failure",
    }
)


def _safe_sse_correlation(value: object) -> str:
    return str(value or "")[:12]


def _sse_conflict(code: str) -> HTTPException:
    retryable = code in _SSE_RETRYABLE_STARTUP_CODES
    return HTTPException(
        status_code=409,
        detail={"code": code, "retryable": retryable},
        headers={
            "X-SSE-Error-Code": code,
            "X-SSE-Retryable": "true" if retryable else "false",
        },
    )


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _sse(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=_json_default)
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {payload}\n\n"


def _session_payload(row: dict[str, Any]) -> dict[str, Any]:
    agent_id = public_agent_id_for_projection(row.get("agent_id"))
    return {
        "id": row["id"],
        "agent_id": agent_id,
        "name": row.get("title") or "新会话",
        "metadata": {"agent_id": agent_id, "workspace_id": row["workspace_id"]},
        "is_active": row.get("status", "active") == "active",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "unread_count": 0,
    }


def _terminal_final_payload(
    run: dict[str, Any],
) -> tuple[str, dict[str, str], str] | None:
    """Adapt the authoritative terminal projection to the compatibility wire."""
    projection = public_chat_terminal_projection(run)
    if projection is None:
        return None
    payload = projection["payload"]
    if not isinstance(payload, dict):
        return None
    return str(projection["event_type"]), payload, str(projection["severity"])


@dataclass(frozen=True)
class _CompatibilityWireEvent:
    """One ordered, public compatibility event for both live and history adapters."""

    id: str
    stream_event_type: str
    stream_data: dict[str, object]
    history_event: dict[str, object]
    terminal: bool = False


@dataclass(frozen=True)
class _CompatibilityFoldState:
    """Public fold facts retained across durable pages of one stream."""

    has_strict_public_execution: bool
    seen_public_lifecycle_singletons: frozenset[str]
    answer_projection_state: tuple[str, str, bool] = ("", "", False)


CHAT_ASSISTANT_DELTA_SOURCE = "worker_answer_delta_v1"

@dataclass(frozen=True)
class _ChatPublicRunEventProjection:
    """Controlled Chat presentation for one explicitly allowlisted run event."""

    event_type: str
    stage: str
    message: str
    progress_kind: str
    wait_reason: str | None = None


CHAT_PUBLIC_RUN_EVENT_PROJECTIONS = {
    PUBLIC_AGENT_PROGRESS_EVENT_TYPE: _ChatPublicRunEventProjection(
        PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
        "agent_progress",
        "Agent progress update",
        "active",
    ),
    "agent.progress": _ChatPublicRunEventProjection(
        PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
        "agent_progress",
        "Agent progress update",
        "active",
    ),
    "thinking.started": _ChatPublicRunEventProjection(
        "public_activity", "thinking_started", "", "active"
    ),
    "thinking.delta": _ChatPublicRunEventProjection(
        "public_activity", "thinking_delta", "", "active"
    ),
    "thinking.completed": _ChatPublicRunEventProjection(
        "public_activity", "thinking_completed", "", "completed"
    ),
    "tool.started": _ChatPublicRunEventProjection(
        "public_tool_activity", "tool", "Tool execution started", "active"
    ),
    "tool.completed": _ChatPublicRunEventProjection(
        "public_tool_activity", "tool", "Tool execution completed", "completed"
    ),
    "tool.failed": _ChatPublicRunEventProjection(
        "public_tool_activity", "tool", "Tool execution failed", "failed"
    ),
    "tool.denied": _ChatPublicRunEventProjection(
        "public_tool_activity", "tool", "Tool execution denied", "failed"
    ),
    "run_queued": _ChatPublicRunEventProjection(
        "queued", "queue", "任务正在排队", "waiting", "queue_capacity"
    ),
    "queued": _ChatPublicRunEventProjection(
        "queued", "queue", "任务正在排队", "waiting", "queue_capacity"
    ),
    "worker_started": _ChatPublicRunEventProjection(
        "run_started", "execution", "已完成请求准备，正在进入受控执行阶段", "active"
    ),
    "run_started": _ChatPublicRunEventProjection(
        "run_started", "execution", "已完成请求准备，正在进入受控执行阶段", "active"
    ),
    "heartbeat": _ChatPublicRunEventProjection(
        "heartbeat", "liveness", "任务仍在运行。", "active"
    ),
    "mcp_tool_call_started": _ChatPublicRunEventProjection(
        "agent_step_started", "activity", "正在执行受控处理步骤", "active"
    ),
    "tool_call_started": _ChatPublicRunEventProjection(
        "agent_step_started", "activity", "正在执行受控处理步骤", "active"
    ),
    "tool_call_delta": _ChatPublicRunEventProjection(
        "agent_step_started", "activity", "受控处理步骤仍在进行", "active"
    ),
    "mcp_tool_call_completed": _ChatPublicRunEventProjection(
        "agent_step_completed", "activity", "受控处理步骤已完成", "completed"
    ),
    "tool_call_completed": _ChatPublicRunEventProjection(
        "agent_step_completed", "activity", "受控处理步骤已完成", "completed"
    ),
    "skill_used": _ChatPublicRunEventProjection(
        "capability_completed", "capability", "所需能力已完成", "completed"
    ),
    "capability_invoking": _ChatPublicRunEventProjection(
        "capability_invoking", "capability", "所需能力已请求调用", "active"
    ),
    "capability_completed": _ChatPublicRunEventProjection(
        "capability_completed", "capability", "所需能力已完成", "completed"
    ),
    "capability_failed": _ChatPublicRunEventProjection(
        "capability_failed", "capability", "所需能力未完成", "failed"
    ),
    "agent_step_started": _ChatPublicRunEventProjection(
        "agent_step_started", "activity", "正在执行当前计划步骤，完成后将汇总结果", "active"
    ),
    "agent_step_reused": _ChatPublicRunEventProjection(
        "agent_step_reused", "activity", "已复用可信阶段结果，正在继续后续步骤", "active"
    ),
    "agent_step_completed": _ChatPublicRunEventProjection(
        "agent_step_completed", "activity", "当前计划步骤已完成，正在继续后续处理", "completed"
    ),
    "agent_step_blocked": _ChatPublicRunEventProjection(
        "agent_step_blocked", "wait", "当前计划步骤正在等待前置条件", "waiting", "dependencies"
    ),
    "agent_step_failed": _ChatPublicRunEventProjection(
        "agent_step_failed", "activity", "当前计划步骤未完成，正在整理可操作错误", "failed"
    ),
    "subagent_started": _ChatPublicRunEventProjection(
        "subagent_started", "agent", "正在协同处理", "active"
    ),
    "subagent_completed": _ChatPublicRunEventProjection(
        "subagent_completed", "agent", "协同处理已完成", "completed"
    ),
    "subagent_failed": _ChatPublicRunEventProjection(
        "subagent_failed", "agent", "协同处理未能完成", "failed"
    ),
    "run_child_created": _ChatPublicRunEventProjection(
        "run_child_created", "agent", "已安排协同任务", "active"
    ),
    "skill_selected": _ChatPublicRunEventProjection(
        "capability_selected", "planning", "已加载授权处理能力，下一步将按所选流程分析请求", "completed"
    ),
    "capability_selected": _ChatPublicRunEventProjection(
        "capability_selected", "planning", "已加载授权处理能力，下一步将按所选流程分析请求", "completed"
    ),
    "capability_staged": _ChatPublicRunEventProjection(
        "capability_staged", "capability", "所需能力已加载到受控环境", "completed"
    ),
    "capability_sdk_registered": _ChatPublicRunEventProjection(
        "capability_sdk_registered", "capability", "所需能力已注册到执行引擎", "completed"
    ),
    "capability_actually_invoked": _ChatPublicRunEventProjection(
        "capability_actually_invoked", "capability", "所需能力已由执行引擎实际调用", "completed"
    ),
    "capability_optional_not_invoked": _ChatPublicRunEventProjection(
        "capability_optional_not_invoked", "capability", "可选能力本次未调用", "completed"
    ),
    "intent_detected": _ChatPublicRunEventProjection(
        "intent_detected", "preparation", "正在准备受控运行请求。", "active"
    ),
    "intent_confirmed": _ChatPublicRunEventProjection(
        "intent_confirmed", "planning", "已确认处理方式，下一步将准备授权上下文", "completed"
    ),
    "context_snapshot_created": _ChatPublicRunEventProjection(
        "context_snapshot_created", "context", "已准备运行上下文，下一步将处理授权输入", "completed"
    ),
    "checkpoint_created": _ChatPublicRunEventProjection(
        "context_snapshot_created", "context", "已保存阶段性进度", "completed"
    ),
    "file_bound": _ChatPublicRunEventProjection(
        "file_bound", "context", "已识别授权附件，下一步将确认文件结构", "completed"
    ),
    "artifact_created": _ChatPublicRunEventProjection(
        "artifact_ready", "artifact", "结果文件已可安全下载", "completed"
    ),
    "artifact_ready": _ChatPublicRunEventProjection(
        "artifact_ready", "artifact", "结果文件已可安全下载", "completed"
    ),
    "mcp_tool_denied": _ChatPublicRunEventProjection(
        "agent_step_blocked", "wait", "当前处理步骤未获授权，正在等待权限调整", "blocked", "permission"
    ),
    "tool_denied": _ChatPublicRunEventProjection(
        "agent_step_blocked", "wait", "当前处理步骤未获授权，正在等待权限调整", "blocked", "permission"
    ),
    "tool_permission_authorized": _ChatPublicRunEventProjection(
        "agent_step_started", "activity", "处理步骤已获授权，正在继续执行", "active"
    ),
    "tool_permission_denied": _ChatPublicRunEventProjection(
        "agent_step_blocked", "wait", "当前处理步骤未获授权，正在等待权限调整", "blocked", "permission"
    ),
    "tool_permission_requested": _ChatPublicRunEventProjection(
        "tool_permission_card", "policy", "正在等待权限决策", "waiting", "permission"
    ),
    "tool_permission_decided": _ChatPublicRunEventProjection(
        "tool_permission_card", "policy", "权限决策已记录", "completed"
    ),
    "tool_permission_terminalized": _ChatPublicRunEventProjection(
        "tool_permission_card", "policy", "权限请求已结束", "completed"
    ),
    "cancel_requested": _ChatPublicRunEventProjection(
        "cancel_requested", "status", "正在取消任务", "waiting", "cancellation"
    ),
    "cancel_requested_but_completed": _ChatPublicRunEventProjection(
        "cancel_requested_but_completed", "status", "任务已在取消前完成", "completed"
    ),
    "error": _ChatPublicRunEventProjection(
        "error", "status", "run_failed", "failed"
    ),
}


def _chat_event_marked_visible(event: dict[str, Any]) -> bool:
    """Honor an explicit hidden marker even for the public admin Chat surface."""
    if event.get("visible_to_user") is not None:
        return bool(event.get("visible_to_user"))
    payload = event.get("payload_json")
    if isinstance(payload, dict) and payload.get("visible_to_user") is not None:
        return bool(payload.get("visible_to_user"))
    return True


@dataclass(frozen=True)
class _StrictChatEventProduct:
    """One narrowly typed Chat product retained beside the generic event envelope."""

    kind: str
    generic_envelope: dict[str, object]
    payload: dict[str, object]


def _strict_typed_chat_event_product(
    run: dict[str, Any],
    event: dict[str, Any],
    principal: AuthPrincipal,
    *,
    answer_projector: PublicChatAnswerStreamProjector | None = None,
    final_answer_delta: bool = False,
) -> _StrictChatEventProduct | None:
    """Retain only exact answer deltas or reconstructed permission cards for Chat.

    Generic run events remain owned by ``run_event_response``.  This seam is
    deliberately narrower: it reads raw persisted data only to construct two
    pre-existing typed Chat products, never to relay arbitrary event text or
    payload fields.  Both live SSE and exact-run history use it through the
    shared compatibility event builder.
    """
    raw_event_type = str(event.get("event_type") or "")
    capability_product = _strict_capability_chat_product(run, event, principal)
    if capability_product is not None:
        return capability_product
    if raw_event_type not in {
        "assistant_delta",
        "tool_permission_requested",
        "tool_permission_decided",
        "tool_permission_terminalized",
    }:
        return None
    if not _chat_event_marked_visible(event) or not event_visible_to_principal(event, principal):
        return None
    run_id = str(run["id"])
    raw_payload = event.get("payload_json")
    if not isinstance(raw_payload, dict):
        return None
    if raw_event_type == "assistant_delta":
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            return None
        page = event_page(cursor=RunCursor(run_id=run_id, sequence=sequence - 1), rows=(event,))
        if len(page.events) != 1:
            return None
        delta = page.events[0]
        content = (
            answer_projector.push(delta.delta, final=final_answer_delta)
            if answer_projector is not None
            else public_chat_answer_text(run, delta.delta)
        )
        if not content:
            return None
        return _StrictChatEventProduct(
            kind="assistant_delta",
            generic_envelope={"event_id": delta.event_id, "sequence": delta.cursor.sequence},
            payload={
                "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
                "projection_kind": "assistant_delta",
                "event_id": delta.event_id,
                "sequence": delta.cursor.sequence,
                "run_id": run_id,
                "content": content,
            },
        )
    generic_envelope = run_event_response(run_id, event, principal=principal)
    card_source = raw_payload.get("tool_permission_card")
    reconstructed = tool_permission_public_event_payload(
        run_id=run_id,
        event_type=raw_event_type,
        payload=card_source if isinstance(card_source, dict) else raw_payload,
    )
    card = reconstructed.get("tool_permission_card")
    if not isinstance(card, dict):
        return None
    return _StrictChatEventProduct(
        kind="tool_permission_card",
        generic_envelope=generic_envelope,
        payload={"tool_permission_card": card},
    )


def _strict_capability_chat_product(
    run: dict[str, Any],
    event: dict[str, Any],
    principal: AuthPrincipal,
) -> _StrictChatEventProduct | None:
    """Project current and legacy capability rows to one identity-safe product."""

    raw_event_type = str(event.get("event_type") or "")
    if raw_event_type not in {
        "skill_selected",
        "capability_selected",
        "skill_used",
        "tool_call_completed",
        "capability_invoking",
        "capability_completed",
        "capability_failed",
    }:
        return None
    if not _chat_event_marked_visible(event) or not event_visible_to_principal(event, principal):
        return None
    payload = event.get("payload_json")
    if not isinstance(payload, dict):
        return None
    kind = ""
    name = ""
    status = ""
    capability = payload.get("capability")
    if isinstance(capability, dict):
        kind = str(capability.get("kind") or "")
        name = public_skill_display_label(capability.get("name")) or ""
        status = str(capability.get("status") or "")
    elif raw_event_type in {"skill_selected", "skill_used"}:
        kind = "skill"
        name = public_skill_display_label(payload.get("public_capability_label")) or ""
        status = "selected" if raw_event_type == "skill_selected" else "completed"
    elif raw_event_type == "tool_call_completed" and payload.get("tool_category") == "mcp":
        kind = "mcp"
        name = public_skill_display_label(payload.get("tool_label")) or ""
        status = "completed"
    if kind not in {"skill", "mcp"} or not name:
        return None
    expected_status = {
        "skill_selected": "selected",
        "capability_selected": "selected",
        "skill_used": "completed",
        "tool_call_completed": "completed",
        "capability_invoking": "invoking",
        "capability_completed": "completed",
        "capability_failed": "failed",
    }[raw_event_type]
    if status != expected_status:
        return None
    generic_envelope = run_event_response(str(run["id"]), event, principal=principal)
    return _StrictChatEventProduct(
        kind="capability",
        generic_envelope=generic_envelope,
        payload={"capability": {"kind": kind, "name": name, "status": status}},
    )


def _chat_projection_payload(envelope: dict[str, Any]) -> dict[str, object]:
    """Copy only the fixed activity tuple from the generic public envelope."""
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return {}
    activity = payload.get("activity")
    if set(payload) != {"activity"} or not isinstance(activity, dict):
        return {}
    activity_fields = set(activity)
    if activity_fields not in (
        {"category", "status"},
        {"category", "status", "meaningful"},
    ):
        return {}
    if not isinstance(activity.get("category"), str) or not isinstance(activity.get("status"), str):
        return {}
    if activity_fields == {"category", "status", "meaningful"} and activity != {
        "category": "liveness",
        "status": "running",
        "meaningful": False,
    }:
        return {}
    return {"activity": dict(activity)}


def _public_progress_identity(
    payload: dict[str, object] | None,
) -> tuple[str, str, str, str, str] | None:
    if payload is None:
        return None
    return (
        str(payload["schema_version"]),
        str(payload["step_id"]),
        str(payload["phase"]),
        str(payload["lifecycle"]),
        str(payload["message"]),
    )


def _strict_v4_execution_history_payload(
    event_type: str, payload: object
) -> dict[str, object] | None:
    if event_type not in {
        "agent.progress",
        "thinking.started",
        "thinking.delta",
        "thinking.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "tool.denied",
    }:
        return None
    if not isinstance(payload, dict):
        return None
    application_payload = {
        key: value for key, value in payload.items() if key != "__stream_v4"
    }
    try:
        validated = validate_public_application_payload_v4(
            event_type, application_payload
        )
    except V4ProjectionError:
        return None
    if event_type.startswith("tool."):
        validated["status"] = event_type.removeprefix("tool.")
    return validated


def _public_run_event_envelope(
    run: dict[str, Any],
    event: dict[str, Any],
    principal: AuthPrincipal,
) -> dict[str, object] | None:
    """Project one persisted event through the explicit public Chat allowlist."""
    run_id = str(run["id"])
    raw_event_type = str(event.get("event_type") or "")
    presentation = CHAT_PUBLIC_RUN_EVENT_PROJECTIONS.get(raw_event_type)
    if presentation is None:
        return None
    strict_v4_payload = _strict_v4_execution_history_payload(
        raw_event_type, event.get("payload_json")
    )
    if raw_event_type in {
        "agent.progress",
        "thinking.started",
        "thinking.delta",
        "thinking.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "tool.denied",
    } and strict_v4_payload is None:
        return None
    if raw_event_type == "agent.progress":
        public_progress = strict_v4_payload
    elif raw_event_type == PUBLIC_AGENT_PROGRESS_EVENT_TYPE:
        public_progress = validate_public_agent_progress_payload(
            event.get("payload_json")
        )
    else:
        public_progress = None
    if raw_event_type in {
        "agent.progress",
        PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
    } and public_progress is None:
        return None
    typed_product = _strict_typed_chat_event_product(run, event, principal)
    if typed_product is not None and typed_product.kind == "capability":
        capability_status = str(typed_product.payload["capability"]["status"])
        presentation = CHAT_PUBLIC_RUN_EVENT_PROJECTIONS[f"capability_{capability_status}"]
    projected = (
        typed_product.generic_envelope
        if typed_product is not None
        else run_event_response(run_id, event, principal=principal)
    )
    raw_payload = event.get("payload_json")
    if projected.get("event_type") == "heartbeat" or (
        raw_event_type == "run_started"
        and isinstance(raw_payload, dict)
        and raw_payload.get("heartbeat") is True
    ):
        presentation = CHAT_PUBLIC_RUN_EVENT_PROJECTIONS["heartbeat"]
    severity = str(projected.get("severity") or "info")
    if presentation.progress_kind == "failed":
        severity = "error"
    elif presentation.progress_kind == "blocked" and severity == "info":
        severity = "warning"
    elif severity not in {"info", "warning", "error"}:
        severity = "info"
    payload = (
        strict_v4_payload
        if strict_v4_payload is not None
        else typed_product.payload
        if typed_product is not None
        else _chat_projection_payload(projected)
    )
    message = presentation.message
    stage = presentation.stage
    if public_progress is not None:
        payload = public_progress
        message = public_progress["message"]
        stage = public_progress["phase"]
        presentation = _ChatPublicRunEventProjection(
            PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
            stage,
            message,
            "failed" if public_progress["lifecycle"] == "failed" else "completed"
            if public_progress["lifecycle"] == "completed"
            else "active",
        )
    if raw_event_type.startswith("thinking.") and strict_v4_payload is not None:
        thinking_message = strict_v4_payload.get("delta")
        if not isinstance(thinking_message, str):
            thinking_message = strict_v4_payload.get("public_summary")
        message = thinking_message if isinstance(thinking_message, str) else ""
        stage = raw_event_type.replace(".", "_")
    if raw_event_type == "error":
        terminal = public_chat_terminal_projection(
            {"status": "failed", "error_code": event.get("error_code")}
        )
        if terminal is not None:
            message = str(terminal["message"])
            terminal_payload = terminal["event_payload"]
            payload = dict(terminal_payload) if isinstance(terminal_payload, dict) else {}
    return {
        "id": str(projected["id"]),
        "schema_version": str(projected["schema_version"]),
        "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
        "event_id": str(projected["event_id"]),
        "sequence": int(projected["sequence"]),
        "run_id": run_id,
        "event_type": presentation.event_type,
        "type": presentation.event_type,
        "stage": stage,
        "message": message,
        "severity": severity,
        "visible_to_user": True,
        "progress_kind": presentation.progress_kind,
        "wait_reason": presentation.wait_reason,
        "payload": payload,
        "created_at": projected.get("created_at"),
    }


def _assistant_delta_projection(
    run: dict[str, Any],
    event: dict[str, Any],
    principal: AuthPrincipal,
    *,
    answer_projector: PublicChatAnswerStreamProjector | None = None,
    final_answer_delta: bool = False,
) -> dict[str, object] | None:
    """Return a sanitized delta frame without carrying any executor payload."""
    typed_product = _strict_typed_chat_event_product(
        run,
        event,
        principal,
        answer_projector=answer_projector,
        final_answer_delta=final_answer_delta,
    )
    if typed_product is None or typed_product.kind != "assistant_delta":
        return None
    return typed_product.payload


def _event_sequence_sort_key(event: dict[str, Any], position: int) -> tuple[int, int]:
    """Keep persisted compatibility playback monotonic even with malformed rows."""
    try:
        return (int(event.get("sequence")), position)
    except (TypeError, ValueError):
        return (2**63 - 1, position)


def _compatibility_events_for_run(
    run: dict[str, Any],
    run_events: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    principal: AuthPrincipal,
    *,
    user_messages: list[dict[str, Any]] | None = None,
    include_terminal: bool = True,
) -> list[_CompatibilityWireEvent]:
    """Build the sole public terminal wire, ordered for live and history replay."""
    compatibility_events, _ = _compatibility_events_for_run_page(
        run,
        run_events,
        artifacts,
        principal,
        fold_state=_CompatibilityFoldState(False, frozenset()),
        user_messages=user_messages,
        include_terminal=include_terminal,
    )
    return compatibility_events


def _compatibility_events_for_run_page(
    run: dict[str, Any],
    run_events: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    principal: AuthPrincipal,
    *,
    fold_state: _CompatibilityFoldState,
    user_messages: list[dict[str, Any]] | None = None,
    include_terminal: bool = True,
) -> tuple[list[_CompatibilityWireEvent], _CompatibilityFoldState]:
    """Fold one durable page while carrying only public compatibility facts forward."""
    run_id = str(run["id"])
    trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
    compatibility_events: list[_CompatibilityWireEvent] = []
    status = _platform_status(str(run.get("status") or ""))
    has_strict_public_execution = fold_state.has_strict_public_execution or any(
        str(event.get("event_type") or "") in PUBLIC_EXECUTION_EVENT_TYPES
        and public_execution_event_from_row(run_id, event) is not None
        for event in run_events
    )
    legacy_capability_event_types = {
        "skill_selected",
        "capability_selected",
        "skill_used",
        "tool_call_completed",
        "capability_invoking",
        "capability_completed",
        "capability_failed",
    }
    public_lifecycle_singletons = {
        "intent_detected",
        "capability_selected",
        "run_started",
    }
    seen_public_lifecycle_singletons = set(fold_state.seen_public_lifecycle_singletons)
    ordered_events = sorted(
        enumerate(run_events),
        key=lambda item: _event_sequence_sort_key(item[1], item[0]),
    )
    canonical_progress_identities = {
        identity
        for _, event in ordered_events
        if str(event.get("event_type") or "") == "agent.progress"
        and (
            identity := _public_progress_identity(
                _strict_v4_execution_history_payload(
                    "agent.progress", event.get("payload_json")
                )
            )
        )
        is not None
    }
    answer_projector = PublicChatAnswerStreamProjector(
        run,
        fold_state.answer_projection_state,
    )
    final_answer_event = next(
        (
            event
            for _, event in reversed(ordered_events)
            if include_terminal
            and status in {"succeeded", "failed", "cancelled"}
            and str(event.get("event_type") or "") == "assistant_delta"
            and _chat_event_marked_visible(event)
            and event_visible_to_principal(event, principal)
        ),
        None,
    )

    for message in user_messages or []:
        message_id = str(message.get("id") or "")
        if (
            not message_id
            or str(message.get("run_id") or "") != run_id
        ):
            continue
        message_data = {
            "message_id": message_id,
            "run_id": run_id,
            "content": str(message.get("content") or ""),
        }
        metadata = message.get("metadata_json")
        locked_skill = metadata.get("locked_skill") if isinstance(metadata, dict) else None
        locked_skill_label = public_skill_display_label(
            locked_skill.get("label") if isinstance(locked_skill, dict) else None
        )
        if locked_skill_label:
            message_data["locked_skill_label"] = locked_skill_label
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=message_id,
                stream_event_type="user:message",
                stream_data=message_data,
                history_event={
                    "id": message_id,
                    "type": "user:message",
                    "event_type": "user:message",
                    "timestamp": message.get("created_at"),
                    "run_id": run_id,
                    "data": message_data,
                },
            )
        )

    for position, event in ordered_events:
        raw_event_type = str(event.get("event_type") or "")
        if raw_event_type in CHAT_STREAM_TERMINAL_EVENT_TYPES:
            continue
        if has_strict_public_execution and raw_event_type in legacy_capability_event_types:
            continue
        if (
            not _chat_event_marked_visible(event)
            or not event_visible_to_principal(event, principal)
        ):
            continue
        if (
            raw_event_type == PUBLIC_AGENT_PROGRESS_EVENT_TYPE
            and _public_progress_identity(
                validate_public_agent_progress_payload(event.get("payload_json"))
            )
            in canonical_progress_identities
        ):
            continue
        if raw_event_type in PUBLIC_EXECUTION_EVENT_TYPES:
            execution_event = public_execution_event_from_row(run_id, event)
            if execution_event is None:
                continue
            event_type = str(event["event_type"])
            compatibility_events.append(
                _CompatibilityWireEvent(
                    id=str(execution_event["event_id"]),
                    stream_event_type=event_type,
                    stream_data=execution_event,
                    history_event={
                        "id": execution_event["event_id"],
                        "schema_version": execution_event["schema_version"],
                        "trace_id": str(event.get("trace_id") or trace_id),
                    "type": event_type,
                    "event_type": event_type,
                        "stage": execution_event["stage"],
                        "severity": "error" if execution_event["status"] == "failed" else "info",
                        "visible_to_user": True,
                        "payload": execution_event,
                        "sequence": execution_event["sequence"],
                        "data": execution_event,
                        "timestamp": execution_event["created_at"],
                        "run_id": run_id,
                    },
                )
            )
            continue
        raw_payload = event.get("payload_json")
        if raw_event_type.startswith("tool_call") and isinstance(raw_payload, dict) and {
            "command",
            "args",
            "arguments",
            "result",
            "output",
            "tool_input",
            "tool_output",
            "private_payload",
            "executor_private_payload",
        } & set(raw_payload):
            continue
        if raw_event_type == "assistant_delta":
            delta = _assistant_delta_projection(
                run,
                event,
                principal,
                answer_projector=answer_projector,
                final_answer_delta=event is final_answer_event,
            )
            if delta is None:
                continue
            compatibility_events.append(
                _CompatibilityWireEvent(
                    id=str(event["id"]),
                    stream_event_type="message:chunk",
                    stream_data=delta,
                    history_event={
                        "id": event["id"],
                        "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                        "trace_id": str(event.get("trace_id") or trace_id),
                        "type": "message:chunk",
                        "event_type": "message:chunk",
                        "stage": "answer",
                        "severity": "info",
                        "visible_to_user": True,
                        "payload": delta,
                        "sequence": delta["sequence"],
                        "data": delta,
                        "timestamp": event.get("created_at"),
                        "run_id": run_id,
                    },
                )
            )
            continue
        envelope = _public_run_event_envelope(run, event, principal)
        if envelope is None:
            continue
        public_event_type = str(envelope["event_type"])
        if public_event_type in public_lifecycle_singletons:
            if public_event_type in seen_public_lifecycle_singletons:
                continue
            seen_public_lifecycle_singletons.add(public_event_type)
        payload = envelope["payload"] if isinstance(envelope.get("payload"), dict) else {}
        history_data = {
            **payload,
            "projection_version": envelope["projection_version"],
            "event_id": envelope["event_id"],
            "run_id": run_id,
            "event_type": envelope["event_type"],
            "stage": envelope["stage"],
            "message": envelope["message"],
            "severity": envelope["severity"],
            "progress_kind": envelope["progress_kind"],
            "wait_reason": envelope["wait_reason"],
            "payload": payload,
            "created_at": envelope.get("created_at"),
        }
        if envelope["event_type"] == "error":
            history_data["error"] = envelope["message"]
        elif envelope["stage"] == "queue":
            history_data["status"] = "queued"
        else:
            history_data["content"] = envelope["message"]
            history_data.setdefault("status", envelope["stage"])
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=str(event["id"]),
                stream_event_type="run_event",
                stream_data=envelope,
                history_event={
                    "id": event["id"],
                    "schema_version": envelope["schema_version"],
                    "trace_id": str(event.get("trace_id") or trace_id),
                    # Production history preserves the public persisted event
                    # type at the outer level; it is not a synthetic run_event.
                    "type": envelope["type"],
                    "event_type": envelope["event_type"],
                    "stage": envelope["stage"],
                    "severity": envelope["severity"],
                    "visible_to_user": envelope["visible_to_user"],
                    "payload": envelope["payload"],
                    "sequence": envelope["sequence"],
                    "data": history_data,
                    "timestamp": event.get("created_at"),
                    "run_id": run_id,
                },
            )
        )

    for artifact in sorted(
        artifacts,
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")),
    ):
        artifact_id = str(artifact["id"])
        public_artifact = artifact_card(artifact, principal=principal)
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=f"{artifact_id}:artifact",
                stream_event_type="artifact_card",
                stream_data=public_artifact,
                history_event={
                    "id": f"{artifact_id}:artifact",
                    "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                    "trace_id": str(artifact.get("trace_id") or trace_id),
                    "type": "artifact_card",
                    "event_type": "artifact_card",
                    "stage": "artifact",
                    "severity": "info",
                    "visible_to_user": True,
                    "payload": public_artifact,
                    "data": public_artifact,
                    "timestamp": artifact.get("created_at"),
                    "run_id": run_id,
                },
            )
        )

    final_payload = _terminal_final_payload(run) if include_terminal else None
    if final_payload is not None:
        event_type, payload, severity = final_payload
        final_data = {"run_id": run_id, **payload}
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=f"{run_id}:final",
                stream_event_type=event_type,
                stream_data=final_data,
                history_event={
                    "id": f"{run_id}:final",
                    "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                    "trace_id": trace_id,
                    "type": event_type,
                    "event_type": event_type,
                    "stage": "answer",
                    "severity": severity,
                    "visible_to_user": True,
                    "payload": final_data,
                    "data": final_data,
                    "timestamp": run.get("finished_at"),
                    "run_id": run_id,
                },
            )
        )

    if include_terminal and status in {"succeeded", "failed", "cancelled"}:
        terminal_data = {"run_id": run_id, "status": status}
        compatibility_events.append(
            _CompatibilityWireEvent(
                id=f"{run_id}:terminal:{status}",
                stream_event_type="done",
                stream_data=terminal_data,
                history_event={
                    "id": f"{run_id}:terminal:{status}",
                    "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                    "trace_id": trace_id,
                    "type": "done",
                    "event_type": "done",
                    "stage": "terminal",
                    "severity": "error" if status == "failed" else "info",
                    "visible_to_user": True,
                    "payload": terminal_data,
                    "data": terminal_data,
                    "timestamp": run.get("finished_at"),
                    "run_id": run_id,
                },
                terminal=True,
            )
        )
    return compatibility_events, _CompatibilityFoldState(
        has_strict_public_execution=has_strict_public_execution,
        seen_public_lifecycle_singletons=frozenset(seen_public_lifecycle_singletons),
        answer_projection_state=answer_projector.state,
    )


def _public_error_text(run: dict[str, Any], _principal: AuthPrincipal) -> str:
    if _platform_status(str(run.get("status") or "")) != "failed":
        return ""
    projection = public_chat_terminal_projection(run)
    return str(projection["message"]) if projection is not None else ""


def _platform_status(status: str) -> str:
    return "cancelled" if status == "canceled" else status


def _lambchat_status(status: str) -> str:
    status = _platform_status(status)
    return {
        "succeeded": "completed",
        "failed": "error",
        "queued": "pending",
        "running": "running",
    }.get(status, status)


@router.post("/auth/login")
async def login(request: LoginRequest) -> dict[str, object]:
    principal = await _login_principal(request)
    token = sign_principal_session(principal)
    settings = get_settings()
    return {
        "access_token": token,
        "refresh_token": token,
        "token_type": "bearer",
        "expires_in": settings.ai_session_max_age_seconds,
    }


@router.get("/auth/me")
async def me(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    return {
        "id": principal.user_id,
        "username": principal.user_id,
        "email": "",
        "avatar_url": None,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "is_active": True,
        "metadata": {"display_name": principal.display_name, "source": principal.source},
        "created_at": "",
        "updated_at": "",
    }


@router.post("/auth/refresh")
async def refresh(payload: dict[str, str]) -> dict[str, object]:
    principal = verify_principal_session(payload.get("refresh_token") or "")
    token = sign_principal_session(principal)
    return {
        "access_token": token,
        "refresh_token": token,
        "token_type": "bearer",
        "expires_in": get_settings().ai_session_max_age_seconds,
    }


@router.get("/auth/oauth/providers")
async def oauth_providers() -> dict[str, object]:
    return {
        "providers": [],
        "registration_enabled": False,
        "turnstile": {
            "enabled": False,
            "site_key": "",
            "require_on_login": False,
            "require_on_register": False,
            "require_on_password_change": False,
        },
    }


UI_PERMISSIONS = [
    "agent:use",
    "artifact:download",
    "model:admin",
    "settings:manage",
    "admin:status",
    "chat:read",
    "chat:write",
    "session:read",
    "session:write",
    "file:upload",
    "file:upload:document",
    "skill:read",
    "skill:write",
    "skill:delete",
    "skill:admin",
    "marketplace:read",
    "marketplace:publish",
    "marketplace:admin",
    "user:read",
    "user:admin",
    "settings:read",
    "settings:admin",
    "feedback:read",
    "feedback:admin",
    "notification:read",
    "notification:admin",
]

CHAT_STREAM_TERMINAL_EVENT_TYPES = {"run_succeeded", "run_failed", "run_cancelled", "run_canceled"}


def _profile_payload(principal: AuthPrincipal, metadata: dict[str, Any] | None = None) -> dict[str, object]:
    merged_metadata = {"display_name": principal.display_name, "source": principal.source}
    if metadata:
        merged_metadata.update(metadata)
    return {
        "id": principal.user_id,
        "username": principal.user_id,
        "email": "",
        "avatar_url": None,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "is_active": True,
        "metadata": merged_metadata,
        "created_at": "",
        "updated_at": "",
    }


@router.get("/auth/permissions")
async def permissions() -> dict[str, object]:
    permission_infos = [
        {"value": item, "label": item, "description": item}
        for item in UI_PERMISSIONS
    ]
    return {
        "groups": [{"name": "AI Platform POC", "permissions": permission_infos}],
        "all_permissions": permission_infos,
    }


@router.get("/auth/profile")
async def profile(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    return _profile_payload(principal)


@router.put("/auth/profile/metadata")
async def update_profile_metadata(
    payload: dict[str, Any], principal: AuthPrincipal = Depends(require_principal)
) -> dict[str, object]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return _profile_payload(principal, metadata=metadata)


@router.get("/agent/models/available")
async def available_models(
    _principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        return await list_public_models(conn)


@router.get("/agent/models/")
async def model_configs() -> dict[str, object]:
    async with transaction() as conn:
        catalog = await list_public_models(conn)
    models = [
        {**model, "enabled": True, "order": index}
        for index, model in enumerate(catalog["models"], start=1)
    ]
    return {**catalog, "models": models}


@router.get("/version")
async def version() -> dict[str, object]:
    return {"version": "ai-platform-poc"}


@router.get("/projects")
@router.get("/projects/")
async def projects() -> list[object]:
    return []


@router.get("/upload/config")
async def upload_config() -> dict[str, object]:
    upload_limits_bytes = {
        "image": MAX_UPLOAD_BYTES,
        "video": MAX_UPLOAD_BYTES,
        "audio": MAX_UPLOAD_BYTES,
        "document": MAX_UPLOAD_BYTES,
    }
    max_files = 10
    return {
        "enabled": True,
        "provider": "ai-platform",
        "uploadLimitsBytes": upload_limits_bytes,
        "maxFiles": max_files,
        # Preserve the pre-existing byte-valued wire aliases. Older frontends
        # remain bounded by the canonical server-side 413 during rollout.
        "uploadLimits": {
            **upload_limits_bytes,
            "maxFiles": max_files,
        },
        "max_file_size_bytes": MAX_UPLOAD_BYTES,
        "max_file_size": MAX_UPLOAD_BYTES,
        "allowed_extensions": ["docx", "txt", "pdf"],
        "categories": ["document"],
    }


@router.post("/upload/file")
async def upload_file(
    file: UploadFile = File(...),
    folder: str = "uploads",
    workspace_id: str = Form("default"),
    session_id: str | None = Form(None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    uploaded = await upload_platform_file(
        file=file,
        workspace_id=workspace_id,
        session_id=session_id,
        principal=principal,
    )
    mime_type = file.content_type or "application/octet-stream"
    return {
        "key": uploaded.file_id,
        "file_id": uploaded.file_id,
        "url": f"/api/ai/files/{uploaded.file_id}",
        "name": uploaded.name,
        "type": folder,
        "mime_type": mime_type,
        "mimeType": mime_type,
        "size": uploaded.size_bytes,
        "sha256": uploaded.sha256,
    }


@router.get("/tools")
async def tools() -> dict[str, object]:
    return {"tools": []}


@router.get("/roles")
@router.get("/roles/")
async def roles(skip: int = 0, limit: int = 100, q: str | None = None) -> dict[str, object]:
    limit = max(1, min(limit, 200))
    skip = max(0, skip)
    return {"roles": [], "total": 0, "skip": skip, "limit": limit, "q": q or ""}


@router.get("/sessions")
async def sessions(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    async with transaction() as conn:
        rows = await repositories.list_authorized_sessions(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
    items = [_session_payload(row) for row in rows]
    return {"sessions": items, "total": len(items), "skip": 0, "limit": 100, "has_more": False}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    async with transaction() as conn:
        row = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return _session_payload(row)


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    request: SessionRenameRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    try:
        async with transaction() as conn:
            row = await session_actions.rename_session(
                conn,
                principal=principal,
                session_id=session_id,
                title=request.name,
            )
    except session_actions.SessionActionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except session_actions.SessionActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    return {"status": "updated", "session": _session_payload(row)}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    try:
        async with transaction() as conn:
            result = await session_actions.delete_session(conn, principal=principal, session_id=session_id)
    except session_actions.SessionActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    return {
        "status": "deleted",
        "already_deleted": result["already_deleted"],
        "session": _session_payload(result["session"]),
    }


@router.post("/sessions/{session_id}/messages/{message_id}/fork")
async def fork_session_message(
    session_id: str,
    message_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    try:
        async with transaction() as conn:
            result = await session_actions.fork_session_message(
                conn,
                principal=principal,
                session_id=session_id,
                message_id=message_id,
            )
    except session_actions.SessionActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc
    except session_actions.SessionActionValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "source_session_id": result["source_session_id"],
        "session": _session_payload(result["session"]),
    }


@router.get("/sessions/{session_id}/runs")
async def session_runs(
    session_id: str,
    limit: int = 20,
    trace_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        session = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        rows = await repositories.list_authorized_session_runs(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
            limit=max(1, min(limit, 100)),
        )
    runs = []
    for row in rows:
        if trace_id and row.get("trace_id") != trace_id:
            continue
        status = _platform_status(str(row["status"]))
        terminal_detail = (
            public_terminal_detail(status, row.get("error_code"))
            if status == "failed"
            else None
        )
        item = {
            "id": row["id"],
            "run_id": row["id"],
            "trace_id": row.get("trace_id") or standard_trace_id(str(row["id"])),
            "agent_id": row["agent_id"]
            if is_ai_admin(principal)
            else public_agent_id_for_projection(row.get("agent_id"), row.get("skill_id")),
            "capability_id": capability_id_from_skill(row["skill_id"], row["agent_id"]),
            "status": status,
            "error": _public_error_text(row, principal),
            "error_code": (terminal_detail or {}).get("detail_code"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at") or row.get("queued_at") or row.get("created_at"),
            "completed_at": row.get("finished_at"),
            "finished_at": row.get("finished_at"),
        }
        if is_ai_admin(principal):
            item["skill_id"] = row["skill_id"]
        runs.append(item)
    return {
        "session_id": session_id,
        "runs": runs,
        "count": len(rows),
    }


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    run_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        session = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        if run_id is not None:
            target = await repositories.get_authorized_run(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if target is None or target.get("session_id") != session_id:
                raise HTTPException(status_code=404, detail="run_not_found")
            target_runs = [target]
            current_run_id = run_id
        else:
            # Display ordering is deterministic, but only a generation-bearing
            # row may be reported as the session's current authority.
            target_runs = await repositories.list_authorized_session_runs(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
                limit=50,
            )
            current = next(
                (row for row in target_runs if row.get("session_generation") is not None),
                None,
            )
            current_run_id = str(current["id"]) if current is not None else None
        target_run_ids = [str(run["id"]) for run in target_runs]
        authorized_user_messages = await repositories.list_authorized_user_messages_for_runs(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
            run_ids=target_run_ids,
        )
        user_messages_by_run: dict[str, list[dict[str, Any]]] = {
            target_run_id: [] for target_run_id in target_run_ids
        }
        for message in authorized_user_messages:
            message_run_id = str(message.get("run_id") or "")
            if message_run_id in user_messages_by_run:
                user_messages_by_run[message_run_id].append(message)
        events = []
        for run in reversed(target_runs):
            run_events = await repositories.list_run_events(conn, tenant_id=principal.tenant_id, run_id=run["id"])
            artifacts = await repositories.list_run_artifacts(
                conn,
                tenant_id=principal.tenant_id,
                run_id=run["id"],
            )
            events.extend(
                record.history_event
                for record in _compatibility_events_for_run(
                    run,
                    run_events,
                    artifacts,
                    principal,
                    user_messages=user_messages_by_run.get(str(run["id"]), []),
                )
            )
    return {
        "session_id": session_id,
        "run_id": run_id,
        "current_run_id": current_run_id,
        "events": events,
    }


@router.post("/sessions/{session_id}/generate-title")
async def generate_title(
    session_id: str,
    message: str = "",
    lang: str = "en",
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, str]:
    title = (message or "").strip().replace("\n", " ")[:32] or "新会话"
    async with transaction() as conn:
        projection = await repositories.get_authorized_session_projection(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if projection is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        try:
            session = await session_actions.initialize_session_title(
                conn,
                principal=principal,
                session_id=session_id,
                title=title,
            )
        except session_actions.SessionActionValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except session_actions.SessionActionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="session_not_found") from exc
    return {"session_id": session_id, "title": str(session["title"])}


@router.post("/sessions/{session_id}/mark-read")
async def mark_read(session_id: str) -> dict[str, bool]:
    return {"success": True}


@router.post("/chat/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> dict[str, object]:
    raise HTTPException(status_code=410, detail="session_cancel_unsupported_use_run_cancel")


@router.get("/chat/sessions/{session_id}/status")
async def chat_status(
    session_id: str,
    run_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        session = await repositories.get_authorized_lambchat_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        if run_id is not None:
            # An explicit id is a precise, principal-scoped lookup: it must
            # not inherit the list endpoint's recency limit or another
            # session's state.
            target = await repositories.get_authorized_run(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_id=run_id,
            )
            if target is None or target.get("session_id") != session_id:
                raise HTTPException(status_code=404, detail="run_not_found")
        else:
            # Legacy rows remain visible through the history route, but cannot
            # become an implicit current-status authority.
            rows = await repositories.list_authorized_session_runs(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
                limit=10,
            )
            target = next((row for row in rows if row.get("session_generation") is not None), None)
    raw_status = _platform_status(str(target["status"])) if target else "idle"
    return {"session_id": session_id, "run_id": run_id, "status": _lambchat_status(raw_status), "raw_status": raw_status}


async def _restore_chat_stream_projection(
    bridge: _V4ReplayBridge,
    *,
    run: dict[str, Any],
    tenant_scope_value: str,
    run_id: str,
    attempt_id: str,
    stream_incarnation: int,
    through_redis_id: str,
) -> tuple[PublicChatAnswerStreamProjector, str | None, bool]:
    """Rebuild private projection and terminal state through one resume cursor."""
    projector = PublicChatAnswerStreamProjector(run)
    terminal_event_id: str | None = None
    ended = False
    if through_redis_id == "0-0":
        return projector, terminal_event_id, ended
    after = "0-0"
    saw_stream_open = False
    while after != through_redis_id:
        previous_after = after
        entries = await bridge.replay_page(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
            after_redis_id=after,
            through_redis_id=through_redis_id,
        )
        if not entries:
            raise StreamContractError("stream_projection_history_unavailable")
        for entry in entries:
            after = entry.cursor.redis_id
            envelope = entry.envelope
            event_type = envelope["event_type"]
            if not saw_stream_open:
                if event_type != "stream.open":
                    raise StreamContractError("stream_projection_history_unavailable")
                saw_stream_open = True
            elif event_type == "message.delta":
                projector.push(envelope["payload"]["delta"])
            elif event_type in {"run.succeeded", "run.failed", "run.cancelled"}:
                terminal_event_id = str(envelope["event_id"])
            elif event_type == "stream.end":
                if envelope["payload"].get("terminal_event_id") != terminal_event_id:
                    raise StreamContractError("stream_end_without_observed_terminal")
                ended = True
            if after == through_redis_id:
                return projector, terminal_event_id, ended
        if after == previous_after:
            raise StreamContractError("stream_projection_history_unavailable")
    return projector, terminal_event_id, ended


@router.get("/chat/sessions/{session_id}/stream")
async def chat_session_stream(
    session_id: str,
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    principal: AuthPrincipal = Depends(require_principal),
) -> StreamingResponse:
    last_event_id = last_event_id if isinstance(last_event_id, str) else None
    connection_id = f"sse_{uuid.uuid4().hex}"
    authority = None
    lease = None
    async with transaction() as conn:
        initial_run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if initial_run is not None and initial_run.get("session_id") == session_id:
            try:
                authority = await get_stream_authority(
                    conn, tenant_id=principal.tenant_id, run_id=run_id
                )
                if authority is not None:
                    lease = await acquire_sse_authority_lease(
                        conn,
                        tenant_id=principal.tenant_id,
                        run_id=run_id,
                        api_instance_id=_SSE_API_INSTANCE_ID,
                        connection_id=connection_id,
                        lease_seconds=SSE_AUTHORITY_LEASE_SECONDS,
                    )
            except SseAuthorityConflictError as exc:
                raise _sse_conflict(str(exc)) from exc
    if initial_run is None or initial_run.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="run_not_found")
    if authority is None:
        if str(initial_run.get("status") or "") in runs_api.TERMINAL_RUN_STATUSES:
            raise _sse_conflict("sse_run_already_terminal")
        raise _sse_conflict("sse_stream_not_admitted")
    if lease is None:
        raise _sse_conflict("sse_authority_lease_unavailable")

    async def release_lease(*, reason: str) -> bool:
        try:
            async with transaction() as conn:
                return bool(
                    await close_sse_authority_lease(
                        conn, lease_id=lease.lease_id, reason=reason
                    )
                )
        except Exception:
            return False

    async def record_sse_exit(reason: str) -> None:
        if reason not in _SSE_EXIT_REASONS:
            reason = "transport_failure"
        lease_released = await release_lease(reason=reason)
        logger.info(
            "sse_stream_exit",
            extra={
                "reason": reason,
                "run_id_prefix": _safe_sse_correlation(run_id),
                "attempt_id_prefix": _safe_sse_correlation(authority.attempt_id),
                "stream_incarnation": authority.stream_incarnation,
                "lease_released": lease_released,
            },
        )

    runtime = getattr(request.app.state, "run_stream_runtime", None)
    if runtime is None:
        await record_sse_exit("stream_setup_failure")
        raise HTTPException(status_code=503, detail="sse_stream_unavailable")
    bridge = runtime.bridge
    channel = stream_live_channel(
        tenant_scope_value=authority.tenant_scope,
        run_id=run_id,
        stream_incarnation=authority.stream_incarnation,
    )
    subscription = None
    setup_gap_requested_event_id: str | None = None
    try:
        subscription = await runtime.hub.subscribe(channel)
        resume = await bridge.resolve_resume(
            tenant_scope_value=authority.tenant_scope,
            run_id=run_id,
            attempt_id=authority.attempt_id,
            current_stream_incarnation=authority.stream_incarnation,
            last_event_id=last_event_id,
        )
        if (
            resume.gap is not None
            and resume.gap.reason == "stream_missing"
            and str(initial_run.get("status") or "") in runs_api.TERMINAL_RUN_STATUSES
        ):
            await subscription.aclose()
            subscription = None
            activation = await recover_v4_missing_terminal_stream(
                runtime.successor_rebuilds,
                runtime.successor_activations,
                runtime.rebuild_transport,
                tenant_id=principal.tenant_id,
                run_id=run_id,
                attempt_id=authority.attempt_id,
                source_incarnation=authority.stream_incarnation,
                claim_ttl=timedelta(seconds=30),
            )
            if activation is None:
                raise StreamContractError("stream_successor_activation_unavailable")
            async with transaction() as conn:
                authority = await get_stream_authority(
                    conn, tenant_id=principal.tenant_id, run_id=run_id
                )
                if authority is None:
                    raise StreamContractError("stream_successor_authority_missing")
                lease = await acquire_sse_authority_lease(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=run_id,
                    api_instance_id=_SSE_API_INSTANCE_ID,
                    connection_id=connection_id,
                    lease_seconds=SSE_AUTHORITY_LEASE_SECONDS,
                )
            channel = stream_live_channel(
                tenant_scope_value=authority.tenant_scope,
                run_id=run_id,
                stream_incarnation=authority.stream_incarnation,
            )
            subscription = await runtime.hub.subscribe(channel)
            resume = await bridge.resolve_resume(
                tenant_scope_value=authority.tenant_scope,
                run_id=run_id,
                attempt_id=authority.attempt_id,
                current_stream_incarnation=authority.stream_incarnation,
                last_event_id=None,
            )
        answer_projector = PublicChatAnswerStreamProjector(initial_run)
        restored_terminal_event_id: str | None = None
        resume_already_ended = False
        replay_tail = resume.after_redis_id or "0-0"
        if resume.gap is None:
            bounds = await bridge.retained_bounds(
                tenant_scope_value=authority.tenant_scope,
                run_id=run_id,
                attempt_id=authority.attempt_id,
                stream_incarnation=authority.stream_incarnation,
            )
            if bounds is None:
                raise StreamContractError("stream_replay_bounds_unavailable")
            replay_tail = bounds[1].cursor.redis_id
            try:
                (
                    answer_projector,
                    restored_terminal_event_id,
                    resume_already_ended,
                ) = await _restore_chat_stream_projection(
                    bridge,
                    run=initial_run,
                    tenant_scope_value=authority.tenant_scope,
                    run_id=run_id,
                    attempt_id=authority.attempt_id,
                    stream_incarnation=authority.stream_incarnation,
                    through_redis_id=resume.after_redis_id or "0-0",
                )
            except StreamContractError as exc:
                if str(exc) != "stream_replay_continuity_unproven":
                    raise
                setup_gap_requested_event_id = resume.after_redis_id or "0-0"
    except StreamContractError as exc:
        cleanup_failed = False
        if subscription is not None:
            try:
                await subscription.aclose()
            except Exception:  # noqa: BLE001
                cleanup_failed = True
        await record_sse_exit(
            "stream_cleanup_failure" if cleanup_failed else "stream_contract_failure"
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (LiveSubscriptionClosed, StreamTransportUnavailable) as exc:
        cleanup_failed = False
        if subscription is not None:
            try:
                await subscription.aclose()
            except Exception:  # noqa: BLE001
                cleanup_failed = True
        await record_sse_exit(
            "stream_cleanup_failure" if cleanup_failed else "transport_failure"
        )
        raise HTTPException(status_code=503, detail="sse_stream_unavailable") from exc
    except Exception as exc:  # noqa: BLE001
        cleanup_failed = False
        if subscription is not None:
            try:
                await subscription.aclose()
            except Exception:  # noqa: BLE001
                cleanup_failed = True
        await record_sse_exit(
            "stream_cleanup_failure" if cleanup_failed else "stream_setup_failure"
        )
        raise HTTPException(status_code=503, detail="sse_stream_unavailable") from exc

    async def stream():
        nonlocal lease
        after = resume.after_redis_id or "0-0"
        exit_reason = "transport_failure"
        exit_recorded = False

        async def record_exit() -> None:
            nonlocal exit_recorded
            if exit_recorded:
                return
            exit_recorded = True
            await record_sse_exit(exit_reason)

        async def refresh_lease() -> bool:
            nonlocal lease
            now = datetime.now(timezone.utc)
            # A committed epoch change fences renewal. This issued lease remains
            # authoritative only until its authority-clock deadline (<=15s).
            if lease.allows_frame(now=now):
                return True
            try:
                async with transaction() as conn:
                    run = await repositories.get_authorized_run(
                        conn,
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        run_id=run_id,
                    )
                    if run is None or run.get("session_id") != session_id:
                        return False
                    lease = await acquire_sse_authority_lease(
                        conn,
                        tenant_id=principal.tenant_id,
                        run_id=run_id,
                        api_instance_id=_SSE_API_INSTANCE_ID,
                        connection_id=connection_id,
                        lease_seconds=SSE_AUTHORITY_LEASE_SECONDS,
                    )
            except (SseAuthorityConflictError, ValueError):
                return False
            return lease.allows_frame(now=datetime.now(timezone.utc))

        async def authorize_frame() -> bool:
            nonlocal exit_reason
            if await refresh_lease():
                return True
            exit_reason = "transport_failure"
            return False

        def project_entry(entry: V4StreamEntry) -> tuple[str | None, bool]:
            nonlocal restored_terminal_event_id
            envelope = project_public_envelope_v4(entry.envelope)
            if envelope is None:
                raise StreamContractError("stream_public_event_unmapped")
            event_type = str(envelope["event_type"])
            if event_type in {"run.succeeded", "run.failed", "run.cancelled"}:
                restored_terminal_event_id = str(envelope["event_id"])
            ended = event_type == "stream.end"
            if ended and envelope["payload"].get(
                "terminal_event_id"
            ) != restored_terminal_event_id:
                raise StreamContractError("stream_end_without_observed_terminal")
            return _sse(event_type, envelope, entry.cursor.event_id), ended

        async def gap_frame(
            *,
            reason: str,
            requested_event_id: str | None,
            requested_stream_incarnation: int | None,
        ) -> str:
            gap_envelope, gap_cursor = await bridge.build_gap(
                event_id=f"evt4_gap_{connection_id}",
                tenant_scope_value=authority.tenant_scope,
                run_id=run_id,
                attempt_id=authority.attempt_id,
                requested_event_id=requested_event_id,
                requested_stream_incarnation=requested_stream_incarnation,
                current_stream_incarnation=authority.stream_incarnation,
                reason=reason,
            )
            public_gap = project_public_envelope_v4(gap_envelope)
            if public_gap is None:
                raise StreamContractError("stream_gap_public_event_unmapped")
            return _sse("stream.gap", public_gap, gap_cursor)

        try:
            if resume.gap is not None or setup_gap_requested_event_id is not None:
                exit_reason = "stream_contract_failure"
                if resume.gap is not None:
                    reason = resume.gap.reason
                    requested_event_id = resume.gap.requested_event_id
                    requested_incarnation = resume.gap.requested_stream_incarnation
                else:
                    reason = "stream_continuity_unproven"
                    requested_event_id = setup_gap_requested_event_id
                    requested_incarnation = authority.stream_incarnation
                frame = await gap_frame(
                    reason=reason,
                    requested_event_id=requested_event_id,
                    requested_stream_incarnation=requested_incarnation,
                )
                if not await authorize_frame():
                    return
                yield frame
                return
            if resume_already_ended:
                exit_reason = "terminal_completed"
                return
            if not await authorize_frame():
                return
            while after != replay_tail:
                previous_after = after
                try:
                    entries = await bridge.replay_page(
                        tenant_scope_value=authority.tenant_scope,
                        run_id=run_id,
                        attempt_id=authority.attempt_id,
                        stream_incarnation=authority.stream_incarnation,
                        after_redis_id=after,
                        through_redis_id=replay_tail,
                    )
                except StreamContractError as exc:
                    if str(exc) != "stream_replay_continuity_unproven":
                        raise
                    exit_reason = "stream_contract_failure"
                    frame = await gap_frame(
                        reason="stream_continuity_unproven",
                        requested_event_id=after,
                        requested_stream_incarnation=authority.stream_incarnation,
                    )
                    if not await authorize_frame():
                        return
                    yield frame
                    return
                if not entries:
                    raise StreamContractError("stream_replay_history_unavailable")
                for entry in entries:
                    after = entry.cursor.redis_id
                    frame, ended = project_entry(entry)
                    if frame is not None:
                        if not await authorize_frame():
                            return
                        yield frame
                    if ended:
                        exit_reason = "terminal_completed"
                        return
                if after == previous_after:
                    raise StreamContractError("stream_replay_history_unavailable")
            while True:
                if not await authorize_frame():
                    return
                try:
                    publication = await subscription.next(timeout_seconds=5.0)
                except TimeoutError:
                    if not await authorize_frame():
                        return
                    yield ": heartbeat\n\n"
                    continue
                except LiveSubscriptionClosed:
                    exit_reason = "live_source_closed"
                    return
                if publication.channel != channel:
                    raise StreamContractError("stream_live_channel_mismatch")
                if not live_redis_id_is_after(publication.redis_id, after):
                    continue
                entry = bridge.decode_live_publication(
                    redis_id=publication.redis_id,
                    envelope_json=publication.envelope_json,
                    tenant_scope_value=authority.tenant_scope,
                    run_id=run_id,
                    attempt_id=authority.attempt_id,
                    stream_incarnation=authority.stream_incarnation,
                )
                after = entry.cursor.redis_id
                frame, ended = project_entry(entry)
                if frame is not None:
                    if not await authorize_frame():
                        return
                    yield frame
                if ended:
                    exit_reason = "terminal_completed"
                    return
        except asyncio.CancelledError:
            exit_reason = "client_disconnected"
            raise
        except StreamTransportUnavailable:
            exit_reason = "transport_failure"
            return
        except StreamContractError:
            exit_reason = "stream_contract_failure"
            return
        finally:
            cleanup_failed = False
            try:
                await subscription.aclose()
            except Exception:  # noqa: BLE001
                cleanup_failed = True
            if cleanup_failed:
                exit_reason = "stream_cleanup_failure"
            await record_exit()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
