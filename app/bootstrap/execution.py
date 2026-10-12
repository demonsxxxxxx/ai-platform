"""Compose Execution application boundaries with concrete Harness adapters."""

from typing import Any

from app.execution.infrastructure.harness.claude.session_store import (
    ClaudeSessionStoreAdapter,
)
from app.context.transport.provider_session_responses import (
    ProviderSessionCallbackResponse as ProviderSessionCallbackResponse,
    ProviderSessionCallbackWireResponse as ProviderSessionCallbackWireResponse,
    build_provider_session_callback_response as build_provider_session_callback_response,
)


def build_claude_session_store(**kwargs: Any) -> ClaudeSessionStoreAdapter:
    return ClaudeSessionStoreAdapter(**kwargs)


__all__ = [
    "build_claude_session_store",
    "ProviderSessionCallbackResponse",
    "ProviderSessionCallbackWireResponse",
    "build_provider_session_callback_response",
]
