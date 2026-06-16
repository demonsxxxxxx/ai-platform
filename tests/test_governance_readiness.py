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


def test_governance_readiness_import_is_runtime_dependency_neutral():
    script = (
        "import builtins\n"
        "real_import = builtins.__import__\n"
        "class SettingsBlocker:\n"
        "    sandbox_container_provider = 'fake'\n"
        "    memory_retention_worker_cleanup_enabled = True\n"
        "    memory_retention_worker_cleanup_limit = 200\n"
        "    multi_agent_dispatch_worker_enabled = False\n"
        "def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
        "    if name == 'app.settings':\n"
        "        raise ModuleNotFoundError(\"No module named 'pydantic_settings'\")\n"
        "    return real_import(name, globals, locals, fromlist, level)\n"
        "builtins.__import__ = guarded_import\n"
        "from app.governance_readiness import build_governance_readiness\n"
        "readiness = build_governance_readiness(SettingsBlocker())\n"
        "assert readiness['status'] == 'partial_blocked'\n"
        "assert len(readiness['open_gaps']) > 1\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_governance_readiness_records_g6_domains_and_open_gaps_without_secrets():
    readiness = build_governance_readiness(
        SecretBearingSettings(),
        include_frontend_projection_audit=True,
    )

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
    assert "tool_allow_deny_ask_policy_taxonomy_evidence" in domains["tool_permission"]["implemented"]
    assert "exact_tool_permission_decision_lookup_source_tests" in domains["tool_permission"]["implemented"]
    assert "admin_policy_change_history_projection" in domains["tool_permission"]["implemented"]
    assert "admin_policy_bulk_review_dashboard_contract" in domains["tool_permission"]["implemented"]
    assert "legacy_frontend_route_policy_enforcement_or_ai_platform_remap" in domains["tool_permission"]["gaps"]
    assert "admin_policy_bulk_review_and_dashboard_acceptance" not in domains["tool_permission"]["gaps"]
    assert "admin_policy_bulk_review_runtime_acceptance" in domains["tool_permission"]["gaps"]
    assert "admin_policy_bulk_review_visual_acceptance" in domains["tool_permission"]["gaps"]
    assert "admin_policy_bulk_review_211_acceptance" in domains["tool_permission"]["gaps"]
    assert "admin_policy_bulk_review_and_change_history_view" not in domains["tool_permission"]["gaps"]
    assert "admin_policy_bulk_review_and_change_history_view" not in readiness["open_gaps"]
    assert "tool_allow_deny_ask_policy_taxonomy_for_all_mcp_tools" not in domains["tool_permission"]["gaps"]
    tool_evidence = domains["tool_permission"]["evidence"]["tool_policy_taxonomy"]
    assert tool_evidence["schema_version"] == "ai-platform.tool-policy-readiness.v1"
    assert tool_evidence["status"] == "partial_blocked"
    assert tool_evidence["registry_contract"] == {
        "registry_source": "platform_registered_mcp_tools_only",
        "ordinary_user_custom_mcp": "not_allowed",
        "unregistered_tool_behavior": "deny",
        "tenant_policy_scope": "same_tenant_registered_tools_only",
    }
    assert "admin_policy_change_history_projection" in tool_evidence["implemented_controls"]
    assert "exact_tool_permission_decision_lookup_source_tests" in tool_evidence["implemented_controls"]
    assert "platform_registered_mcp_only_policy" in tool_evidence["implemented_controls"]
    assert "ordinary_user_custom_mcp_disabled" in tool_evidence["implemented_controls"]
    assert "admin_policy_bulk_review_dashboard_contract" in tool_evidence["implemented_controls"]
    assert tool_evidence["summary"] == {
        "taxonomy_cases": 6,
        "auto_allow_cases": 1,
        "ask_cases": 3,
        "deny_cases": 2,
    }
    bulk_review_evidence = domains["tool_permission"]["evidence"]["admin_policy_bulk_review_dashboard"]
    assert bulk_review_evidence["schema_version"] == "ai-platform.tool-policy-bulk-review-readiness.v1"
    assert bulk_review_evidence["dashboard_contract"]["schema_version"] == (
        "ai-platform.tool-policy-bulk-review-dashboard-contract.v1"
    )
    assert bulk_review_evidence["open_gaps"] == [
        "admin_policy_bulk_review_runtime_acceptance",
        "admin_policy_bulk_review_visual_acceptance",
        "admin_policy_bulk_review_211_acceptance",
    ]
    assert "skill_release_promote_rollback_policy" in domains["skill_governance"]["implemented"]
    assert "skill_dependency_policy_materialization" in domains["skill_governance"]["implemented"]
    assert "skill_release_readiness_evidence_snapshot" in domains["skill_governance"]["implemented"]
    assert "skill_release_review_template_entrypoint" in domains["skill_governance"]["implemented"]
    assert "skill_release_external_evidence_scaffold_entrypoint" in domains["skill_governance"]["implemented"]
    assert "skill_dependency_review_policy_contract" in domains["skill_governance"]["implemented"]
    assert "skill_signed_package_evidence_contract" in domains["skill_governance"]["implemented"]
    assert "skill_signed_package_evidence_source_validation" in domains["skill_governance"]["implemented"]
    assert "admin_skill_release_dashboard_contract" in domains["skill_governance"]["implemented"]
    assert "signed_skill_package_or_sbom_release_gate" in domains["skill_governance"]["gaps"]
    assert "skill_dependency_review_policy_runtime_acceptance" in domains["skill_governance"]["gaps"]
    assert "admin_skill_release_dashboard_acceptance" not in domains["skill_governance"]["gaps"]
    assert "admin_skill_release_dashboard_runtime_acceptance" in domains["skill_governance"]["gaps"]
    assert "admin_skill_release_dashboard_visual_acceptance" in domains["skill_governance"]["gaps"]
    assert "admin_skill_release_dashboard_211_acceptance" in domains["skill_governance"]["gaps"]
    release_evidence = domains["skill_governance"]["evidence"]["release_readiness"]
    assert release_evidence["schema_version"] == "ai-platform.skill-release-readiness.v1"
    assert release_evidence["status"] == "partial_blocked"
    assert release_evidence["source"]["external_evidence"] == {
        "mode": "optional_external_release_evidence",
        "root": "docs/release-evidence/skill-release",
        "present": True,
    }
    assert release_evidence["summary"]["total_skills"] >= 1
    assert release_evidence["summary"]["skills_with_sbom_evidence"] == release_evidence["summary"]["total_skills"]
    assert release_evidence["summary"]["skills_with_license_evidence"] == release_evidence["summary"]["total_skills"]
    assert release_evidence["summary"]["skills_with_vulnerability_evidence"] == release_evidence["summary"][
        "total_skills"
    ]
    assert release_evidence["dependency_review_policy"]["schema_version"] == (
        "ai-platform.skill-dependency-review-policy.v1"
    )
    assert release_evidence["dependency_review_policy"]["signed_package_evidence_contract"]["schema_version"] == (
        "ai-platform.skill-signed-package-evidence-contract.v1"
    )
    assert release_evidence["dependency_review_policy"]["signed_package_evidence_contract"]["status"] == (
        "source_validation_enabled_not_evidence_satisfied"
    )
    assert release_evidence["dependency_review_policy"]["signed_package_evidence_contract"]["runtime_validation"] == (
        "enabled_for_repository_signed_package_evidence_json"
    )
    assert release_evidence["dependency_review_policy"]["does_not_close_g6"] is True
    assert "signed_skill_package_or_sbom_release_gate" in release_evidence["open_gaps"]
    assert "dependency_vulnerability_or_license_policy" in release_evidence["open_gaps"]
    assert "skill_dependency_review_policy_runtime_acceptance" in release_evidence["open_gaps"]
    dashboard_evidence = domains["skill_governance"]["evidence"]["admin_skill_release_dashboard"]
    assert dashboard_evidence["schema_version"] == "ai-platform.skill-release-dashboard-readiness.v1"
    assert dashboard_evidence["dashboard_contract"]["schema_version"] == (
        "ai-platform.skill-release-dashboard-contract.v1"
    )
    assert dashboard_evidence["open_gaps"] == [
        "admin_skill_release_dashboard_runtime_acceptance",
        "admin_skill_release_dashboard_visual_acceptance",
        "admin_skill_release_dashboard_211_acceptance",
    ]
    assert dashboard_evidence["does_not_close_g6"] is True
    assert "memory_retention_cleanup_admin_and_worker" in domains["memory_governance"]["implemented"]
    assert "long_term_cross_session_memory_default_fail_closed" in domains["memory_governance"]["implemented"]
    assert "memory_delete_retention_erasure_evidence_snapshot" in domains["memory_governance"]["implemented"]
    assert "memory_export_erasure_evidence_snapshot" in domains["memory_governance"]["implemented"]
    assert "memory_redaction_policy_admin_preview_and_audit" in domains["memory_governance"]["implemented"]
    assert "office_context_pack_architecture_readiness_snapshot" in domains["memory_governance"]["implemented"]
    assert "context_snapshot_public_provenance_projection_contract" in domains["memory_governance"]["implemented"]
    assert "executor_context_pack_prompt_injection_source_tests" in domains["memory_governance"]["implemented"]
    assert "source_level_context_pack_persistence_and_versioning" in domains["memory_governance"]["implemented"]
    assert "user_visible_context_provenance_api_projection_source_tests" in domains["memory_governance"]["implemented"]
    assert "frontend_context_provenance_playback_source_tests" in domains["memory_governance"]["implemented"]
    assert "office_execution_tier_router_source_tests" in domains["memory_governance"]["implemented"]
    assert "document_centric_followup_state_source_tests" in domains["memory_governance"]["implemented"]
    assert "sandbox_cold_start_latency_split_source_contract" in domains["memory_governance"]["implemented"]
    assert "sandbox_runtime_hardening_source_verifier_contract" in domains["memory_governance"]["implemented"]
    assert "sandbox_cached_lease_scope_revalidation_source_tests" in domains["memory_governance"]["implemented"]
    assert "formal_memory_delete_export_erasure_evidence" not in domains["memory_governance"]["gaps"]
    assert "memory_export_erasure_evidence" not in domains["memory_governance"]["gaps"]
    assert "memory_redaction_policy_admin_preview_and_audit" not in domains["memory_governance"]["gaps"]
    assert "bounded_context_pack_product_contract_for_office_workflows" not in domains["memory_governance"]["gaps"]
    assert "office_context_pack_runtime_implementation_and_acceptance" not in domains["memory_governance"]["gaps"]
    assert "office_context_pack_persistence_and_versioning" not in domains["memory_governance"]["gaps"]
    assert "user_visible_context_provenance_projection" not in domains["memory_governance"]["gaps"]
    assert "frontend_context_provenance_acceptance" not in domains["memory_governance"]["gaps"]
    assert "executor_context_pack_injection" not in domains["memory_governance"]["gaps"]
    assert "document_centric_followup_state" not in domains["memory_governance"]["gaps"]
    assert "executor_context_pack_211_acceptance" in domains["memory_governance"]["gaps"]
    assert "office_execution_tier_router" not in domains["memory_governance"]["gaps"]
    assert "sandbox_cold_start_latency_split" not in domains["memory_governance"]["gaps"]
    assert "sandbox_cold_start_latency_split_211_acceptance" not in domains["memory_governance"]["gaps"]
    context_evidence = domains["memory_governance"]["evidence"]["office_context_pack_readiness"]
    assert context_evidence["schema_version"] == "ai-platform.office-context-pack-readiness.v1"
    assert context_evidence["status"] == "partial_blocked"
    assert context_evidence["policy"]["lightweight_office_tasks_start_sandbox_by_default"] is False
    assert context_evidence["policy"]["ordinary_user_policy"] == "public_projection_only"
    assert context_evidence["policy"]["long_term_memory_policy"] == "fail_closed_until_policy_and_acceptance"
    assert context_evidence["policy"]["does_not_expand_multi_agent_beta"] is True
    assert "executor_context_pack_prompt_injection_source_tests" in context_evidence["implemented_controls"]
    assert "source_level_context_pack_persistence_and_versioning" in context_evidence["implemented_controls"]
    assert "user_visible_context_provenance_api_projection_source_tests" in context_evidence["implemented_controls"]
    assert "frontend_context_provenance_playback_source_tests" in context_evidence["implemented_controls"]
    assert "office_execution_tier_router_source_tests" in context_evidence["implemented_controls"]
    assert "document_centric_followup_state_source_tests" in context_evidence["implemented_controls"]
    assert "sandbox_cold_start_latency_split_source_contract" in context_evidence["implemented_controls"]
    assert "sandbox_runtime_hardening_source_verifier_contract" in context_evidence["implemented_controls"]
    assert "sandbox_cached_lease_scope_revalidation_source_tests" in context_evidence["implemented_controls"]
    assert context_evidence["summary"]["allowed_sources"] >= 7
    assert context_evidence["summary"]["execution_tiers"] >= 3
    assert context_evidence["summary"]["open_gaps"] == 1
    assert context_evidence["summary"]["closed_runtime_gaps"] == 1
    assert context_evidence["summary"]["sandbox_default_for_lightweight_office_tasks"] is False
    assert context_evidence["sandbox_latency_observability"]["status"] == (
        "source_contract_defined_runtime_acceptance_required"
    )
    assert context_evidence["sandbox_latency_observability"]["must_not_hide_cold_start_in_executor_latency"] is True
    assert context_evidence["sandbox_runtime_smoke_contract"]["generator_script"] == (
        "scripts/generate_sandbox_runtime_evidence_211.py"
    )
    assert context_evidence["sandbox_runtime_smoke_contract"]["verifier_script"] == (
        "scripts/verify_sandbox_runtime_211.py"
    )
    assert context_evidence["sandbox_runtime_smoke_contract"]["docker_cmd"] == "sudo -n docker"
    assert "check_platform_runtime_evidence" in context_evidence["sandbox_runtime_smoke_contract"]["required_checks"]
    assert "check_platform_hardening_evidence" in context_evidence["sandbox_runtime_smoke_contract"]["required_checks"]
    assert "non_expansion_invariants" in context_evidence["sandbox_runtime_smoke_contract"]["required_evidence_sections"]
    assert "hardening.evidence_class" in context_evidence["sandbox_runtime_smoke_contract"]["required_evidence_sections"]
    assert context_evidence["sandbox_runtime_smoke_contract"]["non_expansion_invariants"][
        "ordinary_user_multi_agent_allowed"
    ] is False
    assert context_evidence["executor_context_pack_runtime_acceptance_contract"]["schema_version"] == (
        "ai-platform.executor-context-pack-runtime-acceptance.v1"
    )
    assert context_evidence["executor_context_pack_runtime_acceptance_contract"]["target"] == (
        "211_api_worker_runtime"
    )
    assert context_evidence["executor_context_pack_runtime_acceptance_contract"]["generator_script"] == (
        "scripts/generate_executor_context_pack_evidence_211.py"
    )
    assert context_evidence["executor_context_pack_runtime_acceptance_contract"]["verifier_script"] == (
        "scripts/verify_executor_context_pack_211.py"
    )
    assert (
        context_evidence["executor_context_pack_runtime_acceptance_contract"]["source_probe_evidence_strength"]
        == "source_probe_on_target_runtime"
    )
    assert (
        context_evidence["executor_context_pack_runtime_acceptance_contract"]["required_live_evidence_strength"]
        == "live_worker_run_payload"
    )
    assert "accepted_evidence_strength" not in context_evidence["executor_context_pack_runtime_acceptance_contract"]
    assert (
        context_evidence["executor_context_pack_runtime_acceptance_contract"]["does_not_close_211_acceptance"]
        is True
    )
    assert (
        context_evidence["executor_context_pack_runtime_acceptance_contract"][
            "runtime_acceptance_requires_real_run_payload"
        ]
        is True
    )
    assert "runtime_evidence" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_live_evidence_sections"]
    assert "app.repositories.get_context_snapshot_for_worker" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["source_functions"]
    assert "app.context_builder.executor_context_pack_from_snapshot" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["source_functions"]
    assert "app.worker._context_snapshot_ref_from_row" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["source_functions"]
    assert "live_worker_run_payload" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "scoped_context_snapshot_loaded" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "prompt_includes_context_pack_generated_at" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "fresh_generated_at" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert "source_functions_bound_to_current_runtime" in context_evidence[
        "executor_context_pack_runtime_acceptance_contract"
    ]["required_runtime_evidence"]
    assert context_evidence["executor_context_pack_runtime_acceptance_contract"][
        "acceptance_gap"
    ] == "executor_context_pack_211_acceptance"
    assert context_evidence["executor_context_pack_runtime_acceptance_contract"][
        "does_not_close_g6_g9"
    ] is True
    assert "frontend_context_provenance_acceptance" not in context_evidence["open_gaps"]
    assert "document_centric_followup_state" not in context_evidence["open_gaps"]
    assert "executor_context_pack_211_acceptance" in context_evidence["open_gaps"]
    assert "office_execution_tier_router" not in context_evidence["open_gaps"]
    assert "sandbox_cold_start_latency_split" not in context_evidence["open_gaps"]
    assert "sandbox_cold_start_latency_split_211_acceptance" not in context_evidence["open_gaps"]
    assert context_evidence["closed_runtime_gaps"] == [
        "sandbox_cold_start_latency_split_211_acceptance",
    ]
    assert "executor_context_pack_211_acceptance" not in context_evidence["runtime_acceptance_evidence"]
    assert context_evidence["runtime_acceptance_evidence"]["sandbox_cold_start_latency_split_211_acceptance"][
        "timings"
    ]["sandbox_container_cold_start_latency_ms"] > 0
    assert "public_admin_projection_audit_baseline" in domains["frontend_projection"]["implemented"]
    assert "frontend_projection_audit_cli" in domains["frontend_projection"]["implemented"]
    assert "frontend_ci_projection_audit_integration" in domains["frontend_projection"]["implemented"]
    assert "frontend_github_actions_ci_workflow" in domains["frontend_projection"]["implemented"]
    assert "frontend_static_dist_release_manifest" in domains["frontend_projection"]["implemented"]
    assert "frontend_dist_build_provenance_gate" in domains["frontend_projection"]["implemented"]
    assert "frontend_legacy_route_policy_mapping" in domains["frontend_projection"]["implemented"]
    assert "frontend_active_legacy_route_policy_audit" in domains["frontend_projection"]["implemented"]
    assert "frontend_active_browser_projection_audit_clear" in domains["frontend_projection"]["implemented"]
    assert "frontend_run_playback_context_provenance_projection" in domains["frontend_projection"]["implemented"]
    assert "inactive_legacy_secret_like_frontend_sources_quarantined" in domains["frontend_projection"]["implemented"]
    assert "frontend_profile_envvar_surface_fail_closed" in domains["frontend_projection"]["implemented"]
    assert "admin_runtime_capacity_governance_frontend_section" in domains["frontend_projection"]["implemented"]
    assert "admin_runtime_211_frontend_acceptance" in domains["frontend_projection"]["implemented"]
    assert "frontend_packaged_image_blocker_traceability" in domains["frontend_projection"]["implemented"]
    assert "frontend_packaged_image_definition_traceability" in domains["frontend_projection"]["implemented"]
    assert "frontend_packaged_image_ci_build_provenance_contract" in domains["frontend_projection"]["implemented"]
    assert "ordinary_user_g9_acceptance_for_legacy_admin_mcp_model_envvar_routes" in domains["frontend_projection"]["gaps"]
    assert "active_envvar_profile_surface_needs_policy_or_projection_remap" not in domains["frontend_projection"]["gaps"]
    assert "quarantined_legacy_frontend_sources_need_projection_remap" in domains["frontend_projection"]["gaps"]
    assert "frontend_packaged_image_delivery_and_release_acceptance" in domains["frontend_projection"]["gaps"]
    assert "frontend_packaged_image_release_trace_to_backend_worker_commit" not in domains["frontend_projection"]["gaps"]
    assert "admin_runtime_211_visual_acceptance" not in domains["frontend_projection"]["gaps"]
    assert "admin_runtime_governance_visual_acceptance" not in domains["frontend_projection"]["gaps"]
    assert "add and verify the packaged frontend image definition before release acceptance" not in domains["frontend_projection"]["next_checks"]
    assert "verify the packaged frontend image on a Docker-capable host before release acceptance" in domains["frontend_projection"]["next_checks"]
    assert "enforce frontend checks in CI before closing source ownership" not in domains["frontend_projection"]["next_checks"]
    projection_evidence = domains["frontend_projection"]["evidence"]["projection_audit"]
    packaged_contract = domains["frontend_projection"]["evidence"]["packaged_runtime_smoke_contract"]
    assert packaged_contract["schema_version"] == "ai-platform.frontend-packaged-runtime-smoke.v1"
    assert packaged_contract["evidence_contract"]["schema_version"] == (
        "ai-platform.frontend-packaged-runtime-smoke-evidence.v1"
    )
    assert packaged_contract["evidence_contract"]["write_path"] == (
        "frontend_release.packaged_runtime_smoke.<commit_sha>"
    )
    assert packaged_contract["status"] == "blocked_missing_runtime_evidence"
    assert "packaged_frontend_runtime_smoke_evidence_missing" in packaged_contract["blockers"]
    assert "frontend_packaged_runtime_smoke" not in packaged_contract["closed_evidence_items"]
    assert "sudo -n docker build" in " ".join(packaged_contract["operator_commands"])
    assert packaged_contract["runtime_policy"] == "docker_capable_host_only_no_local_windows_docker"
    assert packaged_contract["does_not_close_g6_g9_or_21"] is True
    assert projection_evidence["schema_version"] == "ai-platform.frontend-projection-audit.v1"
    assert projection_evidence["status"] == "pass_with_policy_gaps"
    assert projection_evidence["summary"]["active_forbidden_projection_violations"] == 0
    assert projection_evidence["summary"]["active_legacy_route_policies"] >= 1
    assert projection_evidence["summary"]["quarantined_legacy_source_violations"] >= 1
    gap_details = {item["gap"]: item for item in projection_evidence["open_gap_details"]}
    assert "active_legacy_routes_need_policy_enforcement_or_ai_platform_remap" in gap_details
    assert any(
        route["route_prefix"] == "/api/mcp"
        for route in gap_details["active_legacy_routes_need_policy_enforcement_or_ai_platform_remap"]["routes"]
    )
    assert "quarantined_legacy_sources_need_ai_platform_projection_remap" in gap_details
    assert gap_details["quarantined_legacy_sources_need_ai_platform_projection_remap"]["sample_violations"]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "callback-secret" not in serialized
    assert "tenant-secret" not in serialized
    assert "docker://token" not in serialized
    assert "sandbox_workspace_root" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "storage_key" not in serialized
    assert "executor_private_payload" not in serialized
    assert ".claude/skills" not in serialized


def test_governance_readiness_sanitizes_frontend_ci_gap_detail(monkeypatch):
    import tools.frontend_projection_audit as projection_audit

    def fake_projection_audit():
        return {
            "schema_version": "ai-platform.frontend-projection-audit.v1",
            "status": "blocked",
            "scanned": {"production_source_files": 1},
            "active_browser_entry": {
                "files": [],
                "forbidden_projection_terms": {"violations": []},
                "route_inventory": {"legacy_route_policies": []},
            },
            "route_inventory": {"legacy_route_policies": []},
            "quarantined_legacy_sources": {"violations": []},
            "open_gaps": ["frontend_ci_verify_does_not_yet_run_projection_audit"],
            "open_gap_details": [
                {
                    "gap": "frontend_ci_verify_does_not_yet_run_projection_audit",
                    "status": "missing_ci_projection_audit_gate",
                    "governance_gates": ["G6", "G9"],
                    "count": 1,
                    "required_action": "make_ci_verify_start_with_frontend_projection_audit",
                    "ci_verify_script": "TOKEN=secret eslint . && echo storage_key",
                    "projection_audit_script": "client_secret=leak python audit.py",
                }
            ],
            "policy": {},
        }

    monkeypatch.setattr(
        projection_audit,
        "build_frontend_projection_audit",
        fake_projection_audit,
    )

    readiness = build_governance_readiness(
        SecretBearingSettings(),
        include_frontend_projection_audit=True,
    )

    detail = readiness["domains"]["frontend_projection"]["evidence"]["projection_audit"][
        "open_gap_details"
    ][0]
    assert detail["ci_verify_configured"] is True
    assert detail["projection_audit_configured"] is True
    assert "ci_verify_script" not in detail
    assert "projection_audit_script" not in detail
    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "token=secret" not in serialized
    assert "client_secret=leak" not in serialized
    assert "storage_key" not in serialized


def test_governance_readiness_default_keeps_frontend_projection_audit_off():
    readiness = build_governance_readiness(SecretBearingSettings())

    frontend_projection = readiness["domains"]["frontend_projection"]
    assert "evidence" not in frontend_projection


def test_render_governance_readiness_markdown_is_operator_readable_and_gap_first():
    markdown = render_governance_readiness_markdown(
        build_governance_readiness(
            SecretBearingSettings(),
            include_frontend_projection_audit=True,
        )
    )

    assert "# ai-platform G6 Governance Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "legacy_frontend_route_policy_enforcement_or_ai_platform_remap" in markdown
    assert "active_legacy_routes_need_policy_enforcement_or_ai_platform_remap" in markdown
    assert "admin_policy_bulk_review_dashboard_contract" in markdown
    assert "ai-platform.tool-policy-bulk-review-dashboard-contract.v1" in markdown
    assert "admin_policy_bulk_review_runtime_acceptance" in markdown
    assert "signed_skill_package_or_sbom_release_gate" in markdown
    assert "skill_dependency_review_policy_contract" in markdown
    assert "skill_dependency_review_policy_runtime_acceptance" in markdown
    assert "ai-platform.skill-dependency-review-policy.v1" in markdown
    assert "skill_signed_package_evidence_contract" in markdown
    assert "ai-platform.skill-signed-package-evidence-contract.v1" in markdown
    assert "admin_skill_release_dashboard_contract" in markdown
    assert "ai-platform.skill-release-dashboard-contract.v1" in markdown
    assert "admin_skill_release_dashboard_runtime_acceptance" in markdown
    assert "memory_delete_retention_erasure_evidence_snapshot" in markdown
    assert "memory_export_erasure_evidence_snapshot" in markdown
    assert "context_snapshot_public_provenance_projection_contract" in markdown
    assert "user_visible_context_provenance_api_projection_source_tests" in markdown
    assert "frontend_context_provenance_playback_source_tests" in markdown
    assert "office_execution_tier_router" in markdown
    assert "source_level_context_pack_persistence_and_versioning" in markdown
    assert "- office_context_pack_persistence_and_versioning" not in markdown
    assert "executor_context_pack_prompt_injection_source_tests" in markdown
    assert "sandbox_cold_start_latency_split_source_contract" in markdown
    open_gaps = markdown.split("## Domains", 1)[0]
    assert "executor_context_pack_211_acceptance" in open_gaps
    assert "sandbox_cold_start_latency_split_211_acceptance" not in open_gaps
    assert "closed runtime gaps" in markdown
    assert "executor_context_pack_211_acceptance" in markdown
    assert "sandbox_cold_start_latency_split_211_acceptance" in markdown
    assert "memory_export_erasure_evidence" not in open_gaps
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
