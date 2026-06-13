from __future__ import annotations

from pathlib import Path
from typing import Any

from app.release_evidence_export_acceptance import build_release_evidence_export_acceptance
from app.release_evidence_readiness import build_release_evidence_readiness


SCHEMA_VERSION = "ai-platform.release-evidence-runtime-acceptance.v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = ROOT / "docs" / "release-evidence"
EXTERNAL_EVIDENCE_ROOT_MARKER = "<external-evidence-root>"


def _safe_evidence_root_for_output(root: Path) -> str:
    resolved_root = root.resolve()
    resolved_repo_root = ROOT.resolve()
    resolved_default_root = DEFAULT_EVIDENCE_ROOT.resolve()
    if resolved_root == resolved_default_root or resolved_default_root in resolved_root.parents:
        return str(resolved_root.relative_to(resolved_repo_root)).replace("\\", "/")
    return EXTERNAL_EVIDENCE_ROOT_MARKER


def _runtime_export_acceptance_summary(export_acceptance: dict[str, Any]) -> dict[str, Any]:
    safe_fields = set(export_acceptance.get("safe_entry_fields") or [])
    entries = export_acceptance.get("entries") if isinstance(export_acceptance.get("entries"), list) else []
    return {
        "status": export_acceptance.get("status"),
        "export_policy": export_acceptance.get("export_policy"),
        "safe_entry_count": export_acceptance.get("safe_entry_count"),
        "blocked_entry_count": export_acceptance.get("blocked_entry_count"),
        "excluded_entry_count": export_acceptance.get("excluded_entry_count"),
        "safe_entry_fields_only": all(
            isinstance(entry, dict) and set(entry).issubset(safe_fields) for entry in entries
        ),
        "does_not_export_raw_runtime_payloads": export_acceptance.get("does_not_export_raw_runtime_payloads")
        is True,
    }


def _retention_runtime_acceptance_summary(retention_policy: dict[str, Any]) -> dict[str, Any]:
    forbidden_delete_targets = retention_policy.get("forbidden_delete_targets")
    forbidden_delete_targets_present = isinstance(forbidden_delete_targets, list) and {
        "raw runtime payload",
        "executor private payload",
        "raw storage key",
        "sandbox workdir",
        "secret material",
        "unreviewed evidence draft",
    }.issubset(set(forbidden_delete_targets))
    accepted = (
        retention_policy.get("schema_version") == "ai-platform.release-evidence-retention-policy.v1"
        and retention_policy.get("status") == "contract_only_not_runtime_enforced"
        and retention_policy.get("default_retention_days") == 180
        and retention_policy.get("minimum_retention_days") == 30
        and retention_policy.get("requires_review_before_delete") is True
        and retention_policy.get("delete_only_reviewed_redacted_entries") is True
        and forbidden_delete_targets_present
    )
    return {
        "status": "accepted_review_first_policy" if accepted else "retention_policy_followup_required",
        "schema_version": retention_policy.get("schema_version"),
        "policy_status": retention_policy.get("status"),
        "default_retention_days": retention_policy.get("default_retention_days"),
        "minimum_retention_days": retention_policy.get("minimum_retention_days"),
        "requires_review_before_delete": retention_policy.get("requires_review_before_delete"),
        "delete_only_reviewed_redacted_entries": retention_policy.get("delete_only_reviewed_redacted_entries"),
        "forbidden_delete_targets_present": forbidden_delete_targets_present,
    }


def build_release_evidence_runtime_acceptance(
    *,
    evidence_root: Path | str,
    commit_sha: str = "unknown",
    runtime_subject_commit_sha: str = "",
    image: str = "",
) -> dict[str, Any]:
    """Verify runtime-packaged release evidence can emit a safe index and retention policy summary."""
    root = Path(evidence_root)
    export_acceptance = build_release_evidence_export_acceptance(evidence_root=root)
    readiness = build_release_evidence_readiness()
    retention_policy = readiness["retention_policy"]

    runtime_export = _runtime_export_acceptance_summary(export_acceptance)
    retention_acceptance = _retention_runtime_acceptance_summary(retention_policy)
    runtime_export_ok = (
        runtime_export["status"] == "ready_for_operator_review"
        and runtime_export["export_policy"] == "safe_reviewed_index_only_not_runtime_export"
        and runtime_export["blocked_entry_count"] == 0
        and runtime_export["safe_entry_fields_only"] is True
        and runtime_export["does_not_export_raw_runtime_payloads"] is True
    )
    retention_ok = retention_acceptance["status"] == "accepted_review_first_policy"
    open_gaps: list[str] = []
    if not runtime_export_ok:
        open_gaps.append("release_evidence_runtime_export_acceptance")
    if not retention_ok:
        open_gaps.append("release_evidence_retention_runtime_acceptance")
    ok = not open_gaps
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "status": "accepted_for_operator_review" if ok else "blocked_runtime_acceptance",
        "source": {
            "commit_sha": commit_sha,
            "runtime_subject_commit_sha": runtime_subject_commit_sha or commit_sha,
            "image": image,
            "evidence_root": _safe_evidence_root_for_output(root),
        },
        "checks": {
            "runtime_export_acceptance": runtime_export,
            "retention_runtime_acceptance": retention_acceptance,
        },
        "open_gaps": open_gaps,
        "does_not_export_raw_runtime_payloads": True,
        "does_not_close_g9": True,
    }
