from typing import Any

from app.settings import get_settings


SCHEMA_VERSION = "ai-platform.governance-readiness.v1"
GATE_NAME = "G6 Tool / Skill / Memory Governance"

_SANDBOX_PROVIDER_VALUES = {"docker", "fake"}


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


def _domain(implemented: list[str], gaps: list[str], next_checks: list[str]) -> dict[str, Any]:
    return {
        "status": "partial_blocked" if gaps else "ready_for_verification",
        "implemented": implemented,
        "gaps": gaps,
        "next_checks": next_checks,
    }


def build_governance_readiness(settings: object | None = None) -> dict[str, Any]:
    """Build a secret-safe G6 governance readiness baseline for Admin Runtime and CLI use."""
    resolved_settings = settings or get_settings()
    domains = {
        "tool_permission": _domain(
            implemented=[
                "admin_tool_policy_inventory",
                "tenant_scoped_tool_policy_update_audit",
                "user_tool_permission_request_decision",
                "risk_write_fail_closed_policy_evaluation",
                "public_tool_permission_card_projection",
                "audit_visible_legacy_frontend_route_policy_mapping",
            ],
            gaps=[
                "legacy_frontend_route_policy_enforcement_or_ai_platform_remap",
                "admin_policy_bulk_review_and_change_history_view",
                "tool_allow_deny_ask_policy_taxonomy_for_all_mcp_tools",
            ],
            next_checks=[
                "enforce or remap every legacy frontend MCP/model/env/channel/admin route to ai-platform projections",
                "add ordinary-user confirmation-card acceptance against migrated frontend",
                "keep risky or write-capable tools blocked without a current decision",
            ],
        ),
        "skill_governance": _domain(
            implemented=[
                "skill_version_registry",
                "skill_release_promote_rollback_policy",
                "skill_dependency_policy_materialization",
                "skill_snapshot_and_release_decision_lock",
            ],
            gaps=[
                "signed_skill_package_or_sbom_release_gate",
                "admin_skill_release_dashboard_acceptance",
                "dependency_vulnerability_or_license_policy",
            ],
            next_checks=[
                "record package provenance and dependency review before promoting uploaded skills",
                "keep ordinary users away from raw skill selection and staging internals",
                "verify rollback uses materializable snapshots before changing policy",
            ],
        ),
        "memory_governance": _domain(
            implemented=[
                "session_bound_memory_records",
                "ordinary_user_memory_policy_opt_out",
                "admin_memory_policy_inventory",
                "memory_retention_cleanup_admin_and_worker",
                "memory_content_metadata_redaction",
                "long_term_cross_session_memory_default_fail_closed",
            ],
            gaps=[
                "formal_memory_delete_export_erasure_evidence",
                "bounded_context_pack_product_contract_for_office_workflows",
                "memory_redaction_policy_admin_preview_and_audit",
            ],
            next_checks=[
                "prove delete and retention cleanup evidence across user and admin paths",
                "keep cross-session long-term memory disabled until policy and acceptance are complete",
                "shape context pack work from issue 22 without enabling broad memory reads",
            ],
        ),
        "frontend_projection": _domain(
            implemented=[
                "frontend_source_migrated_to_repo",
                "frontend_ci_verify_script",
                "frontend_release_traceability_cli",
                "frontend_projection_audit_cli",
                "frontend_ci_projection_audit_integration",
                "public_admin_projection_audit_baseline",
                "frontend_legacy_route_policy_mapping",
                "frontend_active_browser_projection_audit_clear",
                "inactive_legacy_secret_like_frontend_sources_quarantined",
            ],
            gaps=[
                "ordinary_user_g9_acceptance_for_legacy_admin_mcp_model_envvar_routes",
                "active_envvar_profile_surface_needs_policy_or_projection_remap",
                "quarantined_legacy_frontend_sources_need_projection_remap",
                "admin_runtime_governance_visual_acceptance",
                "frontend_image_release_trace_to_backend_worker_commit",
            ],
            next_checks=[
                "enforce frontend checks in CI before closing source ownership",
                "remap quarantined inactive model/channel sources and active envvar profile routes to ai-platform projections before release",
                "hide or policy-gate legacy admin/model/MCP/envvar/channel surfaces for ordinary users",
                "consume only ai-platform public or same-tenant admin projections",
            ],
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
        sections.append(
            f"### {name}\n\n"
            f"Status: `{domain['status']}`\n\n"
            "Implemented:\n\n"
            f"{implemented}\n\n"
            "Gaps:\n\n"
            f"{gaps}\n\n"
            "Next checks:\n\n"
            f"{checks}\n"
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
