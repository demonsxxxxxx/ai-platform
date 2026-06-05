from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.agent_apps import router as agent_apps_router
from app.routes.admin_runtime import router as admin_runtime_router
from app.routes.admin_runs import router as admin_runs_router
from app.routes.admin_skills import router as admin_skills_router
from app.routes.admin_tool_policies import router as admin_tool_policies_router
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.context import router as context_router
from app.routes.files import router as files_router
from app.routes.health import router as health_router
from app.routes.lambchat_compat import router as lambchat_compat_router
from app.routes.runtime_callbacks import router as runtime_callbacks_router
from app.routes.runs import router as runs_router
from app.routes.sandbox_leases import router as sandbox_leases_router
from app.routes.tool_permissions import router as tool_permissions_router
from app.settings import get_settings


def _cors_origins(raw_value: str) -> list[str]:
    origins = [item.strip().rstrip("/") for item in raw_value.split(",") if item.strip()]
    if not origins:
        return []
    if "*" in origins:
        raise RuntimeError("cors_wildcard_not_allowed_with_credentials")
    return origins


def create_app() -> FastAPI:
    app = FastAPI(title="AI Platform API", version="0.1.0")
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router, prefix="/api/ai")
    app.include_router(auth_router, prefix="/api/ai")
    app.include_router(agent_apps_router, prefix="/api/ai")
    app.include_router(chat_router, prefix="/api/ai")
    app.include_router(context_router, prefix="/api/ai")
    app.include_router(files_router, prefix="/api/ai")
    app.include_router(runs_router, prefix="/api/ai")
    app.include_router(tool_permissions_router, prefix="/api/ai")
    app.include_router(sandbox_leases_router, prefix="/api/ai")
    app.include_router(runtime_callbacks_router, prefix="/api/ai")
    app.include_router(admin_runtime_router, prefix="/api/ai")
    app.include_router(admin_runs_router, prefix="/api/ai")
    app.include_router(admin_skills_router, prefix="/api/ai")
    app.include_router(admin_tool_policies_router, prefix="/api/ai")
    app.include_router(lambchat_compat_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    return app
