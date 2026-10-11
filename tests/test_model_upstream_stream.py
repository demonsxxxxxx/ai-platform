from __future__ import annotations

import socket
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.execution.infrastructure import model_upstream as upstream
from app.execution.infrastructure.model_security import ValidatedEndpoint
from app.execution.infrastructure.model_upstream import ModelUpstreamError


class Body(httpx.SyncByteStream):
    def __init__(self, chunks=(), *, error=None, close_error=None):
        self.chunks = chunks
        self.error = error
        self.close_error = close_error
        self.closed = 0

    def __iter__(self):
        yield from self.chunks
        if self.error:
            raise self.error

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


def _endpoint(base_url="https://gateway.example", *, ips=("192.0.2.1", "192.0.2.2")):
    url = httpx.URL(base_url)
    return ValidatedEndpoint(base_url, url.host, url.port or (443 if url.scheme == "https" else 80), url.scheme, ips)


def _mock(monkeypatch, handler, *, endpoint=None):
    clients = []
    monkeypatch.setattr(upstream, "validate_endpoint", lambda *_a, **_kw: endpoint or _endpoint())

    def create(timeout_seconds):
        client = httpx.Client(
            transport=httpx.MockTransport(handler), trust_env=False,
            follow_redirects=False, timeout=timeout_seconds,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(upstream, "_new_client", create)
    return clients


def _call(operation="stream", **kwargs):
    defaults = dict(
        base_url="https://gateway.example", allowed_internal_hosts="", api_key="synthetic-key",
        method="POST", path="/v1/messages", provider="anthropic", body=b'{"model":"synthetic"}',
    )
    defaults.update(kwargs)
    function = upstream.open_upstream_stream if operation == "stream" else upstream.request_upstream
    return function(**defaults)


@pytest.mark.parametrize("operation", ["stream", "request"])
@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout, httpx.ReadError, httpx.WriteTimeout, httpx.WriteError,
    httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.PoolTimeout,
])
def test_accepted_post_is_not_replayed_after_transport_failure(monkeypatch, operation, failure):
    accepted = []

    def send(request):
        accepted.append((request.url.host, request.content))
        raise failure("synthetic after dispatch")

    clients = _mock(monkeypatch, send)
    with pytest.raises(ModelUpstreamError, match="^model_upstream_unavailable$"):
        _call(operation)
    assert accepted == [("192.0.2.1", b'{"model":"synthetic"}')]
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.parametrize("operation", ["stream", "request"])
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
def test_only_connect_failure_can_try_next_approved_ip(monkeypatch, operation, failure):
    attempted = []
    accepted = []

    def send(request):
        attempted.append(request.url.host)
        if len(attempted) == 1:
            raise failure("synthetic before dispatch")
        accepted.append(request.content)
        return httpx.Response(200, stream=Body([b"ok"]))

    clients = _mock(monkeypatch, send)
    response = _call(operation)
    assert (b"".join(response.body()) if operation == "stream" else response.body) == b"ok"
    assert attempted == ["192.0.2.1", "192.0.2.2"]
    assert accepted == [b'{"model":"synthetic"}']
    assert len(clients) == 2 and all(client.is_closed for client in clients)


@pytest.mark.parametrize("operation", ["stream", "request"])
def test_response_body_failure_never_replays_post(monkeypatch, operation):
    accepted = []
    body = Body([b"partial"], error=httpx.ReadError("synthetic interrupted body"))

    def send(request):
        accepted.append(request.url.host)
        return httpx.Response(200, stream=body)

    clients = _mock(monkeypatch, send)
    expected = httpx.ReadError if operation == "stream" else ModelUpstreamError
    with pytest.raises(expected):
        response = _call(operation)
        if operation == "stream":
            list(response.body())
    assert accepted == ["192.0.2.1"]
    assert body.closed == 1 and clients[0].is_closed


@pytest.mark.parametrize("operation", ["stream", "request"])
@pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 307, 308, 399])
@pytest.mark.parametrize("location", ["https://other.example/", None])
def test_all_redirects_are_rejected_without_second_dispatch(monkeypatch, operation, status, location):
    calls = []
    body = Body([b"not read"])

    def send(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": location} if location else {}, stream=body)

    clients = _mock(monkeypatch, send)
    with pytest.raises(ModelUpstreamError, match="^model_upstream_redirect_rejected$"):
        _call(operation)
    assert len(calls) == 1 and body.closed == 1 and clients[0].is_closed


@pytest.mark.parametrize("operation", ["stream", "request"])
def test_compressed_response_fails_closed_before_decoding(monkeypatch, operation):
    body = Body([b"not even a gzip body"])

    def send(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=body)

    clients = _mock(monkeypatch, send)
    with pytest.raises(ModelUpstreamError, match="^model_upstream_unavailable$"):
        _call(operation, headers={"accept-encoding": "gzip"})
    assert body.closed == 1 and clients[0].is_closed


@pytest.mark.parametrize("operation", ["stream", "request"])
@pytest.mark.parametrize("limit,too_large", [(6, False), (5, True)])
def test_response_limit_is_cumulative(monkeypatch, operation, limit, too_large):
    body = Body([b"123", b"456"])
    clients = _mock(monkeypatch, lambda _r: httpx.Response(200, stream=body))
    if too_large:
        with pytest.raises(ModelUpstreamError, match="^model_upstream_response_too_large$"):
            response = _call(operation, max_response_bytes=limit)
            if operation == "stream":
                iterator = response.body()
                assert next(iterator) == b"123"
                next(iterator)
    else:
        response = _call(operation, max_response_bytes=limit)
        assert (b"".join(response.body()) if operation == "stream" else response.body) == b"123456"
    assert body.closed == 1 and clients[0].is_closed


def test_count_tokens_retains_8192_byte_bound(monkeypatch):
    body = Body([b"x" * 8192, b"x"])
    clients = _mock(monkeypatch, lambda _r: httpx.Response(200, stream=body))
    with pytest.raises(ModelUpstreamError, match="model_upstream_response_too_large"):
        _call("request", path="/v1/messages/count_tokens", max_response_bytes=8192)
    assert body.closed == 1 and clients[0].is_closed


@pytest.mark.parametrize("started", [True, False])
def test_explicit_owner_close_is_idempotent_even_before_body_starts(monkeypatch, started):
    body = Body([b"event", b"later"])
    clients = _mock(monkeypatch, lambda _r: httpx.Response(200, stream=body))
    response = _call()
    iterator = response.body()
    if started:
        assert next(iterator) == b"event"
        iterator.close()
    else:
        response.close()
    response.close()
    assert body.closed == 1 and clients[0].is_closed


def test_client_closes_even_if_response_close_fails(monkeypatch):
    body = Body([b"event"], close_error=OSError("synthetic close failure"))
    clients = _mock(monkeypatch, lambda _r: httpx.Response(200, stream=body))
    response = _call()
    iterator = response.body()
    assert next(iterator) == b"event"
    with pytest.raises(OSError, match="synthetic close failure"):
        iterator.close()
    assert body.closed == 1 and clients[0].is_closed


def test_read_failure_closes_response_and_client(monkeypatch):
    body = Body(error=httpx.ReadTimeout("synthetic idle timeout"))
    clients = _mock(monkeypatch, lambda _r: httpx.Response(200, stream=body))
    with pytest.raises(httpx.ReadTimeout, match="synthetic idle timeout"):
        next(_call().body())
    assert body.closed == 1 and clients[0].is_closed


@pytest.mark.parametrize("base_url,host", [
    ("https://gateway.example", "gateway.example"),
    ("https://gateway.example:80", "gateway.example:80"),
    ("http://gateway.example:443", "gateway.example:443"),
    ("https://[2001:4860:4860::8888]:8443", "[2001:4860:4860::8888]:8443"),
])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_pinned_request_preserves_authority_and_filters_credentials(monkeypatch, base_url, host, provider):
    seen = []

    def send(request):
        seen.append(request)
        return httpx.Response(200, stream=Body())

    endpoint = _endpoint(base_url, ips=("2001:4860:4860::8844",))
    _mock(monkeypatch, send, endpoint=endpoint)
    response = _call(
        provider=provider, path="/v1/messages" if provider == "anthropic" else "/v1/responses",
        query="beta=true" if provider == "anthropic" else "",
        headers={
            "authorization": "Bearer untrusted", "x-api-key": "untrusted",
            "cookie": "private=untrusted", "host": "other.example",
            "x-ai-platform-internal-token": "untrusted", "content-type": "application/json",
            "anthropic-version": "2023-06-01", "anthropic-beta": "synthetic-beta",
        },
    )
    response.close()
    request = seen[0]
    assert request.url.host == "2001:4860:4860::8844"
    assert request.headers["host"] == host
    assert request.extensions["sni_hostname"] == endpoint.hostname
    assert request.headers["accept-encoding"] == "identity"
    assert request.headers["content-type"] == "application/json"
    assert "untrusted" not in str(request.headers)
    if provider == "anthropic":
        assert request.headers["x-api-key"] == "synthetic-key"
        assert "authorization" not in request.headers
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.url.query == b"beta=true"
    else:
        assert request.headers["authorization"] == "Bearer synthetic-key"
        assert "x-api-key" not in request.headers
        assert "anthropic-version" not in request.headers


@pytest.mark.parametrize("operation,timeout", [("stream", 3600.0), ("request", 20.0)])
def test_default_timeouts_are_explicit_and_status_body_pass_through(monkeypatch, operation, timeout):
    seen = []

    def send(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(429, headers={"content-type": "application/json"}, stream=Body([b"limited"]))

    _mock(monkeypatch, send)
    response = _call(operation)
    assert response.status == 429
    assert (b"".join(response.body()) if operation == "stream" else response.body) == b"limited"
    assert seen == [dict(connect=timeout, read=timeout, write=timeout, pool=timeout)]


@contextmanager
def _serve(handler, *, tls=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _pin_local_dns(monkeypatch, *, fail_second_lookup=False):
    resolve = socket.getaddrinfo
    original_lookups = []

    def lookup(host, port, *args, **kwargs):
        if host == "gateway.example":
            original_lookups.append(host)
            if fail_second_lookup and len(original_lookups) > 1:
                raise AssertionError("original hostname resolved again after validation")
            return resolve("127.0.0.1", port, *args, **kwargs)
        return resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    return original_lookups


@pytest.mark.parametrize("chunked", [True, False])
def test_small_event_is_forwarded_before_upstream_response_completes(monkeypatch, chunked):
    first, last = b"data: first\n\n", b"data: last\n\n"
    release = threading.Event()
    completed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding" if chunked else "content-length", "chunked" if chunked else str(len(first) + len(last)))
            self.end_headers()
            self.wfile.write(f"{len(first):x}\r\n".encode() + first + b"\r\n" if chunked else first)
            self.wfile.flush()
            try:
                release.wait(timeout=5)
                self.wfile.write(f"{len(last):x}\r\n".encode() + last + b"\r\n0\r\n\r\n" if chunked else last)
                self.wfile.flush()
            finally:
                completed.set()

    lookups = _pin_local_dns(monkeypatch, fail_second_lookup=True)
    with _serve(Handler) as port:
        response = _call(base_url=f"http://gateway.example:{port}", allowed_internal_hosts="gateway.example", timeout_seconds=3)
        iterator = response.body()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(next, iterator)
                try:
                    assert pending.result(timeout=1) == first
                    assert not completed.is_set()
                finally:
                    release.set()
            assert b"".join(iterator) == last
        finally:
            release.set()
            iterator.close()
            response.close()
        assert response.response.is_closed and response.client.is_closed
    assert lookups == ["gateway.example"]


def _tls_pair(tmp_path, hostname):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2100, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "synthetic-cert.pem", tmp_path / "synthetic-key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_file, key_file)
    client_context = ssl.create_default_context(cafile=str(cert_file))
    return server_context, client_context


@pytest.mark.parametrize("certificate_hostname", ["gateway.example", "wrong.example"])
def test_real_tls_uses_original_sni_and_certificate_hostname(monkeypatch, tmp_path, certificate_hostname):
    server_context, client_context = _tls_pair(tmp_path, certificate_hostname)
    sni = []
    seen = []
    server_context.set_servername_callback(lambda _sock, hostname, _context: sni.append(hostname))
    monkeypatch.setattr(upstream, "tls_context", lambda: client_context)
    lookups = _pin_local_dns(monkeypatch, fail_second_lookup=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            seen.append((self.headers["host"], self.headers["x-api-key"]))
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    # These values must never redirect the approved request through a proxy.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with _serve(Handler, tls=server_context) as port:
        kwargs = dict(base_url=f"https://gateway.example:{port}", allowed_internal_hosts="gateway.example", timeout_seconds=2)
        if certificate_hostname == "gateway.example":
            assert _call("request", **kwargs).body == b"ok"
            assert seen == [(f"gateway.example:{port}", "synthetic-key")]
        else:
            with pytest.raises(ModelUpstreamError, match="^model_upstream_unavailable$"):
                _call("request", **kwargs)
            assert seen == []
    assert sni == ["gateway.example"]
    assert lookups == ["gateway.example"]


@pytest.mark.parametrize("wire", [
    b"HTTP/1.1 invalid-status\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nz\r\n",
    b"HTTP/1.1 200 OK\r\nX-Incomplete: " + b"x" * (110 * 1024),
], ids=["malformed-status", "malformed-chunk", "oversized-incomplete-header"])
def test_real_http_parser_failure_is_bounded_and_closes_transport(monkeypatch, wire):
    clients = []
    create = upstream._new_client

    def track_client(timeout_seconds):
        client = create(timeout_seconds)
        clients.append(client)
        return client

    monkeypatch.setattr(upstream, "_new_client", track_client)
    _pin_local_dns(monkeypatch, fail_second_lookup=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            self.connection.sendall(wire)
            self.close_connection = True

    with _serve(Handler) as port:
        with pytest.raises(ModelUpstreamError, match="^model_upstream_unavailable$"):
            _call(
                "request", base_url=f"http://gateway.example:{port}",
                allowed_internal_hosts="gateway.example", timeout_seconds=2,
            )
    assert len(clients) == 1 and clients[0].is_closed
