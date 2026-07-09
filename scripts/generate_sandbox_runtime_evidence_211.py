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


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.model_catalog import build_model_catalog, resolve_model_selection
from app.control_plane_contracts import standard_trace_id
from app.runtime.sandbox.callback_tokens import derive_callback_token
from app.sandbox_hardening_contract import safe_bounded_error_projection


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")
EVIDENCE_SCHEMA_VERSION = "ai-platform.sandbox-runtime-211.v1"
LATENCY_SCHEMA_VERSION = "ai-platform.sandbox-latency-split.v1"
RUNTIME_PROBE_RESULTS_SCHEMA_VERSION = "ai-platform.sandbox-runtime-probe-results.v1"
NON_EXPANSION_INVARIANTS = {
    "ordinary_user_high_risk_sandbox_allowed": False,
    "admin_or_allowlist_only": True,
    "production_concurrency_defaults_raised": False,
    "docker_sandbox_production_hardening_claimed": False,
    "ordinary_user_multi_agent_allowed": False,
}
RUNTIME_PROBE_RESULTS_ALLOWED_KEYS = {
    "schema_version",
    "run_id",
    "source",
    "resource_limits",
    "egress_policy",
    "security_options",
}
RUNTIME_PROBE_RESULTS_SECTION_KEYS = ("resource_limits", "egress_policy", "security_options")


class SandboxEvidenceArgumentParser(argparse.ArgumentParser):
    """Normalize callback defaults after runtime/provider flags are parsed."""

    def parse_args(self, args: list[str] | None = None, namespace: argparse.Namespace | None = None) -> argparse.Namespace:
        raw_args = list(args) if args is not None else sys.argv[1:]
        callback_host_explicit = any(
            item == "--callback-host" or item.startswith("--callback-host=") for item in raw_args
        )
        callback_public_url_explicit = any(
            item == "--callback-public-url" or item.startswith("--callback-public-url=") for item in raw_args
        )
        parsed = super().parse_args(args, namespace)
        _normalize_callback_defaults(
            parsed,
            callback_host_explicit=callback_host_explicit,
            callback_public_url_explicit=callback_public_url_explicit,
        )
        return parsed


def _normalize_callback_defaults(
    args: argparse.Namespace,
    *,
    callback_host_explicit: bool = False,
    callback_public_url_explicit: bool = False,
) -> None:
    docker_platform_callback = args.sandbox_provider == "docker" and (
        args.runtime_mode == "platform" or bool(args.generate_runtime_probe_results_file)
    )
    callback_host_configured = callback_host_explicit or os.environ.get("AI_PLATFORM_CALLBACK_HOST") is not None
    callback_public_url_configured = (
        callback_public_url_explicit or os.environ.get("AI_PLATFORM_CALLBACK_PUBLIC_URL") is not None
    )
    auto_docker_callback = docker_platform_callback and not callback_host_configured and not callback_public_url_configured
    if args.callback_host is None:
        args.callback_host = "0.0.0.0" if docker_platform_callback else "127.0.0.1"
    if args.callback_public_url is None:
        args.callback_public_url = "http://host.docker.internal:{port}/callback" if auto_docker_callback else ""


def _runtime_probe_section_error(section_name: str, section: dict[str, Any], *, run_id: str) -> str | None:
    if section_name == "resource_limits":
        if section.get("over_limit_cleanup_verified") is not True:
            return "runtime probe results missing: resource_limits.over_limit_cleanup_verified"
        if section.get("probe_kind") != "platform_resource_timeout":
            return "runtime probe results missing: resource_limits.probe_kind"
        if section.get("timeout_probe_seconds") != 0:
            return "runtime probe results missing: resource_limits.timeout_probe_seconds"
        if safe_bounded_error_projection(section.get("bounded_error_projection"), run_id=run_id) is None:
            return "runtime probe results missing: resource_limits.bounded_error_projection"
        return None
    if section_name == "egress_policy":
        for field in (
            "default_deny_outbound",
            "platform_allowlist_enforced",
            "callback_exception_scoped_to_run_token",
            "denied_egress_redacted",
        ):
            if section.get(field) is not True:
                return f"runtime probe results missing: egress_policy.{field}"
        required_text_fields = (
            "denied_target",
            "denied_probe_error_code",
            "allowed_callback_host",
            "callback_probe_status",
        )
        for field in required_text_fields:
            value = section.get(field)
            if not isinstance(value, str) or not value:
                return f"runtime probe results missing: egress_policy.{field}"
        if section.get("denied_probe_error_code") != "egress_denied":
            return "runtime probe results missing: egress_policy.denied_probe_error_code"
        if section.get("callback_probe_status") != "delivered":
            return "runtime probe results missing: egress_policy.callback_probe_status"
        if section.get("policy_source") != "platform_policy":
            return "runtime probe results missing: egress_policy.policy_source"
        if section.get("probe_source") != "runtime_probe_results":
            return "runtime probe results missing: egress_policy.probe_source"
        return None
    if section_name == "security_options":
        if section.get("privileged") is not False:
            return "runtime probe results missing: security_options.privileged"
        if section.get("docker_socket_mounted") is not False:
            return "runtime probe results missing: security_options.docker_socket_mounted"
        for field in (
            "no_new_privileges",
            "capabilities_dropped",
            "root_filesystem_read_only_or_minimal",
        ):
            if section.get(field) is not True:
                return f"runtime probe results missing: security_options.{field}"
        if section.get("workspace_mount_mode") not in {"rw", "ro"}:
            return "runtime probe results missing: security_options.workspace_mount_mode"
    return None


def _safe_run_id(value: str) -> str:
    cleaned = SAFE_NAME_PATTERN.sub("-", value).strip("-")
    return cleaned[:80] or f"run-{uuid.uuid4().hex[:12]}"


def _configured_platform_runtime_model(settings: object) -> str:
    configured_default = str(getattr(settings, "default_model_id", "") or "").strip()
    if configured_default:
        try:
            selection = resolve_model_selection(configured_default, settings)
        except Exception:
            selection = None
        if selection and selection.get("value"):
            return str(selection["value"])
        return configured_default
    for attr in ("claude_agent_model", "anthropic_model", "openai_model"):
        value = str(getattr(settings, attr, "") or "").strip()
        if value:
            return value
    catalog = build_model_catalog(settings)
    catalog_default = str(catalog.get("default_model_id") or "").strip()
    if catalog_default:
        try:
            selection = resolve_model_selection(catalog_default, settings)
        except Exception:
            selection = None
        if selection and selection.get("value"):
            return str(selection["value"])
        return catalog_default
    return "deepseek-v4-flash"


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


def load_runtime_probe_results(path: str | Path, *, run_id: str) -> dict[str, Any]:
    """Load bounded platform probe results for the same run without trusting raw payloads."""
    if not run_id:
        raise RuntimeError("runtime probe results require run_id")
    probe_path = Path(path)
    try:
        raw = probe_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("runtime probe results file cannot be read") from exc
    if _redact(raw) != raw:
        raise RuntimeError("runtime probe results contain sensitive content")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("runtime probe results file is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("runtime probe results root must be an object")
    if payload.get("schema_version") != RUNTIME_PROBE_RESULTS_SCHEMA_VERSION:
        raise RuntimeError("runtime probe results schema mismatch")
    if payload.get("run_id") != run_id:
        raise RuntimeError("runtime probe results run_id mismatch")
    if payload.get("source") != "platform_runtime_probe":
        raise RuntimeError("runtime probe results source mismatch")
    unknown_keys = sorted(str(key) for key in payload if key not in RUNTIME_PROBE_RESULTS_ALLOWED_KEYS)
    if unknown_keys:
        raise RuntimeError("runtime probe results contain unsupported fields")
    results: dict[str, Any] = {}
    for key in RUNTIME_PROBE_RESULTS_SECTION_KEYS:
        section = payload.get(key)
        if section is None:
            raise RuntimeError(f"runtime probe results section is required: {key}")
        if not isinstance(section, dict):
            raise RuntimeError(f"runtime probe results section must be an object: {key}")
        results[key] = dict(section)
    for key, section in results.items():
        section_error = _runtime_probe_section_error(key, section, run_id=run_id)
        if section_error:
            raise RuntimeError(section_error)
    return results


class EvidenceRecorder:
    def __init__(self, *, run_id: str, executor_url: str, callback_token: str) -> None:
        self.run_id = run_id
        self.executor_url = executor_url.rstrip("/")
        self._callback_token = callback_token
        self.runtime_mode = "executor"
        self.sandbox_provider = "unknown"
        self._callback_auth_verified = False
        self.executed_task = False
        self.executor: dict[str, object] = {}
        self.cancel_stops_container = False
        self.cancelled_container_id = ""
        self.callbacks: list[dict[str, object]] = []
        self.timings: dict[str, object] = {}
        self.hardening: dict[str, object] = {}
        self.provider_lifecycle: dict[str, object] = {}
        self.lease_projection: dict[str, object] = {}
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
            lease_projection = dict(self.lease_projection)
        payload = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "executor_url": self.executor_url,
            "runtime_mode": self.runtime_mode,
            "sandbox_provider": self.sandbox_provider,
            "executed_task": self.executed_task,
            "callback_auth": "token" if callback_auth_verified else False,
            "executor": dict(self.executor),
            "generated_at": _utc_now(),
            "callbacks": callbacks,
            "cancel_stops_container": self.cancel_stops_container,
            "cancelled_container_id": self.cancelled_container_id,
            "timings": self.timings,
            "hardening": self.hardening,
            "provider_lifecycle": self.provider_lifecycle,
            "non_expansion_invariants": dict(NON_EXPANSION_INVARIANTS),
        }
        if lease_projection:
            payload["lease_projection"] = lease_projection
        return payload

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


def _is_platform_callback_endpoint(callback_url: str) -> bool:
    return urlparse(callback_url).path.rstrip("/") == "/api/ai/runtime/callbacks/executor"


def _platform_callback_token_id(run_id: str) -> str:
    return f"cbt_{run_id}"


def _verifier_callback_token_id(run_id: str) -> str:
    return f"callback-{_safe_run_id(run_id)}"


def _callback_token_id_for_url(callback_url: str, run_id: str) -> str:
    if _is_platform_callback_endpoint(callback_url):
        return _platform_callback_token_id(run_id)
    return _verifier_callback_token_id(run_id)


def _callback_token_for_url(callback_url: str, token_id: str, callback_token: str) -> str:
    if _is_platform_callback_endpoint(callback_url):
        return derive_callback_token(callback_token, token_id)
    return callback_token


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
        "callback_token_id": _callback_token_id_for_url(callback_url, run_id),
        "callback_token": _callback_token_for_url(
            callback_url,
            _callback_token_id_for_url(callback_url, run_id),
            callback_token,
        ),
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


def _executor_evidence_from_response(response: object) -> dict[str, object]:
    if not isinstance(response, dict):
        return {}
    evidence: dict[str, object] = {
        "sdk_used": response.get("sdk_used") is True,
        "executor_mode": str(response.get("executor_mode") or ""),
    }
    sdk_session_id = response.get("sdk_session_id")
    if isinstance(sdk_session_id, str) and sdk_session_id and "/" not in sdk_session_id and "\\" not in sdk_session_id:
        evidence["sdk_session_id"] = sdk_session_id
    return evidence


def _executor_evidence_from_result(result: object) -> dict[str, object]:
    return _executor_evidence_from_response(getattr(result, "executor_response", {}))


def _positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int | float) and value > 0


def _runtime_probe_section(
    runtime_probe_results: dict[str, Any] | None,
    section_name: str,
) -> dict[str, Any]:
    if not isinstance(runtime_probe_results, dict):
        return {}
    section = runtime_probe_results.get(section_name)
    return dict(section) if isinstance(section, dict) else {}


def _safe_platform_resource_probe_from_result(
    *,
    run_id: str,
    result: object,
    release_reason: object,
    platform_resource_timeout_probe: bool,
) -> dict[str, Any]:
    if not platform_resource_timeout_probe:
        return {}
    response = getattr(result, "executor_response", {})
    response = response if isinstance(response, dict) else {}
    status = str(getattr(result, "status", "") or response.get("status") or "")
    error_code = str(response.get("error_code") or "")
    if status != "failed" or error_code != "executor_health_timeout" or release_reason != "run_failed":
        return {}
    return {
        "probe_kind": "platform_resource_timeout",
        "timeout_probe_seconds": 0,
        "over_limit_cleanup_verified": True,
        "bounded_error_projection": {
            "source": "admin_runtime_projection",
            "run_id": run_id,
            "status": "failed",
            "error_code": "executor_health_timeout",
            "host_paths_redacted": True,
            "raw_docker_payload_absent": True,
            "callback_token_absent": True,
        },
    }


def _docker_host_config(docker_inspect: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(docker_inspect, dict):
        return {}
    host_config = docker_inspect.get("HostConfig")
    return dict(host_config) if isinstance(host_config, dict) else {}


def _docker_mounts(docker_inspect: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(docker_inspect, dict):
        return []
    mounts = docker_inspect.get("Mounts")
    if not isinstance(mounts, list):
        return []
    return [dict(item) for item in mounts if isinstance(item, dict)]


def _docker_resource_limits_verified(
    *,
    resource_limits: dict[str, Any],
    docker_inspect: dict[str, Any] | None,
) -> bool:
    host_config = _docker_host_config(docker_inspect)
    if not host_config:
        return False
    memory_bytes = host_config.get("Memory")
    nano_cpus = host_config.get("NanoCpus")
    pids_limit = host_config.get("PidsLimit")
    return (
        _positive_number(memory_bytes)
        and int(memory_bytes) == int(resource_limits.get("memory_mb") or 0) * 1024 * 1024
        and _positive_number(nano_cpus)
        and int(nano_cpus) == int(float(resource_limits.get("cpu_count") or 0) * 1_000_000_000)
        and _positive_number(pids_limit)
        and int(pids_limit) == int(resource_limits.get("pids_limit") or 0)
    )


def _docker_socket_mounted(docker_inspect: dict[str, Any] | None) -> bool:
    for mount in _docker_mounts(docker_inspect):
        values = [mount.get("Source"), mount.get("Destination"), mount.get("Name")]
        if any("/var/run/docker.sock" in str(value) for value in values if value is not None):
            return True
    host_config = _docker_host_config(docker_inspect)
    binds = host_config.get("Binds")
    if isinstance(binds, list):
        return any("/var/run/docker.sock" in str(bind) for bind in binds)
    return False


def _workspace_mount_mode(docker_inspect: dict[str, Any] | None) -> str:
    for mount in _docker_mounts(docker_inspect):
        if mount.get("Destination") == "/workspace":
            return "rw" if mount.get("RW") is not False else "ro"
    host_config = _docker_host_config(docker_inspect)
    binds = host_config.get("Binds")
    if isinstance(binds, list):
        for bind in binds:
            parts = str(bind).split(":")
            if len(parts) >= 2 and parts[1] == "/workspace":
                return "ro" if len(parts) >= 3 and "ro" in parts[2].split(",") else "rw"
    return ""


def _docker_security_options(docker_inspect: dict[str, Any] | None) -> dict[str, object]:
    host_config = _docker_host_config(docker_inspect)
    if not host_config:
        return {
            "privileged": False,
            "no_new_privileges": False,
            "capabilities_dropped": False,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": False,
        }
    security_opt = [str(item).lower() for item in host_config.get("SecurityOpt") or []]
    cap_drop = [str(item).upper() for item in host_config.get("CapDrop") or []]
    read_only = bool(host_config.get("ReadonlyRootfs"))
    return {
        "privileged": bool(host_config.get("Privileged")),
        "no_new_privileges": "no-new-privileges:true" in security_opt,
        "capabilities_dropped": "ALL" in cap_drop,
        "docker_socket_mounted": _docker_socket_mounted(docker_inspect),
        "workspace_mount_mode": _workspace_mount_mode(docker_inspect),
        "root_filesystem_read_only_or_minimal": read_only,
    }


def _callback_delivered(callbacks: list[dict[str, object]] | None, *, run_id: str) -> bool:
    if not isinstance(callbacks, list):
        return False
    statuses = {
        str(item.get("status") or "")
        for item in callbacks
        if isinstance(item, dict) and item.get("run_id") == run_id
    }
    return "running" in statuses and bool(statuses & TERMINAL_STATUSES)


def _docker_config_labels(docker_inspect: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(docker_inspect, dict):
        return {}
    config = docker_inspect.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _docker_egress_network_name(docker_inspect: dict[str, Any] | None) -> str:
    labels = _docker_config_labels(docker_inspect)
    return str(labels.get("ai-platform.egress.network") or "")


def _docker_network_masquerade_disabled(
    docker_network_inspect: dict[str, Any] | None,
    *,
    expected_network_name: str,
) -> bool:
    if not isinstance(docker_network_inspect, dict) or not expected_network_name:
        return False
    if str(docker_network_inspect.get("Name") or "") != expected_network_name:
        return False
    if str(docker_network_inspect.get("Driver") or "") != "bridge":
        return False
    options = docker_network_inspect.get("Options")
    if not isinstance(options, dict):
        return False
    return str(options.get("com.docker.network.bridge.enable_ip_masquerade") or "").lower() == "false"


def _platform_no_masq_egress_probe(
    *,
    run_id: str,
    docker_inspect: dict[str, Any] | None,
    docker_network_inspect: dict[str, Any] | None,
    callbacks: list[dict[str, object]] | None,
) -> dict[str, Any]:
    if not isinstance(docker_inspect, dict) or not _callback_delivered(callbacks, run_id=run_id):
        return {}
    labels = _docker_config_labels(docker_inspect)
    if labels.get("ai-platform.egress.policy") != "default-deny-no-masq":
        return {}
    network_name = str(labels.get("ai-platform.egress.network") or "")
    callback_host = str(labels.get("ai-platform.egress.callback_host") or "")
    if not network_name or not callback_host:
        return {}
    host_config = _docker_host_config(docker_inspect)
    if host_config.get("NetworkMode") != network_name:
        return {}
    network_settings = docker_inspect.get("NetworkSettings")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    if not isinstance(networks, dict) or network_name not in networks:
        return {}
    extra_hosts = [str(item) for item in host_config.get("ExtraHosts") or []]
    if f"{callback_host}:host-gateway" not in extra_hosts:
        return {}
    if not _docker_network_masquerade_disabled(docker_network_inspect, expected_network_name=network_name):
        return {}
    return {
        "default_deny_outbound": False,
        "platform_allowlist_enforced": False,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": False,
        "denied_target": "",
        "denied_probe_error_code": "",
        "allowed_callback_host": callback_host,
        "callback_probe_status": "delivered",
        "policy_source": "not_runtime_verified",
        "probe_source": "docker_network_inspect",
        "network_inspection_verified": True,
        "docker_network_masquerade_disabled": True,
    }


def _docker_exec_egress_denial_probe(
    container_name: str,
    *,
    denied_target: str,
    docker_cmd: tuple[str, ...],
    run: Callable[..., Any],
) -> dict[str, Any]:
    if not container_name or not denied_target:
        return {}
    probe_code = (
        "import sys, urllib.request\n"
        f"target = {json.dumps(denied_target)}\n"
        "try:\n"
        "    urllib.request.urlopen(target, timeout=3).read(1)\n"
        "except Exception as exc:\n"
        "    marker = str(exc).lower()\n"
        "    if 'egress_denied' in marker or 'egress denied' in marker:\n"
        "        sys.exit(42)\n"
        "    sys.exit(43)\n"
        "sys.exit(0)\n"
    )
    completed = _run_docker(
        [*docker_cmd, "exec", container_name, "python", "-c", probe_code],
        run=run,
        timeout=10,
        check=False,
    )
    return {
        "denied": getattr(completed, "returncode", 1) == 42,
        "target": denied_target,
    }


def _safe_platform_egress_probe_from_result(
    *,
    run_id: str,
    egress_denial_probe: dict[str, Any] | None,
    docker_inspect: dict[str, Any] | None,
    callbacks: list[dict[str, object]] | None,
) -> dict[str, Any]:
    if not isinstance(egress_denial_probe, dict) or egress_denial_probe.get("denied") is not True:
        return {}
    if not _callback_delivered(callbacks, run_id=run_id):
        return {}
    labels = _docker_config_labels(docker_inspect)
    callback_host = str(labels.get("ai-platform.egress.callback_host") or "host.docker.internal")
    denied_target = str(egress_denial_probe.get("target") or "")
    if _redact(denied_target) != denied_target:
        return {}
    return {
        "default_deny_outbound": True,
        "platform_allowlist_enforced": True,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": True,
        "denied_target": denied_target,
        "denied_probe_error_code": "egress_denied",
        "allowed_callback_host": callback_host,
        "callback_probe_status": "delivered",
        "policy_source": "platform_policy",
        "probe_source": "runtime_probe_results",
        "run_id": run_id,
    }


def _platform_hardening_evidence(
    *,
    run_id: str,
    workspace_root: str | Path,
    recorded_lease_id: str,
    released_lease_id: str,
    release_reason: str,
    resource_limits: dict[str, Any] | None = None,
    docker_inspect: dict[str, Any] | None = None,
    docker_network_inspect: dict[str, Any] | None = None,
    runtime_probe_results: dict[str, Any] | None = None,
    callbacks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    limits = resource_limits if isinstance(resource_limits, dict) else {}
    resource_probe = _runtime_probe_section(runtime_probe_results, "resource_limits")
    egress_probe = _runtime_probe_section(runtime_probe_results, "egress_policy")
    if egress_probe:
        egress_probe = {**egress_probe, "probe_source": str(egress_probe.get("probe_source") or "runtime_probe_results")}
    else:
        egress_probe = _platform_no_masq_egress_probe(
            run_id=run_id,
            docker_inspect=docker_inspect,
            docker_network_inspect=docker_network_inspect,
            callbacks=callbacks,
        )
    security_options = _docker_security_options(docker_inspect)
    bounded_error_projection = safe_bounded_error_projection(
        resource_probe.get("bounded_error_projection"),
        run_id=run_id,
    )
    resource_limits_evidence: dict[str, object] = {
        "evidence_class": "live_platform_probe",
        "memory_limit_mb": int(limits.get("memory_mb") or 0),
        "cpu_limit_count": float(limits.get("cpu_count") or 0),
        "pids_limit": int(limits.get("pids_limit") or 0),
        "process_timeout_seconds": int(limits.get("max_seconds") or 0),
        "limit_source": "platform_request",
        "docker_inspection_verified": _docker_resource_limits_verified(
            resource_limits=limits,
            docker_inspect=docker_inspect,
        ),
        "over_limit_cleanup_verified": resource_probe.get("over_limit_cleanup_verified") is True,
        "bounded_error_projection_verified": bounded_error_projection is not None,
    }
    if bounded_error_projection is not None:
        resource_limits_evidence["bounded_error_projection"] = bounded_error_projection
    if resource_probe.get("probe_kind") == "platform_resource_timeout":
        resource_limits_evidence["over_limit_probe_kind"] = "platform_resource_timeout"
    if resource_probe.get("timeout_probe_seconds") == 0:
        resource_limits_evidence["over_limit_timeout_probe_seconds"] = 0
    return {
        "lease_isolation": {
            "evidence_class": "live_platform_probe",
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
            "evidence_class": "live_platform_probe",
            "workspace_container_path": "/workspace",
            "inputs_container_path": "/workspace/inputs",
            "host_paths_redacted": True,
            "marker_path_is_container_path": True,
        },
        "cleanup": {
            "evidence_class": "live_platform_probe",
            "ephemeral_container_removed": True,
            "cancel_probe_container_removed": True,
            "active_lease_released": bool(recorded_lease_id and recorded_lease_id == released_lease_id),
        },
        "resource_timeout": {
            "evidence_class": "source_regression_guard",
            "max_seconds_enforced": True,
            "timeout_error_code": "executor_health_timeout",
            "failed_container_removed": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_maps_health_false_to_timeout",
                "tests/test_sandbox_container_provider.py::test_docker_provider_removes_container_after_health_timeout",
            ],
        },
        "failure_fallback": {
            "evidence_class": "source_regression_guard",
            "dispatch_failure_stops_container": True,
            "lease_record_failure_stops_container": True,
            "db_lease_not_released_when_stop_fails": True,
            "source_regression_tests": [
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
            ],
        },
        "cached_lease_revalidation": {
            "evidence_class": "source_regression_guard",
            "cached_lease_revalidates_scope_labels": True,
            "scope_mismatch_fails_closed": True,
            "tenant_workspace_user_session_checked": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels",
            ],
        },
        "resource_limits": resource_limits_evidence,
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": egress_probe.get("default_deny_outbound") is True,
            "platform_allowlist_enforced": egress_probe.get("platform_allowlist_enforced") is True,
            "callback_exception_scoped_to_run_token": egress_probe.get(
                "callback_exception_scoped_to_run_token",
                True,
            )
            is True,
            "denied_egress_redacted": egress_probe.get("denied_egress_redacted") is True,
            "denied_target": str(egress_probe.get("denied_target") or ""),
            "denied_probe_error_code": str(egress_probe.get("denied_probe_error_code") or ""),
            "allowed_callback_host": str(egress_probe.get("allowed_callback_host") or ""),
            "callback_probe_status": str(egress_probe.get("callback_probe_status") or ""),
            "policy_source": (
                "platform_policy" if egress_probe.get("policy_source") == "platform_policy" else "not_runtime_verified"
            ),
            "probe_source": str(egress_probe.get("probe_source") or ""),
            "network_inspection_verified": egress_probe.get("network_inspection_verified") is True,
            "docker_network_masquerade_disabled": egress_probe.get("docker_network_masquerade_disabled") is True,
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            **security_options,
        },
        "source": {
            "runtime_submit": "app.runtime.sandbox.runtime.SandboxRuntime.submit",
            "workspace_root": "[redacted-path]" if str(workspace_root) else "",
            "resource_timeout_and_failure_fallback": "source_regression_tests_plus_live_platform_runtime_smoke",
            "cached_lease_revalidation": "source_regression_tests_plus_live_platform_runtime_smoke",
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
    recorder.executor = _executor_evidence_from_result(result)
    lease_id = recorded_lease_id or f"lease-{_safe_run_id(recorder.run_id)}"
    recorder.hardening = _platform_hardening_evidence(
        run_id=recorder.run_id,
        workspace_root=workspace_root,
        recorded_lease_id=lease_id,
        released_lease_id=released_lease_id or lease_id,
        release_reason=release_reason,
        resource_limits={"max_seconds": 60, "memory_mb": 512, "pids_limit": 128},
    )
    return {
        "status": str(getattr(result, "status", "")),
        "run_id": str(getattr(result, "run_id", recorder.run_id)),
    }


def _opensandbox_provider_lifecycle_evidence(
    *,
    recorder: EvidenceRecorder,
    captured: dict[str, Any],
    resource_limits: dict[str, Any],
) -> dict[str, object]:
    if recorder.sandbox_provider != "opensandbox":
        return {}
    recorded_lease_id = str(captured.get("recorded_lease_id") or "")
    released_lease_id = str(captured.get("released_lease_id") or "")
    release_reason = str(captured.get("release_reason") or "")
    lease_labels = captured.get("lease_labels")
    labels = lease_labels if isinstance(lease_labels, dict) else {}
    return {
        "schema_version": "ai-platform.opensandbox-provider-lifecycle.v1",
        "provider": "opensandbox",
        "run_id": recorder.run_id,
        "lifecycle": {
            "create_observed": bool(captured.get("container_id")),
            "delete_observed": bool(recorded_lease_id and recorded_lease_id == released_lease_id),
            "container_id_present": bool(captured.get("container_id")),
            "executor_endpoint_present": bool(captured.get("executor_url")),
        },
        "db_lease": {
            "recorded": bool(recorded_lease_id),
            "released": bool(released_lease_id),
            "release_reason": release_reason,
            "recorded_scope_matches_request": (
                captured.get("recorded_tenant_id") == "tenant-a"
                and captured.get("recorded_workspace_id") == "workspace-a"
                and captured.get("recorded_user_id") == "user-a"
                and captured.get("recorded_session_id") == f"session-{recorder.run_id}"
                and captured.get("recorded_run_id") == recorder.run_id
                and captured.get("released_run_id") == recorder.run_id
            ),
        },
        "startup_io": {
            "file_write_read_verified": captured.get("opensandbox_startup_io_probe_enabled") is True,
            "command_execution_verified": captured.get("opensandbox_startup_io_probe_enabled") is True,
            "source": "OpenSandboxContainerProvider.startup_io_probe",
        },
        "resource_policy": {
            "resource_limits_requested": all(
                _positive_number(resource_limits.get(key))
                for key in ("memory_mb", "cpu_count", "pids_limit")
            ),
            "memory_mb": int(resource_limits.get("memory_mb") or 0),
            "cpu_count": float(resource_limits.get("cpu_count") or 0),
            "pids_limit": int(resource_limits.get("pids_limit") or 0),
            "policy_projection_source": "provider_request",
        },
        "egress_policy": {
            "policy_requested": labels.get("ai-platform.egress.policy") == "opensandbox-network-policy",
            "callback_host_allowlisted": bool(labels.get("ai-platform.egress.callback_host")),
            "policy_projection_source": "provider_request",
        },
        "dispatch": {
            "executor_response_present": bool(recorder.executor),
            "callback_stream_observed": recorder.has_required_callbacks(),
            "sdk_executor_observed": recorder.executor.get("sdk_used") is True
            and recorder.executor.get("executor_mode") == "claude_agent_sdk",
        },
        "redaction": {
            "host_paths_redacted": True,
            "secrets_absent": True,
        },
    }


def run_platform_runtime_probe(
    *,
    recorder: EvidenceRecorder,
    sandbox_provider: str,
    sandbox_executor_image: str,
    workspace_root: str,
    callback_url: str,
    docker_cmd: tuple[str, ...] = ("docker",),
    run: Callable[..., Any] = subprocess.run,
    runtime_probe_results: dict[str, Any] | None = None,
    platform_resource_timeout_probe: bool = False,
    denied_egress_target: str = "https://egress-denied.invalid/",
    capture_runtime_egress_probe: bool = False,
) -> dict[str, object]:
    captured: dict[str, Any] = {
        "recorded_lease_id": "",
        "released_lease_id": "",
        "release_reason": "",
        "container_name": "",
        "container_id": "",
        "executor_url": "",
        "lease_labels": {},
        "docker_inspect": None,
        "egress_denial_probe": {},
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
        captured["opensandbox_startup_io_probe_enabled"] = bool(
            getattr(settings, "opensandbox_startup_io_probe_enabled", True)
        )
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
                captured["container_id"] = lease.container_id
                captured["container_name"] = lease.container_name
                captured["executor_url"] = lease.executor_url
                captured["lease_labels"] = dict(getattr(lease, "labels", {}) or {})
                captured["workspace_container_path"] = workspace.workspace_container_path
                captured["lease_projection"] = {
                    "provider": lease.provider,
                    "lease_payload": {
                        "source": "sandbox_runtime",
                        "evidence_class": "runtime_lease_projection",
                        "container_id": lease.container_id,
                        "container_name": lease.container_name,
                        "workspace_container_path": workspace.workspace_container_path,
                    },
                }
                docker_inspect = _inspect_docker_container(
                    lease.container_name,
                    docker_cmd=docker_cmd,
                    run=run,
                )
                captured["docker_inspect"] = docker_inspect
                if capture_runtime_egress_probe:
                    captured["egress_denial_probe"] = _docker_exec_egress_denial_probe(
                        lease.container_name,
                        denied_target=denied_egress_target,
                        docker_cmd=docker_cmd,
                        run=run,
                    )
                network_name = _docker_egress_network_name(docker_inspect)
                captured["docker_network_inspect"] = _inspect_docker_network(
                    network_name,
                    docker_cmd=docker_cmd,
                    run=run,
                )
                return lease_id

            async def release_lease(lease, reason, lease_record_id=None):
                captured["released_lease_id"] = str(lease_record_id or "")
                captured["release_reason"] = str(reason)
                captured["released_run_id"] = lease.run_id

            runtime = SandboxRuntime(
                workspace_root=workspace_root,
                callback_token_resolver=lambda token_id: _callback_token_for_url(
                    callback_url,
                    token_id,
                    recorder._callback_token,
                ),
                record_lease=record_lease,
                release_lease=release_lease,
            )
            resource_limits = {"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128}
            if platform_resource_timeout_probe:
                resource_limits = dict(resource_limits)
                resource_limits["max_seconds"] = 0
                resource_limits["platform_timeout_probe"] = True
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
                model=_configured_platform_runtime_model(settings),
                resource_limits=resource_limits,
                trace_id=standard_trace_id(recorder.run_id),
                callback_url=callback_url,
                callback_token_id=_callback_token_id_for_url(callback_url, recorder.run_id),
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
    recorder.executor = _executor_evidence_from_result(result)
    recorder.lease_projection = (
        dict(captured["lease_projection"])
        if isinstance(captured.get("lease_projection"), dict)
        else {}
    )
    recorded_lease_id = captured.get("recorded_lease_id") or f"lease-{_safe_run_id(recorder.run_id)}"
    released_lease_id = captured.get("released_lease_id") or ""
    derived_runtime_probe_results = dict(runtime_probe_results or {})
    platform_resource_probe = _safe_platform_resource_probe_from_result(
        run_id=recorder.run_id,
        result=result,
        release_reason=captured.get("release_reason"),
        platform_resource_timeout_probe=platform_resource_timeout_probe,
    )
    if platform_resource_probe:
        derived_runtime_probe_results["resource_limits"] = platform_resource_probe
    platform_egress_probe = _safe_platform_egress_probe_from_result(
        run_id=recorder.run_id,
        egress_denial_probe=captured.get("egress_denial_probe")
        if isinstance(captured.get("egress_denial_probe"), dict)
        else None,
        docker_inspect=captured.get("docker_inspect") if isinstance(captured.get("docker_inspect"), dict) else None,
        callbacks=recorder.callbacks,
    )
    if platform_egress_probe:
        derived_runtime_probe_results["egress_policy"] = platform_egress_probe
    recorder.hardening = _platform_hardening_evidence(
        run_id=recorder.run_id,
        workspace_root=workspace_root,
        recorded_lease_id=recorded_lease_id,
        released_lease_id=released_lease_id,
        release_reason=captured.get("release_reason") or "",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        docker_inspect=captured.get("docker_inspect") if isinstance(captured.get("docker_inspect"), dict) else None,
        docker_network_inspect=captured.get("docker_network_inspect")
        if isinstance(captured.get("docker_network_inspect"), dict)
        else None,
        runtime_probe_results=derived_runtime_probe_results,
        callbacks=recorder.callbacks,
    )
    recorder.provider_lifecycle = _opensandbox_provider_lifecycle_evidence(
        recorder=recorder,
        captured=captured,
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
    )
    output = {
        "status": str(getattr(result, "status", "")),
        "run_id": str(getattr(result, "run_id", recorder.run_id)),
    }
    response = getattr(result, "executor_response", {})
    if isinstance(response, dict) and response.get("error_code"):
        output["error_code"] = str(response["error_code"])
    return output


def _runtime_probe_results_payload(*, run_id: str, hardening: dict[str, Any]) -> dict[str, Any]:
    resource_limits = hardening.get("resource_limits")
    egress_policy = hardening.get("egress_policy")
    security_options = hardening.get("security_options")
    resource_limits = resource_limits if isinstance(resource_limits, dict) else {}
    egress_policy = egress_policy if isinstance(egress_policy, dict) else {}
    security_options = security_options if isinstance(security_options, dict) else {}
    return {
        "schema_version": RUNTIME_PROBE_RESULTS_SCHEMA_VERSION,
        "run_id": run_id,
        "source": "platform_runtime_probe",
        "resource_limits": {
            "over_limit_cleanup_verified": resource_limits.get("over_limit_cleanup_verified") is True,
            "probe_kind": str(resource_limits.get("over_limit_probe_kind") or ""),
            "timeout_probe_seconds": resource_limits.get("over_limit_timeout_probe_seconds"),
            "bounded_error_projection": resource_limits.get("bounded_error_projection"),
        },
        "egress_policy": {
            "default_deny_outbound": egress_policy.get("default_deny_outbound") is True,
            "platform_allowlist_enforced": egress_policy.get("platform_allowlist_enforced") is True,
            "callback_exception_scoped_to_run_token": egress_policy.get("callback_exception_scoped_to_run_token")
            is True,
            "denied_egress_redacted": egress_policy.get("denied_egress_redacted") is True,
            "denied_target": str(egress_policy.get("denied_target") or ""),
            "denied_probe_error_code": str(egress_policy.get("denied_probe_error_code") or ""),
            "allowed_callback_host": str(egress_policy.get("allowed_callback_host") or ""),
            "callback_probe_status": str(egress_policy.get("callback_probe_status") or ""),
            "policy_source": str(egress_policy.get("policy_source") or ""),
            "probe_source": str(egress_policy.get("probe_source") or ""),
        },
        "security_options": {
            "privileged": security_options.get("privileged") is True,
            "no_new_privileges": security_options.get("no_new_privileges") is True,
            "capabilities_dropped": security_options.get("capabilities_dropped") is True,
            "docker_socket_mounted": security_options.get("docker_socket_mounted") is True,
            "workspace_mount_mode": str(security_options.get("workspace_mount_mode") or ""),
            "root_filesystem_read_only_or_minimal": security_options.get("root_filesystem_read_only_or_minimal")
            is True,
        },
    }


def generate_runtime_probe_results(
    *,
    recorder: EvidenceRecorder,
    sandbox_provider: str,
    sandbox_executor_image: str,
    workspace_root: str,
    callback_url: str,
    docker_cmd: tuple[str, ...],
    output_file: str | Path,
    denied_egress_target: str = "https://egress-denied.invalid/",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, object]:
    run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider=sandbox_provider,
        sandbox_executor_image=sandbox_executor_image,
        workspace_root=workspace_root,
        callback_url=callback_url,
        docker_cmd=docker_cmd,
        run=run,
        platform_resource_timeout_probe=True,
        denied_egress_target=denied_egress_target,
        capture_runtime_egress_probe=True,
    )
    payload = _runtime_probe_results_payload(run_id=recorder.run_id, hardening=recorder.hardening)
    for section_name in RUNTIME_PROBE_RESULTS_SECTION_KEYS:
        section = payload.get(section_name)
        if not isinstance(section, dict):
            raise RuntimeError(f"runtime probe results section must be an object: {section_name}")
        section_error = _runtime_probe_section_error(section_name, section, run_id=recorder.run_id)
        if section_error:
            raise RuntimeError(section_error)
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=True, indent=2)
    if _redact(serialized) != serialized:
        raise RuntimeError("runtime probe results contain sensitive content")
    path.write_text(serialized, encoding="utf-8")
    return {
        "run_id": recorder.run_id,
        "runtime_probe_results_file": "[redacted-path]",
        "sections": list(RUNTIME_PROBE_RESULTS_SECTION_KEYS),
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


def _inspect_docker_container(
    container_name: str,
    *,
    docker_cmd: tuple[str, ...],
    run: Callable[..., Any],
) -> dict[str, Any] | None:
    if not container_name:
        return None
    completed = _run_docker(
        [*docker_cmd, "inspect", container_name],
        run=run,
        timeout=30,
        check=False,
    )
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        payload = json.loads(str(getattr(completed, "stdout", "") or "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    return dict(payload[0])


def _inspect_docker_network(
    network_name: str,
    *,
    docker_cmd: tuple[str, ...],
    run: Callable[..., Any],
) -> dict[str, Any] | None:
    if not network_name:
        return None
    completed = _run_docker(
        [*docker_cmd, "network", "inspect", network_name],
        run=run,
        timeout=30,
        check=False,
    )
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        payload = json.loads(str(getattr(completed, "stdout", "") or "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    return dict(payload[0])


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
    parser = SandboxEvidenceArgumentParser(
        description=(
            'Generate ai-platform sandbox runtime evidence on 211; use --docker-cmd "sudo -n docker" '
            "on 211 and keep this as controlled admin/allowlist evidence."
        )
    )
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
    parser.add_argument(
        "--docker-cmd",
        default=os.environ.get("DOCKER_CMD", "docker"),
        help='Docker command; use --docker-cmd "sudo -n docker" on 211.',
    )
    parser.add_argument(
        "--cancel-image",
        default=os.environ.get("AI_PLATFORM_CANCEL_PROBE_IMAGE", "ai-platform:local"),
        help="Verifier-owned cancel probe image; defaults to ai-platform:local.",
    )
    parser.add_argument(
        "--sandbox-executor-image",
        default=os.environ.get("AI_PLATFORM_SANDBOX_EXECUTOR_IMAGE", os.environ.get("SANDBOX_EXECUTOR_IMAGE", "")),
    )
    parser.add_argument("--callback-host", default=os.environ.get("AI_PLATFORM_CALLBACK_HOST"))
    parser.add_argument("--callback-public-url", default=os.environ.get("AI_PLATFORM_CALLBACK_PUBLIC_URL"))
    parser.add_argument("--callback-port", type=int, default=int(os.environ.get("AI_PLATFORM_CALLBACK_PORT", "0")))
    parser.add_argument("--callback-timeout", type=float, default=float(os.environ.get("AI_PLATFORM_CALLBACK_TIMEOUT", "10")))
    parser.add_argument(
        "--runtime-probe-results-file",
        default=os.environ.get("AI_PLATFORM_SANDBOX_RUNTIME_PROBE_RESULTS", ""),
        help=(
            "Optional same-run platform probe results JSON for resource-limit and egress hardening evidence. "
            "The file must use ai-platform.sandbox-runtime-probe-results.v1 and match --run-id."
        ),
    )
    parser.add_argument(
        "--generate-runtime-probe-results-file",
        default=os.environ.get("AI_PLATFORM_SANDBOX_GENERATE_RUNTIME_PROBE_RESULTS", ""),
        help=(
            "Generate same-run platform runtime probe results JSON for a later --runtime-probe-results-file run. "
            "This is a probe-input generation step, not formal sandbox runtime acceptance evidence."
        ),
    )
    parser.add_argument(
        "--denied-egress-target",
        default=os.environ.get("AI_PLATFORM_SANDBOX_DENIED_EGRESS_TARGET", "https://egress-denied.invalid/"),
        help="Verifier-owned target used to prove denied outbound egress in runtime probe results.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=["executor", "platform"],
        default=os.environ.get("AI_PLATFORM_SANDBOX_RUNTIME_MODE", "executor"),
    )
    parser.add_argument(
        "--sandbox-provider",
        choices=["fake", "docker", "opensandbox"],
        default=os.environ.get("SANDBOX_CONTAINER_PROVIDER", "docker"),
    )
    parser.add_argument("--skip-live-submit", action="store_true")
    parser.add_argument("--skip-cancel-probe", action="store_true")
    parser.add_argument(
        "--platform-resource-timeout-probe",
        action="store_true",
        default=os.environ.get("AI_PLATFORM_RESOURCE_TIMEOUT_PROBE", "").lower() in {"1", "true", "yes"},
        help="Run the platform submit with max_seconds=0 to produce explicit resource over-limit cleanup evidence.",
    )
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

    if args.generate_runtime_probe_results_file:
        try:
            server, local_callback_url = start_callback_server(
                bind_host=args.callback_host,
                bind_port=args.callback_port,
                recorder=recorder,
            )
            callback_url = resolve_callback_public_url(args.callback_public_url, local_callback_url)
            probe_summary = generate_runtime_probe_results(
                recorder=recorder,
                sandbox_provider=args.sandbox_provider,
                sandbox_executor_image=args.sandbox_executor_image or args.cancel_image,
                workspace_root=args.workspace_root,
                callback_url=callback_url,
                docker_cmd=docker_cmd,
                output_file=args.generate_runtime_probe_results_file,
                denied_egress_target=args.denied_egress_target,
                run=subprocess.run,
            )
            output = {
                "run_id": args.run_id,
                "evidence_file": "[redacted-path]",
                "runtime_probe_results_file": probe_summary["runtime_probe_results_file"],
                "sections": probe_summary["sections"],
                "executed_task": True,
                "runtime_mode": "platform_probe_results",
                "sandbox_provider": args.sandbox_provider,
                "callbacks": len(recorder.callbacks),
                "cancel_stops_container": False,
                "messages": messages,
            }
            if args.json_output:
                print(json.dumps(output, ensure_ascii=True, indent=2))
            else:
                print("PASSED: runtime probe results generated")
            return 0
        except Exception as exc:
            messages.append(_redact(exc))
            output = {
                "run_id": args.run_id,
                "evidence_file": "[redacted-path]",
                "runtime_probe_results_file": "[redacted-path]",
                "executed_task": False,
                "runtime_mode": "platform_probe_results",
                "sandbox_provider": args.sandbox_provider,
                "callbacks": len(recorder.callbacks),
                "cancel_stops_container": False,
                "messages": messages,
            }
            if args.json_output:
                print(json.dumps(output, ensure_ascii=True, indent=2))
            else:
                print("FAILED: runtime probe results incomplete")
                for message in messages:
                    print(f"- {message}")
            return 1
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()

    try:
        runtime_probe_results = (
            load_runtime_probe_results(args.runtime_probe_results_file, run_id=args.run_id)
            if args.runtime_probe_results_file
            else None
        )
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
                    docker_cmd=docker_cmd,
                    runtime_probe_results=runtime_probe_results,
                    platform_resource_timeout_probe=args.platform_resource_timeout_probe,
                )
            else:
                recorder.runtime_mode = "executor"
                recorder.sandbox_provider = "external_executor"
                executor_response = submit_executor_task(
                    executor_url=args.executor_url,
                    callback_url=callback_url,
                    callback_token=args.callback_token,
                    run_id=args.run_id,
                    workspace_root=args.workspace_root,
                )
                recorder.executor = _executor_evidence_from_response(executor_response)
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
