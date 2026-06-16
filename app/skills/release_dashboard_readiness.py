from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.skill-release-dashboard-readiness.v1"
DASHBOARD_CONTRACT_SCHEMA = "ai-platform.skill-release-dashboard-contract.v1"

_OPEN_GAPS = [
    "admin_skill_release_dashboard_visual_acceptance",
    "admin_skill_release_dashboard_211_acceptance",
]

_SOURCE_ROUTES = [
    "GET /api/ai/admin/skills/{skill_id}",
    "POST /api/ai/admin/skills/sync-builtin",
    "POST /api/ai/admin/skills/{skill_id}/versions/upload",
    "GET /api/ai/admin/skills/{skill_id}/versions/diff",
    "POST /api/ai/admin/skills/{skill_id}/promote",
    "POST /api/ai/admin/skills/{skill_id}/rollback",
]

_FORBIDDEN_PAYLOAD_CLASSES = [
    "raw skill package storage keys",
    "package hashes used as private object metadata",
    "base64 file contents",
    "executor private runtime payloads",
    "sandbox working directories",
    "secret material and credentials",
    "raw staging paths",
]

_RUNTIME_ACCEPTANCE_SOURCE_TESTS = [
    "tests/test_admin_skills.py::test_admin_skill_detail_requires_admin",
    "tests/test_admin_skills.py::test_admin_skill_detail_returns_skill_versions_and_snapshots",
    "tests/test_admin_skills.py::test_admin_sync_builtin_skills_records_registry_versions_dependencies_and_snapshots",
    "tests/test_admin_skills.py::test_admin_sync_builtin_skills_rejects_dependency_policy_violation",
    "tests/test_admin_skills.py::test_admin_upload_skill_package_requires_admin",
    "tests/test_admin_skills.py::test_admin_upload_skill_package_rejects_missing_internal_dependency",
    "tests/test_admin_skills.py::test_admin_upload_skill_package_stores_object_and_upserts_skill_version",
    "tests/test_admin_skills.py::test_admin_upload_skill_package_rejects_unknown_skill_before_storage",
    "tests/test_admin_skills.py::test_admin_skill_release_routes_require_admin",
    "tests/test_admin_skills.py::test_admin_skill_version_diff_returns_manifest_changes",
    "tests/test_admin_skills.py::test_admin_promote_skill_version_sets_release_policy_and_audit",
    "tests/test_admin_skills.py::test_admin_promote_rejects_inactive_skill_version",
    "tests/test_admin_skills.py::test_admin_promote_rejects_builtin_version_that_cannot_be_materialized",
    "tests/test_admin_skills.py::test_admin_rollback_skill_version_sets_release_policy_and_audit",
    "tests/test_admin_skills.py::test_admin_rollback_requires_existing_policy",
    "tests/test_admin_skills.py::test_admin_rollback_missing_version_returns_404",
]

_RUNTIME_ACCEPTANCE_CONTROLS = [
    "ordinary_user_denied_detail",
    "same_tenant_admin_detail_projection",
    "sync_builtin_dependency_policy_enforced",
    "upload_admin_only_and_dependency_policy_enforced",
    "version_diff_admin_only_projection",
    "promote_policy_and_audit_controls",
    "rollback_policy_and_audit_controls",
    "materialization_fail_closed",
]


def _runtime_acceptance() -> dict[str, Any]:
    return {
        "schema_version": "ai-platform.skill-release-dashboard-runtime-acceptance.v1",
        "status": "source_route_tests_recorded",
        "evidence_strength": "source_route_tests",
        "source_routes": list(_SOURCE_ROUTES),
        "covered_runtime_controls": list(_RUNTIME_ACCEPTANCE_CONTROLS),
        "source_tests": list(_RUNTIME_ACCEPTANCE_SOURCE_TESTS),
        "forbidden_payload_classes": list(_FORBIDDEN_PAYLOAD_CLASSES),
        "does_not_close_g6": True,
        "does_not_close_visual_acceptance": True,
        "does_not_close_211_acceptance": True,
    }


def build_skill_release_dashboard_readiness() -> dict[str, Any]:
    """Build a contract-only G6 Admin Skill release dashboard readiness snapshot."""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "partial_blocked",
        "policy": "source_runtime_acceptance_recorded_not_visual_or_211",
        "does_not_close_g6": True,
        "dashboard_contract": {
            "schema_version": DASHBOARD_CONTRACT_SCHEMA,
            "admin_only": True,
            "same_tenant_only": True,
            "source_routes": list(_SOURCE_ROUTES),
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
        "runtime_acceptance": _runtime_acceptance(),
        "forbidden_payload_classes": list(_FORBIDDEN_PAYLOAD_CLASSES),
        "open_gaps": list(_OPEN_GAPS),
    }


def render_skill_release_dashboard_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the Admin Skill release dashboard contract as operator-readable Markdown."""
    contract = readiness["dashboard_contract"]
    runtime_acceptance = readiness["runtime_acceptance"]
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    controls = "\n".join(f"- {item}" for item in contract["required_dashboard_controls"])
    inputs = "\n".join(f"- {item}" for item in contract["required_inputs"])
    source_tests = "\n".join(f"- {item}" for item in runtime_acceptance["source_tests"])
    return (
        "# ai-platform Skill Release Dashboard Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Policy: `{readiness['policy']}`\n\n"
        f"Dashboard contract: `{contract['schema_version']}`\n\n"
        "## Runtime Acceptance\n\n"
        f"Schema: `{runtime_acceptance['schema_version']}`\n\n"
        f"Status: `{runtime_acceptance['status']}`\n\n"
        f"Evidence strength: `{runtime_acceptance['evidence_strength']}`\n\n"
        f"Does not close 211 acceptance: `{runtime_acceptance['does_not_close_211_acceptance']}`\n\n"
        "Source tests:\n\n"
        f"{source_tests}\n\n"
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
