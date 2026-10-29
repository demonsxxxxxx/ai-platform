"""Administrator-only, minimal employee projection for company identity selection."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.identity.application.company_employees import (
    CompanyEmployeeDirectoryService,
    CompanyEmployeeDirectoryUnavailable,
)


class CompanyEmployeeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    display_name: str
    department_ids: list[str]


class CompanyEmployeeListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employees: list[CompanyEmployeeResponse]
    total: int
    offset: int
    limit: int
    snapshot_id: str


def build_company_employees_router(
    *, service: CompanyEmployeeDirectoryService,
    principal_dependency: Callable[..., Any], is_admin: Callable[[Any], bool],
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/company-employees", response_model=CompanyEmployeeListResponse)
    async def list_company_employees(
        search: str = Query(default="", max_length=128),
        department_id: str = Query(default="", pattern=r"^(?:[1-9][0-9]{0,9})?$"),
        offset: int = Query(default=0, ge=0, le=50_000),
        limit: int = Query(default=50, ge=1, le=100),
        principal: Any = Depends(principal_dependency),
    ) -> dict:
        if not is_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        try:
            return await service.list_employees(
                tenant_id=principal.tenant_id, search=search, offset=offset, limit=limit,
                department_id=department_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except CompanyEmployeeDirectoryUnavailable:
            raise HTTPException(status_code=503, detail="company_employee_directory_unavailable") from None

    return router
