from __future__ import annotations

import re
from typing import Any


ERROR_TAXONOMY_SCHEMA_VERSION = "ai-platform.error-taxonomy.v1"
UNKNOWN_ERROR_CATEGORY = "unknown"

ERROR_CATEGORY_DEFINITIONS: dict[str, str] = {
    "executor": "Executor or worker runtime failed outside a narrower queue/tool/sandbox/model category.",
    "tool": "Tool or MCP invocation failed before or during execution.",
    "tool_permission": "Tool permission, allow/deny/ask policy, or risky write-capable gate blocked execution.",
    "sandbox": "Sandbox lease, workspace, container, cleanup, or provider behavior failed.",
    "model_gateway": "Model gateway, upstream LLM provider, timeout, or retry behavior failed.",
    "queue": "Redis queue, lease, enqueue, dead-letter, or active worker capacity behavior failed.",
    "database": "Database, transaction, pool, or Postgres behavior failed.",
    "memory_context": "Memory, context-pack, retention, redaction, or erasure behavior failed.",
    "artifact": "Artifact, file, object-storage, preview, or download behavior failed.",
    "auth_policy": "Auth, RBAC, tenant, workspace, or access policy failed.",
    UNKNOWN_ERROR_CATEGORY: "The error code did not match a stable ai-platform category.",
}

_CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "tool_permission",
        (
            "tool_permission",
            "permission_denied",
            "permission_required",
            "mcp_tool_denied",
            "write_policy",
            "tool_gate",
        ),
    ),
    (
        "model_gateway",
        (
            "model_gateway",
            "llm_gateway",
            "new_api",
            "openai",
            "anthropic",
            "model_timeout",
            "upstream_model",
        ),
    ),
    (
        "sandbox",
        (
            "sandbox",
            "container",
            "workspace_lease",
            "orphan_container",
            "provider_cleanup",
        ),
    ),
    (
        "queue",
        (
            "queue",
            "redis",
            "lease_timeout",
            "dead_letter",
            "enqueue",
            "worker_capacity",
            "worker_busy",
        ),
    ),
    (
        "database",
        (
            "database",
            "postgres",
            "db_pool",
            "pool_waiting",
            "transaction",
        ),
    ),
    (
        "memory_context",
        (
            "memory",
            "context",
            "retention",
            "redaction",
            "erasure",
            "context_pack",
        ),
    ),
    (
        "artifact",
        (
            "artifact",
            "storage",
            "object_storage",
            "minio",
            "preview",
            "download",
            "file_write",
        ),
    ),
    (
        "auth_policy",
        (
            "not_ai_admin",
            "auth",
            "rbac",
            "tenant_forbidden",
            "workspace_forbidden",
            "policy",
        ),
    ),
    (
        "tool",
        (
            "mcp_tool",
            "tool",
            "tool_call",
            "toolcall",
        ),
    ),
    (
        "executor",
        (
            "executor",
            "worker_process",
            "claude_agent_worker",
            "runtime211",
            "agent_step_failed",
            "agent_step_blocked",
        ),
    ),
)


def _normalize_error_code(error_code: object) -> str:
    text = str(error_code or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def classify_error_code(error_code: object) -> str:
    """Classify an error code into a stable, public G9 category."""
    normalized = _normalize_error_code(error_code)
    if not normalized:
        return UNKNOWN_ERROR_CATEGORY
    for category, patterns in _CATEGORY_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return category
    return UNKNOWN_ERROR_CATEGORY


def summarize_error_categories(error_counts: object) -> dict[str, int]:
    """Aggregate raw error-code counts into public category counts."""
    source = error_counts if isinstance(error_counts, dict) else {}
    categories: dict[str, int] = {}
    for error_code, count in source.items():
        if isinstance(count, bool) or not isinstance(count, int | float):
            continue
        numeric_count = int(count)
        if numeric_count <= 0:
            continue
        category = classify_error_code(error_code)
        categories[category] = categories.get(category, 0) + numeric_count
    return categories


def build_error_taxonomy_contract() -> dict[str, Any]:
    """Return the public G9 error taxonomy contract without runtime error payloads."""
    categories = list(ERROR_CATEGORY_DEFINITIONS)
    return {
        "schema_version": ERROR_TAXONOMY_SCHEMA_VERSION,
        "categories": categories,
        "category_count": len(categories),
        "category_definitions": ERROR_CATEGORY_DEFINITIONS,
        "unknown_category": UNKNOWN_ERROR_CATEGORY,
        "mapping_policy": "best_effort_error_code_pattern_mapping",
        "projection_policy": "category_counts_only_no_raw_error_payload",
    }
