import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_verifier():
    path = Path("scripts/verify_sandbox_runtime_211.py")
    spec = importlib.util.spec_from_file_location("verify_sandbox_runtime_211", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_generator():
    path = Path("scripts/generate_sandbox_runtime_evidence_211.py")
    spec = importlib.util.spec_from_file_location("generate_sandbox_runtime_evidence_211", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def observed_resource_probe(*, run_id: str = "run-a", runtime_subject: str = "local") -> dict[str, object]:
    return {
        "over_limit_cleanup_verified": True,
        "probe_kind": "platform_executor_deadline",
        "run_id": run_id,
        "probe_source": "executor_response",
        "runtime_mode": "platform",
        "runtime_subject": runtime_subject,
        "runtime_identity": observed_runtime_identity(runtime_subject=runtime_subject),
        "requested_max_seconds": 0.05,
        "observed_timeout_elapsed_ms": 51,
        "max_seconds_enforced": True,
        "bounded_error_projection": {
            "source": "admin_runtime_projection",
            "run_id": run_id,
            "status": "failed",
            "error_code": "executor_health_timeout",
            "host_paths_redacted": True,
            "raw_docker_payload_absent": True,
            "callback_token_absent": True,
        },
        "bounded_error_projection_source": "executor_callback",
    }


def observed_runtime_identity(
    *,
    runtime_subject: str = "local",
    requested_image: str = "ai-platform:local",
) -> dict[str, object]:
    return {
        "image_id": "sha256:" + "a" * 64,
        "requested_image": requested_image,
        "observed_image": requested_image,
        "source_revision": runtime_subject,
        "oci_revision": runtime_subject,
        "source_tree_commit": runtime_subject,
        "source_tree_dirty": False,
    }


def observed_resource_timeout_hardening(*, run_id: str = "run-a") -> dict[str, object]:
    return {
        "evidence_class": "live_platform_probe",
        "max_seconds_enforced": True,
        "timeout_error_code": "executor_deadline_exceeded",
        "failed_container_removed": True,
        "requested_max_seconds": 0.05,
        "observed_timeout_elapsed_ms": 51,
        "run_id": run_id,
        "probe_source": "executor_response",
        "runtime_mode": "platform",
        "runtime_subject": "local",
        "runtime_identity": observed_runtime_identity(),
    }


def observed_projection_callback(*, run_id: str = "run-a") -> dict[str, object]:
    return {
        "run_id": run_id,
        "status": "failed",
        "state_patch": {
            "bounded_error_projection": {
                "source": "admin_runtime_projection",
                "run_id": run_id,
                "status": "failed",
                "error_code": "executor_health_timeout",
                "host_paths_redacted": True,
                "raw_docker_payload_absent": True,
                "callback_token_absent": True,
            }
        },
    }


def observed_resource_limits_hardening(*, run_id: str = "run-a") -> dict[str, object]:
    probe = observed_resource_probe(run_id=run_id)
    return {
        "evidence_class": "live_platform_probe",
        "memory_limit_mb": 512,
        "cpu_limit_count": 0.5,
        "pids_limit": 128,
        "process_timeout_seconds": 60,
        "limit_source": "platform_request",
        "docker_inspection_verified": True,
        "over_limit_cleanup_verified": True,
        "over_limit_probe_kind": probe["probe_kind"],
        "over_limit_requested_max_seconds": probe["requested_max_seconds"],
        "over_limit_observed_timeout_elapsed_ms": probe["observed_timeout_elapsed_ms"],
        "timeout_probe_run_id": probe["run_id"],
        "timeout_probe_source": probe["probe_source"],
        "timeout_probe_runtime_mode": probe["runtime_mode"],
        "timeout_probe_runtime_subject": probe["runtime_subject"],
        "timeout_probe_runtime_identity": probe["runtime_identity"],
        "max_seconds_enforced": True,
        "bounded_error_projection_verified": True,
        "bounded_error_projection": probe["bounded_error_projection"],
        "bounded_error_projection_source": "executor_callback",
    }


def test_check_result_to_dict():
    verifier = load_verifier()

    result = verifier.CheckResult("check_docker_socket", False, "missing docker")

    assert result.to_dict() == {
        "name": "check_docker_socket",
        "passed": False,
        "message": "missing docker",
    }


def test_run_checks_returns_failure_for_any_failed_check():
    verifier = load_verifier()

    exit_code, results = verifier.run_checks(
        [
            lambda: verifier.CheckResult("a", True, "ok"),
            lambda: verifier.CheckResult("b", False, "failed"),
        ]
    )

    assert exit_code == 1
    assert [result.name for result in results if not result.passed] == ["b"]


def test_run_checks_returns_success_when_all_checks_pass():
    verifier = load_verifier()

    exit_code, results = verifier.run_checks([lambda: verifier.CheckResult("a", True, "ok")])

    assert exit_code == 0
    assert results[0].passed is True


def test_workspace_write_probe_creates_and_removes_file(tmp_path):
    verifier = load_verifier()

    result = verifier.check_workspace_write(tmp_path)

    assert result.passed is True
    assert not list(tmp_path.glob(".ai-platform-sandbox-probe-*"))


def test_executor_health_accepts_healthy_response():
    verifier = load_verifier()

    def fake_urlopen(request, timeout):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return None

            def read(self):
                return b'{"status":"ready"}'

        return Response()

    result = verifier.check_executor_health("http://executor.test", urlopen=fake_urlopen)

    assert result.passed is True


def test_executor_health_accepts_platform_runtime_evidence_when_ephemeral_executor_is_gone(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-211.v1",
                "run_id": "run-a",
                "runtime_mode": "platform",
                "sandbox_provider": "docker",
                "executed_task": True,
                "timings": {
                    "schema_version": "ai-platform.sandbox-latency-split.v1",
                    "sandbox_lease_acquire_latency_ms": 1,
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                    "sandbox_executor_dispatch_latency_ms": 4,
                    "executor_model_latency_ms": 5,
                    "document_processing_latency_ms": 6,
                    "sandbox_cleanup_latency_ms": 7,
                    "sandbox_total_latency_ms": 28,
                },
            }
        ),
        encoding="utf-8",
    )

    def failing_urlopen(request, timeout):
        raise OSError("connection refused")

    result = verifier.check_executor_health_or_platform_evidence(
        "http://executor.test",
        evidence,
        run_id="run-a",
        urlopen=failing_urlopen,
    )

    assert result.passed is True
    assert "platform runtime evidence" in result.message


def test_callback_stream_requires_running_and_terminal_events(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "executor_url": "http://executor.test",
                "executed_task": True,
                "callback_auth": "token",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "callbacks": [
                    {"run_id": "run-a", "status": "running"},
                    {"run_id": "run-a", "status": "completed"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert verifier.check_callback_stream(evidence, run_id="run-a", executor_url="http://executor.test").passed is True

    evidence.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "executor_url": "http://executor.test",
                "executed_task": True,
                "callback_auth": "token",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "callbacks": [{"run_id": "run-a", "status": "running"}],
            }
        ),
        encoding="utf-8",
    )
    failed = verifier.check_callback_stream(evidence, run_id="run-a", executor_url="http://executor.test")
    assert failed.passed is False
    assert "terminal" in failed.message or "metadata" in failed.message


def test_callback_stream_rejects_stale_or_unbound_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "run_id": "other-run",
                "executor_url": "http://executor.test",
                "executed_task": True,
                "callback_auth": "token",
                "generated_at": "2020-01-01T00:00:00+00:00",
                "callbacks": [
                    {"status": "running"},
                    {"status": "completed"},
                ],
            }
        ),
        encoding="utf-8",
    )

    failed = verifier.check_callback_stream(evidence, run_id="run-a", executor_url="http://executor.test")
    assert failed.passed is False
    assert "run_id" in failed.message


def test_callback_stream_requires_explicit_run_id(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "executor_url": "http://executor.test",
                "executed_task": True,
                "callback_auth": "token",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "callbacks": [
                    {"run_id": "run-a", "status": "running"},
                    {"run_id": "run-a", "status": "completed"},
                ],
            }
        ),
        encoding="utf-8",
    )

    failed = verifier.check_callback_stream(evidence, executor_url="http://executor.test")

    assert failed.passed is False
    assert "run_id" in failed.message


def test_cancel_check_requires_stop_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"

    evidence.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "cancel_stops_container": True,
                "cancelled_container_id": "exec-run-a",
            }
        ),
        encoding="utf-8",
    )
    assert verifier.check_cancel_stops_container(evidence, run_id="run-a").passed is True

    evidence.write_text(json.dumps({"cancel_stops_container": False}), encoding="utf-8")
    assert verifier.check_cancel_stops_container(evidence, run_id="run-a").passed is False


def test_platform_runtime_evidence_requires_latency_split_and_real_provider(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    base = {
        "schema_version": "ai-platform.sandbox-runtime-211.v1",
        "run_id": "run-a",
        "executor_url": "http://executor.test",
        "runtime_mode": "platform",
        "sandbox_provider": "docker",
        "executed_task": True,
        "callback_auth": "token",
        "executor": {
            "sdk_used": True,
            "executor_mode": "claude_agent_sdk",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "callbacks": [
            {"run_id": "run-a", "status": "running"},
            {"run_id": "run-a", "status": "completed"},
        ],
        "non_expansion_invariants": {
            "ordinary_user_high_risk_sandbox_allowed": False,
            "admin_or_allowlist_only": True,
            "production_concurrency_defaults_raised": False,
            "docker_sandbox_production_hardening_claimed": False,
            "ordinary_user_multi_agent_allowed": False,
        },
    }
    timings = {
        "schema_version": "ai-platform.sandbox-latency-split.v1",
        "sandbox_queue_wait_latency_ms": 0,
        "sandbox_lease_acquire_latency_ms": 1,
        "sandbox_container_start_latency_ms": 1,
        "sandbox_container_cold_start_latency_ms": 2,
        "sandbox_healthcheck_latency_ms": 3,
        "sandbox_executor_dispatch_latency_ms": 4,
        "executor_first_token_latency_ms": 5,
        "executor_tool_call_latency_ms": 0,
        "executor_model_latency_ms": 5,
        "document_processing_latency_ms": 6,
        "artifact_upload_latency_ms": 0,
        "sandbox_cleanup_latency_ms": 7,
        "sandbox_total_latency_ms": 34,
    }
    evidence.write_text(json.dumps({**base, "timings": timings}), encoding="utf-8")

    assert verifier.check_platform_runtime_evidence(evidence, run_id="run-a").passed is True

    evidence.write_text(json.dumps({**base, "sandbox_provider": "opensandbox", "timings": timings}), encoding="utf-8")
    assert verifier.check_platform_runtime_evidence(evidence, run_id="run-a").passed is True

    evidence.write_text(json.dumps({**base, "runtime_mode": "executor", "timings": timings}), encoding="utf-8")
    failed_mode = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_mode.passed is False
    assert "platform" in failed_mode.message

    evidence.write_text(json.dumps({**base, "sandbox_provider": "fake", "timings": timings}), encoding="utf-8")
    failed_provider = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_provider.passed is False
    assert "real sandbox provider" in failed_provider.message

    incomplete = dict(timings)
    incomplete.pop("sandbox_container_cold_start_latency_ms")
    evidence.write_text(json.dumps({**base, "timings": incomplete}), encoding="utf-8")
    failed_timing = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_timing.passed is False
    assert "sandbox_container_cold_start_latency_ms" in failed_timing.message

    missing_stage_timing = dict(timings)
    missing_stage_timing.pop("sandbox_queue_wait_latency_ms")
    evidence.write_text(json.dumps({**base, "timings": missing_stage_timing}), encoding="utf-8")
    failed_stage_timing = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_stage_timing.passed is False
    assert "sandbox_queue_wait_latency_ms" in failed_stage_timing.message

    missing_executor = dict(base)
    missing_executor.pop("executor")
    evidence.write_text(json.dumps({**missing_executor, "timings": timings}), encoding="utf-8")
    failed_executor = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_executor.passed is False
    assert "claude agent sdk" in failed_executor.message.lower()

    missing_invariants = dict(base)
    missing_invariants.pop("non_expansion_invariants")
    evidence.write_text(json.dumps({**missing_invariants, "timings": timings}), encoding="utf-8")
    failed_invariants = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_invariants.passed is False
    assert "non_expansion_invariants" in failed_invariants.message

    expanded_ordinary_user = {
        **base,
        "non_expansion_invariants": {
            **base["non_expansion_invariants"],
            "ordinary_user_high_risk_sandbox_allowed": True,
        },
    }
    evidence.write_text(json.dumps({**expanded_ordinary_user, "timings": timings}), encoding="utf-8")
    failed_ordinary = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_ordinary.passed is False
    assert "ordinary_user_high_risk_sandbox_allowed" in failed_ordinary.message


def test_platform_runtime_evidence_rejects_sdk_only_placeholder_lease_projection(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-211.v1",
                "run_id": "run-a",
                "executor_url": "http://executor.test",
                "runtime_mode": "platform",
                "sandbox_provider": "docker",
                "executed_task": True,
                "callback_auth": "token",
                "executor": {
                    "sdk_used": True,
                    "executor_mode": "claude_agent_sdk",
                },
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "callbacks": [
                    {"run_id": "run-a", "status": "running"},
                    {"run_id": "run-a", "status": "completed"},
                ],
                "non_expansion_invariants": {
                    "ordinary_user_high_risk_sandbox_allowed": False,
                    "admin_or_allowlist_only": True,
                    "production_concurrency_defaults_raised": False,
                    "docker_sandbox_production_hardening_claimed": False,
                    "ordinary_user_multi_agent_allowed": False,
                },
                "lease_projection": {
                    "provider": "fake",
                    "lease_payload": {
                        "source": "sdk_only_lifecycle_placeholder",
                        "evidence_class": "sdk_only_lifecycle_placeholder",
                    },
                },
                "timings": {
                    "schema_version": "ai-platform.sandbox-latency-split.v1",
                    "sandbox_queue_wait_latency_ms": 0,
                    "sandbox_lease_acquire_latency_ms": 1,
                    "sandbox_container_start_latency_ms": 1,
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                    "sandbox_executor_dispatch_latency_ms": 4,
                    "executor_first_token_latency_ms": 5,
                    "executor_tool_call_latency_ms": 0,
                    "executor_model_latency_ms": 5,
                    "document_processing_latency_ms": 6,
                    "artifact_upload_latency_ms": 0,
                    "sandbox_cleanup_latency_ms": 7,
                    "sandbox_total_latency_ms": 34,
                },
            }
        ),
        encoding="utf-8",
    )

    failed = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")

    assert failed.passed is False
    assert "placeholder" in failed.message


def test_opensandbox_provider_lifecycle_evidence_requires_first_stage_probe_fields(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    lifecycle = {
        "schema_version": "ai-platform.opensandbox-provider-lifecycle.v1",
        "provider": "opensandbox",
        "run_id": "run-a",
        "lifecycle": {
            "create_observed": True,
            "delete_observed": True,
            "container_id_present": True,
            "executor_endpoint_present": True,
        },
        "db_lease": {
            "recorded": True,
            "released": True,
            "release_reason": "dispatch_completed",
            "recorded_scope_matches_request": True,
        },
        "startup_io": {
            "file_write_read_verified": True,
            "command_execution_verified": True,
            "source": "OpenSandboxContainerProvider.startup_io_probe",
        },
        "resource_policy": {
            "resource_limits_requested": True,
            "memory_mb": 512,
            "cpu_count": 0.5,
            "pids_limit": 128,
            "policy_projection_source": "provider_request",
        },
        "egress_policy": {
            "policy_requested": True,
            "callback_host_allowlisted": True,
            "policy_projection_source": "provider_request",
        },
        "dispatch": {
            "executor_response_present": True,
            "callback_stream_observed": True,
            "sdk_executor_observed": True,
        },
        "redaction": {
            "host_paths_redacted": True,
            "secrets_absent": True,
        },
    }
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-211.v1",
                "run_id": "run-a",
                "runtime_mode": "platform",
                "sandbox_provider": "opensandbox",
                "provider_lifecycle": lifecycle,
            }
        ),
        encoding="utf-8",
    )

    passed = verifier.check_opensandbox_provider_lifecycle_evidence(evidence, run_id="run-a")

    assert passed.passed is True

    broken = dict(lifecycle)
    broken["startup_io"] = {**lifecycle["startup_io"], "command_execution_verified": False}
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-211.v1",
                "run_id": "run-a",
                "runtime_mode": "platform",
                "sandbox_provider": "opensandbox",
                "provider_lifecycle": broken,
            }
        ),
        encoding="utf-8",
    )
    failed = verifier.check_opensandbox_provider_lifecycle_evidence(evidence, run_id="run-a")

    assert failed.passed is False
    assert "startup_io.command_execution_verified" in failed.message


def test_platform_runtime_evidence_rejects_hidden_or_invalid_latency_split(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    timings = {
        "schema_version": "ai-platform.sandbox-latency-split.v1",
        "sandbox_queue_wait_latency_ms": 0,
        "sandbox_lease_acquire_latency_ms": 1,
        "sandbox_container_start_latency_ms": 1,
        "sandbox_container_cold_start_latency_ms": 5,
        "sandbox_healthcheck_latency_ms": 1,
        "sandbox_executor_dispatch_latency_ms": 2,
        "executor_first_token_latency_ms": 5,
        "executor_tool_call_latency_ms": 0,
        "executor_model_latency_ms": 5,
        "document_processing_latency_ms": 3,
        "artifact_upload_latency_ms": 0,
        "sandbox_cleanup_latency_ms": 1,
        "sandbox_total_latency_ms": 24,
    }
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-211.v1",
                "run_id": "run-a",
                "runtime_mode": "platform",
                "sandbox_provider": "docker",
                "timings": timings,
            }
        ),
        encoding="utf-8",
    )

    failed_equal = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")

    assert failed_equal.passed is False
    assert "cold start" in failed_equal.message

    timings["sandbox_container_cold_start_latency_ms"] = -1
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-211.v1",
                "run_id": "run-a",
                "runtime_mode": "platform",
                "sandbox_provider": "docker",
                "timings": timings,
            }
        ),
        encoding="utf-8",
    )
    failed_negative = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_negative.passed is False
    assert "non-negative" in failed_negative.message


def test_platform_runtime_hardening_requires_isolation_cleanup_and_fallback_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    hardening = {
        "lease_isolation": {
            "evidence_class": "live_platform_probe",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "recorded_lease_id": "lease-a",
            "released_lease_id": "lease-a",
            "release_reason": "dispatch_completed",
            "host_paths_redacted": True,
        },
        "workspace_isolation": {
            "evidence_class": "live_platform_probe",
            "workspace_container_path": "/workspace",
            "inputs_container_path": "/workspace/inputs",
            "host_paths_redacted": True,
            "marker_path_is_container_path": True,
        },
        "cleanup": {
            "evidence_class": "live_platform_probe",
            "ephemeral_container_removed": True,
            "cancel_probe_container_removed": True,
            "active_lease_released": True,
        },
        "resource_timeout": observed_resource_timeout_hardening(),
        "failure_fallback": {
            "evidence_class": "source_regression_guard",
            "dispatch_failure_stops_container": True,
            "lease_record_failure_stops_container": True,
            "db_lease_not_released_when_stop_fails": True,
            "source_regression_tests": [
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
            ],
        },
        "cached_lease_revalidation": {
            "evidence_class": "source_regression_guard",
            "cached_lease_revalidates_scope_labels": True,
            "scope_mismatch_fails_closed": True,
            "tenant_workspace_user_session_checked": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels"
            ],
        },
        "resource_limits": observed_resource_limits_hardening(),
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "172.17.0.1",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
            "probe_source": "runtime_probe_results",
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": hardening}), encoding="utf-8")

    blocked = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert blocked.passed is False
    assert blocked.message == "hardening evidence blocked: resource_limits.bounded_error_projection_observer"

    broken = dict(hardening)
    broken["cleanup"] = {**hardening["cleanup"], "ephemeral_container_removed": False}
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": broken}), encoding="utf-8")
    failed = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed.passed is False
    assert "ephemeral_container_removed" in failed.message

    missing_cached_revalidation = dict(hardening)
    missing_cached_revalidation.pop("cached_lease_revalidation")
    evidence.write_text(
        json.dumps({"run_id": "run-a", "hardening": missing_cached_revalidation}),
        encoding="utf-8",
    )
    failed_cached = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_cached.passed is False
    assert "cached_lease_revalidation" in failed_cached.message

    wrong_evidence_class = dict(hardening)
    wrong_evidence_class["resource_timeout"] = {
        **hardening["resource_timeout"],
        "evidence_class": "source_regression_guard",
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": wrong_evidence_class}), encoding="utf-8")
    failed_class = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_class.passed is False
    assert "evidence_class" in failed_class.message

    missing_runtime_subject = dict(hardening)
    missing_runtime_subject["resource_timeout"] = {
        **hardening["resource_timeout"],
        "runtime_subject": "",
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": missing_runtime_subject}), encoding="utf-8")
    failed_runtime_subject = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_runtime_subject.passed is False
    assert "runtime_subject" in failed_runtime_subject.message

    mismatched_timeout_run = dict(hardening)
    mismatched_timeout_run["resource_timeout"] = {
        **hardening["resource_timeout"],
        "run_id": "run-b",
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": mismatched_timeout_run}), encoding="utf-8")
    failed_timeout_run = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_timeout_run.passed is False
    assert "resource_timeout.run_id" in failed_timeout_run.message

    missing_resource_limits = dict(hardening)
    missing_resource_limits.pop("resource_limits")
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": missing_resource_limits}), encoding="utf-8")
    failed_resource_limits = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_resource_limits.passed is False
    assert "resource_limits" in failed_resource_limits.message

    self_asserted_bounded_projection = dict(hardening)
    self_asserted_bounded_projection["resource_limits"] = {
        **hardening["resource_limits"],
        "bounded_error_projection_verified": True,
    }
    self_asserted_bounded_projection["resource_limits"].pop("bounded_error_projection")
    evidence.write_text(
        json.dumps({"run_id": "run-a", "hardening": self_asserted_bounded_projection}),
        encoding="utf-8",
    )
    failed_projection = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_projection.passed is False
    assert "resource_limits.bounded_error_projection" in failed_projection.message

    unsafe_egress = dict(hardening)
    unsafe_egress["egress_policy"] = {
        **hardening["egress_policy"],
        "default_deny_outbound": False,
    }
    failed_egress = verifier._egress_policy_hardening_error(unsafe_egress["egress_policy"])
    assert failed_egress == "hardening evidence missing: egress_policy.default_deny_outbound"

    privileged_container = dict(hardening)
    privileged_container["security_options"] = {
        **hardening["security_options"],
        "privileged": True,
    }
    failed_security = verifier._security_options_hardening_error(privileged_container["security_options"])
    assert failed_security == "hardening evidence missing: security_options.privileged"


def test_no_secret_leakage_rejects_sensitive_evidence(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "callbacks": [{"status": "running"}],
                "leak": "/var/run/docker.sock",
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is False
    assert "/var/run/docker.sock" not in result.message


def test_no_secret_leakage_rejects_generic_token_and_bearer(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"token":"abc","auth":"Bearer abc"}', encoding="utf-8")

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is False
    assert "Bearer abc" not in result.message


def test_no_secret_leakage_rejects_standalone_json_token(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"token":"abc"}', encoding="utf-8")

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is False
    assert "abc" not in result.message


def test_no_secret_leakage_rejects_secret_and_authorization_values(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"secret":"abc","authorization":"Basic abc"}', encoding="utf-8")

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is False
    assert "abc" not in result.message


def test_no_secret_leakage_rejects_nested_secret_and_authorization_values(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "secret": {"value": "abc"},
                "authorization": {"header": "Basic abc"},
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is False
    assert "abc" not in result.message


def test_no_secret_leakage_rejects_denied_target_query_secrets(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "egress_policy": {
                    "denied_egress_redacted": False,
                    "denied_target": "https://blocked.invalid/path?token=abc",
                    "denied_probe_error_code": "egress_denied",
                }
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is False
    assert "abc" not in result.message


def test_no_secret_leakage_rejects_denied_target_secret_query_aliases(tmp_path):
    verifier = load_verifier()
    for query in ("client_secret=abc", "api_key=secret-value"):
        evidence = tmp_path / f"evidence-{query.split('=')[0]}.json"
        evidence.write_text(
            json.dumps(
                {
                    "egress_policy": {
                        "denied_egress_redacted": False,
                        "denied_target": f"https://blocked.invalid/path?{query}",
                        "denied_probe_error_code": "egress_denied",
                    }
                }
            ),
            encoding="utf-8",
        )

        result = verifier.check_no_secret_leakage(evidence)

        assert result.passed is False
        assert "abc" not in result.message
        assert "secret-value" not in result.message


def test_no_secret_leakage_allows_safe_absence_field_names(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "resource_limits": {
                    "bounded_error_projection": {
                        "source": "admin_runtime_projection",
                        "run_id": "run-a",
                        "status": "failed",
                        "error_code": "executor_health_timeout",
                        "host_paths_redacted": True,
                        "raw_docker_payload_absent": True,
                        "callback_token_absent": True,
                    },
                },
                "egress_policy": {
                    "denied_egress_redacted": True,
                    "authorization_header_absent": True,
                    "secret_values_absent": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is True


def test_no_secret_leakage_allows_callback_token_absent_bounded_projection(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "hardening": {
                    "resource_limits": {
                        "bounded_error_projection": {
                            "source": "admin_runtime_projection",
                            "run_id": "run-a",
                            "status": "failed",
                            "error_code": "executor_health_timeout",
                            "host_paths_redacted": True,
                            "raw_docker_payload_absent": True,
                            "callback_token_absent": True,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is True


def test_no_secret_leakage_allows_safe_token_evidence_fields(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "callback_auth": "token",
                "egress_policy": {
                    "callback_exception_scoped_to_run_token": True,
                },
                "timings": {
                    "token_counts": {"input": 10, "output": 12, "total": 22},
                    "input_tokens": 10,
                    "output_tokens": 12,
                },
            }
        ),
        encoding="utf-8",
    )

    result = verifier.check_no_secret_leakage(evidence)

    assert result.passed is True


def test_docker_socket_probe_sanitizes_errors():
    verifier = load_verifier()

    def fake_run(cmd, capture_output, text, timeout, check):
        raise RuntimeError("permission denied: /var/run/docker.sock")

    result = verifier.check_docker_socket(run=fake_run)

    assert result.passed is False
    assert "/var/run/docker.sock" not in result.message


def test_docker_socket_probe_sanitizes_url_encoded_socket_errors():
    verifier = load_verifier()

    def fake_run(cmd, capture_output, text, timeout, check):
        completed = type(
            "Completed",
            (),
            {
                "returncode": 1,
                "stderr": "Get http://%2Fvar%2Frun%2Fdocker.sock/v1.45/version: permission denied",
                "stdout": "",
            },
        )()
        return completed

    result = verifier.check_docker_socket(run=fake_run)

    assert result.passed is False
    assert "%2Fvar%2Frun%2Fdocker.sock" not in result.message


def test_main_json_reports_all_checks_as_structured_output(tmp_path, capsys):
    verifier = load_verifier()
    missing = tmp_path / "missing.json"

    exit_code = verifier.main(
        [
            "--workspace-root",
            str(tmp_path),
            "--executor-url",
            "",
            "--evidence-file",
            str(missing),
            "--docker-cmd",
            "definitely-missing-docker-command",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert {item["name"] for item in output["checks"]} == {
        "check_docker_socket",
        "check_workspace_write",
        "check_executor_health",
        "check_callback_stream",
        "check_cancel_stops_container",
        "check_platform_runtime_evidence",
        "check_opensandbox_provider_lifecycle_evidence",
        "check_platform_hardening_evidence",
        "check_no_secret_leakage",
    }


def test_script_help_bootstraps_current_repo_before_importing_app_modules():
    generator_result = subprocess.run(
        [sys.executable, "scripts/generate_sandbox_runtime_evidence_211.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    verifier_result = subprocess.run(
        [sys.executable, "scripts/verify_sandbox_runtime_211.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert generator_result.returncode == 0
    assert "Generate ai-platform sandbox runtime evidence on 211" in generator_result.stdout
    assert verifier_result.returncode == 0
    assert "Verify ai-platform sandbox runtime on 211" in verifier_result.stdout


def test_generator_parser_accepts_opensandbox_provider():
    generator = load_generator()

    args = generator.build_parser().parse_args(["--sandbox-provider", "opensandbox", "--skip-live-submit"])

    assert args.sandbox_provider == "opensandbox"


def test_evidence_recorder_writes_sanitized_callback_evidence(tmp_path):
    generator = load_generator()
    evidence_path = tmp_path / "evidence.json"
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://127.0.0.1:18000",
        callback_token="secret-token",
    )

    assert recorder.record_callback({"run_id": "run-a", "status": "running", "progress": 5}, "secret-token") is True
    assert (
        recorder.record_callback({"run_id": "run-a", "status": "completed", "progress": 100}, "secret-token")
        is True
    )
    recorder.executed_task = True
    recorder.executor = {
        "sdk_used": True,
        "executor_mode": "claude_agent_sdk",
        "sdk_session_id": "sdk-session-a",
    }
    recorder.cancel_stops_container = True
    recorder.cancelled_container_id = "verifier-run-a"
    recorder.runtime_mode = "platform"
    recorder.sandbox_provider = "docker"
    recorder.timings = {
        "schema_version": "ai-platform.sandbox-latency-split.v1",
        "sandbox_lease_acquire_latency_ms": 1,
        "sandbox_container_cold_start_latency_ms": 2,
        "sandbox_healthcheck_latency_ms": 3,
        "sandbox_executor_dispatch_latency_ms": 4,
        "executor_model_latency_ms": 5,
        "document_processing_latency_ms": 6,
        "sandbox_cleanup_latency_ms": 7,
        "sandbox_total_latency_ms": 28,
    }
    recorder.hardening = {
        "lease_isolation": {
            "evidence_class": "live_platform_probe",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "recorded_lease_id": "lease-a",
            "released_lease_id": "lease-a",
            "release_reason": "dispatch_completed",
            "host_paths_redacted": True,
        }
    }
    recorder.write(evidence_path)

    raw = evidence_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["run_id"] == "run-a"
    assert data["executor_url"] == "http://127.0.0.1:18000"
    assert data["executed_task"] is True
    assert data["callback_auth"] == "token"
    assert data["executor"] == {
        "sdk_used": True,
        "executor_mode": "claude_agent_sdk",
        "sdk_session_id": "sdk-session-a",
    }
    assert data["schema_version"] == "ai-platform.sandbox-runtime-211.v1"
    assert data["runtime_mode"] == "platform"
    assert data["sandbox_provider"] == "docker"
    assert data["timings"]["schema_version"] == "ai-platform.sandbox-latency-split.v1"
    assert data["hardening"]["lease_isolation"]["recorded_lease_id"] == "lease-a"
    assert data["non_expansion_invariants"] == {
        "ordinary_user_high_risk_sandbox_allowed": False,
        "admin_or_allowlist_only": True,
        "production_concurrency_defaults_raised": False,
        "docker_sandbox_production_hardening_claimed": False,
        "ordinary_user_multi_agent_allowed": False,
    }
    assert [item["status"] for item in data["callbacks"]] == ["running", "completed"]
    assert "secret-token" not in raw


def test_submit_executor_task_marks_executed_after_http_success():
    generator = load_generator()
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return None

            def read(self):
                return b'{"status":"accepted","run_id":"run-a"}'

        return Response()

    response = generator.submit_executor_task(
        executor_url="http://executor.test",
        callback_url="http://callback.test/callback",
        callback_token="secret-token",
        run_id="run-a",
        workspace_root="/workspace",
        urlopen=fake_urlopen,
    )

    assert response == {"status": "accepted", "run_id": "run-a"}
    request = requests[0][0]
    assert request.full_url == "http://executor.test/v1/tasks/execute"
    assert request.get_method() == "POST"
    body = json.loads(request.data.decode("utf-8"))
    assert body["run_id"] == "run-a"
    assert body["callback_token_id"] == "callback-run-a"
    assert body["callback_url"] == "http://callback.test/callback"
    assert body["callback_token"] == "secret-token"
    assert body["config"]["resource_limits"]["max_seconds"] == 60


def test_submit_executor_task_derives_platform_callback_auth():
    generator = load_generator()
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return None

            def read(self):
                return b'{"status":"accepted","run_id":"run-a"}'

        return Response()

    response = generator.submit_executor_task(
        executor_url="http://executor.test",
        callback_url="http://platform.test/api/ai/runtime/callbacks/executor",
        callback_token="secret-token",
        run_id="run-a",
        workspace_root="/workspace",
        urlopen=fake_urlopen,
    )

    from app.runtime.sandbox.callback_tokens import derive_callback_token

    assert response == {"status": "accepted", "run_id": "run-a"}
    body = json.loads(requests[0][0].data.decode("utf-8"))
    assert body["callback_token_id"] == "cbt_run-a"
    assert body["callback_token"] == derive_callback_token("secret-token", "cbt_run-a")


def test_run_platform_runtime_probe_records_timings_and_hardening(tmp_path):
    generator = load_generator()
    verifier = load_verifier()

    async def fake_probe():
        return type(
            "SandboxRuntimeResult",
            (),
            {
                "status": "accepted",
                "session_id": "session-run-a",
                "run_id": "run-a",
                "executor_response": {
                    "status": "accepted",
                    "run_id": "run-a",
                    "sdk_used": True,
                    "executor_mode": "claude_agent_sdk",
                    "sdk_session_id": "sdk-session-a",
                    "executor_model_latency_ms": 5,
                    "document_processing_latency_ms": 6,
                },
                "timings": {
                    "schema_version": "ai-platform.sandbox-latency-split.v1",
                    "sandbox_lease_acquire_latency_ms": 1,
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                    "sandbox_executor_dispatch_latency_ms": 4,
                    "executor_model_latency_ms": 5,
                    "document_processing_latency_ms": 6,
                    "sandbox_cleanup_latency_ms": 7,
                    "sandbox_total_latency_ms": 28,
                },
            },
        )()

    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )

    result = generator.record_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider="docker",
        workspace_root=tmp_path,
        probe=fake_probe,
    )

    assert result["status"] == "accepted"
    assert recorder.runtime_mode == "platform"
    assert recorder.sandbox_provider == "docker"
    assert recorder.executor == {
        "sdk_used": True,
        "executor_mode": "claude_agent_sdk",
        "sdk_session_id": "sdk-session-a",
    }
    assert recorder.timings["sandbox_container_cold_start_latency_ms"] == 2
    assert recorder.hardening["workspace_isolation"]["workspace_container_path"] == "/workspace"
    assert recorder.hardening["cleanup"]["ephemeral_container_removed"] is True
    assert recorder.hardening["cached_lease_revalidation"] == {
        "evidence_class": "source_regression_guard",
        "cached_lease_revalidates_scope_labels": True,
        "scope_mismatch_fails_closed": True,
        "tenant_workspace_user_session_checked": True,
        "source_regression_tests": [
            "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels"
        ],
    }
    assert recorder.hardening["resource_limits"] == {
        "evidence_class": "live_platform_probe",
        "memory_limit_mb": 512,
        "cpu_limit_count": 0,
        "pids_limit": 128,
        "process_timeout_seconds": 60,
        "limit_source": "platform_request",
        "docker_inspection_verified": False,
        "over_limit_cleanup_verified": False,
        "bounded_error_projection_verified": False,
        "max_seconds_enforced": False,
    }
    assert recorder.hardening["egress_policy"] == {
        "evidence_class": "live_platform_probe",
        "default_deny_outbound": False,
        "platform_allowlist_enforced": False,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": False,
        "denied_target": "",
        "denied_probe_error_code": "",
        "allowed_callback_host": "",
        "callback_probe_status": "",
        "policy_source": "not_runtime_verified",
        "probe_source": "",
        "network_inspection_verified": False,
        "docker_network_masquerade_disabled": False,
    }
    assert recorder.hardening["security_options"] == {
        "evidence_class": "live_platform_probe",
        "privileged": False,
        "no_new_privileges": False,
        "capabilities_dropped": False,
        "docker_socket_mounted": False,
        "workspace_mount_mode": "rw",
        "root_filesystem_read_only_or_minimal": False,
    }
    for section_name, allowed_tests in verifier.ALLOWED_SOURCE_REGRESSION_TESTS.items():
        assert set(recorder.hardening[section_name]["source_regression_tests"]) <= allowed_tests


def test_run_platform_runtime_probe_uses_configured_sdk_model(monkeypatch, tmp_path):
    generator = load_generator()

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease

            assert request.model == "deepseek-v4-pro"
            lease = ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="docker",
                executor_url="http://127.0.0.1:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                labels={"ai-platform.run_id": request.run_id},
                timings={},
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            await self.release_lease(lease, "dispatch_completed", lease_id)
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "accepted",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {
                        "status": "accepted",
                        "run_id": request.run_id,
                        "sdk_used": True,
                        "executor_mode": "claude_agent_sdk",
                    },
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_model_latency_ms": 5,
                        "document_processing_latency_ms": 6,
                        "sandbox_cleanup_latency_ms": 7,
                        "sandbox_total_latency_ms": 28,
                    },
                },
            )()

    def fake_run(cmd, capture_output, text, timeout, check):
        assert tuple(cmd) == ("docker", "inspect", "executor-exec-run-a")
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "HostConfig": {
                                "Memory": 536870912,
                                "NanoCpus": 500000000,
                                "PidsLimit": 128,
                                "Privileged": False,
                                "SecurityOpt": ["no-new-privileges:true"],
                                "CapDrop": ["ALL"],
                                "ReadonlyRootfs": True,
                                "Binds": ["/tmp/workspace:/workspace:rw"],
                            },
                            "Mounts": [{"Destination": "/workspace", "RW": True}],
                        }
                    ]
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)
    monkeypatch.setattr(generator, "_configured_platform_runtime_model", lambda settings: "deepseek-v4-pro")
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )

    result = generator.run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider="docker",
        sandbox_executor_image="ai-platform:local",
        workspace_root=str(tmp_path),
        callback_url="http://callback.test/callback",
        docker_cmd=("docker",),
        run=fake_run,
    )

    assert result["status"] == "accepted"
    assert recorder.executor["sdk_used"] is True
    assert recorder.executor["executor_mode"] == "claude_agent_sdk"


def test_run_platform_runtime_probe_records_opensandbox_lifecycle_projection(monkeypatch, tmp_path):
    generator = load_generator()
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease
            from app.settings import get_settings

            assert get_settings().sandbox_container_provider == "opensandbox"
            assert request.resource_limits == {
                "max_seconds": 60,
                "memory_mb": 512,
                "cpu_count": 0.5,
                "pids_limit": 128,
            }
            lease = ContainerLease(
                container_id="osb-run-a",
                container_name="opensandbox-run-a",
                provider="opensandbox",
                executor_url="http://opensandbox-executor.test:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path / "workspace"),
                workspace_container_path="/workspace",
                labels={
                    "ai-platform.provider_backend": "opensandbox",
                    "ai-platform.egress.policy": "opensandbox-network-policy",
                    "ai-platform.egress.callback_host": "host.docker.internal",
                },
                timings={
                    "sandbox_container_start_latency_ms": 2,
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                },
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path / "workspace"),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            assert recorder.record_callback(
                {"run_id": request.run_id, "status": "running", "progress": 10},
                "secret-token",
            )
            assert recorder.record_callback(
                {"run_id": request.run_id, "status": "completed", "progress": 100},
                "secret-token",
            )
            await self.release_lease(lease, "dispatch_completed", lease_id)
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "accepted",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {
                        "status": "accepted",
                        "run_id": request.run_id,
                        "sdk_used": True,
                        "executor_mode": "claude_agent_sdk",
                        "sdk_session_id": "sdk-session-a",
                    },
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_queue_wait_latency_ms": 0,
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_start_latency_ms": 2,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_first_token_latency_ms": 0,
                        "executor_tool_call_latency_ms": 0,
                        "executor_model_latency_ms": 5,
                        "document_processing_latency_ms": 0,
                        "artifact_upload_latency_ms": 0,
                        "sandbox_cleanup_latency_ms": 6,
                        "sandbox_total_latency_ms": 21,
                    },
                },
            )()

    def fake_run(cmd, capture_output, text, timeout, check):
        return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "not a docker container"})()

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)

    result = generator.run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider="opensandbox",
        sandbox_executor_image="ai-platform:local",
        workspace_root=str(tmp_path),
        callback_url="http://callback.test/callback",
        docker_cmd=("docker",),
        run=fake_run,
    )

    assert result["status"] == "accepted"
    assert recorder.provider_lifecycle["provider"] == "opensandbox"
    assert recorder.provider_lifecycle["lifecycle"] == {
        "create_observed": True,
        "delete_observed": True,
        "container_id_present": True,
        "executor_endpoint_present": True,
    }
    assert recorder.provider_lifecycle["startup_io"] == {
        "file_write_read_verified": True,
        "command_execution_verified": True,
        "source": "OpenSandboxContainerProvider.startup_io_probe",
    }
    assert recorder.provider_lifecycle["resource_policy"]["resource_limits_requested"] is True
    assert recorder.provider_lifecycle["egress_policy"] == {
        "policy_requested": True,
        "callback_host_allowlisted": True,
        "policy_projection_source": "provider_request",
    }
    assert recorder.provider_lifecycle["dispatch"] == {
        "executor_response_present": True,
        "callback_stream_observed": True,
        "sdk_executor_observed": True,
    }
    serialized = json.dumps(recorder.to_dict())
    assert str(tmp_path) not in serialized
    assert "secret-token" not in serialized


def test_platform_hardening_evidence_maps_runtime_docker_inspection_and_probe_results(tmp_path):
    generator = load_generator()

    docker_inspect = {
        "HostConfig": {
            "Memory": 536870912,
            "NanoCpus": 500000000,
            "PidsLimit": 128,
            "Privileged": False,
            "SecurityOpt": ["no-new-privileges:true"],
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "Binds": ["/tmp/workspace:/workspace:rw"],
        },
        "Mounts": [
            {
                "Source": "/tmp/workspace",
                "Destination": "/workspace",
                "RW": True,
            }
        ],
    }
    runtime_probe_results = {
        "resource_limits": observed_resource_probe(),
        "egress_policy": {
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "172.17.0.1",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
            "probe_source": "runtime_probe_results",
        },
    }

    hardening = generator._platform_hardening_evidence(
        run_id="run-a",
        workspace_root=tmp_path,
        recorded_lease_id="lease-a",
        released_lease_id="lease-a",
        release_reason="dispatch_completed",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        docker_inspect=docker_inspect,
        runtime_probe_results=runtime_probe_results,
    )

    expected_resource_limits = observed_resource_limits_hardening()
    expected_resource_limits["bounded_error_projection_verified"] = False
    expected_resource_limits.pop("bounded_error_projection")
    expected_resource_limits.pop("bounded_error_projection_source")
    assert hardening["resource_limits"] == expected_resource_limits
    assert hardening["egress_policy"] == {
        "evidence_class": "live_platform_probe",
        "default_deny_outbound": True,
        "platform_allowlist_enforced": True,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": True,
        "denied_target": "https://egress-denied.invalid/",
        "denied_probe_error_code": "egress_denied",
        "allowed_callback_host": "172.17.0.1",
        "callback_probe_status": "delivered",
        "policy_source": "platform_policy",
        "probe_source": "runtime_probe_results",
        "network_inspection_verified": False,
        "docker_network_masquerade_disabled": False,
    }
    assert hardening["security_options"] == {
        "evidence_class": "live_platform_probe",
        "privileged": False,
        "no_new_privileges": True,
        "capabilities_dropped": True,
        "docker_socket_mounted": False,
        "workspace_mount_mode": "rw",
        "root_filesystem_read_only_or_minimal": True,
    }


def test_platform_hardening_records_network_inspect_without_claiming_denied_egress(tmp_path):
    generator = load_generator()

    docker_inspect = {
        "HostConfig": {
            "NetworkMode": "ai-platform-sandbox-egress",
            "Memory": 536870912,
            "NanoCpus": 500000000,
            "PidsLimit": 128,
            "Privileged": False,
            "SecurityOpt": ["no-new-privileges:true"],
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "Binds": ["/tmp/workspace:/workspace:rw"],
            "ExtraHosts": ["host.docker.internal:host-gateway"],
        },
        "Mounts": [
            {
                "Source": "/tmp/workspace",
                "Destination": "/workspace",
                "RW": True,
            }
        ],
        "NetworkSettings": {
            "Networks": {
                "ai-platform-sandbox-egress": {
                    "NetworkID": "net-a",
                    "Gateway": "192.168.48.1",
                    "IPAMConfig": None,
                }
            }
        },
        "Config": {
            "Labels": {
                "ai-platform.egress.policy": "default-deny-no-masq",
                "ai-platform.egress.network": "ai-platform-sandbox-egress",
                "ai-platform.egress.callback_host": "host.docker.internal",
            },
        },
    }
    docker_network_inspect = {
        "Name": "ai-platform-sandbox-egress",
        "Driver": "bridge",
        "Options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
    }

    hardening = generator._platform_hardening_evidence(
        run_id="run-a",
        workspace_root=tmp_path,
        recorded_lease_id="lease-a",
        released_lease_id="lease-a",
        release_reason="dispatch_completed",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        docker_inspect=docker_inspect,
        docker_network_inspect=docker_network_inspect,
        callbacks=[
            {"run_id": "run-a", "status": "running"},
            {"run_id": "run-a", "status": "completed"},
        ],
    )

    assert hardening["egress_policy"] == {
        "evidence_class": "live_platform_probe",
        "default_deny_outbound": False,
        "platform_allowlist_enforced": False,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": False,
        "denied_target": "",
        "denied_probe_error_code": "",
        "allowed_callback_host": "host.docker.internal",
        "callback_probe_status": "delivered",
        "policy_source": "not_runtime_verified",
        "probe_source": "docker_network_inspect",
        "network_inspection_verified": True,
        "docker_network_masquerade_disabled": True,
    }


def test_platform_hardening_does_not_derive_egress_policy_without_network_inspect(tmp_path):
    generator = load_generator()

    docker_inspect = {
        "HostConfig": {
            "NetworkMode": "ai-platform-sandbox-egress",
            "ExtraHosts": ["host.docker.internal:host-gateway"],
        },
        "NetworkSettings": {"Networks": {"ai-platform-sandbox-egress": {}}},
        "Config": {
            "Labels": {
                "ai-platform.egress.policy": "default-deny-no-masq",
                "ai-platform.egress.network": "ai-platform-sandbox-egress",
                "ai-platform.egress.callback_host": "host.docker.internal",
            },
        },
    }

    hardening = generator._platform_hardening_evidence(
        run_id="run-a",
        workspace_root=tmp_path,
        recorded_lease_id="lease-a",
        released_lease_id="lease-a",
        release_reason="dispatch_completed",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        docker_inspect=docker_inspect,
        callbacks=[{"status": "completed"}],
    )

    assert hardening["egress_policy"]["default_deny_outbound"] is False
    assert hardening["egress_policy"]["network_inspection_verified"] is False
    assert hardening["egress_policy"]["policy_source"] == "not_runtime_verified"


def test_platform_hardening_does_not_derive_egress_policy_without_callback_delivery(tmp_path):
    generator = load_generator()

    docker_inspect = {
        "HostConfig": {
            "NetworkMode": "ai-platform-sandbox-egress",
            "ExtraHosts": ["host.docker.internal:host-gateway"],
        },
        "NetworkSettings": {"Networks": {"ai-platform-sandbox-egress": {}}},
        "Config": {
            "Labels": {
                "ai-platform.egress.policy": "default-deny-no-masq",
                "ai-platform.egress.network": "ai-platform-sandbox-egress",
                "ai-platform.egress.callback_host": "host.docker.internal",
            },
        },
    }

    hardening = generator._platform_hardening_evidence(
        run_id="run-a",
        workspace_root=tmp_path,
        recorded_lease_id="lease-a",
        released_lease_id="lease-a",
        release_reason="dispatch_completed",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        docker_inspect=docker_inspect,
        callbacks=[{"status": "running"}],
    )

    assert hardening["egress_policy"]["default_deny_outbound"] is False
    assert hardening["egress_policy"]["policy_source"] == "not_runtime_verified"


def test_platform_hardening_evidence_does_not_promote_callback_asserted_projection(tmp_path):
    generator = load_generator()

    unsafe_probe_results = {
        "resource_limits": {
            "over_limit_cleanup_verified": True,
            "bounded_error_projection_verified": True,
        }
    }

    unsafe_hardening = generator._platform_hardening_evidence(
        run_id="run-a",
        workspace_root=tmp_path,
        recorded_lease_id="lease-a",
        released_lease_id="lease-a",
        release_reason="dispatch_completed",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        runtime_probe_results=unsafe_probe_results,
    )

    assert unsafe_hardening["resource_limits"]["bounded_error_projection_verified"] is False
    assert "bounded_error_projection" not in unsafe_hardening["resource_limits"]

    safe_probe_results = {
        "resource_limits": {
            "over_limit_cleanup_verified": True,
            "bounded_error_projection_verified": True,
            "bounded_error_projection": {
                "source": "admin_runtime_projection",
                "run_id": "run-a",
                "status": "failed",
                "error_code": "executor_health_timeout",
                "host_paths_redacted": True,
                "raw_docker_payload_absent": True,
                "callback_token_absent": True,
            },
        }
    }

    safe_hardening = generator._platform_hardening_evidence(
        run_id="run-a",
        workspace_root=tmp_path,
        recorded_lease_id="lease-a",
        released_lease_id="lease-a",
        release_reason="dispatch_completed",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        runtime_probe_results=safe_probe_results,
    )

    assert safe_hardening["resource_limits"]["bounded_error_projection_verified"] is False
    assert "bounded_error_projection" not in safe_hardening["resource_limits"]

def test_platform_resource_probe_derivation_requires_explicit_timeout_probe():
    generator = load_generator()
    result = type(
        "SandboxRuntimeResult",
        (),
        {
            "status": "failed",
            "executor_response": {
                "status": "failed",
                "run_id": "run-a",
                "error_code": "executor_deadline_exceeded",
                "requested_max_seconds": 0.05,
                "timeout_elapsed_ms": 51,
            },
        },
    )()

    assert generator._safe_platform_resource_probe_from_result(
        run_id="run-a",
        result=result,
        release_reason="run_failed",
        platform_resource_timeout_probe=False,
        requested_max_seconds=0.05,
        runtime_identity=observed_runtime_identity(runtime_subject="runtime-sha"),
    ) == {}
    observed_without_projection = generator._safe_platform_resource_probe_from_result(
        run_id="run-a",
        result=result,
        release_reason="run_failed",
        platform_resource_timeout_probe=True,
        requested_max_seconds=0.05,
        runtime_identity=observed_runtime_identity(runtime_subject="runtime-sha"),
    )
    assert observed_without_projection == {
        "probe_kind": "platform_executor_deadline",
        "run_id": "run-a",
        "probe_source": "executor_response",
        "runtime_mode": "platform",
        "runtime_subject": "runtime-sha",
        "runtime_identity": observed_runtime_identity(runtime_subject="runtime-sha"),
        "requested_max_seconds": 0.05,
        "observed_timeout_elapsed_ms": 51,
        "max_seconds_enforced": True,
        "over_limit_cleanup_verified": True,
    }
    observed_with_projection = generator._safe_platform_resource_probe_from_result(
        run_id="run-a",
        result=result,
        release_reason="run_failed",
        platform_resource_timeout_probe=True,
        requested_max_seconds=0.05,
        runtime_identity=observed_runtime_identity(runtime_subject="runtime-sha"),
    )
    assert observed_with_projection == observed_without_projection


def test_resource_limit_verifier_blocks_callback_asserted_admin_projection():
    verifier = load_verifier()

    error = verifier._resource_limits_hardening_error(
        observed_resource_limits_hardening(),
        run_id="run-a",
    )

    assert error == "hardening evidence blocked: resource_limits.bounded_error_projection_observer"


def test_platform_resource_probe_rejects_unbound_or_generic_timeout_claims():
    generator = load_generator()
    for response_patch in (
        {"error_code": "executor_health_timeout"},
        {"run_id": "run-b"},
        {"requested_max_seconds": 0.5},
        {"timeout_elapsed_ms": None},
        {"timeout_elapsed_ms": 5000},
    ):
        response = {
            "status": "failed",
            "run_id": "run-a",
            "error_code": "executor_deadline_exceeded",
            "requested_max_seconds": 0.05,
            "timeout_elapsed_ms": 51,
            **response_patch,
        }
        result = type("SandboxRuntimeResult", (), {"status": "failed", "executor_response": response})()

        assert generator._safe_platform_resource_probe_from_result(
            run_id="run-a",
            result=result,
            release_reason="run_failed",
            platform_resource_timeout_probe=True,
            requested_max_seconds=0.05,
            runtime_identity=observed_runtime_identity(runtime_subject="runtime-sha"),
        ) == {}


def test_runtime_identity_requires_observed_matching_clean_image_metadata():
    generator = load_generator()
    requested_image = "ai-platform:local"
    docker_inspect = {
        "Image": "sha256:" + "a" * 64,
        "Config": {
            "Image": requested_image,
            "Labels": {
                "ai-platform.source_revision": "runtime-sha",
                "org.opencontainers.image.revision": "runtime-sha",
                "ai-platform.source_tree_commit": "runtime-sha",
                "ai-platform.build-dirty": "false",
            },
        },
    }

    assert generator._runtime_identity_from_docker_inspect(
        docker_inspect,
        requested_image=requested_image,
    ) == observed_runtime_identity(runtime_subject="runtime-sha")

    invalid_inspects = []
    for patch in (
        {"Image": ""},
        {"Config": {**docker_inspect["Config"], "Image": "ai-platform:other"}},
        {
            "Config": {
                **docker_inspect["Config"],
                "Labels": {**docker_inspect["Config"]["Labels"], "ai-platform.build-dirty": "true"},
            }
        },
        {
            "Config": {
                **docker_inspect["Config"],
                "Labels": {**docker_inspect["Config"]["Labels"], "org.opencontainers.image.revision": "other"},
            }
        },
    ):
        invalid_inspects.append({**docker_inspect, **patch})

    for invalid in invalid_inspects:
        assert generator._runtime_identity_from_docker_inspect(
            invalid,
            requested_image=requested_image,
        ) == {}


def test_imported_resource_probe_is_not_promoted_without_current_observation():
    generator = load_generator()
    imported = {
        "resource_limits": observed_resource_probe(),
        "egress_policy": {"probe_source": "runtime_probe_results"},
    }

    merged = generator._merge_current_runtime_probe_results(
        imported=imported,
        current_resource_probe={},
        current_egress_probe={},
    )

    assert "resource_limits" not in merged
    assert merged["egress_policy"] == imported["egress_policy"]


def test_deadline_number_helpers_reject_non_finite_values():
    generator = load_generator()
    verifier = load_verifier()

    for value in (float("nan"), float("inf"), float("-inf")):
        assert generator._positive_number(value) is False
        assert verifier._positive_number(value) is False
        assert generator._deadline_elapsed_is_bounded(value, requested_max_seconds=0.05) is False
        assert verifier._deadline_elapsed_is_bounded(value, requested_max_seconds=0.05) is False


def test_positive_deadline_probe_uses_realistic_cooperative_window():
    generator = load_generator()

    assert generator.PLATFORM_DEADLINE_PROBE_SECONDS == 2.0


def test_generator_uses_tight_deadline_elapsed_upper_bound():
    generator = load_generator()

    assert generator._deadline_elapsed_is_bounded(300, requested_max_seconds=0.05) is True
    assert generator._deadline_elapsed_is_bounded(301, requested_max_seconds=0.05) is False
    assert generator._deadline_elapsed_is_bounded(835, requested_max_seconds=0.05) is False
    assert generator._deadline_elapsed_is_bounded(2500, requested_max_seconds=2.0) is True
    assert generator._deadline_elapsed_is_bounded(2501, requested_max_seconds=2.0) is False


def test_verifier_uses_tight_deadline_elapsed_upper_bound():
    verifier = load_verifier()

    assert verifier._deadline_elapsed_is_bounded(300, requested_max_seconds=0.05) is True
    assert verifier._deadline_elapsed_is_bounded(301, requested_max_seconds=0.05) is False
    assert verifier._deadline_elapsed_is_bounded(835, requested_max_seconds=0.05) is False
    assert verifier._deadline_elapsed_is_bounded(2500, requested_max_seconds=2.0) is True
    assert verifier._deadline_elapsed_is_bounded(2501, requested_max_seconds=2.0) is False


def test_generated_default_hardening_payload_does_not_pass_full_runtime_hardening_verifier(tmp_path):
    generator = load_generator()
    verifier = load_verifier()

    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )
    recorder.hardening = generator._platform_hardening_evidence(
        run_id="run-a",
        workspace_root=tmp_path,
        recorded_lease_id="lease-a",
        released_lease_id="lease-a",
        release_reason="dispatch_completed",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps({"run_id": "run-a", "hardening": recorder.hardening}),
        encoding="utf-8",
    )

    failed = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")

    assert failed.passed is False
    assert (
        "resource_timeout.max_seconds_enforced" in failed.message
        or "resource_limits.docker_inspection_verified" in failed.message
        or "resource_limits.over_limit_probe_kind" in failed.message
        or "egress_policy.default_deny_outbound" in failed.message
        or "security_options.no_new_privileges" in failed.message
    )


def test_cancel_probe_stops_only_verifier_owned_container():
    generator = load_generator()
    calls = []

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(tuple(cmd))
        if "create" in cmd:
            return type("Completed", (), {"returncode": 0, "stdout": "container-123\n", "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    container_id = generator.run_cancel_probe(
        run_id="run-a",
        docker_cmd=("docker",),
        cancel_image="ai-platform:local",
        run=fake_run,
    )

    assert container_id == "container-123"
    assert calls[0][:2] == ("docker", "create")
    assert "--label" in calls[0]
    assert "ai-platform.verifier=sandbox-runtime-211" in calls[0]
    assert "ai-platform-sandbox-verifier-run-a" in calls[0]
    assert calls[1] == ("docker", "start", "container-123")
    assert calls[2] == ("docker", "stop", "container-123")
    assert calls[3] == ("docker", "rm", "-f", "container-123")


def test_cancel_probe_does_not_remove_by_name_when_create_fails():
    generator = load_generator()
    calls = []

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(tuple(cmd))
        if "create" in cmd:
            return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "name already exists"})()
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    try:
        generator.run_cancel_probe(
            run_id="run-a",
            docker_cmd=("docker",),
            cancel_image="ai-platform:local",
            run=fake_run,
        )
    except RuntimeError:
        pass

    assert calls == [
        (
            "docker",
            "create",
            "--name",
            "ai-platform-sandbox-verifier-run-a",
            "--label",
            "ai-platform.verifier=sandbox-runtime-211",
            "--label",
            "ai-platform.run_id=run-a",
            "ai-platform:local",
            "sh",
            "-c",
            "sleep 300",
        )
    ]


def test_generator_defaults_use_local_ai_platform_cancel_probe_image():
    generator = load_generator()

    args = generator.build_parser().parse_args([])

    assert args.cancel_image == "ai-platform:local"
    assert args.sandbox_executor_image == ""
    assert args.callback_host == "127.0.0.1"
    assert args.callback_public_url == ""
    assert args.platform_resource_timeout_probe is False


def test_generator_platform_mode_defaults_docker_callback_projection():
    generator = load_generator()

    args = generator.build_parser().parse_args(["--runtime-mode", "platform", "--sandbox-provider", "docker"])

    assert args.callback_host == "0.0.0.0"
    assert args.callback_public_url == "http://host.docker.internal:{port}/callback"


def test_sandbox_runtime_211_help_names_211_docker_command_and_local_cancel_image():
    generator = load_generator()
    verifier = load_verifier()

    generator_help = generator.build_parser().format_help()
    verifier_help = verifier.build_parser().format_help()

    assert "--docker-cmd" in generator_help
    assert "--platform-resource-timeout-probe" in generator_help
    assert "--generate-runtime-probe-results-file" in generator_help
    assert "--denied-egress-target" in generator_help
    assert "sudo -n docker" in generator_help
    assert "on 211" in generator_help
    assert "ai-platform" in generator_help
    assert "local" in generator_help
    assert "--docker-cmd" in verifier_help
    assert "sudo -n docker" in verifier_help
    assert "on 211" in verifier_help


def test_platform_runtime_mode_defaults_executor_image_to_cancel_image(tmp_path, monkeypatch, capsys):
    generator = load_generator()
    calls = []

    def fake_run_platform_runtime_probe(**kwargs):
        calls.append(kwargs)
        recorder = kwargs["recorder"]
        recorder.runtime_mode = "platform"
        recorder.sandbox_provider = kwargs["sandbox_provider"]
        recorder.executed_task = True
        recorder.timings = {
            "schema_version": "ai-platform.sandbox-latency-split.v1",
            "sandbox_lease_acquire_latency_ms": 1,
            "sandbox_container_cold_start_latency_ms": 2,
            "sandbox_healthcheck_latency_ms": 3,
            "sandbox_executor_dispatch_latency_ms": 4,
            "executor_model_latency_ms": 5,
            "document_processing_latency_ms": 6,
            "sandbox_cleanup_latency_ms": 7,
            "sandbox_total_latency_ms": 28,
        }

    monkeypatch.setattr(generator, "run_platform_runtime_probe", fake_run_platform_runtime_probe)
    monkeypatch.setattr(generator, "run_cancel_probe", lambda **kwargs: "container-a")

    evidence = tmp_path / "evidence.json"
    exit_code = generator.main(
        [
            "--runtime-mode",
            "platform",
            "--sandbox-provider",
            "docker",
            "--executor-url",
            "http://executor.test",
            "--evidence-file",
            str(evidence),
            "--run-id",
            "run-a",
            "--callback-timeout",
            "0",
            "--cancel-image",
            "ai-platform:local",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["runtime_mode"] == "platform"
    assert calls[0]["sandbox_executor_image"] == "ai-platform:local"
    assert calls[0]["platform_resource_timeout_probe"] is False


def test_main_can_generate_runtime_probe_results_file(tmp_path, monkeypatch, capsys):
    generator = load_generator()
    calls = []

    def fake_generate_runtime_probe_results(**kwargs):
        calls.append(kwargs)
        output_file = Path(kwargs["output_file"])
        output_file.write_text(
            json.dumps(
                {
                    "schema_version": "ai-platform.sandbox-runtime-probe-results.v1",
                    "run_id": kwargs["recorder"].run_id,
                    "source": "platform_runtime_probe",
                    "resource_limits": observed_resource_probe(run_id=kwargs["recorder"].run_id),
                    "egress_policy": {
                        "default_deny_outbound": True,
                        "platform_allowlist_enforced": True,
                        "callback_exception_scoped_to_run_token": True,
                        "denied_egress_redacted": True,
                        "denied_target": kwargs["denied_egress_target"],
                        "denied_probe_error_code": "egress_denied",
                        "allowed_callback_host": "host.docker.internal",
                        "callback_probe_status": "delivered",
                        "policy_source": "platform_policy",
                        "probe_source": "runtime_probe_results",
                    },
                    "security_options": {
                        "privileged": False,
                        "no_new_privileges": True,
                        "capabilities_dropped": True,
                        "docker_socket_mounted": False,
                        "workspace_mount_mode": "rw",
                        "root_filesystem_read_only_or_minimal": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "run_id": kwargs["recorder"].run_id,
            "runtime_probe_results_file": "[redacted-path]",
            "sections": ["resource_limits", "egress_policy", "security_options"],
        }

    monkeypatch.setattr(generator, "generate_runtime_probe_results", fake_generate_runtime_probe_results)
    monkeypatch.setattr(generator, "run_cancel_probe", lambda **kwargs: "should-not-run")

    runtime_probe_results_file = tmp_path / "runtime-probe-results.json"
    evidence = tmp_path / "evidence.json"
    exit_code = generator.main(
        [
            "--runtime-mode",
            "platform",
            "--sandbox-provider",
            "docker",
            "--executor-url",
            "http://executor.test",
            "--evidence-file",
            str(evidence),
            "--run-id",
            "run-a",
            "--generate-runtime-probe-results-file",
            str(runtime_probe_results_file),
            "--denied-egress-target",
            "https://egress-denied.invalid/",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["runtime_probe_results_file"] == "[redacted-path]"
    assert output["evidence_file"] == "[redacted-path]"
    assert calls[0]["sandbox_executor_image"] == "ai-platform:local"
    assert calls[0]["denied_egress_target"] == "https://egress-denied.invalid/"
    assert str(runtime_probe_results_file) not in json.dumps(output)
    assert runtime_probe_results_file.exists()


def test_main_generate_runtime_probe_results_uses_callback_server(tmp_path, monkeypatch, capsys):
    generator = load_generator()
    runtime_probe_results_file = tmp_path / "runtime-probe-results.json"
    evidence = tmp_path / "evidence.json"

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.callback_token_resolver = callback_token_resolver
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease
            import urllib.request

            assert request.callback_url.startswith("http://127.0.0.1:")
            assert request.callback_url.endswith("/callback")
            assert request.trace_id == "trace_run_a"
            callback_token = self.callback_token_resolver(request.callback_token_id)
            for status in ("running", "failed"):
                callback_payload = {"run_id": request.run_id, "status": status}
                if status == "failed":
                    callback_payload = observed_projection_callback(run_id=request.run_id)
                callback = urllib.request.Request(
                    request.callback_url,
                    data=json.dumps(callback_payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "X-AI-Platform-Callback-Token": callback_token,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(callback, timeout=3) as response:
                    assert response.status == 200

            lease = ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="docker",
                executor_url="http://127.0.0.1:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                labels={"ai-platform.run_id": request.run_id},
                timings={
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                },
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            await self.release_lease(lease, "run_failed", lease_id)
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "failed",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {
                        "status": "failed",
                        "run_id": request.run_id,
                        "error_code": "executor_deadline_exceeded",
                        "error_message": "Executor deadline exceeded",
                        "requested_max_seconds": generator.PLATFORM_DEADLINE_PROBE_SECONDS,
                        "timeout_elapsed_ms": 2001,
                    },
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_model_latency_ms": 0,
                        "document_processing_latency_ms": 0,
                        "sandbox_cleanup_latency_ms": 5,
                        "sandbox_total_latency_ms": 15,
                    },
                },
            )()

    def fake_run(cmd, capture_output, text, timeout, check):
        if tuple(cmd[:3]) == ("docker", "exec", "executor-exec-run-a"):
            return type("Completed", (), {"returncode": 42, "stdout": "", "stderr": ""})()
        assert tuple(cmd) == ("docker", "inspect", "executor-exec-run-a")
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "Image": "sha256:" + "a" * 64,
                            "HostConfig": {
                                "Memory": 536870912,
                                "NanoCpus": 500000000,
                                "PidsLimit": 128,
                                "Privileged": False,
                                "SecurityOpt": ["no-new-privileges:true"],
                                "CapDrop": ["ALL"],
                                "ReadonlyRootfs": True,
                                "Binds": ["/tmp/workspace:/workspace:rw"],
                            },
                            "Mounts": [{"Destination": "/workspace", "RW": True}],
                            "Config": {
                                "Image": "ai-platform:local",
                                "Labels": {
                                    "ai-platform.egress.callback_host": "host.docker.internal",
                                    "ai-platform.source_revision": "local",
                                    "org.opencontainers.image.revision": "local",
                                    "ai-platform.source_tree_commit": "local",
                                    "ai-platform.build-dirty": "false",
                                },
                            },
                        }
                    ]
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)
    monkeypatch.setattr(generator.subprocess, "run", fake_run)

    exit_code = generator.main(
        [
            "--runtime-mode",
            "platform",
            "--sandbox-provider",
            "docker",
            "--executor-url",
            "http://executor.test",
            "--evidence-file",
            str(evidence),
            "--run-id",
            "run-a",
            "--generate-runtime-probe-results-file",
            str(runtime_probe_results_file),
            "--callback-host",
            "127.0.0.1",
            "--callback-timeout",
            "1",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["callbacks"] == 2
    payload = json.loads(runtime_probe_results_file.read_text(encoding="utf-8"))
    assert payload["egress_policy"]["callback_probe_status"] == "delivered"


def test_platform_runtime_mode_accepts_bound_runtime_probe_results_file(tmp_path, monkeypatch, capsys):
    generator = load_generator()
    calls = []
    runtime_probe_results_file = tmp_path / "runtime-probe-results.json"
    runtime_probe_results_file.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-probe-results.v1",
                "run_id": "run-a",
                "source": "platform_runtime_probe",
                "resource_limits": observed_resource_probe(),
                "egress_policy": {
                    "default_deny_outbound": True,
                    "platform_allowlist_enforced": True,
                    "callback_exception_scoped_to_run_token": True,
                    "denied_egress_redacted": True,
                    "denied_target": "https://egress-denied.invalid/",
                    "denied_probe_error_code": "egress_denied",
                    "allowed_callback_host": "172.17.0.1",
                    "callback_probe_status": "delivered",
                    "policy_source": "platform_policy",
                    "probe_source": "runtime_probe_results",
                },
                "security_options": {
                    "privileged": False,
                    "no_new_privileges": True,
                    "capabilities_dropped": True,
                    "docker_socket_mounted": False,
                    "workspace_mount_mode": "rw",
                    "root_filesystem_read_only_or_minimal": True,
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_run_platform_runtime_probe(**kwargs):
        calls.append(kwargs)
        recorder = kwargs["recorder"]
        recorder.runtime_mode = "platform"
        recorder.sandbox_provider = kwargs["sandbox_provider"]
        recorder.executed_task = True
        recorder.record_callback({"run_id": "run-a", "status": "running"}, recorder._callback_token)
        recorder.record_callback({"run_id": "run-a", "status": "completed"}, recorder._callback_token)
        recorder.timings = {
            "schema_version": "ai-platform.sandbox-latency-split.v1",
            "sandbox_lease_acquire_latency_ms": 1,
            "sandbox_container_cold_start_latency_ms": 2,
            "sandbox_healthcheck_latency_ms": 3,
            "sandbox_executor_dispatch_latency_ms": 4,
            "executor_model_latency_ms": 5,
            "document_processing_latency_ms": 6,
            "sandbox_cleanup_latency_ms": 7,
            "sandbox_total_latency_ms": 28,
        }

    monkeypatch.setattr(generator, "run_platform_runtime_probe", fake_run_platform_runtime_probe)
    monkeypatch.setattr(generator, "run_cancel_probe", lambda **kwargs: "container-a")

    evidence = tmp_path / "evidence.json"
    exit_code = generator.main(
        [
            "--runtime-mode",
            "platform",
            "--sandbox-provider",
            "docker",
            "--executor-url",
            "http://executor.test",
            "--evidence-file",
            str(evidence),
            "--run-id",
            "run-a",
            "--callback-timeout",
            "0",
            "--runtime-probe-results-file",
            str(runtime_probe_results_file),
            "--platform-resource-timeout-probe",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["runtime_mode"] == "platform"
    assert calls[0]["platform_resource_timeout_probe"] is True
    assert calls[0]["runtime_probe_results"] == {
        "resource_limits": observed_resource_probe(),
        "egress_policy": {
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "172.17.0.1",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
            "probe_source": "runtime_probe_results",
        },
        "security_options": {
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    assert str(runtime_probe_results_file) not in json.dumps(output)


def test_generate_runtime_probe_results_file_from_platform_probe(tmp_path, monkeypatch):
    generator = load_generator()
    runtime_probe_results_file = tmp_path / "runtime-probe-results.json"

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease

            assert request.resource_limits["max_seconds"] == generator.PLATFORM_DEADLINE_PROBE_SECONDS
            assert request.resource_limits["platform_timeout_probe"] is True
            lease = ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="docker",
                executor_url="http://127.0.0.1:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                labels={"ai-platform.run_id": request.run_id},
                timings={
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                },
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            await self.release_lease(lease, "run_failed", lease_id)
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "failed",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {
                        "status": "failed",
                        "run_id": request.run_id,
                        "error_code": "executor_deadline_exceeded",
                        "error_message": "Executor deadline exceeded",
                        "requested_max_seconds": generator.PLATFORM_DEADLINE_PROBE_SECONDS,
                        "timeout_elapsed_ms": 2001,
                    },
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_model_latency_ms": 0,
                        "document_processing_latency_ms": 0,
                        "sandbox_cleanup_latency_ms": 5,
                        "sandbox_total_latency_ms": 15,
                    },
                },
            )()

    docker_calls = []

    def fake_run(cmd, capture_output, text, timeout, check):
        docker_calls.append(tuple(cmd))
        if tuple(cmd[:3]) == ("docker", "exec", "executor-exec-run-a"):
            assert "https://egress-denied.invalid/" in cmd[-1]
            assert "egress_denied" in cmd[-1]
            return type(
                "Completed",
                (),
                {
                    "returncode": 42,
                    "stdout": "",
                    "stderr": "",
                },
            )()
        assert tuple(cmd) == ("docker", "inspect", "executor-exec-run-a")
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "Image": "sha256:" + "a" * 64,
                            "HostConfig": {
                                "Memory": 536870912,
                                "NanoCpus": 500000000,
                                "PidsLimit": 128,
                                "Privileged": False,
                                "SecurityOpt": ["no-new-privileges:true"],
                                "CapDrop": ["ALL"],
                                "ReadonlyRootfs": True,
                                "Binds": ["/tmp/workspace:/workspace:rw"],
                            },
                            "Mounts": [{"Destination": "/workspace", "RW": True}],
                            "Config": {
                                "Image": "ai-platform:local",
                                "Labels": {
                                    "ai-platform.source_revision": "local",
                                    "org.opencontainers.image.revision": "local",
                                    "ai-platform.source_tree_commit": "local",
                                    "ai-platform.build-dirty": "false",
                                },
                            },
                        }
                    ]
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )
    recorder.record_callback({"run_id": "run-a", "status": "running"}, recorder._callback_token)
    recorder.record_callback(observed_projection_callback(), recorder._callback_token)

    result = generator.generate_runtime_probe_results(
        recorder=recorder,
        sandbox_provider="docker",
        sandbox_executor_image="ai-platform:local",
        workspace_root=str(tmp_path),
        callback_url="http://callback.test/callback",
        docker_cmd=("docker",),
        output_file=runtime_probe_results_file,
        run=fake_run,
    )

    assert result["runtime_probe_results_file"] == "[redacted-path]"
    assert docker_calls[0] == ("docker", "inspect", "executor-exec-run-a")
    assert docker_calls[1][:3] == ("docker", "exec", "executor-exec-run-a")
    payload = json.loads(runtime_probe_results_file.read_text(encoding="utf-8"))
    expected_resource_probe = observed_resource_probe()
    expected_resource_probe["requested_max_seconds"] = generator.PLATFORM_DEADLINE_PROBE_SECONDS
    expected_resource_probe["observed_timeout_elapsed_ms"] = 2001
    expected_resource_probe.pop("bounded_error_projection")
    expected_resource_probe.pop("bounded_error_projection_source")
    assert payload == {
        "schema_version": "ai-platform.sandbox-runtime-probe-results.v1",
        "run_id": "run-a",
        "source": "platform_runtime_probe",
        "resource_limits": expected_resource_probe,
        "egress_policy": {
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "host.docker.internal",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
            "probe_source": "runtime_probe_results",
        },
        "security_options": {
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    assert generator.load_runtime_probe_results(runtime_probe_results_file, run_id="run-a") == {
        "resource_limits": payload["resource_limits"],
        "egress_policy": payload["egress_policy"],
        "security_options": payload["security_options"],
    }
    assert "secret-token" not in runtime_probe_results_file.read_text(encoding="utf-8")


def test_runtime_probe_results_do_not_treat_generic_network_failure_as_egress_denied(tmp_path):
    generator = load_generator()
    probe = generator._safe_platform_egress_probe_from_result(
        run_id="run-a",
        egress_denial_probe={
            "denied": False,
            "target": "https://egress-denied.invalid/",
        },
        docker_inspect={
            "Config": {
                "Labels": {
                    "ai-platform.egress.callback_host": "host.docker.internal",
                },
            },
        },
        callbacks=[{"run_id": "run-a", "status": "running"}, {"run_id": "run-a", "status": "completed"}],
    )

    assert probe == {}


def test_docker_exec_egress_denial_probe_accepts_only_explicit_denial_marker():
    generator = load_generator()

    def completed(returncode):
        return type("Completed", (), {"returncode": returncode, "stdout": "", "stderr": ""})()

    for returncode, expected in [(42, True), (43, False), (0, False), (1, False)]:
        calls = []

        def fake_run(cmd, capture_output, text, timeout, check):
            calls.append(tuple(cmd))
            return completed(returncode)

        probe = generator._docker_exec_egress_denial_probe(
            "executor-exec-run-a",
            denied_target="https://egress-denied.invalid/",
            docker_cmd=("docker",),
            run=fake_run,
        )

        assert probe["denied"] is expected
        assert probe["target"] == "https://egress-denied.invalid/"
        assert calls[0][:3] == ("docker", "exec", "executor-exec-run-a")
        assert "egress_denied" in calls[0][-1]


def test_egress_probe_requires_same_run_running_and_terminal_callbacks():
    generator = load_generator()
    docker_inspect = {
        "Config": {
            "Labels": {
                "ai-platform.egress.callback_host": "host.docker.internal",
            },
        },
    }
    egress_denial_probe = {
        "denied": True,
        "target": "https://egress-denied.invalid/",
    }

    terminal_only = generator._safe_platform_egress_probe_from_result(
        run_id="run-a",
        egress_denial_probe=egress_denial_probe,
        docker_inspect=docker_inspect,
        callbacks=[{"run_id": "run-a", "status": "completed"}],
    )
    wrong_run = generator._safe_platform_egress_probe_from_result(
        run_id="run-a",
        egress_denial_probe=egress_denial_probe,
        docker_inspect=docker_inspect,
        callbacks=[
            {"run_id": "run-b", "status": "running"},
            {"run_id": "run-b", "status": "completed"},
        ],
    )
    complete = generator._safe_platform_egress_probe_from_result(
        run_id="run-a",
        egress_denial_probe=egress_denial_probe,
        docker_inspect=docker_inspect,
        callbacks=[
            {"run_id": "run-a", "status": "running"},
            {"run_id": "run-a", "status": "completed"},
        ],
    )

    assert terminal_only == {}
    assert wrong_run == {}
    assert complete["default_deny_outbound"] is True
    assert complete["run_id"] == "run-a"


def test_runtime_probe_results_file_rejects_wrong_run_and_sensitive_content(tmp_path):
    generator = load_generator()
    runtime_probe_results_file = tmp_path / "runtime-probe-results.json"

    runtime_probe_results_file.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-probe-results.v1",
                "run_id": "other-run",
                "source": "platform_runtime_probe",
                "resource_limits": {},
            }
        ),
        encoding="utf-8",
    )

    try:
        generator.load_runtime_probe_results(runtime_probe_results_file, run_id="run-a")
    except RuntimeError as exc:
        assert "run_id" in str(exc)
    else:
        raise AssertionError("mismatched runtime probe results should fail closed")

    runtime_probe_results_file.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-probe-results.v1",
                "run_id": "run-a",
                "source": "platform_runtime_probe",
                "resource_limits": {
                    "debug_path": "/tmp/raw-docker-output.json",
                },
            }
        ),
        encoding="utf-8",
    )

    try:
        generator.load_runtime_probe_results(runtime_probe_results_file, run_id="run-a")
    except RuntimeError as exc:
        assert "sensitive" in str(exc)
    else:
        raise AssertionError("sensitive runtime probe results should fail closed")

    runtime_probe_results_file.write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.sandbox-runtime-probe-results.v1",
                "run_id": "run-a",
                "source": "platform_runtime_probe",
                "resource_limits": {},
                "egress_policy": {},
            }
        ),
        encoding="utf-8",
    )

    try:
        generator.load_runtime_probe_results(runtime_probe_results_file, run_id="run-a")
    except RuntimeError as exc:
        assert "security_options" in str(exc)
    else:
        raise AssertionError("incomplete runtime probe results should fail closed")


def test_runtime_probe_results_file_rejects_under_specified_hardening_sections(tmp_path):
    generator = load_generator()
    runtime_probe_results_file = tmp_path / "runtime-probe-results.json"
    base_payload = {
        "schema_version": "ai-platform.sandbox-runtime-probe-results.v1",
        "run_id": "run-a",
        "source": "platform_runtime_probe",
        "resource_limits": observed_resource_probe(),
        "egress_policy": {
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "172.17.0.1",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
            "probe_source": "runtime_probe_results",
        },
        "security_options": {
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }

    for section_name, expected_message in (
        ("resource_limits", "resource_limits.over_limit_cleanup_verified"),
        ("egress_policy", "egress_policy.default_deny_outbound"),
        ("security_options", "security_options.privileged"),
    ):
        payload = dict(base_payload)
        payload[section_name] = {}
        runtime_probe_results_file.write_text(json.dumps(payload), encoding="utf-8")

        try:
            generator.load_runtime_probe_results(runtime_probe_results_file, run_id="run-a")
        except RuntimeError as exc:
            assert expected_message in str(exc)
        else:
            raise AssertionError(f"under-specified {section_name} should fail closed")

    for missing_field in (
        "denied_target",
        "denied_probe_error_code",
        "allowed_callback_host",
        "callback_probe_status",
        "probe_source",
    ):
        payload = dict(base_payload)
        payload["egress_policy"] = dict(base_payload["egress_policy"])
        payload["egress_policy"].pop(missing_field)
        runtime_probe_results_file.write_text(json.dumps(payload), encoding="utf-8")

        try:
            generator.load_runtime_probe_results(runtime_probe_results_file, run_id="run-a")
        except RuntimeError as exc:
            assert f"egress_policy.{missing_field}" in str(exc)
        else:
            raise AssertionError(f"egress policy missing {missing_field} should fail closed")

    invalid_values = (
        ("denied_probe_error_code", "timeout"),
        ("callback_probe_status", "accepted"),
    )
    for field, value in invalid_values:
        payload = dict(base_payload)
        payload["egress_policy"] = dict(base_payload["egress_policy"])
        payload["egress_policy"][field] = value
        runtime_probe_results_file.write_text(json.dumps(payload), encoding="utf-8")

        try:
            generator.load_runtime_probe_results(runtime_probe_results_file, run_id="run-a")
        except RuntimeError as exc:
            assert f"egress_policy.{field}" in str(exc)
        else:
            raise AssertionError(f"egress policy with invalid {field} should fail closed")


def test_platform_hardening_rejects_wrong_egress_probe_error_code(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    hardening = {
        "lease_isolation": {
            "evidence_class": "live_platform_probe",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "recorded_lease_id": "lease-a",
            "released_lease_id": "lease-a",
            "release_reason": "dispatch_completed",
            "host_paths_redacted": True,
        },
        "workspace_isolation": {
            "evidence_class": "live_platform_probe",
            "workspace_container_path": "/workspace",
            "inputs_container_path": "/workspace/inputs",
            "host_paths_redacted": True,
            "marker_path_is_container_path": True,
        },
        "cleanup": {
            "evidence_class": "live_platform_probe",
            "ephemeral_container_removed": True,
            "cancel_probe_container_removed": True,
            "active_lease_released": True,
        },
        "resource_timeout": observed_resource_timeout_hardening(),
        "failure_fallback": {
            "evidence_class": "source_regression_guard",
            "dispatch_failure_stops_container": True,
            "lease_record_failure_stops_container": True,
            "db_lease_not_released_when_stop_fails": True,
            "source_regression_tests": [
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
            ],
        },
        "cached_lease_revalidation": {
            "evidence_class": "source_regression_guard",
            "cached_lease_revalidates_scope_labels": True,
            "scope_mismatch_fails_closed": True,
            "tenant_workspace_user_session_checked": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels"
            ],
        },
        "resource_limits": observed_resource_limits_hardening(),
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "timeout",
            "allowed_callback_host": "172.17.0.1",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
            "probe_source": "runtime_probe_results",
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": hardening}), encoding="utf-8")

    failed = verifier._egress_policy_hardening_error(hardening["egress_policy"])

    assert failed == "hardening evidence missing: egress_policy.denied_probe_error_code"


def test_platform_hardening_rejects_network_inspect_as_denied_egress_source(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    hardening = {
        "lease_isolation": {
            "evidence_class": "live_platform_probe",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "recorded_lease_id": "lease-a",
            "released_lease_id": "lease-a",
            "release_reason": "dispatch_completed",
            "host_paths_redacted": True,
        },
        "workspace_isolation": {
            "evidence_class": "live_platform_probe",
            "workspace_container_path": "/workspace",
            "inputs_container_path": "/workspace/inputs",
            "host_paths_redacted": True,
            "marker_path_is_container_path": True,
        },
        "cleanup": {
            "evidence_class": "live_platform_probe",
            "ephemeral_container_removed": True,
            "cancel_probe_container_removed": True,
            "active_lease_released": True,
        },
        "resource_timeout": observed_resource_timeout_hardening(),
        "failure_fallback": {
            "evidence_class": "source_regression_guard",
            "dispatch_failure_stops_container": True,
            "lease_record_failure_stops_container": True,
            "db_lease_not_released_when_stop_fails": True,
            "source_regression_tests": [
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
            ],
        },
        "cached_lease_revalidation": {
            "evidence_class": "source_regression_guard",
            "cached_lease_revalidates_scope_labels": True,
            "scope_mismatch_fails_closed": True,
            "tenant_workspace_user_session_checked": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels"
            ],
        },
        "resource_limits": observed_resource_limits_hardening(),
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "denied_target": "https://egress-denied.invalid/",
            "denied_probe_error_code": "egress_denied",
            "allowed_callback_host": "host.docker.internal",
            "callback_probe_status": "delivered",
            "policy_source": "platform_policy",
            "probe_source": "docker_network_inspect",
            "network_inspection_verified": True,
            "docker_network_masquerade_disabled": True,
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            "privileged": False,
            "no_new_privileges": True,
            "capabilities_dropped": True,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": True,
        },
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": hardening}), encoding="utf-8")

    failed = verifier._egress_policy_hardening_error(hardening["egress_policy"])

    assert failed == "hardening evidence missing: egress_policy.probe_source"


def test_run_platform_runtime_probe_captures_executor_container_inspect(monkeypatch, tmp_path):
    generator = load_generator()
    calls = []
    container_released = {"value": False}

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease

            assert request.resource_limits["max_seconds"] == generator.PLATFORM_DEADLINE_PROBE_SECONDS
            assert request.resource_limits["platform_timeout_probe"] is True
            lease = ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="docker",
                executor_url="http://127.0.0.1:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                labels={"ai-platform.run_id": request.run_id},
                timings={
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                },
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            await self.release_lease(lease, "dispatch_completed", lease_id)
            container_released["value"] = True
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "accepted",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {"status": "accepted", "run_id": request.run_id},
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_model_latency_ms": 0,
                        "document_processing_latency_ms": 0,
                        "sandbox_cleanup_latency_ms": 5,
                        "sandbox_total_latency_ms": 15,
                    },
                },
            )()

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(tuple(cmd))
        assert container_released["value"] is False
        assert tuple(cmd) == ("docker", "inspect", "executor-exec-run-a")
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "HostConfig": {
                                "Memory": 536870912,
                                "NanoCpus": 500000000,
                                "PidsLimit": 128,
                                "Privileged": False,
                                "SecurityOpt": ["no-new-privileges:true"],
                                "CapDrop": ["ALL"],
                                "ReadonlyRootfs": True,
                                "Binds": ["/tmp/workspace:/workspace:rw"],
                            },
                            "Mounts": [
                                {
                                    "Source": "/tmp/workspace",
                                    "Destination": "/workspace",
                                    "RW": True,
                                }
                            ],
                        }
                    ]
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )

    result = generator.run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider="docker",
        sandbox_executor_image="ai-platform:local",
        workspace_root=str(tmp_path),
        callback_url="http://callback.test/callback",
        docker_cmd=("docker",),
        run=fake_run,
        platform_resource_timeout_probe=True,
    )

    assert result["status"] == "accepted"
    assert calls == [("docker", "inspect", "executor-exec-run-a")]
    assert recorder.hardening["resource_limits"]["docker_inspection_verified"] is True
    assert recorder.hardening["security_options"]["no_new_privileges"] is True
    assert recorder.hardening["security_options"]["capabilities_dropped"] is True
    assert recorder.hardening["security_options"]["root_filesystem_read_only_or_minimal"] is True
    assert recorder.hardening["resource_limits"]["over_limit_cleanup_verified"] is False
    assert recorder.hardening["resource_limits"]["bounded_error_projection_verified"] is False
    assert recorder.hardening["egress_policy"]["default_deny_outbound"] is False
    assert recorder.hardening["egress_policy"]["platform_allowlist_enforced"] is False


def test_run_platform_runtime_probe_captures_egress_network_inspect(monkeypatch, tmp_path):
    generator = load_generator()
    calls = []

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease

            lease = ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="docker",
                executor_url="http://127.0.0.1:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                labels={"ai-platform.run_id": request.run_id},
                timings={},
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            await self.release_lease(lease, "dispatch_completed", lease_id)
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "accepted",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {"status": "accepted", "run_id": request.run_id},
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_model_latency_ms": 0,
                        "document_processing_latency_ms": 0,
                        "sandbox_cleanup_latency_ms": 5,
                        "sandbox_total_latency_ms": 15,
                    },
                },
            )()

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(tuple(cmd))
        if tuple(cmd) == ("docker", "inspect", "executor-exec-run-a"):
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        [
                            {
                                "HostConfig": {
                                    "NetworkMode": "ai-platform-sandbox-egress",
                                    "ExtraHosts": ["host.docker.internal:host-gateway"],
                                },
                                "NetworkSettings": {"Networks": {"ai-platform-sandbox-egress": {}}},
                                "Config": {
                                    "Labels": {
                                        "ai-platform.egress.policy": "default-deny-no-masq",
                                        "ai-platform.egress.network": "ai-platform-sandbox-egress",
                                        "ai-platform.egress.callback_host": "host.docker.internal",
                                    }
                                },
                            }
                        ]
                    ),
                    "stderr": "",
                },
            )()
        if tuple(cmd) == ("docker", "network", "inspect", "ai-platform-sandbox-egress"):
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        [
                            {
                                "Name": "ai-platform-sandbox-egress",
                                "Driver": "bridge",
                                "Options": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
                            }
                        ]
                    ),
                    "stderr": "",
                },
            )()
        raise AssertionError(f"unexpected docker command: {cmd}")

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )
    recorder.record_callback({"run_id": "run-a", "status": "running"}, "secret-token")
    recorder.record_callback({"run_id": "run-a", "status": "completed"}, "secret-token")

    generator.run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider="docker",
        sandbox_executor_image="ai-platform:local",
        workspace_root=str(tmp_path),
        callback_url="http://callback.test/callback",
        docker_cmd=("docker",),
        run=fake_run,
    )

    assert calls == [
        ("docker", "inspect", "executor-exec-run-a"),
        ("docker", "network", "inspect", "ai-platform-sandbox-egress"),
    ]
    assert recorder.hardening["egress_policy"]["probe_source"] == "docker_network_inspect"
    assert recorder.hardening["egress_policy"]["network_inspection_verified"] is True
    assert recorder.hardening["egress_policy"]["docker_network_masquerade_disabled"] is True


def test_run_platform_runtime_probe_does_not_derive_resource_over_limit_from_generic_failure(monkeypatch, tmp_path):
    generator = load_generator()

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease

            lease = ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="docker",
                executor_url="http://127.0.0.1:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                labels={"ai-platform.run_id": request.run_id},
                timings={
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                },
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            await self.release_lease(lease, "run_failed", lease_id)
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "failed",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {
                        "status": "failed",
                        "run_id": request.run_id,
                        "error_code": "executor_health_timeout",
                        "error_message": "Executor health timeout",
                    },
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_model_latency_ms": 0,
                        "document_processing_latency_ms": 0,
                        "sandbox_cleanup_latency_ms": 5,
                        "sandbox_total_latency_ms": 15,
                    },
                },
            )()

    def fake_run(cmd, capture_output, text, timeout, check):
        assert tuple(cmd) == ("docker", "inspect", "executor-exec-run-a")
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "HostConfig": {
                                "Memory": 536870912,
                                "NanoCpus": 500000000,
                                "PidsLimit": 128,
                                "Privileged": False,
                                "SecurityOpt": ["no-new-privileges:true"],
                                "CapDrop": ["ALL"],
                                "ReadonlyRootfs": True,
                                "Binds": ["/tmp/workspace:/workspace:rw"],
                            },
                            "Mounts": [{"Destination": "/workspace", "RW": True}],
                        }
                    ]
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )
    result = generator.run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider="docker",
        sandbox_executor_image="ai-platform:local",
        workspace_root=str(tmp_path),
        callback_url="http://callback.test/callback",
        docker_cmd=("docker",),
        run=fake_run,
    )

    assert result == {
        "status": "failed",
        "run_id": "run-a",
        "error_code": "executor_health_timeout",
    }
    assert recorder.hardening["lease_isolation"]["release_reason"] == "run_failed"
    assert recorder.hardening["resource_limits"]["over_limit_cleanup_verified"] is False
    assert recorder.hardening["resource_limits"]["bounded_error_projection_verified"] is False
    assert "bounded_error_projection" not in recorder.hardening["resource_limits"]


def test_run_platform_runtime_probe_derives_resource_over_limit_from_explicit_platform_timeout_probe(
    monkeypatch, tmp_path
):
    generator = load_generator()

    class FakeRuntime:
        def __init__(
            self,
            *,
            workspace_root,
            callback_token_resolver,
            record_lease,
            release_lease,
        ):
            self.record_lease = record_lease
            self.release_lease = release_lease

        async def submit(self, request):
            from app.runtime.sandbox.contracts import ContainerLease, WorkspaceLease

            assert request.resource_limits["max_seconds"] == generator.PLATFORM_DEADLINE_PROBE_SECONDS
            assert request.resource_limits["platform_timeout_probe"] is True
            lease = ContainerLease(
                container_id="exec-run-a",
                container_name="executor-exec-run-a",
                provider="docker",
                executor_url="http://127.0.0.1:18000",
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                sandbox_mode=request.sandbox_mode,
                browser_enabled=request.browser_enabled,
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                labels={"ai-platform.run_id": request.run_id},
                timings={
                    "sandbox_container_cold_start_latency_ms": 2,
                    "sandbox_healthcheck_latency_ms": 3,
                },
            )
            workspace = WorkspaceLease(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                host_root=str(tmp_path),
                workspace_host_path=str(tmp_path),
                workspace_container_path="/workspace",
                inputs_host_path=str(tmp_path / "inputs"),
                logs_host_path=str(tmp_path / "logs"),
            )
            lease_id = await self.record_lease(lease, request, workspace)
            await self.release_lease(lease, "run_failed", lease_id)
            return type(
                "SandboxRuntimeResult",
                (),
                {
                    "status": "failed",
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "executor_response": {
                        "status": "failed",
                        "run_id": request.run_id,
                        "error_code": "executor_deadline_exceeded",
                        "error_message": "Executor deadline exceeded",
                        "requested_max_seconds": generator.PLATFORM_DEADLINE_PROBE_SECONDS,
                        "timeout_elapsed_ms": 2001,
                    },
                    "timings": {
                        "schema_version": "ai-platform.sandbox-latency-split.v1",
                        "sandbox_lease_acquire_latency_ms": 1,
                        "sandbox_container_cold_start_latency_ms": 2,
                        "sandbox_healthcheck_latency_ms": 3,
                        "sandbox_executor_dispatch_latency_ms": 4,
                        "executor_model_latency_ms": 0,
                        "document_processing_latency_ms": 0,
                        "sandbox_cleanup_latency_ms": 5,
                        "sandbox_total_latency_ms": 15,
                    },
                },
            )()

    def fake_run(cmd, capture_output, text, timeout, check):
        assert tuple(cmd) == ("docker", "inspect", "executor-exec-run-a")
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "Image": "sha256:" + "a" * 64,
                            "HostConfig": {
                                "Memory": 536870912,
                                "NanoCpus": 500000000,
                                "PidsLimit": 128,
                                "Privileged": False,
                                "SecurityOpt": ["no-new-privileges:true"],
                                "CapDrop": ["ALL"],
                                "ReadonlyRootfs": True,
                                "Binds": ["/tmp/workspace:/workspace:rw"],
                            },
                            "Mounts": [{"Destination": "/workspace", "RW": True}],
                            "Config": {
                                "Image": "ai-platform:local",
                                "Labels": {
                                    "ai-platform.source_revision": "local",
                                    "org.opencontainers.image.revision": "local",
                                    "ai-platform.source_tree_commit": "local",
                                    "ai-platform.build-dirty": "false",
                                },
                            },
                        }
                    ]
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("app.runtime.sandbox.runtime.SandboxRuntime", FakeRuntime)
    recorder = generator.EvidenceRecorder(
        run_id="run-a",
        executor_url="http://executor.test",
        callback_token="secret-token",
    )
    recorder.record_callback({"run_id": "run-a", "status": "running"}, recorder._callback_token)
    recorder.record_callback(observed_projection_callback(), recorder._callback_token)

    result = generator.run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider="docker",
        sandbox_executor_image="ai-platform:local",
        workspace_root=str(tmp_path),
        callback_url="http://callback.test/callback",
        docker_cmd=("docker",),
        run=fake_run,
        platform_resource_timeout_probe=True,
    )

    assert result == {
        "status": "failed",
        "run_id": "run-a",
        "error_code": "executor_deadline_exceeded",
    }
    assert recorder.hardening["lease_isolation"]["release_reason"] == "run_failed"
    assert recorder.hardening["resource_limits"]["over_limit_cleanup_verified"] is True
    assert recorder.hardening["resource_limits"]["bounded_error_projection_verified"] is False
    assert recorder.hardening["resource_limits"]["process_timeout_seconds"] == 60
    assert recorder.hardening["resource_limits"]["over_limit_probe_kind"] == "platform_executor_deadline"
    assert recorder.hardening["resource_limits"]["over_limit_requested_max_seconds"] == 2.0
    assert recorder.hardening["resource_limits"]["over_limit_observed_timeout_elapsed_ms"] == 2001
    assert recorder.hardening["resource_limits"]["timeout_probe_runtime_subject"] == "local"
    assert recorder.hardening["resource_limits"]["timeout_probe_runtime_identity"] == observed_runtime_identity()
    assert "bounded_error_projection_source" not in recorder.hardening["resource_limits"]
    assert "bounded_error_projection" not in recorder.hardening["resource_limits"]


def test_callback_public_url_template_uses_actual_bound_port():
    generator = load_generator()

    assert (
        generator.resolve_callback_public_url(
            "http://172.17.0.1:{port}/callback",
            "http://0.0.0.0:43123/callback",
        )
        == "http://172.17.0.1:43123/callback"
    )


def test_generator_redacts_socket_and_host_paths_from_messages():
    generator = load_generator()

    redacted = generator.redact_for_output(
        "permission denied /var/run/docker.sock token=abc /tmp/evidence.json /home/x/file"
    )

    assert "/var/run/docker.sock" not in redacted
    assert "abc" not in redacted
    assert "/tmp/evidence.json" not in redacted
    assert "/home/x/file" not in redacted


def test_generator_main_skip_live_submit_writes_structured_output(tmp_path, capsys):
    generator = load_generator()
    evidence = tmp_path / "evidence.json"

    exit_code = generator.main(
        [
            "--run-id",
            "run-a",
            "--executor-url",
            "http://executor.test",
            "--evidence-file",
            str(evidence),
            "--callback-token",
            "secret-token",
            "--skip-live-submit",
            "--json",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["run_id"] == "run-a"
    assert output["evidence_file"] == "[redacted-path]"
    assert output["executed_task"] is False
    assert "secret-token" not in evidence.read_text(encoding="utf-8")
