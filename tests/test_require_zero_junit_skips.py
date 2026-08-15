from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.require_zero_junit_skips import (
    JUnitContractError,
    main,
    require_zero_junit_skips,
)


def _write_report(tmp_path: Path, body: str) -> Path:
    report = tmp_path / "report.xml"
    report.write_text(body, encoding="utf-8")
    return report


def test_required_junit_accepts_executed_zero_skip_suites(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path,
        '<testsuites><testsuite name="one" tests="2" skipped="0"/>'
        '<testsuite name="two" tests="3" skipped="0"/></testsuites>',
    )

    assert require_zero_junit_skips(report) == {
        "status": "pass",
        "tests": 5,
        "skipped": 0,
        "testsuites": 2,
    }


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('<testsuite tests="1" skipped="1"/>', "contains skipped tests"),
        (
            '<testsuite tests="1" skipped="0"><testcase><skipped/></testcase></testsuite>',
            "contains skipped tests",
        ),
        ('<testsuite tests="0" skipped="0"/>', "contains zero tests"),
        ('<testsuite skipped="0"/>', "missing 'tests' count"),
        ('<testsuite tests="invalid" skipped="0"/>', "invalid 'tests' count"),
        ('<testsuite tests="1" skipped="-1"/>', "negative 'skipped' count"),
        ("<not-junit/>", "contains no testsuite"),
        ("<testsuite", "unreadable or malformed"),
    ],
)
def test_required_junit_rejects_non_evidence_reports(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    report = _write_report(tmp_path, body)

    with pytest.raises(JUnitContractError, match=message):
        require_zero_junit_skips(report)


def test_required_junit_rejects_missing_report(tmp_path: Path) -> None:
    with pytest.raises(JUnitContractError, match="does not exist"):
        require_zero_junit_skips(tmp_path / "missing.xml")


def test_required_junit_cli_has_stable_pass_and_failure_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _write_report(tmp_path, '<testsuite tests="1" skipped="0"/>')

    assert main([str(report), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "skipped": 0,
        "status": "pass",
        "tests": 1,
        "testsuites": 1,
    }

    report.write_text('<testsuite tests="1" skipped="1"/>', encoding="utf-8")
    assert main([str(report), "--format", "json"]) == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "fail"
    assert "contains skipped tests" in failure["error"]
