from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import apply_schema
from app.queue import get_queue_status

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/admin/status")
async def admin_status(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, object]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    return {
        "status": "ok",
        "queue": await get_queue_status(),
    }


@router.post("/admin/apply-schema")
async def admin_apply_schema(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, str]:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    await apply_schema()
    return {"status": "schema_applied"}
