import json

from app.capacity_baseline import (
    build_capacity_baseline,
)


class SecretBearingSettings:
    database_url = "postgresql://user:super-secret-password@db.internal/ai_platform"
    redis_url = "redis://:redis-secret@redis.internal:6379/0"
    database_pool_min_size = 1
    database_pool_max_size = 10
    database_pool_timeout_seconds = 10.0
    database_pool_max_waiting = 100
    max_active_worker_runs = 3
    max_active_runs_per_user = 3
    queue_tenant_processing_limit = 0
    queue_user_processing_limit = 0
    queue_lease_scan_limit = 50
    queue_insight_scan_limit = 500
    queue_metadata_fallback_scan_limit = 500
    sandbox_container_provider = "fake"
    sandbox_container_start_timeout_seconds = 30
    sandbox_executor_health_timeout_seconds = 60
    multi_agent_dispatch_worker_enabled = False
    multi_agent_dispatch_worker_limit = 1
    llm_gateway_provider = "openai_compatible"
    openai_base_url = "https://model-gateway.internal/v1"
    openai_api_key = "sk-secret"


def test_capacity_baseline_records_defaults_without_secret_like_settings():
    baseline = build_capacity_baseline(SecretBearingSettings())

    assert baseline["schema_version"] == "ai-platform.capacity-baseline.v1"
    assert baseline["profile"] == "unproven_default"
    assert baseline["limits"]["worker"]["max_active_worker_runs"] == 3
    assert baseline["limits"]["admission"]["max_active_runs_per_user"] == 3
    assert baseline["limits"]["queue"]["tenant_processing_limit"] == 0
    assert baseline["limits"]["queue"]["tenant_processing_quota_enabled"] is False
    assert baseline["limits"]["database_pool"]["max_size"] == 10
    assert baseline["limits"]["sandbox"]["container_provider"] == "fake"
    assert "max_active_ephemeral_containers" not in baseline["limits"]["sandbox"]
    assert "max_active_persistent_containers" not in baseline["limits"]["sandbox"]
    assert baseline["limits"]["model_gateway"]["request_concurrency_limit"] is None
    assert baseline["model_gateway_backpressure_policy"] == {
        "schema_version": "ai-platform.model-gateway-backpressure-policy.v1",
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
    assert baseline["production_default_policy"] == "do_not_raise_without_recorded_load_test_evidence"
    assert "model_gateway_concurrency_unbounded_by_platform" in baseline["warnings"]
    assert "queue_tenant_processing_quota_disabled" in baseline["warnings"]

    serialized = json.dumps(baseline, ensure_ascii=False).lower()
    assert "super-secret-password" not in serialized
    assert "redis-secret" not in serialized
    assert "sk-secret" not in serialized
    assert "database_url" not in serialized
    assert "redis_url" not in serialized
    assert "openai_api_key" not in serialized
    assert "model-gateway.internal" not in serialized


def test_capacity_baseline_ignores_retired_model_gateway_limit_without_load_claims():
    class ConfiguredGatewaySettings(SecretBearingSettings):
        model_gateway_request_concurrency_limit = 12

    baseline = build_capacity_baseline(ConfiguredGatewaySettings())

    assert baseline["limits"]["model_gateway"] == {
        "provider": "openai_compatible",
        "request_concurrency_limit": None,
        "configured_request_concurrency_limit": None,
        "limit_enforcement": "not_implemented",
        "capacity_evidence": "unproven_without_load_test",
    }
    assert "model_gateway_concurrency_unbounded_by_platform" in baseline["warnings"]
    assert "model_gateway_configured_limit_not_enforced" not in baseline["warnings"]
    assert baseline["production_default_policy"] == "do_not_raise_without_recorded_load_test_evidence"
    assert "model_gateway_timeout_and_backpressure" in baseline["load_test_gates"]


def test_capacity_baseline_sanitizes_misconfigured_provider_strings():
    class MisconfiguredProviderSettings(SecretBearingSettings):
        sandbox_container_provider = "docker://token@internal/path"
        llm_gateway_provider = "https://gateway.internal/v1?api_key=secret"

    baseline = build_capacity_baseline(MisconfiguredProviderSettings())

    assert baseline["limits"]["sandbox"]["container_provider"] == "unknown"
    assert baseline["limits"]["model_gateway"]["provider"] == "unknown"
    serialized = json.dumps(baseline, ensure_ascii=False).lower()
    assert "docker://token" not in serialized
    assert "gateway.internal" not in serialized
    assert "api_key" not in serialized


def test_capacity_baseline_keeps_sandbox_hardening_warning_for_docker_provider():
    class DockerProviderSettings(SecretBearingSettings):
        sandbox_container_provider = "docker"

    baseline = build_capacity_baseline(DockerProviderSettings())

    assert baseline["limits"]["sandbox"]["container_provider"] == "docker"
    assert "sandbox_provider_not_production_docker" not in baseline["warnings"]
    assert "sandbox_hardening_evidence_missing" in baseline["warnings"]
