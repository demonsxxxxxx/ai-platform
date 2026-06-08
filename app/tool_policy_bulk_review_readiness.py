from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.tool-policy-bulk-review-readiness.v1"
DASHBOARD_CONTRACT_SCHEMA = "ai-platform.tool-policy-bulk-review-dashboard-contract.v1"

_OPEN_GAPS = [
    "admin_policy_bulk_review_runtime_acceptance",
    "admin_policy_bulk_review_visual_acceptance",
    "admin_policy_bulk_review_211_acceptance",
]


def build_tool_policy_bulk_review_readiness() -> dict[str, Any]:
    """Build a contract-only G6 admin tool-policy bulk-review readiness snapshot."""
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
                "GET /api/ai/admin/tool-policies",
                "GET /api/ai/admin/tool-policies/history",
                "PUT /api/ai/admin/tool-policies/{tool_id}",
            ],
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
        "forbidden_payload_classes": [
            "executor private runtime payloads",
            "raw object-storage keys",
            "sandbox working directories",
            "secret material and credentials",
            "raw registry endpoints or auth modes",
            "tool request bodies or shell command payloads",
        ],
        "open_gaps": list(_OPEN_GAPS),
    }


def render_tool_policy_bulk_review_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the bulk-review contract as operator-readable Markdown."""
    contract = readiness["dashboard_contract"]
    gaps = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    controls = "\n".join(f"- {item}" for item in contract["required_dashboard_controls"])
    inputs = "\n".join(f"- {item}" for item in contract["required_inputs"])
    return (
        "# ai-platform Tool Policy Bulk Review Readiness\n\n"
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
        "does not add a batch mutation API, and does not expose private runtime payloads.\n"
    )
