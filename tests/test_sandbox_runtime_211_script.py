import importlib.util
import json
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


def test_platform_runtime_evidence_requires_latency_split_and_docker_provider(tmp_path):
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
        "sandbox_lease_acquire_latency_ms": 1,
        "sandbox_container_cold_start_latency_ms": 2,
        "sandbox_healthcheck_latency_ms": 3,
        "sandbox_executor_dispatch_latency_ms": 4,
        "executor_model_latency_ms": 5,
        "document_processing_latency_ms": 6,
        "sandbox_cleanup_latency_ms": 7,
        "sandbox_total_latency_ms": 28,
    }
    evidence.write_text(json.dumps({**base, "timings": timings}), encoding="utf-8")

    assert verifier.check_platform_runtime_evidence(evidence, run_id="run-a").passed is True

    evidence.write_text(json.dumps({**base, "runtime_mode": "executor", "timings": timings}), encoding="utf-8")
    failed_mode = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_mode.passed is False
    assert "platform" in failed_mode.message

    evidence.write_text(json.dumps({**base, "sandbox_provider": "fake", "timings": timings}), encoding="utf-8")
    failed_provider = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_provider.passed is False
    assert "docker" in failed_provider.message

    incomplete = dict(timings)
    incomplete.pop("sandbox_container_cold_start_latency_ms")
    evidence.write_text(json.dumps({**base, "timings": incomplete}), encoding="utf-8")
    failed_timing = verifier.check_platform_runtime_evidence(evidence, run_id="run-a")
    assert failed_timing.passed is False
    assert "sandbox_container_cold_start_latency_ms" in failed_timing.message

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


def test_platform_runtime_evidence_rejects_hidden_or_invalid_latency_split(tmp_path):
    verifier = load_verifier()
    evidence = tmp_path / "evidence.json"
    timings = {
        "schema_version": "ai-platform.sandbox-latency-split.v1",
        "sandbox_lease_acquire_latency_ms": 1,
        "sandbox_container_cold_start_latency_ms": 5,
        "sandbox_healthcheck_latency_ms": 1,
        "sandbox_executor_dispatch_latency_ms": 2,
        "executor_model_latency_ms": 5,
        "document_processing_latency_ms": 3,
        "sandbox_cleanup_latency_ms": 1,
        "sandbox_total_latency_ms": 18,
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
        "resource_timeout": {
            "evidence_class": "source_regression_guard",
            "max_seconds_enforced": True,
            "timeout_error_code": "executor_health_timeout",
            "failed_container_removed": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_maps_health_false_to_timeout",
                "tests/test_sandbox_container_provider.py::test_docker_provider_removes_container_after_health_timeout"
            ],
        },
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
        "resource_limits": {
            "evidence_class": "live_platform_probe",
            "memory_limit_mb": 512,
            "cpu_limit_count": 0.5,
            "pids_limit": 128,
            "process_timeout_seconds": 60,
            "limit_source": "platform_request",
            "docker_inspection_verified": True,
            "over_limit_cleanup_verified": True,
            "bounded_error_projection_verified": True,
        },
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": True,
            "platform_allowlist_enforced": True,
            "callback_exception_scoped_to_run_token": True,
            "denied_egress_redacted": True,
            "policy_source": "platform_policy",
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

    assert verifier.check_platform_hardening_evidence(evidence, run_id="run-a").passed is True

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
        "evidence_class": "live_platform_probe",
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": wrong_evidence_class}), encoding="utf-8")
    failed_class = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_class.passed is False
    assert "evidence_class" in failed_class.message

    unknown_source_test = dict(hardening)
    unknown_source_test["resource_timeout"] = {
        **hardening["resource_timeout"],
        "source_regression_tests": ["tests/test_unrelated.py::test_not_a_sandbox_guard"],
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": unknown_source_test}), encoding="utf-8")
    failed_unknown_test = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_unknown_test.passed is False
    assert "source_regression_tests" in failed_unknown_test.message

    incomplete_source_tests = dict(hardening)
    incomplete_source_tests["resource_timeout"] = {
        **hardening["resource_timeout"],
        "source_regression_tests": [
            "tests/test_sandbox_container_provider.py::test_docker_provider_removes_container_after_health_timeout"
        ],
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": incomplete_source_tests}), encoding="utf-8")
    failed_incomplete_tests = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_incomplete_tests.passed is False
    assert "source_regression_tests" in failed_incomplete_tests.message

    missing_resource_limits = dict(hardening)
    missing_resource_limits.pop("resource_limits")
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": missing_resource_limits}), encoding="utf-8")
    failed_resource_limits = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_resource_limits.passed is False
    assert "resource_limits" in failed_resource_limits.message

    unsafe_egress = dict(hardening)
    unsafe_egress["egress_policy"] = {
        **hardening["egress_policy"],
        "default_deny_outbound": False,
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": unsafe_egress}), encoding="utf-8")
    failed_egress = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_egress.passed is False
    assert "egress_policy.default_deny_outbound" in failed_egress.message

    privileged_container = dict(hardening)
    privileged_container["security_options"] = {
        **hardening["security_options"],
        "privileged": True,
    }
    evidence.write_text(json.dumps({"run_id": "run-a", "hardening": privileged_container}), encoding="utf-8")
    failed_security = verifier.check_platform_hardening_evidence(evidence, run_id="run-a")
    assert failed_security.passed is False
    assert "security_options.privileged" in failed_security.message


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
        "check_platform_hardening_evidence",
        "check_no_secret_leakage",
    }


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
    }
    assert recorder.hardening["egress_policy"] == {
        "evidence_class": "live_platform_probe",
        "default_deny_outbound": False,
        "platform_allowlist_enforced": False,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": False,
        "policy_source": "not_runtime_verified",
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
        "resource_limits.docker_inspection_verified" in failed.message
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


def test_sandbox_runtime_211_help_names_211_docker_command_and_local_cancel_image():
    generator = load_generator()
    verifier = load_verifier()

    generator_help = generator.build_parser().format_help()
    verifier_help = verifier.build_parser().format_help()

    assert "--docker-cmd" in generator_help
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
