"""Administrator-only actual-model usage read API."""
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.runs.application.model_token_usage import ModelTokenUsageService


def build_model_token_usage_router(
    *, service: ModelTokenUsageService,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]],
    principal_dependency: Callable[..., Any], is_admin: Callable[[Any], bool],
) -> APIRouter:
    router = APIRouter()

    async def query(principal: Any, **kwargs):
        if not is_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        try:
            async with transaction_factory() as conn:
                return await service.query(conn, tenant_id=principal.tenant_id, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="usage_date_range_invalid") from exc

    @router.get("/admin/usage/users")
    async def users(
        start_date: date | None = None, end_date: date | None = None,
        q: str | None = Query(None, max_length=160), limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0), principal: Any = Depends(principal_dependency),
    ):
        return await query(principal, start_date=start_date, end_date=end_date, q=q, limit=limit, offset=offset)

    @router.get("/admin/usage/users/{user_id}/models")
    async def models(
        user_id: str, start_date: date | None = None, end_date: date | None = None,
        principal: Any = Depends(principal_dependency),
    ):
        return await query(principal, start_date=start_date, end_date=end_date, user_id=user_id)

    return router
