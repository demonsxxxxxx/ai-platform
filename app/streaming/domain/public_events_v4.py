"""Canonical v4 public-event identity, validation, and projection rules."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.streaming.domain.protocol_v4 import (
    INTERNAL_STREAM_EVENT_SCHEMA,
    PUBLIC_RUN_STREAM_SCHEMA,
    PUBLIC_STREAM_EVENT_TYPES,
    STREAM_PROJECTION_VERSION,
)
from app.streaming.domain.transport import StreamCursor, canonical_json_bytes


V4_METADATA_KEY = "__stream_v4"
V4_PUBLIC_STAGE = "agent_kernel"
V4_METADATA_VERSION = 1
_V4_PUBLISHER_MUTABLE_METADATA_FIELDS = frozenset(
    {"publication_state", "publication_attempts", "suppression_reason"}
)


class V4ProjectionError(ValueError):
    """A committed row cannot be represented by the closed v4 public schema."""


@dataclass(frozen=True, slots=True)
class V4StreamEntry:
    cursor: StreamCursor
    envelope: dict[str, object]


class StreamAuthorityView(Protocol):
    @property
    def tenant_id(self) -> str: ...

    @property
    def tenant_scope(self) -> str: ...

    @property
    def run_id(self) -> str: ...

    @property
    def attempt_id(self) -> str: ...

    @property
    def stream_incarnation(self) -> int: ...

    @property
    def authorization_epoch(self) -> int: ...


# Callback compatibility is intentionally narrow. A private or unknown
# executor callback never becomes public merely because it has a similar name.
_MESSAGE_EVENT_TYPES = {
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
_REQUIRED_PAYLOAD_KEYS: dict[str, frozenset[str]] = {
    "message.started": frozenset(),
    "message.delta": frozenset({"delta"}),
    "message.completed": frozenset({"content"}),
    "thinking.started": frozenset(),
    "thinking.completed": frozenset(),
    "model.completed": frozenset({"duration_ms", "turn_count", "stop_category"}),
    "tool.started": frozenset({"operation_id", "category", "display_name"}),
    "tool.completed": frozenset({"operation_id", "category", "display_name", "duration_ms"}),
    "tool.failed": frozenset({"operation_id", "category", "display_name", "duration_ms", "failure_category"}),
    "tool.denied": frozenset({"operation_id", "category", "display_name", "denial_code"}),
    "subagent.started": frozenset({"subagent_id", "display_name"}),
    "subagent.progress": frozenset({"subagent_id", "display_name", "duration_ms", "current_category"}),
    "subagent.completed": frozenset({"subagent_id", "display_name", "duration_ms"}),
    "subagent.failed": frozenset({"subagent_id", "display_name", "duration_ms", "failure_category"}),
    "subagent.cancelled": frozenset({"subagent_id", "display_name", "duration_ms", "reason_code"}),
    "artifact.created": frozenset({"artifact_id", "filename", "media_type", "size_bytes", "status"}),
    "artifact.ready": frozenset({"artifact_id", "filename", "media_type", "size_bytes", "status"}),
    "artifact.failed": frozenset({"artifact_id", "status", "failure_category"}),
    "policy.checking": frozenset({"decision_id", "category", "display_name"}),
    "policy.allowed": frozenset({"decision_id", "category", "display_name", "decision_code"}),
    "policy.denied": frozenset({"decision_id", "category", "display_name", "decision_code"}),
    "run.cancel_requested": frozenset({"source"}),
    "run.succeeded": frozenset({"terminal_event_id", "hydrate_required"}),
    "run.cancelled": frozenset({"terminal_event_id", "hydrate_required", "reason_code"}),
    "run.failed": frozenset({"terminal_event_id", "hydrate_required", "projection_version", "code", "default_message", "detail"}),
}
_EVENT_FIELD_VALUES: dict[tuple[str, str], frozenset[object]] = {
    ("tool.failed", "failure_category"): frozenset(
        {"invalid_input", "not_found", "permission_denied", "timeout", "unavailable", "execution_failed"}
    ),
    ("tool.denied", "denial_code"): frozenset({"capability_not_authorized", "policy_denied"}),
    ("subagent.failed", "failure_category"): frozenset({"subagent_failed"}),
    ("subagent.cancelled", "reason_code"): frozenset({"user_cancelled", "run_cancelled", "timeout"}),
    ("artifact.failed", "failure_category"): frozenset({"artifact_failed", "unavailable"}),
    ("policy.allowed", "decision_code"): frozenset({"allowed"}),
    ("policy.denied", "decision_code"): frozenset({"capability_not_authorized", "policy_denied"}),
    ("run.cancelled", "reason_code"): frozenset({"user_cancelled", "policy_cancelled", "timeout"}),
}


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise V4ProjectionError(f"v4_{name}_invalid")
    return value


def opaque_message_id(tenant_id: str, run_id: str) -> str:
    """Return a stable public identity that cannot reveal an attempt identity."""

    digest = hashlib.sha256(
        f"ai-platform-message-v4:{tenant_id}:{run_id}".encode("utf-8")
    ).hexdigest()
    return f"msg4_{digest}"


def _publication_state(row: Mapping[str, object], metadata: Mapping[str, object] | None) -> str | None:
    state = row.get("stream_publication_state")
    if isinstance(state, str):
        return state
    value = metadata.get("publication_state") if metadata is not None else None
    return value if isinstance(value, str) else None


def _stable_event_id(
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    callback_index: int,
    batch_index: int,
) -> str:
    material = [
        "ai-platform-agent-kernel-event-id-v4",
        tenant_id,
        run_id,
        attempt_id,
        batch_id,
        callback_index,
        batch_index,
    ]
    digest = hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()
    return f"evt4_{digest}"


def _stable_run_event_id(
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    stream_incarnation: int,
    event_type: str,
) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            [
                "ai-platform-agent-kernel-run-event-id-v4",
                tenant_id,
                run_id,
                attempt_id,
                stream_incarnation,
                event_type,
            ]
        )
    ).hexdigest()
    return f"evt4_run_{digest}"


_APPLICATION_EVENT_TYPES = PUBLIC_STREAM_EVENT_TYPES - {
    "stream.open",
    "stream.heartbeat",
    "stream.gap",
    "stream.end",
}
_RUN_DOMAIN_EVENT_TYPES = frozenset(
    {"run.cancel_requested", "run.succeeded", "run.failed", "run.cancelled"}
)
_CONTROL_EVENT_TYPES = frozenset({"stream.open", "stream.heartbeat", "stream.gap", "stream.end"})
_CONTROL_SCHEMA = "ai-platform.public-run-stream-control.v4"
_CONTROL_REPLAYABLE = {"stream.open": True, "stream.heartbeat": False, "stream.gap": False, "stream.end": True}
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TRACE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_FILENAME_RE = re.compile(r"^[^/\\\\\x00-\x1f\x7f]+$")
_TOOL_CATEGORIES = frozenset({"skill", "mcp", "read", "write", "edit", "search", "execute"})
_PAYLOAD_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "message.started": (frozenset(), frozenset()),
    "message.delta": (frozenset({"delta"}), frozenset({"delta"})),
    "message.completed": (frozenset({"content"}), frozenset({"content"})),
    "thinking.started": (frozenset(), frozenset()),
    "thinking.completed": (frozenset(), frozenset()),
    "model.completed": (
        frozenset({"duration_ms", "turn_count", "stop_category"}),
        frozenset({"duration_ms", "turn_count", "stop_category"}),
    ),
    "tool.started": (
        frozenset({"operation_id", "category", "display_name"}),
        frozenset({"operation_id", "category", "display_name", "input_summary", "evidence_refs"}),
    ),
    "tool.completed": (
        frozenset({"operation_id", "category", "display_name", "duration_ms"}),
        frozenset({"operation_id", "category", "display_name", "duration_ms", "result_summary", "evidence_refs", "artifact_refs"}),
    ),
    "tool.failed": (
        frozenset({"operation_id", "category", "display_name", "duration_ms", "failure_category"}),
        frozenset({"operation_id", "category", "display_name", "duration_ms", "failure_category", "evidence_refs"}),
    ),
    "tool.denied": (
        frozenset({"operation_id", "category", "display_name", "denial_code"}),
        frozenset({"operation_id", "category", "display_name", "denial_code"}),
    ),
    "subagent.started": (frozenset({"subagent_id", "display_name"}), frozenset({"subagent_id", "display_name"})),
    "subagent.progress": (
        frozenset({"subagent_id", "display_name", "duration_ms", "current_category"}),
        frozenset({"subagent_id", "display_name", "duration_ms", "current_category", "progress_percent"}),
    ),
    "subagent.completed": (
        frozenset({"subagent_id", "display_name", "duration_ms"}),
        frozenset({"subagent_id", "display_name", "duration_ms"}),
    ),
    "subagent.failed": (
        frozenset({"subagent_id", "display_name", "duration_ms", "failure_category"}),
        frozenset({"subagent_id", "display_name", "duration_ms", "failure_category"}),
    ),
    "subagent.cancelled": (
        frozenset({"subagent_id", "display_name", "duration_ms", "reason_code"}),
        frozenset({"subagent_id", "display_name", "duration_ms", "reason_code"}),
    ),
    "artifact.created": (
        frozenset({"artifact_id", "filename", "media_type", "size_bytes", "status"}),
        frozenset({"artifact_id", "filename", "media_type", "size_bytes", "status", "evidence_ref"}),
    ),
    "artifact.ready": (
        frozenset({"artifact_id", "filename", "media_type", "size_bytes", "status"}),
        frozenset({"artifact_id", "filename", "media_type", "size_bytes", "status", "evidence_ref"}),
    ),
    "artifact.failed": (
        frozenset({"artifact_id", "status", "failure_category"}),
        frozenset({"artifact_id", "status", "failure_category", "filename", "media_type"}),
    ),
    "policy.checking": (
        frozenset({"decision_id", "category", "display_name"}),
        frozenset({"decision_id", "category", "display_name"}),
    ),
    "policy.allowed": (
        frozenset({"decision_id", "category", "display_name", "decision_code"}),
        frozenset({"decision_id", "category", "display_name", "decision_code"}),
    ),
    "policy.denied": (
        frozenset({"decision_id", "category", "display_name", "decision_code"}),
        frozenset({"decision_id", "category", "display_name", "decision_code"}),
    ),
    "run.cancel_requested": (frozenset({"source"}), frozenset({"source"})),
    "run.succeeded": (
        frozenset({"terminal_event_id", "hydrate_required"}),
        frozenset({"terminal_event_id", "hydrate_required"}),
    ),
    "run.cancelled": (
        frozenset({"terminal_event_id", "hydrate_required", "reason_code"}),
        frozenset({"terminal_event_id", "hydrate_required", "reason_code"}),
    ),
    "run.failed": (
        frozenset({"terminal_event_id", "hydrate_required", "projection_version", "code", "default_message", "detail"}),
        frozenset({"terminal_event_id", "hydrate_required", "projection_version", "code", "default_message", "detail"}),
    ),
}
_MESSAGE_EVENT_TYPES = frozenset(_MESSAGE_EVENT_TYPES)


def _validate_control_payload(event_type: str, payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping) or len(payload) > 8:
        raise V4ProjectionError("v4_control_payload_invalid")
    result = dict(payload)
    if event_type == "stream.open":
        if result != {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}:
            raise V4ProjectionError("v4_stream_open_payload_invalid")
    elif event_type == "stream.heartbeat":
        if set(result) != {"status"} or result.get("status") not in {"queued", "running"}:
            raise V4ProjectionError("v4_stream_heartbeat_payload_invalid")
    elif event_type == "stream.gap":
        required = {
            "reason",
            "recovery",
            "requested_event_id",
            "requested_stream_incarnation",
            "current_stream_incarnation",
            "earliest_available_event_id",
            "latest_available_event_id",
        }
        if set(result) != required:
            raise V4ProjectionError("v4_stream_gap_payload_invalid")
        if result.get("reason") not in {
            "retained_history_unavailable",
            "stream_missing",
            "stream_continuity_unproven",
            "stream_incarnation_mismatch",
        }:
            raise V4ProjectionError("v4_stream_gap_reason_invalid")
        if result.get("recovery") != "reload_durable_state":
            raise V4ProjectionError("v4_stream_gap_recovery_invalid")
        _nullable_safe_ref(result.get("requested_event_id"), name="requested_event_id")
        requested_incarnation = result.get("requested_stream_incarnation")
        if requested_incarnation is not None:
            _positive_int(requested_incarnation, name="requested_stream_incarnation")
        _positive_int(result.get("current_stream_incarnation"), name="current_stream_incarnation")
        _nullable_safe_ref(result.get("earliest_available_event_id"), name="earliest_available_event_id")
        _nullable_safe_ref(result.get("latest_available_event_id"), name="latest_available_event_id")
    elif event_type == "stream.end":
        if set(result) != {"terminal_event_id"}:
            raise V4ProjectionError("v4_stream_end_payload_invalid")
        _safe_ref(result.get("terminal_event_id"), name="terminal_event_id")
    else:
        raise V4ProjectionError("v4_control_type_invalid")
    return result


def build_v4_control(
    *,
    event_id: str,
    tenant_scope: str,
    run_id: str,
    attempt_id: str,
    stream_incarnation: int,
    event_type: str,
    payload: Mapping[str, object],
    source: Mapping[str, object],
    causation_event_id: str | None = None,
    emitted_at: str | datetime | None = None,
) -> dict[str, object]:
    """Build one strict internal v4 transport control envelope."""

    if event_type not in _CONTROL_EVENT_TYPES:
        raise V4ProjectionError("v4_control_type_invalid")
    envelope = {
        "schema": INTERNAL_STREAM_EVENT_SCHEMA,
        "event_id": event_id,
        "tenant_scope": tenant_scope,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "message_id": None,
        "seq": None,
        "event_type": event_type,
        "stream_incarnation": stream_incarnation,
        "replayable": _CONTROL_REPLAYABLE[event_type],
        "trace_ref": None,
        "causation_event_id": causation_event_id,
        "emitted_at": emitted_at or datetime.now(timezone.utc),
        "projection_version": STREAM_PROJECTION_VERSION,
        "payload": dict(payload),
        "source": dict(source),
    }
    return validate_internal_envelope_v4(envelope)


def build_public_v4_control(**kwargs: object) -> dict[str, object]:
    """Build and strictly project one public v4 transport control."""

    public = project_public_envelope_v4(build_v4_control(**kwargs))
    if public is None:
        raise V4ProjectionError("v4_control_projection_invalid")
    return public


def successor_stream_open_event_id(
    *, tenant_scope: str, run_id: str, attempt_id: str, stream_incarnation: int
) -> str:
    """Return the deterministic v4 open identity for one physical incarnation."""

    _nonempty(tenant_scope, "tenant_scope")
    _nonempty(run_id, "run_id")
    _nonempty(attempt_id, "attempt_id")
    _positive_int(stream_incarnation, name="stream_incarnation")
    digest = hashlib.sha256(
        canonical_json_bytes(
            [
                "ai-platform-stream-open-v4",
                tenant_scope,
                run_id,
                attempt_id,
                stream_incarnation,
            ]
        )
    ).hexdigest()
    return f"sev_{digest}"


def stream_end_event_id(terminal_event_id: str) -> str:
    """Return the deterministic semantic identity for a terminal stream end."""

    _safe_ref(terminal_event_id, name="terminal_event_id")
    return "evt4_end_" + hashlib.sha256(
        canonical_json_bytes(["ai-platform-stream-end-v4", terminal_event_id])
    ).hexdigest()


def _bounded_string(value: object, *, name: str, maximum: int, minimum: int = 0) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise V4ProjectionError(f"v4_{name}_invalid")
    return value


def _safe_ref(value: object, *, name: str = "ref") -> str:
    if not isinstance(value, str) or not _SAFE_REF_RE.fullmatch(value):
        raise V4ProjectionError(f"v4_{name}_invalid")
    return value


def _nullable_safe_ref(value: object, *, name: str = "ref") -> str | None:
    if value is None:
        return None
    return _safe_ref(value, name=name)


def _nonnegative_int(value: object, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise V4ProjectionError(f"v4_{name}_invalid")
    return value


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise V4ProjectionError(f"v4_{name}_invalid")
    return value


def _as_utc(value: object) -> str:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        result = normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        result = _bounded_string(value, name="emitted_at", maximum=64, minimum=1)
        try:
            parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        except ValueError as exc:
            raise V4ProjectionError("v4_emitted_at_invalid") from exc
        if parsed.tzinfo is None:
            raise V4ProjectionError("v4_emitted_at_invalid")
    return _bounded_string(result, name="emitted_at", maximum=64, minimum=1)


def _validate_ref_array(value: object, *, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 32:
        raise V4ProjectionError(f"v4_{name}_invalid")
    refs = [_safe_ref(item, name=name) for item in value]
    if len(refs) != len(set(refs)):
        raise V4ProjectionError(f"v4_{name}_invalid")
    return refs


def _validate_payload(event_type: str, payload: object) -> dict[str, object]:
    if event_type not in _APPLICATION_EVENT_TYPES:
        raise V4ProjectionError("v4_event_type_not_public")
    if not isinstance(payload, Mapping) or len(payload) > 64:
        raise V4ProjectionError("v4_payload_invalid")
    required, allowed = _PAYLOAD_FIELDS[event_type]
    result = dict(payload)
    if set(result) - allowed or not required.issubset(result):
        raise V4ProjectionError("v4_payload_keys_invalid")
    for key in result:
        if not isinstance(key, str) or key.startswith("__"):
            raise V4ProjectionError("v4_payload_unknown_key")
    for key, value in result.items():
        if key in {"operation_id", "subagent_id", "artifact_id", "decision_id", "terminal_event_id"}:
            _safe_ref(value, name=key)
        elif key in {"evidence_ref"}:
            _nullable_safe_ref(value, name=key)
        elif key in {"evidence_refs", "artifact_refs"}:
            _validate_ref_array(value, name=key)
        elif key == "filename":
            filename = _bounded_string(value, name=key, maximum=255, minimum=1)
            if not _FILENAME_RE.fullmatch(filename):
                raise V4ProjectionError("v4_filename_invalid")
        elif key in {"display_name"}:
            _bounded_string(value, name=key, maximum=128, minimum=1)
        elif key == "media_type":
            _bounded_string(value, name=key, maximum=128, minimum=1)
        elif key == "delta":
            _bounded_string(value, name=key, maximum=8192, minimum=1)
        elif key == "content":
            _bounded_string(value, name=key, maximum=262144)
        elif key == "input_summary":
            _bounded_string(value, name=key, maximum=512)
        elif key == "result_summary":
            _bounded_string(value, name=key, maximum=2048)
        elif key == "code":
            _bounded_string(value, name=key, maximum=128, minimum=1)
        elif key == "default_message":
            _bounded_string(value, name=key, maximum=1024, minimum=1)
        elif key == "detail":
            if value is not None:
                _bounded_string(value, name=key, maximum=2048)
        elif key == "category" or key == "current_category":
            if value not in _TOOL_CATEGORIES:
                raise V4ProjectionError(f"v4_{key}_invalid")
        elif key == "duration_ms":
            _nonnegative_int(value, name=key, maximum=86_400_000)
        elif key == "turn_count":
            _nonnegative_int(value, name=key, maximum=10_000)
        elif key == "progress_percent":
            _nonnegative_int(value, name=key, maximum=100)
        elif key == "size_bytes":
            _nonnegative_int(value, name=key, maximum=1_099_511_627_776)
        elif key == "hydrate_required":
            if value is not True:
                raise V4ProjectionError("v4_hydrate_required_invalid")
        elif key == "stop_category":
            if value not in {"completed", "max_turns", "cancelled", "failed", "unknown"}:
                raise V4ProjectionError("v4_stop_category_invalid")
        elif key == "failure_category":
            allowed_failure = _EVENT_FIELD_VALUES.get((event_type, key), frozenset())
            if value not in allowed_failure:
                raise V4ProjectionError("v4_failure_category_invalid")
        elif key == "denial_code":
            allowed_denials = _EVENT_FIELD_VALUES.get((event_type, key), frozenset())
            if value not in allowed_denials:
                raise V4ProjectionError("v4_denial_code_invalid")
        elif key == "reason_code":
            allowed_reasons = _EVENT_FIELD_VALUES.get((event_type, key), frozenset())
            if value not in allowed_reasons:
                raise V4ProjectionError("v4_reason_code_invalid")
        elif key == "decision_code":
            allowed_decisions = _EVENT_FIELD_VALUES.get((event_type, key), frozenset())
            if value not in allowed_decisions:
                raise V4ProjectionError("v4_decision_code_invalid")
        elif key == "status":
            expected_status = {
                "artifact.created": "created", "artifact.ready": "ready", "artifact.failed": "failed",
            }.get(event_type)
            if value != expected_status:
                raise V4ProjectionError("v4_status_invalid")
        elif key == "source":
            if value not in {"user", "system"}:
                raise V4ProjectionError("v4_source_invalid")
        elif key == "projection_version":
            if value != "ai-platform.chat-public-projection.v1":
                raise V4ProjectionError("v4_projection_version_invalid")
        else:
            raise V4ProjectionError("v4_payload_key_unimplemented")
    return result


def _validate_source(source: object) -> dict[str, object]:
    if not isinstance(source, Mapping) or not isinstance(source.get("kind"), str):
        raise V4ProjectionError("v4_source_invalid")
    kind = source["kind"]
    if kind == "run_event":
        if set(source) != {"kind", "run_event_id", "sequence"}:
            raise V4ProjectionError("v4_source_invalid")
        return {"kind": kind, "run_event_id": _safe_ref(source.get("run_event_id"), name="run_event_id"), "sequence": _positive_int(source.get("sequence"), name="source_sequence")}
    if kind == "stream_authority":
        if set(source) != {"kind", "authority_id"}:
            raise V4ProjectionError("v4_source_invalid")
        return {"kind": kind, "authority_id": _safe_ref(source.get("authority_id"), name="authority_id")}
    if kind == "terminal_intent":
        if set(source) != {"kind", "terminal_event_id"}:
            raise V4ProjectionError("v4_source_invalid")
        return {"kind": kind, "terminal_event_id": _safe_ref(source.get("terminal_event_id"), name="terminal_event_id")}
    raise V4ProjectionError("v4_source_invalid")


def validate_internal_envelope_v4(envelope: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(envelope, Mapping):
        raise V4ProjectionError("v4_internal_envelope_invalid")
    required = {"schema", "event_id", "tenant_scope", "run_id", "attempt_id", "message_id", "seq", "event_type", "stream_incarnation", "replayable", "trace_ref", "causation_event_id", "emitted_at", "projection_version", "payload", "source"}
    if set(envelope) != required or envelope.get("schema") != INTERNAL_STREAM_EVENT_SCHEMA:
        raise V4ProjectionError("v4_internal_envelope_invalid")
    event_type = envelope.get("event_type")
    if event_type not in PUBLIC_STREAM_EVENT_TYPES:
        raise V4ProjectionError("v4_event_type_invalid")
    event_id = _bounded_string(envelope.get("event_id"), name="event_id", maximum=256, minimum=1)
    tenant_scope = _bounded_string(envelope.get("tenant_scope"), name="tenant_scope", maximum=128, minimum=1)
    if not re.fullmatch(r"^[A-Za-z0-9_-]{1,128}$", tenant_scope):
        raise V4ProjectionError("v4_tenant_scope_invalid")
    run_id = _bounded_string(envelope.get("run_id"), name="run_id", maximum=128, minimum=1)
    if not _RUN_ID_RE.fullmatch(run_id):
        raise V4ProjectionError("v4_run_id_invalid")
    attempt_id = _bounded_string(envelope.get("attempt_id"), name="attempt_id", maximum=256, minimum=1)
    message_id = envelope.get("message_id")
    seq = envelope.get("seq")
    trace_ref = envelope.get("trace_ref")
    replayable = envelope.get("replayable")
    if event_type in _CONTROL_EVENT_TYPES:
        if message_id is not None or seq is not None or trace_ref is not None:
            raise V4ProjectionError("v4_control_identity_invalid")
        if replayable is not _CONTROL_REPLAYABLE[event_type]:
            raise V4ProjectionError("v4_control_replayable_invalid")
        payload = _validate_control_payload(event_type, envelope.get("payload"))
    else:
        if event_type in _MESSAGE_EVENT_TYPES:
            message_id = _safe_ref(message_id, name="message_id")
        elif message_id is not None:
            message_id = _safe_ref(message_id, name="message_id")
        seq = _positive_int(seq, name="seq")
        if replayable is not True:
            raise V4ProjectionError("v4_replayable_invalid")
        if trace_ref is not None and (not isinstance(trace_ref, str) or not _TRACE_REF_RE.fullmatch(trace_ref)):
            raise V4ProjectionError("v4_trace_ref_invalid")
        payload = _validate_payload(event_type, envelope.get("payload"))
    stream_incarnation = _positive_int(envelope.get("stream_incarnation"), name="stream_incarnation")
    causation_event_id = _nullable_safe_ref(envelope.get("causation_event_id"), name="causation_event_id")
    emitted_at = _as_utc(envelope.get("emitted_at"))
    if envelope.get("projection_version") != STREAM_PROJECTION_VERSION:
        raise V4ProjectionError("v4_projection_version_invalid")
    source = _validate_source(envelope.get("source"))
    return {
        "schema": INTERNAL_STREAM_EVENT_SCHEMA,
        "event_id": event_id,
        "tenant_scope": tenant_scope,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "message_id": message_id,
        "seq": seq,
        "event_type": event_type,
        "stream_incarnation": stream_incarnation,
        "replayable": replayable,
        "trace_ref": trace_ref,
        "causation_event_id": causation_event_id,
        "emitted_at": emitted_at,
        "projection_version": STREAM_PROJECTION_VERSION,
        "payload": payload,
        "source": source,
    }


def _metadata(row: Mapping[str, object]) -> Mapping[str, object] | None:
    payload = row.get("payload_json")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(V4_METADATA_KEY)
    return value if isinstance(value, Mapping) else None


def _public_payload(row: Mapping[str, object]) -> dict[str, object] | None:
    payload = row.get("payload_json")
    if not isinstance(payload, Mapping):
        return None
    return {key: value for key, value in payload.items() if key != V4_METADATA_KEY}


def project_public_v4(
    row: Mapping[str, object],
    *,
    authority: StreamAuthorityView,
) -> dict[str, object] | None:
    """Project one committed row through the strict public v4 boundary."""

    try:
        if row.get("visible_to_user") is not True:
            return None
        metadata = _metadata(row)
        if metadata is None or _publication_state(row, metadata) not in {"pending", "published"}:
            return None
        row_id = row.get("id")
        run_id = row.get("run_id")
        sequence = row.get("sequence")
        event_type = row.get("event_type")
        if (
            not isinstance(row_id, str)
            or not (
                row_id.startswith("evt4_")
                or (
                    event_type in {"run.succeeded", "run.failed", "run.cancelled"}
                    and row_id.startswith("sev_")
                )
            )
            or row.get("tenant_id") != authority.tenant_id
            or run_id != authority.run_id
            or not isinstance(event_type, str)
            or not isinstance(metadata.get("attempt_id"), str)
            or metadata.get("attempt_id") != authority.attempt_id
            or metadata.get("stream_incarnation") != authority.stream_incarnation
            or metadata.get("authorization_epoch") != authority.authorization_epoch
            or metadata.get("version") != V4_METADATA_VERSION
        ):
            return None
        sequence = _positive_int(sequence, name="seq")
        message_id = metadata.get("message_id")
        if event_type in _MESSAGE_EVENT_TYPES:
            try:
                _safe_ref(message_id, name="message_id")
            except V4ProjectionError:
                return None
        elif message_id is not None:
            try:
                _safe_ref(message_id, name="message_id")
            except V4ProjectionError:
                return None
        envelope = {
            "schema": INTERNAL_STREAM_EVENT_SCHEMA,
            "event_id": row_id,
            "tenant_scope": authority.tenant_scope,
            "run_id": run_id,
            "attempt_id": authority.attempt_id,
            "message_id": message_id,
            "seq": sequence,
            "event_type": event_type,
            "stream_incarnation": authority.stream_incarnation,
            "replayable": True,
            "trace_ref": metadata.get("trace_ref"),
            "causation_event_id": metadata.get("causation_event_id"),
            "emitted_at": _as_utc(row.get("created_at")),
            "projection_version": STREAM_PROJECTION_VERSION,
            "payload": _public_payload(row),
            "source": {"kind": "run_event", "run_event_id": row_id, "sequence": sequence},
        }
        return validate_internal_envelope_v4(envelope)
    except V4ProjectionError:
        return None


def project_public_v4_successor(
    row: Mapping[str, object],
    *,
    source_authority: StreamAuthorityView,
    successor_incarnation: int,
    successor_authorization_epoch: int,
) -> dict[str, object]:
    """Project one exact source row into an unactivated successor incarnation."""

    if (
        isinstance(successor_incarnation, bool)
        or not isinstance(successor_incarnation, int)
        or successor_incarnation <= source_authority.stream_incarnation
        or isinstance(successor_authorization_epoch, bool)
        or not isinstance(successor_authorization_epoch, int)
        or successor_authorization_epoch <= source_authority.authorization_epoch
    ):
        raise V4ProjectionError("v4_successor_authority_invalid")
    source = project_public_v4(row, authority=source_authority)
    if source is None:
        raise V4ProjectionError("v4_successor_source_invalid")
    successor = dict(source)
    successor["stream_incarnation"] = successor_incarnation
    return validate_internal_envelope_v4(successor)


def project_public_envelope_v4(envelope: Mapping[str, object]) -> dict[str, object] | None:
    """Strip internal authority fields only at the public gateway boundary."""

    try:
        internal = validate_internal_envelope_v4(envelope)
    except V4ProjectionError:
        return None
    return {
        "schema": _CONTROL_SCHEMA if internal["event_type"] in _CONTROL_EVENT_TYPES else PUBLIC_RUN_STREAM_SCHEMA,
        "event_id": internal["event_id"],
        "run_id": internal["run_id"],
        "message_id": internal["message_id"],
        "seq": internal["seq"],
        "event_type": internal["event_type"],
        "stream_incarnation": internal["stream_incarnation"],
        "replayable": internal["replayable"],
        "trace_ref": internal["trace_ref"],
        "causation_event_id": internal["causation_event_id"],
        "emitted_at": internal["emitted_at"],
        "payload": internal["payload"],
    }


def strip_internal_envelope(envelope: Mapping[str, object]) -> dict[str, object]:
    """Compatibility alias for callers that need the canonical internal copy."""

    return validate_internal_envelope_v4(envelope)


def _immutable_v4_payload(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    payload = dict(value)
    metadata = payload.get(V4_METADATA_KEY)
    if isinstance(metadata, Mapping):
        payload[V4_METADATA_KEY] = {
            key: item
            for key, item in metadata.items()
            if key not in _V4_PUBLISHER_MUTABLE_METADATA_FIELDS
        }
    return payload


__all__ = [
    "V4ProjectionError",
    "V4StreamEntry",
    "build_public_v4_control",
    "build_v4_control",
    "opaque_message_id",
    "project_public_envelope_v4",
    "project_public_v4",
    "stream_end_event_id",
    "validate_internal_envelope_v4",
]
