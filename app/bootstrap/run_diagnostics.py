"""Composition root for Runs-owned private diagnostics."""

from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.infrastructure.diagnostics_postgres import PostgresRunDiagnosticsRepository
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)
from app.settings import get_settings


def build_run_diagnostics_service() -> RunDiagnosticsService:
    settings = get_settings()
    return RunDiagnosticsService(
        persistence=PostgresRunDiagnosticsRepository(
            write_timeout_seconds=settings.database_pool_timeout_seconds,
        ),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
