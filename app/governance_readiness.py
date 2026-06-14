from typing import Any

from app.office_context_readiness import build_office_context_readiness
from app.skills.release_readiness import build_skill_release_readiness
from app.tool_policy_readiness import build_tool_policy_readiness


SCHEMA_VERSION = "ai-platform.governance-readiness.v1"
GATE_NAME = "G6 Tool / Skill / Memory Governance"

_SANDBOX_PROVIDER_VALUES = {"docker", "fake"}
_RAW_FRONTEND_GAP_DETAIL_KEYS = {
    "sample_violations",
    "ci_verify_script",
    "projection_audit_script",
}


def _bool_setting(settings: object, name: str) -> bool:
    value = getattr(settings, name, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_setting(settings: object, name: str, default: int = 0) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _sandbox_provider(settings: object) -> str:
    value = str(getattr(settings, "sandbox_container_provider", "fake") or "fake").strip().lower()
    return value if value in _SANDBOX_PROVIDER_VALUES else "unknown"


def _default_settings() -> object:
    from app.settings import get_settings

    return get_settings()


def _domain(
    implemented: list[str],
    gaps: list[str],
    next_checks: list[str],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    domain = {
        "status": "partial_blocked" if gaps else "ready_for_verification",
        "implemented": implemented,
        "gaps": gaps,
        "next_checks": next_checks,
    }
    if evidence is not None:
        domain["evidence"] = evidence
    return domain


def _sanitize_frontend_gap_detail(detail: dict[str, Any]) -> dict[str, Any]:
    sanitized = {
        key: value
        for key, value in detail.items()
        if key not in _RAW_FRONTEND_GAP_DETAIL_KEYS
    }
    if "ci_verify_script" in detail:
        sanitized["ci_verify_configured"] = bool(detail.get("ci_verify_script"))
    if "projection_audit_script" in detail:
        sanitized["projection_audit_configured"] = bool(
            detail.get("projection_audit_script")
        )
    violations = detail.get("sample_violations")
    if isinstance(violations, list):
        sanitized["sample_violations"] = [
            {
                "path": item.get("path"),
                "line": item.get("line"),
                "reason": item.get("reason"),
                "required_action": item.get("required_action"),
            }
            for item in violations[:20]
        ]
    return sanitized


def _frontend_projection_audit_evidence() -> dict[str, Any]:
    from tools.frontend_projection_audit import build_frontend_projection_audit

    audit = build_frontend_projection_audit()
    active_terms = audit["active_browser_entry"]["forbidden_projection_terms"]
    route_inventory = audit["route_inventory"]
    active_route_inventory = audit["active_browser_entry"]["route_inventory"]
    sanitized_gap_details = [
        _sanitize_frontend_gap_detail(detail)
        for detail in audit.get("open_gap_details", [])
    ]
    return {
        "schema_version": audit["schema_version"],
        "status": audit["status"],
        "summary": {
            "production_source_files": audit["scanned"]["production_source_files"],
            "active_source_files": len(audit["active_browser_entry"]["files"]),
            "active_forbidden_projection_violations": len(active_terms["violations"]),
            "legacy_route_policies": len(route_inventory["legacy_route_policies"]),
            "active_legacy_route_policies": len(active_route_inventory["legacy_route_policies"]),
            "quarantined_legacy_source_violations": len(
                audit["quarantined_legacy_sources"]["violations"]
            ),
        },
        "open_gaps": audit["open_gaps"],
        "open_gap_details": sanitized_gap_details,
        "policy": audit["policy"],
    }


def build_governance_readiness(
    settings: object | None = None,
    *,
    include_frontend_projection_audit: bool = False,
) -> dict[str, Any]:
    """Build a secret-safe G6 governance readiness baseline for Admin Runtime and CLI use."""
    resolved_settings = settings or _default_settings()
    skill_release_readiness = build_skill_release_readiness(
        skill_release_evidence_root="docs/release-evidence/skill-release"
    )
    skill_release_dashboard = skill_release_readiness["admin_skill_release_dashboard"]
    tool_policy_readiness = build_tool_policy_readiness()
    bulk_review_evidence = tool_policy_readiness["evidence"]["admin_policy_bulk_review_dashboard"]
    office_context_readiness = build_office_context_readiness()
    frontend_projection_evidence = (
        {"projection_audit": _frontend_projection_audit_evidence()}
        if include_frontend_projection_audit
        else None
    )
    domains = {
        "tool_permission": _domain(
            implemented=[
                "admin_tool_policy_inventory",
                "tenant_scoped_tool_policy_update_audit",
                "user_tool_permission_request_decision",
                "risk_write_fail_closed_policy_evaluation",
                "public_tool_permission_card_projection",
                "audit_visible_legacy_frontend_route_policy_mapping",
                "tool_allow_deny_ask_policy_taxonomy_evidence",
                "platform_registered_mcp_only_policy",
                "ordinary_user_custom_mcp_disabled",
                "admin_policy_change_history_projection",
                "admin_policy_bulk_review_dashboard_contract",
            ],
            gaps=[
                "legacy_frontend_route_policy_enforcement_or_ai_platform_remap",
                *bulk_review_evidence["open_gaps"],
            ],
            next_checks=[
                "enforce or remap every legacy frontend MCP/model/env/channel/admin route to ai-platform projections",
                "add ordinary-user confirmation-card acceptance against migrated frontend",
                "keep risky or write-capable tools blocked without a current decision",
            ],
            evidence={
                "tool_policy_taxonomy": {
                    "schema_version": tool_policy_readiness["schema_version"],
                    "status": tool_policy_readiness["status"],
                    "registry_contract": tool_policy_readiness["registry_contract"],
                    "summary": tool_policy_readiness["summary"],
                    "implemented_controls": tool_policy_readiness["implemented_controls"],
                    "open_gaps": tool_policy_readiness["open_gaps"],
                },
                "admin_policy_bulk_review_dashboard": {
                    "schema_version": bulk_review_evidence["schema_version"],
                    "status": bulk_review_evidence["status"],
                    "policy": bulk_review_evidence["policy"],
                    "dashboard_contract": bulk_review_evidence["dashboard_contract"],
                    "open_gaps": bulk_review_evidence["open_gaps"],
                    "does_not_close_g6": bulk_review_evidence["does_not_close_g6"],
                },
            },
        ),
        "skill_governance": _domain(
            implemented=[
                "skill_version_registry",
                "skill_release_promote_rollback_policy",
                "skill_dependency_policy_materialization",
                "skill_snapshot_and_release_decision_lock",
                "skill_release_readiness_evidence_snapshot",
                "skill_release_review_template_entrypoint",
                "skill_release_external_evidence_scaffold_entrypoint",
                "skill_dependency_review_policy_contract",
                "skill_signed_package_evidence_contract",
                "skill_signed_package_evidence_source_validation",
                "admin_skill_release_dashboard_contract",
            ],
            gaps=skill_release_readiness["open_gaps"],
            next_checks=[
                "record package provenance and dependency review before promoting uploaded skills",
                "keep ordinary users away from raw skill selection and staging internals",
                "verify rollback uses materializable snapshots before changing policy",
            ],
            evidence={
                "release_readiness": {
                    "schema_version": skill_release_readiness["schema_version"],
                    "status": skill_release_readiness["status"],
                    "source": skill_release_readiness["source"],
                    "summary": skill_release_readiness["summary"],
                    "dependency_review_policy": skill_release_readiness[
                        "dependency_review_policy"
                    ],
                    "open_gaps": skill_release_readiness["open_gaps"],
                },
                "admin_skill_release_dashboard": {
                    "schema_version": skill_release_dashboard["schema_version"],
                    "status": skill_release_dashboard["status"],
                    "policy": skill_release_dashboard["policy"],
                    "dashboard_contract": skill_release_dashboard["dashboard_contract"],
                    "open_gaps": skill_release_dashboard["open_gaps"],
                    "does_not_close_g6": skill_release_dashboard["does_not_close_g6"],
                }
            },
        ),
        "memory_governance": _domain(
            implemented=[
                "session_bound_memory_records",
                "ordinary_user_memory_policy_opt_out",
                "admin_memory_policy_inventory",
                "memory_retention_cleanup_admin_and_worker",
                "memory_content_metadata_redaction",
                "long_term_cross_session_memory_default_fail_closed",
                "memory_delete_retention_erasure_evidence_snapshot",
                "memory_export_erasure_evidence_snapshot",
                "memory_redaction_policy_admin_preview_and_audit",
                "office_context_pack_architecture_readiness_snapshot",
                "context_snapshot_public_provenance_projection_contract",
                "executor_context_pack_prompt_injection_source_tests",
                "office_execution_tier_router_source_tests",
            ],
            gaps=[
                "office_context_pack_persistence_and_versioning",
                "executor_context_pack_211_acceptance",
                "document_centric_followup_state",
                "sandbox_cold_start_latency_split",
                "frontend_context_provenance_acceptance",
            ],
            next_checks=[
                "keep delete, retention, and export erasure evidence current through tools/memory_erasure_readiness.py",
                "keep cross-session long-term memory disabled until policy and acceptance are complete",
                "use tools/office_context_readiness.py to keep the office context-pack source contract and prompt injection tests current",
                "keep context snapshot public provenance limited to counts, safe input keys, execution tier, and generated time",
                "record 211 executor context-pack acceptance before closing #22",
                "do not start Docker sandbox for lightweight office writing tasks by default",
            ],
            evidence={
                "office_context_pack_readiness": {
                    "schema_version": office_context_readiness["schema_version"],
                    "status": office_context_readiness["status"],
                    "policy": office_context_readiness["policy"],
                    "implemented_controls": office_context_readiness["implemented_controls"],
                    "summary": {
                        "allowed_sources": len(
                            office_context_readiness["context_pack_contract"]["allowed_sources"]
                        ),
                        "execution_tiers": len(office_context_readiness["execution_tiers"]),
                        "open_gaps": len(office_context_readiness["open_gaps"]),
                        "sandbox_default_for_lightweight_office_tasks": office_context_readiness[
                            "policy"
                        ]["lightweight_office_tasks_start_sandbox_by_default"],
                    },
                    "open_gaps": office_context_readiness["open_gaps"],
                }
            },
        ),
        "frontend_projection": _domain(
            implemented=[
                "frontend_source_migrated_to_repo",
                "frontend_ci_verify_script",
                "frontend_release_traceability_cli",
                "frontend_static_dist_release_manifest",
                "frontend_dist_build_provenance_gate",
                "frontend_projection_audit_cli",
                "frontend_ci_projection_audit_integration",
                "frontend_github_actions_ci_workflow",
                "public_admin_projection_audit_baseline",
                "frontend_legacy_route_policy_mapping",
                "frontend_active_legacy_route_policy_audit",
                "frontend_active_browser_projection_audit_clear",
                "inactive_legacy_secret_like_frontend_sources_quarantined",
                "frontend_profile_envvar_surface_fail_closed",
                "admin_runtime_capacity_governance_frontend_section",
                "admin_runtime_211_frontend_acceptance",
                "frontend_packaged_image_blocker_traceability",
                "frontend_packaged_image_definition_traceability",
                "frontend_packaged_image_ci_build_provenance_contract",
            ],
            gaps=[
                "ordinary_user_g9_acceptance_for_legacy_admin_mcp_model_envvar_routes",
                "quarantined_legacy_frontend_sources_need_projection_remap",
                "frontend_packaged_image_delivery_and_release_acceptance",
            ],
            next_checks=[
                "remap quarantined inactive model/channel/envvar sources to ai-platform projections before release",
                "hide or policy-gate legacy admin/model/MCP/envvar/channel surfaces for ordinary users",
                "verify the packaged frontend image on a Docker-capable host before release acceptance",
                "consume only ai-platform public or same-tenant admin projections",
            ],
            evidence=frontend_projection_evidence,
        ),
    }
    warnings = [gap for domain in domains.values() for gap in domain["gaps"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked" if warnings else "ready_for_verification",
        "admin_runtime_projection": "/api/ai/admin/runtime/overview",
        "ordinary_user_policy": "fail_closed_until_projection_mapping_and_acceptance_pass",
        "config_signals": {
            "sandbox_provider": _sandbox_provider(resolved_settings),
            "memory_retention_worker_cleanup_enabled": _bool_setting(
                resolved_settings,
                "memory_retention_worker_cleanup_enabled",
            ),
            "memory_retention_worker_cleanup_limit": _int_setting(
                resolved_settings,
                "memory_retention_worker_cleanup_limit",
                200,
            ),
            "multi_agent_dispatch_worker_enabled": _bool_setting(
                resolved_settings,
                "multi_agent_dispatch_worker_enabled",
            ),
        },
        "domains": domains,
        "open_gaps": warnings,
        "evidence_policy": "code_tests_docs_and_211_smoke_required_before_gate_closure",
    }


def render_governance_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render a governance readiness snapshot as operator-readable Markdown."""
    domains = readiness["domains"]
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"])
    sections = []
    for name, domain in domains.items():
        implemented = "\n".join(f"- {item}" for item in domain["implemented"])
        gaps = "\n".join(f"- {item}" for item in domain["gaps"])
        checks = "\n".join(f"- {item}" for item in domain["next_checks"])
        evidence_lines = ""
        evidence = domain.get("evidence")
        projection_audit = evidence.get("projection_audit") if isinstance(evidence, dict) else None
        if isinstance(projection_audit, dict):
            detail_lines = []
            for detail in projection_audit.get("open_gap_details", []):
                detail_lines.append(
                    f"- `{detail.get('gap')}` count `{detail.get('count')}`"
                )
            evidence_lines = (
                "\nEvidence:\n\n"
                f"- projection audit status `{projection_audit.get('status')}`\n"
                + "\n".join(detail_lines)
                + "\n"
            )
        bulk_review = evidence.get("admin_policy_bulk_review_dashboard") if isinstance(evidence, dict) else None
        if isinstance(bulk_review, dict):
            contract = bulk_review.get("dashboard_contract")
            if isinstance(contract, dict):
                evidence_lines += (
                    "\nEvidence:\n\n"
                    f"- admin bulk review readiness `{bulk_review.get('schema_version')}` status "
                    f"`{bulk_review.get('status')}`\n"
                    f"- admin bulk review dashboard contract `{contract.get('schema_version')}`\n"
                )
        release_readiness = evidence.get("release_readiness") if isinstance(evidence, dict) else None
        if isinstance(release_readiness, dict):
            policy = release_readiness.get("dependency_review_policy")
            if isinstance(policy, dict):
                signed_contract = policy.get("signed_package_evidence_contract")
                signed_contract_line = ""
                if isinstance(signed_contract, dict):
                    signed_contract_line = (
                        f"- signed package evidence contract `{signed_contract.get('schema_version')}` status "
                        f"`{signed_contract.get('status')}`\n"
                    )
                evidence_lines += (
                    "\nEvidence:\n\n"
                    f"- skill release readiness `{release_readiness.get('schema_version')}` status "
                    f"`{release_readiness.get('status')}`\n"
                    f"- dependency review policy `{policy.get('schema_version')}` status "
                    f"`{policy.get('status')}`\n"
                    f"{signed_contract_line}"
                )
        skill_dashboard = (
            evidence.get("admin_skill_release_dashboard")
            if isinstance(evidence, dict)
            else None
        )
        if isinstance(skill_dashboard, dict):
            contract = skill_dashboard.get("dashboard_contract")
            if isinstance(contract, dict):
                evidence_lines += (
                    "\nEvidence:\n\n"
                    f"- admin skill release dashboard readiness `{skill_dashboard.get('schema_version')}` status "
                    f"`{skill_dashboard.get('status')}`\n"
                    f"- admin skill release dashboard contract `{contract.get('schema_version')}`\n"
                )
        sections.append(
            f"### {name}\n\n"
            f"Status: `{domain['status']}`\n\n"
            "Implemented:\n\n"
            f"{implemented}\n\n"
            "Gaps:\n\n"
            f"{gaps}\n\n"
            "Next checks:\n\n"
            f"{checks}\n"
            f"{evidence_lines}"
        )
    domain_sections = "\n\n".join(sections)
    return (
        "# ai-platform G6 Governance Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Gate: `{readiness['gate']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Admin Runtime projection: `{readiness['admin_runtime_projection']}`\n\n"
        "## Open Gaps\n\n"
        f"{gap_lines}\n\n"
        "## Domains\n\n"
        f"{domain_sections}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )
