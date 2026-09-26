"""Steps persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.limits import RUN_STEP_PAYLOAD_MAX_BYTES
from app.platform.postgres.limits import compact_json_dumps
from app.platform.postgres.values import _require_json_size
from app.platform.postgres.values import new_id
from psycopg import AsyncConnection
from typing import Any


async def upsert_run_step(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    step_key: str,
    step_kind: str,
    status: str,
    title: str,
    role: str | None,
    sequence: int,
    payload_json: dict[str, Any],
) -> str:
    _require_json_size(
        payload_json,
        max_bytes=RUN_STEP_PAYLOAD_MAX_BYTES,
        code="run_step_payload_too_large",
    )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"run-step:{tenant_id}:{run_id}:{step_key}",),
    )
    existing_cursor = await conn.execute(
        """
        select id, payload_json
        from run_steps
        where tenant_id = %s and run_id = %s and step_key = %s
        for update
        """,
        (tenant_id, run_id, step_key),
    )
    existing = await existing_cursor.fetchone()
    existing_payload = existing.get("payload_json") if existing is not None else {}
    if not isinstance(existing_payload, dict):
        existing_payload = {}
    merged_payload = {**existing_payload, **payload_json}
    _require_json_size(
        merged_payload,
        max_bytes=RUN_STEP_PAYLOAD_MAX_BYTES,
        code="run_step_payload_too_large",
    )
    if existing is not None:
        cursor = await conn.execute(
            """
            update run_steps
            set step_kind = %s,
                status = %s,
                title = %s,
                role = %s,
                sequence = %s,
                payload_json = %s::jsonb,
                started_at = coalesce(
                  started_at,
                  case when %s in ('running', 'succeeded', 'failed') then now() else null end
                ),
                finished_at = coalesce(
                  case when %s in ('succeeded', 'failed', 'cancelled') then now() else null end,
                  finished_at
                ),
                updated_at = now()
            where id = %s and tenant_id = %s and run_id = %s
            returning id
            """,
            (
                step_kind,
                status,
                title,
                role,
                sequence,
                compact_json_dumps(merged_payload),
                status,
                status,
                str(existing["id"]),
                tenant_id,
                run_id,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RepositoryConflictError("run_step_update_conflict")
        return str(row["id"])
    step_id = new_id("step")
    cursor = await conn.execute(
        """
        insert into run_steps(
          id, tenant_id, run_id, step_key, step_kind, status, title, role, sequence,
          payload_json, started_at, finished_at
        )
        values (
          %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s::jsonb,
          case when %s in ('running', 'succeeded', 'failed') then now() else null end,
          case when %s in ('succeeded', 'failed', 'cancelled') then now() else null end
        )
        returning id
        """,
        (
            step_id,
            tenant_id,
            run_id,
            step_key,
            step_kind,
            status,
            title,
            role,
            sequence,
            compact_json_dumps(merged_payload),
            status,
            status,
        ),
    )
    row = await cursor.fetchone()
    return str(row["id"])


async def list_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id, run_id, step_key, step_kind, status, title, role, sequence,
          payload_json, started_at, finished_at, created_at, updated_at
        from run_steps
        where tenant_id = %s and run_id = %s
        order by sequence asc, created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def _cancel_open_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    await conn.execute(
        """
        update run_steps
        set status = 'cancelled',
            finished_at = coalesce(finished_at, now()),
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status in ('pending', 'running')
        """,
        (tenant_id, run_id),
    )


async def _fail_open_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    await conn.execute(
        """
        update run_steps
        set status = case when status = 'running' then 'failed' else 'cancelled' end,
            finished_at = coalesce(finished_at, now()),
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status in ('pending', 'running')
        """,
        (tenant_id, run_id),
    )
