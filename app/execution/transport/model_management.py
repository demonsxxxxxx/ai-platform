"""Administrative model control plane and trusted runtime model proxy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.execution.application.model_control_plane import configured_model_control_plane


PrincipalDependency = Callable[..., Any]
AdminPredicate = Callable[[Any], bool]

class ModelConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_url: Any = None
    credential: Any = None
    deprecated_api_key: Any = Field(default=None, alias="api_key", exclude=True)


class ModelCatalogEntryPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    display_name: str | None = Field(default=None, max_length=160)
    enabled: bool | None = None
    is_default: bool | None = None


def _require_admin(principal: Any, *, is_admin: AdminPredicate) -> None:
    if not is_admin(principal):
        raise HTTPException(status_code=403, detail="model_admin_required")


def _translate_control_plane_error(exc: Exception) -> HTTPException:
    code = str(exc)
    if code in {
        "model_connection_endpoint_invalid",
        "model_connection_endpoint_must_be_origin",
        "model_connection_endpoint_forbidden",
        "model_connection_https_required",
        "model_connection_api_key_invalid",
        "model_connection_api_key_required",
        "model_display_name_invalid",
        "model_default_must_be_available",
    }:
        return HTTPException(status_code=422, detail=code)
    if code in {"model_connection_authentication_failed"}:
        return HTTPException(status_code=400, detail=code)
    if code in {"model_connection_rate_limited"}:
        return HTTPException(status_code=429, detail=code)
    if code in {"model_connection_encryption_key_invalid"}:
        return HTTPException(status_code=503, detail=code)
    return HTTPException(status_code=502, detail=code or "model_connection_unavailable")


async def admin_models(
    principal: Any,
    *,
    is_admin: AdminPredicate,
) -> dict[str, Any]:
    _require_admin(principal, is_admin=is_admin)
    return await configured_model_control_plane().admin_projection()


async def configure_model_connection(
    payload: ModelConnectionRequest,
    principal: Any,
    *,
    is_admin: AdminPredicate,
) -> dict[str, Any]:
    _require_admin(principal, is_admin=is_admin)
    extra_fields = set(payload.model_extra or ())
    if "deprecated_api_key" in payload.model_fields_set or "apiKey" in extra_fields:
        raise HTTPException(
            status_code=422,
            detail="model_connection_credential_field_invalid",
        )
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail="model_connection_request_invalid",
        )
    if payload.credential is not None and (
        not isinstance(payload.credential, str) or len(payload.credential) > 4096
    ):
        raise HTTPException(
            status_code=422,
            detail="model_connection_credential_field_invalid",
        )
    if (
        not isinstance(payload.base_url, str)
        or not payload.base_url
        or len(payload.base_url) > 2048
    ):
        raise HTTPException(
            status_code=422,
            detail="model_connection_endpoint_invalid",
        )
    try:
        return await configured_model_control_plane().configure_connection(
            base_url=payload.base_url,
            api_key=payload.credential,
            actor_user_id=principal.user_id,
        )
    except (RuntimeError, ValueError) as exc:
        raise _translate_control_plane_error(exc) from exc


async def sync_model_catalog(
    principal: Any,
    *,
    is_admin: AdminPredicate,
) -> dict[str, Any]:
    _require_admin(principal, is_admin=is_admin)
    try:
        return await configured_model_control_plane().sync(
            actor_user_id=principal.user_id,
        )
    except (RuntimeError, ValueError) as exc:
        raise _translate_control_plane_error(exc) from exc


async def patch_model_catalog_entry(
    model_id: str,
    payload: ModelCatalogEntryPatch,
    principal: Any,
    *,
    is_admin: AdminPredicate,
) -> dict[str, Any]:
    _require_admin(principal, is_admin=is_admin)
    if payload.model_extra:
        raise HTTPException(
            status_code=422,
            detail="model_catalog_patch_request_invalid",
        )
    try:
        model = await configured_model_control_plane().patch_catalog(
            model_id=model_id,
            display_name=payload.display_name,
            enabled=payload.enabled,
            is_default=payload.is_default,
        )
        if model is None:
            raise HTTPException(status_code=404, detail="model_not_found")
        return model
    except ValueError as exc:
        raise _translate_control_plane_error(exc) from exc


async def proxy_model_request(
    provider: str,
    upstream_path: str,
    request: Request,
    x_ai_platform_run_id: str = Header(default=""),
    x_ai_platform_attempt_id: str = Header(default=""),
    x_ai_platform_internal_token: str = Header(default=""),
) -> Response:
    body_buffer = bytearray()
    async for chunk in request.stream():
        if len(body_buffer) + len(chunk) > 1024 * 1024:
            raise HTTPException(status_code=413, detail="model_proxy_request_too_large")
        body_buffer.extend(chunk)
    body = bytes(body_buffer)
    try:
        upstream = await configured_model_control_plane().proxy(
            provider=provider,
            upstream_path=upstream_path,
            query_present=bool(request.url.query),
            body=body,
            headers=request.headers,
            run_id=x_ai_platform_run_id,
            attempt_id=x_ai_platform_attempt_id,
            internal_token=x_ai_platform_internal_token,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        if str(exc) == "model_proxy_body_invalid":
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StreamingResponse(
        upstream.body,
        status_code=upstream.status,
        media_type=upstream.content_type.split(";", 1)[0],
        headers={"cache-control": "no-store"},
    )


def build_model_management_router(
    *,
    principal_dependency: PrincipalDependency,
    is_admin: AdminPredicate,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/models")
    async def admin_models_endpoint(
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return await admin_models(principal, is_admin=is_admin)

    @router.put("/admin/models/connection")
    async def configure_model_connection_endpoint(
        payload: ModelConnectionRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return await configure_model_connection(
            payload,
            principal,
            is_admin=is_admin,
        )

    @router.post("/admin/models/sync")
    async def sync_model_catalog_endpoint(
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return await sync_model_catalog(principal, is_admin=is_admin)

    @router.patch("/admin/models/{model_id}")
    async def patch_model_catalog_entry_endpoint(
        model_id: str,
        payload: ModelCatalogEntryPatch,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return await patch_model_catalog_entry(
            model_id,
            payload,
            principal,
            is_admin=is_admin,
        )

    router.add_api_route(
        "/internal/model-proxy/{provider}/{upstream_path:path}",
        proxy_model_request,
        methods=["POST"],
        include_in_schema=False,
    )
    return router
