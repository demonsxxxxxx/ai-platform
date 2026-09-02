from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from psycopg import AsyncConnection

from app.identity.application.profile_metadata import validate_profile_metadata
from app.platform.postgres.errors import RepositoryAuthorizationError


async def tenant_exists(conn: AsyncConnection, *, tenant_id: str) -> bool:
    """Return whether the tenant identity is already provisioned."""

    cursor = await conn.execute(
        """
        select 1
        from tenants
        where id = %s
        """,
        (tenant_id,),
    )
    return await cursor.fetchone() is not None


async def ensure_user(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    display_name: str | None = None,
) -> None:
    if not user_id:
        return
    await conn.execute(
        """
        insert into users(id, tenant_id, display_name)
        values (%s, %s, %s)
        on conflict (id) do nothing
        """,
        (user_id, tenant_id, display_name or user_id),
    )


async def ensure_submission_principal(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Provision and tenant-validate a principal before a submission ledger write."""

    await ensure_user(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        display_name=display_name,
    )
    principal_user = await get_user(conn, tenant_id=tenant_id, user_id=user_id)
    if principal_user is None:
        raise RepositoryAuthorizationError("principal_user_scope_mismatch")
    return principal_user


async def get_user(conn: AsyncConnection, *, tenant_id: str, user_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, tenant_id, display_name, email, external_id, status, created_at
        from users
        where tenant_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _get_profile_metadata(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    lock: bool,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        f"""
        select metadata_json
        from users
        where tenant_id = %s
          and id = %s
          and status = 'active'
        {'for update' if lock else ''}
        """,
        (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row["metadata_json"]) if row else None


async def get_user_profile_metadata(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    return await _get_profile_metadata(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        lock=False,
    )


async def lock_user_profile_metadata(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    return await _get_profile_metadata(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        lock=True,
    )


async def update_user_profile_metadata(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update users
        set metadata_json = %s::jsonb
        where tenant_id = %s
          and id = %s
          and status = 'active'
        returning metadata_json
        """,
        (
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            tenant_id,
            user_id,
        ),
    )
    row = await cursor.fetchone()
    return dict(row["metadata_json"]) if row else None


class PostgresProfileMetadataStore:
    def __init__(
        self,
        transaction_factory: Callable[
            [], AbstractAsyncContextManager[AsyncConnection]
        ],
    ) -> None:
        self._transaction_factory = transaction_factory

    async def get(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
    ) -> dict[str, Any] | None:
        async with self._transaction_factory() as conn:
            try:
                await ensure_submission_principal(
                    conn,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    display_name=display_name,
                )
            except RepositoryAuthorizationError:
                return None
            return await get_user_profile_metadata(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
            )

    async def merge(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._transaction_factory() as conn:
            try:
                await ensure_submission_principal(
                    conn,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    display_name=display_name,
                )
            except RepositoryAuthorizationError:
                return None
            current = await lock_user_profile_metadata(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            if current is None:
                return None
            return await update_user_profile_metadata(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                metadata=validate_profile_metadata({**current, **patch}),
            )
