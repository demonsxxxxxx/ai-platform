from __future__ import annotations

from typing import Any

from app.tool_policy_bulk_review_readiness import build_tool_policy_bulk_review_readiness
from app.tool_policy import RISK_ORDER, evaluate_tool_policy


SCHEMA_VERSION = "ai-platform.tool-policy-readiness.v1"
GATE_NAME = "G6 Tool Permission Policy Taxonomy"


def _active_case(*, case_id: str, risk_level: str, write_capable: bool, classification: str) -> dict[str, object]:
    decision = evaluate_tool_policy(tool={"risk_level": risk_level, "write_capable": write_capable})
    return {
        "id": case_id,
        "registry_status": "active",
        "policy_status": "active",
        "risk_level": risk_level,
        "write_capable": write_capable,
        "classification": classification,
        "requires_decision": not decision.allowed and decision.reason == "tool_permission_required",
        "expected_reason": decision.reason,
    }


def _taxonomy_cases() -> list[dict[str, object]]:
    return [
        _active_case(case_id="active_low_read_only", risk_level="low", write_capable=False, classification="allow"),
        _active_case(case_id="active_medium_read_only", risk_level="medium", write_capable=False, classification="ask"),
        _active_case(case_id="active_high_read_only", risk_level="high", write_capable=False, classification="ask"),
        _active_case(case_id="active_low_write_capable", risk_level="low", write_capable=True, classification="ask"),
        {
            "id": "disabled_registry",
            "registry_status": "disabled",
            "policy_status": "active",
            "risk_level": "low",
            "write_capable": False,
            "classification": "deny",
            "requires_decision": False,
            "expected_reason": "mcp_tool_not_active",
        },
        {
            "id": "disabled_tenant_policy",
            "registry_status": "active",
            "policy_status": "disabled",
            "risk_level": "low",
            "write_capable": False,
            "classification": "deny",
            "requires_decision": False,
            "expected_reason": "mcp_tool_not_active",
        },
    ]


_EXPECTED_TAXONOMY_CASE_IDS = (
    "active_low_read_only",
    "active_medium_read_only",
    "active_high_read_only",
    "active_low_write_capable",
    "disabled_registry",
    "disabled_tenant_policy",
)


def _validate_taxonomy_cases(cases: list[dict[str, object]]) -> None:
    case_ids = tuple(str(item.get("id") or "") for item in cases)
    if case_ids != _EXPECTED_TAXONOMY_CASE_IDS:
        raise RuntimeError("tool_policy_taxonomy_case_drift")
    classifications = {str(item.get("classification") or "") for item in cases}
    if classifications != {"allow", "ask", "deny"}:
        raise RuntimeError("tool_policy_taxonomy_classification_drift")


_DECISION_OPTIONS = ["allow_once", "allow_for_run", "deny"]


def build_tool_policy_readiness() -> dict[str, Any]:
    """Build a secret-safe offline G6 tool permission taxonomy readiness snapshot."""
    taxonomy_cases = _taxonomy_cases()
    _validate_taxonomy_cases(taxonomy_cases)
    bulk_review_readiness = build_tool_policy_bulk_review_readiness()
    implemented_controls = [
        "tool_allow_deny_ask_policy_taxonomy_for_all_mcp_tools",
        "platform_registered_mcp_only_policy",
        "ordinary_user_custom_mcp_disabled",
        "admin_policy_change_history_projection",
        "admin_policy_bulk_review_dashboard_contract",
        "exact_tool_permission_decision_lookup_source_tests",
    ]
    open_gaps = [
        "legacy_frontend_route_policy_enforcement_or_ai_platform_remap",
        *bulk_review_readiness["open_gaps"],
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked",
        "policy_contract": {
            "allow": "active read-only low-risk tools can be auto-allowed",
            "ask": "active medium/high-risk or write-capable tools require a current user decision",
            "deny": "disabled tools, missing tenant policy, expired decisions, or explicit deny fail closed",
        },
        "registry_contract": {
            "registry_source": "platform_registered_mcp_tools_only",
            "ordinary_user_custom_mcp": "not_allowed",
            "unregistered_tool_behavior": "deny",
            "tenant_policy_scope": "same_tenant_registered_tools_only",
        },
        "decision_options": list(_DECISION_OPTIONS),
        "risk_levels": list(RISK_ORDER),
        "taxonomy_cases": taxonomy_cases,
        "summary": {
            "taxonomy_cases": len(taxonomy_cases),
            "auto_allow_cases": sum(1 for item in taxonomy_cases if item["classification"] == "allow"),
            "ask_cases": sum(1 for item in taxonomy_cases if item["classification"] == "ask"),
            "deny_cases": sum(1 for item in taxonomy_cases if item["classification"] == "deny"),
        },
        "implemented_controls": implemented_controls,
        "open_gaps": open_gaps,
        "evidence": {
            "admin_policy_bulk_review_dashboard": {
                "schema_version": bulk_review_readiness["schema_version"],
                "status": bulk_review_readiness["status"],
                "policy": bulk_review_readiness["policy"],
                "dashboard_contract": bulk_review_readiness["dashboard_contract"],
                "open_gaps": bulk_review_readiness["open_gaps"],
                "does_not_close_g6": bulk_review_readiness["does_not_close_g6"],
            }
        },
        "evidence_policy": (
            "taxonomy, change-history, and exact-decision lookup source tests document policy behavior only; "
            "legacy route enforcement, admin UX acceptance, and 211 smoke are still required before G6 closure"
        ),
    }


def render_tool_policy_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the tool policy taxonomy readiness snapshot as operator-readable Markdown."""
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    evidence = readiness.get("evidence", {})
    bulk_review = evidence.get("admin_policy_bulk_review_dashboard") if isinstance(evidence, dict) else None
    bulk_contract_lines = ""
    if isinstance(bulk_review, dict):
        contract = bulk_review.get("dashboard_contract")
        if isinstance(contract, dict):
            bulk_contract_lines = (
                "## Admin Bulk Review Dashboard Contract\n\n"
                f"Schema: `{contract.get('schema_version')}`\n\n"
                f"Policy: `{bulk_review.get('policy')}`\n\n"
            )
    case_lines = "\n".join(
        "- "
        f"`{item['id']}`: `{item['classification']}`, risk `{item['risk_level']}`, "
        f"write `{item['write_capable']}`, decision `{item['requires_decision']}`, "
        f"reason `{item['expected_reason']}`"
        for item in readiness["taxonomy_cases"]
    )
    contract_lines = "\n".join(
        f"- `{name}`: {description}" for name, description in readiness["policy_contract"].items()
    )
    implemented_lines = "\n".join(f"- {item}" for item in readiness.get("implemented_controls", [])) or "- none"
    return (
        "# ai-platform Tool Policy Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        "## Implemented Controls\n\n"
        f"{implemented_lines}\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        f"{bulk_contract_lines}"
        "## Policy Contract\n\n"
        f"{contract_lines}\n\n"
        "## Taxonomy Cases\n\n"
        f"{case_lines}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )
