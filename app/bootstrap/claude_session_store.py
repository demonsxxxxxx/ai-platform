"""Compose the Claude harness SessionStore receipt/observation adapter."""

from typing import Any

from app.execution.infrastructure.harness.claude.session_store_tracking import SessionStoreAppendTracker


def track_claude_session_store(store: Any | None) -> SessionStoreAppendTracker | None:
    return SessionStoreAppendTracker(store) if store is not None else None
