from __future__ import annotations

import concurrent.futures
import base64
import hashlib
import json
import ssl
import stat
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import services.opensandbox_gateway.adapters as gateway_adapters
from services.opensandbox_gateway.adapters import InMemoryStateStore, MailboxBroker, SQLiteStateStore
from services.opensandbox_gateway.gateway import GatewayError, LeaseRecord
from services.opensandbox_gateway.server import _model_provider_credentials


def _record(*, sandbox_id: str = "sandbox-one", attempt_id: str = "attempt-one") -> LeaseRecord:
    scope = {
        "tenant_id": "tenant-one",
        "workspace_id": "workspace-one",
        "user_id": "user-one",
        "session_id": "session-one",
        "run_id": "run-one",
        "attempt_id": attempt_id,
    }
    return LeaseRecord(
        sandbox_id=sandbox_id,
        scope=scope,
        metadata={
            "ai-platform.model_id_sha256": base64.b32encode(
                hashlib.sha256(b"deepseek-v4-flash").digest()
            ).decode("ascii").rstrip("=")
        },
        image="registry.example/executor@sha256:" + "1" * 64,
        image_digest="sha256:" + "1" * 64,
        workspace_host_path=f"/data/opensandbox/workspaces/{sandbox_id}",
        mounts=[],
        canonical_request_hash="2" * 64,
        executor_token_hash="3" * 64,
        created_at=datetime.now(timezone.utc).isoformat(),
        signature="4" * 64,
    )


def _consume(
    store,
    record: LeaseRecord,
    *,
    request_id: str = "a" * 32,
    provider: str = "anthropic",
    method: str = "POST",
    path: str = "/v1/messages",
    model: str = "deepseek-v4-flash",
    created_at: float = 100.0,
    now: float = 101.0,
    request_limit: int = 4,
) -> None:
    store.consume_model_route(
        sandbox_id=record.sandbox_id,
        request_id=request_id,
        provider=provider,
        method=method,
        path=path,
        model=model,
        created_at=created_at,
        now=now,
        ttl_seconds=15.0,
        request_limit=request_limit,
    )


def _receipt_count(store: InMemoryStateStore, sandbox_id: str) -> int:
    return sum(value[0] == sandbox_id for value in store._model_route_receipts.values())


def test_model_route_receipt_rejects_expiry_replay_and_binding_drift() -> None:
    store = InMemoryStateStore()
    record = _record()
    store.save(record)

    _consume(store, record)
    with pytest.raises(GatewayError, match="model_route_replayed"):
        _consume(store, record)
    with pytest.raises(GatewayError, match="model_route_binding_mismatch"):
        _consume(store, record, provider="openai", path="/chat/completions")
    with pytest.raises(GatewayError, match="model_route_binding_mismatch"):
        _consume(store, record, path="/v1/messages/count_tokens")
    with pytest.raises(GatewayError, match="model_route_binding_mismatch"):
        _consume(store, record, model="other-model")
    with pytest.raises(GatewayError, match="model_route_expired"):
        _consume(store, record, request_id="b" * 32, created_at=80.0, now=101.0)
    assert _receipt_count(store, record.sandbox_id) == 1


def test_model_route_receipt_survives_gateway_restart(tmp_path) -> None:
    path = tmp_path / "gateway-state.sqlite3"
    record = _record()
    first = SQLiteStateStore(str(path))
    first.save(record)
    _consume(first, record)

    restarted = SQLiteStateStore(str(path))
    with pytest.raises(GatewayError, match="model_route_replayed"):
        _consume(restarted, record)


def test_model_route_request_limit_allows_multi_turn_then_fails_closed() -> None:
    store = InMemoryStateStore()
    record = _record()
    store.save(record)

    for index in range(4):
        _consume(store, record, request_id=f"{index:032x}", request_limit=4)
    with pytest.raises(GatewayError, match="model_route_limit_exceeded"):
        _consume(store, record, request_id="f" * 32, request_limit=4)
    assert _receipt_count(store, record.sandbox_id) == 4


def test_model_route_receipt_is_atomic_under_concurrency(tmp_path) -> None:
    store = SQLiteStateStore(str(tmp_path / "gateway-state.sqlite3"))
    record = _record()
    store.save(record)

    def consume() -> str:
        try:
            _consume(store, record)
        except GatewayError as exc:
            return exc.code
        return "accepted"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: consume(), range(2)))

    assert sorted(results) == ["accepted", "model_route_replayed"]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("provider", "other", "model_route_provider_not_allowed"),
        ("method", "GET", "model_route_method_not_allowed"),
        ("path", "/v1/other", "model_route_path_not_allowed"),
        ("model", "other-model", "model_route_model_mismatch"),
    ],
)
def test_model_route_hostile_scope_is_rejected_before_receipt(field: str, value: str, code: str) -> None:
    store = InMemoryStateStore()
    record = _record()
    store.save(record)
    values = {"request_id": "a" * 32, field: value}

    with pytest.raises(GatewayError, match=code):
        _consume(store, record, **values)
    assert _receipt_count(store, record.sandbox_id) == 0


def test_model_route_rejects_inactive_or_cross_attempt_lease() -> None:
    store = InMemoryStateStore()
    inactive = _record()
    inactive.state = "cleanup_pending"
    store.save(inactive)
    with pytest.raises(GatewayError, match="model_route_lease_inactive"):
        _consume(store, inactive)
    assert _receipt_count(store, inactive.sandbox_id) == 0

    active = _record(sandbox_id="sandbox-two", attempt_id="attempt-two")
    store.save(active)
    with pytest.raises(GatewayError, match="model_route_attempt_mismatch"):
        store.consume_model_route(
            sandbox_id=active.sandbox_id,
            request_id="b" * 32,
            provider="anthropic",
            method="POST",
            path="/v1/messages",
            model="deepseek-v4-flash",
            attempt_id="attempt-one",
            created_at=100.0,
            now=101.0,
            ttl_seconds=15.0,
            request_limit=4,
        )
    assert _receipt_count(store, active.sandbox_id) == 0


def test_model_provider_credentials_are_file_only_and_legacy_config_fails_closed(tmp_path) -> None:
    openai_path = tmp_path / "openai-api-key"
    anthropic_path = tmp_path / "anthropic-auth-token"
    openai_path.write_text("host-openai-secret\n", encoding="utf-8")
    anthropic_path.write_text("host-anthropic-secret\n", encoding="utf-8")
    env = {
        "OPENSANDBOX_GATEWAY_OPENAI_API_KEY_FILE": str(openai_path),
        "OPENSANDBOX_GATEWAY_ANTHROPIC_AUTH_TOKEN_FILE": str(anthropic_path),
    }

    assert _model_provider_credentials(env) == {
        "openai": "host-openai-secret",
        "anthropic": "host-anthropic-secret",
    }
    with pytest.raises(ValueError, match="OPENSANDBOX_GATEWAY_ANTHROPIC_AUTH_TOKEN_FILE"):
        _model_provider_credentials({"OPENSANDBOX_GATEWAY_OPENAI_API_KEY_FILE": str(openai_path)})


def test_mailbox_model_route_denials_and_replay_never_add_upstream_dispatch(monkeypatch) -> None:
    store = InMemoryStateStore()
    record = _record()
    store.save(record)
    dispatched: list[tuple[str, bytes, dict[str, str]]] = []

    class FakeResponse:
        status = 200

        @staticmethod
        def read(_limit):
            return b'{"ok":true}'

        @staticmethod
        def getheaders():
            return [("content-type", "application/json")]

    class FakeConnection:
        sock = None

        def __init__(self, *_args):
            pass

        def request(self, _method, path, *, body, headers):
            dispatched.append((path, body, dict(headers)))

        @staticmethod
        def getresponse():
            return FakeResponse()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr(gateway_adapters, "_PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr(gateway_adapters.os, "open", lambda *_, **__: 7)
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _fd: None)
    monkeypatch.setattr(gateway_adapters.os, "getgid", lambda: 4321, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    policy = SimpleNamespace(
        targets={
            "callback": ("https://models.internal.example", ("10.56.0.211",)),
            "openai": ("https://models.internal.example/openai/v1", ("10.56.0.211",)),
            "anthropic": ("https://models.internal.example/anthropic", ("10.56.0.211",)),
        }
    )
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    tls_context.check_hostname = True
    tls_context.verify_mode = ssl.CERT_REQUIRED

    def process(broker: MailboxBroker, request_id: str, model: str) -> None:
        body = json.dumps({"model": model, "stream": True}).encode()
        raw = json.dumps(
            {
                "version": 1,
                "method": "POST",
                "path": "/model/openai/chat/completions",
                "headers": {"authorization": "Bearer sandbox-secret", "content-type": "application/json"},
                "body": base64.b64encode(body).decode("ascii"),
                "created_at_unix_seconds": time.time(),
                "timeout_seconds": 30.0,
            }
        ).encode()
        evidence = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o640,
            st_size=len(raw),
            st_uid=1000,
            st_gid=4321,
            st_dev=1,
            st_ino=2,
            st_mtime_ns=3,
            st_ctime_ns=4,
        )
        chunks = iter((raw, b""))
        monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: evidence)
        monkeypatch.setattr(gateway_adapters.os, "read", lambda *_: next(chunks))
        broker._process(6, request_id + ".json", record=record)

    trusted = MailboxBroker(
        store,
        policy,
        1.0,
        1024,
        upstream_tls_context=tls_context,
        provider_credentials={"openai": "host-openai-secret", "anthropic": "host-anthropic-secret"},
    )
    with pytest.raises(GatewayError, match="model_route_model_mismatch"):
        process(trusted, "a" * 32, "cross-attempt-model")
    assert dispatched == []

    missing = MailboxBroker(store, policy, 1.0, 1024, upstream_tls_context=tls_context)
    with pytest.raises(GatewayError, match="model_provider_credential_unavailable"):
        process(missing, "b" * 32, "deepseek-v4-flash")
    assert dispatched == []

    process(trusted, "c" * 32, "deepseek-v4-flash")
    with pytest.raises(GatewayError, match="model_route_replayed"):
        process(trusted, "c" * 32, "deepseek-v4-flash")
    assert len(dispatched) == 1
    assert json.loads(dispatched[0][1])["stream"] is True
    assert _receipt_count(store, record.sandbox_id) == 1
    assert "sandbox-secret" not in repr(dispatched)
    assert "host-openai-secret" not in repr(store.records)
    assert "host-openai-secret" not in repr(store.denials)
