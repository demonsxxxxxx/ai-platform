"""Authenticated browser observations for an already-admitted chat submission."""
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.conversations.application.submission_failures import (
    new_submission_diagnostic_id,
    submission_error_detail,
)
from app.runs.api import BROWSER_LATENCY_MAX_MS, RunDiagnosticsService


class BrowserRunLatencyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    duration_ms: int = Field(strict=True, ge=0, le=BROWSER_LATENCY_MAX_MS)


def build_submission_latency_router(
    *, resolve_submission: Callable[..., Awaitable[Any]],
    transaction_factory: Callable[..., Any], principal_dependency: Callable[..., Any],
    log_exception: Callable[..., None],
) -> APIRouter:
    class SubmissionLatencyRoute(APIRoute):
        def get_route_handler(self):  # type: ignore[override]
            handler = super().get_route_handler()

            async def no_store(request: Request):
                try:
                    response = await handler(request)
                except HTTPException as exc:
                    response = await http_exception_handler(request, exc)
                except RequestValidationError:
                    response = JSONResponse({"detail": "invalid_browser_latency"}, status_code=422)
                except Exception as exc:
                    diagnostic_id = new_submission_diagnostic_id()
                    log_exception(phase="resolver", diagnostic_id=diagnostic_id, exc=exc)
                    response = JSONResponse({"detail": submission_error_detail(
                        code="chat_submission_internal_error", rejected_before_persist=False,
                        diagnostic_id=diagnostic_id,
                    )}, status_code=500)
                response.headers["Cache-Control"] = "private, no-store"
                return response

            return no_store

    router = APIRouter(route_class=SubmissionLatencyRoute)

    @router.post("/chat/submissions/{submission_id}/latency")
    async def record_chat_submission_latency(
        submission_id: UUID, observation: BrowserRunLatencyInput, request: Request,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, bool]:
        resolved = await resolve_submission(principal=principal, submission_id=str(submission_id))
        run_id = resolved.outcome.run_id if resolved and resolved.outcome and resolved.run_status else None
        if run_id is None or run_id != observation.run_id:
            raise HTTPException(status_code=404, detail="chat_submission_not_found")
        diagnostics = getattr(request.app.state, "run_diagnostics_service", None)
        if not isinstance(diagnostics, RunDiagnosticsService):
            raise RuntimeError("run_diagnostics_service_unavailable")
        async with transaction_factory() as conn:
            await diagnostics.record_browser_latency(
                conn, tenant_id=principal.tenant_id, run_id=run_id, duration_ms=observation.duration_ms,
            )
        return {"recorded": True}

    return router
