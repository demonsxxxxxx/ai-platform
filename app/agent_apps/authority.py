"""Deep Agent Profile aggregate authority behind route and repository adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, normalize_roles
from app.chat_session_projection import session_response
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
_PRESENCE_AWARE_PROFILE_FIELDS = (
    "welcome_message",
    "starter_prompts",
    "capability_summary",
    "recommended_tasks",
    "supported_input_types",
    "supported_file_types",
    "expected_outputs",
    "permissions_and_data_access_notice",
    "avatar_ref",
    "avatar_asset_id",
    "category",
    "visibility",
    "allowed_department_ids",
    "allowed_roles",
    "allowed_user_ids",
)
_PROFILE_TRANSPORT_SELECTOR_PATHS = frozenset(
    {
        "$.agent_options",
        "$.agent_options.model",
        "$.agent_options.model_id",
        "$.disabled_mcp_tools",
        "$.disabled_skills",
        "$.enabled_skills",
        "$.selected_agent_profile",
        "$.selected_mcp_tool_ids",
    }
)
_PROFILE_TRANSPORT_AGENT_OPTION_KEYS = frozenset(
    {"enable_thinking", "model", "model_id"}
)


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
    return candidate if candidate in _VISIBILITIES else "restricted"


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
        "welcome_message": str(row.get("welcome_message") or ""),
        "starter_prompts": _safe_string_list(row.get("starter_prompts")),
        "capability_summary": str(row.get("capability_summary") or ""),
        "recommended_tasks": _safe_string_list(row.get("recommended_tasks")),
        "supported_input_types": [
            value
            for value in _safe_string_list(row.get("supported_input_types"))
            if value in {"text", "file"}
        ]
        or ["text"],
        "supported_file_types": _safe_string_list(row.get("supported_file_types")),
        "expected_outputs": _safe_string_list(row.get("expected_outputs")),
        "permissions_and_data_access_notice": str(
            row.get("permissions_and_data_access_notice") or ""
        ),
        "avatar_ref": _safe_avatar_ref(row.get("avatar_ref")),
        "category": _safe_category(row.get("category")),
        "published_at": row.get("published_at"),
    }


def conversation_identity_projection(row: dict[str, Any]) -> AgentConversationIdentity:
    """Project safe immutable identity retained by a recovered Agent Conversation."""

    public = profile_public_projection(row)
    return AgentConversationIdentity(
        agent_id=public["agent_id"],
        revision=public["expected_revision"],
        name=public["name"],
        description=public["description"],
        welcome_message=public["welcome_message"],
        starter_prompts=public["starter_prompts"],
        capability_summary=public["capability_summary"],
        recommended_tasks=public["recommended_tasks"],
        supported_input_types=public["supported_input_types"],
        supported_file_types=public["supported_file_types"],
        expected_outputs=public["expected_outputs"],
        permissions_and_data_access_notice=public["permissions_and_data_access_notice"],
        avatar_ref=public["avatar_ref"],
        category=public["category"],
        published_at=public["published_at"],
    )


def _revision_hash(definition: AgentProfileDraftRequest) -> str:
    """Hash every execution and public-lifecycle field under a canonical serialization."""

    material = {
        "name": definition.name,
        "description": definition.description,
        "welcome_message": definition.welcome_message,
        "starter_prompts": definition.starter_prompts,
        "capability_summary": definition.capability_summary,
        "recommended_tasks": definition.recommended_tasks,
        "supported_input_types": definition.supported_input_types,
        "supported_file_types": definition.supported_file_types,
        "expected_outputs": definition.expected_outputs,
        "permissions_and_data_access_notice": definition.permissions_and_data_access_notice,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_id": definition.selected_skill.skill_id,
        "skill_version": definition.selected_skill.expected_version,
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "avatar_asset_id": definition.avatar_asset_id,
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
        welcome_message=str(row.get("welcome_message") or ""),
        starter_prompts=_safe_string_list(row.get("starter_prompts")),
        capability_summary=str(row.get("capability_summary") or ""),
        recommended_tasks=_safe_string_list(row.get("recommended_tasks")),
        supported_input_types=[
            value
            for value in _safe_string_list(row.get("supported_input_types"))
            if value in {"text", "file"}
        ]
        or ["text"],
        supported_file_types=_safe_string_list(row.get("supported_file_types")),
        expected_outputs=_safe_string_list(row.get("expected_outputs")),
        permissions_and_data_access_notice=str(
            row.get("permissions_and_data_access_notice") or ""
        ),
        instructions=str(row["instructions"]),
        model_id=str(row["model_id"]),
        selected_skill=SelectedSkillRequest(
            skill_id=str(row["skill_id"]),
            expected_version=str(row["skill_version"]),
        ),
        mcp_tool_ids=_mcp_tool_ids(row),
        avatar_ref=_safe_avatar_ref(row.get("avatar_ref")),
        avatar_asset_id=(str(row.get("avatar_asset_id")) if row.get("avatar_asset_id") else None),
        category=_safe_category(row.get("category")),
        visibility=_safe_visibility(row.get("visibility")),
        allowed_department_ids=_safe_string_list(row.get("allowed_department_ids")),
        allowed_roles=_safe_string_list(row.get("allowed_roles")),
        allowed_user_ids=_safe_string_list(row.get("allowed_user_ids")),
        expected_draft_revision=int(row["revision"]),
    )


def _merge_omitted_profile_fields(
    definition: AgentProfileDraftRequest,
    *,
    prior_row: dict[str, Any],
) -> AgentProfileDraftRequest:
    """Preserve legacy-client metadata omissions while honoring explicit empty ACLs."""

    prior = _draft_from_row(prior_row)
    updates = {
        field: getattr(prior, field)
        for field in _PRESENCE_AWARE_PROFILE_FIELDS
        if field not in definition.model_fields_set
    }
    return definition.model_copy(update=updates) if updates else definition


def _current_acl_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project current publication ACL aliases over an immutable execution revision."""

    if "current_visibility" not in row:
        return row
    return {
        **row,
        "visibility": row.get("current_visibility"),
        "allowed_department_ids": row.get("current_allowed_department_ids"),
        "allowed_roles": row.get("current_allowed_roles"),
        "allowed_user_ids": row.get("current_allowed_user_ids"),
    }


def _admin_projection(row: dict[str, Any]) -> AgentProfileAdminProjection:
    return AgentProfileAdminProjection(
        agent_id=str(row["agent_id"]),
        revision=int(row["revision"]),
        status=str(row["status"]),
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        welcome_message=str(row.get("welcome_message") or ""),
        starter_prompts=_safe_string_list(row.get("starter_prompts")),
        capability_summary=str(row.get("capability_summary") or ""),
        recommended_tasks=_safe_string_list(row.get("recommended_tasks")),
        supported_input_types=[
            value
            for value in _safe_string_list(row.get("supported_input_types"))
            if value in {"text", "file"}
        ]
        or ["text"],
        supported_file_types=_safe_string_list(row.get("supported_file_types")),
        expected_outputs=_safe_string_list(row.get("expected_outputs")),
        permissions_and_data_access_notice=str(
            row.get("permissions_and_data_access_notice") or ""
        ),
        instructions=str(row["instructions"]),
        model_id=str(row["model_id"]),
        selected_skill=SelectedSkillRequest(
            skill_id=str(row["skill_id"]),
            expected_version=str(row["skill_version"]),
        ),
        mcp_tool_ids=_mcp_tool_ids(row),
        avatar_ref=_safe_avatar_ref(row.get("avatar_ref")),
        avatar_asset_id=(str(row.get("avatar_asset_id")) if row.get("avatar_asset_id") else None),
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

    async def _ensure_principal_user(self, conn, *, principal: AuthPrincipal) -> None:
        """Provision an unseen principal and reject a conflicting tenant identity."""

        try:
            await repositories.ensure_submission_principal(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name or principal.user_id,
            )
        except repositories.RepositoryAuthorizationError as exc:
            raise HTTPException(status_code=403, detail="principal_not_authorized") from exc

    async def _validate_definition(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        definition: AgentProfileDraftRequest,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Revalidate current model, Skill, and MCP authorization for a definition."""

        if definition.avatar_asset_id:
            avatar_asset = await repositories.get_file(
                conn,
                tenant_id=principal.tenant_id,
                file_id=definition.avatar_asset_id,
            )
            if (
                avatar_asset is None
                or str(avatar_asset.get("user_id") or "") != principal.user_id
                or not str(avatar_asset.get("content_type") or "").startswith("image/")
                or int(avatar_asset.get("size_bytes") or 0) <= 0
                or int(avatar_asset.get("size_bytes") or 0) > 5 * 1024 * 1024
            ):
                raise HTTPException(status_code=400, detail="agent_profile_avatar_asset_invalid")
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
        await self._ensure_principal_user(conn, principal=principal)
        await repositories.acquire_agent_profile_lifecycle_lock(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=resolved_agent_id,
        )
        if agent_id is not None:
            prior_row = await repositories.get_agent_profile_revision(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=resolved_agent_id,
                revision=definition.expected_draft_revision,
            )
            if prior_row is None:
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            definition = _merge_omitted_profile_fields(definition, prior_row=prior_row)
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
            welcome_message=definition.welcome_message,
            starter_prompts=definition.starter_prompts,
            capability_summary=definition.capability_summary,
            recommended_tasks=definition.recommended_tasks,
            supported_input_types=definition.supported_input_types,
            supported_file_types=definition.supported_file_types,
            expected_outputs=definition.expected_outputs,
            permissions_and_data_access_notice=definition.permissions_and_data_access_notice,
            avatar_ref=definition.avatar_ref,
            avatar_asset_id=definition.avatar_asset_id,
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
        await self._ensure_principal_user(conn, principal=principal)
        await repositories.acquire_agent_profile_lifecycle_lock(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
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
            welcome_message=definition.welcome_message,
            starter_prompts=definition.starter_prompts,
            capability_summary=definition.capability_summary,
            recommended_tasks=definition.recommended_tasks,
            supported_input_types=definition.supported_input_types,
            supported_file_types=definition.supported_file_types,
            expected_outputs=definition.expected_outputs,
            permissions_and_data_access_notice=definition.permissions_and_data_access_notice,
            avatar_ref=definition.avatar_ref,
            avatar_asset_id=definition.avatar_asset_id,
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
        await self._ensure_principal_user(conn, principal=principal)
        await repositories.acquire_agent_profile_lifecycle_lock(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
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
            welcome_message=definition.welcome_message,
            starter_prompts=definition.starter_prompts,
            capability_summary=definition.capability_summary,
            recommended_tasks=definition.recommended_tasks,
            supported_input_types=definition.supported_input_types,
            supported_file_types=definition.supported_file_types,
            expected_outputs=definition.expected_outputs,
            permissions_and_data_access_notice=definition.permissions_and_data_access_notice,
            avatar_ref=definition.avatar_ref,
            avatar_asset_id=definition.avatar_asset_id,
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
        await self._ensure_principal_user(conn, principal=principal)
        validation_agent_id = agent_id
        if validation_agent_id is not None:
            if definition.expected_draft_revision < 1:
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            await repositories.acquire_agent_profile_lifecycle_lock(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=validation_agent_id,
            )
            aggregate = await repositories.get_agent_profile_aggregate(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=validation_agent_id,
                for_update=True,
            )
            if (
                aggregate is None
                or int(aggregate.get("latest_revision") or 0)
                != definition.expected_draft_revision
            ):
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            prior_row = await repositories.get_agent_profile_revision(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=validation_agent_id,
                revision=definition.expected_draft_revision,
                status="draft",
            )
            if prior_row is None:
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            definition = _merge_omitted_profile_fields(definition, prior_row=prior_row)
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
        submitted_request: ChatStreamRequest | None = None,
        query_agent_id: str | None = None,
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
        admission = await self._admission_from_row(conn, principal=principal, row=row)
        if submitted_request is not None:
            self.reject_profile_selector_conflicts(
                submitted_request,
                active=True,
                query_agent_id=query_agent_id,
                admission=admission,
                allow_default_query_agent=True,
            )
        return admission

    async def resolve_bound_for_submission(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        revision: int,
        content_hash: str,
        submitted_request: ChatStreamRequest | None = None,
        query_agent_id: str | None = None,
    ) -> AgentProfileAdmission:
        """Reauthorize a conversation's immutable publication while its Agent is live."""

        row = await repositories.get_bound_published_agent_profile(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=revision,
            content_hash=content_hash,
            for_update=True,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="agent_profile_not_available")
        admission = await self._admission_from_row(conn, principal=principal, row=row)
        if submitted_request is not None:
            self.reject_profile_selector_conflicts(
                submitted_request,
                active=True,
                query_agent_id=query_agent_id,
                admission=admission,
            )
        return admission

    async def _admission_from_row(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        row: dict[str, Any],
    ) -> AgentProfileAdmission:
        """Reauthorize current capabilities and build private/public admission views."""

        if not profile_acl_allows(_current_acl_row(row), principal=principal):
            raise HTTPException(status_code=403, detail="agent_profile_not_authorized")
        skill, model = await self._validate_definition(
            conn,
            principal=principal,
            agent_id=str(row["agent_id"]),
            definition=_draft_from_row(row),
        )
        agent_id = str(row["agent_id"])
        revision = int(row["revision"])
        content_hash = str(row["content_hash"])
        return AgentProfileAdmission(
            agent_id=agent_id,
            revision=revision,
            content_hash=content_hash,
            skill=skill,
            model=model,
            mcp_tool_ids=tuple(_mcp_tool_ids(row)),
            private_execution_input={
                "agent_id": agent_id,
                "revision": revision,
                "content_hash": content_hash,
                "instructions": str(row["instructions"]),
                "required_skill_id": str(row["skill_id"]),
                "required_skill_version": str(row["skill_version"]),
            },
            public_identity=conversation_identity_projection(row),
        )

    async def reauthorize_pinned_run_for_replay(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        run_id: str,
    ) -> None:
        """Reauthorize one persisted profile run before copy, retry, or resume side effects."""

        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise repositories.RepositoryNotFoundError("run_not_found")
        input_json = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
        snapshot = repositories.copied_run_execution_snapshot(input_json)
        revision, content_hash = repositories.admitted_agent_profile_pins_for_copy(run, snapshot)
        if revision is None:
            return
        admission = await self.resolve_bound_for_submission(
            conn,
            principal=principal,
            agent_id=str(run.get("agent_id") or ""),
            revision=revision,
            content_hash=str(content_hash or ""),
        )
        profile_snapshot = snapshot.get("agent_profile")
        execution_input = snapshot.get("input") if isinstance(snapshot.get("input"), dict) else {}
        expected_profile_snapshot = dict(admission.private_execution_input)
        snapshot_skill_version = str(snapshot.get("skill_version") or "")
        authority_skill_id = str(admission.skill.get("skill_id") or "")
        governed_profile_snapshot = isinstance(profile_snapshot, dict) and (
            "required_skill_id" in profile_snapshot or "required_skill_version" in profile_snapshot
        )
        governed_mcp_tool_ids: tuple[str, ...] | None = None
        if governed_profile_snapshot:
            expected_profile_snapshot.update(
                {
                    "required_skill_id": authority_skill_id,
                    "required_skill_version": snapshot_skill_version,
                }
            )
            primary_manifest = next(
                (
                    manifest
                    for manifest in snapshot.get("skill_manifests", [])
                    if isinstance(manifest, dict)
                    and str(manifest.get("skill_id") or "") == authority_skill_id
                ),
                None,
            )
            manifest_skill_version = (
                str(primary_manifest.get("content_hash") or primary_manifest.get("version") or "")
                if isinstance(primary_manifest, dict)
                else ""
            )
            release_decision = snapshot.get("release_decision")
            release_skill_version = (
                str(release_decision.get("selected_version") or "")
                if isinstance(release_decision, dict)
                else ""
            )
            try:
                repositories.require_replay_source_identity(
                    pinned_version=snapshot_skill_version,
                    pinned_executor_type=str(snapshot.get("executor_type") or ""),
                    release_decision=release_decision if isinstance(release_decision, dict) else {},
                    skill_manifests=[
                        dict(item)
                        for item in snapshot.get("skill_manifests", [])
                        if isinstance(item, dict)
                    ],
                )
                governed_mcp_tool_ids = tuple(
                    await repositories.validate_replay_skill_manifests(
                        conn,
                        skill_id=authority_skill_id,
                        pinned_version=snapshot_skill_version,
                        pinned_executor_type=str(snapshot.get("executor_type") or ""),
                        skill_manifests=[
                            dict(item)
                            for item in snapshot.get("skill_manifests", [])
                            if isinstance(item, dict)
                        ],
                    )
                )
            except (
                repositories.RepositoryAuthorizationError,
                repositories.RepositoryConflictError,
            ) as exc:
                raise repositories.RepositoryConflictError("agent_profile_snapshot_invalid") from exc
            skill_version_matches = (
                bool(snapshot_skill_version)
                and snapshot_skill_version == manifest_skill_version == release_skill_version
                and governed_mcp_tool_ids == admission.mcp_tool_ids
            )
        else:
            skill_version_matches = snapshot_skill_version == str(
                admission.skill.get("skill_version") or ""
            )
        if (
            profile_snapshot != expected_profile_snapshot
            or str(run.get("skill_id") or "") != str(admission.skill.get("skill_id") or "")
            or not skill_version_matches
            or str(snapshot.get("executor_type") or "")
            != str(admission.skill.get("executor_type") or "")
            or str(snapshot.get("model_id") or "") != str(admission.model.get("id") or "")
            or str(snapshot.get("model_value") or "") != str(admission.model.get("value") or "")
            or tuple(repositories.extract_run_mcp_tool_ids(execution_input))
            != admission.mcp_tool_ids
        ):
            raise repositories.RepositoryConflictError("agent_profile_snapshot_invalid")

    async def create_conversation(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        workspace_id: str,
        selection: SelectedAgentProfileRequest,
        title: str,
        session_id: str | None = None,
        purpose: str = "conversation",
        operation_id: UUID | None = None,
    ) -> ChatSessionResponse:
        """Atomically bind a new conversation to one current published profile revision/hash."""

        if purpose not in {"conversation", "builder_test"}:
            raise HTTPException(status_code=400, detail="agent_conversation_purpose_invalid")
        if purpose == "builder_test":
            self._require_admin(principal)
        if operation_id is not None and (purpose != "conversation" or session_id is not None):
            raise HTTPException(status_code=400, detail="agent_conversation_operation_invalid")
        await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
        await self._ensure_principal_user(conn, principal=principal)
        if operation_id is not None:
            session_id = f"ses_agent_{operation_id.hex}"
            existing = await repositories.get_authorized_session_projection(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
            )
            if existing is not None:
                response = session_response(existing)
                expected_title = title or (
                    response.agent_conversation.name if response.agent_conversation is not None else ""
                )
                if (
                    str(existing.get("workspace_id") or "") != workspace_id
                    or str(existing.get("agent_id") or "") != selection.agent_id
                    or response.agent_conversation is None
                    or response.agent_conversation.revision != selection.expected_revision
                    or response.purpose != purpose
                    or response.title != expected_title
                ):
                    raise repositories.RepositoryConflictError("agent_conversation_operation_conflict")
                return response
        admission = await self.resolve_for_admission(conn, principal=principal, selection=selection)
        resolved_title = title or admission.public_identity.name
        create_session_kwargs: dict[str, Any] = {
            "tenant_id": principal.tenant_id,
            "workspace_id": workspace_id,
            "user_id": principal.user_id,
            "agent_id": admission.agent_id,
            "title": resolved_title,
            "admitted_agent_profile_revision": admission.revision,
            "admitted_agent_profile_hash": admission.content_hash,
        }
        if session_id is not None:
            create_session_kwargs["session_id"] = session_id
            create_session_kwargs["return_created"] = True
        if purpose != "conversation":
            create_session_kwargs["purpose"] = purpose
        created = True
        created_session = await repositories.create_session(conn, **create_session_kwargs)
        if session_id is not None:
            session_id, created = created_session
        else:
            session_id = created_session
        if created:
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action=(
                    "agent_profile.test_conversation_created"
                    if purpose == "builder_test"
                    else "agent_conversation.created"
                ),
                target_type="agent_profile",
                target_id=admission.agent_id,
                trace_id=standard_trace_id(session_id),
                payload_json={
                    "revision": admission.revision,
                    "session_id": session_id,
                    "purpose": purpose,
                },
            )
        return ChatSessionResponse(
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=admission.agent_id,
            title=resolved_title,
            purpose=purpose,
            agent_conversation=admission.public_identity,
        )

    @staticmethod
    def reject_profile_selector_conflicts(
        request: ChatStreamRequest,
        *,
        active: bool | None = None,
        query_agent_id: str | None = None,
        admission: AgentProfileAdmission | None = None,
        allow_default_query_agent: bool = False,
    ) -> None:
        """Accept only transport values that cannot alter a resolved profile admission."""

        if active is None:
            active = request.selected_agent_profile is not None
        if not active:
            return
        selector_paths = set(request.profile_capability_selector_paths())
        if admission is None:
            if query_agent_id is not None or selector_paths:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            return

        allowed_query_agent_ids = {admission.agent_id}
        if allow_default_query_agent:
            allowed_query_agent_ids.add("general-agent")
        if query_agent_id is not None and query_agent_id not in allowed_query_agent_ids:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if selector_paths - _PROFILE_TRANSPORT_SELECTOR_PATHS:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")

        submitted_fields = request.model_fields_set
        if "agent_options" in submitted_fields:
            if not isinstance(request.agent_options, dict):
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            if set(request.agent_options) - _PROFILE_TRANSPORT_AGENT_OPTION_KEYS:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            if request.agent_options.get("enable_thinking", "off") != "off":
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            submitted_model_id = request.agent_options.get("model_id")
            if submitted_model_id is not None and submitted_model_id != admission.model["id"]:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            submitted_model_value = request.agent_options.get("model")
            if submitted_model_value is not None and submitted_model_value != admission.model["value"]:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "disabled_skills" in submitted_fields and request.disabled_skills:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "enabled_skills" in submitted_fields and request.enabled_skills:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "disabled_mcp_tools" in submitted_fields and request.disabled_mcp_tools:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "selected_agent_profile" in submitted_fields and (
            request.selected_agent_profile is None
            or request.selected_agent_profile.agent_id != admission.agent_id
            or request.selected_agent_profile.expected_revision != admission.revision
        ):
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "selected_mcp_tool_ids" in submitted_fields and (
            request.selected_mcp_tool_ids is None
            or tuple(request.selected_mcp_tool_ids) != admission.mcp_tool_ids
        ):
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")


def reject_profile_selector_conflicts(
    request: ChatStreamRequest,
    *,
    active: bool | None = None,
    query_agent_id: str | None = None,
) -> None:
    """Compatibility delegate for the authoritative profile selector policy."""

    AgentProfileAuthority.reject_profile_selector_conflicts(
        request,
        active=active,
        query_agent_id=query_agent_id,
    )
