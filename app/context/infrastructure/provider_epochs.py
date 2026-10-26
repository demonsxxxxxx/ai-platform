"""Scoped PostgreSQL authority for Claude provider epochs and callback batches."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from psycopg import AsyncConnection

from app.context.domain.conversation_authority import (
    extend_source_digest,
    initial_source_digest,
)
from app.context.domain.conversation_recovery import (
    RECOVERY_SCHEMA_VERSION,
    provider_restore_size,
    validate_provider_authority_receipt,
)
from app.context.infrastructure.provider_continuation import (
    get_session_continuation,
    settle_provider_continuation,
)
from app.context.infrastructure.unclaimed_provider_continuation import advance_unclaimed_terminal_generation
from app.context.domain.provider_media import (
    PROVIDER_MEDIA_FORMAT, ProviderMediaTransfer, ProviderMediaProjection, restore_provider_images,
)
from app.context.infrastructure.provider_epoch_verification import provider_epoch_verification_scope
from app.context.infrastructure.provider_images import (
    reserve_provider_images, promote_provider_images, queue_provider_images_for_deletion,
)
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_STRUCTURED_ENTRY_BYTES,
    MAX_PROVIDER_SESSION_STRUCTURED_TRANSCRIPT_BYTES,
    provider_entry_json_bytes,
    MAX_PROVIDER_SESSION_ENTRIES,
    MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
    ProviderSessionConflictError,
    ProviderSessionNotFoundError,
    ProviderSessionScope,
    normalize_provider_entry_batch,
    normalize_provider_subpath,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def batch_digest(subpath: str | None, entries: list[Mapping[str, Any]]) -> str:
    return hashlib.sha256(_canonical([subpath or "", [dict(entry) for entry in entries]])).hexdigest()


def _scope_values(scope: ProviderSessionScope) -> tuple[str, ...]:
    return (scope.tenant_id, scope.workspace_id, scope.user_id, scope.session_id, scope.agent_id, scope.engine)


async def claim_provider_lineage(
    conn: AsyncConnection, *, scope: ProviderSessionScope, run_id: str,
) -> None:
    """Serialize Snapshot admission with the previous Run's terminal commit."""
    values = _scope_values(scope)
    await conn.execute(
        """
        insert into provider_session_heads (
          tenant_id, workspace_id, user_id, session_id, agent_id, engine, active_run_id
        )
        select %s, %s, %s, %s, %s, %s, %s
        from sessions where tenant_id = %s and workspace_id = %s and user_id = %s
          and id = %s and agent_id = %s and status = 'active'
        on conflict (tenant_id, session_id, engine) do nothing
        """,
        (*values, run_id, *values[:5]),
    )
    cursor = await conn.execute(
        """
        select active_run_id from provider_session_heads
        where tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and agent_id = %s and engine = %s
        for update
        """, values,
    )
    head = await cursor.fetchone()
    if head is None:
        raise ProviderSessionNotFoundError("provider_session_scope_invalid")
    owner = head.get("active_run_id")
    if owner is not None and owner != run_id:
        raise ProviderSessionConflictError("provider_session_lineage_busy")
    await conn.execute(
        """
        update provider_session_heads set active_run_id = %s, updated_at = now()
        where tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and agent_id = %s and engine = %s
        """, (run_id, *values),
    )


async def read_provider_coverage(
    conn: AsyncConnection,
    *,
    scope: ProviderSessionScope,
    run_id: str,
    session_generation: int,
) -> dict[str, Any]:
    """Read the committed source coverage protected by the claimed lineage."""
    if type(session_generation) is not int or session_generation < 1:
        raise ProviderSessionConflictError("provider_session_scope_invalid")
    values = _scope_values(scope)
    cursor = await conn.execute(
        """
        select head.current_epoch_id, head.active_run_id, head.recovery_json,
               current_run.session_generation,
               epoch.id as epoch_id, epoch.state, epoch.writer_run_id,
               epoch.writer_attempt_id, epoch.writer_owner_generation,
               epoch.coverage_source_sha256, epoch.coverage_message_count,
               epoch.coverage_through_generation, epoch.coverage_schema_version, epoch.entry_count,
               epoch.transcript_bytes, epoch.structured_bytes,
               exists (
                 select 1 from messages prior_message
                 join sessions prior_session on prior_session.tenant_id = prior_message.tenant_id
                   and prior_session.id = prior_message.session_id
                 where prior_message.tenant_id = head.tenant_id
                   and prior_message.session_id = head.session_id
                   and prior_message.run_id is distinct from current_run.id
                   and prior_session.workspace_id = head.workspace_id
                   and prior_session.user_id = head.user_id
                   and prior_session.agent_id = head.agent_id
                   and not (
                     prior_message.role = 'user' and exists (
                       select 1 from runs prior_run
                       where prior_run.tenant_id = head.tenant_id
                         and prior_run.id = prior_message.run_id
                         and prior_run.workspace_id = head.workspace_id
                         and prior_run.user_id = head.user_id
                         and prior_run.session_id = head.session_id
                         and prior_run.agent_id = head.agent_id
                         and prior_run.status in ('failed', 'cancelled')
                         and (
                           (prior_run.started_at is null and not exists (
                             select 1 from run_attempts prior_attempt
                             where prior_attempt.tenant_id = prior_run.tenant_id
                               and prior_attempt.run_id = prior_run.id
                               and (prior_attempt.started_at is not null
                                 or prior_attempt.status not in ('failed', 'cancelled'))
                           ))
                           or exists (
                             select 1 from provider_turn_receipts unaccepted
                             join provider_session_epochs stopped_epoch on stopped_epoch.id = unaccepted.epoch_id
                               and stopped_epoch.tenant_id = head.tenant_id
                               and stopped_epoch.workspace_id = head.workspace_id
                               and stopped_epoch.user_id = head.user_id
                               and stopped_epoch.session_id = head.session_id
                               and stopped_epoch.agent_id = head.agent_id and stopped_epoch.engine = head.engine
                             where unaccepted.tenant_id = prior_run.tenant_id and unaccepted.state = 'failed'
                               and unaccepted.run_id = prior_run.id
                               and unaccepted.user_message_id = prior_message.id
                               and stopped_epoch.next_sequence = unaccepted.start_sequence
                               and stopped_epoch.recovery_json->>'source_run_id' = prior_run.id
                               and stopped_epoch.recovery_json->>'source_attempt_id' = unaccepted.attempt_id
                               and stopped_epoch.recovery_json->>'input_accepted' = 'false'
                               and stopped_epoch.recovery_json->>'stop_evidence' in ('provider_stopped', 'provider_not_found')
                           )
                           or exists (
                             select 1 from run_attempts unclaimed_attempt
                             join provider_session_epochs unused_epoch on unused_epoch.tenant_id = head.tenant_id
                               and unused_epoch.workspace_id = head.workspace_id and unused_epoch.user_id = head.user_id
                               and unused_epoch.session_id = head.session_id and unused_epoch.agent_id = head.agent_id
                               and unused_epoch.engine = head.engine and unused_epoch.state = 'closed'
                               and unused_epoch.id = unclaimed_attempt.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id'
                             where unclaimed_attempt.tenant_id = prior_run.tenant_id and unclaimed_attempt.run_id = prior_run.id
                               and unused_epoch.recovery_json->>'settlement_kind' = 'unclaimed_unaccepted'
                               and unused_epoch.recovery_json->>'source_run_id' = prior_run.id
                               and unused_epoch.recovery_json->>'source_attempt_id' = unclaimed_attempt.id
                               and unused_epoch.recovery_json->>'current_message_id' = prior_message.id
                               and unused_epoch.recovery_json->>'execution_spec_sha256' = unclaimed_attempt.execution_spec_sha256
                               and unused_epoch.recovery_json->>'owner_generation' = unclaimed_attempt.owner_generation::text
                               and unused_epoch.recovery_json->>'input_accepted' = 'false'
                               and unused_epoch.recovery_json->>'ack_unknown' = 'false'
                               and unused_epoch.recovery_json->>'stop_evidence' in ('provider_stopped', 'provider_not_found', 'provider_not_created')
                           )
                         )
                     )
                   )
               ) as has_prior_messages
        from provider_session_heads head
        join runs current_run on current_run.tenant_id = head.tenant_id
          and current_run.workspace_id = head.workspace_id
          and current_run.user_id = head.user_id
          and current_run.session_id = head.session_id
          and current_run.agent_id = head.agent_id
        left join provider_session_epochs epoch on epoch.id = head.current_epoch_id
          and epoch.tenant_id = head.tenant_id and epoch.workspace_id = head.workspace_id
          and epoch.user_id = head.user_id and epoch.session_id = head.session_id
          and epoch.agent_id = head.agent_id and epoch.engine = head.engine
        where head.tenant_id = %s and head.workspace_id = %s and head.user_id = %s
          and head.session_id = %s and head.agent_id = %s and head.engine = %s
          and current_run.id = %s
        """,
        (*values, run_id),
    )
    row = await cursor.fetchone()
    if row is None or row["active_run_id"] != run_id:
        raise ProviderSessionConflictError("provider_session_lineage_busy")
    if row["session_generation"] != session_generation:
        raise ProviderSessionConflictError("provider_session_scope_invalid")
    _require_continuation_available(row.get("recovery_json"))
    if row["current_epoch_id"] is None:
        if row["has_prior_messages"]:
            raise ProviderSessionConflictError("provider_session_requires_new_conversation")
        return {
            "source_sha256": initial_source_digest({
                "tenant_id": scope.tenant_id,
                "workspace_id": scope.workspace_id,
                "user_id": scope.user_id,
                "session_id": scope.session_id,
                "agent_id": scope.agent_id,
            }),
            "message_count": 0,
            "coverage_through_generation": None,
        }
    digest = row["coverage_source_sha256"]
    count = row["coverage_message_count"]
    coverage_generation = row["coverage_through_generation"]
    restore_count, restore_bytes = provider_restore_size(row, row.get("recovery_json"))
    if (
        row["epoch_id"] != row["current_epoch_id"]
        or row["state"] != "ready"
        or row["writer_run_id"] is not None
        or row["writer_attempt_id"] is not None
        or row["writer_owner_generation"] is not None
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or type(count) is not int
        or count < 0
        or type(coverage_generation) is not int
        or coverage_generation < 1
        or coverage_generation >= session_generation
        or type(restore_count) is not int or restore_count < 0
        or restore_count > MAX_PROVIDER_SESSION_ENTRIES - 128
        or type(restore_bytes) is not int or restore_bytes < 0
        or restore_bytes > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES - 16 * 1024 * 1024
        or row["structured_bytes"] > MAX_PROVIDER_SESSION_STRUCTURED_TRANSCRIPT_BYTES - 2 * 1024 * 1024
    ):
        raise ProviderSessionConflictError("provider_session_requires_new_conversation")
    return {
        "source_sha256": digest,
        "message_count": count,
        "coverage_through_generation": coverage_generation,
        "authority_schema_version": row.get("coverage_schema_version", "ai-platform.conversation-authority.v2"),
        "provider_epoch_id": row["epoch_id"],
        "continuation": row.get("recovery_json"),
    }


async def release_provider_lineage(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    """Resolve an owned epoch and claim within the Runs terminal transaction."""
    cursor = await conn.execute(
        """
        select runs.status, head.tenant_id, head.workspace_id, head.user_id,
               head.session_id, head.agent_id, head.engine, head.active_attempt_id,
               epoch.id as epoch_id, epoch.state as epoch_state,
               epoch.next_sequence, epoch.coverage_source_sha256, epoch.recovery_json,
               turn.start_sequence, turn.state as turn_state,
               exists (select 1 from run_attempts started_attempt
                       where started_attempt.tenant_id = runs.tenant_id
                         and started_attempt.run_id = runs.id
                         and started_attempt.started_at is not null) as has_started_attempt
        from runs
        join provider_session_heads head on head.tenant_id = runs.tenant_id
          and head.workspace_id = runs.workspace_id and head.user_id = runs.user_id
          and head.session_id = runs.session_id and head.agent_id = runs.agent_id
          and head.active_run_id = runs.id
        left join provider_session_epochs epoch on epoch.tenant_id = head.tenant_id
          and epoch.session_id = head.session_id and epoch.engine = head.engine
          and epoch.writer_run_id = runs.id and epoch.writer_attempt_id = head.active_attempt_id
        left join provider_turn_receipts turn on turn.tenant_id = runs.tenant_id
          and turn.run_id = runs.id and turn.attempt_id = head.active_attempt_id
          and turn.epoch_id = epoch.id
        where runs.tenant_id = %s and runs.id = %s
          and runs.status in ('succeeded', 'failed', 'cancelled')
        for update of head
        """, (tenant_id, run_id),
    )
    head = await cursor.fetchone()
    if head is None:
        return
    if head["epoch_id"] is None and head.get("has_started_attempt"):
        from app.context.infrastructure.unclaimed_provider_continuation import unclaimed_lineage_release_proven

        if not await unclaimed_lineage_release_proven(conn, head=head, run_id=run_id):
            return
    if head["epoch_id"] is not None:
        recovery = head.get("recovery_json")
        if not isinstance(recovery, dict) or recovery.get("stop_evidence") not in {"provider_stopped", "provider_not_found"}:
            return
        if head["status"] == "succeeded":
            if head["turn_state"] != "committed":
                raise ProviderSessionConflictError("provider_session_coverage_uncommitted")
            await conn.execute(
                """
                update provider_session_epochs set writer_run_id = null,
                  writer_attempt_id = null, writer_owner_generation = null, updated_at = now()
                where id = %s and writer_run_id = %s and writer_attempt_id = %s
                """, (head["epoch_id"], run_id, head["active_attempt_id"]),
            )
        else:
            await conn.execute(
                """
                update provider_session_epochs set writer_run_id = null,
                  writer_attempt_id = null, writer_owner_generation = null, updated_at = now()
                where id = %s and writer_run_id = %s and writer_attempt_id = %s
                """, (head["epoch_id"], run_id, head["active_attempt_id"]),
            )
    await conn.execute(
        """
        update provider_session_heads set active_run_id = null, active_attempt_id = null,
          updated_at = now() where tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and agent_id = %s and engine = %s and active_run_id = %s
        """, (head["tenant_id"], head["workspace_id"], head["user_id"],
              head["session_id"], head["agent_id"], head["engine"], run_id),
    )


async def _locked_callback_epoch(
    conn: AsyncConnection, *, scope: ProviderSessionScope, run_id: str,
    attempt_id: str, provider_session_id: str, allow_cancel_tail: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        provider_uuid = str(uuid.UUID(provider_session_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProviderSessionConflictError("provider_session_identity_mismatch") from exc
    values = _scope_values(scope)
    # Serialize deletion with both phases; a deleted session cannot gain new images.
    cursor = await conn.execute(
        "select status from sessions where tenant_id = %s and id = %s for update",
        (scope.tenant_id, scope.session_id),
    )
    session = await cursor.fetchone()
    if session is None or session["status"] != "active":
        raise ProviderSessionConflictError("provider_session_epoch_unavailable")
    cursor = await conn.execute(
        """
        select head.current_epoch_id, head.active_run_id, head.active_attempt_id,
               epoch.id, epoch.provider_session_id, epoch.state, epoch.next_sequence,
               epoch.entry_count, epoch.transcript_bytes, epoch.structured_bytes, epoch.coverage_source_sha256,
               epoch.source_epoch_id, epoch.source_final_sequence,
               epoch.writer_run_id, epoch.writer_attempt_id, epoch.writer_owner_generation
        from provider_session_heads head
        join provider_session_epochs epoch on epoch.tenant_id = head.tenant_id
          and epoch.workspace_id = head.workspace_id and epoch.user_id = head.user_id
          and epoch.session_id = head.session_id and epoch.agent_id = head.agent_id
          and epoch.engine = head.engine and epoch.provider_session_id = %s::uuid
          and epoch.id = (
            select frozen.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id'
            from run_attempts frozen where frozen.tenant_id = head.tenant_id
              and frozen.run_id = %s and frozen.id = %s
          )
        where head.tenant_id = %s and head.workspace_id = %s and head.user_id = %s
          and head.session_id = %s and head.agent_id = %s and head.engine = %s
          and head.active_run_id = %s
        for update of head, epoch
        """, (provider_uuid, run_id, attempt_id, *values, run_id),
    )
    epoch = await cursor.fetchone()
    if epoch is None or epoch["state"] in {"dirty", "closed"}:
        raise ProviderSessionConflictError("provider_session_epoch_unavailable")
    cursor = await conn.execute(
        """
        select attempt.owner_generation, attempt.execution_spec_sha256,
               attempt.status, attempt.owner_kind,
               attempt.execution_spec_json->'context_pack'->'conversation_context' as conversation_context
        from run_attempts attempt
        join runs owning_run on owning_run.tenant_id = attempt.tenant_id
          and owning_run.id = attempt.run_id and owning_run.status = 'running'
        join sandbox_leases lease on lease.tenant_id = attempt.tenant_id
          and lease.run_id = attempt.run_id and lease.attempt_id = attempt.id
        where attempt.tenant_id = %s and attempt.run_id = %s and attempt.id = %s
          -- v2 froze an input capacity, v3 the window; both stay bound here.
          and attempt.execution_spec_schema_version in (
            'ai-platform.execution-spec.v2', 'ai-platform.execution-spec.v3')
          and (
            (attempt.status = 'running'
             and lease.lease_payload_json->>'owner_generation' = attempt.owner_generation::text)
            or (%s and attempt.status = 'cancel_requested' and attempt.owner_kind = 'queue_worker'
                and owning_run.cancel_requested_at is not null
                and lease.lease_payload_json->>'owner_generation' = (attempt.owner_generation - 1)::text)
          )
          and lease.status = 'active' and lease.released_at is null
          and lease.expires_at is not null and lease.expires_at > clock_timestamp()
        """, (scope.tenant_id, run_id, attempt_id, allow_cancel_tail),
    )
    attempt = await cursor.fetchone()
    private = attempt.get("conversation_context") if isinstance(attempt, dict) else None
    if (
        attempt is None or not isinstance(private, dict)
        or private.get("provider_epoch_id") != epoch["id"]
        or private.get("provider_session_id") != provider_uuid
        or (private.get("source_epoch_id") is not None and (
            private.get("source_epoch_id") != epoch.get("source_epoch_id")
            or private.get("source_final_sequence") != epoch.get("source_final_sequence")
        ))
        or private.get("execution_mode") not in {"native_resume", "empty_start"}
        or (private["execution_mode"] == "native_resume" and (
            epoch["coverage_source_sha256"] != private.get("source_sha256")
        ))
        or (private["execution_mode"] != "native_resume" and epoch["state"] != "bootstrapping" and epoch["writer_run_id"] != run_id)
    ):
        raise ProviderSessionConflictError("provider_session_spec_mismatch")
    generation = attempt.get("owner_generation")
    if type(generation) is not int or generation < 1:
        raise ProviderSessionConflictError("provider_session_owner_invalid")
    if attempt.get("status") == "cancel_requested":
        # Cancellation revokes tool/input authority and advances generation.
        # Only the already-claimed writer may flush its bounded transcript
        # until that original lease expires; this cannot start a new writer.
        if (not allow_cancel_tail or epoch["active_attempt_id"] != attempt_id
            or epoch["writer_run_id"] != run_id or epoch["writer_attempt_id"] != attempt_id
            or epoch["writer_owner_generation"] != generation - 1):
            raise ProviderSessionConflictError("provider_session_writer_conflict")
        generation -= 1
        attempt = {**attempt, "owner_generation": generation}
    if epoch["writer_run_id"] is not None and (
        epoch["writer_run_id"], epoch["writer_attempt_id"], epoch["writer_owner_generation"]
    ) != (run_id, attempt_id, generation):
        raise ProviderSessionConflictError("provider_session_writer_conflict")
    if epoch["active_attempt_id"] not in (None, attempt_id):
        raise ProviderSessionConflictError("provider_session_writer_conflict")
    if epoch["writer_run_id"] is None:
        await conn.execute(
            """
            update provider_session_epochs set writer_run_id = %s, writer_attempt_id = %s,
              writer_owner_generation = %s, state = 'active', updated_at = now()
            where id = %s and state in ('ready', 'bootstrapping')
            """, (run_id, attempt_id, generation, epoch["id"]),
        )
        await conn.execute(
            """
            update provider_session_heads set active_attempt_id = %s, updated_at = now()
            where tenant_id = %s and workspace_id = %s and user_id = %s
              and session_id = %s and agent_id = %s and engine = %s and active_run_id = %s
            """, (attempt_id, *values, run_id),
        )
        cursor = await conn.execute(
            """
            insert into provider_turn_receipts (
              id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
              epoch_id, run_id, attempt_id, execution_spec_sha256,
              bootstrap_source_sha256, start_sequence, user_message_id,
              prior_coverage_sha256, state
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'writing')
            on conflict (tenant_id, run_id, attempt_id) do nothing
            returning epoch_id, execution_spec_sha256, start_sequence, user_message_id,
                      prior_coverage_sha256, bootstrap_source_sha256, state
            """, (f"ptr_{uuid.uuid4().hex}", *values, epoch["id"], run_id,
                  attempt_id, attempt["execution_spec_sha256"],
                  private["source_sha256"] if private["execution_mode"] != "native_resume" else None,
                  epoch["next_sequence"], private.get("current_message_id"),
                  epoch["coverage_source_sha256"]),
        )
        turn = await cursor.fetchone()
    else:
        cursor = await conn.execute(
            """
            select epoch_id, execution_spec_sha256, start_sequence, user_message_id,
                   prior_coverage_sha256, bootstrap_source_sha256, state
            from provider_turn_receipts where tenant_id = %s and run_id = %s and attempt_id = %s
            """, (scope.tenant_id, run_id, attempt_id),
        )
        turn = await cursor.fetchone()
    if (turn is None or turn["state"] != "writing" or turn["epoch_id"] != epoch["id"]
        or turn["execution_spec_sha256"] != attempt["execution_spec_sha256"]
        or turn["user_message_id"] != private.get("current_message_id")
        or turn["prior_coverage_sha256"] != epoch["coverage_source_sha256"]):
        raise ProviderSessionConflictError("provider_session_turn_conflict")
    return dict(epoch), dict(attempt), private


async def _matching_append_receipt(conn, *, epoch, expected_sequence, digest, count,
                                   run_id, attempt_id, owner_generation):
    if type(expected_sequence) is not int or expected_sequence < 1:
        raise ProviderSessionConflictError("provider_session_append_sequence_invalid")
    if expected_sequence == epoch["next_sequence"]:
        return None
    cursor = await conn.execute(
        "select batch_sha256, entry_count, last_sequence, run_id, attempt_id, owner_generation "
        "from provider_session_append_receipts where epoch_id = %s and expected_sequence = %s",
        (epoch["id"], expected_sequence),
    )
    previous = await cursor.fetchone()
    if previous is None or (
        previous["batch_sha256"], previous["entry_count"], previous["run_id"],
        previous["attempt_id"], previous["owner_generation"]
    ) != (digest, count, run_id, attempt_id, owner_generation):
        raise ProviderSessionConflictError("provider_session_append_conflict")
    return previous


def _check_append_capacity(epoch, *, count, batch_bytes, structured_bytes):
    if (epoch["entry_count"] + count > MAX_PROVIDER_SESSION_ENTRIES
        or epoch["transcript_bytes"] + batch_bytes > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES
        or epoch["structured_bytes"] + structured_bytes > MAX_PROVIDER_SESSION_STRUCTURED_TRANSCRIPT_BYTES):
        raise ProviderSessionConflictError("provider_session_transcript_too_large")


async def prepare_callback_media(
    conn: AsyncConnection, *, projection: ProviderMediaProjection | None,
    tenant_id: str, workspace_id: str, user_id: str, session_id: str, agent_id: str,
    run_id: str, attempt_id: str, provider_session_id: str, action: str,
    entries: list[dict], subpath: str | None, expected_sequence: int | None,
    provider_epoch_id: str | None = None,
) -> ProviderMediaTransfer:
    scope = ProviderSessionScope(tenant_id, workspace_id, user_id, session_id, agent_id)
    epoch, attempt, _private = await _locked_callback_epoch(
        conn, scope=scope, run_id=run_id, attempt_id=attempt_id,
        provider_session_id=provider_session_id, allow_cancel_tail=action == "append",
    )
    if provider_epoch_id is not None and provider_epoch_id != epoch["id"]:
        raise ProviderSessionConflictError("provider_session_spec_mismatch")
    if action == "append":
        batch, batch_bytes = normalize_provider_entry_batch(entries, subpath=subpath)
        digest = batch_digest(normalize_provider_subpath(subpath), [item.entry for item in batch])
        previous = await _matching_append_receipt(
            conn, epoch=epoch, expected_sequence=expected_sequence, digest=digest, count=len(batch),
            run_id=run_id, attempt_id=attempt_id, owner_generation=attempt["owner_generation"],
        )
        if previous is not None:
            return ProviderMediaTransfer(epoch["id"], epoch["next_sequence"], attempt["owner_generation"], [])
        if projection is None:
            raise ProviderSessionConflictError("provider_session_integrity_mismatch")
        _check_append_capacity(epoch, count=len(batch), batch_bytes=batch_bytes,
                               structured_bytes=sum(provider_entry_json_bytes(entry) for entry in projection.entries))
    return await reserve_provider_images(
        conn, scope=scope, epoch=epoch, attempt=attempt, run_id=run_id,
        attempt_id=attempt_id, projection=projection,
    )


async def callback_provider_epoch(
    conn: AsyncConnection, *, tenant_id: str, workspace_id: str, user_id: str,
    session_id: str, agent_id: str, run_id: str, attempt_id: str,
    provider_session_id: str, action: str, subpath: str | None,
    entries: list[dict[str, Any]], expected_sequence: int | None,
    include_child_entries: bool = False,
    provider_epoch_id: str | None = None,
    media: ProviderMediaTransfer | None = None,
) -> dict[str, Any]:
    scope = ProviderSessionScope(tenant_id, workspace_id, user_id, session_id, agent_id)
    epoch, attempt, private = await _locked_callback_epoch(
        conn, scope=scope, run_id=run_id, attempt_id=attempt_id,
        provider_session_id=provider_session_id, allow_cancel_tail=action == "append",
    )
    if provider_epoch_id is not None and provider_epoch_id != epoch["id"]:
        raise ProviderSessionConflictError("provider_session_spec_mismatch")
    if media is not None and (
        not media.completed or media.epoch_id != epoch["id"]
        or media.owner_generation != attempt["owner_generation"]
        or (action != "append" and media.next_sequence != epoch["next_sequence"])
    ):
        raise ProviderSessionConflictError("provider_session_image_snapshot_conflict")
    path = normalize_provider_subpath(subpath) or ""
    if action in {"load", "list_subkeys"}:
        if media is not None:
            current = await reserve_provider_images(
                conn, scope=scope, epoch=epoch, attempt=attempt, run_id=run_id,
                attempt_id=attempt_id, projection=None,
            )
            if ({obj["id"]: obj for obj in current.objects}
                != {obj["id"]: obj for obj in media.objects}):
                raise ProviderSessionConflictError("provider_session_image_snapshot_conflict")
        rows = await _verified_provider_entries(conn, epoch=epoch, media=media)
        if action == "list_subkeys":
            result = {"action": action,
                      "subpaths": sorted({row["subpath"] for row in rows if row["subpath"]}),
                      "next_sequence": epoch["next_sequence"]}
            if include_child_entries:
                children: dict[str, list[dict[str, Any]]] = {}
                for row in rows:
                    if row["subpath"]:
                        children.setdefault(row["subpath"], []).append(row["entry_json"])
                result["child_entries"] = children
            return result
        return {"action": action,
                "entries": [row["entry_json"] for row in rows if row["subpath"] == path],
                "next_sequence": epoch["next_sequence"]}
    if action != "append" or type(expected_sequence) is not int or expected_sequence < 1:
        raise ProviderSessionConflictError("provider_session_append_sequence_invalid")
    try:
        batch, batch_bytes = normalize_provider_entry_batch(entries, subpath=path)
        digest = batch_digest(path, [item.entry for item in batch])
    except ValueError as exc:
        raise ProviderSessionConflictError(str(exc)) from exc
    previous = await _matching_append_receipt(
        conn, epoch=epoch, expected_sequence=expected_sequence, digest=digest, count=len(batch),
        run_id=run_id, attempt_id=attempt_id, owner_generation=attempt["owner_generation"],
    )
    if previous is not None:
        return {"action": action, "entry_count": len(batch), "last_sequence": previous["last_sequence"],
                "next_sequence": epoch["next_sequence"]}
    projection = media.projection if media is not None else None
    stored_entries = projection.entries if projection is not None else [item.entry for item in batch]
    references = projection.references if projection is not None else [[] for _ in batch]
    if len(stored_entries) != len(batch) or len(references) != len(batch):
        raise ProviderSessionConflictError("provider_session_integrity_mismatch")
    structured_bytes = 0
    for entry, refs, original in zip(stored_entries, references, batch, strict=True):
        restored = restore_provider_images(entry, refs, values=media.values if media else {})
        if _canonical(restored) != _canonical(original.entry):
            raise ProviderSessionConflictError("provider_session_integrity_mismatch")
        size = provider_entry_json_bytes(entry)
        if size > MAX_PROVIDER_SESSION_STRUCTURED_ENTRY_BYTES:
            raise ProviderSessionConflictError("provider_session_entry_too_large")
        structured_bytes += size
    _check_append_capacity(epoch, count=len(batch), batch_bytes=batch_bytes, structured_bytes=structured_bytes)
    if media is not None:
        await promote_provider_images(conn, transfer=media, scope=scope, run_id=run_id, attempt_id=attempt_id)
    for offset, item in enumerate(batch):
        canonical = _canonical(stored_entries[offset]).decode("utf-8")
        refs = references[offset]
        await conn.execute(
            """
            insert into provider_session_entries (
              id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
              epoch_id, subpath, sequence, sdk_entry_uuid, entry_json, entry_canonical_json,
              image_references, image_format
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::json, %s::jsonb, %s)
            """, (f"pse_{uuid.uuid4().hex}", *_scope_values(scope), epoch["id"], path,
                  expected_sequence + offset, item.sdk_entry_uuid, canonical, canonical,
                  json.dumps(refs), PROVIDER_MEDIA_FORMAT if refs else None),
        )
    last = expected_sequence + len(batch) - 1
    await conn.execute(
        """
        insert into provider_session_append_receipts (
          epoch_id, expected_sequence, batch_sha256, entry_count, last_sequence,
          run_id, attempt_id, owner_generation
        ) values (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (epoch["id"], expected_sequence, digest, len(batch), last,
              run_id, attempt_id, attempt["owner_generation"]),
    )
    await conn.execute(
        """
        update provider_session_epochs set next_sequence = %s, entry_count = entry_count + %s,
          transcript_bytes = transcript_bytes + %s, structured_bytes = structured_bytes + %s,
          updated_at = now() where id = %s
        """, (last + 1, len(batch), batch_bytes, structured_bytes, epoch["id"]),
    )
    return {"action": action, "entry_count": len(batch), "last_sequence": last,
            "next_sequence": last + 1}


async def _verified_provider_entries(
    conn: AsyncConnection, *, epoch: Mapping[str, Any], media: ProviderMediaTransfer | None = None,
) -> list[dict[str, Any]]:
    from app.context.infrastructure.provider_epoch_ledger import read_provider_ledger, verify_provider_ledger

    rows, receipts = await read_provider_ledger(conn, epoch=epoch)
    return verify_provider_ledger(epoch=epoch, rows=rows, receipts=receipts, media=media)

async def verified_provider_prefix(
    conn: AsyncConnection, *, epoch: Mapping[str, Any], final_sequence: int,
) -> tuple[list[dict[str, Any]], int]:
    """Restore only at a complete ACK boundary after verifying the whole ledger."""
    from app.context.infrastructure.provider_epoch_ledger import read_provider_ledger, verify_provider_ledger
    from app.context.infrastructure.provider_epoch_verification import checked_epoch_proof

    rows, receipts = await read_provider_ledger(conn, epoch=epoch)
    proof = None
    if any(row.get("image_references") for row in rows):
        proof = await checked_epoch_proof(conn, epoch=epoch, rows=rows, receipts=receipts)
    else:
        rows = verify_provider_ledger(epoch=epoch, rows=rows, receipts=receipts)
    if type(final_sequence) is not int or not 0 <= final_sequence < epoch["next_sequence"]:
        raise ProviderSessionConflictError("provider_session_terminal_receipt_invalid")
    if final_sequence:
        cursor = await conn.execute(
            "select last_sequence from provider_session_append_receipts "
            "where epoch_id = %s and last_sequence = %s", (epoch["id"], final_sequence),
        )
        if await cursor.fetchone() is None:
            raise ProviderSessionConflictError("provider_session_terminal_receipt_invalid")
    prefix = rows[:final_sequence]
    total = proof.prefix_bytes[final_sequence] if proof else sum(len(_canonical(row["entry_json"])) for row in prefix)
    return prefix, total


def _require_continuation_available(recovery: object) -> None:
    if recovery is None:
        return  # Existing successfully committed v2 epochs predate recovery receipts.
    if (not isinstance(recovery, dict)
        or recovery.get("schema_version") != RECOVERY_SCHEMA_VERSION
        or recovery.get("state") not in ("resumable", "recovery_candidate", "blocked")):
        raise ProviderSessionConflictError("provider_session_native_unavailable")
    if recovery["state"] == "blocked":
        reason = recovery.get("reason_code")
        if not isinstance(reason, str) or reason not in {"writer_unverified", "workspace_unsaved", "native_unavailable"}:
            reason = "native_unavailable"
        raise ProviderSessionConflictError("provider_session_" + reason)


def _has_ambiguous_legacy_number(value: object) -> bool:
    """Classify unprovable legacy bytes without accepting or enumerating variants."""
    if isinstance(value, dict):
        return any(_has_ambiguous_legacy_number(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_ambiguous_legacy_number(item) for item in value)
    return ((type(value) is float and value == 0.0)
            or (type(value) is int and abs(value) >= 10**16))


async def prepare_provider_epoch(
    conn: AsyncConnection, *, scope: ProviderSessionScope, run_id: str,
    conversation_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose a single frozen mode from the scoped head and verified source."""
    digest = conversation_context.get("source_sha256")
    count = conversation_context.get("message_count")
    if (not isinstance(digest, str) or len(digest) != 64 or type(count) is not int
        or count < 0 or not isinstance(conversation_context.get("messages"), list)):
        raise ProviderSessionConflictError("provider_session_source_invalid")
    values = _scope_values(scope)
    cursor = await conn.execute(
        """
        select head.current_epoch_id, head.next_epoch_number, head.active_run_id,
               head.recovery_json as head_recovery_json,
               epoch.id, epoch.next_sequence, epoch.recovery_json, epoch.coverage_schema_version,
               epoch.coverage_through_generation,
               epoch.provider_session_id, epoch.state, epoch.coverage_source_sha256,
               epoch.coverage_message_count, epoch.entry_count, epoch.transcript_bytes, epoch.structured_bytes,
               epoch.writer_run_id, epoch.writer_attempt_id, epoch.writer_owner_generation
        from provider_session_heads head
        left join provider_session_epochs epoch on epoch.id = head.current_epoch_id
          and epoch.tenant_id = head.tenant_id and epoch.workspace_id = head.workspace_id
          and epoch.user_id = head.user_id and epoch.session_id = head.session_id
          and epoch.agent_id = head.agent_id and epoch.engine = head.engine
        where head.tenant_id = %s and head.workspace_id = %s and head.user_id = %s
          and head.session_id = %s and head.agent_id = %s and head.engine = %s
        for update of head
        """, values,
    )
    head = await cursor.fetchone()
    if head is None or head["active_run_id"] != run_id:
        raise ProviderSessionConflictError("provider_session_lineage_busy")
    cursor = await conn.execute(
        """
        select execution_spec_json->'context_pack'->'conversation_context' as frozen_context
        from run_attempts where tenant_id = %s and run_id = %s
          and status = 'running' and execution_spec_schema_version in (
            'ai-platform.execution-spec.v2', 'ai-platform.execution-spec.v3')
        order by ordinal desc limit 1
        """, (scope.tenant_id, run_id),
    )
    existing_attempt = await cursor.fetchone()
    if existing_attempt is not None:
        frozen = existing_attempt.get("frozen_context")
        if (not isinstance(frozen, dict) or frozen.get("source_sha256") != digest
            or frozen.get("current_message_id") != conversation_context.get("current_message_id")
            or frozen.get("message_count") != count
            or frozen.get("authority_schema_version", "ai-platform.conversation-authority.v2") != conversation_context.get("authority_schema_version", "ai-platform.conversation-authority.v2")
            or frozen.get("provider_epoch_id") is None):
            raise ProviderSessionConflictError("provider_session_spec_mismatch")
        return dict(frozen)
    _require_continuation_available(head.get("head_recovery_json"))
    if head["current_epoch_id"] is not None:
        restore_count, restore_bytes = provider_restore_size(head, head.get("recovery_json"))
        if (
            head["state"] != "ready"
            or head["writer_run_id"] is not None
            or head["writer_attempt_id"] is not None
            or head["writer_owner_generation"] is not None
            or head["coverage_source_sha256"] != digest
            or head["coverage_message_count"] != count
            or head.get("coverage_schema_version", "ai-platform.conversation-authority.v2") != conversation_context.get("authority_schema_version", "ai-platform.conversation-authority.v2")
            or type(restore_count) is not int or restore_count < 0
            or restore_count > MAX_PROVIDER_SESSION_ENTRIES - 128
            or type(restore_bytes) is not int or restore_bytes < 0
            or restore_bytes > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES - 16 * 1024 * 1024
            or head["structured_bytes"] > MAX_PROVIDER_SESSION_STRUCTURED_TRANSCRIPT_BYTES - 2 * 1024 * 1024
        ):
            raise ProviderSessionConflictError("provider_session_requires_new_conversation")
        source_id = head["current_epoch_id"]
        if (conversation_context.get("source_epoch_id") is not None
            and conversation_context["source_epoch_id"] != source_id):
            raise ProviderSessionConflictError("provider_session_spec_mismatch")
        source = {**head, **dict(zip(("tenant_id", "workspace_id", "user_id", "session_id", "agent_id"), values)), "id": source_id}
        recovery = head.get("recovery_json") or {}
        if recovery and (recovery.get("provider_epoch_id") != source_id
                         or recovery.get("workspace_saved") is not True):
            raise ProviderSessionConflictError("provider_session_native_unavailable")
        final_sequence = recovery.get("final_sequence", head["next_sequence"] - 1)
        prefix, prefix_bytes = await verified_provider_prefix(
            conn, epoch=source, final_sequence=final_sequence,
        )
        if len(prefix) != restore_count or prefix_bytes != restore_bytes:
            raise ProviderSessionConflictError("provider_session_integrity_mismatch")
        epoch_id = f"pe_{uuid.uuid4().hex}"
        provider_id = str(head["provider_session_id"])
        mode = "native_resume"
        prefix_structured_bytes = sum(
            len(row["entry_canonical_json"].encode("utf-8")) if row.get("entry_canonical_json") is not None
            else provider_entry_json_bytes(row["entry_json"]) for row in prefix
        )
        await conn.execute(
            """
            insert into provider_session_epochs (
              id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
              epoch_number, provider_session_id, state, source_epoch_id, source_final_sequence,
              next_sequence, entry_count, transcript_bytes, structured_bytes, coverage_source_sha256,
              coverage_through_generation, coverage_message_count, coverage_schema_version
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, 'ready', %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s)
            """, (epoch_id, *values, head["next_epoch_number"], provider_id, source_id,
                  final_sequence, final_sequence + 1, len(prefix), prefix_bytes, prefix_structured_bytes, digest,
                  head["coverage_through_generation"], count, head["coverage_schema_version"]),
        )
        await conn.execute(
            """
            insert into provider_session_entries (
              id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
              epoch_id, subpath, sequence, sdk_entry_uuid, entry_json, entry_canonical_json, image_format, image_references, created_at
            ) select %s || sequence::text, tenant_id, workspace_id, user_id, session_id, agent_id,
              engine, %s, subpath, sequence, sdk_entry_uuid, entry_json, entry_canonical_json, image_format, image_references, created_at
            from provider_session_entries where epoch_id = %s and sequence <= %s
            """, (f"pse_{uuid.uuid4().hex}_", epoch_id, source_id, final_sequence),
        )
        await conn.execute(
            """
            insert into provider_session_append_receipts (
              epoch_id, expected_sequence, batch_sha256, entry_count, last_sequence,
              run_id, attempt_id, owner_generation, created_at
            ) select %s, expected_sequence, batch_sha256, entry_count, last_sequence,
              run_id, attempt_id, owner_generation, created_at
            from provider_session_append_receipts where epoch_id = %s and last_sequence <= %s
            """, (epoch_id, source_id, final_sequence),
        )
    else:
        scope_fields = {
            "tenant_id": scope.tenant_id,
            "workspace_id": scope.workspace_id,
            "user_id": scope.user_id,
            "session_id": scope.session_id,
            "agent_id": scope.agent_id,
        }
        if count != 0 or digest != initial_source_digest(scope_fields):
            raise ProviderSessionConflictError("provider_session_requires_new_conversation")
        epoch_id = f"pe_{uuid.uuid4().hex}"
        provider_id = str(uuid.uuid4())
        mode = "empty_start"
        await conn.execute(
            """
            insert into provider_session_epochs (
              id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
              epoch_number, provider_session_id, state
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, 'bootstrapping')
            """, (epoch_id, *values, head["next_epoch_number"], provider_id),
        )
    await conn.execute(
        """
        update provider_session_heads set next_epoch_number = next_epoch_number + 1,
          updated_at = now() where tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and agent_id = %s and engine = %s and active_run_id = %s
        """, (*values, run_id),
    )
    return {
        **conversation_context,
        "messages": [] if mode == "native_resume" else conversation_context["messages"],
        "selected_message_count": 0 if mode == "native_resume" else conversation_context["selected_message_count"],
        "selected_turn_count": 0 if mode == "native_resume" else conversation_context["selected_turn_count"],
        "execution_mode": mode,
        "provider_epoch_id": epoch_id,
        "provider_session_id": provider_id,
        **({"source_epoch_id": source_id, "source_final_sequence": final_sequence,
            "recovery": head.get("recovery_json")} if mode == "native_resume" else {}),
    }


async def commit_provider_turn(
    conn: AsyncConnection, *, tenant_id: str, run_id: str, attempt_id: str,
    assistant_message_id: str, final_sequence: int | None,
) -> None:
    """Extend coverage only inside the assistant + Run success transaction."""
    if not assistant_message_id or (
        final_sequence is not None
        and (type(final_sequence) is not int or final_sequence < 1)
    ):
        raise ProviderSessionConflictError("provider_session_terminal_receipt_invalid")
    cursor = await conn.execute(
        """
        select runs.tenant_id, runs.workspace_id, runs.user_id, runs.session_id,
               runs.agent_id, runs.session_generation, runs.context_snapshot_id,
               attempt.owner_generation, attempt.execution_spec_sha256,
               attempt.execution_spec_json->'context_pack'->'conversation_context' as frozen_context,
               context_snapshot.conversation_authority_json,
               head.current_epoch_id, head.active_run_id, head.active_attempt_id,
               epoch.id as epoch_id, epoch.state as epoch_state, epoch.next_sequence,
               epoch.coverage_source_sha256, epoch.writer_owner_generation,
               epoch.writer_run_id, epoch.writer_attempt_id,
               turn.id as turn_id, turn.state as turn_state,
               turn.execution_spec_sha256 as turn_spec_sha256,
               turn.bootstrap_source_sha256,
               turn.prior_coverage_sha256, turn.start_sequence, turn.user_message_id
        from runs
        join run_attempts attempt on attempt.tenant_id = runs.tenant_id
          and attempt.run_id = runs.id and attempt.id = %s and attempt.status = 'running'
        join run_context_snapshots context_snapshot on context_snapshot.id = runs.context_snapshot_id
          and context_snapshot.tenant_id = runs.tenant_id and context_snapshot.workspace_id = runs.workspace_id
          and context_snapshot.user_id = runs.user_id and context_snapshot.session_id = runs.session_id
          and context_snapshot.run_id = runs.id and context_snapshot.context_kind = 'executor'
        join provider_session_heads head on head.tenant_id = runs.tenant_id
          and head.workspace_id = runs.workspace_id and head.user_id = runs.user_id
          and head.session_id = runs.session_id and head.agent_id = runs.agent_id
          and head.engine = 'claude' and head.active_run_id = runs.id
          and head.active_attempt_id = attempt.id
        join provider_session_epochs epoch on epoch.tenant_id = head.tenant_id
          and epoch.workspace_id = head.workspace_id and epoch.user_id = head.user_id
          and epoch.session_id = head.session_id and epoch.agent_id = head.agent_id
          and epoch.engine = head.engine and epoch.writer_run_id = runs.id
          and epoch.writer_attempt_id = attempt.id
        join provider_turn_receipts turn on turn.tenant_id = runs.tenant_id
          and turn.run_id = runs.id and turn.attempt_id = attempt.id and turn.epoch_id = epoch.id
        where runs.tenant_id = %s and runs.id = %s
          and runs.status = 'running' and runs.cancel_requested_at is null
          and attempt.execution_spec_schema_version in (
            'ai-platform.execution-spec.v2', 'ai-platform.execution-spec.v3')
        for update of head, epoch, turn
        """, (attempt_id, tenant_id, run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ProviderSessionConflictError("provider_session_terminal_writer_invalid")
    if final_sequence is None:
        cursor = await conn.execute(
            """
            select count(*) as main_entry_count
            from provider_session_entries
            where epoch_id = %s and sequence >= %s and subpath = ''
            """,
            (row["epoch_id"], row["start_sequence"]),
        )
        main_entries = await cursor.fetchone()
        if (
            not isinstance(main_entries, dict)
            or type(main_entries.get("main_entry_count")) is not int
            or main_entries["main_entry_count"] < 1
            or type(row["next_sequence"]) is not int
        ):
            raise ProviderSessionConflictError(
                "provider_session_terminal_receipt_invalid"
            )
        # Older sandbox runtimes omitted the terminal sequence after their
        # eager append. The locked epoch is the authoritative sequence source.
        final_sequence = row["next_sequence"] - 1
    frozen = row["frozen_context"]
    receipt = validate_provider_authority_receipt(row["conversation_authority_json"])
    scope = {name: row[name] for name in
             ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")}
    if (
        receipt["scope"] != scope
        or receipt["through_session_generation"] != row["session_generation"]
        or not isinstance(frozen, dict)
        or frozen.get("provider_epoch_id") != row["epoch_id"]
        or frozen.get("source_sha256") != receipt["source_sha256"]
        or frozen.get("current_message_id") != receipt["current_message_id"]
        or row["turn_state"] != "writing"
        or row["turn_spec_sha256"] != row["execution_spec_sha256"]
        or row["writer_owner_generation"] != row["owner_generation"]
        or row["epoch_state"] != "active"
        or row["next_sequence"] != final_sequence + 1
        or final_sequence < row["start_sequence"]
        or row["user_message_id"] != receipt["current_message_id"]
        or row["prior_coverage_sha256"] != row["coverage_source_sha256"]
        or (frozen.get("execution_mode") != "native_resume"
            and (row["bootstrap_source_sha256"] != receipt["source_sha256"]
                 or row["current_epoch_id"] == row["epoch_id"]))
        or (frozen.get("execution_mode") == "native_resume"
            and (row["current_epoch_id"] != frozen.get("source_epoch_id", row["epoch_id"])
                 or row["coverage_source_sha256"] != receipt["source_sha256"]))
        or frozen.get("execution_mode") not in {"native_resume", "empty_start"}
    ):
        raise ProviderSessionConflictError("provider_session_terminal_coverage_invalid")
    ids = [assistant_message_id]
    if receipt["current_message_id"] is not None:
        ids.append(receipt["current_message_id"])
    cursor = await conn.execute(
        """
        select id, run_id, role, content, created_at from messages
        where tenant_id = %s and session_id = %s and run_id = %s
          and id = any(%s::text[]) and role in ('user', 'assistant')
        order by created_at asc, id asc
        """, (tenant_id, row["session_id"], run_id, ids),
    )
    messages = list(await cursor.fetchall())
    if ({message["id"] for message in messages} != set(ids)
        or len(messages) != len(ids) or messages[-1]["id"] != assistant_message_id
        or messages[-1]["role"] != "assistant"
        or (len(ids) == 2 and messages[0]["role"] != "user")):
        raise ProviderSessionConflictError("provider_session_terminal_message_invalid")
    committed_digest = extend_source_digest(receipt["source_sha256"], messages)
    await conn.execute(
        """
        update provider_turn_receipts set state = 'committed', final_sequence = %s,
          assistant_message_id = %s, committed_coverage_sha256 = %s, updated_at = now()
        where id = %s and state = 'writing'
        """, (final_sequence, assistant_message_id, committed_digest, row["turn_id"]),
    )
    await conn.execute(
        """
        update provider_session_epochs set state = 'ready',
          coverage_source_sha256 = %s, coverage_through_generation = %s,
          coverage_message_count = %s, coverage_schema_version = %s, updated_at = now()
        where id = %s and writer_run_id = %s and writer_attempt_id = %s
        """, (committed_digest, row["session_generation"],
              receipt["message_count"] + len(messages), receipt["schema_version"], row["epoch_id"], run_id, attempt_id),
    )
    # Only settle_provider_continuation may publish the head after workspace save.


async def read_verified_native_invocation(
    conn: AsyncConnection, *, scope: Mapping[str, str], epoch_id: str,
    source_run_id: str, source_attempt_id: str, final_sequence: int,
    invocation_id: str, native_tool_name: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """select * from provider_session_epochs where id=%s and tenant_id=%s
           and workspace_id=%s and user_id=%s and session_id=%s
           and recovery_json->>'source_run_id'=%s and recovery_json->>'source_attempt_id'=%s
           and writer_run_id is null for share""",
        (epoch_id, scope["tenant_id"], scope["workspace_id"], scope["user_id"], scope["session_id"],
         source_run_id, source_attempt_id),
    )
    epoch = await cursor.fetchone()
    if epoch is None:
        raise ProviderSessionConflictError("provider_session_scope_mismatch")
    rows, _ = await verified_provider_prefix(conn, epoch=epoch, final_sequence=final_sequence)
    matches = []
    for row in rows:
        entry = row["entry_json"]
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == invocation_id:
                matches.append(block)
    from app.context.infrastructure.provider_epoch_verification import native_call_blocks
    native_blocks = native_call_blocks(epoch_id, final_sequence=final_sequence)
    if native_blocks is not None:
        matches = [block for block in native_blocks if block.get("id") == invocation_id]
    if len(matches) != 1 or matches[0].get("name") != native_tool_name or not isinstance(matches[0].get("input"), dict):
        raise ProviderSessionConflictError("provider_session_native_invocation_invalid")
    return matches[0]["input"]



class PostgresProviderEpochRepository:
    verification_scope = staticmethod(provider_epoch_verification_scope)
    read_verified_native_invocation = staticmethod(read_verified_native_invocation)
    prepare_callback_media = staticmethod(prepare_callback_media)
    queue_images_for_deletion = staticmethod(queue_provider_images_for_deletion)
    callback_epoch = staticmethod(callback_provider_epoch)
    claim_lineage = staticmethod(claim_provider_lineage)
    read_coverage = staticmethod(read_provider_coverage)
    release_lineage = staticmethod(release_provider_lineage)
    prepare_epoch = staticmethod(prepare_provider_epoch)
    commit_turn = staticmethod(commit_provider_turn)
    settle_continuation = staticmethod(settle_provider_continuation)
    advance_unclaimed_terminal_generation = staticmethod(advance_unclaimed_terminal_generation)
    get_continuation = staticmethod(get_session_continuation)


async def read_usage_provider_session(conn, *, tenant_id: str, run_id: str, attempt_id: str) -> str | None:
    """Resolve metering identity from the platform-owned exact provider turn."""
    row = await (await conn.execute("""select e.provider_session_id::text as provider_session_id
        from provider_turn_receipts t join provider_session_epochs e
          on e.id=t.epoch_id and e.tenant_id=t.tenant_id
        join runs r on r.tenant_id=t.tenant_id and r.id=t.run_id
          and r.session_id=t.session_id and r.user_id=t.user_id
          and r.workspace_id=t.workspace_id and r.agent_id=t.agent_id
        where t.tenant_id=%s and t.run_id=%s and t.attempt_id=%s""",
        (tenant_id, run_id, attempt_id))).fetchone()
    return row["provider_session_id"] if row else None
