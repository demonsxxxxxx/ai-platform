from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.alert-trace-export-runtime-acceptance.v1"
ADMIN_RUNTIME_ROUTE = "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false"
OBSERVABILITY_SCHEMA_VERSION = "ai-platform.observability-readiness.v1"
TRACE_EXPORT_CONTRACT_SCHEMA_VERSION = "ai-platform.trace-audit-export-contract.v1"
PUBLIC_TRACE_EXPORT_SOURCES = {
    "run_event_public_projection",
    "audit_event_public_projection",
    "admin_runtime_observability_summary",
    "release_evidence_entry",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_implemented(domain: dict[str, Any], name: str) -> bool:
    return name in {str(item) for item in _as_list(domain.get("implemented"))}


def _alerts_and_exports_summary(
    payload: Any,
    *,
    expected_tenant_id: str,
    forbidden_projection_terms_present: bool,
) -> dict[str, object]:
    body = _as_dict(payload)
    observability = _as_dict(body.get("observability_readiness"))
    domains = _as_dict(observability.get("domains"))
    alerts_domain = _as_dict(domains.get("alerts_and_exports"))
    evidence = _as_dict(alerts_domain.get("evidence"))
    alert_rules = _as_dict(evidence.get("alert_slo_rules"))
    delivery_policy = _as_dict(alert_rules.get("delivery_channel_policy"))
    alert_summary = _as_dict(alert_rules.get("summary"))
    trace_export = _as_dict(evidence.get("trace_audit_export"))
    trace_contract = _as_dict(trace_export.get("export_contract"))
    trace_sources = {str(item) for item in _as_list(trace_contract.get("allowed_event_sources"))}
    gaps = {str(item) for item in _as_list(alerts_domain.get("gaps"))}

    return {
        "route": ADMIN_RUNTIME_ROUTE,
        "status": 200,
        "expected_status": 200,
        "tenant_id": str(body.get("tenant_id") or ""),
        "tenant_matches_requested": str(body.get("tenant_id") or "") == expected_tenant_id,
        "observability_schema_version": str(observability.get("schema_version") or ""),
        "alerts_domain_status": str(alerts_domain.get("status") or ""),
        "alert_rules_status": str(alert_rules.get("status") or ""),
        "alert_rule_count": alert_summary.get("rule_count"),
        "alert_delivery_policy_status": str(delivery_policy.get("status") or ""),
        "alert_delivery_not_enabled": delivery_policy.get("does_not_enable_alert_delivery") is True,
        "slo_threshold_runtime_calibration_gap_present": (
            "slo_threshold_runtime_calibration" in gaps
        ),
        "trace_export_status": str(trace_export.get("status") or ""),
        "trace_export_contract_schema_version": str(trace_contract.get("schema_version") or ""),
        "trace_export_not_raw_runtime_payloads": (
            trace_contract.get("does_not_export_raw_runtime_payloads") is True
        ),
        "trace_export_sources_public_only": bool(trace_sources)
        and trace_sources.issubset(PUBLIC_TRACE_EXPORT_SOURCES),
        "implemented_alert_template": _has_implemented(
            alerts_domain,
            "alert_slo_rule_template_evidence",
        ),
        "implemented_alert_delivery_policy": _has_implemented(
            alerts_domain,
            "alert_delivery_channel_policy_contract",
        ),
        "implemented_trace_export_contract": _has_implemented(
            alerts_domain,
            "trace_audit_export_contract",
        ),
        "forbidden_projection_terms_present": forbidden_projection_terms_present,
    }


def acceptance_is_valid(acceptance: dict[str, Any]) -> bool:
    """Return whether a redacted 211 alert/trace acceptance summary satisfies S1."""
    checks = acceptance.get("checks")
    if not isinstance(checks, dict):
        return False
    ordinary = _as_dict(checks.get("ordinary_admin_runtime"))
    admin = _as_dict(checks.get("admin_runtime_alerts_and_exports"))
    return (
        acceptance.get("schema_version") == SCHEMA_VERSION
        and acceptance.get("ok") is True
        and acceptance.get("status") == "accepted_for_operator_review"
        and acceptance.get("redaction_scan_status") == "passed"
        and acceptance.get("open_gaps") == []
        and acceptance.get("does_not_enable_alert_delivery") is True
        and acceptance.get("does_not_export_raw_runtime_payloads") is True
        and acceptance.get("does_not_close_g9") is True
        and ordinary.get("status") == 403
        and ordinary.get("expected_status") == 403
        and admin.get("status") == 200
        and admin.get("expected_status") == 200
        and admin.get("tenant_matches_requested") is True
        and admin.get("observability_schema_version") == OBSERVABILITY_SCHEMA_VERSION
        and admin.get("alerts_domain_status") in {"partial_blocked", "ready_for_verification"}
        and admin.get("alert_rules_status") == "partial_blocked"
        and admin.get("alert_rule_count") == 7
        and admin.get("alert_delivery_policy_status") == "contract_only_not_enabled"
        and admin.get("alert_delivery_not_enabled") is True
        and admin.get("slo_threshold_runtime_calibration_gap_present") is True
        and admin.get("trace_export_status") == "partial_blocked"
        and admin.get("trace_export_contract_schema_version") == TRACE_EXPORT_CONTRACT_SCHEMA_VERSION
        and admin.get("trace_export_not_raw_runtime_payloads") is True
        and admin.get("trace_export_sources_public_only") is True
        and admin.get("forbidden_projection_terms_present") is False
    )


def acceptance_summary(acceptance: dict[str, Any]) -> dict[str, Any]:
    """Return a safe subset of alert/trace acceptance evidence."""
    checks = _as_dict(acceptance.get("checks"))
    ordinary = _as_dict(checks.get("ordinary_admin_runtime"))
    admin = _as_dict(checks.get("admin_runtime_alerts_and_exports"))
    return {
        "schema_version": acceptance.get("schema_version"),
        "ok": acceptance.get("ok"),
        "status": acceptance.get("status"),
        "redaction_scan_status": acceptance.get("redaction_scan_status"),
        "ordinary_admin_runtime_status": ordinary.get("status"),
        "admin_runtime_status": admin.get("status"),
        "observability_schema_version": admin.get("observability_schema_version"),
        "alerts_domain_status": admin.get("alerts_domain_status"),
        "alert_rule_count": admin.get("alert_rule_count"),
        "alert_delivery_policy_status": admin.get("alert_delivery_policy_status"),
        "alert_delivery_not_enabled": admin.get("alert_delivery_not_enabled"),
        "slo_threshold_runtime_calibration_gap_present": admin.get(
            "slo_threshold_runtime_calibration_gap_present"
        ),
        "trace_export_status": admin.get("trace_export_status"),
        "trace_export_contract_schema_version": admin.get("trace_export_contract_schema_version"),
        "trace_export_not_raw_runtime_payloads": admin.get("trace_export_not_raw_runtime_payloads"),
        "trace_export_sources_public_only": admin.get("trace_export_sources_public_only"),
        "open_gaps": list(acceptance.get("open_gaps") or []),
        "does_not_enable_alert_delivery": acceptance.get("does_not_enable_alert_delivery"),
        "does_not_export_raw_runtime_payloads": acceptance.get(
            "does_not_export_raw_runtime_payloads"
        ),
        "does_not_close_g9": acceptance.get("does_not_close_g9"),
    }
