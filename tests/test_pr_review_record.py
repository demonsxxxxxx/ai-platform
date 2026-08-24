import json
from pathlib import Path

import pytest

from tools import validate_pr_review_record


HEAD_SHA = "a" * 40
PULL_REQUEST_TEMPLATE = Path(".github/PULL_REQUEST_TEMPLATE.md")


def _body(record: dict) -> str:
    return (
        "## Review finding disposition\n\n"
        f"{validate_pr_review_record.MARKER}\n"
        "```json\n"
        f"{json.dumps(record, ensure_ascii=False)}\n"
        "```\n"
    )


def _record(**overrides):
    record = {
        "schema_version": validate_pr_review_record.SCHEMA_VERSION,
        "review_subject": {
            "head_sha": HEAD_SHA,
            "scope": "Review the exact candidate diff for authorization regressions.",
        },
        "no_material_findings": {
            "reviewer_handle": "@reviewer-a",
            "reviewer_role": "security reviewer",
            "evidence_id": "review-run-123",
        },
        "findings": [],
    }
    record.update(overrides)
    return record


def _finding(**overrides):
    finding = {
        "id": "F-001",
        "severity": "P1",
        "summary": "The verified candidate accepted one stale owner transition.",
        "evidence_id": "review-run-123:F-001",
        "disposition": "fixed",
        "owner": None,
        "defer_exit_condition": None,
        "independent_confirmation": {
            "handle": "@reviewer-b",
            "role": "independent reviewer",
            "evidence_id": "review-run-124",
            "head_sha": HEAD_SHA,
        },
        "promotion": {"target": "code", "rule_evidence": None},
    }
    finding.update(overrides)
    return finding


def _codes(body: str) -> set[str]:
    return {
        item.code
        for item in validate_pr_review_record.validate_pr_body(
            body,
            expected_head=HEAD_SHA,
        )
    }


def test_review_record_accepts_exact_sha_no_findings_attestation():
    assert _codes(_body(_record())) == set()


def test_no_findings_actor_violations_use_schema_field_paths():
    record = _record()
    record["no_material_findings"]["reviewer_handle"] = "invalid"
    record["no_material_findings"]["reviewer_role"] = " "

    violations = validate_pr_review_record.validate_pr_body(
        _body(record),
        expected_head=HEAD_SHA,
    )

    assert {
        (item.code, item.path)
        for item in violations
    } >= {
        ("review_record_handle_invalid", "no_material_findings.reviewer_handle"),
        ("review_record_text_invalid", "no_material_findings.reviewer_role"),
    }


def test_review_record_accepts_fixed_finding_and_complete_rule_promotion():
    finding = _finding(
        promotion={
            "target": "rule",
            "rule_evidence": {
                "finding_class": "stale attempt owner write",
                "applicability_boundary": "RunAttempt state transitions",
                "detector": "owner-fenced CAS test",
                "bounded_paths": ["app/runs/", "tests/test_run_attempt_repository.py"],
                "alternatives": "Application-only validation was rejected.",
                "false_positive_evidence": "Exact stale and current generations are tested.",
                "removal_condition": "Replace when storage authority changes.",
            },
        }
    )
    record = _record(no_material_findings=None, findings=[finding])

    assert _codes(_body(record)) == set()


def test_rule_evidence_text_violations_have_deterministic_field_order():
    rule_evidence = {
        field_name: ["tests/test_pr_review_record.py"] if field_name == "bounded_paths" else ""
        for field_name in validate_pr_review_record.RULE_EVIDENCE_FIELDS
    }
    finding = _finding(
        promotion={"target": "rule", "rule_evidence": rule_evidence}
    )
    record = _record(no_material_findings=None, findings=[finding])

    violations = validate_pr_review_record.validate_pr_body(
        _body(record),
        expected_head=HEAD_SHA,
    )
    paths = [
        item.path
        for item in violations
        if item.code == "review_record_text_invalid"
        and ".promotion.rule_evidence." in item.path
    ]

    assert paths == sorted(paths)


def test_review_record_rejects_non_repository_rule_boundaries():
    for invalid_path in ("/etc/passwd", "../outside", "https://example.test/path"):
        finding = _finding(
            promotion={
                "target": "rule",
                "rule_evidence": {
                    "finding_class": "stale attempt owner write",
                    "applicability_boundary": "RunAttempt state transitions",
                    "detector": "owner-fenced CAS test",
                    "bounded_paths": [invalid_path],
                    "alternatives": "Application-only validation was rejected.",
                    "false_positive_evidence": "Exact generations are tested.",
                    "removal_condition": "Replace when storage authority changes.",
                },
            }
        )
        record = _record(no_material_findings=None, findings=[finding])

        assert "review_record_bounded_paths_invalid" in _codes(_body(record))


def test_review_record_accepts_deferred_finding_with_owner_and_confirmation():
    finding = _finding(
        disposition="deferred",
        owner={"handle": "@owner-a", "role": "runs owner"},
        defer_exit_condition="Close after real PostgreSQL race evidence passes.",
        independent_confirmation={
            "handle": "@reviewer-b",
            "role": "database reviewer",
            "evidence_id": "review-run-124",
            "head_sha": HEAD_SHA,
        },
        promotion={"target": "none", "rule_evidence": None},
    )
    record = _record(no_material_findings=None, findings=[finding])

    assert _codes(_body(record)) == set()


def test_review_record_rejects_nonfixed_finding_without_accountable_humans():
    finding = _finding(
        disposition="rejected_with_evidence",
        independent_confirmation=None,
        promotion={"target": "none", "rule_evidence": None},
    )
    record = _record(no_material_findings=None, findings=[finding])

    assert "review_record_type_invalid" in _codes(_body(record))


def test_review_record_rejects_owner_self_confirmation_variants():
    for owner_handle, confirmation_handle in (
        ("@owner-a", "@owner-a"),
        ("@Alice", "@alice"),
        ("@owner-a ", "@owner-a"),
    ):
        finding = _finding(
            disposition="rejected_with_evidence",
            owner={"handle": owner_handle, "role": "runs owner"},
            independent_confirmation={
                "handle": confirmation_handle,
                "role": "database reviewer",
                "evidence_id": "review-run-124",
                "head_sha": HEAD_SHA,
            },
            promotion={"target": "none", "rule_evidence": None},
        )
        record = _record(no_material_findings=None, findings=[finding])

        assert "review_record_confirmation_not_independent" in _codes(_body(record))


def test_review_record_rejects_sha_drift_and_duplicate_finding_ids():
    wrong_confirmation = {
        "handle": "@reviewer-b",
        "role": "domain reviewer",
        "evidence_id": "review-run-124",
        "head_sha": "b" * 40,
    }
    finding = _finding(
        disposition="rejected_with_evidence",
        owner={"handle": "@owner-a", "role": "runs owner"},
        independent_confirmation=wrong_confirmation,
        promotion={"target": "none", "rule_evidence": None},
    )
    record = _record(no_material_findings=None, findings=[finding, finding])

    codes = _codes(_body(record))

    assert "review_record_sha_mismatch" in codes
    assert "review_record_finding_id_duplicate" in codes


def test_review_record_rejects_placeholders_private_paths_and_secret_shapes():
    record = _record()
    record["review_subject"]["scope"] = (
        "REPLACE_ME inspect /Users/alice/project with token " + "sk-" + "x" * 24
    )

    codes = _codes(_body(record))

    assert "review_record_placeholder_present" in codes
    assert "review_record_private_path_present" in codes
    assert "review_record_secret_shape_present" in codes


@pytest.mark.parametrize(
    "private_path",
    [
        r"C:\Users\alice\project",
        r"C:\Users\alice",
        r"c:\users\alice",
        "C:/Users/alice/project",
        "c:/users/alice",
        "/Users/alice",
        "/home/alice",
    ],
)
def test_review_record_rejects_private_user_paths(private_path: str):
    record = _record()
    record["review_subject"]["scope"] = f"inspect {private_path}"

    assert "review_record_private_path_present" in _codes(_body(record))


def test_review_record_rejects_github_fine_grained_pat_shape():
    record = _record()
    record["review_subject"]["scope"] = (
        "token " + "github" + "_pat_" + "A" * 32 + "_" + "b" * 32
    )

    assert "review_record_secret_shape_present" in _codes(_body(record))


def test_review_record_rejects_unknown_fields_and_no_findings_conflict():
    unknown_field_record = _record()
    unknown_field_record["unexpected"] = True
    conflict_record = _record(findings=[_finding()])

    unknown_field_codes = _codes(_body(unknown_field_record))
    conflict_codes = _codes(_body(conflict_record))

    assert "review_record_fields_invalid" in unknown_field_codes
    assert "review_record_no_findings_conflict" in conflict_codes


def test_review_record_cli_reads_untrusted_body_from_event_file(tmp_path, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "body": _body(_record()),
                    "head": {"sha": HEAD_SHA},
                }
            }
        ),
        encoding="utf-8",
    )

    exit_code = validate_pr_review_record.main(
        [
            "--github-event",
            str(event_path),
            "--expected-head",
            HEAD_SHA,
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["head_sha"] == HEAD_SHA


def test_pull_request_template_has_placeholders_only_in_the_active_record():
    template = PULL_REQUEST_TEMPLATE.read_text(encoding="utf-8")
    record = validate_pr_review_record.extract_review_record(template)

    assert "REPLACE_ME" not in template.split("```", 3)[3]
    assert record["review_subject"]["head_sha"] == "REPLACE_ME_40_HEX_SHA"
    assert record["no_material_findings"]["reviewer_handle"] == "@REPLACE_ME"
    assert "requires all seven" in template
    assert len(validate_pr_review_record.RULE_EVIDENCE_FIELDS) == 7

    completed = (
        template.replace("REPLACE_ME_40_HEX_SHA", HEAD_SHA)
        .replace("REPLACE_ME_EXACT_REVIEW_SCOPE", "Exact candidate scope")
        .replace("@REPLACE_ME", "@reviewer-a")
        .replace('"REPLACE_ME"', '"review-run-123"')
    )
    assert _codes(completed) == set()
