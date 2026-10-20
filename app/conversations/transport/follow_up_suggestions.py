"""Bounded HTTP translation for one settled follow-up-suggestion request."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.conversations.application.follow_up_suggestions import FollowUpTargetNotFound

_SAFE_ID = r"^[A-Za-z0-9_.:-]{1,128}$"


class FollowUpSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    run_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)


def build_follow_up_suggestion_router(
    *,
    resolve_suggestions: Callable[..., Awaitable[list[str]]],
    principal_dependency: Callable[..., Any],
) -> APIRouter:
    """Expose the owner-scoped use case; the browser cannot choose its model input."""

    router = APIRouter()

    @router.post("/chat/follow-up-suggestions")
    async def follow_up_suggestions(
        request: FollowUpSuggestionRequest,
        response: Response,
        principal: Any = Depends(principal_dependency),
    ) -> dict[str, list[str]]:
        try:
            suggestions = await resolve_suggestions(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
            )
        except FollowUpTargetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Questions derived from one private conversation are never shared caches.
        response.headers["Cache-Control"] = "private, no-store"
        return {"suggestions": suggestions}

    return router
