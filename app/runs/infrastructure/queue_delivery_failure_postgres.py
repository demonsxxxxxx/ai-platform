"""PostgreSQL eligibility lock for queue-rejection terminalization."""

from __future__ import annotations

from typing import Any


async def lock_queued_run_without_attempt(
    conn: Any, *, tenant_id: str, run_id: str
) -> bool:
    cursor = await conn.execute(
        """
        select runs.id
        from runs
        where runs.tenant_id = %s and runs.id = %s
          and runs.status = 'queued'
          and runs.cancel_requested_at is null
          and runs.terminalization_target is null
          and not exists (
            select 1 from run_attempts attempt
            where attempt.tenant_id = runs.tenant_id and attempt.run_id = runs.id
          )
        for update of runs
        """,
        (tenant_id, run_id),
    )
    return await cursor.fetchone() is not None
