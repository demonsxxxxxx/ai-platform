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
)


V4_METADATA_KEY = "__stream_v4"
V4_PUBLIC_STAGE = "agent_kernel"
V4_METADATA_VERSION = 1


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


@dataclass(frozen=True, slots=True)
class V4Publication:
    event_id: str
    redis_id: str
    envelope: dict[str, object]


@dataclass(frozen=True, slots=True)
class V4Recovery:
    """Authorized durable rows returned after transport continuity is lost."""

    rows: tuple[Mapping[str, object], ...]
    transport_cursor: str | None = None
    transport_cursors: tuple[str, ...] = ()


# Callback compatibility is intentionally narrow.  A private or unknown
# executor callback never becomes public merely because it has a similar name.
_CALLBACK_EVENT_MAP = {"assistant_delta": "message.delta"}
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


def callback_item_to_v4(
    item: Mapping[str, object],
    *,
    callback_index: int,
    batch_index: int,
    message_id: str,
) -> V4CallbackItem | None:
    """Map only an explicitly supported callback item to a public event."""

    event_type = item.get("event_type")
    payload = item.get("payload")
    public_type = _CALLBACK_EVENT_MAP.get(event_type)
    if public_type is None or not isinstance(payload, Mapping):
        return None
    delta = payload.get("delta")
    if not isinstance(delta, str) or not delta:
        return None
    try:
        _safe_ref(message_id, name="message_id")
    except V4ProjectionError:
        return None
    return V4CallbackItem(
        callback_index=callback_index,
        batch_index=batch_index,
        event_type=public_type,
        payload={"delta": delta},
        message_id=message_id,
    )


_APPLICATION_EVENT_TYPES = PUBLIC_STREAM_EVENT_TYPES - {
    "stream.open",
    "stream.heartbeat",
    "stream.gap",
    "stream.end",
}
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
            allowed_failure = {
                "invalid_input", "not_found", "permission_denied", "timeout", "unavailable", "execution_failed",
                "subagent_failed", "artifact_failed",
            }
            if value not in allowed_failure:
                raise V4ProjectionError("v4_failure_category_invalid")
        elif key == "denial_code":
            if value not in {"capability_not_authorized", "policy_denied"}:
                raise V4ProjectionError("v4_denial_code_invalid")
        elif key == "reason_code":
            if value not in {"user_cancelled", "run_cancelled", "timeout", "policy_cancelled"}:
                raise V4ProjectionError("v4_reason_code_invalid")
        elif key == "decision_code":
            if value not in {"allowed", "capability_not_authorized", "policy_denied"}:
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
    if event_type not in _APPLICATION_EVENT_TYPES:
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
    if event_type in _MESSAGE_EVENT_TYPES:
        message_id = _safe_ref(message_id, name="message_id")
    elif message_id is not None:
        message_id = _safe_ref(message_id, name="message_id")
        raise V4ProjectionError("v4_message_id_must_be_null")
    seq = envelope.get("seq")
    if event_type in _APPLICATION_EVENT_TYPES:
        seq = _positive_int(seq, name="seq")
    stream_incarnation = _positive_int(envelope.get("stream_incarnation"), name="stream_incarnation")
    if not isinstance(envelope.get("replayable"), bool) or envelope["replayable"] is not True:
        raise V4ProjectionError("v4_replayable_invalid")
    trace_ref = envelope.get("trace_ref")
    if trace_ref is not None and (not isinstance(trace_ref, str) or not _TRACE_REF_RE.fullmatch(trace_ref)):
        raise V4ProjectionError("v4_trace_ref_invalid")
    causation_event_id = _nullable_safe_ref(envelope.get("causation_event_id"), name="causation_event_id")
    emitted_at = _as_utc(envelope.get("emitted_at"))
    if envelope.get("projection_version") != STREAM_PROJECTION_VERSION:
        raise V4ProjectionError("v4_projection_version_invalid")
    payload = _validate_payload(event_type, envelope.get("payload"))
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
        "replayable": True,
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
            or not row_id.startswith("evt4_")
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
            if message_id != opaque_message_id(authority.tenant_id, authority.run_id):
                return None
        elif message_id is not None:
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
        "schema": PUBLIC_RUN_STREAM_SCHEMA,
        "event_id": internal["event_id"],
        "run_id": internal["run_id"],
        "message_id": internal["message_id"],
        "seq": internal["seq"],
        "event_type": internal["event_type"],
        "stream_incarnation": internal["stream_incarnation"],
        "replayable": True,
        "trace_ref": internal["trace_ref"],
        "causation_event_id": internal["causation_event_id"],
        "emitted_at": internal["emitted_at"],
        "payload": internal["payload"],
    }


def strip_internal_envelope(envelope: Mapping[str, object]) -> dict[str, object]:
    """Compatibility alias for callers that need the canonical internal copy."""

    return _validate_internal_envelope(envelope)



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
    """Append idempotent v4 rows in the callback receipt transaction."""

    _nonempty(execution_lease_id, "execution_lease_id")
    if (
        tenant_id != authority.tenant_id
        or run_id != authority.run_id
        or attempt_id != authority.attempt_id
        or not isinstance(batch_id, str)
        or not batch_id
    ):
        raise V4ProjectionError("v4_callback_authority_scope_mismatch")
    rows: list[Mapping[str, object]] = []
    for item in items:
        if (
            isinstance(item.callback_index, bool)
            or not isinstance(item.callback_index, int)
            or item.callback_index < 0
            or isinstance(item.batch_index, bool)
            or not isinstance(item.batch_index, int)
            or item.batch_index < 0
            or item.event_type not in _APPLICATION_EVENT_TYPES
        ):
            raise V4ProjectionError("v4_callback_item_invalid")
        _validate_payload(item.event_type, item.payload)
        expected_message_id = opaque_message_id(tenant_id, run_id)
        if item.event_type in _MESSAGE_EVENT_TYPES:
            if item.message_id != expected_message_id:
                raise V4ProjectionError("v4_callback_message_id_invalid")
        elif item.message_id is not None:
            raise V4ProjectionError("v4_callback_message_id_invalid")
        event_id = _stable_event_id(
            tenant_id, run_id, attempt_id, batch_id, item.callback_index, item.batch_index
        )
        existing_result = await conn.execute(
            "select id, tenant_id, run_id, sequence, event_type, visible_to_user, payload_json, stream_publication_state, stream_publication_attempts, stream_publication_next_attempt_at, created_at from run_events where id = %s for update",
            (event_id,),
        )
        existing = await existing_result.fetchone()
        if existing is not None:
            rows.append(existing)
            continue
        metadata = {
            "version": V4_METADATA_VERSION,
            "callback_batch_id": batch_id,
            "callback_index": item.callback_index,
            "batch_index": item.batch_index,
            "attempt_id": attempt_id,
            "stream_incarnation": authority.stream_incarnation,
            "authorization_epoch": authority.authorization_epoch,
            "execution_lease_id": execution_lease_id,
            "message_id": item.message_id,
            "trace_ref": item.trace_ref,
            "causation_event_id": item.causation_event_id,
            "publication_state": "pending",
            "publication_attempts": 0,
            "lease_fence": "active",
            "cancellation_fence": "not_requested",
        }
        event = postgres.LedgerEvent(
            event_type=item.event_type,
            stage=V4_PUBLIC_STAGE,
            payload={**dict(item.payload), V4_METADATA_KEY: metadata},
            visible_to_user=True,
            trace_id=item.trace_ref,
        )
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
        rows.append(
            {
                "id": receipt.event_id,
                "run_id": run_id,
                "sequence": receipt.cursor.sequence,
                "event_type": item.event_type,
                "visible_to_user": True,
                "payload_json": dict(event.payload),
                "stream_publication_state": "pending",
                "stream_publication_attempts": 0,
                "stream_publication_next_attempt_at": datetime.now(timezone.utc),
                "created_at": receipt.created_at,
            }
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
        tenant_scope = _nonempty(internal.get("tenant_scope"), "tenant_scope")
        run_id = _nonempty(internal.get("run_id"), "run_id")
        incarnation = internal.get("stream_incarnation")
        if isinstance(incarnation, bool) or not isinstance(incarnation, int) or incarnation < 1:
            raise V4ProjectionError("v4_stream_incarnation_invalid")
        payload = canonical_json_bytes(dict(internal))
        try:
            return await self._bridge.append_canonical(
                tenant_scope_value=tenant_scope,
                run_id=run_id,
                stream_incarnation=incarnation,
                event_id=_nonempty(internal.get("event_id"), "event_id"),
                event_type=_nonempty(internal.get("event_type"), "event_type"),
                envelope_bytes=payload,
                terminal_event_id=str(internal.get("event_id") or ""),
            )
        except StreamContractError:
            raise
        except ResponseError as exc:
            raise StreamTransportUnavailable("v4_stream_append_unavailable") from exc
        except Exception as exc:
            if isinstance(exc, StreamTransportUnavailable):
                raise
            raise StreamTransportUnavailable("v4_stream_append_unavailable") from exc


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
    result = await conn.execute(
        """
        select id, tenant_id, run_id, sequence, event_type, visible_to_user,
               payload_json, stream_publication_state, created_at
        from run_events
        where tenant_id = %s and run_id = %s and sequence > %s
          and visible_to_user = true
          and stream_publication_state in ('pending', 'published')
        order by sequence asc
        limit %s
        """,
        (tenant_id, run_id, after_sequence, limit),
    )
    rows: list[Mapping[str, object]] = []
    for row in await result.fetchall():
        rebound = _row_for_current_authority(row, authority=authority)
        if rebound is not None:
            rows.append(rebound)
    return V4Recovery(tuple(rows))


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
    """Recover exact PG facts, republish them, and return Redis's real cursor."""

    recovery = await recover_v4_rows(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        authority=authority,
        after_sequence=after_sequence,
        limit=limit,
    )
    projected: list[Mapping[str, object]] = []
    transport_cursors: list[str] = []
    for row in recovery.rows:
        internal = project_public_v4(row, authority=authority)
        if internal is None:
            continue
        transport_cursor = await bridge.append(internal)
        transport_cursors.append(transport_cursor)
        projected.append(internal)
    return V4Recovery(
        tuple(projected),
        transport_cursors[-1] if transport_cursors else None,
        tuple(transport_cursors),
    )


__all__ = [
    "V4CallbackItem",
    "V4ProjectionError",
    "V4Publication",
    "V4Recovery",
    "V4RedisStreamBridge",
    "append_callback_v4_rows",
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
    "strip_internal_envelope",
]
