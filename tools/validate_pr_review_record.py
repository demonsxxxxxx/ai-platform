"""Validate the structured review-finding record in one pull-request body."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA_VERSION = "ai-platform.review-findings.v1"
REPORT_SCHEMA_VERSION = "ai-platform.review-findings-validation.v1"
MARKER = f"<!-- {SCHEMA_VERSION} -->"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
HANDLE = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
FINDING_ID = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
PLACEHOLDER = re.compile(r"REPLACE_ME", re.IGNORECASE)
PRIVATE_LOCAL_PATH = re.compile(
    r"(?:/(?:Users|home)/[^/\s]+(?:/|(?=\s|$))|"
    r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+(?:[\\/]+|(?=\s|$)))",
    re.IGNORECASE,
)
REPOSITORY_RELATIVE_PATH = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?![A-Za-z][A-Za-z0-9+.-]*://)"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?$"
)
HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*", re.IGNORECASE),
)

ROOT_FIELDS = {
    "schema_version",
    "review_subject",
    "no_material_findings",
    "findings",
}
REVIEW_SUBJECT_FIELDS = {"head_sha", "scope"}
NO_FINDINGS_FIELDS = {"reviewer_handle", "reviewer_role", "evidence_id"}
FINDING_FIELDS = {
    "id",
    "severity",
    "summary",
    "evidence_id",
    "disposition",
    "owner",
    "defer_exit_condition",
    "independent_confirmation",
    "promotion",
}
OWNER_FIELDS = {"handle", "role"}
CONFIRMATION_FIELDS = {"handle", "role", "evidence_id", "head_sha"}
PROMOTION_FIELDS = {"target", "rule_evidence"}
RULE_EVIDENCE_FIELDS = {
    "finding_class",
    "applicability_boundary",
    "detector",
    "bounded_paths",
    "alternatives",
    "false_positive_evidence",
    "removal_condition",
}


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def _violation(code: str, path: str, message: str) -> Violation:
    return Violation(code=code, path=path, message=message)


def _exact_fields(
    value: Any,
    *,
    fields: set[str],
    path: str,
    violations: list[Violation],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        violations.append(_violation("review_record_type_invalid", path, "must be an object"))
        return None
    if set(value) != fields:
        violations.append(
            _violation(
                "review_record_fields_invalid",
                path,
                f"expected exact fields: {','.join(sorted(fields))}",
            )
        )
        return None
    return value


def _bounded_text(
    value: Any,
    *,
    path: str,
    maximum: int,
    violations: list[Violation],
) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        violations.append(
            _violation(
                "review_record_text_invalid",
                path,
                f"must be nonblank and at most {maximum} characters",
            )
        )
        return None
    return value.strip()


def _validate_actor(
    value: Any,
    *,
    fields: set[str],
    path: str,
    expected_head: str | None,
    violations: list[Violation],
) -> None:
    actor = _exact_fields(value, fields=fields, path=path, violations=violations)
    if actor is None:
        return
    handle_field = "handle" if "handle" in fields else "reviewer_handle"
    role_field = "role" if "role" in fields else "reviewer_role"
    handle = _bounded_text(
        actor.get(handle_field),
        path=f"{path}.{handle_field}",
        maximum=40,
        violations=violations,
    )
    if handle is not None and HANDLE.fullmatch(handle) is None:
        violations.append(
            _violation(
                "review_record_handle_invalid",
                f"{path}.{handle_field}",
                "must be a GitHub @handle",
            )
        )
    _bounded_text(
        actor.get(role_field),
        path=f"{path}.{role_field}",
        maximum=80,
        violations=violations,
    )
    if "evidence_id" in fields:
        _bounded_text(
            actor.get("evidence_id"),
            path=f"{path}.evidence_id",
            maximum=256,
            violations=violations,
        )
    if "head_sha" in fields:
        head_sha = actor.get("head_sha")
        if not isinstance(head_sha, str) or FULL_SHA.fullmatch(head_sha) is None:
            violations.append(
                _violation("review_record_sha_invalid", f"{path}.head_sha", "must be 40 lowercase hex")
            )
        elif expected_head is not None and head_sha != expected_head:
            violations.append(
                _violation("review_record_sha_mismatch", f"{path}.head_sha", "must equal the PR head SHA")
            )


def _normalized_handle(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    handle = value.get("handle")
    if not isinstance(handle, str) or not handle.strip():
        return None
    return handle.strip().casefold()


def _validate_promotion(
    value: Any,
    *,
    path: str,
    violations: list[Violation],
) -> None:
    promotion = _exact_fields(
        value,
        fields=PROMOTION_FIELDS,
        path=path,
        violations=violations,
    )
    if promotion is None:
        return
    target = promotion.get("target")
    if target not in {"code", "test", "rule", "none"}:
        violations.append(
            _violation("review_record_promotion_invalid", f"{path}.target", "unsupported promotion target")
        )
    rule_evidence = promotion.get("rule_evidence")
    if target != "rule":
        if rule_evidence is not None:
            violations.append(
                _violation(
                    "review_record_rule_evidence_forbidden",
                    f"{path}.rule_evidence",
                    "must be null unless target is rule",
                )
            )
        return
    rule = _exact_fields(
        rule_evidence,
        fields=RULE_EVIDENCE_FIELDS,
        path=f"{path}.rule_evidence",
        violations=violations,
    )
    if rule is None:
        return
    for field_name in RULE_EVIDENCE_FIELDS - {"bounded_paths"}:
        _bounded_text(
            rule.get(field_name),
            path=f"{path}.rule_evidence.{field_name}",
            maximum=500,
            violations=violations,
        )
    bounded_paths = rule.get("bounded_paths")
    if (
        not isinstance(bounded_paths, list)
        or not bounded_paths
        or any(
            not isinstance(item, str)
            or REPOSITORY_RELATIVE_PATH.fullmatch(item) is None
            for item in bounded_paths
        )
    ):
        violations.append(
            _violation(
                "review_record_bounded_paths_invalid",
                f"{path}.rule_evidence.bounded_paths",
                "must be a non-empty list of repository-relative paths",
            )
        )


def _validate_finding(
    value: Any,
    *,
    index: int,
    expected_head: str | None,
    violations: list[Violation],
) -> str | None:
    path = f"findings[{index}]"
    finding = _exact_fields(
        value,
        fields=FINDING_FIELDS,
        path=path,
        violations=violations,
    )
    if finding is None:
        return None
    finding_id = finding.get("id")
    if not isinstance(finding_id, str) or FINDING_ID.fullmatch(finding_id) is None:
        violations.append(
            _violation("review_record_finding_id_invalid", f"{path}.id", "must be a stable uppercase ID")
        )
        finding_id = None
    if finding.get("severity") not in {"P0", "P1", "P2", "P3"}:
        violations.append(
            _violation("review_record_severity_invalid", f"{path}.severity", "must be P0, P1, P2, or P3")
        )
    _bounded_text(
        finding.get("summary"),
        path=f"{path}.summary",
        maximum=500,
        violations=violations,
    )
    _bounded_text(
        finding.get("evidence_id"),
        path=f"{path}.evidence_id",
        maximum=256,
        violations=violations,
    )
    disposition = finding.get("disposition")
    if disposition not in {"fixed", "rejected_with_evidence", "deferred"}:
        violations.append(
            _violation("review_record_disposition_invalid", f"{path}.disposition", "unsupported disposition")
        )
    confirmation = finding.get("independent_confirmation")
    _validate_actor(
        confirmation,
        fields=CONFIRMATION_FIELDS,
        path=f"{path}.independent_confirmation",
        expected_head=expected_head,
        violations=violations,
    )
    if disposition == "fixed":
        if finding.get("owner") is not None:
            violations.append(
                _violation(
                    "review_record_fixed_authority_invalid",
                    path,
                    "fixed findings keep owner null",
                )
            )
    else:
        owner = finding.get("owner")
        _validate_actor(
            owner,
            fields=OWNER_FIELDS,
            path=f"{path}.owner",
            expected_head=expected_head,
            violations=violations,
        )
        owner_handle = _normalized_handle(owner)
        confirmation_handle = _normalized_handle(confirmation)
        if owner_handle is not None and owner_handle == confirmation_handle:
            violations.append(
                _violation(
                    "review_record_confirmation_not_independent",
                    f"{path}.independent_confirmation.handle",
                    "must differ from the accountable owner",
                )
            )
    exit_condition = finding.get("defer_exit_condition")
    if disposition == "deferred":
        _bounded_text(
            exit_condition,
            path=f"{path}.defer_exit_condition",
            maximum=500,
            violations=violations,
        )
    elif exit_condition is not None:
        violations.append(
            _violation(
                "review_record_exit_condition_forbidden",
                f"{path}.defer_exit_condition",
                "must be null unless disposition is deferred",
            )
        )
    _validate_promotion(
        finding.get("promotion"),
        path=f"{path}.promotion",
        violations=violations,
    )
    return finding_id


def extract_review_record(body: str) -> dict[str, Any]:
    marker_index = body.find(MARKER)
    if marker_index < 0:
        raise ValueError("review_record_marker_missing")
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", body[marker_index + len(MARKER) :], re.DOTALL)
    if fenced is None:
        raise ValueError("review_record_json_missing")
    try:
        value = json.loads(fenced.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("review_record_json_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("review_record_root_invalid")
    return value


def validate_pr_body(body: str, *, expected_head: str | None = None) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    if PLACEHOLDER.search(body):
        violations.append(
            _violation("review_record_placeholder_present", "$", "replace all template placeholders")
        )
    if PRIVATE_LOCAL_PATH.search(body):
        violations.append(
            _violation("review_record_private_path_present", "$", "remove private local filesystem paths")
        )
    if any(pattern.search(body) for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS):
        violations.append(
            _violation("review_record_secret_shape_present", "$", "remove high-confidence credential material")
        )
    try:
        record_value = extract_review_record(body)
    except ValueError as exc:
        violations.append(_violation(str(exc), "$", "missing or invalid review record"))
        return tuple(violations)
    record = _exact_fields(
        record_value,
        fields=ROOT_FIELDS,
        path="$",
        violations=violations,
    )
    if record is None:
        return tuple(violations)
    if record.get("schema_version") != SCHEMA_VERSION:
        violations.append(
            _violation("review_record_schema_invalid", "schema_version", "unsupported schema version")
        )
    subject = _exact_fields(
        record.get("review_subject"),
        fields=REVIEW_SUBJECT_FIELDS,
        path="review_subject",
        violations=violations,
    )
    if subject is not None:
        head_sha = subject.get("head_sha")
        if not isinstance(head_sha, str) or FULL_SHA.fullmatch(head_sha) is None:
            violations.append(
                _violation("review_record_sha_invalid", "review_subject.head_sha", "must be 40 lowercase hex")
            )
        elif expected_head is not None and head_sha != expected_head:
            violations.append(
                _violation("review_record_sha_mismatch", "review_subject.head_sha", "must equal the PR head SHA")
            )
        _bounded_text(
            subject.get("scope"),
            path="review_subject.scope",
            maximum=500,
            violations=violations,
        )
    findings = record.get("findings")
    if not isinstance(findings, list):
        violations.append(_violation("review_record_findings_invalid", "findings", "must be an array"))
        findings = []
    no_findings = record.get("no_material_findings")
    if findings:
        if no_findings is not None:
            violations.append(
                _violation("review_record_no_findings_conflict", "no_material_findings", "must be null when findings exist")
            )
    else:
        _validate_actor(
            no_findings,
            fields=NO_FINDINGS_FIELDS,
            path="no_material_findings",
            expected_head=expected_head,
            violations=violations,
        )
    seen_ids: set[str] = set()
    for index, finding in enumerate(findings):
        finding_id = _validate_finding(
            finding,
            index=index,
            expected_head=expected_head,
            violations=violations,
        )
        if finding_id is not None:
            if finding_id in seen_ids:
                violations.append(
                    _violation("review_record_finding_id_duplicate", f"findings[{index}].id", "finding IDs must be unique")
                )
            seen_ids.add(finding_id)
    return tuple(violations)


def _load_event(path: Path) -> tuple[str, str]:
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
        pull_request = event["pull_request"]
        body = pull_request.get("body") or ""
        head_sha = pull_request["head"]["sha"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("review_record_event_invalid") from exc
    if not isinstance(body, str) or not isinstance(head_sha, str):
        raise ValueError("review_record_event_invalid")
    return body, head_sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-event", type=Path, required=True)
    parser.add_argument("--expected-head")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        body, event_head = _load_event(args.github_event)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    expected_head = args.expected_head or event_head
    if expected_head != event_head or FULL_SHA.fullmatch(expected_head) is None:
        print("review_record_event_head_mismatch", file=sys.stderr)
        return 3
    violations = validate_pr_body(body, expected_head=expected_head)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "pass" if not violations else "fail",
        "head_sha": expected_head,
        "violations": [item.as_dict() for item in violations],
    }
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif violations:
        for item in violations:
            print(f"{item.code}: {item.path}: {item.message}")
    else:
        print(f"review_record=valid head_sha={expected_head}")
    return 0 if not violations else 2


if __name__ == "__main__":
    raise SystemExit(main())
