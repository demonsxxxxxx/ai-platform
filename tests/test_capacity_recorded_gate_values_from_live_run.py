import json
import subprocess
import sys
from pathlib import Path

from app.capacity_baseline import build_capacity_baseline, build_capacity_evidence_snapshot
from app.foundation_runtime_concurrency import FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA
from tools.capacity_recorded_gate_values_from_live_run import build_recorded_gate_values_from_live_run


COMMIT_SHA = "39aa862b0c6139bcc80578dd51ef5de898ea92cc"


class Settings:
    database_url = "postgresql://user:redacted@db.internal/ai_platform"
    redis_url = "redis://:redacted@redis.internal:6379/0"
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
    sandbox_container_provider = "docker"
    sandbox_container_start_timeout_seconds = 30
    sandbox_executor_health_timeout_seconds = 60
    sandbox_max_active_ephemeral_containers = 2
    sandbox_max_active_persistent_containers = 1
    multi_agent_dispatch_worker_enabled = False
    multi_agent_dispatch_worker_limit = 1
    llm_gateway_provider = "openai_compatible"
    model_gateway_request_concurrency_limit = 0
    openai_base_url = "https://model-gateway.internal/v1"
    openai_api_key = "redacted-test-key"


def _runtime_evidence() -> dict[str, object]:
    overview = {
        "capacity": build_capacity_baseline(Settings()),
        "queue": {
            "status": {
                "depths": {
                    "queued": 0,
                    "processing": 0,
                    "dead_letter": 1,
                    "tenant_queued": 0,
                    "tenant_processing": 0,
                },
                "capacity": {
                    "max_active_worker_runs": 3,
                    "available_worker_slots": 3,
                    "processing_saturated": False,
                    "queue_lease_scan_limit": 50,
                    "queue_tenant_processing_limit": 0,
                    "queue_user_processing_limit": 0,
                },
            }
        },
        "database_pool": {
            "open": True,
            "configured": {"min_size": 1, "max_size": 10, "timeout_seconds": 10, "max_waiting": 100},
            "stats": {"requests_waiting": 0},
        },
        "admission": {
            "active_runs": 0,
            "active_users": 0,
            "saturated_users": 0,
            "max_active_runs_per_user": 3,
            "policy_active": True,
        },
        "backpressure": {"reasons": []},
        "sandbox": {
            "containers": {"running": 0, "total": 0},
            "leases": {"active": 0, "released": 12},
            "list_runtime_containers_status": "available",
        },
        "observability": {
            "event_count": 120,
            "artifact_count": 24,
            "error_count": 0,
            "error_categories": {"executor": 0, "unknown": 0},
            "latency_ms": {"avg": 90, "max": 300, "p50": 80, "p95": 180, "p99": 260},
        },
    }
    snapshot = build_capacity_evidence_snapshot(
        overview,
        commit_sha=COMMIT_SHA,
        runtime_profile="b3-recorded-live-run-39aa862",
    )
    return {
        "schema_version": "ai-platform.capacity-runtime-evidence.v1",
        "snapshot": snapshot,
    }


def _foundation_runtime_evidence(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": FOUNDATION_RUNTIME_CONCURRENCY_SCHEMA,
        "artifact_kind": "foundation_runtime_concurrency",
        "commit_sha": COMMIT_SHA,
        "source_tree_commit_sha": COMMIT_SHA,
        "runtime_subject_commit_sha": COMMIT_SHA,
        "summary": {
            "tenant_count": 2,
            "user_count": 4,
            "session_count": 12,
            "run_count": 12,
            "concurrent_request_count": 12,
            "max_observed_concurrency": 12,
            "concurrency_probe_source": "client_case_timestamps",
            "concurrency_window_sample_count": 12,
        },
        "scenario_counts": {
            "run_creation": 4,
            "execution": 4,
            "cancel": 2,
            "retry": 2,
        },
        "checks": {
            "queue_admission": {
                "status": "passed",
                "admission_limit_violations": 0,
                "cross_tenant_queue_leaks": 0,
                "stale_queue_entries": 0,
                "queue_position_sample_count": 12,
                "queue_position_duplicate_count": 0,
                "queue_probe_sample_count": 12,
                "queue_probe_source": "redis_metadata",
                "cancel_action_statuses": [200, 200],
                "cancel_effect_statuses": ["cancel_requested", "cancelled"],
                "cancel_effect_run_count": 2,
                "retry_action_statuses": [200, 200],
                "retry_created_run_count": 2,
            },
            "sandbox_workspace": {
                "status": "passed",
                "workspace_scope_sample_count": 12,
                "sandbox_lease_sample_count": 12,
                "active_lease_count": 0,
                "cross_scope_lease_leaks": 0,
                "workspace_scope_collisions": 0,
                "lease_probe_source": "runtime_run_detail",
            },
            "memory_context": {
                "status": "passed",
                "context_snapshot_count": 12,
                "context_snapshot_public_projection_count": 12,
                "context_pack_version_sample_count": 12,
                "missing_context_pack_version_count": 0,
                "unsafe_context_pack_version_count": 0,
                "missing_public_summary_fields": [],
                "context_scope_probe_count": 12,
                "cross_scope_context_leaks": 0,
                "long_term_cross_session_memory_read": False,
            },
            "artifact_acl": {
                "status": "passed",
                "owner_statuses": [200, 200],
                "cross_user_statuses": [404, 404],
                "cross_tenant_statuses": [404, 404],
                "preview_cross_user_statuses": [404],
                "preview_cross_tenant_statuses": [404],
            },
            "tool_permission": {
                "status": "passed",
                "decision_sample_count": 12,
                "negative_reuse_probe_count": 48,
                "negative_reuse_denied_count": 48,
                "negative_reuse_unexpected_successes": 0,
                "allow_once_reuse_violations": 0,
                "wrong_decision_reuse_violations": 0,
                "tool_call_id_mismatch_violations": 0,
            },
            "skill_snapshots": {
                "status": "passed",
                "run_skill_snapshot_count": 12,
                "used_count": 12,
                "missing_pinned_snapshots": [],
                "mismatched_pinned_snapshots": [],
                "global_mutable_skill_lookup_used": False,
                "snapshot_binding_sample_count": 12,
            },
            "run_playback": {
                "status": "passed",
                "event_order_violations": 0,
                "private_payload_leak_count": 0,
            },
        },
        "cleanup_proof": {
            "after": {
                "status": "verified",
                "remaining_counts": {
                    "remaining_tenant_count": 0,
                    "remaining_run_count": 0,
                    "remaining_artifact_count": 0,
                    "remaining_queue_count": 0,
                },
            }
        },
        "non_expansion_invariants": {
            "production_concurrency_increase_allowed": False,
            "ordinary_user_multi_agent_allowed": False,
            "docker_sandbox_hardened_claim_allowed": False,
            "department_rollout_allowed": False,
            "long_term_cross_session_memory_enabled": False,
        },
        "role_provenance": {
            "run_creation_role": "user",
            "public_probe_role": "user",
            "admin_probe_role": None,
            "ordinary_user_multi_agent_opened": False,
        },
    }
    payload.update(overrides)
    return payload


def _profile_values() -> dict[str, object]:
    return {
        "target_profile_id": "b3_10x4_sdk_subagents",
        "evidence_source": "operator_reviewed_recorded_snapshot",
        "observed_concurrent_sessions": 10,
        "observed_peak_sdk_subagents_per_session": 4,
        "sdk_subagent_fanout_measurement_ref": "capacity-evidence/b3/sdk-subagent-fanout.json",
        "production_concurrency_defaults_raised": False,
        "safe_concurrency_claimed": False,
        "ordinary_user_platform_multi_run_orchestration_enabled": False,
    }


def test_capacity_recorded_gate_values_from_live_run_materializes_batch_inputs(tmp_path: Path):
    result = build_recorded_gate_values_from_live_run(
        runtime_evidence=_runtime_evidence(),
        foundation_runtime_evidence=_foundation_runtime_evidence(),
        evidence_ref_prefix="capacity-evidence/b3-recorded-live-run-39aa862",
    )

    assert result["status"] == "operator_value_files_ready"
    assert result["input_errors"] == []
    assert result["recorded_gates"] == [
        "api_read_write_burst",
        "run_creation_burst_by_tenant_and_user",
        "worker_processing_throughput",
        "queue_depth_and_lease_latency",
        "cancel_retry_resume_under_load",
        "sandbox_lease_creation_under_load",
        "model_gateway_timeout_and_backpressure",
    ]
    first_gate = result["values_by_gate"]["api_read_write_burst"]
    assert first_gate["commit_sha"] == COMMIT_SHA
    assert first_gate["latency_p50_p95_p99"] == {
        "source": "admin_runtime_observability_latency_ms",
        "p50": 80,
        "p95": 180,
        "p99": 260,
        "max": 300,
        "avg": 90,
    }
    assert "does_not_mark_gate_recorded" not in json.dumps(first_gate)


def test_capacity_recorded_gate_values_cli_feeds_existing_batch_assembler(tmp_path: Path):
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(_runtime_evidence()), encoding="utf-8")
    foundation_path = tmp_path / "foundation.json"
    foundation_path.write_text(json.dumps(_foundation_runtime_evidence()), encoding="utf-8")
    operator_dir = tmp_path / "operator-inputs"

    materialize = subprocess.run(
        [
            sys.executable,
            "tools/capacity_recorded_gate_values_from_live_run.py",
            "--runtime-evidence-json",
            str(runtime_path),
            "--foundation-runtime-evidence-json",
            str(foundation_path),
            "--operator-input-dir",
            str(operator_dir),
            "--evidence-ref-prefix",
            "capacity-evidence/b3-recorded-live-run-39aa862",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert materialize.returncode == 0, materialize.stdout + materialize.stderr
    materialized = json.loads(materialize.stdout)
    assert materialized["status"] == "operator_value_files_ready"
    assert len(materialized["written_files"]) == 8

    (operator_dir / "capacity-operator-reviewed-profile-values-b3-10x4-sdk-subagents.json").write_text(
        json.dumps(_profile_values()),
        encoding="utf-8",
    )
    batch = subprocess.run(
        [
            sys.executable,
            "tools/capacity_recorded_gate_batch_from_values.py",
            "--runtime-evidence-json",
            str(runtime_path),
            "--operator-input-dir",
            str(operator_dir),
            "--cleanup-proof-status",
            "verified",
            "--stop-condition-status",
            "passed",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert batch.returncode == 0, batch.stdout + batch.stderr
    payload = json.loads(batch.stdout)
    assert payload["status"] == "recorded_gate_batch_input_accepted"
    assert payload["readiness"]["status"] == "ready_for_operator_review"
    assert payload["input_status"] == {
        "profile_evidence": "accepted",
        "recorded_gate_evidence": "accepted",
        "runtime_evidence": "accepted",
    }


def test_capacity_recorded_gate_values_rejects_bounded_probe_input():
    result = build_recorded_gate_values_from_live_run(
        runtime_evidence=_runtime_evidence(),
        foundation_runtime_evidence={
            "schema_version": "ai-platform.capacity-bounded-load-harness.v1",
            "status": "probe_completed_not_gate_evidence",
            "load_test_evidence_status": "probe_only_not_recorded",
            "does_not_mark_gate_recorded": True,
        },
        evidence_ref_prefix="capacity-evidence/b3-recorded-live-run-39aa862",
    )

    assert result["status"] == "blocked_incomplete_inputs"
    assert "bounded_probe_input_not_allowed" in result["input_errors"]
    assert result["recorded_gates"] == []


def test_capacity_recorded_gate_values_rejects_runtime_subject_mismatch():
    mismatched = _foundation_runtime_evidence(runtime_subject_commit_sha="945db2bb5926ad7b01ead98c3283d55b77d2677d")

    result = build_recorded_gate_values_from_live_run(
        runtime_evidence=_runtime_evidence(),
        foundation_runtime_evidence=mismatched,
        evidence_ref_prefix="capacity-evidence/b3-recorded-live-run-39aa862",
    )

    assert result["status"] == "blocked_incomplete_inputs"
    assert "runtime_subject_commit_mismatch" in result["input_errors"]


def test_capacity_recorded_gate_values_fails_closed_for_partial_runtime_snapshot():
    runtime = _runtime_evidence()
    snapshot = runtime["snapshot"]
    assert isinstance(snapshot, dict)
    live_signals = snapshot["live_signals"]
    capacity = snapshot["capacity"]
    assert isinstance(live_signals, dict)
    assert isinstance(capacity, dict)
    live_signals.pop("database_pool")
    limits = capacity["limits"]
    assert isinstance(limits, dict)
    limits.pop("database_pool")

    result = build_recorded_gate_values_from_live_run(
        runtime_evidence=runtime,
        foundation_runtime_evidence=_foundation_runtime_evidence(),
        evidence_ref_prefix="capacity-evidence/b3-recorded-live-run-39aa862",
    )

    assert result["status"] == "blocked_incomplete_inputs"
    assert "runtime_evidence_field_database_pool_settings_missing" in result["input_errors"]
    assert result["recorded_gates"] == []
