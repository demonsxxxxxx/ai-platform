"""Persistence and public projection for the shared model connection and catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from app.execution.application.model_selection import RunModelSelection
from app.execution.domain.model_catalog import (
    admin_model_projection,
    discovered_model_mapping,
    normalize_catalog_patch,
    platform_model_id as platform_model_id,
    public_model_projection,
)

from .model_security import decrypt_api_key, encrypt_api_key


_CONNECTION_LOCK_KEY = 749_120_009


@dataclass(frozen=True)
class ActiveConnection:
    revision: int
    base_url: str
    api_key: str
    key_fingerprint: str


async def get_connection_projection(conn: AsyncConnection) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select revision, base_url, key_fingerprint, created_at
        from model_gateway_revisions
        where active = true
        order by revision desc
        limit 1
        """
    )
    row = await cursor.fetchone()
    if row is None:
        return {"configured": False, "revision": None, "base_url": "", "key_fingerprint": ""}
    return {
        "configured": True,
        "revision": int(row["revision"]),
        "base_url": str(row["base_url"]),
        "key_fingerprint": str(row["key_fingerprint"]),
        "updated_at": row["created_at"].isoformat(),
    }


async def get_active_connection(
    conn: AsyncConnection,
    *,
    encryption_key: str,
) -> ActiveConnection | None:
    cursor = await conn.execute(
        """
        select revision, base_url, api_key_ciphertext, key_fingerprint
        from model_gateway_revisions
        where active = true
        order by revision desc
        limit 1
        """
    )
    row = await cursor.fetchone()
    return _connection_from_row(row, encryption_key=encryption_key) if row else None


async def get_run_connection(
    conn: AsyncConnection,
    *,
    run_id: str,
    attempt_id: str,
    model_value: str,
    encryption_key: str,
) -> ActiveConnection | None:
    cursor = await conn.execute(
        """
        select revision, base_url, api_key_ciphertext, key_fingerprint
        from runs
        join model_gateway_revisions
          on model_gateway_revisions.revision = runs.model_gateway_revision
        join sandbox_leases
          on sandbox_leases.run_id = runs.id
         and sandbox_leases.tenant_id = runs.tenant_id
        where runs.id = %s
          and sandbox_leases.attempt_id = %s
          and runs.model_value = %s
          and runs.status in ('queued', 'running')
          and sandbox_leases.status = 'active'
          and sandbox_leases.released_at is null
          and (sandbox_leases.expires_at is null or sandbox_leases.expires_at > now())
        limit 1
        """,
        (run_id, attempt_id, model_value),
    )
    row = await cursor.fetchone()
    return _connection_from_row(row, encryption_key=encryption_key) if row else None


async def activate_connection_and_sync(
    conn: AsyncConnection,
    *,
    base_url: str,
    api_key: str,
    key_fingerprint: str,
    encryption_key: str,
    actor_user_id: str,
    upstream_model_ids: list[str],
) -> tuple[int, list[dict[str, Any]]]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (_CONNECTION_LOCK_KEY,))
    cursor = await conn.execute(
        "select coalesce(max(revision), 0) + 1 as revision from model_gateway_revisions"
    )
    row = await cursor.fetchone()
    revision = int(row["revision"])
    encrypted = encrypt_api_key(api_key, revision=revision, encoded_key=encryption_key)
    discovered_by_platform_id = discovered_model_mapping(upstream_model_ids)
    cursor = await conn.execute(
        """
        select model_id, upstream_model_id
        from model_catalog_entries
        where model_id = any(%s)
        """,
        (list(discovered_by_platform_id),),
    )
    existing_rows = await cursor.fetchall()
    existing_by_platform_id = {
        str(existing["model_id"]): str(existing["upstream_model_id"])
        for existing in existing_rows
    }
    for platform_id, upstream_value in discovered_by_platform_id.items():
        if (
            platform_id in existing_by_platform_id
            and existing_by_platform_id[platform_id] != upstream_value
        ):
            raise ValueError("model_catalog_identity_collision")
    await conn.execute("update model_gateway_revisions set active = false where active = true")
    await conn.execute(
        """
        insert into model_gateway_revisions(
          revision, base_url, api_key_ciphertext, key_fingerprint, active, created_by
        ) values (%s, %s, %s, %s, true, %s)
        """,
        (revision, base_url, encrypted, key_fingerprint, actor_user_id),
    )
    await conn.execute(
        "update model_catalog_entries set upstream_available = false where upstream_available = true"
    )
    for order, (model_id, upstream_value) in enumerate(
        discovered_by_platform_id.items(), start=1
    ):
        await conn.execute(
            """
            insert into model_catalog_entries(
              model_id, upstream_model_id, display_name, provider, enabled,
              upstream_available, display_order, first_seen_revision, last_seen_revision,
              first_seen_at, last_seen_at
            ) values (%s, %s, %s, %s, false, true, %s, %s, %s, now(), now())
            on conflict (model_id) do update
            set upstream_model_id = excluded.upstream_model_id,
                upstream_available = true,
                display_order = excluded.display_order,
                last_seen_revision = excluded.last_seen_revision,
                last_seen_at = now()
            """,
            (
                model_id,
                upstream_value,
                upstream_value,
                "compatible",
                order,
                revision,
                revision,
            ),
        )
    await conn.execute(
        """
        update model_catalog_entries
        set is_default = false
        where is_default = true and (enabled = false or upstream_available = false)
        """
    )
    return revision, await list_admin_models(conn)


async def list_admin_models(conn: AsyncConnection) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select model_id, upstream_model_id, display_name, provider, enabled,
               upstream_available, is_default, display_order, last_seen_revision, last_seen_at
        from model_catalog_entries
        order by display_order, model_id
        """
    )
    return [admin_model_projection(row) for row in await cursor.fetchall()]


async def list_public_models(conn: AsyncConnection) -> dict[str, Any] | None:
    configured = await conn.execute(
        "select 1 from model_gateway_revisions where active = true limit 1"
    )
    if await configured.fetchone() is None:
        return None
    cursor = await conn.execute(
        """
        select model_id, upstream_model_id, display_name, provider, is_default
        from model_catalog_entries
        where enabled = true and upstream_available = true
        order by is_default desc, display_order, model_id
        """
    )
    return public_model_projection(await cursor.fetchall())


async def update_catalog_entry(
    conn: AsyncConnection,
    *,
    model_id: str,
    display_name: str | None,
    enabled: bool | None,
    is_default: bool | None,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "select * from model_catalog_entries where model_id = %s for update",
        (model_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    patch = normalize_catalog_patch(
        row,
        display_name=display_name,
        enabled=enabled,
        is_default=is_default,
    )
    if patch.is_default:
        await conn.execute("update model_catalog_entries set is_default = false where is_default = true")
    await conn.execute(
        """
        update model_catalog_entries
        set display_name = %s, enabled = %s, is_default = %s
        where model_id = %s
        """,
        (patch.display_name, patch.enabled, patch.is_default, model_id),
    )
    cursor = await conn.execute("select * from model_catalog_entries where model_id = %s", (model_id,))
    return admin_model_projection(await cursor.fetchone())


async def resolve_run_model(
    conn: AsyncConnection,
    *,
    model_id: str | None,
    model_value: str | None,
) -> RunModelSelection | None:
    await conn.execute("select pg_advisory_xact_lock_shared(%s)", (_CONNECTION_LOCK_KEY,))
    cursor = await conn.execute(
        """
        with active_gateway as (
          select revision
          from model_gateway_revisions
          where active = true
          order by revision desc
          limit 1
        )
        select active_gateway.revision as connection_revision,
               catalog.model_id,
               catalog.upstream_model_id
        from active_gateway
        left join model_catalog_entries catalog
          on catalog.enabled = true
         and catalog.upstream_available = true
         and catalog.last_seen_revision = active_gateway.revision
         and (%s is null or catalog.model_id = %s)
         and (%s is null or catalog.upstream_model_id = %s)
        order by catalog.is_default desc nulls last,
                 catalog.display_order asc nulls last,
                 catalog.model_id asc nulls last
        limit 1
        """,
        (model_id, model_id, model_value, model_value),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row.get("model_id") is None or row.get("upstream_model_id") is None:
        raise ValueError("model_id_not_available")
    return RunModelSelection(
        model_id=str(row["model_id"]),
        model_value=str(row["upstream_model_id"]),
        connection_revision=int(row["connection_revision"]),
    )


class PostgresModelManagementRepository:
    async def connection_projection(self, conn: AsyncConnection) -> dict[str, Any]:
        return await get_connection_projection(conn)

    async def active_connection(self, conn: AsyncConnection, **kwargs: Any) -> ActiveConnection | None:
        return await get_active_connection(conn, **kwargs)

    async def run_connection(self, conn: AsyncConnection, **kwargs: Any) -> ActiveConnection | None:
        return await get_run_connection(conn, **kwargs)

    async def admin_models(self, conn: AsyncConnection) -> list[dict[str, Any]]:
        return await list_admin_models(conn)

    async def public_models(self, conn: AsyncConnection) -> dict[str, Any] | None:
        return await list_public_models(conn)

    async def activate_and_sync(self, conn: AsyncConnection, **kwargs: Any) -> Any:
        return await activate_connection_and_sync(conn, **kwargs)

    async def update_catalog(self, conn: AsyncConnection, **kwargs: Any) -> Any:
        return await update_catalog_entry(conn, **kwargs)

    async def resolve_run_model(
        self,
        conn: AsyncConnection,
        **kwargs: Any,
    ) -> RunModelSelection | None:
        return await resolve_run_model(conn, **kwargs)


def _connection_from_row(row: dict[str, Any], *, encryption_key: str) -> ActiveConnection:
    revision = int(row["revision"])
    return ActiveConnection(
        revision=revision,
        base_url=str(row["base_url"]),
        api_key=decrypt_api_key(
            bytes(row["api_key_ciphertext"]),
            revision=revision,
            encoded_key=encryption_key,
        ),
        key_fingerprint=str(row["key_fingerprint"]),
    )
