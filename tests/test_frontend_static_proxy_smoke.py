import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.frontend_static_proxy_smoke import run_static_proxy_smoke


def start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class StaticProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(
                200,
                b'<html><head><script type="module" src="/assets/app.js"></script></head><body>AI Platform</body></html>',
                "text/html; charset=utf-8",
            )
            return
        if self.path == "/assets/app.js":
            self._send(200, b"console.log('ai-platform')", "application/javascript")
            return
        if self.path == "/ai-platform-build-provenance.json":
            self._send(
                200,
                json.dumps(
                    {
                        "schema_version": "ai-platform.frontend-build-provenance.v1",
                        "git": {"commit": "a" * 40, "dirty": False},
                    }
                ).encode("utf-8"),
                "application/json",
            )
            return
        if self.path in {"/auth/login", "/chat", "/settings", "/mcp", "/notifications"}:
            self._send(200, b"<html>AI Platform</html>", "text/html; charset=utf-8")
            return
        if self.path == "/api/ai/health":
            self._send(200, b'{"status":"ok"}', "application/json")
            return
        if self.path == "/api/ai/auth/me":
            self._send(401, b'{"detail":"missing_authenticated_principal"}', "application/json")
            return
        self._send(404)

    def log_message(self, *_args):
        return


def test_static_proxy_smoke_accepts_ai_platform_preview_surface():
    server = start_server(StaticProxyHandler)
    try:
        result = run_static_proxy_smoke(
            f"http://127.0.0.1:{server.server_address[1]}",
            routes=["/auth/login", "/chat", "/settings", "/mcp", "/notifications"],
            expected_commit="a" * 40,
        )
    finally:
        server.shutdown()

    assert result["status"] == "pass"
    assert result["checks"]["index"]["ok"] is True
    assert result["checks"]["static_assets"]["ok"] is True
    assert result["checks"]["build_provenance"]["ok"] is True
    assert result["checks"]["api_health"]["ok"] is True
    assert result["checks"]["unauthenticated_auth_me"]["status_code"] == 401
    assert result["failed_checks"] == []


def test_static_proxy_smoke_finds_assets_after_long_index_preamble():
    class LongIndexHandler(StaticProxyHandler):
        def do_GET(self):
            if self.path == "/":
                self._send(
                    200,
                    (
                        "<html><head>"
                        + ("<meta name=\"padding\" content=\"x\" />" * 40)
                        + '<script type="module" src="/assets/app.js"></script>'
                        + "</head><body>AI Platform</body></html>"
                    ).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            super().do_GET()

    server = start_server(LongIndexHandler)
    try:
        result = run_static_proxy_smoke(
            f"http://127.0.0.1:{server.server_address[1]}",
            routes=["/auth/login", "/chat"],
            expected_commit="a" * 40,
        )
    finally:
        server.shutdown()

    assert result["status"] == "pass"
    assert result["checks"]["static_assets"]["ok"] is True


def test_static_proxy_smoke_output_omits_internal_full_body_text():
    server = start_server(StaticProxyHandler)
    try:
        result = run_static_proxy_smoke(
            f"http://127.0.0.1:{server.server_address[1]}",
            routes=["/auth/login", "/chat"],
            expected_commit="a" * 40,
        )
    finally:
        server.shutdown()

    assert "body_text" not in result["checks"]["index"]
    assert "body_text" not in result["checks"]["spa_routes"]["routes"]["/auth/login"]


def test_static_proxy_smoke_fails_closed_on_old_or_unhealthy_frontend():
    class BrokenHandler(StaticProxyHandler):
        def do_GET(self):
            if self.path == "/api/ai/health":
                self._send(503, b'{"status":"down"}', "application/json")
                return
            super().do_GET()

    server = start_server(BrokenHandler)
    try:
        result = run_static_proxy_smoke(
            f"http://127.0.0.1:{server.server_address[1]}",
            routes=["/auth/login", "/chat"],
            expected_commit="b" * 40,
        )
    finally:
        server.shutdown()

    assert result["status"] == "fail"
    assert "api_health" in result["failed_checks"]
    assert "build_provenance" in result["failed_checks"]
