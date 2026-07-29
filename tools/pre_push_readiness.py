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
AUTHORITY_TOOL_PATH = "tools/pre_push_readiness.py"
AUTHORITY_GOVERNANCE_PATH = "tools/code_governance.py"
CODE_GOVERNANCE_EXCEPTION_PATH = ".code-governance-exception.json"
CODE_GOVERNANCE_TEST_PATH = "tests/test_code_governance.py"

FAILURE_TAXONOMY = {
    "stale_base": "The supplied base is not an ancestor of head; merge the current base before push.",
    "product_test_failure": "A deterministic local compile or responsibility-test check failed.",
    "governance_violation": "The exact range violated diff, Ruff, or code-governance policy.",
    "infrastructure_failure": "A required local command or temporary worktree could not run.",
    "external_check": "A remote provider check needs fresh external evidence; do not rerun without positive infrastructure evidence.",
}

class ReadinessError(RuntimeError):
    """Describe one stable, user-actionable readiness failure."""

    def __init__(self, category: str, code: str, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.path = path
        self.result: dict[str, Any] | None = None
        self.cleanup_failure: ReadinessError | None = None
        self.authority_integrity_failure: ReadinessError | None = None


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


@dataclass(frozen=True)
class _ResponsibilityPlan:
    tests: tuple[str, ...]
    frontend: bool


class PrePushReadiness:
    """Keep exact-ref validation, local checks, and failure taxonomy in one seam."""

    def __init__(self, repo_root: Path, *, runner: _CommandRunner | None = None) -> None:
        self._repo_root = repo_root.resolve()
        self._runner = runner or _CommandRunner()
        self._authority_root: Path | None = None

    def check(
        self,
        authority_ref: str,
        base_ref: str,
        head_ref: str,
        *,
        shared_test_suites: Sequence[str] = (),
    ) -> dict[str, Any]:
        result = _new_result(authority_ref, base_ref, head_ref)
        primary_failure: ReadinessError | None = None
        temporary_root: Path | None = None
        base_worktree: Path | None = None
        head_worktree: Path | None = None
        base_added = False
        head_added = False
        try:
            self._assert_repository()
            authority = self._resolve_full_commit(authority_ref, "authority_ref")
            result["authority_ref"] = authority
            self._assert_accepted_authority(authority)
            self._assert_authority_provenance(authority)
            result["authority"] = {"status": "verified"}
            base = self._resolve_full_commit(base_ref, "base_ref")
            head = self._resolve_full_commit(head_ref, "head_ref")
            result["base_ref"] = base
            result["head_ref"] = head
            self._assert_ancestor(base, head)

            temporary_root = self._create_temporary_root()
            base_worktree = temporary_root / "base"
            head_worktree = temporary_root / "head"
            self._add_worktree(base_worktree, base)
            base_added = True
            self._add_worktree(head_worktree, head)
            head_added = True
            self._run_diff_check(result, base, head)
            self._seal_trusted_governance(result, authority, base, head, temporary_root, head_worktree)
            plan = self._plan_responsibilities(base, head, head_worktree, shared_test_suites)
            candidate_failure: ReadinessError | None = None
            try:
                self._run_compileall(result, head_worktree)
                self._run_responsibility_tests(result, head_worktree, plan.tests)
                if plan.frontend:
                    self._run_frontend_responsibility(result, head_worktree)
            except ReadinessError as error:
                candidate_failure = error
            try:
                self._assert_post_candidate_authority_integrity(result, authority)
            except ReadinessError as error:
                if candidate_failure is None:
                    raise
                candidate_failure.authority_integrity_failure = error
            if candidate_failure is not None:
                raise candidate_failure
        except ReadinessError as error:
            primary_failure = error

        cleanup_failure = self._cleanup_worktrees(
            result,
            temporary_root,
            (("head", head_worktree, head_added), ("base", base_worktree, base_added)),
        )
        if primary_failure is not None:
            primary_failure.result = result
            primary_failure.cleanup_failure = cleanup_failure
            raise primary_failure
        if cleanup_failure is not None:
            cleanup_failure.result = result
            raise cleanup_failure

        result["status"] = "pass"
        return result

    def _assert_repository(self) -> None:
        discovered = self._run(("git", "rev-parse", "--show-toplevel"), self._repo_root)
        if discovered.returncode != 0:
            raise ReadinessError("infrastructure_failure", "not_git_repository", _command_failure("git rev-parse", discovered))
        self._repo_root = Path(discovered.stdout.strip()).resolve()

    def _assert_accepted_authority(self, authority: str) -> None:
        accepted = self._run(("git", "rev-parse", "--verify", "origin/main^{commit}"), self._repo_root)
        if accepted.returncode != 0:
            raise ReadinessError(
                "governance_violation",
                "accepted_authority_ref_missing",
                "origin/main must resolve to an accepted authority commit before readiness can run",
            )
        accepted_ref = accepted.stdout.strip().lower()
        ancestor = self._run(("git", "merge-base", "--is-ancestor", authority, accepted_ref), self._repo_root)
        if ancestor.returncode == 1:
            raise ReadinessError(
                "governance_violation",
                "authority_not_accepted",
                "authority_ref must be reachable from accepted origin/main",
            )
        if ancestor.returncode != 0:
            raise ReadinessError("infrastructure_failure", "git_failed", _command_failure("git merge-base", ancestor))

    def _assert_authority_provenance(self, authority: str) -> None:
        source_path = Path(__file__).resolve()
        source_root = self._run(
            ("git", "-C", str(source_path.parent), "rev-parse", "--show-toplevel"),
            self._repo_root,
        )
        if source_root.returncode != 0:
            raise ReadinessError(
                "governance_violation",
                "authority_source_untrusted",
                "the readiness script must run from a detached authority worktree",
            )
        authority_root = Path(source_root.stdout.strip()).resolve()
        source_head = self._run(("git", "-C", str(authority_root), "rev-parse", "HEAD"), self._repo_root)
        if source_head.returncode != 0 or source_head.stdout.strip().lower() != authority:
            raise ReadinessError(
                "governance_violation",
                "authority_source_mismatch",
                "the running readiness script is not checked out at authority_ref",
            )
        if source_path != (authority_root / AUTHORITY_TOOL_PATH).resolve():
            raise ReadinessError(
                "governance_violation",
                "authority_provenance_mismatch",
                "the running readiness script is not the authority worktree copy",
            )
        self._assert_authority_path_matches_ref(
            authority,
            authority_root,
            AUTHORITY_TOOL_PATH,
            source_path,
            code="authority_provenance_mismatch",
            message="the running readiness script does not match the authority_ref Git object",
        )
        self._assert_authority_path_matches_ref(
            authority,
            authority_root,
            AUTHORITY_GOVERNANCE_PATH,
            authority_root / AUTHORITY_GOVERNANCE_PATH,
            code="authority_provenance_mismatch",
            message="the authority governance script does not match the authority_ref Git object",
        )
        self._authority_root = authority_root

    def _assert_authority_path_matches_ref(
        self,
        authority: str,
        authority_root: Path,
        relative_path: str,
        actual_path: Path,
        *,
        code: str,
        message: str,
    ) -> None:
        expected = self._run(("git", "rev-parse", "--verify", f"{authority}:{relative_path}"), self._repo_root)
        actual = self._run(
            ("git", "-C", str(authority_root), "hash-object", "--path", relative_path, "--", str(actual_path)),
            self._repo_root,
        )
        if expected.returncode != 0 or actual.returncode != 0 or expected.stdout.strip() != actual.stdout.strip():
            raise ReadinessError("governance_violation", code, message, path=relative_path)

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

    def _create_temporary_root(self) -> Path:
        try:
            return Path(tempfile.mkdtemp(prefix="ai-platform-pre-push-readiness-"))
        except OSError as error:
            raise ReadinessError("infrastructure_failure", "temporary_directory_failed", str(error)) from error

    def _add_worktree(self, path: Path, commit: str) -> None:
        created = self._run(("git", "worktree", "add", "--detach", str(path), commit), self._repo_root)
        if created.returncode != 0:
            raise ReadinessError("infrastructure_failure", "worktree_add_failed", _command_failure("git worktree add", created))

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
        compiled = self._run(command, head_worktree, env=_candidate_environment())
        if compiled.returncode != 0:
            result["stages"].append(_stage("compileall", command, "failed", compiled))
            raise ReadinessError(
                "product_test_failure",
                "compileall_failed",
                _command_failure("python -m compileall", compiled),
            )
        result["stages"].append(_stage("compileall", command, "pass", compiled))

    def _plan_responsibilities(
        self,
        base: str,
        head: str,
        head_worktree: Path,
        shared_test_suites: Sequence[str],
    ) -> _ResponsibilityPlan:
        # Include unchanged source blobs so copied exception files retain C* status.
        changed = self._run(
            (
                "git",
                "diff",
                "--name-status",
                "--find-renames=50%",
                "--find-copies=50%",
                "--find-copies-harder",
                base,
                head,
                "--",
            ),
            self._repo_root,
        )
        if changed.returncode != 0:
            raise ReadinessError("infrastructure_failure", "git_failed", _command_failure("git diff --name-status", changed))
        selected: set[str] = set()
        shared_paths: list[str] = []
        unowned_paths: list[str] = []
        frontend = False
        for status, path in _changed_paths(changed.stdout):
            if status.startswith("D"):
                continue
            pure_path = PurePosixPath(path)
            if _is_documentation_path(pure_path):
                continue
            if _is_shared_test_fixture(pure_path):
                shared_paths.append(path)
                continue
            if _is_frontend_path(pure_path):
                frontend = True
                continue
            if status in {"A", "M"} and path == CODE_GOVERNANCE_EXCEPTION_PATH:
                if self._git_tree_has_exact_file(head, CODE_GOVERNANCE_TEST_PATH):
                    selected.add(CODE_GOVERNANCE_TEST_PATH)
                    continue
                unowned_paths.append(path)
                continue
            if _is_test_module(pure_path) and (head_worktree / path).is_file():
                selected.add(path)
                continue
            mirrored = _mirrored_test_path(pure_path)
            if mirrored is not None and (head_worktree / mirrored).is_file():
                selected.add(mirrored)
                continue
            unowned_paths.append(path)
        if frontend and not (head_worktree / "frontend" / "web" / "package.json").is_file():
            unowned_paths.append("frontend/web/package.json")
        if unowned_paths:
            raise ReadinessError(
                "external_check",
                "responsibility_suite_required",
                "each affected production path requires a bounded responsible suite",
                path=sorted(unowned_paths)[0],
            )
        if shared_test_suites and not shared_paths:
            raise ReadinessError(
                "governance_violation",
                "unexpected_shared_test_suite",
                "--shared-test-suite is only valid when a named shared test fixture changed",
            )
        if shared_paths and not shared_test_suites:
            raise ReadinessError(
                "external_check",
                "shared_test_suite_required",
                "a changed shared test fixture requires one or more explicit --shared-test-suite paths",
                path=shared_paths[0],
            )
        for suite in shared_test_suites:
            pure_suite = PurePosixPath(suite)
            if not _is_test_module(pure_suite) or not (head_worktree / suite).is_file():
                raise ReadinessError(
                    "governance_violation",
                    "invalid_shared_test_suite",
                    "shared_test_suite must name an existing tests/test_*.py file at head_ref",
                    path=suite,
                )
            selected.add(suite)
        tests = tuple(sorted(selected))
        if len(tests) > MAX_RESPONSIBILITY_TESTS:
            raise ReadinessError(
                "governance_violation",
                "responsibility_test_limit",
                f"bounded responsibility suite selected {len(tests)} tests, limit is {MAX_RESPONSIBILITY_TESTS}",
            )
        return _ResponsibilityPlan(tests=tests, frontend=frontend)

    def _git_tree_has_exact_file(self, head: str, path: str) -> bool:
        membership = self._run(("git", "cat-file", "-e", f"{head}:{path}"), self._repo_root)
        if membership.returncode != 0:
            return False
        object_type = self._run(("git", "cat-file", "-t", f"{head}:{path}"), self._repo_root)
        return object_type.returncode == 0 and object_type.stdout.strip() == "blob"

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
        tested = self._run(command, head_worktree, env=_candidate_environment())
        if tested.returncode != 0:
            result["stages"].append(_stage("responsibility_tests", command, "failed", tested, tests=tests))
            raise ReadinessError(
                "product_test_failure",
                "pytest_failed",
                _command_failure("python -m pytest", tested),
                path=_failed_test_identity(tested),
            )
        result["stages"].append(_stage("responsibility_tests", command, "pass", tested, tests=tests))

    def _run_frontend_responsibility(self, result: dict[str, Any], head_worktree: Path) -> None:
        command = _frontend_command()
        frontend_root = head_worktree / "frontend" / "web"
        verified = self._run(command, frontend_root, env=_candidate_environment())
        if verified.returncode != 0:
            result["stages"].append(_stage("frontend_responsibility", command, "failed", verified))
            raise ReadinessError(
                "product_test_failure",
                "frontend_ci_verify_failed",
                _command_failure("corepack pnpm run ci:verify", verified),
            )
        result["stages"].append(_stage("frontend_responsibility", command, "pass", verified))

    def _seal_trusted_governance(
        self,
        result: dict[str, Any],
        authority: str,
        base: str,
        head: str,
        temporary_root: Path,
        head_worktree: Path,
    ) -> None:
        snapshot = self._materialize_authority_governance(authority, temporary_root)
        command = (
            sys.executable,
            "-P",
            str(snapshot),
            "check",
            "--base-ref",
            base,
            "--head-ref",
            head,
            "--format",
            "json",
        )
        governed = self._run(command, head_worktree, env=_governance_environment())
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
        result["authority"]["governance"] = "sealed"

    def _materialize_authority_governance(self, authority: str, temporary_root: Path) -> Path:
        source = self._run(("git", "show", f"{authority}:{AUTHORITY_GOVERNANCE_PATH}"), self._repo_root)
        if source.returncode != 0:
            raise ReadinessError(
                "governance_violation",
                "authority_provenance_mismatch",
                "the authority governance Git object is unavailable", path=AUTHORITY_GOVERNANCE_PATH,
            )
        snapshot = temporary_root / "authority-governance.py"
        try:
            snapshot.write_text(source.stdout, encoding="utf-8", newline="\n")
        except OSError as error:
            raise ReadinessError("infrastructure_failure", "authority_snapshot_failed", str(error)) from error
        return snapshot

    def _assert_post_candidate_authority_integrity(self, result: dict[str, Any], authority: str) -> None:
        if self._authority_root is None:
            raise ReadinessError("governance_violation", "authority_source_untrusted", "authority source is unavailable")
        try:
            self._assert_authority_path_matches_ref(
                authority,
                self._authority_root,
                AUTHORITY_TOOL_PATH,
                self._authority_root / AUTHORITY_TOOL_PATH,
                code="authority_post_candidate_integrity_mismatch",
                message="candidate activity changed the authority readiness script after governance was sealed",
            )
            self._assert_authority_path_matches_ref(
                authority,
                self._authority_root,
                AUTHORITY_GOVERNANCE_PATH,
                self._authority_root / AUTHORITY_GOVERNANCE_PATH,
                code="authority_post_candidate_integrity_mismatch",
                message="candidate activity changed authority governance after governance was sealed",
            )
        except ReadinessError:
            result["stages"].append({"name": "authority_integrity", "status": "failed"})
            raise
        result["authority"]["integrity"] = "verified"
        result["stages"].append({"name": "authority_integrity", "status": "pass"})

    def _cleanup_worktrees(
        self,
        result: dict[str, Any],
        temporary_root: Path | None,
        worktrees: Sequence[tuple[str, Path | None, bool]],
    ) -> ReadinessError | None:
        if temporary_root is None:
            return None
        failures: list[str] = []
        records: list[dict[str, Any]] = []
        for label, path, added in worktrees:
            if not added or path is None:
                continue
            record = {"label": label, "path": str(path), "registered_after": None, "remove_returncode": None}
            try:
                removed = self._run(("git", "worktree", "remove", "--force", str(path)), self._repo_root)
                record["remove_returncode"] = removed.returncode
                if removed.returncode != 0:
                    failures.append(_command_failure(f"git worktree remove {label}", removed))
                registered = self._worktree_registered(path)
                record["registered_after"] = registered
                if registered:
                    failures.append(f"git worktree registration remains for {label}")
            except ReadinessError as error:
                failures.append(str(error))
            records.append(record)
        try:
            shutil.rmtree(temporary_root)
        except OSError as error:
            failures.append(f"temporary worktree directory removal failed: {error}")
        stage: dict[str, Any] = {"name": "worktree_cleanup", "status": "failed" if failures else "pass", "worktrees": records}
        if failures:
            stage["failures"] = failures
        result["stages"].append(stage)
        if failures:
            return ReadinessError("infrastructure_failure", "worktree_cleanup_failed", "; ".join(failures))
        return None

    def _worktree_registered(self, path: Path) -> bool:
        listed = self._run(("git", "worktree", "list", "--porcelain"), self._repo_root)
        if listed.returncode != 0:
            raise ReadinessError("infrastructure_failure", "git_failed", _command_failure("git worktree list", listed))
        return any(_same_worktree_path(path, value) for value in _registered_worktree_paths(listed.stdout))

    def _run(self, command: Sequence[str], cwd: Path, *, env: dict[str, str] | None = None) -> _CommandResult:
        try:
            return self._runner.run(command, cwd=cwd, env=env)
        except OSError as error:
            raise ReadinessError("infrastructure_failure", "command_unavailable", f"{' '.join(command)}: {error}") from error


def _is_test_module(path: PurePosixPath) -> bool:
    return path.suffix == ".py" and path.parts and path.parts[0] == "tests" and path.name.startswith("test_")


def _is_shared_test_fixture(path: PurePosixPath) -> bool:
    return (
        path.suffix == ".py"
        and path.parts
        and path.parts[0] == "tests"
        and (path.name == "conftest.py" or "fixtures" in path.parts or "helpers" in path.parts)
    )


def _is_frontend_path(path: PurePosixPath) -> bool:
    return len(path.parts) >= 2 and path.parts[:2] == ("frontend", "web")


def _is_documentation_path(path: PurePosixPath) -> bool:
    return (
        (path.parts and path.parts[0] == "docs")
        or path.suffix.lower() in {".md", ".rst"}
        or path.name.lower() in {"agents.md", "license", "notice"}
    )


def _mirrored_test_path(path: PurePosixPath) -> str | None:
    if path.suffix != ".py" or not path.parts or path.parts[0] not in {"app", "scripts", "tools"}:
        return None
    return f"tests/test_{path.stem}.py"


def _changed_paths(output: str) -> tuple[tuple[str, str], ...]:
    changes: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        changes.append((fields[0], fields[-1]))
    return tuple(changes)


def _registered_worktree_paths(output: str) -> tuple[str, ...]:
    return tuple(line.removeprefix("worktree ") for line in output.splitlines() if line.startswith("worktree "))


def _same_worktree_path(expected: Path, registered: str) -> bool:
    candidate = Path(registered)
    try:
        if expected.exists() and candidate.exists() and os.path.samefile(expected, candidate):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.normpath(str(expected))) == os.path.normcase(os.path.normpath(registered))


def _candidate_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONSAFEPATH", None)
    return environment


def _frontend_command() -> tuple[str, str, str, str]:
    """Use Corepack's executable name on the current platform."""
    return ("corepack.cmd" if os.name == "nt" else "corepack", "pnpm", "run", "ci:verify")


def _governance_environment() -> dict[str, str]:
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


def _new_result(authority_ref: str | None, base_ref: str | None, head_ref: str | None) -> dict[str, Any]:
    return {
        "authority": {"status": "unverified"},
        "authority_ref": authority_ref,
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


def _failure_result(
    error: ReadinessError,
    authority_ref: str | None,
    base_ref: str | None,
    head_ref: str | None,
) -> dict[str, Any]:
    result = error.result or _new_result(authority_ref, base_ref, head_ref)
    result["status"] = "failed"
    result["category"] = error.category
    result["failure"] = {
        "code": error.code,
        "message": str(error),
        "path": error.path,
        "test_identity": error.path if error.code == "pytest_failed" else None,
    }
    if error.cleanup_failure is not None:
        result["cleanup_failure"] = {
            "code": error.cleanup_failure.code,
            "message": str(error.cleanup_failure),
        }
    if error.authority_integrity_failure is not None:
        result["authority_integrity_failure"] = {
            "code": error.authority_integrity_failure.code,
            "message": str(error.authority_integrity_failure),
        }
    return result


def _render_text(result: dict[str, Any]) -> str:
    if result["status"] == "pass":
        lines = [
            "pre-push-readiness: PASS",
            f"authority: {result['authority_ref']}",
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
    check.add_argument("--authority-ref", required=True)
    check.add_argument("--base-ref", required=True)
    check.add_argument("--head-ref", required=True)
    check.add_argument("--shared-test-suite", action="append", default=[])
    check.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the readiness command and return a stable process status."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    output_format = "json" if _requested_json(arguments) else "text"
    authority_ref = _argument_value(arguments, "--authority-ref")
    base_ref = _argument_value(arguments, "--base-ref")
    head_ref = _argument_value(arguments, "--head-ref")
    try:
        args = _build_parser().parse_args(arguments)
        result = PrePushReadiness(Path.cwd()).check(
            args.authority_ref,
            args.base_ref,
            args.head_ref,
            shared_test_suites=tuple(args.shared_test_suite),
        )
    except ReadinessError as error:
        result = _failure_result(error, authority_ref, base_ref, head_ref)
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
