"""One authorized, deduplicated follow-up-question use case for a settled Run."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Awaitable, Callable, Protocol, Sequence

FOLLOW_UP_TURN_LIMIT = 12
FOLLOW_UP_CACHE_TTL_SECONDS = 600
FOLLOW_UP_CLAIM_TTL_SECONDS = 60
FOLLOW_UP_SHARED_WAIT_SECONDS = 2.4
FOLLOW_UP_SHARED_POLL_SECONDS = 0.2


class FollowUpSuggestionRepository(Protocol):
    async def authorized_target_run(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    async def authorized_run_turns(
        self, conn: Any, **kwargs: Any
    ) -> Sequence[tuple[str, str]]: ...


class FollowUpSuggestionCache(Protocol):
    async def read(self, key: str) -> list[str] | None: ...

    async def claim(self, key: str, *, ttl_seconds: int) -> bool: ...

    async def release(self, key: str) -> None: ...

    async def store(self, key: str, suggestions: Sequence[str], *, ttl_seconds: int) -> None: ...

class FollowUpSuggestionGenerator(Protocol):
    async def suggest(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        turns: Sequence[tuple[str, str]],
    ) -> Any: ...


class FollowUpTargetNotFound(LookupError):
    """The target Run is not an authorized, settled and still-current answer."""


def follow_up_cache_key(
    *, tenant_id: str, user_id: str, session_id: str, run_id: str
) -> str:
    """Bound one owner-scoped cache identity without exposing raw identifiers."""

    identity = "\n".join((tenant_id, user_id, session_id, run_id)).encode("utf-8")
    return f"ai-platform:chat:follow-up-suggestions:v1:{hashlib.sha256(identity).hexdigest()}"


def _suggestions(result: Any) -> list[str]:
    values = getattr(result, "suggestions", ()) or ()
    return [str(value) for value in values if isinstance(value, str) and value]


def build_follow_up_suggestions(
    *,
    repository: FollowUpSuggestionRepository,
    cache: FollowUpSuggestionCache,
    generator: FollowUpSuggestionGenerator,
    transaction_factory: Callable[[], Any],
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Callable[..., Awaitable[list[str]]]:
    """Return the use case bound to its persistence, cache and model ports."""

    async def await_shared_result(key: str) -> list[str]:
        attempts = int(FOLLOW_UP_SHARED_WAIT_SECONDS / FOLLOW_UP_SHARED_POLL_SECONDS)
        for _ in range(attempts):
            await sleep(FOLLOW_UP_SHARED_POLL_SECONDS)
            shared = await cache.read(key)
            if shared is not None:
                return shared
        return []

    async def resolve(
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> list[str]:
        async with transaction_factory() as conn:
            target = await repository.authorized_target_run(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
            )
            if target is None:
                raise FollowUpTargetNotFound("follow_up_target_not_found")
            turns = list(
                await repository.authorized_run_turns(
                    conn,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    limit=FOLLOW_UP_TURN_LIMIT,
                )
            )
        if not any(role == "assistant" for role, _text in turns):
            raise FollowUpTargetNotFound("follow_up_target_not_found")
        key = follow_up_cache_key(
            tenant_id=tenant_id, user_id=user_id, session_id=session_id, run_id=run_id
        )
        cached = await cache.read(key)
        if cached is not None:
            return cached
        if not await cache.claim(key, ttl_seconds=FOLLOW_UP_CLAIM_TTL_SECONDS):
            return await await_shared_result(key)
        try:
            suggestions = _suggestions(
                await generator.suggest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    turns=turns,
                )
            )
            if not suggestions:
                # A failed generation must not pin an empty answer for the TTL.
                await cache.release(key)
                return []
            await cache.store(key, suggestions, ttl_seconds=FOLLOW_UP_CACHE_TTL_SECONDS)
            return suggestions
        except BaseException:
            # The generator stops its transport before raising, so the paid request
            # is over and the claim can be handed on without overlapping work.
            await cache.release(key)
            raise

    return resolve
