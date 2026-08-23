"""Durable Agent-kernel v4 event projection and publication helpers.

The callback receipt and these rows are written on the caller's PostgreSQL
transaction.  Redis is a bounded transport only; the reserved metadata below
is never included in a public envelope.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from redis.exceptions import ResponseError

from app.run_projection import CHAT_PUBLIC_PROJECTION_VERSION, public_terminal_projection
from app.streaming.api import redis_id_tuple as _redis_id_tuple, stream_key
from app.streaming.contracts import ResumeDecision, StreamCursor, StreamGap

from app.streaming import postgres
from app.streaming.contracts import canonical_json_bytes
from app.streaming.events_v4 import (
    INTERNAL_STREAM_EVENT_SCHEMA,
    PUBLIC_RUN_STREAM_SCHEMA,
    PUBLIC_STREAM_EVENT_TYPES,
    STREAM_PROJECTION_VERSION,
)
from app.streaming.redis import (
    RedisStreamBridge,
    StreamContractError,
    StreamTransportUnavailable,
    StreamAuthority,
    get_stream_authority,
)


V4_METADATA_KEY = "__stream_v4"
V4_PUBLIC_STAGE = "agent_kernel"
V4_METADATA_VERSION = 1
_V4_PUBLISHER_MUTABLE_METADATA_FIELDS = frozenset(
    {"publication_state", "publication_attempts", "suppression_reason"}
)


class V4ProjectionError(ValueError):
    """A committed row cannot be represented by the closed v4 public schema."""


@dataclass(frozen=True, slots=True)
class V4CallbackItem:
    callback_index: int
    batch_index: int
    event_type: str
    payload: Mapping[str, object]
    message_id: str | None = None
    trace_ref: str | None = None
    causation_event_id: str | None = None
    source_event_id: str | None = None
    source_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class V4Publication:
    event_id: str
    redis_id: str
    envelope: dict[str, object]


@dataclass(frozen=True, slots=True)
class V4StreamEntry:
    cursor: StreamCursor
    envelope: dict[str, object]


@dataclass(frozen=True, slots=True)
class V4Recovery:
    """Authorized durable rows returned after transport continuity is lost."""

    rows: tuple[Mapping[str, object], ...]
    transport_cursor: str | None = None
    transport_cursors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _V4RecoveryPage:
    rows: tuple[Mapping[str, object], ...]
    last_sequence: int | None
    exhausted: bool


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


def _stable_artifact_event_id(
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    stream_incarnation: int,
    artifact_id: str,
) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            [
                "ai-platform-agent-kernel-artifact-event-id-v4",
                tenant_id,
                run_id,
                attempt_id,
                stream_incarnation,
                artifact_id,
                "artifact.ready",
            ]
        )
    ).hexdigest()
    return f"evt4_artifact_{digest}"


def callback_item_to_v4(
    item: Mapping[str, object],
    *,
    callback_index: int,
    batch_index: int,
    message_id: str | None = None,
) -> V4CallbackItem | None:
    """Convert one already-validated bridge mapping into a v4 callback item.

    The bridge is the trust boundary. This mapper accepts only the executor
    lifecycle subset that the callback is allowed to persist; platform-owned
    run, artifact, and policy events deliberately return ``None``.
    """

    event_type = item.get("event_type")
    payload = item.get("payload")
    if event_type not in _MESSAGE_EVENT_TYPES or not isinstance(payload, Mapping):
        return None
    source_event_id = item.get("event_id")
    source_run_id = item.get("run_id")
    if not isinstance(source_event_id, str) or not source_event_id:
        return None
    if not isinstance(source_run_id, str) or not source_run_id:
        return None
    try:
        _safe_ref(source_event_id, name="source_event_id")
        _safe_ref(source_run_id, name="source_run_id")
    except V4ProjectionError:
        return None
    resolved_message_id = item.get("message_id", message_id)
    if event_type in _MESSAGE_EVENT_TYPES:
        if not isinstance(resolved_message_id, str):
            return None
        try:
            _safe_ref(resolved_message_id, name="message_id")
        except V4ProjectionError:
            return None
    elif resolved_message_id is not None:
        try:
            _safe_ref(resolved_message_id, name="message_id")
        except V4ProjectionError:
            return None
    trace_ref = item.get("trace_ref")
    if trace_ref is not None:
        try:
            _safe_ref(trace_ref, name="trace_ref")
        except V4ProjectionError:
            return None
    causation_event_id = item.get("causation_event_id")
    if causation_event_id is not None:
        try:
            _safe_ref(causation_event_id, name="causation_event_id")
        except V4ProjectionError:
            return None
    try:
        _validate_payload(str(event_type), payload)
    except V4ProjectionError:
        return None
    return V4CallbackItem(
        callback_index=callback_index,
        batch_index=batch_index,
        event_type=str(event_type),
        payload=dict(payload),
        message_id=resolved_message_id,
        trace_ref=trace_ref,
        causation_event_id=causation_event_id,
        source_event_id=source_event_id,
        source_run_id=source_run_id,
    )


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
    return _validate_internal_envelope(envelope)


def build_public_v4_control(**kwargs: object) -> dict[str, object]:
    """Build and strictly project one public v4 transport control."""

    public = project_public_envelope_v4(build_v4_control(**kwargs))
    if public is None:
        raise V4ProjectionError("v4_control_projection_invalid")
    return public


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


def _validate_internal_envelope(envelope: Mapping[str, object]) -> dict[str, object]:
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


def _row_for_current_authority(
    row: Mapping[str, object], *, authority: StreamAuthority
) -> dict[str, object] | None:
    """Copy a durable row into the current stream incarnation for recovery only."""

    metadata = _metadata(row)
    if metadata is None or metadata.get("attempt_id") != authority.attempt_id:
        return None
    payload_json = row.get("payload_json")
    if not isinstance(payload_json, Mapping):
        return None
    rebound_metadata = dict(metadata)
    rebound_metadata["stream_incarnation"] = authority.stream_incarnation
    rebound_metadata["authorization_epoch"] = authority.authorization_epoch
    rebound_payload = dict(payload_json)
    rebound_payload[V4_METADATA_KEY] = rebound_metadata
    rebound = dict(row)
    rebound["payload_json"] = rebound_payload
    return rebound


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
    authority: StreamAuthority,
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
        return _validate_internal_envelope(envelope)
    except V4ProjectionError:
        return None


def project_public_envelope_v4(envelope: Mapping[str, object]) -> dict[str, object] | None:
    """Strip internal authority fields only at the public gateway boundary."""

    try:
        internal = _validate_internal_envelope(envelope)
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

    return _validate_internal_envelope(envelope)



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


async def append_application_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    callback_index: int,
    batch_index: int,
    event_type: str,
    payload: Mapping[str, object],
    authority: StreamAuthority,
    execution_lease_id: str | None,
    event_id: str | None = None,
    terminal_intent_id: str | None = None,
    message_id: str | None = None,
    trace_ref: str | None = None,
    causation_event_id: str | None = None,
    source_event_id: str | None = None,
    source_run_id: str | None = None,
) -> Mapping[str, object]:
    """Append one idempotent application row without touching Redis."""

    if not isinstance(event_type, str) or event_type not in _APPLICATION_EVENT_TYPES:
        raise V4ProjectionError("v4_callback_item_invalid")
    is_run_domain = event_type in _RUN_DOMAIN_EVENT_TYPES
    if execution_lease_id is None:
        expected_authority_state = (
            "confirmed" if event_type == "run.cancel_requested" else "terminal"
        )
        if not is_run_domain or authority.state != expected_authority_state:
            raise V4ProjectionError("v4_run_authority_scope_mismatch")
    else:
        _nonempty(execution_lease_id, "execution_lease_id")
        if authority.state != "confirmed":
            raise V4ProjectionError("v4_callback_authority_scope_mismatch")
    if (
        tenant_id != authority.tenant_id
        or run_id != authority.run_id
        or attempt_id != authority.attempt_id
        or (not is_run_domain and authority.state != "confirmed")
        or not isinstance(batch_id, str)
        or not batch_id
    ):
        raise V4ProjectionError("v4_callback_authority_scope_mismatch")
    if is_run_domain and event_id is None:
        event_id = _stable_run_event_id(
            tenant_id,
            run_id,
            attempt_id,
            authority.stream_incarnation,
            event_type,
        )
    if event_id is not None:
        _safe_ref(event_id, name="event_id")
    if terminal_intent_id is not None:
        _safe_ref(terminal_intent_id, name="terminal_intent_id")
    if event_type in {"run.succeeded", "run.failed", "run.cancelled"}:
        terminal_payload_id = payload.get("terminal_event_id") if isinstance(payload, Mapping) else None
        if event_id != terminal_payload_id:
            raise V4ProjectionError("v4_terminal_event_id_mismatch")
        if terminal_intent_id is None or terminal_intent_id != event_id:
            raise V4ProjectionError("v4_terminal_intent_identity_mismatch")

    if (
        isinstance(callback_index, bool)
        or not isinstance(callback_index, int)
        or callback_index < 0
        or isinstance(batch_index, bool)
        or not isinstance(batch_index, int)
        or batch_index < 0
    ):
        raise V4ProjectionError("v4_callback_item_invalid")
    _validate_payload(event_type, payload)
    if event_type in _MESSAGE_EVENT_TYPES:
        if not isinstance(message_id, str):
            raise V4ProjectionError("v4_callback_message_id_invalid")
        _safe_ref(message_id, name="message_id")
    elif message_id is not None:
        _safe_ref(message_id, name="message_id")
    if source_event_id is not None:
        _safe_ref(source_event_id, name="source_event_id")
    if source_run_id is not None:
        _safe_ref(source_run_id, name="source_run_id")
        if source_run_id != run_id:
            raise V4ProjectionError("v4_callback_source_run_mismatch")
    if trace_ref is not None:
        _safe_ref(trace_ref, name="trace_ref")
    if causation_event_id is not None:
        _safe_ref(causation_event_id, name="causation_event_id")
    metadata = {
        "version": V4_METADATA_VERSION,
        "callback_batch_id": batch_id,
        "callback_index": callback_index,
        "batch_index": batch_index,
        "attempt_id": attempt_id,
        "stream_incarnation": authority.stream_incarnation,
        "authorization_epoch": authority.authorization_epoch,
        "execution_lease_id": execution_lease_id,
        "message_id": message_id,
        "trace_ref": trace_ref,
        "causation_event_id": causation_event_id,
        "source_event_id": source_event_id,
        "source_run_id": source_run_id,
        "terminal_intent_id": terminal_intent_id,
        "publication_state": "pending",
        "publication_attempts": 0,
        "lease_fence": "active" if execution_lease_id is not None else "not_required",
        "cancellation_fence": "not_requested",
    }
    expected_payload = {**dict(payload), V4_METADATA_KEY: metadata}
    event = postgres.LedgerEvent(
        event_type=event_type,
        stage=V4_PUBLIC_STAGE,
        payload=expected_payload,
        visible_to_user=True,
        trace_id=trace_ref,
    )
    event_id = event_id or _stable_event_id(
        tenant_id, run_id, attempt_id, batch_id, callback_index, batch_index
    )
    existing_result = await conn.execute(
        "select id, tenant_id, run_id, sequence, event_type, visible_to_user, payload_json, stream_publication_state, stream_publication_attempts, stream_publication_next_attempt_at, created_at from run_events where id = %s for update",
        (event_id,),
    )
    existing = await existing_result.fetchone()
    if existing is not None:
        if not isinstance(existing, Mapping) or any(
            (
                existing.get("tenant_id") != tenant_id,
                existing.get("run_id") != run_id,
                existing.get("event_type") != event_type,
                existing.get("visible_to_user") is not True,
                _immutable_v4_payload(existing.get("payload_json"))
                != _immutable_v4_payload(expected_payload),
            )
        ):
            raise V4ProjectionError("v4_callback_existing_row_conflict")
        return existing
    receipt = await postgres.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event=event,
        event_id=event_id,
    )
    await conn.execute(
        """
        update run_events
        set stream_publication_state = 'pending',
            stream_publication_attempts = 0,
            stream_publication_next_attempt_at = now(),
            stream_publication_last_error = null
        where id = %s
        """,
        (receipt.event_id,),
    )
    return {
        "id": receipt.event_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "sequence": receipt.cursor.sequence,
        "event_type": event_type,
        "visible_to_user": True,
        "payload_json": dict(event.payload),
        "stream_publication_state": "pending",
        "stream_publication_attempts": 0,
        "stream_publication_next_attempt_at": datetime.now(timezone.utc),
        "created_at": receipt.created_at,
    }


async def append_run_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    event_type: str,
    payload: Mapping[str, object],
    batch_id: str,
    event_id: str | None = None,
    terminal_intent_id: str | None = None,
    trace_ref: str | None = None,
) -> Mapping[str, object] | None:
    """Append one Run-owned v4 row on the caller's PostgreSQL transaction."""

    authority = await get_stream_authority(
        conn, tenant_id=tenant_id, run_id=run_id, for_update=True
    )
    if authority is None:
        return None
    if not attempt_id:
        attempt_id = authority.attempt_id
    if event_type in {"run.succeeded", "run.failed", "run.cancelled"}:
        if event_id is None or terminal_intent_id != event_id:
            raise V4ProjectionError("v4_terminal_intent_identity_mismatch")
    return await append_application_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        batch_id=batch_id,
        callback_index=0,
        batch_index=0,
        event_type=event_type,
        payload=payload,
        authority=authority,
        execution_lease_id=None,
        event_id=event_id,
        terminal_intent_id=terminal_intent_id,
        trace_ref=trace_ref,
        source_event_id=terminal_intent_id,
        source_run_id=run_id,
    )


async def append_artifact_ready_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    artifact_id: str,
    filename: str,
    media_type: str,
    size_bytes: int,
    execution_lease_id: str,
    trace_ref: str | None = None,
) -> Mapping[str, object]:
    """Append one durable, disclosure-safe Artifact-ready fact."""

    authority = await get_stream_authority(conn, tenant_id=tenant_id, run_id=run_id)
    if authority is None or authority.state != "confirmed":
        raise V4ProjectionError("v4_artifact_authority_unavailable")
    event_id = _stable_artifact_event_id(
        tenant_id,
        run_id,
        authority.attempt_id,
        authority.stream_incarnation,
        artifact_id,
    )
    return await append_application_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=authority.attempt_id,
        batch_id=event_id,
        callback_index=0,
        batch_index=0,
        event_type="artifact.ready",
        payload={
            "artifact_id": artifact_id,
            "filename": filename,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "status": "ready",
        },
        authority=authority,
        execution_lease_id=execution_lease_id,
        event_id=event_id,
        trace_ref=trace_ref,
        source_run_id=run_id,
    )


async def append_run_cancel_requested_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    source: str,
    trace_ref: str | None = None,
) -> Mapping[str, object] | None:
    """Append the first authoritative cancellation request on its Run transaction."""

    if source not in {"user", "system"}:
        raise V4ProjectionError("v4_run_cancel_source_invalid")
    return await append_run_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id="",
        event_type="run.cancel_requested",
        payload={"source": source},
        batch_id=f"cancel-request-{run_id}",
        trace_ref=trace_ref,
    )


def _run_terminal_payload(
    *,
    status: str,
    terminal_event_id: str,
    error_code: object = None,
    reason_code: str = "user_cancelled",
) -> dict[str, object]:
    if status == "succeeded":
        return {"terminal_event_id": terminal_event_id, "hydrate_required": True}
    if status == "cancelled":
        if reason_code not in {"user_cancelled", "policy_cancelled", "timeout"}:
            raise V4ProjectionError("v4_run_cancel_reason_invalid")
        return {
            "terminal_event_id": terminal_event_id,
            "hydrate_required": True,
            "reason_code": reason_code,
        }
    if status != "failed":
        raise V4ProjectionError("v4_run_terminal_status_invalid")
    projection = public_terminal_projection(status, error_code)
    if projection is None or projection.get("detail_kind") != "failed":
        raise V4ProjectionError("v4_run_public_terminal_projection_unavailable")
    detail_code = str(projection.get("detail_code") or "")
    default_message = str(projection.get("message") or "")
    if not detail_code or not default_message:
        raise V4ProjectionError("v4_run_public_terminal_projection_invalid")
    return {
        "terminal_event_id": terminal_event_id,
        "hydrate_required": True,
        "projection_version": CHAT_PUBLIC_PROJECTION_VERSION,
        "code": detail_code,
        "default_message": default_message,
        "detail": None,
    }


async def append_run_terminal_v4_row(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    status: str,
    terminal_event_id: str,
    error_code: object = None,
    reason_code: str = "user_cancelled",
    trace_ref: str | None = None,
) -> Mapping[str, object] | None:
    """Append the terminal Run event using the existing terminal intent identity."""

    return await append_run_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        event_type=f"run.{status}",
        payload=_run_terminal_payload(
            status=status,
            terminal_event_id=terminal_event_id,
            error_code=error_code,
            reason_code=reason_code,
        ),
        batch_id=terminal_event_id,
        event_id=terminal_event_id,
        terminal_intent_id=terminal_event_id,
        trace_ref=trace_ref,
    )


async def append_callback_v4_rows(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    items: Sequence[V4CallbackItem],
    authority: StreamAuthority,
    execution_lease_id: str,
) -> tuple[Mapping[str, object], ...]:
    """Append callback-derived v4 rows in the callback receipt transaction."""

    rows: list[Mapping[str, object]] = []
    for item in items:
        if item.source_run_id is not None and item.source_run_id != run_id:
            raise V4ProjectionError("v4_callback_source_run_mismatch")
        rows.append(
            await append_application_v4_row(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                batch_id=batch_id,
                callback_index=item.callback_index,
                batch_index=item.batch_index,
                event_type=item.event_type,
                payload=item.payload,
                authority=authority,
                execution_lease_id=execution_lease_id,
                message_id=item.message_id,
                trace_ref=item.trace_ref,
                causation_event_id=item.causation_event_id,
                source_event_id=item.source_event_id,
                source_run_id=item.source_run_id,
            )
        )
    return tuple(rows)


async def list_pending_v4_rows(
    conn: Any,
    *,
    limit: int = 64,
) -> tuple[Mapping[str, object], ...]:
    if isinstance(limit, bool) or not 1 <= limit <= 256:
        raise ValueError("v4_pending_limit_invalid")
    result = await conn.execute(
        """
        select id, tenant_id, run_id, sequence, event_type, visible_to_user,
               payload_json, stream_publication_state, stream_publication_attempts,
               stream_publication_next_attempt_at, created_at
        from run_events
        where visible_to_user = true
          and stream_publication_state = 'pending'
          and (stream_publication_next_attempt_at is null or stream_publication_next_attempt_at <= now())
          and not exists (
            select 1
            from run_events predecessor
            where predecessor.tenant_id = run_events.tenant_id
              and predecessor.run_id = run_events.run_id
              and predecessor.visible_to_user = true
              and predecessor.stream_publication_state = 'pending'
              and predecessor.sequence < run_events.sequence
          )
        order by run_id asc, sequence asc
        limit %s
        for update skip locked
        """,
        (limit,),
    )
    return tuple(await result.fetchall())


async def mark_v4_published(
    conn: Any,
    *,
    event_id: str,
    redis_id: str,
) -> bool:
    """Record transport identity only after XADD succeeds."""

    _nonempty(event_id, "event_id")
    _nonempty(redis_id, "redis_id")
    result = await conn.execute(
        """
        update run_events
        set stream_publication_state = 'published',
            stream_publication_redis_id = %s,
            stream_publication_next_attempt_at = null,
            stream_publication_last_error = null,
            payload_json = jsonb_set(
              payload_json, '{__stream_v4,publication_state}', to_jsonb('published'::text)
            )
        where id = %s
          and stream_publication_state = 'pending'
        returning id
        """,
        (redis_id, event_id),
    )
    return await result.fetchone() is not None


async def mark_v4_attempt(
    conn: Any,
    *,
    event_id: str,
) -> None:
    await conn.execute(
        """
        update run_events
        set stream_publication_attempts = coalesce(stream_publication_attempts, 0) + 1,
            stream_publication_next_attempt_at = now() + interval '5 seconds',
            payload_json = jsonb_set(
              payload_json, '{__stream_v4,publication_attempts}',
              to_jsonb(coalesce((payload_json -> '__stream_v4' ->> 'publication_attempts')::integer, 0) + 1)
            )
        where id = %s and stream_publication_state = 'pending'
        """,
        (event_id,),
    )


async def mark_v4_retry_error(
    conn: Any,
    *,
    event_id: str,
    error: str,
) -> None:
    await conn.execute(
        """
        update run_events
        set stream_publication_last_error = %s
        where id = %s and stream_publication_state = 'pending'
        """,
        (_nonempty(error, "publication_error")[:120], event_id),
    )


async def suppress_v4_event(
    conn: Any,
    *,
    event_id: str,
    reason: str,
) -> bool:
    _nonempty(event_id, "event_id")
    _nonempty(reason, "suppression_reason")
    result = await conn.execute(
        """
        update run_events
        set stream_publication_state = 'suppressed',
            stream_publication_next_attempt_at = null,
            stream_publication_last_error = %s,
            payload_json = jsonb_set(
              jsonb_set(payload_json, '{__stream_v4,publication_state}', to_jsonb('suppressed'::text)),
              '{__stream_v4,suppression_reason}', to_jsonb(%s::text)
            )
        where id = %s and stream_publication_state = 'pending'
        returning id
        """,
        (reason, reason, event_id),
    )
    return await result.fetchone() is not None


async def rebind_v4_incarnation(
    conn: Any,
    *,
    event_id: str,
    stream_incarnation: int,
    authorization_epoch: int,
) -> bool:
    if stream_incarnation < 1 or authorization_epoch < 1:
        raise ValueError("v4_authority_binding_invalid")
    result = await conn.execute(
        """
        update run_events
        set payload_json = jsonb_set(
          jsonb_set(payload_json, '{__stream_v4,stream_incarnation}', to_jsonb(%s::bigint)),
          '{__stream_v4,authorization_epoch}', to_jsonb(%s::bigint)
        )
        where id = %s and stream_publication_state = 'pending'
        returning id
        """,
        (stream_incarnation, authorization_epoch, event_id),
    )
    return await result.fetchone() is not None


class V4RedisStreamBridge:
    """Use the existing Redis bridge client for the v4 bounded transport."""

    def __init__(self, bridge: RedisStreamBridge | None = None) -> None:
        self._bridge = bridge or RedisStreamBridge()
        self._owns_bridge = bridge is None

    async def aclose(self) -> None:
        if self._owns_bridge:
            await self._bridge.aclose()

    async def append(self, envelope: Mapping[str, object]) -> str:
        try:
            internal = _validate_internal_envelope(envelope)
        except V4ProjectionError:
            raise
        event_type = _nonempty(internal.get("event_type"), "event_type")
        if event_type in {"stream.heartbeat", "stream.gap"}:
            raise StreamContractError("v4_control_not_replayable")
        tenant_scope = _nonempty(internal.get("tenant_scope"), "tenant_scope")
        run_id = _nonempty(internal.get("run_id"), "run_id")
        incarnation = internal.get("stream_incarnation")
        if isinstance(incarnation, bool) or not isinstance(incarnation, int) or incarnation < 1:
            raise V4ProjectionError("v4_stream_incarnation_invalid")
        payload = canonical_json_bytes(dict(internal))
        terminal_event_id = ""
        if event_type in {"run.succeeded", "run.cancelled", "run.failed"}:
            terminal_event_id = _nonempty(internal["event_id"], "event_id")
            if _nonempty(internal["payload"].get("terminal_event_id"), "terminal_event_id") != terminal_event_id:
                raise StreamContractError("v4_terminal_event_id_mismatch")
        elif event_type == "stream.end":
            terminal_event_id = _nonempty(
                _validate_control_payload(event_type, internal["payload"])["terminal_event_id"],
                "terminal_event_id",
            )
        try:
            redis_id = await self._bridge.append_canonical(
                tenant_scope_value=tenant_scope,
                run_id=run_id,
                stream_incarnation=incarnation,
                event_id=_nonempty(internal.get("event_id"), "event_id"),
                event_type=event_type,
                envelope_bytes=payload,
                terminal_event_id=terminal_event_id,
                protocol="v4",
            )
            if event_type not in {"run.succeeded", "run.cancelled", "run.failed"}:
                return redis_id
            terminal_event_id = _nonempty(internal["event_id"], "event_id")
            end = build_v4_control(
                event_id=stream_end_event_id(terminal_event_id),
                tenant_scope=tenant_scope,
                run_id=run_id,
                attempt_id=_nonempty(internal.get("attempt_id"), "attempt_id"),
                stream_incarnation=incarnation,
                event_type="stream.end",
                payload={"terminal_event_id": terminal_event_id},
                source={"kind": "terminal_intent", "terminal_event_id": terminal_event_id},
                causation_event_id=terminal_event_id,
                emitted_at=internal["emitted_at"],
            )
            return await self._bridge.append_canonical(
                tenant_scope_value=tenant_scope,
                run_id=run_id,
                stream_incarnation=incarnation,
                event_id=str(end["event_id"]),
                event_type="stream.end",
                envelope_bytes=canonical_json_bytes(end),
                terminal_event_id=terminal_event_id,
                protocol="v4",
            )
        except StreamContractError:
            raise
        except ResponseError as exc:
            raise StreamTransportUnavailable("v4_stream_append_unavailable") from exc
        except Exception as exc:
            if isinstance(exc, StreamTransportUnavailable):
                raise
            raise StreamTransportUnavailable("v4_stream_append_unavailable") from exc

    async def publish_non_replayable(self, envelope: Mapping[str, object]) -> str:
        """Publish heartbeat/gap live-only and return the latest real cursor."""

        internal = _validate_internal_envelope(envelope)
        if internal["event_type"] not in {"stream.heartbeat", "stream.gap"}:
            raise StreamContractError("v4_control_replayable")
        latest_cursor = await self.latest_cursor(
            tenant_scope_value=str(internal["tenant_scope"]),
            run_id=str(internal["run_id"]),
            attempt_id=str(internal["attempt_id"]),
            stream_incarnation=int(internal["stream_incarnation"]),
        )
        channel = stream_key(
            tenant_scope_value=str(internal["tenant_scope"]),
            run_id=str(internal["run_id"]),
            stream_incarnation=int(internal["stream_incarnation"]),
        ).removesuffix(":events") + ":live"
        try:
            latest = StreamCursor.parse(
                latest_cursor,
                run_id=str(internal["run_id"]),
            )
            publication = json.dumps(
                {
                    "redis_id": latest.redis_id,
                    "envelope": canonical_json_bytes(dict(internal)).decode("utf-8"),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            await self._bridge._publish_client.publish(channel, publication)
            return latest_cursor
        except Exception as exc:
            raise StreamTransportUnavailable("v4_control_publish_unavailable") from exc

    async def latest_cursor(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> str:
        """Return the latest retained cursor owned by the current Redis stream."""

        bounds = await self.retained_bounds(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )
        if bounds is None:
            raise StreamTransportUnavailable("v4_stream_cursor_unavailable")
        return bounds[1].cursor.event_id

    async def build_heartbeat(
        self,
        *,
        event_id: str,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
        status: str,
        emitted_at: str | datetime | None = None,
    ) -> tuple[dict[str, object], str]:
        """Build a live-only heartbeat and bind it to a real retained cursor."""

        if status not in {"queued", "running"}:
            raise V4ProjectionError("v4_stream_heartbeat_status_invalid")
        cursor = await self.latest_cursor(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )
        envelope = build_v4_control(
            event_id=event_id,
            tenant_scope=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
            event_type="stream.heartbeat",
            payload={"status": status},
            source={"kind": "stream_authority", "authority_id": event_id},
            emitted_at=emitted_at,
        )
        return envelope, cursor

    async def build_gap(
        self,
        *,
        event_id: str,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        requested_event_id: str | None,
        requested_stream_incarnation: int | None,
        current_stream_incarnation: int,
        reason: str,
        emitted_at: str | datetime | None = None,
    ) -> tuple[dict[str, object], str]:
        """Build a gap from Redis-owned retained bounds, never caller bounds."""

        requested_redis_id: str | None = None
        if requested_event_id is not None:
            try:
                parsed = StreamCursor.parse(requested_event_id, run_id=run_id)
                if requested_stream_incarnation != parsed.stream_incarnation:
                    raise StreamContractError("v4_gap_cursor_incarnation_invalid")
                requested_redis_id = parsed.redis_id
            except StreamContractError:
                try:
                    _redis_id_tuple(requested_event_id)
                except Exception as exc:
                    raise StreamContractError("v4_gap_cursor_invalid") from exc
                requested_redis_id = requested_event_id
        bounds = await self.retained_bounds(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=current_stream_incarnation,
        )
        if bounds is None:
            raise StreamTransportUnavailable("v4_gap_bounds_unavailable")
        first, last = bounds
        envelope = build_v4_control(
            event_id=event_id,
            tenant_scope=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=current_stream_incarnation,
            event_type="stream.gap",
            payload={
                "reason": reason,
                "recovery": "reload_durable_state",
                "requested_event_id": requested_redis_id,
                "requested_stream_incarnation": requested_stream_incarnation,
                "current_stream_incarnation": current_stream_incarnation,
                "earliest_available_event_id": first.cursor.redis_id,
                "latest_available_event_id": last.cursor.redis_id,
            },
            source={"kind": "stream_authority", "authority_id": event_id},
            emitted_at=emitted_at,
        )
        return envelope, last.cursor.event_id

    async def publish_gap(self, **kwargs: object) -> tuple[dict[str, object], str]:
        envelope, cursor = await self.build_gap(**kwargs)
        published_cursor = await self.publish_non_replayable(envelope)
        if published_cursor != cursor:
            raise StreamContractError("v4_gap_cursor_changed")
        return envelope, cursor

    async def ensure_open(self, open_payload_bytes: str | bytes) -> str | None:
        """Re-append the persisted v4 open authority before durable recovery."""

        try:
            raw = open_payload_bytes.decode("utf-8") if isinstance(open_payload_bytes, bytes) else open_payload_bytes
            internal = _validate_internal_envelope(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise V4ProjectionError("v4_open_authority_invalid") from exc
        if internal["event_type"] != "stream.open":
            raise V4ProjectionError("v4_open_authority_invalid")
        return await self._bridge.restore_v4_open(
            tenant_scope_value=str(internal["tenant_scope"]),
            run_id=str(internal["run_id"]),
            stream_incarnation=int(internal["stream_incarnation"]),
            event_id=str(internal["event_id"]),
            envelope_bytes=canonical_json_bytes(internal),
        )

    def _decode(
        self,
        row: tuple[object, Mapping[str, object]],
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> V4StreamEntry:
        redis_id = str(row[0])
        _redis_id_tuple(redis_id)
        fields = row[1]
        raw = fields.get("envelope")
        if not isinstance(raw, str):
            raise StreamContractError("v4_stream_envelope_missing")
        try:
            envelope = _validate_internal_envelope(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StreamContractError("v4_stream_envelope_invalid") from exc
        if (
            envelope["tenant_scope"] != tenant_scope_value
            or envelope["run_id"] != run_id
            or envelope["attempt_id"] != attempt_id
            or envelope["stream_incarnation"] != stream_incarnation
        ):
            raise StreamContractError("v4_stream_authority_mismatch")
        return V4StreamEntry(StreamCursor(run_id, stream_incarnation, redis_id), envelope)

    async def retained_bounds(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> tuple[V4StreamEntry, V4StreamEntry] | None:
        key = stream_key(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            stream_incarnation=stream_incarnation,
        )
        try:
            first = await self._bridge._publish_client.xrange(key, min="-", max="+", count=1)
            last = await self._bridge._publish_client.xrevrange(key, max="+", min="-", count=1)
        except Exception as exc:
            raise StreamTransportUnavailable("v4_stream_bounds_unavailable") from exc
        if not first and not last:
            return None
        if not first or not last:
            raise StreamTransportUnavailable("v4_stream_bounds_unproven")
        return self._decode(
            first[0],
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        ), self._decode(
            last[0],
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )

    async def resolve_resume(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        current_stream_incarnation: int,
        last_event_id: str | None,
    ) -> ResumeDecision:
        cursor = StreamCursor.parse(last_event_id, run_id=run_id) if last_event_id else None
        if cursor and cursor.stream_incarnation > current_stream_incarnation:
            raise StreamContractError("stream_cursor_future_incarnation")
        if cursor and cursor.stream_incarnation < current_stream_incarnation:
            return ResumeDecision(None, StreamGap("stream_incarnation_mismatch", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation))
        bounds = await self.retained_bounds(
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=current_stream_incarnation,
        )
        if bounds is None:
            return ResumeDecision(None, StreamGap("stream_missing", cursor.event_id if cursor else None, cursor.stream_incarnation if cursor else None, current_stream_incarnation))
        first, last = bounds
        if cursor is None:
            return ResumeDecision("0-0" if first.envelope["event_type"] == "stream.open" else None, None if first.envelope["event_type"] == "stream.open" else StreamGap("retained_history_unavailable", None, None, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id))
        if _redis_id_tuple(cursor.redis_id) > _redis_id_tuple(last.cursor.redis_id):
            raise StreamContractError("stream_cursor_future_redis_id")
        if _redis_id_tuple(cursor.redis_id) < _redis_id_tuple(first.cursor.redis_id):
            return ResumeDecision(None, StreamGap("retained_history_unavailable", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id))
        key = stream_key(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=current_stream_incarnation)
        try:
            exact = await self._bridge._publish_client.xrange(key, min=cursor.redis_id, max=cursor.redis_id, count=1)
        except Exception as exc:
            raise StreamTransportUnavailable("v4_stream_cursor_lookup_unavailable") from exc
        return ResumeDecision(cursor.redis_id if exact else None, None if exact else StreamGap("stream_continuity_unproven", cursor.event_id, cursor.stream_incarnation, current_stream_incarnation, first.cursor.event_id, last.cursor.event_id))

    async def replay_page(
        self,
        *,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
        after_redis_id: str,
        through_redis_id: str,
    ) -> tuple[V4StreamEntry, ...]:
        after = _redis_id_tuple(after_redis_id)
        through = _redis_id_tuple(through_redis_id)
        if after >= through:
            return ()
        key = stream_key(tenant_scope_value=tenant_scope_value, run_id=run_id, stream_incarnation=stream_incarnation)
        try:
            rows = await self._bridge._publish_client.xrange(key, min=f"({after_redis_id}", max=through_redis_id, count=128)
        except Exception as exc:
            raise StreamTransportUnavailable("v4_stream_replay_unavailable") from exc
        return tuple(
            self._decode(
                row,
                tenant_scope_value=tenant_scope_value,
                run_id=run_id,
                attempt_id=attempt_id,
                stream_incarnation=stream_incarnation,
            )
            for row in rows or ()
        )

    def decode_live_publication(
        self,
        *,
        redis_id: str,
        envelope_json: str,
        tenant_scope_value: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
    ) -> V4StreamEntry:
        return self._decode(
            (redis_id, {"envelope": envelope_json}),
            tenant_scope_value=tenant_scope_value,
            run_id=run_id,
            attempt_id=attempt_id,
            stream_incarnation=stream_incarnation,
        )


async def _recover_v4_page(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    authority: StreamAuthority,
    after_sequence: int,
    limit: int,
) -> _V4RecoveryPage:
    result = await conn.execute(
        """
        select id, tenant_id, run_id, sequence, event_type, visible_to_user,
               payload_json, stream_publication_state, created_at
        from run_events
        where tenant_id = %s and run_id = %s and sequence > %s
          and visible_to_user = true
          and stream_publication_state = 'published'
        order by sequence asc
        limit %s
        """,
        (tenant_id, run_id, after_sequence, limit),
    )
    raw_rows = tuple(await result.fetchall())
    last_sequence = after_sequence
    for row in raw_rows:
        sequence = row.get("sequence")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= last_sequence
        ):
            raise V4ProjectionError("v4_recovery_sequence_not_advancing")
        last_sequence = sequence
    rows: list[Mapping[str, object]] = []
    for row in raw_rows:
        rebound = _row_for_current_authority(row, authority=authority)
        if rebound is not None:
            rows.append(rebound)
    return _V4RecoveryPage(
        tuple(rows),
        last_sequence if raw_rows else None,
        len(raw_rows) < limit,
    )


async def recover_v4_rows(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    authority: StreamAuthority,
    after_sequence: int = 0,
    limit: int = 128,
) -> V4Recovery:
    """Return authorized PG rows after a gap; no Redis cursor is invented."""

    if authority.tenant_id != tenant_id or authority.run_id != run_id:
        raise V4ProjectionError("v4_recovery_authority_scope_mismatch")
    if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
        raise ValueError("v4_recovery_sequence_invalid")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
        raise ValueError("v4_recovery_limit_invalid")
    page = await _recover_v4_page(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        authority=authority,
        after_sequence=after_sequence,
        limit=limit,
    )
    return V4Recovery(page.rows)


async def _recover_all_v4_rows(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    authority: StreamAuthority,
) -> tuple[Mapping[str, object], ...]:
    """Read every published row from sequence zero for a stream rebuild."""

    rows: list[Mapping[str, object]] = []
    after_sequence = 0
    while True:
        page = await _recover_v4_page(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            authority=authority,
            after_sequence=after_sequence,
            limit=256,
        )
        rows.extend(page.rows)
        if page.exhausted:
            return tuple(rows)
        if page.last_sequence is None or page.last_sequence <= after_sequence:
            raise V4ProjectionError("v4_recovery_sequence_not_advancing")
        after_sequence = page.last_sequence


async def recover_v4_and_resume(
    conn: Any,
    *,
    bridge: V4RedisStreamBridge,
    tenant_id: str,
    run_id: str,
    authority: StreamAuthority,
    after_sequence: int = 0,
    limit: int = 128,
) -> V4Recovery:
    """Rebuild a missing v4 stream fully, then return the requested window."""

    if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
        raise ValueError("v4_recovery_sequence_invalid")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
        raise ValueError("v4_recovery_limit_invalid")
    open_payload_bytes = authority.open_payload_bytes
    if not isinstance(open_payload_bytes, (str, bytes)) or not open_payload_bytes:
        raise V4ProjectionError("v4_open_authority_invalid")
    restored = False
    if isinstance(bridge, V4RedisStreamBridge):
        try:
            raw_open = (
                open_payload_bytes.decode("utf-8")
                if isinstance(open_payload_bytes, bytes)
                else open_payload_bytes
            )
            open_internal = _validate_internal_envelope(json.loads(raw_open))
            if (
                raw_open != canonical_json_bytes(open_internal).decode()
                or hashlib.sha256(raw_open.encode("utf-8")).hexdigest()
                != authority.open_payload_digest
                or open_internal["event_type"] != "stream.open"
                or open_internal["event_id"] != authority.open_event_id
                or open_internal["tenant_scope"] != authority.tenant_scope
                or open_internal["run_id"] != authority.run_id
                or open_internal["attempt_id"] != authority.attempt_id
                or open_internal["stream_incarnation"] != authority.stream_incarnation
                or open_internal["payload"]
                != {"design_id": "ai-platform.redis-streams-sse-event-channel.v4"}
                or open_internal["source"]
                != {"kind": "stream_authority", "authority_id": authority.open_event_id}
            ):
                raise ValueError("v4_open_authority_mismatch")
        except Exception as exc:
            raise V4ProjectionError("v4_open_authority_invalid") from exc
        restored = await bridge.ensure_open(open_payload_bytes) is not None
    elif hasattr(bridge, "ensure_open"):
        # Non-production fakes may exercise the same restore/rebuild protocol.
        restored = await bridge.ensure_open(open_payload_bytes) is not None

    if restored:
        source_rows = await _recover_all_v4_rows(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            authority=authority,
        )
        requested_rows = tuple(
            row
            for row in source_rows
            if isinstance(row.get("sequence"), int)
            and not isinstance(row.get("sequence"), bool)
            and row["sequence"] > after_sequence
        )[:limit]
    else:
        recovery = await recover_v4_rows(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            authority=authority,
            after_sequence=after_sequence,
            limit=limit,
        )
        source_rows = recovery.rows
        requested_rows = source_rows
    requested_ids = {str(row.get("id")) for row in requested_rows}
    projected: list[Mapping[str, object]] = []
    transport_cursors: list[str] = []
    all_transport_cursors: list[str] = []
    for row in source_rows:
        internal = project_public_v4(row, authority=authority)
        if internal is None:
            continue
        transport_cursor = await bridge.append(internal)
        all_transport_cursors.append(transport_cursor)
        public = project_public_envelope_v4(internal)
        if public is not None and str(row.get("id")) in requested_ids:
            projected.append(public)
            transport_cursors.append(transport_cursor)
    return V4Recovery(
        tuple(projected),
        all_transport_cursors[-1] if all_transport_cursors else None,
        tuple(transport_cursors),
    )


__all__ = [
    "V4CallbackItem",
    "V4ProjectionError",
    "V4Publication",
    "V4Recovery",
    "V4RedisStreamBridge",
    "V4StreamEntry",
    "append_application_v4_row",
    "append_run_v4_row",
    "append_callback_v4_rows",
    "build_public_v4_control",
    "build_v4_control",
    "callback_item_to_v4",
    "list_pending_v4_rows",
    "mark_v4_attempt",
    "mark_v4_published",
    "mark_v4_retry_error",
    "rebind_v4_incarnation",
    "suppress_v4_event",
    "opaque_message_id",
    "project_public_envelope_v4",
    "project_public_v4",
    "recover_v4_and_resume",
    "recover_v4_rows",
    "stream_end_event_id",
    "strip_internal_envelope",
]
