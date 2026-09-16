"""Compose Execution application boundaries with concrete Harness adapters."""

from typing import Any

from app.execution.infrastructure.harness.claude.session_store import (
    ClaudeSessionStoreAdapter,
)


def build_claude_session_store(**kwargs: Any) -> ClaudeSessionStoreAdapter:
    return ClaudeSessionStoreAdapter(**kwargs)


__all__ = ["build_claude_session_store"]
