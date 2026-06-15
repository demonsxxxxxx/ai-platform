import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg import AsyncConnection

from app.control_plane_contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    AUDIT_EVENT_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EXECUTOR_RESULT_SCHEMA_VERSION,
    HASH_LIKE_VALUE_PATTERN,
    RUN_CONTRACT_VERSION,
    artifact_lineage_contract,
    artifact_manifest_contract,
    sanitize_public_payload,
    sanitize_public_text,
    standard_error_code,
    standard_trace_id,
)
from app.error_taxonomy import summarize_error_categories
from app.memory_redaction import normalize_memory_redaction_mode, redact_memory_metadata, redact_memory_text
from app.projection_redaction import sanitize_user_control_input
from app.skills.dependencies import is_workbench_skill_public
from app.skills.release_policy import resolve_rollout_skill_decision
from app.tool_policy import max_risk


DEFAULT_RUN_EXECUTOR_TYPES = {"claude-agent-worker", "ragflow"}
ACTIVE_RUN_STATUSES = {"queued", "running"}
TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
RETRYABLE_RUN_STATUSES = {"failed", "dead-letter", "dead_letter", "dead-lettered"}
MEMORY_RETENTION_CLEANUP_CURSOR_KEY = "memory_retention_cleanup"


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


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _effective_status(registry_status: str, policy_status: str) -> str:
    return "active" if registry_status == "active" and policy_status == "active" else "disabled"


def _tool_policy_projection(row: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    has_tenant_policy = row.get("policy_status") is not None
    policy_source = "tenant" if has_tenant_policy else "registry"
    registry_status = str(row.get("registry_status") or row.get("status") or "disabled")
    policy_status = str(row.get("policy_status") or "disabled")
    registry_write_capable = _coerce_bool(row.get("registry_write_capable"), _coerce_bool(row.get("write_capable")))
    policy_write_capable = _coerce_bool(row.get("policy_write_capable"), False)
    registry_risk_level = str(row.get("registry_risk_level") or row.get("risk_level") or "low")
    policy_risk_level = str(row.get("policy_risk_level") or "low")
    registry_visible_to_user = _coerce_bool(row.get("registry_visible_to_user"), _coerce_bool(row.get("visible_to_user"), True))
    policy_visible_to_user = _coerce_bool(row.get("policy_visible_to_user"), False)
    effective_visible_to_user = registry_visible_to_user and policy_visible_to_user
    effective_policy_status = _effective_status(registry_status, policy_status)
    if not effective_visible_to_user:
        effective_policy_status = "disabled"
    return {
        "tenant_id": tenant_id,
        "tool_id": str(row.get("tool_id") or row.get("id") or ""),
        "id": str(row.get("tool_id") or row.get("id") or ""),
        "server_id": str(row.get("server_id") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "registry_status": registry_status,
        "policy_status": policy_status,
        "effective_status": effective_policy_status,
        "status": effective_policy_status,
        "registry_write_capable": registry_write_capable,
        "policy_write_capable": policy_write_capable,
        "write_capable": registry_write_capable or policy_write_capable,
        "registry_risk_level": registry_risk_level,
        "policy_risk_level": policy_risk_level,
        "risk_level": max_risk(registry_risk_level, policy_risk_level),
        "registry_visible_to_user": registry_visible_to_user,
        "policy_visible_to_user": policy_visible_to_user,
        "visible_to_user": effective_visible_to_user,
        "source": policy_source,
        "reason": str(row.get("reason") or ""),
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
    }


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
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user,
          mcp_tools.server_id as server_id,
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
        left join tool_policies
          on tool_policies.tenant_id = agents.tenant_id
         and tool_policies.tool_id = mcp_tools.id
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
    if row["executor_type"] == "ragflow":
        tool_policy = _tool_policy_projection({**dict(row), "tool_id": row["skill_id"]}, tenant_id=tenant_id)
        if tool_policy["effective_status"] != "active" or not tool_policy["visible_to_user"]:
            raise RepositoryConflictError("mcp_tool_disabled")
    if row["default_skill_id"] != skill_id:
        raise RepositoryConflictError("agent_skill_mismatch")
    return row


async def ensure_mcp_tool_active(conn: AsyncConnection, *, tenant_id: str, tool_id: str) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select
          mcp_tools.id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        left join tool_policies
          on tool_policies.tenant_id = %s
         and tool_policies.tool_id = mcp_tools.id
        where mcp_tools.id = %s
        """,
        (tenant_id, tool_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_tool_not_found")
    policy = _tool_policy_projection(dict(row), tenant_id=tenant_id)
    if policy["effective_status"] != "active" or not policy["visible_to_user"]:
        raise RepositoryConflictError("mcp_tool_disabled")
    return policy


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
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        left join tool_policies
          on tool_policies.tenant_id = %s
         and tool_policies.tool_id = mcp_tools.id
        where mcp_tools.visible_to_user = true
          and tool_policies.visible_to_user = true
          and (%s or (mcp_tools.status = 'active' and tool_policies.status = 'active'))
        order by case mcp_tools.id
          when 'ragflow-knowledge-search' then 1
          else 99
        end, mcp_tools.id asc
        """,
        (tenant_id, include_disabled),
    )
    return [
        {
            **policy,
            "allowed_for_user": bool(policy["visible_to_user"]),
        }
        for policy in (_tool_policy_projection(dict(row), tenant_id=tenant_id) for row in await cursor.fetchall())
    ]


async def list_admin_tool_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return tenant-scoped admin tool policy projections without private connection fields."""
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user,
          tool_policies.reason,
          tool_policies.updated_by,
          tool_policies.updated_at
        from mcp_tools
        left join tool_policies
          on tool_policies.tenant_id = %s
         and tool_policies.tool_id = mcp_tools.id
        where (
          %s
          or (
            mcp_tools.status = 'active'
            and tool_policies.status = 'active'
            and coalesce(mcp_tools.visible_to_user, false) = true
            and tool_policies.visible_to_user = true
          )
        )
        order by case mcp_tools.id
          when 'ragflow-knowledge-search' then 1
          else 99
        end, mcp_tools.id asc
        limit %s
        """,
        (tenant_id, include_disabled, limit),
    )
    return [_tool_policy_projection(dict(row), tenant_id=tenant_id) for row in await cursor.fetchall()]


async def list_admin_tool_policy_history(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return bounded tenant-scoped audit history for admin tool policy updates."""
    bounded_limit = max(min(int(100 if limit is None else limit), 500), 1)
    clauses = [
        "tenant_id = %s",
        "target_type = %s",
        "action = %s",
    ]
    params: list[Any] = [tenant_id, "tool_policy", "admin.tool_policy.updated"]
    if tool_id:
        clauses.append("target_id = %s")
        params.append(tool_id)
    params.append(bounded_limit)
    cursor = await conn.execute(
        f"""
        select id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json, created_at
        from audit_logs
        where {" and ".join(clauses)}
        order by created_at desc, id desc
        limit %s
        """,
        tuple(params),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def upsert_admin_tool_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
    status: str,
    risk_level: str,
    write_capable: bool,
    visible_to_user: bool,
    reason: str,
    updated_by: str,
) -> dict[str, Any]:
    """Upsert a tenant-scoped tool policy and return its effective runtime projection."""
    cursor = await conn.execute(
        """
        with upserted as (
          insert into tool_policies(
            tenant_id, tool_id, status, write_capable, risk_level,
            visible_to_user, reason, updated_by, updated_at
          )
          select %s, mcp_tools.id, %s, %s, %s, %s, %s, %s, now()
          from mcp_tools
          where mcp_tools.id = %s
          on conflict (tenant_id, tool_id) do update
          set status = excluded.status,
              write_capable = excluded.write_capable,
              risk_level = excluded.risk_level,
              visible_to_user = excluded.visible_to_user,
              reason = excluded.reason,
              updated_by = excluded.updated_by,
              updated_at = now()
          returning *
        )
        select
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          upserted.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          upserted.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          upserted.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          upserted.visible_to_user as policy_visible_to_user,
          upserted.reason,
          upserted.updated_by,
          upserted.updated_at
        from upserted
        join mcp_tools on mcp_tools.id = upserted.tool_id
        """,
        (
            tenant_id,
            status,
            write_capable,
            risk_level,
            visible_to_user,
            reason,
            updated_by,
            tool_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_tool_not_found")
    return _tool_policy_projection(dict(row), tenant_id=tenant_id)


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
             and (
               coalesce(mcp_tools.status, 'disabled') <> 'active'
               or coalesce(tool_policies.status, 'disabled') <> 'active'
               or coalesce(mcp_tools.visible_to_user, false) = false
               or coalesce(tool_policies.visible_to_user, false) = false
             )
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
          case
            when skills.id <> 'ragflow-knowledge-search' then null
            when mcp_tools.risk_level = 'high' or tool_policies.risk_level = 'high' then 'high'
            when mcp_tools.risk_level = 'medium' or tool_policies.risk_level = 'medium' then 'medium'
            else coalesce(mcp_tools.risk_level, 'low')
          end as risk_level,
          0 as recent_failures
        from agents
        join skills on skills.id = agents.default_skill_id
        left join tenant_workbench_skills
          on tenant_workbench_skills.tenant_id = agents.tenant_id
         and tenant_workbench_skills.skill_id = skills.id
        left join mcp_tools
          on mcp_tools.id = skills.id
        left join tool_policies
          on tool_policies.tenant_id = agents.tenant_id
         and tool_policies.tool_id = mcp_tools.id
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


async def enforce_user_active_run_admission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> int:
    limit = int(limit)
    if limit <= 0:
        return 0
    lock_scope = dumps_json({"tenant_id": tenant_id, "user_id": user_id})
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )
    active_count = await count_active_runs_for_user(conn, tenant_id=tenant_id, user_id=user_id)
    if active_count >= limit:
        raise RepositoryConflictError("user_active_run_limit_exceeded")
    return active_count


async def get_active_retry_for_source_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, status
        from runs
        where tenant_id = %s
          and user_id = %s
          and copied_from_run_id = %s
          and status in ('queued', 'running')
        order by created_at desc
        limit 1
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


async def get_active_resume_for_source_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Return an active same-owner child run that would duplicate a resume request."""
    cursor = await conn.execute(
        """
        select id, status
        from runs
        where tenant_id = %s
          and user_id = %s
          and copied_from_run_id = %s
          and status in ('queued', 'running')
        order by created_at desc
        limit 1
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


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


async def get_run(conn: AsyncConnection, *, tenant_id: str, run_id: str, for_update: bool = False) -> dict[str, Any] | None:
    lock_clause = "for update" if for_update else ""
    cursor = await conn.execute(
        f"select * from runs where tenant_id = %s and id = %s {lock_clause}",
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
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = "for update" if for_update else ""
    cursor = await conn.execute(
        f"""
        select *
        from runs
        where tenant_id = %s
          and id = %s
          and user_id = %s
        {lock_clause}
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
        "redaction_mode": "standard",
        "source": "default",
        "reason": "",
        "updated_by": "",
        "updated_at": None,
    }


def _stored_memory_redaction_mode(value: object) -> str:
    if value is None or str(value).strip() == "":
        return "strict"
    try:
        return normalize_memory_redaction_mode(value)
    except ValueError:
        return "strict"


def _validated_memory_redaction_mode(value: object) -> str:
    try:
        return normalize_memory_redaction_mode(value)
    except ValueError as exc:
        raise RepositoryConflictError(str(exc)) from exc


def _memory_policy_from_row(row: dict[str, Any], *, source: str = "stored") -> dict[str, Any]:
    return {
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": row.get("agent_id"),
        "memory_enabled": bool(row.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": int(row.get("retention_days") or 90),
        "redaction_mode": _stored_memory_redaction_mode(row.get("redaction_mode")),
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
               redaction_mode, reason, updated_by, updated_at
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
    redaction_mode: str,
    reason: str,
    updated_by: str,
) -> dict[str, Any]:
    if long_term_memory_enabled:
        raise RepositoryConflictError("long_term_memory_not_available")
    redaction_mode = _validated_memory_redaction_mode(redaction_mode)
    policy_id = memory_policy_id(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    cursor = await conn.execute(
        """
        insert into memory_policies(
          id, tenant_id, workspace_id, user_id, agent_id,
          memory_enabled, long_term_memory_enabled, retention_days, redaction_mode,
          reason, updated_by
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (id) do update
        set memory_enabled = excluded.memory_enabled,
            long_term_memory_enabled = excluded.long_term_memory_enabled,
            retention_days = excluded.retention_days,
            redaction_mode = excluded.redaction_mode,
            reason = excluded.reason,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, workspace_id, user_id, agent_id,
                  memory_enabled, long_term_memory_enabled, retention_days, redaction_mode,
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
            redaction_mode,
            reason,
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    return _memory_policy_from_row(dict(row))


async def list_admin_memory_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return stored memory policies for an admin same-tenant operational view."""
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id,
               memory_enabled, long_term_memory_enabled, retention_days,
               redaction_mode, reason, updated_by, updated_at
        from memory_policies
        where tenant_id = %s
          and workspace_id = %s
          and (%s::text is null or user_id = %s)
          and (%s::text is null or agent_id = %s)
        order by updated_at desc, created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, user_id, agent_id, agent_id, limit),
    )
    return [_memory_policy_from_row(dict(row)) for row in await cursor.fetchall()]


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
    redaction_mode: str = "standard",
) -> dict[str, Any]:
    if not session_id:
        raise RepositoryConflictError("memory_session_id_required")
    if not agent_id:
        raise RepositoryConflictError("memory_agent_id_required")
    retention_days = int(retention_days)
    if retention_days <= 0:
        raise RepositoryConflictError("memory_retention_days_invalid")
    redaction_mode = _validated_memory_redaction_mode(redaction_mode)
    record_id = new_id("mem")
    redacted_content = redact_memory_text(content, mode=redaction_mode)
    redacted_metadata = redact_memory_metadata(metadata_json, mode=redaction_mode)
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
    if not session_id:
        raise RepositoryConflictError("memory_session_id_required")
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


async def cleanup_expired_memory_records_across_scopes(
    conn: AsyncConnection,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Soft-delete expired memory records using a bounded rotating scope cursor."""
    limit = int(limit)
    if limit <= 0:
        raise RepositoryConflictError("memory_cleanup_limit_invalid")
    cursor = await conn.execute(
        """
        select tenant_id, workspace_id
        from worker_maintenance_cursors
        where cursor_key = %s
        for update
        """,
        (MEMORY_RETENTION_CLEANUP_CURSOR_KEY,),
    )
    cursor_row = await cursor.fetchone()
    last_tenant_id = str(cursor_row["tenant_id"]) if cursor_row and cursor_row.get("tenant_id") else None
    last_workspace_id = str(cursor_row["workspace_id"]) if cursor_row and cursor_row.get("workspace_id") else None

    scope_rows: list[dict[str, Any]] = []
    if last_tenant_id is not None and last_workspace_id is not None:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                after_tenant_id=last_tenant_id,
                after_workspace_id=last_workspace_id,
                limit=limit,
            )
        )
    else:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                after_tenant_id=None,
                after_workspace_id=None,
                limit=limit,
            )
        )
    if last_tenant_id is not None and last_workspace_id is not None and len(scope_rows) < limit:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                before_or_at_tenant_id=last_tenant_id,
                before_or_at_workspace_id=last_workspace_id,
                limit=limit - len(scope_rows),
            )
        )
    if not scope_rows:
        return []

    last_scope = scope_rows[-1]
    await conn.execute(
        """
        insert into worker_maintenance_cursors(cursor_key, tenant_id, workspace_id)
        values (%s, %s, %s)
        on conflict (cursor_key) do update
        set tenant_id = excluded.tenant_id,
            workspace_id = excluded.workspace_id,
            updated_at = now()
        """,
        (MEMORY_RETENTION_CLEANUP_CURSOR_KEY, str(last_scope["tenant_id"]), str(last_scope["workspace_id"])),
    )
    per_scope_limit = max(1, (limit + len(scope_rows) - 1) // len(scope_rows))
    tenant_ids = [str(row["tenant_id"]) for row in scope_rows]
    workspace_ids = [str(row["workspace_id"]) for row in scope_rows]
    cursor = await conn.execute(
        """
        with candidate_scopes as (
          select *
          from unnest(%s::text[], %s::text[]) as scope(tenant_id, workspace_id)
        ),
        candidate_rows as (
          select selected.id,
                 selected.expires_at,
                 selected.created_at,
                 selected.tenant_id,
                 selected.workspace_id,
                 selected.scope_rank
          from candidate_scopes scope
          cross join lateral (
            select locked_rows.id,
                   locked_rows.expires_at,
                   locked_rows.created_at,
                   locked_rows.tenant_id,
                   locked_rows.workspace_id,
                   row_number() over (
                     order by locked_rows.expires_at asc, locked_rows.created_at asc, locked_rows.id asc
                   ) as scope_rank
            from (
              select id, expires_at, created_at, tenant_id, workspace_id
              from memory_records
              where memory_records.tenant_id = scope.tenant_id
                and memory_records.workspace_id = scope.workspace_id
                and status = 'active'
                and deleted_at is null
                and expires_at is not null
                and expires_at <= now()
              order by expires_at asc, created_at asc, id asc
              limit %s
              for update skip locked
            ) locked_rows
          ) selected
          order by case when selected.scope_rank = 1 then 0 else 1 end,
                   selected.expires_at asc,
                   selected.created_at asc,
                   selected.tenant_id asc,
                   selected.workspace_id asc,
                   selected.id asc
          limit %s
        )
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where id in (select id from candidate_rows)
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_ids, workspace_ids, per_scope_limit, limit),
    )
    return list(await cursor.fetchall())


async def _list_expired_memory_cleanup_scopes(
    conn: AsyncConnection,
    *,
    after_tenant_id: str | None = None,
    after_workspace_id: str | None = None,
    before_or_at_tenant_id: str | None = None,
    before_or_at_workspace_id: str | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select tenant_id, workspace_id
        from memory_records
        where status = 'active'
          and deleted_at is null
          and expires_at is not null
          and expires_at <= now()
          and (
            %s::text is null
            or (tenant_id, workspace_id) > (%s, %s)
          )
          and (
            %s::text is null
            or (tenant_id, workspace_id) <= (%s, %s)
          )
        group by tenant_id, workspace_id
        order by tenant_id asc, workspace_id asc
        limit %s
        """,
        (
            after_tenant_id,
            after_tenant_id,
            after_workspace_id,
            before_or_at_tenant_id,
            before_or_at_tenant_id,
            before_or_at_workspace_id,
            int(limit),
        ),
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
    expires_in_seconds: int = 900,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update run_tool_permission_requests
        set status = 'decided',
            decision = %s,
            reason = %s,
            decision_payload_json = %s::jsonb,
            expires_at = now() + (%s * interval '1 second'),
            decided_at = now(),
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'pending'
        returning *
        """,
        (
            decision,
            reason,
            dumps_json(decision_payload_json),
            int(expires_in_seconds),
            tenant_id,
            user_id,
            run_id,
            request_id,
        ),
    )
    return await cursor.fetchone()


async def get_exact_tool_permission_decision(
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
    """Fetch a decided permission row only for the exact call or stable request fingerprint."""
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
    """Compatibility wrapper for callers that still use the legacy function name."""
    return await get_exact_tool_permission_decision(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        tool_id=tool_id,
        action=action,
        tool_call_id=tool_call_id,
        request_payload_json=request_payload_json,
    )


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


async def list_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return same-run sandbox lease rows for admin runtime provenance."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
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


def _sandbox_lease_release_message(reason: str) -> str:
    if reason == "expired":
        return "已释放过期 Sandbox 租约"
    if reason in {"cancel_requested", "admin_cancel_requested"}:
        return "已因取消释放 Sandbox 租约"
    return "已释放 Sandbox 租约"


async def release_stopped_sandbox_leases(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    reason: str,
    lease_ids: list[str],
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Release DB leases only after their runtime stop operation has succeeded."""
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
          and id = any(%s)
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, lease_ids),
    )
    released_leases = list(await cursor.fetchall())
    for lease in released_leases:
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=str(lease["run_id"]),
            trace_id=lease.get("trace_id") or trace_id,
            event_type="sandbox_lease_released",
            stage="sandbox",
            message=_sandbox_lease_release_message(reason),
            payload={
                "visible_to_user": True,
                "lease_id": lease.get("id"),
                "reason": reason,
            },
        )
    return released_leases


async def list_expired_active_sandbox_leases(
    conn: AsyncConnection,
    *,
    tenant_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return expired active lease rows so callers can stop runtime providers first."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where (%s::text is null or tenant_id = %s)
          and status = 'active'
          and expires_at is not null
          and expires_at <= now()
        order by expires_at asc, created_at asc
        limit %s
        """,
        (tenant_id, tenant_id, limit),
    )
    return list(await cursor.fetchall())


async def cleanup_expired_sandbox_leases(
    conn: AsyncConnection,
    *,
    tenant_id: str | None = None,
    reason: str = "expired",
) -> list[dict[str, Any]]:
    """Release expired DB-only leases; runtime providers must be stopped first."""
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
          and provider not in ('fake', 'docker')
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


async def list_multi_agent_dispatch_candidate_run_ids(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int = 10,
) -> list[str]:
    """Return bounded running top-level multi-agent parent run ids for worker dispatch."""
    bounded_limit = max(min(int(limit or 10), 500), 1)
    cursor = await conn.execute(
        """
        select id
        from runs
        where tenant_id = %s
          and status = 'running'
          and copied_from_run_id is null
          and (
            input_json#>>'{input,execution_mode}' = 'multi_agent'
            or input_json->>'execution_mode' = 'multi_agent'
          )
          and input_json#>>'{multi_agent_dispatch,orchestration_state}' = 'awaiting_dispatch'
        order by queued_at asc nulls last, created_at asc, id asc
        limit %s
        """,
        (tenant_id, bounded_limit),
    )
    return [str(row["id"]) for row in await cursor.fetchall()]


async def mark_multi_agent_dispatch_parent_awaiting_dispatch(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    worker_id: str | None = None,
) -> bool:
    """Mark a running top-level multi-agent parent as parked for worker dispatch."""
    marker = {
        "orchestration_state": "awaiting_dispatch",
        "source": "worker",
        "worker_id": sanitize_public_text(worker_id) or "worker",
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    cursor = await conn.execute(
        """
        update runs
        set input_json = jsonb_set(
              case when jsonb_typeof(input_json) = 'object' then input_json else '{}'::jsonb end,
              '{multi_agent_dispatch}',
              %s::jsonb,
              true
            )
        where tenant_id = %s
          and id = %s
          and status = 'running'
          and copied_from_run_id is null
          and (
            input_json#>>'{input,execution_mode}' = 'multi_agent'
            or input_json->>'execution_mode' = 'multi_agent'
          )
        returning id
        """,
        (dumps_json(marker), tenant_id, run_id),
    )
    return await cursor.fetchone() is not None


async def claim_multi_agent_dispatch_step(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    claimed_by: str,
    trace_id: str,
    step_key: str,
    step_kind: str,
    title: str,
    role: str | None,
    sequence: int,
    depends_on: list[str],
    lease_ttl_seconds: int = 900,
) -> dict[str, Any]:
    """Record an admin/runtime claim for a ready multi-agent step."""
    dispatch_id = new_id("dispatch")
    claimed_at = datetime.now(timezone.utc)
    lease_expires_at = claimed_at + timedelta(seconds=max(int(lease_ttl_seconds or 0), 1))
    step_id = new_id("step")
    payload_json = {
        "depends_on": depends_on,
        "dispatch_state": "claimed",
        "dispatch_kind": "subagent",
        "dispatch_id": dispatch_id,
        "dispatch_claimed_by": claimed_by,
        "dispatch_claimed_at": claimed_at.isoformat(),
        "dispatch_lease_expires_at": lease_expires_at.isoformat(),
    }
    claim_cursor = await conn.execute(
        """
        insert into run_steps(
          id, tenant_id, run_id, step_key, step_kind, status, title, role, sequence,
          payload_json, started_at, finished_at
        )
        values (
          %s, %s, %s, %s, %s, 'running', %s, %s, %s,
          %s::jsonb, now(), null
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
          finished_at = null,
          updated_at = now()
        where run_steps.status = 'pending'
          and coalesce(run_steps.payload_json->>'dispatch_state', '') not in ('claimed', 'handed_off')
        returning id
        """,
        (
            step_id,
            tenant_id,
            run_id,
            step_key,
            step_kind,
            title,
            role,
            sequence,
            dumps_json(payload_json),
        ),
    )
    claimed = await claim_cursor.fetchone()
    if claimed is None:
        raise RepositoryConflictError("dispatch_step_not_pending")
    step_id = str(claimed["id"])
    await conn.execute(
        """
        update run_steps
        set
          payload_json = payload_json - 'dispatch_expired_at',
          updated_at = now()
        where tenant_id = %s
          and id = %s
          and payload_json ? 'dispatch_expired_at'
        """,
        (tenant_id, step_id),
    )
    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        event_type="agent_step_started",
        stage="agent",
        message="Multi-agent step dispatch claimed",
        visible_to_user=False,
        payload={
            "visible_to_user": False,
            "step_key": step_key,
            "step_index": sequence,
            "dispatch_state": "claimed",
            "dispatch_id": dispatch_id,
        },
    )
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=claimed_by,
        action="run.multi_agent.dispatch.claim",
        target_type="run_step",
        target_id=step_id,
        trace_id=trace_id,
        payload_json={
            "run_id": run_id,
            "step_key": step_key,
            "dispatch_id": dispatch_id,
            "result_status": "claimed",
        },
    )
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    step = next((item for item in steps if str(item.get("step_key")) == step_key), None)
    if step is None:
        raise RepositoryConflictError("dispatch_step_not_persisted")
    return {"dispatch_id": dispatch_id, "event_id": event_id, "audit_id": audit_id, "step": step}


async def cleanup_expired_multi_agent_dispatch_claims(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    cleaned_by: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    bounded_limit = max(min(int(limit or 100), 500), 1)
    cleaned: list[dict[str, Any]] = []
    visited_ids: list[str] = []
    visited_id_set: set[str] = set()
    cleanup_at = datetime.now(timezone.utc)
    expired_at = cleanup_at.isoformat()

    while len(cleaned) < bounded_limit:
        excluded_ids = list(visited_ids)
        cursor = await conn.execute(
            """
            select
              rs.id,
              rs.tenant_id,
              rs.run_id,
              r.trace_id,
              rs.step_key,
              rs.payload_json
            from run_steps rs
            join runs r on r.tenant_id = rs.tenant_id and r.id = rs.run_id
            where rs.tenant_id = %s
              and rs.status = 'running'
              and rs.payload_json->>'dispatch_state' = 'claimed'
              and rs.payload_json->>'dispatch_lease_expires_at' is not null
              and (cardinality(%s::text[]) = 0 or rs.id <> all(%s::text[]))
            order by rs.updated_at asc, rs.created_at asc
            limit %s
            for update of rs skip locked
            """,
            (tenant_id, excluded_ids, excluded_ids, bounded_limit),
        )
        rows = list(await cursor.fetchall())
        if not rows:
            break

        new_row_count = 0
        for row in rows:
            row_id = str(row["id"])
            if row_id in visited_id_set:
                continue
            visited_id_set.add(row_id)
            visited_ids.append(row_id)
            new_row_count += 1

            payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
            lease_expires_at = _parse_iso_datetime(payload.get("dispatch_lease_expires_at"))
            if lease_expires_at is None or lease_expires_at > cleanup_at:
                continue
            dispatch_id = str(payload.get("dispatch_id") or "")
            await conn.execute(
                """
                update run_steps
                set
                  status = 'pending',
                  payload_json = payload_json || %s::jsonb,
                  started_at = null,
                  finished_at = null,
                  updated_at = now()
                where tenant_id = %s
                  and id = %s
                  and status = 'running'
                  and payload_json->>'dispatch_state' = 'claimed'
                """,
                (
                    dumps_json(
                        {
                            "dispatch_state": "expired",
                            "dispatch_expired_at": expired_at,
                        }
                    ),
                    tenant_id,
                    row_id,
                ),
            )
            await append_audit_log(
                conn,
                tenant_id=tenant_id,
                user_id=cleaned_by,
                action="run.multi_agent.dispatch.expire",
                target_type="run_step",
                target_id=row_id,
                trace_id=row.get("trace_id"),
                payload_json={
                    "run_id": str(row["run_id"]),
                    "step_key": str(row["step_key"]),
                    "dispatch_id": dispatch_id,
                    "result_status": "expired",
                },
            )
            cleaned.append(
                {
                    "step_id": row_id,
                    "run_id": str(row["run_id"]),
                    "step_key": str(row["step_key"]),
                    "dispatch_id": dispatch_id,
                    "status": "pending",
                }
            )
            if len(cleaned) >= bounded_limit:
                break

        if new_row_count == 0 or len(rows) < bounded_limit:
            break

    return cleaned


def _run_execution_input_from_row(run: dict[str, Any]) -> dict[str, Any]:
    source_input = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    execution_input = source_input.get("input") if isinstance(source_input.get("input"), dict) else source_input
    return execution_input if isinstance(execution_input, dict) else {}


def _clean_child_execution_input(source_execution_input: dict[str, Any]) -> dict[str, Any]:
    cleaned = sanitize_user_control_input(source_execution_input)
    cleaned.pop("resume", None)
    cleaned.pop("multi_agent_dispatch", None)
    return cleaned


def _completed_dependency_resume_payload(
    steps: list[dict[str, Any]],
    *,
    parent_run_id: str,
    depends_on: list[str],
) -> dict[str, Any]:
    rows_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    completed_outputs: dict[str, str] = {}
    completed_checkpoints: dict[str, dict[str, str]] = {}
    for dependency in depends_on:
        row = rows_by_key.get(dependency)
        if row is None or str(row.get("status") or "") != "succeeded":
            raise RepositoryConflictError("dispatch_dependency_not_succeeded")
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        if payload.get("output") is None:
            raise RepositoryConflictError("dispatch_dependency_output_missing")
        completed_outputs[dependency] = str(payload["output"])
        lineage = artifact_lineage_contract(
            {
                "checkpoint_id": payload.get("checkpoint_id"),
                "source_step_id": payload.get("source_step_id") or row.get("id"),
            },
            source_run_id=payload.get("copied_from_run_id") or parent_run_id,
        )
        checkpoint_id = lineage.get("checkpoint_id")
        source_step_id = lineage.get("source_step_id")
        source_run_id = lineage.get("source_run_id")
        if checkpoint_id and source_step_id and source_run_id:
            completed_checkpoints[dependency] = {
                "checkpoint_id": str(checkpoint_id),
                "source_step_id": str(source_step_id),
                "copied_from_run_id": str(source_run_id),
            }
    if not completed_outputs:
        return {}
    resume: dict[str, Any] = {
        "copied_from_run_id": parent_run_id,
        "completed_step_outputs": completed_outputs,
    }
    if completed_checkpoints:
        resume["completed_step_checkpoints"] = completed_checkpoints
    return resume


async def release_multi_agent_dispatch_claim(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    parent_step_id: str,
    dispatch_id: str,
    reason: str,
    triggered_by: str,
) -> dict[str, Any] | None:
    """Release a claimed dispatch step so fail-closed admission does not strand it."""
    safe_reason = sanitize_public_text(reason) or "dispatch_claim_released"
    released_at = datetime.now(timezone.utc).isoformat()
    step_cursor = await conn.execute(
        """
        update run_steps
        set
          status = 'pending',
          started_at = null,
          finished_at = null,
          payload_json = (
            coalesce(payload_json, '{}'::jsonb)
              - 'dispatch_state'
              - 'dispatch_kind'
              - 'dispatch_id'
              - 'dispatch_claimed_by'
              - 'dispatch_claimed_at'
              - 'dispatch_lease_expires_at'
          ) || %s::jsonb,
          updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and id = %s
          and payload_json->>'dispatch_id' = %s
          and payload_json->>'dispatch_state' = 'claimed'
        returning id, step_key
        """,
        (
            dumps_json(
                {
                    "dispatch_released_at": released_at,
                    "dispatch_release_reason": safe_reason,
                }
            ),
            tenant_id,
            parent_run_id,
            parent_step_id,
            dispatch_id,
        ),
    )
    step = await step_cursor.fetchone()
    if step is None:
        return None
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=triggered_by,
        action="run.multi_agent.dispatch.claim_released",
        target_type="run_step",
        target_id=str(step["id"]),
        payload_json={
            "parent_run_id": parent_run_id,
            "parent_step_id": str(step["id"]),
            "step_key": str(step.get("step_key") or ""),
            "dispatch_id": dispatch_id,
            "reason": safe_reason,
            "result_status": "pending",
        },
    )
    return {"parent_step_id": str(step["id"]), "step_key": str(step.get("step_key") or ""), "audit_id": audit_id}


async def create_multi_agent_dispatch_child_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    dispatch_id: str,
    handed_off_by: str,
    active_run_admission_limit: int,
) -> dict[str, Any]:
    """Create one queued child run for an admin-claimed multi-agent dispatch step."""
    try:
        admission_limit = int(active_run_admission_limit)
    except (TypeError, ValueError) as exc:
        raise RepositoryConflictError("active_run_admission_limit_required") from exc

    parent_cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id,
               trace_id, status, input_json
        from runs
        where tenant_id = %s and id = %s
        for update
        """,
        (tenant_id, parent_run_id),
    )
    parent = await parent_cursor.fetchone()
    if parent is None:
        raise RepositoryNotFoundError("run_not_found")
    if str(parent.get("status") or "") not in ACTIVE_RUN_STATUSES:
        raise RepositoryConflictError("run_not_dispatchable")

    step_cursor = await conn.execute(
        """
        select id, run_id, step_key, step_kind, status, title, role, sequence, payload_json
        from run_steps
        where tenant_id = %s
          and run_id = %s
          and payload_json->>'dispatch_id' = %s
        for update
        """,
        (tenant_id, parent_run_id, dispatch_id),
    )
    step = await step_cursor.fetchone()
    if step is None:
        raise RepositoryNotFoundError("dispatch_claim_not_found")
    payload = step.get("payload_json") if isinstance(step.get("payload_json"), dict) else {}
    if payload.get("dispatch_child_run_id"):
        raise RepositoryConflictError("dispatch_already_handed_off")
    if str(step.get("status") or "") != "running" or payload.get("dispatch_state") != "claimed":
        raise RepositoryConflictError("dispatch_claim_not_active")
    lease_expires_at = _parse_iso_datetime(payload.get("dispatch_lease_expires_at"))
    if lease_expires_at is None:
        raise RepositoryConflictError("dispatch_claim_lease_invalid")
    if lease_expires_at <= datetime.now(timezone.utc):
        raise RepositoryConflictError("dispatch_claim_expired")
    try:
        await enforce_user_active_run_admission(
            conn,
            tenant_id=tenant_id,
            user_id=str(parent["user_id"]),
            limit=admission_limit,
        )
    except RepositoryConflictError as exc:
        await release_multi_agent_dispatch_claim(
            conn,
            tenant_id=tenant_id,
            parent_run_id=parent_run_id,
            parent_step_id=str(step["id"]),
            dispatch_id=dispatch_id,
            reason=str(exc),
            triggered_by=handed_off_by,
        )
        raise

    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=parent_run_id)
    depends_on = [str(item) for item in payload.get("depends_on") or [] if str(item)]
    resume_payload = _completed_dependency_resume_payload(steps, parent_run_id=parent_run_id, depends_on=depends_on)
    source_input = parent.get("input_json") if isinstance(parent.get("input_json"), dict) else {}
    source_execution_input = _run_execution_input_from_row(parent)
    child_execution_input = {
        **_clean_child_execution_input(source_execution_input),
        "copied_from_run_id": parent_run_id,
        "execution_mode": "multi_agent",
        "multi_agent_steps": [
            {
                "step_key": str(step["step_key"]),
                "role": str(step.get("role") or ""),
                "title": str(step.get("title") or step["step_key"]),
                "depends_on": depends_on,
            }
        ],
        "multi_agent_dispatch": {
            "parent_run_id": parent_run_id,
            "parent_step_id": str(step["id"]),
            "step_key": str(step["step_key"]),
            "dispatch_id": dispatch_id,
        },
    }
    if resume_payload:
        child_execution_input["resume"] = resume_payload

    sanitized_source_input = sanitize_user_control_input(source_input)
    if isinstance(source_input.get("input"), dict):
        child_input_json = {
            **sanitized_source_input,
            "input": child_execution_input,
            "copied_from_run_id": parent_run_id,
        }
    else:
        child_input_json = child_execution_input

    skill = await resolve_agent_skill(
        conn,
        tenant_id=tenant_id,
        agent_id=str(parent["agent_id"]),
        skill_id=str(parent["skill_id"]),
    )
    executor_type = str(skill["executor_type"])
    release_decision = resolve_rollout_skill_decision(
        skill,
        tenant_id=tenant_id,
        skill_id=str(parent["skill_id"]),
        rollout_key=str(parent["user_id"]),
    )
    skill_version = release_decision.selected_version
    release_decision_payload = release_decision.to_payload()
    release_policy_version = skill_version if release_decision.policy_active else ""
    if isinstance(child_input_json, dict):
        child_input_json["executor_type"] = executor_type
        child_input_json["skill_version"] = skill_version
        child_input_json["release_decision"] = release_decision_payload

    child_run_id = new_id("run")
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
            child_run_id,
            tenant_id,
            parent["workspace_id"],
            parent["session_id"],
            parent["user_id"],
            parent["agent_id"],
            parent["skill_id"],
            standard_trace_id(child_run_id),
            RUN_CONTRACT_VERSION,
            EXECUTOR_RESULT_SCHEMA_VERSION,
            dumps_json(child_input_json),
            parent_run_id,
        ),
    )
    handed_off_at = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """
        update run_steps
        set
          payload_json = payload_json || %s::jsonb,
          updated_at = now()
        where tenant_id = %s
          and id = %s
          and payload_json->>'dispatch_id' = %s
        """,
        (
            dumps_json(
                {
                    "dispatch_state": "handed_off",
                    "dispatch_child_run_id": child_run_id,
                    "dispatch_handed_off_at": handed_off_at,
                }
            ),
            tenant_id,
            step["id"],
            dispatch_id,
        ),
    )
    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=parent_run_id,
        trace_id=parent.get("trace_id"),
        event_type="multi_agent_dispatch_handoff",
        stage="control",
        message="Multi-agent dispatch handed off to child run",
        visible_to_user=False,
        payload={
            "visible_to_user": False,
            "step_key": str(step["step_key"]),
            "dispatch_id": dispatch_id,
            "child_run_id": child_run_id,
        },
    )
    child_event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=child_run_id,
        event_type="run_multi_agent_child_created",
        stage="control",
        message="Multi-agent child run created",
        payload={
            "visible_to_user": True,
            "copied_from_run_id": parent_run_id,
            "parent_step_id": str(step["id"]),
            "step_key": str(step["step_key"]),
            "dispatch_id": dispatch_id,
        },
    )
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=handed_off_by,
        action="run.multi_agent.dispatch.handoff",
        target_type="run_step",
        target_id=str(step["id"]),
        trace_id=parent.get("trace_id"),
        payload_json={
            "parent_run_id": parent_run_id,
            "parent_step_id": str(step["id"]),
            "step_key": str(step["step_key"]),
            "dispatch_id": dispatch_id,
            "child_run_id": child_run_id,
            "admin_user_id": handed_off_by,
            "result_status": "queued",
        },
    )
    file_ids = list(source_input.get("file_ids") or [])
    return {
        "parent_run_id": parent_run_id,
        "parent_step_id": str(step["id"]),
        "step_key": str(step["step_key"]),
        "dispatch_id": dispatch_id,
        "child_run_id": child_run_id,
        "run_id": child_run_id,
        "session_id": parent["session_id"],
        "workspace_id": parent["workspace_id"],
        "user_id": parent["user_id"],
        "agent_id": parent["agent_id"],
        "skill_id": parent["skill_id"],
        "file_ids": file_ids,
        "input": child_execution_input,
        "executor_type": executor_type,
        "skill_version": skill_version,
        "release_policy_version": release_policy_version,
        "release_decision": release_decision_payload,
        "event_id": event_id,
        "child_event_id": child_event_id,
        "audit_id": audit_id,
    }


async def mark_multi_agent_dispatch_enqueue_failed(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    parent_step_id: str,
    dispatch_id: str,
    child_run_id: str,
    reason: str,
    triggered_by: str,
) -> dict[str, Any] | None:
    """Compensate a committed child handoff when Redis enqueue fails."""
    safe_reason = sanitize_public_text(reason) or "queue_enqueue_failed"
    step_cursor = await conn.execute(
        """
        update run_steps
        set
          status = 'pending',
          started_at = null,
          finished_at = null,
          payload_json = coalesce(payload_json, '{}'::jsonb)
            - 'dispatch_state'
            - 'dispatch_kind'
            - 'dispatch_id'
            - 'dispatch_claimed_by'
            - 'dispatch_claimed_at'
            - 'dispatch_lease_expires_at'
            - 'dispatch_child_run_id'
            - 'dispatch_handed_off_at',
          updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and id = %s
          and payload_json->>'dispatch_id' = %s
          and payload_json->>'dispatch_child_run_id' = %s
          and payload_json->>'dispatch_state' = 'handed_off'
        returning id, step_key
        """,
        (tenant_id, parent_run_id, parent_step_id, dispatch_id, child_run_id),
    )
    step = await step_cursor.fetchone()
    if step is None:
        return None
    child_cursor = await conn.execute(
        """
        update runs
        set
          status = 'failed',
          finished_at = now(),
          error_code = 'multi_agent_child_enqueue_failed',
          error_message = %s
        where tenant_id = %s
          and id = %s
          and copied_from_run_id = %s
          and status = 'queued'
        returning id
        """,
        (safe_reason, tenant_id, child_run_id, parent_run_id),
    )
    child = await child_cursor.fetchone()
    if child is None:
        raise RepositoryConflictError("dispatch_child_not_queued")
    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=parent_run_id,
        event_type="multi_agent_dispatch_enqueue_failed",
        stage="control",
        message="Multi-agent child enqueue failed; dispatch was reset",
        visible_to_user=False,
        payload={
            "visible_to_user": False,
            "step_key": str(step["step_key"]),
            "child_run_id": child_run_id,
            "reason": safe_reason,
        },
    )
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=triggered_by,
        action="run.multi_agent.dispatch.enqueue_failed",
        target_type="run_step",
        target_id=parent_step_id,
        trace_id=standard_trace_id(parent_run_id),
        payload_json={
            "parent_run_id": parent_run_id,
            "parent_step_id": parent_step_id,
            "step_key": str(step["step_key"]),
            "dispatch_id": dispatch_id,
            "child_run_id": child_run_id,
            "reason": safe_reason,
        },
    )
    return {
        "parent_run_id": parent_run_id,
        "parent_step_id": parent_step_id,
        "step_key": str(step["step_key"]),
        "child_run_id": child_run_id,
        "event_id": event_id,
        "audit_id": audit_id,
    }


def _terminal_dispatch_state(child_status: str) -> tuple[str, str]:
    if child_status == "succeeded":
        return "succeeded", "completed"
    if child_status == "failed":
        return "failed", "failed"
    if child_status == "cancelled":
        return "cancelled", "cancelled"
    raise ValueError("unsupported_child_status")


def _safe_child_result_output(result_json: dict[str, Any] | None) -> str:
    if not isinstance(result_json, dict):
        return ""
    return sanitize_public_text(result_json.get("message"))


def _safe_child_error_message(
    *,
    child_status: str,
    result_json: dict[str, Any] | None,
    error_message: str | None,
) -> str:
    safe_message = sanitize_public_text(error_message)
    if safe_message:
        return safe_message
    if isinstance(result_json, dict):
        safe_message = sanitize_public_text(result_json.get("message"))
        if safe_message:
            return safe_message
    return "child_run_cancelled" if child_status == "cancelled" else "child_run_failed"


def _safe_child_error_code(error_code: str | None, *, child_status: str) -> str:
    fallback = "child_run_cancelled" if child_status == "cancelled" else "child_run_failed"
    safe_code = sanitize_public_text(error_code)
    if not safe_code:
        return fallback
    normalized = safe_code.strip().lower().replace("-", "_")
    if not normalized or len(normalized) > 80:
        return fallback
    if not all(ch.isalnum() or ch == "_" for ch in normalized):
        return fallback
    return standard_error_code(normalized)


def _parent_multi_agent_message(status: str) -> str:
    if status == "succeeded":
        return "Multi-agent run succeeded"
    if status == "failed":
        return "Multi-agent run failed"
    if status == "cancelled":
        return "Multi-agent run cancelled"
    return "Multi-agent run finalized"


def _safe_parent_step_text(value: Any) -> str:
    sanitized = sanitize_public_text(value)
    if HASH_LIKE_VALUE_PATTERN.fullmatch(sanitized.strip()):
        return ""
    return sanitized


def _safe_parent_step_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    depends_on = payload.get("depends_on")
    if isinstance(depends_on, list):
        cleaned["depends_on"] = [
            safe_dependency
            for item in depends_on
            if (safe_dependency := _safe_parent_step_text(item))
        ]
    else:
        cleaned["depends_on"] = []
    for key in (
        "dispatch_state",
        "dispatch_child_run_id",
        "checkpoint_id",
        "source_step_id",
        "output",
        "error_code",
        "error",
    ):
        safe_value = _safe_parent_step_text(payload.get(key))
        if safe_value:
            target_key = "child_run_id" if key == "dispatch_child_run_id" else key
            cleaned[target_key] = safe_value
    return cleaned


def _multi_agent_parent_step_summary(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    safe_payload = _safe_parent_step_payload(payload)
    return {
        "step_key": _safe_parent_step_text(row.get("step_key")) or str(row.get("id") or ""),
        "status": str(row.get("status") or ""),
        "role": _safe_parent_step_text(row.get("role")) or None,
        "sequence": _coerce_int(row.get("sequence")),
        "depends_on": safe_payload.pop("depends_on", []),
        **safe_payload,
    }


def _multi_agent_parent_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(steps),
        "succeeded": sum(1 for item in steps if str(item.get("status") or "") == "succeeded"),
        "failed": sum(1 for item in steps if str(item.get("status") or "") == "failed"),
        "cancelled": sum(1 for item in steps if str(item.get("status") or "") == "cancelled"),
    }


def _multi_agent_parent_status(parent_run: dict[str, Any], steps: list[dict[str, Any]]) -> str | None:
    if not steps:
        return None
    statuses = {str(item.get("status") or "") for item in steps}
    if not statuses.issubset(TERMINAL_RUN_STATUSES):
        return None
    if "failed" in statuses:
        return "failed"
    if parent_run.get("cancel_requested_at") or "cancelled" in statuses:
        return "cancelled"
    return "succeeded"


def _multi_agent_parent_has_open_dispatch(steps: list[dict[str, Any]]) -> bool:
    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        if str(payload.get("dispatch_state") or "") in {"claimed", "handed_off"}:
            return True
    return False


def _configured_multi_agent_step_keys(configured_steps: object) -> set[str] | None:
    if not isinstance(configured_steps, list):
        return None
    keys: list[str] = []
    for item in configured_steps:
        if not isinstance(item, dict):
            return None
        key = str(item.get("step_key") or item.get("stepKey") or "").strip()
        if not key:
            return None
        keys.append(key)
    key_set = set(keys)
    if len(key_set) != len(keys):
        return None
    return key_set


async def finalize_multi_agent_parent_run_if_ready(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    triggered_by_child_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Finalize a multi-agent parent run after all server-owned child steps settle."""
    parent_cursor = await conn.execute(
        """
        select id, tenant_id, copied_from_run_id, trace_id, status, cancel_requested_at, input_json
        from runs
        where tenant_id = %s and id = %s
        for update skip locked
        """,
        (tenant_id, parent_run_id),
    )
    parent_run = await parent_cursor.fetchone()
    if parent_run is None:
        return None
    if parent_run.get("copied_from_run_id"):
        return None
    parent_status = str(parent_run.get("status") or "")
    if parent_status in TERMINAL_RUN_STATUSES:
        return None
    if parent_status != "running" and parent_run.get("cancel_requested_at") is None:
        return None
    execution_input = _run_execution_input_from_row(parent_run)
    if str(execution_input.get("execution_mode") or "") != "multi_agent":
        return None
    if "multi_agent_steps" in execution_input:
        configured_keys = _configured_multi_agent_step_keys(execution_input.get("multi_agent_steps"))
    else:
        configured_keys = set()
    if configured_keys is None:
        return None
    configured_count = len(configured_keys)
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=parent_run_id)
    if not steps and configured_count <= 0:
        return None
    if len(steps) < configured_count:
        return None
    recorded_keys = {str(row.get("step_key") or "").strip() for row in steps}
    if not configured_keys.issubset(recorded_keys):
        return None
    if _multi_agent_parent_has_open_dispatch(steps):
        return None
    target_status = _multi_agent_parent_status(parent_run, steps)
    if target_status is None:
        return None
    active_cursor = await conn.execute(
        """
        select child.id, child.status
        from runs child
        where child.tenant_id = %s
          and child.copied_from_run_id = %s
          and child.status in ('queued', 'running')
          and (
            child.input_json#>>'{input,multi_agent_dispatch,parent_run_id}' = %s
            or child.input_json#>>'{multi_agent_dispatch,parent_run_id}' = %s
          )
        """,
        (tenant_id, parent_run_id, parent_run_id, parent_run_id),
    )
    active_children = list(await active_cursor.fetchall())
    if active_children:
        return None
    summaries = [_multi_agent_parent_step_summary(row) for row in steps]
    counts = _multi_agent_parent_counts(summaries)
    result_json: dict[str, Any] = {
        "message": _parent_multi_agent_message(target_status),
        "multi_agent": {
            "status": target_status,
            "counts": counts,
            "steps": summaries,
        },
    }
    safe_triggered_by = sanitize_public_text(triggered_by_child_run_id)
    if safe_triggered_by:
        result_json["multi_agent"]["triggered_by_child_run_id"] = safe_triggered_by
    update_cursor = await conn.execute(
        """
        update runs
        set
          status = %s,
          result_json = %s::jsonb,
          finished_at = now(),
          error_code = case when %s = 'failed' then 'multi_agent_child_failed' else null end,
          error_message = case when %s = 'failed' then 'Multi-agent child step failed' else null end
        where tenant_id = %s
          and id = %s
          and status not in ('succeeded', 'failed', 'cancelled')
        returning id, status
        """,
        (
            target_status,
            dumps_json(result_json),
            target_status,
            target_status,
            tenant_id,
            parent_run_id,
        ),
    )
    updated = await update_cursor.fetchone()
    if updated is None:
        return None
    event_payload: dict[str, Any] = {
        "visible_to_user": False,
        "status": target_status,
        "counts": counts,
    }
    if safe_triggered_by:
        event_payload["triggered_by_child_run_id"] = safe_triggered_by
    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=parent_run_id,
        trace_id=parent_run.get("trace_id"),
        event_type="multi_agent_parent_finalized",
        stage="control",
        message=_parent_multi_agent_message(target_status),
        visible_to_user=False,
        payload=event_payload,
    )
    audit_payload: dict[str, Any] = {"status": target_status, "counts": counts}
    if safe_triggered_by:
        audit_payload["triggered_by_child_run_id"] = safe_triggered_by
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=None,
        action="run.multi_agent.parent.finalize",
        target_type="run",
        target_id=parent_run_id,
        trace_id=parent_run.get("trace_id"),
        payload_json=audit_payload,
    )
    return {
        "parent_run_id": parent_run_id,
        "status": target_status,
        "event_id": event_id,
        "audit_id": audit_id,
        "counts": counts,
    }


def _child_dispatch_metadata(child_run: dict[str, Any]) -> dict[str, Any]:
    execution_input = _run_execution_input_from_row(child_run)
    dispatch = execution_input.get("multi_agent_dispatch")
    return dispatch if isinstance(dispatch, dict) else {}


async def reconcile_multi_agent_child_run_terminal_state(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    child_run_id: str,
    child_status: str,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """Mirror a server-owned terminal child run onto its handed-off parent step."""

    parent_step_status, dispatch_state = _terminal_dispatch_state(child_status)
    child_cursor = await conn.execute(
        """
        select id, tenant_id, copied_from_run_id, trace_id, status, input_json
        from runs
        where tenant_id = %s and id = %s
        for update
        """,
        (tenant_id, child_run_id),
    )
    child_run = await child_cursor.fetchone()
    if child_run is None:
        return None
    if str(child_run.get("status") or "") != child_status or child_status not in TERMINAL_RUN_STATUSES:
        return None
    parent_run_id = str(child_run.get("copied_from_run_id") or "").strip()
    if not parent_run_id:
        return None
    dispatch = _child_dispatch_metadata(child_run)
    if str(dispatch.get("parent_run_id") or "") != parent_run_id:
        return None
    parent_step_id = str(dispatch.get("parent_step_id") or "").strip()
    dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
    step_key = str(dispatch.get("step_key") or "").strip()
    if not parent_step_id or not dispatch_id or not step_key:
        return None

    step_cursor = await conn.execute(
        """
        select id, run_id, step_key, step_kind, status, title, role, sequence, payload_json
        from run_steps
        where tenant_id = %s
          and run_id = %s
          and id = %s
        for update
        """,
        (tenant_id, parent_run_id, parent_step_id),
    )
    parent_step = await step_cursor.fetchone()
    if parent_step is None:
        return None
    step_payload = parent_step.get("payload_json") if isinstance(parent_step.get("payload_json"), dict) else {}
    if (
        str(parent_step.get("step_key") or "") != step_key
        or str(step_payload.get("dispatch_id") or "") != dispatch_id
        or str(step_payload.get("dispatch_child_run_id") or "") != child_run_id
        or step_payload.get("dispatch_state") != "handed_off"
    ):
        return None

    reconciled_at = datetime.now(timezone.utc).isoformat()
    update_payload: dict[str, Any] = {
        "dispatch_state": dispatch_state,
        "dispatch_child_status": child_status,
        "dispatch_reconciled_at": reconciled_at,
        "dispatch_child_run_id": child_run_id,
    }
    if child_status == "succeeded":
        output = _safe_child_result_output(result_json)
        if output:
            update_payload["output"] = output
            lineage = artifact_lineage_contract(
                {
                    "checkpoint_id": f"checkpoint_{parent_step_id}",
                    "source_step_id": parent_step_id,
                },
                source_run_id=parent_run_id,
            )
            checkpoint_id = lineage.get("checkpoint_id")
            source_step_id = lineage.get("source_step_id")
            if checkpoint_id and source_step_id:
                update_payload["checkpoint_id"] = str(checkpoint_id)
                update_payload["source_step_id"] = str(source_step_id)
    elif child_status in {"failed", "cancelled"}:
        update_payload["error_code"] = _safe_child_error_code(error_code or f"child_run_{child_status}", child_status=child_status)
        update_payload["error"] = _safe_child_error_message(
            child_status=child_status,
            result_json=result_json,
            error_message=error_message,
        )

    update_cursor = await conn.execute(
        """
        update run_steps
        set
          payload_json = payload_json || %s::jsonb,
          status = %s,
          finished_at = coalesce(finished_at, now()),
          updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and id = %s
          and payload_json->>'dispatch_id' = %s
          and payload_json->>'dispatch_child_run_id' = %s
          and payload_json->>'dispatch_state' = 'handed_off'
        returning id
        """,
        (
            dumps_json(update_payload),
            parent_step_status,
            tenant_id,
            parent_run_id,
            parent_step_id,
            dispatch_id,
            child_run_id,
        ),
    )
    updated = await update_cursor.fetchone()
    if updated is None:
        return None

    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=parent_run_id,
        trace_id=child_run.get("trace_id"),
        event_type="multi_agent_dispatch_reconciled",
        stage="control",
        message="Multi-agent child run reconciled",
        visible_to_user=False,
        payload={
            "visible_to_user": False,
            "step_key": step_key,
            "dispatch_id": dispatch_id,
            "child_run_id": child_run_id,
            "child_status": child_status,
            "dispatch_state": dispatch_state,
        },
    )
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=None,
        action="run.multi_agent.dispatch.reconcile",
        target_type="run_step",
        target_id=parent_step_id,
        trace_id=child_run.get("trace_id"),
        payload_json={
            "parent_run_id": parent_run_id,
            "parent_step_id": parent_step_id,
            "step_key": step_key,
            "dispatch_id": dispatch_id,
            "child_run_id": child_run_id,
            "child_status": child_status,
            "result_status": parent_step_status,
        },
    )
    await finalize_multi_agent_parent_run_if_ready(
        conn,
        tenant_id=tenant_id,
        parent_run_id=parent_run_id,
        triggered_by_child_run_id=child_run_id,
    )
    return {
        "parent_run_id": parent_run_id,
        "parent_step_id": parent_step_id,
        "child_run_id": child_run_id,
        "step_key": step_key,
        "status": parent_step_status,
        "dispatch_state": dispatch_state,
        "event_id": event_id,
        "audit_id": audit_id,
    }


async def propagate_multi_agent_parent_cancel(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    requested_by: str,
    requested_by_role: str | None = None,
) -> dict[str, Any]:
    """Request cancellation for active server-owned child runs of a multi-agent parent."""

    result: dict[str, Any] = {
        "child_run_ids": [],
        "queued_child_run_ids": [],
        "running_child_run_ids": [],
        "active_sandbox_leases": [],
        "event_ids": [],
        "audit_ids": [],
    }
    cursor = await conn.execute(
        """
        select
          child.id,
          child.status,
          child.trace_id,
          child.cancel_requested_at,
          child.input_json,
          parent_step.id as parent_step_id,
          parent_step.step_key,
          parent_step.payload_json as parent_step_payload_json
        from runs child
        join run_steps parent_step
          on parent_step.tenant_id = child.tenant_id
         and parent_step.run_id = child.copied_from_run_id
         and parent_step.payload_json->>'dispatch_child_run_id' = child.id
        where child.tenant_id = %s
          and child.copied_from_run_id = %s
          and child.status in ('queued', 'running')
          and (
            child.input_json#>>'{input,multi_agent_dispatch,parent_run_id}' = %s
            or child.input_json#>>'{multi_agent_dispatch,parent_run_id}' = %s
          )
          and parent_step.payload_json->>'dispatch_state' = 'handed_off'
        for update of child, parent_step
        """,
        (tenant_id, parent_run_id, parent_run_id, parent_run_id),
    )
    rows = await cursor.fetchall()
    for row in rows:
        child_run_id = str(row.get("id") or "")
        if not child_run_id:
            continue
        dispatch = _child_dispatch_metadata(row)
        parent_step_payload = (
            row.get("parent_step_payload_json") if isinstance(row.get("parent_step_payload_json"), dict) else {}
        )
        parent_step_id = str(dispatch.get("parent_step_id") or "").strip()
        step_key = str(dispatch.get("step_key") or "").strip()
        dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
        if (
            str(dispatch.get("parent_run_id") or "") != parent_run_id
            or str(row.get("parent_step_id") or "") != parent_step_id
            or str(row.get("step_key") or "") != step_key
            or str(parent_step_payload.get("dispatch_id") or "") != dispatch_id
            or str(parent_step_payload.get("dispatch_child_run_id") or "") != child_run_id
            or parent_step_payload.get("dispatch_state") != "handed_off"
        ):
            continue
        update_cursor = await conn.execute(
            """
            update runs
            set
              cancel_requested_at = coalesce(cancel_requested_at, now()),
              cancel_requested_by = coalesce(cancel_requested_by, %s),
              status = case when status = 'queued' then 'cancelled' else status end,
              finished_at = case when status = 'queued' then now() else finished_at end
            where tenant_id = %s
              and id = %s
              and status in ('queued', 'running')
            returning id, status, trace_id
            """,
            (requested_by, tenant_id, child_run_id),
        )
        updated = await update_cursor.fetchone()
        if updated is None:
            continue
        child_status = str(updated.get("status") or "")
        result["child_run_ids"].append(child_run_id)
        if child_status == "cancelled":
            result["queued_child_run_ids"].append(child_run_id)
            await _cancel_open_run_steps(conn, tenant_id=tenant_id, run_id=child_run_id)
        else:
            result["running_child_run_ids"].append(child_run_id)

        active_sandbox_leases = await list_active_sandbox_leases_for_run(
            conn,
            tenant_id=tenant_id,
            run_id=child_run_id,
        )
        result["active_sandbox_leases"].extend(active_sandbox_leases)

        if row.get("cancel_requested_at") is None:
            event_id = await append_event(
                conn,
                tenant_id=tenant_id,
                run_id=child_run_id,
                trace_id=updated.get("trace_id"),
                event_type="cancel_requested",
                stage="control",
                message="已随父任务请求取消",
                payload={
                    "visible_to_user": True,
                    "severity": "warning",
                    "requested_by": requested_by,
                    "requested_by_role": requested_by_role or "owner",
                    "source": "multi_agent_parent_cancel",
                    "parent_run_id": parent_run_id,
                },
            )
            result["event_ids"].append(event_id)

        if child_status == "cancelled":
            cancelled_event_id = await append_event(
                conn,
                tenant_id=tenant_id,
                run_id=child_run_id,
                trace_id=updated.get("trace_id"),
                event_type="run_cancelled",
                stage="control",
                message="任务已取消",
                payload={
                    "visible_to_user": True,
                    "severity": "warning",
                    "source": "multi_agent_parent_cancel",
                },
            )
            result["event_ids"].append(cancelled_event_id)
            reconciled = await reconcile_multi_agent_child_run_terminal_state(
                conn,
                tenant_id=tenant_id,
                child_run_id=child_run_id,
                child_status="cancelled",
                result_json={"message": "parent_cancel_requested"},
                error_code="parent_cancel_requested",
                error_message="parent_cancel_requested",
            )
            if reconciled:
                if reconciled.get("event_id"):
                    result["event_ids"].append(reconciled["event_id"])
                if reconciled.get("audit_id"):
                    result["audit_ids"].append(reconciled["audit_id"])

        audit_id = await append_audit_log(
            conn,
            tenant_id=tenant_id,
            user_id=requested_by,
            action="run.multi_agent.dispatch.cancel_propagate",
            target_type="run",
            target_id=child_run_id,
            trace_id=updated.get("trace_id"),
            payload_json={
                "parent_run_id": parent_run_id,
                "parent_step_id": parent_step_id,
                "step_key": step_key,
                "dispatch_id": dispatch_id,
                "child_run_id": child_run_id,
                "requested_by_role": requested_by_role or "owner",
                "result_status": "cancelled" if child_status == "cancelled" else "cancel_requested",
            },
        )
        result["audit_ids"].append(audit_id)
    return result


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


async def get_admin_runtime_run_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Return same-tenant run status and redacted recent failure aggregates for Admin Runtime."""
    status_cursor = await conn.execute(
        """
        select status, count(*) as count
        from runs
        where tenant_id = %s
        group by status
        """,
        (tenant_id,),
    )
    status_rows = list(await status_cursor.fetchall())
    by_status = {
        str(row["status"]): _coerce_int(row["count"])
        for row in status_rows
        if row.get("status") is not None
    }
    failure_cursor = await conn.execute(
        """
        select id, user_id, agent_id, error_code, error_message, created_at
        from runs
        where tenant_id = %s
          and status = 'failed'
        order by created_at desc
        limit %s
        """,
        (tenant_id, limit),
    )
    failure_rows = list(await failure_cursor.fetchall())
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "active": sum(by_status.get(status, 0) for status in ACTIVE_RUN_STATUSES),
        "terminal": sum(by_status.get(status, 0) for status in TERMINAL_RUN_STATUSES),
        "recent_failures": [
            {
                "run_id": row["id"],
                "user_id": row.get("user_id"),
                "agent_id": row.get("agent_id"),
                "error_code": sanitize_public_text(row.get("error_code")) or None,
                "error_message": sanitize_public_text(row.get("error_message")),
                "created_at": row.get("created_at"),
            }
            for row in failure_rows
        ],
    }


async def get_admin_runtime_admission_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int,
    top_user_limit: int = 10,
) -> dict[str, Any]:
    """Return same-tenant active-run admission pressure for Admin Runtime."""
    active_limit = max(int(limit), 0)
    top_limit = max(min(int(top_user_limit), 50), 1)
    totals_cursor = await conn.execute(
        """
        with grouped as (
          select user_id, count(*) as active
          from runs
          where tenant_id = %s
            and status in ('queued', 'running')
          group by user_id
        )
        select
          coalesce(sum(active), 0) as active_runs,
          count(*) filter (where user_id is not null) as active_users,
          count(*) filter (where user_id is not null and %s > 0 and active >= %s) as saturated_users
        from grouped
        """,
        (tenant_id, active_limit, active_limit),
    )
    totals = await totals_cursor.fetchone() or {}
    top_cursor = await conn.execute(
        """
        select user_id, count(*) as active
        from runs
        where tenant_id = %s
          and status in ('queued', 'running')
          and user_id is not null
        group by user_id
        order by count(*) desc, user_id asc
        limit %s
        """,
        (tenant_id, top_limit),
    )
    top_rows = list(await top_cursor.fetchall())
    top_users = [
        {
            "user_id": str(row["user_id"]),
            "active": _coerce_int(row["active"]),
            "saturated": active_limit > 0 and _coerce_int(row["active"]) >= active_limit,
        }
        for row in top_rows
        if row.get("user_id")
    ]
    return {
        "policy_active": active_limit > 0,
        "max_active_runs_per_user": active_limit,
        "active_runs": _coerce_int(totals.get("active_runs")),
        "active_users": _coerce_int(totals.get("active_users")),
        "saturated_users": _coerce_int(totals.get("saturated_users")),
        "top_users": top_users,
    }


async def get_admin_runtime_observability_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Return same-tenant observability seed metrics for the Admin Runtime overview."""
    cursor = await conn.execute(
        """
        with event_summary as (
          select
            count(*) as event_count,
            count(*) filter (where error_code is not null and error_code <> '') as event_error_count,
            avg(latency_ms) filter (where latency_ms is not null) as avg_latency_ms,
            max(latency_ms) as max_latency_ms,
            percentile_cont(0.5) within group (order by latency_ms)
              filter (where latency_ms is not null) as p50_latency_ms,
            percentile_cont(0.95) within group (order by latency_ms)
              filter (where latency_ms is not null) as p95_latency_ms,
            percentile_cont(0.99) within group (order by latency_ms)
              filter (where latency_ms is not null) as p99_latency_ms
          from run_events
          where tenant_id = %s
        ),
        artifact_summary as (
          select count(*) as artifact_count
          from artifacts
          where tenant_id = %s
        ),
        run_totals as (
          select
            count(*) filter (where error_code is not null and error_code <> '') as run_error_count,
            coalesce(sum(input_token_count), 0) as run_input_token_count,
            coalesce(sum(output_token_count), 0) as run_output_token_count,
            coalesce(sum(total_token_count), 0) as run_total_token_count,
            coalesce(sum(estimated_cost_minor), 0) as run_estimated_cost_minor
          from runs
          where tenant_id = %s
        ),
        error_types as (
          select coalesce(jsonb_object_agg(error_code, error_count), '{}'::jsonb) as error_types
          from (
            select error_code, count(*) as error_count
            from (
              select error_code
              from runs
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
              union all
              select error_code
              from run_events
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
            ) all_errors
            group by error_code
          ) errors
        )
        select
          event_summary.event_count,
          artifact_summary.artifact_count,
          run_totals.run_error_count + event_summary.event_error_count as error_count,
          error_types.error_types,
          event_summary.avg_latency_ms,
          event_summary.max_latency_ms,
          event_summary.p50_latency_ms,
          event_summary.p95_latency_ms,
          event_summary.p99_latency_ms,
          run_totals.run_input_token_count as input_token_count,
          run_totals.run_output_token_count as output_token_count,
          run_totals.run_total_token_count as total_token_count,
          run_totals.run_estimated_cost_minor as estimated_cost_minor
        from event_summary, artifact_summary, run_totals, error_types
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
    )
    row = await cursor.fetchone() or {}
    raw_error_types = row.get("error_types") if isinstance(row.get("error_types"), dict) else {}
    error_types = {
        sanitized_key: _coerce_int(value)
        for key, value in raw_error_types.items()
        if (sanitized_key := sanitize_public_text(key))
    }
    avg_latency = row.get("avg_latency_ms")
    max_latency = row.get("max_latency_ms")
    p50_latency = row.get("p50_latency_ms")
    p95_latency = row.get("p95_latency_ms")
    p99_latency = row.get("p99_latency_ms")
    return {
        "event_count": _coerce_int(row.get("event_count")),
        "artifact_count": _coerce_int(row.get("artifact_count")),
        "error_count": _coerce_int(row.get("error_count")),
        "error_types": error_types,
        "error_categories": summarize_error_categories(error_types),
        "latency_ms": {
            "avg": _coerce_int(avg_latency) if avg_latency is not None else None,
            "max": _coerce_int(max_latency) if max_latency is not None else None,
            "p50": _coerce_int(p50_latency) if p50_latency is not None else None,
            "p95": _coerce_int(p95_latency) if p95_latency is not None else None,
            "p99": _coerce_int(p99_latency) if p99_latency is not None else None,
        },
        "token_counts": {
            "input": _coerce_int(row.get("input_token_count")),
            "output": _coerce_int(row.get("output_token_count")),
            "total": _coerce_int(row.get("total_token_count")),
        },
        "estimated_cost_minor": _coerce_int(row.get("estimated_cost_minor")),
    }


def _sandbox_lease_admin_projection(row: dict[str, Any]) -> dict[str, Any]:
    resource_limits = sanitize_public_payload(
        row.get("resource_limits_json") if isinstance(row.get("resource_limits_json"), dict) else {}
    )
    user_visible_payload = sanitize_public_payload(
        row.get("user_visible_payload_json") if isinstance(row.get("user_visible_payload_json"), dict) else {}
    )
    lease_payload = sanitize_public_payload(
        row.get("lease_payload_json") if isinstance(row.get("lease_payload_json"), dict) else {}
    )
    return {
        "lease_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "run_id": str(row["run_id"]),
        "trace_id": str(row.get("trace_id") or standard_trace_id(str(row["run_id"]))),
        "sandbox_mode": str(row["sandbox_mode"]),
        "provider": str(row.get("provider") or "fake"),
        "status": str(row.get("status") or "active"),
        "browser_enabled": bool(row.get("browser_enabled")),
        "resource_limits": resource_limits if isinstance(resource_limits, dict) else {},
        "workspace": user_visible_payload if isinstance(user_visible_payload, dict) else {},
        "lease_payload": lease_payload if isinstance(lease_payload, dict) else {},
        "heartbeat_at": row.get("heartbeat_at"),
        "expires_at": row.get("expires_at"),
        "released_at": row.get("released_at"),
        "release_reason": str(row.get("release_reason") or ""),
        "created_at": row.get("created_at"),
    }


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
    sandbox_leases = await list_sandbox_leases_for_run(conn, tenant_id=tenant_id, run_id=run_id)
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
        "sandbox_leases": [_sandbox_lease_admin_projection(item) for item in sandbox_leases],
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
        source_execution_input.pop("resume", None)
    else:
        source_execution_input = {}
    copied_execution_input = {**source_execution_input, "copied_from_run_id": run_id}
    completed_step_outputs, completed_step_checkpoints = await _completed_steps_for_resume(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if completed_step_outputs:
        resume_payload: dict[str, Any] = {
            "copied_from_run_id": run_id,
            "completed_step_outputs": completed_step_outputs,
        }
        if completed_step_checkpoints:
            resume_payload["completed_step_checkpoints"] = completed_step_checkpoints
        copied_execution_input["resume"] = resume_payload
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


async def retry_run_as_new_task(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> dict[str, Any] | None:
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id, for_update=True)
    if source is None:
        return None
    source_status = str(source.get("status") or "")
    if source_status not in RETRYABLE_RUN_STATUSES:
        raise RepositoryConflictError("status_not_retryable")
    active_retry = await get_active_retry_for_source_run(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if active_retry is not None:
        raise RepositoryConflictError("retry_already_active")
    copied = await copy_run_as_new_task(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id)
    if copied is None:
        return None
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=source.get("trace_id"),
        event_type="retry_requested",
        stage="control",
        message="已请求重试",
        payload={"visible_to_user": True, "new_run_id": copied["run_id"]},
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=str(copied["run_id"]),
        event_type="run_retry_created",
        stage="control",
        message="已创建重试任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="run.retry",
        target_type="run",
        target_id=run_id,
        trace_id=source.get("trace_id"),
        payload_json={
            "source_run_id": run_id,
            "new_run_id": copied["run_id"],
            "source_status": source_status,
        },
    )
    return copied


async def resume_run_as_new_task(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> dict[str, Any] | None:
    """Create a queued resume child run from a non-active source with reusable output."""
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id, for_update=True)
    if source is None:
        return None
    source_status = str(source.get("status") or "")
    if source_status in ACTIVE_RUN_STATUSES:
        raise RepositoryConflictError("active_run")
    completed_step_outputs, _completed_step_checkpoints = await _completed_steps_for_resume(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if not completed_step_outputs:
        raise RepositoryConflictError("no_checkpoint_outputs")
    active_resume = await get_active_resume_for_source_run(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if active_resume is not None:
        raise RepositoryConflictError("resume_already_active")
    copied = await copy_run_as_new_task(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id)
    if copied is None:
        return None
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=source.get("trace_id"),
        event_type="resume_requested",
        stage="control",
        message="已请求从 checkpoint 恢复",
        payload={"visible_to_user": True, "new_run_id": copied["run_id"]},
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=str(copied["run_id"]),
        event_type="run_resume_created",
        stage="control",
        message="已创建恢复任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="run.resume",
        target_type="run",
        target_id=run_id,
        trace_id=source.get("trace_id"),
        payload_json={
            "source_run_id": run_id,
            "new_run_id": copied["run_id"],
            "source_status": source_status,
        },
    )
    return copied


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


async def _completed_steps_for_resume(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    cursor = await conn.execute(
        """
        select id, step_key, payload_json
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
    checkpoints: dict[str, dict[str, str]] = {}
    for row in rows:
        payload = row.get("payload_json") or {}
        if not isinstance(payload, dict) or payload.get("output") is None:
            continue
        step_key = str(row["step_key"])
        outputs[step_key] = str(payload["output"])
        lineage = artifact_lineage_contract(
            {
                "checkpoint_id": payload.get("checkpoint_id"),
                "source_step_id": payload.get("source_step_id") or row.get("id"),
            },
            source_run_id=payload.get("copied_from_run_id") or run_id,
        )
        checkpoint_id = lineage.get("checkpoint_id")
        source_step_id = lineage.get("source_step_id")
        source_run_id = lineage.get("source_run_id")
        if checkpoint_id and source_step_id and source_run_id:
            checkpoints[step_key] = {
                "checkpoint_id": str(checkpoint_id),
                "source_step_id": str(source_step_id),
                "copied_from_run_id": str(source_run_id),
            }
    return outputs, checkpoints


async def _completed_step_outputs_for_resume(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> dict[str, str]:
    outputs, _checkpoints = await _completed_steps_for_resume(conn, tenant_id=tenant_id, run_id=run_id)
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
