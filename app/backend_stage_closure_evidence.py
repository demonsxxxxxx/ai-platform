from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai-platform.backend-stage-closure-evidence.v1"
_EVIDENCE_ROOT = "docs/release-evidence/backend-stage-closures"


def _path_for_output(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return path.as_posix()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _non_empty_bool_map(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and all(
        isinstance(key, str) and isinstance(item, bool) for key, item in value.items()
    )


def find_stage_issue_closure_evidence(
    repo_root: Path,
    *,
    issue: str,
    backend_stage: str,
    closed_gap: str,
) -> dict[str, Any] | None:
    """Return reviewed local issue-closure evidence for a backend stage gap."""
    evidence_root = repo_root / _EVIDENCE_ROOT
    if not evidence_root.exists():
        return None

    candidates: list[dict[str, Any]] = []
    for path in sorted(evidence_root.rglob("*.json")):
        payload = _load_json(path)
        if payload is None:
            continue
        if (
            payload.get("schema_version") != SCHEMA_VERSION
            or payload.get("issue") != issue
            or payload.get("issue_state") != "closed"
            or payload.get("backend_stage") != backend_stage
            or payload.get("closed_gap") != closed_gap
            or payload.get("review_status") != "reviewed"
            or payload.get("redaction_scan_status") != "passed"
        ):
            continue
        if payload.get("does_not_close_broader_gate") is not True:
            continue
        linked_prs = payload.get("linked_prs")
        closure_comments = payload.get("closure_comments")
        evidence_refs = payload.get("evidence_refs")
        residual_caveats = payload.get("residual_caveats")
        non_expansion_invariants = payload.get("non_expansion_invariants")
        if (
            not isinstance(linked_prs, list)
            or not linked_prs
            or not isinstance(closure_comments, list)
            or not closure_comments
            or not _non_empty_string_list(evidence_refs)
            or not _non_empty_string_list(residual_caveats)
            or not _non_empty_bool_map(non_expansion_invariants)
        ):
            continue
        candidates.append({**payload, "path": _path_for_output(path, repo_root)})

    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            str(item.get("closed_at") or ""),
            str(item.get("path") or ""),
        ),
        reverse=True,
    )
    return candidates[0]
