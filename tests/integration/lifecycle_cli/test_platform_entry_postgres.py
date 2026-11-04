"""Platform admission -> production Worker -> durable Attempt/native epoch dispatch.

The executor is injectable: its host runs the real CLI in the continuation test.
Redis enqueue/publication and company identity lookup are synthetic; PostgreSQL
Run/Attempt/Context persistence and production admission/worker are never mocked.
"""
# ruff: noqa: F401,F811 -- imported pytest fixtures are injected by name.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.executors.base import ExecutorDispatchAccepted
from app.executors.registry import AdapterRegistry
from app.execution.api import WorkerQueueLease
from app.queue import QUEUE_ATTEMPT_ID_FIELD
from app.platform.postgres import sandbox_leases
from app.runtime.sandbox.callback_tokens import CallbackTokenBinding, callback_token_id_for_binding
from app.worker import process_run_payload
from tests.support.db_transactions import event_loop_policy
from tests.support.lifecycle_platform_entry import platform_entry


async def dispatch(platform, raw):
    captured = []

    class HostExecutor:
        name = "claude-agent-worker"

        async def submit_run(self, payload, event_sink=None, execution_owner=None):
            captured.append(payload)
            token_id = callback_token_id_for_binding(CallbackTokenBinding(
                run_id=payload.run_id, attempt_id=payload.attempt_id, owner_generation=payload.owner_generation))
            async with platform.transaction() as conn:
                lease = await sandbox_leases.create_sandbox_lease(
                    conn, **{key: getattr(payload, key) for key in (
                        "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id", "trace_id")},
                    sandbox_mode="ephemeral", provider="fake", browser_enabled=False, ttl_seconds=600,
                    resource_limits_json={}, user_visible_payload_json={},
                    lease_payload_json=dict(attempt_id=payload.attempt_id, owner_generation=payload.owner_generation,
                                            callback_token_id=token_id),
                )
            return ExecutorDispatchAccepted(run_id=payload.run_id, attempt_id=payload.attempt_id,
                                            lease_id=lease["id"], provider="fake")

    now = datetime.now(timezone.utc)
    raw[QUEUE_ATTEMPT_ID_FIELD] = "queue-" + uuid.uuid4().hex
    result = await process_run_payload(
        raw,
        AdapterRegistry({"claude-agent-worker": HostExecutor()}), worker_id="synthetic-worker",
        queue_lease=WorkerQueueLease("1" * 64, now, now + timedelta(minutes=10)),
        transaction_factory=platform.transaction, v4_capabilities=platform.capabilities,
        run_attempt_lifecycle=platform.attempts, run_lifecycle=platform.lifecycle,
    )
    assert result.status == "running", result
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_admitted_run_is_frozen_by_production_worker(platform_entry):
    platform = platform_entry
    response, raw = await platform.admit("hello")
    async with platform.transaction() as conn:
        assert (await (await conn.execute("select count(*) as n from run_attempts")).fetchone())["n"] == 0
    payload = await dispatch(platform, raw)
    assert payload.run_id == response.run_id
    async with platform.transaction() as conn:
        run = await (await conn.execute("select * from runs where id=%s", (response.run_id,))).fetchone()
        attempt = await (await conn.execute("select * from run_attempts where id=%s", (payload.attempt_id,))).fetchone()
        assert run["status"] == attempt["status"] == "running"
        assert attempt["execution_spec_sha256"]
        assert attempt["execution_spec_json"]["context_pack"] == payload.context_pack
        assert attempt["execution_spec_json"]["model_auto_compact_window"] == 100000
        assert payload.model_auto_compact_window == 100000
        native = payload.context_pack["conversation_context"]
        epoch = await (await conn.execute("select * from provider_session_epochs where id=%s",
                                        (native["provider_epoch_id"],))).fetchone()
        assert str(epoch["provider_session_id"]) == native["provider_session_id"]
        # Context claims the epoch writer on the first authenticated callback.
        assert epoch["writer_attempt_id"] is None


@pytest.mark.asyncio
async def test_admission_worker_cli_cancel_terminal_and_new_run_native_resume(platform_entry, tmp_path, monkeypatch):
    """Real CLI and PG; only model wire, company auth, Redis and host dispatch are synthetic.

    The host executor is an in-process test driver, not OpenSandbox. It owns actual
    CLI stop/disconnect and physical workspace save/restore; no native entries,
    receipts, Run, Attempt, Context or terminal events are fabricated.
    """
    import asyncio
    from pathlib import Path
    import socket
    from types import SimpleNamespace

    import claude_agent_sdk as sdk
    from fastapi import FastAPI
    import uvicorn

    from app.bootstrap import conversation_continuation as continuation, run_lifecycle
    from app.execution.infrastructure.harness.claude.session_store import ClaudeSessionStoreAdapter
    from app.routes import runtime_callbacks
    from app.runs.api import cancel_run_with_v4, WorkerResultCommitCommand
    from app.executors.base import ExecutorResult
    from app.execution.api import bind_worker_attempt_lifecycle, project_worker_terminal_result
    from app.bootstrap.worker_attempt_lifecycle import build_worker_attempt_lifecycle_ports
    from app.bootstrap.worker_result_commit import build_worker_result_commit_service
    from app.worker import parse_leased_queue_envelope
    from app.agent_apps.capability_state import exact_invoked_skills
    from app.runtime.sandbox.callback_tokens import derive_callback_token
    from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
    from tests.support.lifecycle_cli_harness import build_round_options, _blocking_server
    from tests.support.lifecycle_model_peer import Turn, synthetic_model_peer

    platform = platform_entry
    monkeypatch.setattr(runtime_callbacks, "transaction", platform.transaction)
    monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: platform.settings)
    monkeypatch.setattr(continuation, "get_settings", lambda: platform.settings)
    cancellation = run_lifecycle.build_run_cancellation_use_case(
        attempt_lifecycle=platform.attempts, lifecycle=platform.lifecycle)
    app = FastAPI()
    app.include_router(runtime_callbacks.router, prefix="/api/ai")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(20)
    listener.setblocking(False)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/api/ai/runtime/callbacks/provider-session"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    previous = None
    session_id = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    serving.result()
                await asyncio.sleep(0.01)
        with synthetic_model_peer([Turn.text(*(f"partial-{i} " for i in range(100)), delay=0.05),
                                   Turn.text("resumed answer", delay=0)]) as model:
            for turn, prompt in enumerate(("hello", "continue after stop"), 1):
                response, raw = await platform.admit(prompt, session_id)
                payload = await dispatch(platform, raw)
                session_id = response.session_id
                native = payload.context_pack["conversation_context"]
                scope = {key: getattr(payload, key) for key in (
                    "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id")}
                request = SimpleNamespace(**scope, owner_generation=payload.owner_generation,
                                          sandbox_mode="ephemeral", browser_enabled=False, tool_policy_subjects=[])
                manager = SandboxWorkspaceManager(root=platform.settings.sandbox_workspace_root)
                if previous is None:
                    workspace = manager.prepare(request)
                    Path(workspace.workspace_host_path, "draft.txt").write_text("synthetic saved draft", encoding="utf-8")
                else:
                    assert payload.run_id != previous["run_id"]
                    assert payload.attempt_id != previous["attempt_id"]
                    assert native["provider_epoch_id"] != previous["provider_epoch_id"]
                    assert native["provider_session_id"] == previous["provider_session_id"]
                    assert native["execution_mode"] == "native_resume"
                    assert native["messages"] == []
                    restored = await continuation.restore_worker_workspace(
                        payload=payload, context_pack=payload.context_pack, tool_policy_subjects=[],
                        workspace_root=platform.settings.sandbox_workspace_root)
                    assert restored is not None
                    workspace = restored[1]
                    assert Path(workspace.workspace_host_path, "draft.txt").read_text(encoding="utf-8") == "synthetic saved draft"
                    async with platform.transaction() as conn:
                        source = await (await conn.execute("select entry_canonical_json from provider_session_entries where epoch_id=%s order by sequence",
                                                          (previous["provider_epoch_id"],))).fetchall()
                        copied = await (await conn.execute("select entry_canonical_json from provider_session_entries where epoch_id=%s order by sequence",
                                                          (native["provider_epoch_id"],))).fetchall()
                        assert source == copied and source
                token_id = callback_token_id_for_binding(CallbackTokenBinding(
                    run_id=payload.run_id, attempt_id=payload.attempt_id, owner_generation=payload.owner_generation))
                store = ClaudeSessionStoreAdapter(
                    callback_url=url, callback_token=derive_callback_token(platform.settings.sandbox_callback_token, token_id),
                    callback_token_id=token_id, run_id=payload.run_id, attempt_id=payload.attempt_id,
                    provider_session_id=native["provider_session_id"], provider_epoch_id=native["provider_epoch_id"])
                await store.load(native["provider_session_id"])
                root = tmp_path / f"cli-{turn}"
                (root / "work").mkdir(parents=True)
                (root / "config").mkdir()
                client = sdk.ClaudeSDKClient(build_round_options(
                    root=root, model_url=model.url, store=store, session_id=native["provider_session_id"],
                    resume=turn == 2, mcp_server=_blocking_server(asyncio.Event(), [], asyncio.Event())))
                interrupted = False
                messages = []
                try:
                    assert payload.input["message"] == prompt
                    await client.connect(payload.input["message"])
                    async with asyncio.timeout(90):
                        async for message in client.receive_messages():
                            messages.append(message)
                            if turn == 1 and isinstance(message, sdk.StreamEvent) and not interrupted:
                                if message.event.get("type") == "content_block_delta":
                                    result = await cancellation.request_owner_cancel(
                                        tenant_id=payload.tenant_id, run_id=payload.run_id, owner_user_id=payload.user_id)
                                    assert result.status == "cancel_requested"
                                    async with platform.transaction() as conn:
                                        row = await (await conn.execute("select status from runs where id=%s", (payload.run_id,))).fetchone()
                                        assert row["status"] == "running"
                                    await client.interrupt()
                                    interrupted = True
                            if isinstance(message, sdk.ResultMessage):
                                await asyncio.sleep(0.5)
                                break
                except Exception:
                    if not (turn == 1 and interrupted):
                        raise
                finally:
                    await client.disconnect()
                assert store.continuation_receipt["ack_unknown"] is False
                assert store.accepted_final_sequence and store.accepted_final_sequence > 0
                if turn == 1:
                    assert interrupted
                    receipt = await continuation._save_stopped_workspace(
                        request=request, workspace_lease=workspace, stop_evidence="provider_stopped")
                    async with platform.transaction() as conn:
                        settled = await continuation._settle_stopped_continuation(
                            conn, tenant_id=payload.tenant_id, run_id=payload.run_id, attempt_id=payload.attempt_id,
                            final_sequence=store.accepted_final_sequence, workspace_receipt=receipt,
                            stop_evidence="provider_stopped", sdk_settlement=dict(input_accepted=True,
                            ack_unknown=False, mirror_complete=True), external_effect_unknown=False)
                        assert settled["state"] == "resumable"
                        progress = await cancel_run_with_v4(
                            conn, lifecycle=platform.lifecycle, capabilities=platform.capabilities,
                            tenant_id=payload.tenant_id, run_id=payload.run_id, result_json={"message": "cancelled"})
                        assert progress.did_transition and progress.status == "cancelled"
                        await platform.attempts.terminalize(
                            conn, tenant_id=payload.tenant_id, run_id=payload.run_id, attempt_id=payload.attempt_id,
                            status="cancelled", terminal_reason="run_cancelled")
                        lease = await (await conn.execute("select id from sandbox_leases where run_id=%s", (payload.run_id,))).fetchone()
                        await sandbox_leases.release_sandbox_lease(conn, tenant_id=payload.tenant_id,
                            user_id=payload.user_id, run_id=payload.run_id, lease_id=lease["id"], reason="synthetic_host_stopped")
                    # Observe committed Run, Attempt and terminal event using a separate connection.
                    async with platform.transaction() as conn:
                        row = await (await conn.execute("select r.status, a.status as attempt_status from runs r join run_attempts a on a.run_id=r.id where r.id=%s",
                                                       (payload.run_id,))).fetchone()
                        assert row == {"status": "cancelled", "attempt_status": "cancelled"}
                        events = await (await conn.execute("select event_type from run_events where run_id=%s", (payload.run_id,))).fetchall()
                        event_types = [event["event_type"] for event in events]
                        assert event_types.count("run_cancelled") == 1
                        assert event_types.count("run.cancelled") == 1
                        assert event_types.count("run.cancel_requested") == 1
                    previous = {**native, "run_id": payload.run_id, "attempt_id": payload.attempt_id}
                else:
                    assert any(isinstance(item, sdk.ResultMessage) and not item.is_error for item in messages)
                    text = "".join(block.text for item in messages if isinstance(item, sdk.AssistantMessage)
                                   for block in item.content if isinstance(block, sdk.TextBlock))
                    assert text == "resumed answer"
                    wire = model.requests[-1].as_evidence()
                    assert wire["turn_text"] == prompt
                    assert "hello" in wire["prior_user_turns"]
                    assert "partial-0" in wire["prior_assistant_text"]
                    assert wire["prior_user_turns"].count("hello") == 1
                    ports = continuation.build_continuation_settlement_ports()
                    settlement = await ports.prepare(
                        request=request, workspace_lease=workspace, stop_result=SimpleNamespace(status="stopped"),
                        terminal_result=dict(status="completed", provider_session_final_sequence=store.accepted_final_sequence,
                            sdk_settlement=dict(input_accepted=True, ack_unknown=False, mirror_complete=True,
                                                received_result=True, terminal_reason="success")))
                    assert settlement["workspace_receipt"]["state"] == "saved"
                    result = ExecutorResult(status="succeeded", adapter_version="synthetic-host-v1",
                        executor_type="claude-agent-worker", executor_version="real-cli", capabilities={},
                        result={"message": text}, executor_payload=dict(
                            provider_session_final_sequence=store.accepted_final_sequence, continuation_settlement=settlement))
                    envelope = parse_leased_queue_envelope(raw)
                    authority = bind_worker_attempt_lifecycle(envelope.payload, leased_attempt_id=envelope.attempt_id,
                        worker_id="synthetic-worker", reconciliation=None,
                        ports=build_worker_attempt_lifecycle_ports(platform.attempts, platform.lifecycle))
                    projection = project_worker_terminal_result(result, [], trace_id=payload.trace_id, latency_ms=0,
                                                               invoked_skill_ids=exact_invoked_skills)

                    async def release_host_lease(conn, *, reason):
                        lease = await (await conn.execute("select id from sandbox_leases where run_id=%s", (payload.run_id,))).fetchone()
                        await sandbox_leases.release_sandbox_lease(conn, tenant_id=payload.tenant_id,
                            user_id=payload.user_id, run_id=payload.run_id, lease_id=lease["id"], reason=reason)

                    committed = await build_worker_result_commit_service(platform.transaction).commit(
                        WorkerResultCommitCommand(payload=envelope.payload, result=result,
                            result_payload=projection.result_payload, artifact_records=[], skill_snapshot=projection.skill_snapshot,
                            terminal_event_kwargs=projection.terminal_event_kwargs, attempt_id=payload.attempt_id,
                            trace_id=payload.trace_id), attempt_authority=authority,
                        capabilities=platform.capabilities, release_runtime_lease=release_host_lease)
                    assert committed.status == "succeeded"
                    async with platform.transaction() as conn:
                        row = await (await conn.execute("select r.status, a.status as attempt_status from runs r join run_attempts a on a.run_id=r.id where r.id=%s",
                                                       (payload.run_id,))).fetchone()
                        assert row == {"status": "succeeded", "attempt_status": "succeeded"}
                        events = await (await conn.execute("select event_type from run_events where run_id=%s", (payload.run_id,))).fetchall()
                        event_types = [event["event_type"] for event in events]
                        assert event_types.count("run_succeeded") == event_types.count("run.succeeded") == 1
                        continuation_view = await continuation.context_api.get_session_continuation(
                            conn, tenant_id=payload.tenant_id, user_id=payload.user_id, session_id=payload.session_id)
                        assert continuation_view["state"] == "resumable"
                        assert continuation_view["workspace_saved"] is True
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 15)
        listener.close()
