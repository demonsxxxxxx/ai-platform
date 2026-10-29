"""PostgreSQL regression for transferring a frozen terminal checkpoint to a new claim."""

# ruff: noqa: F401,F811 -- pytest fixture imported by name.
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from app.bootstrap.attempt_settlement_projection import build_attempt_settlement_projection_service
from app.bootstrap.context import configure_context_services
from app.bootstrap.conversation_continuation import build_continuation_settlement_ports
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.bootstrap.run_lifecycle import build_run_lifecycle_service
from app.context.api import provider_session_verification_scope
from app.context.domain.conversation_recovery import make_provider_coverage_authority_receipt
from app.context.domain.provider_sessions import ProviderSessionScope
from app.context.infrastructure import provider_epochs
from app.execution.application.executor_reconciliation import sandbox_reconciliation_payload
from app.executors.base import project_execution_spec_to_run_payload
from app.runs.api import fail_run_terminalization
from app.sandbox.application.attempt_settlement_shadow import AttemptSettlementShadowLeaseRepository
from app.sandbox.api import finalize_stopped_executor_checkpoint
from app.sandbox.infrastructure import leases_postgres
from tests.test_sandbox_persistence import executor_acceptance_database  # noqa: F401
from tests.test_run_attempt_repository import _execution_spec


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lease_status", "retirement_state", "marker_present"),
    [
        ("active", "expanded", True),
        ("released", "retired", False),
    ],
)
async def test_reclaimed_checkpoint_commits_context_run_and_lease_atomically(
    executor_acceptance_database,
    lease_status,
    retirement_state,
    marker_present,
):
    conn = executor_acceptance_database
    await conn.commit()
    await conn.execute(Path("app/context/infrastructure/conversation_recovery_schema.sql").read_text())
    async with conn.transaction():
        await conn.execute("delete from sandbox_leases where id = 'lease-a'")
        await conn.execute(
            "insert into model_gateway_revisions "
            "(revision, base_url, api_key_ciphertext, key_fingerprint, active, created_by) "
            "values (1, 'https://synthetic.invalid', decode('00','hex'), "
            "'0000000000000000', true, 'user-a')"
        )
        await conn.execute(
            "update runs set status = 'queued', session_generation = 1, "
            "model_id = 'model-a', model_value = 'model-a', model_gateway_revision = 1, "
            "auto_compact_window = 100000, max_output_tokens = 8192, "
            "input_json = '{\"executor_type\":\"claude-agent-worker\"}' where id = 'run-a'"
        )

        scope_map = {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "agent-a",
        }
        scope = ProviderSessionScope(**scope_map)
        configure_context_services()
        await provider_epochs.claim_provider_lineage(conn, scope=scope, run_id="run-a")
        coverage = await provider_epochs.read_provider_coverage(
            conn, scope=scope, run_id="run-a", session_generation=1,
        )
        authority = make_provider_coverage_authority_receipt(
            coverage=coverage,
            scope=scope_map,
            through_session_generation=1,
            current_message_id="user-message",
        )
        await conn.execute(
            "insert into messages(id, tenant_id, session_id, run_id, role, content) "
            "values ('user-message','tenant-a','session-a','run-a','user','synthetic input')"
        )
        await conn.execute(
            "insert into run_context_snapshots "
            "(id, tenant_id, workspace_id, user_id, session_id, run_id, conversation_authority_json) "
            "values ('context-a','tenant-a','workspace-a','user-a','session-a','run-a',%s)",
            (Jsonb(authority),),
        )
        await conn.execute(
            "update runs set context_snapshot_id = 'context-a', "
            "input_json = input_json || %s::jsonb where id = 'run-a'",
            (Jsonb({"context_snapshot_id": "context-a", "context_snapshot": {"context_snapshot_id": "context-a"}}),),
        )
        frozen = await provider_epochs.prepare_provider_epoch(
            conn,
            scope=scope,
            run_id="run-a",
            conversation_context={
                "source_sha256": coverage["source_sha256"],
                "message_count": 0,
                "authority_schema_version": authority["schema_version"],
                "current_message_id": "user-message",
                "messages": [],
                "selected_message_count": 0,
                "selected_turn_count": 0,
            },
        )
        spec = _execution_spec(
            schema_version="ai-platform.execution-spec.v2",
            model_gateway_revision=1,
            model_max_input_tokens=100000,
            model_max_output_tokens=8192,
            context_pack={"conversation_context": frozen},
        )
        attempts = build_run_attempt_lifecycle_service()
        now = datetime.now(timezone.utc)
        started = await attempts.start_worker(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            queue_attempt_id="queue-a",
            worker_id="worker-a",
            execution_spec=spec,
            queue_message_id="a" * 64,
            last_heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=10),
        )
        assert started["owner_generation"] == 4
        payload = replace(
            project_execution_spec_to_run_payload(spec, attempt_id=started["id"]),
            owner_generation=4,
        )
        projection = build_attempt_settlement_projection_service()
        await conn.execute(
            "update run_attempt_settlement_storage set storage_version = 'attempt.v1' "
            "where singleton = true"
        )
        await conn.execute(
            "update run_attempt_settlement_writer set writer_version = 'attempt.v1' "
            "where singleton = true"
        )
        if retirement_state == "retired":
            await conn.execute(
                "update run_attempt_settlement_retirement "
                "set state = 'retired', minimum_compatible_version = '2026.10.25.11', "
                "operator_declaration = '{\"test\":true}'::jsonb, retired_at = now() "
                "where singleton = true"
            )
        lease_port = AttemptSettlementShadowLeaseRepository(
            repository=leases_postgres,
            settlement_projection=projection,
            enabled=lambda: True,
        )
        lease = await lease_port.create_sandbox_lease(
            conn,
            **{key: getattr(payload, key) for key in (
                "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
            )},
            trace_id="trace-a",
            sandbox_mode="ephemeral",
            provider="opensandbox",
            browser_enabled=False,
            ttl_seconds=1800,
            resource_limits_json={},
            user_visible_payload_json={},
            lease_payload_json={
                "attempt_id": started["id"],
                "owner_generation": 4,
                "callback_token_id": f"cbt:run-a:{started['id']}:g4",
            },
            runtime_container_id="container-a",
            runtime_container_name="container-a",
            runtime_executor_url="http://synthetic.invalid",
            runtime_workspace_container_path="/workspace",
            lease_id="lease-a",
        )
        await provider_epochs.callback_provider_epoch(
            conn,
            **{
                **scope_map,
                "run_id": "run-a",
                "attempt_id": started["id"],
                "provider_session_id": frozen["provider_session_id"],
                "subpath": None,
            },
            action="load",
            entries=[],
            expected_sequence=None,
        )
        entries = [
            {"type": "user", "uuid": "synthetic-user", "message": {"role": "user", "content": "synthetic input"}},
            {"type": "assistant", "uuid": "synthetic-answer", "message": {"role": "assistant", "content": [{"type": "text", "text": "Synthetic answer"}]}},
        ]
        await provider_epochs.callback_provider_epoch(
            conn,
            **{
                **scope_map,
                "run_id": "run-a",
                "attempt_id": started["id"],
                "provider_session_id": frozen["provider_session_id"],
                "subpath": None,
            },
            action="append",
            entries=entries,
            expected_sequence=1,
        )
        terminal = {
            "run_id": "run-a",
            "status": "failed",
            "message": "Synthetic failure",
            "error_code": "synthetic_failure",
            "error_message": "Synthetic failure",
            "provider_session_final_sequence": 2,
            "sdk_settlement": {
                "owner_generation": 4,
                "mirror_complete": True,
                "input_accepted": True,
                "received_result": True,
                "ack_unknown": False,
                "external_effect_unknown": False,
            },
        }
        binding = {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "attempt_id": started["id"],
            "lease_id": str(lease["id"]),
        }
        context = {
            "adapter_name": "claude-agent-worker",
            "run_payload": sandbox_reconciliation_payload(payload),
            "adapter_context": {
                "schema_version": "ai-platform.claude-agent-reconciliation-context.v1",
                "workspace": "/tmp/checkpoint-reclaim",
            },
        }
        await projection.record_intent_in_transaction(
            conn, **binding, intent_context=context,
        )
        await lease_port.record_sandbox_executor_accepted(
            conn, **binding, reconciliation_context=context,
        )
        await lease_port.record_sandbox_executor_terminal(
            conn,
            **binding,
            executor_status="failed",
            terminal_result=terminal,
        )
        claimed_a = await lease_port.claim_sandbox_executor_reconciliations(
            conn, claim_token="claim-a", stale_after_seconds=300, limit=1,
        )
        assert [row["id"] for row in claimed_a] == ["lease-a"]
        scope = {key: getattr(payload, key) for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )}
        workspace_receipt = {
            "schema_version": "ai-platform.workspace-version.v1",
            "state": "saved",
            "version_id": "workspace-version-a",
            "scope": scope,
            "file_count": 1,
            "total_bytes": 26,
            "source_stop_evidence": "provider_stopped",
        }
        continuation = {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "attempt_id": started["id"],
            "final_sequence": 2,
            "workspace_receipt": workspace_receipt,
            "stop_evidence": "provider_stopped",
            "sdk_settlement": terminal["sdk_settlement"],
            "external_effect_unknown": False,
        }
        stage = {
            "schema_version": "ai-platform.terminal-reconciliation.v1",
            "scope": scope,
            "owner_generation": 4,
            "container_id": "container-a",
            "artifact_workspace": None,
            "terminal_result": terminal,
            "continuation": continuation,
        }
    async with conn.transaction():
        assert await lease_port.record_sandbox_reconciliation_stage(
            conn, lease_id="lease-a", claim_token="claim-a", stage=stage,
        )
    marker = await (await conn.execute(
        "select lease_payload_json->'terminal_reconciliation_claim' as marker "
        "from sandbox_leases where id = 'lease-a'"
    )).fetchone()
    assert marker["marker"]["claim_token"] == "claim-a"
    if not marker_present:
        await conn.execute(
            "update sandbox_leases set lease_payload_json = "
            "lease_payload_json - 'terminal_reconciliation_claim' where id = 'lease-a'"
        )
        await conn.commit()
    if lease_status == "released":
        await conn.execute(
            "update sandbox_leases set status = 'released', released_at = now() "
            "where id = 'lease-a'"
        )
        await conn.commit()

    async with conn.transaction():
        assert await lease_port.retry_sandbox_executor_reconciliation(
            conn, lease_id="lease-a", claim_token="claim-a", error="simulated_reclaim",
        )
    await conn.execute(
        "update run_attempt_settlements set next_retry_at = now() - interval '1 second' "
        "where tenant_id = 'tenant-a' and run_id = 'run-a' and attempt_id = %s",
        (started["id"],),
    )
    await conn.commit()
    async with conn.transaction():
        claimed_b = await lease_port.claim_sandbox_executor_reconciliations(
            conn, claim_token="claim-b", stale_after_seconds=300, limit=1,
        )
    assert [row["id"] for row in claimed_b] == ["lease-a"]
    assert claimed_b[0]["settlement_phase"] == "claimed"
    assert claimed_b[0]["executor_reconciliation_claim_token"] == "claim-b"

    async with conn.transaction():
        assert not await lease_port.record_sandbox_reconciliation_stage(
            conn, lease_id="lease-a", claim_token="claim-a", stage=stage,
        )
        assert await lease_port.record_sandbox_reconciliation_stage(
            conn, lease_id="lease-a", claim_token="claim-b", stage=stage,
        )
        assert not await lease_port.finalize_stopped_executor_checkpoint(
            conn,
            scope=scope,
            lease_id="lease-a",
            claim_token="claim-a",
            expected_stage=stage,
        )
    marker = await (await conn.execute(
        "select lease_payload_json->'terminal_reconciliation_claim' as marker "
        "from sandbox_leases where id = 'lease-a'"
    )).fetchone()
    assert marker["marker"]["claim_token"] == "claim-b"

    attempts = build_run_attempt_lifecycle_service()
    lifecycle = build_run_lifecycle_service()
    continuation_ports = build_continuation_settlement_ports()

    async def commit_terminal_transaction(claim_token: str) -> None:
        async with conn.transaction():
            # Revalidate the current Runs claim and retain Run -> lease -> Attempt locks.
            assert await lease_port.record_sandbox_reconciliation_stage(
                conn, lease_id="lease-a", claim_token="claim-b", stage=stage,
            )
            await continuation_ports.commit(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id=started["id"],
                settlement=continuation,
            )
            progress = await fail_run_terminalization(
                conn,
                lifecycle=lifecycle,
                capabilities=None,
                tenant_id="tenant-a",
                run_id="run-a",
                error_code="synthetic_failure",
                error_message="Synthetic failure",
                result_json={"message": "Synthetic failure"},
            )
            assert progress.is_terminal()
            await attempts.terminalize(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id=started["id"],
                status="failed",
                terminal_reason="run_failed",
                error_code="synthetic_failure",
            )
            await finalize_stopped_executor_checkpoint(
                conn,
                lease_row=claimed_b[0],
                claim_token=claim_token,
                finalize=lease_port.finalize_stopped_executor_checkpoint,
            )

    @asynccontextmanager
    async def transaction_factory():
        async with conn.transaction():
            yield conn

    with pytest.raises(RuntimeError, match="executor_reconciliation_claim_lost"):
        async with provider_session_verification_scope(
            transaction_factory, tenant_id="tenant-a", run_id="run-a",
        ):
            await commit_terminal_transaction("claim-a")
    rolled_back = await (await conn.execute(
        "select runs.status as run_status, attempt.status as attempt_status, "
        "lease.status as lease_status, lease.executor_cleanup_status, epoch.state as epoch_state, "
        "epoch.recovery_json, detail.settlement_phase, detail.settlement_claim_token "
        "from runs join run_attempts attempt on attempt.run_id = runs.id "
        "join sandbox_leases lease on lease.run_id = runs.id "
        "join run_attempt_settlements detail on detail.run_id = runs.id "
        "join provider_session_epochs epoch on epoch.id = %s where runs.id = 'run-a'",
        (frozen["provider_epoch_id"],),
    )).fetchone()
    assert rolled_back["run_status"] == "running"
    assert rolled_back["attempt_status"] == "running"
    assert rolled_back["lease_status"] == lease_status
    assert rolled_back["epoch_state"] == "active" and rolled_back["recovery_json"] is None
    assert rolled_back["settlement_phase"] == "claimed"
    assert rolled_back["settlement_claim_token"] == "claim-b"

    async with provider_session_verification_scope(
        transaction_factory, tenant_id="tenant-a", run_id="run-a",
    ):
        await commit_terminal_transaction("claim-b")
    committed = await (await conn.execute(
        "select runs.status as run_status, attempt.status as attempt_status, "
        "lease.status as lease_status, lease.executor_cleanup_status, epoch.state as epoch_state, "
        "epoch.recovery_json, detail.settlement_phase, detail.settlement_claim_token, "
        "lease.lease_payload_json->'terminal_reconciliation_claim' as marker "
        "from runs join run_attempts attempt on attempt.run_id = runs.id "
        "join sandbox_leases lease on lease.run_id = runs.id "
        "join run_attempt_settlements detail on detail.run_id = runs.id "
        "join provider_session_epochs epoch on epoch.id = %s where runs.id = 'run-a'",
        (frozen["provider_epoch_id"],),
    )).fetchone()
    assert committed["run_status"] == "failed"
    assert committed["attempt_status"] == "failed"
    assert committed["lease_status"] == "released"
    assert committed["executor_cleanup_status"] == "finalized"
    assert committed["epoch_state"] == "ready"
    assert committed["recovery_json"]["workspace_saved"] is True
    assert committed["settlement_phase"] == "settled"
    assert committed["settlement_claim_token"] is None
    assert committed["marker"]["claim_token"] == "claim-b"
