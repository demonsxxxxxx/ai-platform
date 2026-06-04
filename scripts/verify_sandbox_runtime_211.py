"""211 sandbox runtime verification gate.

This script is a verifier, not a deployer. It exits non-zero until the host has
real Docker access, workspace writeability, executor health, callback evidence,
cancel evidence, and no secret leakage in the evidence payload.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import request as urllib_request


SENSITIVE_PATTERNS = [
    re.compile(r"/var/run/docker\.sock", re.IGNORECASE),
    re.compile(r"%2Fvar%2Frun%2Fdocker\.sock", re.IGNORECASE),
    re.compile(r"/runtime/tenants[^\s\"']*", re.IGNORECASE),
    re.compile(r"/home/[^\s\"']*", re.IGNORECASE),
    re.compile(r"/tmp/[^\s\"']*", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\[^\s\"']*"),
    re.compile(r"callback[_-]?token", re.IGNORECASE),
    re.compile(r'"[^"]*token[^"]*"\s*:\s*"[^"]*"', re.IGNORECASE),
    re.compile(r"\btoken\b\s*[:=]\s*[^,\s\"'}]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"RAGFLOW_API_KEY", re.IGNORECASE),
    re.compile(r"ANTHROPIC_AUTH_TOKEN", re.IGNORECASE),
    re.compile(r"access[_-]?key", re.IGNORECASE),
    re.compile(r"storage[_-]?key", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
]


class CheckResult:
    def __init__(self, name: str, passed: bool, message: str) -> None:
        self.name = name
        self.passed = passed
        self.message = sanitize_message(message)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
        }


def sanitize_message(message: object) -> str:
    text = str(message)
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _read_evidence(path: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return None, "evidence file missing"
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "evidence file is not valid JSON"
    if not isinstance(data, dict):
        return None, "evidence root must be an object"
    return data, None


def _evidence_metadata_error(
    evidence: dict[str, Any],
    *,
    run_id: str,
    executor_url: str,
    max_age_seconds: int = 900,
) -> str | None:
    if not run_id:
        return "run_id argument required"
    if run_id and evidence.get("run_id") != run_id:
        return "run_id evidence mismatch"
    if executor_url and evidence.get("executor_url") != executor_url:
        return "executor_url evidence mismatch"
    if evidence.get("executed_task") is not True:
        return "executed task evidence missing"
    if evidence.get("callback_auth") not in {True, "verified", "token"}:
        return "callback auth evidence missing"
    generated_at = evidence.get("generated_at")
    if not isinstance(generated_at, str):
        return "fresh evidence timestamp missing"
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return "fresh evidence timestamp invalid"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > max_age_seconds:
        return "fresh evidence timestamp stale"
    return None


def check_docker_socket(
    docker_cmd: tuple[str, ...] = ("docker",),
    *,
    run: Callable[..., Any] = subprocess.run,
) -> CheckResult:
    try:
        completed = run(
            [*docker_cmd, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return CheckResult("check_docker_socket", False, f"Docker probe failed: {exc}")
    if getattr(completed, "returncode", 1) != 0:
        output = getattr(completed, "stderr", "") or getattr(completed, "stdout", "")
        return CheckResult("check_docker_socket", False, f"Docker unavailable: {output}")
    return CheckResult("check_docker_socket", True, "Docker daemon reachable")


def check_workspace_write(workspace_root: str | Path) -> CheckResult:
    root = Path(workspace_root)
    probe = root / f".ai-platform-sandbox-probe-{uuid.uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "ok":
            return CheckResult("check_workspace_write", False, "workspace readback failed")
        return CheckResult("check_workspace_write", True, "workspace writeable")
    except Exception as exc:
        return CheckResult("check_workspace_write", False, f"workspace probe failed: {exc}")
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def check_executor_health(
    executor_url: str,
    *,
    timeout_seconds: float = 5.0,
    urlopen: Callable[..., Any] = urllib_request.urlopen,
) -> CheckResult:
    if not executor_url:
        return CheckResult("check_executor_health", False, "executor URL not configured")
    health_url = f"{executor_url.rstrip('/')}/health"
    try:
        request = urllib_request.Request(health_url, method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 0)
    except Exception as exc:
        return CheckResult("check_executor_health", False, f"executor health failed: {exc}")
    if status != 200:
        return CheckResult("check_executor_health", False, f"executor health HTTP {status}")
    if "ready" not in body.lower() and "ok" not in body.lower():
        return CheckResult("check_executor_health", False, "executor health response not ready")
    return CheckResult("check_executor_health", True, "executor health ready")


def check_callback_stream(
    evidence_path: str | Path,
    *,
    run_id: str = "",
    executor_url: str = "",
) -> CheckResult:
    evidence, error = _read_evidence(evidence_path)
    if error:
        return CheckResult("check_callback_stream", False, error)
    metadata_error = _evidence_metadata_error(evidence, run_id=run_id, executor_url=executor_url)
    if metadata_error:
        return CheckResult("check_callback_stream", False, metadata_error)
    callbacks = evidence.get("callbacks", [])
    if not isinstance(callbacks, list):
        return CheckResult("check_callback_stream", False, "callbacks must be a list")
    statuses = {
        item.get("status")
        for item in callbacks
        if isinstance(item, dict) and (not run_id or item.get("run_id") == run_id)
    }
    if "running" not in statuses:
        return CheckResult("check_callback_stream", False, "running callback missing")
    if statuses.isdisjoint({"completed", "failed", "cancelled"}):
        return CheckResult("check_callback_stream", False, "terminal callback missing")
    return CheckResult("check_callback_stream", True, "callback stream evidence present")


def check_cancel_stops_container(evidence_path: str | Path, *, run_id: str = "") -> CheckResult:
    evidence, error = _read_evidence(evidence_path)
    if error:
        return CheckResult("check_cancel_stops_container", False, error)
    if not run_id:
        return CheckResult("check_cancel_stops_container", False, "run_id argument required")
    if run_id and evidence.get("run_id") != run_id:
        return CheckResult("check_cancel_stops_container", False, "run_id evidence mismatch")
    if evidence.get("cancel_stops_container") is True:
        if not evidence.get("cancelled_container_id"):
            return CheckResult("check_cancel_stops_container", False, "cancelled container evidence missing")
        return CheckResult("check_cancel_stops_container", True, "cancel stop evidence present")
    return CheckResult("check_cancel_stops_container", False, "cancel stop evidence missing")


def check_no_secret_leakage(evidence_path: str | Path) -> CheckResult:
    evidence_path = Path(evidence_path)
    if not evidence_path.exists():
        return CheckResult("check_no_secret_leakage", False, "evidence file missing")
    try:
        content = evidence_path.read_text(encoding="utf-8")
    except Exception as exc:
        return CheckResult("check_no_secret_leakage", False, f"evidence read failed: {exc}")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(content):
            return CheckResult("check_no_secret_leakage", False, "sensitive evidence detected")
    return CheckResult("check_no_secret_leakage", True, "no sensitive evidence detected")


def run_checks(checks: list[Callable[[], CheckResult]]) -> tuple[int, list[CheckResult]]:
    results = [check() for check in checks]
    exit_code = 0 if all(result.passed for result in results) else 1
    return exit_code, results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify ai-platform sandbox runtime on 211")
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("AI_PLATFORM_SANDBOX_WORKSPACE_ROOT", "/tmp/ai-platform-sandbox-workspaces"),
    )
    parser.add_argument("--executor-url", default=os.environ.get("AI_PLATFORM_EXECUTOR_URL", ""))
    parser.add_argument(
        "--evidence-file",
        default=os.environ.get("AI_PLATFORM_SANDBOX_EVIDENCE", "/tmp/ai-platform-sandbox-runtime-evidence.json"),
    )
    parser.add_argument("--run-id", default=os.environ.get("AI_PLATFORM_SANDBOX_RUN_ID", ""))
    parser.add_argument("--docker-cmd", default=os.environ.get("DOCKER_CMD", "docker"))
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    docker_cmd = tuple(part for part in args.docker_cmd.split(" ") if part)
    checks = [
        lambda: check_docker_socket(docker_cmd),
        lambda: check_workspace_write(args.workspace_root),
        lambda: check_executor_health(args.executor_url),
        lambda: check_callback_stream(args.evidence_file, run_id=args.run_id, executor_url=args.executor_url),
        lambda: check_cancel_stops_container(args.evidence_file, run_id=args.run_id),
        lambda: check_no_secret_leakage(args.evidence_file),
    ]
    exit_code, results = run_checks(checks)
    if args.json_output:
        print(json.dumps({"checks": [result.to_dict() for result in results]}, ensure_ascii=True, indent=2))
    else:
        failed = [result.name for result in results if not result.passed]
        if failed:
            print("FAILED:", ", ".join(failed))
        else:
            print("PASSED: sandbox runtime verifier checks passed")
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(f"{status} {result.name}: {result.message}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
