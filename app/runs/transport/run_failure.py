"""Translate permanent queue rejection into the existing Runs terminal owner."""

from __future__ import annotations

from typing import Any, Callable


async def compensate_queue_rejection(
    *,
    principal: Any,
    run_id: str,
    trace_id: str,
    diagnostic_error: BaseException | None,
    lifecycle: Any,
    capabilities: Any,
    run_diagnostics: Any | None,
    transaction: Callable[..., Any],
    terminalize: Callable[..., Any],
) -> None:
    async with transaction() as conn:
        await terminalize(
            capabilities,
            conn,
            lifecycle=lifecycle,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
            trace_id=trace_id,
            diagnostic_error=diagnostic_error,
            run_diagnostics=run_diagnostics,
        )
