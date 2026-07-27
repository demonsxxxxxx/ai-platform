"""Fail-closed public execution timeline projection."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION = "ai-platform.public-execution-event.v1"
PUBLIC_EXECUTION_KINDS = frozenset({"analysis", "capability", "file_read", "processing", "generation", "verification", "artifact", "collaboration"})
PUBLIC_EXECUTION_EVENT_TYPES = frozenset({"execution_step", "execution_progress", "execution_step_completed", "execution_step_failed"})
PUBLIC_EXECUTION_STEP_PAYLOAD_FIELDS = frozenset({"step_id", "kind", "stage", "status", "title", "summary", "progress", "safe_file_name", "artifact_public_id"})
_REQUIRED_STEP_PAYLOAD_FIELDS = PUBLIC_EXECUTION_STEP_PAYLOAD_FIELDS - {"safe_file_name", "artifact_public_id"}
PUBLIC_EXECUTION_EVENT_FIELDS = frozenset({"schema_version", "event_id", "sequence", "run_id", *PUBLIC_EXECUTION_STEP_PAYLOAD_FIELDS, "created_at"})

_FACT_KIND_CONFIG = {
    "capability_invocation": ("capability", "execution", "Using authorized capability"),
    "tool_invocation": ("processing", "execution", "Running controlled processing"),
    "execution_analysis": ("analysis", "analysis", "Analyzing request"),
    "file_processing": ("file_read", "file", "Processing authorized file"),
    "structured_terminal_check": ("verification", "verification", "Checking structured result"),
    "artifact_generation": ("generation", "artifact", "Generating result file"),
    "subagent_invocation": ("collaboration", "execution", "Coordinating task"),
}
_LIFECYCLE_CONFIG = {
    "started": ("execution_step", "running", "Started", {"current": 0, "total": 1}),
    "progress": ("execution_progress", "running", "In progress", None),
    "completed": ("execution_step_completed", "completed", "Completed", {"current": 1, "total": 1}),
    "failed": ("execution_step_failed", "failed", "Not completed", {"current": 1, "total": 1}),
}
_EVENT_STATUS = {config[0]: config[1] for config in _LIFECYCLE_CONFIG.values()}
_SAFE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_UNSAFE_LABEL_CHARS = frozenset("\\/:.;`'\"<>|{}[]$\n\r\t")
_UNSAFE_FILE_NAME_CHARS = frozenset("\\/:*?\"<>|;`$")


@dataclass
class _InvocationState:
    step_id: str
    fact_kind: str
    label: str
    total: int
    current: int
    terminal: str | None = None


def public_execution_event_type_for_lifecycle(value: object) -> str | None:
    """Return the strict public event type for a supported private lifecycle."""

    config = _LIFECYCLE_CONFIG.get(value) if isinstance(value, str) else None
    return config[0] if config else None


def _safe_text(
    value: object,
    *,
    max_length: int = 160,
    allowed: frozenset[str] | None = None,
    unsafe: frozenset[str] = frozenset(),
    require_alnum: bool = False,
) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or len(text) > max_length
        or any(ord(char) < 32 for char in text)
        or (allowed is not None and any(char not in allowed for char in text))
        or any(char in unsafe for char in text)
        or (require_alnum and not any(char.isalnum() for char in text))
    ):
        return None
    return text


def _safe_opaque_id(value: object) -> str | None:
    return _safe_text(value, max_length=128, allowed=_SAFE_ID_CHARS)


def _safe_public_label(value: object) -> str | None:
    return _safe_text(value, max_length=96, unsafe=_UNSAFE_LABEL_CHARS, require_alnum=True)


def _safe_optional_file_name(value: object) -> str | None:
    if value is None:
        return None
    file_name = _safe_text(value, max_length=128, unsafe=_UNSAFE_FILE_NAME_CHARS)
    return None if file_name in {None, ".", ".."} else file_name


def _safe_progress(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict) or set(value) != {"current", "total"}:
        return None
    current, total = value["current"], value["total"]
    if type(current) is not int or type(total) is not int or total <= 0 or not 0 <= current <= total:
        return None
    return {"current": current, "total": total}


def validate_public_execution_step_payload(
    payload: object, *, expected_kind: str | None = None
) -> dict[str, object] | None:
    """Accept only the exact persisted step payload contract."""

    expected_status = _EVENT_STATUS.get(expected_kind)
    if (
        not isinstance(payload, dict)
        or not _REQUIRED_STEP_PAYLOAD_FIELDS <= set(payload) <= PUBLIC_EXECUTION_STEP_PAYLOAD_FIELDS
        or payload.get("kind") not in PUBLIC_EXECUTION_KINDS
        or payload.get("status") != expected_status
    ):
        return None
    step_id = _safe_opaque_id(payload.get("step_id"))
    stage = _safe_text(payload.get("stage"), max_length=48)
    title, summary = _safe_public_label(payload.get("title")), _safe_public_label(payload.get("summary"))
    progress, file_name = _safe_progress(payload.get("progress")), _safe_optional_file_name(payload.get("safe_file_name"))
    artifact = payload.get("artifact_public_id")
    artifact_id = _safe_opaque_id(artifact) if artifact is not None else None
    if (
        any(value is None for value in (step_id, stage, title, summary, progress))
        or (payload.get("safe_file_name") is not None and file_name is None)
        or (artifact is not None and artifact_id is None)
        or (
            expected_kind == "execution_step_completed"
            and progress["current"] != progress["total"]
        )
    ):
        return None
    return {
        "step_id": step_id,
        "kind": payload["kind"],
        "stage": stage,
        "status": expected_status,
        "title": title,
        "summary": summary,
        "progress": progress,
        "safe_file_name": file_name,
        "artifact_public_id": artifact_id,
    }


def public_execution_event_from_row(run_id: object, row: Mapping[str, object]) -> dict[str, object] | None:
    """Compose the v1 event only from one persisted envelope and strict payload."""

    event_type = row.get("event_type")
    payload = validate_public_execution_step_payload(row.get("payload_json"), expected_kind=event_type)
    event_id, public_run_id = _safe_opaque_id(row.get("id")), _safe_opaque_id(run_id)
    sequence, created_at = row.get("sequence"), row.get("created_at")
    if (
        event_type not in PUBLIC_EXECUTION_EVENT_TYPES
        or payload is None
        or event_id is None
        or public_run_id is None
        or type(sequence) is not int
        or sequence < 0
        or created_at is not None and not isinstance(created_at, str)
    ):
        return None
    return {
        "schema_version": PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "sequence": sequence,
        "run_id": public_run_id,
        **payload,
        "created_at": created_at,
    }


class PublicExecutionProjector:
    """Project server-validated private lifecycle facts into opaque public steps."""

    def __init__(self) -> None:
        self._invocations: dict[str, _InvocationState] = {}

    def project(self, fact: object) -> dict[str, object] | None:
        if not isinstance(fact, dict):
            return None
        invocation_id = _safe_text(fact.get("invocation_id"), max_length=512)
        fact_kind, lifecycle, label = fact.get("fact_kind"), fact.get("lifecycle"), _safe_public_label(fact.get("public_label"))
        if not invocation_id or fact_kind not in _FACT_KIND_CONFIG or lifecycle not in _LIFECYCLE_CONFIG or not label:
            return None
        state = self._invocations.get(invocation_id)
        _event_type, status, summary, default_progress = _LIFECYCLE_CONFIG[lifecycle]
        kind, stage, default_title = _FACT_KIND_CONFIG[fact_kind]
        supplied_progress = _safe_progress(fact.get("progress"))
        file_name = _safe_optional_file_name(fact.get("safe_file_name"))
        artifact = fact.get("artifact_public_id")
        artifact_id = _safe_opaque_id(artifact) if artifact is not None else None
        if (fact.get("safe_file_name") is not None and file_name is None) or (artifact is not None and artifact_id is None):
            return None
        if lifecycle == "started":
            if state is not None:
                return None
            progress = supplied_progress or default_progress
            if progress is None or progress["current"] != 0:
                return None
            next_state = _InvocationState(
                step_id=f"pex_{uuid.uuid4().hex}",
                fact_kind=fact_kind,
                label=label,
                total=progress["total"],
                current=progress["current"],
            )
        else:
            if (
                state is None
                or state.terminal is not None
                or state.fact_kind != fact_kind
                or state.label != label
            ):
                return None
            if lifecycle == "progress":
                progress = supplied_progress
            elif lifecycle == "completed":
                progress = supplied_progress or {"current": state.total, "total": state.total}
            else:
                progress = supplied_progress or {"current": state.current, "total": state.total}
            if (
                progress is None
                or progress["total"] != state.total
                or progress["current"] < state.current
                or lifecycle == "completed" and progress["current"] != state.total
            ):
                return None
            next_state = state
        event = {
            "step_id": next_state.step_id,
            "kind": kind,
            "stage": stage,
            "status": status,
            "title": label if fact_kind in {"capability_invocation", "tool_invocation"} else default_title,
            "summary": summary,
            "progress": progress,
            "safe_file_name": file_name,
            "artifact_public_id": artifact_id,
        }
        if lifecycle == "started":
            self._invocations[invocation_id] = next_state
        else:
            state.current = progress["current"]
            if lifecycle in {"completed", "failed"}:
                state.terminal = lifecycle
        return event
