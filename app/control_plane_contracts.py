from __future__ import annotations

import re
from typing import Any, Literal
from app.platform.tracing import standard_trace_id as standard_trace_id

from app.platform.public_payload import (
    sanitize_public_payload,
    sanitize_public_text,
)
from app.runs.api import (
    RUN_THINKING_EFFORT_INPUT_KEY,
    ThinkingEffort as ThinkingEffort,
    normalize_thinking_effort,
)
from app.kernel.identifiers import assert_safe_id


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


def validate_thinking_agent_options(value: object) -> object:
    if isinstance(value, dict) and "enable_thinking" in value:
        return {
            **value,
            "enable_thinking": normalize_thinking_effort(value["enable_thinking"]),
        }
    return value


def attach_run_thinking_effort(
    run_input: dict[str, Any],
    agent_options: object,
) -> dict[str, Any]:
    options = agent_options if isinstance(agent_options, dict) else {}
    effort = normalize_thinking_effort(options.get("enable_thinking"))
    if effort != "auto":
        run_input[RUN_THINKING_EFFORT_INPUT_KEY] = effort
    return run_input


def executor_model_controls(
    payload: Any,
    default_model: str | None = None,
    model_key: Literal["model", "model_id"] = "model_id",
) -> dict[str, str | None]:
    return {
        model_key: payload.model_value or payload.model_id or default_model,
        "thinking_effort": normalize_thinking_effort(
            payload.input.get(RUN_THINKING_EFFORT_INPUT_KEY)
        ),
    }


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
