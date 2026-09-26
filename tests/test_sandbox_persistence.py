import asyncio
from datetime import datetime, timedelta, timezone
import uuid
import psycopg
from psycopg import sql as psycopg_sql
from psycopg.rows import dict_row
import pytest
import app.streaming.infrastructure.run_events_postgres as _repo_owner_app_streaming_infrastructure_run_events_postgres
from app.routes import sandbox_runtime_cleanup
from app.platform.postgres.sandbox_leases import SandboxExecutorTerminalConflictError, SandboxLeaseReleaseScopeMismatchError, create_sandbox_lease, fence_sandbox_lease_release, list_expired_active_sandbox_leases, record_opensandbox_renewal_receipt, record_sandbox_executor_heartbeat, record_sandbox_executor_terminal
from app.sandbox.infrastructure.leases_postgres import renew_sandbox_lease
from tests.support.repository_fixtures import RecordingConnection, SingleRowConnection, SingleRowCursor, _run_control_postgres_dsn


@pytest.mark.asyncio
async def test_create_and_renew_sandbox_lease_persists_ttl_contract():
    conn = RecordingConnection()

    await create_sandbox_lease(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        sandbox_mode="ephemeral",
        provider="fake",
        browser_enabled=False,
        ttl_seconds=600,
        resource_limits_json={"max_seconds": 60},
        user_visible_payload_json={"workspace": "/workspace"},
        lease_payload_json={"purpose": "test"},
        lease_id="lease-fixed",
    )
    await renew_sandbox_lease(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        lease_id="lease-a",
        ttl_seconds=900,
    )

    create_sql, create_params = conn.calls[0]
    renew_sql, renew_params = conn.calls[1]
    assert "sandbox_leases" in create_sql
    assert create_params[0] == "lease-fixed"
    assert "now() + (%s * interval '1 second')" in create_sql
    assert 600 in create_params
    assert "status = 'active'" in renew_sql
    assert "(expires_at is null or expires_at > now())" in renew_sql
    assert renew_params == (900, "tenant-a", "user-a", "run-a", "lease-a")


@pytest.mark.asyncio
async def test_sandbox_executor_heartbeat_renews_only_unexpired_attempt_lease():
    class NoMatchConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return SingleRowCursor(None)

    conn = NoMatchConnection()

    recorded = await record_sandbox_executor_heartbeat(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        executor_status="running",
        ttl_seconds=731,
    )

    heartbeat_sql, heartbeat_params = conn.calls[0]
    assert recorded is None
    assert "expires_at = now() + make_interval(secs => %s)" in heartbeat_sql
    assert "(expires_at is null or expires_at > now())" in heartbeat_sql
    assert "executor_terminal_json is null" in heartbeat_sql
    assert heartbeat_params == (
        "running",
        "running",
        731,
        "lease-a",
        "tenant-a",
        "run-a",
        "attempt-a",
    )


@pytest.mark.asyncio
async def test_opensandbox_renewal_receipt_is_provider_and_attempt_fenced():
    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return SingleRowCursor(None)

    conn = RecordingConnection()
    expiration = datetime.now(timezone.utc) + timedelta(minutes=30)
    assert await record_opensandbox_renewal_receipt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        provider_expires_at=expiration,
    ) is None

    sql, params = conn.calls[0]
    assert "provider_renewed_at = now()" in sql
    assert "provider_expires_at = %s" in sql
    assert "provider = 'opensandbox'" in sql
    assert "status = 'active'" in sql
    assert "attempt_id = %s" in sql
    assert "(expires_at is null or expires_at > now())" in sql
    assert "executor_terminal_json is null" in sql
    assert params == (expiration, "lease-a", "tenant-a", "run-a", "attempt-a")
    with pytest.raises(ValueError, match="timezone_invalid"):
        await record_opensandbox_renewal_receipt(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            lease_id="lease-a",
            provider_expires_at=datetime.now(),
        )
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_sandbox_executor_terminal_receipt_is_attempt_fenced_and_idempotent():
    class TerminalConnection:
        def __init__(self):
            self.current = {
                "id": "lease-a",
                "executor_status": None,
                "executor_terminal_json": None,
                "executor_terminal_received_at": None,
            }
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("select * from sandbox_leases"):
                return SingleRowCursor(dict(self.current))
            if normalized.startswith("update sandbox_leases set executor_status"):
                self.current = {
                    **self.current,
                    "executor_status": params[0],
                    "executor_terminal_json": {"status": "completed", "run_id": "run-a"},
                    "executor_terminal_received_at": datetime.now(timezone.utc),
                }
                return SingleRowCursor(dict(self.current))
            raise AssertionError(normalized)

    conn = TerminalConnection()
    terminal = {"status": "completed", "run_id": "run-a"}

    recorded = await record_sandbox_executor_terminal(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        executor_status="completed",
        terminal_result=terminal,
    )
    duplicate = await record_sandbox_executor_terminal(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        executor_status="completed",
        terminal_result=terminal,
    )
    assert recorded["executor_terminal_json"] == terminal
    assert duplicate["executor_terminal_json"] == terminal
    update_calls = [call for call in conn.calls if call[0].startswith("update sandbox_leases")]
    assert len(update_calls) == 1
    assert "attempt_id = %s" in update_calls[0][0]
    assert "status = 'active'" in update_calls[0][0]

    with pytest.raises(
        SandboxExecutorTerminalConflictError,
        match="sandbox_executor_terminal_conflict",
    ):
        await record_sandbox_executor_terminal(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            lease_id="lease-a",
            executor_status="failed",
            terminal_result={"status": "failed", "run_id": "run-a"},
        )


@pytest.mark.asyncio
async def test_sandbox_lease_release_fence_is_durable_before_or_after_insert():
    row = {
        "id": "lease-a",
        "tenant_id": "tenant-a",
        "status": "released",
    }
    conn = SingleRowConnection(row)

    result = await fence_sandbox_lease_release(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        sandbox_mode="ephemeral",
        provider="docker",
        browser_enabled=False,
        reason="lease_record_failed",
    )

    assert result == row
    assert "insert into sandbox_leases" in conn.sql
    assert "on conflict (id) do update" in conn.sql
    assert "set status = 'released'" in conn.sql
    assert "sandbox_leases.executor_terminal_json is null" in conn.sql
    assert "sandbox_leases.executor_reconciliation_status = 'finalized'" in conn.sql
    assert "coalesce(sandbox_leases.attempt_id, '') = coalesce(excluded.attempt_id, '')" in conn.sql
    assert conn.params == (
        "lease-a",
        "tenant-a",
        "workspace-a",
        "user-a",
        "session-a",
        "run-a",
        "attempt-a",
        "ephemeral",
        "docker",
        False,
        "lease_record_failed",
    )


@pytest.mark.asyncio
async def test_sandbox_lease_release_fence_rejects_id_scope_collision():
    with pytest.raises(
        SandboxLeaseReleaseScopeMismatchError,
        match="sandbox_lease_release_scope_mismatch",
    ):
        await fence_sandbox_lease_release(
            SingleRowConnection(None),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            attempt_id="attempt-a",
            lease_id="lease-collision",
            sandbox_mode="ephemeral",
            provider="docker",
            browser_enabled=False,
            reason="lease_record_failed",
        )


@pytest.mark.asyncio
async def test_sandbox_lease_release_fence_serializes_with_postgres_insert():
    dsn = _run_control_postgres_dsn()
    schema_name = f"sandbox_lease_fence_{uuid.uuid4().hex}"
    observer = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    creator: psycopg.AsyncConnection | None = None
    releaser: psycopg.AsyncConnection | None = None
    fence_task: asyncio.Task | None = None
    try:
        await observer.execute(
            psycopg_sql.SQL("create schema {}").format(
                psycopg_sql.Identifier(schema_name)
            )
        )
        await observer.execute(
            psycopg_sql.SQL(
                """
                create table {}.sandbox_leases (
                  id text primary key,
                  tenant_id text not null,
                  workspace_id text not null,
                  user_id text not null,
                  session_id text not null,
                  run_id text not null,
                  attempt_id text,
                  trace_id text not null default '',
                  sandbox_mode text not null,
                  provider text not null,
                  status text not null default 'active',
                  browser_enabled boolean not null default false,
                  resource_limits_json jsonb not null default '{{}}'::jsonb,
                  user_visible_payload_json jsonb not null default '{{}}'::jsonb,
                  lease_payload_json jsonb not null default '{{}}'::jsonb,
                  runtime_container_id text,
                  runtime_container_name text,
                  runtime_executor_url text,
                  runtime_workspace_container_path text,
                  runtime_handle_verified_at timestamptz,
                  executor_terminal_json jsonb,
                  executor_reconciliation_status text not null default 'waiting_terminal',
                  heartbeat_at timestamptz,
                  expires_at timestamptz,
                  released_at timestamptz,
                  release_reason text not null default '',
                  created_at timestamptz not null default now(),
                  updated_at timestamptz not null default now()
                )
                """
            ).format(psycopg_sql.Identifier(schema_name))
        )
        creator = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        releaser = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        for connection in (creator, releaser):
            await connection.execute(
                psycopg_sql.SQL("set search_path to {}").format(
                    psycopg_sql.Identifier(schema_name)
                )
            )
            await connection.commit()
        creator_pid = int(
            (await (await creator.execute("select pg_backend_pid() as pid")).fetchone())["pid"]
        )
        releaser_pid = int(
            (await (await releaser.execute("select pg_backend_pid() as pid")).fetchone())["pid"]
        )
        await creator.commit()
        await releaser.commit()

        await create_sandbox_lease(
            creator,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            attempt_id="attempt-a",
            trace_id="trace-a",
            sandbox_mode="ephemeral",
            provider="fake",
            browser_enabled=False,
            ttl_seconds=600,
            resource_limits_json={},
            user_visible_payload_json={"workspace": "/workspace"},
            lease_payload_json={"attempt_id": "attempt-a"},
            lease_id="lease-race-a",
        )
        fence_task = asyncio.create_task(
            fence_sandbox_lease_release(
                releaser,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id="session-a",
                run_id="run-a",
                attempt_id="attempt-a",
                lease_id="lease-race-a",
                sandbox_mode="ephemeral",
                provider="fake",
                browser_enabled=False,
                reason="lease_record_failed",
            )
        )
        blocking_deadline = asyncio.get_running_loop().time() + 10
        while True:
            cursor = await observer.execute(
                "select %s = any(pg_blocking_pids(%s)) as blocked",
                (creator_pid, releaser_pid),
            )
            if (await cursor.fetchone())["blocked"]:
                break
            if asyncio.get_running_loop().time() >= blocking_deadline:
                raise AssertionError(
                    "release fence did not block behind the active insert"
                )
            await asyncio.sleep(0.02)

        await creator.commit()
        fenced = await asyncio.wait_for(fence_task, timeout=5)
        await releaser.commit()
        assert fenced["status"] == "released"
        cursor = await observer.execute(
            psycopg_sql.SQL(
                "select status, release_reason from {}.sandbox_leases where id = %s"
            ).format(psycopg_sql.Identifier(schema_name)),
            ("lease-race-a",),
        )
        assert await cursor.fetchone() == {
            "status": "released",
            "release_reason": "lease_record_failed",
        }
    finally:
        if fence_task is not None and not fence_task.done():
            fence_task.cancel()
        if creator is not None:
            await creator.close()
        if releaser is not None:
            await releaser.close()
        await observer.execute(
            psycopg_sql.SQL("drop schema if exists {} cascade").format(
                psycopg_sql.Identifier(schema_name)
            )
        )
        await observer.close()


@pytest.mark.asyncio
async def test_real_sandbox_lease_requires_attempt_and_complete_runtime_handle():
    conn = RecordingConnection()
    common = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "sandbox_mode": "ephemeral",
        "provider": "docker",
        "browser_enabled": False,
        "ttl_seconds": 600,
        "resource_limits_json": {},
        "user_visible_payload_json": {"workspace": "/workspace"},
        "lease_payload_json": {"attempt_id": "attempt-a"},
    }

    with pytest.raises(ValueError, match="sandbox_runtime_handle_required"):
        await create_sandbox_lease(conn, **common)
    assert conn.calls == []

    await create_sandbox_lease(
        conn,
        **common,
        attempt_id="attempt-a",
        runtime_container_id="container-a",
        runtime_container_name="executor-container-a",
        runtime_executor_url="http://executor.test",
        runtime_workspace_container_path="/workspace",
    )

    create_sql, create_params = conn.calls[0]
    assert "run_id, attempt_id, trace_id" in create_sql
    assert "attempt-a" in create_params


@pytest.mark.asyncio
async def test_fake_sandbox_lease_rejects_disagreeing_attempt_binding():
    conn = RecordingConnection()

    with pytest.raises(ValueError, match="sandbox_lease_attempt_binding_mismatch"):
        await create_sandbox_lease(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            attempt_id="attempt-a",
            trace_id="trace-a",
            sandbox_mode="ephemeral",
            provider="fake",
            browser_enabled=False,
            ttl_seconds=600,
            resource_limits_json={},
            user_visible_payload_json={"workspace": "/workspace"},
            lease_payload_json={"attempt_id": "attempt-old"},
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_sandbox_lease_insert_requires_returned_persisted_row():
    conn = SingleRowConnection(None)

    with pytest.raises(RuntimeError, match="sandbox_lease_insert_returning_missing"):
        await create_sandbox_lease(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            trace_id="trace-a",
            sandbox_mode="ephemeral",
            provider="fake",
            browser_enabled=False,
            ttl_seconds=600,
            resource_limits_json={},
            user_visible_payload_json={"workspace": "/workspace"},
            lease_payload_json={},
        )


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_leases_releases_expired_non_runtime_leases_and_emits_events(monkeypatch):
    calls = []

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-expired",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-lease",
                    "release_reason": "expired",
                }
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return ExpiredLeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr(_repo_owner_app_streaming_infrastructure_run_events_postgres, "append_event", fake_append_event)

    cleaned = await sandbox_runtime_cleanup.cleanup_expired_sandbox_leases(
        FakeConnection(),
        tenant_id="tenant-a",
        reason="expired",
    )

    assert [item["id"] for item in cleaned] == ["lease-expired"]
    update_sql, update_params = calls[0]
    assert "status = 'released'" in update_sql
    assert "status = 'active'" in update_sql
    assert "expires_at <= now()" in update_sql
    assert "provider not in ('fake', 'docker', 'opensandbox')" in update_sql
    assert update_params == ("expired", "tenant-a", "tenant-a")
    assert calls[1] == (
        "event",
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "trace_id": "trace-lease",
            "event_type": "sandbox_lease_released",
            "stage": "sandbox",
            "message": "已释放过期 Sandbox 租约",
            "payload": {
                "visible_to_user": True,
                "lease_id": "lease-expired",
                "reason": "expired",
            },
        },
    )


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_leases_global_scope_emits_events_for_each_tenant(monkeypatch):
    calls = []

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-a",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                    "release_reason": "expired",
                },
                {
                    "id": "lease-b",
                    "tenant_id": "tenant-b",
                    "run_id": "run-b",
                    "trace_id": "trace-b",
                    "release_reason": "expired",
                },
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return ExpiredLeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['tenant_id']}"

    monkeypatch.setattr(_repo_owner_app_streaming_infrastructure_run_events_postgres, "append_event", fake_append_event)

    cleaned = await sandbox_runtime_cleanup.cleanup_expired_sandbox_leases(FakeConnection())

    assert [item["id"] for item in cleaned] == ["lease-a", "lease-b"]
    update_sql, update_params = calls[0]
    assert "where (%s::text is null or tenant_id = %s)" in update_sql
    assert update_params == ("expired", None, None)
    assert [call[1]["tenant_id"] for call in calls[1:]] == ["tenant-a", "tenant-b"]
    assert [call[1]["payload"]["lease_id"] for call in calls[1:]] == ["lease-a", "lease-b"]


@pytest.mark.asyncio
async def test_generic_sandbox_release_sql_fences_nonfinalized_terminal_receipts():
    from app.platform.postgres import sandbox_leases as sandbox_lease_repository

    statements = []

    class Cursor:
        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

    class Connection:
        async def execute(self, sql, params):
            statements.append((" ".join(sql.split()).lower(), params))
            return Cursor()

    conn = Connection()
    await sandbox_lease_repository.release_sandbox_lease(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        lease_id="lease-a",
        reason="released",
    )
    await sandbox_lease_repository.release_active_sandbox_leases_for_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        reason="terminal",
    )
    await sandbox_lease_repository.release_stopped_sandbox_leases(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        reason="cancelled",
        lease_ids=["lease-a"],
    )
    await sandbox_lease_repository.cleanup_expired_sandbox_leases(
        conn,
        tenant_id="tenant-a",
        reason="expired",
    )

    assert len(statements) == 4
    for statement, _params in statements:
        assert "executor_terminal_json is null" in statement
        assert "executor_reconciliation_status = 'finalized'" in statement


@pytest.mark.asyncio
async def test_list_expired_active_sandbox_leases_preserves_runtime_stop_targets():
    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-docker",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "provider": "docker",
                    "status": "active",
                }
            ]

    class FakeConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            return ExpiredLeaseCursor()

    conn = FakeConnection()

    rows = await list_expired_active_sandbox_leases(
        conn,
        tenant_id="tenant-a",
        limit=25,
    )

    assert [row["id"] for row in rows] == ["lease-docker"]
    select_sql, select_params = conn.calls[0]
    assert select_sql.startswith("select * from sandbox_leases")
    assert "status = 'active'" in select_sql
    assert "expires_at <= now()" in select_sql
    assert "executor_terminal_json is null" in select_sql
    assert "executor_reconciliation_status = 'finalized'" in select_sql
    assert "for update skip locked" in select_sql
    assert "provider not in" not in select_sql
    assert select_params == ("tenant-a", "tenant-a", 25)


@pytest.mark.asyncio
async def test_release_stopped_sandbox_leases_releases_by_stopped_ids_and_emits_expired_events(monkeypatch):
    calls = []

    class LeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-a",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                }
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return LeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr(_repo_owner_app_streaming_infrastructure_run_events_postgres, "append_event", fake_append_event)

    released = await sandbox_runtime_cleanup.release_stopped_sandbox_leases(
        FakeConnection(),
        tenant_id="tenant-a",
        reason="expired",
        lease_ids=["lease-a"],
    )

    assert [lease["id"] for lease in released] == ["lease-a"]
    update_sql, update_params = calls[0]
    assert "id = any(%s)" in update_sql
    assert "run_id = %s" in update_sql
    assert "executor_terminal_json is null" in update_sql
    assert "executor_reconciliation_status = 'finalized'" in update_sql
    assert update_params == ("expired", "tenant-a", None, None, ["lease-a"])
    assert calls[1] == (
        "event",
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "trace_id": "trace-a",
            "event_type": "sandbox_lease_released",
            "stage": "sandbox",
            "message": "已释放过期 Sandbox 租约",
            "payload": {
                "visible_to_user": True,
                "lease_id": "lease-a",
                "reason": "expired",
            },
        },
    )
