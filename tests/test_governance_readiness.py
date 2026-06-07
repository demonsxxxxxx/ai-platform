import json
import os
import subprocess
import sys

from app.governance_readiness import build_governance_readiness, render_governance_readiness_markdown


class SecretBearingSettings:
    sandbox_container_provider = "docker://token@internal/path"
    sandbox_callback_token = "callback-secret"
    sandbox_workspace_root = "/tmp/tenant-secret/workspaces"
    skill_staging_subdir = ".claude/skills"
    claude_agent_allowed_tools = "Read,Glob,LS"
    claude_agent_disallowed_tools = "Write,Edit,NotebookEdit"
    claude_agent_permission_mode = "dontAsk"
    memory_retention_worker_cleanup_enabled = True
    memory_retention_worker_cleanup_limit = 200
    multi_agent_dispatch_worker_enabled = False


def test_governance_readiness_records_g6_domains_and_open_gaps_without_secrets():
    readiness = build_governance_readiness(SecretBearingSettings())

    assert readiness["schema_version"] == "ai-platform.governance-readiness.v1"
    assert readiness["gate"] == "G6 Tool / Skill / Memory Governance"
    assert readiness["status"] == "partial_blocked"
    assert readiness["admin_runtime_projection"] == "/api/ai/admin/runtime/overview"
    assert readiness["ordinary_user_policy"] == "fail_closed_until_projection_mapping_and_acceptance_pass"

    domains = readiness["domains"]
    assert set(domains) == {
        "tool_permission",
        "skill_governance",
        "memory_governance",
        "frontend_projection",
    }
    assert "admin_tool_policy_inventory" in domains["tool_permission"]["implemented"]
    assert "user_tool_permission_request_decision" in domains["tool_permission"]["implemented"]
    assert "audit_visible_legacy_frontend_route_policy_mapping" in domains["tool_permission"]["implemented"]
    assert "legacy_frontend_route_policy_enforcement_or_ai_platform_remap" in domains["tool_permission"]["gaps"]
    assert "skill_release_promote_rollback_policy" in domains["skill_governance"]["implemented"]
    assert "skill_dependency_policy_materialization" in domains["skill_governance"]["implemented"]
    assert "signed_skill_package_or_sbom_release_gate" in domains["skill_governance"]["gaps"]
    assert "memory_retention_cleanup_admin_and_worker" in domains["memory_governance"]["implemented"]
    assert "long_term_cross_session_memory_default_fail_closed" in domains["memory_governance"]["implemented"]
    assert "formal_memory_delete_export_erasure_evidence" in domains["memory_governance"]["gaps"]
    assert "public_admin_projection_audit_baseline" in domains["frontend_projection"]["implemented"]
    assert "frontend_projection_audit_cli" in domains["frontend_projection"]["implemented"]
    assert "frontend_ci_projection_audit_integration" in domains["frontend_projection"]["implemented"]
    assert "frontend_legacy_route_policy_mapping" in domains["frontend_projection"]["implemented"]
    assert "frontend_active_browser_projection_audit_clear" in domains["frontend_projection"]["implemented"]
    assert "inactive_legacy_secret_like_frontend_sources_quarantined" in domains["frontend_projection"]["implemented"]
    assert "ordinary_user_g9_acceptance_for_legacy_admin_mcp_model_envvar_routes" in domains["frontend_projection"]["gaps"]
    assert "active_envvar_profile_surface_needs_policy_or_projection_remap" in domains["frontend_projection"]["gaps"]
    assert "quarantined_legacy_frontend_sources_need_projection_remap" in domains["frontend_projection"]["gaps"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "callback-secret" not in serialized
    assert "tenant-secret" not in serialized
    assert "docker://token" not in serialized
    assert "sandbox_workspace_root" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "storage_key" not in serialized
    assert "executor_private_payload" not in serialized
    assert ".claude/skills" not in serialized


def test_render_governance_readiness_markdown_is_operator_readable_and_gap_first():
    markdown = render_governance_readiness_markdown(build_governance_readiness(SecretBearingSettings()))

    assert "# ai-platform G6 Governance Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "legacy_frontend_route_policy_enforcement_or_ai_platform_remap" in markdown
    assert "signed_skill_package_or_sbom_release_gate" in markdown
    assert "formal_memory_delete_export_erasure_evidence" in markdown
    assert "callback-secret" not in markdown
    assert ".claude/skills" not in markdown


def test_governance_readiness_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["SANDBOX_CALLBACK_TOKEN"] = "callback-secret"
    env["SANDBOX_WORKSPACE_ROOT"] = "/tmp/tenant-secret/workspaces"
    result = subprocess.run(
        [sys.executable, "tools/governance_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.governance-readiness.v1"
    assert payload["status"] == "partial_blocked"
    assert "tool_permission" in payload["domains"]
    assert "callback-secret" not in result.stdout
    assert "tenant-secret" not in result.stdout
