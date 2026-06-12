import importlib
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def load_smoke_module():
    try:
        return importlib.import_module("tools.verify_alert_trace_export_runtime_acceptance")
    except ModuleNotFoundError as exc:
        raise AssertionError("tools.verify_alert_trace_export_runtime_acceptance is missing") from exc


def run_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def observability_payload(tenant_id="default", *, overrides=None):
    payload = {
        "tenant_id": tenant_id,
        "observability_readiness": {
            "schema_version": "ai-platform.observability-readiness.v1",
            "status": "partial_blocked",
            "domains": {
                "alerts_and_exports": {
                    "status": "partial_blocked",
                    "implemented": [
                        "admin_runtime_overview_projection",
                        "alert_slo_rule_template_evidence",
                        "alert_delivery_channel_policy_contract",
                        "trace_audit_export_contract",
                    ],
                    "gaps": [
                        "alert_rules_runtime_dashboard_and_211_acceptance",
                        "alert_delivery_channel_runtime_acceptance",
                        "slo_threshold_runtime_calibration",
                        "trace_audit_export_runtime_acceptance",
                        "trace_audit_export_dashboard_acceptance",
                        "trace_audit_export_211_acceptance",
                    ],
                    "evidence": {
                        "alert_slo_rules": {
                            "schema_version": "ai-platform.alert-slo-readiness.v1",
                            "status": "partial_blocked",
                            "active_alerting_policy": "template_only_not_enabled",
                            "delivery_channel_policy": {
                                "schema_version": "ai-platform.alert-delivery-channel-policy.v1",
                                "status": "contract_only_not_enabled",
                                "does_not_enable_alert_delivery": True,
                                "requires_211_smoke": True,
                            },
                            "summary": {
                                "rule_count": 7,
                                "categories": [
                                    "queue",
                                    "database",
                                    "worker",
                                    "model_gateway",
                                    "sandbox",
                                    "error_taxonomy",
                                    "capacity_gate",
                                ],
                            },
                            "open_gaps": [
                                "alert_rules_runtime_dashboard_and_211_acceptance",
                                "alert_delivery_channel_runtime_acceptance",
                                "slo_threshold_runtime_calibration",
                            ],
                        },
                        "trace_audit_export": {
                            "schema_version": "ai-platform.trace-audit-export-readiness.v1",
                            "status": "partial_blocked",
                            "export_contract": {
                                "schema_version": "ai-platform.trace-audit-export-contract.v1",
                                "allowed_event_sources": [
                                    "run_event_public_projection",
                                    "audit_event_public_projection",
                                    "admin_runtime_observability_summary",
                                    "release_evidence_entry",
                                ],
                                "does_not_export_raw_runtime_payloads": True,
                            },
                            "open_gaps": [
                                "trace_audit_export_runtime_acceptance",
                                "trace_audit_export_dashboard_acceptance",
                                "trace_audit_export_211_acceptance",
                            ],
                        },
                    },
                }
            },
        },
    }
    if overrides:
        payload.update(overrides)
    return payload


class AlertTraceHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview"):
            if self.headers.get("X-AI-Gateway-Secret", "") != "test-secret":
                self._send_json(403, {"detail": "invalid_gateway_principal_secret"})
                return
            roles = self.headers.get("X-AI-Roles", "")
            if "admin" not in roles:
                self._send_json(403, {"detail": "not_ai_admin"})
                return
            self._send_json(200, observability_payload(self.headers.get("X-AI-Tenant-ID", "default")))
            return
        self._send_json(404, {"detail": "not_found"})

    def _send_json(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class MissingTraceContractHandler(AlertTraceHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = observability_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            del payload["observability_readiness"]["domains"]["alerts_and_exports"]["evidence"]["trace_audit_export"]
            self._send_json(200, payload)
            return
        super().do_GET()


class LeakyAlertTraceHandler(AlertTraceHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = observability_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["observability_readiness"]["domains"]["alerts_and_exports"]["evidence"][
                "executor_private_payload"
            ] = {"raw_storage_key": "tenant/default/runs/run-a/private.json"}
            self._send_json(200, payload)
            return
        super().do_GET()


def test_alert_trace_export_runtime_acceptance_checks_admin_only_contracts_and_redaction():
    smoke = load_smoke_module()
    server = run_server(AlertTraceHandler)
    try:
        payload = smoke.build_alert_trace_export_runtime_acceptance(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            commit_sha="b96d02e232176bade455f2af2bc3080f8f372206",
            runtime_subject_commit_sha="b96d02e232176bade455f2af2bc3080f8f372206",
            image="ai-platform:b96d02e-release-evidence-runtime-acceptance",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["schema_version"] == "ai-platform.alert-trace-export-runtime-acceptance.v1"
    assert payload["ok"] is True
    assert payload["status"] == "accepted_for_operator_review"
    assert payload["redaction_scan_status"] == "passed"
    assert payload["source"]["gateway_secret_supplied"] is True
    assert payload["source"]["runtime_subject_commit_sha"] == "b96d02e232176bade455f2af2bc3080f8f372206"
    assert payload["checks"]["ordinary_admin_runtime"]["status"] == 403
    admin = payload["checks"]["admin_runtime_alerts_and_exports"]
    assert admin["status"] == 200
    assert admin["tenant_matches_requested"] is True
    assert admin["observability_schema_version"] == "ai-platform.observability-readiness.v1"
    assert admin["alerts_domain_status"] == "partial_blocked"
    assert admin["alert_rule_count"] == 7
    assert admin["alert_delivery_policy_status"] == "contract_only_not_enabled"
    assert admin["alert_delivery_not_enabled"] is True
    assert admin["slo_threshold_runtime_calibration_gap_present"] is True
    assert admin["trace_export_contract_schema_version"] == "ai-platform.trace-audit-export-contract.v1"
    assert admin["trace_export_not_raw_runtime_payloads"] is True
    assert admin["trace_export_sources_public_only"] is True
    assert admin["forbidden_projection_terms_present"] is False
    assert payload["open_gaps"] == []
    assert payload["does_not_enable_alert_delivery"] is True
    assert payload["does_not_export_raw_runtime_payloads"] is True
    assert payload["does_not_close_g9"] is True

    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "test-secret" not in serialized
    assert "executor_private_payload" not in serialized
    assert "raw_storage_key" not in serialized


def test_alert_trace_export_runtime_acceptance_fails_closed_when_trace_contract_missing():
    smoke = load_smoke_module()
    server = run_server(MissingTraceContractHandler)
    try:
        payload = smoke.build_alert_trace_export_runtime_acceptance(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["status"] == "blocked_runtime_acceptance"
    admin = payload["checks"]["admin_runtime_alerts_and_exports"]
    assert admin["trace_export_contract_schema_version"] == ""
    assert "trace_audit_export_211_acceptance" in payload["open_gaps"]


def test_alert_trace_export_runtime_acceptance_fails_closed_on_private_payload_leak():
    smoke = load_smoke_module()
    server = run_server(LeakyAlertTraceHandler)
    try:
        payload = smoke.build_alert_trace_export_runtime_acceptance(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime_alerts_and_exports"]["forbidden_projection_terms_present"] is True
    assert payload["redaction_scan_status"] == "failed"


def test_alert_trace_export_runtime_acceptance_cli_emits_safe_json_and_exit_status():
    server = run_server(AlertTraceHandler)
    env = {
        **os.environ,
        "TEST_AI_PLATFORM_GATEWAY_SECRET": "test-secret",
    }
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(Path("tools") / "verify_alert_trace_export_runtime_acceptance.py"),
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--gateway-secret-env",
                "TEST_AI_PLATFORM_GATEWAY_SECRET",
                "--commit-sha",
                "b96d02e232176bade455f2af2bc3080f8f372206",
                "--runtime-subject-commit-sha",
                "b96d02e232176bade455f2af2bc3080f8f372206",
                "--image",
                "ai-platform:b96d02e-release-evidence-runtime-acceptance",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == "ai-platform.alert-trace-export-runtime-acceptance.v1"
    assert "test-secret" not in result.stdout
    assert "source_ref" not in result.stdout
    assert "evidence_ref" not in result.stdout
