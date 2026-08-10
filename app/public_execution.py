"""Fail-closed public execution timeline projection."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION = "ai-platform.public-execution-event.v1"
PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION = "ai-platform.public-execution-event.v2"
PUBLIC_AGENT_PROGRESS_SCHEMA_VERSION = "ai-platform.public-agent-progress.v1"
PUBLIC_AGENT_PROGRESS_EVENT_TYPE = "agent_public_progress"
PUBLIC_EXECUTION_KINDS = frozenset({"analysis", "capability", "file_read", "processing", "generation", "verification", "artifact", "collaboration"})
PUBLIC_EXECUTION_EVENT_TYPES = frozenset({"execution_step", "execution_progress", "execution_step_completed", "execution_step_failed"})
PUBLIC_EXECUTION_STEP_PAYLOAD_FIELDS = frozenset({"step_id", "kind", "stage", "status", "title", "summary", "progress", "safe_file_name", "artifact_public_id"})
_REQUIRED_STEP_PAYLOAD_FIELDS = PUBLIC_EXECUTION_STEP_PAYLOAD_FIELDS - {"safe_file_name", "artifact_public_id"}
PUBLIC_EXECUTION_EVENT_FIELDS = frozenset({"schema_version", "event_id", "sequence", "run_id", *PUBLIC_EXECUTION_STEP_PAYLOAD_FIELDS, "created_at"})
PUBLIC_EXECUTION_V2_EVENT_TYPES = frozenset(
    {
        "execution_step",
        "execution_progress",
        "execution_step_completed",
        "execution_step_failed",
    }
)
PUBLIC_EXECUTION_V2_STEP_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "step_id",
        "presentation_kind",
        "kind",
        "stage",
        "status",
        "progress",
        "safe_label",
    }
)
_REQUIRED_V2_STEP_PAYLOAD_FIELDS = PUBLIC_EXECUTION_V2_STEP_PAYLOAD_FIELDS - {
    "safe_label"
}

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
_V2_LIFECYCLE_CONFIG = {
    "started": ("execution_step", "running", 0),
    "progress": ("execution_progress", "running", 0),
    "completed": ("execution_step_completed", "completed", 1),
    "failed": ("execution_step_failed", "failed", 1),
}
_V2_EVENT_STATUS = {
    config[0]: config[1] for config in _V2_LIFECYCLE_CONFIG.values()
}
_V2_PUBLIC_TOOL_CONFIG = {
    "Skill": ("skill", "capability", "execution"),
    "MCP": ("mcp", "capability", "execution"),
    "Read": ("read", "file_read", "read"),
    "Glob": ("read", "file_read", "search"),
    "Grep": ("read", "file_read", "search"),
    "LS": ("read", "file_read", "search"),
    "Write": ("write", "generation", "edit"),
    "Edit": ("write", "generation", "edit"),
    "NotebookEdit": ("write", "generation", "edit"),
    "Bash": ("processing", "processing", "data"),
    "Python": ("processing", "processing", "data"),
    "Agent": ("agent", "collaboration", "execution"),
    "Task": ("agent", "collaboration", "execution"),
    "Artifact": ("artifact", "generation", "artifact"),
    "Validate": ("verification", "verification", "artifact"),
    "Adjust": ("adjustment", "processing", "execution"),
}
_V2_PUBLIC_PRESENTATION_CONFIGS = frozenset(_V2_PUBLIC_TOOL_CONFIG.values())
_V2_PUBLIC_TOOL_LABELS = {
    "Skill": "Skill",
    "MCP": "MCP",
    "Read": "Reading authorized files",
    "Glob": "Finding authorized files",
    "Grep": "Finding authorized files",
    "LS": "Finding authorized files",
    "Write": "Updating authorized files",
    "Edit": "Updating authorized files",
    "NotebookEdit": "Updating authorized files",
    "Bash": "Data processing",
    "Python": "Data processing",
    "Agent": "Coordinating task",
    "Task": "Coordinating task",
    "Artifact": "Generating artifact",
    "Validate": "Checking result",
    "Adjust": "Adjusting result",
}
_PLATFORM_PHASE_CONFIG = {
    "attachment_materialization": ("read", "file_read", "attachments"),
    "skill_staging": ("skill", "capability", "skills"),
    "sandbox_preparation": ("processing", "processing", "sandbox_preparation"),
    "sandbox_submission": ("processing", "processing", "sandbox_submission"),
    "model_wait": ("processing", "processing", "execution"),
    "artifact_validation": ("verification", "verification", "artifact_validation"),
    "artifact_recovery": ("artifact", "generation", "artifact_recovery"),
}
_PLATFORM_PHASE_LABELS = {
    "attachment_materialization": "Preparing authorized attachments",
    "skill_staging": "Loading authorized Skills",
    "sandbox_preparation": "Preparing controlled execution",
    "sandbox_submission": "Running controlled task",
    "model_wait": "Waiting for the model response",
    "artifact_validation": "Checking generated results",
    "artifact_recovery": "Preparing result recovery",
}
_V2_STATIC_LABELS_BY_PRESENTATION = {
    **{
        config: _V2_PUBLIC_TOOL_LABELS[tool_name]
        for tool_name, config in _V2_PUBLIC_TOOL_CONFIG.items()
        if tool_name not in {"Skill", "MCP"}
    },
    **{
        config: _PLATFORM_PHASE_LABELS[phase]
        for phase, config in _PLATFORM_PHASE_CONFIG.items()
    },
}
_PLATFORM_PHASE_PROGRESS_MESSAGES = {
    "attachment_materialization": {
        "started": "Preparing authorized attachments",
        "progress": "Preparing authorized attachments",
        "completed": "Authorized attachments are ready",
        "failed": "Authorized attachments could not be prepared",
    },
    "skill_staging": {
        "started": "Loading authorized Skills",
        "progress": "Loading authorized Skills",
        "completed": "Authorized Skills are ready",
        "failed": "Authorized Skills could not be loaded",
    },
    "sandbox_preparation": {
        "started": "Preparing controlled execution",
        "progress": "Preparing controlled execution",
        "completed": "Controlled execution is ready",
        "failed": "Controlled execution could not be prepared",
    },
    "sandbox_submission": {
        "started": "Running controlled task",
        "progress": "Controlled task is still running",
        "completed": "Controlled task has completed",
        "failed": "Controlled task did not complete",
    },
    "model_wait": {
        "started": "Waiting for the model response",
        "progress": "Waiting for the model response",
        "completed": "Model response is ready",
        "failed": "Model response was not available",
    },
    "artifact_validation": {
        "started": "Checking generated results",
        "progress": "Checking generated results",
        "completed": "Generated results have been checked",
        "failed": "Generated results could not be checked",
    },
    "artifact_recovery": {
        "started": "Preparing result recovery",
        "progress": "Preparing result recovery",
        "completed": "Result recovery is ready",
        "failed": "Result recovery did not complete",
    },
}
PUBLIC_AGENT_PROGRESS_PAYLOAD_FIELDS = frozenset(
    {"schema_version", "step_id", "phase", "lifecycle", "message"}
)
_V2_RAW_FACT_FIELDS = frozenset(
    {"invocation_id", "tool_name", "lifecycle", "safe_label"}
)
_REQUIRED_V2_RAW_FACT_FIELDS = _V2_RAW_FACT_FIELDS - {"safe_label"}
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


@dataclass
class _V2InvocationState:
    step_id: str
    tool_name: str
    safe_label: str | None
    terminal: str | None = None


@dataclass(frozen=True, slots=True, init=False)
class PersistablePublicExecutionStepV2:
    """One validated v2 event ready for the persisted event envelope."""

    event_type: str
    step_id: str
    presentation_kind: str
    kind: str
    stage: str
    status: str
    progress_current: int
    progress_total: int
    safe_label: str | None = None

    def __init__(self) -> None:
        raise TypeError("PersistablePublicExecutionStepV2 is projector-created")

    @property
    def payload_json(self) -> dict[str, object]:
        payload = _serialize_persistable_public_execution_step_v2(self)
        if payload is None:
            raise RuntimeError("projected public execution v2 step became invalid")
        return payload


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


def _normalize_public_execution_created_at(value: object) -> str | None:
    """Return a public timestamp or fail closed when it lacks timezone authority."""

    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
            return value if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
        except (OverflowError, ValueError, TypeError):
            return None
    if not isinstance(value, datetime):
        return None
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").removesuffix("+00:00") + "Z"
    except (OverflowError, ValueError, TypeError):
        return None


def _safe_progress(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict) or set(value) != {"current", "total"}:
        return None
    current, total = value["current"], value["total"]
    if type(current) is not int or type(total) is not int or total <= 0 or not 0 <= current <= total:
        return None
    return {"current": current, "total": total}


def _validate_public_execution_step_payload_v1(
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


def _validate_public_execution_step_payload_v2(
    payload: object,
    *,
    expected_kind: str | None = None,
) -> dict[str, object] | None:
    """Accept only the exact v2 semantic-step payload contract."""

    expected_status = (
        _V2_EVENT_STATUS.get(expected_kind)
        if isinstance(expected_kind, str)
        else None
    )
    if (
        not isinstance(payload, dict)
        or not _REQUIRED_V2_STEP_PAYLOAD_FIELDS
        <= set(payload)
        <= PUBLIC_EXECUTION_V2_STEP_PAYLOAD_FIELDS
        or payload.get("schema_version")
        != PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION
        or not isinstance(expected_kind, str)
        or expected_kind not in PUBLIC_EXECUTION_V2_EVENT_TYPES
        or payload.get("status") != expected_status
    ):
        return None
    step_id = _safe_opaque_id(payload.get("step_id"))
    presentation_config = (
        payload.get("presentation_kind"),
        payload.get("kind"),
        payload.get("stage"),
    )
    progress = _safe_progress(payload.get("progress"))
    has_safe_label = "safe_label" in payload
    safe_label = _safe_public_label(payload.get("safe_label")) if has_safe_label else None
    if (
        step_id is None
        or step_id != payload.get("step_id")
        or not all(isinstance(value, str) for value in presentation_config)
        or presentation_config not in _V2_PUBLIC_PRESENTATION_CONFIGS
        or progress is None
        or progress["total"] != 1
        or expected_kind in {"execution_step", "execution_progress"}
        and progress["current"] != 0
        or expected_kind in {"execution_step_completed", "execution_step_failed"}
        and progress["current"] != 1
        or has_safe_label
        and (safe_label is None or safe_label != payload.get("safe_label"))
        or presentation_config in _V2_STATIC_LABELS_BY_PRESENTATION
        and safe_label != _V2_STATIC_LABELS_BY_PRESENTATION[presentation_config]
        or presentation_config not in _V2_STATIC_LABELS_BY_PRESENTATION
        and has_safe_label
        and presentation_config[0] not in {"skill", "mcp"}
    ):
        return None
    return {
        "schema_version": PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
        "step_id": step_id,
        "presentation_kind": presentation_config[0],
        "kind": presentation_config[1],
        "stage": presentation_config[2],
        "status": expected_status,
        "progress": progress,
        **({"safe_label": safe_label} if has_safe_label else {}),
    }


def _serialize_persistable_public_execution_step_v2(
    step: object,
) -> dict[str, object] | None:
    if type(step) is not PersistablePublicExecutionStepV2:
        return None
    try:
        payload: dict[str, object] = {
            "schema_version": PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
            "step_id": step.step_id,
            "presentation_kind": step.presentation_kind,
            "kind": step.kind,
            "stage": step.stage,
            "status": step.status,
            "progress": {
                "current": step.progress_current,
                "total": step.progress_total,
            },
        }
        if step.safe_label is not None:
            payload["safe_label"] = step.safe_label
        return _validate_public_execution_step_payload_v2(
            payload,
            expected_kind=step.event_type,
        )
    except AttributeError:
        return None


def _projected_public_execution_step_v2(
    *,
    event_type: str,
    step_id: str,
    presentation_kind: str,
    kind: str,
    stage: str,
    status: str,
    progress_current: int,
    progress_total: int,
    safe_label: str | None,
) -> PersistablePublicExecutionStepV2:
    step = object.__new__(PersistablePublicExecutionStepV2)
    for field_name, value in {
        "event_type": event_type,
        "step_id": step_id,
        "presentation_kind": presentation_kind,
        "kind": kind,
        "stage": stage,
        "status": status,
        "progress_current": progress_current,
        "progress_total": progress_total,
        "safe_label": safe_label,
    }.items():
        object.__setattr__(step, field_name, value)
    if _serialize_persistable_public_execution_step_v2(step) is None:
        raise ValueError("public_execution_v2_step_invalid")
    return step


def _versioned_public_execution_step_payload(
    payload: object,
    *,
    expected_kind: str | None = None,
) -> tuple[str, dict[str, object]] | None:
    if not isinstance(expected_kind, str):
        return None
    if isinstance(payload, dict) and "schema_version" in payload:
        validated_v2 = _validate_public_execution_step_payload_v2(
            payload,
            expected_kind=expected_kind,
        )
        if validated_v2 is None:
            return None
        return PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION, validated_v2
    validated_v1 = _validate_public_execution_step_payload_v1(
        payload,
        expected_kind=expected_kind,
    )
    if validated_v1 is None:
        return None
    return PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION, validated_v1


def validate_public_execution_step_payload(
    payload: object,
    *,
    expected_kind: str | None = None,
) -> dict[str, object] | None:
    """Accept only the historical v1 callback persistence contract."""

    return _validate_public_execution_step_payload_v1(
        payload,
        expected_kind=expected_kind,
    )


def validate_versioned_public_execution_step_payload(
    payload: object,
    *,
    expected_kind: str | None = None,
) -> dict[str, object] | None:
    """Validate either public execution wire version before persistence."""

    versioned = _versioned_public_execution_step_payload(
        payload,
        expected_kind=expected_kind,
    )
    return versioned[1] if versioned is not None else None


def public_execution_phase_progress_payload(
    *,
    phase: object,
    lifecycle: object,
    step_id: object,
) -> dict[str, str] | None:
    """Return one fixed public phase message without accepting caller text."""

    if (
        not isinstance(phase, str)
        or not isinstance(lifecycle, str)
        or step_id != f"phase_{phase}"
        or phase not in _PLATFORM_PHASE_CONFIG
    ):
        return None
    message = _PLATFORM_PHASE_PROGRESS_MESSAGES.get(phase, {}).get(lifecycle)
    if message is None:
        return None
    return {
        "schema_version": PUBLIC_AGENT_PROGRESS_SCHEMA_VERSION,
        "step_id": f"phase_{phase}",
        "phase": phase,
        "lifecycle": lifecycle,
        "message": message,
    }


def validate_public_agent_progress_payload(payload: object) -> dict[str, str] | None:
    """Accept only an exact platform-generated public phase message."""

    if not isinstance(payload, dict) or set(payload) != PUBLIC_AGENT_PROGRESS_PAYLOAD_FIELDS:
        return None
    expected = public_execution_phase_progress_payload(
        phase=payload.get("phase"),
        lifecycle=payload.get("lifecycle"),
        step_id=payload.get("step_id"),
    )
    return expected if expected == payload else None


def public_execution_event_from_row(run_id: object, row: Mapping[str, object]) -> dict[str, object] | None:
    """Compose one versioned public event from persisted envelope authority."""

    event_type = row.get("event_type")
    versioned_payload = _versioned_public_execution_step_payload(
        row.get("payload_json"),
        expected_kind=event_type,
    )
    event_id, public_run_id = _safe_opaque_id(row.get("id")), _safe_opaque_id(run_id)
    sequence, raw_created_at = row.get("sequence"), row.get("created_at")
    created_at = _normalize_public_execution_created_at(raw_created_at)
    if (
        not isinstance(event_type, str)
        or event_type not in PUBLIC_EXECUTION_EVENT_TYPES
        or versioned_payload is None
        or event_id is None
        or public_run_id is None
        or type(sequence) is not int
        or sequence < 0
        or raw_created_at is not None and created_at is None
    ):
        return None
    schema_version, payload = versioned_payload
    return {
        "schema_version": schema_version,
        "event_id": event_id,
        "sequence": sequence,
        "run_id": public_run_id,
        **payload,
        "created_at": created_at,
    }


class PublicExecutionV2Projector:
    """Own the closed server-fact-to-persistable-v2 semantic seam."""

    def __init__(self) -> None:
        self._invocations: dict[str, _V2InvocationState] = {}

    def project(
        self,
        fact: object,
    ) -> PersistablePublicExecutionStepV2 | None:
        if (
            not isinstance(fact, dict)
            or not _REQUIRED_V2_RAW_FACT_FIELDS <= set(fact)
            or not set(fact) <= _V2_RAW_FACT_FIELDS
        ):
            return None
        raw_invocation_id = fact.get("invocation_id")
        invocation_id = _safe_text(raw_invocation_id, max_length=512)
        tool_name, lifecycle = fact.get("tool_name"), fact.get("lifecycle")
        if not isinstance(tool_name, str) or not isinstance(lifecycle, str):
            return None
        presentation_config = _V2_PUBLIC_TOOL_CONFIG.get(tool_name)
        lifecycle_config = _V2_LIFECYCLE_CONFIG.get(lifecycle)
        has_safe_label = "safe_label" in fact
        safe_label = _safe_public_label(fact.get("safe_label")) if has_safe_label else None
        if (
            invocation_id is None
            or invocation_id != raw_invocation_id
            or presentation_config is None
            or lifecycle_config is None
            or has_safe_label
            and (safe_label is None or safe_label != fact.get("safe_label"))
            or has_safe_label and tool_name not in {"Skill", "MCP"}
        ):
            return None
        state = self._invocations.get(invocation_id)
        if lifecycle == "started":
            if state is not None:
                return None
            next_state = _V2InvocationState(
                step_id=f"pex_{uuid.uuid4().hex}",
                tool_name=tool_name,
                safe_label=safe_label,
            )
        elif (
            state is None
            or state.terminal is not None
            or state.tool_name != tool_name
            or state.safe_label != safe_label
        ):
            return None
        else:
            next_state = state
        event_type, status, progress_current = lifecycle_config
        presentation_kind, kind, stage = presentation_config
        public_label = (
            safe_label
            if tool_name in {"Skill", "MCP"} and safe_label is not None
            else _V2_PUBLIC_TOOL_LABELS[tool_name]
        )
        projected = _projected_public_execution_step_v2(
            event_type=event_type,
            step_id=next_state.step_id,
            presentation_kind=presentation_kind,
            kind=kind,
            stage=stage,
            status=status,
            progress_current=progress_current,
            progress_total=1,
            safe_label=public_label,
        )
        if lifecycle == "started":
            self._invocations[invocation_id] = next_state
        else:
            state.terminal = lifecycle
        return projected


class PublicExecutionPhasePublisher:
    """Publish only fixed platform-owned execution phases on the v2 wire."""

    def __init__(self) -> None:
        self._phases: dict[str, str | None] = {}

    def project(
        self,
        *,
        phase: object,
        lifecycle: object,
    ) -> PersistablePublicExecutionStepV2 | None:
        if not isinstance(phase, str) or not isinstance(lifecycle, str):
            return None
        presentation_config = _PLATFORM_PHASE_CONFIG.get(phase)
        lifecycle_config = _V2_LIFECYCLE_CONFIG.get(lifecycle)
        if (
            presentation_config is None
            or presentation_config not in _V2_PUBLIC_PRESENTATION_CONFIGS
            or lifecycle_config is None
        ):
            return None
        terminal = self._phases.get(phase)
        if lifecycle == "started":
            if phase in self._phases:
                return None
            self._phases[phase] = None
        elif phase not in self._phases or terminal is not None:
            return None
        event_type, status, progress_current = lifecycle_config
        presentation_kind, kind, stage = presentation_config
        projected = _projected_public_execution_step_v2(
            event_type=event_type,
            step_id=f"phase_{phase}",
            presentation_kind=presentation_kind,
            kind=kind,
            stage=stage,
            status=status,
            progress_current=progress_current,
            progress_total=1,
            safe_label=_PLATFORM_PHASE_LABELS[phase],
        )
        if lifecycle in {"completed", "failed"}:
            self._phases[phase] = lifecycle
        return projected


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
