"""Administrator Run-scoped artifact metadata and content routes."""
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response

from app.artifacts.api import AdminArtifactAccessError, AdminRunArtifactAccess


def build_admin_run_artifact_router(*, access: AdminRunArtifactAccess, principal_dependency: Callable[..., Any],
                                    is_admin: Callable[..., bool], read_content: Callable[..., Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/runs/{run_id}/artifacts/{artifact_id}/{action}")
    async def admin_run_artifact(run_id: str, artifact_id: str, action: str,
                                 principal: Any = Depends(principal_dependency)) -> Response:
        if not is_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")
        if action not in {"access", "preview", "download"}:
            raise HTTPException(status_code=404, detail="artifact_not_found")
        if not all(0 < len(value) <= 160 and all(c.isascii() and (c.isalnum() or c in "_.:-") for c in value) for value in (run_id, artifact_id)):
            raise HTTPException(status_code=400, detail="invalid_artifact_identity")
        try:
            artifact = await access.resolve(tenant_id=principal.tenant_id, user_id=principal.user_id,
                                            run_id=run_id, artifact_id=artifact_id, action=action)
        except AdminArtifactAccessError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        if action == "access":
            from fastapi.responses import JSONResponse
            return JSONResponse(artifact, headers={"Cache-Control": "private, no-store"})
        return await read_content(artifact=artifact, action=action)

    return router
