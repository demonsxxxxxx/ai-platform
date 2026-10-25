

from copy import deepcopy
from typing import Any

LOAD_TEST_GATES = [
    "api_read_write_burst",
    "run_creation_burst_by_tenant_and_user",
    "worker_processing_throughput",
    "queue_depth_and_lease_latency",
    "cancel_retry_resume_under_load",
    "sandbox_lease_creation_under_load",
    "model_gateway_timeout_and_backpressure",
]

_SANDBOX_PROVIDER_VALUES = {"docker", "fake", "opensandbox"}

_MODEL_GATEWAY_PROVIDER_VALUES = {"new-api", "openai_compatible"}

MODEL_GATEWAY_BACKPRESSURE_POLICY_SCHEMA = "ai-platform.model-gateway-backpressure-policy.v1"

_MODEL_GATEWAY_BACKPRESSURE_POLICY = {
    "schema_version": MODEL_GATEWAY_BACKPRESSURE_POLICY_SCHEMA,
    "status": "contract_only_not_enforced",
    "config_signal": None,
    "default_limit_policy": "unbounded_by_platform",
    "required_admin_runtime_fields": [
        "capacity.limits.model_gateway",
        "backpressure.model_gateway",
        "observability.error_categories",
    ],
    "required_load_test_gate": "model_gateway_timeout_and_backpressure",
    "enforcement_status": "not_implemented",
    "capacity_evidence": "unproven_without_load_test",
    "production_default_policy": "do_not_raise_without_recorded_load_test_evidence",
    "does_not_raise_defaults": True,
    "does_not_close_g9": True,
}


def _default_settings() -> object:
    from app.settings import get_settings

    return get_settings()


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


def _int_setting(settings: object, name: str, default: int = 0) -> int:
    return _coerce_int(getattr(settings, name, default), default)


def _float_setting(settings: object, name: str, default: float = 0.0) -> float:
    return _coerce_float(getattr(settings, name, default), default)


def _string_setting(settings: object, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    return str(value or default)


def _enum_setting(settings: object, name: str, *, default: str, allowed_values: set[str]) -> str:
    value = _string_setting(settings, name, default).strip().lower()
    return value if value in allowed_values else "unknown"


def _warnings_for(limits: dict[str, Any]) -> list[str]:
    warnings: list[str] = ["api_request_concurrency_unbounded_by_platform"]
    warnings.append("model_gateway_concurrency_unbounded_by_platform")
    if limits["model_gateway"].get("configured_request_concurrency_limit") is not None:
        warnings.append("model_gateway_configured_limit_not_enforced")
        warnings.append("model_gateway_capacity_unproven_without_load_test")
    if not limits["queue"]["tenant_processing_quota_enabled"]:
        warnings.append("queue_tenant_processing_quota_disabled")
    if not limits["queue"]["user_processing_quota_enabled"]:
        warnings.append("queue_user_processing_quota_disabled")
    if limits["sandbox"]["container_provider"] != "docker":
        warnings.append("sandbox_provider_not_production_docker")
    warnings.append("sandbox_hardening_evidence_missing")
    return warnings


def build_capacity_baseline(settings: object | None = None) -> dict[str, Any]:
    """Build a secret-safe configured capacity baseline for Admin Runtime and CLI use."""
    resolved_settings = settings or _default_settings()
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
            # Preserve the snapshot field for Admin Runtime and recorded evidence.
            "configured_request_concurrency_limit": None,
            "limit_enforcement": "not_implemented",
            "capacity_evidence": "unproven_without_load_test",
        },
    }
    return {
        "schema_version": "ai-platform.capacity-baseline.v1",
        "profile": "unproven_default",
        "limits": limits,
        "model_gateway_backpressure_policy": build_model_gateway_backpressure_policy(),
        "live_signal_route": "/api/ai/admin/runtime/overview",
        "load_test_gates": LOAD_TEST_GATES,
        "production_default_policy": "do_not_raise_without_recorded_load_test_evidence",
        "warnings": _warnings_for(limits),
    }


def build_model_gateway_backpressure_policy() -> dict[str, Any]:
    """Return the source-level model-gateway backpressure policy contract."""
    return deepcopy(_MODEL_GATEWAY_BACKPRESSURE_POLICY)
