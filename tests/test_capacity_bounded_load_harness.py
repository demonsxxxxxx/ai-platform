import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.capacity_baseline import build_capacity_gate_readiness
from app.capacity_bounded_load_harness import (
    CAPACITY_BOUNDED_LOAD_HARNESS_SCHEMA,
    OPERATOR_ACKNOWLEDGEMENT,
    build_capacity_bounded_load_harness_plan,
    render_capacity_bounded_load_harness_markdown,
    run_capacity_bounded_load_harness,
)


def test_capacity_bounded_load_harness_dry_run_is_safe_and_not_gate_evidence():
    plan = build_capacity_bounded_load_harness_plan(
        base_url="https://user:token@ai-platform.internal/api?api_key=secret",
        gate="api_read_write_burst",
        request_count=12,
        concurrency=3,
    )

    assert plan["schema_version"] == CAPACITY_BOUNDED_LOAD_HARNESS_SCHEMA
    assert plan["status"] == "dry_run"
    assert plan["gate"] == "api_read_write_burst"
    assert plan["supported_gates"] == [
        "api_read_write_burst",
        "queue_depth_and_lease_latency",
    ]
    assert plan["base_url"] == "https://ai-platform.internal/api"
    assert plan["request_count"] == 12
    assert plan["concurrency"] == 3
    assert plan["execute"] is False
    assert plan["does_not_raise_defaults"] is True
    assert plan["does_not_mark_gate_recorded"] is True
    assert plan["load_test_evidence_status"] == "probe_only_not_recorded"
    assert plan["gate_evidence_compatibility"] == "not_accepted_by_capacity_gate_readiness"
    assert [endpoint["path"] for endpoint in plan["endpoints"]] == [
        "/api/ai/health",
        "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
    ]

    serialized = json.dumps(plan, ensure_ascii=False).lower()
    assert "user:token" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_capacity_bounded_load_harness_queue_gate_dry_run_is_safe():
    plan = build_capacity_bounded_load_harness_plan(
        base_url="https://ai-platform.internal",
        gate="queue_depth_and_lease_latency",
        request_count=300,
        concurrency=50,
    )

    assert plan["status"] == "dry_run"
    assert plan["gate"] == "queue_depth_and_lease_latency"
    assert plan["request_count"] == 200
    assert plan["concurrency"] == 20
    assert [endpoint["path"] for endpoint in plan["endpoints"]] == [
        "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
    ]
    assert plan["load_test_evidence_status"] == "probe_only_not_recorded"
    assert plan["does_not_raise_defaults"] is True
    assert plan["does_not_mark_gate_recorded"] is True


def test_capacity_bounded_load_harness_refuses_execute_without_acknowledgement():
    result = run_capacity_bounded_load_harness(
        base_url="https://ai-platform.internal",
        gate="api_read_write_burst",
        request_count=2,
        concurrency=1,
        execute=True,
        operator_acknowledgement="wrong",
    )

    assert result["status"] == "blocked_missing_operator_acknowledgement"
    assert result["sent_requests"] == 0
    assert result["required_operator_acknowledgement"] == OPERATOR_ACKNOWLEDGEMENT
    assert result["does_not_raise_defaults"] is True
    assert result["does_not_mark_gate_recorded"] is True


def test_capacity_bounded_load_harness_executes_bounded_read_only_probe_without_private_payloads():
    requests: list[str] = []

    class CapacityProbeHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            requests.append(self.path)
            path = self.path.split("?", 1)[0]
            if path == "/api/ai/health":
                payload = {"status": "ok", "secret": "health-secret"}
            elif path == "/api/ai/admin/runtime/overview":
                assert "include_maintenance_cleanup=false" in self.path
                payload = {
                    "capacity": {"schema_version": "ai-platform.capacity-baseline.v1"},
                    "database_pool": {"open": True, "database_url": "postgres://secret"},
                    "queue": {"status": {"depths": {"queued": 0}}},
                    "admission": {"active_runs": 0},
                    "backpressure": {"reasons": []},
                    "sandbox": {"leases": {"active": 0}, "sandbox_workdir": "/tmp/secret"},
                    "observability": {"error_count": 0, "executor_private_payload": {"token": "secret"}},
                }
            else:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), CapacityProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_capacity_bounded_load_harness(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gate="api_read_write_burst",
            request_count=6,
            concurrency=2,
            execute=True,
            operator_acknowledgement=OPERATOR_ACKNOWLEDGEMENT,
            user_id="capacity-admin",
            tenant_id="default",
            roles="admin",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["status"] == "probe_completed_not_gate_evidence"
    assert result["sent_requests"] == 6
    assert set(requests) == {
        "/api/ai/health",
        "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
    }
    assert result["http_status_counts"] == {"200": 6}
    assert result["latency_ms"]["count"] == 6
    assert result["latency_ms"]["p50"] >= 0
    assert result["latency_ms"]["p95"] >= 0
    assert result["latency_ms"]["p99"] >= 0
    assert result["cleanup_proof_status"] == "not_applicable_read_only_probe"
    assert result["stop_condition_status"] in {"passed", "triggered"}
    assert result["load_test_evidence_status"] == "probe_only_not_recorded"
    assert result["does_not_mark_gate_recorded"] is True

    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "health-secret" not in serialized
    assert "database_url" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "executor_private_payload" not in serialized
    assert "token" not in serialized


def test_capacity_bounded_load_harness_executes_queue_gate_against_admin_projection_only():
    requests: list[str] = []

    class QueueProbeHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            requests.append(self.path)
            if self.path != "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false":
                self.send_response(404)
                self.end_headers()
                return
            payload = {
                "capacity": {"schema_version": "ai-platform.capacity-baseline.v1"},
                "database_pool": {"open": True, "database_url": "postgres://secret"},
                "queue": {
                    "status": {"depths": {"queued": 42, "processing": 3}},
                    "tenant_insight": {"tenant_id": "default", "raw_storage_key": "hidden"},
                },
                "admission": {"active_runs": 3},
                "backpressure": {"reasons": ["queued_behind_existing_work"]},
                "sandbox": {"leases": {"active": 0}, "sandbox_workdir": "/tmp/private"},
                "observability": {"error_count": 0, "executor_private_payload": "hidden"},
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), QueueProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_capacity_bounded_load_harness(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gate="queue_depth_and_lease_latency",
            request_count=5,
            concurrency=2,
            execute=True,
            operator_acknowledgement=OPERATOR_ACKNOWLEDGEMENT,
            user_id="capacity-admin",
            tenant_id="default",
            roles="admin",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["status"] == "probe_completed_not_gate_evidence"
    assert result["gate"] == "queue_depth_and_lease_latency"
    assert result["sent_requests"] == 5
    assert set(requests) == {"/api/ai/admin/runtime/overview?include_maintenance_cleanup=false"}
    assert result["http_status_counts"] == {"200": 5}
    assert result["observed_admin_runtime_sections"] == [
        "admission",
        "backpressure",
        "capacity",
        "database_pool",
        "observability",
        "queue",
        "sandbox",
    ]
    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "database_url" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "executor_private_payload" not in serialized
    assert "secret" not in serialized


def test_capacity_bounded_load_harness_marks_triggered_stop_conditions_as_failed():
    class FailingProbeHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            payload = {"status": "unavailable", "secret": "probe-secret"}
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), FailingProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_capacity_bounded_load_harness(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gate="api_read_write_burst",
            request_count=4,
            concurrency=2,
            execute=True,
            operator_acknowledgement=OPERATOR_ACKNOWLEDGEMENT,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["status"] == "probe_failed_stop_condition_triggered"
    assert result["http_status_counts"] == {"500": 4}
    assert result["stop_condition_status"] == "triggered"
    assert result["triggered_stop_conditions"] == ["http_5xx_rate_exceeds_threshold"]
    assert result["does_not_mark_gate_recorded"] is True
    assert "probe-secret" not in json.dumps(result, ensure_ascii=False)


def test_capacity_bounded_load_harness_marks_non_2xx_status_as_failed():
    class ForbiddenProbeHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            raw = json.dumps({"status": "forbidden"}).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ForbiddenProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_capacity_bounded_load_harness(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gate="api_read_write_burst",
            request_count=2,
            concurrency=1,
            execute=True,
            operator_acknowledgement=OPERATOR_ACKNOWLEDGEMENT,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["status"] == "probe_failed_stop_condition_triggered"
    assert result["http_status_counts"] == {"403": 2}
    assert result["triggered_stop_conditions"] == ["http_non_2xx_response_detected"]


def test_capacity_bounded_load_harness_markdown_preserves_non_evidence_policy():
    payload = build_capacity_bounded_load_harness_plan(
        base_url="https://ai-platform.internal",
        gate="api_read_write_burst",
    )

    markdown = render_capacity_bounded_load_harness_markdown(payload)

    assert "load_test_evidence_status: `probe_only_not_recorded`" in markdown
    assert "gate_evidence_compatibility: `not_accepted_by_capacity_gate_readiness`" in markdown
    assert "does_not_mark_gate_recorded: `true`" in markdown
    assert "Do not raise production concurrency defaults" in markdown


def test_capacity_gate_readiness_rejects_harness_output_as_recorded_evidence():
    payload = build_capacity_bounded_load_harness_plan(
        base_url="https://ai-platform.internal",
        gate="api_read_write_burst",
    )

    readiness = build_capacity_gate_readiness(payload)

    assert readiness["status"] == "blocked_missing_admin_runtime_sections"
    assert readiness["load_test_evidence_status"] == "missing"
    assert readiness["missing_load_test_gates"]
    assert readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"


def test_capacity_gate_readiness_rejects_queue_harness_output_as_recorded_evidence():
    payload = build_capacity_bounded_load_harness_plan(
        base_url="https://ai-platform.internal",
        gate="queue_depth_and_lease_latency",
    )

    readiness = build_capacity_gate_readiness(payload)

    assert readiness["status"] == "blocked_missing_admin_runtime_sections"
    assert readiness["load_test_evidence_status"] == "missing"
    assert "queue_depth_and_lease_latency" in readiness["missing_load_test_gates"]
    assert readiness["production_default_decision"] == "do_not_raise_without_recorded_load_test_evidence"


def test_capacity_bounded_load_harness_cli_exits_nonzero_when_probe_triggers_stop_condition():
    class FailingProbeHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            raw = json.dumps({"status": "unavailable"}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), FailingProbeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "tools/capacity_bounded_load_harness.py",
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--gate",
                "api_read_write_burst",
                "--requests",
                "2",
                "--concurrency",
                "1",
                "--execute",
                "--operator-acknowledgement",
                OPERATOR_ACKNOWLEDGEMENT,
                "--format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "probe_failed_stop_condition_triggered"
    assert payload["stop_condition_status"] == "triggered"
    assert payload["does_not_mark_gate_recorded"] is True


def test_capacity_bounded_load_harness_cli_dry_run_outputs_json_without_secret_markers():
    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_bounded_load_harness.py",
            "--base-url",
            "https://user:token@ai-platform.internal/api?api_key=secret",
            "--gate",
            "api_read_write_burst",
            "--requests",
            "4",
            "--concurrency",
            "2",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == CAPACITY_BOUNDED_LOAD_HARNESS_SCHEMA
    assert payload["status"] == "dry_run"
    assert payload["base_url"] == "https://ai-platform.internal/api"
    assert payload["request_count"] == 4
    assert payload["concurrency"] == 2
    assert "user:token" not in result.stdout
    assert "api_key" not in result.stdout
    assert "secret" not in result.stdout.lower()


def test_capacity_bounded_load_harness_cli_unsupported_dry_run_exits_nonzero():
    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_bounded_load_harness.py",
            "--base-url",
            "https://ai-platform.internal",
            "--gate",
            "unsupported_gate",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked_unsupported_gate"
    assert payload["sent_requests"] == 0


def test_capacity_bounded_load_harness_cli_queue_gate_dry_run_outputs_supported_gate():
    result = subprocess.run(
        [
            sys.executable,
            "tools/capacity_bounded_load_harness.py",
            "--base-url",
            "https://ai-platform.internal",
            "--gate",
            "queue_depth_and_lease_latency",
            "--requests",
            "4",
            "--concurrency",
            "2",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == CAPACITY_BOUNDED_LOAD_HARNESS_SCHEMA
    assert payload["status"] == "dry_run"
    assert payload["gate"] == "queue_depth_and_lease_latency"
    assert payload["supported_gates"] == [
        "api_read_write_burst",
        "queue_depth_and_lease_latency",
    ]
    assert [endpoint["path"] for endpoint in payload["endpoints"]] == [
        "/api/ai/admin/runtime/overview?include_maintenance_cleanup=false",
    ]
