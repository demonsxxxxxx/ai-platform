import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap.agent_profiles import configure_agent_profile_routes
from app.bootstrap.context import configure_context_services
from app.bootstrap.files import (
    configure_file_preview_services,
    configure_file_upload_services,
)
from app.bootstrap.model_token_usage import build_model_token_usage_service, build_model_token_usage_admin_router
from app.bootstrap.identity import (
    build_admin_users_router, build_identity_profile_router, build_company_employee_directory_router,
)
from app.bootstrap.model_services import (
    build_model_management_router,
    configure_model_services,
)
from app.bootstrap.follow_up_suggestions import (
    build_chat_follow_up_suggestion_router,
)
from app.bootstrap.mcp import configure_mcp_runtime
from app.bootstrap.run_lifecycle import (
    build_run_cancellation_use_case,
    build_run_lifecycle_service,
)
from app.bootstrap.run_attempt_lifecycle import build_run_attempt_lifecycle_service
from app.bootstrap.run_diagnostics import build_run_diagnostics_service
from app.bootstrap.run_inputs import build_run_inputs_service
from app.bootstrap.skills import configure_skill_services
from app.bootstrap.submission_latency import build_chat_submission_latency_router
from app.bootstrap.streaming import RunStreamRuntime, build_run_stream_runtime
from app.db import close_pool, transaction
from app.redis_client import close_redis_client
from app.routes.agent_profiles import (
    configure_agent_profile_favorites,
    router as agent_profiles_router,
)
from app.routes.admin_runtime import router as admin_runtime_router
from app.routes.admin_runs import router as admin_runs_router
from app.routes.admin_skills import router as admin_skills_router
from app.routes.admin_tool_policies import router as admin_tool_policies_router
from app.routes.auth import router as auth_router
from app.routes.browser_runtime_config import router as browser_runtime_config_router
from app.routes.chat import router as chat_router
from app.routes.chat import submission_router as chat_submission_router
from app.routes.chat_sessions import router as chat_sessions_router
from app.routes.capability_distributions import router as capability_distributions_router
from app.routes.context import router as context_router
from app.routes.files import router as files_router
from app.routes.frontend_projections import router as frontend_projections_router
from app.routes.health import router as health_router
from app.routes.lambchat_compat import router as lambchat_compat_router
from app.routes.mcp import router as mcp_router
from app.routes.role_governance import router as role_governance_router
from app.routes.runtime_callbacks import router as runtime_callbacks_router
from app.routes.runs import router as runs_router
from app.routes.sandbox_leases import router as sandbox_leases_router
from app.routes.skills_marketplace import router as skills_marketplace_router
from app.routes.workbench_projections import router as workbench_projections_router
from app.platform.tracing import configure_terminal_stage_logging
from app.settings import get_settings


logger = logging.getLogger(__name__)
_API_RESOURCE_CLOSE_TIMEOUT_SECONDS = 30.0


async def _close_api_runtime(
    runtime: RunStreamRuntime, *, primary_error: BaseException | None
) -> None:
    """Join finite resource cleanup despite repeated lifespan cancellation."""

    async def close() -> None:
        first_error: BaseException | None = None
        for phase, operation in (
            ("stream_runtime", runtime.aclose),
            ("redis_client", close_redis_client),
            ("database_pool", close_pool),
        ):
            try:
                budget = asyncio.timeout(_API_RESOURCE_CLOSE_TIMEOUT_SECONDS)
                async with budget:
                    await operation()
                # A close that settles after suppressing deadline cancellation
                # still exceeded its budget; do not report timely success.
                if budget.expired():
                    raise TimeoutError()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                try:
                    logger.warning(
                        "API resource close failed",
                        extra={
                            "cleanup_phase": phase,
                            "cleanup_error_type": type(exc).__name__,
                        },
                    )
                except Exception:
                    # Diagnostics must not interrupt resource settlement.
                    pass
        if first_error is not None:
            raise first_error

    # Budgets are cooperative: cancellation must settle before the next phase.
    # Keep this owner until close ends, rather than leaving a shielded orphan.
    cancellation: asyncio.CancelledError | None = None
    try:
        # Deliver any caller cancellation requested just before teardown. A
        # cancelling() baseline alone cannot distinguish pending from handled.
        await asyncio.sleep(0)
    except asyncio.CancelledError as exc:
        cancellation = exc
    caller = asyncio.current_task()
    initial_cancellations = caller.cancelling() if caller is not None else 0
    cleanup_task = asyncio.create_task(close(), name="ai-platform-api-shutdown")
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            # Both tasks can become cancelled before this await resumes.
            # A resource self-cancel must not hide a concurrent caller cancel.
            caller_cancelled = caller is not None and caller.cancelling() > initial_cancellations
            if not cleanup_task.cancelled() or caller_cancelled:
                cancellation = cancellation or exc
        except BaseException:
            break
    try:
        cleanup_task.result()
    except BaseException:
        if primary_error is None and cancellation is None:
            raise
    if primary_error is None and cancellation is not None:
        raise cancellation


def _cors_origins(raw_value: str) -> list[str]:
    origins = [item.strip().rstrip("/") for item in raw_value.split(",") if item.strip()]
    if not origins:
        return []
    if "*" in origins:
        raise RuntimeError("cors_wildcard_not_allowed_with_credentials")
    return origins


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.run_lifecycle = build_run_lifecycle_service()
    run_stream_runtime = build_run_stream_runtime(
        transaction,
        app.state.run_lifecycle,
    )
    app.state.run_stream_runtime = run_stream_runtime
    app.state.run_cancellation_use_case = build_run_cancellation_use_case(
        attempt_lifecycle=app.state.run_attempt_lifecycle,
        lifecycle=app.state.run_lifecycle,
    )
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        await _close_api_runtime(run_stream_runtime, primary_error=primary_error)


def create_app() -> FastAPI:
    configure_terminal_stage_logging()
    configure_file_upload_services()
    configure_file_preview_services()
    configure_context_services()
    configure_mcp_runtime()
    configure_model_services()
    configure_skill_services()
    configure_agent_profile_routes(configure_agent_profile_favorites)
    app = FastAPI(title="AI Platform API", version="0.1.0", lifespan=lifespan)
    app.state.run_attempt_lifecycle = build_run_attempt_lifecycle_service()
    app.state.run_diagnostics_service = build_run_diagnostics_service()
    app.state.run_inputs_service = build_run_inputs_service()
    app.state.model_token_usage_service = build_model_token_usage_service()
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
    app.include_router(agent_profiles_router, prefix="/api/ai")
    app.include_router(chat_sessions_router, prefix="/api/ai")
    app.include_router(chat_router, prefix="/api/ai")
    submission_latency_router = build_chat_submission_latency_router()
    follow_up_suggestion_router = build_chat_follow_up_suggestion_router()
    app.include_router(follow_up_suggestion_router, prefix="/api/ai")
    app.include_router(context_router, prefix="/api/ai")
    app.include_router(files_router, prefix="/api/ai")
    app.include_router(runs_router, prefix="/api/ai")
    app.include_router(sandbox_leases_router, prefix="/api/ai")
    app.include_router(runtime_callbacks_router, prefix="/api/ai")
    app.include_router(admin_runtime_router, prefix="/api/ai")
    app.include_router(admin_runs_router, prefix="/api/ai")
    app.include_router(build_model_token_usage_admin_router(app.state.model_token_usage_service), prefix="/api/ai")
    app.include_router(build_admin_users_router(), prefix="/api/ai")
    app.include_router(build_company_employee_directory_router(), prefix="/api/ai")
    app.include_router(admin_skills_router, prefix="/api/ai")
    app.include_router(admin_tool_policies_router, prefix="/api/ai")
    app.include_router(build_model_management_router(), prefix="/api/ai")
    app.include_router(capability_distributions_router, prefix="/api")
    app.include_router(skills_marketplace_router, prefix="/api")
    app.include_router(browser_runtime_config_router, prefix="/api")
    app.include_router(frontend_projections_router, prefix="/api")
    app.include_router(role_governance_router, prefix="/api")
    app.include_router(workbench_projections_router, prefix="/api")
    app.include_router(build_identity_profile_router(), prefix="/api")
    app.include_router(lambchat_compat_router, prefix="/api")
    app.include_router(mcp_router, prefix="/api")
    # Chat session reads stay under /api/ai; submission and stream admission
    # keep the /api paths the browser and live diagnostics call.
    app.include_router(chat_submission_router, prefix="/api")
    app.include_router(submission_latency_router, prefix="/api")
    return app
