from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal
from uuid import uuid4

from app.platform.public_payload import (
    sanitize_public_payload,
    sanitize_public_text,
)
from app.validation import assert_safe_id


RUN_CONTRACT_VERSION = "ai-platform.run.v1"
RUN_PAYLOAD_SCHEMA_VERSION = "ai-platform.run-payload.v1"
RUN_PAYLOAD_SCHEMA_VERSION_V2 = "ai-platform.run-payload.v2"
SUPPORTED_RUN_PAYLOAD_SCHEMA_VERSIONS = frozenset(
    {RUN_PAYLOAD_SCHEMA_VERSION, RUN_PAYLOAD_SCHEMA_VERSION_V2}
)
RUN_EXECUTION_KIND_SKILL = "skill"
RUN_EXECUTION_KIND_HARNESS_CHAT = "harness_chat"
RUN_EXECUTION_KINDS = frozenset(
    {RUN_EXECUTION_KIND_SKILL, RUN_EXECUTION_KIND_HARNESS_CHAT}
)
HARNESS_CHAT_EXECUTOR_TYPE = "claude-agent-worker"
HARNESS_CHAT_AGENT_ID = "general-agent"
# Read/replay compatibility only. New requests must never select this as a Skill.
LEGACY_SYNTHETIC_CHAT_SKILL_ID = "general-chat"
RUN_THINKING_EFFORT_INPUT_KEY = "_thinking_effort"
THINKING_EFFORT_LEVELS = frozenset({"off", "low", "medium", "high"})


def normalize_thinking_effort(value: object) -> str:
    if value is None:
        return "off"
    if not isinstance(value, str) or value not in THINKING_EFFORT_LEVELS:
        raise ValueError("thinking_effort_invalid")
    return value


def is_legacy_synthetic_chat_identity(
    *,
    agent_id: object,
    skill_id: object,
    execution_kind: object = None,
) -> bool:
    """Recognize only the pre-v2 synthetic Skill identity kept for compatibility."""

    return (
        agent_id == HARNESS_CHAT_AGENT_ID
        and skill_id == LEGACY_SYNTHETIC_CHAT_SKILL_ID
        and execution_kind in {None, "", RUN_EXECUTION_KIND_SKILL}
    )


EXECUTOR_RESULT_SCHEMA_VERSION = "ai-platform.executor-result.v1"
EVENT_ENVELOPE_SCHEMA_VERSION = "ai-platform.event-envelope.v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "ai-platform.artifact-manifest.v1"
SKILL_MANIFEST_SCHEMA_VERSION = "ai-platform.skill-manifest.v1"
TOOL_POLICY_SCHEMA_VERSION = "ai-platform.tool-policy.v1"
CONTEXT_SNAPSHOT_SCHEMA_VERSION = "ai-platform.context-snapshot.v1"
AUDIT_EVENT_SCHEMA_VERSION = "ai-platform.audit-event.v1"
ARTIFACT_LINEAGE_KEYS = (
    "source_run_id",
    "source_event_id",
    "source_step_id",
    "source_file_id",
    "producer_kind",
    "producer_role",
    "checkpoint_id",
    "subagent_id",
)
ARTIFACT_LINEAGE_ID_PREFIXES = {
    "source_run_id": ("run",),
    "source_event_id": ("evt", "event"),
    "source_step_id": ("step",),
    "source_file_id": ("file",),
    "checkpoint_id": ("checkpoint", "ckpt"),
    "subagent_id": ("subagent",),
}
ARTIFACT_LINEAGE_PRODUCER_KINDS = frozenset({"agent", "subagent", "tool", "runtime", "worker"})
ARTIFACT_LINEAGE_PRODUCER_ROLES = frozenset(
    {
        "agent",
        "auditor",
        "critic",
        "executor",
        "lead",
        "merger",
        "planner",
        "researcher",
        "reviewer",
        "runtime",
        "subagent",
        "translator",
        "verifier",
        "worker",
        "writer",
    }
)
HASH_LIKE_VALUE_PATTERN = re.compile(r"^[a-f0-9]{32,}$", re.IGNORECASE)

STANDARD_EVENT_TYPES = frozenset(
    {
        "agent_step_blocked",
        "agent_step_completed",
        "agent_step_failed",
        "agent_step_reused",
        "agent_step_started",
        "artifact_created",
        "artifact_ready",
        "assistant_delta",
        "assistant_message_created",
        "cancel_requested",
        "cancel_requested_but_completed",
        "capability_selected",
        "capability_staged",
        "capability_sdk_registered",
        "capability_actually_invoked",
        "capability_completed",
        "capability_optional_not_invoked",
        "checkpoint_created",
        "context_snapshot_created",
        "error",
        "event_replayed",
        "file_bound",
        "heartbeat",
        "intent_confirmed",
        "intent_detected",
        "legacy_runtime211_direct_executor_denied",
        "memory_record_created",
        "mcp_tool_call_completed",
        "mcp_tool_call_started",
        "mcp_tool_denied",
        "multi_agent_dispatch_enqueue_failed",
        "multi_agent_dispatch_handoff",
        "multi_agent_dispatch_parent_parked",
        "multi_agent_dispatch_reconciled",
        "multi_agent_parent_finalized",
        "queued",
        "run_cancelled",
        "run_completed",
        "run_created",
        "run_multi_agent_child_created",
        "run_failed",
        "run_started",
        "run_succeeded",
        "skill_selected",
        "skip",
        "sandbox_lease_created",
        "sandbox_lease_released",
        "sandbox_lease_renewed",
        "status",
        "subagent_completed",
        "subagent_failed",
        "subagent_started",
        "tool_call_completed",
        "tool_call_started",
        "tool_denied",
        "tool_permission_authorized",
        "tool_permission_denied",
        "tool_permission_decided",
        "tool_permission_requested",
        "tool_permission_terminalized",
        "worker_started",
    }
)


@dataclass(frozen=True)
class EventEnvelope:
    run_id: str
    trace_id: str
    type: str
    stage: str
    message: str = ""
    severity: Literal["info", "warning", "error"] = "info"
    visible_to_user: bool = True
    error_code: str | None = None
    latency_ms: int | None = None
    token_counts: dict[str, int] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = EVENT_ENVELOPE_SCHEMA_VERSION


@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    version: str
    source: str
    schema_version: str = SKILL_MANIFEST_SCHEMA_VERSION


@dataclass(frozen=True)
class ToolPolicy:
    tool_id: str
    decision: Literal["allow", "deny", "ask"]
    schema_version: str = TOOL_POLICY_SCHEMA_VERSION


@dataclass(frozen=True)
class ContextSnapshot:
    run_id: str
    trace_id: str
    included_message_ids: list[str] = field(default_factory=list)
    included_file_ids: list[str] = field(default_factory=list)
    included_memory_record_ids: list[str] = field(default_factory=list)
    schema_version: str = CONTEXT_SNAPSHOT_SCHEMA_VERSION


def standard_trace_id(seed: str | None = None) -> str:
    if seed:
        normalized = seed.replace("run_", "", 1).replace("-", "_")
        return f"trace_{normalized}"
    return f"trace_{uuid4().hex}"


def standard_error_code(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or "unknown_error"


def is_standard_event_type(value: str | None) -> bool:
    return bool(value and value in STANDARD_EVENT_TYPES)


def artifact_manifest_contract(
    *,
    artifact_type: str,
    manifest: dict[str, Any] | None,
    schema_version: str | None = None,
) -> dict[str, Any]:
    sanitized = sanitize_public_payload(manifest or {})
    if not isinstance(sanitized, dict):
        sanitized = {}
    return {
        **sanitized,
        "schema_version": schema_version or ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "artifact_type": artifact_type,
    }


def artifact_lineage_contract(
    manifest: dict[str, Any] | None,
    *,
    source_run_id: object | None = None,
    row: dict[str, Any] | None = None,
) -> dict[str, object]:
    source: dict[str, object] = {}
    if row:
        source.update({key: row[key] for key in ARTIFACT_LINEAGE_KEYS if key in row})
    if isinstance(manifest, dict):
        source.update({key: manifest[key] for key in ARTIFACT_LINEAGE_KEYS if key in manifest})
    if source_run_id is not None:
        source["source_run_id"] = source_run_id

    lineage: dict[str, object] = {}
    for key in ARTIFACT_LINEAGE_KEYS:
        value = source.get(key)
        if isinstance(value, str):
            sanitized = _sanitize_artifact_lineage_value(key, value)
            if sanitized is not None:
                lineage[key] = sanitized
        elif isinstance(value, (int, bool)):
            lineage[key] = value
    return lineage


def _sanitize_artifact_lineage_value(key: str, value: str) -> str | None:
    raw = value.strip()
    sanitized = sanitize_public_text(raw)
    if not sanitized or sanitized != raw:
        return None
    if HASH_LIKE_VALUE_PATTERN.fullmatch(sanitized):
        return None

    if key in ARTIFACT_LINEAGE_ID_PREFIXES:
        try:
            safe_id = assert_safe_id(sanitized, key)
        except ValueError:
            return None
        normalized = safe_id.lower()
        if not any(normalized == prefix or normalized.startswith(f"{prefix}-") or normalized.startswith(f"{prefix}_") for prefix in ARTIFACT_LINEAGE_ID_PREFIXES[key]):
            return None
        return safe_id

    normalized_value = sanitized.lower().replace("_", "-")
    if key == "producer_kind":
        return normalized_value if normalized_value in ARTIFACT_LINEAGE_PRODUCER_KINDS else None
    if key == "producer_role":
        return normalized_value if normalized_value in ARTIFACT_LINEAGE_PRODUCER_ROLES else None
    return None
