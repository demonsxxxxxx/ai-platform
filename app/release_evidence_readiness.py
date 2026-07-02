from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.release_evidence_export_acceptance import build_release_evidence_export_acceptance


SCHEMA_VERSION = "ai-platform.release-evidence-readiness.v1"
ENTRY_SCHEMA_VERSION = "ai-platform.release-evidence-entry.v1"
RETENTION_POLICY_SCHEMA_VERSION = "ai-platform.release-evidence-retention-policy.v1"
GATE_NAME = "G9 Release Evidence Export"

_EXPORT_LOCATION = {
    "type": "repository_path",
    "path": "docs/release-evidence/",
    "index": "docs/release-evidence/README.md",
    "write_policy": "append_reviewed_redacted_evidence_entries_only",
}
_REQUIRED_FIELDS = [
    "evidence_id",
    "commit_sha",
    "gate",
    "issue_refs",
    "artifact_kind",
    "captured_at",
    "source_ref",
    "evidence_ref",
    "redaction_scan_status",
    "review_status",
]
_FIELD_SEMANTICS = {
    "commit_sha": "verified subject commit for the runtime, capacity, frontend, or governance artifact under review",
    "runtime_subject_commit_sha": (
        "runtime source revision proven by 211 source marker and API/worker image labels "
        "for runtime-bound smoke artifacts"
    ),
    "record_commit_sha": (
        "not embedded because a git commit cannot contain its own final hash; "
        "use VCS history to identify the commit that introduced or updated an evidence record"
    ),
    "capacity_gate_readiness": (
        "B3 capacity visibility and fail-closed readiness evidence; it is not "
        "operator-reviewed recorded load evidence and cannot close B3 by itself"
    ),
}
_CONDITIONAL_FIELDS = {
    "211_runtime_smoke": [
        "runtime_subject_commit_sha",
    ],
    "211_memory_enabled_document_workflow_smoke": [
        "runtime_subject_commit_sha",
    ],
    "211_sandbox_runtime_smoke": [
        "runtime_subject_commit_sha",
    ],
    "211_runtime_identity_label_repair": [
        "runtime_subject_commit_sha",
    ],
}
_FORBIDDEN_MARKER_CLASSES = [
    "executor private payload",
    "raw storage key",
    "sandbox workdir",
    "secret material",
    "API key",
    "bearer token",
    "database URL",
    "Redis URL",
]
_ACCEPTED_ARTIFACT_KINDS = [
    "211_runtime_smoke",
    "capacity_gate_readiness",
    "frontend_packaged_runtime_smoke",
    "frontend_release_traceability",
    "governance_readiness",
    "observability_readiness",
    "alert_trace_export_runtime_acceptance",
    "211_memory_enabled_document_workflow_smoke",
    "211_sandbox_runtime_smoke",
    "211_runtime_identity_label_repair",
]
_RETENTION_POLICY = {
    "schema_version": RETENTION_POLICY_SCHEMA_VERSION,
    "status": "contract_only_not_runtime_enforced",
    "default_retention_days": 180,
    "minimum_retention_days": 30,
    "requires_review_before_delete": True,
    "delete_only_reviewed_redacted_entries": True,
    "forbidden_delete_targets": [
        "raw runtime payload",
        "executor private payload",
        "raw storage key",
        "sandbox workdir",
        "secret material",
        "unreviewed evidence draft",
    ],
    "does_not_close_g9": True,
}
_OPEN_GAPS = [
    "release_evidence_runtime_export_acceptance",
    "release_evidence_retention_runtime_acceptance",
]
_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_EVIDENCE_ROOT = _ROOT / "docs/release-evidence"


def _runtime_acceptance_is_valid(runtime_acceptance: dict[str, Any]) -> bool:
    checks = runtime_acceptance.get("checks")
    if not isinstance(checks, dict):
        return False
    runtime_export = checks.get("runtime_export_acceptance")
    retention = checks.get("retention_runtime_acceptance")
    if not isinstance(runtime_export, dict) or not isinstance(retention, dict):
        return False
    return (
        runtime_acceptance.get("schema_version") == "ai-platform.release-evidence-runtime-acceptance.v1"
        and runtime_acceptance.get("ok") is True
        and runtime_acceptance.get("status") == "accepted_for_operator_review"
        and runtime_acceptance.get("open_gaps") == []
        and runtime_acceptance.get("does_not_export_raw_runtime_payloads") is True
        and runtime_acceptance.get("does_not_close_g9") is True
        and runtime_export.get("status") == "ready_for_operator_review"
        and runtime_export.get("export_policy") == "safe_reviewed_index_only_not_runtime_export"
        and runtime_export.get("blocked_entry_count") == 0
        and runtime_export.get("safe_entry_fields_only") is True
        and runtime_export.get("does_not_export_raw_runtime_payloads") is True
        and retention.get("status") == "accepted_review_first_policy"
        and retention.get("schema_version") == RETENTION_POLICY_SCHEMA_VERSION
        and retention.get("policy_status") == "contract_only_not_runtime_enforced"
        and retention.get("requires_review_before_delete") is True
        and retention.get("delete_only_reviewed_redacted_entries") is True
        and retention.get("forbidden_delete_targets_present") is True
    )


def _runtime_acceptance_summary(runtime_acceptance: dict[str, Any]) -> dict[str, Any]:
    checks = runtime_acceptance.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    return {
        "schema_version": runtime_acceptance.get("schema_version"),
        "ok": runtime_acceptance.get("ok"),
        "status": runtime_acceptance.get("status"),
        "checks": {
            "runtime_export_acceptance": deepcopy(checks.get("runtime_export_acceptance") or {}),
            "retention_runtime_acceptance": deepcopy(checks.get("retention_runtime_acceptance") or {}),
        },
        "open_gaps": list(runtime_acceptance.get("open_gaps") or []),
        "does_not_export_raw_runtime_payloads": runtime_acceptance.get(
            "does_not_export_raw_runtime_payloads"
        ),
        "does_not_close_g9": runtime_acceptance.get("does_not_close_g9"),
    }


def load_latest_reviewed_runtime_acceptance(
    evidence_root: Path | None = None,
) -> dict[str, Any] | None:
    """Return the newest reviewed, redacted release-evidence runtime acceptance summary."""
    root = evidence_root or _DEFAULT_EVIDENCE_ROOT
    if not root.is_dir():
        return None

    candidates: list[tuple[tuple[str, str], dict[str, Any]]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        evidence_ref = payload.get("evidence_ref")
        runtime_checks = evidence_ref.get("runtime_checks") if isinstance(evidence_ref, dict) else {}
        acceptance = (
            runtime_checks.get("release_evidence_runtime_acceptance")
            if isinstance(runtime_checks, dict)
            else None
        )
        if not isinstance(acceptance, dict):
            continue
        if not _reviewed_runtime_acceptance_entry_is_valid(payload, acceptance):
            continue
        sort_key = (str(payload.get("captured_at", "")), path.as_posix())
        candidates.append((sort_key, _runtime_acceptance_summary(acceptance)))

    if not candidates:
        return None
    _, acceptance = max(candidates, key=lambda item: item[0])
    return acceptance


def _reviewed_runtime_acceptance_entry_is_valid(
    payload: dict[str, Any],
    acceptance: dict[str, Any],
) -> bool:
    evidence_ref = payload.get("evidence_ref")
    if not isinstance(evidence_ref, dict):
        return False
    commit_sha = payload.get("commit_sha")
    return (
        payload.get("schema_version") == ENTRY_SCHEMA_VERSION
        and payload.get("artifact_kind") == "211_runtime_smoke"
        and isinstance(commit_sha, str)
        and commit_sha
        and payload.get("runtime_subject_commit_sha") == commit_sha
        and payload.get("redaction_scan_status") == "passed"
        and payload.get("review_status") in {"reviewed", "accepted"}
        and evidence_ref.get("verifier") == "tools/verify_release_evidence_runtime_acceptance.py"
        and evidence_ref.get("schema_version") == "ai-platform.release-evidence-runtime-acceptance.v1"
        and evidence_ref.get("result") == "ok:true"
        and _runtime_acceptance_is_valid(acceptance)
    )


def build_release_evidence_readiness(
    *,
    runtime_acceptance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the source-level G9 release evidence export-location contract."""
    export_acceptance = build_release_evidence_export_acceptance()
    open_gaps = list(_OPEN_GAPS)
    runtime_acceptance_valid = (
        isinstance(runtime_acceptance, dict) and _runtime_acceptance_is_valid(runtime_acceptance)
    )
    if runtime_acceptance_valid:
        open_gaps = []

    readiness = {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": "partial_blocked" if open_gaps else "ready_for_verification",
        "active_export_policy": "location_contract_only_not_runtime_export",
        "export_location": deepcopy(_EXPORT_LOCATION),
        "evidence_contract": {
            "schema_version": ENTRY_SCHEMA_VERSION,
            "write_path": "docs/release-evidence/<gate>/<commit_sha>/<evidence_id>.json",
            "required_fields": list(_REQUIRED_FIELDS),
            "field_semantics": deepcopy(_FIELD_SEMANTICS),
            "conditional_fields": deepcopy(_CONDITIONAL_FIELDS),
            "accepted_artifact_kinds": list(_ACCEPTED_ARTIFACT_KINDS),
            "accepted_redaction_scan_statuses": ["passed"],
            "accepted_review_statuses": ["reviewed", "accepted"],
            "forbidden_marker_classes": list(_FORBIDDEN_MARKER_CLASSES),
            "does_not_export_raw_runtime_payloads": True,
            "does_not_close_g9": True,
        },
        "export_acceptance": export_acceptance,
        "retention_policy": deepcopy(_RETENTION_POLICY),
        "open_gaps": open_gaps,
        "does_not_close_g9": True,
    }
    if runtime_acceptance_valid:
        readiness["runtime_acceptance"] = _runtime_acceptance_summary(runtime_acceptance)
    return readiness
