"""Records persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.control_plane_contracts import ARTIFACT_MANIFEST_SCHEMA_VERSION
from app.control_plane_contracts import standard_trace_id
from app.platform.postgres.limits import ARTIFACT_MANIFEST_MAX_BYTES
from app.platform.postgres.values import _require_json_size
from app.platform.postgres.values import dumps_json
from psycopg import AsyncConnection
from typing import Any


async def list_run_artifacts(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select id, trace_id, artifact_type, label, content_type, storage_key, size_bytes, manifest_version, manifest_json, created_at
        from artifacts
        where tenant_id = %s and run_id = %s
        order by created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def create_artifact(
    conn: AsyncConnection,
    *,
    artifact_id: str,
    tenant_id: str,
    run_id: str,
    trace_id: str | None = None,
    artifact_type: str,
    label: str,
    content_type: str,
    storage_key: str,
    size_bytes: int,
    manifest_json: dict[str, Any],
) -> None:
    _require_json_size(
        manifest_json,
        max_bytes=ARTIFACT_MANIFEST_MAX_BYTES,
        code="artifact_manifest_too_large",
    )
    resolved_trace_id = trace_id or standard_trace_id(run_id)
    await conn.execute(
        """
        insert into artifacts(
          id, tenant_id, run_id, trace_id, artifact_type, label, content_type, storage_key,
          size_bytes, manifest_version, manifest_json
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            artifact_id,
            tenant_id,
            run_id,
            resolved_trace_id,
            artifact_type,
            label,
            content_type,
            storage_key,
            size_bytes,
            ARTIFACT_MANIFEST_SCHEMA_VERSION,
            dumps_json(manifest_json),
        ),
    )
