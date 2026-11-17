"""Exact durable terminal observation after the live executor has disappeared."""
# ruff: noqa: F401,F811 -- pytest fixture imported by name.
import pytest
from psycopg.types.json import Jsonb

from app.bootstrap.sandbox_lease_ports import build_sandbox_executor_reconciliation_port
from tests.test_provider_unclaimed_continuation_postgres import unclaimed_db, prepare
from tests.test_lifecycle_reconciliation_postgres import extend_lease

sandbox_leases = build_sandbox_executor_reconciliation_port()


@pytest.mark.asyncio
async def test_terminal_handoff_survives_release_but_rejects_foreign_stale_or_unowned_receipts(unclaimed_db):
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1)
        await extend_lease(conn)
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
            await conn.execute("""update run_attempt_settlements set settlement_phase=%s,
                settlement_claim_token=case when %s='claimed' then 'synthetic-claim' end,
                settlement_claimed_at=case when %s='claimed' then now() end,
                next_retry_at=case when %s='retry' then now() end""", (state, state, state, state))
            row = await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope)
            assert row["receipt_json"] == terminal
        await conn.execute("""update sandbox_leases set status='released', released_at=now(),
            executor_cleanup_status='finalized', executor_reconciled_at=now();
            update run_attempt_settlements set settlement_phase='settled',
              settlement_claim_token=null,settlement_claimed_at=null,next_retry_at=null;""")
        row = await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope)
        assert row["receipt_json"] == terminal
        for key in scope:
            foreign = {**scope, key: 3 if key == "owner_generation" else "foreign"}
            assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **foreign) is None, key
        # A released lease without finalized cleanup is not a responsibility handoff.
        await conn.execute("update run_attempt_settlements set settlement_phase='pending'")
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
        await conn.execute("""update sandbox_leases set status='active', released_at=null;
            update run_attempt_settlements set settlement_phase='awaiting_context';""")
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
        await conn.execute("update run_attempt_settlements set settlement_phase='pending', accepted_context_json=null")
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None
        await conn.execute("update run_attempt_settlements set accepted_context_json=%s",
            (Jsonb({"adapter_name": "claude-agent-worker"}),))
        await conn.execute("""update sandbox_leases set
            lease_payload_json=jsonb_set(lease_payload_json,'{attempt_id}','\"foreign\"')""")
        assert await sandbox_leases.get_sandbox_executor_terminal_handoff(conn, **scope) is None


@pytest.mark.asyncio
async def test_cancel_forward_lists_only_live_accepted_leases_of_cancel_requested_runs(unclaimed_db):
    async with unclaimed_db() as conn:
        await prepare(conn, "target", 1)
        await extend_lease(conn)
        lease = dict(tenant_id="tenant", run_id="target", attempt_id="attempt-target", lease_id="synthetic-lease")
        await sandbox_leases.record_sandbox_executor_accepted(
            conn, **lease, reconciliation_context={"adapter_name": "claude-agent-worker"})

        async def listed():
            rows = await sandbox_leases.list_cancel_requested_executor_leases(conn, limit=10)
            return [row["id"] for row in rows]

        # A live, heartbeating executor is not selected until its Run requests cancellation.
        await conn.execute("update sandbox_leases set executor_heartbeat_at=now()")
        assert await listed() == []
        await conn.execute("update runs set cancel_requested_at=now() where id='target'")
        assert await listed() == ["synthetic-lease"]
        # Once the executor terminal is recorded, settlement owns the lease.
        await sandbox_leases.record_sandbox_executor_terminal(
            conn, **lease, executor_status="cancelled",
            terminal_result=dict(status="cancelled", run_id="target", message="cancelled",
                                 error_code="executor_cancelled", error_message="Task cancelled"))
        assert await listed() == []
