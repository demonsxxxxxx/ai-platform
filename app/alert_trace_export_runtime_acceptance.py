"""Validate and summarize archived alert/trace evidence for Foundation readers.

The live inventory producer is retired; these checks describe recorded subjects
only and are not an observer of the current Admin Runtime endpoint.
"""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "ai-platform.alert-trace-export-runtime-acceptance.v1"
OBSERVABILITY_SCHEMA_VERSION = "ai-platform.observability-readiness.v1"
TRACE_EXPORT_CONTRACT_SCHEMA_VERSION = "ai-platform.trace-audit-export-contract.v1"
def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def acceptance_is_valid(acceptance: dict[str, Any]) -> bool:
    """Validate a recorded redacted alert/trace summary under its archived S1 contract."""
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
