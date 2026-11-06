from __future__ import annotations
import json
import pytest
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.runs.infrastructure import lifecycle_postgres
from app.runs.infrastructure import attempt_settlement_writer_postgres as writer
from tests.support.frozen_receipts import frozen_spec
from tests import test_sandbox_attempt_authority as authority_tests

attempt_settlement_db = authority_tests.sandbox_attempt_authority_db


async def _start_attempt_with_lease(conn, *, run_id: str, context_id: str):
    await conn.execute(
        """
        insert into runs(id, tenant_id, workspace_id, user_id, session_id, agent_id,
          execution_kind, status, model_id, model_value, model_gateway_revision,
          auto_compact_window, max_output_tokens)
        values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'agent-a',
          'harness_chat', 'queued', 'model-a', 'claude-test', 3, 200000, 4096)
        """,
        (run_id,),
    )
    await conn.execute(
        """
        insert into run_context_snapshots(id, tenant_id, workspace_id, user_id, session_id, run_id)
        values (%s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s)
        """,
        (context_id, run_id),
    )
    await conn.execute(
        "update runs set context_snapshot_id=%s, input_json=%s::jsonb where id=%s",
        (context_id, json.dumps({
            "executor_type": "claude-agent-worker",
            "context_snapshot_id": context_id,
            "context_snapshot": {"context_snapshot_id": context_id},
        }), run_id),
    )
    attempt = await build_run_attempt_lifecycle_service().start_worker(
        conn,
        tenant_id="tenant-a",
        run_id=run_id,
        queue_attempt_id=f"queue-{run_id}",
        worker_id=f"worker-{run_id}",
        execution_spec=frozen_spec(
            run_id=run_id,
            context_snapshot_id=context_id,
            context_snapshot={"context_snapshot_id": context_id},
        ),
    )
    initialized = await writer._current_owned_detail(
        conn, tenant_id="tenant-a", run_id=run_id, attempt_id=str(attempt["id"]),
    )
    assert initialized[3]["source_lease_id"] is None
    assert initialized[3]["attempt_owner_generation_snapshot"] == attempt["owner_generation"]
    lease_id = f"lease-{run_id}"
    await conn.execute(
        """
        insert into sandbox_leases(
          id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
          sandbox_mode, provider, status, lease_payload_json, executor_status
        ) values (
          %s, 'tenant-a', 'workspace-a', 'user-a', 'session-a', %s, %s,
          'ephemeral', 'fake', 'active', %s::jsonb, 'accepted'
        )
        """,
        (lease_id, run_id, attempt["id"], json.dumps({
            "owner_generation": attempt["owner_generation"],
        })),
    )
    await writer.initialize_attempt_settlement_detail(
        conn, tenant_id="tenant-a", run_id=run_id,
        attempt_id=str(attempt["id"]), source_lease_id=lease_id,
    )
    return attempt, lease_id


@pytest.mark.asyncio
async def test_active_writer_initializes_and_merges_receipts_without_generation_changes(
    attempt_settlement_db,
):
    connect, *_ = attempt_settlement_db
    async with connect() as conn, conn.transaction():
        attempt, lease_id = await _start_attempt_with_lease(
            conn, run_id="run-authority-late-accept", context_id="context-authority-late",
        )
        scope = {
            "tenant_id": "tenant-a",
            "run_id": "run-authority-late-accept",
            "attempt_id": str(attempt["id"]),
            "lease_id": lease_id,
        }
        receipt = {
            "run_id": scope["run_id"], "status": "succeeded", "answer": "persisted",
        }
        await writer.record_attempt_settlement_intent(
            conn, **scope, intent_context={"intent": "accepted-run"},
        )
        assert await writer.record_attempt_settlement_terminal_receipt(
            conn, **scope, receipt=receipt,
            receipt_owner_generation=int(attempt["owner_generation"]),
        ) is True
        before_acceptance = await writer._current_owned_detail(
            conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], source_lease_id=lease_id,
        )
        assert before_acceptance[3]["settlement_phase"] == "awaiting_context"
        accepted = {"adapter_name": "test", "acceptance": "late"}
        await writer.record_attempt_settlement_accepted_context(
            conn, **scope, accepted_context=accepted,
        )
        assert await writer.record_attempt_settlement_terminal_receipt(
            conn, **scope, receipt={**receipt, "diagnostics": ["mutable"]},
            receipt_owner_generation=int(attempt["owner_generation"]),
        ) is False
        with pytest.raises(RuntimeError, match="sandbox_executor_terminal_conflict"):
            await writer.record_attempt_settlement_terminal_receipt(
                conn, **scope, receipt={**receipt, "answer": "changed"},
                receipt_owner_generation=int(attempt["owner_generation"]),
            )
        detail = await writer._current_owned_detail(
            conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], source_lease_id=lease_id,
        )
        assert detail[3]["settlement_phase"] == "pending"
        assert detail[3]["accepted_context_json"] == accepted
        assert detail[3]["receipt_json"] == receipt

        claims = await writer.claim_due_attempt_settlements(
            conn, claim_token="authority-claim", limit=10, stale_after_seconds=30,
        )
        assert len(claims) == 1
        assert await writer.append_attempt_settlement_diagnostics(
            conn, **scope, claim_token="authority-claim", diagnostics=["safe"],
        ) is True
        before_retry_generation = await (await conn.execute(
            "select owner_generation from run_attempts where tenant_id=%s and run_id=%s and id=%s",
            (scope["tenant_id"], scope["run_id"], scope["attempt_id"]),
        )).fetchone()
        assert before_retry_generation["owner_generation"] == attempt["owner_generation"]
        assert await writer.retry_attempt_settlement_claim(
            conn, **scope, claim_token="authority-claim",
        ) is True
        after_retry_generation = await (await conn.execute(
            "select owner_generation from run_attempts where tenant_id=%s and run_id=%s and id=%s",
            (scope["tenant_id"], scope["run_id"], scope["attempt_id"]),
        )).fetchone()
        assert after_retry_generation["owner_generation"] == attempt["owner_generation"]

        assert await lifecycle_postgres.complete_run(
            conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"],
            result_json={"answer": "settled"}, observability=(None, 0, 0, 0, 0),
        ) is True
        await build_run_attempt_lifecycle_service().terminalize(
            conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], status="succeeded",
            terminal_reason="test-complete",
        )
        final = await writer._current_owned_detail(
            conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"],
            attempt_id=scope["attempt_id"], source_lease_id=lease_id,
        )
        assert final[0]["owner_generation"] == attempt["owner_generation"] + 1
        assert final[3]["attempt_owner_generation_snapshot"] == final[0]["owner_generation"]
        assert final[3]["settlement_phase"] == "settled"
        assert final[3]["receipt_json"] == {**receipt, "diagnostics": ["safe"]}
        assert final[3]["result_json"] == {"answer": "settled"}

        early_attempt, early_lease_id = await _start_attempt_with_lease(
            conn, run_id="run-authority-early-accept", context_id="context-authority-early",
        )
        early_scope = {
            "tenant_id": "tenant-a",
            "run_id": "run-authority-early-accept",
            "attempt_id": str(early_attempt["id"]),
            "lease_id": early_lease_id,
        }
        early_context = {"adapter_name": "test", "acceptance": "early"}
        early_receipt = {
            "run_id": early_scope["run_id"], "status": "succeeded", "answer": "early",
        }
        await writer.record_attempt_settlement_intent(
            conn, **early_scope, intent_context={"intent": "early-accepted-run"},
        )
        await writer.record_attempt_settlement_accepted_context(
            conn, **early_scope, accepted_context=early_context,
        )
        await writer.record_attempt_settlement_terminal_receipt(
            conn, **early_scope, receipt=early_receipt,
            receipt_owner_generation=int(early_attempt["owner_generation"]),
        )
        early_detail = await writer._current_owned_detail(
            conn, tenant_id=early_scope["tenant_id"], run_id=early_scope["run_id"],
            attempt_id=early_scope["attempt_id"], source_lease_id=early_lease_id,
        )
        assert early_detail[3]["settlement_phase"] == "pending"
        assert early_detail[3]["accepted_context_json"] == early_context
