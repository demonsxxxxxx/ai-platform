from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.agent_apps import AgentProfileAuthority
from app.agent_profiles import list_admin_profiles, list_public_profiles, publish_draft, save_draft
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import (
    AgentAppProjection,
    AgentAppsResponse,
    AgentProfileAdminListResponse,
    AgentProfileCatalogResponse,
    AgentProfileDraftRequest,
    AgentProfileDraftTestRequest,
    AgentProfileHistoryResponse,
    AgentProfileMutationResponse,
    AgentProfilePublishRequest,
    AgentProfilePublicProjection,
    AgentProfileUnpublishRequest,
    AgentProfileValidationResponse,
    ChatSessionResponse,
    CreateAgentConversationRequest,
)
from app.validation import assert_safe_id

router = APIRouter()
_authority = AgentProfileAuthority()


def _projection_mode(agent_type: str) -> str:
    if agent_type == "chat":
        return "chat"
    if agent_type == "file":
        return "chat_file"
    return "chat_file"


@router.get("/agent-apps", response_model=AgentAppsResponse)
async def list_agent_apps(
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentAppsResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    async with transaction() as conn:
        rows = await repositories.list_agent_app_projections(conn, tenant_id=principal.tenant_id)
    return AgentAppsResponse(
        agent_apps=[
            AgentAppProjection(
                app_id=row["app_id"],
                name=row["name"],
                mode=_projection_mode(row["agent_type"]),
                default_skill_id=row["default_skill_id"],
                allowed_input_types=row["input_modes"] or [],
                output_types=row["output_modes"] or [],
                status=row["status"],
            )
            for row in rows
        ]
    )


@router.get("/agent-profiles", response_model=AgentProfileCatalogResponse)
async def list_agent_profiles(
    query: str | None = Query(default=None, min_length=1, max_length=160),
    category: str | None = Query(default=None, pattern="^(general|support|writing|research|operations)$"),
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileCatalogResponse:
    """Return only current-principal-safe published Agent Profile market cards."""

    async with transaction() as conn:
        if query is None and category is None:
            profiles = await list_public_profiles(conn, principal=principal)
        else:
            profiles = await list_public_profiles(conn, principal=principal, query=query, category=category)
    return AgentProfileCatalogResponse(agent_profiles=profiles)


@router.get("/agent-profiles/{agent_id}", response_model=AgentProfilePublicProjection)
async def get_agent_profile(
    agent_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfilePublicProjection:
    """Return public detail through the same ACL/capability path as catalog cards."""

    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="agent_profile_not_found") from exc
    async with transaction() as conn:
        return await _authority.get_public(conn, principal=principal, agent_id=safe_agent_id)


@router.post("/agent-conversations", response_model=ChatSessionResponse, response_model_exclude_none=True)
async def create_agent_conversation(
    request: CreateAgentConversationRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> ChatSessionResponse:
    """Atomically create a conversation pinned to the current authorized publication."""

    try:
        async with transaction() as conn:
            return await _authority.create_conversation(
                conn,
                principal=principal,
                workspace_id=request.workspace_id,
                selection=request.selected_agent_profile,
                title=request.title,
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_not_available") from exc
    except repositories.RepositoryNotFoundError as exc:
        detail = "workspace_not_found" if str(exc) == "workspace_not_found" else "agent_profile_not_available"
        raise HTTPException(status_code=404, detail=detail) from exc
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="agent_profile_not_authorized") from exc


@router.get("/admin/agent-profiles", response_model=AgentProfileAdminListResponse)
async def admin_list_agent_profiles(
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileAdminListResponse:
    """Return same-tenant latest profile revisions to AI administrators only."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    async with transaction() as conn:
        profiles = await list_admin_profiles(conn, principal=principal)
    return AgentProfileAdminListResponse(agent_profiles=profiles)


@router.get("/admin/agent-profiles/{agent_id}/history", response_model=AgentProfileHistoryResponse)
async def admin_agent_profile_history(
    agent_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileHistoryResponse:
    """Return immutable lifecycle history to same-tenant AI administrators."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_id_invalid") from exc
    async with transaction() as conn:
        profiles = await _authority.list_history(conn, principal=principal, agent_id=safe_agent_id)
    return AgentProfileHistoryResponse(agent_profiles=profiles)


@router.post("/admin/agent-profiles", response_model=AgentProfileMutationResponse)
async def create_agent_profile(
    request: AgentProfileDraftRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileMutationResponse:
    """Save the first immutable draft revision with a server-generated Agent identity."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        async with transaction() as conn:
            profile, audit_id = await save_draft(
                conn,
                principal=principal,
                definition=request,
                agent_id=None,
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale") from exc
    return AgentProfileMutationResponse(agent_profile=profile, audit_id=audit_id)


@router.put("/admin/agent-profiles/{agent_id}", response_model=AgentProfileMutationResponse)
async def save_agent_profile_draft(
    agent_id: str,
    request: AgentProfileDraftRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileMutationResponse:
    """Append a later immutable draft revision for the same profile identity."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_id_invalid") from exc
    try:
        async with transaction() as conn:
            profile, audit_id = await save_draft(
                conn,
                principal=principal,
                definition=request,
                agent_id=safe_agent_id,
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale") from exc
    return AgentProfileMutationResponse(agent_profile=profile, audit_id=audit_id)


@router.post("/admin/agent-profiles/test", response_model=AgentProfileValidationResponse)
async def validate_agent_profile_draft(
    request: AgentProfileDraftTestRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileValidationResponse:
    """Validate a saved or unsaved definition without creating an execution run."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        async with transaction() as conn:
            audit_id = await _authority.validate_draft(
                conn,
                principal=principal,
                definition=request.definition,
                agent_id=request.agent_id,
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale") from exc
    return AgentProfileValidationResponse(audit_id=audit_id)


@router.post("/admin/agent-profiles/{agent_id}/publish", response_model=AgentProfileMutationResponse)
async def publish_agent_profile(
    agent_id: str,
    request: AgentProfilePublishRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileMutationResponse:
    """Publish a revalidated immutable copy of the requested draft revision."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_id_invalid") from exc
    try:
        async with transaction() as conn:
            profile, audit_id = await publish_draft(
                conn,
                principal=principal,
                agent_id=safe_agent_id,
                expected_revision=request.expected_revision,
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale") from exc
    return AgentProfileMutationResponse(agent_profile=profile, audit_id=audit_id)


@router.post("/admin/agent-profiles/{agent_id}/unpublish", response_model=AgentProfileMutationResponse)
async def unpublish_agent_profile(
    agent_id: str,
    request: AgentProfileUnpublishRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileMutationResponse:
    """Withdraw a current publication and block every new Agent Conversation admission."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_id_invalid") from exc
    try:
        async with transaction() as conn:
            profile, audit_id = await _authority.unpublish(
                conn,
                principal=principal,
                agent_id=safe_agent_id,
                expected_revision=request.expected_revision,
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_revision_stale") from exc
    return AgentProfileMutationResponse(agent_profile=profile, audit_id=audit_id)
