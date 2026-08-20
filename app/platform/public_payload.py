from __future__ import annotations

import re
from typing import Any

from app.memory_redaction import (
    MEMORY_REDACTION_MODE_STRICT,
    is_sensitive_redaction_key,
    redact_memory_text,
)


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


def _has_forbidden_public_marker(value: str) -> bool:
    return bool(WINDOWS_DRIVE_PATH_PATTERN.search(value)) or any(
        marker in value for marker in FORBIDDEN_PUBLIC_MARKERS
    )


def sanitize_public_payload(value: Any, *, preserve_sensitive_keys: bool = False) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
            if normalized_key in FORBIDDEN_PUBLIC_KEY_ALIASES:
                continue
            if is_sensitive_redaction_key(key) and not preserve_sensitive_keys:
                continue
            sanitized = sanitize_public_payload(
                item,
                preserve_sensitive_keys=preserve_sensitive_keys,
            )
            if sanitized is not None:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = [
            sanitize_public_payload(item, preserve_sensitive_keys=preserve_sensitive_keys)
            for item in value
        ]
        return [item for item in cleaned_items if item is not None]
    if isinstance(value, tuple):
        cleaned_items = (
            sanitize_public_payload(item, preserve_sensitive_keys=preserve_sensitive_keys)
            for item in value
        )
        return tuple(item for item in cleaned_items if item is not None)
    if isinstance(value, str):
        if _has_forbidden_public_marker(value):
            return None
        return redact_memory_text(value, mode=MEMORY_REDACTION_MODE_STRICT)
    return value


def sanitize_public_text(value: object) -> str:
    text = "" if value is None else str(value)
    sanitized = sanitize_public_payload(text)
    return sanitized if isinstance(sanitized, str) else ""
