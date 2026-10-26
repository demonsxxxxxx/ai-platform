"""Close a prepared Attempt that provably stopped before its first native callback."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.context.domain.conversation_authority import initial_source_digest
from app.context.domain.conversation_recovery import RECOVERY_SCHEMA_VERSION, validate_provider_authority_receipt
from app.context.domain.provider_sessions import ProviderSessionConflictError

_SCOPE = ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")
_KIND = "unclaimed_unaccepted"


async def settle_unclaimed_continuation(
    conn: Any, *, tenant_id: str, run_id: str, attempt_id: str,
    final_sequence: int | None, workspace_receipt: Mapping[str, Any] | None,
    stop_evidence: str, sdk_settlement: Mapping[str, Any],
    external_effect_unknown: bool,
) -> dict[str, Any]:
    from app.context.infrastructure.provider_epochs import verified_provider_prefix
    from app.context.infrastructure.provider_continuation import _workspace_saved

    if (stop_evidence not in {"provider_stopped", "provider_not_found", "provider_not_created"}
        or sdk_settlement["input_accepted"] is not False
        or sdk_settlement.get("ack_unknown") is not False or external_effect_unknown):
        raise ProviderSessionConflictError("provider_session_terminal_writer_invalid")
    cursor = await conn.execute(
        """
        select owning_run.tenant_id, owning_run.workspace_id, owning_run.user_id,
               owning_run.session_id, owning_run.agent_id, owning_run.session_generation,
               attempt.owner_generation, attempt.execution_spec_sha256,
               attempt.execution_spec_json->'context_pack'->'conversation_context' as frozen_context,
               snapshot.conversation_authority_json, head.current_epoch_id,
               epoch.id, epoch.provider_session_id, epoch.state, epoch.next_sequence,
               epoch.entry_count, epoch.transcript_bytes, epoch.structured_bytes, epoch.source_epoch_id,
               epoch.source_final_sequence, epoch.coverage_source_sha256,
               epoch.coverage_message_count, epoch.recovery_json
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
          and head.active_attempt_id is null
        join provider_session_epochs epoch on epoch.tenant_id = head.tenant_id
          and epoch.workspace_id = head.workspace_id and epoch.user_id = head.user_id
          and epoch.session_id = head.session_id and epoch.agent_id = head.agent_id and epoch.engine = head.engine
          and epoch.id = attempt.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id'
          and epoch.id is distinct from head.current_epoch_id
          and epoch.writer_run_id is null and epoch.writer_attempt_id is null and epoch.writer_owner_generation is null
        where owning_run.tenant_id = %s and owning_run.id = %s
          and owning_run.status in ('running', 'failed', 'cancelled')
          and not exists (select 1 from provider_turn_receipts turn where turn.epoch_id = epoch.id)
          and not exists (select 1 from provider_session_epochs other_writer
                          where other_writer.tenant_id = head.tenant_id and other_writer.session_id = head.session_id
                            and other_writer.engine = head.engine and other_writer.writer_run_id is not null)
          and not exists (select 1 from run_attempts other_attempt
                          where other_attempt.tenant_id = attempt.tenant_id and other_attempt.id <> attempt.id
                            and other_attempt.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id' = epoch.id)
        for update of owning_run, attempt, snapshot, head, epoch
        """, (attempt_id, tenant_id, run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ProviderSessionConflictError("provider_session_terminal_writer_invalid")
    scope = {key: row[key] for key in _SCOPE}
    frozen = row["frozen_context"]
    authority = validate_provider_authority_receipt(row["conversation_authority_json"])
    if (sdk_settlement["owner_generation"] != row["owner_generation"]
        or authority["scope"] != scope or authority["through_session_generation"] != row["session_generation"]
        or not isinstance(frozen, dict) or frozen.get("provider_epoch_id") != row["id"]
        or frozen.get("provider_session_id") != str(row["provider_session_id"])
        or frozen.get("source_sha256") != authority["source_sha256"]
        or frozen.get("authority_schema_version", "ai-platform.conversation-authority.v2") != authority["schema_version"]
        or frozen.get("current_message_id") != authority["current_message_id"]
        or frozen.get("message_count") != authority["message_count"]):
        raise ProviderSessionConflictError("provider_session_terminal_coverage_invalid")
    _workspace_saved(workspace_receipt, {**{key: scope[key] for key in _SCOPE if key != "agent_id"},
                                         "run_id": run_id, "attempt_id": attempt_id})
    rows, _ = await verified_provider_prefix(conn, epoch=row, final_sequence=row["next_sequence"] - 1)
    mode = frozen.get("execution_mode")
    if mode == "empty_start":
        if (row["current_epoch_id"] is not None or row["source_epoch_id"] is not None
            or row["source_final_sequence"] is not None or rows
            or row["coverage_source_sha256"] is not None or row["coverage_message_count"] != 0
            or authority["message_count"] != 0 or authority["source_sha256"] != initial_source_digest(scope)):
            raise ProviderSessionConflictError("provider_session_terminal_coverage_invalid")
    elif mode == "native_resume":
        boundary = frozen.get("source_final_sequence")
        if (type(boundary) is not int or row["source_epoch_id"] != frozen.get("source_epoch_id")
            or row["source_epoch_id"] != row["current_epoch_id"]
            or row["source_final_sequence"] != boundary or row["next_sequence"] != boundary + 1
            or row["coverage_source_sha256"] != authority["source_sha256"]
            or row["coverage_message_count"] != authority["message_count"]):
            raise ProviderSessionConflictError("provider_session_terminal_coverage_invalid")
        cursor = await conn.execute(
            "select * from provider_session_epochs where id = %s and tenant_id = %s "
            "and workspace_id = %s and user_id = %s and session_id = %s and agent_id = %s "
            "and engine = 'claude' and state = 'ready' and writer_run_id is null "
            "and writer_attempt_id is null and writer_owner_generation is null for share",
            (row["source_epoch_id"], *(scope[key] for key in _SCOPE)),
        )
        source = await cursor.fetchone()
        if source is None or str(source["provider_session_id"]) != str(row["provider_session_id"]):
            raise ProviderSessionConflictError("provider_session_terminal_coverage_invalid")
        original, size = await verified_provider_prefix(conn, epoch=source, final_sequence=boundary)
        if rows != original or row["transcript_bytes"] != size:
            raise ProviderSessionConflictError("provider_session_integrity_mismatch")
        receipts = []
        for epoch_id in (row["id"], row["source_epoch_id"]):
            cursor = await conn.execute(
                "select expected_sequence, batch_sha256, entry_count, last_sequence, run_id, "
                "attempt_id, owner_generation, created_at from provider_session_append_receipts "
                "where epoch_id = %s and last_sequence <= %s order by expected_sequence",
                (epoch_id, boundary),
            )
            receipts.append(list(await cursor.fetchall()))
        if receipts[0] != receipts[1]:
            raise ProviderSessionConflictError("provider_session_integrity_mismatch")
    else:
        raise ProviderSessionConflictError("provider_session_terminal_coverage_invalid")
    if final_sequence is not None and (type(final_sequence) is not int or final_sequence != row["next_sequence"] - 1):
        raise ProviderSessionConflictError("provider_session_terminal_receipt_invalid")
    recovery = dict(
        schema_version=RECOVERY_SCHEMA_VERSION, state="blocked", reason_code="native_unavailable",
        settlement_kind=_KIND, source_run_id=run_id, source_attempt_id=attempt_id,
        provider_epoch_id=row["id"], provider_session_id=str(row["provider_session_id"]),
        final_sequence=final_sequence, prepared_final_sequence=row["next_sequence"] - 1,
        confirmed_entry_count=len(rows), confirmed_transcript_bytes=row["transcript_bytes"],
        current_message_id=authority["current_message_id"], execution_spec_sha256=row["execution_spec_sha256"],
        input_accepted=False, ack_unknown=False, workspace_saved=False, workspace_version_id=None,
        stop_evidence=stop_evidence, mirror_complete=sdk_settlement["mirror_complete"],
        sdk_terminal_reason=(sdk_settlement["terminal_reason"][:128]
                             if isinstance(sdk_settlement.get("terminal_reason"), str) else None),
        external_effect_unknown=False, scope=scope, owner_generation=row["owner_generation"],
    )
    previous = row.get("recovery_json")
    if previous is not None:
        if row["state"] != "closed" or previous != recovery:
            raise ProviderSessionConflictError("provider_session_settlement_conflict")
        return recovery
    if row["state"] not in {"bootstrapping", "ready"}:
        raise ProviderSessionConflictError("provider_session_terminal_writer_invalid")
    # Close only the unused target; never manufacture a writer, turn, or append ACK.
    await conn.execute(
        "update provider_session_epochs set state = 'closed', recovery_json = %s::jsonb, "
        "closed_at = now(), updated_at = now() where id = %s", (json.dumps(recovery), row["id"]),
    )
    return recovery


async def unclaimed_lineage_release_proven(conn: Any, *, head: Mapping[str, Any], run_id: str) -> bool:
    """Every potentially started Attempt needs its own current, stopped closing receipt."""
    cursor = await conn.execute(
        """
        select attempt.id as attempt_id, attempt.status as attempt_status, attempt.owner_generation, attempt.execution_spec_sha256,
               attempt.execution_spec_json->'context_pack'->'conversation_context' as frozen_context,
               epoch.id, epoch.provider_session_id, epoch.state, epoch.next_sequence,
               epoch.entry_count, epoch.transcript_bytes, epoch.structured_bytes, epoch.recovery_json,
               epoch.writer_run_id, epoch.writer_attempt_id, epoch.writer_owner_generation
        from run_attempts attempt
        left join provider_session_epochs epoch on epoch.tenant_id = attempt.tenant_id
          and epoch.workspace_id = %s and epoch.user_id = %s and epoch.session_id = %s
          and epoch.agent_id = %s and epoch.engine = %s
          and epoch.id = attempt.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id'
        where attempt.tenant_id = %s and attempt.run_id = %s
          and (attempt.started_at is not null or attempt.status not in ('failed', 'cancelled'))
        for update of attempt
        """, (head["workspace_id"], head["user_id"], head["session_id"], head["agent_id"], head["engine"],
              head["tenant_id"], run_id),
    )
    attempts = list(await cursor.fetchall())
    if not attempts or head["active_attempt_id"] is not None:
        return False
    scope = {key: head[key] for key in _SCOPE}
    for row in attempts:
        receipt = row.get("recovery_json")
        frozen = row.get("frozen_context")
        if (row["attempt_status"] not in {"failed", "cancelled"}
            or not isinstance(receipt, dict) or not isinstance(frozen, dict)
            or receipt.get("schema_version") != RECOVERY_SCHEMA_VERSION or receipt.get("settlement_kind") != _KIND
            or receipt.get("scope") != scope or receipt.get("source_run_id") != run_id
            or receipt.get("source_attempt_id") != row["attempt_id"] or receipt.get("provider_epoch_id") != row["id"]
            or receipt.get("owner_generation") != row["owner_generation"]
            or receipt.get("execution_spec_sha256") != row["execution_spec_sha256"]
            or receipt.get("provider_session_id") != str(row["provider_session_id"])
            or frozen.get("provider_epoch_id") != row["id"]
            or frozen.get("provider_session_id") != receipt.get("provider_session_id")
            or receipt.get("input_accepted") is not False or receipt.get("ack_unknown") is not False
            or receipt.get("external_effect_unknown") is not False
            or receipt.get("stop_evidence") not in ("provider_stopped", "provider_not_found", "provider_not_created")
            or row["state"] != "closed" or row["next_sequence"] != receipt.get("prepared_final_sequence", -1) + 1
            or row["entry_count"] != receipt.get("confirmed_entry_count")
            or row["transcript_bytes"] != receipt.get("confirmed_transcript_bytes")
            or any(row[field] is not None for field in ("writer_run_id", "writer_attempt_id", "writer_owner_generation"))):
            return False
    return True


async def advance_unclaimed_terminal_generation(
    conn: Any, *, previous: Mapping[str, Any], terminal: Mapping[str, Any],
) -> None:
    """Consume the exact successful terminal CAS, never a G/G+1 read heuristic."""
    if (any(previous.get(key) != terminal.get(key) for key in ("tenant_id", "run_id", "id", "execution_spec_sha256"))
        or previous.get("status") not in {"running", "claimed", "cancel_requested"}
        or terminal.get("status") not in {"succeeded", "failed", "cancelled"}
        or terminal.get("owner_generation") != previous.get("owner_generation", 0) + 1):
        return
    await conn.execute(
        """update provider_session_epochs epoch set recovery_json=jsonb_set(
             epoch.recovery_json, '{owner_generation}', to_jsonb(%s::bigint)), updated_at=now()
           from run_attempts attempt join runs owner on owner.tenant_id=attempt.tenant_id and owner.id=attempt.run_id
           where attempt.tenant_id=%s and attempt.run_id=%s and attempt.id=%s
             and attempt.owner_generation=%s and attempt.status=%s
             and owner.status=attempt.status and attempt.execution_spec_sha256=%s
             and epoch.tenant_id=owner.tenant_id and epoch.workspace_id=owner.workspace_id
             and epoch.user_id=owner.user_id and epoch.session_id=owner.session_id and epoch.agent_id=owner.agent_id
             and epoch.id=attempt.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id'
             and epoch.state='closed' and epoch.writer_run_id is null and epoch.writer_attempt_id is null
             and epoch.recovery_json->>'settlement_kind'='unclaimed_unaccepted'
             and epoch.recovery_json->>'source_run_id'=owner.id
             and epoch.recovery_json->>'source_attempt_id'=attempt.id
             and epoch.recovery_json->>'execution_spec_sha256'=attempt.execution_spec_sha256
             and epoch.recovery_json->>'owner_generation'=%s""",
        (terminal["owner_generation"], terminal["tenant_id"], terminal["run_id"], terminal["id"],
         terminal["owner_generation"], terminal["status"], terminal["execution_spec_sha256"],
         str(previous["owner_generation"])),
    )
    from app.context.infrastructure.provider_epochs import release_provider_lineage
    await release_provider_lineage(conn, tenant_id=terminal["tenant_id"], run_id=terminal["run_id"])
