"""PostgreSQL adapter for the Runs-owned private diagnostic record."""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg import AsyncConnection

from app.runs.domain.diagnostics import (
    RUN_DIAGNOSTICS_SCHEMA_VERSION,
    merge_run_diagnostics,
    serialize_run_diagnostics_payload,
)


class PostgresRunDiagnosticsRepository:
    async def append_observation(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        payload, _ = merge_run_diagnostics(None, observation)
        diagnostic_id = _diagnostic_id(tenant_id=tenant_id, run_id=run_id)
        cursor = await conn.execute(
            """
            insert into run_diagnostics(
              diagnostic_id, tenant_id, run_id, schema_version, revision, payload_json
            ) values (%s, %s, %s, %s, 1, %s::jsonb)
            on conflict (tenant_id, run_id) do nothing
            returning diagnostic_id, schema_version, revision, payload_json,
                      created_at, updated_at
            """,
            (
                diagnostic_id,
                tenant_id,
                run_id,
                RUN_DIAGNOSTICS_SCHEMA_VERSION,
                serialize_run_diagnostics_payload(payload),
            ),
        )
        if row := await cursor.fetchone():
            return dict(row)
        cursor = await conn.execute(
            """
            select diagnostic_id, schema_version, revision, payload_json,
                   created_at, updated_at
            from run_diagnostics
            where tenant_id = %s and run_id = %s
            for update
            """,
            (tenant_id, run_id),
        )
        current = await cursor.fetchone()
        if current is None:
            raise RuntimeError("run_diagnostics_write_conflict")
        if current.get("schema_version") != RUN_DIAGNOSTICS_SCHEMA_VERSION:
            raise RuntimeError("run_diagnostics_schema_unsupported")
        payload, changed = merge_run_diagnostics(current.get("payload_json"), observation)
        if not changed:
            return dict(current)
        cursor = await conn.execute(
            """
            update run_diagnostics
            set payload_json = %s::jsonb, revision = revision + 1, updated_at = now()
            where tenant_id = %s and run_id = %s
            returning diagnostic_id, schema_version, revision, payload_json,
                      created_at, updated_at
            """,
            (serialize_run_diagnostics_payload(payload), tenant_id, run_id),
        )
        updated = await cursor.fetchone()
        if updated is None:
            raise RuntimeError("run_diagnostics_update_conflict")
        return dict(updated)

    async def get_admin_snapshot(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select r.id as run_id, r.session_id, r.user_id, r.workspace_id,
                   r.status, r.trace_id, r.created_at, r.queued_at, r.started_at,
                   r.finished_at, r.error_code, r.result_json,
                   d.diagnostic_id, d.schema_version as diagnostic_schema_version,
                   d.revision, d.payload_json, d.created_at as diagnostic_created_at,
                   d.updated_at as diagnostic_updated_at
            from runs r
            left join run_diagnostics d
              on d.tenant_id = r.tenant_id and d.run_id = r.id
            where r.tenant_id = %s and r.id = %s
            """,
            (tenant_id, run_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        attempt_cursor = await conn.execute(
            """
            select id as attempt_id, ordinal, status, owner_kind, started_at,
                   finished_at, terminal_reason, error_code
            from run_attempts
            where tenant_id = %s and run_id = %s
            order by ordinal asc
            """,
            (tenant_id, run_id),
        )
        attempts = [dict(item) for item in await attempt_cursor.fetchall()]
        diagnostic = None
        if row.get("diagnostic_id"):
            diagnostic = {
                "diagnostic_id": row["diagnostic_id"],
                "schema_version": row["diagnostic_schema_version"],
                "revision": row["revision"],
                "payload_json": row["payload_json"],
                "created_at": row["diagnostic_created_at"],
                "updated_at": row["diagnostic_updated_at"],
            }
        return {
            "run": {
                key: row.get(key)
                for key in (
                    "run_id",
                    "session_id",
                    "user_id",
                    "workspace_id",
                    "status",
                    "trace_id",
                    "created_at",
                    "queued_at",
                    "started_at",
                    "finished_at",
                    "error_code",
                )
            },
            "result_json": row.get("result_json"),
            "diagnostic": diagnostic,
            "attempts": attempts,
        }


def _diagnostic_id(*, tenant_id: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\0{run_id}".encode()).hexdigest()
    return f"rdiag_{digest[:32]}"
