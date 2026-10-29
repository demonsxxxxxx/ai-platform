from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.execution.api import RecoveryInvocationAuthority
from app.bootstrap import recovery_operations
from app.routes import runtime_callbacks
from app.runtime.sandbox.contracts import RecoveryOperationCallbackRequest
from app.sandbox.api import SandboxCallbackIdentity


@pytest.mark.asyncio
async def test_started_recovery_callback_uses_exact_typed_run_attempt_authority(monkeypatch):
    callback_identity = SandboxCallbackIdentity(
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
    )
    run = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
    }
    lease = {"lease_payload_json": {"owner_generation": 4}}
    identity_calls = []

    class CallbackAuthority:
        async def lock_run_then_attempt(self, _conn, identity):
            assert identity == callback_identity
            identity_calls.append(identity)
            return run, lease

        async def lock_provider_session_append(self, *_args):
            pytest.fail("a recovery operation must not use SessionStore append tail authority")

    class Cursor:
        async def fetchone(self):
            return {
                "execution_spec_json": {
                    "input": {
                        "_runtime_tool_policy_subjects": [
                            {
                                "identity": "mcp:server/tool",
                                "registered": True,
                                "declared": True,
                                "active": True,
                                "distributed": True,
                            }
                        ]
                    }
                }
            }

    class Connection:
        async def execute(self, _query, _parameters):
            return Cursor()

    class RecoveryService:
        authoritative_queries = {}

        async def _authority(self, _conn, invocation):
            assert invocation.owner_generation == 4
            assert invocation.run_id == callback_identity.run_id
            assert invocation.attempt_id == callback_identity.attempt_id
            return RecoveryInvocationAuthority(requires_recovery_gate=False)

        async def record_started(self, _conn, *, invocation, tool_facts, public_summary):
            assert invocation.owner_generation == 4
            assert invocation.invocation_id == "native-call-a"
            assert invocation.tool_identity == "mcp:server/tool"
            assert tool_facts == {"write_capable": True}
            assert public_summary == "本轮已授权外部工具操作"
            return {"operation_key": "rop_private", "verified_outcome": "unknown"}

    connection = Connection()

    @asynccontextmanager
    async def callback_transaction(
        _transaction_factory,
        *,
        authorize,
        run_id,
        attempt_id,
        callback_token_id,
        allow_provider_tail,
    ):
        assert allow_provider_tail is False
        await authorize(
            connection,
            run_id=run_id,
            attempt_id=attempt_id,
            callback_token_id=callback_token_id,
            allow_provider_tail=allow_provider_tail,
        )
        yield connection

    monkeypatch.setattr(runtime_callbacks, "_sandbox_callback_authority", CallbackAuthority())
    monkeypatch.setattr(runtime_callbacks, "_receive_callback_identity", lambda *_args: callback_identity)
    monkeypatch.setattr(runtime_callbacks, "provider_callback_transaction", callback_transaction)
    monkeypatch.setattr(
        recovery_operations,
        "build_recovery_operations",
        lambda: (RecoveryService(), object()),
    )

    result = await runtime_callbacks.recovery_operation_callback(
        RecoveryOperationCallbackRequest(
            run_id="run-a",
            attempt_id="attempt-a",
            callback_token_id="cbt:run-a:attempt-a",
            invocation_id="native-call-a",
            tool_identity="mcp:server/tool",
            operation="started",
        )
    )

    assert result == {"operation_key": "rop_private", "verified_outcome": "unknown"}
    assert identity_calls == [callback_identity, callback_identity]


@pytest.mark.asyncio
async def test_generic_recovery_identity_cannot_request_provider_tail_authority(monkeypatch):
    class ForbiddenAuthority:
        async def lock_run_then_attempt(self, *_args):
            pytest.fail("generic recovery identity must not reach a provider-tail lock")

        async def lock_provider_session_append(self, *_args):
            pytest.fail("generic recovery identity must not become an append identity")

    monkeypatch.setattr(runtime_callbacks, "_sandbox_callback_authority", ForbiddenAuthority())
    identity = SandboxCallbackIdentity(
        run_id="run-a",
        attempt_id="attempt-a",
        callback_token_id="cbt:run-a:attempt-a",
    )

    with pytest.raises(HTTPException) as caught:
        await runtime_callbacks._lock_callback_authority(
            object(), identity=identity, allow_provider_tail=True
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "provider_tail_identity_required"
