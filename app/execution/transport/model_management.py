"""Administrative model control plane and trusted runtime model proxy."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from starlette.concurrency import iterate_in_threadpool
from app.execution.domain.runtime_debug import normalize_execution_debug
from app.execution.application.model_control_plane import configured_model_control_plane
from app.execution.application.model_stream_ownership import close_model_stream


_logger = logging.getLogger(__name__)


async def _record_proxy_debug(*, run_id: str, attempt_id: str, error_code: str, debug: object, operation: str) -> None:
    facts = normalize_execution_debug(debug)
    if not facts:
        return
    try:
        await configured_model_control_plane().record_debug(
            run_id=run_id, attempt_id=attempt_id, error_code=error_code,
            debug={**facts, "operation": operation},
        )
    except Exception:
        _logger.warning("model_proxy_debug_capture_unavailable")


async def _diagnostic_stream(body, *, run_id: str, attempt_id: str, operation: str):
    try:
        async for chunk in iterate_in_threadpool(iter(body)):
            yield chunk
    except Exception as exc:
        await _record_proxy_debug(run_id=run_id, attempt_id=attempt_id,
                                  error_code="model_upstream_unavailable", debug=getattr(exc, "debug", None), operation=operation)
        raise


class _OwnedModelStreamingResponse(StreamingResponse):
    def __init__(self, *args, close: Callable[[], None] | None, **kwargs):
        super().__init__(*args, **kwargs)
        self._close_model_stream = close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._close_model_stream is not None:
                # Synchronous cleanup has no cancellation checkpoint and does
                # not queue behind AnyIO's possibly occupied reader threads.
                close_model_stream(self._close_model_stream)


PrincipalDependency = Callable[..., Any]
AdminPredicate = Callable[[Any], bool]

class ModelConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_url: Any = None
    credential: Any = None
    deprecated_api_key: Any = Field(default=None, alias="api_key", exclude=True)


class ModelPublicationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    value: str
    display_name: str | None = Field(default=None, max_length=160)
    enabled: StrictBool
    order: StrictInt = Field(ge=1)
    auto_compact_window: StrictInt | None = Field(default=None, ge=100_000, le=1_000_000)
    max_output_tokens: StrictInt | None = Field(default=None, ge=1, le=10_000_000)


class ModelPublicationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_url: str = Field(min_length=1, max_length=2048)
    credential: Any = None
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    models: list[ModelPublicationEntry] = Field(min_length=1)


class ModelGroupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    remark: str = Field(default="", max_length=2000)
    sort_order: StrictInt = Field(default=0, ge=0)


class ModelGroupUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: StrictBool | None = None
    remark: str | None = Field(default=None, max_length=2000)
    sort_order: StrictInt | None = Field(default=None, ge=0)
    revision: StrictInt = Field(ge=1)
    confirm_impact: StrictBool = False


class ModelGroupRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)
    confirm_impact: StrictBool = False
    delete: StrictBool = False


class ModelGroupModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)
    models: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


class ModelGroupBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_ids: list[str] = Field(min_length=1, max_length=100)
    model_ids: list[str] = Field(min_length=1, max_length=1000)
    operation: Literal["add", "replace", "remove"]
    expected_group_revisions: dict[str, StrictInt]
    preview: StrictBool = False


class ModelGroupMembersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)
    members: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    confirm_default_fallback: StrictBool = False


class UserModelGroupsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_ids: list[str] = Field(default_factory=list, max_length=1000)
    primary_group_id: str | None = None
    expected_revision: StrictInt = Field(ge=1)
    confirm_default_fallback: StrictBool = False


class UserModelGroupsBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)
    group_ids: list[str] = Field(default_factory=list, max_length=1000)
    primary_group_id: str | None = None
    expected_revision: StrictInt = Field(ge=1)


class UserModelGroupsBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: list[UserModelGroupsBatchItem] = Field(min_length=1, max_length=1000)
    confirm_default_fallback: StrictBool = False


def _require_admin(principal: Any, *, is_admin: AdminPredicate) -> None:
    if not is_admin(principal):
        raise HTTPException(status_code=403, detail="model_admin_required")


def _translate_control_plane_error(exc: Exception) -> HTTPException:
    code = str(exc)
    if code in {
        "group_not_available",
        "group_disabled",
        "group_has_no_models",
        "model_not_available",
        "model_group_code_reserved",
        "model_group_name_invalid",
        "model_group_name_duplicate",
        "model_group_code_invalid",
        "model_group_type_invalid",
        "model_group_create_failed",
        "model_group_update_empty",
        "model_group_primary_required",
        "model_group_fallback_confirmation_required",
        "model_group_batch_invalid",
        "model_group_batch_revision_required",
        "model_group_model_parameters_invalid",
        "model_group_default_business_mutually_exclusive",
        "model_group_model_duplicate",
        "model_group_member_duplicate",
        "model_group_member_unknown_user",
        "model_group_duplicate",
        "model_group_primary_required",
        "model_group_member_identity_conflict",
        "model_group_member_identity_required",
        "company_identity_not_mapped",
        "company_identity_ambiguous",
        "model_group_impact_confirmation_required",
        "model_group_references_exist",
        "model_group_active_runs",
        "model_default_group_immutable",
        "model_connection_endpoint_invalid",

        "model_connection_endpoint_must_be_origin",
        "model_connection_endpoint_forbidden",
        "model_connection_https_required",
        "model_connection_api_key_invalid",
        "model_connection_api_key_required",
        "model_display_name_invalid",
        "model_user_access_invalid",
        "model_user_access_requires_users",
        "model_user_access_unknown_user",
        "model_default_must_be_available",
        "model_catalog_default_required",
        "model_user_access_retired",
        "model_catalog_order_invalid",
        "model_capacity_pair_required",
        "model_catalog_identity_collision",
        "auto_compact_window_invalid",
        "max_output_tokens_invalid",
    }:
        return HTTPException(status_code=422, detail=code)
    if code in {"model_catalog_revision_conflict", "model_catalog_discovery_changed", "model_group_revision_conflict", "model_policy_changed", "model_selection_conflict"}:
        return HTTPException(status_code=409, detail=code)
    if code in {"model_connection_authentication_failed"}:
        return HTTPException(status_code=400, detail=code)
    if code in {"model_connection_rate_limited"}:
        return HTTPException(status_code=429, detail=code)
    if code in {"company_employee_directory_forbidden"}:
        return HTTPException(status_code=403, detail=code)
    if code in {"model_group_member_unknown_user", "company_identity_not_mapped", "company_identity_ambiguous"}:
        return HTTPException(status_code=422, detail=code)
    if code in {"model_connection_encryption_key_invalid", "company_employee_directory_unavailable"}:
        return HTTPException(status_code=503, detail=code)
    return HTTPException(status_code=502, detail=code or "model_connection_unavailable")


async def admin_models(
    principal: Any,
    *,
    is_admin: AdminPredicate,
) -> dict[str, Any]:
    _require_admin(principal, is_admin=is_admin)
    return await configured_model_control_plane().admin_projection()


async def discover_model_catalog(
    payload: ModelConnectionRequest,
    principal: Any,
    *,
    is_admin: AdminPredicate,
) -> dict[str, Any]:
    _require_admin(principal, is_admin=is_admin)
    if payload.model_extra or "deprecated_api_key" in payload.model_fields_set:
        raise HTTPException(status_code=422, detail="model_connection_request_invalid")
    if (not isinstance(payload.base_url, str) or not payload.base_url
        or len(payload.base_url) > 2048 or
        (payload.credential is not None and
         (not isinstance(payload.credential, str) or len(payload.credential) > 4096))):
        raise HTTPException(status_code=422, detail="model_connection_request_invalid")
    try:
        return await configured_model_control_plane().discover(
            base_url=payload.base_url, api_key=payload.credential
        )
    except (RuntimeError, ValueError) as exc:
        raise _translate_control_plane_error(exc) from exc


async def publish_model_catalog(
    payload: ModelPublicationRequest,
    principal: Any,
    *,
    is_admin: AdminPredicate,
) -> dict[str, Any]:
    _require_admin(principal, is_admin=is_admin)
    if payload.model_extra or any(model.model_extra for model in payload.models):
        raise HTTPException(status_code=422, detail="model_publication_request_invalid")
    if payload.credential is not None and (
        not isinstance(payload.credential, str) or len(payload.credential) > 4096
    ):
        raise HTTPException(status_code=422, detail="model_connection_credential_field_invalid")
    try:
        return await configured_model_control_plane().publish(
            base_url=payload.base_url,
            api_key=payload.credential,
            expected_revision=payload.expected_revision,
            models=[model.model_dump() for model in payload.models],
            actor_user_id=principal.user_id,
            tenant_id=principal.tenant_id,
        )
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise _translate_control_plane_error(exc) from exc


async def proxy_model_request(
    provider: str,
    upstream_path: str,
    request: Request,
    x_ai_platform_run_id: str = Header(default=""),
    x_ai_platform_attempt_id: str = Header(default=""),
    x_ai_platform_internal_token: str = Header(default=""),
    x_ai_platform_model_authorization: str = Header(default=""),
    x_ai_platform_model_api_key: str = Header(default=""),
) -> Response:
    bearer_capability = ""
    scheme, separator, candidate = x_ai_platform_model_authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        bearer_capability = candidate.strip()
    api_key_capability = x_ai_platform_model_api_key.strip()
    model_proxy_capability = bearer_capability or api_key_capability
    if (
        bearer_capability
        and api_key_capability
        and not hmac.compare_digest(
            bearer_capability.encode("utf-8"), api_key_capability.encode("utf-8")
        )
    ):
        model_proxy_capability = ""
    body_buffer = bytearray()
    async for chunk in request.stream():
        if len(body_buffer) + len(chunk) > 1024 * 1024:
            raise HTTPException(status_code=413, detail="model_proxy_request_too_large")
        body_buffer.extend(chunk)
    body = bytes(body_buffer)
    try:
        raw_query = request.scope.get("query_string", b"")
        try:
            query = raw_query.decode("ascii")
        except UnicodeDecodeError as exc:
            raise PermissionError("model_proxy_query_not_allowed") from exc
        for header_name in ("anthropic-version", "anthropic-beta"):
            if len(request.headers.getlist(header_name)) > 1:
                raise PermissionError("model_proxy_header_duplicate")
        upstream = await configured_model_control_plane().proxy(
            provider=provider,
            upstream_path=upstream_path,
            query=query,
            body=body,
            headers=request.headers,
            run_id=x_ai_platform_run_id,
            attempt_id=x_ai_platform_attempt_id,
            internal_token=x_ai_platform_internal_token,
            model_proxy_capability=model_proxy_capability,
        )
    except PermissionError as exc:
        await _record_proxy_debug(run_id=x_ai_platform_run_id, attempt_id=x_ai_platform_attempt_id,
                                  error_code=str(exc), debug=getattr(exc, "debug", None),
                                  operation="count_tokens" if upstream_path.endswith("count_tokens") else "messages")
        # Keep denial diagnostics structured and bounded. Never include the
        # request body, credentials, or authorization headers in this record.
        _logger.warning(
            "model_proxy_denied",
            extra={
                "reason_code": str(exc),
                "provider": provider,
                "upstream_path": upstream_path,
                "run_id": x_ai_platform_run_id,
                "attempt_id": x_ai_platform_attempt_id,
            },
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        if str(exc) == "model_proxy_body_invalid":
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if str(exc) == "model_proxy_max_tokens_invalid":
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        await _record_proxy_debug(run_id=x_ai_platform_run_id, attempt_id=x_ai_platform_attempt_id,
                                  error_code=str(exc), debug=getattr(exc, "debug", None),
                                  operation="count_tokens" if upstream_path.endswith("count_tokens") else "messages")
        raise HTTPException(
            status_code=503 if str(exc) == "model_proxy_count_tokens_unavailable" else 502,
            detail=str(exc),
        ) from exc
    try:
        operation = "count_tokens" if upstream_path.endswith("count_tokens") else "messages"
        if upstream.status >= 400:
            await _record_proxy_debug(run_id=x_ai_platform_run_id, attempt_id=x_ai_platform_attempt_id,
                                      error_code="model_upstream_http_error", debug={
                                          **(getattr(upstream, "debug", None) or {}), "component": "model_proxy", "phase": "response",
                                          "failure_class": "http_error", "http_status": upstream.status,
                                      }, operation=operation)
        return _OwnedModelStreamingResponse(
            _diagnostic_stream(upstream.body, run_id=x_ai_platform_run_id, attempt_id=x_ai_platform_attempt_id, operation=operation),
            close=upstream.close,
            status_code=upstream.status,
            media_type=upstream.content_type.split(";", 1)[0],
            headers={"cache-control": "no-store"},
        )
    except BaseException:
        if upstream.close is not None:
            close_model_stream(upstream.close)
        raise


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

    @router.get("/models/groups")
    async def model_groups_endpoint(
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        groups = await configured_model_control_plane().groups_for_user(
            tenant_id=principal.tenant_id, user_id=principal.user_id,
        )
        return {"groups": groups, "count": len(groups), "version": "model-groups.v1"}

    @router.get("/admin/model-groups")
    async def admin_model_groups_endpoint(
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        groups = await configured_model_control_plane().admin_groups(
            tenant_id=principal.tenant_id,
        )
        return {"groups": groups, "version": "model-groups.v1"}

    @router.get("/admin/model-groups/{group_id}")
    async def admin_model_group_details_endpoint(
        group_id: str,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        details = await configured_model_control_plane().admin_group_details(
            tenant_id=principal.tenant_id, group_id=group_id,
        )
        if details is None:
            raise HTTPException(status_code=404, detail="group_not_available")
        return details

    @router.post("/admin/model-groups")
    async def create_model_group_endpoint(
        payload: ModelGroupCreateRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().create_model_group(
                tenant_id=principal.tenant_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.patch("/admin/model-groups/{group_id}")
    async def update_model_group_endpoint(
        group_id: str,
        payload: ModelGroupUpdateRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().update_model_group(
                tenant_id=principal.tenant_id, group_id=group_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.post("/admin/model-groups/{group_id}/retire")
    async def retire_model_group_endpoint(
        group_id: str,
        payload: ModelGroupRetireRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().archive_or_delete_model_group(
                tenant_id=principal.tenant_id, group_id=group_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.post("/admin/model-groups/models/batch")
    async def batch_model_group_models_endpoint(
        payload: ModelGroupBatchRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().batch_group_models(
                tenant_id=principal.tenant_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.put("/admin/model-groups/{group_id}/models")
    async def set_model_group_models_endpoint(
        group_id: str,
        payload: ModelGroupModelsRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().set_model_group_models(
                tenant_id=principal.tenant_id, group_id=group_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.get("/admin/users/{user_id}/model-groups")
    async def get_user_model_groups_endpoint(
        user_id: str,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        result = await configured_model_control_plane().user_group_memberships(
            tenant_id=principal.tenant_id, user_id=user_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="user_not_available")
        return result

    @router.put("/admin/users/model-groups/batch")
    async def set_users_model_groups_batch_endpoint(
        payload: UserModelGroupsBatchRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().set_users_group_memberships_batch(
                tenant_id=principal.tenant_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.put("/admin/users/{user_id}/model-groups")
    async def set_user_model_groups_endpoint(
        user_id: str,
        payload: UserModelGroupsRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().set_user_group_memberships(
                tenant_id=principal.tenant_id, user_id=user_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.put("/admin/model-groups/{group_id}/members")
    async def set_model_group_members_endpoint(
        group_id: str,
        payload: ModelGroupMembersRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        _require_admin(principal, is_admin=is_admin)
        try:
            return await configured_model_control_plane().set_model_group_members(
                tenant_id=principal.tenant_id, group_id=group_id, **payload.model_dump(),
            )
        except (ValueError, RuntimeError) as exc:
            raise _translate_control_plane_error(exc) from exc

    @router.post("/admin/models/discover")
    async def discover_model_catalog_endpoint(
        payload: ModelConnectionRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return await discover_model_catalog(payload, principal, is_admin=is_admin)

    @router.post("/admin/models/publish")
    async def publish_model_catalog_endpoint(
        payload: ModelPublicationRequest,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return await publish_model_catalog(payload, principal, is_admin=is_admin)

    router.add_api_route(
        "/internal/model-proxy/{provider}/{upstream_path:path}",
        proxy_model_request,
        methods=["POST"],
        include_in_schema=False,
    )
    return router
