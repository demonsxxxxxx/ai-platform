"""Clean-commit deployment, dirty-source preservation, and runtime parity checks."""

from __future__ import annotations

import argparse
import codecs
from collections import OrderedDict, deque
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import posixpath
import re
import shlex
import signal
import stat
import subprocess
import tarfile
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence
from urllib.request import urlopen


SCHEMA_VERSION = "ai-platform.release-authority.v1"
PRESERVATION_SCHEMA_VERSION = "ai-platform.release-authority-preservation.v1"
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_DIRECTORY_RE = re.compile(r"^[0-9a-f]{7,40}$")
DOCKER_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_COMPOSE_RELATIVE_PATH = Path("deploy/ai-platform/docker-compose.yml")
DEFAULT_MANAGED_ENV_RELATIVE_PATH = Path("deploy/ai-platform/.env")
MANAGED_RELEASE_DIRECTORY_NAME = "releases"
SANDBOX_COMPOSE_RELATIVE_PATH = "deploy/ai-platform/docker-compose.sandbox.yml"
OPENSANDBOX_COMPOSE_RELATIVE_PATH = "deploy/ai-platform/docker-compose.opensandbox.yml"
PROVIDER_OVERLAY_COMPOSE_SELECTIONS = frozenset(
    {
        (DEFAULT_COMPOSE_RELATIVE_PATH.as_posix(), SANDBOX_COMPOSE_RELATIVE_PATH),
        (DEFAULT_COMPOSE_RELATIVE_PATH.as_posix(), OPENSANDBOX_COMPOSE_RELATIVE_PATH),
    }
)
COMPOSE_PROJECT = "ai-platform-phaseb"
WORKER_HEARTBEAT_FILENAME = "ai-platform-worker-runtime-heartbeat.json"
WORKER_TMPDIR_EXPANSION_MARKERS = frozenset("*?$`[]{}")
WORKER_TMPDIR_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
AUTHORITATIVE_REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
AUTHORITATIVE_REPOSITORY_ALIASES = {
    AUTHORITATIVE_REPOSITORY,
    "git@github.com:demonsxxxxxx/ai-platform.git",
    "ssh://git@github.com/demonsxxxxxx/ai-platform.git",
}
SECRET_PATH_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
COMPATIBILITY_IMAGE_COMMIT_LABELS = (
    "ai-platform.source-revision",
    "ai-platform.runtime-subject",
    "ai-platform.source_revision",
    "ai-platform.source_commit",
    "ai-platform.runtime_subject",
    "ai-platform.source_tree_commit",
    "ai_platform_source_revision",
    "ai_platform_source_commit",
    "ai_platform_runtime_subject",
    "ai_platform_source_tree_commit",
)
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 300
HTTP_PROBE_TIMEOUT_SECONDS = 15
BACKEND_STAGE_TIMEOUT_SECONDS = 90
FRONTEND_STAGE_TIMEOUT_SECONDS = 180
DEFAULT_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS = 1800
CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS = DEFAULT_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS
MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS = 300
MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS = 3600
PROCESS_TREE_TERMINATION_GRACE_SECONDS = 1
WINDOWS_CREATE_SUSPENDED = 0x00000004
BUILD_PROGRESS_READ_CHUNK_BYTES = 16 * 1024
BUILD_PROGRESS_MAX_LINE_BYTES = 4096
BUILD_PROGRESS_MAX_LINE_COUNT = 1_000_000
BUILD_PROGRESS_MAX_TRACKED_STEPS = 128
BUILD_PROGRESS_MAX_STEP_ORDINAL = 9999
BUILD_PROGRESS_MAX_TAIL_LINES = 512
BUILD_DIAGNOSTIC_SCAN_OVERLAP_BYTES = 4096
BACKEND_DEPENDENCY_PATHS = frozenset({"pyproject.toml", "Dockerfile"})
FRONTEND_DEPENDENCY_PATHS = frozenset(
    {
        "frontend/web/.npmrc",
        "frontend/web/package.json",
        "frontend/web/pnpm-lock.yaml",
        "frontend/web/pnpm-workspace.yaml",
        "frontend/web/Dockerfile",
    }
)
BACKEND_SOURCE_PREFIXES = (
    "app/",
    "tools/",
    "scripts/",
    "skills/",
    "docs/release-evidence/",
)
BACKEND_SOURCE_PATHS = frozenset({"docker-entrypoint.sh"})
FRONTEND_SOURCE_PREFIX = "frontend/web/"


class ReleaseAuthorityError(RuntimeError):
    """Raised when a release-authority invariant is not satisfied."""


@dataclass(frozen=True)
class RuntimeChangeSet:
    """Classified runtime-affecting paths between a verified live commit and target."""

    backend_dependency: tuple[str, ...]
    backend_source: tuple[str, ...]
    frontend_dependency: tuple[str, ...]
    frontend_source: tuple[str, ...]
    deployment_only: tuple[str, ...]


@dataclass(frozen=True)
class RolePlan:
    """One deterministic role action selected from a classified change set."""

    role: str
    change_kind: str
    action: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class AutoReleasePlan:
    """Compact, role-specific release plan for one current-runtime to target transition."""

    current_commit: str
    target_commit: str
    changes: RuntimeChangeSet
    roles: tuple[RolePlan, ...]
    no_runtime_change: bool


@dataclass(frozen=True)
class _ComposeSelection:
    checkout_root: Path
    relative_paths: tuple[str, ...]
    absolute_paths: tuple[Path, ...]
    working_dir: str
    config_files: str


@dataclass(frozen=True)
class _ManagedContainerOwnership:
    """One preflight snapshot of existing release-authority container ownership."""

    compose_selection: _ComposeSelection | None
    compose_roles: tuple[str, ...]
    manual_frontend_id: str | None


@dataclass
class _BuildProgressStep:
    """Bounded allowlisted state retained for one observed BuildKit step."""

    ordinal: int
    total: int | None
    stage_kind: str
    instruction_category: str
    last_timestamp_seconds: float | None = None
    last_progress_units: float | None = None
    advancing: bool = False


class _BuildProgressClassifier:
    """Stream BuildKit stdout into fixed categories without retaining raw output."""

    _ANSI_ESCAPE_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
    _HEADER_RE = re.compile(
        rb"^#(?P<ordinal>[1-9][0-9]{0,3})(?:\s+\[(?P<label>[^\]\r\n]{1,1024})\])?\s+",
    )
    _TIMESTAMP_RE = re.compile(rb"^#(?P<ordinal>[1-9][0-9]{0,3})\s+(?P<seconds>[0-9]{1,9}(?:\.[0-9]{1,3})?)\s")
    _BYTE_PROGRESS_RE = re.compile(
        rb"(?P<current>[0-9]{1,12}(?:\.[0-9]{1,3})?)"
        rb"\s*(?P<unit>[KMGT]?B)\s*/\s*"
        rb"(?P<total>[0-9]{1,12}(?:\.[0-9]{1,3})?)\s*(?P=unit)(?:\s|$)",
        re.IGNORECASE,
    )
    _COUNT_PROGRESS_RE = re.compile(
        rb"(?<![0-9.])(?P<current>[0-9]{1,12})\s*/\s*(?P<total>[0-9]{1,12})(?:\s|$)",
    )
    _SECRET_MARKERS = (
        b"authorization",
        b"password",
        b"passwd",
        b"secret",
        b"token",
        b"api_key",
        b"api-key",
        b"private key",
        b"credential",
    )

    def __init__(self) -> None:
        self.line_count = 0
        self._buffer = bytearray()
        self._discarding_line = False
        self._unsafe = False
        self._latest_structural_step_unclassifiable = False
        self._steps: OrderedDict[int, _BuildProgressStep] = OrderedDict()

    def feed(self, chunk: str | bytes | None) -> None:
        """Consume one stdout chunk while bounding line memory and parsed state."""
        if not chunk:
            return
        data = chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else bytes(chunk)
        for byte in data:
            if byte == 10:
                self.line_count = min(self.line_count + 1, BUILD_PROGRESS_MAX_LINE_COUNT)
                if not self._discarding_line:
                    self._consume_line(bytes(self._buffer).rstrip(b"\r"))
                self._buffer.clear()
                self._discarding_line = False
            elif not self._discarding_line:
                if len(self._buffer) < BUILD_PROGRESS_MAX_LINE_BYTES:
                    self._buffer.append(byte)
                else:
                    self._buffer.clear()
                    self._discarding_line = True

    def finish(self) -> None:
        """Finalize a trailing partial line after the process pipes have drained."""
        if self._buffer or self._discarding_line:
            self.line_count = min(self.line_count + 1, BUILD_PROGRESS_MAX_LINE_COUNT)
            if not self._discarding_line:
                self._consume_line(bytes(self._buffer).rstrip(b"\r"))
        self._buffer.clear()
        self._discarding_line = False

    def summary(self) -> dict[str, Any]:
        """Return only fixed allowlist values and bounded numeric progress facts."""
        if self._unsafe or self._latest_structural_step_unclassifiable or not self._steps:
            return {"build_progress_status": "unknown"}
        step = next(reversed(self._steps.values()))
        summary: dict[str, Any] = {
            "build_progress_status": "recognized",
            "step_ordinal": step.ordinal,
            "stage_kind": step.stage_kind,
            "instruction_category": step.instruction_category,
            "line_count": self.line_count,
            "advancing": step.advancing,
        }
        if step.total is not None:
            summary["step_total"] = step.total
        if step.last_timestamp_seconds is not None:
            summary["last_progress_timestamp_seconds"] = step.last_timestamp_seconds
        return summary

    def _consume_line(self, line: bytes) -> None:
        cleaned = self._ANSI_ESCAPE_RE.sub(b"", line).strip()
        if not cleaned:
            return
        lowered = cleaned.lower()
        if any(marker in lowered for marker in self._SECRET_MARKERS):
            self._unsafe = True
            self._steps.clear()
            return
        timestamp = self._TIMESTAMP_RE.match(cleaned)
        if timestamp is not None:
            self._consume_progress(timestamp, cleaned[timestamp.end() :])
            return
        header = self._HEADER_RE.match(cleaned)
        if header is not None:
            self._consume_header(header, cleaned[header.end() :].strip())

    def _consume_header(self, match: re.Match[bytes], instruction: bytes) -> None:
        ordinal = int(match.group("ordinal"))
        if ordinal > BUILD_PROGRESS_MAX_STEP_ORDINAL:
            return
        label = match.group("label")
        stage_kind = self._classify_stage(label)
        total: int | None = None
        if label is not None:
            total_match = re.search(rb"(?:^|\s)[1-9][0-9]{0,3}/(?P<total>[1-9][0-9]{0,3})(?:\s|$)", label)
            if total_match is not None:
                total = min(int(total_match.group("total")), BUILD_PROGRESS_MAX_STEP_ORDINAL)
            stage_instruction = re.sub(
                rb"(?:^|\s)[1-9][0-9]{0,3}/[1-9][0-9]{0,3}(?:\s|$)",
                b" ",
                label,
            ).strip()
            if self._classify_stage(label) != "unknown":
                stage_instruction = b""
            instruction = stage_instruction + b" " + instruction
        instruction_category = self._classify_instruction(instruction.strip())
        if instruction_category == "unknown":
            if label is not None:
                self._latest_structural_step_unclassifiable = True
            return
        self._latest_structural_step_unclassifiable = False
        self._steps[ordinal] = _BuildProgressStep(
            ordinal=ordinal,
            total=total,
            stage_kind=stage_kind,
            instruction_category=instruction_category,
        )
        self._steps.move_to_end(ordinal)
        while len(self._steps) > BUILD_PROGRESS_MAX_TRACKED_STEPS:
            self._steps.popitem(last=False)

    def _consume_progress(self, match: re.Match[bytes], payload: bytes) -> None:
        ordinal = int(match.group("ordinal"))
        step = self._steps.get(ordinal)
        if step is None:
            return
        timestamp = min(float(match.group("seconds")), 1_000_000_000.0)
        step.last_timestamp_seconds = timestamp
        progress = self._progress_units(payload)
        if progress is not None:
            if step.last_progress_units is not None and progress > step.last_progress_units:
                step.advancing = True
            step.last_progress_units = progress
        self._steps.move_to_end(ordinal)

    @staticmethod
    def _classify_stage(label: bytes | None) -> str:
        if label is None:
            return "unknown"
        lowered = re.sub(rb"\s+[1-9][0-9]{0,3}/[1-9][0-9]{0,3}$", b"", label.lower()).strip()
        if lowered == b"internal":
            return "internal"
        if lowered == b"source-markers":
            return "source-markers"
        if lowered == b"runtime":
            return "runtime"
        if lowered == b"build":
            return "build"
        return "unknown"

    @staticmethod
    def _classify_instruction(instruction: bytes) -> str:
        normalized = b" ".join(instruction.lower().split())
        normalized = re.sub(rb"^[1-9][0-9]{0,3}/[1-9][0-9]{0,3}\s+", b"", normalized)
        for prefix, category in (
            (b"load build definition", "load-build-definition"),
            (b"load metadata for", "load-base-metadata"),
            (b"load .dockerignore", "load-dockerignore"),
            (b"load build context", "load-build-context"),
            (b"from ", "from"),
            (b"workdir ", "workdir"),
            (b"arg ", "arg"),
            (b"env ", "env"),
            (b"label ", "label"),
            (b"user ", "user"),
            (b"entrypoint ", "entrypoint"),
            (b"cmd ", "cmd"),
            (b"expose ", "expose"),
            (b"healthcheck ", "healthcheck"),
        ):
            if normalized.startswith(prefix):
                return category
        if normalized.startswith(b"copy "):
            if b"pyproject.toml" in normalized or b"package.json" in normalized or b"pnpm-lock.yaml" in normalized:
                return "copy-manifest"
            if b"--from=" in normalized:
                return "copy-from-stage"
            return "copy-source"
        if normalized.startswith(b"run "):
            if b"apt-get" in normalized:
                return "run-apt"
            if b"pip install" in normalized:
                return "run-pip-install"
            if b"pnpm install" in normalized:
                return "run-pnpm-install"
            if b"ci:verify" in normalized:
                return "run-frontend-verify"
            return "unknown"
        return "unknown"

    def _progress_units(self, payload: bytes) -> float | None:
        byte_progress = self._BYTE_PROGRESS_RE.search(payload)
        if byte_progress is not None:
            scale = {b"b": 1.0, b"kb": 1_000.0, b"mb": 1_000_000.0, b"gb": 1_000_000_000.0, b"tb": 1_000_000_000_000.0}
            return min(
                float(byte_progress.group("current")) * scale[byte_progress.group("unit").lower()],
                1_000_000_000_000_000.0,
            )
        count_progress = self._COUNT_PROGRESS_RE.search(payload)
        if count_progress is not None:
            return min(float(count_progress.group("current")), 1_000_000_000_000.0)
        return None


class _BoundedBuildProgressCapture:
    """Drain raw build stdout into a fixed-size in-memory window for later classification."""

    _STRUCTURAL_HEADER_RE = re.compile(rb"^#[1-9][0-9]{0,3}(?:\s+\[[^\]\r\n]{1,1024}\])?\s+")

    def __init__(self) -> None:
        self.line_count = 0
        self._buffer = bytearray()
        self._discarding_line = False
        self._unsafe = False
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._utf8_invalid = False
        self._scan_tail = b""
        self._headers: OrderedDict[int, bytes] = OrderedDict()
        self._tail: deque[bytes] = deque(maxlen=BUILD_PROGRESS_MAX_TAIL_LINES)

    def feed(self, chunk: bytes) -> None:
        """Capture one bytes chunk without decoding partial UTF-8 or growing without bound."""
        if not chunk:
            return
        if not self._utf8_invalid:
            try:
                self._utf8_decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                self._utf8_invalid = True
                self._unsafe = True
        scan = (self._scan_tail + chunk).lower()
        if any(marker in scan for marker in _BuildProgressClassifier._SECRET_MARKERS):
            self._unsafe = True
        marker_overlap = max(len(marker) for marker in _BuildProgressClassifier._SECRET_MARKERS) - 1
        self._scan_tail = scan[-marker_overlap:]
        for byte in chunk:
            if byte == 10:
                self.line_count = min(self.line_count + 1, BUILD_PROGRESS_MAX_LINE_COUNT)
                if not self._discarding_line:
                    self._capture_line(bytes(self._buffer).rstrip(b"\r"))
                self._buffer.clear()
                self._discarding_line = False
            elif not self._discarding_line:
                if len(self._buffer) < BUILD_PROGRESS_MAX_LINE_BYTES:
                    self._buffer.append(byte)
                else:
                    self._buffer.clear()
                    self._discarding_line = True
                    self._unsafe = True

    def finish(self) -> None:
        """Capture a trailing partial line only after the stdout reader has stopped."""
        if not self._utf8_invalid:
            try:
                self._utf8_decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                self._utf8_invalid = True
                self._unsafe = True
        if self._buffer or self._discarding_line:
            self.line_count = min(self.line_count + 1, BUILD_PROGRESS_MAX_LINE_COUNT)
            self._unsafe = True
        self._buffer.clear()
        self._discarding_line = False
        self._scan_tail = b""

    def classify(self) -> dict[str, Any]:
        """Classify only after process cleanup/drain, returning no captured raw text."""
        if self._unsafe:
            return {"build_progress_status": "unknown"}
        classifier = _BuildProgressClassifier()
        for header in self._headers.values():
            classifier.feed(header + b"\n")
        for line in self._tail:
            classifier.feed(line + b"\n")
        classifier.finish()
        classifier.line_count = self.line_count
        return classifier.summary()

    def _capture_line(self, line: bytes) -> None:
        self._tail.append(line)
        match = self._STRUCTURAL_HEADER_RE.match(_BuildProgressClassifier._ANSI_ESCAPE_RE.sub(b"", line).strip())
        if match is None:
            return
        ordinal_match = re.match(rb"^#(?P<ordinal>[1-9][0-9]{0,3})", match.group())
        if ordinal_match is None:
            return
        ordinal = int(ordinal_match.group("ordinal"))
        self._headers[ordinal] = line
        self._headers.move_to_end(ordinal)
        while len(self._headers) > BUILD_PROGRESS_MAX_TRACKED_STEPS:
            self._headers.popitem(last=False)


class _BoundedStderrDiagnosticCapture:
    """Scan stderr into fixed diagnostic phrases without retaining its raw output."""

    def __init__(self) -> None:
        self._has_data = False
        self._unsafe = False
        self._scan_tail = b""
        self._recognized: set[str] = set()

    def feed(self, chunk: bytes) -> None:
        """Scan a bounded overlap window so total stderr size never controls memory use."""
        if not chunk:
            return
        self._has_data = True
        scan = self._scan_tail + chunk
        lowered = scan.lower()
        if any(marker in lowered for marker in _BuildProgressClassifier._SECRET_MARKERS):
            self._unsafe = True
        text = scan.decode("utf-8", errors="replace")
        for pattern, summary in _SAFE_STDERR_DIAGNOSTICS:
            if pattern.search(text):
                self._recognized.add(summary)
        self._scan_tail = scan[-BUILD_DIAGNOSTIC_SCAN_OVERLAP_BYTES:]

    def summary(self) -> dict[str, Any]:
        """Return the existing fixed stderr diagnostic schema."""
        if not self._has_data:
            return {"stderr_status": "empty"}
        if self._unsafe:
            return {"stderr_status": "redacted"}
        for _, summary in _SAFE_STDERR_DIAGNOSTICS:
            if summary in self._recognized:
                return {"stderr_status": "recognized", "stderr_summary": summary}
        return {"stderr_status": "redacted"}


def _communicate_with_bounded_build_progress(
    process: subprocess.Popen[Any],
    *,
    timeout: int,
    text: bool,
    windows_job_handle: int | None,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """Drain build stdout concurrently and classify it only after exit or owned-tree cleanup."""
    capture = _BoundedBuildProgressCapture()
    assert process.stdout is not None
    assert process.stderr is not None
    stderr_capture = _BoundedStderrDiagnosticCapture()
    reader_errors: list[BaseException] = []

    def drain_stdout() -> None:
        try:
            while True:
                chunk = process.stdout.read(BUILD_PROGRESS_READ_CHUNK_BYTES)
                if not chunk:
                    break
                capture.feed(chunk)
        except BaseException as exc:
            reader_errors.append(exc)

    def drain_stderr() -> None:
        try:
            while True:
                chunk = process.stderr.read(BUILD_PROGRESS_READ_CHUNK_BYTES)
                if not chunk:
                    break
                stderr_capture.feed(chunk)
        except BaseException as exc:
            reader_errors.append(exc)

    stdout_reader = threading.Thread(target=drain_stdout, daemon=True)
    stderr_reader = threading.Thread(target=drain_stderr, daemon=True)
    stdout_reader.start()
    stderr_reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_owned_process_tree(
            process,
            force=False,
            windows_job_handle=windows_job_handle,
        )
        try:
            process.wait(timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS)
        except BaseException:
            _terminate_owned_process_tree(
                process,
                force=True,
                windows_job_handle=windows_job_handle,
            )
            try:
                process.wait(timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS)
            except BaseException:
                pass
    drain_deadline = time.monotonic() + PROCESS_TREE_TERMINATION_GRACE_SECONDS
    for reader in (stdout_reader, stderr_reader):
        reader.join(max(0.0, drain_deadline - time.monotonic()))
    drain_complete = not stdout_reader.is_alive() and not stderr_reader.is_alive()
    if not drain_complete:
        _terminate_owned_process_tree(
            process,
            force=False,
            windows_job_handle=windows_job_handle,
        )
        try:
            process.wait(timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS)
        except BaseException:
            pass
        drain_deadline = time.monotonic() + PROCESS_TREE_TERMINATION_GRACE_SECONDS
        for reader in (stdout_reader, stderr_reader):
            reader.join(max(0.0, drain_deadline - time.monotonic()))
        drain_complete = not stdout_reader.is_alive() and not stderr_reader.is_alive()
    if not drain_complete:
        _terminate_owned_process_tree(
            process,
            force=True,
            windows_job_handle=windows_job_handle,
        )
        try:
            process.wait(timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS)
        except BaseException:
            pass
        drain_deadline = time.monotonic() + PROCESS_TREE_TERMINATION_GRACE_SECONDS
        for reader in (stdout_reader, stderr_reader):
            reader.join(max(0.0, drain_deadline - time.monotonic()))
        drain_complete = not stdout_reader.is_alive() and not stderr_reader.is_alive()
    if not drain_complete:
        _close_process_pipes(process)
        drain_deadline = time.monotonic() + PROCESS_TREE_TERMINATION_GRACE_SECONDS
        for reader in (stdout_reader, stderr_reader):
            reader.join(max(0.0, drain_deadline - time.monotonic()))
    capture.finish()
    drain_complete = not stdout_reader.is_alive() and not stderr_reader.is_alive()
    safe_build_progress = (
        capture.classify()
        if drain_complete and not reader_errors
        else {"build_progress_status": "unknown"}
    )
    safe_stderr = (
        stderr_capture.summary()
        if drain_complete and not reader_errors
        else {"stderr_status": "redacted"}
    )
    if timed_out:
        timeout_error = subprocess.TimeoutExpired([], timeout)
        setattr(timeout_error, "safe_stderr_diagnostic", safe_stderr)
        setattr(timeout_error, "safe_build_progress_diagnostic", safe_build_progress)
        raise timeout_error from None
    return ("" if text else b"", "" if text else b"", safe_build_progress, safe_stderr)

def _create_owned_windows_job() -> int | None:
    """Create the Windows Job Object that will retain the complete child tree."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    create_job.restype = ctypes.c_void_p
    handle = create_job(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "unable to create owned Windows Job Object")
    return int(handle)


def _close_owned_windows_job(job_handle: int | None) -> None:
    """Release an owned Job Object without terminating successful descendants."""
    if os.name != "nt" or job_handle is None:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        close_handle(ctypes.c_void_p(job_handle))
    except Exception:
        pass


def _assign_owned_windows_job(process: subprocess.Popen[Any], job_handle: int) -> None:
    """Assign a still-suspended child before it can create untracked descendants."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    assign_process = kernel32.AssignProcessToJobObject
    assign_process.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    assign_process.restype = ctypes.c_int
    process_handle = int(getattr(process, "_handle"))
    if not assign_process(ctypes.c_void_p(job_handle), ctypes.c_void_p(process_handle)):
        raise OSError(ctypes.get_last_error(), "unable to assign subprocess to owned Windows Job Object")


def _resume_owned_windows_process(process: subprocess.Popen[Any]) -> None:
    """Resume a Windows child only after Job Object assignment is complete."""
    ntdll = ctypes.WinDLL("ntdll")
    resume_process = ntdll.NtResumeProcess
    resume_process.argtypes = [ctypes.c_void_p]
    resume_process.restype = ctypes.c_long
    process_handle = int(getattr(process, "_handle"))
    status = int(resume_process(ctypes.c_void_p(process_handle)))
    if status != 0:
        raise OSError(f"unable to resume owned Windows subprocess: NTSTATUS 0x{status & 0xFFFFFFFF:08x}")


def _terminate_owned_windows_job(job_handle: int) -> bool:
    """Force every process assigned to one authority-owned Windows Job Object to exit."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        terminate_job = kernel32.TerminateJobObject
        terminate_job.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        terminate_job.restype = ctypes.c_int
        return bool(terminate_job(ctypes.c_void_p(job_handle), 1))
    except Exception:
        return False


def _owned_process_group_kwargs() -> dict[str, Any]:
    """Start each bounded subprocess in a group that this authority exclusively owns."""
    if os.name == "posix":
        return {"start_new_session": True}
    if os.name == "nt":
        return {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | WINDOWS_CREATE_SUSPENDED,
        }
    return {}


def _terminate_owned_process_tree(
    process: subprocess.Popen[Any],
    *,
    force: bool,
    windows_job_handle: int | None = None,
) -> None:
    """Signal only the process tree/session created by this authority's Popen call."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            pass
        else:
            return
    if os.name == "nt":
        if windows_job_handle is not None and _terminate_owned_windows_job(windows_job_handle):
            return
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill() if force else process.terminate()
    except OSError:
        pass


def _close_process_pipes(process: subprocess.Popen[Any]) -> None:
    """Close authority-owned pipes after bounded cleanup cannot drain them."""
    streams = [stream for stream in (process.stdin, process.stdout, process.stderr) if stream is not None]

    def close_stream(stream: Any) -> None:
        if os.name == "nt":
            try:
                import msvcrt

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                cancel_io = kernel32.CancelIoEx
                cancel_io.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                cancel_io.restype = ctypes.c_int
                os_handle = msvcrt.get_osfhandle(stream.fileno())
                cancel_io(ctypes.c_void_p(os_handle), None)
            except Exception:
                pass
        try:
            stream.close()
        except (OSError, ValueError):
            pass

    if os.name == "nt":
        closers = [threading.Thread(target=close_stream, args=(stream,), daemon=True) for stream in streams]
        for closer in closers:
            closer.start()
        deadline = time.monotonic() + PROCESS_TREE_TERMINATION_GRACE_SECONDS
        for closer in closers:
            closer.join(max(0.0, deadline - time.monotonic()))
        return
    for stream in streams:
        close_stream(stream)


def _terminate_and_drain_owned_process(
    process: subprocess.Popen[Any],
    *,
    windows_job_handle: int | None = None,
) -> tuple[Any, Any]:
    """Terminate an owned tree and bound every post-timeout pipe-drain attempt."""
    _terminate_owned_process_tree(
        process,
        force=False,
        windows_job_handle=windows_job_handle,
    )
    try:
        return process.communicate(timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS)
    except BaseException:
        pass
    _terminate_owned_process_tree(
        process,
        force=True,
        windows_job_handle=windows_job_handle,
    )
    try:
        return process.communicate(timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS)
    except BaseException:
        _close_process_pipes(process)
        try:
            process.wait(timeout=PROCESS_TREE_TERMINATION_GRACE_SECONDS)
        except BaseException:
            pass
        return (None, None)


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    text: bool = True,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
    input: str | bytes | None = None,
    classify_build_progress: bool = False,
) -> subprocess.CompletedProcess[Any]:
    if text and isinstance(input, (bytes, bytearray, memoryview)):
        raise TypeError("text mode input must be str, not bytes-like")
    arguments = list(command)
    windows_job_handle = _create_owned_windows_job()
    process: subprocess.Popen[Any] | None = None
    windows_job_assigned = False
    try:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                stdin=subprocess.PIPE if input is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=text and not classify_build_progress,
                env=env,
                **_owned_process_group_kwargs(),
            )
            if windows_job_handle is not None:
                _assign_owned_windows_job(process, windows_job_handle)
                windows_job_assigned = True
                _resume_owned_windows_process(process)
        except BaseException:
            if process is not None:
                try:
                    _terminate_and_drain_owned_process(
                        process,
                        windows_job_handle=windows_job_handle if windows_job_assigned else None,
                    )
                except BaseException:
                    pass
            raise

        if classify_build_progress:
            if input is not None:
                raise TypeError("build progress classification does not accept stdin")
            try:
                stdout, stderr, safe_build_progress_diagnostic, safe_stderr_diagnostic = (
                    _communicate_with_bounded_build_progress(
                        process,
                        timeout=timeout,
                        text=text,
                        windows_job_handle=(
                            windows_job_handle if windows_job_assigned else None
                        ),
                    )
                )
            except subprocess.TimeoutExpired as exc:
                timeout_error = subprocess.TimeoutExpired(arguments, timeout)
                setattr(
                    timeout_error,
                    "safe_stderr_diagnostic",
                    getattr(exc, "safe_stderr_diagnostic", {"stderr_status": "empty"}),
                )
                setattr(
                    timeout_error,
                    "safe_build_progress_diagnostic",
                    getattr(exc, "safe_build_progress_diagnostic", {"build_progress_status": "unknown"}),
                )
                raise timeout_error from None
            except BaseException:
                try:
                    _terminate_and_drain_owned_process(
                        process,
                        windows_job_handle=windows_job_handle if windows_job_assigned else None,
                    )
                except BaseException:
                    pass
                raise
            result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
            if check and result.returncode:
                failure = subprocess.CalledProcessError(
                    result.returncode,
                    arguments,
                    output=None,
                    stderr=stderr,
                )
                setattr(failure, "safe_build_progress_diagnostic", safe_build_progress_diagnostic)
                setattr(failure, "safe_stderr_diagnostic", safe_stderr_diagnostic)
                raise failure
            return result

        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            captured_stderr = exc.stderr
            try:
                _, drained_stderr = _terminate_and_drain_owned_process(
                    process,
                    windows_job_handle=windows_job_handle if windows_job_assigned else None,
                )
                captured_stderr = drained_stderr or captured_stderr
            except BaseException:
                pass
            safe_stderr_diagnostic = _redacted_stderr_diagnostic(captured_stderr)
            timeout_error = subprocess.TimeoutExpired(arguments, timeout)
            setattr(timeout_error, "safe_stderr_diagnostic", safe_stderr_diagnostic)
            raise timeout_error from None
        except BaseException:
            try:
                _terminate_and_drain_owned_process(
                    process,
                    windows_job_handle=windows_job_handle if windows_job_assigned else None,
                )
            except BaseException:
                pass
            raise
        result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        _close_owned_windows_job(windows_job_handle)


def _git(repo_root: Path, *args: str, text: bool = True) -> str | bytes:
    result = _run(["git", *args], cwd=repo_root, text=text)
    return result.stdout


def _normalize_commit(value: str) -> str:
    commit = value.strip().lower()
    if not FULL_COMMIT_RE.fullmatch(commit):
        raise ReleaseAuthorityError("release commit must be a full 40-character lowercase SHA")
    return commit


def _validate_canonical_dependency_build_timeout(value: int) -> int:
    """Require one finite, operationally bounded canonical dependency-build timeout."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReleaseAuthorityError("canonical dependency build timeout must be an integer number of seconds")
    if not (
        MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS
        <= value
        <= MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS
    ):
        raise ReleaseAuthorityError(
            "canonical dependency build timeout must be between "
            f"{MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS} and "
            f"{MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS} seconds"
        )
    return value


def _canonical_dependency_build_timeout_argument(value: str) -> int:
    """Parse the bounded canonical dependency-build timeout for argparse."""
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be an integer number of seconds between "
            f"{MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS} and "
            f"{MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS}"
        ) from exc
    try:
        return _validate_canonical_dependency_build_timeout(parsed)
    except ReleaseAuthorityError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def assert_clean_commit(repo_root: Path, requested_commit: str) -> str:
    """Require an immutable target checkout, including an empty ignored-file set."""
    repo_root = repo_root.resolve()
    commit = _normalize_commit(requested_commit)
    head = str(_git(repo_root, "rev-parse", "HEAD")).strip().lower()
    if head != commit:
        raise ReleaseAuthorityError(
            "target-checkout-head gate failed: immutable checkout HEAD "
            "does not match requested commit; use a newly materialized target checkout"
        )
    status = str(_git(repo_root, "status", "--porcelain", "--untracked-files=all"))
    if status.strip():
        raise ReleaseAuthorityError(
            "target-checkout-cleanliness gate failed: tracked, staged, or ordinary "
            "untracked content is present; use a newly materialized immutable target "
            "checkout rather than modifying or cleaning it"
        )
    ignored = _git_paths(repo_root, "ls-files", "--others", "--ignored", "--exclude-standard")
    if ignored:
        raise ReleaseAuthorityError(
            "target-checkout-ignored-content gate failed: ignored content is present; "
            "use a newly materialized immutable target checkout rather than cleaning or reusing it"
        )
    return commit


def _git_tracked_entries(repo_root: Path, commit: str) -> list[tuple[str, str]]:
    raw = bytes(_git(repo_root, "ls-tree", "-r", "-z", commit, text=False))
    entries: list[tuple[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
            mode, object_type, _ = metadata.decode("ascii").split(" ", 2)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked tree metadata is invalid; "
                "use a newly materialized immutable target checkout"
            ) from exc
        if object_type != "blob":
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked tree entries must be regular "
                "files; use a newly materialized immutable target checkout"
            )
        entries.append((mode, path_bytes.decode("utf-8", "surrogateescape")))
    return entries


def _managed_target_layout(
    repo_root: Path,
    requested_commit: str,
    release_root: Path,
) -> tuple[Path, Path, Path, str]:
    normalized_release_root, managed_root = _managed_root_from_release_root(release_root)
    supplied = Path(repo_root)
    commit = _normalize_commit(requested_commit)
    expected = normalized_release_root / commit
    try:
        checkout = supplied.resolve(strict=True)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "target-checkout-authority-path gate failed: use the exact normalized managed "
            "release checkout before requesting the release lease"
        ) from exc
    if (
        not supplied.is_absolute()
        or checkout != supplied
        or checkout != expected
        or not checkout.is_dir()
        or _is_link_or_junction(supplied)
    ):
        raise ReleaseAuthorityError(
            "target-checkout-authority-path gate failed: use the exact normalized managed "
            "release checkout before requesting the release lease"
        )
    git_dir = checkout / ".git"
    if _is_link_or_junction(git_dir) or not git_dir.is_dir():
        raise ReleaseAuthorityError(
            "target-checkout-authority-path gate failed: the managed target must be an "
            "isolated non-link Git checkout; have the managed owner materialize a new checkout"
        )
    return normalized_release_root, managed_root, checkout, commit


def _target_owner_mode(path: Path) -> tuple[int, int]:
    try:
        return _posix_owner_mode(path)
    except ReleaseAuthorityError as exc:
        raise ReleaseAuthorityError(
            "target-checkout-authority-metadata gate failed: POSIX owner and mode metadata "
            "must be available for the managed target; run the canonical release as the "
            "managed owner on the managed POSIX host"
        ) from exc


def _validate_target_owner_mode(path: Path, managed_owner: int) -> None:
    owner, mode = _target_owner_mode(path)
    if owner != managed_owner:
        raise ReleaseAuthorityError(
            "target-checkout-authority-ownership gate failed: the managed release root, "
            "checkout, Git metadata, and materialized tree must be owned by the managed-root "
            "owner; have that owner materialize a new immutable checkout"
        )
    if mode & 0o022:
        raise ReleaseAuthorityError(
            "target-checkout-authority-mode gate failed: the managed release root, checkout, "
            "Git metadata, and materialized tree must not be group/world-writable; have the "
            "managed owner materialize a new immutable checkout"
        )


def assert_managed_target_pre_fetch_trust(
    repo_root: Path,
    requested_commit: str,
    release_root: Path,
) -> str:
    """Trust a managed checkout's local metadata before any Git command or fetch."""
    normalized_release_root, managed_root, checkout, commit = _managed_target_layout(
        repo_root,
        requested_commit,
        release_root,
    )
    managed_owner, _ = _target_owner_mode(managed_root)
    _validate_target_owner_mode(normalized_release_root, managed_owner)

    pending = [checkout]
    while pending:
        current = pending.pop()
        if _is_link_or_junction(current):
            raise ReleaseAuthorityError(
                "target-checkout-authority-type gate failed: links are forbidden in an "
                "existing managed checkout; have the managed owner materialize a new checkout"
            )
        try:
            metadata = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseAuthorityError(
                "target-checkout-authority-metadata gate failed: the existing managed checkout "
                "must be fully readable before Git fetch; have the managed owner materialize a "
                "new immutable checkout"
            ) from exc
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise ReleaseAuthorityError(
                "target-checkout-authority-type gate failed: only regular files and directories "
                "are permitted before Git fetch; have the managed owner materialize a new checkout"
            )
        _validate_target_owner_mode(current, managed_owner)
        if stat.S_ISDIR(metadata.st_mode):
            try:
                with os.scandir(current) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
            except OSError as exc:
                raise ReleaseAuthorityError(
                    "target-checkout-authority-metadata gate failed: the existing managed "
                    "checkout must be fully readable before Git fetch; have the managed owner "
                    "materialize a new immutable checkout"
                ) from exc
    return commit


def assert_managed_target_checkout(
    repo_root: Path,
    requested_commit: str,
    release_root: Path,
) -> str:
    """Validate owner, mode, and exact Git-tree authority for one managed target checkout."""
    normalized = assert_managed_target_pre_fetch_trust(
        repo_root,
        requested_commit,
        release_root,
    )
    normalized_release_root, managed_root, checkout, _ = _managed_target_layout(
        repo_root,
        normalized,
        release_root,
    )
    normalized = assert_clean_commit(checkout, normalized)
    managed_owner, _ = _target_owner_mode(managed_root)

    for path in (normalized_release_root, checkout):
        _validate_target_owner_mode(path, managed_owner)

    tracked_entries = _git_tracked_entries(checkout, normalized)
    for git_mode, relative_path in tracked_entries:
        if git_mode == "120000":
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked symlinks are forbidden; use a "
                "newly materialized immutable target checkout"
            )
        if git_mode not in {"100644", "100755"}:
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked entries must be regular files; "
                "use a newly materialized immutable target checkout"
            )
        pure = PurePosixPath(relative_path)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked tree path metadata is invalid; "
                "use a newly materialized immutable target checkout"
            )
        candidate = checkout.joinpath(*pure.parts)
        current = checkout
        for part in pure.parts[:-1]:
            current = current / part
            if _is_link_or_junction(current):
                raise ReleaseAuthorityError(
                    "target-checkout-git-tree gate failed: tracked symlinks are forbidden; "
                    "use a newly materialized immutable target checkout"
                )
            try:
                directory_metadata = current.stat(follow_symlinks=False)
            except OSError as exc:
                raise ReleaseAuthorityError(
                    "target-checkout-git-tree gate failed: tracked content is missing or "
                    "unreadable; use a newly materialized immutable target checkout"
                ) from exc
            if not stat.S_ISDIR(directory_metadata.st_mode):
                raise ReleaseAuthorityError(
                    "target-checkout-git-tree gate failed: tracked path parents must be "
                    "directories; use a newly materialized immutable target checkout"
                )
            _validate_target_owner_mode(current, managed_owner)
        if _is_link_or_junction(candidate):
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked symlinks are forbidden; use a "
                "newly materialized immutable target checkout"
            )
        try:
            resolved = candidate.resolve(strict=True)
            metadata = candidate.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked content is missing or unreadable; "
                "use a newly materialized immutable target checkout"
            ) from exc
        if resolved != candidate or not stat.S_ISREG(metadata.st_mode):
            raise ReleaseAuthorityError(
                "target-checkout-git-tree gate failed: tracked entries must be regular files; "
                "use a newly materialized immutable target checkout"
            )
        _validate_target_owner_mode(candidate, managed_owner)
    return normalized


def build_image_references(commit: str) -> dict[str, str]:
    """Return immutable backend and frontend image tags for one full commit."""
    normalized = _normalize_commit(commit)
    return {
        "backend": f"ai-platform:{normalized}",
        "frontend": f"ai-platform-frontend:{normalized}",
    }


def classify_runtime_changes(paths: Sequence[str]) -> RuntimeChangeSet:
    """Classify changed paths by their image/runtime effect without invoking Docker."""
    categories: dict[str, list[str]] = {
        "backend_dependency": [],
        "backend_source": [],
        "frontend_dependency": [],
        "frontend_source": [],
        "deployment_only": [],
    }
    for path in sorted(set(paths)):
        if path in BACKEND_DEPENDENCY_PATHS:
            categories["backend_dependency"].append(path)
        elif path in FRONTEND_DEPENDENCY_PATHS:
            categories["frontend_dependency"].append(path)
        elif path in BACKEND_SOURCE_PATHS or path.startswith(BACKEND_SOURCE_PREFIXES):
            categories["backend_source"].append(path)
        elif path.startswith(FRONTEND_SOURCE_PREFIX):
            categories["frontend_source"].append(path)
        else:
            categories["deployment_only"].append(path)
    return RuntimeChangeSet(**{name: tuple(value) for name, value in categories.items()})


def build_auto_release_plan(
    current_commit: str,
    target_commit: str,
    changes: RuntimeChangeSet,
) -> AutoReleasePlan:
    """Plan canonical builds only for dependency changes and promotions for unchanged roles."""
    current = _normalize_commit(current_commit)
    target = _normalize_commit(target_commit)

    def role_plan(role: str, dependency: tuple[str, ...], source: tuple[str, ...]) -> RolePlan:
        if dependency:
            return RolePlan(role, "dependency", "canonical-build", dependency)
        if source:
            action = "runtime-rebuild" if role == "backend" else "source-build"
            return RolePlan(role, "source", action, source)
        return RolePlan(
            role,
            "unchanged",
            "reuse" if current == target else "promote",
            (),
        )

    roles = (
        role_plan("backend", changes.backend_dependency, changes.backend_source),
        role_plan("frontend", changes.frontend_dependency, changes.frontend_source),
    )
    return AutoReleasePlan(
        current_commit=current,
        target_commit=target,
        changes=changes,
        roles=roles,
        no_runtime_change=not any(
            (
                changes.backend_dependency,
                changes.backend_source,
                changes.frontend_dependency,
                changes.frontend_source,
            )
        ),
    )


def _plan_as_dict(
    plan: AutoReleasePlan,
    *,
    canonical_dependency_build_timeout_seconds: int = CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Serialize the compact plan without expanding command or environment details."""
    return {
        "current_commit": plan.current_commit,
        "target_commit": plan.target_commit,
        "no_runtime_change": plan.no_runtime_change,
        "canonical_dependency_build_timeout_seconds": (
            _validate_canonical_dependency_build_timeout(
                canonical_dependency_build_timeout_seconds
            )
        ),
        "changes": {
            "backend_dependency": list(plan.changes.backend_dependency),
            "backend_source": list(plan.changes.backend_source),
            "frontend_dependency": list(plan.changes.frontend_dependency),
            "frontend_source": list(plan.changes.frontend_source),
            "deployment_only": list(plan.changes.deployment_only),
        },
        "roles": [
            {
                "role": item.role,
                "change_kind": item.change_kind,
                "action": item.action,
                "paths": list(item.paths),
            }
            for item in plan.roles
        ],
    }


_SAFE_STDERR_DIAGNOSTICS = (
    (re.compile(r"no space left on device", re.IGNORECASE), "no space left on device"),
    (
        re.compile(r"out of memory|cannot allocate memory|oom", re.IGNORECASE),
        "memory allocation failed",
    ),
    (
        re.compile(r"temporary failure in name resolution|could not resolve host", re.IGNORECASE),
        "dependency source name resolution failed",
    ),
    (re.compile(r"connection refused", re.IGNORECASE), "dependency source connection refused"),
    (
        re.compile(r"certificate verify failed|tls handshake|x509", re.IGNORECASE),
        "TLS or certificate verification failed",
    ),
    (re.compile(r"checksum.*mismatch|hash.*mismatch", re.IGNORECASE), "dependency checksum mismatch"),
    (re.compile(r"permission denied", re.IGNORECASE), "permission denied"),
    (
        re.compile(r"context deadline exceeded|i/o timeout|operation timed out", re.IGNORECASE),
        "dependency operation timed out",
    ),
    (re.compile(r"failed to solve", re.IGNORECASE), "Docker build failed to solve"),
    (
        re.compile(r"cannot connect to the docker daemon|docker daemon is not running", re.IGNORECASE),
        "Docker daemon unavailable",
    ),
    (re.compile(r"executable file not found", re.IGNORECASE), "required executable not found"),
)


def _redacted_stderr_diagnostic(stderr: str | bytes | None) -> dict[str, Any]:
    """Classify captured stderr into fixed safe phrases without returning raw output."""
    if stderr is None or not stderr:
        return {"stderr_status": "empty"}
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    else:
        text = stderr
    scrubbed = text.lower()
    if any(
        marker in scrubbed
        for marker in (
            "authorization",
            "password",
            "passwd",
            "secret",
            "token",
            "api_key",
            "api-key",
            "private key",
        )
    ):
        return {"stderr_status": "redacted"}
    for pattern, summary in _SAFE_STDERR_DIAGNOSTICS:
        if pattern.search(text):
            return {"stderr_status": "recognized", "stderr_summary": summary}
    return {"stderr_status": "redacted"}


def _stage_failure_evidence(exc: BaseException) -> dict[str, Any]:
    """Return only bounded, non-secret facts needed to diagnose one failed stage."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return {
            "failure_kind": "timeout",
            "timeout_seconds": exc.timeout,
            **getattr(
                exc,
                "safe_stderr_diagnostic",
                _redacted_stderr_diagnostic(exc.stderr),
            ),
            **getattr(exc, "safe_build_progress_diagnostic", {}),
        }
    if isinstance(exc, subprocess.CalledProcessError):
        return {
            "failure_kind": "nonzero-exit",
            "exit_code": exc.returncode,
            **getattr(
                exc,
                "safe_stderr_diagnostic",
                _redacted_stderr_diagnostic(exc.stderr),
            ),
            **getattr(exc, "safe_build_progress_diagnostic", {}),
        }
    if isinstance(exc, OSError):
        evidence: dict[str, Any] = {"failure_kind": "os-error"}
        if isinstance(exc.errno, int):
            evidence["errno"] = exc.errno
        return evidence
    return {"failure_kind": "authority-error"}


def _stage(
    events: list[dict[str, Any]],
    *,
    name: str,
    strategy: str,
    action: str,
    operation: Any,
    timeout_seconds: int | None = None,
) -> Any:
    """Run one bounded release stage and retain compact, redacted timing evidence."""
    started = time.monotonic()
    stage_error: ReleaseAuthorityError | None = None
    try:
        value = operation()
    except (OSError, subprocess.SubprocessError, ReleaseAuthorityError) as exc:
        event = {
            "stage": name,
            "strategy": strategy,
            "action": action,
            "status": "failed",
            "wall_time_seconds": round(time.monotonic() - started, 3),
            **_stage_failure_evidence(exc),
        }
        if timeout_seconds is not None:
            event["timeout_seconds"] = timeout_seconds
        events.append(event)
        stage_error = ReleaseAuthorityError(f"release stage failed: {name}")
        setattr(stage_error, "stage_events", tuple(events))
    if stage_error is not None:
        raise stage_error from None
    event = {
        "stage": name,
        "strategy": strategy,
        "action": action,
        "status": "ok",
        "wall_time_seconds": round(time.monotonic() - started, 3),
    }
    if timeout_seconds is not None:
        event["timeout_seconds"] = timeout_seconds
    events.append(event)
    return value


def authoritative_repository(repo_root: Path) -> str:
    origin = str(_git(repo_root, "config", "--get", "remote.origin.url")).strip().rstrip("/")
    if origin not in AUTHORITATIVE_REPOSITORY_ALIASES:
        raise ReleaseAuthorityError("authoritative repository mismatch")
    return AUTHORITATIVE_REPOSITORY


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def assert_clean_coordination_source(repo_root: Path) -> Path:
    """Require only tracked, staged, and ordinary untracked coordination cleanliness."""
    supplied = Path(repo_root)
    try:
        absolute = Path(os.path.abspath(supplied))
        root = supplied.resolve(strict=True)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "coordination-source-path gate failed: run the canonical command from the "
            "normalized coordination checkout root before requesting the release lease"
        ) from exc
    if (
        not supplied.is_absolute()
        or absolute != root
        or not root.is_dir()
        or _is_link_or_junction(supplied)
    ):
        raise ReleaseAuthorityError(
            "coordination-source-path gate failed: run the canonical command from the "
            "normalized coordination checkout root before requesting the release lease"
        )
    try:
        top_level_text = str(_git(root, "rev-parse", "--show-toplevel")).strip()
        status = str(_git(root, "status", "--porcelain", "--untracked-files=all"))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseAuthorityError(
            "coordination-source-path gate failed: the coordination source must be a "
            "readable Git checkout before requesting the release lease"
        ) from exc
    try:
        top_level = Path(top_level_text).resolve(strict=True)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "coordination-source-path gate failed: run the canonical command from the "
            "normalized coordination checkout root before requesting the release lease"
        ) from exc
    if top_level != root:
        raise ReleaseAuthorityError(
            "coordination-source-path gate failed: run the canonical command from the "
            "normalized coordination checkout root before requesting the release lease"
        )
    if status.strip():
        raise ReleaseAuthorityError(
            "coordination-source-cleanliness gate failed: tracked, staged, or ordinary "
            "untracked changes are present; rerun from a clean exact-main coordination "
            "checkout before requesting the release lease (ignored-only artifacts are allowed)"
        )
    return root


def _posix_owner_mode(path: Path) -> tuple[int, int]:
    """Return owner and permission bits on the managed POSIX release host."""
    if os.name != "posix":
        raise ReleaseAuthorityError(
            "managed-env-metadata gate failed: POSIX owner and mode verification is "
            "unavailable; run the canonical release on the managed POSIX host"
        )
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "managed-env-metadata gate failed: owner and mode metadata is unavailable; "
            "have the managed owner verify the release layout before requesting the release lease"
        ) from exc
    return int(metadata.st_uid), stat.S_IMODE(metadata.st_mode)


def _managed_root_from_release_root(release_root: Path) -> tuple[Path, Path]:
    supplied = Path(release_root)
    if (
        not supplied.is_absolute()
        or ".." in supplied.parts
        or supplied.name != MANAGED_RELEASE_DIRECTORY_NAME
    ):
        raise ReleaseAuthorityError(
            "managed-env-path gate failed: --release-root must be the normalized absolute "
            "<managed-root>/releases directory"
        )
    normalized = Path(os.path.abspath(supplied))
    managed_root = normalized.parent
    try:
        resolved_managed_root = managed_root.resolve(strict=True)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "managed-env-path gate failed: the managed root must already exist as a "
            "regular directory before requesting the release lease"
        ) from exc
    try:
        resolved_release_root = normalized.resolve(strict=False)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "managed-env-path gate failed: the managed root and release root must be "
            "normalized non-link directories"
        ) from exc
    if (
        resolved_managed_root != managed_root
        or not managed_root.is_dir()
        or _is_link_or_junction(managed_root)
        or _is_link_or_junction(normalized)
        or resolved_release_root != normalized
        or (normalized.exists() and not normalized.is_dir())
    ):
        raise ReleaseAuthorityError(
            "managed-env-path gate failed: the managed root and release root must be "
            "normalized non-link directories"
        )
    return normalized, managed_root


def resolve_managed_env_file(release_root: Path, env_file: Path | None) -> Path:
    """Derive or validate the opaque managed Compose env file without reading it."""
    _, managed_root = _managed_root_from_release_root(release_root)
    canonical = managed_root / DEFAULT_MANAGED_ENV_RELATIVE_PATH
    if env_file is None:
        candidate = canonical
    else:
        supplied = Path(env_file)
        if not supplied.is_absolute() or ".." in supplied.parts:
            raise ReleaseAuthorityError(
                "managed-env-path gate failed: an explicit --env-file override must be "
                "a normalized absolute path"
            )
        candidate = Path(os.path.abspath(supplied))
        if candidate != supplied:
            raise ReleaseAuthorityError(
                "managed-env-path gate failed: an explicit --env-file override must be "
                "a normalized absolute path"
            )
        if candidate != canonical:
            raise ReleaseAuthorityError(
                "managed-env-path gate failed: an explicit --env-file override must equal "
                "the canonical <managed-root>/deploy/ai-platform/.env path; use that managed "
                "file before requesting the release lease"
            )

    if _is_link_or_junction(candidate):
        raise ReleaseAuthorityError(
            "managed-env-file-safety gate failed: the environment file must be a regular "
            "non-link file with no linked parent; have the managed owner provision it before "
            "requesting the release lease"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "managed-env-file-presence gate failed: provision the managed environment file "
            "as a regular owner-only file before requesting the release lease"
        ) from exc
    try:
        metadata = candidate.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseAuthorityError(
            "managed-env-file-presence gate failed: provision the managed environment file "
            "as a regular owner-only file before requesting the release lease"
        ) from exc
    if resolved != candidate or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseAuthorityError(
            "managed-env-file-safety gate failed: the environment file must be a regular "
            "non-link file with no linked parent; have the managed owner provision it before "
            "requesting the release lease"
        )

    managed_owner, _ = _posix_owner_mode(managed_root)
    env_owner, env_mode = _posix_owner_mode(candidate)
    if env_owner != managed_owner:
        raise ReleaseAuthorityError(
            "managed-env-file-ownership gate failed: the environment file owner must match "
            "the managed root owner; have that owner provision the file before requesting "
            "the release lease"
        )
    if env_mode != 0o600:
        raise ReleaseAuthorityError(
            "managed-env-file-mode gate failed: the environment file mode must be 0600; "
            "have the managed owner correct it before requesting the release lease"
        )
    return candidate


def resolve_compose_files(
    repo_root: Path,
    compose_files: Sequence[str | Path] | None,
) -> _ComposeSelection:
    """Validate and resolve one ordered repo-relative Compose file selection."""
    supplied_root = Path(repo_root)
    try:
        absolute_root = Path(os.path.abspath(supplied_root))
        root = supplied_root.resolve(strict=True)
    except OSError as exc:
        raise ReleaseAuthorityError("release checkout path is invalid") from exc
    if (
        not root.is_dir()
        or _is_link_or_junction(supplied_root)
        or absolute_root != root
    ):
        raise ReleaseAuthorityError("release checkout path is invalid")

    values: Sequence[str | Path]
    if compose_files is None:
        values = (DEFAULT_COMPOSE_RELATIVE_PATH.as_posix(),)
    else:
        values = compose_files
    if not values:
        raise ReleaseAuthorityError("compose file selection is invalid")

    relative_paths: list[str] = []
    absolute_paths: list[Path] = []
    identities: set[str] = set()
    for index, value in enumerate(values):
        if isinstance(value, Path):
            raw = value.as_posix()
        elif isinstance(value, str):
            raw = value
        else:
            raise ReleaseAuthorityError("compose file selection is invalid")
        invalid_text = (
            not raw
            or raw != unicodedata.normalize("NFC", raw)
            or "\\" in raw
            or "," in raw
            or any(
                unicodedata.category(character) in WORKER_TMPDIR_UNICODE_CATEGORIES
                for character in raw
            )
        )
        pure = PurePosixPath(raw)
        windows = PureWindowsPath(raw)
        invalid_path = (
            invalid_text
            or pure.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or pure.as_posix() != raw
            or raw.endswith("/")
            or any(part in {"", ".", ".."} for part in pure.parts)
        )
        if invalid_path:
            raise ReleaseAuthorityError("compose file selection is invalid")
        if index == 0 and raw != DEFAULT_COMPOSE_RELATIVE_PATH.as_posix():
            raise ReleaseAuthorityError("canonical main compose file must be first")
        if raw in relative_paths:
            raise ReleaseAuthorityError("duplicate compose file is forbidden")

        candidate = root.joinpath(*pure.parts)
        current = root
        for part in pure.parts:
            current = current / part
            if _is_link_or_junction(current):
                raise ReleaseAuthorityError("compose file links are forbidden")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ReleaseAuthorityError("compose file must exist") from exc
        if (
            resolved != candidate
            or not resolved.is_relative_to(root)
            or not resolved.is_file()
        ):
            raise ReleaseAuthorityError("compose file must be a regular checkout file")
        identity = os.path.normcase(str(resolved))
        if identity in identities or any(os.path.samefile(resolved, other) for other in absolute_paths):
            raise ReleaseAuthorityError("duplicate compose file is forbidden")
        identities.add(identity)
        relative_paths.append(raw)
        absolute_paths.append(resolved)

    working_dir = absolute_paths[0].parent.as_posix()
    return _ComposeSelection(
        checkout_root=root,
        relative_paths=tuple(relative_paths),
        absolute_paths=tuple(absolute_paths),
        working_dir=working_dir,
        config_files=",".join(path.as_posix() for path in absolute_paths),
    )


def _normalized_release_root(release_root: Path) -> Path:
    supplied = Path(release_root)
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise ReleaseAuthorityError("release root must be a normalized absolute path")
    normalized = Path(os.path.abspath(supplied))
    if normalized.exists() and not normalized.is_dir():
        raise ReleaseAuthorityError("release root must be a directory")
    if _is_link_or_junction(normalized) or normalized.resolve(strict=False) != normalized:
        raise ReleaseAuthorityError("release root symlink traversal is forbidden")
    normalized.mkdir(parents=True, exist_ok=True)
    if _is_link_or_junction(normalized) or normalized.resolve(strict=True) != normalized:
        raise ReleaseAuthorityError("release root symlink traversal is forbidden")
    return normalized


def _assert_standalone_checkout(checkout: Path, release_root: Path) -> None:
    if _is_link_or_junction(checkout) or checkout.resolve(strict=False).parent != release_root:
        raise ReleaseAuthorityError("versioned release checkout symlink or path traversal is forbidden")
    git_dir = checkout / ".git"
    if not checkout.is_dir() or not git_dir.is_dir() or _is_link_or_junction(git_dir):
        raise ReleaseAuthorityError("versioned release is not an isolated Git checkout")


def _fetch_and_verify_main_commit(checkout: Path, commit: str) -> None:
    authoritative_repository(checkout)
    _git(checkout, "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")
    commit_object = _run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=checkout,
        check=False,
    )
    if commit_object.returncode != 0:
        raise ReleaseAuthorityError("requested commit object is not available after main fetch")
    ancestor = _run(
        ["git", "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"],
        cwd=checkout,
        check=False,
    )
    if ancestor.returncode == 1:
        raise ReleaseAuthorityError("requested commit is not reachable from fetched main")
    if ancestor.returncode != 0:
        raise ReleaseAuthorityError("unable to verify requested commit against fetched main")


def materialize_main_checkout(release_root: Path, commit: str) -> Path:
    """Fetch main and create or validate one clean isolated checkout by commit."""
    normalized = _normalize_commit(commit)
    root = _normalized_release_root(release_root)
    checkout = root / normalized
    staging = root / f".{normalized}.incoming"

    if _is_link_or_junction(staging) or staging.exists():
        raise ReleaseAuthorityError("interrupted release staging residue requires operator review")
    if _is_link_or_junction(checkout):
        raise ReleaseAuthorityError("versioned release checkout symlink is forbidden")

    if checkout.exists():
        assert_managed_target_pre_fetch_trust(checkout, normalized, root)
        _assert_standalone_checkout(checkout, root)
        assert_clean_commit(checkout, normalized)
        _fetch_and_verify_main_commit(checkout, normalized)
        assert_managed_target_checkout(checkout, normalized, root)
        return checkout

    staging.mkdir(exist_ok=False)
    _git(staging, "init")
    _git(staging, "remote", "add", "origin", AUTHORITATIVE_REPOSITORY)
    _fetch_and_verify_main_commit(staging, normalized)
    _git(staging, "checkout", "--detach", normalized)
    assert_clean_commit(staging, normalized)
    _assert_standalone_checkout(staging, root)
    if checkout.exists() or _is_link_or_junction(checkout):
        raise ReleaseAuthorityError("versioned release destination appeared during materialization")
    staging.rename(checkout)
    _assert_standalone_checkout(checkout, root)
    return checkout


def _is_secret_path(relative_path: str) -> bool:
    path = Path(relative_path)
    name = path.name.lower()
    return name in SECRET_PATH_NAMES or name.startswith(".env.")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes(path: Path, content: bytes) -> dict[str, Any]:
    path.write_bytes(content)
    return {"size": path.stat().st_size, "sha256": _sha256_path(path)}


def _git_paths(repo_root: Path, *args: str) -> list[str]:
    raw = bytes(_git(repo_root, *args, "-z", text=False))
    return [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]


def preserve_dirty_source(repo_root: Path, output_root: Path) -> Path:
    """Preserve dirty Git evidence without changing or cleaning the source tree."""
    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
    head = str(_git(repo_root, "rev-parse", "HEAD")).strip().lower()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_root / f"{timestamp}-{head}"
    destination.mkdir(parents=True, exist_ok=False)

    status = str(_git(repo_root, "status", "--short", "--branch", "--untracked-files=all"))
    status_bytes = status.encode("utf-8")
    tracked_patch = bytes(_git(repo_root, "diff", "--binary", text=False))
    staged_patch = bytes(_git(repo_root, "diff", "--cached", "--binary", text=False))
    modified = set(_git_paths(repo_root, "diff", "--name-only"))
    staged = set(_git_paths(repo_root, "diff", "--cached", "--name-only"))
    untracked = set(_git_paths(repo_root, "ls-files", "--others", "--exclude-standard"))

    inventory: list[dict[str, Any]] = []
    for relative_path in sorted(modified | staged | untracked):
        path = repo_root / relative_path
        secret = _is_secret_path(relative_path)
        category = "untracked" if relative_path in untracked else "tracked"
        if relative_path in staged:
            category = "staged" if category == "tracked" else f"{category}+staged"
        record: dict[str, Any] = {
            "path": relative_path,
            "category": category,
            "exists": path.exists(),
            "content_preserved": bool(path.is_file() and not secret),
            "secret_path_excluded": secret,
            "size": path.stat().st_size if path.is_file() else None,
            "mode": oct(path.stat().st_mode & 0o777) if path.exists() else None,
            "sha256": _sha256_path(path) if path.is_file() and not secret else None,
        }
        inventory.append(record)

    inventory_path = destination / "inventory.json"
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts = {
        "status.txt": _write_bytes(destination / "status.txt", status_bytes),
        "tracked.patch": _write_bytes(destination / "tracked.patch", tracked_patch),
        "staged.patch": _write_bytes(destination / "staged.patch", staged_patch),
        "inventory.json": {
            "size": inventory_path.stat().st_size,
            "sha256": _sha256_path(inventory_path),
        },
    }

    tar_path = destination / "untracked.tar"
    with tarfile.open(tar_path, "w") as archive:
        for relative_path in sorted(untracked):
            path = repo_root / relative_path
            if path.is_file() and not _is_secret_path(relative_path):
                archive.add(path, arcname=relative_path, recursive=False)
    artifacts["untracked.tar"] = {"size": tar_path.stat().st_size, "sha256": _sha256_path(tar_path)}

    manifest = {
        "schema_version": PRESERVATION_SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(repo_root),
        "source_head": head,
        "source_was_dirty": bool(status.strip()),
        "source_tree_unchanged_by_preservation": True,
        "secret_path_policy": "record_metadata_only_without_hash_or_archive_content",
        "artifacts": artifacts,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _compose_identity_mismatches(
    labels: dict[str, Any],
    role: str,
    *,
    expected_compose_dir: str,
    expected_config_files: str,
) -> list[str]:
    mismatches: list[str] = []
    if labels.get("ai-platform.release-owner") != "repo-local-compose":
        mismatches.append(f"{role}_container_not_repo_local_compose_owned")
    if labels.get("ai-platform.release-role") != role:
        mismatches.append(f"{role}_container_role_mismatch")
    if labels.get("com.docker.compose.project.working_dir") != expected_compose_dir:
        mismatches.append(f"{role}_compose_working_dir_mismatch")
    if str(labels.get("com.docker.compose.project.config_files") or "") != expected_config_files:
        mismatches.append(f"{role}_compose_config_mismatch")
    if labels.get("com.docker.compose.project") != COMPOSE_PROJECT:
        mismatches.append(f"{role}_compose_project_mismatch")
    if labels.get("com.docker.compose.service") != role:
        mismatches.append(f"{role}_compose_service_mismatch")
    if labels.get("com.docker.compose.oneoff") != "False":
        mismatches.append(f"{role}_compose_oneoff_mismatch")
    if not str(labels.get("com.docker.compose.config-hash") or "").strip():
        mismatches.append(f"{role}_compose_config_hash_missing")
    return mismatches


def build_parity_report(
    *,
    expected_commit: str,
    source: dict[str, Any],
    images: dict[str, dict[str, Any]],
    containers: dict[str, dict[str, Any]],
    runtime: dict[str, Any],
    expected_compose_dir: str,
    expected_repository: str,
    expected_compose_files: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a strict same-commit report for source, images, and runtime subjects."""
    commit = _normalize_commit(expected_commit)
    mismatches: list[str] = []
    if source.get("commit") != commit:
        mismatches.append("source_commit_mismatch")
    if source.get("dirty") is not False:
        mismatches.append("source_not_clean")

    for role in ("backend", "frontend"):
        image = images.get(role, {})
        labels = image.get("labels") if isinstance(image.get("labels"), dict) else {}
        if labels.get("ai-platform.source-commit") != commit:
            mismatches.append(f"{role}_image_commit_mismatch")
        if labels.get("org.opencontainers.image.revision") != commit:
            mismatches.append(f"{role}_image_oci_revision_mismatch")
        if labels.get("ai-platform.source-repository") != expected_repository:
            mismatches.append(f"{role}_image_repository_mismatch")
        if labels.get("ai-platform.build-dirty") != "false":
            mismatches.append(f"{role}_image_dirty_label_mismatch")
        if labels.get("ai-platform.release-role") != role:
            mismatches.append(f"{role}_image_role_mismatch")
        if any(
            label in labels and labels.get(label) != commit
            for label in COMPATIBILITY_IMAGE_COMMIT_LABELS
        ):
            mismatches.append(f"{role}_image_compatibility_commit_mismatch")

    expected_config_files = ",".join(expected_compose_files) if expected_compose_files else (
        f"{expected_compose_dir}/docker-compose.yml"
    )
    expected_image_roles = {"api": "backend", "worker": "backend", "frontend": "frontend"}
    for role, image_role in expected_image_roles.items():
        container = containers.get(role, {})
        labels = container.get("labels") if isinstance(container.get("labels"), dict) else {}
        if container.get("running") is not True:
            mismatches.append(f"{role}_container_not_running")
        mismatches.extend(
            _compose_identity_mismatches(
                labels,
                role,
                expected_compose_dir=expected_compose_dir,
                expected_config_files=expected_config_files,
            )
        )
        if labels.get("ai-platform.source-commit") != commit:
            mismatches.append(f"{role}_container_commit_mismatch")
        if labels.get("ai-platform.source-dirty") != "false":
            mismatches.append(f"{role}_container_dirty_label_mismatch")
        expected_image_id = images.get(image_role, {}).get("id")
        if not expected_image_id or container.get("image_id") != expected_image_id:
            mismatches.append(f"{role}_container_image_mismatch")

    for role in ("api", "worker", "frontend"):
        if runtime.get(f"{role}_commit") != commit:
            mismatches.append(f"{role}_runtime_commit_mismatch")
    if runtime.get("api_sandbox_executor_image_matches_expected") is not True:
        mismatches.append("api_sandbox_executor_image_mismatch")
    if runtime.get("worker_sandbox_executor_image_matches_expected") is not True:
        mismatches.append("worker_sandbox_executor_image_mismatch")
    if runtime.get("api_worker_sandbox_executor_images_match") is not True:
        mismatches.append("api_worker_sandbox_executor_image_mismatch")
    if runtime.get("api_health_status") != "ok":
        mismatches.append("api_health_not_ok")
    if runtime.get("worker_running") is not True:
        mismatches.append("worker_not_running")

    return {
        "schema_version": SCHEMA_VERSION,
        "expected_commit": commit,
        "verified": not mismatches,
        "mismatches": sorted(set(mismatches)),
        "source": source,
        "images": images,
        "containers": containers,
        "runtime": runtime,
    }


def _docker_base(docker_cmd: str) -> list[str]:
    command = shlex.split(docker_cmd)
    if not command or command[-1] != "docker":
        raise ReleaseAuthorityError("docker command must end with the docker executable")
    return command


def _docker_json(docker: list[str], *args: str) -> Any:
    result = _run([*docker, *args])
    return json.loads(result.stdout)


def _image_record(docker: list[str], image: str) -> dict[str, Any]:
    payload = _docker_json(docker, "image", "inspect", image)[0]
    return {"reference": image, "id": payload.get("Id"), "labels": payload.get("Config", {}).get("Labels") or {}}


def _validate_release_image(image: dict[str, Any], *, commit: str, repository: str, role: str) -> None:
    labels = image.get("labels") if isinstance(image.get("labels"), dict) else {}
    expected = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": repository,
        "ai-platform.build-dirty": "false",
        "ai-platform.release-role": role,
    }
    for label, value in expected.items():
        if labels.get(label) != value:
            raise ReleaseAuthorityError(f"{role} image label mismatch: {label}")
    for label in COMPATIBILITY_IMAGE_COMMIT_LABELS:
        if label in labels and labels.get(label) != commit:
            raise ReleaseAuthorityError(f"{role} image label mismatch: {label}")


def _existing_release_image(
    docker: list[str],
    reference: str,
    *,
    commit: str,
    repository: str,
    role: str,
) -> dict[str, Any] | None:
    try:
        image = _image_record(docker, reference)
    except subprocess.CalledProcessError:
        return None
    _validate_release_image(image, commit=commit, repository=repository, role=role)
    return image


def _release_label_dockerfile_lines(role: str) -> str:
    """Return all target-provenance labels required for one promoted image role."""
    common = [
        "LABEL org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT",
        "LABEL ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT",
        "LABEL ai-platform.source-commit=$AI_PLATFORM_BUILD_COMMIT",
        'LABEL ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"',
        "LABEL ai-platform.source-repository=$AI_PLATFORM_BUILD_REPOSITORY",
        f"LABEL ai-platform.release-role={role}",
    ]
    if role == "backend":
        common[1:1] = [
            "LABEL ai-platform.runtime-subject=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.source_revision=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.source_commit=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.runtime_subject=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.source_tree_commit=$AI_PLATFORM_BUILD_COMMIT",
        ]
    return "\n".join(common)


def _backend_provenance_dockerfile_run() -> str:
    """Return the backend embedded-source marker update used by source rebuilds and promotions."""
    return '''RUN printf '%s\\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.ai-platform-source-revision \\
    && printf '%s\\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.codex-source-revision \\
    && printf '%s\\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.source-commit \\
    && AI_PLATFORM_BUILD_COMMIT="$AI_PLATFORM_BUILD_COMMIT" AI_PLATFORM_BUILD_DIRTY="$AI_PLATFORM_BUILD_DIRTY" \\
       python -c "import json, os; from pathlib import Path; commit = os.environ.get('AI_PLATFORM_BUILD_COMMIT', 'unknown').strip() or 'unknown'; dirty_text = os.environ.get('AI_PLATFORM_BUILD_DIRTY', 'unknown').strip().lower(); dirty = dirty_text != 'false'; dirty_paths = [] if not dirty else ['unknown_runtime_affecting_dirty_paths']; payload = dict(schema_version='ai-platform.source-snapshot.v1', source_tree_commit_sha=commit, runtime_subject_commit_sha=commit, source_tree_dirty=dirty, runtime_affecting_changes_since_runtime_subject=[], runtime_affecting_dirty_paths=dirty_paths, snapshot_source='dockerfile_build_args'); Path('/app/.ai-platform-source-snapshot.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\\n', encoding='utf-8')"'''


def _promotion_dockerfile(role: str) -> str:
    """Build a provenance-only image from a verified local role image without dependency commands."""
    labels = _release_label_dockerfile_lines(role)
    if role == "backend":
        marker = _backend_provenance_dockerfile_run()
        user = "USER 10001:10001"
    elif role == "frontend":
        marker = (
            'RUN sed -i "s/\\\"commit\\\": \\\"[^\\\"]*\\\"/\\\"commit\\\": '
            '\\\"${AI_PLATFORM_BUILD_COMMIT}\\\"/" '
            "/usr/share/nginx/html/ai-platform-build-provenance.json"
        )
        user = ""
    else:
        raise ReleaseAuthorityError("release role is invalid")
    return f"""# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE
FROM ${{BASE_IMAGE}}
ARG AI_PLATFORM_BUILD_COMMIT
ARG AI_PLATFORM_BUILD_DIRTY
ARG AI_PLATFORM_BUILD_REPOSITORY
USER root
{labels}
{marker}
{user}
"""


def _backend_runtime_dockerfile() -> str:
    """Build source-only backend runtime from a verified image with no dependency installer command."""
    labels = _release_label_dockerfile_lines("backend")
    marker = _backend_provenance_dockerfile_run()
    return f"""# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE
FROM ${{BASE_IMAGE}}
ARG AI_PLATFORM_BUILD_COMMIT
ARG AI_PLATFORM_BUILD_DIRTY
ARG AI_PLATFORM_BUILD_REPOSITORY
USER root
RUN rm -rf /app/app /app/tools /app/scripts /app/skills /app/docs/release-evidence \\
    && rm -f /app/docker-entrypoint.sh /app/.ai-platform-source-revision \\
       /app/.codex-source-revision /app/.source-commit /app/.ai-platform-source-snapshot.json
COPY app /app/app
COPY tools /app/tools
COPY scripts /app/scripts
COPY skills /app/skills
COPY docs/release-evidence /app/docs/release-evidence
COPY --chmod=0755 docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod -R a+rX /app && chmod 0755 /app/docker-entrypoint.sh
{labels}
{marker}
USER 10001:10001
"""


def _build_args(commit: str, repository: str) -> list[str]:
    return [
        "--build-arg", f"AI_PLATFORM_BUILD_COMMIT={commit}",
        "--build-arg", "AI_PLATFORM_BUILD_DIRTY=false",
        "--build-arg", f"AI_PLATFORM_BUILD_REPOSITORY={repository}",
    ]


def _role_timeout(
    role: str,
    *,
    canonical_dependency: bool = False,
    canonical_dependency_build_timeout_seconds: int = CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS,
) -> int:
    """Return the bounded timeout for one role action without widening fast-path SLOs."""
    if canonical_dependency:
        return _validate_canonical_dependency_build_timeout(
            canonical_dependency_build_timeout_seconds
        )
    if role == "backend":
        return BACKEND_STAGE_TIMEOUT_SECONDS
    if role == "frontend":
        return FRONTEND_STAGE_TIMEOUT_SECONDS
    raise ReleaseAuthorityError("release role is invalid")


def _canonical_or_source_build(
    docker: list[str],
    *,
    repo_root: Path,
    reference: str,
    commit: str,
    repository: str,
    role: str,
    source_only: bool,
    canonical_dependency_build_timeout_seconds: int = CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS,
) -> None:
    dockerfile = "Dockerfile" if role == "backend" else "frontend/web/Dockerfile"
    command = [*docker, "build", *_build_args(commit, repository), "-t", reference]
    if source_only:
        command.extend(["--target", "runtime"])
    command.extend(["-f", dockerfile, "."])
    _run(
        command,
        cwd=repo_root,
        classify_build_progress=not source_only,
        timeout=_role_timeout(
            role,
            canonical_dependency=not source_only,
            canonical_dependency_build_timeout_seconds=(
                canonical_dependency_build_timeout_seconds
            ),
        ),
    )


def _build_from_verified_role_image(
    docker: list[str],
    *,
    repo_root: Path,
    reference: str,
    base_reference: str,
    commit: str,
    repository: str,
    role: str,
    dockerfile: str,
) -> None:
    """Create one target role image from a verified local source image through a bounded build."""
    _run(
        [
            *docker,
            "build",
            "--build-arg",
            f"BASE_IMAGE={base_reference}",
            *_build_args(commit, repository),
            "-t",
            reference,
            "-f",
            "-",
            ".",
        ],
        cwd=repo_root,
        timeout=_role_timeout(role),
        input=dockerfile,
    )


def _require_sandbox_executor_image(
    docker: list[str],
    reference: str,
    *,
    commit: str,
    repository: str,
) -> dict[str, Any]:
    """Require the exact clean backend image used by Docker sandbox executors."""
    expected_reference = build_image_references(commit)["backend"]
    if reference != expected_reference:
        raise ReleaseAuthorityError(
            "sandbox executor image must be the exact immutable backend reference"
        )
    try:
        image = _image_record(docker, reference)
    except (OSError, subprocess.CalledProcessError, IndexError, json.JSONDecodeError):
        raise ReleaseAuthorityError("sandbox executor image is missing") from None
    if not str(image.get("id") or "").strip():
        raise ReleaseAuthorityError("sandbox executor image ID is missing")
    try:
        _validate_release_image(
            image,
            commit=commit,
            repository=repository,
            role="backend",
        )
    except ReleaseAuthorityError:
        raise ReleaseAuthorityError("sandbox executor image provenance mismatch") from None
    return image


def _immutable_sandbox_executor_reference(image: dict[str, Any]) -> str:
    """Return the verified local Docker image ID used for governed executors."""
    image_id = str(image.get("id") or "").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ReleaseAuthorityError("sandbox executor image ID is not immutable")
    return image_id


def _inspect_optional_container(docker: list[str], name: str) -> dict[str, Any] | None:
    existing = _run([*docker, "container", "inspect", name], check=False)
    if existing.returncode != 0:
        return None
    try:
        payload = json.loads(existing.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseAuthorityError("managed container inspect metadata is invalid") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ReleaseAuthorityError("managed container inspect metadata is invalid")
    return payload[0]


def _compose_ownership_selection(
    labels: dict[str, Any],
    target: _ComposeSelection,
) -> _ComposeSelection | None:
    """Resolve exact or allowlisted provider-transition ownership from live labels."""
    working_dir = labels.get("com.docker.compose.project.working_dir")
    config_files = labels.get("com.docker.compose.project.config_files")
    if not isinstance(working_dir, str) or not isinstance(config_files, str):
        return None
    if working_dir == target.working_dir and config_files == target.config_files:
        return target

    observed_files = config_files.split(",")
    if not observed_files or any(not value for value in observed_files):
        return None
    observed_paths: list[Path] = []
    for value in observed_files:
        path = Path(value)
        if (
            not path.is_absolute()
            or path.as_posix() != value
            or value != unicodedata.normalize("NFC", value)
            or "\\" in value
            or any(
                unicodedata.category(character) in WORKER_TMPDIR_UNICODE_CATEGORIES
                for character in value
            )
        ):
            return None
        observed_paths.append(path)

    observed_main = observed_paths[0]
    observed_root = observed_main
    for _ in DEFAULT_COMPOSE_RELATIVE_PATH.parts:
        observed_root = observed_root.parent
    release_root = target.checkout_root.parent
    if (
        observed_root == target.checkout_root
        or observed_root.parent != release_root
        or not FULL_COMMIT_RE.fullmatch(target.checkout_root.name)
        or not RELEASE_DIRECTORY_RE.fullmatch(observed_root.name)
    ):
        return None
    try:
        observed_relative_paths = tuple(
            path.relative_to(observed_root).as_posix() for path in observed_paths
        )
    except ValueError:
        return None
    try:
        observed = resolve_compose_files(observed_root, observed_relative_paths)
    except (OSError, ReleaseAuthorityError):
        return None
    if observed.working_dir != working_dir or observed.config_files != config_files:
        return None
    if observed.relative_paths == target.relative_paths:
        return observed
    transition = frozenset({observed.relative_paths, target.relative_paths})
    if transition == PROVIDER_OVERLAY_COMPOSE_SELECTIONS:
        return observed
    return None


def _manual_frontend_container_id(inspected: dict[str, Any]) -> str:
    container_id = inspected.get("Id")
    if not isinstance(container_id, str) or not DOCKER_CONTAINER_ID_RE.fullmatch(container_id):
        raise ReleaseAuthorityError("manual frontend container ID metadata is invalid")
    return container_id


def _preflight_managed_container_ownership(
    docker: list[str],
    selection: _ComposeSelection,
    *,
    replace_known_manual_frontend: bool,
    expected_manual_frontend_image: str | None,
    expected_manual_frontend_image_id: str | None,
) -> _ManagedContainerOwnership:
    manual_frontend_id: str | None = None
    compose_owner_selection: _ComposeSelection | None = None
    compose_roles: list[str] = []
    for role in ("api", "worker", "frontend"):
        name = f"ai-platform-{role}"
        inspected = _inspect_optional_container(docker, name)
        if inspected is None:
            continue
        config = inspected.get("Config") if isinstance(inspected.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        if labels.get("ai-platform.release-owner") == "repo-local-compose":
            owned_selection = _compose_ownership_selection(labels, selection)
            if owned_selection is None:
                raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
            if _compose_identity_mismatches(
                labels,
                role,
                expected_compose_dir=owned_selection.working_dir,
                expected_config_files=owned_selection.config_files,
            ):
                raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
            if compose_owner_selection is not None and compose_owner_selection != owned_selection:
                raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
            compose_owner_selection = owned_selection
            compose_roles.append(role)
            continue
        if role != "frontend":
            raise ReleaseAuthorityError(f"{role} compose ownership mismatch")
        if not replace_known_manual_frontend:
            raise ReleaseAuthorityError(
                "manual frontend container is forbidden; rerun with explicit replacement"
            )
        observed_image = str(config.get("Image") or "")
        observed_image_id = str(inspected.get("Image") or "")
        if not expected_manual_frontend_image or not expected_manual_frontend_image_id:
            raise ReleaseAuthorityError(
                "manual frontend replacement requires expected image and image ID"
            )
        if (
            observed_image != expected_manual_frontend_image
            or observed_image_id != expected_manual_frontend_image_id
        ):
            raise ReleaseAuthorityError(
                "manual frontend identity mismatch; refusing container removal"
            )
        manual_frontend_id = _manual_frontend_container_id(inspected)
    required_roles = ("api", "worker", "frontend")
    if (
        compose_owner_selection is not None
        and compose_owner_selection.relative_paths != selection.relative_paths
        and tuple(compose_roles) != required_roles
    ):
        missing_role = next(role for role in required_roles if role not in compose_roles)
        raise ReleaseAuthorityError(f"{missing_role} compose ownership mismatch")
    return _ManagedContainerOwnership(
        compose_selection=compose_owner_selection,
        compose_roles=tuple(compose_roles),
        manual_frontend_id=manual_frontend_id,
    )


def _container_inspect_record(
    docker: list[str],
    name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _docker_json(docker, "container", "inspect", name)[0]
    state = payload.get("State") or {}
    config = payload.get("Config") if isinstance(payload.get("Config"), dict) else {}
    record = {
        "name": name,
        "image_id": payload.get("Image"),
        "labels": config.get("Labels") or {},
        "running": state.get("Running") is True,
        "pid": state.get("Pid"),
        "health": (state.get("Health") or {}).get("Status") or "",
        "ports": (payload.get("NetworkSettings") or {}).get("Ports") or {},
    }
    return record, payload


def _container_record(docker: list[str], name: str) -> dict[str, Any]:
    record, _ = _container_inspect_record(docker, name)
    return record


def _container_sandbox_executor_image(inspected: dict[str, Any]) -> str | None:
    """Read one executor reference without retaining unrelated container environment."""
    config = inspected.get("Config")
    if not isinstance(config, dict):
        return None
    environment = config.get("Env")
    if not isinstance(environment, list) or any(
        not isinstance(entry, str) for entry in environment
    ):
        return None
    entries = [
        entry
        for entry in environment
        if entry == "SANDBOX_EXECUTOR_IMAGE"
        or entry.startswith("SANDBOX_EXECUTOR_IMAGE=")
    ]
    if len(entries) != 1 or not entries[0].startswith("SANDBOX_EXECUTOR_IMAGE="):
        return None
    value = entries[0].partition("=")[2]
    return value or None


def _container_file_commit(docker: list[str], name: str, path: str) -> str:
    result = _run([*docker, "exec", name, "cat", path])
    if path.endswith(".json"):
        payload = json.loads(result.stdout)
        if "git" in payload:
            return str(payload.get("git", {}).get("commit") or "")
        return str(payload.get("source_tree_commit_sha") or "")
    return result.stdout.strip()


def _http_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=HTTP_PROBE_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _published_loopback_url(container: dict[str, Any], container_port: str, path: str) -> str:
    bindings = container.get("ports", {}).get(container_port)
    if not isinstance(bindings, list) or not bindings:
        raise ReleaseAuthorityError(f"expected published bindings for {container_port}")
    host_ports = {str(binding.get("HostPort") or "").strip() for binding in bindings}
    if len(host_ports) != 1:
        raise ReleaseAuthorityError(f"ambiguous published host ports for {container_port}")
    host_port = host_ports.pop()
    if not host_port.isdigit():
        raise ReleaseAuthorityError(f"invalid published host port for {container_port}")
    host_ips = {str(binding.get("HostIp") or "").strip() for binding in bindings}
    if any(host in {"0.0.0.0", "127.0.0.1"} for host in host_ips):
        host = "127.0.0.1"
    elif host_ips and all(host in {"::", "::1"} for host in host_ips):
        host = "[::1]"
    else:
        raise ReleaseAuthorityError(f"unsupported published host binding for {container_port}")
    return f"http://{host}:{host_port}/{path.lstrip('/')}"


def _container_json_file(docker: list[str], name: str, path: str) -> dict[str, Any]:
    result = _run([*docker, "exec", name, "cat", path])
    return json.loads(result.stdout)


def _worker_heartbeat_path(inspected: dict[str, Any]) -> str:
    invalid = "worker container TMPDIR metadata is invalid"
    config = inspected.get("Config")
    if not isinstance(config, dict):
        raise ReleaseAuthorityError(invalid)
    environment = config.get("Env")
    if not isinstance(environment, list) or any(
        not isinstance(entry, str) for entry in environment
    ):
        raise ReleaseAuthorityError(invalid)

    entries = [
        entry
        for entry in environment
        if entry == "TMPDIR" or entry.startswith("TMPDIR=")
    ]
    if not entries:
        tmpdir = "/tmp"
    elif len(entries) != 1 or not entries[0].startswith("TMPDIR="):
        raise ReleaseAuthorityError(invalid)
    else:
        tmpdir = entries[0].partition("=")[2]
        invalid_path = (
            not tmpdir
            or not PurePosixPath(tmpdir).is_absolute()
            or tmpdir.startswith("//")
            or "\\" in tmpdir
            or any(character in WORKER_TMPDIR_EXPANSION_MARKERS for character in tmpdir)
            or any(
                unicodedata.category(character) in WORKER_TMPDIR_UNICODE_CATEGORIES
                for character in tmpdir
            )
            or ".." in PurePosixPath(tmpdir).parts
            or posixpath.normpath(tmpdir) != tmpdir
        )
        if invalid_path:
            raise ReleaseAuthorityError(invalid)
    return str(PurePosixPath(tmpdir) / WORKER_HEARTBEAT_FILENAME)


def _worker_container_id(inspected: dict[str, Any]) -> str:
    container_id = inspected.get("Id")
    if not isinstance(container_id, str) or not DOCKER_CONTAINER_ID_RE.fullmatch(
        container_id
    ):
        raise ReleaseAuthorityError("worker container ID metadata is invalid")
    return container_id


def _read_worker_heartbeat(
    docker: list[str],
    container_id: str,
    path: str,
) -> dict[str, Any]:
    try:
        return _container_json_file(docker, container_id, path)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        raise ReleaseAuthorityError("worker runtime heartbeat read failed") from None


def _container_process_alive(docker: list[str], container_id: str, pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        result = _run(
            [
                *docker,
                "exec",
                container_id,
                "/bin/sh",
                "-c",
                'kill -0 "$1"',
                "sh",
                str(pid),
            ],
            check=False,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return result.returncode == 0


def _validate_worker_runtime_heartbeat(payload: dict[str, Any], *, process_alive: bool) -> None:
    if payload.get("schema_version") != "ai-platform.worker-runtime-heartbeat.v1":
        raise ReleaseAuthorityError("worker runtime heartbeat schema mismatch")
    if not str(payload.get("worker_id") or "").strip():
        raise ReleaseAuthorityError("worker runtime heartbeat worker ID missing")
    if not process_alive:
        raise ReleaseAuthorityError("worker runtime heartbeat process is not alive")
    try:
        observed_at = datetime.fromisoformat(str(payload.get("observed_at") or ""))
    except ValueError as exc:
        raise ReleaseAuthorityError("worker runtime heartbeat timestamp invalid") from exc
    if observed_at.tzinfo is None:
        raise ReleaseAuthorityError("worker runtime heartbeat timestamp lacks timezone")
    age = datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)
    if age < timedelta(seconds=-5) or age > timedelta(seconds=30):
        raise ReleaseAuthorityError("worker runtime heartbeat is stale")


def collect_live_parity(
    repo_root: Path,
    commit: str,
    *,
    docker_cmd: str,
    compose_files: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """Collect live Docker and embedded provenance for the strict parity report."""
    normalized = assert_clean_commit(repo_root, commit)
    selection = resolve_compose_files(repo_root, compose_files)
    docker = _docker_base(docker_cmd)
    refs = build_image_references(normalized)
    repository = authoritative_repository(repo_root)
    images = {
        "backend": _require_sandbox_executor_image(
            docker,
            refs["backend"],
            commit=normalized,
            repository=repository,
        ),
        "frontend": _image_record(docker, refs["frontend"]),
    }
    worker_name = "ai-platform-worker"
    api_container, api_inspect = _container_inspect_record(docker, "ai-platform-api")
    worker_container, worker_inspect = _container_inspect_record(docker, worker_name)
    worker_container_id = _worker_container_id(worker_inspect)
    frontend_container = _container_record(docker, "ai-platform-frontend")
    containers = {
        "api": api_container,
        "worker": worker_container,
        "frontend": frontend_container,
    }
    api_health = _http_json(_published_loopback_url(containers["api"], "8020/tcp", "/api/ai/health"))
    frontend_provenance = _http_json(
        _published_loopback_url(
            containers["frontend"],
            "8080/tcp",
            "/ai-platform-build-provenance.json",
        )
    )
    if frontend_provenance.get("schema_version") != "ai-platform.frontend-build-provenance.v1":
        raise ReleaseAuthorityError("frontend provenance schema mismatch")
    if frontend_provenance.get("frontend_path") != "frontend/web":
        raise ReleaseAuthorityError("frontend provenance path mismatch")
    if frontend_provenance.get("git", {}).get("dirty") is not False:
        raise ReleaseAuthorityError("frontend provenance is dirty")
    worker_heartbeat = _read_worker_heartbeat(
        docker,
        worker_container_id,
        _worker_heartbeat_path(worker_inspect),
    )
    _validate_worker_runtime_heartbeat(
        worker_heartbeat,
        process_alive=_container_process_alive(
            docker,
            worker_container_id,
            worker_heartbeat.get("pid"),
        ),
    )
    api_executor_image = _container_sandbox_executor_image(api_inspect)
    worker_executor_image = _container_sandbox_executor_image(worker_inspect)
    sandbox_executor_image = _immutable_sandbox_executor_reference(images["backend"])
    runtime = {
        "api_commit": str(api_health.get("runtime_commit") or ""),
        "api_health_status": api_health.get("status"),
        "worker_heartbeat": worker_heartbeat,
        "worker_running": containers["worker"].get("running") is True,
        "frontend_commit": str(frontend_provenance.get("git", {}).get("commit") or ""),
        "api_sandbox_executor_image_matches_expected": api_executor_image == sandbox_executor_image,
        "worker_sandbox_executor_image_matches_expected": worker_executor_image == sandbox_executor_image,
    }
    runtime["api_worker_sandbox_executor_images_match"] = (
        api_executor_image == worker_executor_image and api_executor_image is not None
    )
    runtime["worker_commit"] = str(runtime["worker_heartbeat"].get("runtime_commit") or "")
    return build_parity_report(
        expected_commit=normalized,
        source={"commit": normalized, "dirty": False, "path": str(repo_root.resolve())},
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=selection.working_dir,
        expected_compose_files=[path.as_posix() for path in selection.absolute_paths],
        expected_repository=repository,
    )


def _verified_current_runtime(
    docker: list[str],
    target_selection: _ComposeSelection,
    *,
    docker_cmd: str,
) -> dict[str, Any]:
    """Verify the live role provenance before selecting any auto-release action."""
    selections: list[_ComposeSelection] = []
    commits: list[str] = []
    for role in ("api", "worker", "frontend"):
        record, _ = _container_inspect_record(docker, f"ai-platform-{role}")
        labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
        if labels.get("ai-platform.source-dirty") != "false":
            raise ReleaseAuthorityError("current runtime provenance is invalid")
        try:
            commit = _normalize_commit(str(labels.get("ai-platform.source-commit") or ""))
        except ReleaseAuthorityError as exc:
            raise ReleaseAuthorityError("current runtime provenance is invalid") from exc
        owned_selection = _compose_ownership_selection(labels, target_selection)
        if owned_selection is None:
            raise ReleaseAuthorityError("current runtime provenance is invalid")
        commits.append(commit)
        selections.append(owned_selection)
    if len(set(commits)) != 1 or len(set(selections)) != 1:
        raise ReleaseAuthorityError("current runtime provenance is invalid")
    current_commit = commits[0]
    current_selection = selections[0]
    parity = collect_live_parity(
        current_selection.checkout_root,
        current_commit,
        docker_cmd=docker_cmd,
        compose_files=current_selection.relative_paths,
    )
    if parity.get("verified") is not True:
        raise ReleaseAuthorityError("current runtime provenance is invalid")
    return {
        "commit": current_commit,
        "references": build_image_references(current_commit),
        "parity": parity,
    }


def _auto_release_plan(
    repo_root: Path,
    target_commit: str,
    current_commit: str,
) -> AutoReleasePlan:
    """Classify the verified live-to-target diff from the exact target checkout."""
    paths = _git_paths(repo_root, "diff", "--name-only", f"{current_commit}..{target_commit}")
    return build_auto_release_plan(current_commit, target_commit, classify_runtime_changes(paths))


def deploy_clean_commit(
    repo_root: Path,
    commit: str,
    *,
    docker_cmd: str,
    env_file: Path,
    replace_known_manual_frontend: bool,
    expected_manual_frontend_image: str | None = None,
    expected_manual_frontend_image_id: str | None = None,
    compose_files: Sequence[str | Path] | None = None,
    strategy: str = "canonical",
    auto_plan: AutoReleasePlan | None = None,
    current_references: dict[str, str] | None = None,
    stage_events: list[dict[str, Any]] | None = None,
    managed_release_root: Path | None = None,
    canonical_dependency_build_timeout_seconds: int = CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Build immutable images and recreate the repo-local compose release."""
    if strategy not in {"canonical", "auto"}:
        raise ReleaseAuthorityError("release strategy is invalid")
    if strategy == "auto" and (auto_plan is None or current_references is None):
        raise ReleaseAuthorityError("auto release plan is required")
    canonical_dependency_build_timeout_seconds = (
        _validate_canonical_dependency_build_timeout(
            canonical_dependency_build_timeout_seconds
        )
    )
    events = stage_events if stage_events is not None else []
    if managed_release_root is not None:
        compose_env_file = resolve_managed_env_file(managed_release_root, Path(env_file))
    else:
        compose_env_file = Path(env_file).resolve()
    if managed_release_root is not None:
        normalized = assert_managed_target_checkout(repo_root, commit, managed_release_root)
    else:
        normalized = assert_clean_commit(repo_root, commit)
    selection = resolve_compose_files(repo_root, compose_files)
    docker = _docker_base(docker_cmd)
    repository = authoritative_repository(repo_root)
    ownership = _preflight_managed_container_ownership(
        docker,
        selection,
        replace_known_manual_frontend=replace_known_manual_frontend,
        expected_manual_frontend_image=expected_manual_frontend_image,
        expected_manual_frontend_image_id=expected_manual_frontend_image_id,
    )
    refs = build_image_references(normalized)
    images: dict[str, dict[str, Any]] = {}
    for role, reference in refs.items():
        image_lookup_started = time.monotonic()
        image = _existing_release_image(
            docker,
            reference,
            commit=normalized,
            repository=repository,
            role=role,
        )
        if image is not None and strategy == "auto":
            events.append(
                {
                    "stage": f"{role}-image",
                    "strategy": strategy,
                    "action": "reuse-verified-target",
                    "status": "ok",
                    "wall_time_seconds": round(time.monotonic() - image_lookup_started, 3),
                }
            )
        if image is None:
            item = (
                next(plan for plan in auto_plan.roles if plan.role == role)
                if auto_plan is not None
                else RolePlan(role, "dependency", "canonical-build", ())
            )
            if item.action == "canonical-build":
                _stage(
                    events,
                    name=f"{role}-image",
                    strategy=strategy,
                    action=item.action,
                    operation=lambda: _canonical_or_source_build(
                        docker,
                        repo_root=repo_root,
                        reference=reference,
                        commit=normalized,
                        repository=repository,
                        role=role,
                        source_only=False,
                        canonical_dependency_build_timeout_seconds=(
                            canonical_dependency_build_timeout_seconds
                        ),
                    ),
                    timeout_seconds=canonical_dependency_build_timeout_seconds,
                )
            elif item.action == "source-build":
                _stage(
                    events,
                    name=f"{role}-image",
                    strategy=strategy,
                    action=item.action,
                    operation=lambda: _canonical_or_source_build(
                        docker,
                        repo_root=repo_root,
                        reference=reference,
                        commit=normalized,
                        repository=repository,
                        role=role,
                        source_only=True,
                    ),
                )
            elif item.action in {"runtime-rebuild", "promote"}:
                assert current_references is not None
                base_reference = current_references.get(role)
                if not base_reference:
                    raise ReleaseAuthorityError("verified current role image is unavailable")
                base_image = _existing_release_image(
                    docker,
                    base_reference,
                    commit=auto_plan.current_commit if auto_plan is not None else normalized,
                    repository=repository,
                    role=role,
                )
                if base_image is None:
                    raise ReleaseAuthorityError("verified current role image is unavailable")
                dockerfile = (
                    _backend_runtime_dockerfile()
                    if item.action == "runtime-rebuild"
                    else _promotion_dockerfile(role)
                )
                _stage(
                    events,
                    name=f"{role}-image",
                    strategy=strategy,
                    action=item.action,
                    operation=lambda: _build_from_verified_role_image(
                        docker,
                        repo_root=repo_root,
                        reference=reference,
                        base_reference=base_reference,
                        commit=normalized,
                        repository=repository,
                        role=role,
                        dockerfile=dockerfile,
                    ),
                )
            elif item.action == "reuse":
                raise ReleaseAuthorityError("verified target role image is unavailable")
            else:
                raise ReleaseAuthorityError("auto release role action is invalid")
            image = _stage(
                events,
                name=f"{role}-image-validate",
                strategy=strategy,
                action="validate",
                operation=lambda: _image_record(docker, reference),
            )
            _validate_release_image(image, commit=normalized, repository=repository, role=role)
        images[role] = image

    images["backend"] = _require_sandbox_executor_image(
        docker,
        refs["backend"],
        commit=normalized,
        repository=repository,
    )

    if managed_release_root is not None:
        assert_managed_target_checkout(repo_root, normalized, managed_release_root)
    else:
        assert_clean_commit(repo_root, normalized)
    revalidated = resolve_compose_files(repo_root, selection.relative_paths)
    if revalidated != selection:
        raise ReleaseAuthorityError("compose file selection changed during release preflight")
    revalidated_ownership = _preflight_managed_container_ownership(
        docker,
        selection,
        replace_known_manual_frontend=replace_known_manual_frontend,
        expected_manual_frontend_image=expected_manual_frontend_image,
        expected_manual_frontend_image_id=expected_manual_frontend_image_id,
    )
    if revalidated_ownership != ownership:
        raise ReleaseAuthorityError("managed container ownership changed during release preflight")
    if managed_release_root is not None:
        compose_env_file = resolve_managed_env_file(managed_release_root, compose_env_file)
    if ownership.manual_frontend_id is not None:
        _stage(
            events,
            name="manual-frontend-removal",
            strategy=strategy,
            action="remove",
            operation=lambda: _run(
                [*docker, "container", "rm", "-f", ownership.manual_frontend_id]
            ),
        )

    compose_environment = [
        f"AI_PLATFORM_IMAGE={refs['backend']}",
        f"AI_PLATFORM_FRONTEND_IMAGE={refs['frontend']}",
        f"SANDBOX_EXECUTOR_IMAGE={_immutable_sandbox_executor_reference(images['backend'])}",
        f"AI_PLATFORM_SOURCE_COMMIT={normalized}",
        f"AI_PLATFORM_BUILD_COMMIT={normalized}",
        "AI_PLATFORM_BUILD_DIRTY=false",
    ]
    if docker[:2] == ["sudo", "-n"]:
        compose_command = ["sudo", "-n", "env", *compose_environment, "docker"]
    else:
        compose_command = ["env", *compose_environment, *docker]
    compose_file_args = [
        argument
        for path in selection.absolute_paths
        for argument in ("-f", str(path))
    ]
    _stage(
        events,
        name="compose-recreate",
        strategy=strategy,
        action="converge",
        operation=lambda: _run(
            [
                *compose_command,
                "compose",
                "-p",
                COMPOSE_PROJECT,
                "--env-file",
                str(compose_env_file),
                *compose_file_args,
                "up",
                "-d",
                "--no-build",
            ],
            cwd=selection.absolute_paths[0].parent,
        ),
    )
    result = {
        "commit": normalized,
        "images": refs,
        "sandbox_executor_image": _immutable_sandbox_executor_reference(images["backend"]),
        "compose_file": str(selection.absolute_paths[0]),
        "compose_files": [str(path) for path in selection.absolute_paths],
        "strategy": strategy,
        "stages": events,
    }
    if auto_plan is not None:
        result["plan"] = _plan_as_dict(
            auto_plan,
            canonical_dependency_build_timeout_seconds=(
                canonical_dependency_build_timeout_seconds
            ),
        )
    return result


def deploy_main_commit(
    release_root: Path,
    commit: str,
    *,
    docker_cmd: str,
    env_file: Path | None,
    replace_known_manual_frontend: bool,
    expected_manual_frontend_image: str | None = None,
    expected_manual_frontend_image_id: str | None = None,
    compose_files: Sequence[str | Path] | None = None,
    strategy: str = "canonical",
    coordination_source: Path | None = None,
    canonical_dependency_build_timeout_seconds: int = CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Deploy and verify an exact fetched main commit from an isolated checkout."""
    if strategy not in {"canonical", "auto"}:
        raise ReleaseAuthorityError("release strategy is invalid")
    canonical_dependency_build_timeout_seconds = (
        _validate_canonical_dependency_build_timeout(
            canonical_dependency_build_timeout_seconds
        )
    )
    normalized = _normalize_commit(commit)
    if coordination_source is not None:
        assert_clean_coordination_source(coordination_source)
    managed_env_file = resolve_managed_env_file(release_root, env_file)
    checkout = materialize_main_checkout(release_root, normalized)
    if strategy == "canonical":
        deployment = deploy_clean_commit(
            checkout,
            normalized,
            docker_cmd=docker_cmd,
            env_file=managed_env_file,
            replace_known_manual_frontend=replace_known_manual_frontend,
            expected_manual_frontend_image=expected_manual_frontend_image,
            expected_manual_frontend_image_id=expected_manual_frontend_image_id,
            compose_files=compose_files,
            managed_release_root=release_root,
            canonical_dependency_build_timeout_seconds=(
                canonical_dependency_build_timeout_seconds
            ),
        )
        parity = collect_live_parity(
            checkout,
            normalized,
            docker_cmd=docker_cmd,
            compose_files=compose_files,
        )
        if parity.get("verified") is not True:
            mismatches = parity.get("mismatches")
            detail = ", ".join(str(item) for item in mismatches) if isinstance(mismatches, list) else "unknown"
            raise ReleaseAuthorityError(f"deployed release parity failed: {detail}")
    else:
        events: list[dict[str, Any]] = []
        assert_managed_target_checkout(checkout, normalized, release_root)
        target_selection = resolve_compose_files(checkout, compose_files)
        docker = _docker_base(docker_cmd)
        current = _stage(
            events,
            name="current-runtime-provenance",
            strategy=strategy,
            action="verify",
            operation=lambda: _verified_current_runtime(
                docker,
                target_selection,
                docker_cmd=docker_cmd,
            ),
        )
        plan = _stage(
            events,
            name="runtime-diff-classification",
            strategy=strategy,
            action="plan",
            operation=lambda: _auto_release_plan(checkout, normalized, current["commit"]),
        )
        deployment = deploy_clean_commit(
            checkout,
            normalized,
            docker_cmd=docker_cmd,
            env_file=managed_env_file,
            replace_known_manual_frontend=replace_known_manual_frontend,
            expected_manual_frontend_image=expected_manual_frontend_image,
            expected_manual_frontend_image_id=expected_manual_frontend_image_id,
            compose_files=compose_files,
            strategy=strategy,
            auto_plan=plan,
            current_references=current["references"],
            stage_events=events,
            managed_release_root=release_root,
            canonical_dependency_build_timeout_seconds=(
                canonical_dependency_build_timeout_seconds
            ),
        )

        def final_parity() -> dict[str, Any]:
            report = collect_live_parity(
                checkout,
                normalized,
                docker_cmd=docker_cmd,
                compose_files=compose_files,
            )
            if report.get("verified") is not True:
                raise ReleaseAuthorityError("deployed release parity failed")
            return report

        parity = _stage(
            events,
            name="final-parity",
            strategy=strategy,
            action="verify",
            operation=final_parity,
        )
    return {
        "commit": normalized,
        "checkout": str(checkout),
        "deployment": deployment,
        "parity": parity,
    }


def _write_json(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")


def main() -> int:
    """Run the release-authority preservation, deployment, or verification command."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preserve = subparsers.add_parser("preserve-dirty", help="Preserve dirty source without cleaning it")
    preserve.add_argument("--repo-root", type=Path, required=True)
    preserve.add_argument("--output-root", type=Path, required=True)

    deploy = subparsers.add_parser("deploy", help="Build and deploy one clean commit")
    deploy.add_argument("--repo-root", type=Path, required=True)
    deploy.add_argument("--commit", required=True)
    deploy.add_argument("--docker-cmd", default="docker")
    deploy.add_argument("--env-file", type=Path, required=True)
    deploy.add_argument("--replace-known-manual-frontend", action="store_true")
    deploy.add_argument("--expected-manual-frontend-image")
    deploy.add_argument("--expected-manual-frontend-image-id")
    deploy.add_argument(
        "--canonical-build-timeout-seconds",
        type=_canonical_dependency_build_timeout_argument,
        default=CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "Per-stage dependency-triggered canonical build timeout "
            f"({MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS}.."
            f"{MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS}; "
            f"default: {CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS})"
        ),
    )
    deploy.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        metavar="REPO_RELATIVE_PATH",
        help="Ordered repo-relative Compose file; repeat for overlays",
    )

    deploy_main = subparsers.add_parser(
        "deploy-main-commit",
        help="Fetch, deploy, and verify one exact main commit",
    )
    deploy_main.add_argument("--release-root", type=Path, required=True)
    deploy_main.add_argument("--commit", required=True)
    deploy_main.add_argument("--docker-cmd", default="docker")
    deploy_main.add_argument(
        "--env-file",
        type=Path,
        help=(
            "Compatibility override accepted only when it equals the canonical "
            "<managed-root>/deploy/ai-platform/.env derived from --release-root"
        ),
    )
    deploy_main.add_argument("--replace-known-manual-frontend", action="store_true")
    deploy_main.add_argument("--expected-manual-frontend-image")
    deploy_main.add_argument("--expected-manual-frontend-image-id")
    deploy_main.add_argument(
        "--canonical-build-timeout-seconds",
        type=_canonical_dependency_build_timeout_argument,
        default=CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "Per-stage dependency-triggered canonical build timeout "
            f"({MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS}.."
            f"{MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS}; "
            f"default: {CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS})"
        ),
    )
    deploy_main.add_argument(
        "--strategy",
        choices=("auto", "canonical"),
        default="auto",
        help="Role-specific release strategy; auto reuses verified current provenance",
    )
    deploy_main.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        metavar="REPO_RELATIVE_PATH",
        help="Ordered repo-relative Compose file; repeat for overlays",
    )

    verify = subparsers.add_parser("verify", help="Verify source/image/runtime commit parity")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--docker-cmd", default="docker")
    verify.add_argument("--output", type=Path)
    verify.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        metavar="REPO_RELATIVE_PATH",
        help="Ordered repo-relative Compose file; repeat for overlays",
    )

    args = parser.parse_args()
    try:
        if args.command == "preserve-dirty":
            destination = preserve_dirty_source(args.repo_root, args.output_root)
            _write_json({"preserved": True, "path": str(destination)}, None)
        elif args.command == "deploy":
            _write_json(
                deploy_clean_commit(
                    args.repo_root,
                    args.commit,
                    docker_cmd=args.docker_cmd,
                    env_file=args.env_file,
                    replace_known_manual_frontend=args.replace_known_manual_frontend,
                    expected_manual_frontend_image=args.expected_manual_frontend_image,
                    expected_manual_frontend_image_id=args.expected_manual_frontend_image_id,
                    compose_files=args.compose_files,
                    canonical_dependency_build_timeout_seconds=(
                        args.canonical_build_timeout_seconds
                    ),
                ),
                None,
            )
        elif args.command == "deploy-main-commit":
            _write_json(
                deploy_main_commit(
                    args.release_root,
                    args.commit,
                    docker_cmd=args.docker_cmd,
                    env_file=args.env_file,
                    replace_known_manual_frontend=args.replace_known_manual_frontend,
                    expected_manual_frontend_image=args.expected_manual_frontend_image,
                    expected_manual_frontend_image_id=args.expected_manual_frontend_image_id,
                    compose_files=args.compose_files,
                    strategy=args.strategy,
                    coordination_source=Path.cwd(),
                    canonical_dependency_build_timeout_seconds=(
                        args.canonical_build_timeout_seconds
                    ),
                ),
                None,
            )
        else:
            report = collect_live_parity(
                args.repo_root,
                args.commit,
                docker_cmd=args.docker_cmd,
                compose_files=args.compose_files,
            )
            _write_json(report, args.output)
            return 0 if report["verified"] else 1
    except ReleaseAuthorityError as exc:
        payload: dict[str, Any] = {"verified": False, "error": str(exc), "command": args.command}
        stage_events = getattr(exc, "stage_events", None)
        if isinstance(stage_events, tuple):
            payload["stages"] = list(stage_events)
        _write_json(payload, None)
        return 2
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        _write_json(
            {
                "verified": False,
                "error": "release authority command failed",
                "command": args.command,
            },
            None,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
