import socket
import threading
import time
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from tools.serve_lambchat_thin_shell import build_handler


def start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/api/auth/me":
            content = b'{"detail":"missing_authenticated_principal"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if self.path == "/api/chat/sessions/ses_a/stream?run_id=run_a":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"event: message:chunk\ndata: first\n\n")
            self.wfile.flush()
            time.sleep(0.35)
            self.wfile.write(b"event: done\ndata: {}\n\n")
            self.wfile.flush()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        return


def test_spa_fallback_serves_index(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<html>LambChat</html>", encoding="utf-8")
    server = start_server(build_handler(root, "http://127.0.0.1:1", 2))
    try:
        port = server.server_address[1]
        response = urlopen(f"http://127.0.0.1:{port}/chat/ses_a", timeout=2)
        assert response.status == 200
        assert response.read() == b"<html>LambChat</html>"
    finally:
        server.shutdown()


def test_api_proxy_forwards_to_ai_platform(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<html>LambChat</html>", encoding="utf-8")
    upstream = start_server(UpstreamHandler)
    api_base = f"http://127.0.0.1:{upstream.server_address[1]}"
    thin_shell = start_server(build_handler(root, api_base, 2))
    try:
        port = thin_shell.server_address[1]
        response = urlopen(f"http://127.0.0.1:{port}/api/auth/me", timeout=2)
        assert response.status == 401
    except Exception as exc:
        assert getattr(exc, "code", None) == 401
        assert exc.read() == b'{"detail":"missing_authenticated_principal"}'
    finally:
        thin_shell.shutdown()
        upstream.shutdown()


def test_sse_proxy_flushes_first_event_without_waiting_for_completion(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<html>LambChat</html>", encoding="utf-8")
    upstream = start_server(UpstreamHandler)
    api_base = f"http://127.0.0.1:{upstream.server_address[1]}"
    thin_shell = start_server(build_handler(root, api_base, 2))
    started = time.monotonic()
    try:
        port = thin_shell.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(
                b"GET /api/chat/sessions/ses_a/stream?run_id=run_a HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n"
            )
            client.settimeout(0.25)
            received = b""
            while b"data: first" not in received:
                received += client.recv(4096)
        assert b"Content-Type: text/event-stream" in received
        assert b"data: first" in received
        assert time.monotonic() - started < 0.35
    finally:
        thin_shell.shutdown()
        upstream.shutdown()


def test_plain_ws_path_does_not_fallback_to_spa(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<html>LambChat</html>", encoding="utf-8")
    server = start_server(build_handler(root, "http://127.0.0.1:1", 2))
    try:
        port = server.server_address[1]
        try:
            urlopen(f"http://127.0.0.1:{port}/ws", timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("/ws should not serve index.html")
    finally:
        server.shutdown()


def test_ws_path_accepts_browser_websocket_handshake(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<html>LambChat</html>", encoding="utf-8")
    server = start_server(build_handler(root, "http://127.0.0.1:1", 2))
    try:
        port = server.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(
                b"GET /ws HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            response = client.recv(4096)
        assert b"101 Switching Protocols" in response
        assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in response
    finally:
        server.shutdown()
