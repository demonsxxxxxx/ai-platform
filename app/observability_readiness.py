from typing import Any

from app.alert_slo_readiness import build_alert_slo_readiness
from app.capacity_baseline import build_model_gateway_backpressure_policy
from app.error_taxonomy import build_error_taxonomy_contract
from app.error_taxonomy_dashboard_readiness import build_error_taxonomy_dashboard_readiness
from app.quality_golden_set_readiness import build_quality_golden_set_readiness
from app.release_evidence_readiness import build_release_evidence_readiness
from app.settings import get_settings
from app.trace_audit_export_readiness import build_trace_audit_export_readiness


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


def _positive_int_setting(settings: object, name: str) -> int | None:
    value = getattr(settings, name, 0)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
    error_taxonomy_dashboard_readiness = build_error_taxonomy_dashboard_readiness()
    quality_golden_set_readiness = build_quality_golden_set_readiness()
    release_evidence_readiness = build_release_evidence_readiness()
    trace_audit_export_readiness = build_trace_audit_export_readiness()
    model_gateway_backpressure_policy = build_model_gateway_backpressure_policy()
    model_gateway_request_concurrency_limit = _positive_int_setting(
        resolved_settings,
        "model_gateway_request_concurrency_limit",
    )
    model_gateway_capacity_gaps = ["model_gateway_request_concurrency_limit"]
    if model_gateway_request_concurrency_limit is not None:
        model_gateway_capacity_gaps.extend(
            [
                "model_gateway_request_concurrency_limit_enforcement",
                "model_gateway_capacity_load_test_evidence",
            ]
        )
    domains = {
        "runtime_metrics": _domain(
            implemented=[
                "admin_runtime_observability_summary",
                "token_cost_latency_error_counts",
                "latency_percentiles_p50_p95_p99_admin_projection",
                "queue_admission_database_pool_backpressure_summary",
                "model_gateway_backpressure_policy_contract",
                "capacity_runtime_evidence_capture",
            ],
            gaps=[
                "latency_percentile_per_surface_split_and_dashboard_acceptance",
                *model_gateway_capacity_gaps,
                "recorded_capacity_load_test_evidence",
            ],
            next_checks=[
                "split API, queue lease, worker, model, sandbox, artifact, cancel, retry, and resume latency into dashboard-accepted runtime metrics before G9 closure",
                "add or prove model-gateway timeout and concurrency pressure signals before raising defaults",
                "keep capacity gate blocked until real load-test evidence is recorded",
            ],
            evidence={
                "model_gateway_backpressure_policy": model_gateway_backpressure_policy,
            },
        ),
        "error_taxonomy": _domain(
            implemented=[
                "formal_error_taxonomy_contract",
                "error_category_mapping_for_executor_tool_sandbox_model_gateway",
                "error_taxonomy_dashboard_contract",
                "run_event_error_count_projection",
                "recent_failure_projection_redaction",
            ],
            gaps=[
                *error_taxonomy_dashboard_readiness["open_gaps"],
            ],
            next_checks=[
                "define stable error categories before beta reporting",
                "map worker, tool, sandbox, model gateway, memory, and artifact failures to the taxonomy",
                "keep Admin projections same-tenant and free of private payloads",
            ],
            evidence={
                "error_taxonomy_dashboard": error_taxonomy_dashboard_readiness,
            },
        ),
        "quality_evaluation": _domain(
            implemented=[
                "run_trace_audit_linkage_baseline",
                "quality_golden_set_readiness_contract",
                "quality_score_schema_contract",
            ],
            gaps=[
                "golden_set_eval_runtime_and_211_acceptance",
                "office_workflow_acceptance_dataset",
                "quality_threshold_calibration",
                "quality_dashboard_acceptance",
            ],
            next_checks=[
                "run golden-set evals through a reviewed harness before department rollout",
                "separate office document quality signals from coding-task runtime metrics",
                "avoid reading ordinary-user private payloads for quality scoring",
            ],
            evidence={
                "quality_golden_set": quality_golden_set_readiness,
            },
        ),
        "alerts_and_exports": _domain(
            implemented=[
                "admin_runtime_overview_projection",
                "capacity_gate_readiness_verdict",
                "alert_slo_rule_template_evidence",
                "alert_delivery_channel_policy_contract",
                "trace_audit_export_contract",
                "release_evidence_export_location_contract",
                "release_evidence_retention_policy_contract",
            ],
            gaps=[
                *alert_slo_readiness["open_gaps"],
                *trace_audit_export_readiness["open_gaps"],
                *release_evidence_readiness["open_gaps"],
            ],
            next_checks=[
                "wire alert/SLO templates into an Admin dashboard and 211 acceptance smoke before enabling alerts",
                "calibrate thresholds from recorded runtime and capacity evidence instead of raising defaults",
                "add trace and audit export without exposing raw storage keys or executor private payloads",
                "keep release evidence separate from the product roadmap",
            ],
            evidence={
                "alert_slo_rules": alert_slo_readiness,
                "trace_audit_export": trace_audit_export_readiness,
                "release_evidence": release_evidence_readiness,
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
            "model_gateway_request_concurrency_limit": model_gateway_request_concurrency_limit,
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
    rendered_sections: list[str] = []
    quality_golden_set = evidence.get("quality_golden_set")
    if isinstance(quality_golden_set, dict):
        rendered_sections.append(_render_quality_golden_set_evidence(quality_golden_set))
    error_taxonomy_dashboard = evidence.get("error_taxonomy_dashboard")
    if isinstance(error_taxonomy_dashboard, dict):
        rendered_sections.append(_render_error_taxonomy_dashboard(error_taxonomy_dashboard))
    alert_rules = evidence.get("alert_slo_rules")
    if isinstance(alert_rules, dict):
        rendered_sections.append(_render_alert_slo_evidence(alert_rules))
    release_evidence = evidence.get("release_evidence")
    if isinstance(release_evidence, dict):
        rendered_sections.append(_render_release_evidence(release_evidence))
    trace_audit_export = evidence.get("trace_audit_export")
    if isinstance(trace_audit_export, dict):
        rendered_sections.append(_render_trace_audit_export(trace_audit_export))
    model_gateway_policy = evidence.get("model_gateway_backpressure_policy")
    if isinstance(model_gateway_policy, dict):
        rendered_sections.append(_render_model_gateway_backpressure_policy(model_gateway_policy))
    return "".join(section for section in rendered_sections if section)


def _render_error_taxonomy_dashboard(readiness: dict[str, Any]) -> str:
    contract = readiness.get("dashboard_contract") if isinstance(readiness.get("dashboard_contract"), dict) else {}
    required_fields = contract.get("required_admin_runtime_fields")
    field_lines = ""
    if isinstance(required_fields, list):
        field_lines = "\n".join(f"- `{field}`" for field in required_fields)
    open_gaps = readiness.get("open_gaps")
    gap_lines = ""
    if isinstance(open_gaps, list):
        gap_lines = "\n".join(f"- `{gap}`" for gap in open_gaps)
    return (
        "\nEvidence:\n\n"
        f"- `{readiness.get('schema_version')}` status `{readiness.get('status')}`\n"
        f"- dashboard contract `{contract.get('schema_version')}`\n"
        f"- active dashboard policy `{readiness.get('active_dashboard_policy')}`\n"
        f"- does not close G9 `{contract.get('does_not_close_g9')}`\n"
        "Required Admin Runtime fields:\n\n"
        f"{field_lines}\n\n"
        "Nested error taxonomy dashboard gaps:\n\n"
        f"{gap_lines}\n"
    )


def _render_model_gateway_backpressure_policy(policy: dict[str, Any]) -> str:
    fields = policy.get("required_admin_runtime_fields")
    field_lines = ""
    if isinstance(fields, list):
        field_lines = "\n".join(f"- `{field}`" for field in fields)
    return (
        "\nEvidence:\n\n"
        f"- model gateway backpressure policy `{policy.get('schema_version')}` status "
        f"`{policy.get('status')}`\n"
        f"- config signal `{policy.get('config_signal')}`\n"
        f"- required load-test gate `{policy.get('required_load_test_gate')}`\n"
        f"- enforcement status `{policy.get('enforcement_status')}`\n"
        f"- does not raise defaults `{policy.get('does_not_raise_defaults')}`\n"
        "Required Admin Runtime fields:\n\n"
        f"{field_lines}\n"
    )


def _render_release_evidence(release: dict[str, Any]) -> str:
    location = release.get("export_location") if isinstance(release.get("export_location"), dict) else {}
    contract = release.get("evidence_contract") if isinstance(release.get("evidence_contract"), dict) else {}
    retention = release.get("retention_policy") if isinstance(release.get("retention_policy"), dict) else {}
    open_gaps = release.get("open_gaps")
    gap_lines = ""
    if isinstance(open_gaps, list):
        gap_lines = "\n".join(f"- `{gap}`" for gap in open_gaps)
    artifact_kinds = contract.get("accepted_artifact_kinds")
    artifact_lines = ""
    if isinstance(artifact_kinds, list):
        artifact_lines = "\n".join(f"- `{kind}`" for kind in artifact_kinds)
    delete_targets = retention.get("forbidden_delete_targets")
    delete_target_lines = ""
    if isinstance(delete_targets, list):
        delete_target_lines = "\n".join(f"- `{target}`" for target in delete_targets)
    return (
        "\nEvidence:\n\n"
        f"- `{release.get('schema_version')}` status `{release.get('status')}`\n"
        f"- export location `{location.get('path')}` index `{location.get('index')}`\n"
        f"- evidence entry schema `{contract.get('schema_version')}` at `{contract.get('write_path')}`\n"
        f"- retention policy `{retention.get('schema_version')}` status `{retention.get('status')}`\n"
        f"- does not close G9 `{contract.get('does_not_close_g9')}`\n"
        "Nested release-evidence gaps:\n\n"
        f"{gap_lines}\n\n"
        "Accepted artifact kinds:\n\n"
        f"{artifact_lines}\n\n"
        "Forbidden retention delete targets:\n\n"
        f"{delete_target_lines}\n"
    )


def _render_trace_audit_export(trace_export: dict[str, Any]) -> str:
    contract = trace_export.get("export_contract") if isinstance(trace_export.get("export_contract"), dict) else {}
    open_gaps = trace_export.get("open_gaps")
    gap_lines = ""
    if isinstance(open_gaps, list):
        gap_lines = "\n".join(f"- `{gap}`" for gap in open_gaps)
    event_sources = contract.get("allowed_event_sources")
    source_lines = ""
    if isinstance(event_sources, list):
        source_lines = "\n".join(f"- `{source}`" for source in event_sources)
    return (
        "\nEvidence:\n\n"
        f"- `{trace_export.get('schema_version')}` status `{trace_export.get('status')}`\n"
        f"- export contract `{contract.get('schema_version')}` at `{contract.get('write_path')}`\n"
        f"- active export policy `{trace_export.get('active_export_policy')}`\n"
        f"- does not close G9 `{contract.get('does_not_close_g9')}`\n"
        "Allowed event sources:\n\n"
        f"{source_lines}\n\n"
        "Nested trace/audit export gaps:\n\n"
        f"{gap_lines}\n"
    )


def _render_alert_slo_evidence(alert_rules: dict[str, Any]) -> str:
    rules = alert_rules.get("rules")
    if not isinstance(rules, list):
        return ""
    delivery_policy = (
        alert_rules.get("delivery_channel_policy")
        if isinstance(alert_rules.get("delivery_channel_policy"), dict)
        else {}
    )
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
        f"- delivery channel policy `{delivery_policy.get('schema_version')}` status "
        f"`{delivery_policy.get('status')}`\n"
        f"- does not enable alert delivery `{delivery_policy.get('does_not_enable_alert_delivery')}`\n"
        "Nested alert gaps:\n\n"
        f"{gap_lines}\n\n"
        "Template rules:\n\n"
        f"{rule_lines}\n"
    )


def _render_quality_golden_set_evidence(quality: dict[str, Any]) -> str:
    scenarios = quality.get("scenario_catalog")
    scenario_lines = ""
    if isinstance(scenarios, list):
        scenario_lines = "\n".join(
            f"- `{scenario.get('id')}`: {scenario.get('workflow')}"
            for scenario in scenarios
            if isinstance(scenario, dict)
        )
    contract = quality.get("evidence_contract") if isinstance(quality.get("evidence_contract"), dict) else {}
    required_fields = contract.get("required_fields") if isinstance(contract.get("required_fields"), list) else []
    field_lines = "\n".join(f"- `{field}`" for field in required_fields)
    open_gaps = quality.get("open_gaps")
    gap_lines = ""
    if isinstance(open_gaps, list):
        gap_lines = "\n".join(f"- `{gap}`" for gap in open_gaps)
    return (
        "\nEvidence:\n\n"
        f"- `{quality.get('schema_version')}` status `{quality.get('status')}`\n"
        f"- active eval policy `{quality.get('active_eval_policy')}`\n"
        f"- evidence contract `{contract.get('schema_version')}` at `{contract.get('write_path')}`\n"
        f"- does not close G9 `{contract.get('does_not_close_g9')}`\n"
        "Nested quality gaps:\n\n"
        f"{gap_lines}\n\n"
        "Golden-set scenarios:\n\n"
        f"{scenario_lines}\n\n"
        "Required eval evidence fields:\n\n"
        f"{field_lines}\n"
    )
