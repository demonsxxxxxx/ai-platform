"""TLS 1.2+ server and production adapter composition for the gateway."""

from __future__ import annotations

import json
import os
import pathlib
import re
import signal
import socket
import ssl
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from .adapters import (
    BrokerPolicy,
    HelperRuntimeAdapter,
    LoopbackLifecycleTransport,
    MailboxBroker,
    SQLiteStateStore,
)
from .gateway import (
    DeadlineExceeded,
    GatewayApplication,
    GatewayConfig,
    MonotonicDeadline,
    Request,
    deadline_scope,
)


UPSTREAM_CA_BUNDLE_PATH = "/etc/opensandbox-gateway/tls/upstream-ca.pem"


def load_config(environ: Mapping[str, str] | None = None) -> tuple[GatewayConfig, str, str, str, str, str, int]:
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
        _app_scoped_upstream_ca_path(env),
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
        config.dispatch_timeout_seconds,
    )
    return GatewayApplication(config, lifecycle, runtime, store), store


def run() -> None:
    """Run the authenticated HTTPS gateway and host mailbox worker."""

    config, state_path, policy_path, cert_path, key_path, upstream_ca_path, port = load_config()
    _verify_certificate_ip_san(cert_path, config.public_authority)
    application, store = build_application(config, state_path)
    policy_value = json.loads(pathlib.Path(policy_path).read_text(encoding="utf-8"))
    policy = BrokerPolicy(policy_value)
    upstream_tls_context = _load_upstream_tls_context(upstream_ca_path)
    expected_bases = {
        "callback": config.callback_upstream_base,
        "openai": config.openai_upstream_base,
        "anthropic": config.anthropic_upstream_base,
    }
    if any(policy.targets[name][0] != value for name, value in expected_bases.items()):
        raise ValueError("egress policy target does not match signed gateway subjects")
    broker = MailboxBroker(
        store,
        policy,
        config.request_timeout_seconds,
        config.max_response_bytes,
        config.workspace_root,
        config.dispatch_timeout_seconds,
        upstream_tls_context=upstream_tls_context,
    )
    stop = threading.Event()
    worker = threading.Thread(target=_broker_loop, args=(broker, stop), name="opensandbox-mailbox-broker", daemon=True)
    worker.start()

    class Handler(_GatewayHandler):
        app = application
        body_limit = config.max_body_bytes

    server = _BoundedThreadingHTTPServer(
        ("0.0.0.0", port),
        Handler,
        config.max_concurrent_handlers,
        request_deadline_seconds=config.request_timeout_seconds,
        dispatch_deadline_seconds=config.dispatch_timeout_seconds,
    )
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    server.tls_context = context

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
        deadline = self.server.request_deadline(self.connection)
        deadline.bind_socket(self.connection)

    def log_message(self, format: str, *args: object) -> None:
        # Do not log targets, headers, bodies, or upstream secrets.
        return None

    def _dispatch(self) -> None:
        deadline = self.server.request_deadline(self.connection)
        with deadline_scope(deadline):
            self._dispatch_with_deadline(deadline)

    def _dispatch_with_deadline(self, deadline: MonotonicDeadline) -> None:
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
            deadline.bind_socket(self.connection)
            body = self.rfile.read(int(raw_length))
        except (TimeoutError, socket.timeout):
            self._write(408, {"content-type": "application/json"}, b'{"error":{"code":"request_timeout"}}')
            return
        if len(body) != int(raw_length):
            self._write(400, {"content-type": "application/json"}, b'{"error":{"code":"request_body_incomplete"}}')
            return
        request = Request(self.command, self.path, {key: value for key, value in self.headers.items()}, body)
        application_deadline = self.server.application_deadline(self.connection, request)
        with deadline_scope(application_deadline):
            response = self.app.handle(request)
            self._write(response.status, response.headers, response.body)

    def _write(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.server.request_deadline(self.connection).bind_socket(self.connection)
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

    def __init__(
        self,
        server_address,
        handler_class,
        max_handlers: int,
        request_deadline_seconds: float = 5.0,
        dispatch_deadline_seconds: float | None = None,
    ) -> None:
        self._handler_slots = threading.BoundedSemaphore(max_handlers)
        self._request_deadline_seconds = request_deadline_seconds
        self._dispatch_deadline_seconds = (
            request_deadline_seconds if dispatch_deadline_seconds is None else dispatch_deadline_seconds
        )
        self.tls_context: ssl.SSLContext | None = None
        self._request_deadlines: dict[int, _ConnectionDeadline] = {}
        self._request_deadlines_lock = threading.Lock()
        super().__init__(server_address, handler_class)

    def get_request(self):
        raw, address = super().get_request()
        self._ensure_request_deadline(raw)
        return raw, address

    def request_deadline(self, connection: socket.socket) -> MonotonicDeadline:
        return self._ensure_request_deadline(connection).deadline

    def application_deadline(self, connection: socket.socket, request: Request) -> MonotonicDeadline:
        """Promote only the exact executor dispatch route to the accept-time total budget."""

        if (
            request.method.upper() == "POST"
            and re.fullmatch(
                r"/v1/sandboxes/[^/?]+/proxy/18000/v1/tasks/execute(?:\?[^#]*)?",
                request.target,
            )
        ):
            return self._ensure_request_deadline(connection).promote()
        return self.request_deadline(connection)

    def _ensure_request_deadline(self, connection: socket.socket) -> "_ConnectionDeadline":
        with self._request_deadlines_lock:
            value = self._request_deadlines.get(id(connection))
            if value is None:
                total = MonotonicDeadline.after(self._dispatch_deadline_seconds)
                value = _ConnectionDeadline(total, total.bounded(self._request_deadline_seconds), connection)
                self._request_deadlines[id(connection)] = value
        return value

    def _replace_request_connection(self, old: socket.socket, new: socket.socket) -> None:
        with self._request_deadlines_lock:
            value = self._request_deadlines.pop(id(old), None)
            if value is None:
                total = MonotonicDeadline.after(self._dispatch_deadline_seconds)
                value = _ConnectionDeadline(total, total.bounded(self._request_deadline_seconds), new)
            else:
                value.replace(new)
            self._request_deadlines[id(new)] = value

    def release_request_deadline(self, connection: socket.socket) -> None:
        with self._request_deadlines_lock:
            value = self._request_deadlines.pop(id(connection), None)
        if value is not None:
            value.cancel()

    def process_request(self, request, client_address) -> None:
        deadline = self.request_deadline(request)
        if not self._handler_slots.acquire(blocking=False):
            try:
                deadline.bind_socket(request)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: 46\r\nConnection: close\r\n\r\n{\"error\":{\"code\":\"concurrency_limit_reached\"}}"
                )
            except (DeadlineExceeded, OSError, socket.timeout):
                pass
            finally:
                self.release_request_deadline(request)
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.release_request_deadline(request)
            self.shutdown_request(request)
            self._handler_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        connection = request
        try:
            deadline = self.request_deadline(request)
            deadline.bind_socket(request)
            if self.tls_context is not None:
                connection = self.tls_context.wrap_socket(
                    request,
                    server_side=True,
                    do_handshake_on_connect=False,
                )
                self._replace_request_connection(request, connection)
                deadline.bind_socket(connection)
                connection.do_handshake()
            with deadline_scope(deadline):
                self.finish_request(connection, client_address)
        except (DeadlineExceeded, OSError, ssl.SSLError, socket.timeout):
            pass
        except Exception:
            # Never expose request, header, TLS, or upstream details.
            pass
        finally:
            try:
                self.release_request_deadline(connection)
                if connection is not request:
                    self.release_request_deadline(request)
                self.shutdown_request(connection)
            finally:
                if connection is not request:
                    request.close()
                self._handler_slots.release()


class _ConnectionDeadline:
    """Own one accept-time total budget plus its shorter ingress phase cap."""

    def __init__(
        self,
        total_deadline: MonotonicDeadline,
        phase_deadline: MonotonicDeadline,
        connection: socket.socket,
    ) -> None:
        self._total_deadline = total_deadline
        self._phase_deadline = phase_deadline
        self._promoted = False
        self._connection = connection
        self._lock = threading.Lock()
        self._total_timer = total_deadline.arm(self._expire_total)
        self._phase_timer = phase_deadline.arm(self._expire_phase)

    @property
    def deadline(self) -> MonotonicDeadline:
        """Return the current cap without ever extending the accept-time total."""

        with self._lock:
            return self._total_deadline if self._promoted else self._phase_deadline

    def promote(self) -> MonotonicDeadline:
        """Release the ingress cap while retaining the already-running total timer."""

        with self._lock:
            self._promoted = True
            deadline = self._total_deadline
        self._phase_timer.cancel()
        deadline.remaining()
        return deadline

    def replace(self, connection: socket.socket) -> None:
        """Move expiry ownership from the raw socket to its TLS wrapper."""

        with self._lock:
            self._connection = connection
        if self.deadline.expired():
            self._expire_total()

    def cancel(self) -> None:
        """Release the timer after every terminal connection path."""

        self._phase_timer.cancel()
        self._total_timer.cancel()

    def _expire_phase(self) -> None:
        with self._lock:
            if self._promoted:
                return
            connection = self._connection
        _expire_connection(connection)

    def _expire_total(self) -> None:
        with self._lock:
            connection = self._connection
        _expire_connection(connection)


def _broker_loop(broker: MailboxBroker, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            handled = broker.poll_once()
        except Exception:
            handled = 0
        stop.wait(0.02 if handled else 0.1)


def _expire_connection(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        connection.close()


def _verify_certificate_ip_san(cert_path: str, public_authority: str) -> None:
    """Require the configured private literal IP as an exact certificate IP SAN."""

    host = public_authority.rsplit(":", 1)[0]
    expected = str(__import__("ipaddress").ip_address(host))
    try:
        decoded = ssl._ssl._test_decode_cert(cert_path)
    except (OSError, ValueError, ssl.SSLError):
        raise ValueError("gateway TLS certificate is invalid") from None
    ip_sans = {
        str(__import__("ipaddress").ip_address(value))
        for kind, value in decoded.get("subjectAltName", ())
        if kind == "IP Address"
    }
    if ip_sans != {expected}:
        raise ValueError("gateway TLS certificate IP SAN does not exactly match public authority")


def _app_scoped_upstream_ca_path(env: Mapping[str, str]) -> str:
    path = _required(env, "OPENSANDBOX_GATEWAY_UPSTREAM_CA_FILE")
    if path != UPSTREAM_CA_BUNDLE_PATH:
        raise ValueError("gateway upstream CA path is not app-scoped")
    return path


def _load_upstream_tls_context(ca_path: str) -> ssl.SSLContext:
    """Load only the app-scoped CA roots into a hostname-verifying client context."""

    path = pathlib.Path(ca_path)
    try:
        evidence = path.lstat()
    except OSError:
        raise ValueError("gateway upstream CA bundle is invalid") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(evidence.st_mode)
        or not 0 < evidence.st_size <= 1024 * 1024
        or stat.S_IMODE(evidence.st_mode) & 0o022
    ):
        raise ValueError("gateway upstream CA bundle is invalid")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.load_verify_locations(cafile=str(path))
    except (OSError, ssl.SSLError):
        raise ValueError("gateway upstream CA bundle is invalid") from None
    return context


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
