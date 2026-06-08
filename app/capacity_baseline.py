import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.error_taxonomy import ERROR_CATEGORY_DEFINITIONS, summarize_error_categories
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
CAPACITY_EVIDENCE_BUNDLE_SCHEMA = "ai-platform.capacity-evidence-bundle.v1"
CAPACITY_PROFILE_IDS = [
    "conservative_internal",
    "medium_team",
    "high_capacity_1t",
]

_SANDBOX_PROVIDER_VALUES = {"docker", "fake"}
_MODEL_GATEWAY_PROVIDER_VALUES = {"new-api", "openai_compatible"}
_CAPACITY_GATE_READINESS_STATUSES = {
    "blocked_missing_admin_runtime_sections",
    "blocked_incomplete_load_test_evidence",
    "blocked_missing_load_test_evidence",
    "ready_for_operator_review",
}
_CAPACITY_PROFILE_STATUS_BY_GATE_STATUS = {
    "ready_for_operator_review": "operator_review_required",
    "blocked_missing_admin_runtime_sections": "blocked_missing_admin_runtime_sections",
    "blocked_incomplete_load_test_evidence": "blocked_incomplete_load_test_evidence",
    "blocked_missing_load_test_evidence": "blocked_missing_load_test_evidence",
}
_CAPACITY_PROFILE_CATALOG = [
    {
        "id": "conservative_internal",
        "label": "Conservative internal profile",
        "intent": "Small internal pilot profile for bounded office-agent usage.",
        "required_load_test_gates": LOAD_TEST_GATES,
        "profile_specific_requirements": [
            "prove current worker, DB pool, queue, sandbox, and model-gateway settings under bounded load",
            "keep tenant/user queue quota and active-run saturation visible in Admin Runtime",
            "record cleanup proof before any production-default decision",
        ],
    },
    {
        "id": "medium_team",
        "label": "Medium team profile",
        "intent": "Team-level intranet profile that needs tenant/user fairness and sustained queue evidence.",
        "required_load_test_gates": LOAD_TEST_GATES,
        "profile_specific_requirements": [
            "prove N-tenant and M-user run creation bursts with no noisy-neighbor starvation",
            "record worker throughput, queue depth, lease latency, DB waiting, and error taxonomy",
            "require operator review before changing API, worker, DB-pool, Redis, sandbox, or model-gateway settings",
        ],
    },
    {
        "id": "high_capacity_1t",
        "label": "High-capacity 1T-memory server profile",
        "intent": "Large single-server profile; memory size is not accepted as capacity proof.",
        "required_load_test_gates": LOAD_TEST_GATES,
        "profile_specific_requirements": [
            "do not treat 1T memory as evidence of safe concurrency",
            "prove sandbox lease/container behavior and model-gateway backpressure under target load",
            "record alert, cleanup, stop-condition, latency percentile, and operator-review evidence before any default increase",
        ],
    },
]
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
_LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS = [
    "capacity",
    "database_pool",
    "queue",
    "admission",
    "backpressure",
    "sandbox",
    "observability",
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
_CLEANUP_PROOF_STATUS_VALUES = ["recorded", "passed", "verified", "complete"]
_STOP_CONDITION_STATUS_VALUES = ["passed", "not_triggered", "verified", "clear"]
_SECRET_EVIDENCE_MARKERS = (
    "secret",
    "token",
    "password",
    "api_key",
    "authorization",
    "bearer",
    "database_url",
    "redis_url",
    "storage_key",
    "raw_storage_key",
    "sandbox_workdir",
    "executor_private_payload",
)
_LOAD_TEST_OPERATOR_WORKFLOW = [
    {
        "id": "capture_start_runtime_evidence",
        "purpose": "Capture the pre-load Admin Runtime capacity projection and fail-closed gate verdict.",
        "command_template": (
            "python tools/capacity_runtime_evidence.py"
            " --base-url {base_url}"
            " --user-id codex-capacity-audit"
            " --tenant-id default"
            " --roles admin"
            " --commit-sha <deployed-commit>"
            " --runtime-profile <runtime-profile>"
            " --format json > capacity-runtime-evidence-start.json"
        ),
        "expected_evidence": "capacity-runtime-evidence-start.json",
        "requires_explicit_operator_execution": False,
        "does_not_raise_defaults": True,
    },
    {
        "id": "confirm_start_gate_status",
        "purpose": "Confirm the start verdict is fail-closed before applying load.",
        "command_template": (
            "verify capacity-runtime-evidence-start.json readiness.status is"
            " blocked_missing_load_test_evidence,"
            " blocked_incomplete_load_test_evidence, or ready_for_operator_review"
        ),
        "expected_evidence": "recorded start readiness status and missing gates",
        "requires_explicit_operator_execution": False,
        "does_not_raise_defaults": True,
    },
    {
        "id": "execute_bounded_load_scenario",
        "purpose": "Run only an approved bounded load harness for one selected gate and profile.",
        "command_template": (
            "for api_read_write_burst, run python tools/capacity_bounded_load_harness.py"
            " --base-url {base_url}"
            " --gate api_read_write_burst"
            " --requests <operator-approved-request-count>"
            " --concurrency <operator-approved-concurrency>"
            " --execute"
            " --operator-acknowledgement send-bounded-load-without-default-raise"
            " --format json > capacity-bounded-load-harness-api-read-write-burst.json;"
            " output status probe_only_not_recorded is not recorded gate evidence;"
            " other gates require an approved harness extension;"
            " tools/capacity_load_plan.py remains dry-run-only"
        ),
        "expected_evidence": "probe result plus separately recorded scenario evidence with latency, errors, queue depth, DB waiting, cleanup, and stop-condition status",
        "requires_explicit_operator_execution": True,
        "does_not_raise_defaults": True,
    },
    {
        "id": "capture_end_runtime_evidence",
        "purpose": "Capture the post-load Admin Runtime projection after the scenario and cooldown.",
        "command_template": (
            "python tools/capacity_runtime_evidence.py"
            " --base-url {base_url}"
            " --user-id codex-capacity-audit"
            " --tenant-id default"
            " --roles admin"
            " --commit-sha <deployed-commit>"
            " --runtime-profile <runtime-profile>"
            " --format json > capacity-runtime-evidence-end.json"
        ),
        "expected_evidence": "capacity-runtime-evidence-end.json",
        "requires_explicit_operator_execution": False,
        "does_not_raise_defaults": True,
    },
    {
        "id": "record_cleanup_proof",
        "purpose": "Record test-tenant, queue, sandbox lease, and generated artifact cleanup proof.",
        "command_template": "record cleanup_proof in the scenario evidence before marking the gate recorded",
        "expected_evidence": "cleanup_proof",
        "requires_explicit_operator_execution": True,
        "does_not_raise_defaults": True,
    },
    {
        "id": "generate_gate_readiness_verdict",
        "purpose": "Generate the final fail-closed #21 verdict from the recorded evidence snapshot.",
        "command_template": (
            "python tools/capacity_gate_readiness.py"
            " --snapshot-json <capacity-evidence-snapshot-with-recorded-load-gates.json>"
            " --format json > capacity-gate-readiness.json"
        ),
        "expected_evidence": "capacity-gate-readiness.json",
        "requires_explicit_operator_execution": False,
        "does_not_raise_defaults": True,
    },
]
_BACKPRESSURE_REASON_VALUES = {
    "active_run_limit_saturated",
    "queued_behind_existing_work",
    "tenant_quota_full",
    "user_quota_full",
    "worker_capacity_full",
    "workers_busy",
    "worker_capacity_saturated",
    "queue_tenant_quota_saturated",
    "queue_user_quota_saturated",
    "database_pool_waiting",
    "database_pool_waiting_saturated",
}
_QUEUE_DEPTH_KEYS = {"queued", "processing", "dead_letter", "tenant_queued", "tenant_processing"}
_QUEUE_CAPACITY_KEYS = {
    "max_active_worker_runs",
    "available_worker_slots",
    "processing_saturated",
    "queue_lease_scan_limit",
    "queue_tenant_processing_limit",
    "queue_user_processing_limit",
}
_QUEUE_SAMPLE_KEYS = {"queued_scan_limit", "queued_sampled", "queued_sample_complete"}
_DB_POOL_CONFIG_KEYS = {"min_size", "max_size", "timeout_seconds", "max_waiting"}
_DB_POOL_STATS_KEYS = {"size", "free", "used", "min_size", "max_size", "requests_waiting", "requests", "holders"}
_ADMISSION_KEYS = {
    "policy_active",
    "max_active_runs_per_user",
    "active_runs",
    "active_users",
    "saturated_users",
}
_SANDBOX_CONTAINER_KEYS = {"total", "running", "ephemeral_running", "persistent_running"}
_SANDBOX_LEASE_KEYS = {"active", "released", "expired"}
_OBSERVABILITY_KEYS = {
    "event_count",
    "artifact_count",
    "error_count",
    "estimated_cost_minor",
}
_LATENCY_KEYS = {"avg", "max", "p50", "p95", "p99"}


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


def _positive_int_setting(settings: object, name: str) -> int | None:
    value = _int_setting(settings, name)
    return value if value > 0 else None


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


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_identity(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    lowered = text.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "api_key", "authorization", "bearer")):
        return "redacted"
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    cleaned = "".join(ch for ch in text if ch in allowed)
    return cleaned[:128] or "unknown"


def _numeric_bool_map(value: object, allowed_keys: set[str]) -> dict[str, object]:
    source = _dict(value)
    result: dict[str, object] = {}
    for key in sorted(allowed_keys):
        item = source.get(key)
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, int | float) and not isinstance(item, bool):
            result[key] = int(item) if isinstance(item, int) or float(item).is_integer() else float(item)
        elif item is None and key in source:
            result[key] = None
    return result


def _safe_reason_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    reasons: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text in _BACKPRESSURE_REASON_VALUES and text not in reasons:
            reasons.append(text)
    return reasons


def _queue_live_signals(overview: dict[str, Any]) -> dict[str, object]:
    queue = _dict(overview.get("queue"))
    status = _dict(queue.get("status"))
    insight = _dict(queue.get("tenant_insight"))
    status_workers = status.get("workers")
    worker_count = 0
    if isinstance(status_workers, list):
        worker_count = len(status_workers)
    elif isinstance(status_workers, dict):
        worker_count = _coerce_int(status_workers.get("active"))
    throttling = _dict(insight.get("throttling"))
    return {
        "depths": {
            **_numeric_bool_map(status.get("depths"), _QUEUE_DEPTH_KEYS),
            **_numeric_bool_map(insight.get("depths"), _QUEUE_DEPTH_KEYS),
        },
        "active_worker_heartbeats": worker_count,
        "reason": str(insight.get("reason")) if str(insight.get("reason") or "") in _BACKPRESSURE_REASON_VALUES else None,
        "capacity": _numeric_bool_map(insight.get("capacity"), _QUEUE_CAPACITY_KEYS),
        "sample": _numeric_bool_map(insight.get("queue_sample"), _QUEUE_SAMPLE_KEYS),
        "throttling": _numeric_bool_map(
            throttling,
            {"tenant_processing_limit", "tenant_processing_saturated", "user_processing_limit"},
        ),
    }


def _database_pool_live_signals(overview: dict[str, Any]) -> dict[str, object]:
    pool = _dict(overview.get("database_pool"))
    configured = _numeric_bool_map(pool.get("configured"), _DB_POOL_CONFIG_KEYS)
    stats = _numeric_bool_map(pool.get("stats"), _DB_POOL_STATS_KEYS)
    return {
        "open": bool(pool.get("open")),
        "configured": configured,
        "requests_waiting": _coerce_int(stats.get("requests_waiting")),
        "max_waiting": _coerce_int(configured.get("max_waiting")),
        "stats": stats,
    }


def _admission_live_signals(overview: dict[str, Any]) -> dict[str, object]:
    return _numeric_bool_map(overview.get("admission"), _ADMISSION_KEYS)


def _sandbox_live_signals(overview: dict[str, Any]) -> dict[str, object]:
    sandbox = _dict(overview.get("sandbox"))
    containers = _numeric_bool_map(_dict(sandbox.get("containers")), _SANDBOX_CONTAINER_KEYS)
    leases = _numeric_bool_map(_dict(sandbox.get("leases")), _SANDBOX_LEASE_KEYS)
    return {
        "containers": containers,
        "running_containers": _coerce_int(containers.get("running")),
        "active_leases": _coerce_int(leases.get("active")),
        "leases": leases,
    }


def _observability_live_signals(overview: dict[str, Any]) -> dict[str, object]:
    observability = _dict(overview.get("observability"))
    error_categories = _numeric_bool_map(
        observability.get("error_categories"),
        set(ERROR_CATEGORY_DEFINITIONS),
    )
    if not error_categories:
        error_categories = summarize_error_categories(observability.get("error_types"))
    return {
        **_numeric_bool_map(observability, _OBSERVABILITY_KEYS),
        "error_categories": error_categories,
        "latency_ms": _numeric_bool_map(observability.get("latency_ms"), _LATENCY_KEYS),
    }


def _admin_runtime_section_evidence(overview: dict[str, Any]) -> dict[str, object]:
    observed_sections = [
        section
        for section in _LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS
        if section in overview and overview.get(section) is not None
    ]
    return {
        "required_sections": list(_LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS),
        "observed_sections": observed_sections,
        "missing_sections": [
            section
            for section in _LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS
            if section not in observed_sections
        ],
    }


def _safe_capacity_value(value: object, default: object) -> object:
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int) and not isinstance(default, bool):
        return _coerce_int(value, default)
    if isinstance(default, float):
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return default
    if default is None:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float) and not isinstance(value, bool):
            return int(value) if isinstance(value, int) or float(value).is_integer() else float(value)
        return None
    if isinstance(default, str):
        sanitized = _safe_identity(value or default)
        return sanitized if sanitized not in {"unknown", "redacted"} else default
    return default


def _safe_capacity_snapshot(value: object) -> dict[str, Any]:
    baseline = build_capacity_baseline()
    source = _dict(value)
    if source.get("schema_version") != baseline["schema_version"]:
        return baseline
    source_limits = _dict(source.get("limits"))
    limits: dict[str, Any] = {}
    for section, defaults in baseline["limits"].items():
        source_section = _dict(source_limits.get(section))
        limits[section] = {
            key: _safe_capacity_value(source_section.get(key), default)
            for key, default in defaults.items()
        }
    model_gateway = limits.get("model_gateway", {})
    model_gateway_limit = model_gateway.get("request_concurrency_limit")
    configured_model_gateway_limit = model_gateway.get("configured_request_concurrency_limit")
    if isinstance(model_gateway, dict):
        model_gateway["request_concurrency_limit"] = None
        if configured_model_gateway_limit is not None and _coerce_int(configured_model_gateway_limit) <= 0:
            model_gateway["configured_request_concurrency_limit"] = None
        if model_gateway.get("limit_enforcement") != "not_implemented":
            model_gateway["limit_enforcement"] = "not_implemented"
    if model_gateway_limit is not None and _coerce_int(model_gateway_limit) <= 0:
        limits["model_gateway"]["request_concurrency_limit"] = None
    source_gates = source.get("load_test_gates")
    load_test_gates = [gate for gate in source_gates if gate in LOAD_TEST_GATES] if isinstance(source_gates, list) else []
    return {
        "schema_version": baseline["schema_version"],
        "profile": _safe_capacity_value(source.get("profile"), baseline["profile"]),
        "limits": limits,
        "live_signal_route": (
            source.get("live_signal_route")
            if source.get("live_signal_route") == baseline["live_signal_route"]
            else baseline["live_signal_route"]
        ),
        "load_test_gates": load_test_gates or baseline["load_test_gates"],
        "production_default_policy": baseline["production_default_policy"],
        "warnings": _warnings_for(limits),
    }


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
            "configured_request_concurrency_limit": _positive_int_setting(
                resolved_settings,
                "model_gateway_request_concurrency_limit",
            ),
            "limit_enforcement": "not_implemented",
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
            "required_admin_runtime_sections": _LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS,
            "recorded_gate_evidence_contract": build_capacity_recorded_gate_evidence_contract(gate),
        }
        for gate in selected_gates
    ]
    operator_workflow = [
        {
            key: (value.format(base_url=safe_base_url) if key == "command_template" else value)
            for key, value in step.items()
        }
        for step in _LOAD_TEST_OPERATOR_WORKFLOW
    ]
    for step in operator_workflow:
        step["command"] = step.pop("command_template")
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
        "operator_workflow": operator_workflow,
        "required_evidence": _LOAD_TEST_REQUIRED_EVIDENCE,
        "stop_conditions": _LOAD_TEST_STOP_CONDITIONS,
        "cleanup_policy": "remove test tenants, queued payloads, sandbox leases, temporary artifacts, and generated documents after each run",
    }


def build_capacity_recorded_gate_evidence_contract(gate: str) -> dict[str, Any]:
    """Build the machine-readable evidence contract for one recorded #21 load-test gate."""
    normalized_gate = str(gate or "").strip()
    valid_gate = normalized_gate in LOAD_TEST_GATES
    safe_gate = normalized_gate if valid_gate else "unknown"
    return {
        "schema_version": "ai-platform.capacity-recorded-gate-evidence-contract.v1",
        "gate": safe_gate,
        "valid_gate": valid_gate,
        "load_test_evidence_status": "recorded",
        "recorded_gates_entry": safe_gate,
        "gate_evidence_path": f"load_test_evidence.gate_evidence.{safe_gate}",
        "required_evidence": [
            {
                "name": item,
                "required": True,
                "value_rule": (
                    "non-empty measured value or artifact reference; "
                    "must not contain sensitive markers or placeholder/template values"
                ),
                "source": "approved_bounded_load_harness_or_runtime_capture",
            }
            for item in _LOAD_TEST_REQUIRED_EVIDENCE
        ],
        "accepted_statuses": {
            "cleanup_proof_status": list(_CLEANUP_PROOF_STATUS_VALUES),
            "stop_condition_status": list(_STOP_CONDITION_STATUS_VALUES),
        },
        "triggered_stop_conditions_rule": "must_be_empty_for_operator_review",
        "operator_warnings": [
            "do_not_submit_template_or_placeholder_values",
            "do_not_include_raw_sensitive_or_private_runtime_payloads",
            "do_not_raise_defaults_from_this_contract",
        ],
        "does_not_raise_defaults": True,
    }


def build_capacity_evidence_snapshot(
    overview: dict[str, Any],
    *,
    commit_sha: str = "unknown",
    runtime_profile: str = "unproven_default",
) -> dict[str, Any]:
    """Build a secret-safe #21 evidence snapshot from an Admin Runtime overview projection."""
    safe_overview = _dict(overview)
    capacity = _safe_capacity_snapshot(safe_overview.get("capacity"))
    backpressure = _dict(safe_overview.get("backpressure"))
    admin_runtime_evidence = _admin_runtime_section_evidence(safe_overview)
    return {
        "schema_version": "ai-platform.capacity-evidence-snapshot.v1",
        "source": {
            "projection": "/api/ai/admin/runtime/overview",
            "mode": "operator_captured_admin_projection",
        },
        "runtime_identity": {
            "commit_sha": _safe_identity(commit_sha),
            "profile": _safe_identity(runtime_profile),
        },
        "admin_runtime_evidence": admin_runtime_evidence,
        "capacity": capacity,
        "live_signals": {
            "queue": _queue_live_signals(safe_overview),
            "database_pool": _database_pool_live_signals(safe_overview),
            "admission": _admission_live_signals(safe_overview),
            "backpressure": {
                "reasons": _safe_reason_list(backpressure.get("reasons")),
            },
            "sandbox": _sandbox_live_signals(safe_overview),
            "observability": _observability_live_signals(safe_overview),
        },
        "load_test_evidence": {
            "status": "missing",
            "required_evidence": _LOAD_TEST_REQUIRED_EVIDENCE,
            "required_gates": LOAD_TEST_GATES,
        },
        "capacity_answer": "safe_max_concurrency_unproven_without_recorded_load_test_evidence",
        "production_default_decision": "do_not_raise_without_recorded_load_test_evidence",
    }


def _snapshot_admin_runtime_evidence(snapshot: dict[str, Any]) -> dict[str, object]:
    source = _dict(snapshot.get("admin_runtime_evidence"))
    required = source.get("required_sections")
    observed = source.get("observed_sections")
    required_sections = [
        section for section in required if section in _LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS
    ] if isinstance(required, list) else list(_LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS)
    observed_sections = [
        section for section in observed if section in required_sections
    ] if isinstance(observed, list) else []
    if not observed_sections:
        live_signals = _dict(snapshot.get("live_signals"))
        observed_sections = [
            section
            for section in required_sections
            if (section == "capacity" and isinstance(snapshot.get("capacity"), dict))
            or (section != "capacity" and section in live_signals)
        ]
    return {
        "required_sections": required_sections,
        "observed_sections": observed_sections,
        "missing_sections": [section for section in required_sections if section not in observed_sections],
    }


def _safe_status(value: object, allowed: set[str], default: str = "missing") -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _contains_secret_marker(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            any(marker in str(key).lower() for marker in _SECRET_EVIDENCE_MARKERS)
            or _contains_secret_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _SECRET_EVIDENCE_MARKERS)
    return False


def _is_placeholder_evidence_text(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    lowered = text.lower()
    if re.search(r"<[^<>]+>", text) or re.search(r"\$\{[^{}]+\}", text):
        return True
    placeholder_tokens = {
        "todo",
        "tbd",
        "placeholder",
        "fill-me",
        "fill_me",
        "fill me",
        "replace-me",
        "replace_me",
        "replace me",
        "example",
        "sample",
    }
    if lowered in placeholder_tokens:
        return True
    return bool(re.fullmatch(r"(?:todo|tbd)[:\s_-].*", lowered)) or bool(
        re.search(r"\b(?:placeholder|fill[-_ ]?me|replace[-_ ]?me)\b", lowered)
    )


def _has_recorded_evidence_value(value: object) -> bool:
    if value is None or _contains_secret_marker(value):
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, int | float) and not isinstance(value, bool):
        return True
    if isinstance(value, str):
        if _is_placeholder_evidence_text(value):
            return False
        return _safe_identity(value) not in {"unknown", "redacted"}
    if isinstance(value, list):
        return bool(value) and all(_has_recorded_evidence_value(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(
            not _is_placeholder_evidence_text(str(key))
            and _has_recorded_evidence_value(item)
            for key, item in value.items()
        )
    try:
        serialized = json.dumps(value, ensure_ascii=False)
    except TypeError:
        return False
    return bool(serialized.strip()) and not _contains_secret_marker(serialized)


def _safe_triggered_stop_conditions(value: object) -> list[str]:
    if value is None:
        return []
    source = value if isinstance(value, list) else [value]
    triggered: list[str] = []
    for item in source:
        if item in (None, "", [], {}):
            continue
        if isinstance(item, str) and not item.strip():
            continue
        safe_item = (
            "redacted"
            if _contains_secret_marker(item)
            or isinstance(item, (dict, list, tuple, set))
            or not isinstance(item, (str, int, float, bool))
            else _safe_identity(item)
        )
        if safe_item not in triggered:
            triggered.append(safe_item)
    return triggered


def _load_gate_evidence_summary(
    load_test_evidence: dict[str, Any],
    required_gates: list[str],
) -> dict[str, Any]:
    recorded_gates_source = load_test_evidence.get("recorded_gates")
    recorded_gates = (
        [gate for gate in recorded_gates_source if gate in required_gates]
        if load_test_evidence.get("status") == "recorded" and isinstance(recorded_gates_source, list)
        else []
    )
    gate_evidence = _dict(load_test_evidence.get("gate_evidence"))
    statuses: dict[str, str] = {}
    invalid: list[dict[str, object]] = []

    for gate in required_gates:
        if gate not in recorded_gates:
            statuses[gate] = "missing_recorded_load_test_evidence"
            continue

        evidence = _dict(gate_evidence.get(gate))
        evidence_payload = _dict(evidence.get("evidence"))
        evidence_names = {
            key
            for key, value in evidence_payload.items()
            if key in _LOAD_TEST_REQUIRED_EVIDENCE and _has_recorded_evidence_value(value)
        }

        missing_required_evidence = [
            item for item in _LOAD_TEST_REQUIRED_EVIDENCE if item not in evidence_names
        ]
        cleanup_proof_status = _safe_status(
            evidence.get("cleanup_proof_status") or evidence.get("cleanup_proof"),
            {"recorded", "passed", "verified", "complete"},
        )
        stop_condition_status = _safe_status(
            evidence.get("stop_condition_status") or evidence.get("stop_conditions_status"),
            {"passed", "not_triggered", "verified", "clear"},
        )
        triggered_stop_conditions = _safe_triggered_stop_conditions(evidence.get("triggered_stop_conditions"))

        if (
            missing_required_evidence
            or cleanup_proof_status == "missing"
            or stop_condition_status == "missing"
            or triggered_stop_conditions
        ):
            statuses[gate] = "incomplete_recorded_load_test_evidence"
            invalid.append(
                {
                    "gate": gate,
                    "missing_required_evidence": missing_required_evidence,
                    "cleanup_proof_status": cleanup_proof_status,
                    "stop_condition_status": stop_condition_status,
                    "triggered_stop_conditions": triggered_stop_conditions,
                }
            )
        else:
            statuses[gate] = "recorded"

    missing_gates = [gate for gate in required_gates if statuses.get(gate) != "recorded"]
    return {
        "statuses": statuses,
        "invalid_load_test_evidence": invalid,
        "missing_load_test_gates": missing_gates,
    }


def _extract_capacity_evidence_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    source = _dict(payload)
    if source.get("schema_version") == "ai-platform.capacity-evidence-snapshot.v1":
        return source
    snapshot = _dict(source.get("snapshot"))
    if snapshot.get("schema_version") == "ai-platform.capacity-evidence-snapshot.v1":
        return snapshot
    return {}


def _compact_evidence_value(value: object) -> object | None:
    if value is None or _contains_secret_marker(value):
        return None
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _contains_secret_marker(key_text):
                continue
            compact = _compact_evidence_value(item)
            if compact is not None:
                cleaned[key_text] = compact
        return cleaned if _has_recorded_evidence_value(cleaned) else None
    if isinstance(value, list):
        cleaned_items = [
            compact for item in value if (compact := _compact_evidence_value(item)) is not None
        ]
        return cleaned_items if _has_recorded_evidence_value(cleaned_items) else None
    return value if _has_recorded_evidence_value(value) else None


def _add_candidate_evidence(target: dict[str, object], key: str, value: object) -> None:
    compact = _compact_evidence_value(value)
    if compact is not None:
        target[key] = compact


def _capacity_bundle_candidate_evidence(
    snapshot: dict[str, Any],
    probe_output: dict[str, Any],
) -> dict[str, object]:
    runtime_identity = _dict(snapshot.get("runtime_identity"))
    live_signals = _dict(snapshot.get("live_signals"))
    capacity = _dict(snapshot.get("capacity"))
    limits = _dict(capacity.get("limits"))
    queue = _dict(live_signals.get("queue"))
    database_pool = _dict(live_signals.get("database_pool"))
    admission = _dict(live_signals.get("admission"))
    sandbox = _dict(live_signals.get("sandbox"))
    observability = _dict(live_signals.get("observability"))
    latency = _dict(probe_output.get("latency_ms"))

    candidate: dict[str, object] = {}
    _add_candidate_evidence(candidate, "commit_sha", runtime_identity.get("commit_sha"))
    _add_candidate_evidence(candidate, "runtime_profile", runtime_identity.get("profile"))
    _add_candidate_evidence(candidate, "database_pool_settings", _dict(limits.get("database_pool")))
    _add_candidate_evidence(candidate, "redis_queue_settings", _dict(limits.get("queue")))
    _add_candidate_evidence(
        candidate,
        "admission_worker_queue_sandbox_model_settings",
        {
            "admission": _dict(limits.get("admission")),
            "worker": _dict(limits.get("worker")),
            "queue": _dict(limits.get("queue")),
            "sandbox": _dict(limits.get("sandbox")),
            "model_gateway": _dict(limits.get("model_gateway")),
        },
    )
    _add_candidate_evidence(
        candidate,
        "peak_and_sustained_queue_depths",
        {"snapshot": _dict(queue.get("depths"))},
    )
    _add_candidate_evidence(
        candidate,
        "active_worker_runs_users_and_tenants",
        {
            "active_runs": admission.get("active_runs"),
            "active_users": admission.get("active_users"),
            "saturated_users": admission.get("saturated_users"),
            "active_sandbox_leases": sandbox.get("active_leases"),
        },
    )
    _add_candidate_evidence(
        candidate,
        "database_pool_waiting_and_saturation",
        {
            "requests_waiting": database_pool.get("requests_waiting"),
            "max_waiting": database_pool.get("max_waiting"),
            "stats": _dict(database_pool.get("stats")),
        },
    )
    _add_candidate_evidence(
        candidate,
        "latency_p50_p95_p99",
        {
            "count": latency.get("count"),
            "p50": latency.get("p50"),
            "p95": latency.get("p95"),
            "p99": latency.get("p99"),
        },
    )
    _add_candidate_evidence(
        candidate,
        "error_taxonomy_counts",
        {
            "error_count": observability.get("error_count"),
            "error_categories": _dict(observability.get("error_categories")),
            "probe_error_types": _dict(probe_output.get("error_type_counts")),
        },
    )
    _add_candidate_evidence(
        candidate,
        "dead_letter_counts",
        _dict(queue.get("depths")).get("dead_letter"),
    )
    return candidate


def build_capacity_evidence_bundle(
    runtime_evidence: dict[str, Any],
    bounded_probe: dict[str, Any],
    *,
    gate: str = "api_read_write_burst",
) -> dict[str, Any]:
    """Bundle #21 runtime evidence and bounded-probe output without recording a gate."""
    normalized_gate = str(gate or "").strip()
    safe_gate = normalized_gate if normalized_gate in LOAD_TEST_GATES else "unknown"
    snapshot = _extract_capacity_evidence_snapshot(runtime_evidence)
    probe_output = _dict(bounded_probe)
    input_errors: list[str] = []
    if not snapshot:
        input_errors.append("missing_capacity_evidence_snapshot")
    if safe_gate == "unknown":
        input_errors.append("unsupported_gate")
    probe_base_compatible = (
        probe_output.get("schema_version") == "ai-platform.capacity-bounded-load-harness.v1"
        and probe_output.get("gate") == safe_gate
        and probe_output.get("status") == "probe_completed_not_gate_evidence"
        and probe_output.get("load_test_evidence_status") == "probe_only_not_recorded"
        and probe_output.get("does_not_mark_gate_recorded") is True
    )
    probe_no_default_raise = probe_output.get("does_not_raise_defaults") is True
    probe_compatible = probe_base_compatible and probe_no_default_raise
    if not probe_base_compatible:
        input_errors.append("bounded_probe_not_completed_or_not_probe_only")
    if probe_base_compatible and not probe_no_default_raise:
        input_errors.append("bounded_probe_missing_no_default_raise_policy")

    candidate_observed_evidence = (
        _capacity_bundle_candidate_evidence(snapshot, probe_output)
        if snapshot and probe_compatible
        else {}
    )
    missing_required_evidence = [
        item for item in _LOAD_TEST_REQUIRED_EVIDENCE if item not in candidate_observed_evidence
    ]
    gate_evidence_path = f"load_test_evidence.gate_evidence.{safe_gate}"
    recorded_gate_evidence_draft = {
        "status": "draft_not_recorded",
        "gate": safe_gate,
        "gate_evidence_path": gate_evidence_path,
        "load_test_evidence_status": "probe_only_not_recorded",
        "evidence": candidate_observed_evidence,
        "cleanup_proof_status": _safe_status(
            probe_output.get("cleanup_proof_status"),
            set(_CLEANUP_PROOF_STATUS_VALUES),
        ),
        "stop_condition_status": _safe_status(
            probe_output.get("stop_condition_status"),
            set(_STOP_CONDITION_STATUS_VALUES),
        ),
        "triggered_stop_conditions": _safe_triggered_stop_conditions(
            probe_output.get("triggered_stop_conditions")
        ),
        "does_not_raise_defaults": True,
        "does_not_mark_gate_recorded": True,
    }

    if not candidate_observed_evidence:
        candidate_gate_status = "draft_not_available"
    elif (
        missing_required_evidence
        or recorded_gate_evidence_draft["cleanup_proof_status"] == "missing"
        or recorded_gate_evidence_draft["stop_condition_status"] == "missing"
        or recorded_gate_evidence_draft["triggered_stop_conditions"]
    ):
        candidate_gate_status = "draft_incomplete_not_recorded"
    else:
        candidate_gate_status = "draft_complete_not_recorded"
    candidate_gate_summary = {
        "gate": safe_gate,
        "status": candidate_gate_status,
        "observed_required_evidence": sorted(candidate_observed_evidence),
        "missing_required_evidence": missing_required_evidence,
        "cleanup_proof_status": recorded_gate_evidence_draft["cleanup_proof_status"],
        "stop_condition_status": recorded_gate_evidence_draft["stop_condition_status"],
        "triggered_stop_conditions": recorded_gate_evidence_draft["triggered_stop_conditions"],
        "does_not_mark_gate_recorded": True,
    }

    preview_snapshot = dict(snapshot) if snapshot else build_capacity_evidence_snapshot({})
    readiness_preview = build_capacity_gate_readiness(preview_snapshot)
    status = (
        "blocked_incomplete_inputs"
        if input_errors
        else "draft_ready_not_recorded"
        if candidate_gate_status == "draft_complete_not_recorded"
        else "blocked_incomplete_load_test_evidence"
    )
    return {
        "schema_version": CAPACITY_EVIDENCE_BUNDLE_SCHEMA,
        "status": status,
        "gate": safe_gate,
        "gate_evidence_path": gate_evidence_path,
        "input_status": {
            "runtime_evidence": "accepted" if snapshot else "missing_capacity_evidence_snapshot",
            "bounded_probe": (
                "probe_only_not_recorded" if probe_compatible else "not_accepted"
            ),
        },
        "input_errors": input_errors,
        "runtime_identity": readiness_preview["runtime_identity"],
        "candidate_observed_evidence": candidate_observed_evidence,
        "missing_required_evidence": missing_required_evidence,
        "candidate_gate_summary": candidate_gate_summary,
        "recorded_gate_evidence_draft": recorded_gate_evidence_draft,
        "readiness_preview": readiness_preview,
        "remaining_actions": [
            "collect missing measured evidence and artifact references",
            "record cleanup proof from the target runtime",
            "capture final runtime evidence after load and cooldown",
            "run tools/capacity_gate_readiness.py on the final recorded snapshot",
            "require operator review before any production default change",
        ],
        "capacity_answer": "safe_max_concurrency_unproven_without_recorded_load_test_evidence",
        "production_default_decision": "do_not_raise_without_recorded_load_test_evidence",
        "does_not_raise_defaults": True,
        "does_not_mark_gate_recorded": True,
    }


def build_capacity_gate_readiness(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a secret-safe #21 readiness verdict from a capacity evidence snapshot."""
    safe_snapshot = _dict(snapshot)
    runtime_identity = _dict(safe_snapshot.get("runtime_identity"))
    admin_runtime_evidence = _snapshot_admin_runtime_evidence(safe_snapshot)
    load_test_evidence = _dict(safe_snapshot.get("load_test_evidence"))
    required_gates = list(LOAD_TEST_GATES)
    gate_evidence_summary = _load_gate_evidence_summary(load_test_evidence, required_gates)
    missing_gates = gate_evidence_summary["missing_load_test_gates"]
    invalid_load_test_evidence = gate_evidence_summary["invalid_load_test_evidence"]
    missing_sections = list(admin_runtime_evidence["missing_sections"])
    if missing_sections:
        status = "blocked_missing_admin_runtime_sections"
    elif invalid_load_test_evidence:
        status = "blocked_incomplete_load_test_evidence"
    elif missing_gates:
        status = "blocked_missing_load_test_evidence"
    else:
        status = "ready_for_operator_review"
    production_defaults_blocked = bool(missing_gates or invalid_load_test_evidence or missing_sections)
    return {
        "schema_version": "ai-platform.capacity-gate-readiness.v1",
        "status": status,
        "runtime_identity": {
            "commit_sha": _safe_identity(runtime_identity.get("commit_sha")),
            "profile": _safe_identity(runtime_identity.get("profile")),
        },
        "admin_runtime_evidence": admin_runtime_evidence,
        "load_test_evidence_status": _safe_identity(load_test_evidence.get("status") or "missing"),
        "load_test_gates": [
            {
                "gate": gate,
                "status": gate_evidence_summary["statuses"][gate],
            }
            for gate in required_gates
        ],
        "missing_load_test_gates": missing_gates,
        "invalid_load_test_evidence": invalid_load_test_evidence,
        "capacity_answer": "safe_max_concurrency_unproven_without_recorded_load_test_evidence"
        if production_defaults_blocked
        else "operator_review_required_before_default_change",
        "production_default_decision": "do_not_raise_without_recorded_load_test_evidence"
        if production_defaults_blocked
        else "operator_review_required_before_default_change",
    }


def _safe_gate_readiness_status(value: object) -> str:
    text = str(value or "").strip()
    return text if text in _CAPACITY_GATE_READINESS_STATUSES else "blocked_missing_load_test_evidence"


def _safe_gate_status(value: object) -> str:
    text = str(value or "").strip()
    allowed = {
        "missing_recorded_load_test_evidence",
        "incomplete_recorded_load_test_evidence",
        "recorded",
    }
    return text if text in allowed else "missing_recorded_load_test_evidence"


def _safe_capacity_gate_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    runtime_identity = _dict(readiness.get("runtime_identity"))
    admin_runtime_evidence = _dict(readiness.get("admin_runtime_evidence"))
    required_sections_source = admin_runtime_evidence.get("required_sections")
    required_sections_complete = (
        isinstance(required_sections_source, list)
        and set(required_sections_source) == set(_LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS)
    )
    load_test_gates_source = readiness.get("load_test_gates")
    load_test_gates_complete = isinstance(load_test_gates_source, list)
    gate_status_by_name: dict[str, str] = {}
    if isinstance(load_test_gates_source, list):
        for item in load_test_gates_source:
            gate_item = _dict(item)
            gate = str(gate_item.get("gate") or "").strip()
            if gate in LOAD_TEST_GATES:
                gate_status_by_name[gate] = _safe_gate_status(gate_item.get("status"))
    load_test_gates_complete = load_test_gates_complete and set(gate_status_by_name) == set(LOAD_TEST_GATES)

    missing_gates_source = readiness.get("missing_load_test_gates")
    missing_gates_complete = isinstance(missing_gates_source, list)
    reported_missing_gates = {
        gate for gate in missing_gates_source if gate in LOAD_TEST_GATES
    } if isinstance(missing_gates_source, list) else set()
    missing_gates = [
        gate
        for gate in LOAD_TEST_GATES
        if gate in reported_missing_gates or gate_status_by_name.get(gate) != "recorded"
    ]

    missing_sections_source = admin_runtime_evidence.get("missing_sections")
    missing_sections_complete = isinstance(missing_sections_source, list)
    missing_sections = [
        section
        for section in missing_sections_source
        if section in _LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS
    ] if isinstance(missing_sections_source, list) else []
    if not required_sections_complete or not missing_sections_complete:
        missing_sections = list(_LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS)

    invalid_source = readiness.get("invalid_load_test_evidence")
    invalid_load_test_evidence_complete = isinstance(invalid_source, list)
    invalid_load_test_evidence: list[dict[str, object]] = []
    if isinstance(invalid_source, list):
        for item in invalid_source:
            invalid = _dict(item)
            gate = str(invalid.get("gate") or "").strip()
            if gate not in LOAD_TEST_GATES:
                continue
            missing_required_source = invalid.get("missing_required_evidence")
            missing_required = [
                field for field in missing_required_source if field in _LOAD_TEST_REQUIRED_EVIDENCE
            ] if isinstance(missing_required_source, list) else []
            triggered = _safe_triggered_stop_conditions(invalid.get("triggered_stop_conditions"))
            invalid_load_test_evidence.append(
                {
                    "gate": gate,
                    "missing_required_evidence": missing_required,
                    "cleanup_proof_status": _safe_status(
                        invalid.get("cleanup_proof_status"),
                        set(_CLEANUP_PROOF_STATUS_VALUES),
                    ),
                    "stop_condition_status": _safe_status(
                        invalid.get("stop_condition_status"),
                        set(_STOP_CONDITION_STATUS_VALUES),
                    ),
                    "triggered_stop_conditions": triggered,
                }
            )
    if not missing_gates_complete or not invalid_load_test_evidence_complete or not load_test_gates_complete:
        missing_gates = list(LOAD_TEST_GATES)

    if missing_sections:
        status = "blocked_missing_admin_runtime_sections"
    elif invalid_load_test_evidence:
        status = "blocked_incomplete_load_test_evidence"
    elif missing_gates:
        status = "blocked_missing_load_test_evidence"
    else:
        status = "ready_for_operator_review"
    return {
        "schema_version": "ai-platform.capacity-gate-readiness.v1",
        "status": status,
        "runtime_identity": {
            "commit_sha": _safe_identity(runtime_identity.get("commit_sha")),
            "profile": _safe_identity(runtime_identity.get("profile")),
        },
        "admin_runtime_evidence": {
            "required_sections": list(_LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS),
            "missing_sections": missing_sections,
        },
        "load_test_gates": [
            {
                "gate": gate,
                "status": gate_status_by_name.get(gate, "missing_recorded_load_test_evidence"),
            }
            for gate in LOAD_TEST_GATES
        ],
        "missing_load_test_gates": missing_gates,
        "invalid_load_test_evidence": invalid_load_test_evidence,
        "production_default_decision": _safe_identity(
            readiness.get("production_default_decision") or "do_not_raise_without_recorded_load_test_evidence"
        ),
    }


def _build_or_normalize_capacity_gate_readiness(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") == "ai-platform.capacity-gate-readiness.v1":
        return _safe_capacity_gate_readiness(payload)
    return _safe_capacity_gate_readiness(build_capacity_gate_readiness(payload))


def build_capacity_profile_readiness(payload: dict[str, Any]) -> dict[str, Any]:
    """Build fail-closed capacity profile readiness without recommending concurrency numbers."""
    gate_readiness = _build_or_normalize_capacity_gate_readiness(_dict(payload))
    gate_status = _safe_gate_readiness_status(gate_readiness.get("status"))
    profile_status = _CAPACITY_PROFILE_STATUS_BY_GATE_STATUS[gate_status]
    production_default_decision = (
        "operator_review_required_before_default_change"
        if gate_status == "ready_for_operator_review"
        else "do_not_raise_without_recorded_load_test_evidence"
    )
    missing_gates = [
        gate for gate in gate_readiness.get("missing_load_test_gates", []) if gate in LOAD_TEST_GATES
    ]
    invalid_gates = [
        _dict(item).get("gate")
        for item in gate_readiness.get("invalid_load_test_evidence", [])
        if _dict(item).get("gate") in LOAD_TEST_GATES
    ]
    missing_sections = [
        section
        for section in _dict(gate_readiness.get("admin_runtime_evidence")).get("missing_sections", [])
        if section in _LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS
    ]
    profiles: list[dict[str, Any]] = []
    for profile in _CAPACITY_PROFILE_CATALOG:
        profiles.append(
            {
                "id": profile["id"],
                "label": profile["label"],
                "intent": profile["intent"],
                "status": profile_status,
                "required_admin_runtime_sections": list(_LOAD_TEST_REQUIRED_ADMIN_RUNTIME_SECTIONS),
                "required_load_test_gates": list(profile["required_load_test_gates"]),
                "missing_admin_runtime_sections": missing_sections,
                "missing_load_test_gates": missing_gates,
                "invalid_load_test_gates": invalid_gates,
                "profile_specific_requirements": list(profile["profile_specific_requirements"]),
                "safe_concurrency_claim": "not_claimed",
                "automatic_default_raise": False,
                "production_default_decision": production_default_decision,
                "does_not_raise_defaults": True,
            }
        )

    return {
        "schema_version": "ai-platform.capacity-profile-readiness.v1",
        "status": profile_status,
        "source_gate_readiness": {
            "schema_version": gate_readiness["schema_version"],
            "status": gate_status,
            "runtime_identity": gate_readiness["runtime_identity"],
            "missing_admin_runtime_sections": missing_sections,
            "missing_load_test_gates": missing_gates,
            "invalid_load_test_gates": invalid_gates,
        },
        "profiles": profiles,
        "required_evidence": list(_LOAD_TEST_REQUIRED_EVIDENCE),
        "blocking_policy": (
            "operator_review_required_before_default_change; no automatic default raise"
            if gate_status == "ready_for_operator_review"
            else "do_not_raise_without_recorded_load_test_evidence; no automatic default raise"
        ),
        "production_default_decision": production_default_decision,
        "does_not_raise_defaults": True,
    }


def render_capacity_baseline_markdown(baseline: dict[str, Any]) -> str:
    """Render a capacity baseline snapshot as operator-readable Markdown."""
    limits = baseline["limits"]
    model_gateway_limit = limits["model_gateway"].get("request_concurrency_limit")
    configured_model_gateway_limit = limits["model_gateway"].get("configured_request_concurrency_limit")
    model_gateway_concurrency = (
        str(model_gateway_limit)
        if model_gateway_limit is not None
        else f"configured={configured_model_gateway_limit}; not enforced; load-test required"
        if configured_model_gateway_limit is not None
        else "unbounded by platform; load-test required"
    )
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
        ("Model gateway concurrency", model_gateway_concurrency),
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
    workflow_blocks = []
    for step in plan.get("operator_workflow", []):
        workflow_blocks.append(
            "\n".join(
                [
                    f"### {step['id']}",
                    "",
                    step["purpose"],
                    "",
                    "```powershell",
                    step["command"],
                    "```",
                    "",
                    f"Expected evidence: `{step['expected_evidence']}`",
                    "",
                    f"Requires explicit operator execution: `{str(step['requires_explicit_operator_execution']).lower()}`",
                    "",
                    f"Does not raise defaults: `{str(step['does_not_raise_defaults']).lower()}`",
                ]
            )
        )
    scenario_blocks = []
    for item in plan["scenarios"]:
        contract = _dict(item.get("recorded_gate_evidence_contract"))
        required_fields = [
            str(field.get("name"))
            for field in contract.get("required_evidence", [])
            if isinstance(field, dict) and field.get("name")
        ]
        required_field_text = ", ".join(f"`{field}`" for field in required_fields)
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
                    "",
                    "Admin Runtime sections: "
                    + ", ".join(f"`{section}`" for section in item["required_admin_runtime_sections"]),
                    "",
                    f"Recorded gate evidence path: `{contract.get('gate_evidence_path', 'missing')}`",
                    "",
                    f"Required recorded evidence fields: {required_field_text}",
                ]
            )
        )
    evidence = "\n".join(f"- {item}" for item in plan["required_evidence"])
    stop_conditions = "\n".join(f"- {item}" for item in plan["stop_conditions"])
    workflow = "\n\n".join(workflow_blocks)
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
        "## Operator Workflow\n\n"
        f"{workflow}\n\n"
        "## Scenarios\n\n"
        f"{scenarios}\n\n"
        "## Required Evidence\n\n"
        f"{evidence}\n\n"
        "## Stop Conditions\n\n"
        f"{stop_conditions}\n\n"
        "## Cleanup Policy\n\n"
        f"{plan['cleanup_policy']}\n"
    )


def render_capacity_evidence_snapshot_markdown(snapshot: dict[str, Any]) -> str:
    """Render a #21 evidence snapshot as operator-readable Markdown."""
    live = snapshot["live_signals"]
    queue = live["queue"]
    database_pool = live["database_pool"]
    admission = live["admission"]
    sandbox = live["sandbox"]
    backpressure = live["backpressure"]
    reasons = ", ".join(backpressure["reasons"]) or "none"
    return (
        "# ai-platform Capacity Evidence Snapshot\n\n"
        f"Schema: `{snapshot['schema_version']}`\n\n"
        f"Commit: `{snapshot['runtime_identity']['commit_sha']}`\n\n"
        f"Runtime profile: `{snapshot['runtime_identity']['profile']}`\n\n"
        f"Load-test evidence: `{snapshot['load_test_evidence']['status']}`\n\n"
        "| Signal | Value |\n"
        "| --- | --- |\n"
        f"| Queued depth | `{_coerce_int(queue['depths'].get('queued'))}` |\n"
        f"| Processing depth | `{_coerce_int(queue['depths'].get('processing'))}` |\n"
        f"| Active worker heartbeats | `{_coerce_int(queue.get('active_worker_heartbeats'))}` |\n"
        f"| DB waiting requests | `{_coerce_int(database_pool.get('requests_waiting'))}` |\n"
        f"| Active runs | `{_coerce_int(admission.get('active_runs'))}` |\n"
        f"| Saturated users | `{_coerce_int(admission.get('saturated_users'))}` |\n"
        f"| Running sandbox containers | `{_coerce_int(sandbox.get('running_containers'))}` |\n"
        f"| Active sandbox leases | `{_coerce_int(sandbox.get('active_leases'))}` |\n"
        f"| Backpressure reasons | `{reasons}` |\n\n"
        "## Production Default Decision\n\n"
        "Do not raise production concurrency defaults without recorded load-test evidence.\n"
    )


def render_capacity_gate_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render a #21 capacity gate readiness verdict as operator-readable Markdown."""
    missing_sections = "\n".join(
        f"- {section}" for section in readiness["admin_runtime_evidence"]["missing_sections"]
    ) or "- none"
    missing_gates = "\n".join(f"- {gate}" for gate in readiness["missing_load_test_gates"]) or "- none"
    invalid_items = []
    for item in readiness.get("invalid_load_test_evidence", []):
        invalid = _dict(item)
        gate = invalid.get("gate")
        missing_required = invalid.get("missing_required_evidence")
        first_missing = missing_required[0] if isinstance(missing_required, list) and missing_required else "none"
        triggered = invalid.get("triggered_stop_conditions")
        triggered_text = ", ".join(triggered) if isinstance(triggered, list) and triggered else "none"
        invalid_items.append(
            (
                f"- `{gate}` missing `{first_missing}`; "
                f"cleanup=`{invalid.get('cleanup_proof_status', 'missing')}`; "
                f"stop_conditions=`{invalid.get('stop_condition_status', 'missing')}`; "
                f"triggered=`{triggered_text}`"
            )
        )
    incomplete_evidence = "\n".join(invalid_items) or "- none"
    gate_rows = "\n".join(
        f"| {item['gate']} | `{item['status']}` |"
        for item in readiness["load_test_gates"]
    )
    return (
        "# ai-platform Capacity Gate Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Commit: `{readiness['runtime_identity']['commit_sha']}`\n\n"
        f"Runtime profile: `{readiness['runtime_identity']['profile']}`\n\n"
        "## Missing Admin Runtime Sections\n\n"
        f"{missing_sections}\n\n"
        "## Missing Load-Test Gates\n\n"
        f"{missing_gates}\n\n"
        "## Incomplete Load-Test Evidence\n\n"
        f"{incomplete_evidence}\n\n"
        "## Gate Status\n\n"
        "| Gate | Status |\n"
        "| --- | --- |\n"
        f"{gate_rows}\n\n"
        "## Production Default Decision\n\n"
        "Do not raise production concurrency defaults without recorded load-test evidence.\n"
    )


def render_capacity_evidence_bundle_markdown(bundle: dict[str, Any]) -> str:
    """Render a #21 capacity evidence bundle draft as operator-readable Markdown."""
    missing = "\n".join(
        f"- {item}" for item in bundle.get("missing_required_evidence", [])
    ) or "- none"
    actions = "\n".join(f"- {item}" for item in bundle.get("remaining_actions", [])) or "- none"
    candidate = _dict(bundle.get("candidate_observed_evidence"))
    candidate_rows = "\n".join(
        f"| {key} | `present` |" for key in sorted(candidate)
    ) or "| none | `missing` |"
    preview = _dict(bundle.get("readiness_preview"))
    return (
        "# ai-platform Capacity Evidence Bundle\n\n"
        f"Schema: `{bundle['schema_version']}`\n\n"
        f"Status: `{bundle['status']}`\n\n"
        f"Gate: `{bundle['gate']}`\n\n"
        f"Gate evidence path: `{bundle['gate_evidence_path']}`\n\n"
        f"Readiness preview: `{preview.get('status', 'unknown')}`\n\n"
        f"Does not mark gate recorded: `{str(bundle['does_not_mark_gate_recorded']).lower()}`\n\n"
        "This bundle is an operator draft assembled from runtime evidence and a "
        "bounded probe. It is not recorded gate evidence and must not raise "
        "production concurrency defaults.\n\n"
        "## Candidate Observed Evidence\n\n"
        "| Field | Status |\n"
        "| --- | --- |\n"
        f"{candidate_rows}\n\n"
        "## Missing Required Evidence\n\n"
        f"{missing}\n\n"
        "## Remaining Actions\n\n"
        f"{actions}\n\n"
        "## Production Default Decision\n\n"
        "Do not raise production concurrency defaults without recorded load-test evidence.\n"
    )


def render_capacity_profile_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render capacity profile readiness as operator-readable Markdown."""
    source = _dict(readiness.get("source_gate_readiness"))
    missing_sections = "\n".join(
        f"- {section}" for section in source.get("missing_admin_runtime_sections", [])
    ) or "- none"
    missing_gates = "\n".join(
        f"- {gate}" for gate in source.get("missing_load_test_gates", [])
    ) or "- none"
    rows = []
    for profile in readiness.get("profiles", []):
        item = _dict(profile)
        profile_missing_gates = item.get("missing_load_test_gates")
        missing_count = len(profile_missing_gates) if isinstance(profile_missing_gates, list) else 0
        rows.append(
            (
                f"| {item.get('id', 'unknown')} | `{item.get('status', 'unknown')}` | "
                f"`{item.get('production_default_decision', 'unknown')}` | `{missing_count}` |"
            )
        )
    profile_rows = "\n".join(rows)
    return (
        "# ai-platform Capacity Profile Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Source gate readiness: `{source.get('status', 'unknown')}`\n\n"
        "## Missing Admin Runtime Sections\n\n"
        f"{missing_sections}\n\n"
        "## Missing Load-Test Gates\n\n"
        f"{missing_gates}\n\n"
        "## Profiles\n\n"
        "| Profile | Status | Production default decision | Missing gates |\n"
        "| --- | --- | --- | --- |\n"
        f"{profile_rows}\n\n"
        "## Policy\n\n"
        "No profile claims a safe concurrency number. Do not raise production "
        "concurrency defaults without recorded load-test evidence and operator review.\n"
    )
