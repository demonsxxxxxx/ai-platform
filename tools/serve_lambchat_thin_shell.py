#!/usr/bin/env python3
"""Serve a built LambChat SPA with /api/* proxied to ai-platform.

This is a deployment helper for the transition where LambChat is the frontend
shell and ai-platform is the backend source of truth.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOP_BY_HOP_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _copy_request_headers(handler: SimpleHTTPRequestHandler) -> dict[str, str]:
    return {
        key: value
        for key, value in handler.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
    }


def _is_streaming_response(headers) -> bool:
    content_type = headers.get("Content-Type", "").lower()
    return "text/event-stream" in content_type or "application/x-ndjson" in content_type


def build_handler(root: Path, api_base: str, upstream_timeout: int):
    root = root.resolve()
    api_base = api_base.rstrip("/")

    class LambChatThinShellHandler(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        extensions_map = {
            **SimpleHTTPRequestHandler.extensions_map,
            ".js": "application/javascript",
            ".mjs": "application/javascript",
            ".css": "text/css",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".webmanifest": "application/manifest+json",
        }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def handle(self) -> None:
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                return

        def _proxy_api(self) -> None:
            target = api_base + self.path
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            request = Request(
                target,
                data=body,
                headers=_copy_request_headers(self),
                method=self.command,
            )
            try:
                with urlopen(request, timeout=upstream_timeout) as response:
                    if _is_streaming_response(response.headers):
                        self._send_streaming_response(response)
                        return
                    self._send_buffered_response(response.status, response.headers, response.read())
            except HTTPError as exc:
                self._send_buffered_response(exc.code, exc.headers, exc.read())
            except URLError as exc:
                content = str(exc.reason).encode("utf-8", "replace")
                self._send_buffered_response(502, {"Content-Type": "text/plain; charset=utf-8"}, content)

        def _handle_websocket(self) -> None:
            if self.headers.get("Upgrade", "").lower() != "websocket":
                self.send_error(404)
                return
            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                self.send_error(400, "Missing Sec-WebSocket-Key")
                return
            accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
            self.send_response_only(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.close_connection = True
            self.connection.settimeout(upstream_timeout)
            try:
                self._drain_websocket_frames()
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                return

        def _drain_websocket_frames(self) -> None:
            while True:
                header = self.rfile.read(2)
                if len(header) < 2:
                    return
                opcode = header[0] & 0x0F
                masked = bool(header[1] & 0x80)
                length = header[1] & 0x7F
                if length == 126:
                    length = int.from_bytes(self.rfile.read(2), "big")
                elif length == 127:
                    length = int.from_bytes(self.rfile.read(8), "big")
                mask = self.rfile.read(4) if masked else b""
                payload = self.rfile.read(length) if length else b""
                if masked and payload:
                    payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
                if opcode == 0x8:
                    self._send_websocket_frame(0x8, payload)
                    return
                if opcode == 0x9:
                    self._send_websocket_frame(0xA, payload)

        def _send_websocket_frame(self, opcode: int, payload: bytes = b"") -> None:
            first = 0x80 | opcode
            length = len(payload)
            if length < 126:
                header = bytes([first, length])
            elif length < 65536:
                header = bytes([first, 126]) + length.to_bytes(2, "big")
            else:
                header = bytes([first, 127]) + length.to_bytes(8, "big")
            self.wfile.write(header + payload)
            self.wfile.flush()

        def _send_streaming_response(self, response) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = response.readline()
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()

        def _send_buffered_response(self, status: int, headers, content: bytes) -> None:
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            if self.path == "/ws":
                self._handle_websocket()
                return
            if self.path.startswith("/api/"):
                self._proxy_api()
                return
            super().do_GET()

        def do_HEAD(self) -> None:
            if self.path.startswith("/api/"):
                self._proxy_api()
                return
            super().do_HEAD()

        def do_POST(self) -> None:
            if self.path.startswith("/api/"):
                self._proxy_api()
                return
            self.send_error(405)

        def do_PUT(self) -> None:
            if self.path.startswith("/api/"):
                self._proxy_api()
                return
            self.send_error(405)

        def do_PATCH(self) -> None:
            if self.path.startswith("/api/"):
                self._proxy_api()
                return
            self.send_error(405)

        def do_DELETE(self) -> None:
            if self.path.startswith("/api/"):
                self._proxy_api()
                return
            self.send_error(405)

        def send_head(self):
            translated = Path(self.translate_path(self.path))
            if translated.is_dir() or translated.exists():
                return super().send_head()
            self.path = "/index.html"
            return super().send_head()

        def end_headers(self) -> None:
            if not self.path.startswith("/api/"):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

    return LambChatThinShellHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("LAMBCHAT_THIN_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LAMBCHAT_THIN_PORT", "18002")))
    parser.add_argument("--root", required=True, help="Built LambChat frontend directory.")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("AI_PLATFORM_API_PROXY_BASE", "http://127.0.0.1:8020"),
        help="ai-platform API base URL. Default: http://127.0.0.1:8020",
    )
    parser.add_argument(
        "--upstream-timeout",
        type=int,
        default=int(os.environ.get("AI_PLATFORM_API_PROXY_TIMEOUT_SECONDS", "300")),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"SPA root does not exist or is not a directory: {root}")
    handler = build_handler(root, args.api_base, args.upstream_timeout)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"Serving LambChat thin shell from {root} on http://{args.host}:{args.port}; "
        f"proxying /api/* to {args.api_base.rstrip('/')}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
