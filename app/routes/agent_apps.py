from fastapi import APIRouter, Depends, HTTPException

from app import repositories
from app.agent_profiles import list_admin_profiles, list_public_profiles, publish_draft, save_draft
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import (
    AgentAppProjection,
    AgentAppsResponse,
    AgentProfileAdminListResponse,
    AgentProfileCatalogResponse,
    AgentProfileDraftRequest,
    AgentProfileMutationResponse,
    AgentProfilePublishRequest,
)
from app.validation import assert_safe_id

router = APIRouter()


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
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileCatalogResponse:
    """Return only current-principal-safe published Agent Profile market cards."""

    async with transaction() as conn:
        profiles = await list_public_profiles(conn, principal=principal)
    return AgentProfileCatalogResponse(agent_profiles=profiles)


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
