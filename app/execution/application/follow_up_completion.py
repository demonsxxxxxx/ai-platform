"""One bounded, tool-free follow-up-question completion over the governed model."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from app.execution.domain.follow_up_suggestions import (
    FOLLOW_UP_MAX_OUTPUT_TOKENS,
    FollowUpSuggestionError,
    build_follow_up_messages,
    parse_follow_up_suggestions,
)

FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS = 20.0
FOLLOW_UP_COMPLETION_MAX_RESPONSE_BYTES = 64 * 1024
# Bounded wait for the cancelled transport to release its socket before the caller
# may treat the paid request as finished.
FOLLOW_UP_COMPLETION_CLOSE_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class FollowUpCompletionConnection:
    """One already-authorized governed model endpoint for a single completion."""

    revision: int
    model_value: str
    base_url: str
    api_key: str
    auto_compact_window: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class FollowUpSuggestionResult:
    """Public, provider-agnostic completion outcome."""

    suggestions: tuple[str, ...] = ()
    reason: str = "ok"


class FollowUpCompletionRepository(Protocol):
    async def run_model_completion_connection(
        self, conn: Any, **kwargs: Any
    ) -> Any: ...


class FollowUpModelUpstream(Protocol):
    async def request_async(self, **kwargs: Any) -> Any: ...


class FollowUpSuggestionService:
    """Generates suggestions from public turns without creating a Run or tool call."""
    def __init__(
        self,
        *,
        transaction_factory: Callable[[], Any],
        repository: FollowUpCompletionRepository,
        upstream: FollowUpModelUpstream,
        settings_provider: Any,
    ) -> None:
        self._transaction = transaction_factory
        self._repository = repository
        self._upstream = upstream
        self._settings_provider = settings_provider

    async def suggest(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        turns: Sequence[tuple[str, str]],
    ) -> FollowUpSuggestionResult:
        try:
            messages = build_follow_up_messages(turns)
        except FollowUpSuggestionError as exc:
            return FollowUpSuggestionResult(reason=str(exc))
        try:
            connection = await self._connection(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
            )
        except Exception:  # noqa: BLE001 - an unusable pinned connection yields no suggestion
            return FollowUpSuggestionResult(reason="connection_failed")
        if connection is None:
            return FollowUpSuggestionResult(reason="model_unavailable")
        try:
            content = await self._complete(connection, messages=messages)
            return FollowUpSuggestionResult(
                suggestions=tuple(parse_follow_up_suggestions(content))
            )
        except (FollowUpSuggestionError, asyncio.TimeoutError):
            return FollowUpSuggestionResult(reason="completion_invalid")
        except Exception:  # noqa: BLE001 - provider failures stay a bounded empty result
            return FollowUpSuggestionResult(reason="completion_failed")

    async def _connection(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> FollowUpCompletionConnection | None:
        encryption_key = str(
            getattr(self._settings_provider(), "model_connection_encryption_key", "") or ""
        )
        if not encryption_key:
            return None
        async with self._transaction() as conn:
            active = await self._repository.run_model_completion_connection(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                encryption_key=encryption_key,
            )
        if active is None or not active.model_value or not active.api_key:
            return None
        return FollowUpCompletionConnection(
            revision=active.revision,
            model_value=active.model_value,
            base_url=active.base_url,
            api_key=active.api_key,
            auto_compact_window=active.auto_compact_window,
            max_output_tokens=active.max_output_tokens,
        )

    async def _complete(
        self,
        connection: FollowUpCompletionConnection,
        *,
        messages: list[dict[str, str]],
    ) -> str:
        settings = self._settings_provider()
        body = json.dumps(
            {
                "model": connection.model_value,
                "messages": messages,
                "max_tokens": min(
                    FOLLOW_UP_MAX_OUTPUT_TOKENS,
                    connection.max_output_tokens or FOLLOW_UP_MAX_OUTPUT_TOKENS,
                ),
                "temperature": 0.3,
                "stream": False,
                "n": 1,
                "tools": [],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = asyncio.ensure_future(
            self._upstream.request_async(
                base_url=connection.base_url,
                allowed_internal_hosts=str(
                    getattr(settings, "model_connection_allowed_internal_hosts", "") or ""
                ),
                api_key=connection.api_key,
                method="POST",
                path="/v1/chat/completions",
                provider="openai",
                body=body,
                headers={"content-type": "application/json"},
                timeout_seconds=FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS,
                max_response_bytes=FOLLOW_UP_COMPLETION_MAX_RESPONSE_BYTES,
            )
        )
        try:
            response = await asyncio.wait_for(
                asyncio.shield(request), timeout=FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # The transport must stop before the caller can release the paid slot.
            await self._stop_request(request)
            raise
        except BaseException:
            await self._stop_request(request)
            raise
        if getattr(response, "status", None) != 200:
            raise FollowUpSuggestionError("follow_up_completion_status_invalid")
        try:
            payload = json.loads(response.body)
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise FollowUpSuggestionError("follow_up_completion_body_invalid") from exc
        if not isinstance(content, str) or not content.strip():
            raise FollowUpSuggestionError("follow_up_completion_body_invalid")
        return content

    @staticmethod
    async def _stop_request(request: "asyncio.Future[Any]") -> None:
        """Cancel the transport and wait, bounded, for it to release its socket."""

        if request.done():
            # Retrieve the outcome so a failed shielded call is not reported twice.
            with suppress(BaseException):
                request.result()
            return
        request.cancel()
        with suppress(BaseException):
            await asyncio.wait_for(request, timeout=FOLLOW_UP_COMPLETION_CLOSE_GRACE_SECONDS)


_service: FollowUpSuggestionService | None = None


def configure_follow_up_suggestions(service: FollowUpSuggestionService) -> None:
    global _service
    _service = service


def configured_follow_up_suggestions() -> FollowUpSuggestionService:
    if _service is None:
        raise RuntimeError("follow_up_suggestion_service_not_configured")
    return _service
