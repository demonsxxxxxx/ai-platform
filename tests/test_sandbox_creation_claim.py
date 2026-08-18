import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from app.runtime.sandbox.creation_claim import (
    SandboxCreationClaimError,
    SandboxCreationClaimTimeoutError,
    SandboxCreationScope,
    acquire_sandbox_creation_claim,
)
from app.runtime.sandbox.container_provider import (
    ContainerStartFailedError,
    DockerContainerProvider,
    FakeContainerProvider,
    OpenSandboxContainerProvider,
)
from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.runtime import SandboxRuntime, SandboxRuntimeCleanupError


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
        self.release_waits = False
        self.release_started = asyncio.Event()
        self.release_gate = asyncio.Event()
        self.close_waits = False
        self.close_started = asyncio.Event()
        self.close_gate = asyncio.Event()
        self.lock_query_waits = False
        self.lock_query_started = asyncio.Event()
        self.lock_query_gate = asyncio.Event()
        self.inventory_query_waits = False
        self.inventory_query_started = asyncio.Event()
        self.inventory_query_gate = asyncio.Event()

    async def connect(self):
        return _ClaimConnection(self)


class _ClaimConnection:
    def __init__(self, store):
        self.store = store
        self.held: set[str] = set()
        self.closed = False
        self.pgconn = _ClaimPgConnection(self)

    async def execute(self, query, params=()):
        if "pg_try_advisory_lock" in query:
            if self.store.lock_query_waits:
                self.store.lock_query_started.set()
                await self.store.lock_query_gate.wait()
            key = params[0]
            acquired = key not in self.store.locks
            if acquired:
                self.store.locks.add(key)
                self.held.add(key)
            return _Cursor({"acquired": acquired})
        if "pg_advisory_unlock" in query:
            if self.store.release_waits:
                self.store.release_started.set()
                await self.store.release_gate.wait()
            if self.store.release_fails:
                raise RuntimeError("unlock failed")
            key = params[0]
            self.store.locks.discard(key)
            self.held.discard(key)
            return _Cursor({})
        if "select exists" in query:
            if self.store.inventory_query_waits:
                self.store.inventory_query_started.set()
                await self.store.inventory_query_gate.wait()
            if self.store.active:
                self.store.active_observed.set()
            return _Cursor({"active": self.store.active})
        raise AssertionError(query)

    async def close(self):
        if self.closed:
            return
        if self.store.close_waits:
            self.store.close_started.set()
            await self.store.close_gate.wait()
        self.closed = True
        self.store.closed += 1
        for key in self.held:
            self.store.locks.discard(key)
        self.held.clear()


class _ClaimPgConnection:
    def __init__(self, connection):
        self.connection = connection

    def finish(self):
        connection = self.connection
        if connection.closed:
            return
        connection.closed = True
        connection.store.closed += 1
        for key in connection.held:
            connection.store.locks.discard(key)
        connection.held.clear()


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


def test_creation_claim_key_serializes_attempts_for_the_same_run():
    first = _scope()
    second = replace(first, attempt_id="attempt-b")
    other_run = replace(first, run_id="run-b")

    assert first.canonical_key() == second.canonical_key()
    assert first.canonical_key() != other_run.canonical_key()


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
async def test_creation_claim_normal_exit_wraps_release_failure():
    store = _ClaimStore()
    store.release_fails = True

    with pytest.raises(
        SandboxCreationClaimError,
        match="sandbox creation claim release failed",
    ):
        async with acquire_sandbox_creation_claim(
            _scope(),
            timeout_seconds=1,
            connection_factory=store.connect,
        ):
            pass

    assert store.closed == 1


@pytest.mark.asyncio
async def test_creation_claim_times_out_while_another_session_holds_scope():
    store = _ClaimStore()
    async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=1, connection_factory=store.connect):
        with pytest.raises(SandboxCreationClaimTimeoutError):
            async with acquire_sandbox_creation_claim(_scope(), timeout_seconds=0.01, connection_factory=store.connect):
                pytest.fail("second owner must not enter")


@pytest.mark.asyncio
@pytest.mark.parametrize("query_phase", ["lock_query", "inventory_query"])
async def test_creation_claim_bounds_each_database_query_and_force_closes_session(query_phase):
    store = _ClaimStore()
    setattr(store, f"{query_phase}_waits", True)

    with pytest.raises(
        SandboxCreationClaimTimeoutError,
        match="sandbox creation claim unavailable",
    ):
        async with acquire_sandbox_creation_claim(
            _scope(),
            timeout_seconds=0.01,
            connection_factory=store.connect,
        ):
            pytest.fail("stalled claim query must not enter")

    assert store.closed == 1
    assert store.locks == set()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_phase", ["release", "close"])
async def test_creation_claim_cancellation_during_session_cleanup_finishes_cleanup(
    cleanup_phase,
):
    store = _ClaimStore()
    setattr(store, f"{cleanup_phase}_waits", True)
    cleanup_started = getattr(store, f"{cleanup_phase}_started")
    cleanup_gate = getattr(store, f"{cleanup_phase}_gate")

    async def release_claim():
        async with acquire_sandbox_creation_claim(
            _scope(),
            timeout_seconds=1,
            connection_factory=store.connect,
        ):
            pass

    task = asyncio.create_task(release_claim())
    await cleanup_started.wait()
    task.cancel()
    cleanup_gate.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.closed == 1
    assert store.locks == set()


@pytest.mark.asyncio
async def test_creation_claim_session_cleanup_has_a_hard_deadline():
    store = _ClaimStore()
    store.release_waits = True

    with pytest.raises(
        SandboxCreationClaimError,
        match="sandbox creation claim release failed",
    ):
        async with acquire_sandbox_creation_claim(
            _scope(),
            timeout_seconds=0.01,
            connection_factory=store.connect,
        ):
            pass

    await asyncio.sleep(0)
    assert store.closed == 1
    assert store.locks == set()


@pytest.mark.asyncio
async def test_creation_claim_force_close_failure_keeps_cleanup_timeout_visible():
    store = _ClaimStore()
    store.release_waits = True

    async def connect_without_force_finish():
        connection = _ClaimConnection(store)
        connection.pgconn = object()
        return connection

    with pytest.raises(
        SandboxCreationClaimError,
        match="sandbox creation claim release failed",
    ):
        async with acquire_sandbox_creation_claim(
            _scope(),
            timeout_seconds=0.01,
            connection_factory=connect_without_force_finish,
        ):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("body_outcome", ["cancelled", "failed"])
async def test_creation_claim_cleanup_timeout_overrides_body_outcome(body_outcome):
    store = _ClaimStore()
    store.release_waits = True
    store.close_waits = True
    entered = asyncio.Event()

    async def hold_claim():
        async with acquire_sandbox_creation_claim(
            _scope(),
            timeout_seconds=0.01,
            connection_factory=store.connect,
        ):
            entered.set()
            if body_outcome == "failed":
                raise RuntimeError("winner failed")
            await asyncio.Event().wait()

    task = asyncio.create_task(hold_claim())
    if body_outcome == "cancelled":
        await entered.wait()
        task.cancel()

    with pytest.raises(
        SandboxCreationClaimError,
        match="sandbox creation claim release failed",
    ):
        await task
    assert store.closed == 1
    assert store.locks == set()
    store.close_gate.set()
    await asyncio.sleep(0)


class _ClaimedFakeProvider(FakeContainerProvider):
    def __init__(self, provider_name="opensandbox"):
        super().__init__(executor_url="http://executor.test")
        self.provider_name = provider_name
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


def test_real_container_providers_declare_creation_claim_identity():
    assert DockerContainerProvider.provider_name == "docker"
    assert OpenSandboxContainerProvider.provider_name == "opensandbox"
    assert FakeContainerProvider.provider_name == "fake"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["docker", "opensandbox"])
async def test_runtime_cleans_recorded_lease_when_creation_claim_release_fails(
    tmp_path,
    monkeypatch,
    provider_name,
):
    class _Settings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "test-token"
        sandbox_container_provider = provider_name
        sandbox_container_start_timeout_seconds = 1

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        SandboxRuntime,
        "_trusted_callback_target",
        lambda _self, _provider_name: type(
            "CallbackTarget",
            (),
            {
                "callback_url": "http://platform.test/api/ai/runtime/callbacks/executor",
                "base_url": "http://platform.test",
            },
        )(),
    )
    store = _ClaimStore()
    store.release_fails = True
    provider = _ClaimedFakeProvider(provider_name)
    released = []

    @asynccontextmanager
    async def claim_factory(scope, *, timeout_seconds):
        async with acquire_sandbox_creation_claim(
            scope,
            timeout_seconds=timeout_seconds,
            connection_factory=store.connect,
        ) as claim:
            yield claim

    async def release_lease(lease, reason, lease_record_id):
        released.append((lease.container_id, reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=provider,
        execute_task=lambda *_args, **_kwargs: pytest.fail(
            "dispatch must not start after claim release failure"
        ),
        callback_token_resolver=lambda _token_id: "callback-token",
        record_lease=lambda *_args: "lease-a",
        release_lease=release_lease,
        creation_claim_factory=claim_factory,
    )

    with pytest.raises(ContainerStartFailedError, match="creation claim is unavailable"):
        await runtime.submit(_runtime_request())

    assert provider.create_count == 1
    assert provider.stop_count == 1
    assert released == [
        (
            "exec-run-a",
            "creation_claim_release_failed",
            "lease-a",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["docker", "opensandbox"])
async def test_runtime_cleans_recorded_lease_when_claim_release_is_cancelled(
    tmp_path,
    monkeypatch,
    provider_name,
):
    class _Settings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "test-token"
        sandbox_container_provider = provider_name
        sandbox_container_start_timeout_seconds = 1

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        SandboxRuntime,
        "_trusted_callback_target",
        lambda _self, _provider_name: type(
            "CallbackTarget",
            (),
            {
                "callback_url": "http://platform.test/api/ai/runtime/callbacks/executor",
                "base_url": "http://platform.test",
            },
        )(),
    )
    store = _ClaimStore()
    store.release_waits = True
    provider = _ClaimedFakeProvider(provider_name)
    released = []

    @asynccontextmanager
    async def claim_factory(scope, *, timeout_seconds):
        async with acquire_sandbox_creation_claim(
            scope,
            timeout_seconds=timeout_seconds,
            connection_factory=store.connect,
        ) as claim:
            yield claim

    async def release_lease(lease, reason, lease_record_id):
        released.append((lease.container_id, reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=provider,
        execute_task=lambda *_args, **_kwargs: pytest.fail(
            "dispatch must not start after claim release cancellation"
        ),
        callback_token_resolver=lambda _token_id: "callback-token",
        record_lease=lambda *_args: "lease-a",
        release_lease=release_lease,
        creation_claim_factory=claim_factory,
    )

    task = asyncio.create_task(runtime.submit(_runtime_request()))
    await store.release_started.wait()
    task.cancel()
    store.release_gate.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.create_count == 1
    assert provider.stop_count == 1
    assert store.closed == 1
    assert store.locks == set()
    assert released == [
        (
            "exec-run-a",
            "creation_claim_cancelled",
            "lease-a",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["docker", "opensandbox"])
async def test_runtime_converges_lease_record_before_cancel_cleanup(
    tmp_path,
    monkeypatch,
    provider_name,
):
    class _Settings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "test-token"
        sandbox_container_provider = provider_name
        sandbox_container_start_timeout_seconds = 1

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        SandboxRuntime,
        "_trusted_callback_target",
        lambda _self, _provider_name: type(
            "CallbackTarget",
            (),
            {
                "callback_url": "http://platform.test/api/ai/runtime/callbacks/executor",
                "base_url": "http://platform.test",
            },
        )(),
    )
    provider = _ClaimedFakeProvider(provider_name)
    store = _ClaimStore()
    record_started = asyncio.Event()
    record_gate = asyncio.Event()
    released = []

    @asynccontextmanager
    async def claim_factory(scope, *, timeout_seconds):
        async with acquire_sandbox_creation_claim(
            scope,
            timeout_seconds=timeout_seconds,
            connection_factory=store.connect,
        ) as claim:
            yield claim

    async def record_lease(*_args):
        record_started.set()
        await record_gate.wait()
        return "lease-a"

    async def release_lease(lease, reason, lease_record_id):
        released.append((lease.container_id, reason, lease_record_id))

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=provider,
        execute_task=lambda *_args, **_kwargs: pytest.fail(
            "dispatch must not start after lease record cancellation"
        ),
        callback_token_resolver=lambda _token_id: "callback-token",
        record_lease=record_lease,
        release_lease=release_lease,
        creation_claim_factory=claim_factory,
    )

    task = asyncio.create_task(runtime.submit(_runtime_request()))
    await record_started.wait()
    task.cancel()
    record_gate.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.stop_count == 1
    assert released == [
        (
            "exec-run-a",
            "creation_claim_cancelled",
            "lease-a",
        )
    ]


@pytest.mark.asyncio
async def test_runtime_cancel_cleanup_has_a_hard_deadline(tmp_path, monkeypatch):
    class _Settings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "test-token"
        sandbox_container_provider = "docker"
        sandbox_container_start_timeout_seconds = 0.01

    class _HangingStopProvider(_ClaimedFakeProvider):
        async def stop(self, lease, *, reason):
            self.stop_count += 1
            await asyncio.Event().wait()

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        SandboxRuntime,
        "_trusted_callback_target",
        lambda _self, _provider_name: type(
            "CallbackTarget",
            (),
            {
                "callback_url": "http://platform.test/api/ai/runtime/callbacks/executor",
                "base_url": "http://platform.test",
            },
        )(),
    )
    store = _ClaimStore()
    store.release_waits = True
    provider = _HangingStopProvider("docker")

    @asynccontextmanager
    async def claim_factory(scope, *, timeout_seconds):
        async with acquire_sandbox_creation_claim(
            scope,
            timeout_seconds=timeout_seconds,
            connection_factory=store.connect,
        ) as claim:
            yield claim

    runtime = SandboxRuntime(
        workspace_root=tmp_path,
        provider=provider,
        execute_task=lambda *_args, **_kwargs: pytest.fail(
            "dispatch must not start after claim cancellation"
        ),
        callback_token_resolver=lambda _token_id: "callback-token",
        record_lease=lambda *_args: "lease-a",
        release_lease=lambda *_args: None,
        creation_claim_factory=claim_factory,
    )

    task = asyncio.create_task(runtime.submit(_runtime_request()))
    await store.release_started.wait()
    task.cancel()
    store.release_gate.set()

    with pytest.raises(SandboxRuntimeCleanupError) as captured:
        await task
    assert captured.value.reason == "creation_claim_cancelled"
    assert captured.value.stop_result.message == "sandbox cleanup timed out"
    assert provider.stop_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["docker", "opensandbox"])
async def test_two_independent_runtimes_claim_before_zero_inventory_create(
    tmp_path,
    monkeypatch,
    provider_name,
):
    """The loser never reaches provider inventory/create after the winner persists its lease."""

    class _Settings:
        sandbox_callback_base_url = "http://platform.test"
        sandbox_callback_token = "test-token"
        sandbox_container_start_timeout_seconds = 1

    monkeypatch.setattr("app.runtime.sandbox.runtime.get_settings", lambda: _Settings())
    store = _ClaimStore()
    barrier = asyncio.Barrier(2)
    providers = [
        _ClaimedFakeProvider(provider_name),
        _ClaimedFakeProvider(provider_name),
    ]

    @asynccontextmanager
    async def claim_factory(scope, *, timeout_seconds):
        assert scope.provider == provider_name
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
