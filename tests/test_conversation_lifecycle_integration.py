"""Actual composition seams: private files, terminal CAS and HTTP cancel ownership."""
# ruff: noqa: F401,F811 -- Imported pytest fixtures are injected by name.
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import conversation_continuation as composition
from app.bootstrap.context import configure_context_services
from app.context.infrastructure import provider_epochs
from app.context.infrastructure.provider_continuation import get_session_continuation
from tests.test_provider_unclaimed_continuation_postgres import (
    unclaimed_db, prepare, callback,
)
from tests.test_conversation_recovery_postgres import lifecycle_db
from tests.test_run_control_routes import (
    _install_route_cancellation_adapter, _install_test_run_stream_runtime,
)
from tests.test_provider_session_recovery_postgres import recovery_db  # noqa: F401
from tests.test_workspace_versions import (
    request, stop, put, save_workspace_version, stage_current_skill,
)
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_active_cancel_http_keeps_original_executor_as_settlement_owner(
    monkeypatch, role, _install_route_cancellation_adapter, _install_test_run_stream_runtime,
):
    from tests import test_run_control_routes as existing
    async def cancel(conn, **kwargs):
        return {"run_id": kwargs["run_id"], "status": "cancel_requested",
                "active_sandbox_leases": [existing.sandbox_lease_row(run_id=kwargs["run_id"], user_id="user-a")]}

    async def unexpected_cleanup(*args, **kwargs):
        raise AssertionError("active cancel must drain before stop/save/release")

    route = "app.routes.admin_runs" if role == "admin" else "app.routes.runs"
    monkeypatch.setattr("app.auth.get_settings", existing.auth_settings)
    monkeypatch.setattr(route + ".transaction", existing.fake_transaction)
    monkeypatch.setattr(existing.route_cancellation_handlers, role, cancel)
    monkeypatch.setattr(existing.sandbox_runtime_cleanup, "cleanup_cancelled_run_leases", unexpected_cleanup)
    response = TestClient(existing.create_app()).post(
        "/api/ai/" + ("admin/" if role == "admin" else "") + "runs/run_active/cancel",
        headers=existing.admin_headers() if role == "admin" else existing.headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"


@pytest.mark.asyncio
async def test_unclaimed_terminal_uses_real_composition_current_generation_and_atomic_release(unclaimed_db):
    configure_context_services()
    from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
    attempts = build_run_attempt_lifecycle_service()
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1)
        await conn.execute("update runs set cancel_requested_at=now() where id='target'")
        await conn.execute("update run_attempts set status='cancel_requested',owner_generation=3 where id='attempt-target'")
        settlement = dict(tenant_id="tenant", run_id="target", attempt_id="attempt-target", final_sequence=None,
                          workspace_receipt=None, stop_evidence="provider_stopped",
                          sdk_settlement=dict(input_accepted=False, ack_unknown=False, mirror_complete=False),
                          external_effect_unknown=False)
        async with conn.transaction(force_rollback=True):
            result = await composition._settle_stopped_continuation(conn, **settlement)
            assert result["settlement_kind"] == "unclaimed_unaccepted"
            await conn.execute("update runs set status='cancelled' where id='target'")
            terminal = await attempts.terminalize(conn, tenant_id="tenant", run_id="target",
                attempt_id="attempt-target", status="cancelled", terminal_reason="run_cancelled")
            assert terminal["owner_generation"] == 4
            await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="target")
        row = await (await conn.execute("select active_run_id from provider_session_heads")).fetchone()
        assert row["active_run_id"] == "target"
        result = await composition._settle_stopped_continuation(conn, **settlement)
        assert result["owner_generation"] == 3
        await conn.execute("update runs set status='cancelled' where id='target'")
        terminal = await attempts.terminalize(conn, tenant_id="tenant", run_id="target",
            attempt_id="attempt-target", status="cancelled", terminal_reason="run_cancelled")
        assert terminal["owner_generation"] == 4
        await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="target")
        await conn.execute("alter table sandbox_leases add column executor_terminal_json jsonb")
        assert await get_session_continuation(conn, tenant_id="tenant", user_id="user", session_id="session") is None
        assert (await prepare(conn, "next", 2))["execution_mode"] == "empty_start"


@pytest.mark.asyncio
async def test_hard_fault_composition_reconciles_real_host_prefix_not_sandbox_completion(lifecycle_db):
    configure_context_services()
    async with lifecycle_db() as conn:
        result = await composition._settle_stopped_continuation(
            conn, tenant_id="tenant", run_id="run", attempt_id="attempt", final_sequence=None,
            workspace_receipt={"state": "unsaved", "reason_code": "workspace_unsaved"},
            stop_evidence="provider_not_found", sdk_settlement={"mirror_complete": False},
            external_effect_unknown=True,
        )
        assert result["reason_code"] == "workspace_unsaved"
        assert result["input_accepted"] is True
        assert result["final_sequence"] == 2
        assert result["mirror_complete"] is False


@pytest.mark.asyncio
async def test_status_distinguishes_running_supplement_from_actual_settlement(unclaimed_db):
    async with unclaimed_db() as conn:
        await conn.execute("alter table sandbox_leases add column executor_terminal_json jsonb")
        frozen = await prepare(conn, "target", 1)
        # Ordinary active input remains usable, even without a committed recovery head yet.
        assert await get_session_continuation(conn, tenant_id="tenant", user_id="user", session_id="session") is None
        await callback(conn, "target", frozen, "load")
        await conn.execute("update sandbox_leases set executor_terminal_json='{}'::jsonb")
        view = await get_session_continuation(conn, tenant_id="tenant", user_id="user", session_id="session")
        assert view == dict(state="settling", action="wait", reason_code="settling", workspace_saved=False)


@pytest.mark.asyncio
async def test_actual_host_recovery_callback_blocks_ambiguous_confirmation_without_public_object_metadata(unclaimed_db, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.bootstrap.recovery_operations import build_recovery_operations
    from app.routes import runtime_callbacks
    from app.runtime.sandbox.callback_tokens import derive_callback_token

    configure_context_services()
    secret = "synthetic-operation-callback-secret"
    identity, invocation_id = "mcp__vendor__write", "native-pending-write"
    monkeypatch.setattr(runtime_callbacks, "transaction", unclaimed_db)
    monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: SimpleNamespace(sandbox_callback_token=secret))
    async with unclaimed_db() as conn:
        await conn.execute("""
            alter table sandbox_leases add column id text;
            alter table sandbox_leases add column created_at timestamptz default now();
            alter table sandbox_leases add column executor_terminal_json jsonb;
            alter table run_attempts add constraint uq_test_attempt_id unique(id);
        """)
        shared = Path("app/schema.sql").read_text()
        start = shared.index("create table if not exists run_input_sessions")
        await conn.execute(shared[start:shared.index("create or replace function", start)])
        await conn.execute(Path("app/execution/infrastructure/recovery_operations_schema.sql").read_text())
        frozen = await prepare(conn, "source", 1)
        subject = dict(identity=identity, registered=True, declared=True, active=True, distributed=True)
        await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{input}',%s)",
                           (Jsonb({"_runtime_tool_policy_subjects": [subject]}),))
        await conn.execute("update sandbox_leases set id='lease-source',lease_payload_json=%s",
                           (Jsonb(dict(attempt_id="attempt-source", owner_generation=2, callback_token_id="cbt:source:attempt-source:g2")),))
    app = FastAPI()
    app.include_router(runtime_callbacks.router, prefix="/api/ai")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic") as client:
        async def post(name, operation):
            token_id = f"cbt:{name}:attempt-{name}:g2"
            return await client.post("/api/ai/runtime/callbacks/recovery-operation",
                headers={"X-AI-Platform-Callback-Token": derive_callback_token(secret, token_id)},
                json=dict(run_id=name, attempt_id="attempt-" + name, callback_token_id=token_id,
                          invocation_id=invocation_id, tool_identity=identity, operation=operation))
        started = await post("source", "started")
        assert started.status_code == 200, started.text
        async with unclaimed_db() as conn:
            await callback(conn, "source", frozen, "load")
            await callback(conn, "source", frozen, "append", [{"type": "assistant", "uuid": "native-assistant",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": invocation_id,
                "name": identity, "input": {}}]}}], 1)
            saved = dict(schema_version="ai-platform.workspace-version.v1", state="saved", version_id="synthetic-empty",
                         scope=dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                                    run_id="source", attempt_id="attempt-source"), file_count=0, total_bytes=0,
                         source_stop_evidence="provider_stopped")
            await composition._settle_stopped_continuation(conn, tenant_id="tenant", run_id="source",
                attempt_id="attempt-source", final_sequence=1, workspace_receipt=saved, stop_evidence="provider_stopped",
                sdk_settlement=dict(input_accepted=True, ack_unknown=False, mirror_complete=False), external_effect_unknown=True)
            await conn.execute("update runs set status='failed' where id='source'")
            await provider_epochs.release_provider_lineage(conn, tenant_id="tenant", run_id="source")
            await prepare(conn, "target", 2)
            await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{input}',%s) where id='attempt-target'",
                               (Jsonb({"_runtime_tool_policy_subjects": [subject]}),))
            await conn.execute("update sandbox_leases set id='lease-target',lease_payload_json=%s where run_id='target'",
                               (Jsonb(dict(attempt_id="attempt-target", owner_generation=2, callback_token_id="cbt:target:attempt-target:g2")),))
            await conn.execute("insert into run_input_sessions(tenant_id,run_id,attempt_id) values('tenant','target','attempt-target')")
        waiting = await post("target", "query")
        assert waiting.status_code == 200, waiting.text
        assert waiting.json()["decision"] == "deny"
        assert waiting.json()["reason_code"] == "recovery_operation_public_object_metadata_missing"
        assert "question_id" not in waiting.json()
        async with unclaimed_db() as conn:
            questions = await (await conn.execute("select count(*) as n from run_input_questions")).fetchone()
            assert questions["n"] == 0
        rejected = await post("target", "started")
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "recovery_operation_public_object_metadata_missing"



@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="real no-follow/flock/fsync on Linux")
async def test_bootstrap_restores_files_then_actual_current_skill_then_saved_outputs(tmp_path, monkeypatch):
    source = request()
    manager = SandboxWorkspaceManager(root=tmp_path)
    source_lease = manager.prepare(source)
    put(Path(source_lease.workspace_host_path), "draft.txt", "SAVED DRAFT")
    put(Path(source_lease.workspace_host_path), ".claude/skills/editor/output/draft.txt", "SAVED OUTPUT")
    saved = save_workspace_version(request=source, workspace_lease=source_lease,
                                   stop_evidence=stop(source), workspace_root=tmp_path)
    target = request(run_id="run-b", attempt_id="attempt-b")
    payload = SimpleNamespace(**{key: getattr(target, key) for key in (
        "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id", "owner_generation",
    )}, input={})
    pack = {"conversation_context": {"provider_epoch_id": "frozen-epoch", "recovery": {
        "state": "resumable", "workspace_receipt": saved,
    }}}
    binding = await composition.restore_worker_workspace(
        payload=payload, context_pack=pack, tool_policy_subjects=target.tool_policy_subjects,
        workspace_root=tmp_path,
    )
    assert binding[2]["state"] == "awaiting_skill_staging"
    workspace = Path(binding[1].workspace_host_path)
    assert (workspace / "draft.txt").read_text() == "SAVED DRAFT"
    assert not (workspace / ".claude/skills/editor/output").exists()
    staged = stage_current_skill(tmp_path, workspace)
    await composition.finish_worker_workspace_restore(binding, context_pack=pack, staged_names=staged,
                                                     workspace_root=tmp_path)
    assert (workspace / ".claude/skills/editor/SKILL.md").read_text() == "CURRENT SKILL CODE"
    assert (workspace / ".claude/skills/editor/output/draft.txt").read_text() == "SAVED OUTPUT"
    assert (workspace / "CLAUDE.md").is_file()
