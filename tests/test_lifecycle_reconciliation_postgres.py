"""Real lease checkpoints and authenticated cancelling-operation completion tails."""
# ruff: noqa: F401,F811 -- pytest fixtures are injected by name.
from __future__ import annotations

from contextlib import asynccontextmanager
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from app.bootstrap import conversation_continuation as composition
from app.execution.api import ContinuationSettlementPorts
from app.execution.application.executor_reconciliation import sandbox_reconciliation_payload
from app.executors.base import ExecutorResult, RunPayload
from app.platform.postgres import sandbox_leases
from app.routes import runtime_callbacks
from app.runtime.sandbox.callback_tokens import derive_callback_token
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from tests.test_provider_unclaimed_continuation_postgres import unclaimed_db, prepare
from tests.test_sandbox_persistence import executor_acceptance_database
from tests.test_workspace_versions import request, put


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_snapshot", [False, True])
async def test_actual_lease_terminal_claim_records_generation_bound_stage(
    executor_acceptance_database, monkeypatch, legacy_snapshot,
):
    from app import executor_reconciler as reconciler

    conn = executor_acceptance_database
    payload = RunPayload(
        tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a",
        session_id="session-a", run_id="run-a", attempt_id="attempt-a",
        agent_id="agent-a", skill_id=None, file_ids=[], input={}, owner_generation=4,
        execution_kind="harness_chat", schema_version="ai-platform.run-payload.v2",
    )
    await conn.execute("update runs set status='queued' where id='run-a'")
    spec = ('{"schema_version":"ai-platform.execution-spec.v2","tenant_id":"tenant-a",'
            '"workspace_id":"workspace-a","user_id":"user-a","session_id":"session-a",'
            '"run_id":"run-a","agent_id":"agent-a","execution_kind":"harness_chat"}')
    await conn.execute("""
        insert into run_attempts(id,tenant_id,run_id,ordinal,status,owner_kind,owner_id,
          owner_generation,queue_attempt_id,execution_spec_schema_version,execution_spec_json,
          execution_spec_canonical_json,execution_spec_sha256)
        values('attempt-a','tenant-a','run-a',1,'created','queue_worker','worker-a',1,
          'queue-attempt-a','ai-platform.execution-spec.v2',%s::jsonb,%s,
          encode(sha256(convert_to(%s,'UTF8')),'hex'))
    """, (spec, spec, spec))
    await conn.execute("update run_attempts set status='queued',owner_generation=2,queue_message_id='queue-message-a' where id='attempt-a'")
    await conn.execute("update run_attempts set status='claimed',owner_generation=3 where id='attempt-a'")
    await conn.execute("update run_attempts set status='running',owner_generation=4,started_at=now() where id='attempt-a'")
    await sandbox_leases.create_sandbox_lease(
        conn, tenant_id=payload.tenant_id, workspace_id=payload.workspace_id,
        user_id=payload.user_id, session_id=payload.session_id, run_id=payload.run_id,
        attempt_id=payload.attempt_id, trace_id="trace-a", sandbox_mode="ephemeral",
        provider="opensandbox", browser_enabled=False, ttl_seconds=1800,
        resource_limits_json={}, user_visible_payload_json={},
        lease_payload_json={
            "source": "sandbox_runtime", "evidence_class": "runtime_lease_projection",
            "attempt_id": payload.attempt_id, "owner_generation": payload.owner_generation,
            "callback_token_id": "cbt:run-a:attempt-a:g4", "container_id": "container-a",
            "container_name": "executor-a", "executor_url": "http://synthetic.invalid",
            "workspace_container_path": "/workspace", "labels": {},
        },
        runtime_container_id="container-a", runtime_container_name="executor-a",
        runtime_executor_url="http://synthetic.invalid", runtime_workspace_container_path="/workspace",
        lease_id="lease-runtime-a",
    )
    snapshot = sandbox_reconciliation_payload(payload)
    if legacy_snapshot:
        snapshot["execution_payload"].pop("owner_generation", None)
    scope = dict(tenant_id=payload.tenant_id, run_id=payload.run_id,
                 attempt_id=payload.attempt_id, lease_id="lease-runtime-a")
    await sandbox_leases.record_sandbox_executor_accepted(
        conn, **scope, reconciliation_context={
            "adapter_name": "claude-agent-worker", "adapter_context": {}, "run_payload": snapshot,
        },
    )
    terminal = dict(run_id=payload.run_id, status="failed", error_code="synthetic_api_error",
                    sdk_settlement=dict(owner_generation=4, input_accepted=True,
                                        mirror_complete=True, result_received=True,
                                        external_effect_unknown=False))
    async with conn.transaction():
        await sandbox_leases.record_sandbox_executor_terminal(
            conn, **scope, executor_status="failed", terminal_result=terminal,
        )
    async with conn.transaction():
        claimed = await sandbox_leases.claim_sandbox_executor_reconciliations(
            conn, claim_token="claim-runtime-a", limit=10, stale_after_seconds=60,
        )
    assert len(claimed) == 1
    row = claimed[0]
    assert row["executor_reconciliation_status"] == "claimed"
    assert row["status"] == "active" and row["expires_at"] > row["created_at"]
    assert "run_id" not in row["lease_payload_json"]
    _, restored = reconciler._context_payload(row, terminal)
    req = reconciler._reconciliation_request(row, restored)
    assert req.owner_generation == payload.owner_generation
    stage = dict(
        schema_version="ai-platform.terminal-reconciliation.v1",
        scope={key: getattr(req, key) for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )}, owner_generation=req.owner_generation, container_id="container-a",
        artifact_workspace=None, terminal_result=terminal,
    )

    @asynccontextmanager
    async def transaction():
        async with conn.transaction():
            yield conn

    monkeypatch.setattr(reconciler, "transaction", transaction)
    await reconciler._record_reconciliation_stage(row, claim_token="claim-runtime-a", stage=stage)
    for defect in ("claim", "generation", "container", "tenant", "run", "attempt"):
        invalid = {**stage, "scope": dict(stage["scope"])}
        claim = "stale-claim" if defect == "claim" else "claim-runtime-a"
        if defect == "generation":
            invalid["owner_generation"] += 1
        elif defect == "container":
            invalid["container_id"] = "other-container"
        elif defect in ("tenant", "run", "attempt"):
            invalid["scope"][defect + "_id"] = "other"
        with pytest.raises(RuntimeError, match="executor_reconciliation_claim_lost"):
            await reconciler._record_reconciliation_stage(row, claim_token=claim, stage=invalid)
    persisted = await (await conn.execute(
        "select lease_payload_json->'terminal_reconciliation' as stage from sandbox_leases where id=%s",
        (row["id"],),
    )).fetchone()
    assert persisted["stage"] == stage
    assert row["lease_payload_json"]["terminal_reconciliation"] == stage


async def extend_lease(conn):
    # prepare() inserts the minimal owning fixture before its lease table is extended.
    await conn.execute("""
        alter table sandbox_leases add column id text;
        alter table sandbox_leases add column workspace_id text default 'workspace';
        alter table sandbox_leases add column user_id text default 'user';
        alter table sandbox_leases add column session_id text default 'session';
        alter table sandbox_leases add column runtime_container_id text default 'synthetic-container';
        alter table sandbox_leases add column created_at timestamptz default now();
        alter table sandbox_leases add column updated_at timestamptz default now();
        alter table sandbox_leases add column executor_reconciliation_status text default 'claimed';
        alter table sandbox_leases add column executor_reconciliation_claim_token text default 'synthetic-claim';
        update sandbox_leases set id='synthetic-lease', lease_payload_json=jsonb_build_object(
            'attempt_id', attempt_id, 'owner_generation', 2,
            'callback_token_id', 'cbt:target:attempt-target:g2');
    """)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux private workspace no-follow/flock/fsync")
@pytest.mark.parametrize("failure", ["conversion", "terminal_database"])
async def test_stopped_retry_reuses_durable_pg_snapshot_and_linux_workspace(unclaimed_db, monkeypatch, tmp_path, failure):
    from app import executor_reconciler as reconciler

    req = request(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                  run_id="target", attempt_id="attempt-target", owner_generation=2)
    manager = SandboxWorkspaceManager(root=tmp_path)
    workspace = manager.prepare(req)
    put(Path(workspace.workspace_host_path), "draft.txt", "PRIVATE DRAFT")
    snapshot = Path(workspace.workspace_host_path).parent / ".ai-platform-artifact-snapshot-v1"
    snapshot.mkdir()
    put(snapshot, "report.txt", "COMPLETE AUTHORIZED DELIVERY")
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1)
        await extend_lease(conn)

    calls = []
    lease = SimpleNamespace(container_id="synthetic-container", provider="opensandbox")
    lease.model_copy = lambda **kwargs: lease

    class Provider:
        async def collect_workspace(self, *args):
            assert "stop" not in calls, "a stopped retry cannot require the old live sandbox"
            calls.append("collect")
            return snapshot

        async def stop(self, *args, **kwargs):
            calls.append("stop")
            return SimpleNamespace(status="stopped", container_id=lease.container_id)

    async def save(**kwargs):
        calls.append("save")
        from app.sandbox.infrastructure.workspace_versions import save_workspace_version
        evidence = dict(state=kwargs["stop_evidence"], scope={key: getattr(req, key) for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )}, owner_generation=req.owner_generation)
        return await composition._workspace_io(save_workspace_version, request=req,
            workspace_lease=workspace, stop_evidence=evidence, workspace_root=tmp_path)

    def convert(**kwargs):
        calls.append("convert")
        assert kwargs["terminal_result"]["status"] == "completed"
        assert (Path(kwargs["adapter_context"]["_artifact_workspace"]) / "report.txt").read_text() == "COMPLETE AUTHORIZED DELIVERY"
        if failure == "conversion" and calls.count("convert") == 1:
            raise OSError("synthetic conversion failure after stop")
        return ExecutorResult(status="succeeded", adapter_version="1", executor_type="claude_agent_sdk",
            executor_version="1", capabilities={}, result={"message": "complete"})

    terminal = dict(status="completed", response_files=["report.txt"], sdk_settlement={"mirror_complete": True})
    context = dict(adapter_name="claude-agent-worker", adapter_context={})
    monkeypatch.setattr(reconciler, "transaction", unclaimed_db)
    monkeypatch.setattr(reconciler, "_context_and_payload", lambda _: (context, terminal, SimpleNamespace(attempt_id=req.attempt_id)))
    monkeypatch.setattr(reconciler, "_reconciliation_request", lambda *args: req)
    monkeypatch.setattr(reconciler, "SandboxWorkspaceManager", lambda: manager)
    monkeypatch.setattr(reconciler._sandbox_cleanup, "container_lease_from_persisted_row", lambda _: lease)
    monkeypatch.setattr(reconciler, "_container_provider_for_lease", lambda _: Provider())

    async def unused(*args, **kwargs):
        raise AssertionError("conversion cannot publish a Context terminal")

    async def row():
        async with unclaimed_db() as conn:
            return await (await conn.execute("select * from sandbox_leases where id='synthetic-lease'")).fetchone()

    async def collect():
        return await reconciler._collect_workspace_and_convert_result(await row(),
            registry=SimpleNamespace(get=lambda _: SimpleNamespace(reconcile_sandbox_terminal=convert)),
            claim_token="synthetic-claim", continuation_ports=ContinuationSettlementPorts(save, unused))

    if failure == "conversion":
        with pytest.raises(OSError, match="conversion failure"):
            await collect()
    else:
        first, _, _ = await collect()
        assert first.status == "succeeded"
        with pytest.raises(RuntimeError, match="rollback terminal"):
            async with unclaimed_db() as conn:
                await conn.execute("update runs set status='succeeded' where id='target'")
                raise RuntimeError("rollback terminal")
    persisted = await row()
    stage = persisted["lease_payload_json"]["terminal_reconciliation"]
    assert stage["continuation"]["workspace_receipt"]["state"] == "saved"
    assert stage["terminal_result"]["status"] == "completed"
    async with unclaimed_db() as conn:
        assert (await (await conn.execute("select status from runs where id='target'")).fetchone())["status"] == "running"
        for defect in ("claim", "generation", "container", "tenant"):
            candidate = {**stage, "scope": dict(stage["scope"])}
            claim = "other" if defect == "claim" else "synthetic-claim"
            if defect == "generation":
                candidate["owner_generation"] += 1
            if defect == "container":
                candidate["container_id"] = "other-container"
            if defect == "tenant":
                candidate["scope"]["tenant_id"] = "other-tenant"
            assert not await sandbox_leases.record_sandbox_reconciliation_stage(conn,
                lease_id="synthetic-lease", claim_token=claim, stage=candidate)
    result, _, _ = await collect()
    assert result.status == "succeeded"
    assert result.executor_payload["continuation_settlement"]["workspace_receipt"] == stage["continuation"]["workspace_receipt"]
    assert calls == ["collect", "stop", "save", "convert", "convert"]
    async with unclaimed_db() as conn:
        await conn.execute("update sandbox_leases set lease_payload_json=jsonb_set(lease_payload_json,'{terminal_reconciliation,owner_generation}','3'::jsonb)")
    with pytest.raises(RuntimeError, match="stage_scope_invalid"):
        await collect()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "generation", "expired", "released", "takeover", "terminal", "native"])
async def test_authenticated_completion_tail_uses_real_pg_started_operation_and_lease(unclaimed_db, monkeypatch, defect):
    secret = "synthetic-completion-tail-secret"
    identity = "mcp__synthetic__write"
    monkeypatch.setattr(runtime_callbacks, "transaction", unclaimed_db)
    monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: SimpleNamespace(sandbox_callback_token=secret))
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1)
        await extend_lease(conn)
        await conn.execute("alter table run_attempts add constraint uq_tail_attempt_id unique(id)")
        shared = Path("app/schema.sql").read_text()
        start = shared.index("create table if not exists run_input_sessions")
        await conn.execute(shared[start:shared.index("create or replace function", start)])
        await conn.execute(Path("app/execution/infrastructure/recovery_operations_schema.sql").read_text())
        subject = dict(identity=identity, registered=True, declared=True, active=True, distributed=True, write_capable=True)
        await conn.execute("update run_attempts set execution_spec_json=jsonb_set(execution_spec_json,'{input}',%s)",
                           (Jsonb({"_runtime_tool_policy_subjects": [subject]}),))
    app = FastAPI()
    app.include_router(runtime_callbacks.router, prefix="/api/ai")
    token_id = "cbt:target:attempt-target:g2"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic") as client:
        async def post(operation, native="native-tool-use"):
            return await client.post("/api/ai/runtime/callbacks/recovery-operation",
                headers={"X-AI-Platform-Callback-Token": derive_callback_token(secret, token_id)},
                json=dict(run_id="target", attempt_id="attempt-target", callback_token_id=token_id,
                          invocation_id=native, tool_identity=identity, operation=operation))
        started = await post("started")
        assert started.status_code == 200, started.text
        async with unclaimed_db() as conn:
            await conn.execute("update runs set cancel_requested_at=now()")
            await conn.execute("update run_attempts set status='cancel_requested', owner_generation=3")
            if defect == "generation":
                await conn.execute("update run_attempts set owner_generation=4")
            elif defect == "expired":
                await conn.execute("update sandbox_leases set expires_at=now()-interval '1 second'")
            elif defect == "released":
                await conn.execute("update sandbox_leases set status='released',released_at=now()")
            elif defect == "takeover":
                await conn.execute("update run_attempts set owner_kind='reconciler'")
            elif defect == "terminal":
                await conn.execute("update runs set status='cancelled'")
        response = await post("completed", "unstarted-native" if defect == "native" else "native-tool-use")
        if defect is None:
            assert response.status_code == 200, response.text
            assert (await post("completed")).status_code == 200
            assert (await post("failed")).status_code == 409
            for operation in ("started", "query"):
                assert (await post(operation)).status_code == 409
        else:
            assert response.status_code == 409, response.text
        async with unclaimed_db() as conn:
            operation = await (await conn.execute("select reported_status,verified_outcome from execution_recovery_operations")).fetchone()
            assert operation["reported_status"] == ("completed" if defect is None else None)
            assert operation["verified_outcome"] == "unknown"
