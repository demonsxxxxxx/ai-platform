from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.tool-policy-bulk-review-readiness.v1"
DASHBOARD_CONTRACT_SCHEMA = "ai-platform.tool-policy-bulk-review-dashboard-contract.v1"

_OPEN_GAPS = [
    "admin_policy_bulk_review_visual_acceptance",
    "admin_policy_bulk_review_211_acceptance",
]

_SOURCE_ROUTES = [
    "GET /api/ai/admin/tool-policies",
    "GET /api/ai/admin/tool-policies/history",
    "PUT /api/ai/admin/tool-policies/{tool_id}",
]

_FORBIDDEN_PAYLOAD_CLASSES = [
    "executor private runtime payloads",
    "raw object-storage keys",
    "sandbox working directories",
    "secret material and credentials",
    "raw registry endpoints or auth modes",
    "tool request bodies or shell command payloads",
]

_RUNTIME_ACCEPTANCE_SOURCE_TESTS = [
    "tests/test_admin_tool_policies.py::test_admin_list_tool_policies_requires_admin",
    "tests/test_admin_tool_policies.py::test_admin_list_tool_policies_returns_same_tenant_operational_projection",
    "tests/test_admin_tool_policies.py::test_admin_tool_policy_history_requires_admin",
    "tests/test_admin_tool_policies.py::test_admin_tool_policy_history_returns_bounded_same_tenant_secret_safe_projection",
    "tests/test_admin_tool_policies.py::test_admin_tool_policy_history_drops_dirty_scalars_and_nested_allowed_payloads",
    "tests/test_admin_tool_policies.py::test_admin_update_tool_policy_audits_and_keeps_risky_tools_fail_closed",
    "tests/test_admin_tool_policies.py::test_admin_update_tool_policy_returns_404_for_missing_tool",
]

_RUNTIME_ACCEPTANCE_CONTROLS = [
    "ordinary_user_denied_inventory",
    "same_tenant_admin_inventory_projection",
    "ordinary_user_denied_history",
    "same_tenant_admin_history_projection",
    "dirty_history_payload_sanitized",
    "admin_update_audited",
    "risky_or_write_policy_requires_decision",
    "missing_tool_update_fails_closed",
]


def _runtime_acceptance() -> dict[str, Any]:
    return {
        "schema_version": "ai-platform.tool-policy-bulk-review-runtime-acceptance.v1",
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


def build_tool_policy_bulk_review_readiness() -> dict[str, Any]:
    """Build a contract-only G6 admin tool-policy bulk-review readiness snapshot."""
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
                "bounded_tool_policy_inventory",
                "bounded_tool_policy_change_history",
                "tool_policy_taxonomy_summary",
                "decision_options",
                "legacy_route_policy_gap_summary",
            ],
            "allowed_policy_fields": [
                "tool_id",
                "server_id",
                "name",
                "description",
                "registry_status",
                "policy_status",
                "effective_status",
                "write_capable",
                "risk_level",
                "visible_to_user",
                "source",
                "requires_decision",
                "reason",
                "updated_by",
                "updated_at",
            ],
            "allowed_history_fields": [
                "audit_id",
                "action",
                "tool_id",
                "updated_by",
                "trace_id",
                "schema_version",
                "created_at",
                "payload.tool_id",
                "payload.status",
                "payload.risk_level",
                "payload.write_capable",
                "payload.visible_to_user",
                "payload.reason",
            ],
            "required_dashboard_controls": [
                "risk_level_filter",
                "write_capable_filter",
                "requires_decision_filter",
                "effective_status_filter",
                "per_tool_policy_diff_preview",
                "single_tool_update_confirmation",
                "change_history_drilldown",
            ],
        },
        "runtime_acceptance": _runtime_acceptance(),
        "forbidden_payload_classes": list(_FORBIDDEN_PAYLOAD_CLASSES),
        "open_gaps": list(_OPEN_GAPS),
    }


def render_tool_policy_bulk_review_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the bulk-review contract as operator-readable Markdown."""
    contract = readiness["dashboard_contract"]
    runtime_acceptance = readiness["runtime_acceptance"]
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    controls = "\n".join(f"- {item}" for item in contract["required_dashboard_controls"])
    inputs = "\n".join(f"- {item}" for item in contract["required_inputs"])
    source_tests = "\n".join(f"- {item}" for item in runtime_acceptance["source_tests"])
    return (
        "# ai-platform Tool Policy Bulk Review Readiness\n\n"
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
        "does not add a batch mutation API, and does not expose private runtime payloads.\n"
    )
