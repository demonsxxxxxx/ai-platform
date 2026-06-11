import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from tools.verify_auth_rbac_smoke import (
    build_auth_rbac_smoke,
    sanitize_base_url,
)


def run_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class AuthRbacHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):  # noqa: N802
        if self.path == "/api/auth/me":
            self._send_json(401, {"detail": "missing_authenticated_principal"})
            return
        if self.path.startswith("/api/ai/admin/runtime/overview"):
            roles = self.headers.get("X-AI-Roles", "")
            if "admin" not in roles:
                self._send_json(403, {"detail": "not_ai_admin"})
                return
            self._send_json(
                200,
                {
                    "tenant_id": self.headers.get("X-AI-Tenant-ID", "default"),
                    "queue": {"tenant_insight": {"capacity": {"queue_lease_scan_limit": 50}}},
                    "sandbox": {"leases": {"active": 0}},
                    "capacity": {"max_active_worker_runs": 3},
                    "observability": {"error_count": 0},
                    "governance": {"tool_policy": "visible"},
                    "database_pool": {"open": True},
                    "backpressure": {"reasons": []},
                },
            )
            return
        self._send_json(404, {"detail": "not_found"})

    def _send_json(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LeakyAdminHandler(AuthRbacHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            self._send_json(200, {"executor_private_payload": {"token": "secret"}})
            return
        super().do_GET()


class GovernancePolicyTextHandler(AuthRbacHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            self._send_json(
                200,
                {
                    "tenant_id": "default",
                    "queue": {"tenant_insight": {"capacity": {"queue_lease_scan_limit": 50}}},
                    "sandbox": {"leases": {"active": 0}},
                    "capacity": {"max_active_worker_runs": 3},
                    "observability": {"error_count": 0},
                    "governance": {
                        "release_evidence": {
                            "forbidden_marker_classes": [
                                "bearer token",
                                "secret-bearing environment value",
                            ]
                        }
                    },
                    "database_pool": {"open": True},
                    "backpressure": {"reasons": []},
                },
            )
            return
        super().do_GET()


class LeakyBearerValueHandler(AuthRbacHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            self._send_json(
                200,
                {
                    "tenant_id": "default",
                    "queue": {"tenant_insight": {"capacity": {"queue_lease_scan_limit": 50}}},
                    "sandbox": {"leases": {"active": 0}},
                    "capacity": {"max_active_worker_runs": 3},
                    "observability": {"error_count": 0},
                    "governance": {"header_sample": "Authorization: Bearer abcdefgh12345678"},
                    "database_pool": {"open": True},
                    "backpressure": {"reasons": []},
                },
            )
            return
        super().do_GET()


def test_sanitize_base_url_strips_credentials_query_and_fragment():
    assert sanitize_base_url("https://user:token@example.com:8443/path?api_key=secret#x") == "https://example.com:8443/path"


def test_auth_rbac_smoke_checks_unauthenticated_ordinary_admin_and_redaction():
    server = run_server(AuthRbacHandler)
    try:
        payload = build_auth_rbac_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            commit_sha="bf20432f9889efa8b367afdf512c641068ba30bc",
            image="ai-platform:bf20432-foundation-alpha-poc",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is True
    assert payload["schema_version"] == "ai-platform.auth-rbac-smoke.v1"
    assert payload["source"]["gateway_secret_supplied"] is True
    assert payload["checks"]["unauthenticated_auth_me"]["status"] == 401
    assert payload["checks"]["ordinary_admin_runtime"]["status"] == 403
    assert payload["checks"]["admin_runtime"]["status"] == 200
    assert payload["checks"]["admin_runtime"]["required_sections_present"] is True
    assert payload["checks"]["admin_runtime"]["forbidden_projection_terms_present"] is False
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "test-secret" not in serialized
    assert "executor_private_payload" not in serialized
    assert "api_key" not in serialized


def test_auth_rbac_smoke_fails_closed_on_admin_projection_private_payload_leak():
    server = run_server(LeakyAdminHandler)
    try:
        payload = build_auth_rbac_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            commit_sha="bf20432f9889efa8b367afdf512c641068ba30bc",
            image="ai-platform:bf20432-foundation-alpha-poc",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime"]["status"] == 200
    assert payload["checks"]["admin_runtime"]["forbidden_projection_terms_present"] is True


def test_auth_rbac_smoke_allows_governance_policy_text_forbidden_class_names():
    server = run_server(GovernancePolicyTextHandler)
    try:
        payload = build_auth_rbac_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            commit_sha="bf20432f9889efa8b367afdf512c641068ba30bc",
            image="ai-platform:bf20432-foundation-alpha-poc",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is True
    assert payload["checks"]["admin_runtime"]["status"] == 200
    assert payload["checks"]["admin_runtime"]["forbidden_projection_terms_present"] is False


def test_auth_rbac_smoke_fails_closed_on_bearer_credential_value():
    server = run_server(LeakyBearerValueHandler)
    try:
        payload = build_auth_rbac_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            commit_sha="bf20432f9889efa8b367afdf512c641068ba30bc",
            image="ai-platform:bf20432-foundation-alpha-poc",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime"]["status"] == 200
    assert payload["checks"]["admin_runtime"]["forbidden_projection_terms_present"] is True
