#!/usr/bin/env python3
"""Run one explicit pytest stage with bounded, worktree-local evidence."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, NoReturn

REPORT_SCHEMA_VERSION = "ai-platform.local-test-stage.v1"
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 1800
HEARTBEAT_SECONDS = 15
TERMINATION_GRACE_SECONDS = 5
EXIT_INVALID_PLAN = 2
EXIT_ZERO_SKIP_REQUIRED = 6
EXIT_INFRASTRUCTURE = 70
EXIT_BUSY = 75
EXIT_TIMEOUT = 124
STAGE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
LOCK_METADATA_BYTES = 512
WINDOWS_CREATE_SUSPENDED = 0x00000004
WINDOWS_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE = 0x00002000


class StageError(RuntimeError):
    def __init__(self, category: str, code: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True)
class StagePlan:
    repo_root: Path
    head_sha: str
    stage: str
    selectors: tuple[str, ...]
    timeout_seconds: int
    require_zero_skips: bool
    run_id: str
    run_root: Path
    basetemp: Path
    junit: Path
    evidence: Path


class WorktreeLock:
    def __init__(self, path: Path, *, stage: str) -> None:
        self._path = path
        self._stage = stage
        self._file: BinaryIO | None = None

    def __enter__(self) -> WorktreeLock:
        lock_fd: int | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
            lock_file = os.fdopen(lock_fd, "r+b")
            lock_fd = None
        except OSError as error:
            if lock_fd is not None:
                os.close(lock_fd)
            raise StageError(
                "infrastructure_failure",
                "test_runner_lock_unavailable",
                "unable to create the worktree test lock",
                EXIT_INFRASTRUCTURE,
            ) from error
        try:
            lock_file.seek(0)
            self._lock(lock_file)
        except BlockingIOError as error:
            lock_file.close()
            self._raise_busy(error)
        except PermissionError as error:
            lock_file.close()
            if os.name == "nt":
                self._raise_busy(error)
            raise StageError(
                "infrastructure_failure",
                "test_runner_lock_unavailable",
                "unable to acquire the worktree test lock",
                EXIT_INFRASTRUCTURE,
            ) from error
        except OSError as error:
            lock_file.close()
            raise StageError(
                "infrastructure_failure",
                "test_runner_lock_unavailable",
                "unable to acquire the worktree test lock",
                EXIT_INFRASTRUCTURE,
            ) from error
        try:
            metadata = json.dumps(
                {"pid": os.getpid(), "stage": self._stage, "started_at": _utc_now()},
                sort_keys=True,
            ).encode("utf-8")
            if len(metadata) > LOCK_METADATA_BYTES:
                raise OSError("worktree test lock metadata is too large")
            lock_file.seek(0)
            lock_file.write(b"\0" + metadata.ljust(LOCK_METADATA_BYTES, b" "))
            lock_file.flush()
            os.fsync(lock_file.fileno())
        except OSError as error:
            self._unlock(lock_file)
            lock_file.close()
            raise StageError(
                "infrastructure_failure",
                "test_runner_lock_unavailable",
                "unable to write worktree test lock metadata",
                EXIT_INFRASTRUCTURE,
            ) from error
        self._file = lock_file
        return self

    @staticmethod
    def _lock(lock_file: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(lock_file: BinaryIO) -> None:
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _raise_busy(error: OSError) -> NoReturn:
        raise StageError(
            "infrastructure_failure",
            "test_runner_busy",
            "another local test stage already owns this worktree",
            EXIT_BUSY,
        ) from error

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        lock_file = self._file
        if lock_file is None:
            return
        try:
            self._unlock(lock_file)
        finally:
            lock_file.close()
            self._file = None


class OwnedProcess:
    def __init__(self, command: list[str], *, cwd: Path) -> None:
        self._job_handle: int | None = None
        kwargs: dict[str, object] = {"cwd": cwd}
        if os.name == "nt":
            kwargs["creationflags"] = _windows_creation_flags()
        elif os.name == "posix":
            kwargs["start_new_session"] = True
        try:
            self.process = subprocess.Popen(command, **kwargs)
            if os.name == "nt":
                self._job_handle = _create_windows_job(self.process)
                _resume_windows_process(self.process)
        except BaseException:
            process = getattr(self, "process", None)
            if process is not None:
                self.terminate(force=True)
            self.close()
            raise

    def terminate(self, *, force: bool) -> None:
        if os.name == "nt" and self._job_handle is not None:
            if _terminate_windows_job(self._job_handle):
                return
        if os.name == "posix":
            try:
                os.killpg(self.process.pid, signal.SIGKILL if force else signal.SIGTERM)
                return
            except ProcessLookupError:
                return
            except OSError:
                pass
        if os.name == "nt":
            try:
                completed = subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=TERMINATION_GRACE_SECONDS,
                )
                if completed.returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            self.process.kill() if force else self.process.terminate()
        except OSError:
            pass

    def cleanup_descendants(self) -> None:
        if os.name == "nt":
            self.close()
            return
        if os.name == "posix":
            self.terminate(force=False)
            time.sleep(0.05)
            self.terminate(force=True)

    def close(self) -> None:
        if os.name != "nt" or self._job_handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        job_handle = self._job_handle
        self._job_handle = None
        if not close_handle(ctypes.c_void_p(job_handle)):
            raise OSError(
                ctypes.get_last_error(), "unable to close test-stage Windows Job Object"
            )


def _windows_creation_flags() -> int:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    is_process_in_job = kernel32.IsProcessInJob
    is_process_in_job.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    is_process_in_job.restype = wintypes.BOOL
    in_job = wintypes.BOOL()
    if not is_process_in_job(get_current_process(), None, ctypes.byref(in_job)):
        raise OSError(
            ctypes.get_last_error(), "unable to inspect parent Windows Job Object"
        )
    flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | WINDOWS_CREATE_SUSPENDED
    )
    if in_job.value:
        flags |= WINDOWS_CREATE_BREAKAWAY_FROM_JOB
    return flags


def _create_windows_job(process: subprocess.Popen[bytes]) -> int:
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    assign_process = kernel32.AssignProcessToJobObject
    assign_process.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    job = create_job(None, None)
    if not job:
        raise OSError(
            ctypes.get_last_error(), "unable to create test-stage Windows Job Object"
        )
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE
    if not set_information(
        job,
        WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = ctypes.get_last_error()
        close_handle(job)
        raise OSError(error, "unable to configure test-stage Windows Job Object")
    process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
    if not assign_process(job, process_handle):
        error = ctypes.get_last_error()
        close_handle(job)
        raise OSError(error, "unable to assign pytest to test-stage Windows Job Object")
    return int(job)


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    ntdll = ctypes.WinDLL("ntdll")
    resume_process = ntdll.NtResumeProcess
    resume_process.argtypes = [ctypes.c_void_p]
    resume_process.restype = ctypes.c_long
    status = int(resume_process(ctypes.c_void_p(int(getattr(process, "_handle")))))
    if status != 0:
        raise OSError(f"unable to resume pytest: NTSTATUS 0x{status & 0xFFFFFFFF:08x}")


def _terminate_windows_job(job_handle: int) -> bool:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        terminate_job = kernel32.TerminateJobObject
        terminate_job.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        terminate_job.restype = ctypes.c_int
        return bool(terminate_job(ctypes.c_void_p(job_handle), 1))
    except (AttributeError, OSError):
        return False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run_git(repo_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise StageError(
            "infrastructure_failure",
            "git_unavailable",
            "unable to resolve the target Git worktree",
            EXIT_INFRASTRUCTURE,
        ) from error
    return completed.stdout.strip()


def _build_plan(arguments: argparse.Namespace) -> StagePlan:
    cwd = Path.cwd().resolve()
    top_level = Path(_run_git(cwd, "rev-parse", "--show-toplevel")).resolve()
    if cwd != top_level:
        raise StageError(
            "invalid_test_plan",
            "wrong_worktree_root",
            "run the local test stage from the target worktree root",
            EXIT_INVALID_PLAN,
        )
    if not STAGE_NAME.fullmatch(arguments.stage):
        raise StageError(
            "invalid_test_plan",
            "invalid_stage_name",
            "stage must contain only lowercase letters, digits, and hyphens",
            EXIT_INVALID_PLAN,
        )
    selectors = tuple(arguments.selectors)
    if not selectors or len(set(selectors)) != len(selectors):
        raise StageError(
            "invalid_test_plan",
            "invalid_test_selectors",
            "provide one or more unique explicit test selectors",
            EXIT_INVALID_PLAN,
        )
    tests_root = (top_level / "tests").resolve()
    for selector in selectors:
        if selector.startswith("-"):
            raise StageError(
                "invalid_test_plan",
                "pytest_option_forbidden",
                "pytest options are controlled by the local test-stage runner",
                EXIT_INVALID_PLAN,
            )
        source = selector.split("::", 1)[0]
        source_path = Path(source)
        if source_path.is_absolute():
            raise StageError(
                "invalid_test_plan",
                "absolute_selector_forbidden",
                "test selectors must be worktree-relative",
                EXIT_INVALID_PLAN,
            )
        resolved = (top_level / source_path).resolve()
        try:
            resolved.relative_to(tests_root)
        except ValueError as error:
            raise StageError(
                "invalid_test_plan",
                "selector_outside_tests",
                "test selectors must resolve below the worktree tests directory",
                EXIT_INVALID_PLAN,
            ) from error
        if resolved.suffix != ".py" or not resolved.is_file():
            raise StageError(
                "invalid_test_plan",
                "test_selector_missing",
                f"test selector does not name an existing Python test file: {source}",
                EXIT_INVALID_PLAN,
            )
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    run_root = top_level / ".pytest-tmp" / "test-runs" / run_id / arguments.stage
    return StagePlan(
        repo_root=top_level,
        head_sha=_run_git(top_level, "rev-parse", "HEAD"),
        stage=arguments.stage,
        selectors=selectors,
        timeout_seconds=arguments.timeout_seconds,
        require_zero_skips=arguments.require_zero_skips,
        run_id=run_id,
        run_root=run_root,
        basetemp=run_root / "basetemp",
        junit=run_root / "junit.xml",
        evidence=run_root / "evidence.json",
    )


def _pytest_counts(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    names = ("tests", "failures", "errors", "skipped")
    return {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in names
    }


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    timed_out: bool
    interrupted: bool
    cleanup: str
    cleanup_failed: bool


@dataclass(frozen=True)
class ClassifiedResult:
    returncode: int
    status: str
    category: str | None
    code: str | None


def _stop_process(process_tree: OwnedProcess) -> None:
    process_tree.terminate(force=False)
    try:
        process_tree.process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process_tree.terminate(force=True)
        try:
            process_tree.process.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def _wait_for_process(
    process_tree: OwnedProcess, plan: StagePlan, *, started: float
) -> tuple[int, bool]:
    next_heartbeat = started + HEARTBEAT_SECONDS
    deadline = started + plan.timeout_seconds
    while process_tree.process.poll() is None:
        now = time.monotonic()
        if now >= deadline:
            _stop_process(process_tree)
            return EXIT_TIMEOUT, True
        if now >= next_heartbeat:
            print(
                f"[local-test-stage] stage={plan.stage} pid={process_tree.process.pid} "
                f"elapsed_seconds={int(now - started)}",
                flush=True,
            )
            next_heartbeat = now + HEARTBEAT_SECONDS
        time.sleep(min(0.1, max(0.0, deadline - now)))
    return int(process_tree.process.wait()), False


def _execute_process(
    command: list[str], plan: StagePlan, *, started: float
) -> ProcessResult:
    process_tree: OwnedProcess | None = None
    returncode = EXIT_INFRASTRUCTURE
    timed_out = False
    interrupted = False
    cleanup = "not_started"
    cleanup_failed = False
    try:
        process_tree = OwnedProcess(command, cwd=plan.repo_root)
        returncode, timed_out = _wait_for_process(process_tree, plan, started=started)
    except KeyboardInterrupt:
        interrupted = True
        returncode = 130
        if process_tree is not None:
            _stop_process(process_tree)
    except (OSError, subprocess.SubprocessError) as error:
        if process_tree is not None:
            process_tree.terminate(force=True)
        print(
            f"unable to run pytest stage: {type(error).__name__}",
            file=sys.stderr,
            flush=True,
        )
    finally:
        if process_tree is not None:
            try:
                process_tree.cleanup_descendants()
                process_tree.close()
                cleanup = "completed"
            except OSError as error:
                cleanup = "failed"
                cleanup_failed = True
                print(
                    f"unable to clean pytest process tree: {type(error).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
    return ProcessResult(
        returncode=returncode,
        timed_out=timed_out,
        interrupted=interrupted,
        cleanup=cleanup,
        cleanup_failed=cleanup_failed,
    )


def _classify_result(
    process: ProcessResult,
    counts: dict[str, int] | None,
    *,
    require_zero_skips: bool,
) -> ClassifiedResult:
    returncode = process.returncode
    if process.timed_out:
        values = ("timed_out", "test_timeout", "stage_timeout")
    elif process.interrupted:
        values = ("interrupted", "infrastructure_failure", "stage_interrupted")
    elif process.cleanup_failed and returncode == 0:
        values = ("failed", "infrastructure_failure", "process_cleanup_failed")
        returncode = EXIT_INFRASTRUCTURE
    elif returncode == 0 and counts is None:
        values = ("failed", "infrastructure_failure", "junit_missing")
        returncode = EXIT_INFRASTRUCTURE
    elif returncode == 0 and require_zero_skips and counts and counts["skipped"]:
        values = ("failed", "required_dependency_missing", "skipped_tests_forbidden")
        returncode = EXIT_ZERO_SKIP_REQUIRED
    elif returncode == 0 and counts and counts["skipped"]:
        values = ("passed_with_skips", None, None)
    elif returncode == 0:
        values = ("passed", None, None)
    elif returncode == 1:
        values = ("failed", "product_test_failure", "pytest_failed")
    elif returncode in {4, 5}:
        values = ("failed", "invalid_test_plan", "pytest_selection_failed")
    else:
        values = ("failed", "infrastructure_failure", "pytest_unavailable")
    return ClassifiedResult(returncode, *values)


def _run_stage(plan: StagePlan) -> int:
    plan.run_root.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *plan.selectors,
        "-vv",
        "-ra",
        "--basetemp",
        str(plan.basetemp),
        "--junitxml",
        str(plan.junit),
    ]
    started_at = _utc_now()
    started = time.monotonic()
    process = _execute_process(command, plan, started=started)
    counts = _pytest_counts(plan.junit)
    result = _classify_result(
        process,
        counts,
        require_zero_skips=plan.require_zero_skips,
    )
    evidence: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": plan.run_id,
        "stage": plan.stage,
        "status": result.status,
        "category": result.category,
        "code": result.code,
        "returncode": result.returncode,
        "repo_root": str(plan.repo_root),
        "head_sha": plan.head_sha,
        "selectors": list(plan.selectors),
        "command": command,
        "timeout_seconds": plan.timeout_seconds,
        "require_zero_skips": plan.require_zero_skips,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "pytest": counts,
        "cleanup": process.cleanup,
        "paths": {
            "basetemp": _relative(plan.basetemp, plan.repo_root),
            "junit": _relative(plan.junit, plan.repo_root),
            "evidence": _relative(plan.evidence, plan.repo_root),
        },
    }
    _write_json(plan.evidence, evidence)
    print(
        f"[local-test-stage] evidence={_relative(plan.evidence, plan.repo_root)}",
        flush=True,
    )
    return result.returncode


def _bounded_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be an integer") from error
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds"
        )
    return timeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=_bounded_timeout,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--require-zero-skips", action="store_true")
    parser.add_argument("selectors", nargs="+")
    return parser


def _fail(error: StageError) -> NoReturn:
    print(
        json.dumps(
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "status": "failed",
                "category": error.category,
                "code": error.code,
                "message": str(error),
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    raise SystemExit(error.exit_code)


def main() -> int:
    try:
        arguments = _parser().parse_args()
        plan = _build_plan(arguments)
        lock_path = plan.repo_root / ".pytest-tmp" / ".run-test-stage.lock"
        with WorktreeLock(lock_path, stage=plan.stage):
            return _run_stage(plan)
    except StageError as error:
        _fail(error)


if __name__ == "__main__":
    raise SystemExit(main())
