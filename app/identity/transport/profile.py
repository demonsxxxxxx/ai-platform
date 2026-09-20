from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.identity.application.profile_metadata import (
    ProfileMetadataScopeError,
    ProfileMetadataService,
    ProfileMetadataValidationError,
)

PrincipalDependency = Callable[..., Any]


def _profile_payload(
    principal: Any,
    metadata: dict[str, Any],
) -> dict[str, object]:
    merged_metadata = dict(metadata)
    merged_metadata.update(
        {
            "display_name": principal.display_name,
            "source": principal.source,
        }
    )
    return {
        "id": principal.user_id,
        "username": principal.user_id,
        "email": "",
        "avatar_url": None,
        "roles": principal.roles,
        "permissions": principal.permissions,
        "is_active": True,
        "metadata": merged_metadata,
        "created_at": "",
        "updated_at": "",
    }


def build_profile_router(
    *,
    service: ProfileMetadataService,
    principal_dependency: PrincipalDependency,
) -> APIRouter:
    router = APIRouter()

    @router.get("/auth/profile")
    async def profile(
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, object]:
        try:
            metadata = await service.get(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
        except ProfileMetadataScopeError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return _profile_payload(principal, metadata)

    @router.put("/auth/profile/metadata")
    async def update_profile_metadata(
        payload: dict[str, Any],
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, object]:
        try:
            metadata = await service.merge(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
                patch=payload.get("metadata"),
            )
        except ProfileMetadataValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProfileMetadataScopeError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return _profile_payload(principal, metadata)

    return router
