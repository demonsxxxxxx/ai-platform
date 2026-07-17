from __future__ import annotations

from typing import Any

from app.tool_policy import RISK_ORDER, evaluate_tool_policy


SCHEMA_VERSION = "ai-platform.tool-policy-readiness.v2"
GATE_NAME = "G6 Zero-click Tool Policy"


def _active_case(*, case_id: str, risk_level: str, write_capable: bool) -> dict[str, object]:
    identity = "Write" if write_capable else "Read"
    decision = evaluate_tool_policy(
        tool={
            "requested_identity": identity,
            "declared_identities": [identity],
            "registered": True,
            "declared": True,
            "active": True,
            "distributed": True,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": risk_level,
            "write_capable": write_capable,
        }
    )
    return {
        "id": case_id,
        "registry_status": "active",
        "policy_status": "active",
        "risk_level": risk_level,
        "write_capable": write_capable,
        "classification": decision.outcome,
        "expected_reason": decision.reason,
    }


def _taxonomy_cases() -> list[dict[str, object]]:
    return [
        _active_case(case_id="active_low_read_only", risk_level="low", write_capable=False),
        _active_case(case_id="active_medium_read_only", risk_level="medium", write_capable=False),
        _active_case(case_id="active_high_read_only", risk_level="high", write_capable=False),
        _active_case(case_id="active_low_write_capable", risk_level="low", write_capable=True),
        {
            "id": "disabled_registry",
            "registry_status": "disabled",
            "policy_status": "active",
            "risk_level": "low",
            "write_capable": False,
            "classification": "deny",
            "expected_reason": "tool_not_active",
        },
        {
            "id": "disabled_tenant_policy",
            "registry_status": "active",
            "policy_status": "disabled",
            "risk_level": "low",
            "write_capable": False,
            "classification": "deny",
            "expected_reason": "tool_not_active",
        },
    ]


def build_tool_policy_readiness() -> dict[str, Any]:
    """Build a secret-safe offline zero-click policy readiness snapshot."""

    taxonomy_cases = _taxonomy_cases()
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked",
        "policy_contract": {
            "allow": "exact registered, declared, active and distributed tools execute immediately",
            "deny": "all malformed, unknown, inactive, undistributed or unauthorized attempts fail closed",
        },
        "registry_contract": {
            "registry_source": "platform_registered_mcp_tools_only",
            "ordinary_user_custom_mcp": "not_allowed",
            "unregistered_tool_behavior": "deny",
            "tenant_policy_scope": "same_tenant_registered_tools_only",
        },
        "decision_options": [],
        "risk_levels": list(RISK_ORDER),
        "taxonomy_cases": taxonomy_cases,
        "summary": {
            "taxonomy_cases": len(taxonomy_cases),
            "auto_allow_cases": sum(1 for item in taxonomy_cases if item["classification"] == "allow"),
            "ask_cases": 0,
            "deny_cases": sum(1 for item in taxonomy_cases if item["classification"] == "deny"),
        },
        "implemented_controls": [
            "single_allow_deny_runtime_tool_policy",
            "platform_registered_mcp_only_policy",
            "ordinary_user_custom_mcp_disabled",
            "historical_permission_evidence_read_only",
        ],
        "open_gaps": [
            "legacy_frontend_route_policy_enforcement_or_ai_platform_remap",
        ],
        "evidence": {},
        "evidence_policy": (
            "readiness records local policy behavior only; it does not replace independent review, "
            "CI, or deployed-runtime evidence"
        ),
    }


def render_tool_policy_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the operator-readable readiness snapshot."""

    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"]) or "- none"
    case_lines = "\n".join(
        "- "
        f"`{item['id']}`: `{item['classification']}`, risk `{item['risk_level']}`, "
        f"write `{item['write_capable']}`, reason `{item['expected_reason']}`"
        for item in readiness["taxonomy_cases"]
    )
    contract_lines = "\n".join(
        f"- `{name}`: {description}" for name, description in readiness["policy_contract"].items()
    )
    return (
        "# ai-platform Tool Policy Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        "## Policy Contract\n\n"
        f"{contract_lines}\n\n"
        "## Taxonomy Cases\n\n"
        f"{case_lines}\n"
    )
