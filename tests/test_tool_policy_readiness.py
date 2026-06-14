import json
import os
import subprocess
import sys

from app.tool_policy_bulk_review_readiness import build_tool_policy_bulk_review_readiness
from app.tool_policy_readiness import build_tool_policy_readiness, render_tool_policy_readiness_markdown


def test_tool_policy_bulk_review_readiness_contract_is_safe_and_does_not_close_g6():
    readiness = build_tool_policy_bulk_review_readiness()

    assert readiness["schema_version"] == "ai-platform.tool-policy-bulk-review-readiness.v1"
    assert readiness["status"] == "partial_blocked"
    assert readiness["policy"] == "contract_only_not_runtime_dashboard_acceptance"
    assert readiness["does_not_close_g6"] is True
    assert readiness["dashboard_contract"] == {
        "schema_version": "ai-platform.tool-policy-bulk-review-dashboard-contract.v1",
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
    }
    assert readiness["open_gaps"] == [
        "admin_policy_bulk_review_runtime_acceptance",
        "admin_policy_bulk_review_visual_acceptance",
        "admin_policy_bulk_review_211_acceptance",
    ]
    assert "admin_policy_bulk_review_and_dashboard_acceptance" not in readiness["open_gaps"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "database_url" not in serialized
    assert "redis_url" not in serialized
    assert "bearer" not in serialized


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
    assert readiness["registry_contract"] == {
        "registry_source": "platform_registered_mcp_tools_only",
        "ordinary_user_custom_mcp": "not_allowed",
        "unregistered_tool_behavior": "deny",
        "tenant_policy_scope": "same_tenant_registered_tools_only",
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
    assert "admin_policy_bulk_review_dashboard_contract" in readiness["implemented_controls"]
    assert "exact_tool_permission_decision_lookup_source_tests" in readiness["implemented_controls"]
    assert "platform_registered_mcp_only_policy" in readiness["implemented_controls"]
    assert "ordinary_user_custom_mcp_disabled" in readiness["implemented_controls"]
    assert "admin_policy_bulk_review_and_dashboard_acceptance" not in readiness["open_gaps"]
    assert "tool_policy_taxonomy_admin_dashboard_acceptance" not in readiness["open_gaps"]
    assert readiness["evidence"]["admin_policy_bulk_review_dashboard"]["schema_version"] == (
        "ai-platform.tool-policy-bulk-review-readiness.v1"
    )
    assert readiness["evidence"]["admin_policy_bulk_review_dashboard"]["dashboard_contract"][
        "schema_version"
    ] == "ai-platform.tool-policy-bulk-review-dashboard-contract.v1"
    assert readiness["evidence"]["admin_policy_bulk_review_dashboard"]["open_gaps"] == [
        "admin_policy_bulk_review_runtime_acceptance",
        "admin_policy_bulk_review_visual_acceptance",
        "admin_policy_bulk_review_211_acceptance",
    ]
    assert "admin_policy_bulk_review_runtime_acceptance" in readiness["open_gaps"]
    assert "admin_policy_bulk_review_visual_acceptance" in readiness["open_gaps"]
    assert "admin_policy_bulk_review_211_acceptance" in readiness["open_gaps"]


def test_tool_policy_readiness_markdown_is_gap_first_and_secret_safe():
    markdown = render_tool_policy_readiness_markdown(build_tool_policy_readiness())

    assert "# ai-platform Tool Policy Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "active_low_read_only" in markdown
    assert "active_low_write_capable" in markdown
    assert "admin_policy_change_history_projection" in markdown
    assert "admin_policy_bulk_review_dashboard_contract" in markdown
    assert "exact_tool_permission_decision_lookup_source_tests" in markdown
    assert "admin_policy_bulk_review_runtime_acceptance" in markdown
    assert "ai-platform.tool-policy-bulk-review-dashboard-contract.v1" in markdown
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
    assert "admin_policy_bulk_review_dashboard_contract" in payload["implemented_controls"]
    assert "exact_tool_permission_decision_lookup_source_tests" in payload["implemented_controls"]
    assert "admin_policy_bulk_review_runtime_acceptance" in payload["open_gaps"]
    assert "token=secret" not in result.stdout
    assert "work_dir" not in result.stdout
    assert ".env" not in result.stdout


def test_tool_policy_bulk_review_readiness_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://user:secret@db.internal/ai_platform"
    env["REDIS_URL"] = "redis://:redis-secret@redis.internal:6379/0"

    result = subprocess.run(
        [sys.executable, "tools/tool_policy_bulk_review_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.tool-policy-bulk-review-readiness.v1"
    assert payload["status"] == "partial_blocked"
    assert payload["open_gaps"] == [
        "admin_policy_bulk_review_runtime_acceptance",
        "admin_policy_bulk_review_visual_acceptance",
        "admin_policy_bulk_review_211_acceptance",
    ]
    assert "postgresql://" not in result.stdout
    assert "redis-secret" not in result.stdout
    assert "database_url" not in result.stdout.lower()
