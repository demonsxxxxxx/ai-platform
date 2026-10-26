"""Exact durable terminal observation after the live executor has disappeared."""
# ruff: noqa: F401,F811 -- pytest fixture imported by name.
import pytest
from psycopg.types.json import Jsonb

from app.platform.postgres import sandbox_leases
from tests.test_provider_unclaimed_continuation_postgres import unclaimed_db, prepare
from tests.test_lifecycle_reconciliation_postgres import extend_lease


@pytest.mark.asyncio
async def test_terminal_handoff_survives_release_but_rejects_foreign_stale_or_unowned_receipts(unclaimed_db):
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1)
        await extend_lease(conn)
        await conn.execute("""
            alter table sandbox_leases add column executor_status text;
            alter table sandbox_leases add column executor_terminal_json jsonb;
            alter table sandbox_leases add column executor_terminal_received_at timestamptz;
            alter table sandbox_leases add column executor_reconciliation_context_json jsonb;
            alter table sandbox_leases add column executor_reconciled_at timestamptz;
            alter table sandbox_leases add column executor_heartbeat_at timestamptz;
            alter table sandbox_leases add column heartbeat_at timestamptz;
        """)
        scope = dict(tenant_id="tenant", workspace_id="workspace", user_id="user", session_id="session",
                     run_id="target", attempt_id="attempt-target", lease_id="synthetic-lease", owner_generation=2)
        terminal = dict(status="cancelled", run_id="target", message="cancelled",
                        error_code="executor_cancelled", error_message="Task cancelled")
        await sandbox_leases.record_sandbox_executor_accepted(conn,
            **{key: scope[key] for key in ("tenant_id", "run_id", "attempt_id", "lease_id")},
            reconciliation_context={"adapter_name": "claude-agent-worker"})
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
        await sandbox_leases.record_sandbox_executor_terminal(conn,
            **{key: scope[key] for key in ("tenant_id", "run_id", "attempt_id", "lease_id")},
            executor_status="cancelled", terminal_result=terminal)
        for state in ("pending", "claimed", "retry", "failed"):
            await conn.execute("update sandbox_leases set executor_reconciliation_status=%s", (state,))
            row = await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope)
            assert row["executor_terminal_json"] == terminal
        await conn.execute("""update sandbox_leases set status='released', released_at=now(),
            executor_reconciliation_status='finalized', executor_reconciled_at=now()""")
        row = await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope)
        assert row["executor_terminal_json"] == terminal
        for key in scope:
            foreign = {**scope, key: 3 if key == "owner_generation" else "foreign"}
            assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **foreign) is None, key
        # A released lease without finalized cleanup is not a responsibility handoff.
        await conn.execute("update sandbox_leases set executor_reconciliation_status='pending'")
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
        await conn.execute("""update sandbox_leases set status='active', released_at=null,
            executor_reconciliation_status='waiting_terminal'""")
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
        await conn.execute("update sandbox_leases set executor_reconciliation_status='pending', executor_reconciliation_context_json=null")
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
        await conn.execute("""update sandbox_leases set executor_reconciliation_context_json=%s,
            lease_payload_json=jsonb_set(lease_payload_json,'{attempt_id}','\"foreign\"')""",
            (Jsonb({"adapter_name": "claude-agent-worker"}),))
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
