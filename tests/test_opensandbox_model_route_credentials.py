from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone

import pytest

from services.opensandbox_gateway.adapters import InMemoryStateStore, SQLiteStateStore
from services.opensandbox_gateway.gateway import GatewayError, LeaseRecord


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
        metadata={"ai-platform.model_id": "deepseek-v4-flash"},
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


def test_model_route_receipt_rejects_expiry_replay_and_binding_drift() -> None:
    store = InMemoryStateStore()
    record = _record()
    store.save(record)

    _consume(store, record)
    with pytest.raises(GatewayError, match="model_route_replayed"):
        _consume(store, record)
    with pytest.raises(GatewayError, match="model_route_binding_mismatch"):
        _consume(store, record, provider="openai")
    with pytest.raises(GatewayError, match="model_route_binding_mismatch"):
        _consume(store, record, path="/v1/messages/count_tokens")
    with pytest.raises(GatewayError, match="model_route_binding_mismatch"):
        _consume(store, record, model="other-model")
    with pytest.raises(GatewayError, match="model_route_expired"):
        _consume(store, record, request_id="b" * 32, created_at=80.0, now=101.0)
    assert store.model_route_receipt_count(record.sandbox_id) == 1


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
    assert store.model_route_receipt_count(record.sandbox_id) == 4


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
    assert store.model_route_receipt_count(record.sandbox_id) == 0


def test_model_route_rejects_inactive_or_cross_attempt_lease() -> None:
    store = InMemoryStateStore()
    inactive = _record()
    inactive.state = "cleanup_pending"
    store.save(inactive)
    with pytest.raises(GatewayError, match="model_route_lease_inactive"):
        _consume(store, inactive)
    assert store.model_route_receipt_count(inactive.sandbox_id) == 0

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
    assert store.model_route_receipt_count(active.sandbox_id) == 0
