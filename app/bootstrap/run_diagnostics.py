"""Composition root for Runs-owned private diagnostics."""

from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.infrastructure.diagnostics_postgres import PostgresRunDiagnosticsRepository
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)


def build_run_diagnostics_service() -> RunDiagnosticsService:
    return RunDiagnosticsService(
        persistence=PostgresRunDiagnosticsRepository(),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
