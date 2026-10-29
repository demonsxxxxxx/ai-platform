"""Runs-owned authorization and claim transfer for operator checkpoint repair."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runs.domain.attempt_settlement_projection import AttemptSettlementProjectionConflict
from app.runs.infrastructure.attempt_settlement_writer_postgres import _current_owned_detail
from app.runs.infrastructure.attempt_settlement_projection_postgres import _mapping


_CHECKPOINT_REPAIR_SCOPE_FIELDS = frozenset(
    {"tenant_id", "workspace_id", "user_id", "session_id", "agent_id", "run_id", "attempt_id"}
)


async def _checkpoint_repair_authority(
    conn: Any,
    *,
    scope: Mapping[str, str],
    lease_id: str,
    writer_generation: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if (
        set(scope) != _CHECKPOINT_REPAIR_SCOPE_FIELDS
        or any(not isinstance(value, str) or not value for value in scope.values())
        or type(writer_generation) is not int
        or writer_generation < 1
    ):
        return None
    try:
        attempt, _run, lease, detail = await _current_owned_detail(
            conn,
            tenant_id=scope["tenant_id"],
            run_id=scope["run_id"],
            attempt_id=scope["attempt_id"],
            source_lease_id=lease_id,
        )
    except AttemptSettlementProjectionConflict:
        return None
    run = await (
        await conn.execute(
            """
            select tenant_id, workspace_id, user_id, session_id, agent_id,
                   status, terminalization_target, cancel_requested_at
            from runs where tenant_id = %s and id = %s for update
            """,
            (scope["tenant_id"], scope["run_id"]),
        )
    ).fetchone()
    latest_attempt = await (
        await conn.execute(
            """
            select id from run_attempts
            where tenant_id = %s and run_id = %s
            order by ordinal desc limit 1
            """,
            (scope["tenant_id"], scope["run_id"]),
        )
    ).fetchone()
    payload = _mapping(lease.get("lease_payload_json"))
    cancellation_generation = writer_generation + 1
    if (
        run is None
        or any(run.get(key) != scope[key] for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "agent_id",
        ))
        or run.get("status") != "running"
        or run.get("terminalization_target") != "cancel_requested"
        or run.get("cancel_requested_at") is None
        or latest_attempt is None
        or latest_attempt.get("id") != scope["attempt_id"]
        or attempt.get("status") != "cancel_requested"
        or attempt.get("owner_kind") != "queue_worker"
        or attempt.get("owner_generation") != cancellation_generation
        or lease.get("status") != "quarantined"
        or payload is None
        or payload.get("attempt_id") != scope["attempt_id"]
        or payload.get("owner_generation") != writer_generation
        or detail.get("mapping_state") != "mapped"
        or detail.get("source_lease_id") != lease_id
        or detail.get("receipt_json") is None
        or detail.get("accepted_context_json") is None
        or detail.get("receipt_owner_generation") != writer_generation
        or detail.get("settlement_phase") != "claimed"
        or not detail.get("settlement_claim_token")
        or detail.get("attempt_status_snapshot") != "cancel_requested"
        or detail.get("attempt_owner_generation_snapshot") != cancellation_generation
        or detail.get("run_status_snapshot") != "running"
        or detail.get("cancel_requested_at_snapshot") != run.get("cancel_requested_at")
    ):
        return None
    return attempt, dict(run), lease, detail


async def validate_attempt_settlement_checkpoint_repair_candidate(
    conn: Any,
    *,
    scope: Mapping[str, str],
    lease_id: str,
    writer_generation: int,
) -> dict[str, Any] | None:
    """Return Runs authority for one exact, currently cancelled checkpoint."""
    authority = await _checkpoint_repair_authority(
        conn, scope=scope, lease_id=lease_id, writer_generation=writer_generation,
    )
    return dict(authority[3]) if authority is not None else None


async def claim_attempt_settlement_checkpoint_repair(
    conn: Any,
    *,
    scope: Mapping[str, str],
    lease_id: str,
    expected_claim_token: str,
    claim_token: str,
    writer_generation: int,
    cancellation_generation: int,
) -> bool:
    """CAS the Runs settlement claim to the explicit operator invocation."""
    if (
        not isinstance(expected_claim_token, str)
        or not expected_claim_token
        or not isinstance(claim_token, str)
        or not claim_token
        or type(cancellation_generation) is not int
        or cancellation_generation != writer_generation + 1
    ):
        return False
    authority = await _checkpoint_repair_authority(
        conn, scope=scope, lease_id=lease_id, writer_generation=writer_generation,
    )
    if authority is None or authority[3].get("settlement_claim_token") != expected_claim_token:
        return False
    cursor = await conn.execute(
        """
        update run_attempt_settlements
        set settlement_phase = 'claimed', settlement_claim_token = %s,
            settlement_claimed_at = now(), next_retry_at = null,
            settlement_attempt_count = settlement_attempt_count + 1,
            updated_at = now()
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and source_lease_id = %s and mapping_state = 'mapped'
          and receipt_json is not null and accepted_context_json is not null
          and receipt_owner_generation = %s
          and settlement_phase = 'claimed' and settlement_claim_token = %s
        returning attempt_id
        """,
        (
            claim_token,
            scope["tenant_id"],
            scope["run_id"],
            scope["attempt_id"],
            lease_id,
            writer_generation,
            expected_claim_token,
        ),
    )
    return await cursor.fetchone() is not None
