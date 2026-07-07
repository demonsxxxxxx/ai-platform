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
from urllib.parse import parse_qsl, urlsplit
from urllib import request as urllib_request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.sandbox_hardening_contract import bounded_error_projection_error


SENSITIVE_PATTERNS = [
    re.compile(r"/var/run/docker\.sock", re.IGNORECASE),
    re.compile(r"%2Fvar%2Frun%2Fdocker\.sock", re.IGNORECASE),
    re.compile(r"/runtime/tenants[^\s\"']*", re.IGNORECASE),
    re.compile(r"/home/[^\s\"']*", re.IGNORECASE),
    re.compile(r"/tmp/[^\s\"']*", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\[^\s\"']*"),
    re.compile(r'"[^"]*token[^"]*"\s*:\s*"[^"]*"', re.IGNORECASE),
    re.compile(r'"[^"]*secret[^"]*"\s*:\s*"[^"]*"', re.IGNORECASE),
    re.compile(r'"[^"]*authorization[^"]*"\s*:\s*"[^"]*"', re.IGNORECASE),
    re.compile(r"\btoken\b\s*[:=]\s*[^,\s\"'}]+", re.IGNORECASE),
    re.compile(r"\bcallback[_-]?token\b\s*[:=]\s*[^,\s\"'}]+", re.IGNORECASE),
    re.compile(r"\bsecret\b\s*[:=]\s*[^,\s\"'}]+", re.IGNORECASE),
    re.compile(r"\bauthorization\b\s*[:=]\s*[^,\s\"'}]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY", re.IGNORECASE),
    re.compile(r"RAGFLOW_API_KEY", re.IGNORECASE),
    re.compile(r"ANTHROPIC_AUTH_TOKEN", re.IGNORECASE),
    re.compile(r"access[_-]?key", re.IGNORECASE),
    re.compile(r"storage[_-]?key", re.IGNORECASE),
]
SECRET_FIELD_NAMES = {
    "api_key",
    "authorization",
    "authorization_header",
    "auth_token",
    "bearer_token",
    "callback_token",
    "client_secret",
    "gateway_secret",
    "secret",
    "secret_key",
    "secret_value",
    "token",
}
SECRET_FIELD_SUFFIXES = ("_api_key", "_authorization", "_authorization_header", "_secret", "_secret_key", "_token")
SAFE_SECRET_FIELD_SUFFIXES = ("_absent", "_redacted")
SAFE_SECRET_FIELD_NAMES = {
    "callback_auth",
    "callback_exception_scoped_to_run_token",
    "input_token_count",
    "input_tokens",
    "output_token_count",
    "output_tokens",
    "remaining_token_budget",
    "token_count",
    "token_counts",
    "tokenizer",
    "total_token_count",
    "total_tokens",
}

EVIDENCE_SCHEMA_VERSION = "ai-platform.sandbox-runtime-211.v1"
LATENCY_SCHEMA_VERSION = "ai-platform.sandbox-latency-split.v1"
REAL_SANDBOX_PROVIDERS = {"docker", "opensandbox"}
REQUIRED_TIMING_FIELDS = [
    "sandbox_queue_wait_latency_ms",
    "sandbox_lease_acquire_latency_ms",
    "sandbox_container_start_latency_ms",
    "sandbox_container_cold_start_latency_ms",
    "sandbox_healthcheck_latency_ms",
    "sandbox_executor_dispatch_latency_ms",
    "executor_first_token_latency_ms",
    "executor_tool_call_latency_ms",
    "executor_model_latency_ms",
    "document_processing_latency_ms",
    "artifact_upload_latency_ms",
    "sandbox_cleanup_latency_ms",
    "sandbox_total_latency_ms",
]
REQUIRED_NON_EXPANSION_INVARIANTS = {
    "ordinary_user_high_risk_sandbox_allowed": False,
    "admin_or_allowlist_only": True,
    "production_concurrency_defaults_raised": False,
    "docker_sandbox_production_hardening_claimed": False,
    "ordinary_user_multi_agent_allowed": False,
}
REQUIRED_HARDENING_FLAGS = {
    "lease_isolation": [
        "evidence_class",
        "recorded_lease_id",
        "released_lease_id",
        "host_paths_redacted",
    ],
    "workspace_isolation": [
        "evidence_class",
        "workspace_container_path",
        "inputs_container_path",
        "host_paths_redacted",
        "marker_path_is_container_path",
    ],
    "cleanup": [
        "evidence_class",
        "ephemeral_container_removed",
        "cancel_probe_container_removed",
        "active_lease_released",
    ],
    "resource_timeout": [
        "evidence_class",
        "max_seconds_enforced",
        "timeout_error_code",
        "failed_container_removed",
        "source_regression_tests",
    ],
    "failure_fallback": [
        "evidence_class",
        "dispatch_failure_stops_container",
        "lease_record_failure_stops_container",
        "db_lease_not_released_when_stop_fails",
        "source_regression_tests",
    ],
    "cached_lease_revalidation": [
        "evidence_class",
        "cached_lease_revalidates_scope_labels",
        "scope_mismatch_fails_closed",
        "tenant_workspace_user_session_checked",
        "source_regression_tests",
    ],
    "resource_limits": [
        "evidence_class",
        "memory_limit_mb",
        "cpu_limit_count",
        "pids_limit",
        "process_timeout_seconds",
        "over_limit_probe_kind",
        "over_limit_timeout_probe_seconds",
        "limit_source",
        "docker_inspection_verified",
        "over_limit_cleanup_verified",
        "bounded_error_projection_verified",
    ],
    "egress_policy": [
        "evidence_class",
        "default_deny_outbound",
        "platform_allowlist_enforced",
        "callback_exception_scoped_to_run_token",
        "denied_egress_redacted",
        "denied_target",
        "denied_probe_error_code",
        "allowed_callback_host",
        "callback_probe_status",
        "policy_source",
        "probe_source",
    ],
    "security_options": [
        "evidence_class",
        "privileged",
        "no_new_privileges",
        "capabilities_dropped",
        "docker_socket_mounted",
        "workspace_mount_mode",
        "root_filesystem_read_only_or_minimal",
    ],
}
HARDENING_EVIDENCE_CLASS = {
    "lease_isolation": "live_platform_probe",
    "workspace_isolation": "live_platform_probe",
    "cleanup": "live_platform_probe",
    "resource_timeout": "source_regression_guard",
    "failure_fallback": "source_regression_guard",
    "cached_lease_revalidation": "source_regression_guard",
    "resource_limits": "live_platform_probe",
    "egress_policy": "live_platform_probe",
    "security_options": "live_platform_probe",
}
ALLOWED_SOURCE_REGRESSION_TESTS = {
    "resource_timeout": {
        "tests/test_sandbox_container_provider.py::test_docker_provider_maps_health_false_to_timeout",
        "tests/test_sandbox_container_provider.py::test_docker_provider_removes_container_after_health_timeout",
    },
    "failure_fallback": {
        "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
        "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
        "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
    },
    "cached_lease_revalidation": {
        "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels",
    },
}


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


def _platform_executor_health_evidence_error(evidence: dict[str, Any], *, run_id: str = "") -> str | None:
    run_error = _required_evidence_error(evidence, run_id=run_id)
    if run_error:
        return run_error
    if evidence.get("runtime_mode") != "platform":
        return "platform runtime evidence missing"
    if evidence.get("sandbox_provider") not in REAL_SANDBOX_PROVIDERS:
        return "real sandbox provider evidence missing"
    if evidence.get("executed_task") is not True:
        return "executed task evidence missing"
    timings = evidence.get("timings")
    if not isinstance(timings, dict):
        return "latency split timings missing"
    if timings.get("schema_version") != LATENCY_SCHEMA_VERSION:
        return "latency split schema mismatch"
    for field in (
        "sandbox_container_cold_start_latency_ms",
        "sandbox_healthcheck_latency_ms",
        "sandbox_executor_dispatch_latency_ms",
    ):
        value = timings.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return f"platform executor health evidence missing: {field}"
    return None


def check_executor_health_or_platform_evidence(
    executor_url: str,
    evidence_path: str | Path,
    *,
    run_id: str = "",
    timeout_seconds: float = 5.0,
    urlopen: Callable[..., Any] = urllib_request.urlopen,
) -> CheckResult:
    direct_health = check_executor_health(executor_url, timeout_seconds=timeout_seconds, urlopen=urlopen)
    if direct_health.passed:
        return direct_health
    evidence, error = _read_evidence(evidence_path)
    if error:
        return direct_health
    evidence_error = _platform_executor_health_evidence_error(evidence, run_id=run_id)
    if evidence_error:
        return direct_health
    return CheckResult(
        "check_executor_health",
        True,
        "platform runtime evidence includes executor cold-start and healthcheck",
    )


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


def _required_evidence_error(evidence: dict[str, Any], *, run_id: str = "") -> str | None:
    if not run_id:
        return "run_id argument required"
    if evidence.get("run_id") != run_id:
        return "run_id evidence mismatch"
    return None


def _timing_error(timings: object) -> str | None:
    if not isinstance(timings, dict):
        return "latency split timings missing"
    if timings.get("schema_version") != LATENCY_SCHEMA_VERSION:
        return "latency split schema mismatch"
    missing = [field for field in REQUIRED_TIMING_FIELDS if field not in timings]
    if missing:
        return f"latency split timing missing: {', '.join(missing)}"
    for field in REQUIRED_TIMING_FIELDS:
        value = timings.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"latency split timing must be non-negative integer: {field}"
    cold_start = int(timings["sandbox_container_cold_start_latency_ms"])
    executor_model = int(timings["executor_model_latency_ms"])
    if cold_start == executor_model:
        return "cold start latency must not be hidden in executor model latency"
    total = int(timings["sandbox_total_latency_ms"])
    subtotal = sum(int(timings[field]) for field in REQUIRED_TIMING_FIELDS if field != "sandbox_total_latency_ms")
    if total < max(cold_start, executor_model) or total == 0 and subtotal > 0:
        return "sandbox total latency is inconsistent with split timings"
    return None


def _executor_sdk_error(evidence: dict[str, Any]) -> str | None:
    executor = evidence.get("executor")
    if not isinstance(executor, dict):
        return "Claude Agent SDK executor evidence missing"
    if executor.get("sdk_used") is not True:
        return "Claude Agent SDK executor evidence missing: sdk_used"
    if executor.get("executor_mode") != "claude_agent_sdk":
        return "Claude Agent SDK executor evidence missing: executor_mode"
    return None


def _non_expansion_invariants_error(evidence: dict[str, Any]) -> str | None:
    invariants = evidence.get("non_expansion_invariants")
    if not isinstance(invariants, dict):
        return "non_expansion_invariants evidence missing"
    for field, expected in REQUIRED_NON_EXPANSION_INVARIANTS.items():
        if field not in invariants:
            return f"non_expansion_invariants missing: {field}"
        if invariants.get(field) is not expected:
            return f"non_expansion_invariants mismatch: {field}"
    return None


def check_platform_runtime_evidence(evidence_path: str | Path, *, run_id: str = "") -> CheckResult:
    evidence, error = _read_evidence(evidence_path)
    if error:
        return CheckResult("check_platform_runtime_evidence", False, error)
    run_error = _required_evidence_error(evidence, run_id=run_id)
    if run_error:
        return CheckResult("check_platform_runtime_evidence", False, run_error)
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        return CheckResult("check_platform_runtime_evidence", False, "sandbox runtime evidence schema mismatch")
    if evidence.get("runtime_mode") != "platform":
        return CheckResult("check_platform_runtime_evidence", False, "platform runtime evidence missing")
    if evidence.get("sandbox_provider") not in REAL_SANDBOX_PROVIDERS:
        return CheckResult("check_platform_runtime_evidence", False, "real sandbox provider evidence missing")
    timing_error = _timing_error(evidence.get("timings"))
    if timing_error:
        return CheckResult("check_platform_runtime_evidence", False, timing_error)
    executor_error = _executor_sdk_error(evidence)
    if executor_error:
        return CheckResult("check_platform_runtime_evidence", False, executor_error)
    invariants_error = _non_expansion_invariants_error(evidence)
    if invariants_error:
        return CheckResult("check_platform_runtime_evidence", False, invariants_error)
    return CheckResult("check_platform_runtime_evidence", True, "platform runtime latency split evidence present")


def _hardening_error(evidence: dict[str, Any]) -> str | None:
    hardening = evidence.get("hardening")
    if not isinstance(hardening, dict):
        return "platform hardening evidence missing"
    for section_name, required_fields in REQUIRED_HARDENING_FLAGS.items():
        section = hardening.get(section_name)
        if not isinstance(section, dict):
            return f"hardening section missing: {section_name}"
        if section.get("evidence_class") != HARDENING_EVIDENCE_CLASS[section_name]:
            return f"hardening evidence_class mismatch: {section_name}"
        if section_name == "resource_limits":
            section_error = _resource_limits_hardening_error(section, run_id=str(evidence.get("run_id") or ""))
            if section_error:
                return section_error
            continue
        if section_name == "egress_policy":
            section_error = _egress_policy_hardening_error(section)
            if section_error:
                return section_error
            continue
        if section_name == "security_options":
            section_error = _security_options_hardening_error(section)
            if section_error:
                return section_error
            continue
        for field in required_fields:
            value = section.get(field)
            if field == "evidence_class":
                continue
            if field in {"workspace_container_path", "inputs_container_path"}:
                if not isinstance(value, str) or not value:
                    return f"hardening evidence missing: {section_name}.{field}"
            elif field.endswith("_id") or field == "timeout_error_code":
                if not isinstance(value, str) or not value:
                    return f"hardening evidence missing: {section_name}.{field}"
            elif field == "source_regression_tests":
                if not isinstance(value, list) or not value:
                    return f"hardening evidence missing: {section_name}.{field}"
                allowed_tests = ALLOWED_SOURCE_REGRESSION_TESTS.get(section_name)
                if allowed_tests is not None:
                    provided_tests = {test_name for test_name in value if isinstance(test_name, str)}
                    missing_tests = sorted(allowed_tests - provided_tests)
                    if missing_tests:
                        return f"hardening evidence missing source_regression_tests: {section_name}"
                    unknown_tests = [
                        test_name
                        for test_name in value
                        if not isinstance(test_name, str) or test_name not in allowed_tests
                    ]
                    if unknown_tests:
                        return f"hardening evidence unexpected source_regression_tests: {section_name}"
            elif value is not True:
                return f"hardening evidence missing: {section_name}.{field}"
    lease = hardening["lease_isolation"]
    if lease.get("recorded_lease_id") != lease.get("released_lease_id"):
        return "released lease does not match recorded lease"
    if lease.get("release_reason") not in {"dispatch_completed", "run_succeeded", "run_cancelled", "run_failed"}:
        return "lease release reason missing or unexpected"
    workspace = hardening["workspace_isolation"]
    if workspace.get("workspace_container_path") != "/workspace":
        return "workspace container path mismatch"
    if workspace.get("inputs_container_path") != "/workspace/inputs":
        return "inputs container path mismatch"
    if hardening["resource_timeout"].get("timeout_error_code") != "executor_health_timeout":
        return "resource timeout evidence must prove executor_health_timeout fallback"
    return None


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, int | float):
        return False
    return value > 0


def _resource_limits_hardening_error(section: dict[str, Any], *, run_id: str) -> str | None:
    for field in (
        "memory_limit_mb",
        "cpu_limit_count",
        "pids_limit",
        "process_timeout_seconds",
    ):
        if not _positive_number(section.get(field)):
            return f"hardening evidence missing: resource_limits.{field}"
    if section.get("limit_source") != "platform_request":
        return "hardening evidence missing: resource_limits.limit_source"
    if section.get("over_limit_probe_kind") != "platform_resource_timeout":
        return "hardening evidence missing: resource_limits.over_limit_probe_kind"
    if section.get("over_limit_timeout_probe_seconds") != 0:
        return "hardening evidence missing: resource_limits.over_limit_timeout_probe_seconds"
    for field in (
        "docker_inspection_verified",
        "over_limit_cleanup_verified",
        "bounded_error_projection_verified",
    ):
        if section.get(field) is not True:
            return f"hardening evidence missing: resource_limits.{field}"
    projection_error = bounded_error_projection_error(
        section.get("bounded_error_projection"),
        run_id=run_id,
    )
    if projection_error:
        return f"hardening evidence missing: {projection_error}"
    return None


def _egress_policy_hardening_error(section: dict[str, Any]) -> str | None:
    for field in (
        "default_deny_outbound",
        "platform_allowlist_enforced",
        "callback_exception_scoped_to_run_token",
        "denied_egress_redacted",
    ):
        if section.get(field) is not True:
            return f"hardening evidence missing: egress_policy.{field}"
    for field in (
        "denied_target",
        "denied_probe_error_code",
        "allowed_callback_host",
        "callback_probe_status",
    ):
        value = section.get(field)
        if not isinstance(value, str) or not value:
            return f"hardening evidence missing: egress_policy.{field}"
    if section.get("denied_probe_error_code") != "egress_denied":
        return "hardening evidence missing: egress_policy.denied_probe_error_code"
    if section.get("callback_probe_status") != "delivered":
        return "hardening evidence missing: egress_policy.callback_probe_status"
    if section.get("policy_source") != "platform_policy":
        return "hardening evidence missing: egress_policy.policy_source"
    if section.get("probe_source") != "runtime_probe_results":
        return "hardening evidence missing: egress_policy.probe_source"
    return None


def _security_options_hardening_error(section: dict[str, Any]) -> str | None:
    if section.get("privileged") is not False:
        return "hardening evidence missing: security_options.privileged"
    if section.get("docker_socket_mounted") is not False:
        return "hardening evidence missing: security_options.docker_socket_mounted"
    for field in (
        "no_new_privileges",
        "capabilities_dropped",
        "root_filesystem_read_only_or_minimal",
    ):
        if section.get(field) is not True:
            return f"hardening evidence missing: security_options.{field}"
    if section.get("workspace_mount_mode") not in {"rw", "ro"}:
        return "hardening evidence missing: security_options.workspace_mount_mode"
    return None


def _opensandbox_provider_lifecycle_error(evidence: dict[str, Any]) -> str | None:
    if evidence.get("sandbox_provider") != "opensandbox":
        return None
    if evidence.get("runtime_mode") != "platform":
        return "OpenSandbox provider lifecycle requires platform runtime evidence"
    lifecycle = evidence.get("provider_lifecycle")
    if not isinstance(lifecycle, dict):
        return "OpenSandbox provider lifecycle evidence missing"
    if lifecycle.get("schema_version") != "ai-platform.opensandbox-provider-lifecycle.v1":
        return "OpenSandbox provider lifecycle schema mismatch"
    if lifecycle.get("provider") != "opensandbox":
        return "OpenSandbox provider lifecycle provider mismatch"
    if lifecycle.get("run_id") != evidence.get("run_id"):
        return "OpenSandbox provider lifecycle run_id mismatch"

    required_true_fields = {
        "lifecycle": (
            "create_observed",
            "delete_observed",
            "container_id_present",
            "executor_endpoint_present",
        ),
        "db_lease": (
            "recorded",
            "released",
            "recorded_scope_matches_request",
        ),
        "startup_io": (
            "file_write_read_verified",
            "command_execution_verified",
        ),
        "resource_policy": (
            "resource_limits_requested",
        ),
        "egress_policy": (
            "policy_requested",
            "callback_host_allowlisted",
        ),
        "dispatch": (
            "executor_response_present",
            "callback_stream_observed",
            "sdk_executor_observed",
        ),
        "redaction": (
            "host_paths_redacted",
            "secrets_absent",
        ),
    }
    for section_name, fields in required_true_fields.items():
        section = lifecycle.get(section_name)
        if not isinstance(section, dict):
            return f"OpenSandbox provider lifecycle section missing: {section_name}"
        for field in fields:
            if section.get(field) is not True:
                return f"OpenSandbox provider lifecycle evidence missing: {section_name}.{field}"

    db_lease = lifecycle["db_lease"]
    lifecycle_section = lifecycle["lifecycle"]
    if lifecycle_section.get("delete_stop_status") != "stopped":
        return "OpenSandbox provider lifecycle delete stop status missing or unexpected"
    if db_lease.get("release_reason") not in {"dispatch_completed", "run_succeeded", "run_cancelled", "run_failed"}:
        return "OpenSandbox provider lifecycle release reason missing or unexpected"
    startup_io = lifecycle["startup_io"]
    if startup_io.get("source") != "OpenSandboxContainerProvider.startup_io_probe":
        return "OpenSandbox provider lifecycle startup probe source mismatch"
    resource_policy = lifecycle["resource_policy"]
    for field in ("memory_mb", "cpu_count", "pids_limit"):
        if not _positive_number(resource_policy.get(field)):
            return f"OpenSandbox provider lifecycle evidence missing: resource_policy.{field}"
    if resource_policy.get("policy_projection_source") != "provider_request":
        return "OpenSandbox provider lifecycle resource policy source mismatch"
    egress_policy = lifecycle["egress_policy"]
    if egress_policy.get("policy_projection_source") != "provider_request":
        return "OpenSandbox provider lifecycle egress policy source mismatch"
    return None


def check_opensandbox_provider_lifecycle_evidence(evidence_path: str | Path, *, run_id: str = "") -> CheckResult:
    """Validate first-stage OpenSandbox lifecycle evidence when that provider is selected."""

    evidence, error = _read_evidence(evidence_path)
    if error:
        return CheckResult("check_opensandbox_provider_lifecycle_evidence", False, error)
    run_error = _required_evidence_error(evidence, run_id=run_id)
    if run_error:
        return CheckResult("check_opensandbox_provider_lifecycle_evidence", False, run_error)
    lifecycle_error = _opensandbox_provider_lifecycle_error(evidence)
    if lifecycle_error:
        return CheckResult("check_opensandbox_provider_lifecycle_evidence", False, lifecycle_error)
    if evidence.get("sandbox_provider") != "opensandbox":
        return CheckResult(
            "check_opensandbox_provider_lifecycle_evidence",
            True,
            "OpenSandbox provider lifecycle not applicable",
        )
    return CheckResult(
        "check_opensandbox_provider_lifecycle_evidence",
        True,
        "OpenSandbox provider lifecycle evidence present",
    )


def check_platform_hardening_evidence(evidence_path: str | Path, *, run_id: str = "") -> CheckResult:
    evidence, error = _read_evidence(evidence_path)
    if error:
        return CheckResult("check_platform_hardening_evidence", False, error)
    run_error = _required_evidence_error(evidence, run_id=run_id)
    if run_error:
        return CheckResult("check_platform_hardening_evidence", False, run_error)
    hardening_error = _hardening_error(evidence)
    if hardening_error:
        return CheckResult("check_platform_hardening_evidence", False, hardening_error)
    return CheckResult("check_platform_hardening_evidence", True, "platform hardening evidence present")


def _secret_like_field_name(value: str) -> bool:
    normalized = value.replace("-", "_").lower()
    if normalized in SAFE_SECRET_FIELD_NAMES:
        return False
    if normalized.endswith(SAFE_SECRET_FIELD_SUFFIXES):
        return False
    if normalized in SECRET_FIELD_NAMES:
        return True
    return normalized.endswith(SECRET_FIELD_SUFFIXES)


def _url_query_secret_present(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if not parsed.query:
        return False
    return any(_secret_like_field_name(key) and item not in ("", None) for key, item in parse_qsl(parsed.query))


def _json_secret_value_present(value: Any, *, sensitive_parent: bool = False) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_is_sensitive = _secret_like_field_name(str(key))
            if key_is_sensitive and item not in (None, "", False):
                return True
            if _json_secret_value_present(item, sensitive_parent=sensitive_parent or key_is_sensitive):
                return True
        return False
    if isinstance(value, list):
        return any(_json_secret_value_present(item, sensitive_parent=sensitive_parent) for item in value)
    if isinstance(value, str) and _url_query_secret_present(value):
        return True
    return sensitive_parent and value not in (None, "", False)


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
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = None
    if _json_secret_value_present(payload):
        return CheckResult("check_no_secret_leakage", False, "sensitive evidence detected")
    return CheckResult("check_no_secret_leakage", True, "no sensitive evidence detected")


def run_checks(checks: list[Callable[[], CheckResult]]) -> tuple[int, list[CheckResult]]:
    results = [check() for check in checks]
    exit_code = 0 if all(result.passed for result in results) else 1
    return exit_code, results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Verify ai-platform sandbox runtime on 211; use --docker-cmd "sudo -n docker" on 211.'
        )
    )
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
    parser.add_argument(
        "--docker-cmd",
        default=os.environ.get("DOCKER_CMD", "docker"),
        help='Docker command; use --docker-cmd "sudo -n docker" on 211.',
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    docker_cmd = tuple(part for part in args.docker_cmd.split(" ") if part)
    checks = [
        lambda: check_docker_socket(docker_cmd),
        lambda: check_workspace_write(args.workspace_root),
        lambda: check_executor_health_or_platform_evidence(
            args.executor_url,
            args.evidence_file,
            run_id=args.run_id,
        ),
        lambda: check_callback_stream(args.evidence_file, run_id=args.run_id, executor_url=args.executor_url),
        lambda: check_cancel_stops_container(args.evidence_file, run_id=args.run_id),
        lambda: check_platform_runtime_evidence(args.evidence_file, run_id=args.run_id),
        lambda: check_opensandbox_provider_lifecycle_evidence(args.evidence_file, run_id=args.run_id),
        lambda: check_platform_hardening_evidence(args.evidence_file, run_id=args.run_id),
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
