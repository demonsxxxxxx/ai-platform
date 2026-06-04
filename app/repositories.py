import hashlib
import json
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.control_plane_contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    AUDIT_EVENT_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EXECUTOR_RESULT_SCHEMA_VERSION,
    RUN_CONTRACT_VERSION,
    artifact_manifest_contract,
    sanitize_public_payload,
    sanitize_public_text,
    standard_error_code,
    standard_trace_id,
)
from app.memory_redaction import redact_memory_metadata, redact_memory_text
from app.projection_redaction import sanitize_user_control_input
from app.skills.dependencies import is_workbench_skill_public
from app.skills.release_policy import resolve_rollout_skill_decision


DEFAULT_RUN_EXECUTOR_TYPES = {"claude-agent-worker", "ragflow"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def memory_policy_id(*, tenant_id: str, workspace_id: str, user_id: str, agent_id: str | None) -> str:
    raw = "\x1f".join([tenant_id, workspace_id, user_id, agent_id or ""])
    return f"mempol_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


class RepositoryConflictError(ValueError):
    pass


class RepositoryNotFoundError(ValueError):
    pass


def dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _event_severity(payload: dict[str, Any] | None, severity: str | None = None) -> str:
    candidate = severity or (payload or {}).get("severity") or "info"
    return str(candidate) if str(candidate) in {"info", "warning", "error"} else "info"


def _event_visible(payload: dict[str, Any] | None, visible_to_user: bool | None = None) -> bool:
    if visible_to_user is not None:
        return bool(visible_to_user)
    if isinstance(payload, dict) and "visible_to_user" in payload:
        return bool(payload["visible_to_user"])
    return True


def _event_error_code(payload: dict[str, Any] | None, error_code: str | None = None) -> str | None:
    candidate = error_code or ((payload or {}).get("error_code") if isinstance(payload, dict) else None)
    return standard_error_code(str(candidate)) if candidate else None


def _required_schema_version(row: dict[str, Any], field: str, expected: str, error_code: str) -> str:
    value = row.get(field)
    if value != expected:
        raise RepositoryConflictError(error_code)
    return str(value)


def _result_observability_values(result_json: dict[str, Any] | None) -> tuple[int | None, int, int, int, int]:
    result = result_json or {}
    token_counts = result.get("token_counts") if isinstance(result.get("token_counts"), dict) else {}
    cost = result.get("cost") if isinstance(result.get("cost"), dict) else {}
    latency = result.get("latency_ms")
    try:
        latency_ms = int(latency) if latency is not None else None
    except (TypeError, ValueError):
        latency_ms = None
    try:
        input_tokens = int(token_counts.get("input") or 0)
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = int(token_counts.get("output") or 0)
    except (TypeError, ValueError):
        output_tokens = 0
    try:
        total_tokens = int(token_counts.get("total") or (input_tokens + output_tokens))
    except (TypeError, ValueError):
        total_tokens = input_tokens + output_tokens
    try:
        estimated_cost_minor = int(cost.get("estimated_cost_minor") or 0)
    except (TypeError, ValueError):
        estimated_cost_minor = 0
    return latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor


async def resolve_agent_skill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select
          agents.id as agent_id,
          agents.status as agent_status,
          agents.default_skill_id,
          skills.id as skill_id,
          coalesce(tenant_workbench_skills.status, skills.status) as skill_status,
          coalesce(skill_release_policies.current_version, skills.version) as skill_version,
          skill_release_policies.current_version as release_policy_version,
          skill_release_policies.previous_version as release_policy_previous_version,
          skill_release_policies.rollout_percent as release_policy_rollout_percent,
          skills.executor_type,
          mcp_tools.status as mcp_tool_status,
          skills.input_modes
        from agents
        join skills on skills.id = %s
        left join tenant_workbench_skills
          on tenant_workbench_skills.tenant_id = agents.tenant_id
         and tenant_workbench_skills.skill_id = skills.id
        left join skill_release_policies
          on skill_release_policies.tenant_id = agents.tenant_id
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        left join mcp_tools on mcp_tools.id = skills.id
        where agents.tenant_id = %s and agents.id = %s
        """,
        (skill_id, tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("agent_or_skill_not_found")
    if row["agent_status"] != "active":
        raise RepositoryConflictError("agent_inactive")
    if row["skill_status"] != "active":
        raise RepositoryConflictError("skill_inactive")
    if row["executor_type"] not in DEFAULT_RUN_EXECUTOR_TYPES:
        raise RepositoryConflictError("executor_type_not_allowed")
    if row["executor_type"] == "ragflow" and row.get("mcp_tool_status") != "active":
        raise RepositoryConflictError("mcp_tool_disabled")
    if row["default_skill_id"] != skill_id:
        raise RepositoryConflictError("agent_skill_mismatch")
    return row


async def ensure_mcp_tool_active(conn: AsyncConnection, *, tenant_id: str, tool_id: str) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select id, server_id, status, write_capable, risk_level
        from mcp_tools
        where id = %s
        """,
        (tool_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_tool_not_found")
    if row["status"] != "active":
        raise RepositoryConflictError("mcp_tool_disabled")
    return row


async def list_agent_app_projections(conn: AsyncConnection, *, tenant_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          agents.id as app_id,
          agents.name,
          agents.agent_type,
          agents.default_skill_id,
          agents.status,
          skills.input_modes,
          skills.output_modes
        from agents
        join skills on skills.id = agents.default_skill_id
        where agents.tenant_id = %s
          and agents.id in ('baoyu-translate', 'qa-word-review')
          and agents.status = 'active'
          and skills.status = 'active'
        order by case agents.id
          when 'baoyu-translate' then 1
          when 'qa-word-review' then 2
          else 99
        end
        """,
        (tenant_id,),
    )
    return list(await cursor.fetchall())


async def list_lambchat_agents(conn: AsyncConnection, *, tenant_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          agents.id,
          agents.name,
          agents.description,
          agents.agent_type,
          agents.default_skill_id,
          agents.status,
          skills.version as skill_version,
          skills.input_modes,
          skills.output_modes
        from agents
        join skills on skills.id = agents.default_skill_id
        where agents.tenant_id = %s
          and agents.id in ('general-agent', 'baoyu-translate', 'qa-word-review')
          and agents.status = 'active'
          and skills.status = 'active'
        order by case agents.id
          when 'general-agent' then 1
          when 'baoyu-translate' then 2
          when 'qa-word-review' then 3
          else 99
        end, agents.id asc
        """,
        (tenant_id,),
    )
    return list(await cursor.fetchall())


async def list_workbench_skills(conn: AsyncConnection, *, tenant_id: str, include_disabled: bool = False) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.version,
          skills.description,
          skills.input_modes,
          skills.output_modes,
          skills.executor_type,
          coalesce(tenant_workbench_skills.status, skills.status) as status,
          coalesce(tenant_workbench_skills.visible_to_user, true) as visible_to_user
        from skills
        left join tenant_workbench_skills
          on tenant_workbench_skills.tenant_id = %s
         and tenant_workbench_skills.skill_id = skills.id
        where skills.id in ('general-chat', 'qa-file-reviewer', 'baoyu-translate', 'ragflow-knowledge-search')
          and (%s or coalesce(tenant_workbench_skills.status, skills.status) = 'active')
        order by case skills.id
          when 'general-chat' then 1
          when 'qa-file-reviewer' then 2
          when 'baoyu-translate' then 3
          when 'ragflow-knowledge-search' then 4
          else 99
        end
        """,
        (tenant_id, include_disabled),
    )
    return list(await cursor.fetchall())


async def get_skill(conn: AsyncConnection, *, skill_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id as skill_id, version, status
        from skills
        where id = %s
        """,
        (skill_id,),
    )
    return await cursor.fetchone()


async def list_skill_ids(conn: AsyncConnection) -> list[str]:
    cursor = await conn.execute(
        """
        select id
        from skills
        order by id asc
        """,
        (),
    )
    return [str(row["id"]) for row in list(await cursor.fetchall())]


async def set_workbench_skill_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    if not is_workbench_skill_public(skill_id):
        raise RepositoryNotFoundError("workbench_skill_not_found")
    await conn.execute(
        """
        insert into tenant_workbench_skills(tenant_id, skill_id, status, visible_to_user)
        values (%s, %s, %s, true)
        on conflict (tenant_id, skill_id)
        do update set status = excluded.status, visible_to_user = excluded.visible_to_user
        """,
        (tenant_id, skill_id, status),
    )
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.version,
          skills.description,
          skills.input_modes,
          skills.output_modes,
          skills.executor_type,
          coalesce(tenant_workbench_skills.status, skills.status) as status,
          coalesce(tenant_workbench_skills.visible_to_user, true) as visible_to_user
        from skills
        left join tenant_workbench_skills
          on tenant_workbench_skills.tenant_id = %s
         and tenant_workbench_skills.skill_id = skills.id
        where skills.id = %s
        """,
        (tenant_id, skill_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("skill_not_found")
    return row


async def list_workbench_mcp_tools(conn: AsyncConnection, *, tenant_id: str, include_disabled: bool = True) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id as tool_id,
          server_id,
          name,
          description,
          status,
          write_capable,
          risk_level,
          visible_to_user as allowed_for_user
        from mcp_tools
        where visible_to_user = true
          and (%s or status = 'active')
        order by case id
          when 'ragflow-knowledge-search' then 1
          else 99
        end, id asc
        """,
        (include_disabled,),
    )
    return list(await cursor.fetchall())


async def list_workbench_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_admin_fields: bool = False,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          case agents.id
            when 'general-agent' then 'general_chat'
            when 'qa-word-review' then 'document_review'
            when 'baoyu-translate' then 'document_translation'
            when 'sop-assistant' then 'knowledge_answer'
            else agents.id
          end as capability_id,
          agents.name as label,
          agents.description,
          case
            when skills.executor_type = 'ragflow'
             and coalesce(mcp_tools.status, 'disabled') <> 'active'
            then 'disabled'
            else coalesce(tenant_workbench_skills.status, skills.status)
          end as status,
          skills.input_modes,
          skills.output_modes,
          agents.id as agent_id,
          skills.id as skill_id,
          skills.version as skill_version,
          skills.executor_type,
          case when skills.id = 'ragflow-knowledge-search' then mcp_tools.server_id else null end as mcp_server_id,
          case when skills.id = 'ragflow-knowledge-search' then mcp_tools.id else null end as mcp_tool_id,
          case when skills.id = 'ragflow-knowledge-search' then mcp_tools.risk_level else null end as risk_level,
          0 as recent_failures
        from agents
        join skills on skills.id = agents.default_skill_id
        left join tenant_workbench_skills
          on tenant_workbench_skills.tenant_id = agents.tenant_id
         and tenant_workbench_skills.skill_id = skills.id
        left join mcp_tools
          on mcp_tools.id = skills.id
        where agents.tenant_id = %s
          and agents.id in ('general-agent', 'qa-word-review', 'baoyu-translate', 'sop-assistant')
          and agents.status = 'active'
        order by case agents.id
          when 'general-agent' then 1
          when 'qa-word-review' then 2
          when 'baoyu-translate' then 3
          when 'sop-assistant' then 4
          else 99
        end
        """,
        (tenant_id,),
    )
    rows = list(await cursor.fetchall())
    if include_admin_fields:
        return rows
    redacted = []
    for row in rows:
        item = dict(row)
        item["agent_id"] = None
        item["skill_id"] = None
        item["skill_version"] = None
        item["executor_type"] = None
        item["mcp_server_id"] = None
        item["mcp_tool_id"] = None
        item["risk_level"] = None
        item["recent_failures"] = None
        redacted.append(item)
    return redacted


async def ensure_workspace(conn: AsyncConnection, *, tenant_id: str, workspace_id: str) -> None:
    cursor = await conn.execute(
        """
        select 1
        from workspaces
        where tenant_id = %s and id = %s and status = 'active'
        """,
        (tenant_id, workspace_id),
    )
    if await cursor.fetchone() is None:
        raise RepositoryNotFoundError("workspace_not_found")


async def ensure_user(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    display_name: str | None = None,
) -> None:
    if not user_id:
        return
    await conn.execute(
        """
        insert into users(id, tenant_id, display_name)
        values (%s, %s, %s)
        on conflict (id) do nothing
        """,
        (user_id, tenant_id, display_name or user_id),
    )


async def get_user(conn: AsyncConnection, *, tenant_id: str, user_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, tenant_id, display_name, email, external_id, status, created_at
        from users
        where tenant_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_agent(conn: AsyncConnection, *, tenant_id: str, agent_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, tenant_id, name, agent_type, default_skill_id, status, created_at
        from agents
        where tenant_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    user_id: str | None,
    title: str,
    session_id: str | None = None,
) -> str:
    resolved_id = session_id or new_id("ses")
    if session_id:
        cursor = await conn.execute(
            """
            select id, tenant_id, workspace_id, user_id, agent_id
            from sessions
            where id = %s
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is not None:
            if row["tenant_id"] != tenant_id or row["workspace_id"] != workspace_id or row["agent_id"] != agent_id:
                raise RepositoryConflictError("session_scope_mismatch")
            if row["user_id"] and user_id and row["user_id"] != user_id:
                raise RepositoryConflictError("session_user_mismatch")
            return resolved_id
    await conn.execute(
        """
        insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
        values (%s, %s, %s, %s, %s, %s)
        on conflict (id) do nothing
        """,
        (resolved_id, tenant_id, workspace_id, user_id, agent_id, title),
    )
    return resolved_id


async def create_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    session_id: str,
    user_id: str | None,
    agent_id: str,
    skill_id: str,
    input_json: dict[str, Any],
    principal_roles: list[str] | None = None,
    auth_source: str | None = None,
    run_id: str | None = None,
) -> str:
    resolved_run_id = run_id or new_id("run")
    trace_id = standard_trace_id(resolved_run_id)
    cursor = await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
          trace_id, schema_version, executor_schema_version, principal_roles, auth_source,
          status, input_json, queued_at,
          input_token_count, output_token_count, total_token_count, estimated_cost_minor
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'queued', %s::jsonb, now(), 0, 0, 0, 0
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
            skill_id,
            trace_id,
            RUN_CONTRACT_VERSION,
            EXECUTOR_RESULT_SCHEMA_VERSION,
            dumps_json(principal_roles or []),
            auth_source,
            dumps_json(input_json),
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


async def count_active_runs_for_user(conn: AsyncConnection, *, tenant_id: str, user_id: str) -> int:
    cursor = await conn.execute(
        """
        select count(*) as count
        from runs
        where tenant_id = %s
          and user_id = %s
          and status in ('queued', 'running')
        """,
        (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return int(row["count"] if row else 0)


async def append_event(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    severity: str | None = None,
    visible_to_user: bool | None = None,
    error_code: str | None = None,
    latency_ms: int | None = None,
    input_token_count: int = 0,
    output_token_count: int = 0,
    total_token_count: int = 0,
    estimated_cost_minor: int = 0,
) -> str:
    event_id = new_id("evt")
    payload_json = payload or {}
    resolved_trace_id = trace_id or standard_trace_id(run_id)
    await conn.execute(
        """
        insert into run_events(
          id, tenant_id, run_id, trace_id, schema_version, sequence, event_type, stage, message,
          severity, visible_to_user, error_code, latency_ms,
          input_token_count, output_token_count, total_token_count, estimated_cost_minor,
          payload_json
        )
        values (
          %s, %s, %s, %s, %s,
          (select coalesce(max(sequence), 0) + 1 from run_events where tenant_id = %s and run_id = %s),
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        """,
        (
            event_id,
            tenant_id,
            run_id,
            resolved_trace_id,
            EVENT_ENVELOPE_SCHEMA_VERSION,
            tenant_id,
            run_id,
            event_type,
            stage,
            message,
            _event_severity(payload_json, severity),
            _event_visible(payload_json, visible_to_user),
            _event_error_code(payload_json, error_code),
            latency_ms,
            int(input_token_count or 0),
            int(output_token_count or 0),
            int(total_token_count or 0),
            int(estimated_cost_minor or 0),
            dumps_json(payload_json),
        ),
    )
    return event_id


async def get_run(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "select * from runs where tenant_id = %s and id = %s",
        (tenant_id, run_id),
    )
    return await cursor.fetchone()


async def get_run_identity(conn: AsyncConnection, *, run_id: str, for_update: bool = False) -> dict[str, Any] | None:
    sql = "select id, tenant_id, session_id, status from runs where id = %s"
    if for_update:
        sql = f"{sql} for update"
    cursor = await conn.execute(
        sql,
        (run_id,),
    )
    return await cursor.fetchone()


async def get_authorized_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from runs
        where tenant_id = %s
          and id = %s
          and user_id = %s
        """,
        (tenant_id, run_id, user_id),
    )
    return await cursor.fetchone()


async def get_authorized_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from sessions
        where tenant_id = %s
          and id = %s
          and user_id = %s
        """,
        (tenant_id, session_id, user_id),
    )
    return await cursor.fetchone()


async def list_run_events(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    after_sequence: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sequence_filter = "and sequence > %s" if after_sequence is not None else ""
    limit_clause = "limit %s" if limit is not None else ""
    params: list[Any] = [tenant_id, run_id]
    if after_sequence is not None:
        params.append(int(after_sequence))
    if limit is not None:
        params.append(int(limit))
    cursor = await conn.execute(
        f"""
        select id, trace_id, schema_version, sequence, event_type, stage, message, severity, visible_to_user,
               error_code, latency_ms, input_token_count, output_token_count, total_token_count,
               estimated_cost_minor, payload_json, created_at
        from run_events
        where tenant_id = %s and run_id = %s
          {sequence_filter}
        order by sequence asc, created_at asc
        {limit_clause}
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())


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
    snapshot_id = new_id("ctx")
    redaction_summary_json = sanitize_public_payload(redaction_summary_json)
    if not isinstance(redaction_summary_json, dict):
        redaction_summary_json = {}
    payload_json = sanitize_public_payload(payload_json)
    if not isinstance(payload_json, dict):
        payload_json = {}
    cursor = await conn.execute(
        """
        with scoped_run as (
          select runs.id
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
            and sessions.workspace_id = runs.workspace_id
            and sessions.user_id = runs.user_id
            and sessions.agent_id = runs.agent_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id = %s
        )
        insert into run_context_snapshots(
          id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
          schema_version, context_kind, included_message_ids, included_file_ids,
          included_artifact_ids, included_memory_record_ids, redaction_summary_json, payload_json
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
        from scoped_run
        returning id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
                  schema_version, context_kind, included_message_ids, included_file_ids,
                  included_artifact_ids, included_memory_record_ids, redaction_summary_json,
                  payload_json, created_at
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            snapshot_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            trace_id,
            "ai-platform.context-snapshot.v1",
            context_kind,
            json.dumps(included_message_ids, ensure_ascii=False),
            json.dumps(included_file_ids, ensure_ascii=False),
            json.dumps(included_artifact_ids, ensure_ascii=False),
            json.dumps(included_memory_record_ids, ensure_ascii=False),
            dumps_json(redaction_summary_json),
            dumps_json(payload_json),
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("run_not_found")
    return {
        "id": snapshot_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "schema_version": "ai-platform.context-snapshot.v1",
        "context_kind": context_kind,
        "included_message_ids": included_message_ids,
        "included_file_ids": included_file_ids,
        "included_artifact_ids": included_artifact_ids,
        "included_memory_record_ids": included_memory_record_ids,
        "redaction_summary_json": redaction_summary_json,
        "payload_json": payload_json,
    }


async def update_run_context_snapshot_ref(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    context_snapshot_id: str,
    context_snapshot: dict[str, Any],
) -> None:
    await conn.execute(
        """
        update runs
        set input_json = jsonb_set(
          jsonb_set(coalesce(input_json, '{}'::jsonb), '{context_snapshot_id}', %s::jsonb, true),
          '{context_snapshot}',
          %s::jsonb,
          true
        )
        where tenant_id = %s and id = %s
        """,
        (
            json.dumps(context_snapshot_id, ensure_ascii=False),
            dumps_json(context_snapshot),
            tenant_id,
            run_id,
        ),
    )


async def list_context_snapshots(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
               schema_version, context_kind, included_message_ids, included_file_ids,
               included_artifact_ids, included_memory_record_ids, redaction_summary_json,
               payload_json, created_at
        from run_context_snapshots
        where tenant_id = %s and user_id = %s and run_id = %s
        order by created_at desc
        """,
        (tenant_id, user_id, run_id),
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
        select id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
               schema_version, context_kind, included_message_ids, included_file_ids,
               included_artifact_ids, included_memory_record_ids, redaction_summary_json,
               payload_json, created_at
        from run_context_snapshots
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and session_id = %s
          and run_id = %s
          and id = %s
        """,
        (tenant_id, workspace_id, user_id, session_id, run_id, context_snapshot_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


def _default_memory_policy(*, tenant_id: str, workspace_id: str, user_id: str, agent_id: str | None) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "source": "default",
        "reason": "",
        "updated_by": "",
        "updated_at": None,
    }


def _memory_policy_from_row(row: dict[str, Any], *, source: str = "stored") -> dict[str, Any]:
    return {
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": row.get("agent_id"),
        "memory_enabled": bool(row.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": int(row.get("retention_days") or 90),
        "source": source,
        "reason": str(row.get("reason") or ""),
        "updated_by": str(row.get("updated_by") or ""),
        "updated_at": row.get("updated_at"),
    }


async def get_effective_memory_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id,
               memory_enabled, long_term_memory_enabled, retention_days,
               reason, updated_by, updated_at
        from memory_policies
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and (agent_id = %s or agent_id is null)
        order by case when agent_id = %s then 0 else 1 end, updated_at desc
        limit 1
        """,
        (tenant_id, workspace_id, user_id, agent_id, agent_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return _default_memory_policy(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    return _memory_policy_from_row(dict(row))


async def set_memory_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
    memory_enabled: bool,
    long_term_memory_enabled: bool,
    retention_days: int,
    reason: str,
    updated_by: str,
) -> dict[str, Any]:
    if long_term_memory_enabled:
        raise RepositoryConflictError("long_term_memory_not_available")
    policy_id = memory_policy_id(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    cursor = await conn.execute(
        """
        insert into memory_policies(
          id, tenant_id, workspace_id, user_id, agent_id,
          memory_enabled, long_term_memory_enabled, retention_days,
          reason, updated_by
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (id) do update
        set memory_enabled = excluded.memory_enabled,
            long_term_memory_enabled = excluded.long_term_memory_enabled,
            retention_days = excluded.retention_days,
            reason = excluded.reason,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, workspace_id, user_id, agent_id,
                  memory_enabled, long_term_memory_enabled, retention_days,
                  reason, updated_by, updated_at
        """,
        (
            policy_id,
            tenant_id,
            workspace_id,
            user_id,
            agent_id,
            bool(memory_enabled),
            bool(long_term_memory_enabled),
            int(retention_days),
            reason,
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    return _memory_policy_from_row(dict(row))


async def create_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
    session_id: str | None,
    record_type: str,
    content: str,
    metadata_json: dict[str, Any],
    retention_days: int = 90,
) -> dict[str, Any]:
    if not session_id:
        raise RepositoryConflictError("memory_session_id_required")
    if not agent_id:
        raise RepositoryConflictError("memory_agent_id_required")
    retention_days = int(retention_days)
    if retention_days <= 0:
        raise RepositoryConflictError("memory_retention_days_invalid")
    record_id = new_id("mem")
    redacted_content = redact_memory_text(content)
    redacted_metadata = redact_memory_metadata(metadata_json)
    cursor = await conn.execute(
        """
        insert into memory_records(
          id, tenant_id, workspace_id, user_id, agent_id, session_id,
          record_type, content, metadata_json, expires_at
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now() + (%s * interval '1 day')
        from sessions
        where sessions.tenant_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.id = %s
          and sessions.agent_id = %s
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, content, metadata_json, status, expires_at,
                  deleted_at, created_at, updated_at
        """,
        (
            record_id,
            tenant_id,
            workspace_id,
            user_id,
            agent_id,
            session_id,
            record_type,
            redacted_content,
            dumps_json(redacted_metadata),
            retention_days,
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
    return dict(row)


async def list_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, content, metadata_json, status, expires_at,
               deleted_at, created_at, updated_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and status = 'active'
          and deleted_at is null
          and (%s::text is null or agent_id = %s)
          and (%s::text is null or session_id = %s)
          and (expires_at is null or expires_at > now())
        order by created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, agent_id, agent_id, session_id, session_id, limit),
    )
    return list(await cursor.fetchall())


async def list_admin_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    status: str = "active",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status not in {"active", "deleted", "all"}:
        raise RepositoryConflictError("memory_status_invalid")
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, status, expires_at, deleted_at, created_at, updated_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and (%s::text is null or user_id = %s)
          and (%s = 'all' or status = %s)
        order by coalesce(deleted_at, expires_at, created_at) desc, created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, user_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def delete_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    record_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and agent_id = %s
          and session_id = %s
          and id = %s
          and status = 'active'
          and deleted_at is null
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, user_id, agent_id, session_id, record_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def admin_delete_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    record_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and id = %s
          and status = 'active'
          and deleted_at is null
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, record_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def cleanup_expired_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if not workspace_id:
        raise RepositoryConflictError("memory_workspace_id_required")
    limit = int(limit)
    if limit <= 0:
        raise RepositoryConflictError("memory_cleanup_limit_invalid")
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where id in (
          select id
          from memory_records
          where tenant_id = %s
            and workspace_id = %s
            and status = 'active'
            and deleted_at is null
            and expires_at is not null
            and expires_at <= now()
          order by expires_at asc, created_at asc
          limit %s
          for update skip locked
        )
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, limit),
    )
    return list(await cursor.fetchall())


async def create_tool_permission_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    tool_id: str,
    tool_call_id: str,
    action: str,
    risk_level: str,
    write_capable: bool,
    reason: str,
    request_payload_json: dict[str, Any],
) -> dict[str, Any]:
    request_id = new_id("tpr")
    await conn.execute(
        """
        insert into run_tool_permission_requests(
          id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
          tool_id, tool_call_id, action, risk_level, write_capable, reason, request_payload_json
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            request_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            trace_id,
            tool_id,
            tool_call_id,
            action,
            risk_level,
            write_capable,
            reason,
            dumps_json(request_payload_json),
        ),
    )
    return {
        "id": request_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "tool_id": tool_id,
        "tool_call_id": tool_call_id,
        "action": action,
        "risk_level": risk_level,
        "write_capable": write_capable,
        "status": "pending",
        "reason": reason,
        "request_payload_json": request_payload_json,
    }


async def get_tool_permission_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from run_tool_permission_requests
        where tenant_id = %s and user_id = %s and run_id = %s and id = %s
        """,
        (tenant_id, user_id, run_id, request_id),
    )
    return await cursor.fetchone()


async def decide_tool_permission_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    request_id: str,
    decision: str,
    reason: str,
    decision_payload_json: dict[str, Any],
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update run_tool_permission_requests
        set status = 'decided',
            decision = %s,
            reason = %s,
            decision_payload_json = %s::jsonb,
            decided_at = now(),
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'pending'
        returning *
        """,
        (decision, reason, dumps_json(decision_payload_json), tenant_id, user_id, run_id, request_id),
    )
    return await cursor.fetchone()


async def get_latest_tool_permission_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    tool_id: str,
    action: str = "execute",
    tool_call_id: str | None = None,
    request_payload_json: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    exact_clauses: list[str] = []
    params: list[Any] = [tenant_id, user_id, run_id, tool_id, action]
    if tool_call_id:
        exact_clauses.append("(decision in ('allow_once', 'deny') and tool_call_id = %s)")
        params.append(tool_call_id)
    request_payload = request_payload_json if isinstance(request_payload_json, dict) else {}
    for fingerprint_key in ("command_sha256", "input_sha256"):
        fingerprint_value = request_payload.get(fingerprint_key)
        if isinstance(fingerprint_value, str) and fingerprint_value:
            exact_clauses.append("(decision = 'allow_for_run' and request_payload_json ->> %s = %s)")
            params.extend([fingerprint_key, fingerprint_value])
            break
    if not exact_clauses:
        return None
    exact_filter = f"and ({' or '.join(exact_clauses)})" if exact_clauses else ""
    cursor = await conn.execute(
        f"""
        select *
        from run_tool_permission_requests
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and tool_id = %s
          and action = %s
          and status = 'decided'
          and (expires_at is null or expires_at > now())
          {exact_filter}
        order by decided_at desc, updated_at desc, created_at desc
        limit 1
        """,
        tuple(params),
    )
    return await cursor.fetchone()


async def consume_tool_permission_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update run_tool_permission_requests
        set status = 'consumed',
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and decision = 'allow_once'
          and status = 'decided'
          and (expires_at is null or expires_at > now())
        returning *
        """,
        (tenant_id, user_id, run_id, request_id),
    )
    return await cursor.fetchone()


async def create_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    sandbox_mode: str,
    provider: str,
    browser_enabled: bool,
    ttl_seconds: int,
    resource_limits_json: dict[str, Any],
    user_visible_payload_json: dict[str, Any],
    lease_payload_json: dict[str, Any],
) -> dict[str, Any]:
    lease_id = new_id("lease")
    cursor = await conn.execute(
        """
        insert into sandbox_leases(
          id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
          sandbox_mode, provider, browser_enabled, resource_limits_json,
          user_visible_payload_json, lease_payload_json, heartbeat_at, expires_at
        )
        values (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s::jsonb, %s::jsonb, %s::jsonb, now(), now() + (%s * interval '1 second')
        )
        returning *
        """,
        (
            lease_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            trace_id,
            sandbox_mode,
            provider,
            browser_enabled,
            dumps_json(resource_limits_json),
            dumps_json(user_visible_payload_json),
            dumps_json(lease_payload_json),
            int(ttl_seconds),
        ),
    )
    row = await cursor.fetchone()
    return row or {
        "id": lease_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "sandbox_mode": sandbox_mode,
        "provider": provider,
        "status": "active",
        "browser_enabled": browser_enabled,
        "resource_limits_json": resource_limits_json,
        "user_visible_payload_json": user_visible_payload_json,
        "lease_payload_json": lease_payload_json,
    }


async def get_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s and user_id = %s and run_id = %s and id = %s
        """,
        (tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def renew_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    ttl_seconds: int,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set heartbeat_at = now(),
            expires_at = now() + (%s * interval '1 second'),
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'active'
          and (expires_at is null or expires_at > now())
        returning *
        """,
        (int(ttl_seconds), tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def release_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    reason: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def release_active_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    reason: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_active_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return only active sandbox lease rows for runtime cleanup."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
          and status = 'active'
        order by created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def release_stopped_sandbox_leases_for_cancel(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    reason: str,
    lease_ids: list[str],
    trace_id: str | None = None,
    requested_by_role: str | None = None,
) -> list[dict[str, Any]]:
    """Release leases after their runtime containers have been stopped."""
    if not lease_ids:
        return []
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and id = any(%s)
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, run_id, lease_ids),
    )
    released_leases = list(await cursor.fetchall())
    for lease in released_leases:
        payload: dict[str, Any] = {
            "visible_to_user": True,
            "lease_id": lease.get("id"),
            "reason": reason,
        }
        if requested_by_role:
            payload["requested_by_role"] = requested_by_role
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=lease.get("trace_id") or trace_id,
            event_type="sandbox_lease_released",
            stage="sandbox",
            message="已因取消释放 Sandbox 租约",
            payload=payload,
        )
    return released_leases


async def cleanup_expired_sandbox_leases(
    conn: AsyncConnection,
    *,
    tenant_id: str | None = None,
    reason: str = "expired",
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where (%s::text is null or tenant_id = %s)
          and status = 'active'
          and expires_at is not null
          and expires_at <= now()
        returning id, tenant_id, run_id, trace_id, release_reason
        """,
        (reason, tenant_id, tenant_id),
    )
    rows = list(await cursor.fetchall())
    for lease in rows:
        await append_event(
            conn,
            tenant_id=str(lease["tenant_id"]),
            run_id=str(lease["run_id"]),
            trace_id=lease.get("trace_id"),
            event_type="sandbox_lease_released",
            stage="sandbox",
            message="已释放过期 Sandbox 租约",
            payload={
                "visible_to_user": True,
                "lease_id": lease.get("id"),
                "reason": reason,
            },
        )
    return rows


async def list_sandbox_leases(conn: AsyncConnection, *, tenant_id: str, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and (%s::text is null or status = %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def list_run_artifacts(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select id, trace_id, artifact_type, label, content_type, storage_key, size_bytes, manifest_version, manifest_json, created_at
        from artifacts
        where tenant_id = %s and run_id = %s
        order by created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def upsert_run_skill_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_id: str,
    skill_version: str,
    content_hash: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    allowed: bool,
    staged: bool,
    used: bool,
    used_skills_source: str = "",
    inferred_used: bool = False,
) -> None:
    await conn.execute(
        """
        insert into run_skill_snapshots(
          id, tenant_id, run_id, skill_id, skill_version, content_hash,
          source_json, dependency_ids, allowed, staged, used, used_skills_source, inferred_used
        )
        values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
        on conflict (tenant_id, run_id, skill_id)
        do update set
          skill_version = excluded.skill_version,
          content_hash = excluded.content_hash,
          source_json = excluded.source_json,
          dependency_ids = excluded.dependency_ids,
          allowed = run_skill_snapshots.allowed or excluded.allowed,
          staged = run_skill_snapshots.staged or excluded.staged,
          used = run_skill_snapshots.used or excluded.used,
          used_skills_source = case
            when excluded.used then excluded.used_skills_source
            when run_skill_snapshots.used then run_skill_snapshots.used_skills_source
            when excluded.used_skills_source <> '' then excluded.used_skills_source
            else run_skill_snapshots.used_skills_source
          end,
          inferred_used = case
            when run_skill_snapshots.used or excluded.used then false
            else run_skill_snapshots.inferred_used or excluded.inferred_used
          end
        """,
        (
            new_id("rss"),
            tenant_id,
            run_id,
            skill_id,
            skill_version,
            content_hash,
            dumps_json(source_json),
            json.dumps(dependency_ids, ensure_ascii=False),
            allowed,
            staged,
            used,
            used_skills_source,
            inferred_used,
        ),
    )


async def list_run_skill_snapshots(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          skill_id,
          skill_version,
          content_hash,
          source_json,
          dependency_ids,
          allowed,
          staged,
          used,
          used_skills_source,
          inferred_used,
          created_at
        from run_skill_snapshots
        where tenant_id = %s and run_id = %s
        order by skill_id asc
        """,
        (tenant_id, run_id),
    )
    rows = list(await cursor.fetchall())
    snapshots = []
    for row in rows:
        source = sanitize_public_payload(row.get("source_json") if isinstance(row.get("source_json"), dict) else {})
        if not isinstance(source, dict):
            source = {}
        dependency_ids = row.get("dependency_ids") if isinstance(row.get("dependency_ids"), list) else []
        used_skills_source = str(row.get("used_skills_source") or "").strip()
        inferred_used = bool(row.get("inferred_used"))
        usage: dict[str, Any] = {}
        if used_skills_source:
            usage["used_skills_source"] = sanitize_public_text(used_skills_source)
        if inferred_used:
            usage["inferred_used"] = True
            usage["inferred_used_skills"] = [str(row["skill_id"])]
        snapshot = {
            "skill_id": row["skill_id"],
            "skill_version": row["skill_version"],
            "content_hash": row["content_hash"],
            "source": source,
            "dependency_ids": [str(item) for item in dependency_ids],
            "allowed": bool(row["allowed"]),
            "staged": bool(row["staged"]),
            "used": bool(row["used"]),
            "created_at": row["created_at"],
        }
        if usage:
            snapshot["usage"] = usage
        snapshots.append(snapshot)
    return snapshots


def _sanitize_skill_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = sanitize_public_payload(snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {})
    if not isinstance(source, dict):
        source = {}
    usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
    sanitized_usage: dict[str, Any] = {}
    for key, value in usage.items():
        if isinstance(value, str):
            sanitized_text = sanitize_public_text(value)
            if sanitized_text:
                sanitized_usage[key] = sanitized_text
        elif isinstance(value, list):
            cleaned = [sanitize_public_text(item) for item in value]
            sanitized_usage[key] = [item for item in cleaned if item]
        elif isinstance(value, (int, bool)):
            sanitized_usage[key] = value
    sanitized = {
        "skill_id": str(snapshot.get("skill_id") or ""),
        "skill_version": str(snapshot.get("skill_version") or ""),
        "content_hash": str(snapshot.get("content_hash") or ""),
        "source": source,
        "dependency_ids": [str(item) for item in snapshot.get("dependency_ids") or []],
        "allowed": bool(snapshot.get("allowed")),
        "staged": bool(snapshot.get("staged")),
        "used": bool(snapshot.get("used")),
        "created_at": snapshot.get("created_at"),
    }
    if sanitized_usage:
        sanitized["usage"] = sanitized_usage
    return sanitized


def _skill_usage_from_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    usage_by_skill: dict[str, dict[str, Any]] = {}
    ordered_events = sorted(events, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    for item in ordered_events:
        if item.get("event_type") != "skill_used":
            continue
        if bool(item.get("visible_to_user", True)):
            continue
        payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}
        skill_id = str(payload.get("skill_id") or payload.get("skill_name") or "").strip()
        if not skill_id:
            continue
        usage = usage_by_skill.setdefault(
            skill_id,
            {
                "event_source": "",
                "event_count": 0,
                "tool_use_ids": [],
            },
        )
        usage["event_count"] += 1
        event_source = sanitize_public_text(payload.get("source")).strip()
        if event_source and not usage["event_source"]:
            usage["event_source"] = event_source
        tool_use_id = sanitize_public_text(payload.get("tool_use_id")).strip()
        if tool_use_id and tool_use_id not in usage["tool_use_ids"]:
            usage["tool_use_ids"].append(tool_use_id)
    for usage in usage_by_skill.values():
        usage["tool_use_ids"].sort()
    return usage_by_skill


def _attach_skill_usage(
    skill_snapshots: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    usage_by_skill = _skill_usage_from_events(events)
    if not usage_by_skill:
        return skill_snapshots
    enriched: list[dict[str, Any]] = []
    for snapshot in skill_snapshots:
        usage = usage_by_skill.get(str(snapshot.get("skill_id") or ""))
        if usage:
            persisted_usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
            enriched.append({**snapshot, "usage": {**persisted_usage, **usage}})
        else:
            enriched.append(snapshot)
    return enriched


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _project_skill_version(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_id": row["skill_id"],
        "version": row["version"],
        "content_hash": row["content_hash"],
        "description": row.get("description") or "",
        "source": _json_dict(row.get("source_json")),
        "dependency_ids": _json_list(row.get("dependency_ids")),
        "status": row.get("status") or "active",
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
    }


def _project_skill_release_policy(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "skill_id": row["skill_id"],
        "channel": row.get("channel") or "stable",
        "current_version": row["current_version"],
        "previous_version": row.get("previous_version"),
        "rollout_percent": int(row.get("rollout_percent") or 0),
        "status": row.get("status") or "active",
        "promoted_by": row.get("promoted_by"),
        "promoted_at": row.get("promoted_at"),
    }


async def upsert_skill_version(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    content_hash: str,
    description: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    status: str = "active",
    created_by: str | None = None,
) -> None:
    await conn.execute(
        """
        insert into skill_versions(
          id, skill_id, version, content_hash, description, source_json,
          dependency_ids, status, created_by
        )
        values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        on conflict (skill_id, version)
        do nothing
        """,
        (
            new_id("skv"),
            skill_id,
            version,
            content_hash,
            description,
            dumps_json(source_json),
            json.dumps(dependency_ids, ensure_ascii=False),
            status,
            created_by,
        ),
    )


async def update_skill_catalog_version(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    description: str,
) -> None:
    await conn.execute(
        """
        update skills
        set version = %s,
            description = %s
        where id = %s
        """,
        (version, description, skill_id),
    )


async def backfill_builtin_skill_version_snapshot(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    description: str,
) -> None:
    serialized_source_json = dumps_json(source_json)
    serialized_dependency_ids = json.dumps(dependency_ids, ensure_ascii=False)
    await conn.execute(
        """
        update skill_versions
        set source_json = %s::jsonb,
            dependency_ids = %s::jsonb,
            description = %s
        where skill_id = %s
          and version = %s
          and source_json->>'kind' = 'builtin'
          and (
            not (source_json ? 'files')
            or source_json->'files' is distinct from (%s::jsonb->'files')
            or dependency_ids <> %s::jsonb
            or source_json->'dependency_manifests' is distinct from (%s::jsonb->'dependency_manifests')
          )
        """,
        (
            serialized_source_json,
            serialized_dependency_ids,
            description,
            skill_id,
            version,
            serialized_source_json,
            serialized_dependency_ids,
            serialized_source_json,
        ),
    )


async def get_skill_version(conn: AsyncConnection, *, skill_id: str, version: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select
          skill_id,
          version,
          content_hash,
          description,
          source_json,
          dependency_ids,
          status,
          created_by,
          created_at
        from skill_versions
        where skill_id = %s and version = %s
        """,
        (skill_id, version),
    )
    row = await cursor.fetchone()
    return _project_skill_version(row) if row is not None else None


async def get_effective_skill_version_for_policy(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
) -> dict[str, Any] | None:
    return await get_skill_version(conn, skill_id=skill_id, version=version)


async def list_skill_versions(conn: AsyncConnection, *, skill_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          skill_id,
          version,
          content_hash,
          description,
          source_json,
          dependency_ids,
          status,
          created_by,
          created_at
        from skill_versions
        where skill_id = %s
        order by created_at desc, version desc
        """,
        (skill_id,),
    )
    return [_project_skill_version(row) for row in list(await cursor.fetchall())]


async def get_skill_release_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    channel: str = "stable",
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select
          skill_id,
          channel,
          current_version,
          previous_version,
          rollout_percent,
          status,
          promoted_by,
          promoted_at
        from skill_release_policies
        where tenant_id = %s and skill_id = %s and channel = %s and status = 'active'
        """,
        (tenant_id, skill_id, channel),
    )
    return _project_skill_release_policy(await cursor.fetchone())


async def set_skill_release_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    version: str,
    previous_version: str | None,
    promoted_by: str | None,
    channel: str = "stable",
    rollout_percent: int = 100,
    status: str = "active",
) -> None:
    await conn.execute(
        """
        insert into skill_release_policies(
          id, tenant_id, skill_id, channel, current_version, previous_version,
          rollout_percent, status, promoted_by, promoted_at, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
        on conflict (tenant_id, skill_id, channel)
        do update set
          current_version = excluded.current_version,
          previous_version = excluded.previous_version,
          rollout_percent = excluded.rollout_percent,
          status = excluded.status,
          promoted_by = excluded.promoted_by,
          promoted_at = now(),
          updated_at = now()
        """,
        (
            new_id("skr"),
            tenant_id,
            skill_id,
            channel,
            version,
            previous_version,
            rollout_percent,
            status,
            promoted_by,
        ),
    )


async def diff_skill_versions(
    conn: AsyncConnection,
    *,
    skill_id: str,
    from_version: str,
    to_version: str,
) -> dict[str, Any]:
    source = await get_skill_version(conn, skill_id=skill_id, version=from_version)
    target = await get_skill_version(conn, skill_id=skill_id, version=to_version)
    if source is None or target is None:
        raise RepositoryNotFoundError("skill_version_not_found")
    source_dependencies = set(_json_list(source.get("dependency_ids")))
    target_dependencies = set(_json_list(target.get("dependency_ids")))
    return {
        "skill_id": skill_id,
        "from_version": from_version,
        "to_version": to_version,
        "content_hash_changed": source.get("content_hash") != target.get("content_hash"),
        "description_changed": source.get("description") != target.get("description"),
        "source_changed": source.get("source") != target.get("source"),
        "dependency_added": sorted(target_dependencies - source_dependencies),
        "dependency_removed": sorted(source_dependencies - target_dependencies),
    }


async def get_admin_skill_detail(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.version,
          skills.description,
          skills.input_modes,
          skills.output_modes,
          skills.executor_type,
          coalesce(tenant_workbench_skills.status, skills.status) as status,
          coalesce(tenant_workbench_skills.visible_to_user, false) as visible_to_user
        from skills
        left join tenant_workbench_skills
          on tenant_workbench_skills.tenant_id = %s
         and tenant_workbench_skills.skill_id = skills.id
        where skills.id = %s
        """,
        (tenant_id, skill_id),
    )
    skill = await cursor.fetchone()
    if skill is None:
        return None

    versions = await list_skill_versions(conn, skill_id=skill_id)
    release_policy = await get_skill_release_policy(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
    )
    snapshots_cursor = await conn.execute(
        """
        select
          run_id,
          skill_id,
          skill_version,
          content_hash,
          source_json,
          dependency_ids,
          allowed,
          staged,
          used,
          used_skills_source,
          inferred_used,
          created_at
        from run_skill_snapshots
        where tenant_id = %s and skill_id = %s
        order by created_at desc
        limit 20
        """,
        (tenant_id, skill_id),
    )
    snapshots = []
    for row in list(await snapshots_cursor.fetchall()):
        used_skills_source = str(row.get("used_skills_source") or "").strip()
        inferred_used = bool(row.get("inferred_used"))
        snapshot = {
            "run_id": row["run_id"],
            "skill_id": row["skill_id"],
            "skill_version": row["skill_version"],
            "content_hash": row["content_hash"],
            "source": _json_dict(row.get("source_json")),
            "dependency_ids": _json_list(row.get("dependency_ids")),
            "allowed": bool(row["allowed"]),
            "staged": bool(row["staged"]),
            "used": bool(row["used"]),
            "created_at": row.get("created_at"),
        }
        usage: dict[str, Any] = {}
        if used_skills_source:
            usage["used_skills_source"] = used_skills_source
        if inferred_used:
            usage["inferred_used"] = True
            usage["inferred_used_skills"] = [str(row["skill_id"])]
        if usage:
            snapshot["usage"] = usage
        snapshots.append(snapshot)

    return {
        "skill": dict(skill),
        "release_policy": release_policy,
        "versions": versions,
        "recent_snapshots": snapshots,
    }


async def upsert_run_step(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    step_key: str,
    step_kind: str,
    status: str,
    title: str,
    role: str | None,
    sequence: int,
    payload_json: dict[str, Any],
) -> str:
    step_id = new_id("step")
    cursor = await conn.execute(
        """
        insert into run_steps(
          id, tenant_id, run_id, step_key, step_kind, status, title, role, sequence,
          payload_json, started_at, finished_at
        )
        values (
          %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s::jsonb,
          case when %s in ('running', 'succeeded', 'failed') then now() else null end,
          case when %s in ('succeeded', 'failed', 'cancelled') then now() else null end
        )
        on conflict (tenant_id, run_id, step_key)
        do update set
          step_kind = excluded.step_kind,
          status = excluded.status,
          title = excluded.title,
          role = excluded.role,
          sequence = excluded.sequence,
          payload_json = run_steps.payload_json || excluded.payload_json,
          started_at = coalesce(run_steps.started_at, excluded.started_at),
          finished_at = coalesce(excluded.finished_at, run_steps.finished_at),
          updated_at = now()
        returning id
        """,
        (
            step_id,
            tenant_id,
            run_id,
            step_key,
            step_kind,
            status,
            title,
            role,
            sequence,
            dumps_json(payload_json),
            status,
            status,
        ),
    )
    row = await cursor.fetchone()
    return str(row["id"])


async def list_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id, run_id, step_key, step_kind, status, title, role, sequence,
          payload_json, started_at, finished_at, created_at, updated_at
        from run_steps
        where tenant_id = %s and run_id = %s
        order by sequence asc, created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_admin_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id as run_id,
          session_id,
          user_id,
          workspace_id,
          status,
          agent_id,
          skill_id,
          created_at,
          queued_at,
          started_at,
          finished_at,
          cancel_requested_at,
          cancel_requested_by,
          error_code,
          error_message
        from runs
        where tenant_id = %s
          and (%s::text is null or user_id = %s)
          and (%s::text is null or status = %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, user_id, user_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def get_admin_run_detail(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    run = await get_run(conn, tenant_id=tenant_id, run_id=run_id)
    if run is None:
        return None
    run_contract_version = _required_schema_version(run, "schema_version", RUN_CONTRACT_VERSION, "invalid_run_contract")
    executor_schema_version = _required_schema_version(
        run,
        "executor_schema_version",
        EXECUTOR_RESULT_SCHEMA_VERSION,
        "invalid_executor_result_schema_version",
    )
    events = await list_run_events(conn, tenant_id=tenant_id, run_id=run_id)
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    artifacts = await list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id)
    skill_snapshots = _attach_skill_usage(
        await list_run_skill_snapshots(conn, tenant_id=tenant_id, run_id=run_id),
        events,
    )
    skill_snapshots = [_sanitize_skill_snapshot(snapshot) for snapshot in skill_snapshots]
    cursor = await conn.execute(
        """
        select id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json, created_at
        from audit_logs
        where tenant_id = %s
          and (
            target_id = %s
            or payload_json->>'run_id' = %s
          )
        order by created_at asc
        """,
        (tenant_id, run_id, run_id),
    )
    audit_rows = list(await cursor.fetchall())
    run_input = sanitize_public_payload(run["input_json"] if isinstance(run.get("input_json"), dict) else {})
    if not isinstance(run_input, dict):
        run_input = {}
    run_result = sanitize_public_payload(run["result_json"] if isinstance(run.get("result_json"), dict) else {})
    if not isinstance(run_result, dict):
        run_result = {}
    return {
        "run": {
            "run_id": run["id"],
            "session_id": run["session_id"],
            "user_id": run["user_id"],
            "workspace_id": run["workspace_id"],
            "status": run["status"],
            "agent_id": run["agent_id"],
            "skill_id": run["skill_id"],
            "created_at": run["created_at"],
            "queued_at": run.get("queued_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "cancel_requested_at": run.get("cancel_requested_at"),
            "cancel_requested_by": run.get("cancel_requested_by"),
            "input": run_input,
            "result": run_result,
            "error_code": sanitize_public_text(run.get("error_code")) or None,
            "error_message": sanitize_public_text(run.get("error_message")),
            "trace_id": run.get("trace_id") or standard_trace_id(str(run["id"])),
            "contract_version": run_contract_version,
            "executor_schema_version": executor_schema_version,
        },
        "events": [
            {
                "event_id": item["id"],
                "schema_version": _required_schema_version(
                    item,
                    "schema_version",
                    EVENT_ENVELOPE_SCHEMA_VERSION,
                    "invalid_event_schema_version",
                ),
                "sequence": int(item.get("sequence") or 0),
                "trace_id": item.get("trace_id") or standard_trace_id(str(run["id"])),
                "type": item["event_type"],
                "stage": item["stage"],
                "message": sanitize_public_text(item.get("message")),
                "severity": item.get("severity") or "info",
                "visible_to_user": bool(item.get("visible_to_user", True)),
                "error_code": sanitize_public_text(item.get("error_code")) or None,
                "latency_ms": item.get("latency_ms"),
                "token_counts": {
                    "input": int(item.get("input_token_count") or 0),
                    "output": int(item.get("output_token_count") or 0),
                    "total": int(item.get("total_token_count") or 0),
                },
                "cost": {"estimated_cost_minor": int(item.get("estimated_cost_minor") or 0)},
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "created_at": item["created_at"],
            }
            for item in events
        ],
        "steps": [
            {
                "step_id": item["id"],
                "run_id": item["run_id"],
                "step_key": item["step_key"],
                "step_kind": item["step_kind"],
                "status": item["status"],
                "title": sanitize_public_text(item.get("title")),
                "role": sanitize_public_text(item.get("role")) if item.get("role") is not None else None,
                "sequence": item["sequence"],
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "started_at": item["started_at"],
                "finished_at": item["finished_at"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            for item in steps
        ],
        "artifacts": [
            {
                "artifact_id": item["id"],
                "trace_id": item.get("trace_id") or standard_trace_id(str(run["id"])),
                "artifact_type": item["artifact_type"],
                "label": sanitize_public_text(item.get("label")) or str(item["artifact_type"]),
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "manifest": artifact_manifest_contract(
                    artifact_type=str(item["artifact_type"]),
                    manifest=item.get("manifest_json") if isinstance(item.get("manifest_json"), dict) else {},
                    schema_version=_required_schema_version(
                        item,
                        "manifest_version",
                        ARTIFACT_MANIFEST_SCHEMA_VERSION,
                        "invalid_artifact_manifest_schema_version",
                    ),
                ),
                "created_at": item["created_at"],
            }
            for item in artifacts
        ],
        "skill_snapshots": skill_snapshots,
        "audit": [
            {
                "audit_id": item["id"],
                "schema_version": _required_schema_version(
                    item,
                    "schema_version",
                    AUDIT_EVENT_SCHEMA_VERSION,
                    "invalid_audit_event_schema_version",
                ),
                "trace_id": item.get("trace_id"),
                "user_id": item["user_id"],
                "action": item["action"],
                "target_type": item["target_type"],
                "target_id": item["target_id"],
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "created_at": item["created_at"],
            }
            for item in audit_rows
        ],
    }


async def request_run_cancel(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update runs
        set
          cancel_requested_at = coalesce(cancel_requested_at, now()),
          cancel_requested_by = coalesce(cancel_requested_by, %s),
          status = case when status = 'queued' then 'cancelled' else status end,
          finished_at = case when status = 'queued' then now() else finished_at end
        where tenant_id = %s
          and id = %s
          and user_id = %s
          and (
            status in ('queued', 'running')
            or (
              status = 'cancelled'
              and exists (
                select 1
                from sandbox_leases
                where sandbox_leases.tenant_id = runs.tenant_id
                  and sandbox_leases.run_id = runs.id
                  and sandbox_leases.status = 'active'
              )
            )
          )
        returning id, status, trace_id
        """,
        (user_id, tenant_id, run_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row["status"] == "cancelled":
        await _cancel_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    active_sandbox_leases = await list_active_sandbox_leases_for_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=row.get("trace_id"),
        event_type="cancel_requested",
        stage="control",
        message="已请求取消",
        payload={"visible_to_user": True, "severity": "warning", "requested_by": user_id},
    )
    if row["status"] == "cancelled":
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=row.get("trace_id"),
            event_type="run_cancelled",
            stage="control",
            message="任务已取消",
            payload={"visible_to_user": True, "severity": "warning"},
        )
    status = "cancelled" if row["status"] == "cancelled" else "cancel_requested"
    result = {"run_id": row["id"], "status": status}
    if active_sandbox_leases:
        result["trace_id"] = row.get("trace_id")
        result["active_sandbox_leases"] = active_sandbox_leases
    return result


async def request_admin_run_cancel(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    admin_user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update runs
        set
          cancel_requested_at = coalesce(cancel_requested_at, now()),
          cancel_requested_by = coalesce(cancel_requested_by, %s),
          status = case when status = 'queued' then 'cancelled' else status end,
          finished_at = case when status = 'queued' then now() else finished_at end
        where tenant_id = %s
          and id = %s
          and (
            status in ('queued', 'running')
            or (
              status = 'cancelled'
              and exists (
                select 1
                from sandbox_leases
                where sandbox_leases.tenant_id = runs.tenant_id
                  and sandbox_leases.run_id = runs.id
                  and sandbox_leases.status = 'active'
              )
            )
          )
        returning id, status, user_id, trace_id
        """,
        (admin_user_id, tenant_id, run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    result_status = "cancelled" if row["status"] == "cancelled" else "cancel_requested"
    if result_status == "cancelled":
        await _cancel_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    active_sandbox_leases = await list_active_sandbox_leases_for_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=row.get("trace_id"),
        event_type="cancel_requested",
        stage="control",
        message="管理员已请求取消",
        payload={
            "visible_to_user": True,
            "severity": "warning",
            "requested_by": admin_user_id,
            "requested_by_role": "admin",
            "target_user_id": row.get("user_id"),
        },
    )
    if row["status"] == "cancelled":
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="run_cancelled",
            stage="control",
            message="任务已取消",
            payload={"visible_to_user": True, "severity": "warning"},
            trace_id=row.get("trace_id"),
        )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=admin_user_id,
        action="admin.run.cancel",
        target_type="run",
        target_id=run_id,
        trace_id=row.get("trace_id"),
        payload_json={
            "run_id": run_id,
            "target_user_id": row.get("user_id"),
            "result_status": result_status,
        },
    )
    result = {"run_id": row["id"], "status": result_status}
    if active_sandbox_leases:
        result["trace_id"] = row.get("trace_id")
        result["active_sandbox_leases"] = active_sandbox_leases
    return result


async def is_cancel_requested(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> bool:
    cursor = await conn.execute(
        "select cancel_requested_at from runs where tenant_id = %s and id = %s",
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("cancel_requested_at"))


async def copy_run_as_new_task(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> dict[str, Any] | None:
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id)
    if source is None:
        return None
    source_input = source["input_json"] if isinstance(source.get("input_json"), dict) else {}
    sanitized_source_input = sanitize_user_control_input(source_input)
    skill = await resolve_agent_skill(
        conn,
        tenant_id=tenant_id,
        agent_id=source["agent_id"],
        skill_id=source["skill_id"],
    )
    executor_type = str(skill["executor_type"])
    release_decision = resolve_rollout_skill_decision(
        skill,
        tenant_id=tenant_id,
        skill_id=str(source["skill_id"]),
        rollout_key=user_id,
    )
    skill_version = release_decision.selected_version
    release_decision_payload = release_decision.to_payload()
    release_policy_version = skill_version if release_decision.policy_active else ""
    file_ids = list(source_input.get("file_ids") or [])
    new_run_id = new_id("run")
    source_execution_input = source_input.get("input") if isinstance(source_input.get("input"), dict) else source_input
    if isinstance(source_execution_input, dict):
        source_execution_input = sanitize_user_control_input(source_execution_input)
    else:
        source_execution_input = {}
    copied_execution_input = {**source_execution_input, "copied_from_run_id": run_id}
    completed_step_outputs = await _completed_step_outputs_for_resume(conn, tenant_id=tenant_id, run_id=run_id)
    if completed_step_outputs:
        copied_execution_input["resume"] = {
            "copied_from_run_id": run_id,
            "completed_step_outputs": completed_step_outputs,
        }
    copied_input_json = (
        {**sanitized_source_input, "input": copied_execution_input, "copied_from_run_id": run_id}
        if isinstance(source_input.get("input"), dict)
        else copied_execution_input
    )
    if isinstance(copied_input_json, dict):
        copied_input_json["executor_type"] = executor_type
        copied_input_json["skill_version"] = skill_version
        copied_input_json["release_decision"] = release_decision_payload
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
          trace_id, schema_version, executor_schema_version,
          status, input_json, queued_at, copied_from_run_id
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb, now(), %s)
        """,
        (
            new_run_id,
            tenant_id,
            source["workspace_id"],
            source["session_id"],
            user_id,
            source["agent_id"],
            source["skill_id"],
            standard_trace_id(new_run_id),
            RUN_CONTRACT_VERSION,
            EXECUTOR_RESULT_SCHEMA_VERSION,
            dumps_json(copied_input_json),
            run_id,
        ),
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=new_run_id,
        event_type="run_created",
        stage="control",
        message="已复制为新任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_message(
        conn,
        tenant_id=tenant_id,
        session_id=source["session_id"],
        run_id=new_run_id,
        role="assistant",
        content="已复制为新任务，将继续执行未完成步骤。",
        metadata_json={
            "type": "copy_run_anchor",
            "copied_from_run_id": run_id,
            "agent_id": source["agent_id"],
            "skill_id": source["skill_id"],
        },
    )
    return {
        "session_id": source["session_id"],
        "run_id": new_run_id,
        "agent_id": source["agent_id"],
        "skill_id": source["skill_id"],
        "workspace_id": source["workspace_id"],
        "file_ids": file_ids,
        "input": copied_execution_input,
        "executor_type": executor_type,
        "skill_version": skill_version,
        "release_policy_version": release_policy_version,
        "release_decision": release_decision_payload,
    }


async def update_run_input_skill_version(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_version: str,
) -> None:
    await conn.execute(
        """
        update runs
        set input_json = jsonb_set(
          case
            when coalesce((input_json->'release_decision'->>'policy_active')::boolean, false) = false
             and input_json ? 'release_decision'
            then jsonb_set(
              jsonb_set(coalesce(input_json, '{}'::jsonb), '{release_decision,selected_version}', %s::jsonb, true),
              '{release_decision,selected_track}', %s::jsonb,
              true
            )
            else coalesce(input_json, '{}'::jsonb)
          end,
          '{skill_version}', %s::jsonb,
          true
        )
        where tenant_id = %s and id = %s
        """,
        (
            json.dumps(skill_version, ensure_ascii=False),
            json.dumps("manifest_pin", ensure_ascii=False),
            json.dumps(skill_version, ensure_ascii=False),
            tenant_id,
            run_id,
        ),
    )


async def _completed_step_outputs_for_resume(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> dict[str, str]:
    cursor = await conn.execute(
        """
        select step_key, payload_json
        from run_steps
        where tenant_id = %s
          and run_id = %s
          and status = 'succeeded'
        order by sequence asc, created_at asc
        """,
        (tenant_id, run_id),
    )
    rows = await cursor.fetchall()
    outputs: dict[str, str] = {}
    for row in rows:
        payload = row.get("payload_json") or {}
        if not isinstance(payload, dict) or payload.get("output") is None:
            continue
        outputs[str(row["step_key"])] = str(payload["output"])
    return outputs


async def create_file(
    conn: AsyncConnection,
    *,
    file_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    original_name: str,
    content_type: str,
    size_bytes: int,
    storage_key: str,
    sha256: str,
) -> None:
    await conn.execute(
        """
        insert into files(id, tenant_id, workspace_id, user_id, session_id, original_name, content_type, size_bytes, storage_key, sha256)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            file_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            original_name,
            content_type,
            size_bytes,
            storage_key,
            sha256,
        ),
    )


async def bind_files_to_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_ids: list[str],
) -> None:
    for file_id in file_ids:
        cursor = await conn.execute(
            """
            select id, tenant_id, workspace_id, user_id, session_id, run_id
            from files
            where id = %s
            """,
            (file_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RepositoryNotFoundError("file_not_found")
        if row["tenant_id"] != tenant_id or row["workspace_id"] != workspace_id:
            raise RepositoryConflictError("file_scope_mismatch")
        if row["user_id"] != user_id:
            raise RepositoryConflictError("file_user_mismatch")
        if row["session_id"] and row["session_id"] != session_id:
            raise RepositoryConflictError("file_session_mismatch")
        if row["run_id"] and row["run_id"] != run_id:
            raise RepositoryConflictError("file_already_bound")
        await conn.execute(
            """
            update files
            set session_id = %s, run_id = %s
            where id = %s
            """,
            (session_id, run_id, file_id),
        )


async def get_file(conn: AsyncConnection, *, tenant_id: str, file_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from files
        where tenant_id = %s and id = %s
        """,
        (tenant_id, file_id),
    )
    return await cursor.fetchone()


async def get_run_file(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    file_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select files.*
        from files
        join runs on runs.id = files.run_id and runs.tenant_id = files.tenant_id
        where files.tenant_id = %s
          and files.id = %s
          and files.run_id = %s
        """,
        (tenant_id, file_id, run_id),
    )
    return await cursor.fetchone()


async def mark_run_running(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update runs
        set status = 'running', started_at = coalesce(started_at, now())
        from sessions
        where runs.tenant_id = %s
          and runs.id = %s
          and runs.status = 'queued'
          and sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        returning runs.id, runs.tenant_id, runs.workspace_id, runs.user_id,
                  runs.session_id, runs.agent_id, runs.skill_id, runs.trace_id,
                  runs.input_json
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def complete_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    result_json: dict[str, Any],
) -> None:
    latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor = _result_observability_values(result_json)
    await conn.execute(
        """
        update runs
        set
          status = 'succeeded',
          result_json = %s::jsonb,
          finished_at = now(),
          error_code = null,
          error_message = null,
          latency_ms = %s,
          input_token_count = %s,
          output_token_count = %s,
          total_token_count = %s,
          estimated_cost_minor = %s
        where tenant_id = %s and id = %s
        """,
        (
            dumps_json(result_json),
            latency_ms,
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_cost_minor,
            tenant_id,
            run_id,
        ),
    )


async def fail_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    result_json: dict[str, Any] | None = None,
) -> None:
    latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor = _result_observability_values(result_json)
    await conn.execute(
        """
        update runs
        set
          status = 'failed',
          result_json = %s::jsonb,
          finished_at = now(),
          error_code = %s,
          error_message = %s,
          latency_ms = %s,
          input_token_count = %s,
          output_token_count = %s,
          total_token_count = %s,
          estimated_cost_minor = %s
        where tenant_id = %s and id = %s
        """,
        (
            dumps_json(result_json or {}),
            error_code,
            error_message,
            latency_ms,
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_cost_minor,
            tenant_id,
            run_id,
        ),
    )
    await _fail_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)


async def cancel_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    result_json: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        update runs
        set status = 'cancelled', result_json = %s::jsonb, finished_at = now(), error_code = null, error_message = null
        where tenant_id = %s and id = %s
        """,
        (dumps_json(result_json or {}), tenant_id, run_id),
    )
    await _cancel_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)


async def _cancel_open_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    await conn.execute(
        """
        update run_steps
        set status = 'cancelled',
            finished_at = coalesce(finished_at, now()),
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status in ('pending', 'running')
        """,
        (tenant_id, run_id),
    )


async def _fail_open_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    await conn.execute(
        """
        update run_steps
        set status = case when status = 'running' then 'failed' else 'cancelled' end,
            finished_at = coalesce(finished_at, now()),
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status in ('pending', 'running')
        """,
        (tenant_id, run_id),
    )


async def create_artifact(
    conn: AsyncConnection,
    *,
    artifact_id: str,
    tenant_id: str,
    run_id: str,
    trace_id: str | None = None,
    artifact_type: str,
    label: str,
    content_type: str,
    storage_key: str,
    size_bytes: int,
    manifest_json: dict[str, Any],
) -> None:
    resolved_trace_id = trace_id or standard_trace_id(run_id)
    await conn.execute(
        """
        insert into artifacts(
          id, tenant_id, run_id, trace_id, artifact_type, label, content_type, storage_key,
          size_bytes, manifest_version, manifest_json
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            artifact_id,
            tenant_id,
            run_id,
            resolved_trace_id,
            artifact_type,
            label,
            content_type,
            storage_key,
            size_bytes,
            ARTIFACT_MANIFEST_SCHEMA_VERSION,
            dumps_json(manifest_json),
        ),
    )


async def get_artifact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select artifacts.*
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        where artifacts.tenant_id = %s and artifacts.id = %s
        """,
        (tenant_id, artifact_id),
    )
    return await cursor.fetchone()


async def get_authorized_artifact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select artifacts.*
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        where artifacts.tenant_id = %s
          and artifacts.id = %s
          and runs.user_id = %s
        """,
        (tenant_id, artifact_id, user_id),
    )
    return await cursor.fetchone()


async def get_admin_artifact(conn: AsyncConnection, *, tenant_id: str, artifact_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select
          artifacts.*,
          runs.id as run_id,
          runs.user_id as target_user_id
        from artifacts
        join runs on runs.id = artifacts.run_id and runs.tenant_id = artifacts.tenant_id
        where artifacts.tenant_id = %s
          and artifacts.id = %s
        """,
        (tenant_id, artifact_id),
    )
    return await cursor.fetchone()


async def append_audit_log(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    trace_id: str | None = None,
    payload_json: dict[str, Any] | None = None,
) -> str:
    audit_id = new_id("aud")
    await conn.execute(
        """
        insert into audit_logs(id, tenant_id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            audit_id,
            tenant_id,
            user_id,
            action,
            target_type,
            target_id,
            trace_id,
            AUDIT_EVENT_SCHEMA_VERSION,
            dumps_json(payload_json or {}),
        ),
    )
    return audit_id


async def list_authorized_sessions(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select id, workspace_id, agent_id, title, created_at, updated_at
        from sessions
        where tenant_id = %s and user_id = %s and status = 'active'
        order by updated_at desc, created_at desc
        limit 100
        """,
        (tenant_id, user_id),
    )
    return list(await cursor.fetchall())


async def get_authorized_lambchat_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, workspace_id, agent_id, title, status, created_at, updated_at
        from sessions
        where tenant_id = %s
          and id = %s
          and user_id = %s
        """,
        (tenant_id, session_id, user_id),
    )
    return await cursor.fetchone()


async def list_authorized_session_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select id, trace_id, schema_version, agent_id, skill_id, status, error_code, error_message,
               created_at, queued_at, started_at, finished_at, result_json
        from runs
        where tenant_id = %s
          and user_id = %s
          and session_id = %s
        order by created_at desc
        limit %s
        """,
        (tenant_id, user_id, session_id, limit),
    )
    return list(await cursor.fetchall())


async def append_message(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    run_id: str | None,
    role: str,
    content: str,
    metadata_json: dict[str, Any] | None = None,
) -> str:
    message_id = new_id("msg")
    await conn.execute(
        """
        insert into messages(id, tenant_id, session_id, run_id, role, content, metadata_json)
        values (%s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (message_id, tenant_id, session_id, run_id, role, content, dumps_json(metadata_json or {})),
    )
    await conn.execute("update sessions set updated_at = now() where tenant_id = %s and id = %s", (tenant_id, session_id))
    return message_id


async def list_authorized_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select messages.id, messages.session_id, messages.run_id, messages.role, messages.content,
               messages.metadata_json, messages.created_at
        from messages
        join sessions on sessions.id = messages.session_id and sessions.tenant_id = messages.tenant_id
        where messages.tenant_id = %s
          and messages.session_id = %s
          and sessions.user_id = %s
        order by messages.created_at asc
        """,
        (tenant_id, session_id, user_id),
    )
    return list(await cursor.fetchall())
