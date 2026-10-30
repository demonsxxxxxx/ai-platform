"""PostgreSQL outbox for exact-attempt executor cancellation delivery."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from psycopg import AsyncConnection

from app.sandbox.infrastructure.cancel_delivery_authority import (
    authority_is_current as _authority_is_current,
)
from app.sandbox.infrastructure.cancel_delivery_authority import (
    claimed_row_matches_lease as _claimed_row_matches_lease,
)
from app.sandbox.infrastructure.cancel_delivery_authority import (
    dispatch_owner_generation as _dispatch_owner_generation,
)

_MAX_LIMIT = 20
_MAX_SCAN_BUDGET = 100
_RETRY_DELAY_CAP_SECONDS = 300
_AUTHORITY_CANDIDATE_SQL = """
exists (
  select 1
  from runs current_run
  join run_attempts current_attempt
    on current_attempt.tenant_id = current_run.tenant_id
   and current_attempt.run_id = current_run.id
   and current_attempt.id = lease.attempt_id
  join sandbox_execution_dispatches dispatch
    on dispatch.tenant_id = lease.tenant_id
   and dispatch.run_id = lease.run_id
   and dispatch.attempt_id = lease.attempt_id
   and dispatch.lease_id = lease.id
  where current_run.tenant_id = lease.tenant_id
    and current_run.id = lease.run_id
    and current_run.workspace_id = lease.workspace_id
    and current_run.user_id = lease.user_id
    and current_run.session_id = lease.session_id
    and current_run.status = 'running'
    and current_run.cancel_requested_at is not null
    and current_attempt.status = 'cancel_requested'
    and current_attempt.owner_kind = 'queue_worker'
    and current_attempt.owner_id <> ''
    and dispatch.status in ('intent', 'accepted', 'unknown')
    and dispatch.provider = lease.provider
    and dispatch.provider_task_id = current_attempt.id
    and lease.cancel_delivery_owner_generation = dispatch.owner_generation
    and current_attempt.owner_generation = dispatch.owner_generation + 1
    and lease.lease_payload_json->>'attempt_id' = lease.attempt_id
    and lease.lease_payload_json->>'owner_generation' = dispatch.owner_generation::text
    and lease.status = 'active'
    and lease.released_at is null
    and lease.expires_at > clock_timestamp()
    and lease.runtime_handle_verified_at is not null
    and coalesce(lease.runtime_container_id, '') <> ''
    and coalesce(lease.runtime_container_name, '') <> ''
    and coalesce(lease.runtime_executor_url, '') <> ''
    and coalesce(lease.runtime_workspace_container_path, '') <> ''
    and lease.executor_status in ('pending', 'accepted', 'running')
    and lease.executor_terminal_received_at is null
    and not exists (
      select 1 from run_attempts later_attempt
      where later_attempt.tenant_id = current_attempt.tenant_id
        and later_attempt.run_id = current_attempt.run_id
        and later_attempt.ordinal > current_attempt.ordinal
    )
    and not exists (
      select 1 from run_attempt_settlements receipt
      where receipt.tenant_id = lease.tenant_id
        and receipt.run_id = lease.run_id
        and receipt.attempt_id = lease.attempt_id
        and (receipt.receipt_identity is not null
          or receipt.receipt_json is not null
          or receipt.receipt_recorded_at is not null)
    )
)
"""


async def stage_in_transaction(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> int:
    """Stage the exact frozen lease after the owning Run cancellation transition."""
    run = await (
        await conn.execute(
            """
            select tenant_id, id, workspace_id, user_id, session_id, status,
                   cancel_requested_at
            from runs
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, run_id),
        )
    ).fetchone()
    if run is None:
        return 0

    # Lock lease rows in stable order before locking the Attempt. Cancellation
    # already owns the Run row, which also excludes competing delivery claims.
    leases_cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and status = 'active'
          and cancel_delivery_status in ('none', 'pending', 'retry', 'claimed')
        order by id asc
        for update
        """,
        (tenant_id, run_id, attempt_id),
    )
    leases = [dict(row) for row in await leases_cursor.fetchall()]
    attempt = await (
        await conn.execute(
            """
            select * from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
            for update
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    latest = await (
        await conn.execute(
            """
            select id from run_attempts
            where tenant_id = %s and run_id = %s
            order by ordinal desc
            limit 1
            """,
            (tenant_id, run_id),
        )
    ).fetchone()
    if not leases or attempt is None or latest is None:
        return 0

    dispatch_cursor = await conn.execute(
        """
        select * from sandbox_execution_dispatches
        where tenant_id = %s and run_id = %s and attempt_id = %s
        """,
        (tenant_id, run_id, attempt_id),
    )
    dispatches = {
        str(row.get("lease_id") or ""): dict(row)
        for row in await dispatch_cursor.fetchall()
    }
    receipt = await (
        await conn.execute(
            """
            select receipt_identity, receipt_json, receipt_recorded_at
            from run_attempt_settlements
            where tenant_id = %s and run_id = %s and attempt_id = %s
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    terminal_receipt = bool(
        receipt is not None
        and (
            receipt.get("receipt_identity") is not None
            or receipt.get("receipt_json") is not None
            or receipt.get("receipt_recorded_at") is not None
        )
    )

    staged = 0
    for lease in leases:
        dispatch = dispatches.get(str(lease.get("id") or ""))
        if (
            lease.get("cancel_delivery_status") != "none"
            or not _authority_is_current(
                run=run,
                latest_attempt_id=str(latest.get("id") or ""),
                attempt=attempt,
                lease=lease,
                dispatch=dispatch,
                lease_unexpired=await _lease_unexpired(conn, str(lease["id"])),
                terminal_receipt=terminal_receipt,
            )
        ):
            continue
        owner_generation = _dispatch_owner_generation(dispatch)
        if owner_generation is None:
            continue
        cursor = await conn.execute(
            """
            update sandbox_leases
            set cancel_delivery_status = 'pending',
                cancel_delivery_owner_generation = %s,
                cancel_delivery_last_error_code = '',
                updated_at = clock_timestamp()
            where id = %s and tenant_id = %s and run_id = %s
              and attempt_id = %s and status = 'active'
              and cancel_delivery_status = 'none'
            returning id
            """,
            (owner_generation, lease["id"], tenant_id, run_id, attempt_id),
        )
        if await cursor.fetchone() is not None:
            staged += 1
    return staged


async def claim_due(
    conn: AsyncConnection,
    *,
    tenant_id: str | None,
    run_id: str | None,
    limit: int,
    stale_after_seconds: int,
    after_lease_key: tuple[str, str, str] | None = None,
    scan_budget: int = _MAX_SCAN_BUDGET,
) -> tuple[list[dict[str, Any]], tuple[str, str, str] | None]:
    """Scan bounded Lease keys, then lock Run, Lease and Attempt rows in order."""
    if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
        raise ValueError("sandbox_cancel_delivery_limit_invalid")
    if type(stale_after_seconds) is not int or stale_after_seconds < 1:
        raise ValueError("sandbox_cancel_delivery_claim_timeout_invalid")
    if type(scan_budget) is not int or not 1 <= scan_budget <= _MAX_SCAN_BUDGET:
        raise ValueError("sandbox_cancel_delivery_scan_budget_invalid")
    if after_lease_key is not None and (
        type(after_lease_key) is not tuple
        or len(after_lease_key) != 3
        or any(not isinstance(value, str) or not value for value in after_lease_key)
    ):
        raise ValueError("sandbox_cancel_delivery_cursor_invalid")

    after_tenant, after_run, after_lease = after_lease_key or (None, None, None)
    candidate_cursor = await conn.execute(
        f"""
        select lease.tenant_id, lease.run_id, lease.id
        from sandbox_leases lease
        join runs
          on runs.tenant_id = lease.tenant_id and runs.id = lease.run_id
        where (%s::text is null or lease.tenant_id = %s)
          and (%s::text is null or lease.run_id = %s)
          and (%s::text is null
            or (lease.tenant_id, lease.run_id, lease.id)
              > (%s::text, %s::text, %s::text))
          and lease.cancel_delivery_status in ('pending', 'retry', 'claimed')
          and (
            lease.cancel_delivery_status = 'pending'
            or (lease.cancel_delivery_status = 'retry'
              and (lease.cancel_delivery_retry_not_before is null
                or lease.cancel_delivery_retry_not_before <= clock_timestamp()))
            or (lease.cancel_delivery_status = 'claimed'
              and (lease.cancel_delivery_claimed_at is null
                or lease.cancel_delivery_claimed_at <= clock_timestamp()
                  - make_interval(secs => %s)))
            or not {_AUTHORITY_CANDIDATE_SQL}
          )
        order by lease.tenant_id asc, lease.run_id asc, lease.id asc
        limit %s
        """,
        (
            tenant_id,
            tenant_id,
            run_id,
            run_id,
            after_tenant,
            after_tenant,
            after_run,
            after_lease,
            stale_after_seconds,
            scan_budget,
        ),
    )
    candidate_leases = [dict(row) for row in await candidate_cursor.fetchall()]
    if not candidate_leases:
        return [], None

    next_cursor = (
        str(candidate_leases[-1]["tenant_id"]),
        str(candidate_leases[-1]["run_id"]),
        str(candidate_leases[-1]["id"]),
    )
    candidate_lease_ids = [str(row["id"]) for row in candidate_leases]
    candidate_pairs = list(dict.fromkeys(
        (str(row["tenant_id"]), str(row["run_id"])) for row in candidate_leases
    ))
    candidate_placeholders = ", ".join("(%s, %s)" for _ in candidate_pairs)
    candidate_params = tuple(value for pair in candidate_pairs for value in pair)
    run_cursor = await conn.execute(
        f"""
        select runs.*
        from runs
        where (runs.tenant_id, runs.id) in ({candidate_placeholders})
        order by runs.tenant_id asc, runs.id asc
        for update of runs skip locked
        """,
        candidate_params,
    )
    runs = [dict(row) for row in await run_cursor.fetchall()]
    if not runs:
        return [], next_cursor

    run_pairs = [(str(row["tenant_id"]), str(row["id"])) for row in runs]
    run_placeholders = ", ".join("(%s, %s)" for _ in run_pairs)
    run_params = tuple(value for pair in run_pairs for value in pair)
    lease_cursor = await conn.execute(
        f"""
        select lease.*,
          (lease.expires_at is not null and lease.expires_at > clock_timestamp())
            as cancel_lease_unexpired,
          (lease.cancel_delivery_status = 'pending'
            or (lease.cancel_delivery_status = 'retry'
              and (lease.cancel_delivery_retry_not_before is null
                or lease.cancel_delivery_retry_not_before <= clock_timestamp()))
            or (lease.cancel_delivery_status = 'claimed'
              and (lease.cancel_delivery_claimed_at is null
                or lease.cancel_delivery_claimed_at <= clock_timestamp()
                  - make_interval(secs => %s)))) as cancel_delivery_due,
          {_AUTHORITY_CANDIDATE_SQL} as cancel_delivery_authority_candidate
        from sandbox_leases lease
        where lease.id = any(%s::text[])
          and (lease.tenant_id, lease.run_id) in ({run_placeholders})
          and lease.cancel_delivery_status in ('pending', 'retry', 'claimed')
          and (
            lease.cancel_delivery_status = 'pending'
            or (lease.cancel_delivery_status = 'retry'
              and (lease.cancel_delivery_retry_not_before is null
                or lease.cancel_delivery_retry_not_before <= clock_timestamp()))
            or (lease.cancel_delivery_status = 'claimed'
              and (lease.cancel_delivery_claimed_at is null
                or lease.cancel_delivery_claimed_at <= clock_timestamp()
                  - make_interval(secs => %s)))
            or not {_AUTHORITY_CANDIDATE_SQL}
          )
        order by lease.tenant_id asc, lease.run_id asc, lease.id asc
        for update of lease skip locked
        limit %s
        """,
        (
            stale_after_seconds,
            candidate_lease_ids,
            *run_params,
            stale_after_seconds,
            scan_budget,
        ),
    )
    leases = [dict(row) for row in await lease_cursor.fetchall()]
    if not leases:
        return [], next_cursor

    candidate_keys = [
        (
            str(lease.get("tenant_id") or ""),
            str(lease.get("run_id") or ""),
            str(lease.get("attempt_id") or ""),
        )
        for lease in leases
        if lease.get("attempt_id")
    ]
    attempt_exists: set[tuple[str, str, str]] = set()
    latest_by_run: dict[tuple[str, str], str] = {}
    dispatch_by_lease: dict[str, dict[str, Any]] = {}
    receipt_by_attempt: dict[tuple[str, str, str], bool] = {}
    context_by_attempt: dict[tuple[str, str, str], Any] = {}
    if candidate_keys:
        key_placeholders = ", ".join("(%s, %s, %s)" for _ in candidate_keys)
        key_params = tuple(value for key in candidate_keys for value in key)
        locked_attempt_cursor = await conn.execute(
            f"""
            select * from run_attempts
            where (tenant_id, run_id, id) in ({key_placeholders})
            order by tenant_id asc, run_id asc, id asc
            for update skip locked
            """,
            key_params,
        )
        attempt_rows = [dict(row) for row in await locked_attempt_cursor.fetchall()]
        attempt_by_key = {
            (str(row["tenant_id"]), str(row["run_id"]), str(row["id"])): row
            for row in attempt_rows
        }

        exists_cursor = await conn.execute(
            f"""
            select tenant_id, run_id, id from run_attempts
            where (tenant_id, run_id, id) in ({key_placeholders})
            """,
            key_params,
        )
        attempt_exists = {
            (str(row["tenant_id"]), str(row["run_id"]), str(row["id"]))
            for row in await exists_cursor.fetchall()
        }

        run_placeholders = ", ".join("(%s, %s)" for _ in run_pairs)
        latest_cursor = await conn.execute(
            f"""
            select distinct on (tenant_id, run_id) tenant_id, run_id, id
            from run_attempts
            where (tenant_id, run_id) in ({run_placeholders})
            order by tenant_id asc, run_id asc, ordinal desc
            """,
            run_params,
        )
        latest_by_run = {
            (str(row["tenant_id"]), str(row["run_id"])): str(row["id"])
            for row in await latest_cursor.fetchall()
        }

        lease_ids = [str(lease["id"]) for lease in leases]
        dispatch_cursor = await conn.execute(
            """
            select * from sandbox_execution_dispatches
            where lease_id = any(%s::text[])
            """,
            (lease_ids,),
        )
        dispatch_by_lease = {
            str(row.get("lease_id") or ""): dict(row)
            for row in await dispatch_cursor.fetchall()
        }

        receipt_cursor = await conn.execute(
            f"""
            select tenant_id, run_id, attempt_id, receipt_identity, receipt_json,
                   receipt_recorded_at, accepted_context_json, intent_recovery_context_json
            from run_attempt_settlements
            where (tenant_id, run_id, attempt_id) in ({key_placeholders})
            """,
            key_params,
        )
        receipt_rows = await receipt_cursor.fetchall()
        context_by_attempt = {
            (str(row["tenant_id"]), str(row["run_id"]), str(row["attempt_id"])):
                row.get("accepted_context_json") or row.get("intent_recovery_context_json")
            for row in receipt_rows
        }
        receipt_by_attempt = {
            (str(row["tenant_id"]), str(row["run_id"]), str(row["attempt_id"])): bool(
                row.get("receipt_identity") is not None
                or row.get("receipt_json") is not None
                or row.get("receipt_recorded_at") is not None
            )
            for row in receipt_rows
        }
    else:
        attempt_by_key = {}

    run_by_key = {
        (str(row["tenant_id"]), str(row["id"])): row
        for row in runs
    }
    claimed: list[dict[str, Any]] = []
    last_scanned_lease_key: tuple[str, str, str] | None = None
    for lease in leases:
        if len(claimed) >= limit:
            break
        key = (
            str(lease.get("tenant_id") or ""),
            str(lease.get("run_id") or ""),
            str(lease.get("attempt_id") or ""),
        )
        last_scanned_lease_key = (
            key[0],
            key[1],
            str(lease.get("id") or ""),
        )
        if not key[2]:
            await _clear_queued_row(conn, lease)
            continue
        if key in attempt_exists and key not in attempt_by_key:
            # The exact Attempt is contended. Leave its lease queued; a later
            # batch will re-check it without waiting behind a foreign lock.
            continue

        run = run_by_key.get((key[0], key[1]))
        attempt = attempt_by_key.get(key)
        dispatch = dispatch_by_lease.get(str(lease.get("id") or ""))
        terminal_receipt = receipt_by_attempt.get(key, False)
        authority_current = attempt is not None and _authority_is_current(
            run=run or {},
            latest_attempt_id=latest_by_run.get((key[0], key[1]), ""),
            attempt=attempt,
            lease=lease,
            dispatch=dispatch,
            lease_unexpired=bool(lease.get("cancel_lease_unexpired")),
            terminal_receipt=terminal_receipt,
        )
        if not authority_current:
            await _clear_queued_row(conn, lease)
            continue
        if not bool(lease.get("cancel_delivery_due")):
            continue

        claim_token = uuid.uuid4().hex
        owner_generation = _dispatch_owner_generation(dispatch)
        if owner_generation is None:
            await _clear_queued_row(conn, lease)
            continue
        claim_cursor = await conn.execute(
            """
            update sandbox_leases
            set cancel_delivery_status = 'claimed',
                cancel_delivery_owner_generation = %s,
                cancel_delivery_claim_token = %s,
                cancel_delivery_claimed_at = clock_timestamp(),
                cancel_delivery_retry_not_before = null,
                cancel_delivery_attempt_count = least(cancel_delivery_attempt_count + 1, 2147483647),
                cancel_delivery_last_error_code = '',
                updated_at = clock_timestamp()
            where id = %s and tenant_id = %s and run_id = %s
              and attempt_id = %s
              and cancel_delivery_status in ('pending', 'retry', 'claimed')
            returning id
            """,
            (
                owner_generation,
                claim_token,
                lease["id"],
                key[0],
                key[1],
                key[2],
            ),
        )
        if await claim_cursor.fetchone() is None:
            continue
        claimed.append(
            {
                **lease,
                "executor_reconciliation_context_json": (
                    context_by_attempt.get(key)
                    or lease.get("executor_reconciliation_context_json")
                    or lease.get("executor_reconciliation_intent_context_json")
                ),
                "lease_id": str(lease["id"]),
                "tenant_id": key[0],
                "workspace_id": str(lease["workspace_id"]),
                "user_id": str(lease["user_id"]),
                "session_id": str(lease["session_id"]),
                "run_id": key[1],
                "attempt_id": key[2],
                "owner_generation": owner_generation,
                "provider": str(lease["provider"]),
                "provider_task_id": str(dispatch["provider_task_id"]),
                "dispatch_status": str(dispatch["status"]),
                "runtime_container_id": str(lease["runtime_container_id"]),
                "runtime_container_name": str(lease["runtime_container_name"]),
                "runtime_executor_url": str(lease["runtime_executor_url"]),
                "runtime_workspace_container_path": str(
                    lease["runtime_workspace_container_path"]
                ),
                "cancel_delivery_claim_token": claim_token,
            }
        )
    return claimed, last_scanned_lease_key or next_cursor


async def record_delivery_result(
    conn: AsyncConnection,
    *,
    claimed_row: Mapping[str, Any],
    accepted: bool,
    error_code: str,
) -> str | None:
    """Commit an ACK or retry only while the claim still owns current authority."""
    tenant_id = str(claimed_row.get("tenant_id") or "")
    run_id = str(claimed_row.get("run_id") or "")
    attempt_id = str(claimed_row.get("attempt_id") or "")
    lease_id = str(claimed_row.get("lease_id") or "")
    claim_token = str(claimed_row.get("cancel_delivery_claim_token") or "")
    if not all((tenant_id, run_id, attempt_id, lease_id, claim_token)):
        return None

    run = await (
        await conn.execute(
            "select * from runs where tenant_id = %s and id = %s for update",
            (tenant_id, run_id),
        )
    ).fetchone()
    lease = await (
        await conn.execute(
            """
            select *, (expires_at is not null and expires_at > clock_timestamp())
                        as cancel_lease_unexpired
            from sandbox_leases
            where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
            for update
            """,
            (lease_id, tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    if run is None or lease is None:
        return None
    lease = dict(lease)
    if (
        lease.get("cancel_delivery_status") != "claimed"
        or str(lease.get("cancel_delivery_claim_token") or "") != claim_token
    ):
        return None

    attempt = await (
        await conn.execute(
            """
            select * from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
            for update skip locked
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    if attempt is None:
        exists = await (
            await conn.execute(
                "select 1 from run_attempts where tenant_id=%s and run_id=%s and id=%s",
                (tenant_id, run_id, attempt_id),
            )
        ).fetchone()
        if exists is not None:
            return None

    latest = await (
        await conn.execute(
            """
            select id from run_attempts
            where tenant_id = %s and run_id = %s
            order by ordinal desc limit 1
            """,
            (tenant_id, run_id),
        )
    ).fetchone()
    dispatch = await (
        await conn.execute(
            """
            select * from sandbox_execution_dispatches
            where tenant_id = %s and run_id = %s and attempt_id = %s and lease_id = %s
            """,
            (tenant_id, run_id, attempt_id, lease_id),
        )
    ).fetchone()
    receipt = await (
        await conn.execute(
            """
            select receipt_identity, receipt_json, receipt_recorded_at
            from run_attempt_settlements
            where tenant_id = %s and run_id = %s and attempt_id = %s
            """,
            (tenant_id, run_id, attempt_id),
        )
    ).fetchone()
    terminal_receipt = bool(
        receipt is not None
        and (
            receipt.get("receipt_identity") is not None
            or receipt.get("receipt_json") is not None
            or receipt.get("receipt_recorded_at") is not None
        )
    )
    if (
        attempt is None
        or latest is None
        or not _claimed_row_matches_lease(claimed_row, lease, dispatch)
        or not _authority_is_current(
            run=dict(run),
            latest_attempt_id=str(latest.get("id") or ""),
            attempt=dict(attempt),
            lease=lease,
            dispatch=dict(dispatch) if dispatch is not None else None,
            lease_unexpired=await _lease_unexpired(conn, lease_id),
            terminal_receipt=terminal_receipt,
        )
        or _dispatch_owner_generation(dispatch) != lease.get("cancel_delivery_owner_generation")
    ):
        await _clear_claim(conn, lease_id, tenant_id, run_id, attempt_id, claim_token)
        return "none"

    if accepted:
        cursor = await conn.execute(
            """
            update sandbox_leases
            set cancel_delivery_status = 'accepted',
                cancel_delivery_claim_token = null,
                cancel_delivery_claimed_at = null,
                cancel_delivery_retry_not_before = null,
                cancel_delivery_accepted_at = clock_timestamp(),
                cancel_delivery_last_error_code = '',
                updated_at = clock_timestamp()
            where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
              and cancel_delivery_status = 'claimed'
              and cancel_delivery_claim_token = %s
            returning cancel_delivery_status
            """,
            (lease_id, tenant_id, run_id, attempt_id, claim_token),
        )
    else:
        safe_error_code = error_code if error_code in {
            "timeout", "provider_error", "invalid_response",
        } else "provider_error"
        cursor = await conn.execute(
            """
            update sandbox_leases
            set cancel_delivery_status = 'retry',
                cancel_delivery_claim_token = null,
                cancel_delivery_claimed_at = null,
                cancel_delivery_retry_not_before = clock_timestamp() + make_interval(
                  secs => least(%s, power(2, least(cancel_delivery_attempt_count - 1, 20))::integer)
                ),
                cancel_delivery_last_error_code = %s,
                updated_at = clock_timestamp()
            where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
              and cancel_delivery_status = 'claimed'
              and cancel_delivery_claim_token = %s
            returning cancel_delivery_status
            """,
            (
                _RETRY_DELAY_CAP_SECONDS,
                safe_error_code,
                lease_id,
                tenant_id,
                run_id,
                attempt_id,
                claim_token,
            ),
        )
    row = await cursor.fetchone()
    return str(row["cancel_delivery_status"]) if row is not None else None


async def _lease_unexpired(conn: AsyncConnection, lease_id: str) -> bool:
    row = await (
        await conn.execute(
            "select expires_at is not null and expires_at > clock_timestamp() as unexpired "
            "from sandbox_leases where id = %s",
            (lease_id,),
        )
    ).fetchone()
    return bool(row and row.get("unexpired"))


async def _clear_queued_row(conn: AsyncConnection, lease: Mapping[str, Any]) -> None:
    await conn.execute(
        """
        update sandbox_leases
        set cancel_delivery_status = 'none',
            cancel_delivery_owner_generation = null,
            cancel_delivery_claim_token = null,
            cancel_delivery_claimed_at = null,
            cancel_delivery_retry_not_before = null,
            cancel_delivery_accepted_at = null,
            cancel_delivery_last_error_code = '',
            updated_at = clock_timestamp()
        where id = %s and tenant_id = %s and run_id = %s
          and cancel_delivery_status in ('pending', 'retry', 'claimed')
        """,
        (lease["id"], lease["tenant_id"], lease["run_id"]),
    )


async def _clear_claim(
    conn: AsyncConnection,
    lease_id: str,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    claim_token: str,
) -> None:
    await conn.execute(
        """
        update sandbox_leases
        set cancel_delivery_status = 'none',
            cancel_delivery_owner_generation = null,
            cancel_delivery_claim_token = null,
            cancel_delivery_claimed_at = null,
            cancel_delivery_retry_not_before = null,
            cancel_delivery_accepted_at = null,
            cancel_delivery_last_error_code = '',
            updated_at = clock_timestamp()
        where id = %s and tenant_id = %s and run_id = %s and attempt_id = %s
          and cancel_delivery_status = 'claimed'
          and cancel_delivery_claim_token = %s
        """,
        (lease_id, tenant_id, run_id, attempt_id, claim_token),
    )
