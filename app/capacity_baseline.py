from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.settings import get_settings


LOAD_TEST_GATES = [
    "api_read_write_burst",
    "run_creation_burst_by_tenant_and_user",
    "worker_processing_throughput",
    "queue_depth_and_lease_latency",
    "cancel_retry_resume_under_load",
    "sandbox_lease_creation_under_load",
    "model_gateway_timeout_and_backpressure",
]

_SANDBOX_PROVIDER_VALUES = {"docker", "fake"}
_MODEL_GATEWAY_PROVIDER_VALUES = {"new-api", "openai_compatible"}
_LOAD_TEST_GATE_PURPOSES = {
    "api_read_write_burst": "Measure API health and admin projection behavior during bounded read/write bursts.",
    "run_creation_burst_by_tenant_and_user": "Prove serialized admission and queue behavior across tenants and users.",
    "worker_processing_throughput": "Measure sustained worker throughput at the configured active-run ceiling.",
    "queue_depth_and_lease_latency": "Measure queue depth growth, lease latency, and bounded metadata behavior.",
    "cancel_retry_resume_under_load": "Exercise cancel, retry, and resume while queue and worker pressure are present.",
    "sandbox_lease_creation_under_load": "Measure sandbox lease create, renew, cleanup, and cold-start latency.",
    "model_gateway_timeout_and_backpressure": "Measure model gateway timeout, retry, and backpressure behavior.",
}
_LOAD_TEST_REQUIRED_EVIDENCE = [
    "commit_sha",
    "api_worker_image_labels",
    "frontend_commit_or_image_label",
    "runtime_profile",
    "api_worker_process_counts",
    "database_pool_settings",
    "redis_queue_settings",
    "admission_worker_queue_sandbox_model_settings",
    "peak_and_sustained_queue_depths",
    "active_worker_runs_users_and_tenants",
    "database_pool_waiting_and_saturation",
    "latency_p50_p95_p99",
    "error_taxonomy_counts",
    "dead_letter_counts",
    "cleanup_proof",
]
_LOAD_TEST_STOP_CONDITIONS = [
    "do_not_raise_concurrency_defaults",
    "http_5xx_rate_exceeds_threshold",
    "database_pool_waiting_saturated",
    "worker_capacity_saturated_without_recovery",
    "queue_lease_latency_exceeds_profile_threshold",
    "sandbox_cleanup_or_orphan_detection_fails",
    "model_gateway_timeout_or_retry_storm_detected",
]


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_setting(settings: object, name: str, default: int = 0) -> int:
    return _coerce_int(getattr(settings, name, default), default)


def _float_setting(settings: object, name: str, default: float = 0.0) -> float:
    return _coerce_float(getattr(settings, name, default), default)


def _bool_setting(settings: object, name: str) -> bool:
    return _coerce_bool(getattr(settings, name, False))


def _string_setting(settings: object, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    return str(value or default)


def _enum_setting(settings: object, name: str, *, default: str, allowed_values: set[str]) -> str:
    value = _string_setting(settings, name, default).strip().lower()
    return value if value in allowed_values else "unknown"


def _safe_base_url(value: str) -> str:
    raw = str(value or "http://127.0.0.1:8020").strip()
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        return "http://127.0.0.1:8020"
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "")) or "http://127.0.0.1:8020"


def _warnings_for(limits: dict[str, Any]) -> list[str]:
    warnings: list[str] = [
        "api_request_concurrency_unbounded_by_platform",
        "model_gateway_concurrency_unbounded_by_platform",
    ]
    if not limits["queue"]["tenant_processing_quota_enabled"]:
        warnings.append("queue_tenant_processing_quota_disabled")
    if not limits["queue"]["user_processing_quota_enabled"]:
        warnings.append("queue_user_processing_quota_disabled")
    if limits["sandbox"]["container_provider"] != "docker":
        warnings.append("sandbox_provider_not_production_docker")
    warnings.append("sandbox_hardening_evidence_missing")
    if limits["multi_agent"]["worker_enabled"]:
        warnings.append("multi_agent_dispatch_enabled_requires_capacity_evidence")
    return warnings


def build_capacity_baseline(settings: object | None = None) -> dict[str, Any]:
    """Build a secret-safe configured capacity baseline for Admin Runtime and CLI use."""
    resolved_settings = settings or get_settings()
    queue_tenant_processing_limit = _int_setting(resolved_settings, "queue_tenant_processing_limit")
    queue_user_processing_limit = _int_setting(resolved_settings, "queue_user_processing_limit")
    limits: dict[str, Any] = {
        "api": {
            "uvicorn_processes": 1,
            "request_concurrency_limit": None,
            "capacity_evidence": "unproven_without_load_test",
        },
        "worker": {
            "max_active_worker_runs": _int_setting(resolved_settings, "max_active_worker_runs", 3),
            "worker_processes": 1,
        },
        "admission": {
            "max_active_runs_per_user": _int_setting(resolved_settings, "max_active_runs_per_user", 3),
        },
        "database_pool": {
            "min_size": _int_setting(resolved_settings, "database_pool_min_size", 1),
            "max_size": _int_setting(resolved_settings, "database_pool_max_size", 10),
            "timeout_seconds": _float_setting(resolved_settings, "database_pool_timeout_seconds", 10.0),
            "max_waiting": _int_setting(resolved_settings, "database_pool_max_waiting", 100),
        },
        "queue": {
            "tenant_processing_limit": queue_tenant_processing_limit,
            "tenant_processing_quota_enabled": queue_tenant_processing_limit > 0,
            "user_processing_limit": queue_user_processing_limit,
            "user_processing_quota_enabled": queue_user_processing_limit > 0,
            "lease_scan_limit": _int_setting(resolved_settings, "queue_lease_scan_limit", 50),
            "insight_scan_limit": _int_setting(resolved_settings, "queue_insight_scan_limit", 500),
            "metadata_fallback_scan_limit": _int_setting(
                resolved_settings,
                "queue_metadata_fallback_scan_limit",
                500,
            ),
        },
        "sandbox": {
            "container_provider": _enum_setting(
                resolved_settings,
                "sandbox_container_provider",
                default="fake",
                allowed_values=_SANDBOX_PROVIDER_VALUES,
            ),
            "max_active_ephemeral_containers": _int_setting(
                resolved_settings,
                "sandbox_max_active_ephemeral_containers",
                2,
            ),
            "max_active_persistent_containers": _int_setting(
                resolved_settings,
                "sandbox_max_active_persistent_containers",
                1,
            ),
            "container_start_timeout_seconds": _int_setting(
                resolved_settings,
                "sandbox_container_start_timeout_seconds",
                30,
            ),
            "executor_health_timeout_seconds": _int_setting(
                resolved_settings,
                "sandbox_executor_health_timeout_seconds",
                60,
            ),
        },
        "model_gateway": {
            "provider": _enum_setting(
                resolved_settings,
                "llm_gateway_provider",
                default="openai_compatible",
                allowed_values=_MODEL_GATEWAY_PROVIDER_VALUES,
            ),
            "request_concurrency_limit": None,
            "capacity_evidence": "unproven_without_load_test",
        },
        "multi_agent": {
            "worker_enabled": _bool_setting(resolved_settings, "multi_agent_dispatch_worker_enabled"),
            "worker_limit": _int_setting(resolved_settings, "multi_agent_dispatch_worker_limit", 1),
        },
    }
    return {
        "schema_version": "ai-platform.capacity-baseline.v1",
        "profile": "unproven_default",
        "limits": limits,
        "live_signal_route": "/api/ai/admin/runtime/overview",
        "load_test_gates": LOAD_TEST_GATES,
        "production_default_policy": "do_not_raise_without_recorded_load_test_evidence",
        "warnings": _warnings_for(limits),
    }


def build_capacity_load_test_plan(
    settings: object | None = None,
    *,
    base_url: str = "http://127.0.0.1:8020",
    tenants: int = 3,
    users_per_tenant: int = 5,
    runs_per_user: int = 2,
    duration_seconds: int = 300,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Build a repeatable, secret-safe #21 load-test plan without executing load."""
    safe_base_url = _safe_base_url(base_url)
    parameters = {
        "tenants": max(_coerce_int(tenants, 3), 1),
        "users_per_tenant": max(_coerce_int(users_per_tenant, 5), 1),
        "runs_per_user": max(_coerce_int(runs_per_user, 2), 1),
        "duration_seconds": max(_coerce_int(duration_seconds, 300), 30),
    }
    selected_gates = LOAD_TEST_GATES
    if scenario:
        normalized = str(scenario).strip()
        selected_gates = [gate for gate in LOAD_TEST_GATES if gate == normalized]
        if not selected_gates:
            selected_gates = []

    def command_for(gate: str) -> str:
        return (
            "python tools/capacity_load_plan.py --dry-run"
            f" --scenario {gate}"
            f" --base-url {safe_base_url}"
            f" --tenants {parameters['tenants']}"
            f" --users-per-tenant {parameters['users_per_tenant']}"
            f" --runs-per-user {parameters['runs_per_user']}"
            f" --duration-seconds {parameters['duration_seconds']}"
        )

    scenarios = [
        {
            "gate": gate,
            "purpose": _LOAD_TEST_GATE_PURPOSES[gate],
            "mode": "dry_run_command_manifest",
            "parameters": parameters,
            "command": command_for(gate),
            "required_admin_runtime_sections": [
                "capacity",
                "database_pool",
                "queue",
                "admission",
                "backpressure",
                "observability",
            ],
        }
        for gate in selected_gates
    ]
    return {
        "schema_version": "ai-platform.capacity-load-test-plan.v1",
        "baseline": build_capacity_baseline(settings),
        "base_url": safe_base_url,
        "execution_policy": {
            "default_mode": "dry_run_plan_only",
            "requires_explicit_operator_execution": True,
            "production_defaults_policy": "do_not_raise_without_recorded_load_test_evidence",
        },
        "scenarios": scenarios,
        "required_evidence": _LOAD_TEST_REQUIRED_EVIDENCE,
        "stop_conditions": _LOAD_TEST_STOP_CONDITIONS,
        "cleanup_policy": "remove test tenants, queued payloads, sandbox leases, temporary artifacts, and generated documents after each run",
    }


def render_capacity_baseline_markdown(baseline: dict[str, Any]) -> str:
    """Render a capacity baseline snapshot as operator-readable Markdown."""
    limits = baseline["limits"]
    rows = [
        ("API request concurrency", "unbounded by platform; load-test required"),
        ("Active worker runs", str(limits["worker"]["max_active_worker_runs"])),
        ("Per-user active runs", str(limits["admission"]["max_active_runs_per_user"])),
        ("DB pool max size", str(limits["database_pool"]["max_size"])),
        ("DB pool max waiting", str(limits["database_pool"]["max_waiting"])),
        ("Tenant queue processing limit", str(limits["queue"]["tenant_processing_limit"])),
        ("User queue processing limit", str(limits["queue"]["user_processing_limit"])),
        ("Queue lease scan limit", str(limits["queue"]["lease_scan_limit"])),
        ("Sandbox provider", str(limits["sandbox"]["container_provider"])),
        (
            "Sandbox active containers",
            (
                f"ephemeral={limits['sandbox']['max_active_ephemeral_containers']}, "
                f"persistent={limits['sandbox']['max_active_persistent_containers']}"
            ),
        ),
        ("Model gateway concurrency", "unbounded by platform; load-test required"),
        ("Multi-agent dispatcher enabled", str(limits["multi_agent"]["worker_enabled"]).lower()),
    ]
    table = "\n".join(f"| {name} | {value} |" for name, value in rows)
    gates = "\n".join(f"- {gate}" for gate in baseline["load_test_gates"])
    warnings = "\n".join(f"- {warning}" for warning in baseline["warnings"])
    return (
        "# ai-platform Capacity Baseline\n\n"
        f"Schema: `{baseline['schema_version']}`\n\n"
        "| Capacity term | Current configured value |\n"
        "| --- | --- |\n"
        f"{table}\n\n"
        "## Load-Test Gates\n\n"
        f"{gates}\n\n"
        "## Production Default Policy\n\n"
        "Do not raise production concurrency defaults without recorded load-test evidence.\n\n"
        "## Current Warnings\n\n"
        f"{warnings}\n"
    )


def render_capacity_load_test_plan_markdown(plan: dict[str, Any]) -> str:
    """Render a #21 capacity load-test command manifest as operator-readable Markdown."""
    scenario_blocks = []
    for item in plan["scenarios"]:
        scenario_blocks.append(
            "\n".join(
                [
                    f"### {item['gate']}",
                    "",
                    item["purpose"],
                    "",
                    "```powershell",
                    item["command"],
                    "```",
                ]
            )
        )
    evidence = "\n".join(f"- {item}" for item in plan["required_evidence"])
    stop_conditions = "\n".join(f"- {item}" for item in plan["stop_conditions"])
    scenarios = "\n\n".join(scenario_blocks)
    return (
        "# ai-platform Capacity Load-Test Plan\n\n"
        f"Schema: `{plan['schema_version']}`\n\n"
        "This is a repeatable command manifest. It does not raise production "
        "concurrency defaults and defaults to dry-run planning until an operator "
        "executes a concrete load harness for the target deployment profile.\n\n"
        "## Execution Policy\n\n"
        "- Default mode: dry-run plan only.\n"
        "- Explicit operator execution is required for any real load.\n"
        "- Do not raise production concurrency defaults without recorded load-test evidence.\n\n"
        "## Scenarios\n\n"
        f"{scenarios}\n\n"
        "## Required Evidence\n\n"
        f"{evidence}\n\n"
        "## Stop Conditions\n\n"
        f"{stop_conditions}\n\n"
        "## Cleanup Policy\n\n"
        f"{plan['cleanup_policy']}\n"
    )
