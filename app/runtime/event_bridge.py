import re

from app.control_plane_contracts import sanitize_public_payload
from app.public_execution import (
    PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
    PUBLIC_EXECUTION_EVENT_TYPES,
    validate_public_agent_progress_payload,
    validate_versioned_public_execution_step_payload,
)
from app.runtime.kernel_contracts import AgentEvent

_V4_EVENT_STAGES = {
    "message.started": "message",
    "message.delta": "message",
    "message.completed": "message",
    "thinking.started": "message",
    "thinking.completed": "message",
    "model.completed": "message",
    "tool.started": "tool",
    "tool.completed": "tool",
    "tool.failed": "tool",
    "tool.denied": "tool",
    "subagent.started": "subagent",
    "subagent.progress": "subagent",
    "subagent.completed": "subagent",
    "subagent.failed": "subagent",
    "subagent.cancelled": "subagent",
    "artifact.created": "artifact",
    "artifact.ready": "artifact",
    "artifact.failed": "artifact",
    "policy.checking": "tool_policy",
    "policy.allowed": "tool_policy",
    "policy.denied": "tool_policy",
    "run.cancel_requested": "control",
    "run.succeeded": "runtime",
    "run.cancelled": "control",
    "run.failed": "runtime",
}

EVENT_STAGE_MAP = {
    "run_queued": "queue",
    "run_started": "runtime",
    "runtime_container_started": "runtime",
    "sandbox_executor_readiness_failed": "runtime",
    "assistant_delta": "message",
    "tool_call_started": "tool",
    "tool_call_delta": "tool",
    "tool_call_completed": "tool",
    "capability_invoking": "capability",
    "capability_completed": "capability",
    "capability_failed": "capability",
    "tool_permission_requested": "tool_policy",
    "tool_permission_authorized": "tool_policy",
    "tool_permission_denied": "tool_policy",
    "browser_snapshot": "browser",
    "workspace_file_changed": "workspace",
    "artifact_created": "artifact",
    "checkpoint_created": "checkpoint",
    "subagent_started": "subagent",
    "subagent_completed": "subagent",
    "subagent_failed": "subagent",
    "agent_step_started": "agent",
    "agent_step_reused": "agent",
    "agent_step_completed": "agent",
    "agent_step_blocked": "agent",
    "agent_step_failed": "agent",
    "run_failed": "runtime",
    "run_completed": "runtime",
    "run_cancelled": "control",
}

_V4_MESSAGE_EVENT_TYPES = frozenset(
    {
        "message.started",
        "message.delta",
        "message.completed",
        "thinking.started",
        "thinking.completed",
        "model.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "tool.denied",
        "subagent.started",
        "subagent.progress",
        "subagent.completed",
        "subagent.failed",
        "subagent.cancelled",
    }
)
_V4_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_V4_SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
_V4_EVENT_ID_PATTERN = _V4_SAFE_REF_PATTERN

_RAW_TOOL_PRIVATE_FIELDS = frozenset(
    {
        "command",
        "args",
        "arguments",
        "result",
        "output",
        "tool_input",
        "tool_output",
        "private_payload",
        "executor_private_payload",
    }
)


def _private_executor_event() -> dict[str, object]:
    return {
        "event_type": "executor_private_event",
        "stage": "runtime",
        "message": "",
        "payload": {"visible_to_user": False, "admin_only": True},
    }


def _v4_envelope_identity_is_valid(event: AgentEvent) -> bool:
    if not isinstance(event.run_id, str) or _V4_RUN_ID_PATTERN.fullmatch(event.run_id) is None:
        return False
    if not isinstance(event.event_id, str) or _V4_EVENT_ID_PATTERN.fullmatch(event.event_id) is None:
        return False
    if event.type in _V4_MESSAGE_EVENT_TYPES:
        if not isinstance(event.message_id, str):
            return False
    elif event.message_id is not None and not isinstance(event.message_id, str):
        return False
    if event.message_id is not None and _V4_SAFE_REF_PATTERN.fullmatch(event.message_id) is None:
        return False
    if event.causation_event_id is not None and _V4_SAFE_REF_PATTERN.fullmatch(event.causation_event_id) is None:
        return False
    return True


def _v4_public_candidate(event: AgentEvent) -> dict[str, object]:
    return {
        "event_type": event.type,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "message_id": event.message_id,
        "causation_event_id": event.causation_event_id,
        "message": event.message,
        "payload": event.payload,
    }


def _without_none_public_values(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_none_public_values(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [
            _without_none_public_values(item)
            for item in value
            if item is not None
        ]
    if isinstance(value, tuple):
        return tuple(
            _without_none_public_values(item)
            for item in value
            if item is not None
        )
    return value


def _public_strings_are_identity_safe(value: object) -> bool:
    """Reject public candidates when strict text redaction would change them."""

    return sanitize_public_payload(value) == _without_none_public_values(value)


def _v4_agent_event_to_executor_event(event: AgentEvent) -> dict[str, object]:
    """Carry a validated v4 candidate without adding legacy visibility fields."""

    if event.admin_only or event.message:
        return _private_executor_event()
    if not _v4_envelope_identity_is_valid(event):
        return _private_executor_event()
    if not _public_strings_are_identity_safe(_v4_public_candidate(event)):
        return _private_executor_event()
    stage = _V4_EVENT_STAGES.get(event.type)
    if stage is None or not event.run_id or not event.event_id:
        return _private_executor_event()
    try:
        from app.execution.api import ClaudeAgentEventCandidate

        candidate = ClaudeAgentEventCandidate(
            run_id=event.run_id,
            event_id=event.event_id,
            event_type=event.type,
            message_id=event.message_id,
            causation_event_id=event.causation_event_id,
            payload=dict(event.payload),
            payload_sanitizer=sanitize_public_payload,
        )
    except (TypeError, ValueError):
        return _private_executor_event()
    return {
        "event_type": candidate.event_type,
        "stage": stage,
        "message": "",
        "payload": candidate.payload,
        "event_id": candidate.event_id,
        "run_id": candidate.run_id,
        "message_id": candidate.message_id,
        "causation_event_id": candidate.causation_event_id,
    }


def agent_event_to_executor_event(event: AgentEvent) -> dict[str, object]:
    if event.type in _V4_EVENT_STAGES:
        return _v4_agent_event_to_executor_event(event)
    if event.type == PUBLIC_AGENT_PROGRESS_EVENT_TYPE:
        payload = validate_public_agent_progress_payload(event.payload)
        if payload is None or event.admin_only or event.message:
            return _private_executor_event()
        return {
            "event_type": PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
            "stage": "agent_progress",
            "message": payload["message"],
            "payload": payload,
        }
    if event.type in PUBLIC_EXECUTION_EVENT_TYPES:
        payload = validate_versioned_public_execution_step_payload(
            event.payload,
            expected_kind=event.type,
        )
        if payload is None or event.admin_only or event.message:
            return _private_executor_event()
        return {
            "event_type": event.type,
            "stage": str(payload["stage"]),
            "message": "",
            "payload": payload,
        }
    if event.type.startswith("tool_call") and (
        _RAW_TOOL_PRIVATE_FIELDS & set(event.payload)
    ):
        return _private_executor_event()
    stage = EVENT_STAGE_MAP.get(event.type)
    if stage is None:
        return _private_executor_event()

    payload = dict(event.payload)
    if event.admin_only or event.type == "sandbox_executor_readiness_failed":
        payload["visible_to_user"] = False
        payload["admin_only"] = True
    else:
        payload.setdefault("visible_to_user", True)

    return {
        "event_type": event.type,
        "stage": stage,
        "message": event.message,
        "payload": payload,
    }
