"""Sandbox-owned terminal checkpoint persistence.

Callers own the transaction. Attempt settlement authority remains in Runs;
these functions persist only the host checkpoint and Sandbox resource state.
"""

from __future__ import annotations

import json
from typing import Any


_REPAIRABLE_ERROR = "executor_reconciliation_runtime_handle_invalid"


def _scope_values(scope: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        str(scope[key])
        for key in (
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "run_id",
            "attempt_id",
        )
    )


async def get_scoped_checkpoint_lease(
    conn: Any, *, scope: dict[str, str], lease_id: str,
) -> dict[str, Any] | None:
    """Lock one resource row using only retained Sandbox identity fields."""
    row = await (
        await conn.execute(
            """
            select * from sandbox_leases
            where id = %s and tenant_id = %s and workspace_id = %s and user_id = %s
              and session_id = %s and run_id = %s and attempt_id = %s
            for update
            """,
            (lease_id, *_scope_values(scope)),
        )
    ).fetchone()
    return dict(row) if row is not None else None


async def record_sandbox_reconciliation_stage_legacy(
    conn: Any, *, lease_id: str, claim_token: str, stage: dict[str, Any],
) -> bool:
    """Keep pre-cutover behavior while legacy settlement columns are live."""
    cursor = await conn.execute(
        """
        update sandbox_leases set lease_payload_json = jsonb_set(
             lease_payload_json, '{terminal_reconciliation}', %s::jsonb, true),
             updated_at = now()
        where id = %s and executor_reconciliation_claim_token = %s
          and executor_reconciliation_status = 'claimed'
          and status in ('active', 'released')
          and tenant_id = %s and run_id = %s and attempt_id = %s
          and lease_payload_json->>'owner_generation' = %s
          and runtime_container_id = %s
        returning id
        """,
        (
            json.dumps(stage, ensure_ascii=False),
            lease_id,
            claim_token,
            stage["scope"]["tenant_id"],
            stage["scope"]["run_id"],
            stage["scope"]["attempt_id"],
            str(stage["owner_generation"]),
            stage["container_id"],
        ),
    )
    return await cursor.fetchone() is not None


async def record_sandbox_reconciliation_stage_resource(
    conn: Any,
    *,
    scope: dict[str, str],
    lease_id: str,
    owner_generation: int,
    container_id: str,
    stage: dict[str, Any],
    expected_stage: dict[str, Any] | None,
    expected_claim_marker: dict[str, Any] | None,
    claim_marker: dict[str, Any],
) -> bool:
    """CAS the host stage and its Runs-verified claim mirror on the resource row."""
    stage_predicate = (
        "not (lease_payload_json ? 'terminal_reconciliation')"
        if expected_stage is None
        else "lease_payload_json->'terminal_reconciliation' = %s::jsonb"
    )
    claim_predicate = (
        "not (lease_payload_json ? 'terminal_reconciliation_claim')"
        if expected_claim_marker is None
        else "lease_payload_json->'terminal_reconciliation_claim' = %s::jsonb"
    )
    parameters: list[Any] = [
        json.dumps(stage, ensure_ascii=False),
        json.dumps(claim_marker, ensure_ascii=False),
        lease_id,
        *_scope_values(scope),
        scope["attempt_id"],
        str(owner_generation),
        container_id,
    ]
    if expected_stage is not None:
        parameters.append(json.dumps(expected_stage, ensure_ascii=False))
    if expected_claim_marker is not None:
        parameters.append(json.dumps(expected_claim_marker, ensure_ascii=False))
    cursor = await conn.execute(
        f"""
        update sandbox_leases
        set lease_payload_json = jsonb_set(
              jsonb_set(lease_payload_json, '{{terminal_reconciliation}}', %s::jsonb, true),
              '{{terminal_reconciliation_claim}}', %s::jsonb, true
            ),
            updated_at = now()
        where id = %s and tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and run_id = %s and attempt_id = %s
          and status in ('active', 'released')
          and lease_payload_json->>'attempt_id' = %s
          and lease_payload_json->>'owner_generation' = %s
          and runtime_container_id = %s
          and {stage_predicate}
          and {claim_predicate}
        returning id
        """,
        tuple(parameters),
    )
    return await cursor.fetchone() is not None


async def finalize_stopped_executor_checkpoint_legacy(
    conn: Any,
    *,
    scope: dict[str, str],
    lease_id: str,
    claim_token: str,
    expected_stage: dict[str, Any],
) -> bool:
    """Finalize a checkpoint under the former lease-owned claim."""
    cursor = await conn.execute(
        """
        update sandbox_leases set status='released', released_at=coalesce(released_at,now()),
             release_reason='executor_reconciled', executor_reconciliation_status='finalized',
             executor_reconciliation_retry_not_before=null, executor_reconciliation_claim_token=null,
             executor_reconciliation_claimed_at=null, executor_reconciliation_error='',
             executor_reconciled_at=now(), updated_at=now()
        where id=%s and tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
          and run_id=%s and attempt_id=%s and status in ('active','released')
          and executor_reconciliation_status='claimed' and executor_reconciliation_claim_token=%s
          and lease_payload_json->'terminal_reconciliation'=%s::jsonb
          and lease_payload_json->>'owner_generation'=%s and runtime_container_id=%s
          and executor_terminal_json is not null
        returning id
        """,
        (
            lease_id,
            *_scope_values(scope),
            claim_token,
            json.dumps(expected_stage, ensure_ascii=False),
            str(expected_stage["owner_generation"]),
            expected_stage["container_id"],
        ),
    )
    return await cursor.fetchone() is not None


async def finalize_stopped_executor_checkpoint_resource(
    conn: Any,
    *,
    scope: dict[str, str],
    lease_id: str,
    claim_token: str,
    expected_stage: dict[str, Any],
    claim_marker: dict[str, Any],
) -> bool:
    """Release only the exact stopped resource after Runs terminal settlement."""
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = 'executor_reconciled',
            executor_cleanup_status = 'finalized',
            executor_cleanup_claim_token = null,
            executor_cleanup_claimed_at = null,
            executor_cleanup_retry_not_before = null,
            executor_reconciliation_error = '',
            executor_reconciled_at = now(),
            updated_at = now()
        where id = %s and tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and run_id = %s and attempt_id = %s
          and status in ('active', 'released')
          and executor_status in ('completed', 'failed', 'cancelled')
          and executor_cleanup_status in ('waiting_settlement', 'failed', 'finalized')
          and executor_cleanup_claim_token is null
          and lease_payload_json->>'attempt_id' = %s
          and lease_payload_json->>'owner_generation' = %s
          and runtime_container_id = %s
          and lease_payload_json->'terminal_reconciliation' = %s::jsonb
          and lease_payload_json->'terminal_reconciliation_claim' = %s::jsonb
          and coalesce(lease_payload_json->'checkpoint_repair'->>'claim_token', %s) = %s
        returning id
        """,
        (
            lease_id,
            *_scope_values(scope),
            str(expected_stage["scope"]["attempt_id"]),
            str(expected_stage["owner_generation"]),
            expected_stage["container_id"],
            json.dumps(expected_stage, ensure_ascii=False),
            json.dumps(claim_marker, ensure_ascii=False),
            claim_token,
            claim_token,
        ),
    )
    return await cursor.fetchone() is not None


async def get_quarantined_checkpoint_for_repair_legacy(
    conn: Any, *, scope: dict[str, str], lease_id: str,
) -> dict[str, Any] | None:
    """Read the one legacy quarantine state that operator repair recognizes."""
    row = await (
        await conn.execute(
            """
            select * from sandbox_leases
            where id = %s and tenant_id = %s and workspace_id = %s and user_id = %s
              and session_id = %s and run_id = %s and attempt_id = %s
              and status = 'quarantined' and executor_reconciliation_status = 'failed'
              and executor_reconciliation_error = %s
              and executor_reconciliation_claim_token is null
              and not (lease_payload_json ? 'checkpoint_repair')
            for update
            """,
            (lease_id, *_scope_values(scope), _REPAIRABLE_ERROR),
        )
    ).fetchone()
    return dict(row) if row is not None else None


async def get_quarantined_checkpoint_for_repair_resource(
    conn: Any, *, scope: dict[str, str], lease_id: str,
) -> dict[str, Any] | None:
    """Read quarantined host checkpoint fields without retired settlement columns."""
    row = await get_scoped_checkpoint_lease(conn, scope=scope, lease_id=lease_id)
    if row is None:
        return None
    payload = row.get("lease_payload_json")
    if not isinstance(payload, dict):
        return None
    if (
        row.get("status") != "quarantined"
        or row.get("executor_cleanup_status") != "failed"
        or row.get("executor_reconciliation_error") != _REPAIRABLE_ERROR
        or row.get("executor_cleanup_claim_token") is not None
        or "checkpoint_repair" in payload
        or not isinstance(payload.get("terminal_reconciliation"), dict)
    ):
        return None
    return row


async def claim_quarantined_checkpoint_for_repair_legacy(
    conn: Any,
    *,
    scope: dict[str, str],
    lease_id: str,
    invocation_id: str,
    claim_token: str,
    expected_updated_at: Any,
    expected_stage: dict[str, Any],
    writer_generation: int,
    cancellation_generation: int,
) -> dict[str, Any] | None:
    """Preserve explicit repair behavior before the Attempt writer cutover."""
    cursor = await conn.execute(
        """
        update sandbox_leases set status='released',released_at=coalesce(released_at,now()),
             release_reason='verified_checkpoint_operator_repair',
             executor_reconciliation_status='claimed',executor_reconciliation_claim_token=%s,
             executor_reconciliation_claimed_at=now(),
             executor_terminal_reconciliation_attempt_count=executor_terminal_reconciliation_attempt_count+1,
             lease_payload_json=jsonb_set(lease_payload_json,'{checkpoint_repair}',jsonb_build_object(
               'schema_version','ai-platform.checkpoint-repair.v1','invocation_id',%s::text,
               'source_status',status,'source_reconciliation_status',executor_reconciliation_status,
               'source_error',executor_reconciliation_error,'source_quarantined_at',updated_at,
               'writer_generation',%s::bigint,'cancellation_generation',%s::bigint,
               'claim_token',%s::text,'authorized_at',now()), true),
             updated_at=now()
        where id=%s and tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
          and run_id=%s and attempt_id=%s and status='quarantined'
          and executor_reconciliation_status='failed'
          and executor_reconciliation_error=%s and executor_reconciliation_claim_token is null
          and updated_at=%s and not (lease_payload_json ? 'checkpoint_repair')
          and lease_payload_json->'terminal_reconciliation'=%s::jsonb
        returning *
        """,
        (
            claim_token,
            invocation_id,
            writer_generation,
            cancellation_generation,
            claim_token,
            lease_id,
            *_scope_values(scope),
            _REPAIRABLE_ERROR,
            expected_updated_at,
            json.dumps(expected_stage, ensure_ascii=False),
        ),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def claim_quarantined_checkpoint_for_repair_resource(
    conn: Any,
    *,
    scope: dict[str, str],
    lease_id: str,
    invocation_id: str,
    claim_token: str,
    expected_updated_at: Any,
    expected_stage: dict[str, Any],
    writer_generation: int,
    cancellation_generation: int,
    expected_claim_marker: dict[str, Any] | None,
    claim_marker: dict[str, Any],
) -> dict[str, Any] | None:
    """Claim the exact quarantined resource after Runs transfers settlement authority."""
    claim_predicate = (
        "not (lease_payload_json ? 'terminal_reconciliation_claim')"
        if expected_claim_marker is None
        else "lease_payload_json->'terminal_reconciliation_claim' = %s::jsonb"
    )
    parameters: list[Any] = [
        invocation_id,
        writer_generation,
        cancellation_generation,
        claim_token,
        json.dumps(claim_marker, ensure_ascii=False),
        lease_id,
        *_scope_values(scope),
        _REPAIRABLE_ERROR,
        expected_updated_at,
        json.dumps(expected_stage, ensure_ascii=False),
        str(expected_stage["scope"]["attempt_id"]),
        str(writer_generation),
        expected_stage["container_id"],
    ]
    if expected_claim_marker is not None:
        parameters.append(json.dumps(expected_claim_marker, ensure_ascii=False))
    cursor = await conn.execute(
        f"""
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = 'verified_checkpoint_operator_repair',
            lease_payload_json = jsonb_set(
              jsonb_set(
                lease_payload_json,
                '{{checkpoint_repair}}',
                jsonb_build_object(
                  'schema_version', 'ai-platform.checkpoint-repair.v1',
                  'invocation_id', %s::text,
                  'source_status', status,
                  'source_cleanup_status', executor_cleanup_status,
                  'source_error', executor_reconciliation_error,
                  'source_quarantined_at', updated_at,
                  'writer_generation', %s::bigint,
                  'cancellation_generation', %s::bigint,
                  'claim_token', %s::text,
                  'authorized_at', now()
                ),
                true
              ),
              '{{terminal_reconciliation_claim}}', %s::jsonb, true
            ),
            updated_at = now()
        where id = %s and tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and run_id = %s and attempt_id = %s
          and status = 'quarantined'
          and executor_cleanup_status = 'failed'
          and executor_reconciliation_error = %s
          and executor_cleanup_claim_token is null
          and updated_at = %s
          and not (lease_payload_json ? 'checkpoint_repair')
          and lease_payload_json->'terminal_reconciliation' = %s::jsonb
          and lease_payload_json->>'attempt_id' = %s
          and lease_payload_json->>'owner_generation' = %s
          and runtime_container_id = %s
          and {claim_predicate}
        returning *
        """,
        tuple(parameters),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None
