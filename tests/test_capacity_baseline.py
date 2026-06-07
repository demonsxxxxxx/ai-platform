import json
import os
import subprocess
import sys

from app.capacity_baseline import (
    LOAD_TEST_GATES,
    build_capacity_baseline,
    build_capacity_evidence_snapshot,
    build_capacity_load_test_plan,
    render_capacity_baseline_markdown,
    render_capacity_evidence_snapshot_markdown,
    render_capacity_load_test_plan_markdown,
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


def test_capacity_load_test_plan_covers_each_gate_with_dry_run_commands_and_no_default_raise():
    plan = build_capacity_load_test_plan(
        SecretBearingSettings(),
        base_url="https://ai-platform.internal",
        tenants=3,
        users_per_tenant=4,
        runs_per_user=2,
        duration_seconds=300,
    )

    assert plan["schema_version"] == "ai-platform.capacity-load-test-plan.v1"
    assert plan["execution_policy"] == {
        "default_mode": "dry_run_plan_only",
        "requires_explicit_operator_execution": True,
        "production_defaults_policy": "do_not_raise_without_recorded_load_test_evidence",
    }
    assert [scenario["gate"] for scenario in plan["scenarios"]] == LOAD_TEST_GATES
    assert all(scenario["command"].startswith("python tools/capacity_load_plan.py --dry-run") for scenario in plan["scenarios"])
    assert all("https://ai-platform.internal" in scenario["command"] for scenario in plan["scenarios"])
    assert "commit_sha" in plan["required_evidence"]
    assert "api_worker_image_labels" in plan["required_evidence"]
    assert "cleanup_proof" in plan["required_evidence"]
    assert "do_not_raise_concurrency_defaults" in plan["stop_conditions"]

    serialized = json.dumps(plan, ensure_ascii=False).lower()
    assert "super-secret-password" not in serialized
    assert "redis-secret" not in serialized
    assert "sk-secret" not in serialized
    assert "database_url" not in serialized
    assert "redis_url" not in serialized
    assert "openai_api_key" not in serialized


def test_render_capacity_load_test_plan_markdown_is_repeatable_and_safe():
    markdown = render_capacity_load_test_plan_markdown(
        build_capacity_load_test_plan(SecretBearingSettings(), base_url="http://127.0.0.1:8020")
    )

    assert "# ai-platform Capacity Load-Test Plan" in markdown
    assert "python tools/capacity_load_plan.py --dry-run --scenario api_read_write_burst" in markdown
    assert "Do not raise production concurrency defaults" in markdown
    assert "super-secret-password" not in markdown
    assert "redis-secret" not in markdown
    assert "sk-secret" not in markdown


def test_capacity_load_plan_cli_outputs_json_without_secret_markers():
    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_load_plan.py",
            "--format",
            "json",
            "--base-url",
            "https://ai-platform.internal",
            "--tenants",
            "2",
            "--users-per-tenant",
            "3",
            "--runs-per-user",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-load-test-plan.v1"
    assert len(payload["scenarios"]) == len(LOAD_TEST_GATES)
    assert payload["scenarios"][0]["parameters"] == {
        "tenants": 2,
        "users_per_tenant": 3,
        "runs_per_user": 1,
        "duration_seconds": 300,
    }
    assert "database_url" not in result.stdout.lower()
    assert "openai_api_key" not in result.stdout.lower()


def test_capacity_load_test_plan_sanitizes_secret_like_base_url():
    plan = build_capacity_load_test_plan(
        SecretBearingSettings(),
        base_url="https://user:token@ai-platform.internal/api?api_key=secret#fragment",
    )

    serialized = json.dumps(plan, ensure_ascii=False).lower()
    assert "user:token" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized
    assert "fragment" not in serialized
    assert plan["base_url"] == "https://ai-platform.internal/api"
    assert "https://ai-platform.internal/api" in plan["scenarios"][0]["command"]


def test_capacity_evidence_snapshot_extracts_live_signals_without_private_payloads():
    capacity = build_capacity_baseline(SecretBearingSettings())
    capacity["database_url"] = "postgresql://user:capacity-secret@db.internal/platform"
    capacity["limits"]["database_pool"]["password"] = "capacity-password"
    capacity["warnings"].append("capacity-secret-warning")
    overview = {
        "tenant_id": "default",
        "capacity": capacity,
        "queue": {
            "status": {
                "depths": {"queued": 8, "processing": 3, "dead_letter": 1},
                "keys": {"queued": "ai-platform:queued", "raw_storage_key": "storage-secret"},
                "workers": ["worker-a"],
            },
            "tenant_insight": {
                "reason": "worker_capacity_full",
                "depths": {"tenant_queued": 5, "tenant_processing": 3},
                "capacity": {
                    "max_active_worker_runs": 3,
                    "available_worker_slots": 0,
                    "processing_saturated": True,
                    "queue_lease_scan_limit": 50,
                },
                "queue_sample": {
                    "queued_scan_limit": 500,
                    "queued_sampled": 8,
                    "queued_sample_complete": True,
                },
                "throttling": {
                    "tenant_processing_limit": 0,
                    "tenant_processing_saturated": False,
                    "user_processing_limit": 0,
                    "users": {"user-a": {"queued": 2, "processing": 1, "processing_saturated": False}},
                },
            },
        },
        "database_pool": {
            "configured": {"min_size": 1, "max_size": 10, "timeout_seconds": 10.0, "max_waiting": 100},
            "open": True,
            "stats": {"size": 4, "free": 1, "requests_waiting": 2, "token": "pool-secret"},
            "database_url": "postgresql://user:pool-secret@db.internal/platform",
        },
        "admission": {
            "policy_active": True,
            "max_active_runs_per_user": 3,
            "active_runs": 9,
            "active_users": 4,
            "saturated_users": 2,
            "top_users": [{"user_id": "user-secret", "active_runs": 3}],
        },
        "backpressure": {
            "reasons": ["worker_capacity_full", "database_pool_waiting"],
            "queue": {"raw_queue_payload": "token=queue-secret"},
        },
        "sandbox": {
            "containers": {"total": 2, "running": 1, "ephemeral_running": 1, "persistent_running": 0},
            "leases": {"active": 1, "released": 1, "expired": 0, "sandbox_workdir": "/tmp/work-secret"},
        },
        "observability": {
            "event_count": 12,
            "artifact_count": 3,
            "error_count": 1,
            "latency_ms": {"avg": 123, "max": 456},
            "executor_private_payload": {"api_key": "sk-secret"},
        },
    }

    snapshot = build_capacity_evidence_snapshot(overview, commit_sha="abc123")

    assert snapshot["schema_version"] == "ai-platform.capacity-evidence-snapshot.v1"
    assert snapshot["runtime_identity"]["commit_sha"] == "abc123"
    assert snapshot["source"] == {
        "projection": "/api/ai/admin/runtime/overview",
        "mode": "operator_captured_admin_projection",
    }
    assert snapshot["capacity"]["schema_version"] == "ai-platform.capacity-baseline.v1"
    assert snapshot["live_signals"]["queue"]["depths"]["queued"] == 8
    assert snapshot["live_signals"]["queue"]["capacity"]["processing_saturated"] is True
    assert snapshot["live_signals"]["database_pool"]["requests_waiting"] == 2
    assert snapshot["live_signals"]["admission"]["saturated_users"] == 2
    assert snapshot["live_signals"]["sandbox"]["active_leases"] == 1
    assert snapshot["live_signals"]["observability"]["error_count"] == 1
    assert snapshot["load_test_evidence"]["status"] == "missing"
    assert snapshot["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"

    serialized = json.dumps(snapshot, ensure_ascii=False).lower()
    assert "pool-secret" not in serialized
    assert "queue-secret" not in serialized
    assert "work-secret" not in serialized
    assert "sk-secret" not in serialized
    assert "capacity-secret" not in serialized
    assert "capacity-password" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "executor_private_payload" not in serialized
    assert "database_url" not in serialized


def test_render_capacity_evidence_snapshot_markdown_is_operator_readable_and_safe():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 1, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 1, "saturated_users": 0},
            "backpressure": {"reasons": []},
        },
        commit_sha="abc123",
    )

    markdown = render_capacity_evidence_snapshot_markdown(snapshot)

    assert "# ai-platform Capacity Evidence Snapshot" in markdown
    assert "Commit: `abc123`" in markdown
    assert "Load-test evidence: `missing`" in markdown
    assert "Do not raise production concurrency defaults" in markdown
    assert "super-secret-password" not in markdown
    assert "redis-secret" not in markdown
    assert "sk-secret" not in markdown


def test_capacity_evidence_snapshot_cli_outputs_json_from_overview_file(tmp_path):
    overview_path = tmp_path / "overview.json"
    overview_path.write_text(
        json.dumps(
            {
                "capacity": build_capacity_baseline(SecretBearingSettings()),
                "queue": {"status": {"depths": {"queued": 2, "processing": 1, "dead_letter": 0}}},
                "database_pool": {"open": True, "stats": {"requests_waiting": 0, "token": "pool-secret"}},
                "admission": {"active_runs": 1, "saturated_users": 0},
                "backpressure": {"reasons": []},
                "executor_private_payload": {"api_key": "sk-secret"},
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_evidence_snapshot.py",
            "--overview-json",
            str(overview_path),
            "--commit-sha",
            "abc123",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-evidence-snapshot.v1"
    assert payload["runtime_identity"]["commit_sha"] == "abc123"
    assert payload["live_signals"]["queue"]["depths"]["queued"] == 2
    assert payload["load_test_evidence"]["status"] == "missing"
    assert "pool-secret" not in result.stdout
    assert "sk-secret" not in result.stdout
    assert "executor_private_payload" not in result.stdout
