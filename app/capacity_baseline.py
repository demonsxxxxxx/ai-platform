from typing import Any

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
