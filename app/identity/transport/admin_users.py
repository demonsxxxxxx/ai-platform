from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.identity.application.admin_user_diagnostics import (
    ADMIN_USER_DIAGNOSTICS_SCHEMA_VERSION,
    AdminUserDiagnosticsService,
)
PrincipalDependency = Callable[..., Any]
AdminPredicate = Callable[[Any], bool]


class AdminUserSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    display_name: str
    status: str
    created_at: Any | None = None
    session_count: int = 0
    run_count: int = 0
    queued_run_count: int = 0
    running_run_count: int = 0
    succeeded_run_count: int = 0
    failed_run_count: int = 0
    cancelled_run_count: int = 0
    last_activity_at: Any | None = None


class AdminUserListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ai-platform.admin-user-diagnostics.v1"] = (
        ADMIN_USER_DIAGNOSTICS_SCHEMA_VERSION
    )
    users: list[AdminUserSummaryResponse] = Field(default_factory=list)
    total: int
    offset: int
    limit: int


class AdminUserSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    workspace_id: str
    agent_id: str
    status: str
    purpose: str
    run_count: int = 0
    failed_run_count: int = 0
    last_run_at: Any | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class AdminUserRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str
    workspace_id: str
    status: str
    agent_id: str
    execution_kind: str
    skill_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: Any | None = None
    queued_at: Any | None = None
    started_at: Any | None = None
    finished_at: Any | None = None


class AdminUserAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    actor_user_id: str | None = None
    action: str
    target_type: str
    target_id: str
    relations: list[Literal["actor", "subject", "run", "session"]]
    run_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    created_at: Any | None = None


class AdminUserDiagnosticsLimitsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions: int
    runs: int
    audit: int


class AdminUserDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ai-platform.admin-user-diagnostics.v1"] = (
        ADMIN_USER_DIAGNOSTICS_SCHEMA_VERSION
    )
    user: AdminUserSummaryResponse
    sessions: list[AdminUserSessionResponse] = Field(default_factory=list)
    runs: list[AdminUserRunResponse] = Field(default_factory=list)
    audit: list[AdminUserAuditResponse] = Field(default_factory=list)
    limits: AdminUserDiagnosticsLimitsResponse


def build_admin_users_router(
    *,
    service: AdminUserDiagnosticsService,
    principal_dependency: PrincipalDependency,
    is_admin: AdminPredicate,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/users", response_model=AdminUserListResponse)
    async def list_admin_users(
        search: str | None = Query(default=None, max_length=128),
        offset: int = Query(default=0, ge=0, le=10_000),
        limit: int = Query(default=50, ge=1, le=100),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        if not is_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        normalized_search = search.strip() if search and search.strip() else None
        return await service.list_users(
            tenant_id=principal.tenant_id,
            search=normalized_search,
            offset=offset,
            limit=limit,
        )

    @router.get(
        "/admin/users/{user_id}/diagnostics",
        response_model=AdminUserDiagnosticsResponse,
    )
    async def admin_user_diagnostics(
        user_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@+\-]*$",
        ),
        session_limit: int = Query(default=20, ge=1, le=50),
        run_limit: int = Query(default=50, ge=1, le=100),
        audit_limit: int = Query(default=50, ge=1, le=100),
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        if not is_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        if ".." in user_id:
            raise HTTPException(
                status_code=400,
                detail="user_id contains unsupported characters",
            )
        result = await service.get_diagnostics(
            tenant_id=principal.tenant_id,
            user_id=user_id,
            session_limit=session_limit,
            run_limit=run_limit,
            audit_limit=audit_limit,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="user_not_found")
        return result

    return router


__all__ = ["build_admin_users_router"]
