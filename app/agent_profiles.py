"""Tenant-scoped immutable Agent Profile revision authority.

This module deliberately owns the complete profile lifecycle: draft save,
publish, ordinary-user market projection, and exact run admission.  Routes and
Chat only translate HTTP/transport concerns; they do not duplicate resolution
or authorization policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin
from app.control_plane_contracts import standard_trace_id
from app.model_catalog import resolve_model_selection
from app.models import (
    AgentProfileAdminProjection,
    AgentProfileDraftRequest,
    AgentProfilePublicProjection,
    ChatStreamRequest,
    SelectedAgentProfileRequest,
    SelectedSkillRequest,
)
from app.settings import get_settings


@dataclass(frozen=True)
class AgentProfileAdmission:
    """Fully reauthorized immutable revision admitted to one Chat run."""

    agent_id: str
    revision: int
    content_hash: str
    skill: dict[str, Any]
    model: dict[str, str]
    mcp_tool_ids: tuple[str, ...]
    private_execution_input: dict[str, Any]


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _mcp_tool_ids(row: dict[str, Any]) -> list[str]:
    raw = row.get("mcp_tool_ids")
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    return list(dict.fromkeys(raw))


def _revision_hash(definition: AgentProfileDraftRequest) -> str:
    """Hash every execution-relevant field under one canonical serialization."""

    material = {
        "name": definition.name,
        "description": definition.description,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_id": definition.selected_skill.skill_id,
        "skill_version": definition.selected_skill.expected_version,
        "mcp_tool_ids": definition.mcp_tool_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _draft_from_row(row: dict[str, Any]) -> AgentProfileDraftRequest:
    return AgentProfileDraftRequest(
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        instructions=str(row["instructions"]),
        model_id=str(row["model_id"]),
        selected_skill=SelectedSkillRequest(
            skill_id=str(row["skill_id"]),
            expected_version=str(row["skill_version"]),
        ),
        mcp_tool_ids=_mcp_tool_ids(row),
        expected_draft_revision=int(row["revision"]),
    )


def _admin_projection(row: dict[str, Any]) -> AgentProfileAdminProjection:
    return AgentProfileAdminProjection(
        agent_id=str(row["agent_id"]),
        revision=int(row["revision"]),
        status=str(row["status"]),
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        instructions=str(row["instructions"]),
        model_id=str(row["model_id"]),
        selected_skill=SelectedSkillRequest(
            skill_id=str(row["skill_id"]),
            expected_version=str(row["skill_version"]),
        ),
        mcp_tool_ids=_mcp_tool_ids(row),
        content_hash=str(row["content_hash"]),
        created_at=row.get("created_at"),
        published_at=row.get("published_at"),
    )


def profile_public_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Return the only market payload visible to ordinary authenticated users."""

    return {
        "agent_id": str(row["agent_id"]),
        "expected_revision": int(row["revision"]),
        "name": str(row["name"]),
        "description": str(row.get("description") or ""),
    }


def reject_profile_selector_conflicts(request: ChatStreamRequest) -> None:
    """Profiles own model/Skill/MCP selectors; client overrides fail closed."""

    if request.selected_agent_profile is None:
        return
    agent_options = request.agent_options if isinstance(request.agent_options, dict) else {}
    client_model_selected = any(key in {"model", "model_id"} for key in agent_options)
    if (
        request.skill_id is not None
        or request.selected_skill is not None
        or request.selected_mcp_tool_ids is not None
        or client_model_selected
    ):
        raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")


async def _validate_definition(
    conn,
    *,
    principal: AuthPrincipal,
    agent_id: str,
    definition: AgentProfileDraftRequest,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Revalidate current model, Skill and MCP authorization for one revision."""

    try:
        model = resolve_model_selection(definition.model_id, get_settings())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_profile_model_not_available") from exc
    try:
        skill = await repositories.authorize_selected_run_capabilities(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            skill_id=definition.selected_skill.skill_id,
            expected_version=definition.selected_skill.expected_version,
            rollout_key=principal.user_id,
            normalized_input={"mcp_tool_ids": list(definition.mcp_tool_ids)},
            principal_department_id=principal.department_id,
            principal_roles=principal.roles,
            is_admin=is_ai_admin(principal),
            permissions=principal.permissions,
        )
        await repositories.authorize_selected_chat_mcp_tools(
            conn,
            tenant_id=principal.tenant_id,
            tool_ids=list(definition.mcp_tool_ids),
            principal_department_id=principal.department_id,
            principal_roles=principal.roles,
            is_admin=is_ai_admin(principal),
            permissions=principal.permissions,
        )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale") from exc
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="agent_profile_capability_not_available") from exc
    return skill, model


async def save_draft(
    conn,
    *,
    principal: AuthPrincipal,
    definition: AgentProfileDraftRequest,
    agent_id: str | None,
) -> tuple[AgentProfileAdminProjection, str]:
    """Persist a new immutable draft under a server-owned tenant identity."""

    _require_admin(principal)
    if agent_id is None and definition.expected_draft_revision != 0:
        raise HTTPException(status_code=409, detail="agent_profile_create_revision_invalid")
    if agent_id is not None and definition.expected_draft_revision < 1:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
    resolved_agent_id = agent_id or repositories.new_id("agt")
    await repositories.ensure_user(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        display_name=principal.display_name or principal.user_id,
    )
    await repositories.ensure_agent_profile_identity(
        conn,
        tenant_id=principal.tenant_id,
        agent_id=resolved_agent_id,
        name=definition.name,
        default_skill_id=definition.selected_skill.skill_id,
    )
    row = await repositories.create_agent_profile_revision(
        conn,
        tenant_id=principal.tenant_id,
        agent_id=resolved_agent_id,
        status="draft",
        name=definition.name,
        description=definition.description,
        instructions=definition.instructions,
        model_id=definition.model_id,
        skill_id=definition.selected_skill.skill_id,
        skill_version=definition.selected_skill.expected_version,
        mcp_tool_ids=definition.mcp_tool_ids,
        content_hash=_revision_hash(definition),
        created_by=principal.user_id,
        expected_previous_revision=definition.expected_draft_revision,
    )
    audit_id = await repositories.append_audit_log(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="agent_profile.draft_saved",
        target_type="agent_profile",
        target_id=resolved_agent_id,
        trace_id=standard_trace_id(resolved_agent_id),
        payload_json={"revision": int(row["revision"]), "content_hash": str(row["content_hash"])},
    )
    return _admin_projection(row), audit_id


async def publish_draft(
    conn,
    *,
    principal: AuthPrincipal,
    agent_id: str,
    expected_revision: int,
) -> tuple[AgentProfileAdminProjection, str]:
    """Revalidate and append a published immutable copy of one draft revision."""

    _require_admin(principal)
    draft_row = await repositories.get_agent_profile_revision(
        conn,
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        revision=expected_revision,
        status="draft",
    )
    if draft_row is None:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
    definition = _draft_from_row(draft_row)
    await _validate_definition(
        conn,
        principal=principal,
        agent_id=agent_id,
        definition=definition,
    )
    row = await repositories.create_agent_profile_revision(
        conn,
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        status="published",
        name=definition.name,
        description=definition.description,
        instructions=definition.instructions,
        model_id=definition.model_id,
        skill_id=definition.selected_skill.skill_id,
        skill_version=definition.selected_skill.expected_version,
        mcp_tool_ids=definition.mcp_tool_ids,
        content_hash=str(draft_row["content_hash"]),
        created_by=principal.user_id,
        published_by=principal.user_id,
        expected_previous_revision=expected_revision,
        published_from_revision=expected_revision,
    )
    audit_id = await repositories.append_audit_log(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="agent_profile.published",
        target_type="agent_profile",
        target_id=agent_id,
        trace_id=standard_trace_id(agent_id),
        payload_json={"revision": int(row["revision"]), "content_hash": str(row["content_hash"])},
    )
    return _admin_projection(row), audit_id


async def list_admin_profiles(conn, *, principal: AuthPrincipal) -> list[AgentProfileAdminProjection]:
    """List only latest same-tenant revisions for the admin Builder surface."""

    _require_admin(principal)
    rows = await repositories.list_latest_agent_profile_revisions(
        conn,
        tenant_id=principal.tenant_id,
    )
    return [_admin_projection(row) for row in rows]


async def list_public_profiles(conn, *, principal: AuthPrincipal) -> list[AgentProfilePublicProjection]:
    """Filter published profiles through current caller capability authorization."""

    rows = await repositories.list_latest_agent_profile_revisions(
        conn,
        tenant_id=principal.tenant_id,
        status="published",
    )
    visible: list[AgentProfilePublicProjection] = []
    for row in rows:
        try:
            await _validate_definition(
                conn,
                principal=principal,
                agent_id=str(row["agent_id"]),
                definition=_draft_from_row(row),
            )
        except HTTPException:
            continue
        visible.append(AgentProfilePublicProjection.model_validate(profile_public_projection(row)))
    return visible


async def resolve_profile_for_admission(
    conn,
    *,
    principal: AuthPrincipal,
    selection: SelectedAgentProfileRequest,
) -> AgentProfileAdmission:
    """Resolve and reauthorize one exact published revision for Chat admission."""

    row = await repositories.get_agent_profile_revision(
        conn,
        tenant_id=principal.tenant_id,
        agent_id=selection.agent_id,
        revision=selection.expected_revision,
        status="published",
    )
    if row is None:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
    definition = _draft_from_row(row)
    skill, model = await _validate_definition(
        conn,
        principal=principal,
        agent_id=selection.agent_id,
        definition=definition,
    )
    return AgentProfileAdmission(
        agent_id=selection.agent_id,
        revision=selection.expected_revision,
        content_hash=str(row["content_hash"]),
        skill=skill,
        model=model,
        mcp_tool_ids=tuple(definition.mcp_tool_ids),
        private_execution_input={
            "agent_id": selection.agent_id,
            "revision": selection.expected_revision,
            "content_hash": str(row["content_hash"]),
            "instructions": definition.instructions,
        },
    )
