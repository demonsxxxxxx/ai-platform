"""Persistence and public projection for the shared model connection and catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from app.execution.application.model_selection import RunModelSelection
from app.execution.domain.model_catalog import (
    admin_model_projection,
    discovered_model_mapping,
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
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    conversation_mode: str | None = None
    model_value: str | None = None


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
        select revision, base_url, api_key_ciphertext, key_fingerprint,
               runs.max_input_tokens, runs.max_output_tokens,
               run_attempts.execution_spec_json #>> '{context_pack,conversation_context,execution_mode}' as conversation_mode
        from runs
        join model_gateway_revisions
          on model_gateway_revisions.revision = runs.model_gateway_revision
        join run_attempts
          on run_attempts.run_id = runs.id and run_attempts.tenant_id = runs.tenant_id
        join sandbox_leases
          on sandbox_leases.run_id = runs.id
         and sandbox_leases.tenant_id = runs.tenant_id
        where runs.id = %s
          and run_attempts.id = %s
          and run_attempts.execution_spec_schema_version = 'ai-platform.execution-spec.v2'
          and run_attempts.execution_spec_json->>'model_value' = runs.model_value
          and run_attempts.execution_spec_json->>'model_max_input_tokens' = runs.max_input_tokens::text
          and run_attempts.execution_spec_json->>'model_max_output_tokens' = runs.max_output_tokens::text
          and sandbox_leases.attempt_id = %s
          and sandbox_leases.lease_payload_json ->> 'owner_generation' = run_attempts.owner_generation::text
          and runs.model_value = %s
          and runs.status in ('queued', 'running')
          and sandbox_leases.status = 'active'
          and sandbox_leases.released_at is null
          and (sandbox_leases.expires_at is null or sandbox_leases.expires_at > now())
        limit 1
        """,
        (run_id, attempt_id, attempt_id, model_value),
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
               upstream_available, is_default, display_order, last_seen_revision,
               last_seen_at, max_input_tokens, max_output_tokens
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
        select model_id, upstream_model_id, display_name, provider, is_default,
               max_input_tokens, max_output_tokens
        from model_catalog_entries
        where enabled = true and upstream_available = true
          and max_input_tokens is not null and max_output_tokens is not null
        order by is_default desc, display_order, model_id
        """
    )
    return public_model_projection(await cursor.fetchall())


async def publish_models(
    conn: AsyncConnection,
    *,
    expected_revision: int | None,
    models: list[dict[str, Any]],
    **connection: Any,
) -> tuple[int, list[dict[str, Any]]]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (_CONNECTION_LOCK_KEY,))
    cursor = await conn.execute(
        "select revision from model_gateway_revisions where active = true limit 1"
    )
    active = await cursor.fetchone()
    if (int(active["revision"]) if active else None) != expected_revision:
        raise ValueError("model_catalog_revision_conflict")
    revision, _ = await activate_connection_and_sync(
        conn, upstream_model_ids=connection.pop("upstream_model_ids"), **connection
    )
    await conn.execute("update model_catalog_entries set enabled = false, is_default = false")
    for model in models:
        cursor = await conn.execute(
            """
            update model_catalog_entries
            set display_name = %s, enabled = %s, is_default = %s,
                display_order = %s, max_input_tokens = %s, max_output_tokens = %s
            where model_id = %s and upstream_model_id = %s
              and upstream_available = true and last_seen_revision = %s
            returning model_id
            """,
            (
                model["display_name"], model["enabled"], model["is_default"],
                model["order"], model.get("max_input_tokens"),
                model.get("max_output_tokens"), model["id"], model["value"], revision,
            ),
        )
        if await cursor.fetchone() is None:
            raise ValueError("model_catalog_discovery_changed")
    return revision, await list_admin_models(conn)


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
               catalog.upstream_model_id,
               catalog.max_input_tokens,
               catalog.max_output_tokens
        from active_gateway
        left join model_catalog_entries catalog
          on catalog.enabled = true
         and catalog.upstream_available = true
         and catalog.last_seen_revision = active_gateway.revision
         and (%s::text is null or catalog.model_id = %s)
         and (%s::text is null or catalog.upstream_model_id = %s)
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
    if row.get("max_input_tokens") is None or row.get("max_output_tokens") is None:
        raise ValueError("model_capacity_missing")
    return RunModelSelection(
        model_id=str(row["model_id"]),
        model_value=str(row["upstream_model_id"]),
        connection_revision=int(row["connection_revision"]),
        max_input_tokens=(
            int(row["max_input_tokens"])
            if row.get("max_input_tokens") is not None
            else None
        ),
        max_output_tokens=(
            int(row["max_output_tokens"])
            if row.get("max_output_tokens") is not None
            else None
        ),
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

    async def publish_models(self, conn: AsyncConnection, **kwargs: Any) -> Any:
        return await publish_models(conn, **kwargs)

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
        max_input_tokens=(
            int(row["max_input_tokens"])
            if row.get("max_input_tokens") is not None
            else None
        ),
        max_output_tokens=(
            int(row["max_output_tokens"])
            if row.get("max_output_tokens") is not None
            else None
        ),
        conversation_mode=row.get("conversation_mode"),
        model_value=row.get("model_value"),
    )
