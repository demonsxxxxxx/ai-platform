"""Model administration normalizes locally; the offloaded request owns DNS approval."""
from __future__ import annotations

import asyncio
import base64
import socket
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.execution.application.model_control_plane import ModelControlPlaneService
from app.execution.infrastructure import model_upstream
from app.execution.infrastructure.model_security import (
    ModelConnectionSecurityError,
    ModelEndpointSecurityAdapter,
    normalize_endpoint_url,
    validate_endpoint,
)


@pytest.mark.parametrize(("raw", "expected"), [
    (" HTTPS://Gateway.Example.:443/v1/ ", "https://gateway.example"),
    ("https://gateway.example:80", "https://gateway.example:80"),
    ("http://gateway.example:443/v1", "http://gateway.example:443"),
    ("https://[2001:4860:4860::8888]:8443/v1", "https://[2001:4860:4860::8888]:8443"),
    ("https://XN--BCHER-KVA.example/v1", "https://xn--bcher-kva.example"),
    ("https://BÜCHER.example/v1", "https://bücher.example"),
])
def test_origin_normalization_is_pure_and_preserves_existing_host_spelling(monkeypatch, raw, expected):
    def forbidden_dns(*_args, **_kwargs):
        pytest.fail("pure origin normalization must not resolve a hostname")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_dns)
    assert normalize_endpoint_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "https://user:password@gateway.example", "https://gateway.example?x=1",
    "https://gateway.example#fragment", "ftp://gateway.example", "https://gateway.example:0",
    "https://gateway.example:65536", "https://gateway.example:not-a-port", "https://[invalid",
    "https://gateway.example/other", "https://gateway.example\n/path", "https://gateway.example\x00",
])
def test_invalid_origin_is_rejected_before_dns(monkeypatch, raw):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: pytest.fail("invalid origin reached DNS"))
    with pytest.raises(ModelConnectionSecurityError, match="model_connection_endpoint_"):
        normalize_endpoint_url(raw)
    with pytest.raises(ModelConnectionSecurityError, match="model_connection_endpoint_"):
        validate_endpoint(raw)


class Repository:
    def __init__(self, saved_origin="https://gateway.example"):
        self.saved_origin = saved_origin
        self.in_transaction = False
        self.publications = []

    @asynccontextmanager
    async def transaction(self):
        assert not self.in_transaction
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False

    async def active_connection(self, _conn, **_kwargs):
        return SimpleNamespace(base_url=self.saved_origin, api_key="synthetic-saved-key")

    async def require_provider(self, _conn, *, provider_id):
        assert provider_id == "legacy"
        return {"id": provider_id, "name": "Synthetic gateway"}

    async def connection_projection(self, _conn, *, provider_id="legacy"):
        return {"revision": 1}

    async def admin_models(self, _conn, *, provider_id="legacy"):
        return []

    async def publish_models(self, _conn, **kwargs):
        self.publications.append(kwargs)
        return 2, kwargs["models"]

    async def catalog_access(self, _conn):
        return {}


def _service(repository):
    return ModelControlPlaneService(
        transaction_factory=repository.transaction,
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
            model_connection_allowed_internal_hosts="",
        ),
        repository=repository, security=ModelEndpointSecurityAdapter(),
        upstream=model_upstream.ModelUpstreamAdapter(),
        attempt_capability_verifier=lambda **_kwargs: False,
    )


def _mock_http(monkeypatch):
    requests = []
    clients = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'{"data":[{"id":"mock-model"}]}'))
    def client(_timeout_seconds):
        result = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
        clients.append(result)
        return result
    monkeypatch.setattr(model_upstream, "_new_client", client)
    return requests, clients


def _public_dns():
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


async def _until_set(event):
    async def poll():
        while not event.is_set():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(poll(), timeout=2)


def _entry():
    return dict(id="mock-model", value="mock-model", display_name="Mock", enabled=True,
                order=1, auto_compact_window=200_000, max_output_tokens=2048)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "publish"])
async def test_dns_runs_once_in_request_worker_without_holding_transaction(monkeypatch, operation):
    repository = Repository()
    service = _service(repository)
    requests, clients = _mock_http(monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    calls = []
    def dns(host, port, **_kwargs):
        calls.append((host, port, threading.get_ident()))
        entered.set()
        try:
            assert threading.get_ident() != main_thread
            assert not repository.in_transaction
            assert release.wait(2), "test did not release DNS worker"
            return _public_dns()
        finally:
            finished.set()
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    kwargs = dict(base_url="HTTPS://Gateway.Example.:443/v1/", api_key=None)
    if operation == "publish":
        kwargs.update(expected_revision=1, models=[_entry()], actor_user_id="synthetic-admin")
    task = asyncio.create_task(getattr(service, operation)(**kwargs))
    try:
        await _until_set(entered)
        # Reaching this coroutine while DNS is paused proves event-loop progress.
        assert not task.done()
        assert repository.publications == []
        release.set()
        result = await asyncio.wait_for(task, 2)
        assert len(calls) == 1
        assert calls[0][:2] == ("gateway.example", 443)
        assert requests[0].url.host == "8.8.8.8"
        assert requests[0].headers["host"] == "gateway.example"
        assert requests[0].headers["authorization"] == "Bearer synthetic-saved-key"
        assert all(client.is_closed for client in clients)
        if operation == "publish":
            assert result["revision"] == 2
            assert repository.publications[0]["base_url"] == "https://gateway.example"
        else:
            assert result["base_url"] == "https://gateway.example"
            assert result["models"][0]["provider_id"] == "legacy"
            assert result["models"][0]["provider_name"] == "Synthetic gateway"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await _until_set(finished)


@pytest.mark.asyncio
@pytest.mark.parametrize("different_origin", [
    "https://other.example/v1", "https://gateway.example:8443", "http://gateway.example",
    "https://xn--bcher-kva.example",
])
async def test_saved_key_never_reused_for_different_origin(monkeypatch, different_origin):
    repository = Repository()
    requests, _clients = _mock_http(monkeypatch)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: pytest.fail("keyless new origin reached DNS"))
    with pytest.raises(ValueError, match="model_connection_api_key_required"):
        await _service(repository).discover(base_url=different_origin, api_key=None)
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ips", [("10.0.0.8",), ("8.8.8.8", "10.0.0.8"), ("169.254.169.254",)])
async def test_final_request_rejects_private_or_mixed_dns_before_sending_key(monkeypatch, ips):
    requests, clients = _mock_http(monkeypatch)
    calls = []
    def dns(*_args, **_kwargs):
        calls.append(threading.get_ident())
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    with pytest.raises(ModelConnectionSecurityError, match="model_connection_endpoint_forbidden"):
        await _service(Repository()).discover(base_url="https://gateway.example", api_key=None)
    assert len(calls) == 1
    assert calls[0] != threading.get_ident()
    assert requests == clients == []


@pytest.mark.asyncio
async def test_cancelled_publication_during_dns_never_enters_write_transaction(monkeypatch):
    repository = Repository()
    service = _service(repository)
    requests, clients = _mock_http(monkeypatch)
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    original_request = service._upstream.request
    def request(**kwargs):
        try:
            return original_request(**kwargs)
        finally:
            completed.set()
    service._upstream = SimpleNamespace(request=request, parse_model_ids=service._upstream.parse_model_ids)
    def dns(*_args, **_kwargs):
        entered.set()
        assert release.wait(2), "test did not release DNS worker"
        return _public_dns()
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    task = asyncio.create_task(service.publish(
        base_url="https://gateway.example", api_key=None, expected_revision=1,
        models=[_entry()], actor_user_id="synthetic-admin",
    ))
    try:
        await _until_set(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert repository.publications == []
        assert not repository.in_transaction
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await _until_set(completed)
    assert len(requests) == 1  # Cancellation does not pretend to interrupt synchronous DNS/GET.
    assert all(client.is_closed for client in clients)
    assert repository.publications == []
