"""Durable terminalization for committed runs rejected at queue admission."""

from __future__ import annotations

from psycopg import AsyncConnection

from app import repositories
from app.run_admission_policy import (
    PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON,
)


async def terminalize_retired_platform_multi_agent_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> repositories.ToolPermissionTerminalizationProgress:
    """Stage a retired-control failure through the durable terminalization lifecycle."""

    error_message = "Platform multi-agent orchestration is no longer supported."
    progress = await repositories.fail_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
        error_message=error_message,
        result_json={"message": error_message, "retryable": False},
        terminal_reason=RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON,
    )
    return progress
