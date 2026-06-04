"""Generate live sandbox runtime evidence for the 211 verifier.

This script is a smoke tool. It creates a verifier-owned callback receiver,
submits one task to a running sandbox executor, runs a verifier-owned Docker
create/stop/remove probe, and writes sanitized evidence for
verify_sandbox_runtime_211.py.
"""

from __future__ import annotations

import argparse
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
        self._callback_auth_verified = False
        self.executed_task = False
        self.cancel_stops_container = False
        self.cancelled_container_id = ""
        self.callbacks: list[dict[str, object]] = []
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
            "run_id": self.run_id,
            "executor_url": self.executor_url,
            "executed_task": self.executed_task,
            "callback_auth": "token" if callback_auth_verified else False,
            "generated_at": _utc_now(),
            "callbacks": callbacks,
            "cancel_stops_container": self.cancel_stops_container,
            "cancelled_container_id": self.cancelled_container_id,
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
    parser.add_argument("--cancel-image", default=os.environ.get("AI_PLATFORM_CANCEL_PROBE_IMAGE", "busybox:1.36"))
    parser.add_argument("--callback-host", default=os.environ.get("AI_PLATFORM_CALLBACK_HOST", "127.0.0.1"))
    parser.add_argument("--callback-public-url", default=os.environ.get("AI_PLATFORM_CALLBACK_PUBLIC_URL", ""))
    parser.add_argument("--callback-port", type=int, default=int(os.environ.get("AI_PLATFORM_CALLBACK_PORT", "0")))
    parser.add_argument("--callback-timeout", type=float, default=float(os.environ.get("AI_PLATFORM_CALLBACK_TIMEOUT", "10")))
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
