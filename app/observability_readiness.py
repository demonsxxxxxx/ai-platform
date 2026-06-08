from typing import Any

from app.alert_slo_readiness import build_alert_slo_readiness
from app.error_taxonomy import build_error_taxonomy_contract
from app.settings import get_settings


SCHEMA_VERSION = "ai-platform.observability-readiness.v1"
GATE_NAME = "G9 Observability / Quality / Ops"

_SANDBOX_PROVIDER_VALUES = {"docker", "fake"}
_MODEL_GATEWAY_PROVIDER_VALUES = {"new-api", "openai_compatible"}


def _bool_setting(settings: object, name: str) -> bool:
    value = getattr(settings, name, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _enum_setting(settings: object, name: str, *, default: str, allowed_values: set[str]) -> str:
    value = str(getattr(settings, name, default) or default).strip().lower()
    return value if value in allowed_values else "unknown"


def _domain(
    implemented: list[str],
    gaps: list[str],
    next_checks: list[str],
    *,
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


def build_observability_readiness(settings: object | None = None) -> dict[str, Any]:
    """Build a secret-safe G9 observability readiness baseline for Admin Runtime and CLI use."""
    resolved_settings = settings or get_settings()
    alert_slo_readiness = build_alert_slo_readiness()
    domains = {
        "runtime_metrics": _domain(
            implemented=[
                "admin_runtime_observability_summary",
                "token_cost_latency_error_counts",
                "queue_admission_database_pool_backpressure_summary",
                "capacity_runtime_evidence_capture",
            ],
            gaps=[
                "latency_percentiles_p50_p95_p99",
                "model_gateway_request_concurrency_limit",
                "recorded_capacity_load_test_evidence",
            ],
            next_checks=[
                "record p50, p95, and p99 latency for API, queue lease, worker, model, sandbox, artifact, cancel, retry, and resume",
                "add model-gateway timeout and concurrency pressure signals before raising defaults",
                "keep capacity gate blocked until real load-test evidence is recorded",
            ],
        ),
        "error_taxonomy": _domain(
            implemented=[
                "formal_error_taxonomy_contract",
                "error_category_mapping_for_executor_tool_sandbox_model_gateway",
                "run_event_error_count_projection",
                "recent_failure_projection_redaction",
            ],
            gaps=[
                "error_taxonomy_dashboard_acceptance",
            ],
            next_checks=[
                "define stable error categories before beta reporting",
                "map worker, tool, sandbox, model gateway, memory, and artifact failures to the taxonomy",
                "keep Admin projections same-tenant and free of private payloads",
            ],
        ),
        "quality_evaluation": _domain(
            implemented=[
                "run_trace_audit_linkage_baseline",
            ],
            gaps=[
                "golden_set_eval_run_contract",
                "quality_score_schema",
                "office_workflow_acceptance_dataset",
            ],
            next_checks=[
                "define golden-set scenarios before department rollout",
                "separate office document quality signals from coding-task runtime metrics",
                "avoid reading ordinary-user private payloads for quality scoring",
            ],
        ),
        "alerts_and_exports": _domain(
            implemented=[
                "admin_runtime_overview_projection",
                "capacity_gate_readiness_verdict",
                "alert_slo_rule_template_evidence",
            ],
            gaps=[
                "alert_rules_runtime_dashboard_and_211_acceptance",
                "alert_delivery_channel_policy",
                "slo_threshold_runtime_calibration",
                "trace_audit_export_contract",
                "release_evidence_export_location",
            ],
            next_checks=[
                "wire alert/SLO templates into an Admin dashboard and 211 acceptance smoke before enabling alerts",
                "calibrate thresholds from recorded runtime and capacity evidence instead of raising defaults",
                "add trace and audit export without exposing raw storage keys or executor private payloads",
                "keep release evidence separate from the product roadmap",
            ],
            evidence={
                "alert_slo_rules": alert_slo_readiness,
            },
        ),
    }
    gaps = [gap for domain in domains.values() for gap in domain["gaps"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked" if gaps else "ready_for_verification",
        "admin_runtime_projection": "/api/ai/admin/runtime/overview",
        "ordinary_user_policy": "admin_only_operational_projection",
        "error_taxonomy": build_error_taxonomy_contract(),
        "config_signals": {
            "model_gateway_provider": _enum_setting(
                resolved_settings,
                "llm_gateway_provider",
                default="openai_compatible",
                allowed_values=_MODEL_GATEWAY_PROVIDER_VALUES,
            ),
            "sandbox_provider": _enum_setting(
                resolved_settings,
                "sandbox_container_provider",
                default="fake",
                allowed_values=_SANDBOX_PROVIDER_VALUES,
            ),
            "multi_agent_dispatch_worker_enabled": _bool_setting(
                resolved_settings,
                "multi_agent_dispatch_worker_enabled",
            ),
        },
        "domains": domains,
        "open_gaps": gaps,
        "evidence_policy": "admin_runtime_projection_plus_tests_docs_and_211_smoke_required_before_gate_closure",
    }


def render_observability_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render an observability readiness snapshot as operator-readable Markdown."""
    gap_lines = "\n".join(f"- {gap}" for gap in readiness["open_gaps"])
    sections = []
    for name, domain in readiness["domains"].items():
        implemented = "\n".join(f"- {item}" for item in domain["implemented"])
        gaps = "\n".join(f"- {item}" for item in domain["gaps"])
        checks = "\n".join(f"- {item}" for item in domain["next_checks"])
        evidence_lines = _render_domain_evidence(domain.get("evidence"))
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
        "# ai-platform G9 Observability Readiness\n\n"
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


def _render_domain_evidence(evidence: object) -> str:
    if not isinstance(evidence, dict) or not evidence:
        return ""
    alert_rules = evidence.get("alert_slo_rules")
    if not isinstance(alert_rules, dict):
        return ""
    rules = alert_rules.get("rules")
    if not isinstance(rules, list):
        return ""
    rule_lines = "\n".join(
        f"- `{rule.get('id')}`: `{rule.get('category')}`"
        for rule in rules
        if isinstance(rule, dict)
    )
    open_gaps = alert_rules.get("open_gaps")
    gap_lines = ""
    if isinstance(open_gaps, list):
        gap_lines = "\n".join(f"- `{gap}`" for gap in open_gaps)
    return (
        "\nEvidence:\n\n"
        f"- `{alert_rules.get('schema_version')}` status `{alert_rules.get('status')}`\n"
        f"- active alerting policy `{alert_rules.get('active_alerting_policy')}`\n"
        "Nested alert gaps:\n\n"
        f"{gap_lines}\n\n"
        "Template rules:\n\n"
        f"{rule_lines}\n"
    )
