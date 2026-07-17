from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal
from uuid import uuid4

from app.memory_redaction import is_sensitive_redaction_key, redact_memory_text
from app.validation import assert_safe_id


RUN_CONTRACT_VERSION = "ai-platform.run.v1"
RUN_PAYLOAD_SCHEMA_VERSION = "ai-platform.run-payload.v1"
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

FORBIDDEN_PUBLIC_MARKERS = (
    ".claude/",
    ".claude\\",
    "/tmp/",
    "/app/",
    "/home/",
    "/var/",
    "agent-workspaces",
    "output/",
    "qa-review-queue-runtime",
    "run_qa_review.py",
    "run_translation.py",
    "runtime211",
    "used_skills_source",
    "executor_hook",
    "executor_native",
    "inferred_used",
    "tenants/",
    "workspaces/",
)
FORBIDDEN_PUBLIC_KEYS = {
    "storage_key",
    "local_path",
    "review_result",
    "artifact_path",
    "output_path",
    "workspace_output",
    "workspace_path",
    "worker_path",
    "runtime_private_payload",
    "private_payload",
    "executor_payload",
    "source_json",
    "sandbox_workdir",
    "runner",
    "runner_path",
    "runtime_path",
    "executable_path",
    "cwd",
    "adapter_version",
    "claude_agent_model",
    "claude_agent_sdk_enabled",
    "claude_agent_sdk_import",
    "executor_type",
    "executor_version",
    "skill_version",
    "skill_manifest",
    "skill_manifests",
    "content_base64",
    "content_hash",
    "content_hashes",
    "release_decision",
    "fallback_version",
    "policy_active",
    "channel",
    "release_policy_version",
    "release_policy_previous_version",
    "release_policy_rollout_percent",
    "current_version",
    "previous_version",
    "selected_version",
    "selected_track",
    "rollout_percent",
    "bucket",
    "cohort_basis",
    "mcp_tool_id",
    "mcp_tool_ids",
    "dataset_id",
    "dataset_ids",
    "document_id",
    "chunk_id",
    "ragflow_payload",
    "resource_limits",
    "sandbox_mode",
    "browser_enabled",
    "worker_id",
    "sdk_session_id",
    "command_sha256",
    "used_skills_source",
    "inferred_used",
    "inferred_used_skills",
    "worker_boundary",
    "delegate_used",
    "delegate_executor_type",
    "legacy_runtime_fallback_used",
}
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?i)(?:^|[\s\"'({\[,=:])(?:[a-z]:[\\/])")
FORBIDDEN_PUBLIC_KEY_ALIASES = {
    "".join(ch for ch in key if ch.isalnum()).lower()
    for key in FORBIDDEN_PUBLIC_KEYS
}

STANDARD_EVENT_TYPES = frozenset(
    {
        "agent_step_blocked",
        "agent_step_completed",
        "agent_step_failed",
        "agent_step_reused",
        "agent_step_started",
        "artifact_created",
        "assistant_delta",
        "assistant_message_created",
        "cancel_requested",
        "cancel_requested_but_completed",
        "capability_selected",
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


def _has_forbidden_public_marker(value: str) -> bool:
    return bool(WINDOWS_DRIVE_PATH_PATTERN.search(value)) or any(marker in value for marker in FORBIDDEN_PUBLIC_MARKERS)


def sanitize_public_payload(value: Any, *, preserve_sensitive_keys: bool = False) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
            if normalized_key in FORBIDDEN_PUBLIC_KEY_ALIASES:
                continue
            if is_sensitive_redaction_key(key) and not preserve_sensitive_keys:
                continue
            sanitized = sanitize_public_payload(item, preserve_sensitive_keys=preserve_sensitive_keys)
            if sanitized is not None:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = [sanitize_public_payload(item, preserve_sensitive_keys=preserve_sensitive_keys) for item in value]
        return [item for item in cleaned_items if item is not None]
    if isinstance(value, str):
        if _has_forbidden_public_marker(value):
            return None
        return redact_memory_text(value)
    return value


def sanitize_public_text(value: object) -> str:
    text = "" if value is None else str(value)
    sanitized = sanitize_public_payload(text)
    return sanitized if isinstance(sanitized, str) else ""


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
