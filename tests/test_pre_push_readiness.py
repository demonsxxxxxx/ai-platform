from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
READINESS_TOOL = REPO_ROOT / "tools" / "pre_push_readiness.py"
GOVERNANCE_TOOL = REPO_ROOT / "tools" / "code_governance.py"
ARCHITECTURE_TOOL = REPO_ROOT / "tools" / "architecture_governance.py"
ARCHITECTURE_POLICY = REPO_ROOT / "architecture-policy.json"
ARCHITECTURE_SCHEMA = REPO_ROOT / "schemas" / "architecture-policy.v1.schema.json"
CODE_GOVERNANCE_TEST = REPO_ROOT / "tests" / "test_code_governance.py"
ISSUE_WORKFLOW = REPO_ROOT / "docs" / "agent-rules" / "github-issue-pr-workflow.md"
EXCEPTION_PATH = ".code-governance-exception.json"
FRONTEND_PACKAGE_MANAGER = "pnpm@10.32.1"


def _retired_runs_legacy_api_cutover() -> dict[str, object]:
    return {
        "source_path": "app/repositories.py",
        "public_module": "app.runs.api",
        "canonical_module": "app.runs.domain.terminalization",
        "module_alias": "runs_api",
        "removed_imports": [{"module": "dataclasses", "name": "dataclass"}],
        "rewrites": [
            {
                "old_symbol": "TERMINAL_RUN_STATUSES",
                "new_symbol": "TERMINAL_RUN_STATUSES",
            },
            {
                "old_symbol": "ToolPermissionTerminalizationProgress",
                "new_symbol": "RunTerminalizationProgress",
            },
            {
                "old_symbol": "_terminalization_progress_for_requested_status",
                "new_symbol": "progress_for_requested_status",
            },
        ],
        "owner": "runs",
        "reason": (
            "The frozen global repository may perform one exact hard cut from its "
            "locally owned Run terminalization policy symbols to the public Runs API "
            "without retaining policy aliases or importing Runs internals."
        ),
        "removal_condition": (
            "After the exact Runs policy cutover is merged, remove this consumed "
            "authority entry before any further app/repositories.py source change."
        ),
    }


def _run(
    repo: Path,
    *arguments: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _git(repo: Path, *arguments: str) -> str:
    return _run(repo, "git", *arguments).stdout.strip()


def _write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _create_readiness_repo(tmp_path: Path, *, code_governance_test_path: str) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "readiness@example.test")
    _git(repo, "config", "user.name", "Readiness Test")
    _write(repo, "README.md", "fixture\n")
    _write(repo, "app/__init__.py", "")
    _write(repo, "app/billing.py", "RATE = 2\n")
    _write(
        repo,
        "app/artifact_lifecycle_repository.py",
        "from app.persistence.object_deletions import OUTBOX_TARGET_ARTIFACT\n\n"
        "__all__ = ['OUTBOX_TARGET_ARTIFACT']\n",
    )
    _write(repo, "app/persistence/object_deletions.py", "OUTBOX_TARGET_ARTIFACT = 'artifact'\nOUTBOX_TARGET_FILE = 'file'\n")
    _write(repo, "app/executors/claude_agent_worker.py", "class ClaudeAgentWorkerAdapter:\n    pass\n")
    _write(
        repo,
        "app/executors/registry.py",
        "from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter\n\n\n"
        "def _default_adapters():\n"
        "    return {'claude-agent-worker': ClaudeAgentWorkerAdapter()}\n",
    )
    _write(repo, "app/repositories.py", "DEFAULT_RUN_EXECUTOR_TYPES = {'claude-agent-worker'}\n")
    _write(repo, "docs/architecture/source-code-architecture.md", "# Source architecture\n")
    _write(repo, "tools/code_governance.py", GOVERNANCE_TOOL.read_text(encoding="utf-8"))
    _write(repo, "tools/architecture_governance.py", ARCHITECTURE_TOOL.read_text(encoding="utf-8"))
    _write(repo, "tools/pre_push_readiness.py", READINESS_TOOL.read_text(encoding="utf-8"))
    policy = json.loads(ARCHITECTURE_POLICY.read_text(encoding="utf-8"))
    policy["legacy_api_cutovers"] = [_retired_runs_legacy_api_cutover()]
    migration_bridge_sources = sorted(
        {bridge["source_path"] for bridge in policy["migration_bridges"]}
    )
    cutover_sources = sorted(
        {cutover["source_path"] for cutover in policy["legacy_api_cutovers"]}
    )
    for source_path in sorted(set(migration_bridge_sources) | set(cutover_sources)):
        if not (repo / source_path).exists():
            _write(repo, source_path, "FIXTURE = True\n")
    for cutover in policy["legacy_api_cutovers"]:
        source_path = cutover["source_path"]
        source = (repo / source_path).read_text(encoding="utf-8")
        imports = "".join(
            f"from {item['module']} import {item['name']}\n"
            for item in cutover["removed_imports"]
        )
        definitions: list[str] = []
        for rewrite in cutover["rewrites"]:
            name = rewrite["old_symbol"]
            if name.isupper():
                definitions.append(f"{name} = {{'succeeded'}}")
            elif name[:1].isupper():
                definitions.append(f"class {name}:\n    pass")
            else:
                definitions.append(f"def {name}(value=None):\n    return value")
        _write(
            repo,
            source_path,
            imports
            + source
            + "\n\n"
            + "\n\n".join(definitions)
            + "\n\ndef fixture_cutover_use():\n    return ("
            + ", ".join(item["old_symbol"] for item in cutover["rewrites"])
            + ")\n",
        )
    policy["approved_root_modules"] = sorted(
        {
            "app/__init__.py",
            "app/artifact_lifecycle_repository.py",
            "app/billing.py",
            "app/repositories.py",
            *(
                source_path
                for source_path in sorted(set(migration_bridge_sources) | set(cutover_sources))
                if Path(source_path).parent.as_posix() == "app"
            ),
        }
    )
    policy["frozen_hot_files"] = [
        {
            "path": "app/persistence/object_deletions.py",
            "max_lines": 2,
            "owner": "fixture",
            "reason": "Fixture hot-file authority.",
        }
    ]
    policy["compatibility_facades"] = [
        {
            "path": "app/artifact_lifecycle_repository.py",
            "canonical_prefix": "app.persistence",
            "max_lines": 8,
            "shape": "imports_only",
            "owner": "fixture",
            "reason": "Fixture facade authority.",
        }
    ]
    policy["production_registries"] = [
        {
            "path": "app/executors/registry.py",
            "factory_function": "_default_adapters",
            "allowed_keys": ["claude-agent-worker"],
            "adapter_constructors": [
                {
                    "module": "app.executors.claude_agent_worker",
                    "name": "ClaudeAgentWorkerAdapter",
                }
            ],
            "selector_owners": [
                {"path": "app/repositories.py", "symbol": "DEFAULT_RUN_EXECUTOR_TYPES"}
            ],
            "test_double_terms": ["dummy", "fake", "mock", "stub", "test"],
            "owner": "fixture",
            "reason": "Fixture registry authority.",
        }
    ]
    policy["governed_symbols"] = [
        {
            "name": name,
            "path": "app/persistence/object_deletions.py",
            "owner": "fixture",
            "reason": "Fixture governed symbol authority.",
        }
        for name in ("OUTBOX_TARGET_ARTIFACT", "OUTBOX_TARGET_FILE")
    ]
    _write(repo, "architecture-policy.json", json.dumps(policy, indent=2, sort_keys=True) + "\n")
    _write(
        repo,
        "schemas/architecture-policy.v1.schema.json",
        ARCHITECTURE_SCHEMA.read_text(encoding="utf-8"),
    )
    _write(repo, code_governance_test_path, CODE_GOVERNANCE_TEST.read_text(encoding="utf-8"))
    base = _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    return repo, base


@pytest.fixture
def readiness_repo(tmp_path: Path) -> tuple[Path, str]:
    return _create_readiness_repo(tmp_path, code_governance_test_path="tests/test_code_governance.py")


def test_readiness_fixture_materializes_every_governed_migration_source(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, authority = readiness_repo
    policy = json.loads(_git(repo, "show", f"{authority}:architecture-policy.json"))

    governed_sources = {
        item["source_path"]
        for key in ("migration_bridges", "legacy_api_cutovers")
        for item in policy[key]
    }
    for source_path in sorted(governed_sources):
        assert _run(
            repo,
            "git",
            "cat-file",
            "-e",
            f"{authority}:{source_path}",
            check=False,
        ).returncode == 0
        if Path(source_path).parent.as_posix() == "app":
            assert source_path in policy["approved_root_modules"]


def _check(
    repo: Path,
    base: str,
    head: str,
    *,
    output_format: str = "json",
    authority_ref: str | None = None,
    regression_test_suites: tuple[str, ...] = (),
    shared_test_suites: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    authority = authority_ref or _git(repo, "rev-parse", "refs/remotes/origin/main")
    temporary_root = Path(tempfile.mkdtemp(prefix="pre-push-readiness-test-authority-"))
    authority_worktree = temporary_root / "authority"
    _git(repo, "worktree", "add", "--detach", str(authority_worktree), authority)
    try:
        arguments = [
            sys.executable,
            "-P",
            str(authority_worktree / "tools" / "pre_push_readiness.py"),
            "check",
            "--authority-ref",
            authority,
            "--base-ref",
            base,
            "--head-ref",
            head,
            "--format",
            output_format,
        ]
        for suite in regression_test_suites:
            arguments.extend(("--regression-test-suite", suite))
        for suite in shared_test_suites:
            arguments.extend(("--shared-test-suite", suite))
        return _run(authority_worktree, *arguments, check=False, env=env)
    finally:
        _run(repo, "git", "worktree", "remove", "--force", str(authority_worktree), check=False)
        shutil.rmtree(temporary_root)


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def _governance_scope_sha256(repo: Path, base: str, head: str) -> str:
    scope = _run(
        repo,
        "git",
        "-c",
        "core.quotepath=false",
        "diff",
        "--raw",
        "--no-abbrev",
        "-z",
        "--no-renames",
        base,
        head,
        "--",
        ".",
        f":(exclude){EXCEPTION_PATH}",
    ).stdout
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()


def _governance_exception(
    *,
    reason: str,
    base_ref: str = "0" * 40,
    scope_sha256: str = "0" * 64,
) -> str:
    violations: list[dict[str, str | None]] = [{"code": "functional_hot_file_growth", "path": "app/billing.py"}]
    return json.dumps(
        {
            "schema_version": "ai-platform.code-governance-exception.v2",
            "candidate": {"base_ref": base_ref, "scope_sha256": scope_sha256},
            "expires_on": "2099-01-01",
            "owner": "platform-governance",
            "reason": reason,
            "violations": violations,
        }
    ) + "\n"


def _python_assignments(count: int) -> str:
    return "".join(f"VALUE_{index} = {index}\n" for index in range(count))


def _write_frontend_project(
    repo: Path,
    *,
    package_manager: str = FRONTEND_PACKAGE_MANAGER,
    include_lockfile: bool = True,
) -> None:
    _write(
        repo,
        "frontend/web/package.json",
        json.dumps(
            {
                "name": "readiness-frontend-fixture",
                "packageManager": package_manager,
                "scripts": {"ci:verify": "true"},
            }
        )
        + "\n",
    )
    if include_lockfile:
        _write(repo, "frontend/web/pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(repo, "frontend/web/src/App.test.tsx", "export const appTest = true;\n")


def _create_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (NotImplementedError, OSError) as link_error:
        if os.name != "nt":
            pytest.skip(f"directory link creation is unavailable on this platform: {link_error}")
    environment = os.environ.copy()
    environment["READINESS_JUNCTION_LINK"] = str(link)
    environment["READINESS_JUNCTION_TARGET"] = str(target)
    try:
        junction = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:READINESS_JUNCTION_LINK -Target $env:READINESS_JUNCTION_TARGET | Out-Null",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
    except OSError as junction_error:
        pytest.skip(f"directory link creation is unavailable on this Windows host: {junction_error}")
    if junction.returncode != 0:
        pytest.skip(f"directory link creation is unavailable on this Windows host: {junction.stderr or junction.stdout}")


def _exception_transition(
    repo: Path,
    *,
    operation: str,
    source: str,
    destination: str,
) -> tuple[str, str]:
    exception_at_head = operation == "copy" or destination == EXCEPTION_PATH
    exception = _governance_exception(
        reason=f"{operation} exception",
    )
    if exception_at_head:
        _write(repo, "app/billing.py", _python_assignments(3_001))
    _write(repo, source, exception)
    base = _commit(repo, f"{operation} exception baseline")
    if operation == "copy":
        _write(repo, destination, exception)
    else:
        (repo / destination).parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "mv", source, destination)
    if exception_at_head:
        _write(repo, "app/billing.py", _python_assignments(3_001) + "NEW_VALUE = 1\n")
        _write(repo, "tests/test_billing.py", "def test_billing():\n    assert True\n")
    scope_head = _commit(repo, f"{operation} exception transition scope")
    if exception_at_head:
        _write(
            repo,
            EXCEPTION_PATH,
            _governance_exception(
                reason=f"{operation} exception",
                base_ref=base,
                scope_sha256=_governance_scope_sha256(repo, base, scope_head),
            ),
        )
        head = _commit(repo, f"bind {operation} exception transition")
    else:
        head = scope_head
    return base, head


def _fake_corepack_environment(
    tmp_path: Path,
    *,
    reported_version: str = "10.32.1",
    version_returncode: int = 0,
    install_returncode: int = 0,
    verify_returncode: int = 0,
    require_fresh_node_modules: bool = False,
    expected_corepack_home: str | None = None,
) -> dict[str, str]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "fake-corepack.log"
    if os.name == "nt":
        (fake_bin / "corepack.cmd").write_text(
            "@echo off\r\n"
            "if not \"%FAKE_COREPACK_LOG%\"==\"\" echo %*>>\"%FAKE_COREPACK_LOG%\"\r\n"
            "if not \"%FAKE_COREPACK_EXPECT_COREPACK_HOME%\"==\"\" if not \"%COREPACK_HOME%\"==\"%FAKE_COREPACK_EXPECT_COREPACK_HOME%\" exit /b 98\r\n"
            "if \"%2\"==\"--version\" (\r\n"
            "  echo %FAKE_PNPM_VERSION%\r\n"
            "  exit /b %FAKE_COREPACK_VERSION_EXIT%\r\n"
            ")\r\n"
            "if \"%2\"==\"install\" (\r\n"
            "  if not \"%FAKE_COREPACK_INSTALL_EXIT%\"==\"0\" exit /b %FAKE_COREPACK_INSTALL_EXIT%\r\n"
            "  if \"%FAKE_COREPACK_REQUIRE_FRESH_NODE_MODULES%\"==\"1\" if exist node_modules exit /b 97\r\n"
            "  if not exist node_modules mkdir node_modules\r\n"
            "  exit /b 0\r\n"
            ")\r\n"
            "if \"%2\"==\"run\" exit /b %FAKE_COREPACK_VERIFY_EXIT%\r\n"
            "exit /b 0\r\n",
            encoding="utf-8",
        )
    else:
        corepack = fake_bin / "corepack"
        corepack.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_COREPACK_LOG\"\n"
            "if [ -n \"$FAKE_COREPACK_EXPECT_COREPACK_HOME\" ] && [ \"$COREPACK_HOME\" != \"$FAKE_COREPACK_EXPECT_COREPACK_HOME\" ]; then exit 98; fi\n"
            "if [ \"$2\" = \"--version\" ]; then\n"
            "  printf '%s\\n' \"$FAKE_PNPM_VERSION\"\n"
            "  exit \"$FAKE_COREPACK_VERSION_EXIT\"\n"
            "fi\n"
            "if [ \"$2\" = \"install\" ]; then\n"
            "  if [ \"$FAKE_COREPACK_INSTALL_EXIT\" != \"0\" ]; then exit \"$FAKE_COREPACK_INSTALL_EXIT\"; fi\n"
            "  if [ \"$FAKE_COREPACK_REQUIRE_FRESH_NODE_MODULES\" = \"1\" ] && [ -e node_modules ]; then exit 97; fi\n"
            "  mkdir -p node_modules\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$2\" = \"run\" ]; then exit \"$FAKE_COREPACK_VERIFY_EXIT\"; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        corepack.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    environment["FAKE_COREPACK_LOG"] = str(log)
    environment["FAKE_PNPM_VERSION"] = reported_version
    environment["FAKE_COREPACK_VERSION_EXIT"] = str(version_returncode)
    environment["FAKE_COREPACK_INSTALL_EXIT"] = str(install_returncode)
    environment["FAKE_COREPACK_VERIFY_EXIT"] = str(verify_returncode)
    environment["FAKE_COREPACK_REQUIRE_FRESH_NODE_MODULES"] = "1" if require_fresh_node_modules else "0"
    if expected_corepack_home is not None:
        environment["COREPACK_HOME"] = expected_corepack_home
        environment["FAKE_COREPACK_EXPECT_COREPACK_HOME"] = expected_corepack_home
    return environment


def _readiness_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pre_push_readiness_cleanup_test", READINESS_TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("returncode", "code", "infrastructure_codes", "expected"),
    (
        (3, "git_failed", frozenset({"git_failed"}), "infrastructure_failure"),
        (3, "git_output_invalid", frozenset({"git_output_invalid"}), "infrastructure_failure"),
        (3, "invalid_exception", frozenset({"git_failed"}), "governance_violation"),
        (2, "git_failed", frozenset({"git_failed"}), "governance_violation"),
    ),
)
def test_governance_failure_taxonomy_preserves_infrastructure_errors(
    returncode: int,
    code: str,
    infrastructure_codes: frozenset[str],
    expected: str,
) -> None:
    module = _readiness_module()

    assert (
        module._governance_failure_category(
            returncode,
            code,
            infrastructure_codes=infrastructure_codes,
        )
        == expected
    )


class _CleanupRunner:
    def __init__(self, module: ModuleType, *, remove_returncode: int = 0) -> None:
        self.module = module
        self.remove_returncode = remove_returncode
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None) -> object:
        del cwd, env
        self.commands.append(command)
        if command[:5] == ("git", "-c", "core.longpaths=true", "worktree", "remove"):
            return self.module._CommandResult(self.remove_returncode, "", "remove failed")
        return self.module._CommandResult(0, "", "")


class _RegistrationResidueCleanupRunner(_CleanupRunner):
    def __init__(self, module: ModuleType, registered_path: Path) -> None:
        super().__init__(module, remove_returncode=1)
        self.registered_path = registered_path

    def run(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None) -> object:
        if command == ("git", "worktree", "list", "--porcelain"):
            return self.module._CommandResult(0, f"worktree {self.registered_path}\n", "")
        return super().run(command, cwd=cwd, env=env)


class _WorktreeListFailureCleanupRunner(_CleanupRunner):
    def run(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None) -> object:
        if command == ("git", "worktree", "list", "--porcelain"):
            return self.module._CommandResult(1, "", "list failed")
        return super().run(command, cwd=cwd, env=env)


class _DependencyCleanupRunner(_CleanupRunner):
    def __init__(self, module: ModuleType, dependencies: tuple[Path, ...], *, remove_returncode: int = 0) -> None:
        super().__init__(module, remove_returncode=remove_returncode)
        self.dependencies = dependencies
        self.dependencies_present_before_worktree_remove: list[bool] = []

    def run(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None) -> object:
        if command[:5] == ("git", "-c", "core.longpaths=true", "worktree", "remove"):
            self.dependencies_present_before_worktree_remove.extend(os.path.lexists(path) for path in self.dependencies)
        return super().run(command, cwd=cwd, env=env)


class _AncestryCleanupRunner(_CleanupRunner):
    def __init__(self, module: ModuleType, unsafe_parent: Path) -> None:
        super().__init__(module)
        self.unsafe_parent = unsafe_parent
        self.unsafe_parent_present_before_worktree_remove: list[bool] = []

    def run(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None) -> object:
        if command[:5] == ("git", "-c", "core.longpaths=true", "worktree", "remove"):
            self.unsafe_parent_present_before_worktree_remove.append(os.path.lexists(self.unsafe_parent))
        return super().run(command, cwd=cwd, env=env)


def test_worktree_cleanup_records_successful_remove_and_absent_registration(tmp_path: Path) -> None:
    module = _readiness_module()
    runner = _CleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    base = temporary_root / "base"
    head.mkdir(parents=True)
    base.mkdir()
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(result, temporary_root, (("head", head, True), ("base", base, True)))

    assert failure is None
    cleanup = result["stages"][-1]
    assert cleanup["name"] == "worktree_cleanup"
    assert cleanup["status"] == "pass"
    assert all(record["remove_returncode"] == 0 for record in cleanup["worktrees"])
    assert all(record["registered_after"] is False for record in cleanup["worktrees"])
    assert temporary_root.exists() is False


def test_disposable_worktree_commands_enable_windows_long_path_handling(tmp_path: Path) -> None:
    module = _readiness_module()
    runner = _CleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)

    readiness._add_worktree(tmp_path / "head", "a" * 40)

    assert runner.commands == [
        ("git", "-c", "core.longpaths=true", "worktree", "add", "--detach", str(tmp_path / "head"), "a" * 40)
    ]


def test_temporary_root_uses_verified_profile_instead_of_deep_windows_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _readiness_module()
    readiness = module.PrePushReadiness(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    deep_temp = tmp_path.joinpath(*(["detached-worktree-depth"] * 10))
    assert len(str(deep_temp)) + module.WINDOWS_LONGEST_SKILL_RELATIVE_SUFFIX_LENGTH > 240
    created = profile / "apr-owned"
    verified: list[Path] = []

    def create_root(*, prefix: str, dir: str | Path) -> str:
        assert prefix == module.TEMPORARY_ROOT_PREFIX
        assert Path(dir) == profile
        created.mkdir()
        return str(created)

    monkeypatch.setattr(module, "IS_WINDOWS", True)
    monkeypatch.setattr(module.Path, "home", lambda: profile)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(deep_temp))
    monkeypatch.setattr(module.tempfile, "mkdtemp", create_root)
    monkeypatch.setattr(module, "_assert_windows_nonreparse_directory", verified.append)
    monkeypatch.setattr(module, "_temporary_root_has_windows_headroom", lambda path: True)
    temporary_root = readiness._create_temporary_root()
    try:
        base, head = module._temporary_worktree_paths(temporary_root)

        assert temporary_root == created
        assert verified == [profile, created]
        assert base.parent == temporary_root
        assert head.parent == temporary_root
        assert base.name == "base"
        assert head.name == "head"
        assert module._temporary_root_has_windows_headroom(temporary_root)
        assert (
            len(str(Path("C:/Users/current-user/apr-owned")))
            + module.WINDOWS_LONGEST_SKILL_RELATIVE_SUFFIX_LENGTH
            + module.WINDOWS_DIRECTORY_PATH_HEADROOM
            <= module.WINDOWS_CONSERVATIVE_DIRECTORY_PATH_BUDGET
        )
    finally:
        if os.path.lexists(temporary_root):
            module._remove_cleanup_tree(temporary_root)


def test_windows_temporary_root_rejects_a_reparse_profile_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _readiness_module()
    profile = tmp_path / "reparse-profile"
    created: list[Path] = []

    monkeypatch.setattr(module, "IS_WINDOWS", True)
    monkeypatch.setattr(module.Path, "home", lambda: profile)
    monkeypatch.setattr(
        module,
        "_assert_windows_nonreparse_directory",
        lambda path: (_ for _ in ()).throw(OSError("profile is a reparse point")),
    )
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kwargs: created.append(profile / "apr-never"))

    with pytest.raises(module.ReadinessError) as raised:
        module.PrePushReadiness(tmp_path)._create_temporary_root()

    assert raised.value.code == "temporary_directory_failed"
    assert created == []


def test_temporary_root_over_budget_fails_and_cleans_only_the_owned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _readiness_module()
    readiness = module.PrePushReadiness(tmp_path)
    temporary_root = tmp_path / "apr-over-budget"
    temporary_root.mkdir()
    sentinel = tmp_path / "outside-owned-root.txt"
    sentinel.write_text("must survive temporary-root cleanup", encoding="utf-8")
    prefixes: list[str] = []

    def create_over_budget_root(*, prefix: str, dir: str | Path) -> str:
        prefixes.append(prefix)
        assert Path(dir) == Path(tempfile.gettempdir())
        return str(temporary_root)

    monkeypatch.setattr(module, "IS_WINDOWS", False)
    monkeypatch.setattr(module.tempfile, "mkdtemp", create_over_budget_root)
    try:
        with pytest.raises(module.ReadinessError) as raised:
            readiness._create_temporary_root()
    finally:
        if os.path.lexists(temporary_root):
            module._remove_cleanup_tree(temporary_root)

    assert prefixes == [module.TEMPORARY_ROOT_PREFIX]
    assert raised.value.category == "infrastructure_failure"
    assert raised.value.code == "temporary_directory_path_too_long"
    assert not os.path.lexists(temporary_root)
    assert sentinel.read_text(encoding="utf-8") == "must survive temporary-root cleanup"


def _windows_removal_error(winerror: int) -> OSError:
    error = OSError(f"Windows removal failed with {winerror}")
    setattr(error, "winerror", winerror)
    return error


def test_windows_cleanup_retry_window_uses_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _readiness_module()
    retry = module._WindowsDirectoryRemovalRetry()
    times = iter((10.0, 11.01))
    delays: list[float] = []
    error = _windows_removal_error(module.WINDOWS_ERROR_DIRECTORY_NOT_EMPTY)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(module.time, "sleep", delays.append)

    assert retry.wait(error)
    assert not retry.wait(error)
    assert retry.attempts == 1
    assert delays == [0.05]


def test_windows_cleanup_retries_transient_directory_not_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _readiness_module()
    target = tmp_path / "transient"
    target.mkdir()
    real_rmdir = module.os.rmdir
    calls: list[Path] = []
    delays: list[float] = []

    def transient_rmdir(path: str) -> None:
        calls.append(Path(path))
        if len(calls) <= 2:
            raise _windows_removal_error(module.WINDOWS_ERROR_DIRECTORY_NOT_EMPTY)
        real_rmdir(path)

    monkeypatch.setattr(module.os, "rmdir", transient_rmdir)
    monkeypatch.setattr(module.time, "sleep", delays.append)

    module._remove_windows_cleanup_tree(str(target))

    assert calls == [target, target, target]
    assert delays == [0.05, 0.1]
    assert not target.exists()


def test_windows_cleanup_stops_after_directory_not_empty_retry_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _readiness_module()
    target = tmp_path / "persistent"
    target.mkdir()
    readiness = module.PrePushReadiness(tmp_path)
    result = module._new_result(None, None, None)
    calls: list[Path] = []
    delays: list[float] = []

    def persistent_rmdir(path: str) -> None:
        calls.append(Path(path))
        raise _windows_removal_error(module.WINDOWS_ERROR_DIRECTORY_NOT_EMPTY)

    monkeypatch.setattr(module.os, "rmdir", persistent_rmdir)
    monkeypatch.setattr(module.time, "sleep", delays.append)
    monkeypatch.setattr(
        module,
        "_remove_cleanup_tree",
        lambda path: module._remove_windows_cleanup_tree(str(path)),
    )

    failure = readiness._cleanup_worktrees(result, target, ())

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    assert "temporary worktree directory removal failed" in str(failure)
    assert calls == [target] * (module.WINDOWS_DIRECTORY_REMOVE_RETRY_LIMIT + 1)
    assert delays == [0.05, 0.1, 0.2, 0.4]
    assert target.exists()


def test_windows_cleanup_does_not_retry_other_removal_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _readiness_module()
    target = tmp_path / "denied"
    target.mkdir()
    calls: list[Path] = []

    def denied_rmdir(path: str) -> None:
        calls.append(Path(path))
        raise _windows_removal_error(5)

    monkeypatch.setattr(module.os, "rmdir", denied_rmdir)
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda delay: pytest.fail(f"unexpected retry delay: {delay}"),
    )

    with pytest.raises(OSError) as raised:
        module._remove_windows_cleanup_tree(str(target))

    assert getattr(raised.value, "winerror", None) == 5
    assert calls == [target]
    assert target.exists()


def test_worktree_cleanup_removes_detached_frontend_node_modules_and_normalizes_windows_separators(tmp_path: Path) -> None:
    module = _readiness_module()
    runner = _CleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    node_modules = temporary_root / "head" / "frontend" / "web" / "node_modules"
    node_modules.mkdir(parents=True)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(
        result,
        temporary_root,
        (("head", head, True),),
        frontend_dependencies=(
            ("node_modules", node_modules),
        ),
    )

    assert failure is None
    cleanup = result["stages"][-1]
    assert cleanup["status"] == "pass"
    assert all(resource["exists_after"] is False for resource in cleanup["frontend_dependencies"])
    assert module._same_worktree_path(node_modules, str(node_modules).replace("\\", "/"))


def test_worktree_cleanup_removes_nested_long_dependency_tree_before_worktree_remove(tmp_path: Path) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    node_modules = head / "frontend" / "web" / "node_modules"
    nested = node_modules / ".pnpm" / ("dependency-" + "a" * 100) / ("package-" + "b" * 100)
    os.makedirs(module._windows_extended_path(nested), exist_ok=True)
    sentinel = os.path.join(module._windows_extended_path(nested), "sandpack-client.js")
    with open(sentinel, "w", encoding="utf-8") as handle:
        handle.write("candidate-local dependency\n")
    runner = _DependencyCleanupRunner(module, (node_modules,))
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(
        result,
        temporary_root,
        (("head", head, True),),
        frontend_dependencies=(("node_modules", node_modules),),
    )

    assert failure is None
    assert runner.dependencies_present_before_worktree_remove == [False]
    assert not os.path.lexists(node_modules)
    assert not temporary_root.exists()
    cleanup = result["stages"][-1]
    assert cleanup["status"] == "pass"
    assert cleanup["worktrees"][0]["registered_after"] is False


def test_worktree_cleanup_surfaces_dependency_removal_error_after_root_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    node_modules = head / "frontend" / "web" / "node_modules"
    node_modules.mkdir(parents=True)
    runner = _CleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)
    original_remove = module._remove_cleanup_tree

    def fail_dependency_only(path: Path) -> None:
        if path == node_modules:
            raise OSError("locked candidate dependency tree")
        original_remove(path)

    monkeypatch.setattr(module, "_remove_cleanup_tree", fail_dependency_only)

    failure = readiness._cleanup_worktrees(
        result,
        temporary_root,
        (("head", head, True),),
        frontend_dependencies=(("node_modules", node_modules),),
    )

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    assert "frontend dependency path removal failed" in str(failure)
    assert not temporary_root.exists()
    cleanup = result["stages"][-1]
    assert cleanup["status"] == "failed"
    assert cleanup["worktrees"][0]["registered_after"] is False


def test_worktree_cleanup_refuses_intermediate_candidate_link_and_preserves_external_sentinel(tmp_path: Path) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    external = tmp_path / "external"
    external_node_modules = external / "web" / "node_modules"
    external_sentinel = external_node_modules / "outside-sentinel.txt"
    external_sentinel.parent.mkdir(parents=True)
    external_sentinel.write_text("must survive cleanup", encoding="utf-8")
    head.mkdir(parents=True)
    unsafe_parent = head / "frontend"
    _create_directory_link(unsafe_parent, external)
    node_modules = unsafe_parent / "web" / "node_modules"
    runner = _AncestryCleanupRunner(module, unsafe_parent)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(
        result,
        temporary_root,
        (("head", head, True),),
        frontend_dependencies=(("node_modules", node_modules),),
    )

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    assert external_sentinel.read_text(encoding="utf-8") == "must survive cleanup"
    assert runner.unsafe_parent_present_before_worktree_remove == [False]
    assert not temporary_root.exists()
    cleanup = result["stages"][-1]
    assert cleanup["status"] == "failed"
    assert "ancestor_error" in cleanup["frontend_dependencies"][0]
    assert cleanup["worktrees"][0]["registered_after"] is False


def test_worktree_cleanup_skips_git_remove_when_unsafe_ancestor_cannot_be_unlinked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    external = tmp_path / "external"
    external_node_modules = external / "web" / "node_modules"
    external_sentinel = external_node_modules / "outside-sentinel.txt"
    external_sentinel.parent.mkdir(parents=True)
    external_sentinel.write_text("must survive cleanup", encoding="utf-8")
    head.mkdir(parents=True)
    unsafe_parent = head / "frontend"
    _create_directory_link(unsafe_parent, external)
    node_modules = unsafe_parent / "web" / "node_modules"
    runner = _CleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)

    def refuse_link_removal(path: Path) -> None:
        assert path == unsafe_parent
        raise OSError("candidate link is locked")

    monkeypatch.setattr(module, "_remove_cleanup_link", refuse_link_removal)

    failure = readiness._cleanup_worktrees(
        result,
        temporary_root,
        (("head", head, True),),
        frontend_dependencies=(("node_modules", node_modules),),
    )

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    assert external_sentinel.read_text(encoding="utf-8") == "must survive cleanup"
    assert not any(command[:5] == ("git", "-c", "core.longpaths=true", "worktree", "remove") for command in runner.commands)
    assert not temporary_root.exists()
    cleanup = result["stages"][-1]
    assert cleanup["worktrees"][0]["remove_skipped"] == "unsafe dependency ancestor remains"
    assert cleanup["worktrees"][0]["registered_after"] is False


def test_worktree_cleanup_rejects_dependency_target_outside_generated_head(tmp_path: Path) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    head.mkdir(parents=True)
    external_node_modules = tmp_path / "external" / "node_modules"
    external_sentinel = external_node_modules / "outside-sentinel.txt"
    external_sentinel.parent.mkdir(parents=True)
    external_sentinel.write_text("must survive cleanup", encoding="utf-8")
    runner = _CleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(
        result,
        temporary_root,
        (("head", head, True),),
        frontend_dependencies=(("node_modules", external_node_modules),),
    )

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    assert external_sentinel.read_text(encoding="utf-8") == "must survive cleanup"
    assert not temporary_root.exists()
    assert result["stages"][-1]["status"] == "failed"


def test_worktree_cleanup_recovers_after_nonzero_remove_when_final_postconditions_are_absent(tmp_path: Path) -> None:
    module = _readiness_module()
    runner = _CleanupRunner(module, remove_returncode=1)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    head.mkdir(parents=True)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(result, temporary_root, (("head", head, True),))

    assert failure is None
    cleanup = result["stages"][-1]
    assert cleanup["status"] == "pass"
    assert cleanup["worktrees"][0]["remove_returncode"] == 1
    assert cleanup["worktrees"][0]["remove_diagnostic"] == "git worktree remove head failed: remove failed"
    assert cleanup["worktrees"][0]["registered_after"] is False
    assert cleanup["worktrees"][0]["path_exists_after"] is False


def test_worktree_cleanup_nonzero_remove_fails_when_registration_remains(tmp_path: Path) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    head.mkdir(parents=True)
    runner = _RegistrationResidueCleanupRunner(module, head)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(result, temporary_root, (("head", head, True),))

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    cleanup = result["stages"][-1]
    assert cleanup["worktrees"][0]["remove_returncode"] == 1
    assert cleanup["worktrees"][0]["registered_after"] is True
    assert cleanup["worktrees"][0]["path_exists_after"] is False


def test_worktree_cleanup_nonzero_remove_fails_when_owned_path_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    head.mkdir(parents=True)
    runner = _CleanupRunner(module, remove_returncode=1)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)
    original_remove = module._remove_cleanup_tree

    def retain_owned_root(path: Path) -> None:
        if path != temporary_root:
            original_remove(path)

    monkeypatch.setattr(module, "_remove_cleanup_tree", retain_owned_root)
    try:
        failure = readiness._cleanup_worktrees(result, temporary_root, (("head", head, True),))
    finally:
        original_remove(temporary_root)

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    cleanup = result["stages"][-1]
    assert cleanup["worktrees"][0]["remove_returncode"] == 1
    assert cleanup["worktrees"][0]["registered_after"] is False
    assert cleanup["worktrees"][0]["path_exists_after"] is True


def test_worktree_cleanup_fails_closed_when_post_cleanup_registration_check_fails(tmp_path: Path) -> None:
    module = _readiness_module()
    temporary_root = tmp_path / "temporary"
    head = temporary_root / "head"
    head.mkdir(parents=True)
    runner = _WorktreeListFailureCleanupRunner(module)
    readiness = module.PrePushReadiness(tmp_path, runner=runner)
    result = module._new_result(None, None, None)

    failure = readiness._cleanup_worktrees(result, temporary_root, (("head", head, True),))

    assert failure is not None
    assert failure.category == "infrastructure_failure"
    assert failure.code == "worktree_cleanup_failed"
    assert str(failure) == "git worktree list failed: list failed"
    cleanup = result["stages"][-1]
    assert cleanup["status"] == "failed"
    assert cleanup["worktrees"][0]["registered_after"] is None
    assert cleanup["worktrees"][0]["path_exists_after"] is False
    assert cleanup["worktrees"][0]["worktree_list_error"] == "git worktree list failed: list failed"


def test_primary_product_failure_is_preserved_when_cleanup_also_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _readiness_module()
    readiness = module.PrePushReadiness(tmp_path)
    temporary_root = tmp_path / "temporary"
    primary = module.ReadinessError("product_test_failure", "pytest_failed", "deterministic test failed")
    cleanup = module.ReadinessError("infrastructure_failure", "worktree_cleanup_failed", "cleanup failed")

    monkeypatch.setattr(readiness, "_assert_repository", lambda: None)
    monkeypatch.setattr(readiness, "_resolve_full_commit", lambda value, label: value)
    monkeypatch.setattr(readiness, "_assert_accepted_authority", lambda authority: None)
    monkeypatch.setattr(readiness, "_assert_authority_provenance", lambda authority: None)
    monkeypatch.setattr(readiness, "_assert_ancestor", lambda base, head: None)
    monkeypatch.setattr(readiness, "_create_temporary_root", lambda: temporary_root)
    monkeypatch.setattr(readiness, "_add_worktree", lambda path, commit: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(readiness, "_run_diff_check", lambda result, base, head: (_ for _ in ()).throw(primary))
    monkeypatch.setattr(readiness, "_cleanup_worktrees", lambda result, root, worktrees, **kwargs: cleanup)

    with pytest.raises(module.ReadinessError) as raised:
        readiness.check("a" * 40, "b" * 40, "c" * 40)

    assert raised.value is primary
    assert raised.value.category == "product_test_failure"
    assert raised.value.cleanup_failure is cleanup


def test_authority_worktree_never_executes_a_candidate_tool_replacement(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    marker = tmp_path / "candidate-tool-executed.txt"
    _write(
        repo,
        "tools/pre_push_readiness.py",
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "raise RuntimeError('candidate tool executed')\n",
    )
    _write(repo, "tests/test_pre_push_readiness.py", "def test_pre_push_readiness():\n    assert True\n")
    head = _commit(repo, "replace candidate readiness tool")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    assert marker.exists() is False
    assert payload["authority_ref"] == base
    assert payload["authority"]["status"] == "verified"


def test_trusted_architecture_rejects_a_new_app_root_before_candidate_tests(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(repo, "app/new_root.py", "VALUE = 1\n")
    _write(repo, "tests/test_new_root.py", "def test_new_root():\n    assert True\n")
    head = _commit(repo, "add unowned app root")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "governance_violation"
    assert payload["failure"] == {
        "code": "unapproved_app_root_module",
        "message": "new app-root modules require an explicit architecture-policy owner",
        "path": "app/new_root.py",
        "test_identity": None,
    }
    architecture = next(
        stage for stage in payload["stages"] if stage["name"] == "architecture_governance"
    )
    assert architecture["status"] == "failed"
    assert Path(architecture["command"][2]).name == "authority-architecture.py"
    assert architecture["command"][4:10] == [
        "--authority-ref",
        base,
        "--base-ref",
        base,
        "--head-ref",
        head,
    ]
    assert architecture["policy"]["path"] == "architecture-policy.json"
    assert architecture["exception"]["status"] == "absent"
    assert architecture["exempted_findings"] == []
    assert all(stage["name"] != "responsibility_tests" for stage in payload["stages"])


def test_pre_push_accepts_exact_one_shot_legacy_api_cutover(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    policy = json.loads(_git(repo, "show", f"{base}:architecture-policy.json"))
    cutover = policy["legacy_api_cutovers"][0]
    source_path = repo / cutover["source_path"]
    source = source_path.read_text(encoding="utf-8")
    for removed in cutover["removed_imports"]:
        source = source.replace(
            f"from {removed['module']} import {removed['name']}\n",
            "",
            1,
        )
    for rewrite in cutover["rewrites"]:
        old = rewrite["old_symbol"]
        if old.isupper():
            definition = f"{old} = {{'succeeded'}}\n\n"
        elif old[:1].isupper():
            definition = f"class {old}:\n    pass\n\n"
        else:
            definition = f"def {old}(value=None):\n    return value\n\n"
        source = source.replace(definition, "", 1)
        source = source.replace(
            old,
            f"{cutover['module_alias']}.{rewrite['new_symbol']}",
        )
    source_path.write_text(
        f"import {cutover['public_module']} as {cutover['module_alias']}\n" + source,
        encoding="utf-8",
    )
    target_symbols = sorted(rewrite["new_symbol"] for rewrite in cutover["rewrites"])
    _write(
        repo,
        f"{cutover['canonical_module'].replace('.', '/')}.py",
        "\n\n".join(
            f"def {name}(value=None):\n    return value"
            if not name.isupper() and not name[:1].isupper()
            else (
                f"{name} = {{'succeeded'}}"
                if name.isupper()
                else f"class {name}:\n    pass"
            )
            for name in target_symbols
        )
        + "\n",
    )
    _write(
        repo,
        f"{cutover['public_module'].replace('.', '/')}.py",
        f"from {cutover['canonical_module']} import "
        + ", ".join(f"{name} as {name}" for name in target_symbols)
        + "\n",
    )
    _write(repo, "tests/test_runs_cutover.py", "def test_runs_cutover():\n    assert True\n")
    head = _commit(repo, "exact public API hard cut")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    architecture = next(
        stage for stage in payload["stages"] if stage["name"] == "architecture_governance"
    )
    assert architecture["status"] == "pass"


def test_authority_architecture_snapshot_never_executes_a_candidate_replacement(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    marker = tmp_path / "candidate-architecture-executed.txt"
    _write(
        repo,
        "tools/architecture_governance.py",
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "raise RuntimeError('candidate architecture executed')\n",
    )
    _write(
        repo,
        "tests/test_architecture_governance.py",
        "def test_candidate_architecture_change():\n    assert True\n",
    )
    head = _commit(repo, "replace candidate architecture checker")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    assert marker.exists() is False
    architecture = next(
        stage for stage in payload["stages"] if stage["name"] == "architecture_governance"
    )
    assert architecture["status"] == "pass"
    assert Path(architecture["command"][2]).name == "authority-architecture.py"
    assert payload["authority"]["architecture_governance"] == "sealed"


@pytest.mark.parametrize(
    "authority_path",
    (
        "tools/architecture_governance.py",
        "architecture-policy.json",
        "schemas/architecture-policy.v1.schema.json",
    ),
)
def test_candidate_cannot_tamper_with_architecture_authority_after_it_is_sealed(
    readiness_repo: tuple[Path, str], authority_path: str
) -> None:
    repo, base = readiness_repo
    _write(
        repo,
        "tests/test_architecture_authority_tamper.py",
        "import subprocess\n"
        "from pathlib import Path\n\n\n"
        "def test_tamper_architecture_authority():\n"
        "    worktrees = subprocess.check_output(['git', 'worktree', 'list', '--porcelain'], text=True)\n"
        "    authority = next(\n"
        "        Path(line.removeprefix('worktree '))\n"
        "        for line in worktrees.splitlines()\n"
        "        if line.startswith('worktree ') and Path(line.removeprefix('worktree ')).name == 'authority'\n"
        "    )\n"
        f"    target = authority / {authority_path!r}\n"
        "    target.write_text('candidate tamper\\n', encoding='utf-8')\n",
    )
    head = _commit(repo, f"tamper {authority_path} from candidate test")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "authority_post_candidate_integrity_mismatch"
    architecture = next(
        stage for stage in payload["stages"] if stage["name"] == "architecture_governance"
    )
    tests = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    integrity = next(stage for stage in payload["stages"] if stage["name"] == "authority_integrity")
    assert architecture["status"] == "pass"
    assert payload["stages"].index(architecture) < payload["stages"].index(tests)
    assert integrity["status"] == "failed"


def test_candidate_authority_governance_tamper_cannot_change_the_sealed_result(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(
        repo,
        "tests/test_authority_tamper.py",
        "import subprocess\n"
        "from pathlib import Path\n\n\n"
        "def test_tamper_authority_governance():\n"
        "    worktrees = subprocess.check_output(['git', 'worktree', 'list', '--porcelain'], text=True)\n"
        "    authority = next(\n"
        "        Path(line.removeprefix('worktree '))\n"
        "        for line in worktrees.splitlines()\n"
        "        if line.startswith('worktree ') and Path(line.removeprefix('worktree ')).name == 'authority'\n"
        "    )\n"
        "    (authority / 'tools' / 'code_governance.py').write_text(\n"
        "        \"raise RuntimeError('candidate changed authority governance')\\n\", encoding='utf-8'\n"
        "    )\n",
    )
    head = _commit(repo, "tamper authority governance from candidate test")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "authority_post_candidate_integrity_mismatch"
    governance_index = next(index for index, stage in enumerate(payload["stages"]) if stage["name"] == "governance")
    tests_index = next(index for index, stage in enumerate(payload["stages"]) if stage["name"] == "responsibility_tests")
    assert governance_index < tests_index
    governance_stage = payload["stages"][governance_index]
    assert governance_stage["status"] == "pass"
    assert Path(governance_stage["command"][2]).name == "authority-governance.py"
    assert next(stage for stage in payload["stages"] if stage["name"] == "authority_integrity")["status"] == "failed"


def test_frontend_typescript_change_runs_the_repository_native_frontend_suite(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write_frontend_project(repo)
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    _write(repo, "frontend/web/src/App.test.tsx", "export const appTest = true;\n")
    head = _commit(repo, "frontend responsibility")

    environment = _fake_corepack_environment(tmp_path)
    result = _check(repo, base, head, env=environment)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    bootstrap_stage = next(stage for stage in payload["stages"] if stage["name"] == "frontend_dependency_bootstrap")
    assert bootstrap_stage["status"] == "pass"
    assert bootstrap_stage["package_manager"] == FRONTEND_PACKAGE_MANAGER
    assert bootstrap_stage["lockfile"] == "frontend/web/pnpm-lock.yaml"
    assert "--frozen-lockfile" in bootstrap_stage["command"]
    assert "--prefer-offline" in bootstrap_stage["command"]
    assert "--store-dir" not in bootstrap_stage["command"]
    assert bootstrap_stage["dependency_store"] == "host_content_addressed"
    frontend_stage = next(stage for stage in payload["stages"] if stage["name"] == "frontend_responsibility")
    assert frontend_stage["command"] == [
        "corepack.cmd" if os.name == "nt" else "corepack",
        FRONTEND_PACKAGE_MANAGER,
        "run",
        "ci:verify",
    ]
    cleanup_stage = next(stage for stage in payload["stages"] if stage["name"] == "worktree_cleanup")
    assert all(resource["exists_after"] is False for resource in cleanup_stage["frontend_dependencies"])
    commands = Path(environment["FAKE_COREPACK_LOG"]).read_text(encoding="utf-8")
    assert f"{FRONTEND_PACKAGE_MANAGER} --version" in commands
    assert f"{FRONTEND_PACKAGE_MANAGER} install" in commands
    assert f"{FRONTEND_PACKAGE_MANAGER} run ci:verify" in commands


def test_frontend_bootstrap_does_not_reuse_source_worktree_node_modules(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write_frontend_project(repo)
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    head = _commit(repo, "frontend bootstrap uses detached modules")
    source_sentinel = repo / "frontend" / "web" / "node_modules" / "source-only-sentinel.txt"
    source_sentinel.parent.mkdir(parents=True)
    source_sentinel.write_text("must not be linked", encoding="utf-8")

    host_corepack_home = str(tmp_path / "host-corepack-cache")
    result = _check(
        repo,
        base,
        head,
        env=_fake_corepack_environment(
            tmp_path,
            require_fresh_node_modules=True,
            expected_corepack_home=host_corepack_home,
        ),
    )
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    assert source_sentinel.read_text(encoding="utf-8") == "must not be linked"
    bootstrap_stage = next(stage for stage in payload["stages"] if stage["name"] == "frontend_dependency_bootstrap")
    assert "--store-dir" not in bootstrap_stage["command"]
    assert bootstrap_stage["dependency_store"] == "host_content_addressed"


def test_frontend_dependency_bootstrap_requires_a_lockfile(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write_frontend_project(repo, include_lockfile=False)
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    head = _commit(repo, "frontend without lockfile")

    result = _check(repo, base, head, env=_fake_corepack_environment(tmp_path))
    payload = _payload(result)

    assert result.returncode == 3, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "infrastructure_failure"
    assert payload["failure"]["code"] == "frontend_dependency_metadata_missing"
    assert payload["failure"]["path"] == "frontend/web/pnpm-lock.yaml"
    assert next(stage for stage in payload["stages"] if stage["name"] == "frontend_dependency_bootstrap")["status"] == "failed"
    assert all(stage["name"] != "frontend_responsibility" for stage in payload["stages"])


def test_frontend_dependency_bootstrap_requires_a_pinned_package_manager(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write(repo, "frontend/web/package.json", json.dumps({"scripts": {"ci:verify": "true"}}) + "\n")
    _write(repo, "frontend/web/pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    _write(repo, "frontend/web/src/App.test.tsx", "export const appTest = true;\n")
    head = _commit(repo, "frontend without package manager pin")

    result = _check(repo, base, head, env=_fake_corepack_environment(tmp_path))
    payload = _payload(result)

    assert result.returncode == 3, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "infrastructure_failure"
    assert payload["failure"]["code"] == "frontend_dependency_metadata_missing"
    assert payload["failure"]["path"] == "frontend/web/package.json"
    assert all(stage["name"] != "frontend_responsibility" for stage in payload["stages"])


def test_frontend_dependency_bootstrap_rejects_a_package_manager_version_mismatch(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write_frontend_project(repo)
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    head = _commit(repo, "frontend mismatched package manager")
    environment = _fake_corepack_environment(tmp_path, reported_version="10.31.0")

    result = _check(repo, base, head, env=environment)
    payload = _payload(result)

    assert result.returncode == 3, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "infrastructure_failure"
    assert payload["failure"]["code"] == "frontend_dependency_provenance_mismatch"
    assert payload["failure"]["path"] == "frontend/web/package.json"
    commands = Path(environment["FAKE_COREPACK_LOG"]).read_text(encoding="utf-8")
    assert f"{FRONTEND_PACKAGE_MANAGER} --version" in commands
    assert " install" not in commands
    assert all(stage["name"] != "frontend_responsibility" for stage in payload["stages"])


def test_frontend_dependency_bootstrap_failure_is_infrastructure_and_cleans_resources(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write_frontend_project(repo)
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    head = _commit(repo, "frontend dependency bootstrap failure")

    result = _check(repo, base, head, env=_fake_corepack_environment(tmp_path, install_returncode=7))
    payload = _payload(result)

    assert result.returncode == 3, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "infrastructure_failure"
    assert payload["failure"]["code"] == "frontend_dependency_bootstrap_failed"
    assert next(stage for stage in payload["stages"] if stage["name"] == "frontend_dependency_bootstrap")["status"] == "failed"
    assert all(stage["name"] != "frontend_responsibility" for stage in payload["stages"])
    cleanup_stage = next(stage for stage in payload["stages"] if stage["name"] == "worktree_cleanup")
    assert cleanup_stage["status"] == "pass"
    assert all(resource["exists_after"] is False for resource in cleanup_stage["frontend_dependencies"])


def test_frontend_product_failure_cleans_detached_node_modules(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write_frontend_project(repo)
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    head = _commit(repo, "frontend verification failure")

    result = _check(repo, base, head, env=_fake_corepack_environment(tmp_path, verify_returncode=9))
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "product_test_failure"
    assert payload["failure"]["code"] == "frontend_ci_verify_failed"
    cleanup_stage = next(stage for stage in payload["stages"] if stage["name"] == "worktree_cleanup")
    assert cleanup_stage["status"] == "pass"
    assert all(record["registered_after"] is False for record in cleanup_stage["worktrees"])
    assert all(resource["exists_after"] is False for resource in cleanup_stage["frontend_dependencies"])


def test_shared_test_fixture_requires_an_explicit_bounded_suite(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "tests/conftest.py", "VALUE = True\n")
    head = _commit(repo, "shared test fixture")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "shared_test_suite_required"
    assert payload["failure"]["path"] == "tests/conftest.py"


def test_shared_test_fixture_runs_the_explicit_bounded_suite(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "tests/conftest.py", "VALUE = True\n")
    _write(repo, "tests/test_shared_fixture.py", "def test_shared_fixture():\n    assert True\n")
    head = _commit(repo, "shared fixture with suite")

    result = _check(repo, base, head, regression_test_suites=("tests/test_shared_fixture.py",))
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["tests"] == ["tests/test_shared_fixture.py"]


def test_shared_suite_requires_a_changed_shared_fixture(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "tests/test_explicit_suite.py", "def test_explicit_suite():\n    assert True\n")
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "unrelated suite flag")

    result = _check(repo, base, head, shared_test_suites=("tests/test_explicit_suite.py",))
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "unexpected_shared_test_suite"


def test_cross_layer_behavior_change_runs_a_changed_test_without_stem_matching(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(repo, "app/billing.py", "RATE = 3\n")
    _write(repo, "tests/test_cross_layer_regression.py", "def test_behavior():\n    assert True\n")
    head = _commit(repo, "cross-layer regression")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["tests"] == ["tests/test_cross_layer_regression.py"]


@pytest.mark.parametrize(
    ("source", "destination", "production_path", "category", "code"),
    (
        (
            "docs/moved.py",
            "app/moved.py",
            "app/moved.py",
            "governance_violation",
            "unapproved_app_root_module",
        ),
        (
            "app/moved.py",
            "docs/moved.py",
            "app/moved.py",
            "external_check",
            "regression_test_suite_required",
        ),
    ),
)
def test_cross_boundary_r100_rename_requires_regression_evidence(
    readiness_repo: tuple[Path, str],
    source: str,
    destination: str,
    production_path: str,
    category: str,
    code: str,
) -> None:
    repo, _authority = readiness_repo
    _write(repo, source, "MOVED = True\n")
    base = _commit(repo, "rename source")
    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", source, destination)
    head = _commit(repo, "cross-boundary rename")

    assert _git(repo, "diff", "--name-status", "--find-renames=50%", base, head, "--").startswith("R100")
    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == category
    assert payload["failure"]["code"] == code
    assert payload["failure"]["path"] == production_path


MCP_IRREGULAR_RESPONSIBILITY_SUITES = {
    "app/mcp/__init__.py": ("tests/test_mcp_tool_catalog.py", "tests/test_mcp_repository.py"),
    "app/mcp/catalog.py": ("tests/test_mcp_tool_catalog.py",),
    "app/mcp/repository.py": ("tests/test_mcp_repository.py", "tests/test_mcp_repository_postgres.py"),
    "app/schema.sql": ("tests/test_schema.py",),
}


def _write_irregular_responsibility_suites(repo: Path, suites: tuple[str, ...]) -> None:
    for index, suite in enumerate(suites):
        _write(repo, suite, f"def test_irregular_suite_{index}():\n    assert True\n")


def _assert_responsibility_tests(result: subprocess.CompletedProcess[str], expected: list[str]) -> None:
    payload = _payload(result)
    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["tests"] == expected


@pytest.mark.parametrize(
    ("production_path", "authorized_suite"),
    (
        ("app/skills/catalog.py", "tests/test_authorized_skill_catalog.py"),
        (
            "app/skills/deliverable_runtime.py",
            "tests/test_skill_deliverable_runtime.py",
        ),
        (
            "app/skills/deliverables.py",
            "tests/test_skill_deliverables.py",
        ),
        (
            "app/skills/packages.py",
            "tests/test_skill_packages.py",
        ),
        (
            "app/skills/pinning.py",
            "tests/test_skill_pinning.py",
        ),
    ),
)
def test_frozen_skill_mapping_runs_its_exact_safety_suite(
    readiness_repo: tuple[Path, str],
    production_path: str,
    authorized_suite: str,
) -> None:
    repo, _authority = readiness_repo
    _write(repo, production_path, "SKILL_READY = False\n")
    _write(repo, authorized_suite, "def test_skill_baseline():\n    assert True\n")
    base = _commit(repo, "skill delivery responsibility baseline")
    _write(repo, production_path, "")
    head = _commit(repo, "change skill delivery path")

    result = _check(repo, base, head)

    _assert_responsibility_tests(result, [authorized_suite])


@pytest.mark.parametrize(
    ("production_path", "baseline", "content", "suites", "mirror"),
    (
        ("app/mcp/__init__.py", "MCP_READY = False\n", "MCP_READY = True\n", MCP_IRREGULAR_RESPONSIBILITY_SUITES["app/mcp/__init__.py"], "tests/test_init.py"),
        ("app/mcp/catalog.py", "CATALOG_READY = False\n", "CATALOG_READY = True\n", MCP_IRREGULAR_RESPONSIBILITY_SUITES["app/mcp/catalog.py"], "tests/test_catalog.py"),
        ("app/mcp/repository.py", "REPOSITORY_READY = False\n", "REPOSITORY_READY = True\n", MCP_IRREGULAR_RESPONSIBILITY_SUITES["app/mcp/repository.py"], "tests/test_repository.py"),
        ("app/schema.sql", "CREATE TABLE readiness_mapping_old (id INTEGER);\n", "CREATE TABLE readiness_mapping_new (id INTEGER);\n", MCP_IRREGULAR_RESPONSIBILITY_SUITES["app/schema.sql"], "tests/test_schema.py"),
    ),
)
def test_irregular_production_paths_run_their_exact_bounded_suites(
    readiness_repo: tuple[Path, str],
    production_path: str,
    baseline: str,
    content: str,
    suites: tuple[str, ...],
    mirror: str,
) -> None:
    repo, _authority = readiness_repo
    _write_irregular_responsibility_suites(repo, suites)
    _write(repo, production_path, baseline)
    _write(repo, mirror, "def test_governance_mirror():\n    assert True\n")
    base = _commit(repo, "irregular responsibility baseline")
    _write(repo, production_path, content)
    _write(repo, mirror, "def test_governance_mirror_changed():\n    assert True\n")
    head = _commit(repo, "change irregular production path")

    result = _check(repo, base, head)

    _assert_responsibility_tests(result, sorted({*suites, mirror}))


def test_frozen_mcp_mapping_applies_to_the_source_of_an_r100_rename(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, _authority = readiness_repo
    source = "app/mcp/catalog.py"
    destination = "app/mcp/domain/renamed_catalog.py"
    suites = MCP_IRREGULAR_RESPONSIBILITY_SUITES[source]
    _write_irregular_responsibility_suites(repo, suites)
    _write(repo, source, "CATALOG_READY = True\n")
    base = _commit(repo, "mapped rename source")
    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", source, destination)
    head = _commit(repo, "rename mapped mcp path")

    assert _git(repo, "diff", "--name-status", "--find-renames=50%", base, head, "--").startswith("R100")
    result = _check(repo, base, head)

    _assert_responsibility_tests(result, list(suites))


def test_compose_path_runs_the_runtime_launch_contract_suite(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, _authority = readiness_repo
    production_path = "deploy/ai-platform/docker-compose.yml"
    _write_irregular_responsibility_suites(repo, ("tests/test_runtime_launch_script.py",))
    _write(repo, production_path, "services: {}\n")
    _write(repo, "tests/test_compose_contract.py", "def test_compose_contract():\n    assert True\n")
    base = _commit(repo, "compose responsibility baseline")
    _write(repo, production_path, "services:\n  api: {}\n")
    _write(repo, "tests/test_compose_contract.py", "def test_compose_contract_changed():\n    assert True\n")
    head = _commit(repo, "compose responsibility change")

    result = _check(repo, base, head)

    _assert_responsibility_tests(
        result,
        ["tests/test_compose_contract.py", "tests/test_runtime_launch_script.py"],
    )


@pytest.mark.parametrize(
    "production_path",
    (
        "deploy/ai-platform/docker-compose.unlisted.yml",
        "deploy/ai-platform-archive/docker-compose.yml",
    ),
)
def test_unlisted_compose_variants_remain_external_with_the_contract_suite(
    readiness_repo: tuple[Path, str], production_path: str
) -> None:
    repo, _authority = readiness_repo
    _write(repo, "tests/test_runtime_launch_script.py", "def test_compose_contract():\n    assert True\n")
    _write(repo, production_path, "services: {}\n")
    base = _commit(repo, "unlisted deploy baseline")
    _write(repo, production_path, "services:\n  worker: {}\n")
    head = _commit(repo, "unlisted deploy change")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "regression_test_suite_required"
    assert payload["failure"]["path"] == production_path


@pytest.mark.parametrize("invalid_suite", ("missing", "wrong_case", "non_blob"))
def test_irregular_mapping_requires_every_exact_head_blob(
    readiness_repo: tuple[Path, str], invalid_suite: str
) -> None:
    repo, _authority = readiness_repo
    _write(repo, "tests/test_mcp_repository.py", "def test_repository():\n    assert True\n")
    if invalid_suite == "wrong_case":
        _write(repo, "tests/Test_mcp_tool_catalog.py", "def test_catalog():\n    assert True\n")
    elif invalid_suite == "non_blob":
        _write(repo, "tests/test_mcp_tool_catalog.py/sentinel.txt", "tree, not a test blob\n")
    _write(repo, "app/mcp/__init__.py", "MCP_READY = False\n")
    base = _commit(repo, "invalid mapped suite baseline")
    _write(repo, "app/mcp/__init__.py", "MCP_READY = True\n")
    _write(repo, "tests/test_init.py", "def test_initializer():\n    assert True\n")
    head = _commit(repo, "change initializer with invalid mapped suite")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "responsibility_suite_required"
    assert payload["failure"]["path"] == "app/mcp/__init__.py"


@pytest.mark.parametrize("production_path", ("app/mcp/unlisted.py", "app/mcp/catalog.json"))
def test_unlisted_mcp_path_runs_an_explicit_unchanged_regression_suite(
    readiness_repo: tuple[Path, str], production_path: str
) -> None:
    repo, _authority = readiness_repo
    _write(repo, "tests/test_explicit_suite.py", "def test_explicit_suite():\n    assert True\n")
    if production_path.endswith(".py"):
        _write(repo, production_path, "VALUE = False\n")
    else:
        _write(repo, production_path, "{\"enabled\": false}\n")
    base = _commit(repo, "unlisted mcp baseline")
    _write(repo, production_path, "VALUE = True\n" if production_path.endswith(".py") else "{\"enabled\": true}\n")
    head = _commit(repo, "unlisted mcp production path")

    result = _check(repo, base, head, regression_test_suites=("tests/test_explicit_suite.py",))

    _assert_responsibility_tests(result, ["tests/test_explicit_suite.py"])


@pytest.mark.parametrize("existing_exception", (False, True), ids=("added", "modified"))
def test_changed_code_governance_exception_runs_its_exact_bounded_suite(
    readiness_repo: tuple[Path, str],
    existing_exception: bool,
) -> None:
    repo, _authority = readiness_repo
    _write(repo, "app/billing.py", _python_assignments(3_001))
    if existing_exception:
        _write(repo, ".code-governance-exception.json", _governance_exception(reason="initial exception"))
    base = _commit(repo, "governance exception baseline")
    _write(repo, "app/billing.py", _python_assignments(3_001) + "NEW_VALUE = 1\n")
    _write(repo, "tests/test_billing.py", "def test_billing():\n    assert True\n")
    _write(repo, ".code-governance-exception.json", _governance_exception(reason="updated exception"))
    scope_head = _commit(repo, "change governance exception scope")
    _write(
        repo,
        ".code-governance-exception.json",
        _governance_exception(
            reason="updated exception",
            base_ref=base,
            scope_sha256=_governance_scope_sha256(repo, base, scope_head),
        ),
    )
    head = _commit(repo, "bind governance exception")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["status"] == "pass"
    assert responsibility_stage["tests"] == ["tests/test_billing.py", "tests/test_code_governance.py"]


def test_deleted_code_governance_exception_follows_the_deleted_path_policy(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, _authority = readiness_repo
    _write(repo, ".code-governance-exception.json", _governance_exception(reason="initial exception"))
    base = _commit(repo, "governance exception baseline")
    (repo / ".code-governance-exception.json").unlink()
    head = _commit(repo, "delete governance exception")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["status"] == "not_applicable"
    assert responsibility_stage["tests"] == []


def test_copied_code_governance_exception_remains_external(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, _authority = readiness_repo
    exception = _governance_exception(reason="copied exception")
    _write(repo, "app/billing.py", _python_assignments(3_001))
    _write(repo, "source-policy.json", exception)
    base = _commit(repo, "copy source baseline")
    _write(repo, "app/billing.py", _python_assignments(3_001) + "NEW_VALUE = 1\n")
    _write(repo, "tests/test_billing.py", "def test_billing():\n    assert True\n")
    _write(repo, ".code-governance-exception.json", exception)
    scope_head = _commit(repo, "copy governance exception scope")
    _write(
        repo,
        ".code-governance-exception.json",
        _governance_exception(
            reason="copied exception",
            base_ref=base,
            scope_sha256=_governance_scope_sha256(repo, base, scope_head),
        ),
    )
    head = _commit(repo, "bind copied governance exception")

    production_status = _git(
        repo,
        "diff",
        "--name-status",
        "--find-renames=50%",
        "--find-copies=50%",
        "--find-copies-harder",
        base,
        head,
        "--",
    )
    result = _check(repo, base, head)
    payload = _payload(result)

    assert any(
        line.startswith("C") and line.endswith("\tsource-policy.json\t.code-governance-exception.json")
        for line in production_status.splitlines()
    )
    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "responsibility_suite_required"
    assert payload["failure"]["path"] == ".code-governance-exception.json"


def test_wrong_case_code_governance_suite_remains_external(tmp_path: Path) -> None:
    repo, _authority = _create_readiness_repo(tmp_path, code_governance_test_path="tests/Test_code_governance.py")
    _write(repo, "app/billing.py", _python_assignments(3_001))
    base = _commit(repo, "wrong case baseline")
    _write(repo, "app/billing.py", _python_assignments(3_001) + "NEW_VALUE = 1\n")
    _write(repo, "tests/test_billing.py", "def test_billing():\n    assert True\n")
    _write(repo, ".code-governance-exception.json", _governance_exception(reason="wrong case suite"))
    scope_head = _commit(repo, "wrong case governance scope")
    _write(
        repo,
        ".code-governance-exception.json",
        _governance_exception(
            reason="wrong case suite",
            base_ref=base,
            scope_sha256=_governance_scope_sha256(repo, base, scope_head),
        ),
    )
    head = _commit(repo, "bind wrong case governance suite")

    exact_case = _run(repo, "git", "cat-file", "-e", f"{head}:tests/test_code_governance.py", check=False)
    wrong_case = _run(repo, "git", "cat-file", "-e", f"{head}:tests/Test_code_governance.py", check=False)
    result = _check(repo, base, head)
    payload = _payload(result)

    assert exact_case.returncode != 0
    assert wrong_case.returncode == 0
    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "responsibility_suite_required"
    assert payload["failure"]["path"] == ".code-governance-exception.json"


@pytest.mark.parametrize(
    ("operation", "source", "destination", "status"),
    (
        ("copy", EXCEPTION_PATH, "docs/copied-exception.md", "C100"),
        ("copy", EXCEPTION_PATH, "tests/copied-exception.json", "C100"),
        ("copy", EXCEPTION_PATH, "frontend/web/copied-exception.json", "C100"),
        ("rename", EXCEPTION_PATH, "docs/renamed-exception.md", "R100"),
        ("rename", EXCEPTION_PATH, "tests/renamed-exception.json", "R100"),
        ("rename", EXCEPTION_PATH, "frontend/web/renamed-exception.json", "R100"),
        ("copy", "docs/exception-source.md", EXCEPTION_PATH, "C100"),
        ("copy", "tests/exception-source.json", EXCEPTION_PATH, "C100"),
        ("copy", "frontend/web/exception-source.json", EXCEPTION_PATH, "C100"),
        ("rename", "docs/exception-source.md", EXCEPTION_PATH, "R100"),
        ("rename", "tests/exception-source.json", EXCEPTION_PATH, "R100"),
        ("rename", "frontend/web/exception-source.json", EXCEPTION_PATH, "R100"),
    ),
)
def test_non_add_modify_exception_transitions_remain_external(
    readiness_repo: tuple[Path, str],
    operation: str,
    source: str,
    destination: str,
    status: str,
) -> None:
    repo, _authority = readiness_repo
    base, head = _exception_transition(repo, operation=operation, source=source, destination=destination)
    production_status = _git(
        repo,
        "diff",
        "--name-status",
        "--find-renames=50%",
        "--find-copies=50%",
        "--find-copies-harder",
        base,
        head,
        "--",
    )

    result = _check(repo, base, head)
    payload = _payload(result)

    assert any(
        line.startswith(status[0]) and line.endswith(f"\t{source}\t{destination}")
        for line in production_status.splitlines()
    )
    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "responsibility_suite_required"
    assert payload["failure"]["path"] == EXCEPTION_PATH
    assert {stage["name"] for stage in payload["stages"]} == {
        "architecture_governance",
        "diff_check",
        "governance",
        "worktree_cleanup",
    }


def test_regression_suite_traversal_never_executes_an_outside_file(
    readiness_repo: tuple[Path, str],
    tmp_path: Path,
) -> None:
    repo, _authority = readiness_repo
    marker = tmp_path / "outside-suite-executed.txt"
    _write(
        repo,
        "test_evil.py",
        "from pathlib import Path\n\n\n"
        "def test_evil():\n"
        f"    Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
    )
    base = _commit(repo, "outside suite sentinel")
    _write(repo, "app/billing.py", "RATE = 3\n")
    head = _commit(repo, "behavior change with traversal suite")

    result = _check(repo, base, head, regression_test_suites=("tests/../test_evil.py",))
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "invalid_regression_test_suite"
    assert payload["failure"]["path"] == "tests/../test_evil.py"
    assert marker.exists() is False


@pytest.mark.parametrize(
    "suite",
    (
        "/tests/test_shared_fixture.py",
        "tests\\test_shared_fixture.py",
        "tests//test_shared_fixture.py",
        "tests/./test_shared_fixture.py",
        "tests/../test_shared_fixture.py",
        "tests/../../outside/test_evil.py",
        "tests/test_shared_fixture.py/",
    ),
)
def test_regression_suite_requires_canonical_posix_spelling(
    readiness_repo: tuple[Path, str],
    suite: str,
) -> None:
    repo, _authority = readiness_repo
    _write(repo, "tests/test_shared_fixture.py", "def test_shared_fixture():\n    assert True\n")
    base = _commit(repo, "regression suite baseline")
    _write(repo, "app/billing.py", "RATE = 3\n")
    head = _commit(repo, "behavior change with noncanonical suite")

    result = _check(repo, base, head, regression_test_suites=(suite,))
    payload = _payload(result)

    assert result.returncode == 2, json.dumps(payload, indent=2, sort_keys=True)
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "invalid_regression_test_suite"
    assert payload["failure"]["path"] == suite


def test_deleted_test_file_is_not_sent_to_pytest(readiness_repo: tuple[Path, str]) -> None:
    repo, authority = readiness_repo
    _write(repo, "tests/test_deleted.py", "def test_deleted():\n    assert False\n")
    base = _commit(repo, "test scheduled for deletion")
    (repo / "tests" / "test_deleted.py").unlink()
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "delete stale test")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert "tests/test_deleted.py" not in responsibility_stage["tests"]
    assert authority == payload["authority_ref"]


def test_unclassifiable_production_change_requires_external_suite(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _write(repo, "config/policy.json", "{\"enabled\": true}\n")
    head = _commit(repo, "unclassifiable production change")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "external_check"
    assert payload["failure"]["code"] == "regression_test_suite_required"
    assert payload["failure"]["path"] == "config/policy.json"


def test_mixed_backend_and_frontend_changes_run_both_responsibility_suites(
    readiness_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, base = readiness_repo
    _write(repo, "app/runs/application/invoice.py", "TOTAL = 1\n")
    _write(repo, "tests/test_invoice.py", "def test_invoice():\n    assert True\n")
    _write_frontend_project(repo)
    _write(repo, "frontend/web/src/App.tsx", "export const App = () => null;\n")
    head = _commit(repo, "mixed responsibilities")

    result = _check(repo, base, head, env=_fake_corepack_environment(tmp_path))
    payload = _payload(result)

    assert result.returncode == 0, json.dumps(payload, indent=2, sort_keys=True)
    stages = {stage["name"]: stage for stage in payload["stages"]}
    assert stages["governance"]["status"] == "pass"
    assert stages["responsibility_tests"]["status"] == "pass"
    assert stages["frontend_responsibility"]["status"] == "pass"


def test_stale_base_fails_before_any_local_checks(readiness_repo: tuple[Path, str]) -> None:
    repo, base = readiness_repo
    _git(repo, "checkout", "-b", "candidate", base)
    _write(repo, "docs/candidate.md", "candidate\n")
    head = _commit(repo, "candidate")
    _git(repo, "checkout", "main")
    _write(repo, "docs/main.md", "main\n")
    stale_base = _commit(repo, "main advances")

    result = _check(repo, stale_base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "stale_base"
    assert payload["failure"]["code"] == "non_ancestor_range"
    assert payload["stages"] == []


def test_malformed_exact_ref_is_rejected_as_a_governance_violation(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, head = readiness_repo

    result = _check(repo, "main", head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "invalid_ref"
    assert "40-hex" in payload["failure"]["message"]


def test_malformed_authority_ref_is_rejected_before_candidate_checks(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo

    result = _check(repo, base, base, authority_ref="origin/main")
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "governance_violation"
    assert payload["failure"]["code"] == "invalid_ref"
    assert payload["failure"]["message"] == "authority_ref must be a full 40-hex commit id"
    assert payload["stages"] == []


def test_deterministic_product_failure_preserves_pytest_identity(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(
        repo,
        "tests/test_deterministic_failure.py",
        "from app import billing\n\n\ndef test_deterministic_failure():\n    assert billing.RATE == 3\n",
    )
    head = _commit(repo, "failing responsibility test")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 2
    assert payload["category"] == "product_test_failure"
    assert payload["failure"]["code"] == "pytest_failed"
    assert payload["failure"]["test_identity"] == "tests/test_deterministic_failure.py::test_deterministic_failure"


def test_size_advisory_does_not_fail_pre_push_readiness(readiness_repo: tuple[Path, str]) -> None:
    repo, _authority = readiness_repo
    _write(
        repo,
        "app/billing.py",
        "\n".join(f"VALUE_{index} = {index}" for index in range(3_001)) + "\nRATE = 2\n",
    )
    base = _commit(repo, "large billing module")
    _write(repo, "app/billing.py", (repo / "app" / "billing.py").read_text(encoding="utf-8") + "EXTRA = 1\n")
    _write(repo, "tests/test_billing.py", "def test_billing():\n    assert True\n")
    head = _commit(repo, "grow functional hot file")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "pass"
    governance_stage = next(stage for stage in payload["stages"] if stage["name"] == "governance")
    assert governance_stage["status"] == "pass"


def test_governance_ruff_ignores_a_head_root_shadow_module(readiness_repo: tuple[Path, str]) -> None:
    repo, _authority = readiness_repo
    _write(repo, "ruff.py", "raise RuntimeError('head ruff module was imported')\n")
    base = _commit(repo, "shadow ruff in candidate base")
    _write(repo, "tests/test_ruff.py", "def test_ruff():\n    assert True\n")
    head = _commit(repo, "shadow ruff module")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    governance_stage = next(stage for stage in payload["stages"] if stage["name"] == "governance")
    assert governance_stage["ruff"]["status"] == "pass"


def test_success_uses_the_exact_resolved_range_and_stable_taxonomy(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "docs only")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "pass"
    assert payload["base_ref"] == base
    assert payload["head_ref"] == head
    assert payload["category"] is None
    assert set(payload["taxonomy"]) == {
        "external_check",
        "governance_violation",
        "infrastructure_failure",
        "product_test_failure",
        "stale_base",
    }
    assert {stage["name"] for stage in payload["stages"]} == {
        "architecture_governance",
        "authority_integrity",
        "compileall",
        "diff_check",
        "governance",
        "responsibility_tests",
        "worktree_cleanup",
    }
    architecture = next(
        stage for stage in payload["stages"] if stage["name"] == "architecture_governance"
    )
    governance = next(stage for stage in payload["stages"] if stage["name"] == "governance")
    compileall = next(stage for stage in payload["stages"] if stage["name"] == "compileall")
    assert architecture["status"] == "pass"
    assert architecture["policy"]["schema_version"] == "ai-platform.architecture-policy.v1"
    assert architecture["exception"]["status"] == "absent"
    assert architecture["exempted_findings"] == []
    assert payload["stages"].index(governance) < payload["stages"].index(architecture)
    assert payload["stages"].index(architecture) < payload["stages"].index(compileall)
    assert payload["authority"]["architecture_governance"] == "sealed"


def test_docs_only_range_does_not_run_an_unrelated_existing_test(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, _initial = readiness_repo
    _write(repo, "tests/test_repositories.py", "def test_unrelated_failure():\n    assert False\n")
    base = _commit(repo, "unrelated legacy test")
    _write(repo, "docs/readiness.md", "ready\n")
    head = _commit(repo, "docs only")

    result = _check(repo, base, head)
    payload = _payload(result)

    assert result.returncode == 0, result.stderr
    responsibility_stage = next(stage for stage in payload["stages"] if stage["name"] == "responsibility_tests")
    assert responsibility_stage["status"] == "not_applicable"
    assert responsibility_stage["tests"] == []


def test_text_output_is_human_readable_and_uses_the_stable_category(
    readiness_repo: tuple[Path, str],
) -> None:
    repo, base = readiness_repo
    _git(repo, "checkout", "-b", "candidate", base)
    _write(repo, "docs/candidate.md", "candidate\n")
    head = _commit(repo, "candidate")
    _git(repo, "checkout", "main")
    _write(repo, "docs/main.md", "main\n")
    stale_base = _commit(repo, "main advances")

    result = _check(repo, stale_base, head, output_format="text")

    assert result.returncode == 2
    assert "pre-push-readiness: FAIL" in result.stdout
    assert "category: stale_base" in result.stdout
    assert "code: non_ancestor_range" in result.stdout


def test_pr_workflow_keeps_immutable_authority_and_failure_contract() -> None:
    workflow = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    normalized = workflow.lower()
    compact = " ".join(workflow.split())

    assert "tools/pre_push_readiness.py\") check" in workflow
    assert "python tools/pre_push_readiness.py" not in workflow
    assert "--authority-ref $authority" in workflow
    assert "detached temporary worktree" in normalized
    assert "never execute" in normalized
    assert "python -P" in workflow
    assert "PYTHONSAFEPATH=1" in workflow
    assert "before the first push" in normalized
    assert "after every ordinary merge-up" in normalized
    assert "full 40-hex commits" in workflow
    assert "immutable authority git object" in compact.lower()
    assert "governance decision is sealed first" in compact.lower()
    assert "regression evidence regardless of filename stem" in compact.lower()
    for category in (
        "stale_base",
        "product_test_failure",
        "governance_violation",
        "infrastructure_failure",
        "external_check",
    ):
        assert f"`{category}`" in workflow
