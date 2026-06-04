#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Document Reviewer - Pipeline Gate Validator

Validate hard gates before final output/comment writing:
1) required review branches exist in branch_execution_manifest
2) branch manifest fields are complete and status is valid
3) issue required fields are complete
4) needs_user_check issues are present in human_review_queue
5) manifest issue_count matches actual issue counts by branch
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple


REQUIRED_BRANCHES = (
    "format",
    "project_number",
    "content_consistency",
)

OPTIONAL_ISSUE_BRANCHES = ("llm_full_review",)

ALLOWED_BRANCH_STATUS = (
    "completed",
    "completed_with_human_review",
    "completed_with_errors",
    "skipped",
    "failed",
)

MANIFEST_REQUIRED_FIELDS = (
    "branch",
    "agent_role",
    "status",
    "issue_count",
    "duration_ms",
    "retry_count",
    "error",
)

ISSUE_REQUIRED_FIELDS = (
    "branch",
    "agent_role",
    "rule_id",
    "type",
    "location",
    "document_zone",
    "severity",
    "comment_text",
    "evidence",
    "comment_visibility",
    "requires_external_evidence",
    "external_evidence_type",
    "coverage_domain",
    "review_basis",
    "comment_intent",
)

HUMAN_REVIEW_REQUIRED_FIELDS = (
    "branch",
    "reason",
    "location",
)

ALLOWED_ISSUE_BRANCHES = set(REQUIRED_BRANCHES) | set(OPTIONAL_ISSUE_BRANCHES) | {"human_review"}
ALLOWED_SEVERITIES = {"关键", "主要", "次要"}
ALLOWED_ISSUE_STATUS = {"confirmed", "needs_user_check"}
CHECK_STATUSES = {"needs_user_check"}
ALLOWED_MATCH_METHODS = {"span", "exact", "contains", "inference"}
ALLOWED_COMMENT_VISIBILITY = {"word_comment", "internal"}
ALLOWED_EXTERNAL_EVIDENCE_TYPES = {"none", "record", "protocol", "sample_info", "lims", "other"}
ALLOWED_COVERAGE_DOMAINS = {
    "format",
    "structure",
    "zh_language",
    "en_language",
    "bilingual",
    "data_consistency",
    "terminology",
    "external_check",
}
ALLOWED_REVIEW_BASIS = {"company_standard", "single_doc_internal", "agent_semantic", "external_required"}
ALLOWED_COMMENT_INTENTS = {"suggest_change", "request_check", "global_summary"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate document review pipeline hard gates from review JSON.")
    parser.add_argument("review_json", help="Path to review JSON (review-schema payload).")
    parser.add_argument(
        "--report-json",
        help="Optional output path for machine-readable validation report JSON.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Treat warnings as failure (non-zero exit code).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only summary line.",
    )
    return parser.parse_args()


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def to_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是"}:
        return True
    if text in {"0", "false", "no", "n", "否"}:
        return False
    return None


def infer_requires_external_evidence(
    *,
    requires_external_evidence: bool,
    external_type: str,
    review_basis: str,
    coverage_domain: str,
    comment_intent: str,
) -> bool:
    return (
        requires_external_evidence
        or (external_type not in {"", "none"})
        or review_basis == "external_required"
        or coverage_domain == "external_check"
    )


def is_global_word_comment_issue(item: Dict[str, Any]) -> bool:
    if as_text(item.get("comment_intent")) == "global_summary":
        return True
    if as_text(item.get("location_kind")) == "global":
        return True
    if as_text(item.get("location_kind")) in {"property", "section", "footer"}:
        return True
    if as_text(item.get("anchor_locator")) in {"section_properties", "footer_properties"}:
        return True
    location = as_text(item.get("location"))
    if location in {"全文审核意见", "全文人工复核"}:
        return True
    combined = "\n".join(as_text(item.get(key)) for key in ("original", "issue", "evidence"))
    return any(marker in combined for marker in ("全文", "全篇", "整体", "全局"))


def load_json(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"review_json not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"unable to read review_json: {path} ({exc})") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid json: {path} ({exc})") from exc

    if not isinstance(payload, dict):
        raise ValueError("top-level JSON must be an object")

    return payload


def validate_manifest(
    manifest: Any,
    errors: List[str],
    warnings: List[str],
) -> Dict[str, Dict[str, Any]]:
    if not isinstance(manifest, list):
        errors.append("branch_execution_manifest must be a list")
        return {}

    manifest_by_branch: Dict[str, Dict[str, Any]] = {}

    for index, item in enumerate(manifest):
        row = f"branch_execution_manifest[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{row} must be an object")
            continue

        for field in MANIFEST_REQUIRED_FIELDS:
            if field not in item:
                errors.append(f"{row} missing required field: {field}")

        branch = as_text(item.get("branch"))
        if not branch:
            errors.append(f"{row}.branch is empty")
        else:
            if branch in manifest_by_branch:
                errors.append(f"duplicate manifest branch: {branch}")
            manifest_by_branch[branch] = item

        agent_role = as_text(item.get("agent_role"))
        if not agent_role:
            errors.append(f"{row}.agent_role is empty")

        status = as_text(item.get("status"))
        if status not in ALLOWED_BRANCH_STATUS:
            errors.append(f"{row}.status invalid: {status!r}")

        issue_count = to_non_negative_int(item.get("issue_count"))
        if issue_count is None:
            errors.append(f"{row}.issue_count must be a non-negative integer")

        duration_ms = to_non_negative_int(item.get("duration_ms"))
        if duration_ms is None:
            errors.append(f"{row}.duration_ms must be a non-negative integer")

        retry_count = to_non_negative_int(item.get("retry_count"))
        if retry_count is None:
            errors.append(f"{row}.retry_count must be a non-negative integer")

        error_text = as_text(item.get("error"))
        if status in ("failed", "completed_with_errors") and not error_text:
            errors.append(f"{row}.error must be non-empty when status={status}")
        if status in ("completed", "completed_with_human_review") and error_text:
            warnings.append(f"{row}.error is non-empty while status={status}")

    manifest_branch_set = set(manifest_by_branch)
    missing_branches = [branch for branch in REQUIRED_BRANCHES if branch not in manifest_branch_set]
    if missing_branches:
        errors.append(f"missing required manifest branches: {', '.join(missing_branches)}")

    extra_branches = sorted(manifest_branch_set - set(REQUIRED_BRANCHES) - set(OPTIONAL_ISSUE_BRANCHES))
    if extra_branches:
        warnings.append(f"manifest has unknown branches: {', '.join(extra_branches)}")

    failed_branches = [branch for branch, item in manifest_by_branch.items() if as_text(item.get("status")) == "failed"]
    if failed_branches:
        errors.append(f"failed branches detected: {', '.join(sorted(failed_branches))}")

    return manifest_by_branch


def validate_issues(
    issues: Any,
    errors: List[str],
    warnings: List[str],
) -> Tuple[Counter, List[Tuple[str, str, str]]]:
    if not isinstance(issues, list):
        errors.append("issues must be a list")
        return Counter(), []

    issue_counter: Counter = Counter()
    check_issue_refs: List[Tuple[str, str, str]] = []

    for index, item in enumerate(issues):
        row = f"issues[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{row} must be an object")
            continue

        for field in ISSUE_REQUIRED_FIELDS:
            if not has_value(item.get(field)):
                errors.append(f"{row} missing required field or empty: {field}")

        branch = as_text(item.get("branch"))
        if branch and branch not in ALLOWED_ISSUE_BRANCHES:
            errors.append(f"{row}.branch not allowed: {branch}")
        if branch:
            issue_counter[branch] += 1

        severity = as_text(item.get("severity"))
        if severity and severity not in ALLOWED_SEVERITIES:
            errors.append(f"{row}.severity invalid: {severity}")

        status = as_text(item.get("status"))
        if status and status not in ALLOWED_ISSUE_STATUS:
            errors.append(f"{row}.status invalid: {status}")

        comment_visibility = as_text(item.get("comment_visibility"))
        if comment_visibility and comment_visibility not in ALLOWED_COMMENT_VISIBILITY:
            errors.append(f"{row}.comment_visibility invalid: {comment_visibility}")

        requires_external = to_bool(item.get("requires_external_evidence"))
        if requires_external is None:
            errors.append(f"{row}.requires_external_evidence must be boolean")
            requires_external = False

        external_type = as_text(item.get("external_evidence_type"))
        if external_type and external_type not in ALLOWED_EXTERNAL_EVIDENCE_TYPES:
            errors.append(f"{row}.external_evidence_type invalid: {external_type}")

        coverage_domain = as_text(item.get("coverage_domain"))
        if coverage_domain and coverage_domain not in ALLOWED_COVERAGE_DOMAINS:
            errors.append(f"{row}.coverage_domain invalid: {coverage_domain}")

        review_basis = as_text(item.get("review_basis"))
        if review_basis and review_basis not in ALLOWED_REVIEW_BASIS:
            errors.append(f"{row}.review_basis invalid: {review_basis}")

        comment_intent = as_text(item.get("comment_intent"))
        if comment_intent and comment_intent not in ALLOWED_COMMENT_INTENTS:
            errors.append(f"{row}.comment_intent invalid: {comment_intent}")

        inferred_external = infer_requires_external_evidence(
            requires_external_evidence=bool(requires_external),
            external_type=external_type,
            review_basis=review_basis,
            coverage_domain=coverage_domain,
            comment_intent=comment_intent,
        )
        if inferred_external and not requires_external:
            errors.append(
                f"{row} external evidence fields require requires_external_evidence=true"
            )

        if inferred_external:
            if status == "confirmed":
                errors.append(f"{row} external evidence dependent issues must not use status=confirmed")
            if comment_intent != "request_check":
                errors.append(f"{row} external evidence dependent issues must use comment_intent=request_check")
            if external_type == "none":
                errors.append(f"{row} external evidence dependent issues must set a concrete external_evidence_type")

        comments_added = item.get("comments_added", 1)
        if to_non_negative_int(comments_added) is None:
            errors.append(f"{row}.comments_added must be a non-negative integer")

        match_method = as_text(item.get("match_method"))
        if match_method and match_method not in ALLOWED_MATCH_METHODS:
            errors.append(f"{row}.match_method invalid: {match_method}")
        if match_method == "fallback":
            errors.append(f"{row}.match_method=fallback is not allowed in final review output")

        if to_non_negative_int(comments_added) != 0:
            if status and status not in {"confirmed", "needs_user_check"}:
                errors.append(f"{row} with comments_added>0 has unsupported status={status}")
            if not isinstance(item.get("anchor_span"), dict):
                errors.append(f"{row} with comments_added>0 must include a stable anchor_span")
            if not has_value(item.get("anchor_locator")) or not has_value(item.get("anchor_text")):
                errors.append(f"{row} missing stable anchor target fields for comment insertion")
            if match_method in {"fallback", "inference"}:
                errors.append(f"{row}.match_method={match_method} is not allowed for automatic comment insertion")
        elif comment_visibility == "word_comment" and not is_global_word_comment_issue(item):
            errors.append(f"{row} word_comment without stable anchor is only allowed for global summary issues")

        if status in CHECK_STATUSES:
            issue_id = as_text(item.get("id"))
            location = as_text(item.get("location"))
            check_issue_refs.append((issue_id, branch, location))
            if not issue_id:
                warnings.append(f"{row} status={status} but id is empty")

    return issue_counter, check_issue_refs


def validate_human_review_queue(
    queue: Any,
    check_issue_refs: List[Tuple[str, str, str]],
    errors: List[str],
) -> Tuple[int, set[str]]:
    if queue is None:
        queue = []

    if not isinstance(queue, list):
        errors.append("human_review_queue must be a list")
        return 0, set()

    queue_issue_ids = set()
    queue_branch_locations = set()
    queue_branches: set[str] = set()

    for index, item in enumerate(queue):
        row = f"human_review_queue[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{row} must be an object")
            continue

        for field in HUMAN_REVIEW_REQUIRED_FIELDS:
            if not has_value(item.get(field)):
                errors.append(f"{row} missing required field or empty: {field}")

        issue_id = as_text(item.get("issue_id"))
        branch = as_text(item.get("branch"))
        location = as_text(item.get("location"))

        if issue_id:
            queue_issue_ids.add(issue_id)
        if branch:
            queue_branches.add(branch)
        if branch and location:
            queue_branch_locations.add((branch, location))

    if check_issue_refs and not queue:
        errors.append("human_review_queue is empty but issues contain needs_user_check items")

    for issue_id, branch, location in check_issue_refs:
        if issue_id and issue_id in queue_issue_ids:
            continue
        if branch and location and (branch, location) in queue_branch_locations:
            continue
        errors.append(
            "needs_user_check issue missing from human_review_queue: "
            f"id={issue_id or '<empty>'}, branch={branch or '<empty>'}, location={location or '<empty>'}"
        )

    return len(queue), queue_branches


def validate_manifest_issue_counts(
    manifest_by_branch: Dict[str, Dict[str, Any]],
    issue_counter: Counter,
    errors: List[str],
) -> None:
    for branch in REQUIRED_BRANCHES:
        item = manifest_by_branch.get(branch)
        if not item:
            continue
        expected = to_non_negative_int(item.get("issue_count"))
        if expected is None:
            continue
        actual = int(issue_counter.get(branch, 0))
        if expected != actual:
            errors.append(f"issue_count mismatch for branch={branch}: manifest={expected}, issues={actual}")


def validate_partial_branch_visibility(
    manifest_by_branch: Dict[str, Dict[str, Any]],
    human_review_queue_branches: set[str],
    errors: List[str],
) -> None:
    for branch, item in manifest_by_branch.items():
        if as_text(item.get("status")) != "completed_with_errors":
            continue
        if branch in OPTIONAL_ISSUE_BRANCHES:
            continue
        if branch not in human_review_queue_branches:
            errors.append(
                f"branch={branch} status=completed_with_errors must have a visible human_review_queue entry"
            )


def build_report(
    payload: Dict[str, Any],
    errors: List[str],
    warnings: List[str],
    manifest_by_branch: Dict[str, Dict[str, Any]],
    issue_counter: Counter,
    human_review_queue_count: int,
) -> Dict[str, Any]:
    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "document": payload.get("document", ""),
            "required_branch_count": len(REQUIRED_BRANCHES),
            "manifest_branch_count": len(manifest_by_branch),
            "issues_total": int(sum(issue_counter.values())),
            "issues_by_branch": {branch: int(issue_counter.get(branch, 0)) for branch in REQUIRED_BRANCHES},
            "human_review_issues": int(issue_counter.get("human_review", 0)),
            "human_review_queue_count": int(human_review_queue_count),
        },
    }


def print_report(report: Dict[str, Any], quiet: bool) -> None:
    status = "PASS" if report["passed"] else "FAIL"
    errors = report.get("errors", [])
    warnings = report.get("warnings", [])
    summary = report.get("summary", {})
    print(
        f"[{status}] errors={len(errors)} warnings={len(warnings)} "
        f"manifest_branches={summary.get('manifest_branch_count', 0)}/{summary.get('required_branch_count', 0)} "
        f"issues_total={summary.get('issues_total', 0)}"
    )
    if quiet:
        return

    if errors:
        print("Errors:")
        for item in errors:
            print(f"  - {item}")

    if warnings:
        print("Warnings:")
        for item in warnings:
            print(f"  - {item}")


def write_report_json(path: Path, report: Dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    errors: List[str] = []
    warnings: List[str] = []

    try:
        payload = load_json(Path(args.review_json))
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    manifest_by_branch = validate_manifest(payload.get("branch_execution_manifest"), errors, warnings)
    issue_counter, check_issue_refs = validate_issues(payload.get("issues"), errors, warnings)
    human_review_queue_count, human_review_queue_branches = validate_human_review_queue(
        payload.get("human_review_queue"),
        check_issue_refs,
        errors,
    )
    validate_manifest_issue_counts(manifest_by_branch, issue_counter, errors)
    validate_partial_branch_visibility(manifest_by_branch, human_review_queue_branches, errors)

    report = build_report(payload, errors, warnings, manifest_by_branch, issue_counter, human_review_queue_count)
    print_report(report, quiet=args.quiet)

    if args.report_json:
        try:
            write_report_json(Path(args.report_json), report)
        except OSError as exc:
            print(f"[FAIL] unable to write report json: {exc}", file=sys.stderr)
            return 1

    if errors:
        return 1
    if warnings and args.fail_on_warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
