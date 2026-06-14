from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.office-context-pack-readiness.v1"
GATE_NAME = "G6/G9/#22 Office Context Pack Architecture"

_ALLOWED_CONTEXT_SOURCES = [
    "uploaded_source_documents",
    "previous_generated_artifacts",
    "user_instructions",
    "department_templates",
    "terminology_glossary",
    "meeting_notes",
    "accepted_style_preferences",
]

_USER_VISIBLE_PROJECTION = [
    "referenced_materials",
    "used_context_summary",
    "latest_artifact_version",
    "execution_tier",
    "context_pack_generated_at",
]

_FORBIDDEN_PROJECTION_TERMS = [
    "executor_private_payload",
    "raw_storage_key",
    "sandbox_workdir",
    "secret_like_values",
    "absolute_runtime_paths",
]

_EXECUTION_TIERS = [
    {
        "id": "sdk_only_writing",
        "uses_sandbox_by_default": False,
        "task_examples": [
            "rewrite",
            "summarize",
            "translate",
            "proposal_followup",
        ],
        "required_runtime_evidence": [
            "context_pack_prompt_injection",
            "model_latency_metric",
            "user_visible_context_projection",
        ],
    },
    {
        "id": "document_worker",
        "uses_sandbox_by_default": False,
        "task_examples": [
            "docx_generation",
            "pptx_generation",
            "format_conversion",
            "document_comments",
        ],
        "required_runtime_evidence": [
            "artifact_version_linkage",
            "document_processing_latency_metric",
            "cleanup_proof",
        ],
    },
    {
        "id": "heavy_sandbox",
        "uses_sandbox_by_default": True,
        "task_examples": [
            "script_execution",
            "browser_automation",
            "risky_tool_use",
            "complex_multi_tool_workflow",
        ],
        "required_runtime_evidence": [
            "sandbox_lease_policy",
            "cold_start_latency_metric",
            "sandbox_cleanup_orphan_check",
        ],
    },
]

_OPEN_GAPS = [
    "office_context_pack_persistence_and_versioning",
    "executor_context_pack_211_acceptance",
    "user_visible_context_provenance_projection",
    "document_centric_followup_state",
    "office_execution_tier_router",
    "sandbox_cold_start_latency_split_211_acceptance",
    "frontend_context_provenance_acceptance",
]

_IMPLEMENTED_CONTROLS = [
    "source_level_context_pack_contract",
    "context_snapshot_public_provenance_projection_contract",
    "executor_context_pack_prompt_injection_source_tests",
    "sandbox_cold_start_latency_split_source_contract",
]

_NON_GOALS = [
    "do_not_start_docker_sandbox_for_lightweight_writing_by_default",
    "do_not_expose_raw_storage_keys_or_executor_private_payloads",
    "do_not_enable_long_term_cross_session_memory_by_default",
    "do_not_expand_g8_g10_multi_agent_to_ordinary_users",
]

_SANDBOX_LATENCY_OBSERVABILITY = {
    "status": "source_contract_defined_runtime_acceptance_required",
    "applies_to_execution_tiers": ["heavy_sandbox"],
    "required_metric_fields": [
        "sandbox_lease_acquire_latency_ms",
        "sandbox_container_cold_start_latency_ms",
        "sandbox_healthcheck_latency_ms",
        "executor_model_latency_ms",
        "document_processing_latency_ms",
        "sandbox_cleanup_latency_ms",
    ],
    "must_not_hide_cold_start_in_executor_latency": True,
    "runtime_acceptance_required": "211_sandbox_latency_split_smoke",
}


def build_office_context_readiness() -> dict[str, Any]:
    """Build a source-level #22 context-pack baseline without claiming 211 acceptance."""
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "issue": "#22",
        "status": "partial_blocked",
        "policy": {
            "default_office_execution_tier": "sdk_only_writing",
            "lightweight_office_tasks_start_sandbox_by_default": False,
            "ordinary_user_policy": "public_projection_only",
            "long_term_memory_policy": "fail_closed_until_policy_and_acceptance",
            "does_not_expand_multi_agent_beta": True,
        },
        "implemented_controls": list(_IMPLEMENTED_CONTROLS),
        "context_pack_contract": {
            "bounded_summary_required": True,
            "allowed_sources": list(_ALLOWED_CONTEXT_SOURCES),
            "user_visible_projection": list(_USER_VISIBLE_PROJECTION),
            "forbidden_projection_terms": list(_FORBIDDEN_PROJECTION_TERMS),
        },
        "execution_tiers": [dict(tier) for tier in _EXECUTION_TIERS],
        "sandbox_latency_observability": {
            **_SANDBOX_LATENCY_OBSERVABILITY,
            "applies_to_execution_tiers": list(_SANDBOX_LATENCY_OBSERVABILITY["applies_to_execution_tiers"]),
            "required_metric_fields": list(_SANDBOX_LATENCY_OBSERVABILITY["required_metric_fields"]),
        },
        "open_gaps": list(_OPEN_GAPS),
        "non_goals": list(_NON_GOALS),
        "evidence_policy": (
            "This records source-level context-pack contract, executor prompt-injection tests, "
            "and sandbox latency split observability contract; versioned persistence, "
            "frontend acceptance, 211 executor smoke, and 211 sandbox latency split smoke are still required "
            "before office context continuity can close G6/G9."
        ),
    }


def render_office_context_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the office context-pack readiness snapshot for operator review."""
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    source_lines = "\n".join(
        f"- {source}" for source in readiness["context_pack_contract"]["allowed_sources"]
    )
    projection_lines = "\n".join(
        f"- {field}" for field in readiness["context_pack_contract"]["user_visible_projection"]
    )
    forbidden_lines = "\n".join(
        f"- {field}" for field in readiness["context_pack_contract"]["forbidden_projection_terms"]
    )
    implemented_lines = "\n".join(f"- {item}" for item in readiness["implemented_controls"])
    tier_lines = []
    for tier in readiness["execution_tiers"]:
        examples = ", ".join(tier["task_examples"])
        tier_lines.append(
            f"- `{tier['id']}`: sandbox by default `{str(tier['uses_sandbox_by_default']).lower()}`, "
            f"examples `{examples}`"
        )
    non_goal_lines = "\n".join(f"- {item}" for item in readiness["non_goals"])
    return (
        "# ai-platform Office Context Pack Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Issue: `{readiness['issue']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        "## Implemented Controls\n\n"
        f"{implemented_lines}\n\n"
        "## Context Pack Contract\n\n"
        f"Bounded summary required: `{str(readiness['context_pack_contract']['bounded_summary_required']).lower()}`\n\n"
        "Allowed sources:\n\n"
        f"{source_lines}\n\n"
        "User-visible projection:\n\n"
        f"{projection_lines}\n\n"
        "Forbidden projection terms:\n\n"
        f"{forbidden_lines}\n\n"
        "## Execution Tiers\n\n"
        + "\n".join(tier_lines)
        + "\n\n"
        "## Non-goals\n\n"
        f"{non_goal_lines}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )
