from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET


class JUnitContractError(RuntimeError):
    """Raised when a required-test JUnit report is absent or not zero-skip."""


def _non_negative_int(value: str | None, *, field: str, suite_index: int) -> int:
    if value is None:
        raise JUnitContractError(f"testsuite[{suite_index}] missing {field!r} count")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise JUnitContractError(
            f"testsuite[{suite_index}] has invalid {field!r} count {value!r}"
        ) from exc
    if parsed < 0:
        raise JUnitContractError(
            f"testsuite[{suite_index}] has negative {field!r} count {parsed}"
        )
    return parsed


def require_zero_junit_skips(report_path: str | Path) -> dict[str, int | str]:
    """Validate that a required-test JUnit report exists, ran tests, and skipped none."""
    path = Path(report_path)
    if not path.is_file():
        raise JUnitContractError(f"JUnit report does not exist: {path}")
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ET.ParseError) as exc:
        raise JUnitContractError(f"JUnit report is unreadable or malformed: {path}") from exc

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise JUnitContractError("JUnit report contains no testsuite")

    test_count = 0
    declared_skip_count = 0
    for index, suite in enumerate(suites):
        test_count += _non_negative_int(suite.attrib.get("tests"), field="tests", suite_index=index)
        declared_skip_count += _non_negative_int(
            suite.attrib.get("skipped", "0"),
            field="skipped",
            suite_index=index,
        )
    if test_count == 0:
        raise JUnitContractError("required test report contains zero tests")

    skipped_elements = sum(1 for _ in root.iter("skipped"))
    if declared_skip_count or skipped_elements:
        raise JUnitContractError(
            "required test report contains skipped tests: "
            f"declared={declared_skip_count}, elements={skipped_elements}"
        )

    return {
        "status": "pass",
        "tests": test_count,
        "skipped": 0,
        "testsuites": len(suites),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail unless a required-test JUnit report ran at least one test and skipped none."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = require_zero_junit_skips(args.report)
    except JUnitContractError as exc:
        if args.format == "json":
            print(json.dumps({"status": "fail", "error": str(exc)}, sort_keys=True))
        else:
            print(f"required-test-junit: FAIL: {exc}")
        return 2
    if args.format == "json":
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "required-test-junit: PASS: "
            f"tests={result['tests']} skipped=0 testsuites={result['testsuites']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
