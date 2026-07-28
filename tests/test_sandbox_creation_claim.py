import asyncio
from contextlib import asynccontextmanager

import pytest

from app.runtime.sandbox.creation_claim import (
    SandboxCreationClaimTimeoutError,
    SandboxCreationScope,
    acquire_sandbox_creation_claim,
)
from app.runtime.sandbox.container_provider import ContainerStartFailedError, FakeContainerProvider
from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.runtime import SandboxRuntime


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _ClaimStore:
    def __init__(self):
        self.locks: set[str] = set()
        self.active = False
        self.closed = 0
        self.active_observed = asyncio.Event()
        self.release_fails = False

    async def connect(self):
        return _ClaimConnection(self)


class _ClaimConnection:
    def __init__(self, store):
        self.store = store
        self.held: set[str] = set()
        self.closed = False

    async def execute(self, query, params=()):
        if "pg_try_advisory_lock" in query:
            key = params[0]
            acquired = key not in self.store.locks
            if acquired:
                self.store.locks.add(key)
                self.held.add(key)
            return _Cursor({"acquired": acquired})
        if "pg_advisory_unlock" in query:
            if self.store.release_fails:
                raise RuntimeError("unlock failed")
            key = params[0]
            self.store.locks.discard(key)
            self.held.discard(key)
            return _Cursor({})
        if "select exists" in query:
            if self.store.active:
                self.store.active_observed.set()
            return _Cursor({"active": self.store.active})
        raise AssertionError(query)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.store.closed += 1
        for key in self.held:
            self.store.locks.discard(key)
        self.held.clear()


def _scope():
    return SandboxCreationScope(
        provider="opensandbox",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
    )


@pytest.mark.asyncio
async def test_creation_claim_observes_existing_exact_attempt_lease_and_closes_session():
    store = _ClaimStore()
    store.active = True

    async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect) as claim:
        assert claim.active_lease_exists is True

    assert store.locks == set()
    assert store.closed == 1


@pytest.mark.asyncio
async def test_creation_claim_winner_failure_releases_its_session_lock():
    store = _ClaimStore()

    with pytest.raises(RuntimeError, match="winner failed"):
        async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect):
            raise RuntimeError("winner failed")

    async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect) as claim:
        assert claim.active_lease_exists is False

    assert store.locks == set()
    assert store.closed == 2


@pytest.mark.asyncio
async def test_creation_claim_release_failure_does_not_mask_winner_failure():
    store = _ClaimStore()
    store.release_fails = True

    with pytest.raises(RuntimeError, match="winner failed"):
        async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect):
            raise RuntimeError("winner failed")

    assert store.closed == 1


@pytest.mark.asyncio
async def test_creation_claim_times_out_while_another_session_holds_scope():
    store = _ClaimStore()
    async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect):
        with pytest.raises(SandboxCreationClaimTimeoutError):
            async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=0.01, connection_factory=store.connect):
                pytest.fail("second owner must not enter")


@pytest.mark.asyncio
async def test_creation_claim_cancellation_closes_session_and_releases_scope():
    store = _ClaimStore()
    entered = asyncio.Event()

    async def hold_claim():
        async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold_claim())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect):
        pass
    assert store.locks == set()


class _ClaimedFakeProvider(FakeContainerProvider):
    provider_name = "opensandbox"

    def __init__(self):
        super().__init__(executor_url="http://executor.test")
        self.create_count = 0
        self.stop_count = 0

    async def create_or_reuse(self, request, workspace):
        self.create_count += 1
        return await super().create_or_reuse(request, workspace)

    async def stop(self, lease, *, reason):
        self.stop_count += 1
        return await super().stop(lease, reason=reason)


def _runtime_request() -> SandboxRuntimeRequest:
    return SandboxRuntimeRequest(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        agent_id="agent-a",
        skill_ids=[],
        mcp_tool_ids=[],
        input_message="hello",
        file_ids=[],
        sandbox_mode="ephemeral",
        browser_enabled=False,
        model="test-model",
        permissions=[],
        resource_limits={},
        callback_url="http://callback.test",
        callback_token_id="callback-a",
    )


@pytest.mark.asyncio
async def test_two_independent_runtimes_claim_before_zero_inventory_create(tmp_path, monkeypatch):
    """The loser never reaches provider inventory/create after the winner persists its lease."""

    class _Settings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "test-token"
        sandbox_container_start_timeout_seconds = 1

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: _Settings())
    store = _ClaimStore()
    barrier = asyncio.Barrier(2)
    providers = [_ClaimedFakeProvider(), _ClaimedFakeProvider()]

    @asynccontextmanager
    async def claim_factory(scope, *, timeout_seconds):
        await barrier.wait()
        async with acquire_sandbox_creation_claim(
            scope,
            timeout_seconds=timeout_seconds,
            connection_factory=store.connect,
        ) as claim:
            yield claim

    async def record_lease(*_args):
        store.active = True
        return "lease-a"

    async def execute(*_args, **_kwargs):
        await asyncio.wait_for(store.active_observed.wait(), timeout=1)
        return {"status": "accepted", "session_id": "session-a", "run_id": "run-a"}

    runtimes = [
        SandboxRuntime(
            workspace_root=tmp_path / str(index),
            provider=provider,
            execute_task=execute,
            callback_token_resolver=lambda _token_id: "callback-token",
            record_lease=record_lease,
            release_lease=lambda *_args: None,
            creation_claim_factory=claim_factory,
        )
        for index, provider in enumerate(providers)
    ]

    outcomes = await asyncio.gather(*(runtime.submit(_runtime_request()) for runtime in runtimes), return_exceptions=True)

    assert sum(provider.create_count for provider in providers) == 1
    assert sum(provider.stop_count for provider in providers) == 1
    assert sorted((provider.create_count, provider.stop_count) for provider in providers) == [(0, 0), (1, 1)]
    assert sum(isinstance(outcome, ContainerStartFailedError) for outcome in outcomes) == 1
    assert sum(getattr(outcome, "status", None) == "accepted" for outcome in outcomes) == 1
