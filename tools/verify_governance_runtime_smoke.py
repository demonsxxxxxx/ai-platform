#!/usr/bin/env python3
"""Verify Admin Runtime governance projection behavior for the 211 POC loop."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verify_auth_rbac_smoke import (
    _contains_forbidden_projection_term,
    _detail,
    _principal_headers,
    _request_json,
    sanitize_base_url,
)


SCHEMA_VERSION = "ai-platform.governance-runtime-smoke.v1"
ADMIN_RUNTIME_ROUTE = "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false"
GOVERNANCE_SCHEMA_VERSION = "ai-platform.governance-readiness.v1"
REQUIRED_GOVERNANCE_DOMAINS = (
    "tool_permission",
    "skill_governance",
    "memory_governance",
)
ALLOWED_GOVERNANCE_STATUSES = {"partial_blocked", "ready_for_verification"}
ADDITIONAL_FORBIDDEN_VALUE_MARKERS = (
    ".claude/skills",
    "executor private payload",
    "executor-private payload",
    "executor_private_payload",
    "runtime private payload",
    "runtime-private payload",
    "runtime_private_payload",
    "sandbox workspace root",
    "sandbox_workspace_root",
)
ADDITIONAL_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Za-z]:\\Users\\", re.IGNORECASE),
    re.compile(r"/home/[^/\s]+/(?:\.claude|\.codex|ai-platform-phaseb/staging)\b", re.IGNORECASE),
)
STRICT_PRIVATE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:executor[_-]?private[_-]?payload|runtime[_-]?private[_-]?payload|raw[_-]?storage[_-]?key|sandbox[_-]?workdir|storage[_-]?key)\s*[:=]",
        re.IGNORECASE,
    ),
)
ALLOWED_POLICY_TEXT_PATH_PARTS = {
    "forbidden_delete_targets",
    "forbidden_marker_classes",
    "forbidden_payload_classes",
    "next_checks",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_implemented(domain: dict[str, Any], name: str) -> bool:
    return name in {str(item) for item in _as_list(domain.get("implemented"))}


def _is_allowed_policy_text_path(path: tuple[str, ...]) -> bool:
    return any(part in ALLOWED_POLICY_TEXT_PATH_PARTS for part in path)


def _contains_strict_private_value(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in STRICT_PRIVATE_VALUE_PATTERNS)


def _contains_key_recursive(value: Any, key_name: str) -> bool:
    if isinstance(value, dict):
        return any(
            str(key) == key_name or _contains_key_recursive(item, key_name)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key_recursive(item, key_name) for item in value)
    return False


def _has_additional_forbidden_marker(payload: Any) -> bool:
    def walk(value: Any, path: tuple[str, ...] = ()) -> bool:
        if isinstance(value, dict):
            return any(
                walk(key, (*path, str(key), "$key")) or walk(item, (*path, str(key)))
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(walk(item, (*path, "[]")) for item in value)
        if isinstance(value, str):
            text = value.lower()
            if _contains_strict_private_value(value):
                return True
            if (
                any(marker in text for marker in ADDITIONAL_FORBIDDEN_VALUE_MARKERS)
                and not _is_allowed_policy_text_path(path)
            ):
                return True
            return any(pattern.search(value) is not None for pattern in ADDITIONAL_FORBIDDEN_PATTERNS)
        return False

    return walk(payload)


def _contains_forbidden_governance_projection_term(payload: Any) -> bool:
    return _contains_forbidden_projection_term(payload) or _has_additional_forbidden_marker(payload)


def _tool_permission_summary(domain: dict[str, Any]) -> dict[str, object]:
    evidence = _as_dict(domain.get("evidence"))
    return {
        "domain_status": str(domain.get("status") or ""),
        "taxonomy_present": isinstance(evidence.get("tool_policy_taxonomy"), dict),
        "bulk_review_present": isinstance(evidence.get("admin_policy_bulk_review_dashboard"), dict),
        "implemented_policy_taxonomy": _has_implemented(
            domain,
            "tool_allow_deny_ask_policy_taxonomy_evidence",
        ),
        "implemented_bulk_review_dashboard": _has_implemented(
            domain,
            "admin_policy_bulk_review_dashboard_contract",
        ),
        "gap_count": len(_as_list(domain.get("gaps"))),
    }


def _skill_governance_summary(domain: dict[str, Any]) -> dict[str, object]:
    evidence = _as_dict(domain.get("evidence"))
    dashboard = _as_dict(evidence.get("admin_skill_release_dashboard"))
    return {
        "domain_status": str(domain.get("status") or ""),
        "release_readiness_present": isinstance(evidence.get("release_readiness"), dict),
        "dashboard_present": isinstance(evidence.get("admin_skill_release_dashboard"), dict),
        "dashboard_contract_exposed": _contains_key_recursive(evidence, "dashboard_contract"),
        "implemented_version_registry": _has_implemented(domain, "skill_version_registry"),
        "implemented_snapshot_lock": _has_implemented(domain, "skill_snapshot_and_release_decision_lock"),
        "gap_count": len(_as_list(domain.get("gaps"))),
    }


def _memory_governance_summary(domain: dict[str, Any]) -> dict[str, object]:
    evidence = _as_dict(domain.get("evidence"))
    return {
        "domain_status": str(domain.get("status") or ""),
        "long_term_fail_closed_present": _has_implemented(
            domain,
            "long_term_cross_session_memory_default_fail_closed",
        ),
        "context_provenance_present": _has_implemented(
            domain,
            "context_snapshot_public_provenance_projection_contract",
        ),
        "office_context_readiness_present": isinstance(evidence.get("office_context_pack_readiness"), dict),
        "gap_count": len(_as_list(domain.get("gaps"))),
    }


def _admin_governance_summary(payload: Any, *, expected_tenant_id: str) -> dict[str, object]:
    body = _as_dict(payload)
    governance = _as_dict(body.get("governance"))
    domains = _as_dict(governance.get("domains"))
    missing_domains = [name for name in REQUIRED_GOVERNANCE_DOMAINS if name not in domains]
    tool_permission = _tool_permission_summary(_as_dict(domains.get("tool_permission")))
    skill_governance = _skill_governance_summary(_as_dict(domains.get("skill_governance")))
    memory_governance = _memory_governance_summary(_as_dict(domains.get("memory_governance")))
    governance_status = str(governance.get("status") or "")
    return {
        "tenant_id": str(body.get("tenant_id") or ""),
        "tenant_matches_requested": str(body.get("tenant_id") or "") == expected_tenant_id,
        "governance_schema_version": str(governance.get("schema_version") or ""),
        "governance_status": governance_status,
        "governance_status_allowed": governance_status in ALLOWED_GOVERNANCE_STATUSES,
        "required_domains_present": not missing_domains,
        "missing_domains": missing_domains,
        "tool_permission": tool_permission,
        "skill_governance": skill_governance,
        "memory_governance": memory_governance,
        "open_gap_count": len(_as_list(governance.get("open_gaps"))),
        "forbidden_projection_terms_present": _contains_forbidden_governance_projection_term(payload),
    }


def _governance_summary_ok(summary: dict[str, object]) -> bool:
    tool_permission = _as_dict(summary.get("tool_permission"))
    skill_governance = _as_dict(summary.get("skill_governance"))
    memory_governance = _as_dict(summary.get("memory_governance"))
    return (
        bool(summary.get("tenant_matches_requested"))
        and summary.get("governance_schema_version") == GOVERNANCE_SCHEMA_VERSION
        and bool(summary.get("governance_status_allowed"))
        and bool(summary.get("required_domains_present"))
        and bool(tool_permission.get("taxonomy_present"))
        and bool(tool_permission.get("bulk_review_present"))
        and bool(tool_permission.get("implemented_policy_taxonomy"))
        and bool(tool_permission.get("implemented_bulk_review_dashboard"))
        and bool(skill_governance.get("release_readiness_present"))
        and bool(skill_governance.get("dashboard_present"))
        and not bool(skill_governance.get("dashboard_contract_exposed"))
        and bool(skill_governance.get("implemented_version_registry"))
        and bool(skill_governance.get("implemented_snapshot_lock"))
        and bool(memory_governance.get("long_term_fail_closed_present"))
        and bool(memory_governance.get("context_provenance_present"))
        and bool(memory_governance.get("office_context_readiness_present"))
        and not bool(summary.get("forbidden_projection_terms_present"))
    )


def build_governance_runtime_smoke(
    *,
    base_url: str,
    gateway_secret: str,
    commit_sha: str = "unknown",
    runtime_subject_commit_sha: str = "",
    image: str = "",
    tenant_id: str = "default",
    ordinary_user_id: str = "poc-ordinary-governance-smoke",
    admin_user_id: str = "poc-admin-governance-smoke",
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    """Build a redacted Admin Runtime governance smoke summary."""
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
    admin_summary = _admin_governance_summary(admin_payload, expected_tenant_id=tenant_id)
    redaction_failed = any(
        [
            _contains_forbidden_governance_projection_term(ordinary_payload),
            bool(admin_summary["forbidden_projection_terms_present"]),
        ]
    )
    checks = {
        "ordinary_admin_runtime": {
            "route": ADMIN_RUNTIME_ROUTE,
            "status": ordinary_status,
            "detail": _detail(ordinary_payload, redactions=(gateway_secret,)),
            "expected_status": 403,
        },
        "admin_runtime_governance": {
            "route": ADMIN_RUNTIME_ROUTE,
            "status": admin_status,
            "expected_status": 200,
            **admin_summary,
        },
    }
    ok = (
        ordinary_status == 403
        and checks["ordinary_admin_runtime"]["detail"] == "not_ai_admin"
        and admin_status == 200
        and _governance_summary_ok(admin_summary)
        and not redaction_failed
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "redaction_scan_status": "failed" if redaction_failed else "passed",
        "source": {
            "base_url": safe_base_url,
            "commit_sha": commit_sha,
            "runtime_subject_commit_sha": runtime_subject_commit_sha or commit_sha,
            "image": image,
            "tenant_id": tenant_id,
            "gateway_secret_supplied": bool(gateway_secret),
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Admin Runtime governance projection behavior for ai-platform.")
    parser.add_argument("--base-url", default=os.environ.get("AI_PLATFORM_BASE_URL", "http://127.0.0.1:8020"))
    parser.add_argument("--gateway-secret-env", default="AI_PLATFORM_GATEWAY_SECRET")
    parser.add_argument("--commit-sha", default=os.environ.get("AI_PLATFORM_COMMIT_SHA", "unknown"))
    parser.add_argument(
        "--runtime-subject-commit-sha",
        default=os.environ.get("AI_PLATFORM_RUNTIME_SUBJECT_COMMIT_SHA", ""),
    )
    parser.add_argument("--image", default=os.environ.get("AI_PLATFORM_IMAGE", ""))
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--ordinary-user-id", default="poc-ordinary-governance-smoke")
    parser.add_argument("--admin-user-id", default="poc-admin-governance-smoke")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()

    evidence = build_governance_runtime_smoke(
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
