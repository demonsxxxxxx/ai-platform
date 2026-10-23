"""Real transaction/lock coverage for expired provider-resource cleanup."""

from app.bootstrap.sandbox_cleanup import build_sandbox_runtime_cleanup
from app.sandbox.api import SandboxRuntimeCleanupError



import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.runtime.sandbox.contracts import StopResult

sandbox_runtime_cleanup = build_sandbox_runtime_cleanup()
cleanup = sandbox_runtime_cleanup


@pytest.fixture
async def cleanup_database():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"sandbox_cleanup_{uuid.uuid4().hex}"
    observer = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await observer.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        await observer.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await observer.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await observer.execute("insert into tenants(id, name) values ('tenant-a', 'A')")
        await observer.execute("insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')")
        await observer.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'A')")
        await observer.execute("insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'A', 'chat')")
        await observer.execute(
            "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) "
            "values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A')"
        )
        for ordinal in (1, 2):
            await observer.execute(
                "insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, status, execution_kind) "
                "values (%s, 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'running', 'harness_chat')",
                (f"run-{ordinal}",),
            )
            await observer.execute(
                """
                insert into sandbox_leases(
                    id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
                    sandbox_mode, provider, status, lease_payload_json,
                    runtime_container_id, runtime_container_name, runtime_executor_url,
                    runtime_workspace_container_path, runtime_handle_verified_at, expires_at
                ) values (
                    %s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s, %s,
                    'ephemeral', 'docker', 'active', %s::jsonb,
                    %s, %s, 'http://executor.test', '/workspace', now(),
                    now() - (%s * interval '1 minute')
                )
                """,
                (
                    f"lease-{ordinal}", f"run-{ordinal}", f"attempt-{ordinal}",
                    json.dumps({"attempt_id": f"attempt-{ordinal}"}),
                    f"container-{ordinal}", f"executor-{ordinal}", 3 - ordinal,
                ),
            )

        @asynccontextmanager
        async def transaction():
            async with await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, row_factory=dict_row,
                options=f"-c search_path={schema}",
            ) as conn:
                async with conn.transaction():
                    await conn.execute("set local lock_timeout = '500ms'")
                    yield conn

        yield observer, transaction
    finally:
        await observer.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await observer.close()


async def lease_states(observer):
    cursor = await observer.execute("select id, status from sandbox_leases order by id")
    return {row["id"]: row["status"] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_expiry_partial_failure_commits_locked_rows_and_audit_before_raising(cleanup_database):
    observer, transaction = cleanup_database
    stopped = []

    class Provider:
        async def stop(self, lease, *, reason):
            cursor = await observer.execute("select id from sandbox_leases for update skip locked")
            assert await cursor.fetchall() == []  # The cleanup owner retains both locks.
            stopped.append(lease.run_id)
            return StopResult(
                container_id=lease.container_id,
                status="stopped" if lease.run_id == "run-1" else "failed",
                message=reason,
            )

    with pytest.raises(SandboxRuntimeCleanupError):
        await cleanup.cleanup_expired_sandbox_runtime_leases(
            provider_factory=lambda _provider: Provider(), transaction_factory=transaction,
        )
    assert stopped == ["run-1", "run-2"]
    assert await lease_states(observer) == {"lease-1": "released", "lease-2": "active"}
    cursor = await observer.execute("select action, target_id from audit_logs")
    assert await cursor.fetchall() == [{"action": "sandbox.runtime.cleanup.failed", "target_id": "run-2"}]

    class RetryProvider:
        async def stop(self, lease, *, reason):
            assert lease.run_id == "run-2"
            return StopResult(container_id=lease.container_id, status="not_found", message=reason)

    released = await cleanup.cleanup_expired_sandbox_runtime_leases(
        provider_factory=lambda _provider: RetryProvider(), transaction_factory=transaction,
    )
    assert [row["id"] for row in released] == ["lease-2"]
    assert await lease_states(observer) == {"lease-1": "released", "lease-2": "released"}
    cursor = await observer.execute("select count(*) as n from run_events where event_type = 'sandbox_lease_released'")
    assert (await cursor.fetchone())["n"] == 2


@pytest.mark.asyncio
async def test_expiry_audit_failure_rolls_back_release_and_events(cleanup_database, monkeypatch):
    observer, transaction = cleanup_database

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit persistence unavailable")

    monkeypatch.setattr("app.bootstrap.sandbox_cleanup.audit_postgres.append_audit_log", fail_audit)

    class Provider:
        async def stop(self, lease, *, reason):
            return StopResult(
                container_id=lease.container_id,
                status="stopped" if lease.run_id == "run-1" else "failed", message=reason,
            )

    with pytest.raises(RuntimeError, match="audit persistence unavailable"):
        await cleanup.cleanup_expired_sandbox_runtime_leases(
            provider_factory=lambda _provider: Provider(), transaction_factory=transaction,
        )
    assert await lease_states(observer) == {"lease-1": "active", "lease-2": "active"}
    cursor = await observer.execute("select count(*) as n from run_events")
    assert (await cursor.fetchone())["n"] == 0
    cursor = await observer.execute("select count(*) as n from audit_logs")
    assert (await cursor.fetchone())["n"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_owner", [False, True])
async def test_expiry_concurrent_cleaner_skips_locked_batch_and_cancel_remains_retryable(cleanup_database, cancel_owner):
    observer, transaction = cleanup_database
    waiting = asyncio.Event()
    proceed = asyncio.Event()
    stopped = []

    class Provider:
        async def stop(self, lease, *, reason):
            stopped.append(lease.run_id)
            if lease.run_id == "run-2":
                waiting.set()
                await proceed.wait()
            return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    task = asyncio.create_task(cleanup.cleanup_expired_sandbox_runtime_leases(
        provider_factory=lambda _provider: Provider(), transaction_factory=transaction,
    ))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        assert await asyncio.wait_for(cleanup.cleanup_expired_sandbox_runtime_leases(
            provider_factory=lambda _provider: pytest.fail("a locked batch cannot be stopped twice"),
            transaction_factory=transaction,
        ), timeout=2) == []
        if cancel_owner:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await lease_states(observer) == {"lease-1": "active", "lease-2": "active"}
            proceed.set()
            await cleanup.cleanup_expired_sandbox_runtime_leases(
                provider_factory=lambda _provider: Provider(), transaction_factory=transaction,
            )
        else:
            proceed.set()
            await task
        assert await lease_states(observer) == {"lease-1": "released", "lease-2": "released"}
        assert stopped == ["run-1", "run-2"] * (2 if cancel_owner else 1)
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def cleanup_snapshot(observer):
    """Read committed state through the independent fixture connection."""
    result = {}
    for table in ("sandbox_leases", "run_events", "audit_logs", "run_event_cursors"):
        cursor = await observer.execute(sql.SQL("select * from {} order by 1, 2").format(sql.Identifier(table)))
        result[table] = await cursor.fetchall()
    return result


async def assert_cleanup_locks_released(transaction):
    async with transaction() as conn:
        cursor = await conn.execute("select id from sandbox_leases order by id for update nowait")
        assert len(await cursor.fetchall()) >= 2
        await conn.execute("select run_id from run_event_cursors for update nowait")


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_single_release_route_rolls_back_t1_before_t2_outcome(cleanup_database, monkeypatch, audit_fails):
    from app.auth import AuthPrincipal
    from app.models import SandboxLeaseReleaseRequest
    from app.routes import sandbox_leases as route
    from app.bootstrap import sandbox_cleanup as wiring
    from fastapi import HTTPException

    observer, transaction = cleanup_database
    before = await cleanup_snapshot(observer)
    connections = []
    provider_verified = []
    writes = []
    fault_reached = []
    real_event = wiring.run_events_postgres.append_event
    real_audit = wiring.audit_postgres.append_audit_log

    @asynccontextmanager
    async def tracked_transaction():
        async with transaction() as conn:
            if connections:
                assert connections[0].closed
                await assert_cleanup_locks_released(transaction)
                assert await cleanup_snapshot(observer) == before
            connections.append(conn)
            yield conn

    async def event(conn, **kwargs):
        writes.append(("event", conn))
        assert conn is connections[1]
        return await real_event(conn, **kwargs)

    async def audit(conn, **kwargs):
        writes.append(("audit", conn))
        assert conn is connections[1]
        # The T2 event/cursor are real but remain invisible to another connection.
        assert await cleanup_snapshot(observer) == before
        await real_audit(conn, **kwargs)
        if audit_fails:
            fault_reached.append(True)
            raise RuntimeError("after real audit insert")

    class Provider:
        async def stop(self, lease, *, reason):
            cursor = await observer.execute("select id from sandbox_leases where id = 'lease-1' for update skip locked")
            assert await cursor.fetchall() == []
            assert lease.run_id == "run-1"
            # A real T1 write must disappear before T2, not merely an unchanged lease.
            await connections[0].execute("update sandbox_leases set release_reason='t1-marker' where id='lease-1'")
            cursor = await connections[0].execute("select release_reason from sandbox_leases where id='lease-1'")
            assert (await cursor.fetchone())["release_reason"] == "t1-marker"
            provider_verified.append(True)
            return StopResult(container_id=lease.container_id, status="failed", message=reason)

    monkeypatch.setattr(route, "transaction", tracked_transaction)
    monkeypatch.setattr(route, "create_container_provider", lambda _provider: Provider())
    monkeypatch.setattr(wiring.run_events_postgres, "append_event", event)
    monkeypatch.setattr(wiring.audit_postgres, "append_audit_log", audit)
    with pytest.raises(HTTPException) as caught:
        await route.release_sandbox_lease(
            "run-1", "lease-1", SandboxLeaseReleaseRequest(reason="manual"),
            AuthPrincipal(user_id="user-a", display_name="A", tenant_id="tenant-a"),
        )
    assert caught.value.status_code == (503 if audit_fails else 502)
    assert caught.value.detail == ("sandbox_cleanup_audit_unavailable" if audit_fails else "sandbox_runtime_cleanup_failed")
    if audit_fails:
        assert fault_reached == [True]
        assert type(caught.value.__cause__) is RuntimeError
        assert str(caught.value.__cause__) == "after real audit insert"
    assert provider_verified == [True]
    assert len(connections) == 2 and connections[0] is not connections[1]
    assert all(conn.closed for conn in connections)
    assert writes == [("event", connections[1]), ("audit", connections[1])]
    after = await cleanup_snapshot(observer)
    if audit_fails:
        assert after == before
    else:
        assert after["sandbox_leases"] == before["sandbox_leases"]
        assert len(after["run_events"]) == len(after["audit_logs"]) == 1
        assert after["run_events"][0]["event_type"] == "sandbox_runtime_cleanup_failed"
        assert after["run_events"][0]["payload_json"]["visible_to_user"] is False
        assert after["audit_logs"][0]["tenant_id"] == "tenant-a"
        assert after["audit_logs"][0]["target_id"] == "run-1"
        assert after["run_event_cursors"][0]["next_sequence"] == 2
    await assert_cleanup_locks_released(transaction)


async def add_foreign_cleanup_scope(observer):
    """Add a second tenant with real FK-constrained Run and lease rows."""
    for statement in (
        "insert into tenants(id,name) values ('tenant-b','B')",
        "insert into workspaces(id,tenant_id,name) values ('workspace-b','tenant-b','B')",
        "insert into users(id,tenant_id,display_name) values ('user-b','tenant-b','B')",
        "insert into agents(id,tenant_id,name,agent_type) values ('agent-b','tenant-b','B','chat')",
        "insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title) values ('session-b','tenant-b','workspace-b','user-b','agent-b','B')",
        "insert into runs(id,tenant_id,workspace_id,session_id,user_id,agent_id,status,execution_kind) values ('run-b','tenant-b','workspace-b','session-b','user-b','agent-b','running','harness_chat')",
        """insert into sandbox_leases(id,tenant_id,workspace_id,user_id,session_id,run_id,attempt_id,
            sandbox_mode,provider,status,lease_payload_json,runtime_container_id,runtime_container_name,
            runtime_executor_url,runtime_workspace_container_path,runtime_handle_verified_at,expires_at)
            values ('lease-b','tenant-b','workspace-b','user-b','session-b','run-b','attempt-b',
            'ephemeral','docker','active','{}','container-b','executor-b','http://executor.test','/workspace',now(),now())""",
    ):
        await observer.execute(statement)


@pytest.mark.asyncio
async def test_expiry_later_tenant_outcome_failure_rolls_back_every_group(cleanup_database, monkeypatch):
    from app.bootstrap import sandbox_cleanup as wiring

    observer, transaction = cleanup_database
    await add_foreign_cleanup_scope(observer)
    before = await cleanup_snapshot(observer)
    real_event = wiring.run_events_postgres.append_event
    real_audit = wiring.audit_postgres.append_audit_log
    writes = []

    async def event(conn, **kwargs):
        writes.append(("event", conn, kwargs["tenant_id"], kwargs["run_id"]))
        return await real_event(conn, **kwargs)

    async def audit(conn, **kwargs):
        writes.append(("audit", conn, kwargs["tenant_id"], kwargs["target_id"]))
        await real_audit(conn, **kwargs)
        if kwargs["tenant_id"] == "tenant-b":
            # Prove the earlier group's event+audit and release exist inside T1.
            cursor = await conn.execute("select count(*) as n from audit_logs")
            assert (await cursor.fetchone())["n"] == 2
            cursor = await conn.execute("select status from sandbox_leases where id='lease-1'")
            assert (await cursor.fetchone())["status"] == "released"
            assert await cleanup_snapshot(observer) == before
            raise RuntimeError("later tenant audit failed")

    class Provider:
        async def stop(self, lease, *, reason):
            return StopResult(container_id=lease.container_id,
                              status="stopped" if lease.run_id == "run-1" else "failed", message=reason)

    monkeypatch.setattr(wiring.run_events_postgres, "append_event", event)
    monkeypatch.setattr(wiring.audit_postgres, "append_audit_log", audit)
    with pytest.raises(RuntimeError, match="later tenant audit failed"):
        await cleanup.cleanup_expired_sandbox_runtime_leases(provider_factory=lambda _: Provider(), transaction_factory=transaction)
    assert [(kind, tenant, run) for kind, _, tenant, run in writes] == [
        ("event", "tenant-a", "run-1"), ("event", "tenant-a", "run-2"),
        ("audit", "tenant-a", "run-2"), ("event", "tenant-b", "run-b"), ("audit", "tenant-b", "run-b"),
    ]
    assert all(conn is writes[0][1] for _, conn, _, _ in writes)
    assert writes[0][1].closed
    assert await cleanup_snapshot(observer) == before
    await assert_cleanup_locks_released(transaction)


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_owner_cancel_route_settlement_preserves_committed_cancel_and_scope(cleanup_database, monkeypatch, audit_fails):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.auth import AuthPrincipal
    from app.bootstrap import run_lifecycle, sandbox_cleanup as wiring
    from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
    from app.routes import runs as route

    observer, transaction = cleanup_database
    await add_foreign_cleanup_scope(observer)
    monkeypatch.setattr(run_lifecycle, "transaction", transaction)
    # Match the existing v4 PostgreSQL fixture: test-only authority material,
    # scoped to composition, with the real event writer and persistence intact.
    monkeypatch.setattr(run_lifecycle, "get_settings", lambda: SimpleNamespace(
        ai_session_secret="test-v4-authority-secret",
    ))
    use_case = run_lifecycle.build_run_cancellation_use_case(
        attempt_lifecycle=build_run_attempt_lifecycle_service(),
        lifecycle=run_lifecycle.build_run_lifecycle_service(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        run_stream_runtime=SimpleNamespace(), run_cancellation_use_case=use_case,
    )))
    before = await cleanup_snapshot(observer)
    real_event = wiring.run_events_postgres.append_event
    real_audit = wiring.audit_postgres.append_audit_log
    committed_cancel = []
    writes = []
    fault_reached = []

    class Provider:
        async def stop(self, lease, *, reason):
            assert lease.run_id == "run-1"
            assert reason == "cancel_requested"
            cursor = await observer.execute("select cancel_requested_at from runs where id='run-1'")
            assert (await cursor.fetchone())["cancel_requested_at"] is not None
            committed_cancel.append(await cleanup_snapshot(observer))
            # Provider stop is outside settlement and holds no lease lock.
            await assert_cleanup_locks_released(transaction)
            return StopResult(container_id=lease.container_id, status="stopped", message=reason)

    async def event(conn, **kwargs):
        writes.append(("event", conn, kwargs["event_type"]))
        return await real_event(conn, **kwargs)

    async def audit(conn, **kwargs):
        writes.append(("audit", conn, kwargs["action"]))
        cursor = await conn.execute("select status from sandbox_leases where id='lease-1'")
        assert (await cursor.fetchone())["status"] == "released"
        cursor = await conn.execute("select count(*) as n from run_events where event_type in ('sandbox_lease_released','sandbox_runtime_cleanup_succeeded')")
        assert (await cursor.fetchone())["n"] == 2
        await real_audit(conn, **kwargs)
        assert await cleanup_snapshot(observer) == committed_cancel[0]
        if audit_fails:
            fault_reached.append(True)
            raise RuntimeError("settlement audit failed after insert")

    monkeypatch.setattr(route, "transaction", transaction)
    monkeypatch.setattr(route, "create_container_provider", lambda _: Provider())
    monkeypatch.setattr(wiring.run_events_postgres, "append_event", event)
    monkeypatch.setattr(wiring.audit_postgres, "append_audit_log", audit)
    principal = AuthPrincipal(user_id="user-a", display_name="A", tenant_id="tenant-a")
    if audit_fails:
        with pytest.raises(HTTPException) as caught:
            await route.cancel_run("run-1", request, principal)
        assert caught.value.status_code == 503
        assert caught.value.detail == "sandbox_cleanup_persistence_unavailable"
        assert fault_reached == [True]
        assert type(caught.value.__cause__.__cause__) is RuntimeError
        assert str(caught.value.__cause__.__cause__) == "settlement audit failed after insert"
    else:
        result = await route.cancel_run("run-1", request, principal)
        assert result.run_id == "run-1"
    assert len(committed_cancel) == 1
    assert [(kind, name) for kind, _, name in writes] == [
        ("event", "sandbox_lease_released"), ("event", "sandbox_runtime_cleanup_succeeded"),
        ("audit", "sandbox.runtime.cleanup.succeeded"),
    ]
    assert all(conn is writes[0][1] for _, conn, _ in writes)
    assert writes[0][1].closed
    after = await cleanup_snapshot(observer)
    if audit_fails:
        assert after == committed_cancel[0]
    else:
        assert await lease_states(observer) == {"lease-1": "released", "lease-2": "active", "lease-b": "active"}
        outcome = [row for row in after["run_events"] if row["event_type"] == "sandbox_runtime_cleanup_succeeded"]
        assert len(outcome) == 1 and outcome[0]["payload_json"]["visible_to_user"] is False
        assert outcome[0]["tenant_id"] == "tenant-a" and outcome[0]["run_id"] == "run-1"
    for lease_id in ("lease-2", "lease-b"):
        assert next(row for row in after["sandbox_leases"] if row["id"] == lease_id) == next(row for row in before["sandbox_leases"] if row["id"] == lease_id)
    cursor = await observer.execute("select cancel_requested_at from runs where id='run-1'")
    assert (await cursor.fetchone())["cancel_requested_at"] is not None
    assert any(row["action"] == "run.cancel" for row in after["audit_logs"])
    await assert_cleanup_locks_released(transaction)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["audit_await", "before_commit"])
async def test_cancel_settlement_task_cancellation_rolls_back_and_retries(cleanup_database, monkeypatch, cancel_at):
    from app.bootstrap import sandbox_cleanup as wiring

    observer, transaction = cleanup_database
    cursor = await observer.execute("select * from sandbox_leases where id='lease-1'")
    rows = await cursor.fetchall()
    before = await cleanup_snapshot(observer)
    waiting = asyncio.Event()
    proceed = asyncio.Event()
    connections = []
    real_audit = wiring.audit_postgres.append_audit_log

    @asynccontextmanager
    async def boundary_transaction():
        async with transaction() as conn:
            connections.append(conn)
            yield conn
            if cancel_at == "before_commit":
                waiting.set()
                await proceed.wait()  # Known pre-COMMIT boundary, not lost acknowledgement.

    async def audit(conn, **kwargs):
        await real_audit(conn, **kwargs)
        cursor = await conn.execute("select status from sandbox_leases where id='lease-1'")
        assert (await cursor.fetchone())["status"] == "released"
        cursor = await conn.execute("select count(*) as n from run_events")
        assert (await cursor.fetchone())["n"] == 2
        if cancel_at == "audit_await":
            waiting.set()
            await proceed.wait()

    stopped = []

    class Provider:
        async def stop(self, lease, *, reason):
            stopped.append(lease.run_id)
            return StopResult(container_id=lease.container_id, status="stopped" if len(stopped) == 1 else "not_found", message=reason)

    kwargs = dict(tenant_id="tenant-a", run_id="run-1", user_id="user-a", requested_by_role="owner",
                  reason="cancel_requested", trace_id=None, provider_factory=lambda _: Provider())
    monkeypatch.setattr(wiring.audit_postgres, "append_audit_log", audit)
    task = asyncio.create_task(cleanup.cleanup_cancelled_run_leases(rows, **kwargs, transaction_factory=boundary_transaction))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        assert await cleanup_snapshot(observer) == before
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert connections and all(conn.closed for conn in connections)
        assert await cleanup_snapshot(observer) == before
        await assert_cleanup_locks_released(transaction)
        monkeypatch.setattr(wiring.audit_postgres, "append_audit_log", real_audit)
        await cleanup.cleanup_cancelled_run_leases(rows, **kwargs, transaction_factory=transaction)
        assert stopped == ["run-1", "run-1"]
        assert await lease_states(observer) == {"lease-1": "released", "lease-2": "active"}
        after = await cleanup_snapshot(observer)
        assert len(after["run_events"]) == 2 and len(after["audit_logs"]) == 1
        assert after["run_event_cursors"][0]["next_sequence"] == 3
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
