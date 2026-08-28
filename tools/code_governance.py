"""Evaluate one Git change range against the ai-platform code-governance policy."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

REPORT_SCHEMA_VERSION = "ai-platform.code-governance-report.v2"
FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")

PRODUCTION_FILE_LIMIT = 12
PRODUCTION_NET_LOC_LIMIT = 800
HOT_FILE_LINES = 1500
HOT_FILE_NET_GROWTH_LIMIT = 100
FUNCTIONAL_HOT_FILE_LINES = 3000
FUNCTIONAL_HOT_FILE_NET_GROWTH_LIMIT = 0
TEST_HOT_FILE_LINES = 2500
TEST_HOT_FILE_NET_GROWTH_LIMIT = 100
TEST_ADDED_LOC_REVIEW_THRESHOLD = 300
TEST_TO_PRODUCTION_ADDED_LOC_REVIEW_RATIO = 2.0

FUNCTIONAL_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".css", ".go", ".html", ".java", ".js", ".jsx",
    ".mjs", ".py", ".rs", ".scss", ".sh", ".sql", ".ts", ".tsx",
})
DOCUMENTATION_SUFFIXES = frozenset({
    ".bmp", ".gif", ".jpeg", ".jpg", ".md", ".pdf", ".png", ".rst",
    ".svg", ".txt", ".webp",
})


class GovernanceError(RuntimeError):
    """Describe an input, repository, or evaluation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _GovernanceArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise GovernanceError("invalid_cli", message)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


class _CommandRunner:
    def run(self, command: Sequence[str], *, cwd: Path) -> _CommandResult:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        return _CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class _ChangedFile:
    status: str
    old_path: str | None
    new_path: str | None
    additions: int
    deletions: int
    binary: bool
    old_lines: int
    new_lines: int

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def net_loc(self) -> int:
        return self.additions - self.deletions

    @property
    def is_test(self) -> bool:
        return self.old_is_test or self.new_is_test

    @property
    def old_is_test(self) -> bool:
        return self.old_path is not None and _is_test_path(self.old_path)

    @property
    def new_is_test(self) -> bool:
        return self.new_path is not None and _is_test_path(self.new_path)

    @property
    def old_is_production(self) -> bool:
        return self.old_path is not None and _is_production_path(self.old_path)

    @property
    def new_is_production(self) -> bool:
        return self.new_path is not None and _is_production_path(self.new_path)

    @property
    def production_path(self) -> str:
        return self.new_path if self.new_is_production else self.old_path or ""

    @property
    def production_net_loc(self) -> int:
        if self.old_is_production == self.new_is_production:
            return self.net_loc
        return self.new_lines if self.new_is_production else -self.old_lines

    @property
    def production_added_lines(self) -> int:
        return self.additions if self.old_is_production else (self.new_lines if self.new_is_production else 0)

    @property
    def test_net_loc(self) -> int:
        if self.old_is_test == self.new_is_test:
            return self.net_loc if self.old_is_test else 0
        return self.new_lines if self.new_is_test else -self.old_lines

    @property
    def test_added_lines(self) -> int:
        return self.additions if self.old_is_test else (self.new_lines if self.new_is_test else 0)

    @property
    def is_functional(self) -> bool:
        return PurePosixPath(self.production_path).suffix.lower() in FUNCTIONAL_SUFFIXES

    @property
    def is_move_only(self) -> bool:
        return self.status.startswith("R") and self.additions == 0 and self.deletions == 0 and self.old_is_production and self.new_is_production

    @property
    def is_behavior_change(self) -> bool:
        return (self.old_is_production or self.new_is_production) and not self.is_move_only

    def as_dict(self) -> dict[str, Any]:
        return {
            "additions": self.additions,
            "binary": self.binary,
            "deletions": self.deletions,
            "new_lines": self.new_lines,
            "new_path": self.new_path,
            "old_lines": self.old_lines,
            "old_path": self.old_path,
            "path": self.path,
            "role": _change_role(self),
            "status": self.status,
        }


@dataclass(frozen=True)
class Violation:
    """One deterministic policy violation emitted by the evaluator."""

    code: str
    message: str
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "details": self.details,
            "message": self.message,
            "path": self.path,
        }


@dataclass(frozen=True)
class Evaluation:
    """Stable v2 result returned by code-governance evaluation."""

    base_ref: str
    head_ref: str
    status: str
    mode: str
    changes: tuple[_ChangedFile, ...]
    metrics: dict[str, Any]
    ruff: dict[str, Any]
    violations: tuple[Violation, ...]
    advisories: tuple[Violation, ...]

    @property
    def exit_code(self) -> int:
        return 0 if self.status == "pass" else 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "advisories": [item.as_dict() for item in self.advisories],
            "changes": [change.as_dict() for change in self.changes],
            "head_ref": self.head_ref,
            "metrics": self.metrics,
            "mode": self.mode,
            "policy": _policy_as_dict(),
            "reserved_gates": {
                "typed_payloads": "phase_2b_not_enforced",
                "error_taxonomy": "phase_2b_not_enforced",
            },
            "ruff": self.ruff,
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": self.status,
            "violations": [item.as_dict() for item in self.violations],
        }


@dataclass(frozen=True)
class _GitRange:
    base: str
    head: str
    changes: tuple[_ChangedFile, ...]


class _GitChangeReader:
    """Translate exact Git commits into normalized changed-file records."""

    def __init__(self, repo_root: Path, runner: _CommandRunner) -> None:
        self.repo_root = repo_root
        self.runner = runner

    def read(self, base_ref: str, head_ref: str) -> _GitRange:
        base = self._resolve_full_commit(base_ref, "base_ref")
        head = self._resolve_full_commit(head_ref, "head_ref")
        self._assert_ancestor(base, head)
        records = _parse_name_status(
            self._git(
                "-c",
                "core.quotepath=false",
                "diff",
                "--name-status",
                "-z",
                "--find-renames=50%",
                base,
                head,
                "--",
            ).stdout
        )
        stats = _parse_numstat(
            self._git(
                "-c",
                "core.quotepath=false",
                "diff",
                "--numstat",
                "-z",
                "--find-renames=50%",
                base,
                head,
                "--",
            ).stdout
        )
        changes = []
        for status, old_path, new_path in records:
            key = (old_path or new_path or "", new_path or old_path or "")
            additions, deletions, binary = stats.get(key, (0, 0, False))
            changes.append(
                _ChangedFile(
                    status=status,
                    old_path=old_path,
                    new_path=new_path,
                    additions=additions,
                    deletions=deletions,
                    binary=binary,
                    old_lines=self._blob_line_count(base, old_path) if old_path is not None else 0,
                    new_lines=self._blob_line_count(head, new_path) if new_path is not None else 0,
                )
            )
        return _GitRange(
            base=base,
            head=head,
            changes=tuple(sorted(changes, key=lambda item: (item.path, item.old_path or "", item.status))),
        )

    def _resolve_full_commit(self, value: str, label: str) -> str:
        if FULL_SHA.fullmatch(value) is None:
            raise GovernanceError("invalid_ref", f"{label} must be a full 40-hex commit id")
        resolved = self._git("rev-parse", "--verify", f"{value}^{{commit}}").stdout.strip().lower()
        if resolved != value.lower():
            raise GovernanceError("invalid_ref", f"{label} did not resolve to the exact supplied commit id")
        return resolved

    def _assert_ancestor(self, base: str, head: str) -> None:
        result = self.runner.run(["git", "merge-base", "--is-ancestor", base, head], cwd=self.repo_root)
        if result.returncode == 1:
            raise GovernanceError("non_ancestor_range", "base_ref must be an ancestor of head_ref")
        if result.returncode != 0:
            raise GovernanceError("git_failed", _command_failure("git merge-base", result))

    def _blob_line_count(self, commit: str, path: str) -> int:
        result = self.runner.run(["git", "show", f"{commit}:{path}"], cwd=self.repo_root)
        if result.returncode != 0:
            raise GovernanceError("git_failed", _command_failure(f"git show {commit}:{path}", result))
        return len(result.stdout.splitlines())

    def _git(self, *arguments: str) -> _CommandResult:
        result = self.runner.run(["git", *arguments], cwd=self.repo_root)
        if result.returncode != 0:
            raise GovernanceError("git_failed", _command_failure("git " + " ".join(arguments), result))
        return result


class CodeGovernanceEvaluator:
    """Hide Git, classification, size, and Ruff mechanics behind evaluate()."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: _CommandRunner | None = None,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._runner = runner or _CommandRunner()
        self._git_reader = _GitChangeReader(self._repo_root, self._runner)

    def evaluate(self, base_ref: str, head_ref: str) -> Evaluation:
        """Evaluate one exact commit range and return a deterministic result."""
        self._assert_repository()
        git_range = self._git_reader.read(base_ref, head_ref)
        changes = git_range.changes
        violations, metrics, advisories = self._evaluate_policy(changes)
        ruff, ruff_violation = self._evaluate_ruff(changes)
        if ruff_violation is not None:
            violations.append(ruff_violation)

        ordered = _sort_violations(violations)
        ordered_advisories = _sort_violations(advisories)
        mode = _evaluation_mode(changes)
        return Evaluation(
            base_ref=git_range.base,
            head_ref=git_range.head,
            status="pass" if not ordered else "violation",
            mode=mode,
            changes=changes,
            metrics=metrics,
            ruff=ruff,
            violations=tuple(ordered),
            advisories=tuple(ordered_advisories),
        )

    def _assert_repository(self) -> None:
        result = self._runner.run(["git", "rev-parse", "--show-toplevel"], cwd=self._repo_root)
        if result.returncode != 0:
            raise GovernanceError("not_git_repository", "current directory is not inside a Git repository")
        discovered = Path(result.stdout.strip()).resolve()
        if discovered != self._repo_root:
            self._repo_root = discovered
            self._git_reader = _GitChangeReader(self._repo_root, self._runner)

    def _evaluate_policy(
        self,
        changes: Sequence[_ChangedFile],
    ) -> tuple[list[Violation], dict[str, Any], list[Violation]]:
        behavior_files = [item for item in changes if item.is_behavior_change]
        move_only_files = [item for item in changes if item.is_move_only]
        test_files = [item for item in changes if item.is_test]
        subsystems = sorted({_production_subsystem(item.production_path) for item in behavior_files})
        net_loc = sum(item.production_net_loc for item in behavior_files)
        production_added_loc = sum(item.production_added_lines for item in behavior_files)
        test_added_loc = sum(item.test_added_lines for item in changes)
        test_to_production_ratio = (
            round(test_added_loc / production_added_loc, 4) if production_added_loc > 0 else None
        )
        violations: list[Violation] = []
        advisories: list[Violation] = []

        if len(behavior_files) > PRODUCTION_FILE_LIMIT:
            advisories.append(
                Violation(
                    "production_file_count",
                    f"review the responsibility split across more than {PRODUCTION_FILE_LIMIT} behavior-changing production files",
                    details={"actual": len(behavior_files), "limit": PRODUCTION_FILE_LIMIT},
                )
            )
        if net_loc >= PRODUCTION_NET_LOC_LIMIT:
            advisories.append(
                Violation(
                    "production_net_loc",
                    f"review the responsibility split for {PRODUCTION_NET_LOC_LIMIT} or more net behavior-changing production LOC",
                    details={"actual": net_loc, "limit_exclusive": PRODUCTION_NET_LOC_LIMIT},
                )
            )
        for item in changes:
            peak_lines = max(item.old_lines, item.new_lines)
            if (
                item.is_behavior_change
                and item.is_functional
                and peak_lines > FUNCTIONAL_HOT_FILE_LINES
                and item.production_net_loc > FUNCTIONAL_HOT_FILE_NET_GROWTH_LIMIT
            ):
                advisories.append(
                    Violation(
                        "functional_hot_file_growth",
                        f"review responsibilities added to a functional production file over {FUNCTIONAL_HOT_FILE_LINES} lines",
                        path=item.production_path,
                        details={"net_loc": item.production_net_loc, "peak_lines": peak_lines},
                    )
                )
            elif (
                item.is_behavior_change
                and peak_lines > HOT_FILE_LINES
                and item.production_net_loc > HOT_FILE_NET_GROWTH_LIMIT
            ):
                advisories.append(
                    Violation(
                        "hot_file_growth",
                        f"review responsibilities added to a production file over {HOT_FILE_LINES} lines",
                        path=item.production_path,
                        details={"net_loc": item.production_net_loc, "peak_lines": peak_lines},
                    )
                )
            if item.is_test and peak_lines > TEST_HOT_FILE_LINES and item.net_loc > TEST_HOT_FILE_NET_GROWTH_LIMIT:
                advisories.append(
                    Violation(
                        "test_hot_file_growth",
                        f"review responsibilities added to a test file over {TEST_HOT_FILE_LINES} lines",
                        path=item.path,
                        details={"net_loc": item.net_loc, "peak_lines": peak_lines},
                    )
                )

        test_loc_review_recommended = (production_added_loc == 0 and test_added_loc > 0) or test_added_loc > TEST_ADDED_LOC_REVIEW_THRESHOLD or (
            test_to_production_ratio is not None
            and test_to_production_ratio > TEST_TO_PRODUCTION_ADDED_LOC_REVIEW_RATIO
        )
        if test_loc_review_recommended:
            advisories.append(
                Violation(
                    "test_loc_review",
                    "review and explain the test-to-production change mix",
                    details={
                        "production_added_loc": production_added_loc,
                        "test_added_loc": test_added_loc,
                        "test_to_production_added_loc_ratio": test_to_production_ratio,
                    },
                )
            )

        responsibilities: dict[str, set[str]] = {}
        for item in behavior_files:
            responsibility = _production_subsystem(item.production_path)
            responsibilities.setdefault(responsibility, set()).add(item.production_path)
        changed_responsibilities = [
            {"name": name, "paths": sorted(paths)}
            for name, paths in sorted(responsibilities.items())
        ]

        hot_files: list[dict[str, Any]] = []
        for item in changes:
            peak_lines = max(item.old_lines, item.new_lines)
            if item.is_behavior_change and item.is_functional and peak_lines > FUNCTIONAL_HOT_FILE_LINES:
                kind = "functional_production"
                path = item.production_path
                net_change = item.production_net_loc
            elif item.is_behavior_change and peak_lines > HOT_FILE_LINES:
                kind = "production"
                path = item.production_path
                net_change = item.production_net_loc
            elif item.is_test and peak_lines > TEST_HOT_FILE_LINES:
                kind = "test"
                path = item.path
                net_change = item.net_loc
            else:
                continue
            hot_files.append(
                {
                    "kind": kind,
                    "net_loc": net_change,
                    "path": path,
                    "peak_lines": peak_lines,
                }
            )

        metrics = {
            "behavior_production_files": len(behavior_files),
            "changed_files": len(changes),
            "changed_responsibilities": changed_responsibilities,
            "changed_test_files": len(test_files),
            "hot_files": sorted(hot_files, key=lambda item: item["path"]),
            "move_only_production_files": len(move_only_files),
            "production_added_loc": production_added_loc,
            "production_net_loc": net_loc,
            "production_subsystem_count": len(subsystems),
            "production_subsystems": subsystems,
            "test_added_loc": test_added_loc,
            "test_net_loc": sum(item.test_net_loc for item in changes),
            "test_to_production_added_loc_ratio": test_to_production_ratio,
            "review_recommendations": ["explain_test_loc_mix"] if test_loc_review_recommended else [],
            "test_loc_review_explanation_recommended": test_loc_review_recommended,
        }
        return violations, metrics, advisories

    def _evaluate_ruff(self, changes: Sequence[_ChangedFile]) -> tuple[dict[str, Any], Violation | None]:
        paths = sorted(
            {
                item.new_path
                for item in changes
                if item.new_path is not None and PurePosixPath(item.new_path).suffix.lower() == ".py"
            }
        )
        display_command = ["python", "-m", "ruff", "check", "--isolated", "--", *paths]
        if not paths:
            return {"command": display_command, "paths": [], "returncode": None, "status": "not_applicable"}, None
        try:
            available = importlib.util.find_spec("ruff") is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            return (
                {"command": display_command, "paths": paths, "returncode": None, "status": "unavailable"},
                Violation(
                    "ruff_unavailable",
                    "Ruff is required for changed Python files and was not importable by the active Python interpreter",
                    details={"command": display_command},
                ),
            )
        actual_command = [sys.executable, "-m", "ruff", "check", "--isolated", "--", *paths]
        result = self._runner.run(actual_command, cwd=self._repo_root)
        summary = {
            "command": display_command,
            "paths": paths,
            "returncode": result.returncode,
            "status": "pass" if result.returncode == 0 else "failed",
            "stderr": result.stderr.strip(),
            "stdout": result.stdout.strip(),
        }
        if result.returncode == 0:
            return summary, None
        return (
            summary,
            Violation(
                "ruff_failed",
                "Ruff failed for changed Python files",
                details={"command": display_command, "returncode": result.returncode},
            ),
        )


def _parse_name_status(output: str) -> list[tuple[str, str | None, str | None]]:
    tokens = iter(filter(None, output.split("\0")))
    records: list[tuple[str, str | None, str | None]] = []
    try:
        for token in tokens:
            if "\t" in token:
                status, first_path = token.split("\t", 1)
            else:
                status, first_path = token, next(tokens)
            kind = status[0]
            if kind in {"R", "C"}:
                records.append((status, first_path, next(tokens)))
            elif kind == "A":
                records.append((status, None, first_path))
            elif kind == "D":
                records.append((status, first_path, None))
            else:
                records.append((status, first_path, first_path))
    except StopIteration as exc:
        raise GovernanceError("git_output_invalid", "malformed git --name-status output") from exc
    return records


def _parse_numstat(output: str) -> dict[tuple[str, str], tuple[int, int, bool]]:
    tokens = iter(filter(None, output.split("\0")))
    records: dict[tuple[str, str], tuple[int, int, bool]] = {}
    try:
        for record in tokens:
            fields = record.split("\t")
            if len(fields) != 3:
                raise GovernanceError("git_output_invalid", "malformed git --numstat output")
            additions_raw, deletions_raw, path = fields
            binary = additions_raw == "-" or deletions_raw == "-"
            additions = 0 if binary else int(additions_raw)
            deletions = 0 if binary else int(deletions_raw)
            if path:
                records[(path, path)] = (additions, deletions, binary)
            else:
                records[(next(tokens), next(tokens))] = (additions, deletions, binary)
    except (StopIteration, ValueError) as exc:
        raise GovernanceError("git_output_invalid", "malformed git --numstat output") from exc
    return records


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = {part.lower() for part in pure.parts[:-1]}
    name = pure.name.lower()
    return (
        "tests" in parts
        or "test" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def _is_production_path(path: str) -> bool:
    pure = PurePosixPath(path)
    if _is_test_path(path):
        return False
    if pure.parts and pure.parts[0].lower() in {"assets", "docs"}:
        return False
    return pure.suffix.lower() not in DOCUMENTATION_SUFFIXES


def _change_role(change: _ChangedFile) -> str:
    if change.is_move_only:
        return "move_only_production"
    if change.is_behavior_change:
        return "behavior_production"
    if change.is_test:
        return "test"
    return "non_production"


def _production_subsystem(path: str) -> str:
    pure = PurePosixPath(path)
    parts = list(pure.parts)
    if not parts:
        return "root"
    if parts[0] == "app":
        if len(parts) >= 4 and parts[1:3] == ["runtime", "sandbox"]:
            return "app/runtime/sandbox"
        if len(parts) >= 3:
            return "/".join(parts[:2])
        return "app"
    if parts[0] == "frontend" and len(parts) >= 5 and parts[2] == "src":
        return "/".join(parts[:4])
    if parts[0] in {"deploy", "skills"} and len(parts) >= 2:
        return "/".join(parts[:2])
    if len(parts) >= 2:
        return parts[0]
    return f"root/{pure.stem or pure.name}"


def _evaluation_mode(changes: Sequence[_ChangedFile]) -> str:
    behavior = any(item.is_behavior_change for item in changes)
    move_only = any(item.is_move_only for item in changes)
    if behavior and move_only:
        return "mixed"
    if behavior:
        return "behavior_fix"
    if move_only:
        return "move_only"
    return "non_production_only"


def _sort_violations(violations: Iterable[Violation]) -> list[Violation]:
    return sorted(violations, key=lambda item: (item.code, item.path or "", item.message))


def _policy_as_dict() -> dict[str, Any]:
    return {
        "advisory_codes": [
            "functional_hot_file_growth",
            "hot_file_growth",
            "production_file_count",
            "production_net_loc",
            "test_hot_file_growth",
            "test_loc_review",
        ],
        "functional_hot_file_lines_exclusive": FUNCTIONAL_HOT_FILE_LINES,
        "functional_hot_file_net_growth_max": FUNCTIONAL_HOT_FILE_NET_GROWTH_LIMIT,
        "hot_file_lines_exclusive": HOT_FILE_LINES,
        "hot_file_net_growth_max": HOT_FILE_NET_GROWTH_LIMIT,
        "production_file_count_max": PRODUCTION_FILE_LIMIT,
        "production_net_loc_max_exclusive": PRODUCTION_NET_LOC_LIMIT,
        "size_and_hot_file_gates": "advisory",
        "test_hot_file_lines_exclusive": TEST_HOT_FILE_LINES,
        "test_hot_file_net_growth_max": TEST_HOT_FILE_NET_GROWTH_LIMIT,
        "test_loc_review": {
            "enforcement": "soft",
            "ratio_exclusive": TEST_TO_PRODUCTION_ADDED_LOC_REVIEW_RATIO,
            "test_added_loc_exclusive": TEST_ADDED_LOC_REVIEW_THRESHOLD,
        },
    }


def _command_failure(label: str, result: _CommandResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    return f"{label} failed: {detail}"


def _render_text(evaluation: Evaluation) -> str:
    lines = [
        f"code-governance: {evaluation.status.upper()}",
        f"schema: {REPORT_SCHEMA_VERSION}",
        f"base: {evaluation.base_ref}",
        f"head: {evaluation.head_ref}",
        f"mode: {evaluation.mode}",
        f"behavior production files: {evaluation.metrics['behavior_production_files']}",
        f"move-only production files: {evaluation.metrics['move_only_production_files']}",
        f"production added LOC: {evaluation.metrics['production_added_loc']}",
        f"production net LOC: {evaluation.metrics['production_net_loc']}",
        f"test added LOC: {evaluation.metrics['test_added_loc']}",
        f"production subsystem count: {evaluation.metrics['production_subsystem_count']}",
        f"production subsystems: {', '.join(evaluation.metrics['production_subsystems']) or 'none'}",
        f"Ruff: {evaluation.ruff['status']}",
    ]
    lines.append("changed responsibilities:")
    if evaluation.metrics["changed_responsibilities"]:
        for responsibility in evaluation.metrics["changed_responsibilities"]:
            lines.append(
                f"- {responsibility['name']}: {', '.join(responsibility['paths'])}"
            )
    else:
        lines.append("- none")
    lines.append("hot files:")
    if evaluation.metrics["hot_files"]:
        for hot_file in evaluation.metrics["hot_files"]:
            lines.append(
                f"- {hot_file['path']} ({hot_file['kind']}, "
                f"{hot_file['peak_lines']} lines, net {hot_file['net_loc']:+d})"
            )
    else:
        lines.append("- none")
    lines.append("advisories:" if evaluation.advisories else "advisories: none")
    lines.extend(_violation_text(item) for item in evaluation.advisories)
    lines.append("violations:" if evaluation.violations else "violations: none")
    lines.extend(_violation_text(item) for item in evaluation.violations)
    return "\n".join(lines)


def _violation_text(item: Violation) -> str:
    location = f" [{item.path}]" if item.path else ""
    return f"- {item.code}{location}: {item.message}"


def _error_payload(error: GovernanceError) -> dict[str, Any]:
    return {
        "error": {"code": error.code, "message": str(error)},
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "error",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = _GovernanceArgumentParser(description="Evaluate ai-platform code-governance gates.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="check one exact commit range")
    check.add_argument("--base-ref", required=True)
    check.add_argument("--head-ref", required=True)
    check.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return 0, 2, or 3."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    requested_format = "json" if _requested_json(arguments) else "text"
    try:
        args = _build_parser().parse_args(arguments)
        evaluation = CodeGovernanceEvaluator(Path.cwd()).evaluate(args.base_ref, args.head_ref)
    except GovernanceError as error:
        if requested_format == "json":
            print(json.dumps(_error_payload(error), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"code-governance: ERROR\n{error.code}: {error}")
        return 3
    if args.format == "json":
        print(json.dumps(evaluation.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_text(evaluation))
    return evaluation.exit_code


def _requested_json(arguments: Sequence[str]) -> bool:
    try:
        index = arguments.index("--format")
    except ValueError:
        return False
    return index + 1 < len(arguments) and arguments[index + 1] == "json"


if __name__ == "__main__":
    raise SystemExit(main())
