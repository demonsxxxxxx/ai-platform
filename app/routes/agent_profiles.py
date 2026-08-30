import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query, Request as HttpRequest
from app import repositories
from app.agent_apps import AgentProfileAuthority
from app.agent_profiles import list_admin_profiles, list_public_profiles, publish_draft, save_draft
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.department_directory import validate_profile_department_authorities
from app.models import (
    AgentProfileAdminListResponse,
    AgentAppRunRequest,
    AgentProfileCatalogResponse,
    AgentProfileDraftRequest,
    AgentProfileDraftTestRequest,
    AgentProfileHistoryResponse,
    AgentProfileMutationResponse,
    AgentProfilePublishRequest,
    AgentProfilePublicProjection,
    AgentProfileTrialRunRequest,
    AgentProfileTrialRunResponse,
    AgentProfileUnpublishRequest,
    AgentProfileValidationResponse,
    ChatSessionResponse,
    ChatStreamRequest,
    ChatStreamResponse,
    CreateAgentConversationRequest,
    SelectedAgentProfileRequest,
)
from app.validation import assert_safe_id

router = APIRouter()
_authority = AgentProfileAuthority(
    department_authority_validator=validate_profile_department_authorities,
)
_DEDICATED_OVERRIDE_HEADERS = frozenset(
    {
        "x-agent-id",
        "x-agent-profile-revision",
        "x-agent-profile-hash",
        "x-model-id",
        "x-skill-id",
        "x-skill-version",
        "x-mcp-tool-ids",
    }
)


def _reject_dedicated_capability_overrides(http_request: HttpRequest) -> None:
    """Reject every non-body transport that could claim execution authority."""

    if http_request.query_params:
        raise HTTPException(status_code=400, detail="agent_app_override_not_allowed")
    if _DEDICATED_OVERRIDE_HEADERS.intersection(name.casefold() for name in http_request.headers):
        raise HTTPException(status_code=400, detail="agent_app_override_not_allowed")


def _normalize_catalog_query(query: str | None) -> str | None:
    if query is None:
        return None
    normalized = unicodedata.normalize("NFKC", query).strip()
    if not normalized or len(normalized) > 160:
        raise HTTPException(status_code=422, detail="agent_profile_query_invalid")
    return normalized


async def _submit_dedicated_agent_run(
    *,
    agent_id: str,
    session_id: str,
    request: AgentAppRunRequest,
    http_request: HttpRequest,
    principal: AuthPrincipal,
) -> ChatStreamResponse:
    """Restore session scope, then delegate to the sole Chat admission/streaming chain."""

    async with transaction() as conn:
        session = await repositories.get_authorized_session_projection(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
    if session is None:
        raise HTTPException(status_code=404, detail="agent_conversation_not_found")
    if str(session.get("agent_id") or "") != agent_id:
        raise HTTPException(status_code=409, detail="agent_profile_session_mismatch")

    canonical_request = ChatStreamRequest(
        workspace_id=str(session["workspace_id"]),
        session_id=session_id,
        message=request.message,
        file_ids=request.file_ids,
        submission_id=request.submission_id,
        user_timezone=request.user_timezone,
    )
    # Local import avoids making the Chat route depend on this adapter while
    # preserving one admission, Run, Queue, SSE, and artifact authority.
    from app.routes.chat import chat_stream

    return await chat_stream(
        canonical_request,
        http_request,
        agent_id=agent_id,
        principal=principal,
    )


@router.get("/agent-apps", include_in_schema=False)
async def retired_agent_apps(
    _principal: AuthPrincipal = Depends(require_principal),
) -> None:
    """Retire the legacy hard-coded Agent App catalog without a second projection authority."""

    raise HTTPException(status_code=410, detail="agent_apps_retired_use_agent_profiles")


@router.get("/agent-profiles", response_model=AgentProfileCatalogResponse)
async def list_agent_profiles(
    query: str | None = Query(default=None, min_length=1, max_length=160),
    category: str | None = Query(default=None, pattern="^(general|support|writing|research|operations)$"),
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileCatalogResponse:
    """Return only current-principal-safe published Agent Profile market cards."""

    normalized_query = _normalize_catalog_query(query)
    async with transaction() as conn:
        if normalized_query is None and category is None:
            profiles = await list_public_profiles(conn, principal=principal)
        else:
            profiles = await list_public_profiles(
                conn,
                principal=principal,
                query=normalized_query,
                category=category,
            )
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
                operation_id=request.operation_id,
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_not_available") from exc
    except repositories.RepositoryNotFoundError as exc:
        detail = "workspace_not_found" if str(exc) == "workspace_not_found" else "agent_profile_not_available"
        raise HTTPException(status_code=404, detail=detail) from exc
    except repositories.RepositoryAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="agent_profile_not_authorized") from exc


@router.post(
    "/agent-apps/{agent_id}/conversations/{session_id}/runs",
    response_model=ChatStreamResponse,
    response_model_exclude_none=True,
)
async def submit_agent_app_run(
    agent_id: str,
    session_id: str,
    request: AgentAppRunRequest,
    http_request: HttpRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> ChatStreamResponse:
    """Submit through the fixed Agent Conversation authority, with no client selectors."""

    _reject_dedicated_capability_overrides(http_request)
    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
        safe_session_id = assert_safe_id(session_id, "session_id")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="agent_conversation_not_found") from exc
    return await _submit_dedicated_agent_run(
        agent_id=safe_agent_id,
        session_id=safe_session_id,
        request=request,
        http_request=http_request,
        principal=principal,
    )


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


@router.post(
    "/admin/agent-profiles/{agent_id}/test-runs",
    response_model=AgentProfileTrialRunResponse,
    response_model_exclude_none=True,
)
async def run_agent_profile_test(
    agent_id: str,
    request: AgentProfileTrialRunRequest,
    http_request: HttpRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentProfileTrialRunResponse:
    """Create an idempotent test conversation and execute through the canonical chain."""

    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    _reject_dedicated_capability_overrides(http_request)
    try:
        safe_agent_id = assert_safe_id(agent_id, "agent_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_id_invalid") from exc
    test_session_id = f"ses_test_{request.submission_id.hex}"
    selection = SelectedAgentProfileRequest(
        agent_id=safe_agent_id,
        expected_revision=request.expected_revision,
    )
    try:
        async with transaction() as conn:
            await _authority.create_conversation(
                conn,
                principal=principal,
                workspace_id=request.workspace_id,
                selection=selection,
                title=f"[Builder test] {safe_agent_id}",
                session_id=test_session_id,
                purpose="builder_test",
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail="agent_profile_test_submission_conflict") from exc
    outcome = await _submit_dedicated_agent_run(
        agent_id=safe_agent_id,
        session_id=test_session_id,
        request=AgentAppRunRequest(
            message=request.message,
            submission_id=request.submission_id,
            file_ids=request.file_ids,
            user_timezone=request.user_timezone,
        ),
        http_request=http_request,
        principal=principal,
    )
    return AgentProfileTrialRunResponse.model_validate(
        {**outcome.model_dump(mode="python"), "purpose": "builder_test"}
    )


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
