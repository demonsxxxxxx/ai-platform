"""Creation persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.auth import normalize_roles
from app.control_plane_contracts import EXECUTOR_RESULT_SCHEMA_VERSION
from app.control_plane_contracts import RUN_CONTRACT_VERSION
from app.control_plane_contracts import RUN_EXECUTION_KIND_HARNESS_CHAT
from app.control_plane_contracts import RUN_EXECUTION_KIND_SKILL
from app.control_plane_contracts import standard_trace_id
from app.conversations.infrastructure.postgres import ensure_workspace_belongs_to_tenant
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from app.platform.postgres.limits import RUN_INPUT_MAX_BYTES
from app.platform.postgres.values import _require_json_size
from app.platform.postgres.values import dumps_json
from app.platform.postgres.values import new_id
from datetime import datetime
from psycopg import AsyncConnection
from typing import Any


ACTIVE_RUN_STATUSES = {"queued", "running"}


RETRYABLE_RUN_STATUSES = {"failed", "dead-letter", "dead_letter", "dead-lettered"}


async def allocate_session_run_generation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None,
    session_id: str,
    agent_id: str,
) -> int:
    """Atomically allocate the sole durable creation generation for one session run."""

    cursor = await conn.execute(
        """
        update sessions
        set next_run_generation = next_run_generation + 1,
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and user_id is not distinct from %s
          and id = %s
          and agent_id = %s
          and status = 'active'
        returning next_run_generation
        """,
        (tenant_id, workspace_id, user_id, session_id, agent_id),
    )
    row = await cursor.fetchone()
    generation = row.get("next_run_generation") if row else None
    if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
        raise RepositoryNotFoundError("session_not_found")
    return generation


async def create_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    session_id: str,
    user_id: str | None,
    agent_id: str,
    skill_id: str | None,
    execution_kind: str = RUN_EXECUTION_KIND_SKILL,
    input_json: dict[str, Any],
    principal_roles: list[str] | None = None,
    principal_department_id: str = "",
    auth_source: str | None = None,
    authz_policy_version: int = 1,
    authority_source: str = "",
    authority_checked_at: str | None = None,
    run_id: str | None = None,
    admitted_agent_profile_revision: int | None = None,
    admitted_agent_profile_hash: str | None = None,
) -> str:
    _require_json_size(
        input_json, max_bytes=RUN_INPUT_MAX_BYTES, code="run_input_too_large"
    )
    if (
        (execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT and skill_id is not None)
        or (execution_kind == RUN_EXECUTION_KIND_SKILL and not skill_id)
        or execution_kind
        not in {
            RUN_EXECUTION_KIND_HARNESS_CHAT,
            RUN_EXECUTION_KIND_SKILL,
        }
    ):
        raise RepositoryConflictError("run_execution_skill_identity_mismatch")
    resolved_run_id = run_id or new_id("run")
    trace_id = standard_trace_id(resolved_run_id)
    await ensure_workspace_belongs_to_tenant(conn, tenant_id=tenant_id, workspace_id=workspace_id)
    session_generation = await allocate_session_run_generation(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
    )
    cursor = await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, execution_kind, skill_id,
          trace_id, schema_version, executor_schema_version,
          principal_roles, principal_department_id, auth_source,
          authz_policy_version, authority_source, authority_checked_at,
          admitted_agent_profile_revision, admitted_agent_profile_hash,
          status, input_json, queued_at,
          session_generation,
          input_token_count, output_token_count, total_token_count, estimated_cost_minor
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb, now(), %s, 0, 0, 0, 0
        from sessions
        where sessions.tenant_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.id = %s
          and sessions.agent_id = %s
        returning id
        """,
        (
            resolved_run_id,
            tenant_id,
            workspace_id,
            session_id,
            user_id,
            agent_id,
            execution_kind,
            skill_id,
            trace_id,
            RUN_CONTRACT_VERSION,
            EXECUTOR_RESULT_SCHEMA_VERSION,
            dumps_json(normalize_roles(principal_roles or [])),
            str(principal_department_id or ""),
            auth_source,
            int(authz_policy_version),
            str(authority_source or auth_source or ""),
            authority_checked_at or None,
            admitted_agent_profile_revision,
            admitted_agent_profile_hash,
            dumps_json(input_json),
            session_generation,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            agent_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("session_not_found")
    return resolved_run_id


async def update_run_auth_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    principal_roles: list[str] | None,
    principal_department_id: str,
    auth_source: str | None,
    authz_policy_version: int,
    authority_source: str,
    authority_checked_at: str | datetime | None,
) -> None:
    """Refresh the server-owned authorization snapshot for one tenant run."""

    await conn.execute(
        """
        update runs
        set principal_roles = %s::jsonb,
            principal_department_id = %s,
            auth_source = %s,
            authz_policy_version = %s,
            authority_source = %s,
            authority_checked_at = %s
        where tenant_id = %s
          and id = %s
        """,
        (
            dumps_json(normalize_roles(principal_roles or [])),
            str(principal_department_id or ""),
            auth_source,
            int(authz_policy_version),
            str(authority_source or auth_source or ""),
            authority_checked_at,
            tenant_id,
            run_id,
        ),
    )


async def get_authorized_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = "for update of runs" if for_update else ""
    cursor = await conn.execute(
        f"""
        select runs.*
        from runs
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        where runs.tenant_id = %s
          and runs.id = %s
          and runs.user_id = %s
          and sessions.status = 'active'
        {lock_clause}
        """,
        (tenant_id, run_id, user_id),
    )
    return await cursor.fetchone()
