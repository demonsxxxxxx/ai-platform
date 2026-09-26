"""Capability admission persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.auth import normalize_roles
from app.capability_distribution import CapabilityAccessContext
from app.capability_distribution import CapabilityDistributionSubject
from app.capability_distribution import resolve_capability_access
from app.identity.infrastructure.capability_distributions_postgres import _capability_not_authorized
from app.identity.infrastructure.capability_distributions_postgres import get_capability_distribution_row
from app.identity.infrastructure.capability_distributions_postgres import is_capability_distribution_archived
from app.mcp import repository as _mcp_repository
from app.mcp.repository import get_mcp_tool_registry_entry
from app.platform.postgres.errors import RepositoryAuthorizationError
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.errors import RepositoryNotFoundError
from app.projection_redaction import sanitize_user_control_input
from app.projection_redaction import strip_server_owned_control_metadata
from app.skills.infrastructure.postgres import validate_replay_skill_manifests
from app.skills.infrastructure.resolution_postgres import DEFAULT_RUN_EXECUTOR_TYPES
from app.skills.infrastructure.resolution_postgres import resolve_agent_skill
from app.skills.infrastructure.resolution_postgres import resolve_selected_skill
from app.skills.infrastructure.versions_postgres import get_effective_skill_version_for_policy
from app.skills.lifecycle import is_user_runnable_status
from app.skills.release_policy import resolve_rollout_skill_decision
from psycopg import AsyncConnection
from typing import Any


_MCP_TOOL_ID_KEYS = ("mcp_tool_ids", "mcpToolIds")


_CALLER_AUTH_SNAPSHOT_KEY_ALIASES = {
    "principalroles",
    "principaldepartmentid",
    "authsource",
}


def _append_explicit_mcp_tool_ids(target: list[str], container: dict[str, Any]) -> None:
    for key in _MCP_TOOL_ID_KEYS:
        if key not in container:
            continue
        raw_tool_ids = container[key]
        if not isinstance(raw_tool_ids, list):
            raise _capability_not_authorized()
        for raw_tool_id in raw_tool_ids:
            if not isinstance(raw_tool_id, str) or not raw_tool_id.strip():
                raise _capability_not_authorized()
            tool_id = raw_tool_id.strip()
            if tool_id not in target:
                target.append(tool_id)


def _explicit_mcp_tool_scope(container: dict[str, Any]) -> tuple[bool, list[str]]:
    tool_ids: list[str] = []
    _append_explicit_mcp_tool_ids(tool_ids, container)
    return any(key in container for key in _MCP_TOOL_ID_KEYS), tool_ids


def extract_run_mcp_tool_ids(normalized_input: dict[str, Any]) -> list[str]:
    """Extract explicit MCP IDs selected for the run."""

    requested_tool_ids: list[str] = []
    _append_explicit_mcp_tool_ids(requested_tool_ids, normalized_input)
    return requested_tool_ids


def run_mcp_tool_ids_for_skill(skill: dict[str, Any], normalized_input: dict[str, Any]) -> list[str]:
    """Return one canonical MCP authorization set for a Harness-backed Skill."""

    requested_tool_ids: list[str] = []
    skill_id = str(skill.get("skill_id") or "").strip()
    backing_tool_id = str(skill.get("backing_mcp_tool_id") or "").strip()
    if skill_id == _mcp_repository.TRUSTED_BUILTIN_MCP_TOOL_ID and not backing_tool_id:
        raise _capability_not_authorized()
    if backing_tool_id:
        requested_tool_ids.append(backing_tool_id)
    for tool_id in extract_run_mcp_tool_ids(normalized_input):
        if tool_id not in requested_tool_ids:
            requested_tool_ids.append(tool_id)
    return requested_tool_ids


def strip_caller_run_auth_snapshot_fields(value: Any) -> Any:
    """Remove caller-controlled run authorization snapshot fields recursively."""

    if isinstance(value, list):
        return [strip_caller_run_auth_snapshot_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = "".join(character for character in str(key) if character.isalnum()).lower()
        if normalized_key in _CALLER_AUTH_SNAPSHOT_KEY_ALIASES:
            continue
        cleaned[key] = strip_caller_run_auth_snapshot_fields(item)
    return cleaned


def normalize_run_input_for_enqueue(input_payload: object, *, redact_public: bool) -> dict[str, Any]:
    """Sanitize run input while preserving validated explicit MCP selectors."""

    if not isinstance(input_payload, dict):
        return {}
    top_level_tools_present, top_level_tool_ids = _explicit_mcp_tool_scope(input_payload)
    if redact_public:
        cleaned = sanitize_user_control_input(input_payload)
    else:
        stripped = strip_server_owned_control_metadata(input_payload)
        cleaned = stripped if isinstance(stripped, dict) else {}
    normalized = strip_caller_run_auth_snapshot_fields(cleaned)
    if not isinstance(normalized, dict):
        normalized = {}
    if redact_public:
        for key in _MCP_TOOL_ID_KEYS:
            normalized.pop(key, None)
    if top_level_tools_present:
        normalized["mcp_tool_ids"] = top_level_tool_ids

    original_steps = input_payload.get("multi_agent_steps")
    normalized_steps = normalized.get("multi_agent_steps")
    if isinstance(original_steps, list) and isinstance(normalized_steps, list):
        for original_step, normalized_step in zip(original_steps, normalized_steps):
            if not isinstance(original_step, dict) or not isinstance(normalized_step, dict):
                continue
            step_tools_present, step_tool_ids = _explicit_mcp_tool_scope(original_step)
            if redact_public:
                for key in _MCP_TOOL_ID_KEYS:
                    normalized_step.pop(key, None)
            if step_tools_present:
                normalized_step["mcp_tool_ids"] = step_tool_ids
    return normalized


async def _authorize_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
    skill_resolver,
) -> dict[str, Any]:
    """Apply shared distribution and MCP admission to one Skill resolver."""

    context = CapabilityAccessContext(
        tenant_id=tenant_id,
        department_id=str(principal_department_id or ""),
        roles=normalize_roles(principal_roles or []),
        is_admin=bool(is_admin),
        permissions=[str(item) for item in permissions or [] if str(item)],
    )
    try:
        skill = await skill_resolver(
            conn,
            tenant_id=tenant_id,
            agent_id=agent_id,
            skill_id=skill_id,
        )
    except RepositoryNotFoundError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc
    except RepositoryConflictError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc

    try:
        skill_distribution = await get_capability_distribution_row(
            conn,
            tenant_id=tenant_id,
            capability_kind="skill",
            capability_id=skill_id,
        )
    except RepositoryConflictError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc
    if is_capability_distribution_archived(skill_distribution):
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        )
    skill_decision = resolve_capability_access(
        context,
        CapabilityDistributionSubject(
            capability_kind="skill",
            capability_id=skill_id,
            lifecycle_status=str(skill.get("skill_status") or "disabled"),
            distribution=skill_distribution,
        ),
        intent="use",
    )
    if not skill_decision.usable:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
            decision=skill_decision,
        )

    try:
        tool_ids = run_mcp_tool_ids_for_skill(skill, normalized_input)
    except RepositoryAuthorizationError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc
    for tool_id in tool_ids:
        tool = await get_mcp_tool_registry_entry(
            conn,
            tenant_id=tenant_id,
            tool_id=tool_id,
        )
        if tool is None:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            )
        server_id = str(tool.get("server_id") or "").strip()
        if not server_id:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            )
        try:
            server_distribution = await get_capability_distribution_row(
                conn,
                tenant_id=tenant_id,
                capability_kind="mcp_server",
                capability_id=server_id,
            )
        except RepositoryConflictError as exc:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            ) from exc
        tool_lifecycle_status = (
            "active"
            if str(tool.get("effective_status") or "disabled") == "active"
            and str(tool.get("server_status") or "disabled") == "active"
            and bool(tool.get("visible_to_user", True))
            else "disabled"
        )
        tool_decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="mcp_tool",
                capability_id=tool_id,
                lifecycle_status=tool_lifecycle_status,
                distribution=server_distribution,
                inherited_distribution_source=f"mcp_server:{server_id}",
            ),
            intent="use",
        )
        if not tool_decision.usable:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
                decision=tool_decision,
            )
    return skill


async def authorize_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> dict[str, Any]:
    """Authorize a fixed Agent/default Skill and its explicit MCP tools."""

    return await _authorize_run_capabilities(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        normalized_input=normalized_input,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
        skill_resolver=resolve_agent_skill,
    )


async def authorize_selected_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    expected_version: str,
    rollout_key: str,
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> dict[str, Any]:
    """Authorize an ordinary selected Skill and validate its optimistic hash lock."""

    skill = await _authorize_run_capabilities(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        normalized_input=normalized_input,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
        skill_resolver=resolve_selected_skill,
    )
    release_decision = resolve_rollout_skill_decision(
        skill,
        tenant_id=tenant_id,
        skill_id=skill_id,
        rollout_key=rollout_key,
    )
    selected_version = str(release_decision.selected_version or "")
    if release_decision.policy_active:
        exact_version = await get_effective_skill_version_for_policy(
            conn,
            skill_id=skill_id,
            version=selected_version,
        )
        if exact_version is None or not is_user_runnable_status(exact_version.get("status")):
            raise _capability_not_authorized()
        content_hash = str(exact_version.get("content_hash") or "")
        materialized_version = str(exact_version.get("version") or "")
    else:
        materialized_version = str(skill.get("skill_version") or "")
        content_hash = str(skill.get("skill_content_hash") or materialized_version)
    if not materialized_version or materialized_version != selected_version or content_hash != materialized_version:
        raise _capability_not_authorized()
    if expected_version != selected_version:
        raise RepositoryConflictError("skill_selection_stale")
    return {**skill, "skill_version": selected_version, "skill_content_hash": content_hash}


async def authorize_replay_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> dict[str, Any]:
    """Reauthorize current access while preserving an exact historical Skill pin."""

    pinned_mcp_tool_ids = pinned_replay_mcp_tool_ids(
        skill_id=skill_id,
        pinned_version=pinned_version,
        pinned_executor_type=pinned_executor_type,
        skill_manifests=skill_manifests,
    )
    replay_input = dict(normalized_input)
    if pinned_mcp_tool_ids:
        replay_input["mcp_tool_ids"] = pinned_mcp_tool_ids
    skill = await _authorize_run_capabilities(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        normalized_input=replay_input,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
        skill_resolver=resolve_selected_skill,
    )
    await validate_replay_skill_manifests(
        conn,
        skill_id=skill_id,
        pinned_version=pinned_version,
        pinned_executor_type=pinned_executor_type,
        skill_manifests=skill_manifests,
    )
    return skill


def pinned_replay_mcp_tool_ids(
    *,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
) -> list[str]:
    """Extract the historical MCP authorization set without consulting mutable release state."""

    if pinned_executor_type not in DEFAULT_RUN_EXECUTOR_TYPES or not pinned_version:
        raise _capability_not_authorized()
    primary = next(
        (
            manifest
            for manifest in skill_manifests
            if str(manifest.get("skill_id") or "") == skill_id
            and str(manifest.get("version") or manifest.get("skill_version") or "") == pinned_version
        ),
        None,
    )
    if primary is None:
        raise _capability_not_authorized()
    raw_mcp_tool_ids = primary.get("mcp_tool_ids")
    if not isinstance(raw_mcp_tool_ids, list) or any(
        not isinstance(item, str) or not item for item in raw_mcp_tool_ids
    ):
        raise _capability_not_authorized()
    pinned_mcp_tool_ids = list(dict.fromkeys(raw_mcp_tool_ids))
    if (
        skill_id == _mcp_repository.TRUSTED_BUILTIN_MCP_TOOL_ID
        and _mcp_repository.TRUSTED_BUILTIN_MCP_TOOL_ID not in pinned_mcp_tool_ids
    ):
        raise _capability_not_authorized()
    return pinned_mcp_tool_ids


def require_replay_source_identity(
    *,
    pinned_version: str,
    pinned_executor_type: str,
    release_decision: dict[str, Any],
    skill_manifests: list[dict[str, Any]],
) -> None:
    """Fail closed when a source run lacks the immutable replay contract."""

    if (
        not pinned_version
        or pinned_executor_type not in DEFAULT_RUN_EXECUTOR_TYPES
        or not release_decision
        or not skill_manifests
    ):
        raise _capability_not_authorized()
