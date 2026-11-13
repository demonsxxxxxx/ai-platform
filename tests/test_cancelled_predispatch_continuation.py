"""Cancellation must keep the exact proof that no SDK input was dispatched."""
import asyncio
from types import SimpleNamespace

import pytest

from app.execution.api import WorkerRunCancelled, submit_run_until_cancelled
from app.executors.base import RunExecutionOwner
from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter
from app.runtime.sandbox.container_provider import FakeContainerProvider
from tests.test_sandbox_runtime import SandboxRuntime
from tests.test_sandbox_runtime import request


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preparation", "provider_creation", "lease_record", "runtime_staging"])
async def test_worker_cancel_preserves_undispatched_proof(monkeypatch, tmp_path, phase):
    entered = asyncio.Event()
    dispatched = []
    settings = SimpleNamespace(claude_agent_sdk_enabled=True, sandbox_workspace_root=str(tmp_path))
    monkeypatch.setattr("app.executors.claude_agent_worker.get_settings", lambda: settings)
    payload = SimpleNamespace(
        tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a", session_id="session-a",
        run_id="run-a", attempt_id="attempt-a", owner_generation=4,
        context_pack={"conversation_context": {"provider_epoch_id": "epoch-a"}},
    )
    adapter = ClaudeAgentWorkerAdapter()

    async def prepare(*args, **kwargs):
        if phase == "preparation":
            entered.set()
            await asyncio.Event().wait()
        return object(), None

    class Provider(FakeContainerProvider):
        async def create_or_reuse(self, *args):
            if phase == "provider_creation":
                entered.set()
                await asyncio.sleep(0.01)
            return await super().create_or_reuse(*args)

        async def stage_workspace(self, *args):
            entered.set()
            await asyncio.Event().wait()

    async def execute(*args, **kwargs):
        dispatched.append(True)
        raise AssertionError("cancelled before dispatch")

    async def record(*args):
        if phase == "lease_record":
            entered.set()
            await asyncio.sleep(0.01)
        return "lease-a"

    runtime = SandboxRuntime(
        workspace_root=tmp_path, provider=Provider(), execute_task=execute,
        record_lease=record, release_lease=lambda *args: None,
    )

    async def submit_prepared(*args, execution_owner, **kwargs):
        return await runtime.submit(
            request(attempt_id=payload.attempt_id, owner_generation=4, provider_epoch_id="epoch-a"),
            execution_owner=execution_owner,
        )

    monkeypatch.setattr(adapter, "_prepare_sdk_run", prepare)
    monkeypatch.setattr(adapter, "_submit_prepared_run_to_sandbox_runtime", submit_prepared)

    class StagingAdapter:
        async def submit_run(self, payload, *, event_sink, execution_owner):
            return await adapter._run_with_staged_skills(
                payload, event_sink=event_sink, execution_owner=execution_owner,
            )

    async def cancel_requested():
        await asyncio.wait_for(entered.wait(), 2)
        return True

    async def event_sink(**kwargs):
        pass

    with pytest.raises(WorkerRunCancelled) as caught:
        await submit_run_until_cancelled(
            StagingAdapter(), payload, owner_factory=RunExecutionOwner,
            event_sink=event_sink, cancel_requested=cancel_requested,
            poll_interval_seconds=0.001,
        )
    proof = getattr(caught.value, "predispatch_continuation_settlement", None)
    assert proof is not None, "Run cancelled without closing the prepared continuation"
    assert {key: proof[key] for key in ("tenant_id", "run_id", "attempt_id")} == {
        "tenant_id": payload.tenant_id, "run_id": payload.run_id, "attempt_id": payload.attempt_id,
    }
    assert proof["stop_evidence"] == ("provider_not_created" if phase == "preparation" else "provider_stopped")
    assert proof["sdk_settlement"]["owner_generation"] == 4
    assert proof["sdk_settlement"]["input_accepted"] is False
    assert proof["sdk_settlement"]["ack_unknown"] is False
    assert proof["external_effect_unknown"] is False
    assert dispatched == []


@pytest.mark.asyncio
async def test_cancel_waiting_for_creation_claim_has_not_created_proof(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    entered = asyncio.Event()

    class Provider(FakeContainerProvider):
        provider_name = "opensandbox"

        async def create_or_reuse(self, *args):
            raise AssertionError("creation claim has not been acquired")

    @asynccontextmanager
    async def claim(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        yield

    runtime = SandboxRuntime(workspace_root=tmp_path, provider=Provider(), creation_claim_factory=claim)
    monkeypatch.setattr(runtime, "_trusted_callback_target", lambda *args: None)
    task = asyncio.create_task(runtime.submit(request(provider_epoch_id="epoch-a")))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.predispatch_continuation_settlement["stop_evidence"] == "provider_not_created"


@pytest.mark.asyncio
async def test_unknown_creation_cancel_has_no_closing_proof(monkeypatch, tmp_path):
    entered = asyncio.Event()
    stopped = []

    class Provider(FakeContainerProvider):
        async def create_or_reuse(self, *args):
            entered.set()
            await asyncio.Event().wait()

        async def stop(self, *args, **kwargs):
            stopped.append(True)
            raise AssertionError("unknown creation has no exact provider identity")

    runtime = SandboxRuntime(workspace_root=tmp_path, provider=Provider(), record_lease=lambda *args: None)
    monkeypatch.setattr(runtime.settings, "sandbox_cleanup_timeout_seconds", 0.02)
    task = asyncio.create_task(runtime.submit(request(provider_epoch_id="epoch-a")))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert getattr(caught.value, "predispatch_continuation_settlement", None) is None
    assert stopped == []


@pytest.mark.asyncio
async def test_predispatch_failure_during_cancel_check_retains_its_proof():
    entered, failed = asyncio.Event(), asyncio.Event()
    proof = {"stop_evidence": "provider_stopped"}
    failure = ValueError("synthetic stage failure")
    failure.predispatch_continuation_settlement = proof

    class Adapter:
        async def submit_run(self, payload, **kwargs):
            entered.set()
            await failed.wait()
            raise failure

    async def cancel_requested():
        await entered.wait()
        failed.set()
        await asyncio.sleep(0)
        return True

    with pytest.raises(WorkerRunCancelled) as caught:
        await submit_run_until_cancelled(
            Adapter(), SimpleNamespace(run_id="run-a"), owner_factory=RunExecutionOwner,
            event_sink=None, cancel_requested=cancel_requested,
        )
    assert caught.value.predispatch_continuation_settlement is proof
    assert caught.value.__cause__ is failure
