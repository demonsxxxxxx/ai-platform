from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai-platform.foundation-alpha-poc-readiness.v1"
SOURCE_SNAPSHOT_SCHEMA_VERSION = "ai-platform.source-snapshot.v1"
STAGE_NAME = "Foundation Alpha POC"
RUNTIME_SUBJECT_COMMIT_SHA = "8c0cffca63bc747fad0a5771f209acc8a608ab9e"
_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_BASE_ROOT = _ROOT / "docs/release-evidence/foundation-alpha-poc"
_EVIDENCE_ROOT = _EVIDENCE_BASE_ROOT / RUNTIME_SUBJECT_COMMIT_SHA
_SMOKE_EVIDENCE = _EVIDENCE_ROOT / "2026-06-11-211-foundation-alpha-poc-current-main-smoke.json"
_AUTH_RBAC_EVIDENCE = _EVIDENCE_ROOT / "2026-06-11-211-foundation-alpha-poc-current-main-auth-rbac-smoke.json"
_SOURCE_REVISION_MARKER = _ROOT / ".ai-platform-source-revision"
_SOURCE_SNAPSHOT_MARKER = _ROOT / ".ai-platform-source-snapshot.json"
_RUNTIME_NEUTRAL_PATH_PREFIXES = (
    "docs/",
    "tests/",
)
_RUNTIME_NEUTRAL_EXACT_PATHS = {
    ".gitignore",
    "app/foundation_alpha_readiness.py",
    "tools/foundation_alpha_readiness.py",
    "tools/verify_auth_rbac_smoke.py",
}

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


def _path_for_output(path: Path) -> str:
    try:
        return str(path.relative_to(_ROOT)).replace("\\", "/")
    except ValueError:
        return path.as_posix()


def _status_from_gaps(gaps: list[str]) -> str:
    return "partial_followups_open" if gaps else "poc_verified_keep_under_regression"


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        normalized = item.replace("\\", "/").strip()
        if normalized:
            result.append(normalized)
    return result


def _read_source_revision_marker() -> str | None:
    if not _SOURCE_REVISION_MARKER.exists():
        return None
    marker = _SOURCE_REVISION_MARKER.read_text(encoding="utf-8").strip()
    return marker or None


def _source_snapshot_marker_for_source_tree(source_tree_commit: str | None = None) -> dict[str, Any] | None:
    source_tree_commit = source_tree_commit or _read_source_revision_marker()
    if not source_tree_commit or source_tree_commit == "unknown" or not _SOURCE_SNAPSHOT_MARKER.exists():
        return None
    try:
        payload = _load_json(_SOURCE_SNAPSHOT_MARKER)
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema_version") != SOURCE_SNAPSHOT_SCHEMA_VERSION:
        return None
    if payload.get("source_tree_commit_sha") != source_tree_commit:
        return None
    if not isinstance(payload.get("source_tree_dirty"), bool):
        return None
    if _string_list(payload.get("runtime_affecting_changes_since_runtime_subject")) is None:
        return None
    if _string_list(payload.get("runtime_affecting_dirty_paths")) is None:
        return None
    return payload


def _resolve_source_tree_revision() -> str:
    marker = _read_source_revision_marker()
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


def _resolve_source_tree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        marker = _source_snapshot_marker_for_source_tree()
        return marker.get("source_tree_dirty") if marker else None
    return bool(result.stdout.strip())


def _resolve_source_tree_dirty_paths() -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        normalized = path.replace("\\", "/")
        if normalized:
            paths.append(normalized)
    return paths


def _is_runtime_affecting_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        return False
    if normalized in _RUNTIME_NEUTRAL_EXACT_PATHS:
        return False
    return not normalized.startswith(_RUNTIME_NEUTRAL_PATH_PREFIXES)


def _resolve_runtime_affecting_changes_since(runtime_subject_commit: str) -> list[str] | None:
    if runtime_subject_commit == "unknown":
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{runtime_subject_commit}..HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        marker = _source_snapshot_marker_for_source_tree()
        if marker is None or marker.get("runtime_subject_commit_sha") != runtime_subject_commit:
            return None
        return _string_list(marker.get("runtime_affecting_changes_since_runtime_subject"))
    paths = [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]
    return [path for path in paths if _is_runtime_affecting_path(path)]


def _resolve_runtime_affecting_dirty_paths() -> list[str] | None:
    dirty_paths = _resolve_source_tree_dirty_paths()
    if dirty_paths is None:
        marker = _source_snapshot_marker_for_source_tree()
        if marker is None:
            return None
        return _string_list(marker.get("runtime_affecting_dirty_paths"))
    return [path for path in dirty_paths if _is_runtime_affecting_path(path)]


def _release_evidence_entry_is_valid(payload: dict[str, Any], commit_sha: str) -> bool:
    source_ref = payload.get("source_ref")
    evidence_ref = payload.get("evidence_ref")
    if not isinstance(source_ref, dict) or not isinstance(evidence_ref, dict):
        return False

    labels = source_ref.get("image_labels")
    return (
        payload.get("schema_version") == "ai-platform.release-evidence-entry.v1"
        and payload.get("gate") == STAGE_NAME
        and payload.get("artifact_kind") == "211_runtime_smoke"
        and payload.get("commit_sha") == commit_sha
        and payload.get("runtime_subject_commit_sha") == commit_sha
        and payload.get("redaction_scan_status") == "passed"
        and payload.get("review_status") == "reviewed"
        and source_ref.get("runtime_source_marker") == commit_sha
        and isinstance(labels, dict)
        and labels.get("ai-platform.source-revision") == commit_sha
        and labels.get("org.opencontainers.image.revision") == commit_sha
        and evidence_ref.get("result") == "ok:true"
        and isinstance(evidence_ref.get("runtime_checks"), dict)
    )


def _is_auth_rbac_evidence(payload: dict[str, Any]) -> bool:
    evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    if not isinstance(runtime_checks, dict):
        return False
    return (
        evidence_ref.get("verifier") == "tools/verify_auth_rbac_smoke.py"
        or {"unauthenticated_auth_me", "ordinary_admin_runtime", "admin_runtime"}.issubset(runtime_checks)
        or "auth-rbac" in str(payload.get("evidence_id", ""))
    )


def _is_poc_smoke_evidence(payload: dict[str, Any]) -> bool:
    evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    if not isinstance(runtime_checks, dict) or _is_auth_rbac_evidence(payload):
        return False
    return (
        evidence_ref.get("verifier") == "tools/verify_poc_gate.py"
        or "document_review_attachment_run" in runtime_checks
        or "lambchat_frontend" in runtime_checks
        or "frontend_http_status" in runtime_checks
    )


def _release_evidence_sort_key(path: Path, payload: dict[str, Any]) -> tuple[str, str]:
    return (str(payload.get("captured_at", "")), path.name)


def _discover_release_evidence_pair(commit_sha: str) -> tuple[Path, Path] | None:
    commit_root = _EVIDENCE_BASE_ROOT / commit_sha
    if not commit_root.is_dir():
        return None

    smoke_entries: list[tuple[Path, dict[str, Any]]] = []
    auth_entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(commit_root.glob("*.json")):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not _release_evidence_entry_is_valid(payload, commit_sha):
            continue
        if _is_auth_rbac_evidence(payload):
            auth_entries.append((path, payload))
        elif _is_poc_smoke_evidence(payload):
            smoke_entries.append((path, payload))

    if not smoke_entries or not auth_entries:
        return None
    smoke_path, _ = max(smoke_entries, key=lambda item: _release_evidence_sort_key(item[0], item[1]))
    auth_path, _ = max(auth_entries, key=lambda item: _release_evidence_sort_key(item[0], item[1]))
    return smoke_path, auth_path


def _discover_latest_release_evidence_pair() -> tuple[Path, Path] | None:
    candidates: list[tuple[tuple[str, str], Path, Path]] = []
    if not _EVIDENCE_BASE_ROOT.is_dir():
        return None

    for commit_root in sorted(_EVIDENCE_BASE_ROOT.iterdir()):
        if not commit_root.is_dir():
            continue
        pair = _discover_release_evidence_pair(commit_root.name)
        if pair is None:
            continue
        smoke_path, auth_path = pair
        try:
            smoke_payload = _load_json(smoke_path)
            auth_payload = _load_json(auth_path)
        except (OSError, json.JSONDecodeError):
            continue
        candidates.append(
            (
                max(
                    _release_evidence_sort_key(smoke_path, smoke_payload),
                    _release_evidence_sort_key(auth_path, auth_payload),
                ),
                smoke_path,
                auth_path,
            )
        )

    if not candidates:
        return None
    _, smoke_path, auth_path = max(candidates, key=lambda item: item[0])
    return smoke_path, auth_path


def _resolve_release_evidence_paths(source_tree_commit: str) -> tuple[Path, Path]:
    if source_tree_commit != "unknown":
        current_pair = _discover_release_evidence_pair(source_tree_commit)
        if current_pair is not None:
            return current_pair
    latest_pair = _discover_latest_release_evidence_pair()
    if latest_pair is not None:
        return latest_pair
    return _SMOKE_EVIDENCE, _AUTH_RBAC_EVIDENCE


def _runtime_source_relation(
    source_tree_commit: str,
    source_tree_dirty: bool | None,
    runtime_subject_commit: str,
    runtime_source_marker: str,
    runtime_affecting_changes_since_runtime_subject: list[str] | None,
    runtime_affecting_dirty_paths: list[str] | None,
) -> dict[str, Any]:
    no_runtime_affecting_dirty_paths = runtime_affecting_dirty_paths == []
    runtime_matches_source_tree = (
        source_tree_commit != "unknown"
        and source_tree_dirty is False
        and source_tree_commit == runtime_subject_commit
        and source_tree_commit == runtime_source_marker
    )
    runtime_relevant_source_matches = (
        runtime_matches_source_tree
        or (
            source_tree_commit != "unknown"
            and source_tree_dirty is not None
            and no_runtime_affecting_dirty_paths
            and runtime_subject_commit == runtime_source_marker
            and runtime_affecting_changes_since_runtime_subject == []
        )
    )
    if source_tree_dirty is True and runtime_affecting_dirty_paths is None:
        status = "source_tree_uncommitted_changes_pending"
    elif source_tree_dirty is True and runtime_affecting_dirty_paths:
        status = "source_tree_runtime_affecting_uncommitted_changes_pending"
    elif runtime_matches_source_tree:
        status = "runtime_current_for_source_tree"
    elif runtime_relevant_source_matches:
        status = "runtime_current_for_runtime_relevant_source"
    else:
        status = "source_synced_runtime_pending"
    return {
        "source_tree_commit_sha": source_tree_commit,
        "source_tree_dirty": source_tree_dirty,
        "runtime_subject_commit_sha": runtime_subject_commit,
        "runtime_source_marker": runtime_source_marker,
        "runtime_matches_source_tree": runtime_matches_source_tree,
        "runtime_relevant_source_matches": runtime_relevant_source_matches,
        "runtime_affecting_changes_since_runtime_subject": runtime_affecting_changes_since_runtime_subject,
        "runtime_affecting_dirty_paths": runtime_affecting_dirty_paths,
        "status": status,
    }


def _verified_runtime_subject(smoke: dict[str, Any], evidence_scope: str) -> dict[str, Any]:
    source_ref = smoke["source_ref"]
    return {
        "commit_sha": smoke["runtime_subject_commit_sha"],
        "image": source_ref.get("image") or source_ref.get("runtime_image"),
        "image_id": source_ref.get("image_id"),
        "evidence_scope": evidence_scope,
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


def _top_level_status(runtime_relation_status: str, runtime_matches_source_tree: bool) -> str:
    if runtime_matches_source_tree:
        return "211_verified_followups_open"
    return f"{runtime_relation_status}_followups_open"


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
    source_tree_commit = _resolve_source_tree_revision()
    source_tree_dirty = _resolve_source_tree_dirty()
    runtime_affecting_dirty_paths = _resolve_runtime_affecting_dirty_paths()
    smoke_evidence_path, auth_rbac_evidence_path = _resolve_release_evidence_paths(source_tree_commit)
    smoke = _load_json(smoke_evidence_path)
    auth_rbac = _load_json(auth_rbac_evidence_path)
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
    runtime_source_marker = smoke["source_ref"]["runtime_source_marker"]
    runtime_affecting_changes = (
        []
        if source_tree_commit == runtime_subject_commit
        else _resolve_runtime_affecting_changes_since(runtime_subject_commit)
    )
    runtime_relation = _runtime_source_relation(
        source_tree_commit=source_tree_commit,
        source_tree_dirty=source_tree_dirty,
        runtime_subject_commit=runtime_subject_commit,
        runtime_source_marker=runtime_source_marker,
        runtime_affecting_changes_since_runtime_subject=runtime_affecting_changes,
        runtime_affecting_dirty_paths=runtime_affecting_dirty_paths,
    )
    runtime_matches_source_tree = runtime_relation["runtime_matches_source_tree"]
    runtime_relevant_source_matches = runtime_relation["runtime_relevant_source_matches"]
    evidence_scope = (
        "current_source_tree"
        if runtime_matches_source_tree
        else (
            "current_runtime_relevant_source"
            if runtime_relevant_source_matches
            else "reviewed_historical_runtime_evidence"
        )
    )
    domains = {
        "g0_g1_source_authority_security": {
            "status": "poc_verified_keep_under_regression"
            if runtime_matches_source_tree
            else runtime_relation["status"],
            "evidence": {
                "runtime_subject_commit_sha": runtime_subject_commit,
                "source_tree_commit_sha": source_tree_commit,
                "source_tree_dirty": source_tree_dirty,
                "runtime_source_marker": runtime_source_marker,
                "runtime_source_relation": runtime_relation["status"],
                "runtime_affecting_dirty_paths": runtime_affecting_dirty_paths,
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
        "status": _top_level_status(runtime_relation["status"], runtime_matches_source_tree),
        "source_tree_commit_sha": source_tree_commit,
        "source_tree_dirty": source_tree_dirty,
        "runtime_subject_commit_sha": runtime_subject_commit,
        "runtime_source_relation": runtime_relation,
        "verified_runtime_subject": _verified_runtime_subject(smoke, evidence_scope),
        "evidence_entries": {
            "poc_smoke": _path_for_output(smoke_evidence_path),
            "auth_rbac_smoke": _path_for_output(auth_rbac_evidence_path),
        },
        "decision": {
            "reviewed_poc_loop_evidence_available": True,
            "controlled_poc_loop_verified_for_current_source": runtime_relevant_source_matches,
            "current_source_verified_by_running_runtime": runtime_relevant_source_matches,
            "current_source_exact_runtime_commit_match": runtime_matches_source_tree,
            "runtime_rollout_required_for_current_source": not runtime_relevant_source_matches,
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
    verified_runtime_subject = readiness["verified_runtime_subject"]
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
        "## Verified Runtime Subject\n\n"
        f"Commit: `{verified_runtime_subject['commit_sha']}`\n\n"
        f"Image: `{verified_runtime_subject['image']}`\n\n"
        f"Image ID: `{verified_runtime_subject['image_id']}`\n\n"
        f"Evidence scope: `{verified_runtime_subject['evidence_scope']}`\n\n"
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
