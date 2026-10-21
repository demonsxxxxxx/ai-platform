"""Administrator-only actual-model usage read API."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction

router = APIRouter()


async def _query(request, principal, **kwargs):
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    try:
        async with transaction() as conn:
            return await request.app.state.model_token_usage_service.query(conn, tenant_id=principal.tenant_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="usage_date_range_invalid") from exc


@router.get("/admin/usage/users")
async def users(request: Request, start_date: date | None = None, end_date: date | None = None,
                q: str | None = Query(None, max_length=160), limit: int = Query(50, ge=1, le=100),
                offset: int = Query(0, ge=0), principal: AuthPrincipal = Depends(require_principal)):
    return await _query(request, principal, start_date=start_date, end_date=end_date, q=q, limit=limit, offset=offset)


@router.get("/admin/usage/users/{user_id}/models")
async def models(request: Request, user_id: str, start_date: date | None = None, end_date: date | None = None,
                 principal: AuthPrincipal = Depends(require_principal)):
    return await _query(request, principal, start_date=start_date, end_date=end_date, user_id=user_id)
