from __future__ import annotations

import json
import re
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.limits import (
    CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES,
    PersistenceSizeLimitError,
    ensure_json_size,
)
from app.platform.public_payload import sanitize_public_payload


CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT = 128
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _require_json_size(value: Any, *, max_bytes: int, code: str) -> None:
    try:
        ensure_json_size(value, max_bytes=max_bytes, code=code)
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


async def create_context_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    context_kind: str,
    included_message_ids: list[str],
    included_file_ids: list[str],
    included_artifact_ids: list[str],
    included_memory_record_ids: list[str],
    redaction_summary_json: dict[str, Any],
    payload_json: dict[str, Any],
) -> dict[str, Any]:
    """Atomically authorize and persist one run-scoped context snapshot."""
    snapshot_id = f"ctx_{uuid.uuid4().hex}"
    included_message_ids = _normalize_context_snapshot_member_ids(included_message_ids)
    included_file_ids = _normalize_context_snapshot_member_ids(included_file_ids)
    included_artifact_ids = _normalize_context_snapshot_member_ids(included_artifact_ids)
    included_memory_record_ids = _normalize_context_snapshot_member_ids(included_memory_record_ids)
    if (
        len(included_message_ids)
        + len(included_file_ids)
        + len(included_artifact_ids)
        + len(included_memory_record_ids)
        > CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT
    ):
        raise RepositoryConflictError("context_snapshot_material_invalid")
    redaction_summary_json = sanitize_public_payload(redaction_summary_json)
    if not isinstance(redaction_summary_json, dict):
        redaction_summary_json = {}
    payload_json = sanitize_public_payload(payload_json)
    if not isinstance(payload_json, dict):
        payload_json = {}
    _require_json_size(
        payload_json,
        max_bytes=CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES,
        code="context_snapshot_payload_too_large",
    )
    cursor = await conn.execute(
        """
        with scoped_run as (
          select runs.tenant_id, runs.workspace_id, runs.user_id, runs.session_id,
                 runs.id as run_id, runs.agent_id, runs.trace_id
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
            and sessions.workspace_id = runs.workspace_id
            and sessions.user_id = runs.user_id
            and sessions.agent_id = runs.agent_id
          where runs.tenant_id = %s
            and runs.user_id = %s
            and runs.id = %s
        ), requested_members as (
          select %s::jsonb as message_ids,
                 %s::jsonb as file_ids,
                 %s::jsonb as artifact_ids,
                 %s::jsonb as memory_record_ids
        ), locked_artifacts as materialized (
          select artifacts.id
          from scoped_run
          cross join requested_members
          cross join lateral jsonb_array_elements_text(requested_members.artifact_ids) requested(id)
          join artifacts on artifacts.id = requested.id
            and artifacts.tenant_id = scoped_run.tenant_id
          join runs artifact_run on artifact_run.id = artifacts.run_id
            and artifact_run.tenant_id = artifacts.tenant_id
          where artifact_run.workspace_id = scoped_run.workspace_id
            and artifact_run.user_id = scoped_run.user_id
            and artifact_run.session_id = scoped_run.session_id
            and artifact_run.agent_id = scoped_run.agent_id
            and artifacts.lifecycle_state = 'active'
            and (artifacts.expires_at is null or artifacts.expires_at > statement_timestamp())
          for update of artifacts
        ), locked_memory_records as materialized (
          select memory_records.id
          from scoped_run
          cross join requested_members
          cross join lateral jsonb_array_elements_text(requested_members.memory_record_ids) requested(id)
          join memory_records on memory_records.id = requested.id
            and memory_records.tenant_id = scoped_run.tenant_id
          where memory_records.workspace_id = scoped_run.workspace_id
            and memory_records.user_id = scoped_run.user_id
            and memory_records.session_id = scoped_run.session_id
            and memory_records.agent_id = scoped_run.agent_id
            and memory_records.status = 'active'
            and memory_records.deleted_at is null
            and (memory_records.expires_at is null or memory_records.expires_at > statement_timestamp())
          for update of memory_records
        ), eligible_members as (
          select scoped_run.*, requested_members.*,
            (
              select count(*)
              from jsonb_array_elements_text(requested_members.message_ids) requested(id)
              join messages on messages.id = requested.id
              join sessions message_session on message_session.id = messages.session_id
                and message_session.tenant_id = messages.tenant_id
              join runs message_run on message_run.id = messages.run_id
                and message_run.tenant_id = messages.tenant_id
              where messages.tenant_id = scoped_run.tenant_id
                and messages.session_id = scoped_run.session_id
                and message_session.workspace_id = scoped_run.workspace_id
                and message_session.user_id = scoped_run.user_id
                and message_session.agent_id = scoped_run.agent_id
                and message_run.workspace_id = scoped_run.workspace_id
                and message_run.user_id = scoped_run.user_id
                and message_run.session_id = scoped_run.session_id
                and message_run.agent_id = scoped_run.agent_id
            ) as eligible_message_count,
            (
              select count(*)
              from jsonb_array_elements_text(requested_members.file_ids) requested(id)
              join files on files.id = requested.id
              join sessions file_session on file_session.id = files.session_id
                and file_session.tenant_id = files.tenant_id
              join runs file_run on file_run.id = files.run_id
                and file_run.tenant_id = files.tenant_id
              where files.tenant_id = scoped_run.tenant_id
                and files.workspace_id = scoped_run.workspace_id
                and files.user_id = scoped_run.user_id
                and files.lifecycle_state = 'active'
                and files.session_id = scoped_run.session_id
                and file_session.user_id = scoped_run.user_id
                and file_session.workspace_id = scoped_run.workspace_id
                and file_session.agent_id = scoped_run.agent_id
                and file_run.workspace_id = scoped_run.workspace_id
                and file_run.user_id = scoped_run.user_id
                and file_run.session_id = scoped_run.session_id
                and file_run.agent_id = scoped_run.agent_id
            ) as eligible_file_count,
            (
              select count(*)
              from locked_artifacts
            ) as eligible_artifact_count,
            (
              select count(*)
              from locked_memory_records
            ) as eligible_memory_record_count
          from scoped_run
          cross join requested_members
        )
        insert into run_context_snapshots(
          id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
          schema_version, context_kind, included_message_ids, included_file_ids,
          included_artifact_ids, included_memory_record_ids, redaction_summary_json, payload_json
        )
        select %s, tenant_id, workspace_id, user_id, session_id, run_id, coalesce(trace_id, ''),
               %s, %s, message_ids, file_ids, artifact_ids, memory_record_ids, %s::jsonb, %s::jsonb
        from eligible_members
        where eligible_message_count = jsonb_array_length(message_ids)
          and eligible_file_count = jsonb_array_length(file_ids)
          and eligible_artifact_count = jsonb_array_length(artifact_ids)
          and eligible_memory_record_count = jsonb_array_length(memory_record_ids)
        returning id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
                  schema_version, context_kind, included_message_ids, included_file_ids,
                  included_artifact_ids, included_memory_record_ids, redaction_summary_json,
                  payload_json, created_at
        """,
        (
            tenant_id,
            user_id,
            run_id,
            json.dumps(included_message_ids, ensure_ascii=False),
            json.dumps(included_file_ids, ensure_ascii=False),
            json.dumps(included_artifact_ids, ensure_ascii=False),
            json.dumps(included_memory_record_ids, ensure_ascii=False),
            snapshot_id,
            "ai-platform.context-snapshot.v1",
            context_kind,
            _dumps_json(redaction_summary_json),
            _dumps_json(payload_json),
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("context_snapshot_material_invalid")
    return {
        "id": snapshot_id,
        "tenant_id": str(row.get("tenant_id") or tenant_id),
        "workspace_id": str(row.get("workspace_id") or workspace_id),
        "user_id": str(row.get("user_id") or user_id),
        "session_id": str(row.get("session_id") or session_id),
        "run_id": str(row.get("run_id") or run_id),
        "trace_id": str(row.get("trace_id") or trace_id),
        "schema_version": "ai-platform.context-snapshot.v1",
        "context_kind": context_kind,
        "included_message_ids": included_message_ids,
        "included_file_ids": included_file_ids,
        "included_artifact_ids": included_artifact_ids,
        "included_memory_record_ids": included_memory_record_ids,
        "redaction_summary_json": redaction_summary_json,
        "payload_json": payload_json,
    }


def _normalize_context_snapshot_member_ids(member_ids: list[str]) -> list[str]:
    """Reject malformed or duplicate snapshot members before the atomic SQL seam."""
    if not isinstance(member_ids, list) or len(member_ids) > CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT:
        raise RepositoryConflictError("context_snapshot_material_invalid")
    normalized: list[str] = []
    seen: set[str] = set()
    for member_id in member_ids:
        if not isinstance(member_id, str):
            raise RepositoryConflictError("context_snapshot_material_invalid")
        normalized_id = member_id.strip()
        if not _SAFE_ID_PATTERN.fullmatch(normalized_id) or normalized_id in seen:
            raise RepositoryConflictError("context_snapshot_material_invalid")
        seen.add(normalized_id)
        normalized.append(normalized_id)
    return normalized


async def update_run_context_snapshot_ref(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    context_snapshot_id: str,
    context_snapshot: dict[str, Any],
) -> None:
    if str(context_snapshot.get("context_snapshot_id") or "") != context_snapshot_id:
        raise RepositoryConflictError("context_snapshot_binding_invalid")
    cursor = await conn.execute(
        """
        update runs
        set context_snapshot_id = %s,
            input_json = case
              when runs.context_snapshot_id is null then jsonb_set(
                jsonb_set(coalesce(input_json, '{}'::jsonb), '{context_snapshot_id}', %s::jsonb, true),
                '{context_snapshot}',
                %s::jsonb,
                true
              )
              else input_json
            end
        where tenant_id = %s
          and id = %s
          and exists (
            select 1
            from run_context_snapshots
            where id = %s
              and tenant_id = runs.tenant_id
              and workspace_id = runs.workspace_id
              and user_id = runs.user_id
              and session_id = runs.session_id
              and run_id = runs.id
              and context_kind = 'executor'
          )
          and (
            context_snapshot_id is null
            and coalesce(input_json->>'context_snapshot_id', '') = ''
            or (
              context_snapshot_id = %s
              and input_json->>'context_snapshot_id' = context_snapshot_id
            )
          )
        returning context_snapshot_id
        """,
        (
            context_snapshot_id,
            json.dumps(context_snapshot_id, ensure_ascii=False),
            _dumps_json(context_snapshot),
            tenant_id,
            run_id,
            context_snapshot_id,
            context_snapshot_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None or str(row.get("context_snapshot_id") or "") != context_snapshot_id:
        raise RepositoryConflictError("context_snapshot_binding_invalid")


async def list_context_snapshots(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select run_context_snapshots.id, run_context_snapshots.tenant_id,
               run_context_snapshots.workspace_id, run_context_snapshots.user_id,
               run_context_snapshots.session_id, run_context_snapshots.run_id,
               run_context_snapshots.trace_id, run_context_snapshots.schema_version,
               run_context_snapshots.context_kind, run_context_snapshots.included_message_ids,
               run_context_snapshots.included_file_ids, run_context_snapshots.included_artifact_ids,
               run_context_snapshots.included_memory_record_ids,
               run_context_snapshots.redaction_summary_json, run_context_snapshots.payload_json,
               run_context_snapshots.created_at
        from run_context_snapshots
        where tenant_id = %s and user_id = %s and run_id = %s
        order by created_at desc
        """,
        (tenant_id, user_id, run_id),
    )
    return list(await cursor.fetchall())


async def get_latest_authorized_executor_context_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Compatibility lookup that still returns only the physical run binding."""
    cursor = await conn.execute(
        """
        select context_snapshot.id, context_snapshot.tenant_id, context_snapshot.workspace_id,
               context_snapshot.user_id, context_snapshot.session_id, context_snapshot.run_id,
               context_snapshot.trace_id, context_snapshot.schema_version, context_snapshot.context_kind,
               context_snapshot.included_message_ids, context_snapshot.included_file_ids,
               context_snapshot.included_artifact_ids, context_snapshot.included_memory_record_ids,
               context_snapshot.redaction_summary_json, context_snapshot.payload_json,
               context_snapshot.created_at
        from runs
        join run_context_snapshots context_snapshot
          on context_snapshot.id = runs.context_snapshot_id
          and context_snapshot.tenant_id = runs.tenant_id
          and context_snapshot.workspace_id = runs.workspace_id
          and context_snapshot.user_id = runs.user_id
          and context_snapshot.session_id = runs.session_id
          and context_snapshot.run_id = runs.id
          and context_snapshot.context_kind = 'executor'
        where runs.tenant_id = %s
          and runs.user_id = %s
          and runs.id = %s
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


async def get_bound_executor_context_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Load exactly the run's immutable physical snapshot binding, never the latest row."""

    cursor = await conn.execute(
        """
        select context_snapshot.id, context_snapshot.tenant_id, context_snapshot.workspace_id,
               context_snapshot.user_id, context_snapshot.session_id, context_snapshot.run_id,
               context_snapshot.trace_id, context_snapshot.schema_version, context_snapshot.context_kind,
               context_snapshot.included_message_ids, context_snapshot.included_file_ids,
               context_snapshot.included_artifact_ids, context_snapshot.included_memory_record_ids,
               context_snapshot.redaction_summary_json, context_snapshot.payload_json,
               context_snapshot.created_at
        from runs
        join run_context_snapshots context_snapshot
          on context_snapshot.id = runs.context_snapshot_id
          and context_snapshot.tenant_id = runs.tenant_id
          and context_snapshot.workspace_id = runs.workspace_id
          and context_snapshot.user_id = runs.user_id
          and context_snapshot.session_id = runs.session_id
          and context_snapshot.run_id = runs.id
          and context_snapshot.context_kind = 'executor'
        where runs.tenant_id = %s
          and runs.workspace_id = %s
          and runs.user_id = %s
          and runs.session_id = %s
          and runs.id = %s
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        """,
        (tenant_id, workspace_id, user_id, session_id, run_id),
    )
    return await cursor.fetchone()


async def list_context_share_snapshots_for_target_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    target_session_id: str,
) -> list[dict[str, Any]]:
    """List share/fork snapshots whose public binding names an authorized target session."""
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
               schema_version, context_kind, included_message_ids, included_file_ids,
               included_artifact_ids, included_memory_record_ids, redaction_summary_json,
               payload_json, created_at
        from run_context_snapshots
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and context_kind = 'share_fork'
          and payload_json->'share_fork_context'->>'target_session_id' = %s
        order by created_at desc
        """,
        (tenant_id, workspace_id, user_id, target_session_id),
    )
    return list(await cursor.fetchall())


async def get_context_snapshot_for_worker(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    context_snapshot_id: str,
) -> dict[str, Any] | None:
    """Load a context snapshot only when it matches the full worker run identity."""
    cursor = await conn.execute(
        """
        select run_context_snapshots.id, run_context_snapshots.tenant_id,
               run_context_snapshots.workspace_id, run_context_snapshots.user_id,
               run_context_snapshots.session_id, run_context_snapshots.run_id,
               run_context_snapshots.trace_id, run_context_snapshots.schema_version,
               run_context_snapshots.context_kind, run_context_snapshots.included_message_ids,
               run_context_snapshots.included_file_ids, run_context_snapshots.included_artifact_ids,
               run_context_snapshots.included_memory_record_ids,
               run_context_snapshots.redaction_summary_json, run_context_snapshots.payload_json,
               run_context_snapshots.created_at
        from run_context_snapshots
        join runs on runs.context_snapshot_id = run_context_snapshots.id
          and runs.tenant_id = run_context_snapshots.tenant_id
          and runs.workspace_id = run_context_snapshots.workspace_id
          and runs.user_id = run_context_snapshots.user_id
          and runs.session_id = run_context_snapshots.session_id
          and runs.id = run_context_snapshots.run_id
        where run_context_snapshots.tenant_id = %s
          and run_context_snapshots.workspace_id = %s
          and run_context_snapshots.user_id = %s
          and run_context_snapshots.session_id = %s
          and run_context_snapshots.run_id = %s
          and run_context_snapshots.id = %s
          and run_context_snapshots.context_kind = 'executor'
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        """,
        (tenant_id, workspace_id, user_id, session_id, run_id, context_snapshot_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None
