"""Worker-owned failure projection boundary."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from app import repositories
from app.execution.api import (
    context_file_failure_event_fields,
    context_file_failure_event_payload,
    context_file_failure_log_extra,
    validated_context_file_diagnostic,
)


logger = logging.getLogger(__name__)


async def persist_worker_failure_event(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    result: Any,
    attempt_id: str,
    trace_id: str,
    error_code: str,
) -> None:
    """Persist one hidden terminal event while retaining only validated diagnostics."""

    executor_payload = result.executor_payload if isinstance(result.executor_payload, Mapping) else {}
    diagnostic = validated_context_file_diagnostic(executor_payload)
    if diagnostic is not None:
        logger.error(
            "Context file preprocessing failed",
            extra=context_file_failure_log_extra(
                diagnostic,
                run_id=run_id,
                attempt_id=attempt_id,
                trace_id=trace_id,
            ),
        )
    await repositories.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type="error",
        stage="worker",
        message="Run failed",
        payload={
            "artifact_count": len(result.artifacts),
            "visible_to_user": False,
            **context_file_failure_event_payload(diagnostic),
        },
        **context_file_failure_event_fields(
            diagnostic,
            trace_id=trace_id,
            error_code=error_code,
        ),
    )


__all__ = ["persist_worker_failure_event"]
