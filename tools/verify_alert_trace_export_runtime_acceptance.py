#!/usr/bin/env python3
"""Verify G9 alert delivery and trace export runtime acceptance for the POC loop."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.alert_trace_export_runtime_acceptance import (  # noqa: E402
    ADMIN_RUNTIME_ROUTE,
    SCHEMA_VERSION,
    _alerts_and_exports_summary,
)
from tools.verify_auth_rbac_smoke import (  # noqa: E402
    _contains_forbidden_projection_term,
    _detail,
    _principal_headers,
    _request_json,
    sanitize_base_url,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _acceptance_ok(summary: dict[str, object], *, ordinary_status: int, ordinary_detail: str) -> bool:
    return (
        ordinary_status == 403
        and ordinary_detail == "not_ai_admin"
        and summary.get("status") == 200
        and summary.get("tenant_matches_requested") is True
        and summary.get("observability_schema_version") == "ai-platform.observability-readiness.v1"
        and summary.get("alerts_domain_status") in {"partial_blocked", "ready_for_verification"}
        and summary.get("alert_rules_status") == "partial_blocked"
        and summary.get("alert_rule_count") == 7
        and summary.get("alert_delivery_policy_status") == "contract_only_not_enabled"
        and summary.get("alert_delivery_not_enabled") is True
        and summary.get("slo_threshold_runtime_calibration_gap_present") is True
        and summary.get("trace_export_status") == "partial_blocked"
        and summary.get("trace_export_contract_schema_version")
        == "ai-platform.trace-audit-export-contract.v1"
        and summary.get("trace_export_not_raw_runtime_payloads") is True
        and summary.get("trace_export_sources_public_only") is True
        and summary.get("implemented_alert_template") is True
        and summary.get("implemented_alert_delivery_policy") is True
        and summary.get("implemented_trace_export_contract") is True
        and summary.get("forbidden_projection_terms_present") is False
    )


def _open_gaps(summary: dict[str, object], *, ordinary_status: int, ordinary_detail: str) -> list[str]:
    gaps: list[str] = []
    if ordinary_status != 403 or ordinary_detail != "not_ai_admin":
        gaps.append("alert_trace_export_ordinary_admin_runtime_rbac")
    if summary.get("status") != 200 or summary.get("tenant_matches_requested") is not True:
        gaps.append("alert_trace_export_admin_runtime_projection")
    if (
        summary.get("observability_schema_version") != "ai-platform.observability-readiness.v1"
        or summary.get("alerts_domain_status") not in {"partial_blocked", "ready_for_verification"}
    ):
        gaps.append("observability_readiness_alerts_and_exports_projection")
    if (
        summary.get("alert_rules_status") != "partial_blocked"
        or summary.get("alert_rule_count") != 7
        or summary.get("implemented_alert_template") is not True
    ):
        gaps.append("alert_rules_runtime_dashboard_and_211_acceptance")
    if (
        summary.get("alert_delivery_policy_status") != "contract_only_not_enabled"
        or summary.get("alert_delivery_not_enabled") is not True
        or summary.get("implemented_alert_delivery_policy") is not True
    ):
        gaps.append("alert_delivery_channel_runtime_acceptance")
    if summary.get("slo_threshold_runtime_calibration_gap_present") is not True:
        gaps.append("slo_threshold_runtime_calibration")
    if (
        summary.get("trace_export_status") != "partial_blocked"
        or summary.get("trace_export_contract_schema_version")
        != "ai-platform.trace-audit-export-contract.v1"
        or summary.get("trace_export_not_raw_runtime_payloads") is not True
        or summary.get("trace_export_sources_public_only") is not True
        or summary.get("implemented_trace_export_contract") is not True
    ):
        gaps.append("trace_audit_export_211_acceptance")
    if summary.get("forbidden_projection_terms_present") is True:
        gaps.append("alert_trace_export_projection_redaction")
    return list(dict.fromkeys(gaps))


def build_alert_trace_export_runtime_acceptance(
    *,
    base_url: str,
    gateway_secret: str,
    commit_sha: str = "unknown",
    runtime_subject_commit_sha: str = "",
    image: str = "",
    tenant_id: str = "default",
    ordinary_user_id: str = "poc-ordinary-alert-trace-smoke",
    admin_user_id: str = "poc-admin-alert-trace-smoke",
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    """Build a redacted G9 alert/trace runtime acceptance summary."""
    safe_base_url = sanitize_base_url(base_url)
    ordinary_status, ordinary_payload = _request_json(
        f"{safe_base_url}{ADMIN_RUNTIME_ROUTE}",
        headers=_principal_headers(
            user_id=ordinary_user_id,
            roles="user",
            tenant_id=tenant_id,
            gateway_secret=gateway_secret,
        ),
        timeout_seconds=timeout_seconds,
    )
    admin_status, admin_payload = _request_json(
        f"{safe_base_url}{ADMIN_RUNTIME_ROUTE}",
        headers=_principal_headers(
            user_id=admin_user_id,
            roles="admin",
            tenant_id=tenant_id,
            gateway_secret=gateway_secret,
        ),
        timeout_seconds=timeout_seconds,
    )
    forbidden = _contains_forbidden_projection_term(ordinary_payload) or _contains_forbidden_projection_term(
        admin_payload
    )
    admin_summary = _alerts_and_exports_summary(
        _as_dict(admin_payload),
        expected_tenant_id=tenant_id,
        forbidden_projection_terms_present=forbidden,
    )
    admin_summary["status"] = admin_status
    ordinary_detail = _detail(ordinary_payload, redactions=(gateway_secret,))
    open_gaps = _open_gaps(
        admin_summary,
        ordinary_status=ordinary_status,
        ordinary_detail=ordinary_detail,
    )
    ok = not open_gaps and _acceptance_ok(
        admin_summary,
        ordinary_status=ordinary_status,
        ordinary_detail=ordinary_detail,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "status": "accepted_for_operator_review" if ok else "blocked_runtime_acceptance",
        "redaction_scan_status": "failed" if forbidden else "passed",
        "source": {
            "base_url": safe_base_url,
            "commit_sha": commit_sha,
            "runtime_subject_commit_sha": runtime_subject_commit_sha or commit_sha,
            "image": image,
            "tenant_id": tenant_id,
            "gateway_secret_supplied": bool(gateway_secret),
        },
        "checks": {
            "ordinary_admin_runtime": {
                "route": ADMIN_RUNTIME_ROUTE,
                "status": ordinary_status,
                "detail": ordinary_detail,
                "expected_status": 403,
            },
            "admin_runtime_alerts_and_exports": admin_summary,
        },
        "open_gaps": open_gaps,
        "does_not_enable_alert_delivery": True,
        "does_not_export_raw_runtime_payloads": True,
        "does_not_close_g9": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify G9 alert/trace runtime acceptance for ai-platform.")
    parser.add_argument("--base-url", default=os.environ.get("AI_PLATFORM_BASE_URL", "http://127.0.0.1:8020"))
    parser.add_argument("--gateway-secret-env", default="AI_PLATFORM_GATEWAY_SECRET")
    parser.add_argument("--commit-sha", default=os.environ.get("AI_PLATFORM_COMMIT_SHA", "unknown"))
    parser.add_argument(
        "--runtime-subject-commit-sha",
        default=os.environ.get("AI_PLATFORM_RUNTIME_SUBJECT_COMMIT_SHA", ""),
    )
    parser.add_argument("--image", default=os.environ.get("AI_PLATFORM_IMAGE", ""))
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--ordinary-user-id", default="poc-ordinary-alert-trace-smoke")
    parser.add_argument("--admin-user-id", default="poc-admin-alert-trace-smoke")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()

    evidence = build_alert_trace_export_runtime_acceptance(
        base_url=args.base_url,
        gateway_secret=os.environ.get(args.gateway_secret_env, ""),
        commit_sha=args.commit_sha,
        runtime_subject_commit_sha=args.runtime_subject_commit_sha,
        image=args.image,
        tenant_id=args.tenant_id,
        ordinary_user_id=args.ordinary_user_id,
        admin_user_id=args.admin_user_id,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
