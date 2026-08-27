"""Strict Claude SDK Agent-kernel event projection for the v4 event contract.

This module is deliberately independent of Redis, PostgreSQL, and transport
serialization.  It accepts SDK-shaped values, keeps SDK identities private,
and returns only validated application-event candidates.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from typing import Any, Callable

from app.streaming.events import PUBLIC_APPLICATION_EVENT_TYPES_V4

_APPLICATION_EVENT_TYPES = PUBLIC_APPLICATION_EVENT_TYPES_V4
_THINKING_SUMMARY_EVENT_TYPE = "claude_sdk_thinking_summary"
_TOOL_CATEGORIES = frozenset({"skill", "mcp", "read", "write", "edit", "search", "execute"})
_BUILTIN_TOOL_CATEGORIES = {
    "Read": "read",
    "Glob": "search",
    "Grep": "search",
    "LS": "read",
    "Bash": "execute",
    "Write": "write",
    "Edit": "edit",
    "NotebookEdit": "edit",
    "Agent": "execute",
    "WebFetch": "search",
    "WebSearch": "search",
    "Skill": "skill",
}
_FAILURE_CATEGORIES = frozenset(
    {"invalid_input", "not_found", "permission_denied", "timeout", "unavailable", "execution_failed"}
)
_PRIVATE_KEYS = frozenset(
    {
        "id",
        "uuid",
        "session_id",
        "task_id",
        "tool_use_id",
        "attempt_id",
        "tool_input",
        "tool_output",
        "input",
        "output",
        "result",
        "summary",
        "description",
        "command",
        "path",
        "error",
        "exception",
        "reason",
        "raw",
    }
)
_MAX_TEXT = 262_144
_MAX_DELTA = 8_192
_MAX_DISPLAY = 128
_MAX_FILENAME = 255
_MAX_SUMMARY = 512
_MAX_RESULT_SUMMARY = 2_048
_MAX_MEDIA_TYPE = 128
_MAX_CODE = 128
_MAX_DEFAULT_MESSAGE = 1_024
_MAX_DETAIL = 2_048
_MAX_DURATION = 86_400_000
_MAX_TURNS = 10_000
_MAX_SIZE_BYTES = 1_099_511_627_776
_MAX_REFS = 32
_ALLOWED_CATEGORIES = frozenset({"skill", "mcp", "read", "write", "edit", "search", "execute"})
_ALLOWED_STOP_CATEGORIES = frozenset({"completed", "max_turns", "cancelled", "failed", "unknown"})
_ALLOWED_FAILURE_CATEGORIES = frozenset({"invalid_input", "not_found", "permission_denied", "timeout", "unavailable", "execution_failed"})
_ALLOWED_TASK_REASON_CODES = frozenset({"user_cancelled", "run_cancelled", "timeout"})


_SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
_EVENT_ID_PATTERN = _SAFE_REF_PATTERN
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _assert_safe_ref(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_REF_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is not a safe reference")
    return value


def _assert_run_id(value: object) -> str:
    if not isinstance(value, str) or _RUN_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("run_id is not a v4 RunId")
    return value


def _assert_attempt_id(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise ValueError("attempt_id is not a v4 AttemptId")
    return value


def _assert_event_id(value: object) -> str:
    if not isinstance(value, str) or _EVENT_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("event_id is not a v4 EventId")
    return value


def _opaque(prefix: str, run_id: str, kind: str, identity: str) -> str:
    digest = hashlib.sha256(f"{run_id}\x00{kind}\x00{identity}".encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _bounded_int(value: object, *, maximum: int, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, min(value, maximum))


def _safe_text(
    value: object,
    *,
    maximum: int,
    sanitizer: Callable[[object], object],
) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    sanitized = sanitizer(value)
    if not isinstance(sanitized, str) or sanitized != value:
        return None
    if len(sanitized) > maximum:
        return None
    return sanitized


def _safe_display(
    value: object,
    *,
    sanitizer: Callable[[object], object],
) -> str | None:
    text = _safe_text(value, maximum=_MAX_DISPLAY, sanitizer=sanitizer)
    return text.strip() if text and text.strip() else None


def _safe_filename(
    value: object,
    *,
    sanitizer: Callable[[object], object],
) -> str | None:
    text = _safe_text(value, maximum=_MAX_FILENAME, sanitizer=sanitizer)
    if text is None or any(ord(char) < 32 or ord(char) == 127 or char in "/\\\\" for char in text):
        return None
    return text


def _safe_private_identity(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    return value


def _tool_category(name: str, identity: str | None = None) -> str | None:
    if identity and identity.startswith("mcp__"):
        return "mcp"
    return _BUILTIN_TOOL_CATEGORIES.get(name)


def _failure_category(value: object) -> str:
    text = str(value or "").lower()
    if "permission" in text or "denied" in text or "unauthor" in text:
        return "permission_denied"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "not found" in text or "missing" in text:
        return "not_found"
    if "invalid" in text:
        return "invalid_input"
    if "unavailable" in text or "network" in text:
        return "unavailable"
    return "execution_failed"


def _stop_category(result: object, *, sealed: bool = False) -> str:
    if sealed:
        return "cancelled"
    reason = str(getattr(result, "terminal_reason", None) or getattr(result, "stop_reason", None) or "").lower()
    if "cancel" in reason or "abort" in reason:
        return "cancelled"
    if getattr(result, "is_error", False):
        return "failed"
    if reason in {"end_turn", "completed", "success"} or not reason:
        return "completed"
    if "turn" in reason or "max" in reason:
        return "max_turns"
    return "unknown"


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


@dataclass(frozen=True)
class ClaudeAgentEventCandidate:
    """One strict v4 application event before durable envelope assignment."""

    run_id: str
    event_id: str
    event_type: str
    message_id: str | None
    causation_event_id: str | None
    payload: dict[str, object]
    payload_sanitizer: InitVar[Callable[[object], object]]

    def __post_init__(self, payload_sanitizer: Callable[[object], object]) -> None:
        _assert_run_id(self.run_id)
        _assert_event_id(self.event_id)
        if self.message_id is not None:
            _assert_safe_ref(self.message_id, "message_id")
        if self.causation_event_id is not None:
            _assert_safe_ref(self.causation_event_id, "causation_event_id")
        public_candidate = {
            "event_type": self.event_type,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "message_id": self.message_id,
            "causation_event_id": self.causation_event_id,
            "payload": self.payload,
        }
        if payload_sanitizer(public_candidate) != _without_none_public_values(public_candidate):
            raise ValueError("public event candidate contains private text")
        if self.event_type not in _APPLICATION_EVENT_TYPES:
            raise ValueError("unsupported Claude application event")
        _validate_payload(self.event_type, self.payload)

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "message_id": self.message_id,
            "causation_event_id": self.causation_event_id,
            "payload": dict(self.payload),
        }

    def as_agent_event_fields(self) -> dict[str, object]:
        return {
            "type": self.event_type,
            "message": "",
            "payload": dict(self.payload),
            "event_id": self.event_id,
            "run_id": self.run_id,
            "message_id": self.message_id,
            "causation_event_id": self.causation_event_id,
        }


@dataclass(frozen=True)
class ClaudeSdkThinkingSummaryCandidate:
    """One private executor fact awaiting server-owned public projection."""

    run_id: str
    event_id: str
    message_id: str
    summary: str
    sanitizer: InitVar[Callable[[object], str]]

    def __post_init__(self, sanitizer: Callable[[object], str]) -> None:
        _assert_run_id(self.run_id)
        _assert_event_id(self.event_id)
        _assert_safe_ref(self.message_id, "message_id")
        if not self.summary or len(self.summary) > _MAX_TEXT:
            raise ValueError("invalid summarized thinking bound")
        if sanitizer(self.summary) != self.summary:
            raise ValueError("summarized thinking is not sanitized")

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "event_id": self.event_id,
            "event_type": _THINKING_SUMMARY_EVENT_TYPE,
            "message_id": self.message_id,
            "summary": self.summary,
        }

    def as_agent_event_fields(self) -> dict[str, object]:
        return {
            "type": _THINKING_SUMMARY_EVENT_TYPE,
            "message": "",
            "payload": {"summary": self.summary},
            "event_id": self.event_id,
            "run_id": self.run_id,
            "message_id": self.message_id,
            "causation_event_id": None,
            "admin_only": True,
        }


def _validate_payload(event_type: str, payload: Mapping[str, object]) -> None:
    if not isinstance(payload, dict) or len(payload) > 64:
        raise ValueError("event payload must be an object")
    required: dict[str, set[str]] = {
        "message.started": set(),
        "message.delta": {"delta"},
        "message.completed": {"content"},
        "thinking.started": {"thinking_id"},
        "thinking.delta": {"thinking_id", "delta"},
        "thinking.completed": {"thinking_id"},
        "model.completed": {"duration_ms", "turn_count", "stop_category"},
        "tool.started": {"operation_id", "category", "display_name"},
        "tool.completed": {"operation_id", "category", "display_name", "duration_ms"},
        "tool.failed": {"operation_id", "category", "display_name", "duration_ms", "failure_category"},
        "tool.denied": {"operation_id", "category", "display_name", "denial_code"},
        "subagent.started": {"subagent_id", "display_name"},
        "subagent.progress": {"subagent_id", "display_name", "duration_ms", "current_category"},
        "subagent.completed": {"subagent_id", "display_name", "duration_ms"},
        "subagent.failed": {"subagent_id", "display_name", "duration_ms", "failure_category"},
        "subagent.cancelled": {"subagent_id", "display_name", "duration_ms", "reason_code"},
        "artifact.created": {"artifact_id", "filename", "media_type", "size_bytes", "status"},
        "artifact.ready": {"artifact_id", "filename", "media_type", "size_bytes", "status"},
        "artifact.failed": {"artifact_id", "status", "failure_category"},
        "policy.checking": {"decision_id", "category", "display_name"},
        "policy.allowed": {"decision_id", "category", "display_name", "decision_code"},
        "policy.denied": {"decision_id", "category", "display_name", "decision_code"},
        "run.cancel_requested": {"source"},
        "run.succeeded": {"terminal_event_id", "hydrate_required"},
        "run.cancelled": {"terminal_event_id", "hydrate_required", "reason_code"},
        "run.failed": {"terminal_event_id", "hydrate_required", "projection_version", "code", "default_message", "detail"},
    }
    optional = {
        "tool.started": {"input_summary", "evidence_refs"},
        "tool.completed": {"result_summary", "evidence_refs", "artifact_refs"},
        "tool.failed": {"evidence_refs"},
        "subagent.progress": {"progress_percent"},
        "artifact.created": {"evidence_ref"},
        "artifact.ready": {"evidence_ref"},
        "artifact.failed": {"filename", "media_type"},
    }
    expected = required[event_type]
    keys = set(payload)
    if not expected <= keys or not keys <= expected | optional.get(event_type, set()):
        raise ValueError("event payload fields do not match schema")
    if any(key.lower() in _PRIVATE_KEYS for key in keys):
        raise ValueError("private event payload field")

    string_bounds = {
        "delta": (1, _MAX_DELTA),
        "content": (0, _MAX_TEXT),
        "display_name": (1, _MAX_DISPLAY),
        "public_summary": (1, _MAX_SUMMARY),
        "input_summary": (0, _MAX_SUMMARY),
        "result_summary": (0, _MAX_RESULT_SUMMARY),
        "filename": (1, _MAX_FILENAME),
        "media_type": (1, _MAX_MEDIA_TYPE),
        "code": (1, _MAX_CODE),
        "default_message": (1, _MAX_DEFAULT_MESSAGE),
        "detail": (0, _MAX_DETAIL),
    }
    integer_bounds = {
        "duration_ms": (0, _MAX_DURATION),
        "turn_count": (0, _MAX_TURNS),
        "progress_percent": (0, 100),
        "size_bytes": (0, _MAX_SIZE_BYTES),
    }
    ref_fields = {"thinking_id", "operation_id", "subagent_id", "artifact_id", "decision_id", "terminal_event_id", "evidence_ref"}
    array_fields = {"evidence_refs", "artifact_refs"}
    for key, value in payload.items():
        if key in string_bounds:
            if key == "detail" and value is None:
                continue
            minimum, maximum = string_bounds[key]
            if not isinstance(value, str) or len(value) < minimum or len(value) > maximum:
                raise ValueError(f"invalid {key} bound")
            if key == "filename" and any(ord(char) < 32 or ord(char) == 127 or char in "/\\\\" for char in value):
                raise ValueError("invalid filename")
        elif key in integer_bounds:
            minimum, maximum = integer_bounds[key]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"invalid {key} bound")
        elif key in ref_fields:
            if key == "evidence_ref" and value is None:
                continue
            _assert_safe_ref(value, key)
        elif key in array_fields:
            if not isinstance(value, list) or len(value) > _MAX_REFS or any(not isinstance(ref, str) for ref in value):
                raise ValueError(f"invalid {key}")
            if len(set(value)) != len(value):
                raise ValueError(f"invalid {key}")
            for ref in value:
                _assert_safe_ref(ref, key)
        elif key == "detail":
            if value is not None and not isinstance(value, str):
                raise ValueError("invalid detail")

    if "public_summary" in payload:
        expected_summary = {
            "thinking.started": "Analyzing the request",
            "thinking.completed": "Analysis step completed",
        }.get(event_type)
        if payload["public_summary"] != expected_summary:
            raise ValueError("invalid public_summary")
    if "category" in payload and payload["category"] not in _ALLOWED_CATEGORIES:
        raise ValueError("invalid category")
    if "current_category" in payload and payload["current_category"] not in _ALLOWED_CATEGORIES:
        raise ValueError("invalid current_category")
    if "stop_category" in payload and payload["stop_category"] not in _ALLOWED_STOP_CATEGORIES:
        raise ValueError("invalid stop_category")
    if "failure_category" in payload:
        allowed = {"subagent_failed"} if event_type == "subagent.failed" else {"artifact_failed", "unavailable"} if event_type == "artifact.failed" else _ALLOWED_FAILURE_CATEGORIES
        if payload["failure_category"] not in allowed:
            raise ValueError("invalid failure_category")
    if "reason_code" in payload:
        allowed = {"user_cancelled", "policy_cancelled", "timeout"} if event_type == "run.cancelled" else _ALLOWED_TASK_REASON_CODES
        if payload["reason_code"] not in allowed:
            raise ValueError("invalid reason_code")
    if "denial_code" in payload and payload["denial_code"] not in {"capability_not_authorized", "policy_denied"}:
        raise ValueError("invalid denial_code")
    if "decision_code" in payload:
        allowed = {"allowed"} if event_type == "policy.allowed" else {"capability_not_authorized", "policy_denied"}
        if payload["decision_code"] not in allowed:
            raise ValueError("invalid decision_code")
    if "source" in payload and payload["source"] not in {"user", "system"}:
        raise ValueError("invalid source")
    if "status" in payload:
        expected_status = {"artifact.created": "created", "artifact.ready": "ready", "artifact.failed": "failed"}[event_type]
        if payload["status"] != expected_status:
            raise ValueError("invalid status")
    if "hydrate_required" in payload and payload["hydrate_required"] is not True:
        raise ValueError("hydrate_required must be true")
    if "projection_version" in payload and payload["projection_version"] != "ai-platform.chat-public-projection.v1":
        raise ValueError("invalid projection_version")


@dataclass
class _ToolState:
    identity: str
    name: str
    category: str
    display_name: str
    started: bool = False
    terminal: bool = False
    started_at: float = field(default_factory=time.monotonic)


@dataclass
class _TaskState:
    identity: str
    started: bool = False
    terminal: bool = False
    started_at: float = field(default_factory=time.monotonic)


class ClaudeSdkAgentEventAdapter:
    """Correlate Claude SDK facts into safe, ordered v4 candidates."""

    def __init__(
        self,
        *,
        run_id: str,
        attempt_id: str,
        authorized_capabilities: Mapping[str, object] | None = None,
        tool_policy_subjects: list[dict[str, Any]] | None = None,
        public_skill_metadata: Mapping[str, Mapping[str, str]] | None = None,
        sanitizer: Callable[[object], object],
        payload_sanitizer: Callable[[object], object],
        reasoning_sanitizer: Callable[[object], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _assert_run_id(run_id)
        _assert_attempt_id(attempt_id)
        self.run_id = run_id
        self.attempt_id = attempt_id
        self._clock = clock
        self._sanitizer = sanitizer
        self._reasoning_sanitizer = reasoning_sanitizer or sanitizer
        self._payload_sanitizer = payload_sanitizer
        self._sealed = False
        self._message_id = _opaque("msg", run_id, "assistant", attempt_id)
        self._answer_started = False
        self._answer_content = ""
        self._thinking_indices: set[tuple[object, object]] = set()
        self._task_progress_seen: set[tuple[str, str]] = set()
        self._accepted_event_ids: dict[str, str] = {}
        self._tool_blocks: dict[str, tuple[str, dict[str, object]]] = {}
        self._tools: dict[str, tuple[str, str, str]] = {}
        self._tool_states: dict[str, _ToolState] = {}
        self._tasks: dict[str, _TaskState] = {}
        self._policy_decisions: set[str] = set()
        self._seen_events: set[str] = set()
        self._capabilities: dict[str, tuple[str, str]] = {}
        self._load_capabilities(authorized_capabilities, tool_policy_subjects, public_skill_metadata)

    @property
    def sealed(self) -> bool:
        return self._sealed

    @property
    def message_id(self) -> str:
        return self._message_id

    def seal(self, reason: str = "") -> None:
        del reason
        self._sealed = True

    def _load_capabilities(
        self,
        configured: Mapping[str, object] | None,
        subjects: list[dict[str, Any]] | None,
        skill_metadata: Mapping[str, Mapping[str, str]] | None,
    ) -> None:
        if isinstance(configured, Mapping):
            for identity, value in configured.items():
                if not isinstance(identity, str):
                    continue
                category: str | None = None
                label: object = None
                if isinstance(value, Mapping):
                    category = value.get("category") if isinstance(value.get("category"), str) else None
                    label = value.get("display_name") or value.get("label")
                elif isinstance(value, (tuple, list)) and len(value) >= 2:
                    category = value[0] if isinstance(value[0], str) else None
                    label = value[1]
                safe_label = _safe_display(label, sanitizer=self._sanitizer)
                if category in _TOOL_CATEGORIES and safe_label:
                    self._capabilities[identity] = (category, safe_label)
        for subject in subjects or []:
            if not isinstance(subject, dict) or subject.get("active") is False or subject.get("identity_authorized") is False:
                continue
            identity = subject.get("identity")
            if not isinstance(identity, str):
                continue
            if identity == "Skill":
                names = subject.get("allowed_skill_names")
                if isinstance(names, list):
                    labels = subject.get("public_skill_labels")
                    for name in names:
                        if not isinstance(name, str):
                            continue
                        label = labels.get(name) if isinstance(labels, dict) else None
                        safe_label = _safe_display(label, sanitizer=self._sanitizer)
                        if not safe_label and isinstance(skill_metadata, Mapping):
                            metadata = skill_metadata.get(name)
                            label = metadata.get("display_name") if isinstance(metadata, Mapping) else None
                            safe_label = _safe_display(label, sanitizer=self._sanitizer)
                        self._capabilities[name] = ("skill", safe_label or "Skill")
                continue
            category = _tool_category(identity, identity)
            label = subject.get("public_tool_label") or identity if category != "mcp" else subject.get("public_tool_label")
            safe_label = _safe_display(label, sanitizer=self._sanitizer)
            if category in _TOOL_CATEGORIES and safe_label:
                self._capabilities[identity] = (category, safe_label)

    def _candidate(
        self,
        event_type: str,
        payload: dict[str, object],
        *,
        identity: str,
        causation_identity: str | None = None,
        message_id: str | None = None,
    ) -> ClaudeAgentEventCandidate:
        event_id = _opaque("evt", self.run_id, event_type, identity)
        if event_id in self._seen_events:
            raise KeyError("duplicate semantic event")
        self._seen_events.add(event_id)
        if causation_identity:
            causation = self._accepted_event_ids.get(causation_identity)
        else:
            causation = None
        candidate = ClaudeAgentEventCandidate(
            run_id=self.run_id,
            event_id=event_id,
            event_type=event_type,
            message_id=self._message_id if message_id is None else message_id,
            causation_event_id=causation,
            payload=payload,
            payload_sanitizer=self._payload_sanitizer,
        )
        self._accepted_event_ids[identity] = candidate.event_id
        return candidate

    def accept_answer_text(self, value: object, *, already_gated: bool = False) -> tuple[ClaudeAgentEventCandidate, ...]:
        if self._sealed or not isinstance(value, str) or not value:
            return ()
        sanitized = self._sanitizer(value)
        if not isinstance(sanitized, str) or sanitized != value:
            return ()
        if not already_gated and _safe_text(
            value,
            maximum=_MAX_TEXT,
            sanitizer=self._sanitizer,
        ) is None:
            return ()
        if len(self._answer_content + value) > _MAX_TEXT:
            self._sealed = True
            return ()
        events: list[ClaudeAgentEventCandidate] = []
        if not self._answer_started:
            self._answer_started = True
            events.append(self._candidate("message.started", {}, identity="message"))
        self._answer_content += value
        events.append(self._candidate("message.delta", {"delta": value}, identity=f"delta:{len(self._answer_content)}"))
        return tuple(events)

    def complete_answer(self, value: object) -> tuple[ClaudeAgentEventCandidate, ...]:
        if self._sealed or not self._answer_started:
            return ()
        content = _safe_text(value, maximum=_MAX_TEXT, sanitizer=self._sanitizer)
        if content is None:
            return ()
        self._answer_content = content
        return (self._candidate("message.completed", {"content": content}, identity="message.completed"),)

    def accept_thinking_summary(
        self,
        value: object,
        *,
        block_index: object,
        message_identity: object,
    ) -> tuple[ClaudeSdkThinkingSummaryCandidate, ...]:
        if self._sealed or not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
            return ()
        key = (message_identity, block_index)
        if key in self._thinking_indices:
            return ()
        sanitized = self._reasoning_sanitizer(value)
        if not isinstance(sanitized, str) or not sanitized or len(sanitized) > _MAX_TEXT:
            return ()
        identity = f"thinking:{message_identity!s}:{block_index!s}"
        event_id = _opaque(
            "evt",
            self.run_id,
            _THINKING_SUMMARY_EVENT_TYPE,
            identity,
        )
        if event_id in self._seen_events:
            return ()
        self._thinking_indices.add(key)
        self._seen_events.add(event_id)
        return (
            ClaudeSdkThinkingSummaryCandidate(
                run_id=self.run_id,
                event_id=event_id,
                message_id=self._message_id,
                summary=sanitized,
                sanitizer=self._reasoning_sanitizer,
            ),
        )

    def accept_content_block(
        self,
        block: object,
        *,
        block_index: object = None,
        message_identity: object = None,
    ) -> tuple[ClaudeAgentEventCandidate, ...]:
        del block_index, message_identity
        if self._sealed:
            return ()
        name = type(block).__name__
        if name == "ToolUseBlock":
            identity = _safe_private_identity(getattr(block, "id", None))
            tool_name = getattr(block, "name", None)
            tool_input = getattr(block, "input", None)
            if identity is None or not isinstance(tool_name, str) or not isinstance(tool_input, dict):
                return ()
            self._tool_blocks[identity] = (tool_name, dict(tool_input))
            resolved = self._resolve_tool(tool_name, tool_input)
            if resolved is not None:
                self._tools[identity] = resolved
            return ()
        return ()

    def _resolve_tool(self, name: str, tool_input: Mapping[str, object] | None = None) -> tuple[str, str, str] | None:
        identity = name
        if name == "Skill" and isinstance(tool_input, Mapping):
            selected = tool_input.get("skill")
            if isinstance(selected, str):
                identity = selected
        configured = self._capabilities.get(identity) or self._capabilities.get(name)
        if configured is None:
            return None
        category, label = configured
        return identity, category, label

    def accept_policy_decision(
        self,
        *,
        tool_name: object,
        tool_input: object,
        allowed: bool,
        tool_use_id: object = None,
    ) -> tuple[ClaudeAgentEventCandidate, ...]:
        if self._sealed or not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return ()
        resolved = self._resolve_tool(tool_name, tool_input)
        if resolved is None:
            return ()
        identity, category, label = resolved
        call_id = _safe_private_identity(tool_use_id)
        if call_id is None or call_id in self._policy_decisions:
            return ()
        self._policy_decisions.add(call_id)
        decision_id = _opaque("decision", self.run_id, "policy", call_id)
        events: list[ClaudeAgentEventCandidate] = [
            self._candidate(
                "policy.checking",
                {"decision_id": decision_id, "category": category, "display_name": label},
                identity=f"policy.checking:{call_id}",
            )
        ]
        if allowed:
            events.append(
                self._candidate(
                    "policy.allowed",
                    {
                        "decision_id": decision_id,
                        "category": category,
                        "display_name": label,
                        "decision_code": "allowed",
                    },
                    identity=f"policy.allowed:{call_id}",
                )
            )
            return tuple(events)
        events.append(
            self._candidate(
                "policy.denied",
                {
                    "decision_id": decision_id,
                    "category": category,
                    "display_name": label,
                    "decision_code": "policy_denied",
                },
                identity=f"policy.denied:{call_id}",
            )
        )
        events.append(
            self._candidate(
                "tool.denied",
                {
                    "operation_id": _opaque("op", self.run_id, "tool", call_id),
                    "category": category,
                    "display_name": label,
                    "denial_code": "policy_denied",
                },
                identity=f"denied:{call_id}",
                causation_identity=f"policy.denied:{call_id}",
            )
        )
        return tuple(events)

    def accept_hook(
        self,
        hook_event_name: object,
        hook_input: object,
        *,
        tool_use_id: object = None,
    ) -> tuple[ClaudeAgentEventCandidate, ...]:
        if self._sealed or not isinstance(hook_event_name, str) or not isinstance(hook_input, dict):
            return ()
        if hook_event_name not in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
            return ()
        supplied = [value for value in (hook_input.get("tool_use_id"), tool_use_id) if value not in (None, "")]
        if not supplied or len({str(value) for value in supplied}) != 1:
            return ()
        call_id = _safe_private_identity(supplied[0])
        if call_id is None:
            return ()
        block = self._tool_blocks.get(call_id)
        tool_name = hook_input.get("tool_name")
        if block is None or not isinstance(tool_name, str) or block[0] != tool_name:
            return ()
        resolved = self._tools.get(call_id) or self._resolve_tool(tool_name, block[1])
        if resolved is None:
            return ()
        identity, category, label = resolved
        state = self._tool_states.get(call_id)
        if hook_event_name == "PreToolUse":
            if state is not None:
                return ()
            state = _ToolState(
                identity=identity,
                name=tool_name,
                category=category,
                display_name=label,
                started_at=self._clock(),
            )
            state.started = True
            self._tool_states[call_id] = state
            return (
                self._candidate(
                    "tool.started",
                    {
                        "operation_id": _opaque("op", self.run_id, "tool", call_id),
                        "category": category,
                        "display_name": label,
                        "input_summary": f"Starting {label}",
                    },
                    identity=f"started:{call_id}",
                ),
            )
        if state is None or not state.started or state.terminal:
            return ()
        state.terminal = True
        duration = _bounded_int(round((self._clock() - state.started_at) * 1000), maximum=_MAX_DURATION)
        operation_id = _opaque("op", self.run_id, "tool", call_id)
        if hook_event_name == "PostToolUseFailure":
            failure = _failure_category(hook_input.get("error_type") or hook_input.get("error_category"))
            return (
                self._candidate(
                    "tool.failed",
                    {"operation_id": operation_id, "category": category, "display_name": label, "duration_ms": duration, "failure_category": failure},
                    identity=f"failed:{call_id}",
                ),
            )
        return (
            self._candidate(
                "tool.completed",
                {
                    "operation_id": operation_id,
                    "category": category,
                    "display_name": label,
                    "duration_ms": duration,
                    "result_summary": f"{label} completed",
                },
                identity=f"completed:{call_id}",
            ),
        )

    def accept_task_message(self, message: object) -> tuple[ClaudeAgentEventCandidate, ...]:
        if self._sealed:
            return ()
        name = type(message).__name__
        task_id = _safe_private_identity(getattr(message, "task_id", None))
        if task_id is None:
            return ()
        state = self._tasks.get(task_id)
        public_id = _opaque("sub", self.run_id, "task", task_id)
        display = "Sub-agent"
        if name == "TaskStartedMessage":
            if state is not None:
                return ()
            self._tasks[task_id] = _TaskState(identity=task_id, started=True, started_at=self._clock())
            parent = getattr(message, "tool_use_id", None)
            parent_identity = f"started:{parent}" if isinstance(parent, str) else None
            return (
                self._candidate(
                    "subagent.started",
                    {"subagent_id": public_id, "display_name": display},
                    identity=f"subagent.started:{task_id}",
                    causation_identity=parent_identity,
                ),
            )
        if state is None or not state.started or state.terminal:
            return ()

        patch = getattr(message, "patch", None)
        patch = patch if isinstance(patch, Mapping) else {}
        status = getattr(message, "status", None) or patch.get("status")
        terminal_statuses = {"completed", "failed", "killed"}
        duration = _bounded_int(round((self._clock() - state.started_at) * 1000), maximum=_MAX_DURATION)
        if status in terminal_statuses or (name == "TaskNotificationMessage" and status in {"completed", "failed", "stopped"}):
            state.terminal = True
            if status == "completed":
                event_type = "subagent.completed"
                payload: dict[str, object] = {"subagent_id": public_id, "display_name": display, "duration_ms": duration}
            elif status in {"stopped", "killed"}:
                event_type = "subagent.cancelled"
                payload = {"subagent_id": public_id, "display_name": display, "duration_ms": duration, "reason_code": "run_cancelled"}
            else:
                event_type = "subagent.failed"
                payload = {"subagent_id": public_id, "display_name": display, "duration_ms": duration, "failure_category": "subagent_failed"}
            return (self._candidate(event_type, payload, identity=f"{event_type}:{task_id}"),)

        if name not in {"TaskProgressMessage", "TaskUpdatedMessage"}:
            return ()
        last_tool = getattr(message, "last_tool_name", None) or patch.get("last_tool_name")
        resolved = self._resolve_tool(last_tool, {}) if isinstance(last_tool, str) else None
        category = resolved[1] if resolved else patch.get("current_category", "execute")
        if category not in _ALLOWED_CATEGORIES:
            category = "execute"
        usage = getattr(message, "usage", None)
        progress = usage.get("progress_percent") if isinstance(usage, Mapping) else patch.get("progress_percent")
        payload: dict[str, object] = {"subagent_id": public_id, "display_name": display, "duration_ms": duration, "current_category": category}
        if isinstance(progress, int) and not isinstance(progress, bool):
            payload["progress_percent"] = _bounded_int(progress, maximum=100)
        token = getattr(message, "uuid", None) or hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()
        progress_key = (task_id, str(token))
        if progress_key in self._task_progress_seen:
            return ()
        self._task_progress_seen.add(progress_key)
        return (self._candidate("subagent.progress", payload, identity=f"subagent.progress:{task_id}:{token}"),)


    def accept_result(self, result: object, *, final_content: object = None, sealed: bool = False) -> tuple[ClaudeAgentEventCandidate, ...]:
        if self._sealed:
            return ()
        if sealed:
            self.seal("terminal")
            return ()
        duration = _bounded_int(getattr(result, "duration_ms", 0), maximum=_MAX_DURATION)
        turns = _bounded_int(getattr(result, "num_turns", 0), maximum=_MAX_TURNS)
        events: list[ClaudeAgentEventCandidate] = []
        if self._answer_started:
            events.extend(self.complete_answer(final_content))
        events.append(
            self._candidate(
                "model.completed",
                {"duration_ms": duration, "turn_count": turns, "stop_category": _stop_category(result)},
                identity="model.completed",
            )
        )
        return tuple(events)

    def accept_artifact_reference(self, reference: Mapping[str, object]) -> tuple[ClaudeAgentEventCandidate, ...]:
        """Project only an already-authorized artifact reference."""

        if self._sealed or not isinstance(reference, Mapping):
            return ()
        artifact_id = reference.get("artifact_id")
        filename = reference.get("filename")
        media_type = reference.get("media_type")
        size = reference.get("size_bytes")
        status = reference.get("status")
        if not all(isinstance(value, str) and value for value in (artifact_id, filename, media_type)) or not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= _MAX_SIZE_BYTES:
            return ()
        safe_filename = _safe_filename(filename, sanitizer=self._sanitizer)
        safe_media_type = _safe_text(
            media_type,
            maximum=_MAX_MEDIA_TYPE,
            sanitizer=self._sanitizer,
        )
        if safe_filename is None or safe_media_type is None:
            return ()
        if status not in {"created", "ready", "failed"}:
            return ()
        payload = {"artifact_id": _opaque("art", self.run_id, "artifact", artifact_id), "filename": safe_filename, "media_type": safe_media_type, "size_bytes": size, "status": status}
        event_type = "artifact.ready" if status == "ready" else "artifact.created" if status == "created" else "artifact.failed"
        if event_type == "artifact.failed":
            payload = {"artifact_id": payload["artifact_id"], "status": "failed", "failure_category": "artifact_failed", "filename": safe_filename, "media_type": safe_media_type}
        return (self._candidate(event_type, payload, identity=f"artifact:{artifact_id}:{status}"),)


# Short aliases make the ownership boundary discoverable to runner callers.
ClaudeSdkAgentEventProjector = ClaudeSdkAgentEventAdapter
ClaudeSdkEventCandidate = ClaudeAgentEventCandidate
