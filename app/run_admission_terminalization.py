"""Durable terminalization for committed runs rejected at queue admission."""

from __future__ import annotations

from psycopg import AsyncConnection

from app.runs.api import RunTerminalizationProgress
from app.streaming.api import WorkerV4Capabilities
from app.tool_permission_lifecycle import fail_run_with_v4
from app.run_admission_policy import (
    PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
    RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON,
)


async def terminalize_retired_platform_multi_agent_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    v4_capabilities: WorkerV4Capabilities,
) -> RunTerminalizationProgress:
    """Stage a retired-control failure through the durable terminalization lifecycle."""

    error_message = "Platform multi-agent orchestration is no longer supported."
    progress = await fail_run_with_v4(
        conn,
        capabilities=v4_capabilities,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=PLATFORM_MULTI_AGENT_NOT_SUPPORTED,
        error_message=error_message,
        result_json={"message": error_message, "retryable": False},
        terminal_reason=RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON,
    )
    return progress
