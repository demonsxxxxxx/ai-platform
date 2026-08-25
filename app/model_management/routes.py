"""Administrative model control plane and trusted runtime model proxy."""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.settings import get_settings

from .client import (
    ModelUpstreamError,
    open_upstream_stream,
    parse_model_ids,
    request_upstream,
)
from .repository import (
    activate_connection_and_sync,
    get_active_connection,
    get_connection_projection,
    get_run_connection,
    list_admin_models,
    update_catalog_entry,
)
from .security import (
    ModelConnectionSecurityError,
    api_key_fingerprint,
    validate_endpoint,
)


router = APIRouter()
_ALLOWED_RUNTIME_PATHS = {
    "openai": frozenset({"v1/chat/completions", "v1/responses"}),
    "anthropic": frozenset({"v1/messages", "v1/messages/count_tokens"}),
}


class ModelConnectionRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)


class ModelCatalogEntryPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    enabled: bool | None = None
    is_default: bool | None = None


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="model_admin_required")


def _settings_security() -> tuple[str, str]:
    settings = get_settings()
    return (
        str(settings.model_connection_encryption_key or ""),
        str(settings.model_connection_allowed_internal_hosts or ""),
    )


def _translate_control_plane_error(exc: Exception) -> HTTPException:
    code = str(exc)
    if code in {
        "model_connection_endpoint_invalid",
        "model_connection_endpoint_must_be_origin",
        "model_connection_endpoint_forbidden",
        "model_connection_https_required",
        "model_connection_api_key_invalid",
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


async def _discover_models(*, base_url: str, api_key: str) -> tuple[str, list[str]]:
    encryption_key, allowed_hosts = _settings_security()
    if not encryption_key:
        raise ModelConnectionSecurityError("model_connection_encryption_key_invalid")
    endpoint = validate_endpoint(base_url, allowed_internal_hosts=allowed_hosts)
    response = await asyncio.to_thread(
        request_upstream,
        base_url=endpoint.base_url,
        allowed_internal_hosts=allowed_hosts,
        api_key=api_key,
        method="GET",
        path="/v1/models",
        provider="catalog",
    )
    return endpoint.base_url, parse_model_ids(response)


@router.get("/admin/models")
async def admin_models(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    async with transaction() as conn:
        return {
            "connection": await get_connection_projection(conn),
            "models": await list_admin_models(conn),
        }


@router.put("/admin/models/connection")
async def configure_model_connection(
    payload: ModelConnectionRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    encryption_key, _ = _settings_security()
    api_key = str(payload.api_key or "").strip()
    if not api_key:
        async with transaction() as conn:
            current = await get_active_connection(conn, encryption_key=encryption_key)
        if current is None:
            raise HTTPException(status_code=422, detail="model_connection_api_key_required")
        api_key = current.api_key
    try:
        base_url, model_ids = await _discover_models(base_url=payload.base_url, api_key=api_key)
        async with transaction() as conn:
            revision, models = await activate_connection_and_sync(
                conn,
                base_url=base_url,
                api_key=api_key,
                key_fingerprint=api_key_fingerprint(api_key),
                encryption_key=encryption_key,
                actor_user_id=principal.user_id,
                upstream_model_ids=model_ids,
            )
            connection = await get_connection_projection(conn)
        return {"connection": connection, "models": models, "revision": revision}
    except (ModelConnectionSecurityError, ModelUpstreamError, ValueError) as exc:
        raise _translate_control_plane_error(exc) from exc


@router.post("/admin/models/sync")
async def sync_model_catalog(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    encryption_key, _ = _settings_security()
    try:
        async with transaction() as conn:
            current = await get_active_connection(conn, encryption_key=encryption_key)
        if current is None:
            raise ModelConnectionSecurityError("model_connection_not_configured")
        base_url, model_ids = await _discover_models(
            base_url=current.base_url,
            api_key=current.api_key,
        )
        async with transaction() as conn:
            revision, models = await activate_connection_and_sync(
                conn,
                base_url=base_url,
                api_key=current.api_key,
                key_fingerprint=current.key_fingerprint,
                encryption_key=encryption_key,
                actor_user_id=principal.user_id,
                upstream_model_ids=model_ids,
            )
            connection = await get_connection_projection(conn)
        return {"connection": connection, "models": models, "revision": revision}
    except (ModelConnectionSecurityError, ModelUpstreamError, ValueError) as exc:
        raise _translate_control_plane_error(exc) from exc


@router.patch("/admin/models/{model_id}")
async def patch_model_catalog_entry(
    model_id: str,
    payload: ModelCatalogEntryPatch,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    try:
        async with transaction() as conn:
            model = await update_catalog_entry(
                conn,
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


@router.api_route(
    "/internal/model-proxy/{provider}/{upstream_path:path}",
    methods=["POST"],
    include_in_schema=False,
)
async def proxy_model_request(
    provider: str,
    upstream_path: str,
    request: Request,
    x_ai_platform_run_id: str = Header(default=""),
    x_ai_platform_attempt_id: str = Header(default=""),
    x_ai_platform_internal_token: str = Header(default=""),
) -> Response:
    settings = get_settings()
    expected_token = str(settings.model_proxy_internal_token or "")
    if not expected_token or not hmac.compare_digest(x_ai_platform_internal_token, expected_token):
        raise HTTPException(status_code=403, detail="model_proxy_forbidden")
    if not x_ai_platform_attempt_id:
        raise HTTPException(status_code=403, detail="model_proxy_attempt_required")
    if upstream_path not in _ALLOWED_RUNTIME_PATHS.get(provider, frozenset()):
        raise HTTPException(status_code=403, detail="model_proxy_path_not_allowed")
    if request.url.query:
        raise HTTPException(status_code=403, detail="model_proxy_query_not_allowed")
    body_buffer = bytearray()
    async for chunk in request.stream():
        if len(body_buffer) + len(chunk) > 1024 * 1024:
            raise HTTPException(status_code=413, detail="model_proxy_request_too_large")
        body_buffer.extend(chunk)
    body = bytes(body_buffer)
    try:
        payload = json.loads(body)
        model_value = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(model_value, str) or not model_value:
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="model_proxy_body_invalid") from None
    encryption_key, allowed_hosts = _settings_security()
    try:
        async with transaction() as conn:
            connection = await get_run_connection(
                conn,
                run_id=x_ai_platform_run_id,
                attempt_id=x_ai_platform_attempt_id,
                model_value=model_value,
                encryption_key=encryption_key,
            )
        if connection is None:
            raise HTTPException(status_code=403, detail="model_proxy_run_binding_invalid")
        upstream = await asyncio.to_thread(
            open_upstream_stream,
            base_url=connection.base_url,
            allowed_internal_hosts=allowed_hosts,
            api_key=connection.api_key,
            method="POST",
            path=f"/{upstream_path}",
            provider=provider,
            body=body,
            headers=request.headers,
        )
    except HTTPException:
        raise
    except (ModelConnectionSecurityError, ModelUpstreamError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StreamingResponse(
        upstream.body(),
        status_code=upstream.status,
        media_type=upstream.content_type.split(";", 1)[0],
        headers={"cache-control": "no-store"},
    )
