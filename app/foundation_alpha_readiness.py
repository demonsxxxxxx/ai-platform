from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai-platform.foundation-alpha-poc-readiness.v1"
STAGE_NAME = "Foundation Alpha POC"
RUNTIME_SUBJECT_COMMIT_SHA = "8c0cffca63bc747fad0a5771f209acc8a608ab9e"
_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_ROOT = _ROOT / "docs/release-evidence/foundation-alpha-poc" / RUNTIME_SUBJECT_COMMIT_SHA
_SMOKE_EVIDENCE = _EVIDENCE_ROOT / "2026-06-11-211-foundation-alpha-poc-current-main-smoke.json"
_AUTH_RBAC_EVIDENCE = _EVIDENCE_ROOT / "2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json"
_SOURCE_REVISION_MARKER = _ROOT / ".ai-platform-source-revision"

_OPEN_FOLLOWUPS = [
    "#21_recorded_capacity_evidence",
    "g7_docker_sandbox_hardening",
    "g8_ordinary_user_multi_agent_exposure",
    "g9_runtime_export_and_retention_acceptance",
    "packaged_frontend_image_release_acceptance",
    "broader_auth_session_rbac_tenant_redaction_regression",
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _status_from_gaps(gaps: list[str]) -> str:
    return "partial_followups_open" if gaps else "poc_verified_keep_under_regression"


def _resolve_source_tree_revision() -> str:
    if _SOURCE_REVISION_MARKER.exists():
        marker = _SOURCE_REVISION_MARKER.read_text(encoding="utf-8").strip()
        if marker:
            return marker
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _runtime_source_relation(source_tree_commit: str, runtime_subject_commit: str, runtime_source_marker: str) -> dict[str, Any]:
    runtime_matches_source_tree = (
        source_tree_commit != "unknown"
        and source_tree_commit == runtime_subject_commit
        and source_tree_commit == runtime_source_marker
    )
    return {
        "source_tree_commit_sha": source_tree_commit,
        "runtime_subject_commit_sha": runtime_subject_commit,
        "runtime_source_marker": runtime_source_marker,
        "runtime_matches_source_tree": runtime_matches_source_tree,
        "status": "runtime_current_for_source_tree"
        if runtime_matches_source_tree
        else "source_synced_runtime_pending",
    }


def _safe_runtime_check(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _artifact_review_summary(runtime_checks: dict[str, Any]) -> dict[str, Any]:
    document_review = _safe_runtime_check(runtime_checks.get("document_review_attachment_run"))
    return {
        "status": document_review.get("status"),
        "skill_id": document_review.get("skill_id"),
        "artifact_types": document_review.get("artifact_types") or [],
        "playback_contract_version": document_review.get("playback_contract_version"),
    }


def _projection_summary(runtime_checks: dict[str, Any]) -> dict[str, Any]:
    return {
        "frontend_http_status": _safe_runtime_check(runtime_checks.get("lambchat_frontend")).get("status"),
        "same_origin_api_health": _safe_runtime_check(runtime_checks.get("same_origin_api_health")),
        "forbidden_reference_count": _safe_runtime_check(runtime_checks.get("frontend_dist_api_boundary")).get(
            "forbidden_reference_count"
        ),
        "artifact_download_cross_user_statuses": _safe_runtime_check(
            runtime_checks.get("artifact_download_isolation")
        ).get("cross_user_statuses"),
        "artifact_preview_cross_user_statuses": _safe_runtime_check(
            runtime_checks.get("artifact_preview_isolation")
        ).get("cross_user_statuses"),
    }


def _auth_rbac_summary(runtime_checks: dict[str, Any]) -> dict[str, Any]:
    admin_runtime = _safe_runtime_check(runtime_checks.get("admin_runtime"))
    return {
        "unauthenticated_auth_me_status": _safe_runtime_check(runtime_checks.get("unauthenticated_auth_me")).get(
            "status"
        ),
        "ordinary_admin_runtime_status": _safe_runtime_check(runtime_checks.get("ordinary_admin_runtime")).get(
            "status"
        ),
        "admin_runtime_status": admin_runtime.get("status"),
        "admin_required_sections_present": admin_runtime.get("required_sections_present"),
        "admin_forbidden_projection_terms_present": admin_runtime.get("forbidden_projection_terms_present"),
    }


def _dependency_unavailable_summary(kind: str, exc: ModuleNotFoundError) -> dict[str, Any]:
    status_key = f"{kind}_readiness_status"
    return {
        status_key: "dependency_unavailable",
        "open_gap_count": 1,
        "dependency_error_class": exc.__class__.__name__,
    }


def _governance_dependency_unavailable_summary(exc: ModuleNotFoundError) -> dict[str, Any]:
    summary = _dependency_unavailable_summary("governance", exc)
    summary["ordinary_user_policy"] = "fail_closed_until_projection_mapping_and_acceptance_pass"
    return summary


def _observability_dependency_unavailable_summary(exc: ModuleNotFoundError) -> dict[str, Any]:
    summary = _dependency_unavailable_summary("observability", exc)
    summary["admin_runtime_projection"] = "/api/ai/admin/runtime/overview"
    return summary


def _build_governance_summary(settings: object | None) -> dict[str, Any]:
    try:
        from app.governance_readiness import build_governance_readiness
    except ModuleNotFoundError as exc:
        return _governance_dependency_unavailable_summary(exc)

    governance = build_governance_readiness(settings, include_frontend_projection_audit=False)
    return {
        "governance_readiness_status": governance["status"],
        "ordinary_user_policy": governance["ordinary_user_policy"],
        "open_gap_count": len(governance["open_gaps"]),
    }


def _build_observability_summary(settings: object | None) -> dict[str, Any]:
    try:
        from app.observability_readiness import build_observability_readiness
    except ModuleNotFoundError as exc:
        return _observability_dependency_unavailable_summary(exc)

    observability = build_observability_readiness(settings)
    return {
        "observability_readiness_status": observability["status"],
        "admin_runtime_projection": observability["admin_runtime_projection"],
        "open_gap_count": len(observability["open_gaps"]),
    }


def build_foundation_alpha_readiness(settings: object | None = None) -> dict[str, Any]:
    """Build a secret-safe Foundation Alpha POC readiness summary for operators."""
    smoke = _load_json(_SMOKE_EVIDENCE)
    auth_rbac = _load_json(_AUTH_RBAC_EVIDENCE)
    smoke_checks = smoke["evidence_ref"]["runtime_checks"]
    auth_checks = auth_rbac["evidence_ref"]["runtime_checks"]
    try:
        governance_summary = _build_governance_summary(settings)
    except ModuleNotFoundError as exc:
        governance_summary = _governance_dependency_unavailable_summary(exc)
    try:
        observability_summary = _build_observability_summary(settings)
    except ModuleNotFoundError as exc:
        observability_summary = _observability_dependency_unavailable_summary(exc)

    runtime_subject_commit = smoke["runtime_subject_commit_sha"]
    source_tree_commit = _resolve_source_tree_revision()
    runtime_source_marker = smoke["source_ref"]["runtime_source_marker"]
    runtime_relation = _runtime_source_relation(
        source_tree_commit=source_tree_commit,
        runtime_subject_commit=runtime_subject_commit,
        runtime_source_marker=runtime_source_marker,
    )
    runtime_matches_source_tree = runtime_relation["runtime_matches_source_tree"]
    domains = {
        "g0_g1_source_authority_security": {
            "status": "poc_verified_keep_under_regression"
            if runtime_matches_source_tree
            else "source_synced_runtime_pending",
            "evidence": {
                "runtime_subject_commit_sha": runtime_subject_commit,
                "source_tree_commit_sha": source_tree_commit,
                "runtime_source_marker": runtime_source_marker,
                "runtime_source_relation": runtime_relation["status"],
                "image": smoke["source_ref"]["image"],
                "image_id": smoke["source_ref"]["image_id"],
                "api_worker_label_revision": smoke["source_ref"]["image_labels"]["ai-platform.source-revision"],
                "auth_rbac": _auth_rbac_summary(auth_checks),
                "repo_local_env_present": smoke["source_ref"]["repo_local_env_present"],
            },
            "open_followups": [
                "broader_auth_session_rbac_tenant_redaction_regression",
            ],
        },
        "g2_g4_control_plane_contracts": {
            "status": "poc_verified_keep_under_regression",
            "evidence": {
                "artifact_download_isolation": _safe_runtime_check(
                    smoke_checks.get("artifact_download_isolation")
                ),
                "artifact_preview_isolation": _safe_runtime_check(
                    smoke_checks.get("artifact_preview_isolation")
                ),
                "public_playback_contract_version": _safe_runtime_check(
                    smoke_checks.get("document_review_attachment_run")
                ).get("playback_contract_version"),
                "private_payload_leaked": _safe_runtime_check(
                    smoke_checks.get("document_review_attachment_run")
                ).get("private_payload_leaked"),
            },
            "open_followups": [],
        },
        "g5_run_lifecycle_worker_runtime": {
            "status": "poc_verified_capacity_followups_open",
            "evidence": {
                "general_chat_run": smoke_checks.get("general_chat_run"),
                "upload_attachment_chat": _safe_runtime_check(smoke_checks.get("upload_attachment_chat")),
                "document_review_attachment_run": _artifact_review_summary(smoke_checks),
                "capacity_default_policy": "do_not_raise_without_recorded_load_test_evidence",
            },
            "open_followups": [
                "#21_recorded_capacity_evidence",
            ],
        },
        "g6_poc_governance": {
            "status": "partial_followups_open"
            if governance_summary["open_gap_count"]
            else "poc_verified_keep_under_regression",
            "evidence": {
                **governance_summary,
                "skill_snapshot_run_seen": True,
                "tool_permission_decision_audit_required": True,
                "memory_long_term_default_fail_closed": True,
            },
            "open_followups": [
                "runtime_admin_dashboard_acceptance_for_governance",
                "signed_skill_package_or_sbom_review_evidence",
            ],
        },
        "g9_admin_runtime_observability": {
            "status": "partial_followups_open"
            if observability_summary["open_gap_count"]
            else "poc_verified_keep_under_regression",
            "evidence": {
                **observability_summary,
                "release_evidence_result": smoke["evidence_ref"]["result"],
            },
            "open_followups": [
                "g9_runtime_export_and_retention_acceptance",
                "alert_delivery_and_trace_export_211_acceptance",
            ],
        },
        "frontend_poc": {
            "status": "poc_verified_packaged_release_followup_open",
            "evidence": _projection_summary(smoke_checks),
            "open_followups": [
                "packaged_frontend_image_release_acceptance",
                "ordinary_user_acceptance_for_quarantined_legacy_routes",
            ],
        },
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE_NAME,
        "status": "211_verified_followups_open"
        if runtime_matches_source_tree
        else "211_source_synced_runtime_pending_followups_open",
        "source_tree_commit_sha": source_tree_commit,
        "runtime_subject_commit_sha": runtime_subject_commit,
        "runtime_source_relation": runtime_relation,
        "runtime_image": smoke["source_ref"]["image"],
        "evidence_entries": {
            "poc_smoke": str(_SMOKE_EVIDENCE.relative_to(_ROOT)).replace("\\", "/"),
            "auth_rbac_smoke": str(_AUTH_RBAC_EVIDENCE.relative_to(_ROOT)).replace("\\", "/"),
        },
        "decision": {
            "controlled_poc_loop_verified": True,
            "current_source_verified_by_running_runtime": runtime_matches_source_tree,
            "runtime_rollout_required_for_current_source": not runtime_matches_source_tree,
            "can_enter_next_stage_without_restrictions": False,
            "production_claim_allowed": False,
            "ordinary_user_multi_agent_allowed": False,
            "docker_sandbox_hardened_claim_allowed": False,
            "capacity_default_increase_allowed": False,
        },
        "domains": domains,
        "open_followups": list(_OPEN_FOLLOWUPS),
        "evidence_policy": "source_docs_tests_211_smoke_and_release_evidence_required_before_stage_closure",
    }


def render_foundation_alpha_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render Foundation Alpha POC readiness as operator-readable Markdown."""
    decision = readiness["decision"]
    decision_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in decision.items())
    followups = "\n".join(f"- {item}" for item in readiness["open_followups"])
    domain_sections: list[str] = []
    for name, domain in readiness["domains"].items():
        domain_followups = "\n".join(f"- {item}" for item in domain.get("open_followups", [])) or "- none"
        domain_sections.append(
            f"### {name}\n\n"
            f"Status: `{domain['status']}`\n\n"
            "Open followups:\n\n"
            f"{domain_followups}\n"
        )
    return (
        "# ai-platform Foundation Alpha POC Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Stage: `{readiness['stage']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Source tree: `{readiness['source_tree_commit_sha']}`\n\n"
        f"Runtime subject: `{readiness['runtime_subject_commit_sha']}`\n\n"
        f"Runtime source relation: `{readiness['runtime_source_relation']['status']}`\n\n"
        "## Current decision\n\n"
        f"{decision_lines}\n\n"
        "## Open Followups\n\n"
        f"{followups}\n\n"
        "## Domains\n\n"
        + "\n\n".join(domain_sections)
        + "\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )
