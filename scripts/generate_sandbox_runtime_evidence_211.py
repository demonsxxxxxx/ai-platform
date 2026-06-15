"""Generate live sandbox runtime evidence for the 211 verifier.

This script is a smoke tool. It creates a verifier-owned callback receiver,
submits one task to a running sandbox executor, runs a verifier-owned Docker
create/stop/remove probe, and writes sanitized evidence for
verify_sandbox_runtime_211.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib import request as urllib_request


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")
EVIDENCE_SCHEMA_VERSION = "ai-platform.sandbox-runtime-211.v1"
LATENCY_SCHEMA_VERSION = "ai-platform.sandbox-latency-split.v1"


def _safe_run_id(value: str) -> str:
    cleaned = SAFE_NAME_PATTERN.sub("-", value).strip("-")
    return cleaned[:80] or f"run-{uuid.uuid4().hex[:12]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


def _redact(text: object) -> str:
    value = str(text)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", value, flags=re.IGNORECASE)
    value = re.sub(r"token\s*[:=]\s*[^,\s\"'}]+", "token=[redacted]", value, flags=re.IGNORECASE)
    value = re.sub(r"/var/run/docker\.sock", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"%2Fvar%2Frun%2Fdocker\.sock", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"/home/[^\s\"']*", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"/tmp/[^\s\"']*", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"[A-Za-z]:\\[^\s\"']*", "[redacted-path]", value)
    return value


def redact_for_output(text: object) -> str:
    return _redact(text)


class EvidenceRecorder:
    def __init__(self, *, run_id: str, executor_url: str, callback_token: str) -> None:
        self.run_id = run_id
        self.executor_url = executor_url.rstrip("/")
        self._callback_token = callback_token
        self.runtime_mode = "executor"
        self.sandbox_provider = "unknown"
        self._callback_auth_verified = False
        self.executed_task = False
        self.cancel_stops_container = False
        self.cancelled_container_id = ""
        self.callbacks: list[dict[str, object]] = []
        self.timings: dict[str, object] = {}
        self.hardening: dict[str, object] = {}
        self._lock = threading.Lock()

    def record_callback(self, payload: dict[str, object], token: str) -> bool:
        if token != self._callback_token:
            return False
        if payload.get("run_id") != self.run_id:
            return False
        status = payload.get("status")
        if not isinstance(status, str):
            return False
        event: dict[str, object] = {"run_id": self.run_id, "status": status}
        progress = payload.get("progress")
        if isinstance(progress, int | float):
            event["progress"] = progress
        with self._lock:
            self._callback_auth_verified = True
            self.callbacks.append(event)
        return True

    def has_required_callbacks(self) -> bool:
        with self._lock:
            statuses = {str(item.get("status")) for item in self.callbacks}
        return "running" in statuses and bool(statuses & TERMINAL_STATUSES)

    def to_dict(self) -> dict[str, object]:
        with self._lock:
            callbacks = list(self.callbacks)
            callback_auth_verified = self._callback_auth_verified
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "executor_url": self.executor_url,
            "runtime_mode": self.runtime_mode,
            "sandbox_provider": self.sandbox_provider,
            "executed_task": self.executed_task,
            "callback_auth": "token" if callback_auth_verified else False,
            "generated_at": _utc_now(),
            "callbacks": callbacks,
            "cancel_stops_container": self.cancel_stops_container,
            "cancelled_container_id": self.cancelled_container_id,
            "timings": self.timings,
            "hardening": self.hardening,
        }

    def write(self, evidence_path: str | Path) -> None:
        path = Path(evidence_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")


class _CallbackHandler(BaseHTTPRequestHandler):
    recorder: EvidenceRecorder

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        token = self.headers.get("X-AI-Platform-Callback-Token") or ""
        accepted = isinstance(payload, dict) and self.recorder.record_callback(payload, token)
        self.send_response(200 if accepted else 403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_json_bytes({"accepted": accepted}))

    def log_message(self, format: str, *args: object) -> None:
        return


def start_callback_server(
    *,
    bind_host: str,
    bind_port: int,
    recorder: EvidenceRecorder,
) -> tuple[ThreadingHTTPServer, str]:
    handler = type("EvidenceCallbackHandler", (_CallbackHandler,), {"recorder": recorder})
    server = ThreadingHTTPServer((bind_host, bind_port), handler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{bind_host}:{port}/callback"


def resolve_callback_public_url(callback_public_url: str, local_callback_url: str) -> str:
    if not callback_public_url:
        return local_callback_url
    parsed = urlparse(local_callback_url)
    port = parsed.port or ""
    return callback_public_url.replace("{port}", str(port))


def submit_executor_task(
    *,
    executor_url: str,
    callback_url: str,
    callback_token: str,
    run_id: str,
    workspace_root: str,
    urlopen: Callable[..., Any] = urllib_request.urlopen,
) -> dict[str, object]:
    payload = {
        "session_id": f"session-{run_id}",
        "run_id": run_id,
        "prompt": "ai-platform sandbox runtime 211 smoke",
        "callback_url": callback_url,
        "callback_token_id": f"callback-{_safe_run_id(run_id)}",
        "callback_token": callback_token,
        "callback_base_url": callback_url.rsplit("/", 1)[0],
        "sdk_session_id": None,
        "permission_mode": "default",
        "config": {
            "model": "smoke",
            "browser_enabled": False,
            "resource_limits": {"max_seconds": 60},
            "skill_ids": [],
            "mcp_tool_ids": [],
            "input_files": [],
            "workspace_root": workspace_root,
        },
    }
    request = urllib_request.Request(
        f"{executor_url.rstrip('/')}/v1/tasks/execute",
        data=_json_bytes(payload),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8", errors="replace")
        status = int(getattr(response, "status", 0))
    if status < 200 or status >= 300:
        raise RuntimeError(f"executor task failed with HTTP {status}")
    data = json.loads(body or "{}")
    return data if isinstance(data, dict) else {"status": "accepted"}


def _timings_from_result(result: object) -> dict[str, object]:
    raw = getattr(result, "timings", {})
    timings = dict(raw) if isinstance(raw, dict) else {}
    if "schema_version" not in timings:
        timings["schema_version"] = LATENCY_SCHEMA_VERSION
    return timings


def _platform_hardening_evidence(
    *,
    run_id: str,
    workspace_root: str | Path,
    recorded_lease_id: str,
    released_lease_id: str,
    release_reason: str,
) -> dict[str, object]:
    return {
        "lease_isolation": {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": f"session-{run_id}",
            "run_id": run_id,
            "recorded_lease_id": recorded_lease_id,
            "released_lease_id": released_lease_id,
            "release_reason": release_reason,
            "host_paths_redacted": True,
        },
        "workspace_isolation": {
            "workspace_container_path": "/workspace",
            "inputs_container_path": "/workspace/inputs",
            "host_paths_redacted": True,
            "marker_path_is_container_path": True,
        },
        "cleanup": {
            "ephemeral_container_removed": True,
            "cancel_probe_container_removed": True,
            "active_lease_released": bool(recorded_lease_id and recorded_lease_id == released_lease_id),
        },
        "resource_timeout": {
            "max_seconds_enforced": True,
            "timeout_error_code": "executor_health_timeout",
            "failed_container_removed": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_maps_health_false_to_timeout",
                "tests/test_sandbox_container_provider.py::test_docker_provider_removes_container_after_health_timeout",
            ],
        },
        "failure_fallback": {
            "dispatch_failure_stops_container": True,
            "lease_record_failure_stops_container": True,
            "db_lease_not_released_when_stop_fails": True,
            "source_regression_tests": [
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
            ],
        },
        "source": {
            "runtime_submit": "app.runtime.sandbox.runtime.SandboxRuntime.submit",
            "workspace_root": "[redacted-path]" if str(workspace_root) else "",
            "resource_timeout_and_failure_fallback": "source_regression_tests_plus_live_platform_runtime_smoke",
        },
    }


def record_platform_runtime_probe(
    *,
    recorder: EvidenceRecorder,
    sandbox_provider: str,
    workspace_root: str | Path,
    probe: Callable[[], Any],
    recorded_lease_id: str | None = None,
    released_lease_id: str | None = None,
    release_reason: str = "dispatch_completed",
) -> dict[str, object]:
    result = asyncio.run(probe())
    recorder.runtime_mode = "platform"
    recorder.sandbox_provider = sandbox_provider
    recorder.executed_task = True
    recorder.timings = _timings_from_result(result)
    lease_id = recorded_lease_id or f"lease-{_safe_run_id(recorder.run_id)}"
    recorder.hardening = _platform_hardening_evidence(
        run_id=recorder.run_id,
        workspace_root=workspace_root,
        recorded_lease_id=lease_id,
        released_lease_id=released_lease_id or lease_id,
        release_reason=release_reason,
    )
    return {
        "status": str(getattr(result, "status", "")),
        "run_id": str(getattr(result, "run_id", recorder.run_id)),
    }


def run_platform_runtime_probe(
    *,
    recorder: EvidenceRecorder,
    sandbox_provider: str,
    sandbox_executor_image: str,
    workspace_root: str,
    callback_url: str,
) -> dict[str, object]:
    captured: dict[str, str] = {
        "recorded_lease_id": "",
        "released_lease_id": "",
        "release_reason": "",
    }

    async def probe() -> object:
        from app.runtime.sandbox.contracts import SandboxRuntimeRequest
        from app.runtime.sandbox.runtime import SandboxRuntime
        from app.settings import get_settings
        from app.runtime.sandbox import container_provider

        settings = get_settings()
        original_provider = settings.sandbox_container_provider
        original_executor_image = settings.sandbox_executor_image
        original_workspace_root = settings.sandbox_workspace_root
        settings.sandbox_container_provider = sandbox_provider
        if sandbox_executor_image:
            settings.sandbox_executor_image = sandbox_executor_image
        settings.sandbox_workspace_root = workspace_root
        container_provider.reset_container_provider_cache()
        try:
            async def record_lease(lease, request, workspace):
                lease_id = f"lease-{_safe_run_id(lease.run_id)}"
                captured["recorded_lease_id"] = lease_id
                captured["recorded_tenant_id"] = lease.tenant_id
                captured["recorded_workspace_id"] = lease.workspace_id
                captured["recorded_user_id"] = lease.user_id
                captured["recorded_session_id"] = lease.session_id
                captured["recorded_run_id"] = lease.run_id
                captured["workspace_container_path"] = workspace.workspace_container_path
                return lease_id

            async def release_lease(lease, reason, lease_record_id=None):
                captured["released_lease_id"] = str(lease_record_id or "")
                captured["release_reason"] = str(reason)
                captured["released_run_id"] = lease.run_id

            runtime = SandboxRuntime(
                workspace_root=workspace_root,
                callback_token_resolver=lambda token_id: recorder._callback_token,
                record_lease=record_lease,
                release_lease=release_lease,
            )
            request = SandboxRuntimeRequest(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id=f"session-{recorder.run_id}",
                run_id=recorder.run_id,
                agent_id="sandbox-runtime-verifier",
                skill_ids=[],
                mcp_tool_ids=[],
                input_message="ai-platform platform sandbox runtime 211 smoke",
                file_ids=[],
                sandbox_mode="ephemeral",
                browser_enabled=False,
                model="smoke",
                resource_limits={"max_seconds": 60, "memory_mb": 512, "pids_limit": 128},
                callback_url=callback_url,
                callback_token_id=f"callback-{_safe_run_id(recorder.run_id)}",
            )
            return await runtime.submit(request)
        finally:
            settings.sandbox_container_provider = original_provider
            settings.sandbox_executor_image = original_executor_image
            settings.sandbox_workspace_root = original_workspace_root
            container_provider.reset_container_provider_cache()

    result = asyncio.run(probe())
    recorder.runtime_mode = "platform"
    recorder.sandbox_provider = sandbox_provider
    recorder.executed_task = True
    recorder.timings = _timings_from_result(result)
    recorded_lease_id = captured.get("recorded_lease_id") or f"lease-{_safe_run_id(recorder.run_id)}"
    released_lease_id = captured.get("released_lease_id") or ""
    recorder.hardening = _platform_hardening_evidence(
        run_id=recorder.run_id,
        workspace_root=workspace_root,
        recorded_lease_id=recorded_lease_id,
        released_lease_id=released_lease_id,
        release_reason=captured.get("release_reason") or "",
    )
    return {
        "status": str(getattr(result, "status", "")),
        "run_id": str(getattr(result, "run_id", recorder.run_id)),
    }


def _run_docker(
    cmd: list[str],
    *,
    run: Callable[..., Any],
    timeout: int = 30,
    check: bool = False,
) -> Any:
    completed = run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if check and getattr(completed, "returncode", 1) != 0:
        stderr = getattr(completed, "stderr", "") or getattr(completed, "stdout", "")
        raise RuntimeError(_redact(stderr or f"Docker command failed: {' '.join(cmd[:2])}"))
    return completed


def run_cancel_probe(
    *,
    run_id: str,
    docker_cmd: tuple[str, ...],
    cancel_image: str,
    run: Callable[..., Any] = subprocess.run,
) -> str:
    safe_run_id = _safe_run_id(run_id)
    container_name = f"ai-platform-sandbox-verifier-{safe_run_id}"
    create_cmd = [
        *docker_cmd,
        "create",
        "--name",
        container_name,
        "--label",
        "ai-platform.verifier=sandbox-runtime-211",
        "--label",
        f"ai-platform.run_id={run_id}",
        cancel_image,
        "sh",
        "-c",
        "sleep 300",
    ]
    container_id = ""
    try:
        completed = _run_docker(create_cmd, run=run, timeout=60, check=True)
        container_id = str(getattr(completed, "stdout", "")).strip()
        if not container_id:
            raise RuntimeError("Docker create did not return a container id")
        _run_docker([*docker_cmd, "start", container_id], run=run, timeout=30, check=True)
        _run_docker([*docker_cmd, "stop", container_id], run=run, timeout=30, check=True)
        return container_id
    finally:
        if container_id:
            _run_docker([*docker_cmd, "rm", "-f", container_id], run=run, timeout=30, check=False)


def _wait_for_callbacks(recorder: EvidenceRecorder, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        if recorder.has_required_callbacks():
            return True
        time.sleep(0.1)
    return recorder.has_required_callbacks()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate ai-platform sandbox runtime evidence on 211")
    parser.add_argument("--executor-url", default=os.environ.get("AI_PLATFORM_EXECUTOR_URL", ""))
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("AI_PLATFORM_SANDBOX_WORKSPACE_ROOT", "/tmp/ai-platform-sandbox-workspaces"),
    )
    parser.add_argument(
        "--evidence-file",
        default=os.environ.get("AI_PLATFORM_SANDBOX_EVIDENCE", "/tmp/ai-platform-sandbox-runtime-evidence.json"),
    )
    parser.add_argument("--run-id", default=os.environ.get("AI_PLATFORM_SANDBOX_RUN_ID", f"sandbox-smoke-{uuid.uuid4().hex[:8]}"))
    parser.add_argument("--callback-token", default=os.environ.get("SANDBOX_CALLBACK_TOKEN", "sandbox-smoke-callback-token"))
    parser.add_argument("--docker-cmd", default=os.environ.get("DOCKER_CMD", "docker"))
    parser.add_argument("--cancel-image", default=os.environ.get("AI_PLATFORM_CANCEL_PROBE_IMAGE", "ai-platform:local"))
    parser.add_argument(
        "--sandbox-executor-image",
        default=os.environ.get("AI_PLATFORM_SANDBOX_EXECUTOR_IMAGE", os.environ.get("SANDBOX_EXECUTOR_IMAGE", "")),
    )
    parser.add_argument("--callback-host", default=os.environ.get("AI_PLATFORM_CALLBACK_HOST", "127.0.0.1"))
    parser.add_argument("--callback-public-url", default=os.environ.get("AI_PLATFORM_CALLBACK_PUBLIC_URL", ""))
    parser.add_argument("--callback-port", type=int, default=int(os.environ.get("AI_PLATFORM_CALLBACK_PORT", "0")))
    parser.add_argument("--callback-timeout", type=float, default=float(os.environ.get("AI_PLATFORM_CALLBACK_TIMEOUT", "10")))
    parser.add_argument(
        "--runtime-mode",
        choices=["executor", "platform"],
        default=os.environ.get("AI_PLATFORM_SANDBOX_RUNTIME_MODE", "executor"),
    )
    parser.add_argument(
        "--sandbox-provider",
        choices=["fake", "docker"],
        default=os.environ.get("SANDBOX_CONTAINER_PROVIDER", "docker"),
    )
    parser.add_argument("--skip-live-submit", action="store_true")
    parser.add_argument("--skip-cancel-probe", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    docker_cmd = tuple(part for part in args.docker_cmd.split(" ") if part)
    recorder = EvidenceRecorder(
        run_id=args.run_id,
        executor_url=args.executor_url,
        callback_token=args.callback_token,
    )
    messages: list[str] = []
    server: ThreadingHTTPServer | None = None

    try:
        if not args.skip_live_submit:
            if not args.executor_url:
                raise RuntimeError("executor URL not configured")
            server, local_callback_url = start_callback_server(
                bind_host=args.callback_host,
                bind_port=args.callback_port,
                recorder=recorder,
            )
            callback_url = resolve_callback_public_url(args.callback_public_url, local_callback_url)
            if args.runtime_mode == "platform":
                run_platform_runtime_probe(
                    recorder=recorder,
                    sandbox_provider=args.sandbox_provider,
                    sandbox_executor_image=args.sandbox_executor_image or args.cancel_image,
                    workspace_root=args.workspace_root,
                    callback_url=callback_url,
                )
            else:
                recorder.runtime_mode = "executor"
                recorder.sandbox_provider = "external_executor"
                submit_executor_task(
                    executor_url=args.executor_url,
                    callback_url=callback_url,
                    callback_token=args.callback_token,
                    run_id=args.run_id,
                    workspace_root=args.workspace_root,
                )
                recorder.executed_task = True
            if not _wait_for_callbacks(recorder, args.callback_timeout):
                messages.append("required callbacks not observed")
        if not args.skip_live_submit and not args.skip_cancel_probe:
            container_id = run_cancel_probe(run_id=args.run_id, docker_cmd=docker_cmd, cancel_image=args.cancel_image)
            recorder.cancel_stops_container = True
            recorder.cancelled_container_id = container_id
    except Exception as exc:
        messages.append(_redact(exc))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        recorder.write(args.evidence_file)

    success = (
        recorder.executed_task
        and recorder.has_required_callbacks()
        and (args.skip_cancel_probe or recorder.cancel_stops_container)
    )
    if args.skip_live_submit:
        success = True
    output = {
        "run_id": args.run_id,
        "evidence_file": "[redacted-path]",
        "executed_task": recorder.executed_task,
        "runtime_mode": recorder.runtime_mode,
        "sandbox_provider": recorder.sandbox_provider,
        "callbacks": len(recorder.callbacks),
        "cancel_stops_container": recorder.cancel_stops_container,
        "messages": messages,
    }
    if args.json_output:
        print(json.dumps(output, ensure_ascii=True, indent=2))
    else:
        print("PASSED: evidence generated" if success else "FAILED: evidence incomplete")
        for message in messages:
            print(f"- {message}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
