from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SOURCE = REPO_ROOT / "tools" / "run_test_stage.py"


@pytest.fixture
def tmp_path(tmp_path_factory):
    # These tests nest another runner worktree below the owned basetemp.
    # Keep the fixture name short enough for Windows MAX_PATH installations.
    return tmp_path_factory.mktemp("r")


def _subprocess_environment(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("PYTEST_")
    }
    environment.update(overrides or {})
    return environment


def _run(
    *arguments: str,
    cwd: Path,
    timeout: float = 20,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        env=_subprocess_environment(environment),
    )


def _git(repo: Path, *arguments: str) -> None:
    completed = _run("git", *arguments, cwd=repo)
    assert completed.returncode == 0, completed.stderr


def _make_repo(tmp_path: Path, tests: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tests").mkdir()
    shutil.copyfile(RUNNER_SOURCE, repo / "tools" / "run_test_stage.py")
    (repo / ".gitignore").write_text(".pytest-tmp/\n", encoding="utf-8")
    for name, source in tests.items():
        (repo / "tests" / name).write_text(source, encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test-runner@example.test")
    _git(repo, "config", "user.name", "Test Runner")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _runner(
    repo: Path,
    *arguments: str,
    cwd: Path | None = None,
    timeout: float = 20,
    environment: dict[str, str] | None = None,
):
    return _run(
        sys.executable,
        str(repo / "tools" / "run_test_stage.py"),
        *arguments,
        cwd=cwd or repo,
        timeout=timeout,
        environment=environment,
    )


def _single_evidence(repo: Path) -> dict[str, object]:
    paths = list((repo / ".pytest-tmp" / "test-runs").glob("*/*/evidence.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _pid_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    open_process.restype = ctypes.c_void_p
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    get_exit_code.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = open_process(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_uint()
        return (
            bool(get_exit_code(handle, ctypes.byref(exit_code)))
            and exit_code.value == 259
        )
    finally:
        close_handle(handle)


def test_runner_executes_explicit_selector_with_local_evidence(tmp_path):
    repo = _make_repo(tmp_path, {"test_pass.py": "def test_pass():\n    assert True\n"})

    completed = _runner(
        repo,
        "--stage",
        "owning",
        "--timeout-seconds",
        "10",
        "tests/test_pass.py::test_pass",
    )

    assert completed.returncode == 0, completed.stderr
    evidence = _single_evidence(repo)
    assert evidence["status"] == "passed"
    assert (
        evidence["head_sha"]
        == _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    )
    assert evidence["selectors"] == ["tests/test_pass.py::test_pass"]
    assert evidence["command"][:3] == [sys.executable, "-m", "pytest"]
    assert evidence["environment"] == {"removed_pytest_variables": []}
    assert evidence["pytest"] == {"errors": 0, "failures": 0, "skipped": 0, "tests": 1}
    assert evidence["cleanup"] == "completed"
    assert (
        (repo / ".pytest-tmp" / ".run-test-stage.lock").read_bytes().startswith(b"\0")
    )
    paths = evidence["paths"]
    assert isinstance(paths, dict)
    assert str(paths["basetemp"]).startswith(".pytest-tmp/test-runs/")
    assert (repo / str(paths["junit"])).is_file()
    assert (repo / str(paths["evidence"])).is_file()


def test_runner_removes_caller_pytest_control_environment(tmp_path):
    repo = _make_repo(tmp_path, {"test_pass.py": "def test_pass():\n    assert True\n"})

    completed = _runner(
        repo,
        "--stage",
        "environment",
        "tests/test_pass.py",
        environment={
            "PYTEST_ADDOPTS": "--not-a-real-pytest-option",
            "PYTEST_CURRENT_TEST": "foreign-test-state",
            "PYTEST_PLUGINS": "module_that_does_not_exist",
        },
    )

    assert completed.returncode == 0, completed.stderr
    evidence = _single_evidence(repo)
    assert evidence["environment"] == {
        "removed_pytest_variables": [
            "PYTEST_ADDOPTS",
            "PYTEST_CURRENT_TEST",
            "PYTEST_PLUGINS",
        ]
    }
    assert evidence["pytest"] == {"errors": 0, "failures": 0, "skipped": 0, "tests": 1}


def _coverage_repo(tmp_path: Path, *, body: str = "assert choose(True) == 1") -> Path:
    repo = _make_repo(tmp_path, {
        "test_covered.py": "from app.subject import choose\n\ndef test_choose():\n    " + body + "\n",
    })
    (repo / "app").mkdir()
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "subject.py").write_text(
        "def choose(flag):\n    if flag:\n        return 1\n    return 2\n", encoding="utf-8",
    )
    (repo / "app" / "unvisited.py").write_text("def unvisited():\n    return 3\n", encoding="utf-8")
    (repo / "app" / "namespace").mkdir()
    (repo / "app" / "namespace" / "unvisited.py").write_text("def unseen():\n    return 4\n", encoding="utf-8")
    return repo


def test_explicit_coverage_measures_branches_and_unvisited_app_sources(tmp_path):
    repo = _coverage_repo(tmp_path)
    foreign = tmp_path / "foreign.coverage"
    config = repo / ".coveragerc"
    config.write_text("[run]\nomit = app/*\n[report]\nfail_under = 100\n", encoding="utf-8")
    completed = _runner(
        repo, "--stage", "coverage", "--coverage", "tests/test_covered.py",
        environment={"COVERAGE_FILE": str(foreign), "COVERAGE_RCFILE": str(config), "COV_CORE_SOURCE": "foreign"},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = _single_evidence(repo)
    assert evidence["status"] == "passed"
    observation = evidence["coverage"]
    assert observation["status"] == "recorded"
    assert observation["scope"] == "app" and observation["branch"] is True
    assert len(observation["source_sha256"]) == len(observation["runner_sha256"]) == 64
    assert evidence["environment"]["removed_coverage_variables"] == ["COVERAGE_FILE", "COVERAGE_RCFILE", "COV_CORE_SOURCE"]
    assert not foreign.exists()
    data = json.loads((repo / evidence["paths"]["coverage"]).read_text(encoding="utf-8"))
    assert data["meta"]["branch_coverage"] is True
    files = {Path(path).as_posix(): value for path, value in data["files"].items()}
    assert files["app/subject.py"]["summary"]["missing_branches"] == 1
    assert files["app/unvisited.py"]["summary"]["covered_lines"] == 0
    assert files["app/namespace/unvisited.py"]["summary"]["num_statements"] == 2
    assert files["app/namespace/unvisited.py"]["summary"]["covered_lines"] == 0
    assert observation["totals"]["covered_branches"] < observation["totals"]["num_branches"]
    assert (repo / evidence["paths"]["coverage_data"]).is_file()


def test_coverage_keeps_product_failure(tmp_path):
    repo = _coverage_repo(tmp_path, body="assert False")
    completed = _runner(repo, "--stage", "failed-coverage", "--coverage", "tests/test_covered.py")
    assert completed.returncode == 1
    evidence = _single_evidence(repo)
    assert evidence["category"] == "product_test_failure"
    assert evidence["coverage"]["status"] == "recorded"



def test_coverage_keeps_zero_skip_enforcement(tmp_path):
    repo = _coverage_repo(tmp_path, body="__import__('pytest').skip('synthetic missing service')")
    completed = _runner(repo, "--stage", "skip-coverage", "--coverage", "--require-zero-skips", "tests/test_covered.py")
    assert completed.returncode == 6
    evidence = _single_evidence(repo)
    assert evidence["category"] == "required_dependency_missing"
    assert evidence["coverage"]["status"] == "recorded"


def test_coverage_does_not_accept_source_mutation_during_execution(tmp_path):
    repo = _coverage_repo(tmp_path, body="__import__('pathlib').Path('app/subject.py').write_text('changed = 1\\n', encoding='utf-8')")
    completed = _runner(repo, "--stage", "changed-coverage", "--coverage", "tests/test_covered.py")
    assert completed.returncode == 70, completed.stdout + completed.stderr
    evidence = _single_evidence(repo)
    assert evidence["code"] == "coverage_report_source_changed"
    assert evidence["coverage"]["status"] == "source_changed"



def test_coverage_requires_owned_source_and_installed_plugin(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, {"test_pass.py": "def test_pass():\n    assert True\n"})
    completed = _runner(repo, "--stage", "missing-source", "--coverage", "tests/test_pass.py")
    assert completed.returncode == 2
    assert json.loads(completed.stderr.strip().splitlines()[-1])["code"] == "coverage_source_missing"
    (repo / "app").mkdir()
    spec = importlib.util.spec_from_file_location("runner_coverage_under_test", RUNNER_SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    args = module._parser().parse_args(["--stage", "missing-plugin", "--coverage", "tests/test_pass.py"])
    with pytest.raises(module.StageError) as caught:
        module._build_plan(args)
    assert caught.value.code == "coverage_plugin_missing"
    assert not (repo / ".pytest-tmp/test-runs").exists()


@pytest.mark.parametrize("report_kind", ["missing", "incomplete", "invalid-count"])
@pytest.mark.parametrize("returncode", [0, 1])
def test_invalid_coverage_report_never_relabels_a_product_failure(tmp_path, monkeypatch, returncode, report_kind):
    repo = _coverage_repo(tmp_path)
    spec = importlib.util.spec_from_file_location("runner_missing_coverage_under_test", RUNNER_SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.chdir(repo)
    plan = module._build_plan(module._parser().parse_args(["--stage", "missing-report", "--coverage", "tests/test_covered.py"]))

    def execute(*args, **kwargs):
        plan.junit.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"/>', encoding="utf-8")
        if report_kind != "missing":
            report = {
                "meta": {"branch_coverage": True},
                "totals": {"covered_lines": 0, "num_statements": 1, "covered_branches": 0, "num_branches": 0},
                "files": {"app/__init__.py": {}},
            }
            if report_kind == "invalid-count":
                report["totals"]["covered_lines"] = True
            (plan.run_root / "coverage.json").write_text(json.dumps(report), encoding="utf-8")
        return module.ProcessResult(returncode, False, False, "completed", False)

    monkeypatch.setattr(module, "_execute_process", execute)
    assert module._run_stage(plan) == (70 if returncode == 0 else 1)
    evidence = _single_evidence(repo)
    expected = "coverage_report_missing" if report_kind == "missing" else "coverage_report_invalid"
    assert evidence["code"] == (expected if returncode == 0 else "pytest_failed")


@pytest.mark.parametrize(
    ("arguments", "cwd_name", "code"),
    [
        (
            ("--stage", "wrong-cwd", "tests/test_pass.py"),
            "tests",
            "wrong_worktree_root",
        ),
        (("--stage", "escape", "../outside.py"), ".", "selector_outside_tests"),
    ],
)
def test_runner_rejects_invalid_worktree_or_selector(
    tmp_path, arguments, cwd_name, code
):
    repo = _make_repo(tmp_path, {"test_pass.py": "def test_pass():\n    assert True\n"})
    cwd = repo if cwd_name == "." else repo / cwd_name

    completed = _runner(repo, *arguments, cwd=cwd)

    assert completed.returncode == 2
    failure = json.loads(completed.stderr.strip().splitlines()[-1])
    assert failure["category"] == "invalid_test_plan"
    assert failure["code"] == code
    assert not (repo / ".pytest-tmp" / "test-runs").exists()


def test_runner_accepts_untracked_test_selector(tmp_path):
    repo = _make_repo(tmp_path, {"test_pass.py": "def test_pass():\n    assert True\n"})
    (repo / "tests" / "test_untracked.py").write_text(
        "def test_untracked():\n    assert True\n",
        encoding="utf-8",
    )

    completed = _runner(repo, "--stage", "untracked", "tests/test_untracked.py")

    assert completed.returncode == 0, completed.stderr
    evidence = _single_evidence(repo)
    assert evidence["status"] == "passed"
    assert evidence["pytest"] == {
        "errors": 0,
        "failures": 0,
        "skipped": 0,
        "tests": 1,
    }


def test_runner_preserves_pytest_failure(tmp_path):
    repo = _make_repo(
        tmp_path, {"test_fail.py": "def test_fail():\n    assert False\n"}
    )

    completed = _runner(repo, "--stage", "failure", "tests/test_fail.py")

    assert completed.returncode == 1
    evidence = _single_evidence(repo)
    assert evidence["status"] == "failed"
    assert evidence["category"] == "product_test_failure"
    assert evidence["code"] == "pytest_failed"


@pytest.mark.parametrize(
    ("runner_options", "expected_returncode", "expected_status", "expected_category"),
    [
        ((), 0, "passed_with_skips", None),
        (("--require-zero-skips",), 6, "failed", "required_dependency_missing"),
    ],
)
def test_runner_reports_skips_and_can_require_zero_skips(
    tmp_path,
    runner_options,
    expected_returncode,
    expected_status,
    expected_category,
):
    repo = _make_repo(
        tmp_path,
        {
            "test_skip.py": "import pytest\n\ndef test_skip():\n    pytest.skip('missing fixture service')\n"
        },
    )

    completed = _runner(
        repo,
        "--stage",
        "integration",
        *runner_options,
        "tests/test_skip.py",
    )

    assert completed.returncode == expected_returncode
    evidence = _single_evidence(repo)
    assert evidence["status"] == expected_status
    assert evidence["category"] == expected_category
    assert evidence["pytest"]["skipped"] == 1
    if runner_options:
        assert evidence["code"] == "skipped_tests_forbidden"


def test_runner_rejects_a_second_stage_in_the_same_worktree(tmp_path):
    repo = _make_repo(
        tmp_path,
        {
            "test_wait.py": (
                "import time\nfrom pathlib import Path\n\ndef test_wait():\n"
                "    Path('entered').write_text('ready', encoding='utf-8')\n"
                "    deadline = time.monotonic() + 15\n"
                "    while not Path('release').exists():\n"
                "        assert time.monotonic() < deadline\n"
                "        time.sleep(0.02)\n"
            )
        },
    )
    command = [
        sys.executable,
        str(repo / "tools" / "run_test_stage.py"),
        "--stage",
        "first",
        "--timeout-seconds",
        "20",
        "tests/test_wait.py",
    ]
    first = subprocess.Popen(
        command,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_subprocess_environment(),
    )
    lock_path = repo / ".pytest-tmp" / ".run-test-stage.lock"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if lock_path.is_file():
            try:
                lock_metadata = lock_path.read_text(encoding="utf-8")
            except PermissionError:
                break
            if '"stage": "first"' in lock_metadata:
                break
        time.sleep(0.05)
    else:
        first.kill()
        pytest.fail("first runner did not acquire the worktree lock")

    second = _runner(repo, "--stage", "second", "tests/test_wait.py")
    (repo / "release").write_text("continue", encoding="utf-8")
    stdout, stderr = first.communicate(timeout=20)

    assert first.returncode == 0, f"{stdout}\n{stderr}"
    assert second.returncode == 75
    failure = json.loads(second.stderr.strip().splitlines()[-1])
    assert failure["code"] == "test_runner_busy"


@pytest.mark.parametrize("coverage", [False, True])
def test_timeout_terminates_the_owned_child_process_tree(tmp_path, coverage):
    repo = _make_repo(
        tmp_path,
        {
            "test_hang.py": (
                "import pathlib\n"
                "import subprocess\n"
                "import sys\n"
                "import time\n\n"
                "def test_hang():\n"
                "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "    pathlib.Path('child.pid').write_text(str(child.pid), encoding='utf-8')\n"
                "    time.sleep(60)\n"
            )
        },
    )

    if coverage:
        (repo / "app").mkdir()
        (repo / "app" / "unvisited.py").write_text("value = 1\n", encoding="utf-8")
    completed = _runner(
        repo,
        "--stage",
        "timeout",
        "--timeout-seconds",
        "8",
        *("--coverage",) if coverage else (),
        "tests/test_hang.py",
        timeout=25,
    )

    assert completed.returncode == 124, completed.stderr
    assert (repo / "child.pid").is_file(), "timed stage never started its child fixture"
    evidence = _single_evidence(repo)
    assert evidence["status"] == "timed_out"
    assert evidence["category"] == "test_timeout"
    child_pid = int((repo / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _pid_is_running(child_pid):
        time.sleep(0.05)
    assert not _pid_is_running(child_pid)


def test_successful_stage_terminates_a_leaked_descendant(tmp_path):
    repo = _make_repo(
        tmp_path,
        {
            "test_leak.py": (
                "import pathlib\n"
                "import subprocess\n"
                "import sys\n\n"
                "_CHILDREN = []\n\n"
                "def test_leak():\n"
                "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "    _CHILDREN.append(child)\n"
                "    pathlib.Path('child.pid').write_text(str(child.pid), encoding='utf-8')\n"
                "    assert child.poll() is None\n"
            )
        },
    )

    completed = _runner(
        repo,
        "--stage",
        "normal-cleanup",
        "--timeout-seconds",
        "10",
        "tests/test_leak.py",
    )

    assert completed.returncode == 0, completed.stderr
    assert (repo / "child.pid").is_file()
    child_pid = int((repo / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _pid_is_running(child_pid):
        time.sleep(0.05)
    assert not _pid_is_running(child_pid)
    assert _single_evidence(repo)["cleanup"] == "completed"
