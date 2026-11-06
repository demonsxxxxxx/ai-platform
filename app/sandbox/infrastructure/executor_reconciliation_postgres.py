"""Sandbox resource handoff and trusted stopped-checkpoint persistence."""

from __future__ import annotations

import json
from typing import Any

from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size


async def mark_sandbox_executor_cleanup_pending(
    connection: Any, *, lease_id: str, claim_token: str, error: str,
) -> bool:
    """The Runs wrapper has already verified its committed settled fact."""
    cursor = await connection.execute(
        """
        update sandbox_leases
        set executor_cleanup_status = 'cleanup_pending',
            executor_cleanup_retry_not_before = null,
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            executor_reconciliation_error = %s,
            expires_at = least(coalesce(expires_at, now()), now()), updated_at = now()
        where id = %s and status in ('active', 'released')
          and executor_cleanup_status in ('waiting_settlement', 'cleanup_pending')
          and exists (
            select 1 from run_attempt_settlements detail
            where detail.tenant_id = sandbox_leases.tenant_id
              and detail.run_id = sandbox_leases.run_id and detail.attempt_id = sandbox_leases.attempt_id
              and detail.source_lease_id = sandbox_leases.id and detail.settlement_phase = 'settled'
              and detail.settled_claim_token = %s
          )
        returning id
        """, (error[:1000], lease_id, claim_token),
    )
    return await cursor.fetchone() is not None


async def quarantine_sandbox_executor_reconciliation(
    connection: Any, *, lease_id: str, claim_token: str, error: str,
    probe_claim_token: str | None = None,
) -> bool:
    """Quarantine resources without falsely asserting release or consuming a stale claim."""
    if probe_claim_token is not None:
        predicate = "executor_probe_claim_token = %s"
        token = probe_claim_token
    else:
        predicate = """
          executor_cleanup_status = 'waiting_settlement'
          and exists (
            select 1 from run_attempt_settlements detail
            where detail.tenant_id = sandbox_leases.tenant_id
              and detail.run_id = sandbox_leases.run_id and detail.attempt_id = sandbox_leases.attempt_id
              and detail.source_lease_id = sandbox_leases.id and detail.mapping_state = 'mapped'
              and detail.settlement_phase = 'claimed' and detail.settlement_claim_token = %s
          )
        """
        token = claim_token
    cursor = await connection.execute(
        f"""
        update sandbox_leases set status = 'quarantined', executor_cleanup_status = 'failed',
            executor_cleanup_claim_token = null, executor_cleanup_claimed_at = null,
            executor_probe_claim_token = null, executor_probe_claimed_at = null,
            executor_reconciliation_error = %s, updated_at = now()
        where id = %s and status in ('active', 'released') and {predicate}
        returning id
        """, (error[:1000], lease_id, token),
    )
    return await cursor.fetchone() is not None


async def get_quarantined_checkpoint_for_repair(
    connection: Any, *, scope: dict[str, str], lease_id: str,
) -> dict[str, Any] | None:
    row = await (await connection.execute(
        """select lease.* from sandbox_leases lease
           join run_attempt_settlements detail
             on detail.tenant_id=lease.tenant_id and detail.run_id=lease.run_id
            and detail.attempt_id=lease.attempt_id and detail.source_lease_id=lease.id
           where lease.id=%s and lease.tenant_id=%s and lease.workspace_id=%s
             and lease.user_id=%s and lease.session_id=%s and lease.run_id=%s and lease.attempt_id=%s
             and lease.status='quarantined' and lease.executor_cleanup_status='failed'
             and lease.executor_reconciliation_error='executor_reconciliation_runtime_handle_invalid'
             and detail.settlement_phase='claimed'
             and not (lease.lease_payload_json ? 'checkpoint_repair') for update of lease""",
        (lease_id, *(scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id"))),
    )).fetchone()
    return dict(row) if row is not None else None


async def claim_quarantined_checkpoint_for_repair(
    connection: Any, *, scope: dict[str, str], lease_id: str, invocation_id: str,
    claim_token: str, expected_updated_at: Any, expected_stage: dict[str, Any],
    writer_generation: int, cancellation_generation: int,
) -> dict[str, Any] | None:
    """Operator-only resource CAS; Runs separately owns its recovery claim."""
    row = await (await connection.execute(
        """update sandbox_leases set status='released',released_at=coalesce(released_at,now()),
             release_reason='verified_checkpoint_operator_repair',executor_cleanup_status='waiting_settlement',
             lease_payload_json=jsonb_set(lease_payload_json,'{checkpoint_repair}',jsonb_build_object(
               'schema_version','ai-platform.checkpoint-repair.v1','invocation_id',%s::text,
               'source_status',status,'source_cleanup_status',executor_cleanup_status,
               'source_error',executor_reconciliation_error,'source_quarantined_at',updated_at,
               'writer_generation',%s::bigint,'cancellation_generation',%s::bigint,
               'claim_token',%s::text,'authorized_at',now())),updated_at=now()
           where id=%s and tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
             and run_id=%s and attempt_id=%s and status='quarantined' and executor_cleanup_status='failed'
             and executor_reconciliation_error='executor_reconciliation_runtime_handle_invalid'
             and updated_at=%s and not (lease_payload_json ? 'checkpoint_repair')
             and lease_payload_json->'terminal_reconciliation'=%s::jsonb returning *""",
        (invocation_id, writer_generation, cancellation_generation, claim_token,
         lease_id, *(scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id")),
         expected_updated_at, json.dumps(expected_stage)),
    )).fetchone()
    return dict(row) if row is not None else None


async def record_sandbox_reconciliation_stage(
    connection: Any, *, lease_id: str, claim_token: str, stage: dict[str, Any],
) -> bool:
    """Bind a host stop/save checkpoint to the exact Runs settlement claim."""
    ensure_json_size(stage, max_bytes=RUN_RESULT_MAX_BYTES, code="sandbox_reconciliation_stage_too_large")
    cursor = await connection.execute(
        """update sandbox_leases set lease_payload_json=jsonb_set(
             lease_payload_json,'{terminal_reconciliation}',%s::jsonb),updated_at=now()
           where id=%s and status in ('active','released') and tenant_id=%s and run_id=%s and attempt_id=%s
             and lease_payload_json->>'owner_generation'=%s and runtime_container_id=%s
             and exists (
               select 1 from run_attempt_settlements detail
               where detail.tenant_id=sandbox_leases.tenant_id and detail.run_id=sandbox_leases.run_id
                 and detail.attempt_id=sandbox_leases.attempt_id and detail.source_lease_id=sandbox_leases.id
                 and detail.settlement_phase='claimed' and detail.settlement_claim_token=%s
             ) returning id""",
        (json.dumps(stage), lease_id, stage["scope"]["tenant_id"], stage["scope"]["run_id"],
         stage["scope"]["attempt_id"], str(stage["owner_generation"]), stage["container_id"], claim_token),
    )
    return await cursor.fetchone() is not None


async def finalize_stopped_executor_checkpoint(
    connection: Any, *, scope: dict[str, str], lease_id: str, claim_token: str,
    expected_stage: dict[str, Any],
) -> bool:
    """Release only after the same claim atomically committed the Run/Attempt/Context."""
    cursor = await connection.execute(
        """update sandbox_leases set status='released',released_at=coalesce(released_at,now()),
             release_reason='executor_reconciled',executor_cleanup_status='finalized',
             executor_cleanup_retry_not_before=null,executor_cleanup_claim_token=null,
             executor_cleanup_claimed_at=null,executor_reconciliation_error='',
             executor_reconciled_at=now(),updated_at=now()
           where id=%s and tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
             and run_id=%s and attempt_id=%s and status in ('active','released')
             and lease_payload_json->'terminal_reconciliation'=%s::jsonb
             and lease_payload_json->>'owner_generation'=%s and runtime_container_id=%s
             and exists (
               select 1 from run_attempt_settlements detail
               where detail.tenant_id=sandbox_leases.tenant_id and detail.run_id=sandbox_leases.run_id
                 and detail.attempt_id=sandbox_leases.attempt_id and detail.source_lease_id=sandbox_leases.id
                 and detail.mapping_state='mapped' and detail.receipt_json is not null
                 and detail.settlement_phase='settled' and detail.settled_claim_token=%s
                 and detail.result_json is not null and detail.settled_at is not null
             ) returning id""",
        (lease_id, *(scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id")),
         json.dumps(expected_stage), str(expected_stage["owner_generation"]), expected_stage["container_id"], claim_token),
    )
    return await cursor.fetchone() is not None
