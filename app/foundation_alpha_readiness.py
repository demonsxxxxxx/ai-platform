from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.public_context_keys import public_context_input_key_findings


SCHEMA_VERSION = "ai-platform.foundation-alpha-poc-readiness.v1"
SOURCE_SNAPSHOT_SCHEMA_VERSION = "ai-platform.source-snapshot.v1"
STAGE_NAME = "Foundation Alpha POC"
RUNTIME_SUBJECT_COMMIT_SHA = "d4486ebf5a33ce23a632a69bcf07ef1220b61ea3"
_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_BASE_ROOT = _ROOT / "docs/release-evidence/foundation-alpha-poc"
_EVIDENCE_ROOT = _EVIDENCE_BASE_ROOT / RUNTIME_SUBJECT_COMMIT_SHA
_SMOKE_EVIDENCE = _EVIDENCE_ROOT / "2026-06-12-211-foundation-alpha-poc-d4486eb-runtime-poc-smoke.json"
_AUTH_RBAC_EVIDENCE = _EVIDENCE_ROOT / "2026-06-12-211-foundation-alpha-poc-d4486eb-auth-rbac-smoke.json"
_SOURCE_REVISION_MARKER = _ROOT / ".ai-platform-source-revision"
_SOURCE_SNAPSHOT_MARKER = _ROOT / ".ai-platform-source-snapshot.json"
_RUNTIME_NEUTRAL_PATH_PREFIXES = (
    "assets/ai-platform-architecture-illustrations/",
    "docs/",
    "tests/",
)
_RUNTIME_NEUTRAL_EXACT_PATHS = {
    ".gitignore",
    "AGENTS.md",
    "app/capacity_bounded_load_harness.py",
    "app/foundation_alpha_readiness.py",
    "tools/foundation_alpha_readiness.py",
    "tools/frontend_release_traceability.py",
    "tools/verify_auth_rbac_smoke.py",
    "tools/verify_governance_runtime_smoke.py",
    "tests/test_source_authority_docs.py",
}

_OPEN_FOLLOWUPS = [
    "g7_docker_sandbox_hardening",
    "g8_ordinary_user_multi_agent_exposure",
    "broader_auth_session_rbac_tenant_redaction_regression",
]
_FOUNDATION_ALPHA_STAGE_BLOCKER_ORDER = [
    "runtime_admin_dashboard_acceptance_for_governance",
    "g9_runtime_export_and_retention_acceptance",
    "alert_delivery_and_trace_export_211_acceptance",
    "ordinary_user_acceptance_for_quarantined_legacy_routes",
]
_FOUNDATION_ALPHA_NON_STAGE_FOLLOWUPS = {
    # S1 requires governed pinned snapshots and fail-closed production release.
    # Reviewed signed/SBOM/license/vulnerability evidence closes later G6/S2
    # production-release governance, not the Foundation Alpha POC loop.
    "signed_skill_package_or_sbom_review_evidence",
    # S1 requires public/admin projection safety and reproducible frontend
    # source checks. Packaged frontend image smoke is S2 delivery evidence.
    "packaged_frontend_image_release_acceptance",
}
_FOUNDATION_ALPHA_NON_STAGE_PARTIAL_DOMAINS = {
    # S1 needs enough redacted Admin Runtime visibility for controlled
    # operation. The remaining G9 observability readiness gaps are S2
    # dashboard/capacity/golden-set/alert/export operational acceptance unless
    # explicit S1 followups are present in the domain.
    "g9_admin_runtime_observability",
}
_STAGE_BLOCKING_DOMAIN_STATUSES = {
    "dependency_unavailable",
    "partial_followups_open",
}
_DENIED_HTTP_STATUSES = {401, 403, 404}


class _ReadinessDefaultSettings:
    sandbox_container_provider = "fake"
    llm_gateway_provider = "openai_compatible"
    model_gateway_request_concurrency_limit = 0
    memory_retention_worker_cleanup_enabled = True
    memory_retention_worker_cleanup_limit = 200
    multi_agent_dispatch_worker_enabled = False


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
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return _read_source_revision_marker() or "unknown"
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
    if normalized.startswith("ai-platform-") and normalized.endswith(".tar") and "/" not in normalized:
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


def _release_evidence_entry_base_is_valid(payload: dict[str, Any], commit_sha: str) -> bool:
    source_ref = payload.get("source_ref")
    evidence_ref = payload.get("evidence_ref")
    if not isinstance(source_ref, dict) or not isinstance(evidence_ref, dict):
        return False

    labels = source_ref.get("image_labels")
    return (
        payload.get("schema_version") == "ai-platform.release-evidence-entry.v1"
        and payload.get("gate") == STAGE_NAME
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


def _release_evidence_entry_is_valid(payload: dict[str, Any], commit_sha: str) -> bool:
    return (
        _release_evidence_entry_base_is_valid(payload, commit_sha)
        and payload.get("artifact_kind") == "211_runtime_smoke"
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
    has_frontend_signal = (
        "lambchat_frontend" in runtime_checks
        or "frontend_http_status" in runtime_checks
    )
    has_document_loop_signal = (
        "document_review_attachment_run" in runtime_checks
        or "word_review_attachment_chat" in runtime_checks
    )
    return (
        evidence_ref.get("verifier") == "tools/verify_poc_gate.py"
        or (has_frontend_signal and has_document_loop_signal)
    )


def _is_governance_runtime_evidence(payload: dict[str, Any]) -> bool:
    evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    if not isinstance(runtime_checks, dict):
        return False

    ordinary_admin_runtime = _safe_runtime_check(runtime_checks.get("ordinary_admin_runtime"))
    admin_runtime_governance = _safe_runtime_check(runtime_checks.get("admin_runtime_governance"))
    tool_permission = _safe_runtime_check(admin_runtime_governance.get("tool_permission"))
    skill_governance = _safe_runtime_check(admin_runtime_governance.get("skill_governance"))
    memory_governance = _safe_runtime_check(admin_runtime_governance.get("memory_governance"))
    missing_domains = admin_runtime_governance.get("missing_domains")
    return (
        evidence_ref.get("verifier") == "tools/verify_governance_runtime_smoke.py"
        and evidence_ref.get("schema_version") == "ai-platform.governance-runtime-smoke.v1"
        and ordinary_admin_runtime.get("status") == 403
        and admin_runtime_governance.get("status") == 200
        and admin_runtime_governance.get("tenant_matches_requested") is True
        and admin_runtime_governance.get("governance_schema_version")
        == "ai-platform.governance-readiness.v1"
        and admin_runtime_governance.get("governance_status_allowed") is True
        and admin_runtime_governance.get("required_domains_present") is True
        and admin_runtime_governance.get("forbidden_projection_terms_present") is False
        and missing_domains == []
        and tool_permission.get("taxonomy_present") is True
        and tool_permission.get("bulk_review_present") is True
        and skill_governance.get("release_readiness_present") is True
        and skill_governance.get("dashboard_present") is True
        and memory_governance.get("long_term_fail_closed_present") is True
        and memory_governance.get("context_provenance_present") is True
        and memory_governance.get("office_context_readiness_present") is True
    )


def _release_evidence_runtime_acceptance_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    if not isinstance(runtime_checks, dict):
        return None
    acceptance = runtime_checks.get("release_evidence_runtime_acceptance")
    if not isinstance(acceptance, dict):
        return None
    if (
        evidence_ref.get("verifier") != "tools/verify_release_evidence_runtime_acceptance.py"
        or evidence_ref.get("schema_version") != "ai-platform.release-evidence-runtime-acceptance.v1"
    ):
        return None

    from app.release_evidence_readiness import (
        _runtime_acceptance_is_valid,
        _runtime_acceptance_summary,
    )

    if not _runtime_acceptance_is_valid(acceptance):
        return None
    return _runtime_acceptance_summary(acceptance)


def _is_release_evidence_runtime_acceptance_evidence(payload: dict[str, Any]) -> bool:
    return _release_evidence_runtime_acceptance_from_payload(payload) is not None


def _alert_trace_export_runtime_acceptance_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    if not isinstance(runtime_checks, dict):
        return None
    acceptance = runtime_checks.get("alert_trace_export_runtime_acceptance")
    if not isinstance(acceptance, dict):
        return None
    if (
        evidence_ref.get("verifier") != "tools/verify_alert_trace_export_runtime_acceptance.py"
        or evidence_ref.get("schema_version")
        != "ai-platform.alert-trace-export-runtime-acceptance.v1"
    ):
        return None

    from app.alert_trace_export_runtime_acceptance import (
        acceptance_is_valid,
        acceptance_summary,
    )

    if not acceptance_is_valid(acceptance):
        return None
    return acceptance_summary(acceptance)


def _is_alert_trace_export_runtime_acceptance_evidence(payload: dict[str, Any]) -> bool:
    return _alert_trace_export_runtime_acceptance_from_payload(payload) is not None


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


def _discover_governance_runtime_evidence(commit_sha: str) -> Path | None:
    commit_root = _EVIDENCE_BASE_ROOT / commit_sha
    if not commit_root.is_dir():
        return None

    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(commit_root.glob("*.json")):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not _release_evidence_entry_is_valid(payload, commit_sha):
            continue
        if _is_governance_runtime_evidence(payload):
            entries.append((path, payload))

    if entries:
        path, _ = max(entries, key=lambda item: _release_evidence_sort_key(item[0], item[1]))
        return path
    return None


def _discover_release_evidence_runtime_acceptance_evidence(commit_sha: str) -> Path | None:
    commit_root = _EVIDENCE_BASE_ROOT / commit_sha
    if not commit_root.is_dir():
        return None

    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(commit_root.glob("*.json")):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not _release_evidence_entry_is_valid(payload, commit_sha):
            continue
        if _is_release_evidence_runtime_acceptance_evidence(payload):
            entries.append((path, payload))

    if entries:
        path, _ = max(entries, key=lambda item: _release_evidence_sort_key(item[0], item[1]))
        return path
    return None


def _discover_alert_trace_export_runtime_acceptance_evidence(commit_sha: str) -> Path | None:
    commit_root = _EVIDENCE_BASE_ROOT / commit_sha
    if not commit_root.is_dir():
        return None

    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(commit_root.glob("*.json")):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not _release_evidence_entry_base_is_valid(payload, commit_sha):
            continue
        if (
            payload.get("artifact_kind") == "alert_trace_export_runtime_acceptance"
            and _is_alert_trace_export_runtime_acceptance_evidence(payload)
        ):
            entries.append((path, payload))

    if entries:
        path, _ = max(entries, key=lambda item: _release_evidence_sort_key(item[0], item[1]))
        return path
    return None


def _discover_frontend_packaged_runtime_smoke_evidence(commit_sha: str) -> Path | None:
    commit_root = _EVIDENCE_BASE_ROOT / commit_sha
    if not commit_root.is_dir():
        return None

    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(commit_root.glob("*.json")):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not _release_evidence_entry_base_is_valid(payload, commit_sha):
            continue
        evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
        runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
        smoke = runtime_checks.get("frontend_packaged_runtime_smoke") if isinstance(runtime_checks, dict) else None
        if (
            payload.get("artifact_kind") == "frontend_packaged_runtime_smoke"
            and evidence_ref.get("verifier") == "tools/frontend_packaged_runtime_smoke.py"
            and evidence_ref.get("schema_version") == "ai-platform.frontend-packaged-runtime-smoke.v1"
            and isinstance(smoke, dict)
        ):
            entries.append((path, payload))

    if entries:
        path, _ = max(entries, key=lambda item: _release_evidence_sort_key(item[0], item[1]))
        return path
    return None


def _resolve_release_evidence_paths(source_tree_commit: str) -> tuple[Path, Path]:
    if source_tree_commit != "unknown":
        current_pair = _discover_release_evidence_pair(source_tree_commit)
        if current_pair is not None:
            return current_pair
        marker = _source_snapshot_marker_for_source_tree(source_tree_commit)
        if marker is not None:
            runtime_subject_commit = str(marker.get("runtime_subject_commit_sha") or "")
            marker_pair = _discover_release_evidence_pair(runtime_subject_commit)
            if marker_pair is not None:
                return marker_pair
    configured_runtime_pair = _discover_release_evidence_pair(RUNTIME_SUBJECT_COMMIT_SHA)
    if configured_runtime_pair is not None:
        return configured_runtime_pair
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


def _status_values_from_check(check: dict[str, Any], summary_key: str, result_key: str) -> list[int]:
    values = check.get(summary_key)
    if isinstance(values, list):
        return [item for item in values if type(item) is int]
    results = check.get("results")
    if not isinstance(results, list):
        return []
    statuses: list[int] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        status = item.get(result_key)
        if type(status) is int:
            statuses.append(status)
    return statuses


def _all_denied(statuses: list[int]) -> bool:
    return bool(statuses) and all(status in _DENIED_HTTP_STATUSES for status in statuses)


def _context_material_count(value: Any) -> tuple[int, bool]:
    if type(value) is not int:
        return 0, False
    if value < 0:
        return 0, False
    return value, True


def _artifact_review_summary(runtime_checks: dict[str, Any]) -> dict[str, Any]:
    document_review = _safe_runtime_check(runtime_checks.get("document_review_attachment_run"))
    artifact_types = document_review.get("artifact_types")
    if not isinstance(artifact_types, list):
        artifact_types = document_review.get("artifact_type_summary")
    return {
        "status": document_review.get("status"),
        "skill_id": document_review.get("skill_id"),
        "artifact_types": sorted(artifact_types or []),
        "playback_contract_version": document_review.get("playback_contract_version"),
    }


def _projection_summary(runtime_checks: dict[str, Any]) -> dict[str, Any]:
    frontend = _safe_runtime_check(runtime_checks.get("lambchat_frontend"))
    boundary = _safe_runtime_check(runtime_checks.get("frontend_dist_api_boundary"))
    same_origin_api_health = _safe_runtime_check(runtime_checks.get("same_origin_api_health"))
    if same_origin_api_health.get("status") is None and same_origin_api_health.get("api_status") is not None:
        same_origin_api_health["status"] = same_origin_api_health.get("api_status")
    return {
        "frontend_http_status": frontend.get("status") or runtime_checks.get("frontend_http_status"),
        "same_origin_api_health": same_origin_api_health,
        "forbidden_reference_count": boundary.get("forbidden_reference_count")
        if boundary.get("forbidden_reference_count") is not None
        else runtime_checks.get("frontend_forbidden_reference_count"),
        "artifact_download_cross_user_statuses": _safe_runtime_check(
            runtime_checks.get("artifact_download_isolation")
        ).get("cross_user_statuses"),
        "artifact_preview_cross_user_statuses": _safe_runtime_check(
            runtime_checks.get("artifact_preview_isolation")
        ).get("cross_user_statuses"),
    }


def _safe_blockers(*values: Any) -> list[str]:
    blockers: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item.strip():
                blockers.append(item.strip())
    return sorted(set(blockers))


def _frontend_traceability_dependency_unavailable_summary(exc: BaseException) -> dict[str, Any]:
    return {
        "status": "dependency_unavailable",
        "open_gap_count": 1,
        "dependency_error_class": exc.__class__.__name__,
        "ci_verify_script_present": False,
        "ci_verify_includes_projection_audit": False,
        "dist_build_verified_same_commit": False,
        "blockers": ["frontend_release_traceability_dependency_unavailable"],
    }


def _frontend_release_traceability_summary(trace: dict[str, Any]) -> dict[str, Any]:
    scripts = trace.get("scripts") if isinstance(trace.get("scripts"), dict) else {}
    ci_verify_script = scripts.get("ci:verify") if isinstance(scripts.get("ci:verify"), str) else ""
    projection_audit_script = (
        scripts.get("projection:audit") if isinstance(scripts.get("projection:audit"), str) else ""
    )
    git = trace.get("git") if isinstance(trace.get("git"), dict) else {}
    workflow = trace.get("workflow") if isinstance(trace.get("workflow"), dict) else {}
    dist = trace.get("dist") if isinstance(trace.get("dist"), dict) else {}
    build_provenance = (
        dist.get("build_provenance") if isinstance(dist.get("build_provenance"), dict) else {}
    )
    packaged = (
        trace.get("packaged_frontend_image")
        if isinstance(trace.get("packaged_frontend_image"), dict)
        else {}
    )
    contract_scan = (
        packaged.get("contract_scan") if isinstance(packaged.get("contract_scan"), dict) else {}
    )
    blockers = _safe_blockers(
        workflow.get("blockers"),
        dist.get("blockers"),
        build_provenance.get("blockers"),
        packaged.get("blockers"),
    )
    dist_build_verified_same_commit = build_provenance.get("verified_same_commit") is True
    if not ci_verify_script:
        blockers.append("frontend_ci_verify_script_missing")
    if "frontend_projection_audit.py" not in ci_verify_script:
        blockers.append("frontend_ci_verify_projection_audit_missing")
    if dist.get("status") != "built":
        blockers.append("frontend_dist_not_built")
    if not dist_build_verified_same_commit:
        blockers.append("frontend_dist_build_provenance_not_verified")
    if workflow.get("status") != "present":
        blockers.append("frontend_workflow_not_present")
    if packaged.get("status") not in {"configured", "configured_with_policy_gaps"}:
        blockers.append("frontend_packaged_image_not_configured")
    if contract_scan.get("status") != "pass":
        blockers.append("frontend_packaged_contract_scan_failed")
    blockers = sorted(set(blockers))
    status = (
        "verified_packaged_release_followup_open"
        if dist.get("status") == "built"
        and dist_build_verified_same_commit
        and workflow.get("status") == "present"
        and "frontend_projection_audit.py" in ci_verify_script
        and not blockers
        else "frontend_release_traceability_followup_required"
    )
    return {
        "status": status,
        "schema_version": trace.get("schema_version"),
        "frontend_path": trace.get("frontend_path"),
        "package_name": trace.get("package_name"),
        "package_version": trace.get("package_version"),
        "package_manager": trace.get("package_manager"),
        "git_commit": git.get("commit"),
        "git_dirty": git.get("dirty") if isinstance(git.get("dirty"), bool) else None,
        "ci_verify_script_present": bool(ci_verify_script),
        "ci_verify_includes_projection_audit": "frontend_projection_audit.py" in ci_verify_script,
        "projection_audit_script_present": bool(projection_audit_script),
        "dist_status": dist.get("status"),
        "dist_file_count": dist.get("file_count") if isinstance(dist.get("file_count"), int) else None,
        "dist_build_provenance_status": build_provenance.get("status"),
        "dist_build_commit": build_provenance.get("build_commit"),
        "dist_build_verified_same_commit": dist_build_verified_same_commit,
        "workflow_status": workflow.get("status"),
        "workflow_path": workflow.get("path"),
        "packaged_frontend_image_status": packaged.get("status"),
        "packaged_contract_scan_status": contract_scan.get("status"),
        "blockers": blockers,
        "open_gap_count": len(blockers),
    }


def _build_frontend_traceability_summary() -> dict[str, Any]:
    try:
        from tools.frontend_release_traceability import build_frontend_release_traceability
    except ModuleNotFoundError as exc:
        return _frontend_traceability_dependency_unavailable_summary(exc)

    return _frontend_release_traceability_summary(build_frontend_release_traceability(_ROOT))


def _frontend_projection_audit_dependency_unavailable_summary(exc: BaseException) -> dict[str, Any]:
    return {
        "status": "dependency_unavailable",
        "ordinary_user_acceptance": "blocked_projection_audit_dependency_unavailable",
        "active_legacy_route_count": None,
        "ordinary_user_reachable_legacy_route_count": None,
        "permission_gated_active_legacy_route_count": None,
        "active_forbidden_projection_violation_count": None,
        "ci_verify_includes_projection_audit": False,
        "open_gap_count": 1,
        "open_gaps": ["frontend_projection_audit_dependency_unavailable"],
        "dependency_error_class": exc.__class__.__name__,
    }


def _frontend_projection_audit_summary(audit: dict[str, Any]) -> dict[str, Any]:
    active_entry = audit.get("active_browser_entry") if isinstance(audit.get("active_browser_entry"), dict) else {}
    active_route_inventory = (
        active_entry.get("route_inventory") if isinstance(active_entry.get("route_inventory"), dict) else {}
    )
    active_legacy_routes = active_route_inventory.get("legacy_route_policies")
    active_legacy_route_count = len(active_legacy_routes) if isinstance(active_legacy_routes, list) else None
    ordinary_user_reachable_legacy_routes = active_route_inventory.get(
        "ordinary_user_reachable_legacy_route_policies"
    )
    ordinary_user_reachable_legacy_route_count = (
        len(ordinary_user_reachable_legacy_routes)
        if isinstance(ordinary_user_reachable_legacy_routes, list)
        else active_legacy_route_count
    )
    permission_gated_active_legacy_route_count = (
        active_legacy_route_count - ordinary_user_reachable_legacy_route_count
        if isinstance(active_legacy_route_count, int)
        and isinstance(ordinary_user_reachable_legacy_route_count, int)
        else None
    )
    active_forbidden_terms = (
        active_entry.get("forbidden_projection_terms")
        if isinstance(active_entry.get("forbidden_projection_terms"), dict)
        else {}
    )
    active_violations = active_forbidden_terms.get("violations")
    active_forbidden_violation_count = len(active_violations) if isinstance(active_violations, list) else None
    ci = audit.get("ci_integration") if isinstance(audit.get("ci_integration"), dict) else {}
    ci_verify_includes_projection_audit = ci.get("ci_verify_includes_projection_audit") is True
    open_gaps = [
        str(item)
        for item in audit.get("open_gaps", [])
        if isinstance(item, str) and item.strip()
    ]
    ordinary_user_accepted = (
        ci_verify_includes_projection_audit
        and ordinary_user_reachable_legacy_route_count == 0
        and active_forbidden_violation_count == 0
        and audit.get("status") in {"pass", "pass_with_policy_gaps"}
    )
    return {
        "status": audit.get("status"),
        "schema_version": audit.get("schema_version"),
        "frontend_path": audit.get("frontend_path"),
        "ordinary_user_acceptance": (
            "accepted_active_legacy_routes_clear"
            if ordinary_user_accepted and active_legacy_route_count == 0
            else "accepted_active_legacy_routes_permission_gated"
            if ordinary_user_accepted
            else "blocked_active_legacy_routes_or_projection_audit"
        ),
        "active_legacy_route_count": active_legacy_route_count,
        "ordinary_user_reachable_legacy_route_count": ordinary_user_reachable_legacy_route_count,
        "permission_gated_active_legacy_route_count": permission_gated_active_legacy_route_count,
        "active_forbidden_projection_violation_count": active_forbidden_violation_count,
        "ci_verify_includes_projection_audit": ci_verify_includes_projection_audit,
        "open_gap_count": len(open_gaps),
        "open_gaps": open_gaps,
    }


def _build_frontend_projection_audit_summary() -> dict[str, Any]:
    try:
        from tools.frontend_projection_audit import build_frontend_projection_audit
    except ModuleNotFoundError as exc:
        return _frontend_projection_audit_dependency_unavailable_summary(exc)

    return _frontend_projection_audit_summary(build_frontend_projection_audit(_ROOT))


def _context_projection_summary(runtime_checks: dict[str, Any]) -> dict[str, Any]:
    projection = _safe_runtime_check(runtime_checks.get("context_snapshot_public_projection"))
    if not projection:
        return {
            "status": "missing_context_snapshot_public_projection",
            "referenced_material_counts": {},
            "raw_material_id_fields_present": None,
            "forbidden_projection_leak_count": None,
            "summary_source": None,
            "input_keys": [],
            "memory_policy_source": None,
            "long_term_memory_read": None,
            "execution_tier": None,
            "context_pack_generated_at_present": False,
            "missing_public_summary_fields": [
                "context_pack_generated_at",
                "execution_tier",
                "input_keys",
                "long_term_memory_read",
                "memory_policy_source",
                "summary_source",
            ],
        }

    forbidden_leaks = projection.get("forbidden_projection_leaks")
    forbidden_leak_count = len(forbidden_leaks) if isinstance(forbidden_leaks, list) else None
    raw_material_id_fields_present = projection.get("raw_material_id_fields_present")
    input_keys, unsafe_input_keys = public_context_input_key_findings(projection.get("input_keys"))
    summary_source = projection.get("summary_source") if isinstance(projection.get("summary_source"), str) else None
    memory_policy_source = (
        projection.get("memory_policy_source") if isinstance(projection.get("memory_policy_source"), str) else None
    )
    long_term_memory_read = (
        projection.get("long_term_memory_read") if isinstance(projection.get("long_term_memory_read"), bool) else None
    )
    execution_tier = projection.get("execution_tier") if isinstance(projection.get("execution_tier"), str) else None
    generated_at_present = bool(projection.get("context_pack_generated_at_present"))
    missing_public_summary_fields: list[str] = []
    if not summary_source:
        missing_public_summary_fields.append("summary_source")
    if not input_keys:
        missing_public_summary_fields.append("input_keys")
    counts = projection.get("referenced_material_counts")
    invalid_count_fields: list[str] = []
    count_keys = (
        "message_count",
        "file_count",
        "artifact_count",
        "memory_record_count",
    )
    if not isinstance(counts, dict):
        invalid_count_fields = list(count_keys)
    else:
        invalid_count_fields = [
            key for key in count_keys if not _context_material_count(counts.get(key))[1]
        ]
    file_count = 0
    if isinstance(counts, dict):
        file_count = _context_material_count(counts.get("file_count"))[0]
    if invalid_count_fields:
        missing_public_summary_fields.append("referenced_material_counts")
    if file_count > 0 and "attachments" not in input_keys:
        missing_public_summary_fields.append("attachments_input_key")
    if unsafe_input_keys:
        missing_public_summary_fields.append("unsafe_input_keys")
    if memory_policy_source is None:
        missing_public_summary_fields.append("memory_policy_source")
    if long_term_memory_read is None:
        missing_public_summary_fields.append("long_term_memory_read")
    if not execution_tier:
        missing_public_summary_fields.append("execution_tier")
    if not generated_at_present:
        missing_public_summary_fields.append("context_pack_generated_at")
    reported_missing = projection.get("missing_public_summary_fields")
    if isinstance(reported_missing, list):
        missing_public_summary_fields.extend(str(item) for item in reported_missing if str(item).strip())
    missing_public_summary_fields = sorted(set(missing_public_summary_fields))
    status = (
        "verified_public_context_projection"
        if projection.get("ok") is True
        and raw_material_id_fields_present is False
        and forbidden_leak_count == 0
        and not missing_public_summary_fields
        else "context_snapshot_public_projection_followup_required"
    )
    summary = {
        "status": status,
        "referenced_material_counts": deepcopy(counts) if isinstance(counts, dict) else {},
        "raw_material_id_fields_present": raw_material_id_fields_present
        if isinstance(raw_material_id_fields_present, bool)
        else None,
        "forbidden_projection_leak_count": forbidden_leak_count,
        "summary_source": summary_source,
        "input_keys": input_keys,
        "memory_policy_source": memory_policy_source,
        "long_term_memory_read": long_term_memory_read,
        "execution_tier": execution_tier,
        "context_pack_generated_at_present": generated_at_present,
        "missing_public_summary_fields": missing_public_summary_fields,
    }
    if unsafe_input_keys:
        summary["unsafe_input_keys"] = unsafe_input_keys
    if invalid_count_fields:
        summary["invalid_referenced_material_count_fields"] = sorted(invalid_count_fields)
    return summary


def _auth_rbac_summary(
    runtime_checks: dict[str, Any],
    artifact_checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    admin_runtime = _safe_runtime_check(runtime_checks.get("admin_runtime"))
    authenticated_auth_me = _safe_runtime_check(runtime_checks.get("authenticated_auth_me"))
    artifact_checks = artifact_checks or runtime_checks
    artifact_download = _safe_runtime_check(artifact_checks.get("artifact_download_isolation"))
    artifact_preview = _safe_runtime_check(artifact_checks.get("artifact_preview_isolation"))
    download_cross_user_statuses = _status_values_from_check(
        artifact_download,
        "cross_user_statuses",
        "cross_user_status",
    )
    download_cross_tenant_statuses = _status_values_from_check(
        artifact_download,
        "cross_tenant_statuses",
        "cross_tenant_status",
    )
    preview_cross_user_statuses = _status_values_from_check(
        artifact_preview,
        "cross_user_statuses",
        "cross_user_status",
    )
    preview_cross_tenant_statuses = _status_values_from_check(
        artifact_preview,
        "cross_tenant_statuses",
        "cross_tenant_status",
    )
    broader_regression_verified = (
        _safe_runtime_check(runtime_checks.get("unauthenticated_auth_me")).get("status") == 401
        and authenticated_auth_me.get("status") == 200
        and authenticated_auth_me.get("tenant_matches_requested") is True
        and authenticated_auth_me.get("user_matches_requested") is True
        and authenticated_auth_me.get("forbidden_projection_terms_present") is False
        and _safe_runtime_check(runtime_checks.get("invalid_gateway_secret_auth_me")).get("status") == 403
        and _safe_runtime_check(runtime_checks.get("ordinary_admin_runtime")).get("status") == 403
        and admin_runtime.get("status") == 200
        and admin_runtime.get("required_sections_present") is True
        and admin_runtime.get("tenant_matches_requested") is True
        and admin_runtime.get("forbidden_projection_terms_present") is False
        and _all_denied(download_cross_user_statuses)
        and _all_denied(download_cross_tenant_statuses)
        and _all_denied(preview_cross_user_statuses)
        and _all_denied(preview_cross_tenant_statuses)
    )
    return {
        "unauthenticated_auth_me_status": _safe_runtime_check(runtime_checks.get("unauthenticated_auth_me")).get(
            "status"
        ),
        "authenticated_auth_me_status": authenticated_auth_me.get("status"),
        "authenticated_auth_me_route": authenticated_auth_me.get("route"),
        "authenticated_auth_me_tenant_matches_requested": authenticated_auth_me.get("tenant_matches_requested"),
        "authenticated_auth_me_user_matches_requested": authenticated_auth_me.get("user_matches_requested"),
        "authenticated_auth_me_forbidden_projection_terms_present": authenticated_auth_me.get(
            "forbidden_projection_terms_present"
        ),
        "invalid_gateway_secret_auth_me_status": _safe_runtime_check(
            runtime_checks.get("invalid_gateway_secret_auth_me")
        ).get("status"),
        "ordinary_admin_runtime_status": _safe_runtime_check(runtime_checks.get("ordinary_admin_runtime")).get(
            "status"
        ),
        "admin_runtime_status": admin_runtime.get("status"),
        "admin_required_sections_present": admin_runtime.get("required_sections_present"),
        "admin_tenant_matches_requested": admin_runtime.get("tenant_matches_requested"),
        "admin_forbidden_projection_terms_present": admin_runtime.get("forbidden_projection_terms_present"),
        "artifact_download_cross_user_statuses": download_cross_user_statuses,
        "artifact_download_cross_tenant_statuses": download_cross_tenant_statuses,
        "artifact_preview_cross_user_statuses": preview_cross_user_statuses,
        "artifact_preview_cross_tenant_statuses": preview_cross_tenant_statuses,
        "broader_auth_session_rbac_tenant_redaction_regression_verified": broader_regression_verified,
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


def _poc_loop_status(runtime_relation: dict[str, Any]) -> str:
    if runtime_relation.get("runtime_relevant_source_matches"):
        if runtime_relation.get("runtime_matches_source_tree"):
            return "core_loop_verified_for_current_source_tree"
        return "core_loop_verified_for_runtime_relevant_source"
    if runtime_relation.get("runtime_affecting_dirty_paths"):
        return "runtime_affecting_uncommitted_changes_pending"
    if runtime_relation.get("status") == "source_tree_uncommitted_changes_pending":
        return "source_dirty_unknown_runtime_impact"
    return "runtime_rollout_required"


def _ordered_stage_blockers(domains: dict[str, dict[str, Any]]) -> list[str]:
    blockers: set[str] = set()
    for name, domain in domains.items():
        if domain.get("status") not in _STAGE_BLOCKING_DOMAIN_STATUSES:
            continue
        domain_blockers: list[str] = []
        saw_named_followup = False
        for item in domain.get("open_followups", []):
            if isinstance(item, str) and item.strip():
                saw_named_followup = True
                followup = item.strip()
                if followup not in _FOUNDATION_ALPHA_NON_STAGE_FOLLOWUPS:
                    domain_blockers.append(followup)
        if domain_blockers:
            blockers.update(domain_blockers)
        elif (
            not saw_named_followup
            and name not in _FOUNDATION_ALPHA_NON_STAGE_PARTIAL_DOMAINS
        ):
            blockers.add(f"{name}_{domain.get('status')}")

    ordered: list[str] = []
    for item in _FOUNDATION_ALPHA_STAGE_BLOCKER_ORDER:
        if item in blockers:
            ordered.append(item)
            blockers.remove(item)
    ordered.extend(sorted(blockers))
    return ordered


def _top_level_open_followups(
    stage_acceptance_blockers: list[str],
    *,
    excluded_static_followups: set[str] | None = None,
) -> list[str]:
    excluded_static_followups = excluded_static_followups or set()
    ordered: list[str] = []
    static_followups = [item for item in _OPEN_FOLLOWUPS if item not in excluded_static_followups]
    for item in [*static_followups, *stage_acceptance_blockers]:
        if item not in ordered:
            ordered.append(item)
    return ordered


def _stage_acceptance_status(
    *,
    runtime_relevant_source_matches: bool,
    context_projection_verified: bool,
    stage_acceptance_blockers: list[str],
) -> str:
    if not runtime_relevant_source_matches:
        return "runtime_rollout_required"
    if not context_projection_verified:
        return "context_snapshot_public_summary_followup_required"
    if stage_acceptance_blockers:
        return "core_poc_loop_verified_followups_open"
    return "foundation_alpha_stage_complete"


def _operator_context(
    runtime_relation: dict[str, Any],
    *,
    context_projection_verified: bool = True,
    stage_acceptance_status: str,
    stage_acceptance_blockers: list[str] | None = None,
    governance_runtime_smoke_verified: bool = False,
    release_evidence_runtime_acceptance_verified: bool = False,
    alert_trace_export_runtime_acceptance_verified: bool = False,
    frontend_packaged_runtime_smoke_verified: bool = False,
    broader_auth_regression_verified: bool = False,
) -> dict[str, Any]:
    poc_loop_status = _poc_loop_status(runtime_relation)
    if runtime_relation.get("runtime_relevant_source_matches") and not context_projection_verified:
        poc_loop_status = "context_snapshot_public_summary_followup_required"
    next_recommended_slices = [
        "alert_delivery_and_trace_export_211_acceptance",
        "g9_runtime_export_and_retention_acceptance",
        "packaged_frontend_image_release_acceptance",
        "broader_auth_session_rbac_tenant_redaction_regression",
    ]
    if not governance_runtime_smoke_verified:
        next_recommended_slices.insert(0, "g6_runtime_admin_dashboard_acceptance_for_governance")
    if release_evidence_runtime_acceptance_verified:
        next_recommended_slices = [
            item for item in next_recommended_slices if item != "g9_runtime_export_and_retention_acceptance"
        ]
    if alert_trace_export_runtime_acceptance_verified:
        next_recommended_slices = [
            item
            for item in next_recommended_slices
            if item != "alert_delivery_and_trace_export_211_acceptance"
        ]
    if frontend_packaged_runtime_smoke_verified:
        next_recommended_slices = [
            item for item in next_recommended_slices if item != "packaged_frontend_image_release_acceptance"
        ]
    if broader_auth_regression_verified:
        next_recommended_slices = [
            item
            for item in next_recommended_slices
            if item != "broader_auth_session_rbac_tenant_redaction_regression"
        ]
    next_recommended_slices = [
        item
        for item in next_recommended_slices
        if item not in _FOUNDATION_ALPHA_NON_STAGE_FOLLOWUPS
    ]
    for blocker in reversed(stage_acceptance_blockers or []):
        if blocker not in next_recommended_slices:
            next_recommended_slices.insert(0, blocker)
    return {
        "poc_scope": "foundation_alpha_controlled_internal_poc",
        "poc_loop_status": poc_loop_status,
        "current_runtime_relation": runtime_relation["status"],
        "stage_acceptance_status": stage_acceptance_status,
        "stage_gate": "foundation_alpha_poc_not_production",
        "verified_poc_capabilities": [
            "source_authority_security_baseline",
            "control_plane_public_admin_projection_contracts",
            "queue_worker_document_task_artifact_loop",
            "frontend_public_projection_poc",
        ],
        "blocked_expansions": [
            "production_concurrency_increase",
            "docker_sandbox_hardening_claim",
            "ordinary_user_multi_agent_exposure",
            "department_rollout",
        ],
        "next_recommended_slices": next_recommended_slices,
    }


def _build_governance_summary(settings: object | None) -> dict[str, Any]:
    try:
        from app.governance_readiness import build_governance_readiness
    except ModuleNotFoundError as exc:
        return _governance_dependency_unavailable_summary(exc)

    settings = settings or _ReadinessDefaultSettings()
    governance = build_governance_readiness(settings, include_frontend_projection_audit=False)
    return {
        "governance_readiness_status": governance["status"],
        "ordinary_user_policy": governance["ordinary_user_policy"],
        "open_gap_count": len(governance["open_gaps"]),
        "open_gaps": [
            item
            for item in governance["open_gaps"]
            if isinstance(item, str) and item.strip()
        ],
    }


def _g6_open_followups(
    governance_summary: dict[str, Any],
    *,
    governance_runtime_smoke_verified: bool,
) -> list[str]:
    followups: list[str] = []
    if not governance_runtime_smoke_verified:
        followups.append("runtime_admin_dashboard_acceptance_for_governance")
    for gap in governance_summary.get("open_gaps", []):
        if not isinstance(gap, str) or not gap.strip():
            continue
        if gap == "signed_skill_package_or_sbom_release_gate":
            followups.append("signed_skill_package_or_sbom_review_evidence")
    if (
        "open_gaps" not in governance_summary
        and governance_summary.get("open_gap_count")
        and "signed_skill_package_or_sbom_review_evidence" not in followups
    ):
        followups.append("signed_skill_package_or_sbom_review_evidence")
    return list(dict.fromkeys(followups))


def _build_observability_summary(
    settings: object | None,
    *,
    release_evidence_runtime_acceptance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from app.observability_readiness import build_observability_readiness
    except ModuleNotFoundError as exc:
        return _observability_dependency_unavailable_summary(exc)

    settings = settings or _ReadinessDefaultSettings()
    observability = build_observability_readiness(
        settings,
        release_evidence_runtime_acceptance=release_evidence_runtime_acceptance,
    )
    return {
        "observability_readiness_status": observability["status"],
        "admin_runtime_projection": observability["admin_runtime_projection"],
        "open_gap_count": len(observability["open_gaps"]),
    }


def _governance_runtime_smoke_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "missing_governance_runtime_smoke",
            "schema_version": None,
            "ordinary_admin_runtime_status": None,
            "admin_runtime_governance_status": None,
            "governance_schema_version": None,
            "required_domains_present": None,
            "forbidden_projection_terms_present": None,
            "verified": False,
        }

    evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    ordinary_admin_runtime = _safe_runtime_check(runtime_checks.get("ordinary_admin_runtime"))
    admin_runtime_governance = _safe_runtime_check(runtime_checks.get("admin_runtime_governance"))
    verified = _is_governance_runtime_evidence(payload)
    return {
        "status": "verified_admin_runtime_governance_projection"
        if verified
        else "governance_runtime_smoke_followup_required",
        "schema_version": evidence_ref.get("schema_version") if isinstance(evidence_ref, dict) else None,
        "ordinary_admin_runtime_status": ordinary_admin_runtime.get("status"),
        "admin_runtime_governance_status": admin_runtime_governance.get("status"),
        "governance_schema_version": admin_runtime_governance.get("governance_schema_version"),
        "required_domains_present": admin_runtime_governance.get("required_domains_present"),
        "forbidden_projection_terms_present": admin_runtime_governance.get(
            "forbidden_projection_terms_present"
        ),
        "verified": verified,
    }


def _release_evidence_runtime_acceptance_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "missing_release_evidence_runtime_acceptance",
            "schema_version": None,
            "runtime_export_status": None,
            "retention_status": None,
            "safe_entry_count": None,
            "blocked_entry_count": None,
            "verified": False,
        }

    acceptance = _release_evidence_runtime_acceptance_from_payload(payload)
    if acceptance is None:
        return {
            "status": "release_evidence_runtime_acceptance_followup_required",
            "schema_version": None,
            "runtime_export_status": None,
            "retention_status": None,
            "safe_entry_count": None,
            "blocked_entry_count": None,
            "verified": False,
        }
    checks = acceptance["checks"]
    runtime_export = checks["runtime_export_acceptance"]
    retention = checks["retention_runtime_acceptance"]
    return {
        "status": "verified_release_evidence_runtime_acceptance",
        "schema_version": acceptance["schema_version"],
        "runtime_export_status": runtime_export.get("status"),
        "retention_status": retention.get("status"),
        "safe_entry_count": runtime_export.get("safe_entry_count"),
        "blocked_entry_count": runtime_export.get("blocked_entry_count"),
        "verified": True,
    }


def _alert_trace_export_runtime_acceptance_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "missing_alert_trace_export_runtime_acceptance",
            "schema_version": None,
            "redaction_scan_status": None,
            "ordinary_admin_runtime_status": None,
            "admin_runtime_status": None,
            "alert_delivery_not_enabled": None,
            "trace_export_sources_public_only": None,
            "verified": False,
        }

    acceptance = _alert_trace_export_runtime_acceptance_from_payload(payload)
    if acceptance is None:
        return {
            "status": "alert_trace_export_runtime_acceptance_followup_required",
            "schema_version": None,
            "redaction_scan_status": None,
            "ordinary_admin_runtime_status": None,
            "admin_runtime_status": None,
            "alert_delivery_not_enabled": None,
            "trace_export_sources_public_only": None,
            "verified": False,
        }
    return {
        "status": "verified_alert_trace_export_runtime_acceptance",
        "schema_version": acceptance["schema_version"],
        "redaction_scan_status": acceptance.get("redaction_scan_status"),
        "ordinary_admin_runtime_status": acceptance.get("ordinary_admin_runtime_status"),
        "admin_runtime_status": acceptance.get("admin_runtime_status"),
        "alert_delivery_policy_status": acceptance.get("alert_delivery_policy_status"),
        "alert_delivery_not_enabled": acceptance.get("alert_delivery_not_enabled"),
        "trace_export_contract_schema_version": acceptance.get(
            "trace_export_contract_schema_version"
        ),
        "trace_export_not_raw_runtime_payloads": acceptance.get(
            "trace_export_not_raw_runtime_payloads"
        ),
        "trace_export_sources_public_only": acceptance.get("trace_export_sources_public_only"),
        "verified": True,
    }


def _frontend_packaged_runtime_smoke_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "missing_frontend_packaged_runtime_smoke",
            "runtime_host": None,
            "closed_evidence_items": [],
            "verified": False,
        }

    evidence_ref = payload.get("evidence_ref") if isinstance(payload, dict) else {}
    runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
    evidence = runtime_checks.get("frontend_packaged_runtime_smoke") if isinstance(runtime_checks, dict) else None
    if not isinstance(evidence, dict):
        return {
            "status": "frontend_packaged_runtime_smoke_followup_required",
            "runtime_host": None,
            "closed_evidence_items": [],
            "verified": False,
        }

    from tools.frontend_packaged_runtime_smoke import build_frontend_packaged_runtime_smoke_readiness

    readiness = build_frontend_packaged_runtime_smoke_readiness(evidence)
    closed_items = readiness.get("closed_evidence_items")
    closed_items = closed_items if isinstance(closed_items, list) else []
    return {
        "status": readiness.get("status"),
        "runtime_host": evidence.get("runtime_host"),
        "closed_evidence_items": [item for item in closed_items if isinstance(item, str)],
        "verified": "211_packaged_frontend_runtime_smoke" in closed_items,
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
    auth_rbac_summary = _auth_rbac_summary(auth_checks, artifact_checks=smoke_checks)
    broader_auth_regression_verified = bool(
        auth_rbac_summary.get("broader_auth_session_rbac_tenant_redaction_regression_verified")
    )
    runtime_subject_commit = smoke["runtime_subject_commit_sha"]
    governance_runtime_evidence_path = _discover_governance_runtime_evidence(runtime_subject_commit)
    governance_runtime_payload = (
        _load_json(governance_runtime_evidence_path) if governance_runtime_evidence_path is not None else None
    )
    governance_runtime_smoke = _governance_runtime_smoke_summary(governance_runtime_payload)
    governance_runtime_smoke_verified = governance_runtime_smoke["verified"] is True
    release_evidence_runtime_acceptance_path = _discover_release_evidence_runtime_acceptance_evidence(
        runtime_subject_commit
    )
    release_evidence_runtime_acceptance_payload = (
        _load_json(release_evidence_runtime_acceptance_path)
        if release_evidence_runtime_acceptance_path is not None
        else None
    )
    release_evidence_runtime_acceptance = _release_evidence_runtime_acceptance_from_payload(
        release_evidence_runtime_acceptance_payload
    ) if release_evidence_runtime_acceptance_payload is not None else None
    release_evidence_runtime_acceptance_summary = _release_evidence_runtime_acceptance_summary(
        release_evidence_runtime_acceptance_payload
    )
    release_evidence_runtime_acceptance_verified = (
        release_evidence_runtime_acceptance_summary["verified"] is True
    )
    alert_trace_export_runtime_acceptance_path = _discover_alert_trace_export_runtime_acceptance_evidence(
        runtime_subject_commit
    )
    alert_trace_export_runtime_acceptance_payload = (
        _load_json(alert_trace_export_runtime_acceptance_path)
        if alert_trace_export_runtime_acceptance_path is not None
        else None
    )
    alert_trace_export_runtime_acceptance_summary = _alert_trace_export_runtime_acceptance_summary(
        alert_trace_export_runtime_acceptance_payload
    )
    alert_trace_export_runtime_acceptance_verified = (
        alert_trace_export_runtime_acceptance_summary["verified"] is True
    )
    frontend_packaged_runtime_smoke_path = _discover_frontend_packaged_runtime_smoke_evidence(
        runtime_subject_commit
    )
    frontend_packaged_runtime_smoke_payload = (
        _load_json(frontend_packaged_runtime_smoke_path)
        if frontend_packaged_runtime_smoke_path is not None
        else None
    )
    frontend_packaged_runtime_smoke = _frontend_packaged_runtime_smoke_summary(
        frontend_packaged_runtime_smoke_payload
    )
    frontend_packaged_runtime_smoke_verified = frontend_packaged_runtime_smoke["verified"] is True
    try:
        governance_summary = _build_governance_summary(settings)
    except ModuleNotFoundError as exc:
        governance_summary = _governance_dependency_unavailable_summary(exc)
    try:
        observability_summary = _build_observability_summary(
            settings,
            release_evidence_runtime_acceptance=release_evidence_runtime_acceptance,
        )
    except ModuleNotFoundError as exc:
        observability_summary = _observability_dependency_unavailable_summary(exc)
    try:
        frontend_traceability_summary = _build_frontend_traceability_summary()
    except (ModuleNotFoundError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        frontend_traceability_summary = _frontend_traceability_dependency_unavailable_summary(exc)
    try:
        frontend_projection_audit_summary = _build_frontend_projection_audit_summary()
    except (ModuleNotFoundError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        frontend_projection_audit_summary = _frontend_projection_audit_dependency_unavailable_summary(exc)

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
    g6_open_followups = _g6_open_followups(
        governance_summary,
        governance_runtime_smoke_verified=governance_runtime_smoke_verified,
    )
    g9_open_followups = [
        "g9_runtime_export_and_retention_acceptance",
        "alert_delivery_and_trace_export_211_acceptance",
    ]
    if release_evidence_runtime_acceptance_verified:
        g9_open_followups.remove("g9_runtime_export_and_retention_acceptance")
    if alert_trace_export_runtime_acceptance_verified:
        g9_open_followups.remove("alert_delivery_and_trace_export_211_acceptance")

    frontend_open_followups = []
    if (
        frontend_projection_audit_summary.get("ordinary_user_acceptance")
        not in {
            "accepted_active_legacy_routes_clear",
            "accepted_active_legacy_routes_permission_gated",
        }
    ):
        frontend_open_followups.append("ordinary_user_acceptance_for_quarantined_legacy_routes")
    if not frontend_packaged_runtime_smoke_verified:
        frontend_open_followups.insert(0, "packaged_frontend_image_release_acceptance")

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
                "auth_rbac": auth_rbac_summary,
                "repo_local_env_present": smoke["source_ref"]["repo_local_env_present"],
            },
            "open_followups": []
            if broader_auth_regression_verified
            else [
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
            "status": "poc_verified_capacity_baseline_keep_defaults_locked",
            "evidence": {
                "general_chat_run": smoke_checks.get("general_chat_run"),
                "upload_attachment_chat": _safe_runtime_check(smoke_checks.get("upload_attachment_chat")),
                "document_review_attachment_run": _artifact_review_summary(smoke_checks),
                "capacity_default_policy": "do_not_raise_without_separate_recorded_profile_evidence",
            },
            "open_followups": [],
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
                "context_snapshot_public_projection": _context_projection_summary(smoke_checks),
                "governance_runtime_smoke": governance_runtime_smoke,
            },
            "open_followups": g6_open_followups,
        },
        "g9_admin_runtime_observability": {
            "status": "partial_followups_open"
            if observability_summary["open_gap_count"]
            else "poc_verified_keep_under_regression",
            "evidence": {
                **observability_summary,
                "release_evidence_result": smoke["evidence_ref"]["result"],
                "release_evidence_runtime_acceptance": release_evidence_runtime_acceptance_summary,
                "alert_trace_export_runtime_acceptance": alert_trace_export_runtime_acceptance_summary,
            },
            "open_followups": g9_open_followups,
        },
        "frontend_poc": {
            "status": "partial_followups_open"
            if frontend_traceability_summary.get("open_gap_count") or frontend_open_followups
            else "poc_verified_packaged_release_followup_open",
            "evidence": {
                **_projection_summary(smoke_checks),
                "frontend_release_traceability": frontend_traceability_summary,
                "frontend_projection_audit": frontend_projection_audit_summary,
                "frontend_packaged_runtime_smoke": frontend_packaged_runtime_smoke,
            },
            "open_followups": frontend_open_followups,
        },
    }
    context_projection_summary = domains["g6_poc_governance"]["evidence"]["context_snapshot_public_projection"]
    context_projection_verified = (
        isinstance(context_projection_summary, dict)
        and context_projection_summary.get("status") == "verified_public_context_projection"
    )
    stage_acceptance_blockers = _ordered_stage_blockers(domains)
    stage_acceptance_status = _stage_acceptance_status(
        runtime_relevant_source_matches=runtime_relevant_source_matches,
        context_projection_verified=context_projection_verified,
        stage_acceptance_blockers=stage_acceptance_blockers,
    )
    poc_loop_verified_for_runtime_relevant_source = runtime_relevant_source_matches and context_projection_verified
    poc_loop_verified_for_current_source = runtime_matches_source_tree and context_projection_verified
    decision_summary = {
        "reviewed_poc_loop_evidence_available": True,
        "controlled_poc_loop_verified_for_current_source": poc_loop_verified_for_current_source,
        "controlled_core_poc_loop_verified_for_runtime_relevant_source": (
            poc_loop_verified_for_runtime_relevant_source
        ),
        "runtime_relevant_source_verified_by_running_runtime": runtime_relevant_source_matches,
        "current_source_verified_by_running_runtime": runtime_matches_source_tree,
        "current_source_exact_runtime_commit_match": runtime_matches_source_tree,
        "runtime_rollout_required_for_current_source": not runtime_relevant_source_matches,
        "foundation_alpha_stage_complete": stage_acceptance_status == "foundation_alpha_stage_complete",
        "foundation_alpha_stage_status": stage_acceptance_status,
        "stage_acceptance_blockers": stage_acceptance_blockers,
        "can_enter_next_stage_without_restrictions": False,
        "production_claim_allowed": False,
        "ordinary_user_multi_agent_allowed": False,
        "docker_sandbox_hardened_claim_allowed": False,
        "capacity_default_increase_allowed": False,
    }

    evidence_entries = {
        "poc_smoke": _path_for_output(smoke_evidence_path),
        "auth_rbac_smoke": _path_for_output(auth_rbac_evidence_path),
    }
    if governance_runtime_evidence_path is not None and governance_runtime_smoke_verified:
        evidence_entries["governance_runtime_smoke"] = _path_for_output(governance_runtime_evidence_path)
    if (
        release_evidence_runtime_acceptance_path is not None
        and release_evidence_runtime_acceptance_verified
    ):
        evidence_entries["release_evidence_runtime_acceptance"] = _path_for_output(
            release_evidence_runtime_acceptance_path
        )
    if (
        alert_trace_export_runtime_acceptance_path is not None
        and alert_trace_export_runtime_acceptance_verified
    ):
        evidence_entries["alert_trace_export_runtime_acceptance"] = _path_for_output(
            alert_trace_export_runtime_acceptance_path
        )
    if frontend_packaged_runtime_smoke_path is not None and frontend_packaged_runtime_smoke_verified:
        evidence_entries["frontend_packaged_runtime_smoke"] = _path_for_output(
            frontend_packaged_runtime_smoke_path
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE_NAME,
        "status": _top_level_status(runtime_relation["status"], runtime_matches_source_tree),
        "source_tree_commit_sha": source_tree_commit,
        "source_tree_dirty": source_tree_dirty,
        "runtime_subject_commit_sha": runtime_subject_commit,
        "current_source_verified_by_running_runtime": decision_summary[
            "current_source_verified_by_running_runtime"
        ],
        "runtime_relevant_source_verified_by_running_runtime": decision_summary[
            "runtime_relevant_source_verified_by_running_runtime"
        ],
        "controlled_poc_loop_verified_for_current_source": decision_summary[
            "controlled_poc_loop_verified_for_current_source"
        ],
        "controlled_core_poc_loop_verified_for_runtime_relevant_source": decision_summary[
            "controlled_core_poc_loop_verified_for_runtime_relevant_source"
        ],
        "foundation_alpha_stage_complete": decision_summary["foundation_alpha_stage_complete"],
        "foundation_alpha_stage_status": decision_summary["foundation_alpha_stage_status"],
        "runtime_source_relation": runtime_relation,
        "verified_runtime_subject": _verified_runtime_subject(smoke, evidence_scope),
        "evidence_entries": evidence_entries,
        "operator_context": _operator_context(
            runtime_relation,
            context_projection_verified=context_projection_verified,
            stage_acceptance_status=stage_acceptance_status,
            stage_acceptance_blockers=stage_acceptance_blockers,
            governance_runtime_smoke_verified=governance_runtime_smoke_verified,
            release_evidence_runtime_acceptance_verified=release_evidence_runtime_acceptance_verified,
            alert_trace_export_runtime_acceptance_verified=alert_trace_export_runtime_acceptance_verified,
            frontend_packaged_runtime_smoke_verified=frontend_packaged_runtime_smoke_verified,
            broader_auth_regression_verified=broader_auth_regression_verified,
        ),
        "decision": decision_summary,
        "domains": domains,
        "open_followups": _top_level_open_followups(
            stage_acceptance_blockers,
            excluded_static_followups=(
                {"packaged_frontend_image_release_acceptance"}
                if frontend_packaged_runtime_smoke_verified
                else set()
            )
            | (
                {"broader_auth_session_rbac_tenant_redaction_regression"}
                if broader_auth_regression_verified
                else set()
            ),
        ),
        "evidence_policy": "source_docs_tests_211_smoke_and_release_evidence_required_before_stage_closure",
    }


def render_foundation_alpha_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render Foundation Alpha POC readiness as operator-readable Markdown."""
    decision = readiness["decision"]
    operator_context = readiness["operator_context"]
    verified_runtime_subject = readiness["verified_runtime_subject"]
    decision_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in decision.items())
    verified_capabilities = "\n".join(f"- {item}" for item in operator_context["verified_poc_capabilities"])
    blocked_expansions = "\n".join(f"- {item}" for item in operator_context["blocked_expansions"])
    next_slices = "\n".join(f"- {item}" for item in operator_context["next_recommended_slices"])
    followups = "\n".join(f"- {item}" for item in readiness["open_followups"])
    domain_sections: list[str] = []
    for name, domain in readiness["domains"].items():
        domain_followups = "\n".join(f"- {item}" for item in domain.get("open_followups", [])) or "- none"
        evidence_lines = ""
        if name == "g6_poc_governance":
            context_projection = domain.get("evidence", {}).get("context_snapshot_public_projection")
            if isinstance(context_projection, dict):
                counts = context_projection.get("referenced_material_counts")
                counts = counts if isinstance(counts, dict) else {}
                count_summary = (
                    f"message={int(counts.get('message_count') or 0)}, "
                    f"file={int(counts.get('file_count') or 0)}, "
                    f"artifact={int(counts.get('artifact_count') or 0)}, "
                    f"memory={int(counts.get('memory_record_count') or 0)}"
                )
                evidence_lines = (
                    "\n"
                    f"Context snapshot public projection: `{context_projection.get('status')}`\n\n"
                    f"Context referenced material counts: `{count_summary}`\n\n"
                )
                input_keys = context_projection.get("input_keys")
                input_key_summary = ",".join(input_keys) if isinstance(input_keys, list) and input_keys else "none"
                evidence_lines += (
                    "Context public summary: `"
                    f"source={context_projection.get('summary_source')}, "
                    f"input_keys={input_key_summary}, "
                    f"memory_policy={context_projection.get('memory_policy_source')}, "
                    f"long_term_memory_read={context_projection.get('long_term_memory_read')}, "
                    f"tier={context_projection.get('execution_tier')}, "
                    f"generated_at={context_projection.get('context_pack_generated_at_present')}`\n\n"
                )
                missing_fields = context_projection.get("missing_public_summary_fields")
                if isinstance(missing_fields, list) and missing_fields:
                    missing_summary = ",".join(str(item) for item in missing_fields)
                    evidence_lines += f"Missing context public summary fields: `{missing_summary}`\n\n"
        if name == "frontend_poc":
            frontend_traceability = domain.get("evidence", {}).get("frontend_release_traceability")
            if isinstance(frontend_traceability, dict):
                evidence_lines = (
                    "\n"
                    f"Frontend release traceability: `{frontend_traceability.get('status')}`\n\n"
                    "Frontend build summary: `"
                    f"dist={frontend_traceability.get('dist_status')}, "
                    f"provenance={frontend_traceability.get('dist_build_provenance_status')}, "
                    f"same_commit={frontend_traceability.get('dist_build_verified_same_commit')}, "
                    f"ci_projection_audit={frontend_traceability.get('ci_verify_includes_projection_audit')}, "
                    f"workflow={frontend_traceability.get('workflow_status')}, "
                    f"packaged_image={frontend_traceability.get('packaged_frontend_image_status')}`\n\n"
                )
                frontend_blockers = frontend_traceability.get("blockers")
                if isinstance(frontend_blockers, list) and frontend_blockers:
                    blocker_summary = ",".join(str(item) for item in frontend_blockers)
                    evidence_lines += f"Frontend traceability blockers: `{blocker_summary}`\n\n"
            frontend_projection_audit = domain.get("evidence", {}).get("frontend_projection_audit")
            if isinstance(frontend_projection_audit, dict):
                evidence_lines += (
                    "Frontend projection audit: `"
                    f"status={frontend_projection_audit.get('status')}, "
                    f"ordinary_user={frontend_projection_audit.get('ordinary_user_acceptance')}, "
                    f"active_legacy_routes={frontend_projection_audit.get('active_legacy_route_count')}, "
                    f"ordinary_reachable_legacy_routes={frontend_projection_audit.get('ordinary_user_reachable_legacy_route_count')}, "
                    f"active_forbidden_terms={frontend_projection_audit.get('active_forbidden_projection_violation_count')}, "
                    f"ci_projection_audit={frontend_projection_audit.get('ci_verify_includes_projection_audit')}`\n\n"
                )
        domain_sections.append(
            f"### {name}\n\n"
            f"Status: `{domain['status']}`\n\n"
            f"{evidence_lines}"
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
        "## Operator Context\n\n"
        f"POC scope: `{operator_context['poc_scope']}`\n\n"
        f"POC loop status: `{operator_context['poc_loop_status']}`\n\n"
        f"Stage acceptance status: `{operator_context['stage_acceptance_status']}`\n\n"
        f"Stage gate: `{operator_context['stage_gate']}`\n\n"
        "Verified POC capabilities:\n\n"
        f"{verified_capabilities}\n\n"
        "Blocked expansions:\n\n"
        f"{blocked_expansions}\n\n"
        "Next recommended slices:\n\n"
        f"{next_slices}\n\n"
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
