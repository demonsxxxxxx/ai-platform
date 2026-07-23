import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.auth import AuthPrincipal, is_ai_admin, require_principal, sign_principal_session, verify_principal_session
from app.db import transaction
from app.model_catalog import build_model_catalog
from app.models import LoginRequest, SessionRenameRequest
from app import repositories, session_actions
from app.routes.auth import _login_principal
from app.routes.files import upload_file as upload_platform_file
from app.projection_redaction import (
    capability_id_from_skill,
    public_agent_id_for_projection,
    public_skill_display_label,
)
from app.control_plane_contracts import EVENT_ENVELOPE_SCHEMA_VERSION, sanitize_public_text, standard_trace_id
from app.routes.runs import artifact_card, event_visible_to_principal, run_event_response
from app.run_projection import public_terminal_detail
from app.settings import get_settings
from app.tool_permission_projection import tool_permission_public_event_payload

router = APIRouter()


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


def _run_answer(run: dict[str, Any]) -> str:
    result = run.get("result_json") or {}
    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, str) and message.strip():
            return message
    if run.get("error_message"):
        return str(run["error_message"])
    return ""


def _public_run_answer(run: dict[str, Any], principal: AuthPrincipal) -> str:
    answer = _run_answer(run)
    if is_ai_admin(principal):
        return answer
    return sanitize_public_text(answer)


def _public_terminal_text(run: dict[str, Any], principal: AuthPrincipal) -> str:
    status = _platform_status(str(run.get("status") or ""))
    answer = _public_run_answer(run, principal)
    if answer:
        return answer
    if status == "succeeded":
        return "任务完成"
    if status == "failed":
        detail = public_terminal_detail(status, run.get("error_code"))
        return detail["message"] if detail is not None else ""
    if status == "cancelled":
        detail = public_terminal_detail(status)
        return detail["message"] if detail is not None else ""
    return ""


def _chat_identifier_token_pattern(identifier: str) -> re.Pattern[str]:
    """Match an identifier only outside Unicode word, dash, dot, or colon tokens."""
    token_character = r"[\w.:\-]"
    return re.compile(
        rf"(?<!{token_character}){re.escape(identifier)}(?!{token_character})"
    )


def _sanitize_chat_answer_text(run: dict[str, Any], value: object) -> str:
    """Remove public-text hazards and publicize identifiers owned by the run."""
    content = sanitize_public_text(value)
    if not content:
        return ""
    raw_skill_id = str(run.get("skill_id") or "")
    raw_agent_id = str(run.get("agent_id") or "")
    skill_capability_id = capability_id_from_skill(raw_skill_id)
    agent_capability_id = capability_id_from_skill(None, raw_agent_id)
    run_capability_id = skill_capability_id or agent_capability_id
    public_agent_id = public_agent_id_for_projection(raw_agent_id, raw_skill_id)
    identifiers = (
        (raw_skill_id, skill_capability_id),
        (raw_agent_id, agent_capability_id),
    )
    matched_identifiers = []
    for identifier, identifier_capability_id in identifiers:
        if not identifier:
            continue
        token_pattern = _chat_identifier_token_pattern(identifier)
        if token_pattern.search(content):
            matched_identifiers.append(
                (identifier, identifier_capability_id, token_pattern)
            )
    if (
        matched_identifiers
        and raw_skill_id
        and raw_agent_id
        and skill_capability_id != agent_capability_id
    ):
        return ""
    for identifier, identifier_capability_id, token_pattern in matched_identifiers:
        if (
            not run_capability_id
            or identifier_capability_id != run_capability_id
            or not public_agent_id
        ):
            return ""
        if public_agent_id != identifier:
            content = token_pattern.sub(public_agent_id, content)
    content = sanitize_public_text(content)
    return content if content.strip() else ""


def _terminal_final_payload(
    run: dict[str, Any],
) -> tuple[str, dict[str, str], str] | None:
    """Return the safe final user-facing payload that precedes terminal replay."""
    status = _platform_status(str(run.get("status") or ""))
    if status == "succeeded":
        canonical_answer = _sanitize_chat_answer_text(run, _run_answer(run)) or "任务完成"
        return (
            "message:chunk",
            {
                "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
                "projection_kind": "assistant_final",
                "content": canonical_answer,
            },
            "info",
        )
    if status == "failed":
        detail = public_terminal_detail(status, run.get("error_code"))
        if detail is None:
            return None
        return (
            "final_detail",
            {
                "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
                **detail,
            },
            "error",
        )
    if status == "cancelled":
        detail = public_terminal_detail(status)
        if detail is None:
            return None
        return (
            "final_detail",
            {
                "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
                **detail,
            },
            "info",
        )
    return None


@dataclass(frozen=True)
class _CompatibilityWireEvent:
    """One ordered, public compatibility event for both live and history adapters."""

    id: str
    stream_event_type: str
    stream_data: dict[str, object]
    history_event: dict[str, object]
    terminal: bool = False


CHAT_PUBLIC_PROJECTION_VERSION = "ai-platform.chat-public-projection.v1"
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
    "run_multi_agent_child_created": _ChatPublicRunEventProjection(
        "run_child_created", "agent", "已安排协同任务", "active"
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
        "artifact_created", "artifact", "已生成结果文件，正在完成可用性检查", "completed"
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
) -> _StrictChatEventProduct | None:
    """Retain only exact answer deltas or reconstructed permission cards for Chat.

    Generic run events remain owned by ``run_event_response``.  This seam is
    deliberately narrower: it reads raw persisted data only to construct two
    pre-existing typed Chat products, never to relay arbitrary event text or
    payload fields.  Both live SSE and exact-run history use it through the
    shared compatibility event builder.
    """
    raw_event_type = str(event.get("event_type") or "")
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
    generic_envelope = run_event_response(run_id, event, principal=principal)
    raw_payload = event.get("payload_json")
    if not isinstance(raw_payload, dict):
        return None
    if raw_event_type == "assistant_delta":
        if (
            event.get("stage") != "answer"
            or event.get("visible_to_user") is not True
            or event.get("severity") != "info"
            or set(raw_payload) != {"delta", "source", "visible_to_user", "severity"}
            or raw_payload.get("source") != CHAT_ASSISTANT_DELTA_SOURCE
            or raw_payload.get("visible_to_user") is not True
            or raw_payload.get("severity") != "info"
            or not isinstance(raw_payload.get("delta"), str)
        ):
            return None
        content = _sanitize_chat_answer_text(run, raw_payload["delta"])
        if not content:
            return None
        return _StrictChatEventProduct(
            kind="assistant_delta",
            generic_envelope=generic_envelope,
            payload={
                "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
                "projection_kind": "assistant_delta",
                "event_id": str(generic_envelope["event_id"]),
                "sequence": int(generic_envelope["sequence"]),
                "run_id": run_id,
                "content": content,
            },
        )
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
    typed_product = _strict_typed_chat_event_product(run, event, principal)
    projected = (
        typed_product.generic_envelope
        if typed_product is not None
        else run_event_response(run_id, event, principal=principal)
    )
    if projected.get("event_type") == "heartbeat":
        presentation = CHAT_PUBLIC_RUN_EVENT_PROJECTIONS["heartbeat"]
    severity = str(projected.get("severity") or "info")
    if presentation.progress_kind == "failed":
        severity = "error"
    elif presentation.progress_kind == "blocked" and severity == "info":
        severity = "warning"
    elif severity not in {"info", "warning", "error"}:
        severity = "info"
    payload = (
        typed_product.payload
        if typed_product is not None and typed_product.kind == "tool_permission_card"
        else _chat_projection_payload(projected)
    )
    message = presentation.message
    stage = presentation.stage
    if raw_event_type == "error":
        terminal_detail = public_terminal_detail("failed", event.get("error_code"))
        if terminal_detail is not None:
            message = terminal_detail["message"]
            payload = {"detail_code": terminal_detail["detail_code"]}
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
) -> dict[str, object] | None:
    """Return a sanitized delta frame without carrying any executor payload."""
    typed_product = _strict_typed_chat_event_product(run, event, principal)
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
) -> list[_CompatibilityWireEvent]:
    """Build the sole public terminal wire, ordered for live and history replay."""
    run_id = str(run["id"])
    trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
    compatibility_events: list[_CompatibilityWireEvent] = []
    status = _platform_status(str(run.get("status") or ""))

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

    for position, event in sorted(
        enumerate(run_events),
        key=lambda item: _event_sequence_sort_key(item[1], item[0]),
    ):
        raw_event_type = str(event.get("event_type") or "")
        if raw_event_type in CHAT_STREAM_TERMINAL_EVENT_TYPES:
            continue
        if (
            not _chat_event_marked_visible(event)
            or not event_visible_to_principal(event, principal)
        ):
            continue
        if raw_event_type == "assistant_delta":
            # Successful terminal history converges to the canonical final
            # snapshot. Failed/cancelled runs retain only canonical, sanitized
            # answer deltas so useful partial output survives reconnect/reload.
            if status == "succeeded":
                continue
            delta = _assistant_delta_projection(run, event, principal)
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
        payload = envelope["payload"] if isinstance(envelope.get("payload"), dict) else {}
        history_data = {
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
            history_data["status"] = envelope["stage"]
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

    final_payload = _terminal_final_payload(run)
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

    if status in {"succeeded", "failed", "cancelled"}:
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
    return compatibility_events


def _public_error_text(run: dict[str, Any], _principal: AuthPrincipal) -> str:
    status = _platform_status(str(run.get("status") or ""))
    if status != "failed":
        return ""
    detail = public_terminal_detail(status, run.get("error_code"))
    return detail["message"] if detail is not None else ""


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
async def available_models() -> dict[str, object]:
    return build_model_catalog(get_settings())


@router.get("/agent/models/")
async def model_configs() -> dict[str, object]:
    catalog = build_model_catalog(get_settings())
    models = [{**model, "enabled": True, "order": index} for index, model in enumerate(catalog["models"], start=1)]
    return {**catalog, "models": models}


@router.get("/settings")
@router.get("/settings/")
async def settings() -> dict[str, object]:
    return {"settings": {}}


@router.get("/version")
async def version() -> dict[str, object]:
    return {"version": "ai-platform-poc"}


@router.get("/projects")
@router.get("/projects/")
async def projects() -> list[object]:
    return []


@router.get("/notifications/active")
async def active_notifications() -> dict[str, object]:
    return {"notifications": []}


@router.get("/upload/config")
async def upload_config() -> dict[str, object]:
    max_file_size = 52428800
    return {
        "enabled": True,
        "provider": "ai-platform",
        "uploadLimits": {
            "image": max_file_size,
            "video": max_file_size,
            "audio": max_file_size,
            "document": max_file_size,
            "maxFiles": 10,
        },
        "max_file_size": max_file_size,
        "allowed_extensions": ["docx", "txt", "pdf"],
        "categories": ["document"],
    }


@router.post("/upload/check")
async def upload_check() -> dict[str, object]:
    return {"exists": False}


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
    filename = file.filename or uploaded.file_id
    mime_type = file.content_type or "application/octet-stream"
    return {
        "key": uploaded.file_id,
        "file_id": uploaded.file_id,
        "url": f"/api/ai/files/{uploaded.file_id}",
        "name": filename,
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
async def generate_title(session_id: str, message: str = "", lang: str = "en") -> dict[str, str]:
    title = (message or "").strip().replace("\n", " ")[:32] or "新会话"
    return {"session_id": session_id, "title": title}


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


@router.get("/chat/sessions/{session_id}/stream")
async def chat_session_stream(
    session_id: str,
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> StreamingResponse:
    async with transaction() as conn:
        initial_run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
    if initial_run is None or initial_run.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="run_not_found")

    async def stream():
        metadata_emitted = False
        last_status = ""
        seen_event_ids: set[str] = set()
        max_heartbeats = max(int(get_settings().run_event_stream_max_heartbeats), 1)
        for _ in range(max_heartbeats):
            async with transaction() as conn:
                run = await repositories.get_authorized_run(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    run_id=run_id,
                )
                if run is None or run.get("session_id") != session_id:
                    run_events = []
                    artifacts = []
                else:
                    run_events = await repositories.list_run_events(
                        conn,
                        tenant_id=principal.tenant_id,
                        run_id=run_id,
                    )
                    artifacts = await repositories.list_run_artifacts(
                        conn,
                        tenant_id=principal.tenant_id,
                        run_id=run_id,
                    )
            if run is None or run.get("session_id") != session_id:
                yield _sse("error", {"error": "run_not_found"})
                yield _sse("done", {})
                return
            if not metadata_emitted:
                yield _sse("metadata", {"session_id": session_id, "run_id": run_id})
                metadata_emitted = True
            status = _platform_status(str(run["status"]))
            try:
                compatibility_events = _compatibility_events_for_run(
                    run,
                    run_events,
                    artifacts,
                    principal,
                )
            except HTTPException as exc:
                yield _sse("error", {"error": str(exc.detail)})
                yield _sse("done", {"status": "error"})
                return
            for record in compatibility_events:
                if record.id in seen_event_ids:
                    continue
                seen_event_ids.add(record.id)
                yield _sse(
                    record.stream_event_type,
                    record.stream_data,
                    event_id=record.id,
                )
                if record.terminal:
                    return
            if status != last_status and status in {"queued", "running"}:
                yield _sse("queue_update", {"status": "processing" if status == "running" else "queued"})
                last_status = status
            await asyncio.sleep(1)
        yield _sse("error", {"error": "stream_timeout"})
        yield _sse("done", {"status": "timeout"}, event_id=f"{run_id}:done")

    return StreamingResponse(stream(), media_type="text/event-stream")
