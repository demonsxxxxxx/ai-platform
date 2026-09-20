"""Scoped PostgreSQL authority for Claude provider epochs and callback batches."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from psycopg import AsyncConnection

from app.context.domain.conversation_authority import (
    extend_source_digest,
    validate_authority_receipt,
)
from app.context.domain.provider_sessions import (
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


async def matching_ready_epoch(
    conn: AsyncConnection, *, scope: ProviderSessionScope, run_id: str,
    source_sha256: str, message_count: int,
) -> bool:
    cursor = await conn.execute(
        """
        select epoch.id
        from provider_session_heads head
        join provider_session_epochs epoch on epoch.id = head.current_epoch_id
          and epoch.tenant_id = head.tenant_id and epoch.workspace_id = head.workspace_id
          and epoch.user_id = head.user_id and epoch.session_id = head.session_id
          and epoch.agent_id = head.agent_id and epoch.engine = head.engine
        where head.tenant_id = %s and head.workspace_id = %s and head.user_id = %s
          and head.session_id = %s and head.agent_id = %s and head.engine = %s
          and head.active_run_id = %s and head.active_attempt_id is null
          and epoch.state = 'ready' and epoch.writer_run_id is null
          and epoch.coverage_source_sha256 = %s and epoch.coverage_message_count = %s
          and epoch.entry_count <= %s and epoch.transcript_bytes <= %s
        """, (*_scope_values(scope), run_id, source_sha256, message_count,
              MAX_PROVIDER_SESSION_ENTRIES - 128,
              MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES - 2 * 1024 * 1024),
    )
    return await cursor.fetchone() is not None


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


async def release_provider_lineage(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    """Resolve an owned epoch and claim within the Runs terminal transaction."""
    cursor = await conn.execute(
        """
        select runs.status, head.tenant_id, head.workspace_id, head.user_id,
               head.session_id, head.agent_id, head.engine, head.active_attempt_id,
               epoch.id as epoch_id, epoch.state as epoch_state,
               epoch.next_sequence, epoch.coverage_source_sha256,
               turn.start_sequence, turn.state as turn_state
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
    if head["epoch_id"] is not None:
        if head["status"] == "succeeded":
            if head["turn_state"] != "committed" or head["epoch_state"] != "ready":
                raise ProviderSessionConflictError("provider_session_coverage_uncommitted")
            await conn.execute(
                """
                update provider_session_epochs set writer_run_id = null,
                  writer_attempt_id = null, writer_owner_generation = null, updated_at = now()
                where id = %s and writer_run_id = %s and writer_attempt_id = %s
                """, (head["epoch_id"], run_id, head["active_attempt_id"]),
            )
        else:
            untouched = (type(head["start_sequence"]) is int
                         and head["start_sequence"] == head["next_sequence"])
            await conn.execute(
                """
                update provider_session_epochs set state = %s, writer_run_id = null,
                  writer_attempt_id = null, writer_owner_generation = null, updated_at = now()
                where id = %s and writer_run_id = %s and writer_attempt_id = %s
                """, ("ready" if untouched else "dirty", head["epoch_id"], run_id,
                      head["active_attempt_id"]),
            )
            await conn.execute(
                """
                update provider_turn_receipts set state = 'failed', updated_at = now()
                where tenant_id = %s and run_id = %s and attempt_id = %s and state <> 'committed'
                """, (tenant_id, run_id, head["active_attempt_id"]),
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
    attempt_id: str, provider_session_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        provider_uuid = str(uuid.UUID(provider_session_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProviderSessionConflictError("provider_session_identity_mismatch") from exc
    values = _scope_values(scope)
    cursor = await conn.execute(
        """
        select head.current_epoch_id, head.active_run_id, head.active_attempt_id,
               epoch.id, epoch.provider_session_id, epoch.state, epoch.next_sequence,
               epoch.entry_count, epoch.transcript_bytes, epoch.coverage_source_sha256,
               epoch.writer_run_id, epoch.writer_attempt_id, epoch.writer_owner_generation
        from provider_session_heads head
        join provider_session_epochs epoch on epoch.tenant_id = head.tenant_id
          and epoch.workspace_id = head.workspace_id and epoch.user_id = head.user_id
          and epoch.session_id = head.session_id and epoch.agent_id = head.agent_id
          and epoch.engine = head.engine and epoch.provider_session_id = %s::uuid
        where head.tenant_id = %s and head.workspace_id = %s and head.user_id = %s
          and head.session_id = %s and head.agent_id = %s and head.engine = %s
          and head.active_run_id = %s
        for update of head, epoch
        """, (provider_uuid, *values, run_id),
    )
    epoch = await cursor.fetchone()
    if epoch is None or epoch["state"] in {"dirty", "closed"}:
        raise ProviderSessionConflictError("provider_session_epoch_unavailable")
    cursor = await conn.execute(
        """
        select attempt.owner_generation, attempt.execution_spec_sha256,
               attempt.execution_spec_json->'context_pack'->'conversation_context' as conversation_context
        from run_attempts attempt
        join sandbox_leases lease on lease.tenant_id = attempt.tenant_id
          and lease.run_id = attempt.run_id and lease.attempt_id = attempt.id
        where attempt.tenant_id = %s and attempt.run_id = %s and attempt.id = %s
          and attempt.status = 'running' and attempt.execution_spec_schema_version = 'ai-platform.execution-spec.v2'
          and lease.status = 'active' and lease.released_at is null
          and (lease.expires_at is null or lease.expires_at > now())
        """, (scope.tenant_id, run_id, attempt_id),
    )
    attempt = await cursor.fetchone()
    private = attempt.get("conversation_context") if isinstance(attempt, dict) else None
    if (
        attempt is None or not isinstance(private, dict)
        or private.get("provider_epoch_id") != epoch["id"]
        or private.get("provider_session_id") != provider_uuid
        or private.get("execution_mode") not in {"native_resume", "platform_bootstrap", "empty_start"}
        or (private["execution_mode"] == "native_resume" and (
            epoch["current_epoch_id"] != epoch["id"] or epoch["coverage_source_sha256"] != private.get("source_sha256")
        ))
        or (private["execution_mode"] != "native_resume" and epoch["state"] != "bootstrapping" and epoch["writer_run_id"] != run_id)
    ):
        raise ProviderSessionConflictError("provider_session_spec_mismatch")
    generation = attempt.get("owner_generation")
    if type(generation) is not int or generation < 1:
        raise ProviderSessionConflictError("provider_session_owner_invalid")
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
        returning id
        """, (f"ptr_{uuid.uuid4().hex}", *values, epoch["id"], run_id,
              attempt_id, attempt["execution_spec_sha256"],
              private["source_sha256"] if private["execution_mode"] != "native_resume" else None,
              epoch["next_sequence"], private.get("current_message_id"),
              epoch["coverage_source_sha256"]),
    )
    await cursor.fetchone()
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


async def callback_provider_epoch(
    conn: AsyncConnection, *, tenant_id: str, workspace_id: str, user_id: str,
    session_id: str, agent_id: str, run_id: str, attempt_id: str,
    provider_session_id: str, action: str, subpath: str | None,
    entries: list[dict[str, Any]], expected_sequence: int | None,
) -> dict[str, Any]:
    scope = ProviderSessionScope(tenant_id, workspace_id, user_id, session_id, agent_id)
    epoch, attempt, private = await _locked_callback_epoch(
        conn, scope=scope, run_id=run_id, attempt_id=attempt_id,
        provider_session_id=provider_session_id,
    )
    path = normalize_provider_subpath(subpath) or ""
    if action == "list_subkeys":
        cursor = await conn.execute(
            "select distinct subpath from provider_session_entries where epoch_id = %s and subpath <> '' order by subpath asc limit %s",
            (epoch["id"], MAX_PROVIDER_SESSION_ENTRIES + 1),
        )
        paths = [row["subpath"] for row in await cursor.fetchall()]
        if len(paths) > MAX_PROVIDER_SESSION_ENTRIES:
            raise ProviderSessionConflictError("provider_session_transcript_too_large")
        return {"action": action, "subpaths": paths, "next_sequence": epoch["next_sequence"]}
    if action == "load":
        cursor = await conn.execute(
            "select entry_json from provider_session_entries where epoch_id = %s and subpath = %s order by sequence asc limit %s",
            (epoch["id"], path, MAX_PROVIDER_SESSION_ENTRIES + 1),
        )
        rows = list(await cursor.fetchall())
        if len(rows) > MAX_PROVIDER_SESSION_ENTRIES:
            raise ProviderSessionConflictError("provider_session_transcript_too_large")
        return {"action": action, "entries": [row["entry_json"] for row in rows],
                "next_sequence": epoch["next_sequence"]}
    if action != "append" or type(expected_sequence) is not int or expected_sequence < 1:
        raise ProviderSessionConflictError("provider_session_append_sequence_invalid")
    try:
        batch, batch_bytes = normalize_provider_entry_batch(entries, subpath=path)
        digest = batch_digest(path, [item.entry for item in batch])
    except ValueError as exc:
        raise ProviderSessionConflictError(str(exc)) from exc
    if expected_sequence != epoch["next_sequence"]:
        cursor = await conn.execute(
            "select batch_sha256, entry_count, last_sequence, run_id, attempt_id, owner_generation from provider_session_append_receipts where epoch_id = %s and expected_sequence = %s",
            (epoch["id"], expected_sequence),
        )
        previous = await cursor.fetchone()
        if previous is None or (
            previous["batch_sha256"], previous["entry_count"], previous["run_id"],
            previous["attempt_id"], previous["owner_generation"]
        ) != (digest, len(batch), run_id, attempt_id, attempt["owner_generation"]):
            raise ProviderSessionConflictError("provider_session_append_conflict")
        return {"action": action, "entry_count": len(batch), "last_sequence": previous["last_sequence"],
                "next_sequence": epoch["next_sequence"]}
    if (epoch["entry_count"] + len(batch) > MAX_PROVIDER_SESSION_ENTRIES
        or epoch["transcript_bytes"] + batch_bytes > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES):
        raise ProviderSessionConflictError("provider_session_transcript_too_large")
    for offset, item in enumerate(batch):
        await conn.execute(
            """
            insert into provider_session_entries (
              id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
              epoch_id, subpath, sequence, sdk_entry_uuid, entry_json
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (f"pse_{uuid.uuid4().hex}", *_scope_values(scope), epoch["id"], path,
                  expected_sequence + offset, item.sdk_entry_uuid, _canonical(item.entry).decode("utf-8")),
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
          transcript_bytes = transcript_bytes + %s, updated_at = now() where id = %s
        """, (last + 1, len(batch), batch_bytes, epoch["id"]),
    )
    return {"action": action, "entry_count": len(batch), "last_sequence": last,
            "next_sequence": last + 1}


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
               epoch.provider_session_id, epoch.state, epoch.coverage_source_sha256,
               epoch.coverage_message_count, epoch.entry_count, epoch.transcript_bytes
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
          and status = 'running' and execution_spec_schema_version = 'ai-platform.execution-spec.v2'
        order by ordinal desc limit 1
        """, (scope.tenant_id, run_id),
    )
    existing_attempt = await cursor.fetchone()
    if existing_attempt is not None:
        frozen = existing_attempt.get("frozen_context")
        if (not isinstance(frozen, dict) or frozen.get("source_sha256") != digest
            or frozen.get("current_message_id") != conversation_context.get("current_message_id")
            or frozen.get("provider_epoch_id") is None):
            raise ProviderSessionConflictError("provider_session_spec_mismatch")
        return dict(frozen)
    if (head["state"] == "ready" and head["coverage_source_sha256"] == digest
        and head["coverage_message_count"] == count
        and head["entry_count"] <= MAX_PROVIDER_SESSION_ENTRIES - 128
        and head["transcript_bytes"] <= MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES - 2 * 1024 * 1024):
        epoch_id = head["current_epoch_id"]
        provider_id = str(head["provider_session_id"])
        mode = "native_resume"
    elif conversation_context.get("native_source_verified"):
        raise ProviderSessionConflictError("provider_session_epoch_changed")
    else:
        epoch_id = f"pe_{uuid.uuid4().hex}"
        provider_id = str(uuid.uuid4())
        mode = "platform_bootstrap" if count else "empty_start"
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
          and attempt.execution_spec_schema_version = 'ai-platform.execution-spec.v2'
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
    receipt = validate_authority_receipt(row["conversation_authority_json"])
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
            and (row["current_epoch_id"] != row["epoch_id"]
                 or row["coverage_source_sha256"] != receipt["source_sha256"]))
        or frozen.get("execution_mode") not in {"native_resume", "platform_bootstrap", "empty_start"}
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
          coverage_message_count = %s, updated_at = now()
        where id = %s and writer_run_id = %s and writer_attempt_id = %s
        """, (committed_digest, row["session_generation"],
              receipt["message_count"] + len(messages), row["epoch_id"], run_id, attempt_id),
    )
    if row["current_epoch_id"] != row["epoch_id"]:
        if row["current_epoch_id"]:
            await conn.execute(
                """
                update provider_session_epochs set state = 'closed', closed_at = now(), updated_at = now()
                where id = %s and state in ('ready', 'dirty')
                """, (row["current_epoch_id"],),
            )
        await conn.execute(
            """
            update provider_session_heads set current_epoch_id = %s, updated_at = now()
            where tenant_id = %s and workspace_id = %s and user_id = %s
              and session_id = %s and agent_id = %s and engine = 'claude'
              and active_run_id = %s and active_attempt_id = %s
            """, (row["epoch_id"], *scope.values(), run_id, attempt_id),
        )


class PostgresProviderEpochRepository:
    matching_ready_epoch = staticmethod(matching_ready_epoch)
    callback_epoch = staticmethod(callback_provider_epoch)
    claim_lineage = staticmethod(claim_provider_lineage)
    release_lineage = staticmethod(release_provider_lineage)
    prepare_epoch = staticmethod(prepare_provider_epoch)
    commit_turn = staticmethod(commit_provider_turn)
