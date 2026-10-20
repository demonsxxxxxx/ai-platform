"""Compose the authorized follow-up-suggestion use case with its owning ports."""

from fastapi import APIRouter

from app.auth import require_principal
from app.conversations.application.follow_up_suggestions import (
    build_follow_up_suggestions,
)
from app.conversations.infrastructure import postgres as conversations_postgres
from app.conversations.infrastructure.follow_up_cache_redis import (
    RedisFollowUpSuggestionCache,
)
from app.conversations.infrastructure.follow_up_projection import (
    public_follow_up_turns,
)
from app.conversations.transport.follow_up_suggestions import (
    build_follow_up_suggestion_router,
)
from app.db import transaction
from app.execution.api import configured_follow_up_suggestions
from app.redis_client import get_redis_client


class _FollowUpSuggestionRepository:
    async def authorized_target_run(self, conn, **kwargs):
        return await conversations_postgres.authorized_follow_up_target_run(conn, **kwargs)

    async def authorized_run_turns(self, conn, **kwargs):
        rows = await conversations_postgres.authorized_follow_up_turns(conn, **kwargs)
        return public_follow_up_turns(rows)


def build_chat_follow_up_suggestion_router() -> APIRouter:
    resolve_suggestions = build_follow_up_suggestions(
        repository=_FollowUpSuggestionRepository(),
        cache=RedisFollowUpSuggestionCache(get_redis_client),
        generator=configured_follow_up_suggestions(),
        transaction_factory=transaction,
    )
    return build_follow_up_suggestion_router(
        resolve_suggestions=resolve_suggestions,
        principal_dependency=require_principal,
    )
