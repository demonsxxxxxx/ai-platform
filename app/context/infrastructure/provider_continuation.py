"""Terminal-transaction settlement of native history and private workspace receipts."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.context.domain.conversation_authority import extend_source_digest
from app.context.domain.conversation_recovery import (
    AUTHORITY_V3, RECOVERY_SCHEMA_VERSION, public_continuation, provider_restore_size,
    validate_provider_authority_receipt,
)
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_ENTRIES, MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
    MAX_PROVIDER_SESSION_STRUCTURED_TRANSCRIPT_BYTES,
    ProviderSessionConflictError,
)

_SCOPE_FIELDS = ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")
_STOPPED = {"provider_stopped", "provider_not_found"}


def _workspace_saved(receipt: object, scope: Mapping[str, str]) -> bool:
    if receipt is None:
        return False
    if not isinstance(receipt, Mapping):
        raise ProviderSessionConflictError("provider_session_workspace_receipt_invalid")
    if (receipt.get("schema_version") != "ai-platform.workspace-version.v1"
        or not isinstance(receipt.get("scope"), Mapping)
        or any(receipt["scope"].get(key) != value for key, value in scope.items())):
        raise ProviderSessionConflictError("provider_session_workspace_scope_invalid")
    return (receipt.get("state") == "saved"
            and isinstance(receipt.get("version_id"), str) and bool(receipt["version_id"])
            and type(receipt.get("file_count")) is int and receipt["file_count"] >= 0
            and type(receipt.get("total_bytes")) is int and receipt["total_bytes"] >= 0
            and isinstance(receipt.get("source_stop_evidence"), str)
            and receipt["source_stop_evidence"] in _STOPPED)


async def settle_provider_continuation(
    conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
    final_sequence: int | None, workspace_receipt: Mapping[str, Any] | None,
    stop_evidence: str, sdk_settlement: Mapping[str, Any],
    external_effect_unknown: bool = False, validate_only: bool = False,
) -> dict[str, Any]:
    """Called by trusted Runs composition in its terminal CAS transaction.

    sdk_settlement: owner_generation is the current platform CAS generation;
    writer_owner_generation (optional) identifies a previously fenced writer.
    input_accepted/mirror_complete are observed SDK facts, not inferred Result text.
    ack_unknown blocks publication until the original append ACK is reconciled.
    """
    from app.context.infrastructure.provider_epochs import (
        verified_provider_prefix,
    )

    if (not isinstance(sdk_settlement, Mapping)
        or type(sdk_settlement.get("owner_generation")) is not int
        or sdk_settlement["owner_generation"] < 1
        or type(sdk_settlement.get("mirror_complete")) is not bool
        or type(sdk_settlement.get("input_accepted")) is not bool
        or type(sdk_settlement.get("ack_unknown", False)) is not bool
        or type(external_effect_unknown) is not bool
        or not isinstance(stop_evidence, str)
        or stop_evidence not in _STOPPED | {"unverified", "provider_not_created"}):
        raise ProviderSessionConflictError("provider_session_settlement_invalid")
    cursor = await conn.execute(
        """
        select owning_run.tenant_id, owning_run.workspace_id, owning_run.user_id,
               owning_run.session_id, owning_run.agent_id, owning_run.session_generation,
               owning_run.status as run_status, owning_run.cancel_requested_at,
               attempt.owner_generation, attempt.execution_spec_sha256,
               attempt.execution_spec_json->'context_pack'->'conversation_context' as frozen_context,
               snapshot.conversation_authority_json,
               head.current_epoch_id, head.active_run_id, head.active_attempt_id,
               epoch.id, epoch.provider_session_id, epoch.state, epoch.next_sequence,
               epoch.entry_count, epoch.transcript_bytes, epoch.structured_bytes, epoch.coverage_source_sha256,
               epoch.coverage_message_count, epoch.coverage_schema_version,
               epoch.writer_run_id, epoch.writer_attempt_id, epoch.writer_owner_generation,
               epoch.recovery_json,
               turn.id as turn_id, turn.state as turn_state, turn.start_sequence,
               turn.final_sequence as turn_final_sequence, turn.user_message_id,
               turn.execution_spec_sha256 as turn_spec_sha256
        from runs owning_run
        join run_attempts attempt on attempt.tenant_id = owning_run.tenant_id
          and attempt.run_id = owning_run.id and attempt.id = %s
          and attempt.execution_spec_schema_version in ('ai-platform.execution-spec.v2', 'ai-platform.execution-spec.v3')
        join run_context_snapshots snapshot on snapshot.tenant_id = owning_run.tenant_id
          and snapshot.workspace_id = owning_run.workspace_id and snapshot.user_id = owning_run.user_id
          and snapshot.session_id = owning_run.session_id and snapshot.run_id = owning_run.id
          and snapshot.id = owning_run.context_snapshot_id and snapshot.context_kind = 'executor'
        join provider_session_heads head on head.tenant_id = owning_run.tenant_id
          and head.workspace_id = owning_run.workspace_id and head.user_id = owning_run.user_id
          and head.session_id = owning_run.session_id and head.agent_id = owning_run.agent_id
          and head.engine = 'claude' and head.active_run_id = owning_run.id
          and head.active_attempt_id = attempt.id
        join provider_session_epochs epoch on epoch.tenant_id = head.tenant_id
          and epoch.workspace_id = head.workspace_id and epoch.user_id = head.user_id
          and epoch.session_id = head.session_id and epoch.agent_id = head.agent_id
          and epoch.engine = head.engine
          and epoch.id = attempt.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id'
          and epoch.writer_run_id = owning_run.id and epoch.writer_attempt_id = attempt.id
        join provider_turn_receipts turn on turn.tenant_id = owning_run.tenant_id
          and turn.run_id = owning_run.id and turn.attempt_id = attempt.id and turn.epoch_id = epoch.id
        where owning_run.tenant_id = %s and owning_run.id = %s
          and owning_run.status in ('running', 'succeeded', 'failed', 'cancelled')
        for update of owning_run, attempt, head, epoch, turn
        """, (attempt_id, tenant_id, run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        if validate_only:
            raise ProviderSessionConflictError("provider_session_terminal_writer_invalid")
        from app.context.infrastructure.unclaimed_provider_continuation import settle_unclaimed_continuation

        return await settle_unclaimed_continuation(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            final_sequence=final_sequence, workspace_receipt=workspace_receipt,
            stop_evidence=stop_evidence, sdk_settlement=sdk_settlement,
            external_effect_unknown=external_effect_unknown,
        )
    if (stop_evidence == "provider_not_created"
        or sdk_settlement.get("terminal_reason") == "predispatch_failure"):
        raise ProviderSessionConflictError("provider_session_terminal_writer_invalid")
    scope = {key: row[key] for key in _SCOPE_FIELDS}
    frozen = row["frozen_context"]
    authority = validate_provider_authority_receipt(row["conversation_authority_json"])
    writer_generation = sdk_settlement.get("writer_owner_generation", row["owner_generation"])
    if (type(writer_generation) is not int or writer_generation < 1
        or sdk_settlement["owner_generation"] != row["owner_generation"]
        or writer_generation > row["owner_generation"]
        or writer_generation != row["writer_owner_generation"]
        or authority["scope"] != scope
        or authority["through_session_generation"] != row["session_generation"]
        or not isinstance(frozen, dict)
        or frozen.get("provider_epoch_id") != row["id"]
        or frozen.get("provider_session_id") != str(row["provider_session_id"])
        or frozen.get("source_sha256") != authority["source_sha256"]
        or frozen.get("current_message_id") != authority["current_message_id"]
        or row["user_message_id"] != authority["current_message_id"]
        or row["turn_spec_sha256"] != row["execution_spec_sha256"]
        or row["turn_state"] not in {"writing", "committed", "failed"}):
        raise ProviderSessionConflictError("provider_session_terminal_coverage_invalid")
    workspace_scope = {**{key: scope[key] for key in _SCOPE_FIELDS if key != "agent_id"},
                       "run_id": run_id, "attempt_id": attempt_id}
    saved = _workspace_saved(workspace_receipt, workspace_scope)
    stopped = stop_evidence in _STOPPED
    # Verify even blocked settlements; unknown ACKs never become trusted prefixes.
    prefix = []
    prefix_bytes = 0
    if final_sequence is not None:
        prefix, prefix_bytes = await verified_provider_prefix(conn, epoch=row, final_sequence=final_sequence)
    else:
        await verified_provider_prefix(conn, epoch=row, final_sequence=row["next_sequence"] - 1)
    has_current_main = any(not entry["subpath"] and entry["sequence"] >= row["start_sequence"]
                           for entry in prefix)
    native = (final_sequence is not None and has_current_main
              and sdk_settlement["input_accepted"] and not sdk_settlement.get("ack_unknown", False))
    # An outcome receipt is not broad write-policy clearance for later turns.
    inherited = frozen.get("recovery") or {}
    operation_sources = inherited.get("operation_sources", [inherited]) if inherited.get("state") == "recovery_candidate" else []
    source_keys = ("source_run_id", "source_attempt_id", "provider_epoch_id", "final_sequence")
    if (not isinstance(operation_sources, list) or len(operation_sources) > 128
        or any(not isinstance(source, dict) or any(source.get(key) is None for key in source_keys)
               for source in operation_sources)):
        raise ProviderSessionConflictError("provider_session_settlement_invalid")
    operation_sources = [{key: source[key] for key in source_keys} for source in operation_sources]
    if external_effect_unknown:
        current_source = dict(source_run_id=run_id, source_attempt_id=attempt_id,
                              provider_epoch_id=row["id"], final_sequence=final_sequence)
        if current_source not in operation_sources:
            if len(operation_sources) >= 128:
                raise ProviderSessionConflictError("provider_session_settlement_invalid")
            operation_sources.append(current_source)
    if not stopped:
        state, reason = "blocked", "writer_unverified"
    elif not saved:
        state, reason = "blocked", "workspace_unsaved"
    elif not native:
        state, reason = "blocked", "native_unavailable"
    elif (not sdk_settlement["mirror_complete"] or external_effect_unknown or operation_sources
          or final_sequence != row["next_sequence"] - 1):
        state = "recovery_candidate"
        reason = "external_effect_unknown" if external_effect_unknown or operation_sources else "fault_candidate"
    else:
        state = "resumable"
        reason = ("native_ready" if row["turn_state"] == "committed" else "interrupted"
                  if row["cancel_requested_at"] is not None or row["run_status"] == "cancelled"
                  else "controlled_failure")
    recovery = {
        "schema_version": RECOVERY_SCHEMA_VERSION, "state": state, "reason_code": reason,
        "source_run_id": run_id, "source_attempt_id": attempt_id,
        "provider_epoch_id": row["id"], "provider_session_id": str(row["provider_session_id"]),
        "final_sequence": final_sequence,
        "confirmed_entry_count": len(prefix), "confirmed_transcript_bytes": prefix_bytes,
        "workspace_version_id": workspace_receipt["version_id"] if saved else None,
        "workspace_receipt": dict(workspace_receipt) if saved else None,
        "workspace_saved": saved, "stop_evidence": stop_evidence,
        "sdk_terminal_reason": (sdk_settlement["terminal_reason"][:128]
                                if isinstance(sdk_settlement.get("terminal_reason"), str) else None),
        "mirror_complete": sdk_settlement["mirror_complete"],
        "input_accepted": sdk_settlement["input_accepted"],
        "external_effect_unknown": external_effect_unknown,
        "scope": scope, "owner_generation": row["owner_generation"],
        **({"operation_sources": operation_sources} if operation_sources else {}),
    }
    if validate_only:
        if (row.get("state") != "active" or row.get("recovery_json") is not None
            or not stopped or not saved or not native or final_sequence != row["next_sequence"] - 1):
            raise ProviderSessionConflictError("provider_session_terminal_receipt_invalid")
        cursor = await conn.execute(
            "select 1 from provider_session_epochs where tenant_id=%s and workspace_id=%s "
            "and user_id=%s and session_id=%s and agent_id=%s and engine='claude' "
            "and id<>%s and writer_run_id is not null limit 1",
            (*(scope[key] for key in _SCOPE_FIELDS), row["id"]),
        )
        if await cursor.fetchone() is not None:
            raise ProviderSessionConflictError("provider_session_terminal_writer_invalid")
        return recovery
    previous = row.get("recovery_json")
    if isinstance(previous, dict) and previous.get("state") in {"resumable", "recovery_candidate"}:
        if previous != recovery:
            raise ProviderSessionConflictError("provider_session_settlement_conflict")
        return previous
    if state != "blocked":
        if row["turn_state"] == "committed":
            if row["turn_final_sequence"] != final_sequence:
                raise ProviderSessionConflictError("provider_session_terminal_receipt_invalid")
        else:
            messages = []
            if authority["current_message_id"] is not None:
                cursor = await conn.execute(
                    "select id, run_id, role, content from messages where tenant_id = %s "
                    "and session_id = %s and run_id = %s and id = %s and role = 'user'",
                    (tenant_id, scope["session_id"], run_id, authority["current_message_id"]),
                )
                message = await cursor.fetchone()
                if message is None:
                    raise ProviderSessionConflictError("provider_session_terminal_message_invalid")
                messages.append(message)
            digest = extend_source_digest(authority["source_sha256"], messages)
            await conn.execute(
                """
                update provider_session_epochs set coverage_source_sha256 = %s,
                  coverage_message_count = %s, coverage_through_generation = %s,
                  coverage_schema_version = %s where id = %s
                """, (digest, authority["message_count"] + len(messages),
                      row["session_generation"], AUTHORITY_V3, row["id"]),
            )
            # Failure receipts retain failure meaning; recovery_json is the new closing evidence.
            await conn.execute(
                "update provider_turn_receipts set state = 'failed', final_sequence = %s, "
                "updated_at = now() where id = %s and state <> 'committed'",
                (final_sequence, row["turn_id"]),
            )
    await conn.execute(
        "update provider_session_epochs set recovery_json = %s::jsonb, state = %s, "
        "updated_at = now() where id = %s",
        (json.dumps(recovery), "ready" if state != "blocked" else "dirty", row["id"]),
    )
    untouched_unaccepted = (stopped and sdk_settlement["input_accepted"] is False
                            and row["next_sequence"] == row["start_sequence"]
                            and not sdk_settlement.get("ack_unknown", False))
    if untouched_unaccepted:
        await conn.execute(
            "update provider_turn_receipts set state = 'failed', updated_at = now() "
            "where id = %s and state <> 'committed'", (row["turn_id"],),
        )
    await conn.execute(
        """
        update provider_session_heads set recovery_json = case when %s then recovery_json else %s::jsonb end,
          current_epoch_id = case when %s then %s else current_epoch_id end,
          updated_at = now() where tenant_id = %s and session_id = %s and engine = 'claude'
          and active_run_id = %s and active_attempt_id = %s
        """, (untouched_unaccepted, json.dumps(recovery), state != "blocked", row["id"], tenant_id,
              scope["session_id"], run_id, attempt_id),
    )
    return recovery


async def get_session_continuation(
    conn: Any, *, tenant_id: str, user_id: str, session_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select head.recovery_json, head.active_run_id, head.current_epoch_id,
               owner_run.status as active_run_status, owner_run.cancel_requested_at,
               exists (select 1 from sandbox_leases pending
                       where pending.tenant_id = head.tenant_id and pending.run_id = head.active_run_id
                         and pending.status = 'active' and pending.executor_terminal_json is not null) as terminal_pending,
               epoch.entry_count, epoch.transcript_bytes, epoch.structured_bytes
        from sessions session
        left join provider_session_heads head on head.tenant_id = session.tenant_id
          and head.workspace_id = session.workspace_id and head.user_id = session.user_id
          and head.session_id = session.id and head.agent_id = session.agent_id and head.engine = 'claude'
        left join runs owner_run on owner_run.tenant_id = head.tenant_id
          and owner_run.workspace_id = head.workspace_id and owner_run.user_id = head.user_id
          and owner_run.session_id = head.session_id and owner_run.agent_id = head.agent_id
          and owner_run.id = head.active_run_id
        left join provider_session_epochs epoch on epoch.id = head.current_epoch_id
          and epoch.tenant_id = head.tenant_id and epoch.workspace_id = head.workspace_id
          and epoch.user_id = head.user_id and epoch.session_id = head.session_id
          and epoch.agent_id = head.agent_id and epoch.engine = head.engine
        where session.tenant_id = %s and session.user_id = %s and session.id = %s
          and session.status = 'active'
        """, (tenant_id, user_id, session_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return public_continuation(None)
    recovery = row.get("recovery_json")
    settling = bool(row.get("active_run_id")) and (
        row.get("cancel_requested_at") is not None or row.get("terminal_pending") is True
        or row.get("active_run_status") in {"succeeded", "failed", "cancelled"}
    )
    if not settling and recovery is None and row.get("current_epoch_id") is None:
        # A confirmed unused lineage is not a failed native continuation.
        return None
    if (row.get("active_run_id") and row.get("active_run_status") in {"succeeded", "failed", "cancelled"}
        and (not isinstance(recovery, dict)
             or recovery.get("source_run_id") != row["active_run_id"]
             or recovery.get("stop_evidence") not in ("provider_stopped", "provider_not_found"))):
        recovery = {"schema_version": RECOVERY_SCHEMA_VERSION, "state": "blocked",
                    "reason_code": "writer_unverified", "workspace_saved": False}
    blocked = isinstance(recovery, dict) and recovery.get("state") == "blocked"
    restore_count, restore_bytes = provider_restore_size(row, recovery)
    capacity = (type(restore_count) is int and restore_count > MAX_PROVIDER_SESSION_ENTRIES - 128
                or type(restore_bytes) is int and restore_bytes > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES - 16 * 1024 * 1024
                or type(row.get("structured_bytes")) is int and row["structured_bytes"] > MAX_PROVIDER_SESSION_STRUCTURED_TRANSCRIPT_BYTES - 2 * 1024 * 1024)
    if recovery is None and not settling and not capacity:
        return None  # Confirmed legacy v2 head keeps its original admission contract.
    return public_continuation(recovery, settling=settling and not blocked,
                               capacity_exhausted=capacity)
