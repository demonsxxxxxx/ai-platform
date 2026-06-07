import json
import os
import subprocess
import sys

from app.capacity_baseline import build_capacity_baseline, render_capacity_baseline_markdown


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
    sandbox_max_active_ephemeral_containers = 2
    sandbox_max_active_persistent_containers = 1
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
    assert baseline["limits"]["model_gateway"]["request_concurrency_limit"] is None
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


def test_render_capacity_baseline_markdown_is_operator_readable_and_safe():
    markdown = render_capacity_baseline_markdown(build_capacity_baseline(SecretBearingSettings()))

    assert "# ai-platform Capacity Baseline" in markdown
    assert "Active worker runs | 3" in markdown
    assert "DB pool max size | 10" in markdown
    assert "Do not raise production concurrency defaults" in markdown
    assert "super-secret-password" not in markdown
    assert "redis-secret" not in markdown
    assert "sk-secret" not in markdown


def test_capacity_baseline_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["MAX_ACTIVE_WORKER_RUNS"] = "7"
    result = subprocess.run(
        [sys.executable, "tools/capacity_baseline.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-baseline.v1"
    assert payload["limits"]["worker"]["max_active_worker_runs"] == 7
    assert "ai_platform_dev_password" not in result.stdout
    assert "database_url" not in result.stdout.lower()
