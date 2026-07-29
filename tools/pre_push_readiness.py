"""Run bounded local CI-readiness checks for one exact Git range."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

REPORT_SCHEMA_VERSION = "ai-platform.pre-push-readiness.v1"
FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")
MAX_RESPONSIBILITY_TESTS = 24

FAILURE_TAXONOMY = {
    "stale_base": "The supplied base is not an ancestor of head; merge the current base before push.",
    "product_test_failure": "A deterministic local compile or responsibility-test check failed.",
    "governance_violation": "The exact range violated diff, Ruff, or code-governance policy.",
    "infrastructure_failure": "A required local command or temporary worktree could not run.",
    "external_check": "A remote provider check needs fresh external evidence; do not rerun without positive infrastructure evidence.",
}

# This is the current bounded backend responsibility suite. Changed and mirrored
# tests are added below so a newly changed responsibility is not skipped.
BASELINE_RESPONSIBILITY_TESTS = (
    "tests/test_sandbox_container_provider.py",
    "tests/test_sandbox_runtime.py",
    "tests/test_sandbox_runtime_cleanup.py",
    "tests/test_sandbox_runtime_211_script.py",
    "tests/test_b2_sandbox_readiness.py",
    "tests/test_repositories.py",
    "tests/test_backend_ci_workflow.py",
    "tests/test_governance_readiness.py",
    "tests/test_release_authority.py",
    "tests/test_contract.py",
    "tests/test_worker_main.py",
)


class ReadinessError(RuntimeError):
    """Describe one stable, user-actionable readiness failure."""

    def __init__(self, category: str, code: str, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.path = path


class _ReadinessArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ReadinessError("governance_violation", "invalid_cli", message)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


class _CommandRunner:
    def run(self, command: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None) -> _CommandResult:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return _CommandResult(completed.returncode, completed.stdout, completed.stderr)


class PrePushReadiness:
    """Keep exact-ref validation, local checks, and failure taxonomy in one seam."""

    def __init__(self, repo_root: Path, *, runner: _CommandRunner | None = None) -> None:
        self._repo_root = repo_root.resolve()
        self._runner = runner or _CommandRunner()

    def check(self, base_ref: str, head_ref: str) -> dict[str, Any]:
        result = _new_result(base_ref, head_ref)
        self._assert_repository()
        base = self._resolve_full_commit(base_ref, "base_ref")
        head = self._resolve_full_commit(head_ref, "head_ref")
        result["base_ref"] = base
        result["head_ref"] = head
        self._assert_ancestor(base, head)

        temporary_root = Path(tempfile.mkdtemp(prefix="ai-platform-pre-push-readiness-"))
        base_worktree = temporary_root / "base"
        head_worktree = temporary_root / "head"
        try:
            self._add_worktree(base_worktree, base)
            self._add_worktree(head_worktree, head)
            self._run_diff_check(result, base, head)
            self._run_compileall(result, head_worktree)
            selected_tests = self._select_responsibility_tests(base, head, head_worktree)
            self._run_responsibility_tests(result, head_worktree, selected_tests)
            self._run_governance(result, base, head, base_worktree, head_worktree)
        finally:
            self._remove_worktree(head_worktree)
            self._remove_worktree(base_worktree)
            shutil.rmtree(temporary_root, ignore_errors=True)

        result["status"] = "pass"
        return result

    def _assert_repository(self) -> None:
        discovered = self._run(("git", "rev-parse", "--show-toplevel"), self._repo_root)
        if discovered.returncode != 0:
            raise ReadinessError("infrastructure_failure", "not_git_repository", _command_failure("git rev-parse", discovered))
        self._repo_root = Path(discovered.stdout.strip()).resolve()

    def _resolve_full_commit(self, value: str, label: str) -> str:
        if FULL_SHA.fullmatch(value) is None:
            raise ReadinessError(
                "governance_violation",
                "invalid_ref",
                f"{label} must be a full 40-hex commit id",
            )
        resolved = self._run(("git", "rev-parse", "--verify", f"{value}^{{commit}}"), self._repo_root)
        actual = resolved.stdout.strip().lower()
        if resolved.returncode != 0 or actual != value.lower():
            raise ReadinessError(
                "governance_violation",
                "invalid_ref",
                f"{label} did not resolve to the exact supplied commit id",
            )
        return actual

    def _assert_ancestor(self, base: str, head: str) -> None:
        ancestor = self._run(("git", "merge-base", "--is-ancestor", base, head), self._repo_root)
        if ancestor.returncode == 1:
            raise ReadinessError(
                "stale_base",
                "non_ancestor_range",
                "base_ref must be an ancestor of head_ref; merge the current base before push",
            )
        if ancestor.returncode != 0:
            raise ReadinessError("infrastructure_failure", "git_failed", _command_failure("git merge-base", ancestor))

    def _add_worktree(self, path: Path, commit: str) -> None:
        created = self._run(("git", "worktree", "add", "--detach", str(path), commit), self._repo_root)
        if created.returncode != 0:
            raise ReadinessError("infrastructure_failure", "worktree_add_failed", _command_failure("git worktree add", created))

    def _remove_worktree(self, path: Path) -> None:
        if not path.exists():
            return
        self._run(("git", "worktree", "remove", "--force", str(path)), self._repo_root)

    def _run_diff_check(self, result: dict[str, Any], base: str, head: str) -> None:
        command = ("git", "diff", "--check", base, head, "--")
        checked = self._run(command, self._repo_root)
        if checked.returncode != 0:
            result["stages"].append(_stage("diff_check", command, "failed", checked))
            raise ReadinessError(
                "governance_violation",
                "diff_check_failed",
                _command_failure("git diff --check", checked),
            )
        result["stages"].append(_stage("diff_check", command, "pass", checked))

    def _run_compileall(self, result: dict[str, Any], head_worktree: Path) -> None:
        command = (sys.executable, "-m", "compileall", "-q", "app", "tools", "scripts")
        compiled = self._run(command, head_worktree, env=_safe_environment())
        if compiled.returncode != 0:
            result["stages"].append(_stage("compileall", command, "failed", compiled))
            raise ReadinessError(
                "product_test_failure",
                "compileall_failed",
                _command_failure("python -m compileall", compiled),
            )
        result["stages"].append(_stage("compileall", command, "pass", compiled))

    def _select_responsibility_tests(self, base: str, head: str, head_worktree: Path) -> tuple[str, ...]:
        changed = self._run(("git", "diff", "--name-only", base, head, "--"), self._repo_root)
        if changed.returncode != 0:
            raise ReadinessError("infrastructure_failure", "git_failed", _command_failure("git diff --name-only", changed))
        changed_paths = tuple(path for path in changed.stdout.splitlines() if path)
        selected = {path for path in BASELINE_RESPONSIBILITY_TESTS if (head_worktree / path).is_file()}
        for path in changed_paths:
            pure_path = PurePosixPath(path)
            if _is_test_module(pure_path):
                selected.add(path)
            mirrored = _mirrored_test_path(pure_path)
            if mirrored is not None and (head_worktree / mirrored).is_file():
                selected.add(mirrored)
        tests = tuple(sorted(selected))
        if len(tests) > MAX_RESPONSIBILITY_TESTS:
            raise ReadinessError(
                "governance_violation",
                "responsibility_test_limit",
                f"bounded responsibility suite selected {len(tests)} tests, limit is {MAX_RESPONSIBILITY_TESTS}",
            )
        return tests

    def _run_responsibility_tests(
        self,
        result: dict[str, Any],
        head_worktree: Path,
        tests: tuple[str, ...],
    ) -> None:
        command = (sys.executable, "-m", "pytest", *tests, "-q", "--basetemp", ".pytest-tmp")
        if not tests:
            result["stages"].append({"command": list(command), "name": "responsibility_tests", "status": "not_applicable", "tests": []})
            return
        tested = self._run(command, head_worktree, env=_safe_environment())
        if tested.returncode != 0:
            result["stages"].append(_stage("responsibility_tests", command, "failed", tested, tests=tests))
            raise ReadinessError(
                "product_test_failure",
                "pytest_failed",
                _command_failure("python -m pytest", tested),
                path=_failed_test_identity(tested),
            )
        result["stages"].append(_stage("responsibility_tests", command, "pass", tested, tests=tests))

    def _run_governance(
        self,
        result: dict[str, Any],
        base: str,
        head: str,
        base_worktree: Path,
        head_worktree: Path,
    ) -> None:
        command = (
            sys.executable,
            "-P",
            str(base_worktree / "tools" / "code_governance.py"),
            "check",
            "--base-ref",
            base,
            "--head-ref",
            head,
            "--format",
            "json",
        )
        governed = self._run(command, head_worktree, env=_safe_environment())
        payload = _json_payload(governed)
        ruff = payload.get("ruff") if isinstance(payload, dict) else None
        if governed.returncode != 0:
            result["stages"].append(_stage("governance", command, "failed", governed, ruff=ruff))
            violation = _first_governance_failure(payload)
            raise ReadinessError(
                "governance_violation",
                violation["code"],
                violation["message"],
                path=violation["path"],
            )
        result["stages"].append(_stage("governance", command, "pass", governed, ruff=ruff))

    def _run(self, command: Sequence[str], cwd: Path, *, env: dict[str, str] | None = None) -> _CommandResult:
        try:
            return self._runner.run(command, cwd=cwd, env=env)
        except OSError as error:
            raise ReadinessError("infrastructure_failure", "command_unavailable", f"{' '.join(command)}: {error}") from error


def _is_test_module(path: PurePosixPath) -> bool:
    return path.suffix == ".py" and path.parts and path.parts[0] == "tests" and path.name.startswith("test_")


def _mirrored_test_path(path: PurePosixPath) -> str | None:
    if path.suffix != ".py" or not path.parts or path.parts[0] not in {"app", "tools"}:
        return None
    return f"tests/test_{path.stem}.py"


def _safe_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONSAFEPATH"] = "1"
    return environment


def _stage(
    name: str,
    command: Sequence[str],
    status: str,
    completed: _CommandResult,
    *,
    tests: tuple[str, ...] | None = None,
    ruff: Any = None,
) -> dict[str, Any]:
    stage: dict[str, Any] = {
        "command": list(command),
        "name": name,
        "status": status,
    }
    if tests is not None:
        stage["tests"] = list(tests)
    if ruff is not None:
        stage["ruff"] = ruff
    if status == "failed":
        stage["output"] = _command_output(completed)
    return stage


def _command_output(completed: _CommandResult) -> str:
    return (completed.stderr.strip() or completed.stdout.strip())[:8_000]


def _command_failure(label: str, completed: _CommandResult) -> str:
    detail = _command_output(completed) or f"exit {completed.returncode}"
    return f"{label} failed: {detail}"


def _failed_test_identity(completed: _CommandResult) -> str | None:
    output = "\n".join((completed.stdout, completed.stderr))
    match = re.search(r"(?m)^FAILED ([^\s]+::[^\s]+)", output)
    return match.group(1) if match else None


def _json_payload(completed: _CommandResult) -> dict[str, Any]:
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_governance_failure(payload: dict[str, Any]) -> dict[str, str | None]:
    violations = payload.get("violations")
    if isinstance(violations, list) and violations and isinstance(violations[0], dict):
        violation = violations[0]
        return {
            "code": str(violation.get("code", "code_governance_failed")),
            "message": str(violation.get("message", "code governance failed")),
            "path": violation.get("path") if isinstance(violation.get("path"), str) else None,
        }
    error = payload.get("error")
    if isinstance(error, dict):
        return {
            "code": str(error.get("code", "code_governance_error")),
            "message": str(error.get("message", "code governance failed")),
            "path": None,
        }
    return {"code": "code_governance_failed", "message": "code governance failed", "path": None}


def _new_result(base_ref: str | None, head_ref: str | None) -> dict[str, Any]:
    return {
        "base_ref": base_ref,
        "category": None,
        "external_check": {
            "category": "external_check",
            "status": "not_run",
        },
        "failure": None,
        "head_ref": head_ref,
        "schema_version": REPORT_SCHEMA_VERSION,
        "stages": [],
        "status": "failed",
        "taxonomy": FAILURE_TAXONOMY,
    }


def _failure_result(error: ReadinessError, base_ref: str | None, head_ref: str | None) -> dict[str, Any]:
    result = _new_result(base_ref, head_ref)
    result["category"] = error.category
    result["failure"] = {
        "code": error.code,
        "message": str(error),
        "path": error.path,
        "test_identity": error.path if error.code == "pytest_failed" else None,
    }
    return result


def _render_text(result: dict[str, Any]) -> str:
    if result["status"] == "pass":
        lines = [
            "pre-push-readiness: PASS",
            f"base: {result['base_ref']}",
            f"head: {result['head_ref']}",
        ]
    else:
        failure = result["failure"] or {}
        lines = [
            "pre-push-readiness: FAIL",
            f"category: {result['category']}",
            f"code: {failure.get('code')}",
            f"message: {failure.get('message')}",
        ]
        if failure.get("path"):
            lines.append(f"path: {failure['path']}")
    lines.extend(f"stage {stage['name']}: {stage['status']}" for stage in result["stages"])
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ReadinessArgumentParser(description="Run ai-platform bounded pre-push readiness checks.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="check one exact base/head range")
    check.add_argument("--base-ref", required=True)
    check.add_argument("--head-ref", required=True)
    check.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the readiness command and return a stable process status."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    output_format = "json" if _requested_json(arguments) else "text"
    base_ref = _argument_value(arguments, "--base-ref")
    head_ref = _argument_value(arguments, "--head-ref")
    try:
        args = _build_parser().parse_args(arguments)
        result = PrePushReadiness(Path.cwd()).check(args.base_ref, args.head_ref)
    except ReadinessError as error:
        result = _failure_result(error, base_ref, head_ref)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if output_format == "json" else _render_text(result)
    print(rendered)
    return 0 if result["status"] == "pass" else 3 if result["category"] == "infrastructure_failure" else 2


def _requested_json(arguments: Sequence[str]) -> bool:
    return _argument_value(arguments, "--format") == "json"


def _argument_value(arguments: Sequence[str], option: str) -> str | None:
    try:
        index = arguments.index(option)
    except ValueError:
        return None
    return arguments[index + 1] if index + 1 < len(arguments) else None


if __name__ == "__main__":
    raise SystemExit(main())
