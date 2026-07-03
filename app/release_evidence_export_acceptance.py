from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai-platform.release-evidence-export-acceptance.v1"
ENTRY_SCHEMA_VERSION = "ai-platform.release-evidence-entry.v1"
GATE_NAME = "G9 Release Evidence Export Acceptance"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = ROOT / "docs" / "release-evidence"

_SAFE_ENTRY_FIELDS = (
    "path",
    "evidence_id",
    "commit_sha",
    "runtime_subject_commit_sha",
    "gate",
    "issue_refs",
    "pr_refs",
    "artifact_kind",
    "captured_at",
    "redaction_scan_status",
    "review_status",
)
_REQUIRED_FIELDS = (
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
)
_FORBIDDEN_EXACT_KEYS = {
    "api_key",
    "authorization",
    "bearer_token",
    "database_url",
    "executor_private_payload",
    "password",
    "private_payload",
    "raw_storage_key",
    "redis_url",
    "sandbox_workdir",
    "sandbox_work_dir",
}
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"\b(?:postgres|postgresql|mysql|redis)://[^\s\"']+", re.IGNORECASE),
    re.compile(r"\bc:\\users\\", re.IGNORECASE),
    re.compile(r"/home/[^\s\"']+", re.IGNORECASE),
    re.compile(r"/users/[^\s\"']+", re.IGNORECASE),
    re.compile(r"/tmp/ai-platform-compose[^\s\"']*", re.IGNORECASE),
    re.compile(r"/var/run/docker\.sock", re.IGNORECASE),
)
_LEGACY_EXCLUSION_REASONS = {
    "missing_runtime_subject_commit_sha",
}
_ALLOWED_REDACTED_MARKERS = (
    "<redacted>",
    "[redacted]",
    "[redacted-secret]",
)
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_SUBJECT_ARTIFACT_KINDS = {
    "211_runtime_smoke",
    "211_memory_enabled_document_workflow_smoke",
    "211_sandbox_runtime_smoke",
    "211_runtime_identity_label_repair",
    "211_deployment_image_cleanup",
}


def _path_for_output(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_release_evidence_entry_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    parts = relative.parts
    return len(parts) >= 3 and _COMMIT_SHA_RE.fullmatch(parts[-2]) is not None


def _normalized_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _safe_negative_or_redacted_value(value: Any) -> bool:
    if value is False or value is None:
        return True
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _ALLOWED_REDACTED_MARKERS)
    return False


def _contains_forbidden_marker(value: Any, *, key_context: str | None = None) -> bool:
    if key_context and _normalized_key(key_context) in _FORBIDDEN_EXACT_KEYS:
        return not _safe_negative_or_redacted_value(value)
    if isinstance(value, dict):
        return any(
            _contains_forbidden_marker(item, key_context=str(key))
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in _ALLOWED_REDACTED_MARKERS):
            masked = lowered
            for marker in _ALLOWED_REDACTED_MARKERS:
                masked = masked.replace(marker, "")
            return any(pattern.search(masked) for pattern in _FORBIDDEN_VALUE_PATTERNS)
        return any(pattern.search(value) for pattern in _FORBIDDEN_VALUE_PATTERNS)
    return False


def _entry_blockers(entry: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if entry.get("schema_version") != ENTRY_SCHEMA_VERSION:
        blockers.append("invalid_schema_version")
    for field in _REQUIRED_FIELDS:
        if field not in entry:
            blockers.append(f"missing_{field}")
    if entry.get("redaction_scan_status") != "passed":
        blockers.append("redaction_scan_not_passed")
    if entry.get("review_status") not in {"reviewed", "accepted"}:
        blockers.append("review_status_not_accepted")
    if entry.get("artifact_kind") in _RUNTIME_SUBJECT_ARTIFACT_KINDS and "runtime_subject_commit_sha" not in entry:
        blockers.append("missing_runtime_subject_commit_sha")
    if _contains_forbidden_marker(entry):
        blockers.append("forbidden_marker_detected")
    return sorted(set(blockers))


def _entry_can_be_excluded_as_legacy(
    entry: dict[str, Any],
    entry_blockers: list[str],
) -> bool:
    return (
        entry.get("artifact_kind") == "211_runtime_smoke"
        and set(entry_blockers).issubset(_LEGACY_EXCLUSION_REASONS)
    )


def _safe_entry(entry: dict[str, Any], *, path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": _path_for_output(path, root),
        "evidence_id": entry.get("evidence_id"),
        "commit_sha": entry.get("commit_sha"),
        "runtime_subject_commit_sha": entry.get("runtime_subject_commit_sha"),
        "gate": entry.get("gate"),
        "issue_refs": list(entry.get("issue_refs") or []),
        "pr_refs": list(entry.get("pr_refs") or []),
        "artifact_kind": entry.get("artifact_kind"),
        "captured_at": entry.get("captured_at"),
        "redaction_scan_status": entry.get("redaction_scan_status"),
        "review_status": entry.get("review_status"),
    }


def build_release_evidence_export_acceptance(
    *,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
) -> dict[str, Any]:
    """Build a safe reviewed-evidence export index without exporting raw runtime payloads."""
    root = Path(evidence_root)
    entry_count = 0
    entries: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    excluded_entries: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*.json")) if root.exists() else []:
        entry_count += 1
        if not _is_release_evidence_entry_path(path, root):
            excluded_entries.append(
                {
                    "path": _path_for_output(path, root),
                    "reasons": ["non_release_evidence_entry_path"],
                }
            )
            continue
        payload = _read_json(path)
        if payload is None:
            blockers.append(
                {
                    "path": _path_for_output(path, root),
                    "reasons": ["invalid_json"],
                }
            )
            continue
        entry_blockers = _entry_blockers(payload)
        if entry_blockers:
            if _entry_can_be_excluded_as_legacy(payload, entry_blockers):
                excluded_entries.append(
                    {
                        "path": _path_for_output(path, root),
                        "reasons": entry_blockers,
                    }
                )
                continue
            blockers.append(
                {
                    "path": _path_for_output(path, root),
                    "reasons": entry_blockers,
                }
            )
            continue
        entries.append(_safe_entry(payload, path=path, root=root))

    blocker_codes = sorted({reason for blocker in blockers for reason in blocker["reasons"]})
    status = "ready_for_operator_review"
    if not root.exists():
        status = "blocked_evidence_root_missing"
        blocker_codes.append("evidence_root_missing")
    elif blockers:
        status = "blocked_forbidden_evidence" if "forbidden_marker_detected" in blocker_codes else "blocked_invalid_evidence"
    elif entry_count == 0:
        status = "blocked_no_evidence_entries"
        blocker_codes.append("no_evidence_entries")

    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "status": status,
        "export_policy": "safe_reviewed_index_only_not_runtime_export",
        "evidence_root": _path_for_output(root, ROOT),
        "entry_count": entry_count,
        "safe_entry_count": len(entries),
        "entries": entries,
        "blockers": blocker_codes,
        "blocked_entry_count": len(blockers),
        "blocked_entries": blockers,
        "excluded_entry_count": len(excluded_entries),
        "excluded_entries": excluded_entries,
        "safe_entry_fields": list(_SAFE_ENTRY_FIELDS),
        "open_gaps": [
            "release_evidence_runtime_export_acceptance",
            "release_evidence_retention_runtime_acceptance",
        ],
        "does_not_export_raw_runtime_payloads": True,
        "does_not_close_g9": True,
    }
