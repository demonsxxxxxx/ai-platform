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
    recorder.write(evidence_path)

    raw = evidence_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["run_id"] == "run-a"
    assert data["executor_url"] == "http://127.0.0.1:18000"
    assert data["executed_task"] is True
    assert data["callback_auth"] == "token"
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
    assert body["callback_url"] == "http://callback.test/callback"
    assert body["callback_token"] == "secret-token"
    assert body["config"]["resource_limits"]["max_seconds"] == 60


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
        cancel_image="busybox:1.36",
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
            cancel_image="busybox:1.36",
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
            "busybox:1.36",
            "sh",
            "-c",
            "sleep 300",
        )
    ]


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
