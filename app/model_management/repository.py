"""Persistence and public projection for the shared model connection and catalog."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from .security import decrypt_api_key, encrypt_api_key


_SAFE_PLATFORM_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_GENERATED_PLATFORM_ID_PREFIX = "mdl_"
_CONNECTION_LOCK_KEY = 749_120_009


@dataclass(frozen=True)
class ActiveConnection:
    revision: int
    base_url: str
    api_key: str
    key_fingerprint: str


@dataclass(frozen=True)
class RunModelSelection:
    model_id: str
    model_value: str
    connection_revision: int | None


def platform_model_id(upstream_model_id: str) -> str:
    if (
        _SAFE_PLATFORM_ID.fullmatch(upstream_model_id)
        and not upstream_model_id.startswith(_GENERATED_PLATFORM_ID_PREFIX)
    ):
        return upstream_model_id
    digest = hashlib.sha256(upstream_model_id.encode("utf-8")).hexdigest()[:32]
    return f"mdl_{digest}"


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
    discovered_ids = [platform_model_id(value) for value in upstream_model_ids]
    discovered_by_platform_id: dict[str, str] = {}
    for platform_id, upstream_value in zip(discovered_ids, upstream_model_ids, strict=True):
        previous_value = discovered_by_platform_id.setdefault(platform_id, upstream_value)
        if previous_value != upstream_value:
            raise ValueError("model_catalog_identity_collision")
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
    discovered_ids = [platform_model_id(value) for value in upstream_model_ids]
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
    return [_admin_model_projection(row) for row in await cursor.fetchall()]


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
    rows = await cursor.fetchall()
    models = [
        {
            "id": str(row["model_id"]),
            "value": str(row["upstream_model_id"]),
            "label": str(row["display_name"]),
            "provider": str(row["provider"]),
            "description": "",
            "profile": {},
        }
        for row in rows
    ]
    default = next((model["id"] for model, row in zip(models, rows, strict=True) if row["is_default"]), None)
    return {
        "models": models,
        "count": len(models),
        "enabled_count": len(models),
        "default_model_id": default,
    }


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
    next_name = str(row["display_name"]) if display_name is None else display_name.strip()
    if not next_name or len(next_name) > 160 or any(ord(char) < 32 for char in next_name):
        raise ValueError("model_display_name_invalid")
    next_enabled = bool(row["enabled"]) if enabled is None else enabled
    next_default = bool(row["is_default"]) if is_default is None else is_default
    if next_default and (not next_enabled or not bool(row["upstream_available"])):
        raise ValueError("model_default_must_be_available")
    if next_default:
        await conn.execute("update model_catalog_entries set is_default = false where is_default = true")
    if not next_enabled:
        next_default = False
    await conn.execute(
        """
        update model_catalog_entries
        set display_name = %s, enabled = %s, is_default = %s
        where model_id = %s
        """,
        (next_name, next_enabled, next_default, model_id),
    )
    cursor = await conn.execute("select * from model_catalog_entries where model_id = %s", (model_id,))
    return _admin_model_projection(await cursor.fetchone())


async def resolve_run_model(
    conn: AsyncConnection,
    *,
    model_id: str | None,
    model_value: str | None,
) -> RunModelSelection | None:
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


def _admin_model_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["model_id"]),
        "value": str(row["upstream_model_id"]),
        "label": str(row["display_name"]),
        "provider": str(row["provider"]),
        "enabled": bool(row["enabled"]),
        "available": bool(row["upstream_available"]),
        "is_default": bool(row["is_default"]),
        "order": int(row["display_order"]),
        "last_seen_revision": int(row["last_seen_revision"]),
        "last_seen_at": row["last_seen_at"].isoformat(),
    }
