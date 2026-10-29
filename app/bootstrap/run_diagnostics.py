"""Composition root for Runs-owned private diagnostics."""

import logging

from app.db import transaction
from app.execution.events import normalize_execution_debug
from app.platform.tracing import safe_exception_log_fields
from app.execution.api import public_tool_operation_id
from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.infrastructure.diagnostics_postgres import PostgresRunDiagnosticsRepository
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)
from app.bootstrap.sandbox_lease_ports import build_sandbox_run_lease_reader
from app.settings import get_settings

_logger = logging.getLogger(__name__)


async def record_execution_debug(*, run_id: str, attempt_id: str, error_code: str, debug: object) -> None:
    """Best-effort private evidence; never alter a proxy/callback result."""
    try:
        async with transaction() as conn:
            await build_run_diagnostics_service().capture_execution_debug(
                conn, run_id=run_id, attempt_id=attempt_id, error_code=error_code,
                debug=normalize_execution_debug({
                    "api_runtime_commit": get_settings().ai_platform_runtime_commit,
                    **normalize_execution_debug(debug),
                }),
            )
    except Exception:
        _logger.warning("execution_debug_capture_unavailable")


def build_run_diagnostics_service() -> RunDiagnosticsService:
    settings = get_settings()
    return RunDiagnosticsService(
        exception_log_fields=safe_exception_log_fields,
        persistence=PostgresRunDiagnosticsRepository(
            write_timeout_seconds=settings.database_pool_timeout_seconds,
        ),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        tool_operation_id=public_tool_operation_id,
        runtime_debug_reader=build_sandbox_run_lease_reader().read_run_runtime_debug,
        query_runtime_commit=settings.ai_platform_runtime_commit,
    )
