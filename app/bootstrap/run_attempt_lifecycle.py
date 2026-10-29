"""Composition root for the Runs attempt lifecycle."""

from app.context.api import advance_unclaimed_terminal_generation
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.infrastructure import postgres as run_attempt_persistence
from app.bootstrap.attempt_settlement_projection import (
    build_attempt_settlement_projection_service,
)
from app.settings import get_settings


def build_run_attempt_lifecycle_service() -> RunAttemptLifecycleService:
    """Build one stateless service over the process-owned database runtime."""

    projection = build_attempt_settlement_projection_service()
    return RunAttemptLifecycleService(
        persistence=run_attempt_persistence,
        settlement_projection=projection,
        settlement_shadow_enabled=lambda: bool(
            get_settings().run_attempt_settlement_shadow_enabled
        ),
        finalize_terminal_transition=advance_unclaimed_terminal_generation,
    )
