import asyncio
import sys
from types import SimpleNamespace

import httpx
import pytest

from app.runtime.sandbox import executor_app
from app.runtime.sandbox.executor_app import create_executor_app
from app.runtime.sandbox.contracts import ExecutorTerminalResult
from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings
from tests.test_sandbox_executor_app import (
    EXECUTOR_AUTH_TOKEN,
    TRUSTED_CALLBACK_BASE_URL,
    auth_headers,
    callback_ack,
    task_payload,
)


_UNACCEPTED_SDK_SETTLEMENT = {
    "schema_version": "ai-platform.sdk-settlement.v1",
    "received_result": False,
    "input_accepted": False,
    "ack_unknown": False,
    "mirror_complete": False,
    "external_effect_unknown": False,
}


class _BlockingPreloadStore:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def load(self, _key):
        self.started.set()
        await asyncio.Event().wait()

    async def append(self, _key, _entries):
        raise AssertionError("preload cancellation must happen before provider writes")


async def _cancel_preload_through_executor(
    monkeypatch,
    tmp_path,
    *,
    tenant_id="tenant-a",
    workspace_id="workspace-a",
    user_id="user-a",
    session_id="session-a",
    agent_id="agent-a",
    run_id="run-a",
    attempt_id="qat-attempt-a",
    sdk_session_id="provider-session-a",
    provider_epoch_id=None,
    provider_session_resume_required=False,
):
    store = _BlockingPreloadStore()
    captured = {}
    callbacks = []
    monkeypatch.setattr(
        executor_app,
        "build_claude_session_store",
        lambda **_kwargs: store,
    )
    monkeypatch.setattr(
        executor_app,
        "get_settings",
        lambda: SimpleNamespace(claude_agent_sdk_enabled=True),
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        _fake_sdk(captured, hook_invocations=[]),
    )

    async def callback_sender(_url, payload, _token):
        body = dict(payload)
        callbacks.append(body)
        return callback_ack(body)

    raw = task_payload()
    raw.update(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        attempt_id=attempt_id,
        callback_token_id=f"cbt_{run_id}",
        sdk_session_id=sdk_session_id,
    )
    scope = raw["config"]["context_retrieval_scope"]
    scope.update(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        run_id=run_id,
    )
    raw["config"]["provider_session_resume_required"] = provider_session_resume_required
    if provider_epoch_id is not None:
        raw["config"]["provider_epoch_id"] = provider_epoch_id

    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id=session_id,
        expected_run_id=run_id,
        expected_attempt_id=attempt_id,
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        dispatch_in_background=True,
        terminal_callback_retry_seconds=1.0,
        heartbeat_interval_seconds=60.0,
    )
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://executor.test") as client:
            dispatched = await client.post("/v2/tasks", json=raw, headers=auth_headers())
            assert dispatched.status_code == 202
            await asyncio.wait_for(store.started.wait(), timeout=2)

            cancelled = await client.post(
                f"/v2/tasks/{run_id}/{attempt_id}/cancel",
                headers=auth_headers(),
            )
            assert cancelled.status_code == 202

            status_response = None
            for _ in range(300):
                status_response = await client.get(
                    f"/v2/tasks/{run_id}/{attempt_id}",
                    headers=auth_headers(),
                )
                body = status_response.json()
                terminal_callbacks = [item for item in callbacks if item.get("terminal_result") is not None]
                if body.get("terminal_result") is not None and terminal_callbacks:
                    break
                await asyncio.sleep(0.01)
            assert status_response is not None
            assert status_response.status_code == 200
            terminal_result = status_response.json().get("terminal_result")
            assert terminal_result is not None
            assert len(terminal_callbacks) == 1
            terminal_result = ExecutorTerminalResult.model_validate(terminal_result).model_dump(
                mode="json", exclude_none=True,
            )
            assert terminal_callbacks[0]["terminal_result"] == terminal_result

    assert not captured.get("client_connected", False)
    return terminal_result


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["session_load", "initial_usage_snapshot"])
async def test_sdk_cancellation_before_first_prompt_has_proven_unaccepted_settlement(
    monkeypatch, tmp_path, cancel_at,
):
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk

    started = asyncio.Event()
    captured = {}
    sdk = _fake_sdk(captured, hook_invocations=[])

    class Store:
        async def load(self, _key):
            if cancel_at == "session_load":
                started.set()
                await asyncio.Event().wait()
            return None

        async def append(self, _key, _entries):
            raise AssertionError("no provider input was handed off")

    async def usage_snapshot(_snapshot):
        if cancel_at == "initial_usage_snapshot":
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="first input",
        cwd=tmp_path,
        skill_id=None,
        session_id="provider-session-a",
        session_store=Store(),
        provider_session_resume_required=False,
        on_model_usage_snapshot=usage_snapshot,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    settlement = caught.value.sdk_settlement
    assert settlement.sdk_settlement == _UNACCEPTED_SDK_SETTLEMENT
    assert settlement.error == "claude_agent_sdk_cancelled"
    assert settlement.provider_final_sequence is None
    assert not captured.get("client_connected", False)


@pytest.mark.asyncio
async def test_default_runner_preload_cancel_reaches_executor_terminal_receipt(monkeypatch, tmp_path):
    terminal = await _cancel_preload_through_executor(monkeypatch, tmp_path)

    assert terminal["status"] == "cancelled"
    assert terminal["error_code"] == "executor_cancelled"
    assert terminal["sdk_settlement"] == _UNACCEPTED_SDK_SETTLEMENT


@pytest.mark.asyncio
async def test_repeated_cancel_does_not_interrupt_executor_drain(tmp_path):
    runner_entered = asyncio.Event()
    drain_started = asyncio.Event()
    release_drain = asyncio.Event()
    drain_completed = asyncio.Event()
    runner_cancellations = []
    callbacks = []

    async def executor_runner(_request, _workspace_root, _emit_event):
        runner_entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            runner_cancellations.append(True)
            drain_started.set()
            await release_drain.wait()
            drain_completed.set()
            raise

    async def callback_sender(_url, payload, _token):
        body = dict(payload)
        callbacks.append(body)
        return callback_ack(body)

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        dispatch_in_background=True,
        terminal_callback_retry_seconds=1.0,
        heartbeat_interval_seconds=60.0,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://executor.test") as client:
                dispatched = await client.post(
                    "/v2/tasks", json=task_payload(), headers=auth_headers(),
                )
                assert dispatched.status_code == 202
                await asyncio.wait_for(runner_entered.wait(), timeout=2)

                first_cancel = await client.post(
                    "/v2/tasks/run-a/qat-attempt-a/cancel", headers=auth_headers(),
                )
                assert first_cancel.status_code == 202
                await asyncio.wait_for(drain_started.wait(), timeout=2)

                second_cancel = await client.post(
                    "/v2/tasks/run-a/qat-attempt-a/cancel", headers=auth_headers(),
                )
                assert second_cancel.status_code == 202
                assert runner_cancellations == [True]
                assert not drain_completed.is_set()

                release_drain.set()
                terminal_result = None
                for _ in range(300):
                    status_response = await client.get(
                        "/v2/tasks/run-a/qat-attempt-a", headers=auth_headers(),
                    )
                    terminal_result = status_response.json().get("terminal_result")
                    if terminal_result is not None:
                        break
                    await asyncio.sleep(0.01)
                assert terminal_result is not None
                assert terminal_result["status"] == "cancelled"
    finally:
        release_drain.set()

    assert drain_completed.is_set()
    assert runner_cancellations == [True]
    assert len([item for item in callbacks if item.get("terminal_result") is not None]) == 1
