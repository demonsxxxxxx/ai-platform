"""TLS 1.2+ server and production adapter composition for the gateway."""

from __future__ import annotations

import json
import os
import pathlib
import signal
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from .adapters import (
    BrokerPolicy,
    HelperRuntimeAdapter,
    LoopbackLifecycleTransport,
    MailboxBroker,
    SQLiteStateStore,
)
from .gateway import GatewayApplication, GatewayConfig, Request


def load_config(environ: Mapping[str, str] | None = None) -> tuple[GatewayConfig, str, str, str, str, int]:
    """Load policy from environment and secret bytes only from named files."""

    env = dict(os.environ if environ is None else environ)
    config = GatewayConfig(
        lifecycle_api_key=_secret(env, "OPENSANDBOX_GATEWAY_API_KEY_FILE"),
        capability_bearer_token=_secret(env, "OPENSANDBOX_GATEWAY_CAPABILITY_TOKEN_FILE"),
        record_signing_key=_secret(env, "OPENSANDBOX_GATEWAY_SIGNING_KEY_FILE").encode("utf-8"),
        proof_key_id=_required(env, "OPENSANDBOX_GATEWAY_PROOF_KEY_ID"),
        profile_id=_required(env, "OPENSANDBOX_GATEWAY_PROFILE_ID"),
        public_authority=_required(env, "OPENSANDBOX_GATEWAY_PUBLIC_AUTHORITY"),
        lifecycle_endpoint="http://127.0.0.1:8080",
        executor_image=_required(env, "OPENSANDBOX_GATEWAY_EXECUTOR_IMAGE"),
        runtime_subject=_required(env, "OPENSANDBOX_GATEWAY_RUNTIME_SUBJECT"),
        gateway_policy_subject=_required(env, "OPENSANDBOX_GATEWAY_POLICY_SUBJECT"),
        callback_boundary_subject=_required(env, "OPENSANDBOX_GATEWAY_CALLBACK_SUBJECT"),
        deny_audit_subject=_required(env, "OPENSANDBOX_GATEWAY_DENY_AUDIT_SUBJECT"),
        deny_counter_subject=_required(env, "OPENSANDBOX_GATEWAY_DENY_COUNTER_SUBJECT"),
        callback_upstream_base=_required(env, "OPENSANDBOX_GATEWAY_CALLBACK_BASE"),
        openai_upstream_base=_required(env, "OPENSANDBOX_GATEWAY_OPENAI_BASE"),
        anthropic_upstream_base=_required(env, "OPENSANDBOX_GATEWAY_ANTHROPIC_BASE"),
        executor_entrypoint=tuple(json.loads(env.get("OPENSANDBOX_GATEWAY_EXECUTOR_ENTRYPOINT_JSON", '["/app/docker-entrypoint.sh","uvicorn"]'))),
        request_timeout_seconds=float(env.get("OPENSANDBOX_GATEWAY_TIMEOUT_SECONDS", "5")),
        dispatch_timeout_seconds=float(env.get("OPENSANDBOX_GATEWAY_DISPATCH_TIMEOUT_SECONDS", "3600")),
    )
    config.validate()
    return (
        config,
        _required(env, "OPENSANDBOX_GATEWAY_STATE_PATH"),
        _required(env, "OPENSANDBOX_GATEWAY_EGRESS_POLICY_FILE"),
        _required(env, "OPENSANDBOX_GATEWAY_TLS_CERT_FILE"),
        _required(env, "OPENSANDBOX_GATEWAY_TLS_KEY_FILE"),
        int(env.get("OPENSANDBOX_GATEWAY_LISTEN_PORT", "8443")),
    )


def build_application(config: GatewayConfig, state_path: str) -> tuple[GatewayApplication, SQLiteStateStore]:
    """Compose the production core without starting a listener."""

    store = SQLiteStateStore(state_path)
    lifecycle = LoopbackLifecycleTransport(config.request_timeout_seconds, config.max_response_bytes)
    runtime = HelperRuntimeAdapter(
        os.environ.get("OPENSANDBOX_GATEWAY_HELPER_SOCKET", "/run/opensandbox-gateway/helper.sock"),
        config.request_timeout_seconds,
        config.max_response_bytes,
    )
    return GatewayApplication(config, lifecycle, runtime, store), store


def run() -> None:
    """Run the authenticated HTTPS gateway and host mailbox worker."""

    config, state_path, policy_path, cert_path, key_path, port = load_config()
    application, store = build_application(config, state_path)
    policy_value = json.loads(pathlib.Path(policy_path).read_text(encoding="utf-8"))
    policy = BrokerPolicy(policy_value)
    expected_bases = {
        "callback": config.callback_upstream_base,
        "openai": config.openai_upstream_base,
        "anthropic": config.anthropic_upstream_base,
    }
    if any(policy.targets[name][0] != value.rstrip("/") for name, value in expected_bases.items()):
        raise ValueError("egress policy target does not match signed gateway subjects")
    broker = MailboxBroker(store, policy, config.request_timeout_seconds, config.max_response_bytes)
    stop = threading.Event()
    worker = threading.Thread(target=_broker_loop, args=(broker, stop), name="opensandbox-mailbox-broker", daemon=True)
    worker.start()

    class Handler(_GatewayHandler):
        app = application
        body_limit = config.max_body_bytes

    server = _BoundedThreadingHTTPServer(("0.0.0.0", port), Handler, config.max_concurrent_handlers)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    def shutdown(*_: object) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        stop.set()
        worker.join(timeout=2.0)
        server.server_close()


class _GatewayHandler(BaseHTTPRequestHandler):
    """Minimal HTTP/1.1 transport for the transport-neutral gateway core."""

    app: GatewayApplication
    body_limit: int
    protocol_version = "HTTP/1.1"
    server_version = "opensandbox-gateway/1"
    sys_version = ""
    socket_timeout_seconds = 5.0

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.socket_timeout_seconds)

    def log_message(self, format: str, *args: object) -> None:
        # Do not log targets, headers, bodies, or upstream secrets.
        return None

    def _dispatch(self) -> None:
        if self.headers.get_all("transfer-encoding", []):
            self._write(400, {"content-type": "application/json"}, b'{"error":{"code":"transfer_encoding_rejected"}}')
            return
        if any(len(self.headers.get_all(name, [])) > 1 for name in ("content-length", "open-sandbox-api-key", "authorization", "open-sandbox-route-token")):
            self._write(400, {"content-type": "application/json"}, b'{"error":{"code":"ambiguous_header"}}')
            return
        raw_length = self.headers.get("content-length")
        if self.command in {"POST", "PUT", "PATCH"} and raw_length is None:
            self._write(411, {"content-type": "application/json"}, b'{"error":{"code":"content_length_required"}}')
            return
        raw_length = "0" if raw_length is None else raw_length
        if not raw_length.isdigit() or str(int(raw_length)) != raw_length or int(raw_length) > self.body_limit:
            self._write(413, {"content-type": "application/json"}, b'{"error":{"code":"request_too_large"}}')
            return
        try:
            body = self.rfile.read(int(raw_length))
        except (TimeoutError, socket.timeout):
            self._write(408, {"content-type": "application/json"}, b'{"error":{"code":"request_timeout"}}')
            return
        if len(body) != int(raw_length):
            self._write(400, {"content-type": "application/json"}, b'{"error":{"code":"request_body_incomplete"}}')
            return
        request = Request(self.command, self.path, {key: value for key, value in self.headers.items()}, body)
        response = self.app.handle(request)
        self._write(response.status, response.headers, response.body)

    def _write(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() not in {"connection", "transfer-encoding", "server", "date"}:
                self.send_header(key, value)
        if not any(key.lower() == "content-length" for key in headers):
            self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _dispatch


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threading listener with a hard upper bound on active handlers."""

    def __init__(self, server_address, handler_class, max_handlers: int) -> None:
        self._handler_slots = threading.BoundedSemaphore(max_handlers)
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address) -> None:
        if not self._handler_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: 46\r\nConnection: close\r\n\r\n{\"error\":{\"code\":\"concurrency_limit_reached\"}}"
                )
            finally:
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()


def _broker_loop(broker: MailboxBroker, stop: threading.Event) -> None:
    while not stop.wait(0.02 if broker.poll_once() else 0.1):
        pass


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value or "\x00" in value or len(value) > 4096:
        raise ValueError(f"missing or invalid setting: {name}")
    return value


def _secret(env: Mapping[str, str], name: str) -> str:
    path = pathlib.Path(_required(env, name))
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError(f"invalid secret file: {name}")
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\x00" in value:
        raise ValueError(f"empty secret file: {name}")
    return value
