"""Deep Agent Profile aggregate authority behind route and repository adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, normalize_roles
from app.control_plane_contracts import standard_trace_id
from app.model_catalog import resolve_model_selection
from app.models import (
    AgentConversationIdentity,
    AgentProfileAdminProjection,
    AgentProfileDraftRequest,
    AgentProfilePublicProjection,
    ChatSessionResponse,
    ChatStreamRequest,
    SelectedAgentProfileRequest,
    SelectedSkillRequest,
)
from app.settings import get_settings


_AVATAR_REFS = {"builtin:agent", "builtin:assistant", "builtin:document", "builtin:research"}
_CATEGORIES = {"general", "support", "writing", "research", "operations"}
_VISIBILITIES = {"tenant", "restricted"}


@dataclass(frozen=True)
class AgentProfileAdmission:
    """Fully reauthorized immutable profile revision admitted to one Chat run."""

    agent_id: str
    revision: int
    content_hash: str
    skill: dict[str, Any]
    model: dict[str, str]
    mcp_tool_ids: tuple[str, ...]
    private_execution_input: dict[str, Any]
    public_identity: AgentConversationIdentity


def _safe_avatar_ref(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _AVATAR_REFS else "builtin:agent"


def _safe_category(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _CATEGORIES else "general"


def _safe_visibility(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _VISIBILITIES else "tenant"


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if isinstance(item, str) and item.strip()))


def _mcp_tool_ids(row: dict[str, Any]) -> list[str]:
    raw = row.get("mcp_tool_ids")
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    return list(dict.fromkeys(raw))


def profile_acl_allows(row: dict[str, Any], *, principal: AuthPrincipal) -> bool:
    """Apply the immutable profile ACL before any public projection or use admission."""

    if is_ai_admin(principal):
        return True
    if _safe_visibility(row.get("visibility")) == "tenant":
        return True
    if principal.user_id in _safe_string_list(row.get("allowed_user_ids")):
        return True
    allowed_departments = set(_safe_string_list(row.get("allowed_department_ids")))
    if allowed_departments and principal.department_id not in allowed_departments:
        return False
    allowed_roles = set(normalize_roles(_safe_string_list(row.get("allowed_roles"))))
    if allowed_roles and not allowed_roles.intersection(normalize_roles(principal.roles)):
        return False
    return bool(allowed_departments or allowed_roles)


def profile_public_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Return the only Agent Profile card/detail fields available to ordinary users."""

    return {
        "agent_id": str(row["agent_id"]),
        "expected_revision": int(row["revision"]),
        "name": str(row["name"]),
        "description": str(row.get("description") or ""),
        "avatar_ref": _safe_avatar_ref(row.get("avatar_ref")),
        "category": _safe_category(row.get("category")),
    }


def conversation_identity_projection(row: dict[str, Any]) -> AgentConversationIdentity:
    """Project safe immutable identity retained by a recovered Agent Conversation."""

    public = profile_public_projection(row)
    return AgentConversationIdentity(
        agent_id=public["agent_id"],
        revision=public["expected_revision"],
        name=public["name"],
        description=public["description"],
        avatar_ref=public["avatar_ref"],
        category=public["category"],
    )


def _revision_hash(definition: AgentProfileDraftRequest) -> str:
    """Hash every execution and public-lifecycle field under a canonical serialization."""

    material = {
        "name": definition.name,
        "description": definition.description,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_id": definition.selected_skill.skill_id,
        "skill_version": definition.selected_skill.expected_version,
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "category": definition.category,
        "visibility": definition.visibility,
        "allowed_department_ids": definition.allowed_department_ids,
        "allowed_roles": definition.allowed_roles,
        "allowed_user_ids": definition.allowed_user_ids,
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
        avatar_ref=_safe_avatar_ref(row.get("avatar_ref")),
        category=_safe_category(row.get("category")),
        visibility=_safe_visibility(row.get("visibility")),
        allowed_department_ids=_safe_string_list(row.get("allowed_department_ids")),
        allowed_roles=_safe_string_list(row.get("allowed_roles")),
        allowed_user_ids=_safe_string_list(row.get("allowed_user_ids")),
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
        avatar_ref=_safe_avatar_ref(row.get("avatar_ref")),
        category=_safe_category(row.get("category")),
        visibility=_safe_visibility(row.get("visibility")),
        allowed_department_ids=_safe_string_list(row.get("allowed_department_ids")),
        allowed_roles=_safe_string_list(row.get("allowed_roles")),
        allowed_user_ids=_safe_string_list(row.get("allowed_user_ids")),
        content_hash=str(row["content_hash"]),
        created_at=row.get("created_at"),
        published_at=row.get("published_at"),
    )


class AgentProfileAuthority:
    """Own lifecycle, public discovery, admission, and safe conversation recovery."""

    def _require_admin(self, principal: AuthPrincipal) -> None:
        if not is_ai_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")

    async def _validate_definition(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        definition: AgentProfileDraftRequest,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Revalidate current model, Skill, and MCP authorization for a definition."""

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
        self,
        conn,
        *,
        principal: AuthPrincipal,
        definition: AgentProfileDraftRequest,
        agent_id: str | None,
    ) -> tuple[AgentProfileAdminProjection, str]:
        """Append a draft revision and advance the one authoritative profile aggregate."""

        self._require_admin(principal)
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
            avatar_ref=definition.avatar_ref,
            category=definition.category,
            visibility=definition.visibility,
            allowed_department_ids=definition.allowed_department_ids,
            allowed_roles=definition.allowed_roles,
            allowed_user_ids=definition.allowed_user_ids,
            content_hash=_revision_hash(definition),
            created_by=principal.user_id,
            expected_previous_revision=definition.expected_draft_revision,
        )
        await repositories.record_agent_profile_draft(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=resolved_agent_id,
            revision=int(row["revision"]),
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
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        expected_revision: int,
    ) -> tuple[AgentProfileAdminProjection, str]:
        """Publish a revalidated immutable copy and move the aggregate publication pointer."""

        self._require_admin(principal)
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
        await self._validate_definition(conn, principal=principal, agent_id=agent_id, definition=definition)
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
            avatar_ref=definition.avatar_ref,
            category=definition.category,
            visibility=definition.visibility,
            allowed_department_ids=definition.allowed_department_ids,
            allowed_roles=definition.allowed_roles,
            allowed_user_ids=definition.allowed_user_ids,
            content_hash=str(draft_row["content_hash"]),
            created_by=principal.user_id,
            published_by=principal.user_id,
            expected_previous_revision=expected_revision,
            published_from_revision=expected_revision,
        )
        await repositories.record_agent_profile_publication(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=int(row["revision"]),
            content_hash=str(row["content_hash"]),
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

    async def unpublish(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        expected_revision: int,
    ) -> tuple[AgentProfileAdminProjection, str]:
        """Withdraw the current publication while preserving immutable version history."""

        self._require_admin(principal)
        aggregate = await repositories.get_agent_profile_aggregate(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            for_update=True,
        )
        if (
            aggregate is None
            or str(aggregate.get("lifecycle_status") or "") != "published"
            or int(aggregate.get("published_revision") or 0) != expected_revision
        ):
            raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
        published_row = await repositories.get_agent_profile_revision(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=expected_revision,
            status="published",
        )
        if published_row is None:
            raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
        definition = _draft_from_row(published_row)
        row = await repositories.create_agent_profile_revision(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            status="withdrawn",
            name=definition.name,
            description=definition.description,
            instructions=definition.instructions,
            model_id=definition.model_id,
            skill_id=definition.selected_skill.skill_id,
            skill_version=definition.selected_skill.expected_version,
            mcp_tool_ids=definition.mcp_tool_ids,
            avatar_ref=definition.avatar_ref,
            category=definition.category,
            visibility=definition.visibility,
            allowed_department_ids=definition.allowed_department_ids,
            allowed_roles=definition.allowed_roles,
            allowed_user_ids=definition.allowed_user_ids,
            content_hash=str(published_row["content_hash"]),
            created_by=principal.user_id,
            expected_previous_revision=int(aggregate["latest_revision"]),
            withdrawn_from_revision=expected_revision,
        )
        await repositories.record_agent_profile_withdrawal(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=int(row["revision"]),
        )
        audit_id = await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="agent_profile.unpublished",
            target_type="agent_profile",
            target_id=agent_id,
            trace_id=standard_trace_id(agent_id),
            payload_json={"revision": expected_revision, "withdrawn_revision": int(row["revision"])},
        )
        return _admin_projection(row), audit_id

    async def validate_draft(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        definition: AgentProfileDraftRequest,
        agent_id: str | None,
    ) -> str:
        """Validate unsaved or saved drafts without creating a revision or execution run."""

        self._require_admin(principal)
        validation_agent_id = agent_id
        if validation_agent_id is None:
            validation_agent_id = await repositories.get_tenant_profile_validation_agent(
                conn,
                tenant_id=principal.tenant_id,
            )
        if not validation_agent_id:
            raise HTTPException(status_code=409, detail="agent_profile_validation_unavailable")
        await self._validate_definition(
            conn,
            principal=principal,
            agent_id=validation_agent_id,
            definition=definition,
        )
        return await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="agent_profile.draft_validated",
            target_type="agent_profile",
            target_id=agent_id or "draft-preview",
            trace_id=standard_trace_id(agent_id or "draft-preview"),
            payload_json={"content_hash": _revision_hash(definition)},
        )

    async def list_admin(self, conn, *, principal: AuthPrincipal) -> list[AgentProfileAdminProjection]:
        """List latest same-tenant revisions for the Builder without public redaction."""

        self._require_admin(principal)
        rows = await repositories.list_latest_agent_profile_revisions(conn, tenant_id=principal.tenant_id)
        return [_admin_projection(row) for row in rows]

    async def list_history(self, conn, *, principal: AuthPrincipal, agent_id: str) -> list[AgentProfileAdminProjection]:
        """Return the immutable same-tenant lifecycle history for one profile identity."""

        self._require_admin(principal)
        rows = await repositories.list_agent_profile_revision_history(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
        return [_admin_projection(row) for row in rows]

    async def list_public(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        query: str | None = None,
        category: str | None = None,
    ) -> list[AgentProfilePublicProjection]:
        """Return only current published profiles visible and usable by this principal."""

        rows = await repositories.list_current_published_agent_profiles(
            conn,
            tenant_id=principal.tenant_id,
            query=query,
            category=category,
        )
        visible: list[AgentProfilePublicProjection] = []
        for row in rows:
            try:
                await self._authorize_public_row(conn, principal=principal, row=row)
            except HTTPException:
                continue
            visible.append(AgentProfilePublicProjection.model_validate(profile_public_projection(row)))
        return visible

    async def get_public(self, conn, *, principal: AuthPrincipal, agent_id: str) -> AgentProfilePublicProjection:
        """Return public detail only when it passes the same list authorization path."""

        row = await repositories.get_current_published_agent_profile(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="agent_profile_not_found")
        try:
            await self._authorize_public_row(conn, principal=principal, row=row)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="agent_profile_not_found") from exc
        return AgentProfilePublicProjection.model_validate(profile_public_projection(row))

    async def _authorize_public_row(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        row: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        if not profile_acl_allows(row, principal=principal):
            raise HTTPException(status_code=403, detail="agent_profile_not_authorized")
        return await self._validate_definition(
            conn,
            principal=principal,
            agent_id=str(row["agent_id"]),
            definition=_draft_from_row(row),
        )

    async def resolve_for_admission(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        selection: SelectedAgentProfileRequest,
    ) -> AgentProfileAdmission:
        """Lock and reauthorize exactly the current published revision for one submission."""

        row = await repositories.get_current_published_agent_profile(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=selection.agent_id,
            expected_revision=selection.expected_revision,
            for_update=True,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="agent_profile_not_available")
        skill, model = await self._authorize_public_row(conn, principal=principal, row=row)
        content_hash = str(row["content_hash"])
        return AgentProfileAdmission(
            agent_id=selection.agent_id,
            revision=selection.expected_revision,
            content_hash=content_hash,
            skill=skill,
            model=model,
            mcp_tool_ids=tuple(_mcp_tool_ids(row)),
            private_execution_input={
                "agent_id": selection.agent_id,
                "revision": selection.expected_revision,
                "content_hash": content_hash,
                "instructions": str(row["instructions"]),
            },
            public_identity=conversation_identity_projection(row),
        )

    async def create_conversation(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        workspace_id: str,
        selection: SelectedAgentProfileRequest,
        title: str,
    ) -> ChatSessionResponse:
        """Atomically bind a new conversation to one current published profile revision/hash."""

        admission = await self.resolve_for_admission(conn, principal=principal, selection=selection)
        await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
        await repositories.ensure_user(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            display_name=principal.display_name or principal.user_id,
        )
        session_id = await repositories.create_session(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            user_id=principal.user_id,
            agent_id=admission.agent_id,
            title=title or admission.public_identity.name,
            admitted_agent_profile_revision=admission.revision,
            admitted_agent_profile_hash=admission.content_hash,
        )
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="agent_conversation.created",
            target_type="agent_profile",
            target_id=admission.agent_id,
            trace_id=standard_trace_id(session_id),
            payload_json={"revision": admission.revision, "session_id": session_id},
        )
        return ChatSessionResponse(
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=admission.agent_id,
            title=title or admission.public_identity.name,
            agent_conversation=admission.public_identity,
        )


def reject_profile_selector_conflicts(request: ChatStreamRequest, *, active: bool | None = None) -> None:
    """Fail closed whenever a profile-bound submission carries client-owned execution overrides."""

    if active is None:
        active = request.selected_agent_profile is not None
    if not active:
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
