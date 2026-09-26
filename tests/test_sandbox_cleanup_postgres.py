"""Real transaction/lock coverage for expired provider-resource cleanup."""
import app.sandbox.infrastructure.leases_postgres as _owner_sandbox_infrastructure_leases_postgres

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

from app.routes import sandbox_runtime_cleanup as cleanup
from app.runtime.sandbox.contracts import StopResult


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

    with pytest.raises(cleanup.SandboxRuntimeCleanupError):
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

    monkeypatch.setattr(_owner_sandbox_infrastructure_leases_postgres, "append_audit_log", fail_audit)

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
