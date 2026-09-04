from __future__ import annotations

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
    ) -> None:
        self._payload = {
            "action": action,
            "accepted": accepted,
            "entry_count": entry_count,
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
        return _FakeStream(_FakeResponse(kwargs["json"]["action"]))


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

    await adapter.append(
        {
            "session_id": "provider-a",
            "project_key": "must-not-cross-boundary",
            "subpath": "worker",
        },
        [{"type": "assistant", "uuid": "entry-a"}],
    )

    payload = _FakeClient.requests[0][1]["json"]
    assert payload == {
        "action": "append",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "callback_token_id": "cbt:run-a:attempt-a",
        "provider_session_id": "provider-a",
        "subpath": "worker",
        "entries": [{"type": "assistant", "uuid": "entry-a"}],
    }
    assert "project_key" not in payload
    assert _FakeClient.requests[0][1]["headers"] == {
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
        max_request_bytes=128,
    )

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
        await adapter.append({"session_id": "provider-a"}, [{"uuid": "entry-a"}])


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
        await adapter.append({"session_id": "provider-a"}, [{"uuid": "entry-a"}])


@pytest.mark.asyncio
async def test_session_store_adapter_rejects_mismatched_response_action(monkeypatch):
    class MismatchedClient(_FakeClient):
        def stream(self, _method: str, _url: str, **_kwargs: Any) -> _FakeStream:
            return _FakeStream(_FakeResponse("load"))

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
        def stream(self, _method: str, _url: str, **_kwargs: Any) -> _FakeStream:
            return _FakeStream(response)

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

    with pytest.raises(ClaudeSessionStoreTransportError, match="append_rejected"):
        await adapter.append({"session_id": "provider-a"}, [{"uuid": "entry-a"}])


@pytest.mark.parametrize(
    ("action", "entries", "error"),
    [
        ("append", [], "append_entries_required"),
        ("load", [{"uuid": "entry-a"}], "entries_forbidden"),
        ("list_subkeys", [{"uuid": "entry-a"}], "entries_forbidden"),
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
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
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
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
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
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
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
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
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
    monkeypatch.setattr(runtime_callbacks.repositories, "get_run_identity", get_run_identity)
    monkeypatch.setattr(
        runtime_callbacks.repositories,
        "list_current_sandbox_runtime_leases_for_attempt",
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
