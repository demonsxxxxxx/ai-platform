"""Validated request shapes for explicit administrator evidence queries."""
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from typing import Any, Callable

from app.kernel.identifiers import assert_safe_id


class AdminEvidenceSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    match_offset: int = Field(default=0, ge=0, le=1100)
    after_sequence: int = Field(default=0, ge=0, le=2**63 - 1)
    attempt_id: str | None = Field(default=None, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    limit: int = Field(default=50, ge=1, le=100)


class AdminRunComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    right_run_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


def build_admin_inspection_router(*, principal_dependency: Callable[..., Any], is_admin: Callable[..., bool], transaction: Callable[..., Any], service_for: Callable[..., Any]) -> APIRouter:
    router = APIRouter()
    is_ai_admin = is_admin
    @router.post("/admin/runs/{run_id}/evidence-search")
    async def admin_run_evidence_search(run_id: str, body: AdminEvidenceSearchInput, request: Request,
                                        response: Response, principal: Any = Depends(principal_dependency)) -> dict[str, Any]:
        if not is_ai_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        try:
            run_id = assert_safe_id(run_id, "run_id")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        async with transaction() as conn:
            await conn.execute("set local statement_timeout = '5s'")
            result = await service_for(request).search_admin_evidence(
                conn, tenant_id=principal.tenant_id, run_id=run_id, **body.model_dump())
        if result is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        response.headers["Cache-Control"] = "private, no-store"
        return result

    @router.post("/admin/runs/{run_id}/comparison")
    async def admin_run_comparison(run_id: str, body: AdminRunComparisonInput, request: Request,
                                   response: Response, principal: Any = Depends(principal_dependency)) -> dict[str, Any]:
        if not is_ai_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        try:
            run_id = assert_safe_id(run_id, "run_id")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if run_id == body.right_run_id:
            raise HTTPException(status_code=400, detail="comparison_requires_distinct_runs")
        async with transaction() as conn:
            await conn.execute("set transaction isolation level repeatable read")
            await conn.execute("set local statement_timeout = '5s'")
            result = await service_for(request).compare_admin_runs(
                conn, tenant_id=principal.tenant_id, left_run_id=run_id, right_run_id=body.right_run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        response.headers["Cache-Control"] = "private, no-store"
        return result

    @router.get("/admin/runs/{run_id}/inspection")
    async def admin_run_inspection(
        run_id: str, request: Request, response: Response,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, Any]:
        if not is_ai_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        try:
            run_id = assert_safe_id(run_id, "run_id")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        async with transaction() as conn:
            await conn.execute("set local statement_timeout = '5s'")
            inspection = await service_for(request).read_admin_inspection(
                conn, tenant_id=principal.tenant_id, run_id=run_id,
            )
        if inspection is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        response.headers["Cache-Control"] = "no-store"
        return inspection

    return router
