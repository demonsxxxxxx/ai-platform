from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.skill-release-dashboard-readiness.v1"
DASHBOARD_CONTRACT_SCHEMA = "ai-platform.skill-release-dashboard-contract.v1"

_OPEN_GAPS = [
    "admin_skill_release_dashboard_runtime_acceptance",
    "admin_skill_release_dashboard_visual_acceptance",
    "admin_skill_release_dashboard_211_acceptance",
]


def build_skill_release_dashboard_readiness() -> dict[str, Any]:
    """Build a contract-only G6 Admin Skill release dashboard readiness snapshot."""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "partial_blocked",
        "policy": "contract_only_not_runtime_dashboard_acceptance",
        "does_not_close_g6": True,
        "dashboard_contract": {
            "schema_version": DASHBOARD_CONTRACT_SCHEMA,
            "admin_only": True,
            "same_tenant_only": True,
            "source_routes": [
                "GET /api/ai/admin/skills/{skill_id}",
                "POST /api/ai/admin/skills/sync-builtin",
                "POST /api/ai/admin/skills/{skill_id}/versions/upload",
                "GET /api/ai/admin/skills/{skill_id}/versions/diff",
                "POST /api/ai/admin/skills/{skill_id}/promote",
                "POST /api/ai/admin/skills/{skill_id}/rollback",
            ],
            "required_inputs": [
                "skill_inventory_summary",
                "skill_dependency_policy",
                "release_review_evidence_summary",
                "version_diff_summary",
                "promote_rollback_policy",
                "runtime_materialization_status",
            ],
            "allowed_dashboard_fields": [
                "skill_id",
                "name",
                "description",
                "current_version",
                "previous_version",
                "rollout_percent",
                "status",
                "dependency_ids",
                "dependency_policy_status",
                "release_review_status",
                "blockers",
                "created_by",
                "created_at",
            ],
            "required_dashboard_controls": [
                "skill_filter",
                "dependency_policy_filter",
                "release_review_status_filter",
                "version_diff_preview",
                "promote_confirmation",
                "rollback_confirmation",
                "review_evidence_drilldown",
            ],
        },
        "forbidden_payload_classes": [
            "raw skill package storage keys",
            "package hashes used as private object metadata",
            "base64 file contents",
            "executor private runtime payloads",
            "sandbox working directories",
            "secret material and credentials",
            "raw staging paths",
        ],
        "open_gaps": list(_OPEN_GAPS),
    }


def render_skill_release_dashboard_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the Admin Skill release dashboard contract as operator-readable Markdown."""
    contract = readiness["dashboard_contract"]
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    controls = "\n".join(f"- {item}" for item in contract["required_dashboard_controls"])
    inputs = "\n".join(f"- {item}" for item in contract["required_inputs"])
    return (
        "# ai-platform Skill Release Dashboard Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Policy: `{readiness['policy']}`\n\n"
        f"Dashboard contract: `{contract['schema_version']}`\n\n"
        "## Required Inputs\n\n"
        f"{inputs}\n\n"
        "## Required Dashboard Controls\n\n"
        f"{controls}\n\n"
        "## Open Gaps\n\n"
        f"{gaps}\n\n"
        "This is a contract-only readiness snapshot. It does not close G6, "
        "does not expose raw Skill package internals, and does not grant ordinary "
        "users raw Skill selection or staging access.\n"
    )
