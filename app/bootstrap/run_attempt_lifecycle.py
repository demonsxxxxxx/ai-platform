"""Composition root for the Runs attempt lifecycle."""

from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.infrastructure import postgres as run_attempt_persistence


def build_run_attempt_lifecycle_service() -> RunAttemptLifecycleService:
    """Build one stateless service over the process-owned database runtime."""

    return RunAttemptLifecycleService(persistence=run_attempt_persistence)
