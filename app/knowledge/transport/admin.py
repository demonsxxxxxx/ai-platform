"""Administrative HTTP transport for the Knowledge control plane."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.knowledge.application.control_plane import configured_knowledge_control_plane
from app.knowledge.domain import KnowledgeError, canonical_knowledge_source_id


PrincipalDependency = Callable[..., Any]
AdminPredicate = Callable[[Any], bool]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionCreateRequest(_StrictRequest):
    operation_id: str
    name: str
    base_url: str
    credential: Any


class ConnectionCredentialRequest(_StrictRequest):
    operation_id: str
    credential: Any


class OperationRequest(_StrictRequest):
    operation_id: str


class SourcePatchRequest(_StrictRequest):
    operation_id: str
    display_name: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=1000)
    status: str | None = None


class SourceAclRequest(_StrictRequest):
    operation_id: str
    expected_authorization_version: int = Field(ge=1)
    visibility: str
    department_ids: list[str] = Field(default_factory=list, max_length=200)


def _require_admin(principal: Any, *, is_admin: AdminPredicate) -> None:
    if not is_admin(principal):
        raise HTTPException(status_code=403, detail="knowledge_admin_required")


def _operation_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="knowledge_operation_id_invalid") from exc
    if parsed.version != 4 or str(parsed) != value.lower():
        raise HTTPException(status_code=422, detail="knowledge_operation_id_invalid")
    return str(parsed)


def _request(model_type: type[_StrictRequest], payload: Any) -> _StrictRequest:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        fields = {
            str(error.get("loc", ("request",))[-1])
            for error in exc.errors(include_input=False)
        }
        if "credential" in fields:
            detail = "knowledge_connection_credential_invalid"
        else:
            detail = "knowledge_request_invalid"
        raise HTTPException(status_code=422, detail=detail) from exc


def _credential(value: Any) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="knowledge_connection_credential_invalid")
    normalized = value.strip()
    if not normalized or "\x00" in normalized or len(normalized.encode("utf-8")) > 4096:
        raise HTTPException(status_code=422, detail="knowledge_connection_credential_invalid")
    return normalized


def _http_error(exc: Exception) -> HTTPException:
    code = str(exc) or "knowledge_unavailable"
    if code in {
        "knowledge_connection_name_invalid",
        "knowledge_connection_endpoint_invalid",
        "knowledge_connection_endpoint_forbidden",
        "knowledge_connection_https_required",
        "knowledge_connection_dns_unavailable",
        "knowledge_connection_provider_change_forbidden",
        "knowledge_source_display_name_invalid",
        "knowledge_source_description_invalid",
        "knowledge_source_status_invalid",
        "knowledge_source_visibility_invalid",
        "knowledge_source_acl_identity_invalid",
        "knowledge_source_acl_enterprise_scope_invalid",
        "knowledge_source_acl_scope_required",
        "knowledge_cursor_invalid",
        "knowledge_provider_catalog_invalid",
        "knowledge_provider_catalog_limit_exceeded",
        "platform_credential_value_invalid",
    }:
        return HTTPException(status_code=422, detail=code)
    if code in {
        "knowledge_connection_not_found",
        "knowledge_connection_candidate_not_found",
        "knowledge_connection_not_active",
    }:
        return HTTPException(status_code=404, detail="knowledge_resource_not_found")
    if code in {
        "knowledge_connection_conflict",
        "knowledge_connection_candidate_stale",
        "knowledge_connection_revision_stale",
        "knowledge_source_acl_version_stale",
        "knowledge_source_connection_inactive",
        "knowledge_source_acl_invalid",
        "knowledge_source_missing",
        "knowledge_sync_in_progress",
        "knowledge_sync_lease_stale",
        "knowledge_check_in_progress",
        "knowledge_check_lease_stale",
        "knowledge_operation_identity_reused",
    }:
        return HTTPException(status_code=409, detail=code)
    if code == "knowledge_connection_auth_invalid":
        return HTTPException(status_code=400, detail=code)
    if code == "knowledge_provider_rate_limited":
        return HTTPException(status_code=429, detail=code)
    if code in {
        "platform_credential_key_invalid",
        "platform_credential_invalid",
        "platform_credential_not_found",
        "knowledge_source_acl_identity_authority_unavailable",
        "knowledge_sync_reconcile_required",
        "knowledge_check_reconcile_required",
    }:
        return HTTPException(status_code=503, detail="knowledge_credential_service_unavailable")
    return HTTPException(status_code=502, detail=code)


async def _call(awaitable: Any) -> Any:
    try:
        return await awaitable
    except KnowledgeError as exc:
        raise _http_error(exc) from exc


def _not_found(value: Any) -> Any:
    if value is None:
        raise HTTPException(status_code=404, detail="knowledge_resource_not_found")
    return value


def build_knowledge_admin_router(
    *,
    principal_dependency: PrincipalDependency,
    is_admin: AdminPredicate,
) -> APIRouter:
    router = APIRouter(prefix="/admin/knowledge", tags=["knowledge-admin"])

    @router.post("/connections", status_code=201)
    async def create_connection(
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(ConnectionCreateRequest, payload)
        assert isinstance(request, ConnectionCreateRequest)
        return await _call(
            configured_knowledge_control_plane().create_connection(
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                operation_id=_operation_id(request.operation_id),
                name=request.name,
                base_url=request.base_url,
                credential=_credential(request.credential),
            )
        )

    @router.get("/connections")
    async def list_connections(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=1024),
        q: str = Query(default="", max_length=120),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        return await _call(
            configured_knowledge_control_plane().list_connections(
                tenant_id=principal.tenant_id,
                limit=limit,
                cursor=cursor,
                query=q.strip(),
            )
        )

    @router.get("/connections/{connection_id}")
    async def get_connection(
        connection_id: str,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        return _not_found(
            await _call(
                configured_knowledge_control_plane().get_connection(
                    tenant_id=principal.tenant_id,
                    connection_id=connection_id,
                )
            )
        )

    @router.patch("/connections/{connection_id}")
    async def rotate_connection_credential(
        connection_id: str,
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(ConnectionCredentialRequest, payload)
        assert isinstance(request, ConnectionCredentialRequest)
        return _not_found(
            await _call(
                configured_knowledge_control_plane().rotate_credential(
                    tenant_id=principal.tenant_id,
                    actor_id=principal.user_id,
                    connection_id=connection_id,
                    operation_id=_operation_id(request.operation_id),
                    credential=_credential(request.credential),
                )
            )
        )

    @router.post("/connections/{connection_id}/check")
    async def check_connection(
        connection_id: str,
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(OperationRequest, payload)
        assert isinstance(request, OperationRequest)
        return await _call(
            configured_knowledge_control_plane().check_connection(
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                connection_id=connection_id,
                operation_id=_operation_id(request.operation_id),
            )
        )

    @router.post("/connections/{connection_id}/activate-candidate")
    async def activate_candidate(
        connection_id: str,
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(OperationRequest, payload)
        assert isinstance(request, OperationRequest)
        return await _call(
            configured_knowledge_control_plane().activate_candidate(
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                connection_id=connection_id,
                operation_id=_operation_id(request.operation_id),
            )
        )

    @router.post("/connections/{connection_id}/disable")
    async def disable_connection(
        connection_id: str,
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(OperationRequest, payload)
        assert isinstance(request, OperationRequest)
        return _not_found(
            await _call(
                configured_knowledge_control_plane().disable_connection(
                    tenant_id=principal.tenant_id,
                    connection_id=connection_id,
                    operation_id=_operation_id(request.operation_id),
                    actor_id=principal.user_id,
                )
            )
        )

    @router.post("/connections/{connection_id}/syncs")
    async def sync_connection(
        connection_id: str,
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(OperationRequest, payload)
        assert isinstance(request, OperationRequest)
        return await _call(
            configured_knowledge_control_plane().sync_connection(
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                connection_id=connection_id,
                operation_id=_operation_id(request.operation_id),
            )
        )

    @router.get("/syncs/{sync_id}")
    async def get_sync(
        sync_id: str,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        return _not_found(
            await _call(
                configured_knowledge_control_plane().get_sync(
                    tenant_id=principal.tenant_id,
                    sync_id=sync_id,
                )
            )
        )

    @router.get("/sources")
    async def list_sources(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=1024),
        q: str = Query(default="", max_length=240),
        connection_id: str | None = Query(default=None, max_length=160),
        status: str | None = Query(default=None, max_length=32),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        if status not in {None, "pending_review", "active", "disabled", "missing"}:
            raise HTTPException(status_code=422, detail="knowledge_source_status_invalid")
        return await _call(
            configured_knowledge_control_plane().list_sources(
                tenant_id=principal.tenant_id,
                limit=limit,
                cursor=cursor,
                query=q.strip(),
                connection_id=connection_id,
                status=status,
            )
        )

    @router.get("/builder-catalog")
    async def builder_catalog(
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=1024),
        q: str = Query(default="", max_length=240),
        selected_source_id: list[str] = Query(default=[]),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        if len(selected_source_id) > 8 or len(selected_source_id) != len(
            set(selected_source_id)
        ):
            raise HTTPException(status_code=422, detail="knowledge_builder_selection_invalid")
        try:
            selected_source_ids = [
                canonical_knowledge_source_id(value)
                for value in selected_source_id
            ]
        except KnowledgeError as exc:
            raise HTTPException(
                status_code=422,
                detail="knowledge_builder_selection_invalid",
            ) from exc
        return await _call(
            configured_knowledge_control_plane().list_builder_catalog(
                tenant_id=principal.tenant_id,
                limit=limit,
                cursor=cursor,
                query=q.strip(),
                selected_source_ids=selected_source_ids,
            )
        )

    @router.get("/sources/{source_id}")
    async def get_source(
        source_id: str,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        return _not_found(
            await _call(
                configured_knowledge_control_plane().get_source(
                    tenant_id=principal.tenant_id,
                    source_id=source_id,
                )
            )
        )

    @router.patch("/sources/{source_id}")
    async def update_source(
        source_id: str,
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(SourcePatchRequest, payload)
        assert isinstance(request, SourcePatchRequest)
        fields = request.model_fields_set
        if not fields.intersection({"display_name", "description", "status"}):
            raise HTTPException(status_code=422, detail="knowledge_source_update_empty")
        return _not_found(
            await _call(
                configured_knowledge_control_plane().update_source(
                    tenant_id=principal.tenant_id,
                    actor_id=principal.user_id,
                    source_id=source_id,
                    operation_id=_operation_id(request.operation_id),
                    display_name_present="display_name" in fields,
                    display_name=request.display_name,
                    description_present="description" in fields,
                    description=request.description,
                    status=request.status,
                )
            )
        )

    @router.put("/sources/{source_id}/acl")
    async def replace_source_acl(
        source_id: str,
        payload: Any = Body(...),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        request = _request(SourceAclRequest, payload)
        assert isinstance(request, SourceAclRequest)
        return _not_found(
            await _call(
                configured_knowledge_control_plane().replace_source_acl(
                    tenant_id=principal.tenant_id,
                    actor_id=principal.user_id,
                    source_id=source_id,
                    operation_id=_operation_id(request.operation_id),
                    expected_version=request.expected_authorization_version,
                    visibility=request.visibility,
                    department_ids=request.department_ids,
                )
            )
        )

    return router
