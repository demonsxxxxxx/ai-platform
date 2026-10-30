from __future__ import annotations

from dataclasses import asdict
from types import ModuleType
from types import SimpleNamespace

import pytest

from app.bootstrap import sandbox_cancel_delivery as wiring
from app.executors.base import RunPayload
import app.sandbox.api as sandbox_api
import app.sandbox.infrastructure as sandbox_infrastructure


def _claimed_row() -> dict[str, object]:
    payload = RunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="owner-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        agent_id="agent-a",
        skill_id=None,
        file_ids=["file-a"],
        input={
            "_runtime_tool_policy_subjects": [
                {"tool_name": "search", "mcp_server_config": {"secret": "never-forward"}},
            ],
        },
        owner_generation=4,
        trace_id="trace-a",
        model_id="model-id-a",
        model_value="model-value-a",
        execution_kind="harness_chat",
        schema_version="ai-platform.run-payload.v2",
    )
    return {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "owner-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "sandbox_mode": "ephemeral",
        "browser_enabled": True,
        "lease_payload_json": {"attempt_id": "attempt-a", "owner_generation": 4},
        "executor_reconciliation_context_json": {"run_payload": asdict(payload)},
    }


class _Client:
    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.calls: list[dict[str, object]] = []

    async def cancel(self, executor_url, *, run_id, attempt_id, executor_headers):
        self.calls.append({
            "executor_url": executor_url,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "executor_headers": executor_headers,
        })
        return {"status": "cancel_requested", "run_id": run_id, "attempt_id": attempt_id}


class _Provider:
    async def executor_control_endpoint(self, lease, request):
        assert lease.provider == "docker"
        assert request.tenant_id == "tenant-a"
        assert request.workspace_id == "workspace-a"
        assert request.user_id == "owner-a"
        assert request.session_id == "session-a"
        assert request.run_id == "run-a"
        assert request.attempt_id == "attempt-a"
        assert request.owner_generation == 4
        assert request.agent_id == "agent-a"
        assert request.skill_ids == []
        assert request.file_ids == []
        assert request.sandbox_mode == "ephemeral"
        assert request.browser_enabled is True
        assert request.model == "model-value-a"
        assert request.trace_id == "trace-a"
        assert request.tool_policy_subjects == [{"tool_name": "search"}]
        assert "never-forward" not in str(request.model_dump())
        assert request.callback_token_id == "reconciler"
        return "http://fresh.executor.invalid", {"Authorization": "Bearer short-lived-secret"}


def test_builder_composes_delivery_service_with_postgres_repository(monkeypatch):
    repository = ModuleType("cancel_delivery_postgres")

    class CapturingService:
        def __init__(self, repository, transaction_factory, send_cancel):
            self.repository = repository
            self.transaction_factory = transaction_factory
            self.send_cancel = send_cancel

    monkeypatch.setattr(sandbox_api, "SandboxCancelDeliveryService", CapturingService, raising=False)
    monkeypatch.setattr(
        sandbox_infrastructure,
        "cancel_delivery_postgres",
        repository,
        raising=False,
    )
    monkeypatch.setattr(wiring, "build_sandbox_runtime_cleanup", lambda: object())

    service = wiring.build_sandbox_cancel_delivery_service()

    assert service.repository is repository
    assert service.transaction_factory is wiring.transaction
    assert callable(service.send_cancel)


@pytest.mark.asyncio
async def test_cancel_sender_uses_fresh_authenticated_endpoint_for_persisted_scope(monkeypatch):
    claimed_row = _claimed_row()
    recovered = SimpleNamespace(provider="docker")
    cleanup = SimpleNamespace(
        container_lease_from_persisted_row=lambda row: recovered if row is claimed_row else None,
    )
    provider = _Provider()
    client = _Client(timeout_seconds=5)
    monkeypatch.setattr(wiring, "SandboxExecutorClient", lambda *, timeout_seconds: client)

    await wiring._send_cancel(
        claimed_row,
        sandbox_cleanup=cleanup,
        provider_factory=lambda _provider_name: provider,
    )

    assert client.timeout_seconds == 5
    assert client.calls == [{
        "executor_url": "http://fresh.executor.invalid",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "executor_headers": {"Authorization": "Bearer short-lived-secret"},
    }]
    assert "short-lived-secret" not in str(claimed_row)
    assert "fresh.executor.invalid" not in str(claimed_row)


@pytest.mark.asyncio
async def test_cancel_sender_rejects_non_exact_executor_acknowledgement():
    row = _claimed_row()
    cleanup = SimpleNamespace(container_lease_from_persisted_row=lambda _row: SimpleNamespace(provider="docker"))
    provider = _Provider()

    class BadAckClient(_Client):
        async def cancel(self, executor_url, *, run_id, attempt_id, executor_headers):
            await super().cancel(
                executor_url,
                run_id=run_id,
                attempt_id=attempt_id,
                executor_headers=executor_headers,
            )
            return {"status": "cancel_requested", "run_id": run_id, "attempt_id": attempt_id, "extra": True}

    with pytest.raises(ValueError, match="sandbox_cancel_ack_invalid"):
        await wiring._send_cancel(
            row,
            sandbox_cleanup=cleanup,
            provider_factory=lambda _provider: provider,
            client_factory=BadAckClient,
        )
