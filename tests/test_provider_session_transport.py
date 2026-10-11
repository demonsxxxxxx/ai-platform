from __future__ import annotations
import app.runs.infrastructure.postgres as _owner_runs_infrastructure_postgres
import app.sandbox.infrastructure.leases_postgres as _owner_sandbox_infrastructure_leases_postgres

import json
from types import SimpleNamespace
from typing import Any

import pytest
import httpx
from fastapi import HTTPException

from app.execution.infrastructure.harness.claude.session_store import (
    MAX_PROVIDER_SESSION_RESPONSE_BYTES,
    ClaudeSessionStoreAdapter,
    ClaudeSessionStoreTransportError,
)
from app.routes import runtime_callbacks
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
    derive_callback_token,
)
from app.runtime.sandbox.contracts import ProviderSessionCallbackRequest


class _FakeResponse:
    status_code = 200

    def __init__(
        self,
        action: str = "append",
        *,
        accepted: object = True,
        entry_count: object = 1,
        next_sequence: object = 2,
        last_sequence: object = 1,
    ) -> None:
        self._payload = {
            "action": action,
            "accepted": accepted,
            "entry_count": entry_count,
            "next_sequence": next_sequence,
            "last_sequence": last_sequence if action == "append" else None,
            "entries": [],
            "subpaths": [],
        }

    async def aiter_bytes(self):
        yield json.dumps(self._payload).encode("utf-8")


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeResponse:
        return self.response

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeClient:
    requests: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.timeout = kwargs["timeout"]

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStream:
        assert method == "POST"
        self.requests.append((url, kwargs))
        payload = kwargs["json"]
        if payload["action"] == "append":
            return _FakeStream(_FakeResponse("append", next_sequence=payload["expected_sequence"] + len(payload["entries"]),
                                             last_sequence=payload["expected_sequence"] + len(payload["entries"]) - 1,
                                             entry_count=len(payload["entries"])))
        return _FakeStream(_FakeResponse(payload["action"], next_sequence=1))


@pytest.mark.asyncio
async def test_uuidless_append_retry_after_lost_response_keeps_sequence_and_batch(monkeypatch):
    class LostResponseClient(_FakeClient):
        lost = False

        def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStream:
            if kwargs["json"]["action"] == "append" and not self.lost:
                type(self).lost = True
                type(self).requests.append((url, kwargs))
                raise httpx.ReadTimeout("response lost after commit")
            return super().stream(method, url, **kwargs)

    LostResponseClient.lost = False
    LostResponseClient.requests = []
    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        LostResponseClient,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/provider-session",
        callback_token="secret", callback_token_id="cbt:run-a:attempt-a",
        run_id="run-a", attempt_id="attempt-a", provider_session_id="provider-a",
    )
    await adapter.load({"session_id": "provider-a"})
    with pytest.raises(ClaudeSessionStoreTransportError, match="callback_timeout"):
        await adapter.append({"session_id": "provider-a"}, [{"type": "assistant", "content": "one"}])
    with pytest.raises(ClaudeSessionStoreTransportError, match="append_pending"):
        await adapter.append({"session_id": "provider-a"}, [{"type": "assistant", "content": "different"}])
    await adapter.append({"session_id": "provider-a"}, [{"type": "assistant", "content": "one"}])
    assert LostResponseClient.requests[1][1]["json"] == LostResponseClient.requests[2][1]["json"]
    assert adapter._next_sequence == 2


def test_session_store_response_bound_includes_transcript_envelope():
    assert MAX_PROVIDER_SESSION_RESPONSE_BYTES == 8 * 1024 * 1024 + 64 * 1024


@pytest.mark.asyncio
async def test_session_store_adapter_does_not_forward_project_key(monkeypatch):
    _FakeClient.requests = []
    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        _FakeClient,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/provider-session",
        callback_token="secret",
        callback_token_id="cbt:run-a:attempt-a",
        run_id="run-a",
        attempt_id="attempt-a",
        provider_session_id="provider-a",
    )

    await adapter.load({"session_id": "provider-a"})
    await adapter.append(
        {
            "session_id": "provider-a",
            "project_key": "must-not-cross-boundary",
            "subpath": "worker",
        },
        [{"type": "assistant", "uuid": "entry-a"}],
    )

    payload = _FakeClient.requests[1][1]["json"]
    assert payload == {
        "action": "append",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "callback_token_id": "cbt:run-a:attempt-a",
        "provider_session_id": "provider-a",
        "subpath": "worker",
        "entries": [{"type": "assistant", "uuid": "entry-a"}],
        "expected_sequence": 1,
    }
    assert "project_key" not in payload
    assert _FakeClient.requests[1][1]["headers"] == {
        "X-AI-Platform-Callback-Token": "secret"
    }


@pytest.mark.asyncio
async def test_session_store_adapter_bounds_request_body(monkeypatch):
    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        _FakeClient,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/provider-session",
        callback_token="secret",
        callback_token_id="cbt:run-a:attempt-a",
        run_id="run-a",
        attempt_id="attempt-a",
        provider_session_id="provider-a",
        max_request_bytes=256,
    )

    await adapter.load({"session_id": "provider-a"})
    with pytest.raises(ClaudeSessionStoreTransportError, match="request_too_large"):
        await adapter.append({"session_id": "provider-a"}, [{"text": "x" * 512}])


@pytest.mark.asyncio
async def test_session_store_adapter_stops_oversized_stream(monkeypatch):
    class OversizedResponse:
        status_code = 200

        async def aiter_bytes(self):
            yield b"x" * 9

    class OversizedClient(_FakeClient):
        def stream(self, _method: str, _url: str, **_kwargs: Any) -> _FakeStream:
            return _FakeStream(OversizedResponse())

    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        OversizedClient,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/provider-session",
        callback_token="secret",
        callback_token_id="cbt:run-a:attempt-a",
        run_id="run-a",
        attempt_id="attempt-a",
        provider_session_id="provider-a",
        max_response_bytes=8,
    )

    with pytest.raises(ClaudeSessionStoreTransportError, match="response_too_large"):
        await adapter.load({"session_id": "provider-a"})


@pytest.mark.asyncio
async def test_provider_session_callback_body_limit_runs_before_model_materialization(monkeypatch):
    class OversizedRequest:
        async def stream(self):
            yield b"x" * (runtime_callbacks.MAX_PROVIDER_SESSION_CALLBACK_BODY_BYTES + 1)

    with pytest.raises(HTTPException) as error:
        await runtime_callbacks._enforce_provider_session_callback_body_limit(
            OversizedRequest()
        )
    assert error.value.status_code == 413
    assert error.value.detail == "provider_session_request_too_large"

    class TimeoutClient(_FakeClient):
        def stream(self, _method: str, _url: str, **_kwargs: Any) -> _FakeStream:
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        TimeoutClient,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/provider-session",
        callback_token="secret",
        callback_token_id="cbt:run-a:attempt-a",
        run_id="run-a",
        attempt_id="attempt-a",
        provider_session_id="provider-a",
    )

    with pytest.raises(ClaudeSessionStoreTransportError, match="callback_timeout"):
        await adapter.load({"session_id": "provider-a"})


@pytest.mark.asyncio
async def test_session_store_adapter_rejects_mismatched_response_action(monkeypatch):
    class MismatchedClient(_FakeClient):
        def stream(self, _method: str, _url: str, **kwargs: Any) -> _FakeStream:
            if kwargs["json"]["action"] == "append":
                return _FakeStream(_FakeResponse("load"))
            return super().stream(_method, _url, **kwargs)

    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        MismatchedClient,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/provider-session",
        callback_token="secret",
        callback_token_id="cbt:run-a:attempt-a",
        run_id="run-a",
        attempt_id="attempt-a",
        provider_session_id="provider-a",
    )

    await adapter.load({"session_id": "provider-a"})
    with pytest.raises(ClaudeSessionStoreTransportError, match="response_invalid"):
        await adapter.append({"session_id": "provider-a"}, [{"uuid": "entry-a"}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse("append", accepted=False),
        _FakeResponse("append", entry_count=0),
    ],
)
async def test_session_store_adapter_rejects_invalid_append_receipt(monkeypatch, response):
    class RejectedClient(_FakeClient):
        def stream(self, _method: str, _url: str, **kwargs: Any) -> _FakeStream:
            if kwargs["json"]["action"] == "append":
                return _FakeStream(response)
            return super().stream(_method, _url, **kwargs)

    monkeypatch.setattr(
        "app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient",
        RejectedClient,
    )
    adapter = ClaudeSessionStoreAdapter(
        callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/provider-session",
        callback_token="secret",
        callback_token_id="cbt:run-a:attempt-a",
        run_id="run-a",
        attempt_id="attempt-a",
        provider_session_id="provider-a",
    )

    await adapter.load({"session_id": "provider-a"})
    with pytest.raises(ClaudeSessionStoreTransportError, match="append_rejected"):
        await adapter.append({"session_id": "provider-a"}, [{"uuid": "entry-a"}])


@pytest.mark.parametrize(
    ("action", "entries", "error"),
    [
        ("append", [], "append_sequence_required"),
        ("append", [{"uuid": "entry-a"}], "append_sequence_required"),
        ("load", [{"uuid": "entry-a"}], "append_fields_forbidden"),
        ("list_subkeys", [{"uuid": "entry-a"}], "append_fields_forbidden"),
    ],
)
def test_provider_session_callback_request_enforces_action_shape(action, entries, error):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match=error):
        ProviderSessionCallbackRequest(
            action=action,
            run_id="run-a",
            attempt_id="attempt-a",
            callback_token_id="cbt:run-a:attempt-a",
            provider_session_id="provider-a",
            entries=entries,
        )


@pytest.mark.asyncio
async def test_provider_session_callback_load_uses_locked_scope(monkeypatch):
    monkeypatch.setattr(
        runtime_callbacks,
        "get_settings",
        lambda: SimpleNamespace(sandbox_callback_token="secret"),
    )

    class Transaction:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async def get_run_identity(_conn: object, *, run_id: str, for_update: bool):
        return {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "agent-a",
            "status": "running",
        }

    async def current_lease(_conn: object, **_kwargs: Any):
        return [{"attempt_id": "attempt-a", "lease_payload_json": {"attempt_id": "attempt-a"}}]

    captured: dict[str, Any] = {}

    async def execute_callback(_conn: object, **kwargs: Any):
        captured.update(kwargs)
        return runtime_callbacks.context_api.ProviderSessionOperationResult(action="load")

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: Transaction())
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run_identity', get_run_identity)
    monkeypatch.setattr(
        _owner_sandbox_infrastructure_leases_postgres,
        'list_current_sandbox_runtime_leases_for_attempt',
        current_lease,
    )
    monkeypatch.setattr(
        runtime_callbacks.context_api,
        "execute_provider_session_callback",
        execute_callback,
    )

    callback = ProviderSessionCallbackRequest(
        action="load",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id=callback_token_id_for_binding(
            CallbackTokenBinding(run_id="run-a", attempt_id="attempt-a")
        ),
        provider_session_id="provider-a",
    )
    response = await runtime_callbacks.provider_session_callback(
        callback,
        callback_token=derive_callback_token("secret", callback.callback_token_id),
    )

    assert response.entries == []
    assert response.entry_count == 0
    assert captured == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "agent_id": "agent-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "provider_session_id": "provider-a",
        "action": "load",
        "entries": [],
        "subpath": None,
        "expected_sequence": None,
    }


def test_provider_session_entry_conflict_is_409_with_stable_detail():
    error = runtime_callbacks._provider_session_http_error(
        runtime_callbacks.context_api.ProviderSessionConflictError(
            "provider_session_entry_conflict"
        )
    )
    assert error.status_code == 409
    assert error.detail == "provider_session_entry_conflict"


@pytest.mark.asyncio
async def test_provider_session_callback_entry_conflict_is_409(monkeypatch):
    monkeypatch.setattr(
        runtime_callbacks,
        "get_settings",
        lambda: SimpleNamespace(sandbox_callback_token="secret"),
    )

    class Transaction:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async def get_run_identity(_conn: object, *, run_id: str, for_update: bool):
        return {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "agent-a",
            "status": "running",
        }

    async def current_lease(_conn: object, **_kwargs: Any):
        return [{"attempt_id": "attempt-a", "lease_payload_json": {"attempt_id": "attempt-a"}}]

    async def execute_callback(_conn: object, **_kwargs: Any):
        raise runtime_callbacks.context_api.ProviderSessionConflictError(
            "provider_session_entry_conflict"
        )

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: Transaction())
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run_identity', get_run_identity)
    monkeypatch.setattr(
        _owner_sandbox_infrastructure_leases_postgres,
        'list_current_sandbox_runtime_leases_for_attempt',
        current_lease,
    )
    monkeypatch.setattr(
        runtime_callbacks.context_api,
        "execute_provider_session_callback",
        execute_callback,
    )

    callback = ProviderSessionCallbackRequest(
        action="append",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id=callback_token_id_for_binding(
            CallbackTokenBinding(run_id="run-a", attempt_id="attempt-a")
        ),
        provider_session_id="provider-a",
        entries=[{"uuid": "entry-a"}],
        expected_sequence=1,
    )
    with pytest.raises(HTTPException) as error:
        await runtime_callbacks.provider_session_callback(
            callback,
            callback_token=derive_callback_token("secret", callback.callback_token_id),
        )
    assert error.value.status_code == 409
    assert error.value.detail == "provider_session_entry_conflict"


@pytest.mark.asyncio
async def test_provider_session_callback_writer_conflict_is_409(monkeypatch):
    monkeypatch.setattr(
        runtime_callbacks,
        "get_settings",
        lambda: SimpleNamespace(sandbox_callback_token="secret"),
    )

    class Transaction:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async def get_run_identity(_conn: object, *, run_id: str, for_update: bool):
        return {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "agent-a",
            "status": "running",
        }

    async def current_lease(_conn: object, **_kwargs: Any):
        return [{"attempt_id": "attempt-a", "lease_payload_json": {"attempt_id": "attempt-a"}}]

    async def execute_callback(_conn: object, **_kwargs: Any):
        raise runtime_callbacks.context_api.ProviderSessionConflictError(
            "provider_session_writer_conflict"
        )

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: Transaction())
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run_identity', get_run_identity)
    monkeypatch.setattr(
        _owner_sandbox_infrastructure_leases_postgres,
        'list_current_sandbox_runtime_leases_for_attempt',
        current_lease,
    )
    monkeypatch.setattr(
        runtime_callbacks.context_api,
        "execute_provider_session_callback",
        execute_callback,
    )

    callback = ProviderSessionCallbackRequest(
        action="list_subkeys",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id=callback_token_id_for_binding(
            CallbackTokenBinding(run_id="run-a", attempt_id="attempt-a")
        ),
        provider_session_id="provider-a",
    )
    with pytest.raises(HTTPException) as error:
        await runtime_callbacks.provider_session_callback(
            callback,
            callback_token=derive_callback_token("secret", callback.callback_token_id),
        )
    assert error.value.status_code == 409
    assert error.value.detail == "provider_session_writer_conflict"


@pytest.mark.asyncio
async def test_provider_session_callback_uses_locked_run_scope(monkeypatch):
    monkeypatch.setattr(
        runtime_callbacks,
        "get_settings",
        lambda: SimpleNamespace(sandbox_callback_token="secret"),
    )

    class Transaction:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    identity = {
        "id": "run-a",
        "tenant_id": "tenant-authoritative",
        "workspace_id": "workspace-authoritative",
        "user_id": "user-authoritative",
        "session_id": "session-authoritative",
        "agent_id": "agent-authoritative",
        "status": "running",
    }
    captured: dict[str, Any] = {}

    async def get_run_identity(_conn: object, *, run_id: str, for_update: bool):
        assert run_id == "run-a"
        return identity

    async def current_lease(_conn: object, **kwargs: Any):
        assert kwargs == {
            "tenant_id": "tenant-authoritative",
            "run_id": "run-a",
            "attempt_id": "attempt-a",
        }
        return [{"attempt_id": "attempt-a", "lease_payload_json": {"attempt_id": "attempt-a"}}]

    async def execute_callback(_conn: object, **kwargs: Any):
        captured.update(kwargs)
        return runtime_callbacks.context_api.ProviderSessionOperationResult(
            action="append",
            accepted_entry_count=1,
        )

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: Transaction())
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run_identity', get_run_identity)
    monkeypatch.setattr(
        _owner_sandbox_infrastructure_leases_postgres,
        'list_current_sandbox_runtime_leases_for_attempt',
        current_lease,
    )
    monkeypatch.setattr(
        runtime_callbacks.context_api,
        "execute_provider_session_callback",
        execute_callback,
    )

    callback = ProviderSessionCallbackRequest(
        action="append",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id=callback_token_id_for_binding(
            CallbackTokenBinding(run_id="run-a", attempt_id="attempt-a")
        ),
        provider_session_id="provider-a",
        entries=[{"uuid": "entry-a"}],
        expected_sequence=1,
    )
    response = await runtime_callbacks.provider_session_callback(
        callback,
        callback_token=derive_callback_token("secret", callback.callback_token_id),
    )

    assert response.entry_count == 1
    assert captured == {
        "tenant_id": "tenant-authoritative",
        "workspace_id": "workspace-authoritative",
        "user_id": "user-authoritative",
        "session_id": "session-authoritative",
        "agent_id": "agent-authoritative",
        "provider_session_id": "provider-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "action": "append",
        "entries": [{"uuid": "entry-a"}],
        "subpath": None,
        "expected_sequence": 1,
    }


@pytest.mark.asyncio
async def test_provider_session_callback_rejects_provider_identity_mismatch(monkeypatch):
    monkeypatch.setattr(
        runtime_callbacks,
        "get_settings",
        lambda: SimpleNamespace(sandbox_callback_token="secret"),
    )

    class Transaction:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async def get_run_identity(_conn: object, *, run_id: str, for_update: bool):
        return {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "agent-a",
            "status": "running",
        }

    async def current_lease(_conn: object, **_kwargs: Any):
        return [{"attempt_id": "attempt-a", "lease_payload_json": {"attempt_id": "attempt-a"}}]

    async def reject_identity(_conn: object, **kwargs: Any):
        assert kwargs["provider_session_id"] == "wrong-provider"
        raise runtime_callbacks.context_api.ProviderSessionConflictError(
            "provider_session_identity_mismatch"
        )

    monkeypatch.setattr(runtime_callbacks, "transaction", lambda: Transaction())
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, 'get_run_identity', get_run_identity)
    monkeypatch.setattr(
        _owner_sandbox_infrastructure_leases_postgres,
        'list_current_sandbox_runtime_leases_for_attempt',
        current_lease,
    )
    monkeypatch.setattr(
        runtime_callbacks.context_api,
        "execute_provider_session_callback",
        reject_identity,
    )

    callback = ProviderSessionCallbackRequest(
        action="load",
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id=callback_token_id_for_binding(
            CallbackTokenBinding(run_id="run-a", attempt_id="attempt-a")
        ),
        provider_session_id="wrong-provider",
    )
    with pytest.raises(HTTPException) as error:
        await runtime_callbacks.provider_session_callback(
            callback,
            callback_token=derive_callback_token("secret", callback.callback_token_id),
        )
    assert error.value.status_code == 409
    assert error.value.detail == "provider_session_identity_mismatch"


def _chunk_adapter(**kwargs):
    return ClaudeSessionStoreAdapter(
        callback_url="https://callback.example.test/provider-session",
        callback_token="synthetic", callback_token_id="synthetic",
        run_id="run-a", attempt_id="attempt-a", provider_session_id="provider-a", **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_sequence", [1, 129, 257])
async def test_sdk_whole_batch_retry_resumes_exact_unacknowledged_chunk(monkeypatch, lost_sequence):
    """Fake HTTP commit receipts, real adapter serialization and ACK checks."""
    class ReceiptClient(_FakeClient):
        requests = []
        receipts = {}
        lost = False

        def stream(self, method, url, **kwargs):
            payload = kwargs["json"]
            if payload["action"] == "append":
                sequence = payload["expected_sequence"]
                previous = self.receipts.setdefault(sequence, payload)
                assert previous == payload
                if sequence == lost_sequence and not type(self).lost:
                    type(self).lost = True
                    self.requests.append((url, kwargs))
                    raise httpx.ReadTimeout("synthetic ACK loss after commit")
            return super().stream(method, url, **kwargs)

    monkeypatch.setattr("app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient", ReceiptClient)
    adapter = _chunk_adapter()
    key = {"session_id": "provider-a"}
    await adapter.load(key)
    entries = [{"type": "assistant", "uuid": f"entry-{i}"} for i in range(257)]
    with pytest.raises(ClaudeSessionStoreTransportError, match="callback_timeout"):
        await adapter.append(key, entries)
    assert adapter.accepted_final_sequence == 0
    with pytest.raises(ClaudeSessionStoreTransportError, match="append_pending"):
        await adapter.append({**key, "subpath": "subagents/other"}, entries)
    with pytest.raises(ClaudeSessionStoreTransportError, match="append_pending"):
        await adapter.load(key)
    await adapter.append(key, entries)
    requests = [r[1]["json"] for r in ReceiptClient.requests if r[1]["json"]["action"] == "append"]
    assert [r["expected_sequence"] for r in requests] == [s for n in (1, 129, 257) for s in ([n, n] if n == lost_sequence else [n])]
    assert [len(r["entries"]) for r in ReceiptClient.receipts.values()] == [128, 128, 1]
    assert adapter.accepted_final_sequence == 257
    assert adapter._pending_batch is None


@pytest.mark.asyncio
async def test_append_preflights_all_entries_and_chunks_by_envelope(monkeypatch):
    from app.context.domain.provider_sessions import MAX_PROVIDER_SESSION_BATCH_COUNT, MAX_PROVIDER_SESSION_ENTRY_BYTES
    from app.execution.infrastructure.harness.claude.session_store import MAX_PROVIDER_SESSION_APPEND_ENTRIES, MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES
    assert MAX_PROVIDER_SESSION_APPEND_ENTRIES == MAX_PROVIDER_SESSION_BATCH_COUNT
    assert MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES == MAX_PROVIDER_SESSION_ENTRY_BYTES
    _FakeClient.requests = []
    monkeypatch.setattr("app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient", _FakeClient)
    adapter = _chunk_adapter(max_request_bytes=600)
    key = {"session_id": "provider-a"}
    await adapter.load(key)
    with pytest.raises(ClaudeSessionStoreTransportError, match="entry_too_large"):
        await adapter.append(key, [{"type": "assistant"}] * 129 + [{"content": "x" * MAX_PROVIDER_SESSION_ENTRY_BYTES}])
    assert len(_FakeClient.requests) == 1
    assert adapter._pending_batch is None
    await adapter.append(key, [{"content": "x" * 100}] * 10)
    appends = [r[1]["json"] for r in _FakeClient.requests[1:]]
    assert len(appends) > 1
    assert sum(len(r["entries"]) for r in appends) == 10
    assert all(len(json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode()) <= 600 for r in appends)
    assert adapter.accepted_final_sequence == 10
    await adapter.append(key, [])
    assert len(_FakeClient.requests) == len(appends) + 1


@pytest.mark.asyncio
async def test_concurrent_subpath_appends_are_serialized_over_global_sequence(monkeypatch):
    import asyncio
    _FakeClient.requests = []
    monkeypatch.setattr("app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient", _FakeClient)
    adapter = _chunk_adapter()
    key = {"session_id": "provider-a"}
    await adapter.load(key)
    entered, release = asyncio.Event(), asyncio.Event()
    request = adapter._request

    async def blocked_request(payload):
        if payload["expected_sequence"] == 1:
            entered.set()
            await release.wait()
        return await request(payload)

    monkeypatch.setattr(adapter, "_request", blocked_request)
    first = asyncio.create_task(adapter.append(key, [{"n": i} for i in range(129)]))
    await entered.wait()
    second = asyncio.create_task(adapter.append({**key, "subpath": "subagents/agent-a"}, [{"n": 0}]))
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    await asyncio.gather(first, second)
    appends = [r[1]["json"] for r in _FakeClient.requests[1:]]
    assert [r["expected_sequence"] for r in appends] == [1, 129, 130]
    assert [r["subpath"] for r in appends] == [None, None, "subagents/agent-a"]
    assert adapter.accepted_final_sequence == 130


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [129, 500])
async def test_real_sdk_eager_read_loop_coalesced_batch_is_chunked(monkeypatch, tmp_path, count):
    from claude_agent_sdk._internal.query import Query
    from claude_agent_sdk._internal.session_resume import build_mirror_batcher
    from app.executors.claude_agent_sdk_runner import _SessionStoreAppendTracker
    _FakeClient.requests = []
    monkeypatch.setattr("app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient", _FakeClient)
    adapter = _chunk_adapter()
    tracker = _SessionStoreAppendTracker(adapter)
    await tracker.load({"session_id": "provider-a"})
    errors = []

    async def on_error(_key, error):
        errors.append(error)

    class Transport:
        async def read_messages(self):
            for i in range(count):
                yield {"type": "transcript_mirror", "filePath": str(tmp_path / "projects/proj/provider-a.jsonl"),
                       "entries": [{"type": "assistant", "uuid": f"entry-{i}"}]}

    batcher = build_mirror_batcher(tracker, None, {"CLAUDE_CONFIG_DIR": str(tmp_path)}, on_error, flush_mode="eager")
    query = Query(Transport(), is_streaming_mode=True)
    query.set_transcript_mirror_batcher(batcher)
    await query._read_messages()
    await batcher.close()
    assert errors == []
    assert tracker.final_sequence == count and tracker.main_append_acknowledged
    assert not tracker.failed
    sizes = [len(r[1]["json"]["entries"]) for r in _FakeClient.requests[1:]]
    assert sizes == [128] * (count // 128) + ([count % 128] if count % 128 else [])


@pytest.mark.asyncio
async def test_real_sdk_exhausted_chunk_retry_keeps_runner_failed_and_final_sequence_unadvanced(monkeypatch, tmp_path):
    from claude_agent_sdk._internal.query import Query
    from claude_agent_sdk._internal.session_resume import build_mirror_batcher
    from app.executors.claude_agent_sdk_runner import _SessionStoreAppendTracker

    class FailingChunkClient(_FakeClient):
        requests = []

        def stream(self, method, url, **kwargs):
            payload = kwargs["json"]
            if payload["action"] == "append" and payload["expected_sequence"] == 129:
                self.requests.append((url, kwargs))
                raise httpx.ReadError("synthetic transport failure")
            return super().stream(method, url, **kwargs)

    monkeypatch.setattr("app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient", FailingChunkClient)
    adapter = _chunk_adapter()
    tracker = _SessionStoreAppendTracker(adapter)
    await tracker.load({"session_id": "provider-a"})
    errors = []

    async def on_error(_key, error):
        errors.append(error)

    class Transport:
        async def read_messages(self):
            yield {"type": "transcript_mirror", "filePath": str(tmp_path / "projects/proj/provider-a.jsonl"),
                   "entries": [{"type": "assistant", "uuid": f"entry-{i}"} for i in range(129)]}

    batcher = build_mirror_batcher(tracker, None, {"CLAUDE_CONFIG_DIR": str(tmp_path)}, on_error, flush_mode="eager")
    query = Query(Transport(), is_streaming_mode=True)
    query.set_transcript_mirror_batcher(batcher)
    await query._read_messages()
    await batcher.close()
    assert len(errors) == 1
    assert tracker.failed and not tracker.main_append_acknowledged
    assert tracker.final_sequence is None
    assert adapter.accepted_final_sequence == 0
    assert [r[1]["json"]["expected_sequence"] for r in FailingChunkClient.requests[1:]] == [1, 129, 129, 129]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_method", ["load", "list_subkeys"])
async def test_deferred_read_cannot_publish_partial_chunk_cursor(monkeypatch, read_method):
    import asyncio
    _FakeClient.requests = []
    monkeypatch.setattr("app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient", _FakeClient)
    adapter = _chunk_adapter()
    key = {"session_id": "provider-a"}
    await adapter.load(key)
    adapter._load_cache.clear()
    read_entered, release_read = asyncio.Event(), asyncio.Event()
    second_chunk, release_chunk = asyncio.Event(), asyncio.Event()
    request = adapter._request

    async def deferred(payload):
        if payload["action"] == read_method:
            read_entered.set()
            await release_read.wait()
        if payload["action"] == "append" and payload["expected_sequence"] == 129:
            second_chunk.set()
            await release_chunk.wait()
        return await request(payload)

    monkeypatch.setattr(adapter, "_request", deferred)
    reader = asyncio.create_task(getattr(adapter, read_method)(key))
    await read_entered.wait()
    writer = asyncio.create_task(adapter.append(key, [{"n": i} for i in range(129)]))
    await asyncio.sleep(0)
    assert not writer.done() and adapter._pending_batch is None
    release_read.set()
    await reader
    await second_chunk.wait()
    assert adapter._next_sequence == 129
    assert adapter.accepted_final_sequence == 0
    release_chunk.set()
    await writer
    assert adapter.accepted_final_sequence == 129
