"""Real CLI -> authenticated production HTTP callback -> PostgreSQL -> new epoch."""
# ruff: noqa: F811 -- Imported pytest fixture is injected by name.
from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from app.bootstrap.context import configure_context_services
from app.bootstrap import conversation_continuation as composition
from app.context.infrastructure import provider_epochs
from app.execution.infrastructure.harness.claude.session_store import ClaudeSessionStoreAdapter
from app.routes import runtime_callbacks
from app.runtime.sandbox.callback_tokens import derive_callback_token
from tests.test_provider_unclaimed_continuation_postgres import unclaimed_db, prepare  # noqa: F401
from tests.support.lifecycle_cli_harness import build_round_options, _blocking_server
from tests.support.lifecycle_model_peer import Turn, synthetic_model_peer


@pytest.mark.asyncio
async def test_real_cli_interrupt_resume_uses_authenticated_callback_and_fresh_pg_epoch(unclaimed_db, tmp_path, monkeypatch):
    import claude_agent_sdk as sdk
    import httpx
    import uvicorn

    configure_context_services()
    secret = "synthetic-lifecycle-callback-secret"
    monkeypatch.setattr(runtime_callbacks, "transaction", unclaimed_db)
    monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: SimpleNamespace(sandbox_callback_token=secret))
    async with unclaimed_db() as conn:
        await conn.execute("""
            alter table sandbox_leases add column id text;
            alter table sandbox_leases add column workspace_id text default 'workspace';
            alter table sandbox_leases add column user_id text default 'user';
            alter table sandbox_leases add column session_id text default 'session';
            alter table sandbox_leases add column created_at timestamptz default now();
            alter table sandbox_leases add column executor_terminal_json jsonb;
        """)
        frozen = await prepare(conn, "first", 1)
        await conn.execute("update sandbox_leases set id='lease-first',lease_payload_json=%s where run_id='first'",
                           (Jsonb(dict(attempt_id="attempt-first", owner_generation=2,
                                       callback_token_id="cbt:first:attempt-first:g2")),))
    app = FastAPI()
    app.include_router(runtime_callbacks.router, prefix="/api/ai")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(20)
    listener.setblocking(False)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/api/ai/runtime/callbacks/provider-session"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    serving.result()
                await asyncio.sleep(0.01)
        with synthetic_model_peer([Turn.text(*(f"partial-{i} " for i in range(100)), delay=0.05),
                                   Turn.text("resumed answer", delay=0.0)]) as model:
            for turn, name in enumerate(("first", "second"), 1):
                root = tmp_path / name
                (root / "work").mkdir(parents=True)
                (root / "config").mkdir()
                token_id = f"cbt:{name}:attempt-{name}:g2"
                store = ClaudeSessionStoreAdapter(
                    callback_url=url, callback_token=derive_callback_token(secret, token_id),
                    callback_token_id=token_id, run_id=name, attempt_id="attempt-" + name,
                    provider_session_id=frozen["provider_session_id"], provider_epoch_id=frozen["provider_epoch_id"],
                )
                await store.load(frozen["provider_session_id"])
                options = build_round_options(
                    root=root, model_url=model.url, store=store, session_id=frozen["provider_session_id"],
                    resume=turn == 2, mcp_server=_blocking_server(asyncio.Event(), [], asyncio.Event()),
                )
                client = sdk.ClaudeSDKClient(options)
                interrupted = False
                messages = []
                try:
                    await client.connect("first question" if turn == 1 else "continue after stop")
                    async with asyncio.timeout(90):
                        async for message in client.receive_messages():
                            messages.append(message)
                            if turn == 1 and isinstance(message, sdk.StreamEvent) and not interrupted:
                                if message.event.get("type") == "content_block_delta":
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
                    async with unclaimed_db() as conn:
                        await conn.execute("update runs set cancel_requested_at=now() where id='first'")
                        await conn.execute("update run_attempts set owner_generation=3,status='cancel_requested' where id='attempt-first'")
                        receipt = dict(schema_version="ai-platform.workspace-version.v1", state="saved", version_id="synthetic-empty-workspace",
                                       scope=dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                                                  run_id="first", attempt_id="attempt-first"), file_count=0, total_bytes=0,
                                       source_stop_evidence="provider_stopped")
                        result = await composition._settle_stopped_continuation(
                            conn, tenant_id="tenant", run_id="first", attempt_id="attempt-first",
                            final_sequence=store.accepted_final_sequence, workspace_receipt=receipt,
                            stop_evidence="provider_stopped", sdk_settlement=dict(input_accepted=True, ack_unknown=False,
                            mirror_complete=True), external_effect_unknown=False,
                        )
                        assert result["state"] == "resumable"
                        await conn.execute("update runs set status='cancelled' where id='first'")
                        await conn.execute("update run_attempts set status='cancelled' where id='attempt-first'")
                        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="first")
                        old_epoch = frozen["provider_epoch_id"]
                        native_id = frozen["provider_session_id"]
                        frozen = await prepare(conn, "second", 2)
                        assert frozen["provider_session_id"] == native_id and frozen["provider_epoch_id"] != old_epoch
                        await conn.execute("update sandbox_leases set id='lease-second',lease_payload_json=%s where run_id='second'",
                                           (Jsonb(dict(attempt_id="attempt-second", owner_generation=2,
                                                       callback_token_id="cbt:second:attempt-second:g2")),))
                else:
                    assert any(isinstance(item, sdk.ResultMessage) and not item.is_error for item in messages)
                    text = "".join(block.text for item in messages if isinstance(item, sdk.AssistantMessage)
                                   for block in item.content if isinstance(block, sdk.TextBlock))
                    assert text == "resumed answer"
                    evidence = model.requests[-1].as_evidence()
                    assert "first question" in evidence["prior_user_turns"]
                    assert "partial-0" in evidence["prior_assistant_text"]
                    assert evidence["turn_text"] == "continue after stop"
                    async with httpx.AsyncClient() as client_http:
                        rejected = await client_http.post(url, headers={"X-AI-Platform-Callback-Token": "wrong-token"},
                                                          json=store._payload(action="load", key=native_id))
                    assert rejected.status_code == 401
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 15)
        listener.close()
