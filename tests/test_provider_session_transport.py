from __future__ import annotations
import asyncio
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


def test_provider_session_response_legacy_import_is_canonical_transport_contract():
    from app.bootstrap.execution import ProviderSessionCallbackResponse as composed
    from app.context.transport.provider_session_responses import ProviderSessionCallbackResponse as canonical
    from app.runtime.sandbox.contracts import ProviderSessionCallbackResponse as legacy

    assert legacy is composed is canonical
    assert legacy(action="load", next_sequence=1).model_dump() == {
        "action": "load", "entries": [], "subpaths": [], "accepted": True,
        "entry_count": 0, "next_sequence": 1, "last_sequence": None,
    }


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
    from app.execution.infrastructure.harness.claude.session_store_tracking import SessionStoreAppendTracker
    _FakeClient.requests = []
    monkeypatch.setattr("app.execution.infrastructure.harness.claude.session_store.httpx.AsyncClient", _FakeClient)
    adapter = _chunk_adapter()
    tracker = SessionStoreAppendTracker(adapter)
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
    from app.execution.infrastructure.harness.claude.session_store_tracking import SessionStoreAppendTracker

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
    tracker = SessionStoreAppendTracker(adapter)
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


def _child_view_http(monkeypatch, *, child_entries=None, omit_view=False, list_sequence=3):
    from app.execution.infrastructure.harness.claude import session_store

    main = [{"type": "user", "uuid": "main", "message": {"content": "current"}}]
    children = {"subagents/a": [{"type": "assistant", "uuid": "child", "message": {"content": "child"}}]}

    class Client(_FakeClient):
        requests = []

        def stream(self, method, url, **kwargs):
            payload = kwargs["json"]
            if payload["action"] == "append":
                return super().stream(method, url, **kwargs)
            self.requests.append((url, kwargs))
            response = _FakeResponse(payload["action"], next_sequence=list_sequence if payload["action"] == "list_subkeys" else 3)
            if payload["action"] == "list_subkeys":
                response._payload["subpaths"] = list(children)
                if not omit_view:
                    response._payload["child_entries"] = children if child_entries is None else child_entries
            else:
                response._payload["entries"] = children[payload["subpath"]] if payload["subpath"] else main
            return _FakeStream(response)

    monkeypatch.setattr(session_store.httpx, "AsyncClient", Client)
    return Client, main, children


@pytest.mark.asyncio
@pytest.mark.parametrize("omit_view", [False, True])
async def test_child_view_negotiates_only_list_and_old_host_keeps_original_load(monkeypatch, omit_view):
    from app.execution.infrastructure.harness.claude import session_store
    from app.execution.infrastructure.harness.claude.session_store_tracking import SessionStoreAppendTracker
    from app.runtime.sandbox import contracts

    assert session_store.PROVIDER_SESSION_READ_VIEW_HEADER == contracts.PROVIDER_SESSION_READ_VIEW_HEADER
    assert session_store.PROVIDER_SESSION_CHILD_READ_VIEW == contracts.PROVIDER_SESSION_CHILD_READ_VIEW
    assert session_store.MAX_PROVIDER_SESSION_RESPONSE_BYTES == contracts.MAX_PROVIDER_SESSION_CALLBACK_RESPONSE_BYTES
    client, main, children = _child_view_http(monkeypatch, omit_view=omit_view)
    adapter = _chunk_adapter()
    tracker = SessionStoreAppendTracker(adapter)
    assert await tracker.load("provider-a") == main
    assert await tracker.list_subkeys("provider-a") == list(children)
    assert await tracker.load({"session_id": "provider-a", "subpath": "subagents/a"}) == children["subagents/a"]
    assert len(client.requests) == (3 if omit_view else 2)
    for _, request in client.requests:
        assert (session_store.PROVIDER_SESSION_READ_VIEW_HEADER in request["headers"]) == (request["json"]["action"] == "list_subkeys")
        assert "child_entries" not in request["json"]
    assert tracker.main_append_acknowledged is False and tracker.final_sequence is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {}, {"subagents/a": []}, {"subagents/a": ["not-an-entry"]},
    {"subagents/b": [{"uuid": "child"}]}, {"subagents/a": [{}, {}]},
])
async def test_child_view_malformed_is_atomic_and_never_downgrades(monkeypatch, invalid):
    client, main, _ = _child_view_http(monkeypatch, child_entries=invalid)
    adapter = _chunk_adapter()
    await adapter.load("provider-a")
    with pytest.raises(ClaudeSessionStoreTransportError, match="response_invalid"):
        await adapter.list_subkeys("provider-a")
    assert adapter._load_cache == {("provider-a", None): main}
    assert adapter._next_sequence == 3
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_present_null_child_view_is_invalid_not_old_host(monkeypatch):
    _child_view_http(monkeypatch)
    adapter = _chunk_adapter()
    await adapter.load("provider-a")
    request = adapter._request

    async def null_view(payload):
        result = await request(payload)
        result["child_entries"] = None
        return result

    monkeypatch.setattr(adapter, "_request", null_view)
    with pytest.raises(ClaudeSessionStoreTransportError, match="response_invalid"):
        await adapter.list_subkeys("provider-a")
    assert set(adapter._load_cache) == {("provider-a", None)}


@pytest.mark.asyncio
async def test_child_view_rejects_sequence_change_without_replacing_main(monkeypatch):
    _, main, _ = _child_view_http(monkeypatch, list_sequence=4)
    adapter = _chunk_adapter()
    await adapter.load("provider-a")
    with pytest.raises(ClaudeSessionStoreTransportError, match="sequence_conflict"):
        await adapter.list_subkeys("provider-a")
    assert adapter._load_cache == {("provider-a", None): main}
    assert adapter._next_sequence == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 409, 422, 503])
async def test_child_view_http_rejection_is_not_unsupported_fallback(monkeypatch, status):
    client, _, _ = _child_view_http(monkeypatch)
    original = client.stream

    def rejected(self, method, url, **kwargs):
        stream = original(self, method, url, **kwargs)
        stream.response.status_code = status
        return stream

    monkeypatch.setattr(client, "stream", rejected)
    with pytest.raises(ClaudeSessionStoreTransportError, match="callback_rejected"):
        await _chunk_adapter().list_subkeys("provider-a")
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_child_view_cache_hit_has_cancellation_checkpoint(monkeypatch):
    client, _, _ = _child_view_http(monkeypatch)
    adapter = _chunk_adapter()
    await adapter.load("provider-a")
    await adapter.list_subkeys("provider-a")
    task = asyncio.create_task(adapter.load({"session_id": "provider-a", "subpath": "subagents/a"}))
    asyncio.get_running_loop().call_soon(task.cancel)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(client.requests) == 2
    assert not adapter._append_lock.locked()


@pytest.mark.asyncio
async def test_child_view_load_returns_only_detached_path_copies(monkeypatch):
    client, main, children = _child_view_http(monkeypatch)
    children["subagents/b"] = [{"type": "assistant", "message": {"content": "other"}}]
    original_stream = client.stream

    def stream(self, method, url, **kwargs):
        response = original_stream(self, method, url, **kwargs)
        response.response._payload["next_sequence"] = 4
        return response

    monkeypatch.setattr(client, "stream", stream)
    adapter = _chunk_adapter()
    fresh_main = await adapter.load("provider-a")
    fresh_main[0]["message"]["content"] = "mutated fresh main"
    fresh_main.append({"type": "injected"})
    fresh_main.pop(0)
    assert await adapter.load("provider-a") == main
    await adapter.list_subkeys("provider-a")
    first_child = await adapter.load({"session_id": "provider-a", "subpath": "subagents/a"})
    first_child[0]["message"]["content"] = "mutated child"
    first_child.append({"type": "injected"})
    first_child.pop(0)
    assert await adapter.load({"session_id": "provider-a", "subpath": "subagents/a"}) == children["subagents/a"]
    assert await adapter.load({"session_id": "provider-a", "subpath": "subagents/b"}) == children["subagents/b"]
    assert await adapter.load("provider-a") == main
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_child_view_is_cleared_before_uncertain_append_ack(monkeypatch):
    _child_view_http(monkeypatch)
    adapter = _chunk_adapter()
    await adapter.load("provider-a")
    await adapter.list_subkeys("provider-a")
    request = adapter._request
    lose_ack = True

    async def append_request(payload):
        nonlocal lose_ack
        assert adapter._load_cache == {}
        if lose_ack:
            lose_ack = False
            raise ClaudeSessionStoreTransportError("provider_session_callback_timeout")
        return await request(payload)

    monkeypatch.setattr(adapter, "_request", append_request)
    with pytest.raises(ClaudeSessionStoreTransportError, match="callback_timeout"):
        await adapter.append("provider-a", [{"type": "assistant", "uuid": "new"}])
    with pytest.raises(ClaudeSessionStoreTransportError, match="append_pending"):
        await adapter.load({"session_id": "provider-a", "subpath": "subagents/a"})
    with pytest.raises(ClaudeSessionStoreTransportError, match="append_pending"):
        await adapter.list_subkeys("provider-a")
    await adapter.append("provider-a", [{"type": "assistant", "uuid": "new"}])
    assert adapter.accepted_final_sequence == 3
    assert adapter._load_cache == {} and adapter._pending_batch is None


@pytest.fixture
def child_view_callback(monkeypatch):
    from contextlib import asynccontextmanager
    from app.context.application.provider_sessions import ProviderSessionOperationResult

    calls = []
    children = {"subagents/测试": [{"text": "quoted \\\"é", "number": -0.0}]}

    @asynccontextmanager
    async def transaction():
        yield object()

    async def run_identity(_conn, **_kwargs):
        return dict(tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a", session_id="session-a", agent_id="agent-a", status="running")

    async def current_lease(_conn, **_kwargs):
        return [{"attempt_id": "attempt-a", "lease_payload_json": {"attempt_id": "attempt-a"}}]

    async def execute(_conn, **kwargs):
        calls.append(kwargs)
        return ProviderSessionOperationResult(action=kwargs["action"], subpaths=tuple(children), next_sequence=3, child_entries=children)

    monkeypatch.setattr(runtime_callbacks, "transaction", transaction)
    monkeypatch.setattr(runtime_callbacks, "_require_valid_callback_token", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, "get_run_identity", run_identity)
    monkeypatch.setattr(_owner_sandbox_infrastructure_leases_postgres, "list_current_sandbox_runtime_leases_for_attempt", current_lease)
    monkeypatch.setattr(runtime_callbacks.context_api, "execute_provider_session_callback", execute)
    callback = ProviderSessionCallbackRequest(action="list_subkeys", run_id="run-a", attempt_id="attempt-a", callback_token_id="cbt-a", provider_session_id="provider-a")
    return callback, children, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("view", [None, "unknown-version"])
async def test_old_client_response_shape_does_not_gain_optional_fields(child_view_callback, view):
    from fastapi.encoders import jsonable_encoder

    callback, _, calls = child_view_callback
    response = await runtime_callbacks.provider_session_callback(callback, callback_token="test", provider_read_view=view)
    assert set(jsonable_encoder(response)) == {"action", "entries", "subpaths", "accepted", "entry_count", "next_sequence", "last_sequence"}
    assert response.last_sequence is None
    assert "include_child_entries" not in calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("allowance", [-1, 0, 1])
async def test_child_view_budget_uses_actual_encoded_response(child_view_callback, monkeypatch, allowance):
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    from app.runtime.sandbox.contracts import ProviderSessionCallbackResponse
    from app.context.transport import provider_session_responses

    callback, children, calls = child_view_callback
    model = ProviderSessionCallbackResponse(action="list_subkeys", subpaths=list(children), next_sequence=3, child_entries=children)
    expected = JSONResponse(content=jsonable_encoder(model)).body
    monkeypatch.setattr(provider_session_responses, "MAX_PROVIDER_SESSION_CALLBACK_RESPONSE_BYTES", len(expected) + allowance)
    response = await runtime_callbacks.provider_session_callback(callback, callback_token="test", provider_read_view="children-v1")
    assert calls[0]["include_child_entries"] is True
    if allowance >= 0:
        assert response.body == expected
        assert json.loads(response.body)["child_entries"] == children
    else:
        model.child_entries = None
        assert response.body == JSONResponse(content=jsonable_encoder(model)).body
        assert "child_entries" not in json.loads(response.body)


@pytest.mark.asyncio
async def test_child_view_opt_in_still_validates_response_model(child_view_callback):
    callback, children, _ = child_view_callback
    children["subagents/测试"] = ["invalid-entry"]
    with pytest.raises(HTTPException) as error:
        await runtime_callbacks.provider_session_callback(callback, callback_token="test", provider_read_view="children-v1")
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("opt_in", [False, True])
async def test_child_view_http_route_preserves_old_wire_shape(child_view_callback, opt_in):
    from fastapi import FastAPI

    callback, children, _ = child_view_callback
    app = FastAPI()
    app.include_router(runtime_callbacks.router)
    headers = {"X-AI-Platform-Provider-Read-View": "children-v1"} if opt_in else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/runtime/callbacks/provider-session", json=callback.model_dump(), headers=headers)
    assert response.status_code == 200
    expected = {"action": "list_subkeys", "entries": [], "subpaths": list(children), "accepted": True, "entry_count": 0, "next_sequence": 3, "last_sequence": None}
    if opt_in:
        expected["child_entries"] = children
    assert response.json() == expected


@pytest.mark.asyncio
async def test_real_sdk_cached_child_cancellation_cleans_materialization(monkeypatch, tmp_path):
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal import session_resume

    _child_view_http(monkeypatch)
    session_id = "00000000-0000-4000-8000-000000000041"
    adapter = ClaudeSessionStoreAdapter(callback_url="https://callback.example.test/provider-session", callback_token="synthetic", callback_token_id="synthetic", run_id="run-a", attempt_id="attempt-a", provider_session_id=session_id)
    await adapter.load(session_id)
    original_load = adapter.load

    async def cancel_at_child(key):
        if isinstance(key, dict) and key.get("subpath"):
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
        return await original_load(key)

    monkeypatch.setattr(adapter, "load", cancel_at_child)
    monkeypatch.setattr(session_resume, "_copy_auth_files", lambda *_args: None)
    materialized = tmp_path / "resume"
    materialized.mkdir()
    monkeypatch.setattr(session_resume.tempfile, "mkdtemp", lambda **_kwargs: str(materialized))
    options = ClaudeAgentOptions(session_store=adapter, resume=session_id, cwd=str(tmp_path))
    task = asyncio.create_task(session_resume.materialize_resume_session(options))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not materialized.exists()
    assert not adapter._append_lock.locked()


def _append_wire_bytes(payload):
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")


def _prefix_append_oracle(adapter, key, entries, start):
    """Pre-optimization planner, retained only as a differential test oracle."""
    from app.execution.infrastructure.harness.claude.session_store import MAX_PROVIDER_SESSION_APPEND_ENTRIES

    payloads, chunk = [], []
    for entry in entries:
        candidate = [*chunk, entry]
        try:
            if len(candidate) > MAX_PROVIDER_SESSION_APPEND_ENTRIES:
                raise ClaudeSessionStoreTransportError("provider_session_request_too_large")
            adapter._payload(action="append", key=key, entries=candidate, expected_sequence=start)
        except ClaudeSessionStoreTransportError as exc:
            if str(exc) != "provider_session_request_too_large" or not chunk:
                raise
            payloads.append(adapter._payload(action="append", key=key, entries=chunk, expected_sequence=start))
            start += len(chunk)
            chunk = [entry]
            adapter._payload(action="append", key=key, entries=chunk, expected_sequence=start)
        else:
            chunk = candidate
    if chunk:
        payloads.append(adapter._payload(action="append", key=key, entries=chunk, expected_sequence=start))
    return payloads


@pytest.mark.parametrize("start", [1, 9, 99, 999])
@pytest.mark.parametrize("count", [0, 1, 127, 128, 129, 256])
def test_append_sizing_preserves_exact_partition_and_httpx_bytes(start, count):
    import random

    rng = random.Random(987654 + start + count)
    values = [None, True, False, 0, -0.0, 1e20, 1e-20, 2**80, 5e-324,
              '中文🙂\\"\b\f\n\r\t\x00\u2028\u2029', {"nested": [[], {}, {"value": "e\u0301"}]}]
    adapter = ClaudeSessionStoreAdapter(
        callback_url="https://callback.example.test/provider-session",
        callback_token="synthetic", callback_token_id='synthetic-令牌\\"',
        run_id='run-🙂', attempt_id='attempt-\\\n', provider_session_id="provider-a",
    )
    key = {"session_id": " provider-a ", "subpath": None if count % 2 else ' 路径/"\\' + "p" * count + ' '}
    entries = [{"n": i, "value": rng.choice(values), "body": "x" * rng.randrange(150)} for i in range(count)]
    signature, frozen, sizes = adapter._append_input(key, entries)
    assert signature == json.dumps(
        ["provider-a", adapter._subpath(key), entries],
        ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"),
    )
    assert sizes == [len(_append_wire_bytes(entry)) for entry in frozen]
    session_id, subpath, _ = json.loads(signature)
    key = {"session_id": session_id, "subpath": subpath}
    empty = len(_append_wire_bytes(adapter._payload(action="append", key=key, expected_sequence=start)))
    single = empty + (sizes[0] if sizes else 0)
    full = empty + sum(sizes[:128]) + max(0, min(count, 128) - 1)
    for cap in sorted({2 * 1024 * 1024, empty - 1, single - 1, single, single + 1,
                       full - 1, full, full + 1, empty + 300, empty + 1500}):
        adapter._max_request_bytes = cap
        try:
            expected = _prefix_append_oracle(adapter, key, frozen, start)
        except ClaudeSessionStoreTransportError as exc:
            with pytest.raises(ClaudeSessionStoreTransportError, match=f"^{exc}$"):
                adapter._append_chunks(key, frozen, sizes, start)
        else:
            actual = adapter._append_chunks(key, frozen, sizes, start)
            assert [_append_wire_bytes(p) for p in actual] == [_append_wire_bytes(p) for p in expected]
            cursor = start
            for payload in actual:
                wire = _append_wire_bytes(payload)
                assert httpx.Request("POST", "https://callback.example.test", json=payload).content == wire
                assert len(wire) <= cap
                assert 1 <= len(payload["entries"]) <= 128
                assert payload["expected_sequence"] == cursor
                cursor += len(payload["entries"])
            assert cursor == start + count


@pytest.mark.asyncio
@pytest.mark.parametrize("start", [9, 99, 999])
@pytest.mark.parametrize("split", ["bytes", "count"])
async def test_append_sequence_growth_rejects_late_singleton_before_any_request(monkeypatch, start, split):
    adapter = _chunk_adapter()
    key = {"session_id": "provider-a"}
    last = {} if split == "bytes" else {"body": "x" * 500}
    entries = [{}, last] if split == "bytes" else [{}] * 128 + [last]
    adapter._max_request_bytes = len(_append_wire_bytes(adapter._payload(
        action="append", key=key, entries=[last], expected_sequence=start,
    )))
    adapter._next_sequence = start
    adapter._completed_sequence = start - 1
    cache = {("provider-a", None): [{"cached": True}]}
    adapter._load_cache = cache.copy()
    calls = []

    async def request(payload):
        calls.append(payload)
        raise AssertionError("Failed preflight must not send a prefix")

    monkeypatch.setattr(adapter, "_request", request)
    with pytest.raises(ClaudeSessionStoreTransportError, match="request_too_large"):
        await adapter.append(key, entries)
    assert calls == [] and adapter._pending_batch is None
    assert adapter._next_sequence == start and adapter.accepted_final_sequence == start - 1
    assert adapter._load_cache == cache


@pytest.mark.asyncio
@pytest.mark.parametrize("bad,error,subpath", [
    ({"body": "x" * (256 * 1024)}, "entry_too_large", None),
    ({"number": float("nan")}, "entry_not_json", None),
    ({"number": float("inf")}, "entry_not_json", None),
    ({"number": -float("inf")}, "entry_not_json", None),
    ({"value": object()}, "entry_not_json", None),
    (1, "entry_shape_invalid", None),
    ({"body": "x" * 1000}, "request_too_large", None),
    ({}, "request_too_large", "界" * 1024),
])
async def test_append_sizing_late_invalid_batch_preserves_all_state(monkeypatch, bad, error, subpath):
    adapter = _chunk_adapter(max_request_bytes=600)
    adapter._next_sequence = 9
    adapter._completed_sequence = 8
    cache = {("provider-a", None): [{"cached": True}]}
    adapter._load_cache = cache.copy()
    calls = []

    async def request(payload):
        calls.append(payload)
        raise AssertionError("Failed preflight must not send a prefix")

    monkeypatch.setattr(adapter, "_request", request)
    with pytest.raises(ClaudeSessionStoreTransportError, match=error):
        await adapter.append({"session_id": "provider-a", "subpath": subpath},
                             [{"n": i} for i in range(129)] + [bad])
    assert calls == [] and adapter._pending_batch is None
    assert adapter._next_sequence == 9 and adapter.accepted_final_sequence == 8
    assert adapter._load_cache == cache


def test_append_sizing_entry_limit_and_final_payload_guard():
    from app.execution.infrastructure.harness.claude.session_store import MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES

    adapter = _chunk_adapter()
    overhead = len(_append_wire_bytes({"body": ""}))
    entry = {"body": "x" * (MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES - overhead)}
    _, frozen, sizes = adapter._append_input("provider-a", [entry])
    assert sizes == [MAX_PROVIDER_SESSION_APPEND_ENTRY_BYTES]
    payloads = adapter._append_chunks("provider-a", frozen, sizes, 1)
    assert payloads[0]["entries"] == [entry]
    with pytest.raises(ClaudeSessionStoreTransportError, match="entry_too_large"):
        adapter._append_input("provider-a", [{"body": entry["body"] + "x"}])
    adapter._max_request_bytes = 600
    with pytest.raises(ClaudeSessionStoreTransportError, match="request_too_large"):
        # A future sizing mistake still cannot bypass the final complete validator.
        adapter._append_chunks("provider-a", frozen, [1], 1)
    with pytest.raises(ValueError, match="zip"):
        adapter._append_chunks("provider-a", [{}], [], 1)
    with pytest.raises(ValueError, match="zip"):
        adapter._append_chunks("provider-a", [], [2], 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "cancel"])
async def test_append_sizing_freezes_lock_waiter_and_replays_exact_pending_bytes(monkeypatch, failure):
    import asyncio
    import copy

    adapter = _chunk_adapter()
    adapter._next_sequence = 1
    adapter._completed_sequence = 0
    key = {"session_id": "provider-a", "subpath": "child/original"}
    entries = [{"nested": [-0.0, True, 1e20, {"value": "中文🙂"}]}]
    original_key, original_entries = copy.deepcopy(key), copy.deepcopy(entries)
    expected_signature = json.dumps(
        ["provider-a", "child/original", original_entries],
        ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"),
    )
    calls = []

    async def request(payload):
        calls.append(_append_wire_bytes(payload))
        if len(calls) == 1:
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise ClaudeSessionStoreTransportError("provider_session_callback_timeout")
        return {"next_sequence": 2}

    monkeypatch.setattr(adapter, "_request", request)
    async with adapter._append_lock:
        task = asyncio.create_task(adapter.append(key, entries))
        await asyncio.sleep(0)
        assert not task.done()
        key["subpath"] = "child/changed"
        entries[0]["nested"][:] = [0.0, 1, "changed"]
    expected_error = asyncio.CancelledError if failure == "cancel" else ClaudeSessionStoreTransportError
    with pytest.raises(expected_error):
        await task
    pending = adapter._pending_batch
    assert pending is not None and pending.signature == expected_signature
    assert adapter._next_sequence == 1 and adapter.accepted_final_sequence == 0
    expected = adapter._payload(action="append", key=original_key,
                                entries=json.loads(expected_signature)[2], expected_sequence=1)
    assert calls == [_append_wire_bytes(expected)]
    with pytest.raises(ClaudeSessionStoreTransportError, match="append_pending"):
        await adapter.append(key, entries)
    assert adapter._pending_batch is pending and len(calls) == 1
    await adapter.append(original_key, original_entries)
    assert calls == [_append_wire_bytes(expected)] * 2
    assert adapter._pending_batch is None and adapter.accepted_final_sequence == 1


@pytest.mark.parametrize("count,body_size", [(1, 0), (1, 8192), (128, 8192), (256, 8192)])
def test_append_sizing_serializes_only_empty_envelopes_and_final_chunks(monkeypatch, count, body_size):
    from app.execution.infrastructure.harness.claude import session_store

    adapter = _chunk_adapter()
    entries = [{"n": i, "body": "x" * body_size} for i in range(count)]
    calls = []

    def dumps(value, **kwargs):
        result = json.dumps(value, **kwargs)
        calls.append((len(result.encode("utf-8")), len(value["entries"]) if isinstance(value, dict) and "action" in value else None))
        return result

    monkeypatch.setattr(session_store, "json", SimpleNamespace(dumps=dumps, loads=json.loads))
    _, frozen, sizes = adapter._append_input("provider-a", entries)
    assert len(calls) == count + 1  # Original batch freeze plus existing entry checks.
    calls.clear()
    payloads = adapter._append_chunks("provider-a", frozen, sizes, 1)
    assert len(calls) == 3 * len(payloads)  # Two small envelope encodings, one final payload.
    assert [size for _, size in calls if size] == [len(p["entries"]) for p in payloads]
    assert sum(size for _, size in calls) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["append", "load", "list_subkeys"])
async def test_only_provider_append_uses_cancelling_tail_authority(monkeypatch, action):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from fastapi import HTTPException

    @asynccontextmanager
    async def transaction():
        yield object()

    token_id = "cbt:run-a:attempt-a"
    tail = AsyncMock(return_value=[{
        "attempt_id": "attempt-a", "lease_payload_json": {
            "attempt_id": "attempt-a", "callback_token_id": token_id,
        },
    }])
    execute = AsyncMock(return_value=runtime_callbacks.context_api.ProviderSessionOperationResult(
        action=action, accepted_entry_count=1,
    ))
    monkeypatch.setattr(runtime_callbacks, "transaction", transaction)
    monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: SimpleNamespace(sandbox_callback_token="secret"))
    monkeypatch.setattr(_owner_runs_infrastructure_postgres, "get_run_identity", AsyncMock(return_value={
        "tenant_id": "tenant-a", "session_id": "session-a", "status": "running",
    }))
    monkeypatch.setattr(_owner_sandbox_infrastructure_leases_postgres, "list_current_sandbox_runtime_leases_for_attempt", AsyncMock(return_value=[]))
    monkeypatch.setattr(_owner_sandbox_infrastructure_leases_postgres, "list_cancelling_provider_tail_leases_for_attempt", tail)
    monkeypatch.setattr(runtime_callbacks.context_api, "execute_provider_session_callback", execute)
    callback = ProviderSessionCallbackRequest(
        action=action, run_id="run-a", attempt_id="attempt-a", callback_token_id=token_id,
        provider_session_id="provider-a", entries=[{"uuid": "entry-a"}] if action == "append" else [],
        expected_sequence=1 if action == "append" else None,
    )
    if action == "append":
        await runtime_callbacks.provider_session_callback(callback, callback_token=derive_callback_token("secret", token_id))
        tail.assert_awaited_once()
        execute.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as caught:
            await runtime_callbacks.provider_session_callback(callback, callback_token=derive_callback_token("secret", token_id))
        assert caught.value.status_code == 409
        assert caught.value.detail == "sandbox_runtime_attempt_inactive"
        tail.assert_not_awaited()
        execute.assert_not_awaited()
