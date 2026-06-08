import json
import os
import subprocess
import sys

from app.tool_policy_readiness import build_tool_policy_readiness, render_tool_policy_readiness_markdown


def test_tool_policy_readiness_records_allow_ask_deny_taxonomy_without_closing_g6():
    readiness = build_tool_policy_readiness()

    assert readiness["schema_version"] == "ai-platform.tool-policy-readiness.v1"
    assert readiness["gate"] == "G6 Tool Permission Policy Taxonomy"
    assert readiness["status"] == "partial_blocked"
    assert readiness["policy_contract"] == {
        "allow": "active read-only low-risk tools can be auto-allowed",
        "ask": "active medium/high-risk or write-capable tools require a current user decision",
        "deny": "disabled tools, missing tenant policy, expired decisions, or explicit deny fail closed",
    }
    assert readiness["decision_options"] == ["allow_once", "allow_for_run", "deny"]
    assert readiness["risk_levels"] == ["low", "medium", "high"]
    assert readiness["summary"] == {
        "taxonomy_cases": 6,
        "auto_allow_cases": 1,
        "ask_cases": 3,
        "deny_cases": 2,
    }
    assert readiness["taxonomy_cases"] == [
        {
            "id": "active_low_read_only",
            "registry_status": "active",
            "policy_status": "active",
            "risk_level": "low",
            "write_capable": False,
            "classification": "allow",
            "requires_decision": False,
            "expected_reason": "read_only_low_risk_auto_allowed",
        },
        {
            "id": "active_medium_read_only",
            "registry_status": "active",
            "policy_status": "active",
            "risk_level": "medium",
            "write_capable": False,
            "classification": "ask",
            "requires_decision": True,
            "expected_reason": "tool_permission_required",
        },
        {
            "id": "active_high_read_only",
            "registry_status": "active",
            "policy_status": "active",
            "risk_level": "high",
            "write_capable": False,
            "classification": "ask",
            "requires_decision": True,
            "expected_reason": "tool_permission_required",
        },
        {
            "id": "active_low_write_capable",
            "registry_status": "active",
            "policy_status": "active",
            "risk_level": "low",
            "write_capable": True,
            "classification": "ask",
            "requires_decision": True,
            "expected_reason": "tool_permission_required",
        },
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
    assert "tool_allow_deny_ask_policy_taxonomy_for_all_mcp_tools" not in readiness["open_gaps"]
    assert "legacy_frontend_route_policy_enforcement_or_ai_platform_remap" in readiness["open_gaps"]
    assert "admin_policy_change_history_projection" in readiness["implemented_controls"]
    assert "admin_policy_bulk_review_and_change_history_view" not in readiness["open_gaps"]
    assert "admin_policy_bulk_review_and_dashboard_acceptance" in readiness["open_gaps"]


def test_tool_policy_readiness_markdown_is_gap_first_and_secret_safe():
    markdown = render_tool_policy_readiness_markdown(build_tool_policy_readiness())

    assert "# ai-platform Tool Policy Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "active_low_read_only" in markdown
    assert "active_low_write_capable" in markdown
    assert "admin_policy_change_history_projection" in markdown
    assert "token=secret" not in markdown
    assert ".env" not in markdown
    assert "work_dir" not in markdown
    assert "sandbox_workdir" not in markdown


def test_tool_policy_readiness_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["SANDBOX_WORKSPACE_ROOT"] = "/tmp/work_dir/token=secret"
    env["MCP_TOKEN"] = "token=secret"

    result = subprocess.run(
        [sys.executable, "tools/tool_policy_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.tool-policy-readiness.v1"
    assert payload["status"] == "partial_blocked"
    assert payload["summary"]["taxonomy_cases"] == 6
    assert "tool_allow_deny_ask_policy_taxonomy_for_all_mcp_tools" not in payload["open_gaps"]
    assert "token=secret" not in result.stdout
    assert "work_dir" not in result.stdout
    assert ".env" not in result.stdout
