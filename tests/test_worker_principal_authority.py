from contextlib import asynccontextmanager

import pytest

from app.auth import AuthPrincipal
from app.models import QueueRunPayload
from app.worker_principal_authority import _resolve_current_principal_before_dispatch


def _payload() -> QueueRunPayload:
    return QueueRunPayload.model_construct(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="agent-a",
        skill_id="skill-a",
        executor_type="fake",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("run_status", ["queued", "running"])
async def test_current_principal_resolution_releases_preflight_transaction_before_http(
    run_status,
):
    payload = _payload()
    transaction_depth = 0
    calls = []

    @asynccontextmanager
    async def recording_transaction():
        nonlocal transaction_depth
        transaction_depth += 1
        calls.append("transaction_enter")
        try:
            yield object()
        finally:
            calls.append("transaction_exit")
            transaction_depth -= 1

    async def get_run(_conn, **_kwargs):
        assert transaction_depth == 1
        return {
            **payload.model_dump(mode="json"),
            "id": payload.run_id,
            "status": run_status,
        }

    async def resolve_current_principal(*, user_id, tenant_id):
        assert transaction_depth == 0
        calls.append("current_principal_http")
        return AuthPrincipal(
            user_id=user_id,
            display_name=user_id,
            tenant_id=tenant_id,
            roles=["user"],
            permissions=["agent:use"],
            source="test-current-principal",
        )

    principal = await _resolve_current_principal_before_dispatch(
        payload,
        transaction_factory=recording_transaction,
        run_loader=get_run,
        principal_resolver=resolve_current_principal,
    )

    assert principal is not None
    assert principal.user_id == "user-a"
    assert calls == ["transaction_enter", "transaction_exit", "current_principal_http"]


@pytest.mark.asyncio
@pytest.mark.parametrize("run_status", ["succeeded", "failed", "cancelled"])
async def test_current_principal_resolution_rejects_terminal_runs(run_status):
    payload = _payload()

    @asynccontextmanager
    async def recording_transaction():
        yield object()

    async def get_run(_conn, **_kwargs):
        return {
            **payload.model_dump(mode="json"),
            "id": payload.run_id,
            "status": run_status,
        }

    async def unexpected_principal_resolution(**_kwargs):
        raise AssertionError("terminal runs must not resolve current authority")

    principal = await _resolve_current_principal_before_dispatch(
        payload,
        transaction_factory=recording_transaction,
        run_loader=get_run,
        principal_resolver=unexpected_principal_resolution,
    )

    assert principal is None
