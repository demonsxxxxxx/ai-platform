"""Run bounded local CI-readiness checks for one exact Git range."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

REPORT_SCHEMA_VERSION = "ai-platform.pre-push-readiness.v1"
FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")
MAX_RESPONSIBILITY_TESTS = 24
AUTHORITY_TOOL_PATH = "tools/pre_push_readiness.py"
AUTHORITY_GOVERNANCE_PATH = "tools/code_governance.py"
AUTHORITY_ARCHITECTURE_PATH = "tools/architecture_governance.py"
ARCHITECTURE_POLICY_PATH = "architecture-policy.json"
ARCHITECTURE_POLICY_SCHEMA_PATH = "schemas/architecture-policy.v1.schema.json"
CODE_GOVERNANCE_EXCEPTION_PATH = ".code-governance-exception.json"
CODE_GOVERNANCE_TEST_PATH = "tests/test_code_governance.py"
# Frozen high-risk safety suites. Do not expand this map for ordinary product changes.
IRREGULAR_RESPONSIBILITY_SUITES = {
    "app/skills/catalog.py": ("tests/test_authorized_skill_catalog.py",),
    "app/skills/deliverable_runtime.py": ("tests/test_skill_deliverable_runtime.py",),
    "app/skills/deliverables.py": ("tests/test_skill_deliverables.py",),
    "app/skills/packages.py": ("tests/test_skill_packages.py",),
    "app/skills/pinning.py": ("tests/test_skill_pinning.py",),
    "app/mcp/__init__.py": ("tests/test_mcp_tool_catalog.py", "tests/test_mcp_repository.py"),
    "app/mcp/catalog.py": ("tests/test_mcp_tool_catalog.py",),
    "app/mcp/repository.py": ("tests/test_mcp_repository.py", "tests/test_mcp_repository_postgres.py"),
    "app/schema.sql": ("tests/test_schema.py",),
    "deploy/ai-platform/docker-compose.yml": ("tests/test_runtime_launch_script.py",),
}
FRONTEND_ROOT_PATH = "frontend/web"
FRONTEND_PACKAGE_PATH = f"{FRONTEND_ROOT_PATH}/package.json"
FRONTEND_LOCKFILE_PATH = f"{FRONTEND_ROOT_PATH}/pnpm-lock.yaml"
PINNED_PNPM_PACKAGE_MANAGER = re.compile(r"pnpm@(?P<version>[0-9]+(?:\.[0-9]+){2}(?:-[0-9A-Za-z.-]+)?)\Z")
TEMPORARY_ROOT_PREFIX = "apr-"
WINDOWS_CONSERVATIVE_DIRECTORY_PATH_BUDGET = 240
WINDOWS_LONGEST_SKILL_RELATIVE_SUFFIX_LENGTH = 163
WINDOWS_DIRECTORY_PATH_HEADROOM = 16
WINDOWS_ERROR_DIRECTORY_NOT_EMPTY = 145
WINDOWS_DIRECTORY_REMOVE_RETRY_LIMIT = 4
WINDOWS_DIRECTORY_REMOVE_RETRY_WINDOW_SECONDS = 1.0
WINDOWS_DIRECTORY_REMOVE_RETRY_INITIAL_DELAY_SECONDS = 0.05
IS_WINDOWS = os.name == "nt"

FAILURE_TAXONOMY = {
    "stale_base": "The supplied base is not an ancestor of head; merge the current base before push.",
    "product_test_failure": "A deterministic local compile or responsibility-test check failed.",
    "governance_violation": "The exact range violated diff, Ruff, or code-governance policy.",
    "infrastructure_failure": "A required local command or temporary worktree could not run.",
    "external_check": "A remote provider check needs fresh external evidence; do not rerun without positive infrastructure evidence.",
}
GOVERNANCE_INFRASTRUCTURE_ERROR_CODES = frozenset(
    {"git_failed", "git_output_invalid", "not_git_repository"}
)
ARCHITECTURE_INFRASTRUCTURE_ERROR_CODES = frozenset(
    {
        "authority_source_unavailable",
        "git_failed",
        "git_object_missing",
        "git_output_invalid",
        "missing_ref",
        "not_git_repository",
    }
)

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


class _UnsafeDependencyCleanupPathError(OSError):
    def __init__(self, path: Path, message: str, *, removable_link: bool = False) -> None:
        super().__init__(message)
        self.path = path
        self.removable_link = removable_link


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


@dataclass(frozen=True)
class _ChangedPath:
    status: str
    source_path: str | None
    destination_path: str | None


@dataclass(frozen=True)
class _FrontendDependencyPaths:
    node_modules: Path


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
        regression_test_suites: Sequence[str] = (),
        shared_test_suites: Sequence[str] = (),
    ) -> dict[str, Any]:
        result = _new_result(authority_ref, base_ref, head_ref)
        primary_failure: ReadinessError | None = None
        temporary_root: Path | None = None
        base_worktree: Path | None = None
        head_worktree: Path | None = None
        base_added = False
        head_added = False
        frontend_dependencies: tuple[tuple[str, Path], ...] = ()
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
            base_worktree, head_worktree = _temporary_worktree_paths(temporary_root)
            self._add_worktree(base_worktree, base)
            base_added = True
            self._add_worktree(head_worktree, head)
            head_added = True
            self._run_diff_check(result, base, head)
            self._seal_trusted_governance(result, authority, base, head, temporary_root, head_worktree)
            self._seal_trusted_architecture(result, authority, base, head, temporary_root, head_worktree)
            plan = self._plan_responsibilities(
                base,
                head,
                head_worktree,
                regression_test_suites,
                shared_test_suites,
            )
            dependency_paths = _FrontendDependencyPaths(head_worktree / FRONTEND_ROOT_PATH / "node_modules")
            if plan.frontend:
                frontend_dependencies = (("node_modules", dependency_paths.node_modules),)
            candidate_failure: ReadinessError | None = None
            try:
                self._run_compileall(result, head_worktree)
                self._run_responsibility_tests(result, head_worktree, plan.tests)
                if plan.frontend:
                    package_manager = self._bootstrap_frontend_dependencies(
                        result,
                        head,
                        head_worktree,
                        dependency_paths,
                    )
                    self._run_frontend_responsibility(result, head_worktree, package_manager)
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
            frontend_dependencies=frontend_dependencies,
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
        for relative_path, message in (
            (AUTHORITY_ARCHITECTURE_PATH, "the authority architecture checker does not match the authority_ref Git object"),
            (ARCHITECTURE_POLICY_PATH, "the authority architecture policy does not match the authority_ref Git object"),
            (ARCHITECTURE_POLICY_SCHEMA_PATH, "the authority architecture schema does not match the authority_ref Git object"),
        ):
            self._assert_authority_path_matches_ref(
                authority,
                authority_root,
                relative_path,
                authority_root / relative_path,
                code="authority_provenance_mismatch",
                message=message,
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
        temporary_root: Path | None = None
        try:
            parent = _temporary_root_parent()
            temporary_root = Path(tempfile.mkdtemp(prefix=TEMPORARY_ROOT_PREFIX, dir=parent))
            if IS_WINDOWS:
                _assert_windows_nonreparse_directory(temporary_root)
        except OSError as error:
            cleanup_error: OSError | None = None
            if temporary_root is not None and _path_lexists(temporary_root):
                try:
                    _remove_cleanup_tree(temporary_root)
                except OSError as removal_error:
                    cleanup_error = removal_error
            detail = str(error)
            if cleanup_error is not None:
                detail = f"{detail}; rejected temporary root cleanup failed: {cleanup_error}"
            raise ReadinessError("infrastructure_failure", "temporary_directory_failed", detail) from error
        if _temporary_root_has_windows_headroom(temporary_root):
            return temporary_root
        try:
            _remove_cleanup_tree(temporary_root)
        except OSError as error:
            raise ReadinessError(
                "infrastructure_failure",
                "temporary_directory_path_too_long",
                f"temporary root {temporary_root} exceeds the safe Windows path budget and could not be removed: {error}",
            ) from error
        raise ReadinessError(
            "infrastructure_failure",
            "temporary_directory_path_too_long",
            f"temporary root {temporary_root} exceeds the safe Windows path budget; configure a shorter temporary directory parent",
        )

    def _add_worktree(self, path: Path, commit: str) -> None:
        created = self._run(_git_worktree_command("add", "--detach", str(path), commit), self._repo_root)
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
        regression_test_suites: Sequence[str],
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
        changed_test_modules: set[str] = set()
        shared_paths: set[str] = set()
        mapped_behavior_paths: set[str] = set()
        production_paths: set[str] = set()
        invalid_mapped_paths: set[str] = set()
        frontend = False
        for changed_path in _changed_paths(changed.stdout):
            status = changed_path.status
            affected_paths = _affected_change_paths(changed_path)
            if not affected_paths:
                production_paths.add("<unknown-change-path>")
                continue
            if _touches_code_governance_exception(changed_path):
                if status.startswith("D"):
                    continue
                if (
                    status in {"A", "M"}
                    and changed_path.source_path is None
                    and changed_path.destination_path == CODE_GOVERNANCE_EXCEPTION_PATH
                    and self._git_tree_has_exact_file(head, CODE_GOVERNANCE_TEST_PATH)
                ):
                    selected.add(CODE_GOVERNANCE_TEST_PATH)
                    continue
                invalid_mapped_paths.add(CODE_GOVERNANCE_EXCEPTION_PATH)
                continue
            mapped_paths_for_change: set[str] = set()
            for path in affected_paths:
                mapped_suites = IRREGULAR_RESPONSIBILITY_SUITES.get(path)
                if mapped_suites is None:
                    continue
                if all(self._is_valid_bounded_test_suite(head, head_worktree, suite) for suite in mapped_suites):
                    selected.update(mapped_suites)
                    mapped_behavior_paths.add(path)
                    mapped_paths_for_change.add(path)
                    continue
                invalid_mapped_paths.add(path)
            move_only = status == "R100" and len(affected_paths) == 2 and all(
                _is_ordinary_production_path(path) for path in affected_paths
            )
            for path in affected_paths:
                pure_path = PurePosixPath(path)
                if _is_documentation_path(pure_path):
                    continue
                if _is_shared_test_fixture(pure_path):
                    shared_paths.add(path)
                    continue
                if _is_frontend_path(pure_path):
                    frontend = True
                    continue
                if _is_test_module(pure_path):
                    if (head_worktree / path).is_file():
                        changed_test_modules.add(path)
                        selected.add(path)
                    continue
                if not move_only and not mapped_paths_for_change:
                    production_paths.add(path)
        if frontend and not (head_worktree / "frontend" / "web" / "package.json").is_file():
            invalid_mapped_paths.add("frontend/web/package.json")
        if invalid_mapped_paths:
            raise ReadinessError(
                "external_check",
                "responsibility_suite_required",
                "a frozen high-risk path requires its exact bounded safety suite",
                path=sorted(invalid_mapped_paths)[0],
            )
        if shared_test_suites and not shared_paths:
            raise ReadinessError(
                "governance_violation",
                "unexpected_shared_test_suite",
                "--shared-test-suite is only valid when a named shared test fixture changed",
            )
        declared_suites = (*regression_test_suites, *shared_test_suites)
        if regression_test_suites and not (production_paths or mapped_behavior_paths or shared_paths):
            raise ReadinessError(
                "governance_violation",
                "unexpected_regression_test_suite",
                "--regression-test-suite is only valid when production behavior or a shared test fixture changed",
            )
        if shared_paths and not declared_suites:
            raise ReadinessError(
                "external_check",
                "shared_test_suite_required",
                "a changed shared test fixture requires one or more explicit --regression-test-suite paths",
                path=sorted(shared_paths)[0],
            )
        if production_paths and not changed_test_modules and not declared_suites:
            raise ReadinessError(
                "external_check",
                "regression_test_suite_required",
                "production behavior changed without a changed test module or an explicit --regression-test-suite",
                path=sorted(production_paths)[0],
            )
        for suite in regression_test_suites:
            if not self._is_valid_bounded_test_suite(head, head_worktree, suite):
                raise ReadinessError(
                    "governance_violation",
                    "invalid_regression_test_suite",
                    "regression_test_suite must name an existing tests/test_*.py file at head_ref",
                    path=suite,
                )
            selected.add(suite)
        for suite in shared_test_suites:
            if not self._is_valid_bounded_test_suite(head, head_worktree, suite):
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

    def _is_valid_bounded_test_suite(self, head: str, head_worktree: Path, suite: str) -> bool:
        if not _is_canonical_posix_test_path(suite):
            return False
        if not self._git_tree_has_exact_file(head, suite):
            return False
        try:
            tests_root = (head_worktree / "tests").resolve(strict=True)
            resolved_suite = (head_worktree / PurePosixPath(suite)).resolve(strict=True)
            resolved_suite.relative_to(tests_root)
        except (OSError, ValueError):
            return False
        return resolved_suite.is_file()

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

    def _bootstrap_frontend_dependencies(
        self,
        result: dict[str, Any],
        head: str,
        head_worktree: Path,
        dependencies: _FrontendDependencyPaths,
    ) -> str:
        command: tuple[str, ...] = ()
        package_manager: str | None = None
        completed: _CommandResult | None = None
        try:
            package_manager = self._frontend_package_manager(head, head_worktree)
            if _path_lexists(dependencies.node_modules):
                raise ReadinessError(
                    "infrastructure_failure",
                    "frontend_dependency_provenance_mismatch",
                    "the detached frontend worktree must not reuse an existing node_modules tree",
                    path=f"{FRONTEND_ROOT_PATH}/node_modules",
                )
            version_command = _frontend_command(package_manager, "--version")
            completed = self._run(version_command, head_worktree / FRONTEND_ROOT_PATH, env=_candidate_environment())
            if completed.returncode != 0:
                command = version_command
                raise ReadinessError(
                    "infrastructure_failure",
                    "frontend_dependency_bootstrap_failed",
                    _command_failure("pinned Corepack pnpm --version", completed),
                    path=FRONTEND_PACKAGE_PATH,
                )
            expected_version = package_manager.removeprefix("pnpm@")
            if completed.stdout.strip() != expected_version:
                command = version_command
                raise ReadinessError(
                    "infrastructure_failure",
                    "frontend_dependency_provenance_mismatch",
                    f"Corepack resolved pnpm {completed.stdout.strip()!r}, expected {expected_version!r}",
                    path=FRONTEND_PACKAGE_PATH,
                )
            command = _frontend_install_command(package_manager)
            completed = self._run(command, head_worktree / FRONTEND_ROOT_PATH, env=_candidate_environment())
            if completed.returncode != 0:
                raise ReadinessError(
                    "infrastructure_failure",
                    "frontend_dependency_bootstrap_failed",
                    _command_failure("pinned Corepack pnpm install", completed),
                    path=FRONTEND_LOCKFILE_PATH,
                )
            if not dependencies.node_modules.is_dir():
                raise ReadinessError(
                    "infrastructure_failure",
                    "frontend_dependency_bootstrap_failed",
                    "pinned Corepack pnpm install did not create detached frontend/web/node_modules",
                    path=f"{FRONTEND_ROOT_PATH}/node_modules",
                )
        except ReadinessError as error:
            if error.code == "command_unavailable":
                error = ReadinessError(
                    "infrastructure_failure",
                    "frontend_dependency_bootstrap_failed",
                    str(error),
                    path=FRONTEND_PACKAGE_PATH,
                )
            self._record_frontend_dependency_stage(
                result,
                "failed",
                command=command,
                package_manager=package_manager,
                completed=completed,
            )
            raise error
        self._record_frontend_dependency_stage(
            result,
            "pass",
            command=command,
            package_manager=package_manager,
            completed=completed,
        )
        return package_manager

    def _frontend_package_manager(self, head: str, head_worktree: Path) -> str:
        self._assert_frontend_metadata_matches_head(head, head_worktree, FRONTEND_PACKAGE_PATH)
        self._assert_frontend_metadata_matches_head(head, head_worktree, FRONTEND_LOCKFILE_PATH)
        package_path = head_worktree / PurePosixPath(FRONTEND_PACKAGE_PATH)
        try:
            metadata = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReadinessError(
                "infrastructure_failure",
                "frontend_dependency_provenance_mismatch",
                f"frontend package metadata is unreadable: {error}",
                path=FRONTEND_PACKAGE_PATH,
            ) from error
        package_manager = metadata.get("packageManager") if isinstance(metadata, dict) else None
        if not isinstance(package_manager, str) or PINNED_PNPM_PACKAGE_MANAGER.fullmatch(package_manager) is None:
            raise ReadinessError(
                "infrastructure_failure",
                "frontend_dependency_metadata_missing",
                "frontend package.json must declare an exact pnpm@<version> packageManager",
                path=FRONTEND_PACKAGE_PATH,
            )
        return package_manager

    def _assert_frontend_metadata_matches_head(self, head: str, head_worktree: Path, path: str) -> None:
        candidate_path = head_worktree / PurePosixPath(path)
        if not self._git_tree_has_exact_file(head, path) or not candidate_path.is_file():
            raise ReadinessError(
                "infrastructure_failure",
                "frontend_dependency_metadata_missing",
                "frontend dependency bootstrap requires package.json and pnpm-lock.yaml at head_ref",
                path=path,
            )
        expected = self._run(("git", "rev-parse", f"{head}:{path}"), self._repo_root)
        actual = self._run(("git", "hash-object", f"--path={path}", "--", path), head_worktree)
        if expected.returncode != 0 or actual.returncode != 0 or expected.stdout.strip() != actual.stdout.strip():
            raise ReadinessError(
                "infrastructure_failure",
                "frontend_dependency_provenance_mismatch",
                "detached frontend dependency metadata no longer matches the exact head Git tree",
                path=path,
            )

    def _record_frontend_dependency_stage(
        self,
        result: dict[str, Any],
        status: str,
        *,
        command: Sequence[str],
        package_manager: str | None,
        completed: _CommandResult | None,
    ) -> None:
        stage: dict[str, Any] = {
            "command": list(command),
            "dependency_store": "host_content_addressed",
            "lockfile": FRONTEND_LOCKFILE_PATH,
            "name": "frontend_dependency_bootstrap",
            "package_manager": package_manager,
            "status": status,
        }
        if status == "failed" and completed is not None:
            stage["output"] = _command_output(completed)
        result["stages"].append(stage)

    def _run_frontend_responsibility(self, result: dict[str, Any], head_worktree: Path, package_manager: str) -> None:
        command = _frontend_command(package_manager, "run", "ci:verify")
        frontend_root = head_worktree / FRONTEND_ROOT_PATH
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
                _governance_failure_category(
                    governed.returncode,
                    violation["code"],
                    infrastructure_codes=GOVERNANCE_INFRASTRUCTURE_ERROR_CODES,
                ),
                violation["code"],
                violation["message"],
                path=violation["path"],
            )
        result["stages"].append(_stage("governance", command, "pass", governed, ruff=ruff))
        result["authority"]["governance"] = "sealed"

    def _materialize_authority_governance(self, authority: str, temporary_root: Path) -> Path:
        return self._materialize_authority_snapshot(
            authority,
            temporary_root,
            AUTHORITY_GOVERNANCE_PATH,
            "authority-governance.py",
        )

    def _seal_trusted_architecture(
        self,
        result: dict[str, Any],
        authority: str,
        base: str,
        head: str,
        temporary_root: Path,
        head_worktree: Path,
    ) -> None:
        snapshot = self._materialize_authority_snapshot(
            authority,
            temporary_root,
            AUTHORITY_ARCHITECTURE_PATH,
            "authority-architecture.py",
        )
        command = (
            sys.executable,
            "-P",
            str(snapshot),
            "check",
            "--authority-ref",
            authority,
            "--base-ref",
            base,
            "--head-ref",
            head,
            "--format",
            "json",
        )
        governed = self._run(command, head_worktree, env=_governance_environment())
        payload = _json_payload(governed)
        metadata = {
            key: payload.get(key)
            for key in ("policy", "exception", "exempted_findings")
            if key in payload
        }
        if governed.returncode != 0:
            stage = _stage("architecture_governance", command, "failed", governed)
            stage.update(metadata)
            result["stages"].append(stage)
            failure = _first_architecture_failure(payload)
            category = _governance_failure_category(
                governed.returncode,
                failure["code"],
                infrastructure_codes=ARCHITECTURE_INFRASTRUCTURE_ERROR_CODES,
            )
            raise ReadinessError(
                category,
                failure["code"],
                failure["message"],
                path=failure["path"],
            )
        stage = _stage("architecture_governance", command, "pass", governed)
        stage.update(metadata)
        result["stages"].append(stage)
        result["authority"]["architecture_governance"] = "sealed"

    def _materialize_authority_snapshot(
        self,
        authority: str,
        temporary_root: Path,
        relative_path: str,
        snapshot_name: str,
    ) -> Path:
        source = self._run(("git", "show", f"{authority}:{relative_path}"), self._repo_root)
        if source.returncode != 0:
            raise ReadinessError(
                "governance_violation",
                "authority_provenance_mismatch",
                "the authority Git object is unavailable",
                path=relative_path,
            )
        snapshot = temporary_root / snapshot_name
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
            for relative_path, message in (
                (AUTHORITY_ARCHITECTURE_PATH, "candidate activity changed the authority architecture checker after governance was sealed"),
                (ARCHITECTURE_POLICY_PATH, "candidate activity changed the authority architecture policy after governance was sealed"),
                (ARCHITECTURE_POLICY_SCHEMA_PATH, "candidate activity changed the authority architecture schema after governance was sealed"),
            ):
                self._assert_authority_path_matches_ref(
                    authority,
                    self._authority_root,
                    relative_path,
                    self._authority_root / relative_path,
                    code="authority_post_candidate_integrity_mismatch",
                    message=message,
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
        *,
        frontend_dependencies: Sequence[tuple[str, Path]] = (),
    ) -> ReadinessError | None:
        if temporary_root is None:
            return None
        failures: list[str] = []
        dependency_records: list[dict[str, Any]] = []
        candidate_worktree = _head_worktree_path(worktrees)
        blocked_worktrees: set[str] = set()
        for label, path in frontend_dependencies:
            record: dict[str, Any] = {"label": label, "path": str(path), "exists_after": None}
            try:
                _assert_frontend_dependency_cleanup_target(temporary_root, candidate_worktree, path)
                _remove_cleanup_tree(path)
            except _UnsafeDependencyCleanupPathError as error:
                record["ancestor_error"] = str(error)
                failures.append(f"frontend dependency path is unsafe for cleanup for {label}: {error}")
                if error.removable_link:
                    try:
                        _remove_cleanup_link(error.path)
                        record["unsafe_ancestor_removed"] = not _path_lexists(error.path)
                    except OSError as removal_error:
                        record["unsafe_ancestor_remove_error"] = str(removal_error)
                        failures.append(f"unsafe dependency ancestor removal failed for {label}: {removal_error}")
                    if _path_lexists(error.path) and candidate_worktree is not None:
                        blocked_worktrees.add(_lexical_path_key(candidate_worktree))
            except OSError as error:
                record["remove_error"] = str(error)
                failures.append(f"frontend dependency path removal failed for {label}: {error}")
            record["exists_before_worktree_remove"] = _path_lexists(path)
            dependency_records.append(record)
        records: list[dict[str, Any]] = []
        for label, path, added in worktrees:
            if not added or path is None:
                continue
            record = {
                "label": label,
                "path": str(path),
                "path_exists_after": None,
                "registered_after": None,
                "remove_diagnostic": None,
                "remove_returncode": None,
            }
            try:
                if _lexical_path_key(path) in blocked_worktrees:
                    record["remove_skipped"] = "unsafe dependency ancestor remains"
                    failures.append(f"git worktree removal skipped for {label}: unsafe dependency ancestor remains")
                else:
                    removed = self._run(_git_worktree_command("remove", "--force", str(path)), self._repo_root)
                    record["remove_returncode"] = removed.returncode
                    if removed.returncode != 0:
                        record["remove_diagnostic"] = _command_failure(f"git worktree remove {label}", removed)
            except ReadinessError as error:
                failures.append(str(error))
            records.append(record)
        try:
            _remove_cleanup_tree(temporary_root)
        except OSError as error:
            failures.append(f"temporary worktree directory removal failed: {error}")
        for record, (_, path, _) in zip(records, (item for item in worktrees if item[1] is not None and item[2]), strict=True):
            assert path is not None
            try:
                registered = self._worktree_registered(path)
                record["registered_after"] = registered
                if registered:
                    failures.append(f"git worktree registration remains for {record['label']}")
            except ReadinessError as error:
                record["worktree_list_error"] = str(error)
                failures.append(str(error))
            path_exists = _path_lexists(path)
            record["path_exists_after"] = path_exists
            if path_exists:
                failures.append(f"git worktree path remains for {record['label']}")
        for record, (_, path) in zip(dependency_records, frontend_dependencies, strict=True):
            exists_after = _path_lexists(path)
            record["exists_after"] = exists_after
            if exists_after:
                failures.append(f"frontend dependency path remains after cleanup: {record['label']}")
        stage: dict[str, Any] = {
            "frontend_dependencies": dependency_records,
            "name": "worktree_cleanup",
            "status": "failed" if failures else "pass",
            "worktrees": records,
        }
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


def _affected_change_paths(change: _ChangedPath) -> tuple[str, ...]:
    if change.status.startswith("R"):
        return tuple(path for path in (change.source_path, change.destination_path) if path is not None)
    path = change.destination_path or change.source_path
    return (path,) if path is not None else ()


def _is_ordinary_production_path(path: str) -> bool:
    pure_path = PurePosixPath(path)
    return (
        path not in IRREGULAR_RESPONSIBILITY_SUITES
        and not _is_documentation_path(pure_path)
        and not _is_shared_test_fixture(pure_path)
        and not _is_frontend_path(pure_path)
        and not _is_test_module(pure_path)
    )


def _changed_paths(output: str) -> tuple[_ChangedPath, ...]:
    changes: list[_ChangedPath] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        status = fields[0]
        if status.startswith(("C", "R")) and len(fields) >= 3:
            changes.append(_ChangedPath(status, fields[1], fields[2]))
            continue
        changes.append(_ChangedPath(status, None, fields[-1]))
    return tuple(changes)


def _touches_code_governance_exception(change: _ChangedPath) -> bool:
    return CODE_GOVERNANCE_EXCEPTION_PATH in {change.source_path, change.destination_path}


def _is_canonical_posix_test_path(value: str) -> bool:
    if not value or "\\" in value:
        return False
    pure_path = PurePosixPath(value)
    return (
        not pure_path.is_absolute()
        and bool(pure_path.parts)
        and all(part not in {"", ".", ".."} for part in pure_path.parts)
        and pure_path.as_posix() == value
        and _is_test_module(pure_path)
    )


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


def _path_lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _temporary_worktree_paths(temporary_root: Path) -> tuple[Path, Path]:
    return temporary_root / "base", temporary_root / "head"


def _temporary_root_parent() -> Path:
    if not IS_WINDOWS:
        return Path(tempfile.gettempdir())
    parent = Path.home()
    _assert_windows_nonreparse_directory(parent)
    return parent


def _assert_windows_nonreparse_directory(path: Path) -> None:
    try:
        details = os.lstat(_windows_extended_path(path))
    except OSError as error:
        raise OSError(f"readiness temporary directory is unavailable: {path}") from error
    if _is_link_or_reparse_point(details) or not stat.S_ISDIR(details.st_mode):
        kind = "a reparse point" if _is_link_or_reparse_point(details) else "not a directory"
        raise OSError(f"readiness temporary directory is {kind}: {path}")


def _temporary_root_has_windows_headroom(temporary_root: Path) -> bool:
    return (
        len(os.fspath(temporary_root))
        + WINDOWS_LONGEST_SKILL_RELATIVE_SUFFIX_LENGTH
        + WINDOWS_DIRECTORY_PATH_HEADROOM
        <= WINDOWS_CONSERVATIVE_DIRECTORY_PATH_BUDGET
    )


def _head_worktree_path(worktrees: Sequence[tuple[str, Path | None, bool]]) -> Path | None:
    head_paths = [path for label, path, added in worktrees if label == "head" and added and path is not None]
    return head_paths[0] if len(head_paths) == 1 else None


def _assert_frontend_dependency_cleanup_target(
    temporary_root: Path,
    candidate_worktree: Path | None,
    dependency_path: Path,
) -> None:
    if candidate_worktree is None:
        raise _UnsafeDependencyCleanupPathError(dependency_path, "the generated candidate worktree is unavailable")
    if _lexical_relative_parts(temporary_root, candidate_worktree) != ("head",):
        raise _UnsafeDependencyCleanupPathError(candidate_worktree, "candidate worktree is not the generated temporary head worktree")
    if _lexical_relative_parts(candidate_worktree, dependency_path) != ("frontend", "web", "node_modules"):
        raise _UnsafeDependencyCleanupPathError(
            dependency_path,
            "frontend dependency path is not exactly within the generated candidate worktree",
        )
    for ancestor in (
        candidate_worktree,
        candidate_worktree / "frontend",
        candidate_worktree / "frontend" / "web",
    ):
        _assert_nonreparse_directory(ancestor)


def _lexical_relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    root_text = _lexical_path_key(root)
    path_text = _lexical_path_key(path)
    try:
        relative = os.path.relpath(path_text, root_text)
    except ValueError as error:
        raise _UnsafeDependencyCleanupPathError(path, "cleanup path is on a different volume") from error
    if relative in (os.curdir, os.pardir) or relative.startswith(os.pardir + os.sep) or os.path.isabs(relative):
        raise _UnsafeDependencyCleanupPathError(path, "cleanup path is not a strict lexical descendant")
    return tuple(part for part in relative.split(os.sep) if part and part != os.curdir)


def _lexical_path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def _assert_nonreparse_directory(path: Path) -> None:
    try:
        details = os.lstat(_windows_extended_path(path))
    except FileNotFoundError as error:
        raise _UnsafeDependencyCleanupPathError(path, "candidate dependency ancestor is missing") from error
    if _is_link_or_reparse_point(details):
        raise _UnsafeDependencyCleanupPathError(
            path,
            f"candidate dependency ancestor is a reparse point: {path}",
            removable_link=True,
        )
    if not stat.S_ISDIR(details.st_mode):
        raise _UnsafeDependencyCleanupPathError(path, f"candidate dependency ancestor is not a directory: {path}")


def _windows_extended_path(path: Path | str) -> str:
    rendered = os.fspath(path)
    if os.name != "nt" or rendered.startswith("\\\\?\\"):
        return rendered
    absolute = os.path.abspath(rendered)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def _remove_cleanup_tree(path: Path) -> None:
    if not _path_lexists(path):
        return
    rendered = _windows_extended_path(path)
    details = os.lstat(rendered)
    if _is_link_or_reparse_point(details):
        _remove_cleanup_link_rendered(rendered, details)
        return
    if os.name != "nt":
        shutil.rmtree(path)
        return
    _remove_windows_cleanup_tree(rendered)


def _remove_cleanup_link(path: Path) -> None:
    rendered = _windows_extended_path(path)
    details = os.lstat(rendered)
    if not _is_link_or_reparse_point(details):
        raise OSError(f"cleanup path is no longer a link or reparse point: {path}")
    _remove_cleanup_link_rendered(rendered, details)


def _remove_cleanup_link_rendered(path: str, details: os.stat_result) -> None:
    attributes = _windows_file_attributes(details)
    if os.name == "nt":
        _clear_windows_read_only(path, attributes)
    if stat.S_ISDIR(details.st_mode):
        os.rmdir(path)
    else:
        os.unlink(path)


@dataclass
class _WindowsDirectoryRemovalRetry:
    attempts: int = 0
    deadline: float | None = None

    def wait(self, error: OSError) -> bool:
        if (
            getattr(error, "winerror", None) != WINDOWS_ERROR_DIRECTORY_NOT_EMPTY
            or self.attempts >= WINDOWS_DIRECTORY_REMOVE_RETRY_LIMIT
        ):
            return False
        now = time.monotonic()
        if self.deadline is None:
            self.deadline = now + WINDOWS_DIRECTORY_REMOVE_RETRY_WINDOW_SECONDS
        remaining = self.deadline - now
        if remaining <= 0:
            return False
        delay = min(
            WINDOWS_DIRECTORY_REMOVE_RETRY_INITIAL_DELAY_SECONDS * (2**self.attempts),
            remaining,
        )
        self.attempts += 1
        time.sleep(delay)
        return True


def _remove_windows_cleanup_tree(
    path: str,
    *,
    retry: _WindowsDirectoryRemovalRetry | None = None,
) -> None:
    if retry is None:
        retry = _WindowsDirectoryRemovalRetry()
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return
    attributes = _windows_file_attributes(details)
    is_directory = stat.S_ISDIR(details.st_mode)
    if _is_link_or_reparse_point(details):
        _remove_cleanup_link_rendered(path, details)
        return
    if is_directory:
        with os.scandir(path) as entries:
            for entry in entries:
                _remove_windows_cleanup_tree(entry.path, retry=retry)
        _clear_windows_read_only(path, attributes)
        try:
            os.rmdir(path)
        except OSError as error:
            if not retry.wait(error):
                raise
            _remove_windows_cleanup_tree(path, retry=retry)
        return
    _clear_windows_read_only(path, attributes)
    os.unlink(path)


def _is_link_or_reparse_point(details: os.stat_result) -> bool:
    return stat.S_ISLNK(details.st_mode) or bool(
        _windows_file_attributes(details) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _windows_file_attributes(details: os.stat_result) -> int:
    return getattr(details, "st_file_attributes", 0)


def _clear_windows_read_only(path: str, attributes: int) -> None:
    if attributes & stat.FILE_ATTRIBUTE_READONLY:
        os.chmod(path, stat.S_IWRITE)


def _git_worktree_command(*arguments: str) -> tuple[str, ...]:
    """Enable Windows long-path checkout handling for disposable worktrees only."""
    return ("git", "-c", "core.longpaths=true", "worktree", *arguments)


def _candidate_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONSAFEPATH", None)
    return environment


def _frontend_command(package_manager: str, *arguments: str) -> tuple[str, ...]:
    """Run the exact package-manager pin through Corepack on the current platform."""
    return ("corepack.cmd" if os.name == "nt" else "corepack", package_manager, *arguments)


def _frontend_install_command(package_manager: str) -> tuple[str, ...]:
    return _frontend_command(package_manager, "install", "--frozen-lockfile", "--prefer-offline")


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


def _first_architecture_failure(payload: dict[str, Any]) -> dict[str, str | None]:
    findings = payload.get("findings")
    if isinstance(findings, list) and findings and isinstance(findings[0], dict):
        finding = findings[0]
        return {
            "code": str(finding.get("code", "architecture_governance_failed")),
            "message": str(finding.get("message", "architecture governance failed")),
            "path": finding.get("path") if isinstance(finding.get("path"), str) else None,
        }
    error = payload.get("error")
    if isinstance(error, dict):
        return {
            "code": str(error.get("code", "architecture_governance_error")),
            "message": str(error.get("message", "architecture governance failed")),
            "path": None,
        }
    return {
        "code": "architecture_governance_failed",
        "message": "architecture governance failed",
        "path": None,
    }


def _governance_failure_category(
    returncode: int,
    code: str | None,
    *,
    infrastructure_codes: frozenset[str],
) -> str:
    if returncode == 3 and code in infrastructure_codes:
        return "infrastructure_failure"
    return "governance_violation"


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
    check.add_argument("--regression-test-suite", action="append", default=[])
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
            regression_test_suites=tuple(args.regression_test_suite),
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
