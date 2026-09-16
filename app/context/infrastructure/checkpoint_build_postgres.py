"""Short PostgreSQL fences for one source-bound checkpoint build."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection


async def load_checkpoint_build_source(conn: AsyncConnection, *, tenant_id: str,
                                       run_id: str, context_snapshot_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select runs.id as run_id, runs.tenant_id, runs.workspace_id, runs.user_id,
               runs.session_id, runs.agent_id, runs.session_generation, runs.model_id,
               runs.model_value, runs.model_gateway_revision, runs.max_input_tokens,
               runs.max_output_tokens, snapshot.conversation_authority_json,
               snapshot.included_message_ids, current_message.content as current_user_text,
               runs.input_json->>'message' as input_message
        from runs
        join run_context_snapshots snapshot on snapshot.id = runs.context_snapshot_id
          and snapshot.tenant_id = runs.tenant_id and snapshot.workspace_id = runs.workspace_id
          and snapshot.user_id = runs.user_id and snapshot.session_id = runs.session_id
          and snapshot.run_id = runs.id and snapshot.context_kind = 'executor'
        join provider_session_heads head on head.tenant_id = runs.tenant_id
          and head.workspace_id = runs.workspace_id and head.user_id = runs.user_id
          and head.session_id = runs.session_id and head.agent_id = runs.agent_id
          and head.engine = 'claude' and head.active_run_id = runs.id
        left join messages current_message on current_message.tenant_id = runs.tenant_id
          and current_message.session_id = runs.session_id and current_message.run_id = runs.id
          and current_message.role = 'user'
          and current_message.id = snapshot.conversation_authority_json->>'current_message_id'
          and snapshot.included_message_ids ? current_message.id
        where runs.tenant_id = %s and runs.id = %s
          and runs.context_snapshot_id = %s and runs.status = 'queued'
          and runs.cancel_requested_at is null
          and runs.max_input_tokens > 0 and runs.max_output_tokens > 0
          and runs.model_gateway_revision > 0
        """, (tenant_id, run_id, context_snapshot_id),
    )
    return await cursor.fetchone()


async def find_checkpoint_build(
    conn: AsyncConnection, *, scope: dict[str, str], build_key_sha256: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, state, owner_run_id, source_snapshot_id
        from conversation_context_checkpoints
        where tenant_id = %s and workspace_id = %s and user_id = %s
          and session_id = %s and agent_id = %s and build_key_sha256 = %s
        """, (*[scope[key] for key in
                 ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")],
               build_key_sha256),
    )
    return await cursor.fetchone()


async def claim_checkpoint_build(
    conn: AsyncConnection, *, scope: dict[str, str], run_id: str,
    source_snapshot_id: str, build_key_sha256: str, source_sha256: str,
    predecessor_checkpoint_id: str | None, predecessor_sha256: str,
) -> dict[str, Any] | None:
    values = tuple(scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id"))
    checkpoint_id, lease_id = f"ccp_{uuid.uuid4().hex}", f"cbl_{uuid.uuid4().hex}"
    cursor = await conn.execute(
        """
        insert into conversation_context_checkpoints (
          id, tenant_id, workspace_id, user_id, session_id, agent_id,
          predecessor_checkpoint_id, source_snapshot_id, through_session_generation,
          source_sha256, summary_schema_version, summary_prompt_version,
          model_id, model_value, model_gateway_revision,
          max_input_tokens, max_output_tokens, build_key_sha256,
          state, owner_run_id, builder_lease_id, lease_not_after
        )
        select %s, runs.tenant_id, runs.workspace_id, runs.user_id, runs.session_id,
          runs.agent_id, %s, snapshot.id, runs.session_generation,
          %s, 'ai-platform.context-checkpoint.v1', 'ai-platform.checkpoint-prompt.v1',
          runs.model_id, runs.model_value, runs.model_gateway_revision,
          runs.max_input_tokens, runs.max_output_tokens, %s,
          'building', runs.id, %s, clock_timestamp() + interval '120 seconds'
        from runs
        join run_context_snapshots snapshot on snapshot.tenant_id = runs.tenant_id
          and snapshot.workspace_id = runs.workspace_id and snapshot.user_id = runs.user_id
          and snapshot.session_id = runs.session_id and snapshot.run_id = runs.id
          and snapshot.id = %s and snapshot.context_kind = 'executor'
        join provider_session_heads head on head.tenant_id = runs.tenant_id
          and head.workspace_id = runs.workspace_id and head.user_id = runs.user_id
          and head.session_id = runs.session_id and head.agent_id = runs.agent_id
          and head.active_run_id = runs.id and head.engine = 'claude'
        where runs.tenant_id = %s and runs.workspace_id = %s and runs.user_id = %s
          and runs.session_id = %s and runs.agent_id = %s and runs.id = %s
          and runs.status = 'queued' and runs.cancel_requested_at is null
          and runs.model_id is not null and runs.model_value is not null
          and runs.model_gateway_revision > 0
          and runs.max_input_tokens > 0 and runs.max_output_tokens > 0
          and snapshot.conversation_authority_json->>'source_sha256' = %s
        on conflict (tenant_id, workspace_id, user_id, session_id, agent_id, build_key_sha256)
        do update set builder_lease_id = excluded.builder_lease_id,
          lease_not_after = excluded.lease_not_after, updated_at = clock_timestamp()
        where conversation_context_checkpoints.state = 'building'
          and conversation_context_checkpoints.lease_not_after < clock_timestamp()
          and conversation_context_checkpoints.owner_run_id = excluded.owner_run_id
          and conversation_context_checkpoints.source_snapshot_id = excluded.source_snapshot_id
          and conversation_context_checkpoints.predecessor_checkpoint_id
            is not distinct from excluded.predecessor_checkpoint_id
        returning id, builder_lease_id, predecessor_checkpoint_id,
          summary_text, summary_sha256, source_sha256, covered_message_count,
          covered_turn_count, range_start_created_at, range_start_id,
          range_end_created_at, range_end_id, input_tokens, output_tokens,
          max_input_tokens, max_output_tokens
        """,
        (checkpoint_id, predecessor_checkpoint_id, predecessor_sha256,
         build_key_sha256, lease_id, source_snapshot_id, *values, run_id, source_sha256),
    )
    return await cursor.fetchone()


async def save_checkpoint_progress(
    conn: AsyncConnection, *, checkpoint_id: str, lease_id: str, run_id: str,
    source_sha256: str, covered_message_count: int, covered_turn_count: int,
    range_start: dict[str, str], range_end: dict[str, str],
    summary: str, summary_sha256: str, input_tokens: int, output_tokens: int,
) -> None:
    cursor = await conn.execute(
        """
        update conversation_context_checkpoints checkpoint
        set source_sha256 = %s, covered_message_count = %s, covered_turn_count = %s,
          range_start_created_at = %s::timestamptz, range_start_id = %s,
          range_end_created_at = %s::timestamptz, range_end_id = %s,
          summary_text = %s, summary_sha256 = %s,
          input_tokens = input_tokens + %s, output_tokens = output_tokens + %s,
          lease_not_after = clock_timestamp() + interval '120 seconds',
          updated_at = clock_timestamp()
        from runs
        where checkpoint.id = %s and checkpoint.builder_lease_id = %s
          and checkpoint.state = 'building' and checkpoint.lease_not_after > clock_timestamp()
          and runs.id = checkpoint.owner_run_id and runs.id = %s
          and runs.tenant_id = checkpoint.tenant_id and runs.status = 'queued'
          and runs.cancel_requested_at is null
          and checkpoint.covered_message_count < %s
          and checkpoint.covered_turn_count < %s
        returning checkpoint.id
        """,
        (source_sha256, covered_message_count, covered_turn_count,
         range_start["created_at"], range_start["id"],
         range_end["created_at"], range_end["id"], summary, summary_sha256,
         input_tokens, output_tokens, checkpoint_id, lease_id, run_id,
         covered_message_count, covered_turn_count),
    )
    if await cursor.fetchone() is None:
        raise ValueError("conversation_checkpoint_builder_fenced")


async def assert_checkpoint_lease(conn: AsyncConnection, *, checkpoint_id: str,
                                  lease_id: str, run_id: str) -> None:
    cursor = await conn.execute(
        """
        select checkpoint.id from conversation_context_checkpoints checkpoint
        join runs on runs.id = checkpoint.owner_run_id
          and runs.tenant_id = checkpoint.tenant_id
        where checkpoint.id = %s and checkpoint.builder_lease_id = %s
          and checkpoint.lease_not_after > clock_timestamp()
          and checkpoint.state = 'building' and runs.id = %s
          and runs.status = 'queued' and runs.cancel_requested_at is null
        """, (checkpoint_id, lease_id, run_id),
    )
    if await cursor.fetchone() is None:
        raise ValueError("conversation_checkpoint_builder_fenced")


async def complete_checkpoint_build(conn: AsyncConnection, *, checkpoint_id: str,
                                    lease_id: str, run_id: str, source_sha256: str) -> None:
    cursor = await conn.execute(
        """
        update conversation_context_checkpoints checkpoint
        set state = 'ready', builder_lease_id = null, lease_not_after = null,
          updated_at = clock_timestamp()
        from runs, run_context_snapshots snapshot
        where checkpoint.id = %s and checkpoint.builder_lease_id = %s
          and checkpoint.lease_not_after > clock_timestamp()
          and checkpoint.state = 'building' and checkpoint.owner_run_id = %s
          and runs.id = checkpoint.owner_run_id and runs.tenant_id = checkpoint.tenant_id
          and runs.status = 'queued' and runs.cancel_requested_at is null
          and snapshot.id = checkpoint.source_snapshot_id
          and snapshot.tenant_id = runs.tenant_id and snapshot.run_id = runs.id
          and snapshot.conversation_authority_json->>'source_sha256' = %s
          and checkpoint.covered_message_count > 0 and checkpoint.covered_turn_count > 0
          and checkpoint.summary_text <> '' and checkpoint.source_sha256 <> ''
        returning checkpoint.id
        """, (checkpoint_id, lease_id, run_id, source_sha256),
    )
    if await cursor.fetchone() is None:
        raise ValueError("conversation_checkpoint_builder_fenced")


class PostgresCheckpointBuildRepository:
    load_source = staticmethod(load_checkpoint_build_source)
    find = staticmethod(find_checkpoint_build)
    claim = staticmethod(claim_checkpoint_build)
    assert_lease = staticmethod(assert_checkpoint_lease)
    save_progress = staticmethod(save_checkpoint_progress)
    complete = staticmethod(complete_checkpoint_build)
