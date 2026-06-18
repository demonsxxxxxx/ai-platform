import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.capacity_baseline import (
    LOAD_TEST_GATES,
    build_capacity_baseline,
    build_capacity_evidence_snapshot,
    build_capacity_gate_readiness,
    build_capacity_load_test_plan,
    build_capacity_evidence_bundle,
    build_capacity_profile_readiness,
    build_capacity_recorded_gate_snapshot,
    build_capacity_recorded_gate_evidence_contract,
    render_capacity_baseline_markdown,
    render_capacity_evidence_bundle_markdown,
    render_capacity_evidence_snapshot_markdown,
    render_capacity_gate_readiness_markdown,
    render_capacity_profile_readiness_markdown,
    render_capacity_load_test_plan_markdown,
)


LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST = [
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
    model_gateway_request_concurrency_limit = 0
    openai_base_url = "https://model-gateway.internal/v1"
    openai_api_key = "sk-secret"


def _admin_runtime_overview() -> dict[str, object]:
    return {
        "capacity": build_capacity_baseline(SecretBearingSettings()),
        "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
        "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
        "admission": {"active_runs": 0, "saturated_users": 0},
        "backpressure": {"reasons": []},
        "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
        "observability": {"event_count": 3, "error_count": 0},
    }


def _snapshot_with_complete_recorded_gates() -> dict[str, object]:
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
        "gate_evidence": {
            gate: {
                "evidence": {item: f"capacity-evidence/{gate}/{item}.json" for item in required_evidence},
                "cleanup_proof_status": "recorded",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
            for gate in LOAD_TEST_GATES
        },
    }
    return snapshot


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
    assert baseline["model_gateway_backpressure_policy"] == {
        "schema_version": "ai-platform.model-gateway-backpressure-policy.v1",
        "status": "contract_only_not_enforced",
        "config_signal": "MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT",
        "default_limit_policy": "0_disables_platform_request_limit",
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


def test_capacity_baseline_reports_configured_model_gateway_limit_without_load_claims():
    class ConfiguredGatewaySettings(SecretBearingSettings):
        model_gateway_request_concurrency_limit = 12

    baseline = build_capacity_baseline(ConfiguredGatewaySettings())

    assert baseline["limits"]["model_gateway"] == {
        "provider": "openai_compatible",
        "request_concurrency_limit": None,
        "configured_request_concurrency_limit": 12,
        "limit_enforcement": "not_implemented",
        "capacity_evidence": "unproven_without_load_test",
    }
    assert "model_gateway_concurrency_unbounded_by_platform" in baseline["warnings"]
    assert "model_gateway_configured_limit_not_enforced" in baseline["warnings"]
    assert "model_gateway_capacity_unproven_without_load_test" in baseline["warnings"]
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


def test_render_capacity_baseline_markdown_is_operator_readable_and_safe():
    markdown = render_capacity_baseline_markdown(build_capacity_baseline(SecretBearingSettings()))

    assert "# ai-platform Capacity Baseline" in markdown
    assert "Active worker runs | 3" in markdown
    assert "DB pool max size | 10" in markdown
    assert "ai-platform.model-gateway-backpressure-policy.v1" in markdown
    assert "model_gateway_timeout_and_backpressure" in markdown
    assert "Do not raise production concurrency defaults" in markdown
    assert "super-secret-password" not in markdown
    assert "redis-secret" not in markdown
    assert "sk-secret" not in markdown


def test_render_capacity_baseline_markdown_shows_configured_model_gateway_limit():
    class ConfiguredGatewaySettings(SecretBearingSettings):
        model_gateway_request_concurrency_limit = 12

    markdown = render_capacity_baseline_markdown(build_capacity_baseline(ConfiguredGatewaySettings()))

    assert "Model gateway concurrency | configured=12; not enforced; load-test required" in markdown
    assert "model_gateway_configured_limit_not_enforced" in markdown
    assert "model_gateway_capacity_unproven_without_load_test" in markdown


def test_capacity_baseline_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["MAX_ACTIVE_WORKER_RUNS"] = "7"
    env["MODEL_GATEWAY_REQUEST_CONCURRENCY_LIMIT"] = "11"
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
    assert payload["limits"]["model_gateway"]["request_concurrency_limit"] is None
    assert payload["limits"]["model_gateway"]["configured_request_concurrency_limit"] == 11
    assert payload["limits"]["model_gateway"]["limit_enforcement"] == "not_implemented"
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
    assert all("sandbox" in scenario["required_admin_runtime_sections"] for scenario in plan["scenarios"])
    assert all(
        scenario["recorded_gate_evidence_contract"]["gate_evidence_path"]
        == f"load_test_evidence.gate_evidence.{scenario['gate']}"
        for scenario in plan["scenarios"]
    )
    assert all(
        [
            field["name"]
            for field in scenario["recorded_gate_evidence_contract"]["required_evidence"]
        ]
        == plan["required_evidence"]
        for scenario in plan["scenarios"]
    )
    assert all(
        scenario["recorded_gate_evidence_contract"]["does_not_raise_defaults"] is True
        for scenario in plan["scenarios"]
    )
    assert all(
        set(scenario["required_admin_runtime_sections"])
        == {"capacity", "database_pool", "queue", "admission", "backpressure", "sandbox", "observability"}
        for scenario in plan["scenarios"]
    )
    assert "commit_sha" in plan["required_evidence"]
    assert "api_worker_image_labels" in plan["required_evidence"]
    assert "cleanup_proof" in plan["required_evidence"]
    assert "do_not_raise_concurrency_defaults" in plan["stop_conditions"]
    assert [step["id"] for step in plan["operator_workflow"]] == [
        "capture_start_runtime_evidence",
        "confirm_start_gate_status",
        "execute_bounded_load_scenario",
        "capture_end_runtime_evidence",
        "record_cleanup_proof",
        "assemble_evidence_bundle_draft",
        "assemble_recorded_gate_snapshot",
        "generate_gate_readiness_verdict",
    ]
    assert plan["operator_workflow"][0]["command"].startswith("python tools/capacity_runtime_evidence.py")
    assert "https://ai-platform.internal" in plan["operator_workflow"][0]["command"]
    assert "blocked_incomplete_load_test_evidence" in plan["operator_workflow"][1]["command"]
    assert plan["operator_workflow"][2]["requires_explicit_operator_execution"] is True
    assert plan["operator_workflow"][2]["does_not_raise_defaults"] is True
    assert "capacity_bounded_load_harness.py" in plan["operator_workflow"][2]["command"]
    assert "--gate api_read_write_burst" in plan["operator_workflow"][2]["command"]
    assert "capacity-bounded-load-harness-api-read-write-burst.json" in plan["operator_workflow"][2]["command"]
    assert "--gate queue_depth_and_lease_latency" not in plan["operator_workflow"][2]["command"]
    assert (
        "capacity-bounded-load-harness-queue-depth-and-lease-latency.json"
        not in plan["operator_workflow"][2]["command"]
    )
    assert "probe_only_not_recorded" in plan["operator_workflow"][2]["command"]
    assert "other gates require an approved harness extension" not in plan["operator_workflow"][2]["command"]
    assert plan["operator_workflow"][4]["requires_explicit_operator_execution"] is True
    assert plan["operator_workflow"][4]["expected_evidence"] == "capacity-cleanup-proof-api-read-write-burst.json"
    assert plan["operator_workflow"][5]["requires_explicit_operator_execution"] is False
    assert plan["operator_workflow"][5]["does_not_raise_defaults"] is True
    assert "capacity_evidence_bundle.py" in plan["operator_workflow"][5]["command"]
    assert "--start-runtime-evidence-json capacity-runtime-evidence-start.json" in plan["operator_workflow"][5]["command"]
    assert "capacity-runtime-evidence-end.json" in plan["operator_workflow"][5]["command"]
    assert "capacity-bounded-load-harness-api-read-write-burst.json" in plan["operator_workflow"][5]["command"]
    assert "--cleanup-proof-json capacity-cleanup-proof-api-read-write-burst.json" in plan["operator_workflow"][5]["command"]
    assert "capacity-evidence-bundle-api-read-write-burst.md" in plan["operator_workflow"][5]["command"]
    assert plan["operator_workflow"][6]["requires_explicit_operator_execution"] is False
    assert plan["operator_workflow"][6]["does_not_raise_defaults"] is True
    assert "capacity_recorded_gate_snapshot.py" in plan["operator_workflow"][6]["command"]
    assert "capacity-recorded-gate-evidence-api-read-write-burst.json" in plan["operator_workflow"][6]["command"]
    assert "capacity-evidence-snapshot-recorded-api-read-write-burst.json" in plan["operator_workflow"][6]["command"]
    assert "capacity_gate_readiness.py" in plan["operator_workflow"][-1]["command"]

    serialized = json.dumps(plan, ensure_ascii=False).lower()
    assert "super-secret-password" not in serialized
    assert "redis-secret" not in serialized
    assert "sk-secret" not in serialized
    assert "database_url" not in serialized
    assert "redis_url" not in serialized
    assert "openai_api_key" not in serialized


def test_capacity_load_test_plan_names_b3_10x4_sdk_subagent_target_profile_without_default_raise():
    plan = build_capacity_load_test_plan(
        SecretBearingSettings(),
        base_url="https://ai-platform.internal",
    )

    assert plan["target_profile"] == {
        "id": "b3_10x4_sdk_subagents",
        "stage": "B3",
        "concurrent_sessions": 10,
        "peak_sdk_subagents_per_session": 4,
        "measurement_first": True,
        "automatic_default_raise": False,
        "production_default_decision": "do_not_raise_without_recorded_load_test_evidence",
        "status_label_before_evidence": "local partial",
    }
    assert plan["parameters"]["tenants"] == 1
    assert plan["parameters"]["users_per_tenant"] == 10
    assert plan["parameters"]["runs_per_user"] == 1
    assert plan["parameters"]["peak_sdk_subagents_per_session"] == 4
    assert all(
        scenario["target_profile_id"] == "b3_10x4_sdk_subagents"
        for scenario in plan["scenarios"]
    )
    assert all(
        scenario["parameters"]["peak_sdk_subagents_per_session"] == 4
        for scenario in plan["scenarios"]
    )
    assert " --tenants 1" in plan["scenarios"][0]["command"]
    assert " --users-per-tenant 10" in plan["scenarios"][0]["command"]
    assert " --runs-per-user 1" in plan["scenarios"][0]["command"]
    assert "--peak-sdk-subagents-per-session 4" in plan["scenarios"][0]["command"]
    assert plan["execution_policy"]["production_defaults_policy"] == (
        "do_not_raise_without_recorded_load_test_evidence"
    )


def test_capacity_recorded_gate_evidence_contract_is_machine_readable_and_fail_closed():
    contract = build_capacity_recorded_gate_evidence_contract("queue_depth_and_lease_latency")

    assert contract["schema_version"] == "ai-platform.capacity-recorded-gate-evidence-contract.v1"
    assert contract["gate"] == "queue_depth_and_lease_latency"
    assert contract["valid_gate"] is True
    assert contract["load_test_evidence_status"] == "recorded"
    assert contract["recorded_gates_entry"] == "queue_depth_and_lease_latency"
    assert contract["gate_evidence_path"] == "load_test_evidence.gate_evidence.queue_depth_and_lease_latency"
    assert [field["name"] for field in contract["required_evidence"]] == LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST
    assert all(field["required"] is True for field in contract["required_evidence"])
    assert all("placeholder" in field["value_rule"] for field in contract["required_evidence"])
    assert contract["accepted_statuses"]["cleanup_proof_status"] == [
        "recorded",
        "passed",
        "verified",
        "complete",
    ]
    assert contract["accepted_statuses"]["stop_condition_status"] == [
        "passed",
        "not_triggered",
        "verified",
        "clear",
    ]
    assert contract["triggered_stop_conditions_rule"] == "must_be_empty_for_operator_review"
    assert contract["does_not_raise_defaults"] is True
    assert "do_not_submit_template_or_placeholder_values" in contract["operator_warnings"]

    invalid = build_capacity_recorded_gate_evidence_contract("not_a_gate")
    assert invalid["valid_gate"] is False
    assert invalid["gate"] == "unknown"
    assert invalid["gate_evidence_path"] == "load_test_evidence.gate_evidence.unknown"

    serialized = json.dumps(contract, ensure_ascii=False).lower()
    assert "database_url" not in serialized
    assert "redis_url" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "executor_private_payload" not in serialized


def test_capacity_load_test_plan_uses_selected_gate_in_operator_workflow():
    plan = build_capacity_load_test_plan(
        SecretBearingSettings(),
        base_url="https://ai-platform.internal",
        scenario="queue_depth_and_lease_latency",
    )

    workflow = {step["id"]: step for step in plan["operator_workflow"]}

    assert [scenario["gate"] for scenario in plan["scenarios"]] == [
        "queue_depth_and_lease_latency",
    ]
    assert "--gate queue_depth_and_lease_latency" in workflow["execute_bounded_load_scenario"]["command"]
    assert (
        "capacity-bounded-load-harness-queue-depth-and-lease-latency.json"
        in workflow["execute_bounded_load_scenario"]["command"]
    )
    assert "--gate api_read_write_burst" not in workflow["execute_bounded_load_scenario"]["command"]
    assert (
        "capacity-bounded-load-harness-api-read-write-burst.json"
        not in workflow["execute_bounded_load_scenario"]["command"]
    )
    assert workflow["record_cleanup_proof"]["expected_evidence"] == (
        "capacity-cleanup-proof-queue-depth-and-lease-latency.json"
    )
    assert (
        "capacity-bounded-load-harness-queue-depth-and-lease-latency.json"
        in workflow["assemble_evidence_bundle_draft"]["command"]
    )
    assert "--gate queue_depth_and_lease_latency" in workflow["assemble_evidence_bundle_draft"]["command"]
    assert (
        "capacity-evidence-bundle-queue-depth-and-lease-latency.md"
        in workflow["assemble_evidence_bundle_draft"]["command"]
    )
    assert (
        "capacity-recorded-gate-evidence-queue-depth-and-lease-latency.json"
        in workflow["assemble_recorded_gate_snapshot"]["command"]
    )
    assert "--gate queue_depth_and_lease_latency" in workflow["assemble_recorded_gate_snapshot"]["command"]
    assert (
        "capacity-evidence-snapshot-recorded-queue-depth-and-lease-latency.json"
        in workflow["generate_gate_readiness_verdict"]["command"]
    )


def test_capacity_load_test_plan_uses_model_gateway_selected_gate_in_operator_workflow():
    plan = build_capacity_load_test_plan(
        SecretBearingSettings(),
        base_url="https://ai-platform.internal",
        scenario="model_gateway_timeout_and_backpressure",
    )

    workflow = {step["id"]: step for step in plan["operator_workflow"]}

    assert [scenario["gate"] for scenario in plan["scenarios"]] == [
        "model_gateway_timeout_and_backpressure",
    ]
    assert "--gate model_gateway_timeout_and_backpressure" in workflow["execute_bounded_load_scenario"]["command"]
    assert (
        "capacity-bounded-load-harness-model-gateway-timeout-and-backpressure.json"
        in workflow["execute_bounded_load_scenario"]["command"]
    )
    assert "--gate api_read_write_burst" not in workflow["execute_bounded_load_scenario"]["command"]
    assert (
        "capacity-bounded-load-harness-api-read-write-burst.json"
        not in workflow["execute_bounded_load_scenario"]["command"]
    )
    assert workflow["record_cleanup_proof"]["expected_evidence"] == (
        "capacity-cleanup-proof-model-gateway-timeout-and-backpressure.json"
    )
    assert (
        "capacity-bounded-load-harness-model-gateway-timeout-and-backpressure.json"
        in workflow["assemble_evidence_bundle_draft"]["command"]
    )
    assert "--gate model_gateway_timeout_and_backpressure" in workflow["assemble_evidence_bundle_draft"]["command"]
    assert (
        "capacity-evidence-bundle-model-gateway-timeout-and-backpressure.md"
        in workflow["assemble_evidence_bundle_draft"]["command"]
    )
    assert (
        "capacity-recorded-gate-evidence-model-gateway-timeout-and-backpressure.json"
        in workflow["assemble_recorded_gate_snapshot"]["command"]
    )
    assert "--gate model_gateway_timeout_and_backpressure" in workflow["assemble_recorded_gate_snapshot"]["command"]
    assert (
        "capacity-evidence-snapshot-recorded-model-gateway-timeout-and-backpressure.json"
        in workflow["generate_gate_readiness_verdict"]["command"]
    )


def test_render_capacity_load_test_plan_markdown_is_repeatable_and_safe():
    markdown = render_capacity_load_test_plan_markdown(
        build_capacity_load_test_plan(SecretBearingSettings(), base_url="http://127.0.0.1:8020")
    )

    assert "# ai-platform Capacity Load-Test Plan" in markdown
    assert "## Operator Workflow" in markdown
    assert "capture_start_runtime_evidence" in markdown
    assert "capture_end_runtime_evidence" in markdown
    assert "assemble_evidence_bundle_draft" in markdown
    assert "assemble_recorded_gate_snapshot" in markdown
    assert "record_cleanup_proof" in markdown
    assert "## Target Profile" in markdown
    assert "`b3_10x4_sdk_subagents`" in markdown
    assert "Stage: `B3`" in markdown
    assert "Concurrent sessions: `10`" in markdown
    assert "Peak SDK subagents per session: `4`" in markdown
    assert "Measurement first: `true`" in markdown
    assert "capacity_bounded_load_harness.py" in markdown
    assert "capacity_evidence_bundle.py" in markdown
    assert "capacity_recorded_gate_snapshot.py" in markdown
    assert "--start-runtime-evidence-json capacity-runtime-evidence-start.json" in markdown
    assert "--cleanup-proof-json capacity-cleanup-proof-api-read-write-burst.json" in markdown
    assert "probe_only_not_recorded" in markdown
    assert "capacity_gate_readiness.py" in markdown
    assert "python tools/capacity_load_plan.py --dry-run --scenario api_read_write_burst" in markdown
    assert "Recorded gate evidence path: `load_test_evidence.gate_evidence.api_read_write_burst`" in markdown
    assert "Required recorded evidence fields: `commit_sha`, `api_worker_image_labels`" in markdown
    assert "Admin Runtime sections: `capacity`, `database_pool`, `queue`, `admission`, `backpressure`, `sandbox`, `observability`" in markdown
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
        "peak_sdk_subagents_per_session": 4,
        "duration_seconds": 300,
    }
    assert payload["target_profile"]["id"] == "b3_10x4_sdk_subagents"
    assert payload["target_profile"]["automatic_default_raise"] is False
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
            "error_categories": {"executor": 1, "sandbox": 2, "api_key": 99},
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
    assert snapshot["live_signals"]["observability"]["error_categories"] == {
        "executor": 1,
        "sandbox": 2,
    }
    assert snapshot["load_test_evidence"]["status"] == "missing"
    assert snapshot["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert snapshot["admin_runtime_evidence"] == {
        "required_sections": ["capacity", "database_pool", "queue", "admission", "backpressure", "sandbox", "observability"],
        "observed_sections": ["capacity", "database_pool", "queue", "admission", "backpressure", "sandbox", "observability"],
        "missing_sections": [],
    }

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
    assert "sandbox" in payload["admin_runtime_evidence"]["missing_sections"]
    assert "pool-secret" not in result.stdout
    assert "sk-secret" not in result.stdout
    assert "executor_private_payload" not in result.stdout


def test_capacity_gate_readiness_blocks_default_raise_until_load_gates_are_recorded():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 1, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 1, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
            "executor_private_payload": {"api_key": "sk-secret"},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["schema_version"] == "ai-platform.capacity-gate-readiness.v1"
    assert readiness["status"] == "blocked_missing_load_test_evidence"
    assert readiness["runtime_identity"] == {"commit_sha": "abc123", "profile": "211-current"}
    assert readiness["admin_runtime_evidence"]["missing_sections"] == []
    assert readiness["missing_load_test_gates"] == LOAD_TEST_GATES
    assert {gate["gate"]: gate["status"] for gate in readiness["load_test_gates"]} == {
        gate: "missing_recorded_load_test_evidence" for gate in LOAD_TEST_GATES
    }
    assert readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "sk-secret" not in serialized
    assert "executor_private_payload" not in serialized


def test_capacity_gate_readiness_rejects_recorded_gates_without_evidence_contract():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["missing_load_test_gates"] == LOAD_TEST_GATES
    assert readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert {gate["gate"]: gate["status"] for gate in readiness["load_test_gates"]} == {
        gate: "incomplete_recorded_load_test_evidence" for gate in LOAD_TEST_GATES
    }
    assert [item["gate"] for item in readiness["invalid_load_test_evidence"]] == LOAD_TEST_GATES
    assert "commit_sha" in readiness["invalid_load_test_evidence"][0]["missing_required_evidence"]
    assert readiness["invalid_load_test_evidence"][0]["cleanup_proof_status"] == "missing"


def test_capacity_gate_readiness_does_not_trust_empty_or_partial_required_gates():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": [],
        "recorded_gates": [],
        "gate_evidence": {},
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_missing_load_test_evidence"
    assert [item["gate"] for item in readiness["load_test_gates"]] == LOAD_TEST_GATES
    assert readiness["missing_load_test_gates"] == LOAD_TEST_GATES
    assert readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"

    snapshot["load_test_evidence"]["required_gates"] = ["api_read_write_burst", "unknown_gate"]
    snapshot["load_test_evidence"]["recorded_gates"] = ["api_read_write_burst"]
    snapshot["load_test_evidence"]["gate_evidence"] = {
        "api_read_write_burst": {
            "evidence": {item: f"recorded-{item}" for item in required_evidence},
            "cleanup_proof_status": "recorded",
            "stop_condition_status": "passed",
            "triggered_stop_conditions": [],
        }
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_missing_load_test_evidence"
    assert [item["gate"] for item in readiness["load_test_gates"]] == LOAD_TEST_GATES
    assert "run_creation_burst_by_tenant_and_user" in readiness["missing_load_test_gates"]


def test_capacity_gate_readiness_requires_actual_evidence_payload_not_only_names():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
        "gate_evidence": {
            gate: {
                "required_evidence": list(required_evidence),
                "cleanup_proof_status": "recorded",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
            for gate in LOAD_TEST_GATES
        },
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["invalid_load_test_evidence"][0]["missing_required_evidence"] == required_evidence


def test_capacity_gate_readiness_blocks_unknown_triggered_stop_conditions():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
        "gate_evidence": {
            gate: {
                "evidence": {item: f"recorded-{item}" for item in required_evidence},
                "cleanup_proof_status": "recorded",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": ["unexpected_operator_abort"],
            }
            for gate in LOAD_TEST_GATES
        },
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["invalid_load_test_evidence"][0]["triggered_stop_conditions"] == [
        "unexpected_operator_abort"
    ]

    snapshot["load_test_evidence"]["gate_evidence"] = {
        gate: {
            "evidence": {item: f"recorded-{item}" for item in required_evidence},
            "cleanup_proof_status": "recorded",
            "stop_condition_status": "passed",
            "triggered_stop_conditions": "unexpected_operator_abort",
        }
        for gate in LOAD_TEST_GATES
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["invalid_load_test_evidence"][0]["triggered_stop_conditions"] == [
        "unexpected_operator_abort"
    ]

    snapshot["load_test_evidence"]["gate_evidence"] = {
        gate: {
            "evidence": {item: f"recorded-{item}" for item in required_evidence},
            "cleanup_proof_status": "recorded",
            "stop_condition_status": "passed",
            "triggered_stop_conditions": "unknown",
        }
        for gate in LOAD_TEST_GATES
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["invalid_load_test_evidence"][0]["triggered_stop_conditions"] == ["unknown"]


def test_capacity_gate_readiness_redacts_storage_key_stop_conditions_in_json_and_markdown():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"]["gate_evidence"] = {
        gate: {
            "evidence": {item: f"recorded-{item}" for item in required_evidence},
            "cleanup_proof_status": "recorded",
            "stop_condition_status": "passed",
            "triggered_stop_conditions": ["storage_key=tenants/default/private/object"],
        }
        for gate in LOAD_TEST_GATES
    }
    snapshot["load_test_evidence"]["status"] = "recorded"
    snapshot["load_test_evidence"]["recorded_gates"] = list(LOAD_TEST_GATES)

    readiness = build_capacity_gate_readiness(snapshot)
    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    markdown = render_capacity_gate_readiness_markdown(readiness).lower()

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["invalid_load_test_evidence"][0]["triggered_stop_conditions"] == ["redacted"]
    assert "storage_key" not in serialized
    assert "tenants/default/private/object" not in serialized
    assert "storage_key" not in markdown
    assert "tenants/default/private/object" not in markdown
    assert "triggered=`redacted`" in markdown

    snapshot["load_test_evidence"]["gate_evidence"] = {
        gate: {
            "evidence": {item: f"recorded-{item}" for item in required_evidence},
            "cleanup_proof_status": "recorded",
            "stop_condition_status": "passed",
            "triggered_stop_conditions": [
                {"type": "object_storage", "key": "tenants/default/private/object"}
            ],
        }
        for gate in LOAD_TEST_GATES
    }

    readiness = build_capacity_gate_readiness(snapshot)
    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    markdown = render_capacity_gate_readiness_markdown(readiness).lower()

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["invalid_load_test_evidence"][0]["triggered_stop_conditions"] == ["redacted"]
    assert "object_storage" not in serialized
    assert "tenants/default/private/object" not in serialized
    assert "object_storage" not in markdown
    assert "tenants/default/private/object" not in markdown
    assert "triggered=`redacted`" in markdown


def test_capacity_gate_readiness_accepts_complete_recorded_gate_evidence_for_operator_review():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
        "gate_evidence": {
            gate: {
                "evidence": {item: f"recorded-{item}" for item in required_evidence},
                "cleanup_proof_status": "recorded",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
            for gate in LOAD_TEST_GATES
        },
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "ready_for_operator_review"
    assert readiness["missing_load_test_gates"] == []
    assert readiness["invalid_load_test_evidence"] == []
    assert {gate["gate"]: gate["status"] for gate in readiness["load_test_gates"]} == {
        gate: "recorded" for gate in LOAD_TEST_GATES
    }
    assert readiness["production_default_decision"] == "operator_review_required_before_default_change"


def test_capacity_gate_readiness_rejects_placeholder_load_test_evidence():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
        "gate_evidence": {
            gate: {
                "evidence": {item: f"<{item}>" for item in required_evidence},
                "cleanup_proof_status": "recorded",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
            for gate in LOAD_TEST_GATES
        },
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert {gate["status"] for gate in readiness["load_test_gates"]} == {
        "incomplete_recorded_load_test_evidence"
    }
    first_invalid = readiness["invalid_load_test_evidence"][0]
    assert first_invalid["gate"] == "api_read_write_burst"
    assert first_invalid["missing_required_evidence"] == required_evidence


def test_capacity_gate_readiness_rejects_embedded_placeholder_load_test_evidence():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
        "gate_evidence": {
            gate: {
                "evidence": {
                    item: {
                        "artifact": f"artifact://capacity/{gate}/{item}=<{item}>",
                        "notes": [f"${{{item}}}"],
                    }
                    for item in required_evidence
                },
                "cleanup_proof_status": "recorded",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
            for gate in LOAD_TEST_GATES
        },
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "blocked_incomplete_load_test_evidence"
    assert {gate["status"] for gate in readiness["load_test_gates"]} == {
        "incomplete_recorded_load_test_evidence"
    }
    assert readiness["invalid_load_test_evidence"][0]["missing_required_evidence"] == required_evidence


def test_capacity_gate_readiness_accepts_real_ticket_named_artifact_refs():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
        runtime_profile="211-current",
    )
    required_evidence = snapshot["load_test_evidence"]["required_evidence"]
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
        "gate_evidence": {
            gate: {
                "evidence": {
                    item: f"capacity-evidence/TODO-123/{gate}/{item}.json"
                    for item in required_evidence
                },
                "cleanup_proof_status": "recorded",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
            for gate in LOAD_TEST_GATES
        },
    }

    readiness = build_capacity_gate_readiness(snapshot)

    assert readiness["status"] == "ready_for_operator_review"
    assert readiness["invalid_load_test_evidence"] == []


def test_capacity_profile_readiness_blocks_all_profiles_without_recorded_load_evidence():
    readiness = build_capacity_gate_readiness(
        build_capacity_evidence_snapshot(
            _admin_runtime_overview(),
            commit_sha="abc123",
            runtime_profile="211-current",
        )
    )

    profile_readiness = build_capacity_profile_readiness(readiness)

    assert profile_readiness["schema_version"] == "ai-platform.capacity-profile-readiness.v1"
    assert profile_readiness["status"] == "blocked_missing_load_test_evidence"
    assert profile_readiness["source_gate_readiness"]["status"] == "blocked_missing_load_test_evidence"
    assert [profile["id"] for profile in profile_readiness["profiles"]] == [
        "b3_10x4_sdk_subagents",
        "conservative_internal",
        "medium_team",
        "high_capacity_1t",
    ]
    assert {profile["status"] for profile in profile_readiness["profiles"]} == {
        "blocked_missing_load_test_evidence"
    }
    assert all(
        profile["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
        for profile in profile_readiness["profiles"]
    )
    assert all(profile["does_not_raise_defaults"] is True for profile in profile_readiness["profiles"])
    assert all(profile["automatic_default_raise"] is False for profile in profile_readiness["profiles"])
    assert all(profile["safe_concurrency_claim"] == "not_claimed" for profile in profile_readiness["profiles"])
    assert all(profile["missing_load_test_gates"] == LOAD_TEST_GATES for profile in profile_readiness["profiles"])
    target_profile = profile_readiness["profiles"][0]
    assert target_profile["stage"] == "B3"
    assert target_profile["target_profile"] == {
        "concurrent_sessions": 10,
        "peak_sdk_subagents_per_session": 4,
    }
    assert target_profile["measurement_first"] is True
    assert target_profile["safe_concurrency_claim"] == "not_claimed"


def test_capacity_profile_readiness_keeps_non_b3_profiles_reviewable_after_complete_gate_evidence():
    readiness = build_capacity_gate_readiness(_snapshot_with_complete_recorded_gates())

    profile_readiness = build_capacity_profile_readiness(readiness)

    assert readiness["status"] == "ready_for_operator_review"
    assert profile_readiness["status"] == "blocked_missing_profile_evidence"
    assert profile_readiness["production_default_decision"] == (
        "do_not_raise_without_recorded_load_test_evidence"
    )
    assert profile_readiness["profiles"][0]["status"] == "blocked_missing_profile_evidence"
    assert {
        profile["status"] for profile in profile_readiness["profiles"][1:]
    } == {"operator_review_required"}
    assert all(
        profile["production_default_decision"] == "operator_review_required_before_default_change"
        for profile in profile_readiness["profiles"][1:]
    )
    assert all(profile["automatic_default_raise"] is False for profile in profile_readiness["profiles"])
    assert all(profile["safe_concurrency_claim"] == "not_claimed" for profile in profile_readiness["profiles"])
    assert "b3_10x4_profile_evidence_missing" in profile_readiness["blocking_policy"]


def test_capacity_profile_readiness_blocks_b3_target_without_sdk_subagent_profile_evidence():
    readiness = build_capacity_gate_readiness(_snapshot_with_complete_recorded_gates())

    profile_readiness = build_capacity_profile_readiness(readiness)
    target_profile = profile_readiness["profiles"][0]

    assert readiness["status"] == "ready_for_operator_review"
    assert profile_readiness["status"] == "blocked_missing_profile_evidence"
    assert profile_readiness["production_default_decision"] == (
        "do_not_raise_without_recorded_load_test_evidence"
    )
    assert "b3_10x4_profile_evidence_missing" in profile_readiness["blocking_policy"]
    assert target_profile["id"] == "b3_10x4_sdk_subagents"
    assert target_profile["status"] == "blocked_missing_profile_evidence"
    assert target_profile["missing_profile_evidence"] == [
        "observed_concurrent_sessions",
        "observed_peak_sdk_subagents_per_session",
        "sdk_subagent_fanout_measurement_ref",
    ]
    assert target_profile["production_default_decision"] == (
        "do_not_raise_without_recorded_load_test_evidence"
    )


def test_capacity_profile_readiness_allows_b3_operator_review_with_sdk_subagent_profile_evidence():
    snapshot = _snapshot_with_complete_recorded_gates()
    snapshot["load_test_evidence"]["profile_evidence"] = {
        "b3_10x4_sdk_subagents": {
            "observed_concurrent_sessions": 10,
            "observed_peak_sdk_subagents_per_session": 4,
            "sdk_subagent_fanout_measurement_ref": "capacity-evidence/b3/sdk-subagent-fanout.json",
        }
    }
    readiness = build_capacity_gate_readiness(snapshot)

    profile_readiness = build_capacity_profile_readiness(readiness)
    target_profile = profile_readiness["profiles"][0]

    assert profile_readiness["status"] == "operator_review_required"
    assert target_profile["status"] == "operator_review_required"
    assert target_profile["profile_evidence_status"] == "accepted"
    assert target_profile["missing_profile_evidence"] == []
    assert target_profile["observed_profile_evidence"] == {
        "observed_concurrent_sessions": 10,
        "observed_peak_sdk_subagents_per_session": 4,
        "sdk_subagent_fanout_measurement_ref": "capacity-evidence/b3/sdk-subagent-fanout.json",
    }
    assert target_profile["safe_concurrency_claim"] == "not_claimed"


def test_capacity_profile_readiness_rejects_under_target_sdk_subagent_profile_evidence():
    snapshot = _snapshot_with_complete_recorded_gates()
    snapshot["load_test_evidence"]["profile_evidence"] = {
        "b3_10x4_sdk_subagents": {
            "observed_concurrent_sessions": 9,
            "observed_peak_sdk_subagents_per_session": 3,
            "sdk_subagent_fanout_measurement_ref": "capacity-evidence/b3/sdk-subagent-fanout.json",
        }
    }
    readiness = build_capacity_gate_readiness(snapshot)

    profile_readiness = build_capacity_profile_readiness(readiness)
    target_profile = profile_readiness["profiles"][0]

    assert profile_readiness["status"] == "blocked_missing_profile_evidence"
    assert target_profile["profile_evidence_status"] == "missing"
    assert target_profile["missing_profile_evidence"] == [
        "observed_concurrent_sessions",
        "observed_peak_sdk_subagents_per_session",
    ]
    assert target_profile["observed_profile_evidence"] == {}


def test_capacity_profile_readiness_recomputes_inconsistent_gate_readiness_fail_closed():
    inconsistent_gate_readiness = {
        "schema_version": "ai-platform.capacity-gate-readiness.v1",
        "status": "ready_for_operator_review",
        "runtime_identity": {"commit_sha": "abc123", "profile": "211-current"},
        "admin_runtime_evidence": {
            "required_sections": [
                "capacity",
                "database_pool",
                "queue",
                "admission",
                "backpressure",
                "sandbox",
                "observability",
            ],
            "missing_sections": [],
        },
        "load_test_gates": [
            {"gate": gate, "status": "missing_recorded_load_test_evidence"}
            for gate in LOAD_TEST_GATES
        ],
        "missing_load_test_gates": ["api_read_write_burst"],
        "invalid_load_test_evidence": [],
        "production_default_decision": "operator_review_required_before_default_change",
    }

    profile_readiness = build_capacity_profile_readiness(inconsistent_gate_readiness)

    assert profile_readiness["source_gate_readiness"]["status"] == "blocked_missing_load_test_evidence"
    assert profile_readiness["status"] == "blocked_missing_load_test_evidence"
    assert profile_readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert profile_readiness["profiles"][0]["missing_load_test_gates"] == LOAD_TEST_GATES


def test_capacity_profile_readiness_blocks_schema_incomplete_gate_readiness_json():
    incomplete_gate_readiness = {
        "schema_version": "ai-platform.capacity-gate-readiness.v1",
        "status": "ready_for_operator_review",
        "runtime_identity": {"commit_sha": "abc123", "profile": "211-current"},
        "load_test_gates": [
            {"gate": gate, "status": "recorded"}
            for gate in LOAD_TEST_GATES
        ],
        "production_default_decision": "operator_review_required_before_default_change",
    }

    profile_readiness = build_capacity_profile_readiness(incomplete_gate_readiness)

    assert profile_readiness["source_gate_readiness"]["status"] == "blocked_missing_admin_runtime_sections"
    assert profile_readiness["status"] == "blocked_missing_admin_runtime_sections"
    assert profile_readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert profile_readiness["source_gate_readiness"]["missing_admin_runtime_sections"] == [
        "capacity",
        "database_pool",
        "queue",
        "admission",
        "backpressure",
        "sandbox",
        "observability",
    ]


def test_capacity_profile_readiness_is_secret_safe_and_markdown_gap_first():
    snapshot = build_capacity_evidence_snapshot(
        {
            **_admin_runtime_overview(),
            "executor_private_payload": {"api_key": "sk-secret"},
            "raw_storage_key": "tenants/default/private/capacity.json",
            "sandbox_workdir": "/tmp/tenants/default/work",
        },
        commit_sha="token-secret",
        runtime_profile="profile-with-secret",
    )

    profile_readiness = build_capacity_profile_readiness(snapshot)
    markdown = render_capacity_profile_readiness_markdown(profile_readiness)

    assert "Status: `blocked_missing_load_test_evidence`" in markdown
    assert "conservative_internal" in markdown
    assert "Missing profile evidence" in markdown
    assert "observed_concurrent_sessions" in markdown
    assert "sdk_subagent_fanout_measurement_ref" in markdown
    serialized = json.dumps(profile_readiness, ensure_ascii=False).lower() + markdown.lower()
    assert "sk-secret" not in serialized
    assert "tenants/default/private" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "executor_private_payload" not in serialized
    assert "api_key" not in serialized
    assert "token-secret" not in serialized


def test_capacity_profile_readiness_cli_outputs_json_from_snapshot_file(tmp_path):
    snapshot_path = tmp_path / "capacity-snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            build_capacity_evidence_snapshot(
                _admin_runtime_overview(),
                commit_sha="abc123",
                runtime_profile="211-current",
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_profile_readiness.py",
            "--snapshot-json",
            str(snapshot_path),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-profile-readiness.v1"
    assert payload["status"] == "blocked_missing_load_test_evidence"
    assert [profile["id"] for profile in payload["profiles"]] == [
        "b3_10x4_sdk_subagents",
        "conservative_internal",
        "medium_team",
        "high_capacity_1t",
    ]
    assert payload["profiles"][0]["target_profile"] == {
        "concurrent_sessions": 10,
        "peak_sdk_subagents_per_session": 4,
    }
    assert payload["profiles"][0]["automatic_default_raise"] is False
    assert "executor_private_payload" not in result.stdout
    assert "storage_key" not in result.stdout


def test_render_capacity_gate_readiness_markdown_is_gap_first_and_safe():
    readiness = build_capacity_gate_readiness(
        build_capacity_evidence_snapshot(
            {
                "capacity": build_capacity_baseline(SecretBearingSettings()),
                "queue": {"status": {"depths": {"queued": 1, "processing": 0, "dead_letter": 0}}},
                "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
                "admission": {"active_runs": 1, "saturated_users": 0},
                "backpressure": {"reasons": []},
                "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
                "observability": {"event_count": 3, "error_count": 0},
            },
            commit_sha="abc123",
        )
    )

    markdown = render_capacity_gate_readiness_markdown(readiness)

    assert "# ai-platform Capacity Gate Readiness" in markdown
    assert "Status: `blocked_missing_load_test_evidence`" in markdown
    assert "- api_read_write_burst" in markdown
    assert "Do not raise production concurrency defaults" in markdown
    assert "super-secret-password" not in markdown
    assert "sk-secret" not in markdown


def test_render_capacity_gate_readiness_markdown_lists_incomplete_recorded_evidence():
    snapshot = build_capacity_evidence_snapshot(
        {
            "capacity": build_capacity_baseline(SecretBearingSettings()),
            "queue": {"status": {"depths": {"queued": 0, "processing": 0, "dead_letter": 0}}},
            "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
            "admission": {"active_runs": 0, "saturated_users": 0},
            "backpressure": {"reasons": []},
            "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
            "observability": {"event_count": 3, "error_count": 0},
        },
        commit_sha="abc123",
    )
    snapshot["load_test_evidence"] = {
        "status": "recorded",
        "required_gates": list(LOAD_TEST_GATES),
        "recorded_gates": list(LOAD_TEST_GATES),
    }

    markdown = render_capacity_gate_readiness_markdown(build_capacity_gate_readiness(snapshot))

    assert "Status: `blocked_incomplete_load_test_evidence`" in markdown
    assert "## Incomplete Load-Test Evidence" in markdown
    assert "`api_read_write_burst` missing `commit_sha`" in markdown
    assert "cleanup=`missing`" in markdown
    assert "stop_conditions=`missing`" in markdown


def test_capacity_gate_readiness_cli_outputs_json_from_snapshot_file(tmp_path):
    snapshot_path = tmp_path / "capacity-snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            build_capacity_evidence_snapshot(
                {
                    "capacity": build_capacity_baseline(SecretBearingSettings()),
                    "queue": {"status": {"depths": {"queued": 1, "processing": 0, "dead_letter": 0}}},
                    "database_pool": {"open": True, "stats": {"requests_waiting": 0}},
                    "admission": {"active_runs": 1, "saturated_users": 0},
                    "backpressure": {"reasons": []},
                    "sandbox": {"containers": {"running": 0}, "leases": {"active": 0}},
                    "observability": {"event_count": 3, "error_count": 0},
                    "executor_private_payload": {"api_key": "sk-secret"},
                },
                commit_sha="abc123",
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_gate_readiness.py",
            "--snapshot-json",
            str(snapshot_path),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-gate-readiness.v1"
    assert payload["status"] == "blocked_missing_load_test_evidence"
    assert payload["missing_load_test_gates"] == LOAD_TEST_GATES
    assert "sk-secret" not in result.stdout
    assert "executor_private_payload" not in result.stdout


def test_capacity_runtime_evidence_cli_captures_overview_without_printing_raw_private_payloads():
    requests: list[dict[str, str]] = []

    class OverviewHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            requests.append(
                {
                    "path": self.path,
                    "user_id": self.headers.get("X-AI-User-ID", ""),
                    "tenant_id": self.headers.get("X-AI-Tenant-ID", ""),
                    "roles": self.headers.get("X-AI-Roles", ""),
                }
            )
            payload = {
                "capacity": build_capacity_baseline(SecretBearingSettings()),
                "queue": {"status": {"depths": {"queued": 2, "processing": 1, "dead_letter": 0}}},
                "database_pool": {"open": True, "stats": {"requests_waiting": 0, "token": "pool-secret"}},
                "admission": {"active_runs": 2, "saturated_users": 0},
                "backpressure": {"reasons": []},
                "sandbox": {"containers": {"running": 0}, "leases": {"active": 0, "sandbox_workdir": "/tmp/work-secret"}},
                "observability": {"event_count": 5, "error_count": 0, "executor_private_payload": {"api_key": "sk-secret"}},
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), OverviewHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [
                sys.executable,
                "tools/capacity_runtime_evidence.py",
                "--base-url",
                base_url,
                "--user-id",
                "capacity-admin",
                "--tenant-id",
                "tenant-a",
                "--roles",
                "admin",
                "--commit-sha",
                "abc123",
                "--runtime-profile",
                "local-test",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-runtime-evidence.v1"
    assert payload["source"]["overview_route"] == "/api/ai/admin/runtime/overview"
    assert payload["source"]["http_status"] == 200
    assert payload["snapshot"]["schema_version"] == "ai-platform.capacity-evidence-snapshot.v1"
    assert payload["readiness"]["status"] == "blocked_missing_load_test_evidence"
    assert payload["readiness"]["missing_load_test_gates"] == LOAD_TEST_GATES
    assert requests == [
        {
            "path": "/api/ai/admin/runtime/overview",
            "user_id": "capacity-admin",
            "tenant_id": "tenant-a",
            "roles": "admin",
        }
    ]
    assert "pool-secret" not in result.stdout
    assert "work-secret" not in result.stdout
    assert "sk-secret" not in result.stdout
    assert "executor_private_payload" not in result.stdout


def test_capacity_evidence_bundle_drafts_probe_evidence_without_marking_gate_recorded():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current",
    )
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    probe_output = {
        "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
        "gate": "api_read_write_burst",
        "status": "probe_completed_not_gate_evidence",
        "load_test_evidence_status": "probe_only_not_recorded",
        "does_not_mark_gate_recorded": True,
        "does_not_raise_defaults": True,
        "sent_requests": 20,
        "http_status_counts": {"200": 20},
        "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
        "observed_admin_runtime_sections": [
            "admission",
            "backpressure",
            "capacity",
            "database_pool",
            "observability",
            "queue",
            "sandbox",
            "status",
        ],
        "cleanup_proof_status": "not_applicable_read_only_probe",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
        "executor_private_payload": {"api_key": "sk-secret"},
    }

    bundle = build_capacity_evidence_bundle(runtime_evidence, probe_output)

    assert bundle["schema_version"] == "ai-platform.capacity-evidence-bundle.v1"
    assert bundle["status"] == "blocked_incomplete_load_test_evidence"
    assert bundle["gate"] == "api_read_write_burst"
    assert bundle["gate_evidence_path"] == "load_test_evidence.gate_evidence.api_read_write_burst"
    assert bundle["does_not_raise_defaults"] is True
    assert bundle["does_not_mark_gate_recorded"] is True
    assert bundle["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert bundle["input_status"]["bounded_probe"] == "probe_only_not_recorded"
    assert bundle["candidate_observed_evidence"]["commit_sha"] == "3d607c96b8d8e21f59461bd94cc4b64de1d49dd5"
    assert bundle["candidate_observed_evidence"]["runtime_profile"] == "211-current"
    assert bundle["candidate_observed_evidence"]["latency_p50_p95_p99"] == {
        "count": 20,
        "p50": 72.864,
        "p95": 252.748,
        "p99": 283.726,
    }
    assert "cleanup_proof" in bundle["missing_required_evidence"]
    assert "api_worker_image_labels" in bundle["missing_required_evidence"]
    assert bundle["recorded_gate_evidence_draft"]["status"] == "draft_not_recorded"
    assert bundle["recorded_gate_evidence_draft"]["does_not_mark_gate_recorded"] is True
    assert bundle["candidate_gate_summary"]["status"] == "draft_incomplete_not_recorded"
    assert "cleanup_proof" in bundle["candidate_gate_summary"]["missing_required_evidence"]
    assert bundle["readiness_preview"]["status"] == "blocked_missing_load_test_evidence"
    assert bundle["readiness_preview"]["load_test_evidence_status"] == "missing"
    assert bundle["readiness_preview"]["load_test_gates"][0] == {
        "gate": "api_read_write_burst",
        "status": "missing_recorded_load_test_evidence",
    }

    serialized = json.dumps(bundle, ensure_ascii=False).lower()
    assert "sk-secret" not in serialized
    assert "executor_private_payload" not in serialized
    assert "api_key" not in serialized


def test_capacity_evidence_bundle_accepts_start_end_runtime_and_cleanup_proof_without_recording_gate():
    start_snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-start",
    )
    end_overview = _admin_runtime_overview()
    end_overview["queue"] = {"status": {"depths": {"queued": 1, "processing": 0, "dead_letter": 0}}}
    end_snapshot = build_capacity_evidence_snapshot(
        end_overview,
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    start_runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": start_snapshot,
        "readiness": build_capacity_gate_readiness(start_snapshot),
    }
    end_runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": end_snapshot,
        "readiness": build_capacity_gate_readiness(end_snapshot),
    }
    probe_output = {
        "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
        "gate": "api_read_write_burst",
        "status": "probe_completed_not_gate_evidence",
        "load_test_evidence_status": "probe_only_not_recorded",
        "does_not_mark_gate_recorded": True,
        "does_not_raise_defaults": True,
        "sent_requests": 20,
        "http_status_counts": {"200": 20},
        "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
        "cleanup_proof_status": "not_applicable_read_only_probe",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }
    cleanup_proof = {
        "schema_version": "ai-platform.capacity-cleanup-proof.v1",
        "gate": "api_read_write_burst",
        "status": "verified",
        "evidence_ref": "capacity-evidence/api-burst-cleanup.json",
        "test_tenants_removed": True,
        "queued_payloads_removed": True,
        "sandbox_leases_released": True,
        "temporary_artifacts_removed": True,
        "generated_documents_removed": True,
        "raw_storage_key": "tenants/default/private/capacity-cleanup.json",
        "executor_private_payload": {"api_key": "sk-secret"},
    }

    bundle = build_capacity_evidence_bundle(
        end_runtime_evidence,
        probe_output,
        start_runtime_evidence=start_runtime_evidence,
        cleanup_proof=cleanup_proof,
    )

    assert bundle["input_status"]["start_runtime_evidence"] == "accepted"
    assert bundle["input_status"]["runtime_evidence"] == "accepted"
    assert bundle["input_status"]["cleanup_proof"] == "accepted"
    assert bundle["runtime_window"] == {
        "start_commit_sha": "3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        "end_commit_sha": "3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        "start_profile": "211-current-start",
        "end_profile": "211-current-end",
    }
    assert bundle["candidate_observed_evidence"]["runtime_profile"] == "211-current-end"
    assert bundle["candidate_observed_evidence"]["cleanup_proof"] == {
        "schema_version": "ai-platform.capacity-cleanup-proof.v1",
        "status": "verified",
        "evidence_ref": "capacity-evidence/api-burst-cleanup.json",
        "test_tenants_removed": True,
        "queued_payloads_removed": True,
        "sandbox_leases_released": True,
        "temporary_artifacts_removed": True,
        "generated_documents_removed": True,
    }
    assert "cleanup_proof" not in bundle["missing_required_evidence"]
    assert bundle["recorded_gate_evidence_draft"]["cleanup_proof_status"] == "verified"
    assert bundle["recorded_gate_evidence_draft"]["status"] == "draft_not_recorded"
    assert bundle["recorded_gate_evidence_draft"]["does_not_mark_gate_recorded"] is True
    assert bundle["candidate_gate_summary"]["status"] == "draft_incomplete_not_recorded"
    assert bundle["readiness_preview"]["status"] == "blocked_missing_load_test_evidence"
    assert bundle["readiness_preview"]["load_test_evidence_status"] == "missing"
    assert bundle["does_not_mark_gate_recorded"] is True
    assert bundle["does_not_raise_defaults"] is True

    serialized = json.dumps(bundle, ensure_ascii=False).lower()
    assert "sk-secret" not in serialized
    assert "raw_storage_key" not in serialized
    assert "executor_private_payload" not in serialized
    assert "api_key" not in serialized


def test_capacity_evidence_bundle_rejects_cleanup_proof_with_private_evidence_ref():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    probe_output = {
        "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
        "gate": "api_read_write_burst",
        "status": "probe_completed_not_gate_evidence",
        "load_test_evidence_status": "probe_only_not_recorded",
        "does_not_mark_gate_recorded": True,
        "does_not_raise_defaults": True,
        "sent_requests": 20,
        "http_status_counts": {"200": 20},
        "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
        "cleanup_proof_status": "not_applicable_read_only_probe",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }
    cleanup_proof = {
        "schema_version": "ai-platform.capacity-cleanup-proof.v1",
        "gate": "api_read_write_burst",
        "status": "verified",
        "evidence_ref": "tenants/default/private/capacity-cleanup.json",
        "test_tenants_removed": True,
        "queued_payloads_removed": True,
        "sandbox_leases_released": True,
        "temporary_artifacts_removed": True,
        "generated_documents_removed": True,
    }

    bundle = build_capacity_evidence_bundle(
        runtime_evidence,
        probe_output,
        cleanup_proof=cleanup_proof,
    )

    assert bundle["status"] == "blocked_incomplete_inputs"
    assert bundle["input_status"]["cleanup_proof"] == "not_accepted"
    assert "cleanup_proof_evidence_ref_unsafe" in bundle["input_errors"]
    assert "cleanup_proof" in bundle["missing_required_evidence"]
    assert "cleanup_proof" not in bundle["candidate_observed_evidence"]
    assert bundle["recorded_gate_evidence_draft"]["cleanup_proof_status"] == "missing"
    assert bundle["recorded_gate_evidence_draft"]["does_not_mark_gate_recorded"] is True
    assert bundle["does_not_raise_defaults"] is True

    serialized = json.dumps(bundle, ensure_ascii=False).lower()
    assert "tenants/default/private" not in serialized
    assert "private/capacity-cleanup" not in serialized


def test_capacity_evidence_bundle_rejects_cleanup_proof_with_raw_evidence_ref_segment():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    probe_output = {
        "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
        "gate": "api_read_write_burst",
        "status": "probe_completed_not_gate_evidence",
        "load_test_evidence_status": "probe_only_not_recorded",
        "does_not_mark_gate_recorded": True,
        "does_not_raise_defaults": True,
        "sent_requests": 20,
        "http_status_counts": {"200": 20},
        "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
        "cleanup_proof_status": "not_applicable_read_only_probe",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }
    cleanup_proof = {
        "schema_version": "ai-platform.capacity-cleanup-proof.v1",
        "gate": "api_read_write_burst",
        "status": "verified",
        "evidence_ref": "capacity-evidence/raw/capacity-cleanup.json",
        "test_tenants_removed": True,
        "queued_payloads_removed": True,
        "sandbox_leases_released": True,
        "temporary_artifacts_removed": True,
        "generated_documents_removed": True,
    }

    bundle = build_capacity_evidence_bundle(
        runtime_evidence,
        probe_output,
        cleanup_proof=cleanup_proof,
    )

    assert bundle["status"] == "blocked_incomplete_inputs"
    assert bundle["input_status"]["cleanup_proof"] == "not_accepted"
    assert "cleanup_proof_evidence_ref_unsafe" in bundle["input_errors"]
    assert "cleanup_proof" in bundle["missing_required_evidence"]
    assert "cleanup_proof" not in bundle["candidate_observed_evidence"]
    assert bundle["recorded_gate_evidence_draft"]["does_not_mark_gate_recorded"] is True
    assert bundle["does_not_raise_defaults"] is True

    serialized = json.dumps(bundle, ensure_ascii=False).lower()
    assert "capacity-evidence/raw" not in serialized


def test_capacity_evidence_bundle_rejects_incomplete_cleanup_proof():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    probe_output = {
        "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
        "gate": "api_read_write_burst",
        "status": "probe_completed_not_gate_evidence",
        "load_test_evidence_status": "probe_only_not_recorded",
        "does_not_mark_gate_recorded": True,
        "does_not_raise_defaults": True,
        "sent_requests": 20,
        "http_status_counts": {"200": 20},
        "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
        "cleanup_proof_status": "not_applicable_read_only_probe",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }
    cleanup_proof = {
        "schema_version": "ai-platform.capacity-cleanup-proof.v1",
        "gate": "api_read_write_burst",
        "status": "verified",
        "evidence_ref": "capacity-evidence/api-burst-cleanup.json",
        "test_tenants_removed": True,
        "queued_payloads_removed": True,
        "sandbox_leases_released": True,
        "temporary_artifacts_removed": True,
    }

    bundle = build_capacity_evidence_bundle(
        runtime_evidence,
        probe_output,
        cleanup_proof=cleanup_proof,
    )

    assert bundle["status"] == "blocked_incomplete_inputs"
    assert bundle["input_status"]["cleanup_proof"] == "not_accepted"
    assert "cleanup_proof_generated_documents_removed_not_verified" in bundle["input_errors"]
    assert "cleanup_proof" in bundle["missing_required_evidence"]
    assert "cleanup_proof" not in bundle["candidate_observed_evidence"]
    assert bundle["recorded_gate_evidence_draft"]["cleanup_proof_status"] == "missing"
    assert bundle["recorded_gate_evidence_draft"]["does_not_mark_gate_recorded"] is True
    assert bundle["does_not_raise_defaults"] is True


def test_capacity_evidence_bundle_rejects_probe_without_no_default_raise_policy():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current",
    )
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    probe_output = {
        "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
        "gate": "api_read_write_burst",
        "status": "probe_completed_not_gate_evidence",
        "load_test_evidence_status": "probe_only_not_recorded",
        "does_not_mark_gate_recorded": True,
        "does_not_raise_defaults": False,
        "sent_requests": 20,
        "http_status_counts": {"200": 20},
        "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
        "cleanup_proof_status": "not_applicable_read_only_probe",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }

    bundle = build_capacity_evidence_bundle(runtime_evidence, probe_output)

    assert bundle["status"] == "blocked_incomplete_inputs"
    assert bundle["input_status"]["bounded_probe"] == "not_accepted"
    assert "bounded_probe_missing_no_default_raise_policy" in bundle["input_errors"]
    assert bundle["candidate_observed_evidence"] == {}
    assert bundle["candidate_gate_summary"]["status"] == "draft_not_available"
    assert bundle["readiness_preview"]["status"] == "blocked_missing_load_test_evidence"
    assert bundle["does_not_raise_defaults"] is True
    assert bundle["does_not_mark_gate_recorded"] is True


def test_capacity_evidence_bundle_cli_outputs_gap_first_markdown(tmp_path):
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current",
    )
    runtime_path = tmp_path / "runtime-evidence.json"
    probe_path = tmp_path / "bounded-probe.json"
    runtime_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-runtime-evidence.v1",
                "snapshot": snapshot,
                "readiness": build_capacity_gate_readiness(snapshot),
            }
        ),
        encoding="utf-8",
    )
    probe_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
                "gate": "api_read_write_burst",
                "status": "probe_completed_not_gate_evidence",
                "load_test_evidence_status": "probe_only_not_recorded",
                "does_not_mark_gate_recorded": True,
                "does_not_raise_defaults": True,
                "sent_requests": 20,
                "http_status_counts": {"200": 20},
                "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
                "cleanup_proof_status": "not_applicable_read_only_probe",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_evidence_bundle.py",
            "--runtime-evidence-json",
            str(runtime_path),
            "--bounded-probe-json",
            str(probe_path),
            "--format",
            "markdown",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "ai-platform Capacity Evidence Bundle" in result.stdout
    assert "Status: `blocked_incomplete_load_test_evidence`" in result.stdout
    assert "Does not mark gate recorded: `true`" in result.stdout
    assert "cleanup_proof" in result.stdout
    assert "Do not raise production concurrency defaults" in result.stdout
    assert "C:\\Users" not in result.stdout


def test_capacity_evidence_bundle_cli_accepts_start_runtime_and_cleanup_proof_json(tmp_path):
    start_snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-start",
    )
    end_snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    start_path = tmp_path / "runtime-evidence-start.json"
    end_path = tmp_path / "runtime-evidence-end.json"
    probe_path = tmp_path / "bounded-probe.json"
    cleanup_path = tmp_path / "cleanup-proof.json"
    start_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-runtime-evidence.v1",
                "snapshot": start_snapshot,
                "readiness": build_capacity_gate_readiness(start_snapshot),
            }
        ),
        encoding="utf-8",
    )
    end_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-runtime-evidence.v1",
                "snapshot": end_snapshot,
                "readiness": build_capacity_gate_readiness(end_snapshot),
            }
        ),
        encoding="utf-8",
    )
    probe_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
                "gate": "api_read_write_burst",
                "status": "probe_completed_not_gate_evidence",
                "load_test_evidence_status": "probe_only_not_recorded",
                "does_not_mark_gate_recorded": True,
                "does_not_raise_defaults": True,
                "sent_requests": 20,
                "http_status_counts": {"200": 20},
                "latency_ms": {"count": 20, "p50": 72.864, "p95": 252.748, "p99": 283.726},
                "cleanup_proof_status": "not_applicable_read_only_probe",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
        ),
        encoding="utf-8",
    )
    cleanup_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-cleanup-proof.v1",
                "gate": "api_read_write_burst",
                "status": "verified",
                "evidence_ref": "capacity-evidence/api-burst-cleanup.json",
                "test_tenants_removed": True,
                "queued_payloads_removed": True,
                "sandbox_leases_released": True,
                "temporary_artifacts_removed": True,
                "generated_documents_removed": True,
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_evidence_bundle.py",
            "--start-runtime-evidence-json",
            str(start_path),
            "--runtime-evidence-json",
            str(end_path),
            "--bounded-probe-json",
            str(probe_path),
            "--cleanup-proof-json",
            str(cleanup_path),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-evidence-bundle.v1"
    assert payload["input_status"]["start_runtime_evidence"] == "accepted"
    assert payload["input_status"]["cleanup_proof"] == "accepted"
    assert payload["runtime_window"]["start_profile"] == "211-current-start"
    assert payload["runtime_window"]["end_profile"] == "211-current-end"
    assert payload["recorded_gate_evidence_draft"]["cleanup_proof_status"] == "verified"
    assert payload["readiness_preview"]["status"] == "blocked_missing_load_test_evidence"
    assert "raw_storage_key" not in result.stdout
    assert "executor_private_payload" not in result.stdout
    assert "api_key" not in result.stdout


def test_capacity_recorded_gate_snapshot_records_only_explicit_complete_gate():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    evidence_packet = {
        "schema_version": "ai-platform.capacity-recorded-gate-evidence.v1",
        "gate": "api_read_write_burst",
        "does_not_raise_defaults": True,
        "evidence": {
            item: f"capacity-evidence/api-read-write-burst/{item}.json"
            for item in LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST
        },
        "cleanup_proof_status": "verified",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }

    result = build_capacity_recorded_gate_snapshot(runtime_evidence, evidence_packet)

    assert result["schema_version"] == "ai-platform.capacity-recorded-gate-snapshot.v1"
    assert result["status"] == "recorded_gate_input_accepted"
    assert result["input_status"] == {
        "runtime_evidence": "accepted",
        "recorded_gate_evidence": "accepted",
    }
    assert result["recorded_gate"] == "api_read_write_burst"
    assert result["does_not_raise_defaults"] is True
    assert result["snapshot"]["load_test_evidence"]["status"] == "recorded"
    assert result["snapshot"]["load_test_evidence"]["recorded_gates"] == [
        "api_read_write_burst"
    ]
    assert result["readiness"]["status"] == "blocked_missing_load_test_evidence"
    assert "api_read_write_burst" not in result["readiness"]["missing_load_test_gates"]
    assert result["readiness"]["production_default_decision"] == (
        "do_not_raise_without_recorded_load_test_evidence"
    )


def test_capacity_recorded_gate_snapshot_does_not_echo_unsafe_runtime_snapshot_fields():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    snapshot["executor_private_payload"] = {"raw_storage_key": "tenants/default/private/raw.json"}
    snapshot["live_signals"]["queue"]["raw_storage_key"] = "tenants/default/private/queue.json"
    snapshot["live_signals"]["database_pool"]["database_url"] = (
        "postgresql://user:secret@db.internal/ai_platform"
    )
    snapshot["runtime_identity"]["commit_sha"] = (
        "C:\\Users\\Xinlin.jiang\\Temp\\capacity.json"
    )
    snapshot["runtime_identity"]["profile"] = "mailto:operator@example.com"
    snapshot["local_path"] = "C:\\Users\\Xinlin.jiang\\AppData\\Local\\Temp\\capacity.json"
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    evidence_packet = {
        "schema_version": "ai-platform.capacity-recorded-gate-evidence.v1",
        "gate": "api_read_write_burst",
        "does_not_raise_defaults": True,
        "evidence": {
            item: f"capacity-evidence/api-read-write-burst/{item}.json"
            for item in LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST
        },
        "cleanup_proof_status": "verified",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }

    result = build_capacity_recorded_gate_snapshot(runtime_evidence, evidence_packet)
    serialized = json.dumps(result, ensure_ascii=False).lower()

    assert result["status"] == "recorded_gate_input_accepted"
    assert result["input_status"]["runtime_evidence"] == "accepted"
    assert result["snapshot"]["runtime_identity"] == {
        "commit_sha": "unknown",
        "profile": "unknown",
    }
    assert result["readiness"]["runtime_identity"] == {
        "commit_sha": "unknown",
        "profile": "unknown",
    }
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "tenants/default/private" not in serialized
    assert "xinlin" not in serialized
    assert "operator@example.com" not in serialized
    assert "database_url" not in serialized
    assert "postgresql://" not in serialized
    assert "c:\\users" not in serialized


def test_capacity_recorded_gate_snapshot_recomputes_admin_runtime_sections_fail_closed():
    snapshot = _snapshot_with_complete_recorded_gates()
    existing_recorded_gates = [gate for gate in LOAD_TEST_GATES if gate != "api_read_write_burst"]
    snapshot["load_test_evidence"]["recorded_gates"] = existing_recorded_gates
    snapshot["load_test_evidence"]["gate_evidence"].pop("api_read_write_burst")
    snapshot["admin_runtime_evidence"] = {
        "required_sections": ["queue"],
        "observed_sections": ["queue"],
        "missing_sections": [],
    }
    snapshot["capacity"] = None
    snapshot["live_signals"] = {
        "queue": snapshot["live_signals"]["queue"],
    }
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    evidence_packet = {
        "schema_version": "ai-platform.capacity-recorded-gate-evidence.v1",
        "gate": "api_read_write_burst",
        "does_not_raise_defaults": True,
        "evidence": {
            item: f"capacity-evidence/api-read-write-burst/{item}.json"
            for item in LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST
        },
        "cleanup_proof_status": "verified",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }

    result = build_capacity_recorded_gate_snapshot(runtime_evidence, evidence_packet)

    assert result["status"] == "recorded_gate_input_accepted"
    assert result["readiness"]["status"] == "blocked_missing_admin_runtime_sections"
    assert result["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"
    assert result["readiness"]["admin_runtime_evidence"]["required_sections"] == [
        "capacity",
        "database_pool",
        "queue",
        "admission",
        "backpressure",
        "sandbox",
        "observability",
    ]
    assert result["readiness"]["admin_runtime_evidence"]["missing_sections"] == [
        "capacity",
        "database_pool",
        "queue",
        "admission",
        "backpressure",
        "sandbox",
        "observability",
    ]


def test_capacity_recorded_gate_snapshot_preserves_existing_recorded_gates():
    snapshot = _snapshot_with_complete_recorded_gates()
    existing_recorded_gates = [gate for gate in LOAD_TEST_GATES if gate != "api_read_write_burst"]
    snapshot["load_test_evidence"]["recorded_gates"] = existing_recorded_gates
    snapshot["load_test_evidence"]["gate_evidence"].pop("api_read_write_burst")
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    evidence_packet = {
        "schema_version": "ai-platform.capacity-recorded-gate-evidence.v1",
        "gate": "api_read_write_burst",
        "does_not_raise_defaults": True,
        "evidence": {
            item: f"capacity-evidence/api-read-write-burst/{item}.json"
            for item in LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST
        },
        "cleanup_proof_status": "verified",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }

    result = build_capacity_recorded_gate_snapshot(runtime_evidence, evidence_packet)

    assert result["status"] == "recorded_gate_input_accepted"
    assert result["snapshot"]["load_test_evidence"]["recorded_gates"] == list(LOAD_TEST_GATES)
    assert set(result["snapshot"]["load_test_evidence"]["gate_evidence"]) == set(LOAD_TEST_GATES)
    assert result["readiness"]["status"] == "ready_for_operator_review"
    assert result["production_default_decision"] == "operator_review_required_before_default_change"


def test_capacity_recorded_gate_snapshot_rejects_unsafe_evidence_without_echoing_values():
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    runtime_evidence = {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
        "readiness": build_capacity_gate_readiness(snapshot),
    }
    evidence_packet = {
        "schema_version": "ai-platform.capacity-recorded-gate-evidence.v1",
        "gate": "api_read_write_burst",
        "does_not_raise_defaults": True,
        "evidence": {
            item: f"capacity-evidence/api-read-write-burst/{item}.json"
            for item in LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST
        },
        "cleanup_proof_status": "verified",
        "stop_condition_status": "passed",
        "triggered_stop_conditions": [],
    }
    evidence_packet["evidence"]["commit_sha"] = "https://example.invalid/private/commit.json"
    evidence_packet["evidence"]["api_worker_image_labels"] = {
        "raw_storage_key": "tenants/default/private/capacity.json"
    }

    result = build_capacity_recorded_gate_snapshot(runtime_evidence, evidence_packet)

    assert result["status"] == "blocked_incomplete_inputs"
    assert result["input_status"]["recorded_gate_evidence"] == "not_accepted"
    assert "recorded_evidence_commit_sha_unsafe" in result["input_errors"]
    assert "recorded_evidence_api_worker_image_labels_unsafe" in result["input_errors"]
    assert result["snapshot"]["load_test_evidence"]["status"] == "missing"
    assert result["does_not_raise_defaults"] is True

    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "example.invalid" not in serialized
    assert "tenants/default/private" not in serialized
    assert "raw_storage_key" not in serialized


def test_capacity_recorded_gate_snapshot_cli_outputs_snapshot_and_verdict(tmp_path):
    snapshot = build_capacity_evidence_snapshot(
        _admin_runtime_overview(),
        commit_sha="3d607c96b8d8e21f59461bd94cc4b64de1d49dd5",
        runtime_profile="211-current-end",
    )
    runtime_path = tmp_path / "runtime-evidence-end.json"
    evidence_path = tmp_path / "recorded-gate-evidence.json"
    runtime_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-runtime-evidence.v1",
                "snapshot": snapshot,
                "readiness": build_capacity_gate_readiness(snapshot),
            }
        ),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.capacity-recorded-gate-evidence.v1",
                "gate": "api_read_write_burst",
                "does_not_raise_defaults": True,
                "evidence": {
                    item: f"capacity-evidence/api-read-write-burst/{item}.json"
                    for item in LOAD_TEST_REQUIRED_EVIDENCE_FOR_TEST
                },
                "cleanup_proof_status": "verified",
                "stop_condition_status": "passed",
                "triggered_stop_conditions": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_recorded_gate_snapshot.py",
            "--runtime-evidence-json",
            str(runtime_path),
            "--recorded-gate-evidence-json",
            str(evidence_path),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.capacity-recorded-gate-snapshot.v1"
    assert payload["status"] == "recorded_gate_input_accepted"
    assert payload["readiness"]["status"] == "blocked_missing_load_test_evidence"
    assert payload["snapshot"]["load_test_evidence"]["recorded_gates"] == [
        "api_read_write_burst"
    ]
    assert "C:\\Users" not in result.stdout
