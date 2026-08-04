import asyncio
import os
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import apply_schema, transaction
from app.queue import get_queue_status, get_redis
from app.settings import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "runtime_commit": os.environ.get("AI_PLATFORM_RUNTIME_COMMIT", "unknown"),
    }


async def _probe_postgresql() -> None:
    async with transaction() as conn:
        await conn.execute("select 1")


async def _probe_redis() -> None:
    redis = await get_redis()
    try:
        if not await redis.ping():
            raise RuntimeError("redis_ping_failed")
    finally:
        await redis.aclose()


async def _dependency_status(
    probe: Callable[[], Awaitable[None]],
    *,
    timeout_seconds: float,
) -> str:
    try:
        await asyncio.wait_for(probe(), timeout=timeout_seconds)
    except Exception:
        return "unavailable"
    return "ok"


@router.get("/ready")
async def readiness() -> JSONResponse:
    settings = get_settings()
    timeout_seconds = float(settings.datastore_readiness_timeout_seconds)
    postgresql, redis = await asyncio.gather(
        _dependency_status(_probe_postgresql, timeout_seconds=timeout_seconds),
        _dependency_status(_probe_redis, timeout_seconds=timeout_seconds),
    )
    ready = postgresql == redis == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "runtime_commit": os.environ.get("AI_PLATFORM_RUNTIME_COMMIT", "unknown"),
            "dependencies": {
                "postgresql": postgresql,
                "redis": redis,
            },
        },
    )


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
