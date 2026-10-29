"""Persistence and public projection for the shared model connection and catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from app.execution.application.model_selection import RunModelSelection
from app.execution.domain.model_catalog import (
    admin_model_projection,
    discovered_model_mapping,
)
from app.execution.domain.model_groups import ModelGroupPolicyError
from app.execution.infrastructure.model_groups import resolve_group_model

from .model_security import decrypt_api_key, encrypt_api_key
from .model_providers import require_provider


_CONNECTION_LOCK_KEY = 749_120_009


@dataclass(frozen=True)
class ActiveConnection:
    revision: int
    base_url: str
    api_key: str
    key_fingerprint: str
    auto_compact_window: int | None = None
    max_output_tokens: int | None = None
    conversation_mode: str | None = None
    model_value: str | None = None


async def get_connection_projection(conn: AsyncConnection, *, provider_id: str = "legacy") -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select revision, base_url, key_fingerprint, created_at
        from model_gateway_revisions
        where active = true and provider_id = %s
        order by revision desc
        limit 1
        """, (provider_id,),
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
    provider_id: str = "legacy",
) -> ActiveConnection | None:
    cursor = await conn.execute(
        """
        select revision, base_url, api_key_ciphertext, key_fingerprint
        from model_gateway_revisions
        where active = true and provider_id = %s
        order by revision desc
        limit 1
        """, (provider_id,),
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
               runs.auto_compact_window, runs.max_output_tokens,
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
          and (
            run_attempts.execution_spec_schema_version in (
            'ai-platform.execution-spec.v2', 'ai-platform.execution-spec.v3'
          )
            or run_attempts.execution_spec_schema_version = 'ai-platform.execution-spec.v4'
          )
          and run_attempts.execution_spec_json->>'model_value' = runs.model_value
          -- v3 froze the window itself; v2 froze an input capacity the runner
          -- clamped into the CLI range to derive that same window.
          and case run_attempts.execution_spec_schema_version
                when 'ai-platform.execution-spec.v3'
                  then run_attempts.execution_spec_json->>'model_auto_compact_window'
                when 'ai-platform.execution-spec.v4'
                  then run_attempts.execution_spec_json->>'model_auto_compact_window'
                else (least(1000000, greatest(100000,
                       (run_attempts.execution_spec_json->>'model_max_input_tokens')::bigint)))::text
              end = runs.auto_compact_window::text
          and run_attempts.execution_spec_json->>'model_max_output_tokens' = runs.max_output_tokens::text
          and ((run_attempts.execution_spec_schema_version <> 'ai-platform.execution-spec.v4'
                and runs.model_group_id is null and runs.model_group_code is null
                and runs.model_group_type is null and runs.model_group_revision is null
                and runs.model_selection_mode is null and runs.usage_scene is null
                and runs.model_policy_version is null)
               or (run_attempts.execution_spec_schema_version = 'ai-platform.execution-spec.v4'
                   and run_attempts.execution_spec_json->>'model_id' = runs.model_id
                   and run_attempts.execution_spec_json->>'model_gateway_revision' = runs.model_gateway_revision::text
                   and run_attempts.execution_spec_json->>'model_group_id' = runs.model_group_id
                   and run_attempts.execution_spec_json->>'model_group_code' = runs.model_group_code
                   and run_attempts.execution_spec_json->>'model_group_type' = runs.model_group_type
                   and (run_attempts.execution_spec_json->>'model_group_revision')::bigint = runs.model_group_revision
                   and run_attempts.execution_spec_json->>'model_selection_mode' = runs.model_selection_mode
                   and run_attempts.execution_spec_json->>'usage_scene' = runs.usage_scene
                   and run_attempts.execution_spec_json->>'model_policy_version' = runs.model_policy_version
                   and not (
                     runs.model_group_type = 'ordinary_default'
                     and runs.usage_scene = 'chat'
                   )))
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


async def get_run_model_completion_connection(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    encryption_key: str,
) -> ActiveConnection | None:
    """Resolve a settled Run's own pinned model, only while it stays enabled.

    A disabled or delisted pinned model returns no connection so the caller omits
    the completion instead of silently switching to another model.
    """

    cursor = await conn.execute(
        """
        select runs.model_gateway_revision as revision, gateway.base_url,
               gateway.api_key_ciphertext, gateway.key_fingerprint,
               runs.model_value, runs.auto_compact_window, runs.max_output_tokens
        from runs
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
        join model_catalog_entries catalog
          on catalog.model_id = runs.model_id
         and catalog.upstream_model_id = runs.model_value
        join model_gateway_revisions gateway
          on gateway.revision = runs.model_gateway_revision
        where runs.tenant_id = %s
          and runs.user_id = %s
          and runs.session_id = %s
          and runs.id = %s
          and runs.status = 'succeeded'
          and sessions.status = 'active'
          and runs.model_id is not null
          and runs.model_value is not null
          and runs.model_gateway_revision is not null
          and runs.model_group_id is not null and runs.model_group_id <> ''
          and ((runs.model_group_type = 'business' and runs.model_group_code <> 'default')
            or (runs.model_group_type = 'ordinary_default' and runs.model_group_code = 'default' and runs.usage_scene = 'expert'))
          and runs.model_group_code <> '' and runs.model_group_revision > 0
          and runs.model_selection_mode in ('auto', 'explicit')
          and runs.usage_scene in ('chat', 'expert')
          and runs.model_policy_version = 'model-groups.v1'
          and exists (
            select 1 from run_attempts a
            where a.tenant_id = runs.tenant_id and a.run_id = runs.id and a.status = 'succeeded'
              and a.execution_spec_schema_version = 'ai-platform.execution-spec.v4'
              and a.execution_spec_json->>'model_id' = runs.model_id
              and a.execution_spec_json->>'model_value' = runs.model_value
              and a.execution_spec_json->>'model_gateway_revision' = runs.model_gateway_revision::text
              and a.execution_spec_json->>'model_group_id' = runs.model_group_id
              and a.execution_spec_json->>'model_group_code' = runs.model_group_code
              and a.execution_spec_json->>'model_group_type' = runs.model_group_type
              and a.execution_spec_json->>'model_group_revision' = runs.model_group_revision::text
              and a.execution_spec_json->>'model_selection_mode' = runs.model_selection_mode
              and a.execution_spec_json->>'usage_scene' = runs.usage_scene
              and a.execution_spec_json->>'model_policy_version' = runs.model_policy_version
              and a.execution_spec_json->>'model_auto_compact_window' = runs.auto_compact_window::text
              and a.execution_spec_json->>'model_max_output_tokens' = runs.max_output_tokens::text
          )
          and catalog.enabled = true
          and catalog.archived_at is null
          and catalog.upstream_available = true
          and runs.auto_compact_window is not null
          and runs.max_output_tokens is not null
        limit 1
        """,
        (tenant_id, user_id, session_id, run_id),
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
    provider_id: str = "legacy",
) -> tuple[int, list[dict[str, Any]]]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (_CONNECTION_LOCK_KEY,))
    cursor = await conn.execute(
        "select coalesce(max(revision), 0) + 1 as revision from model_gateway_revisions"
    )
    row = await cursor.fetchone()
    revision = int(row["revision"])
    await require_provider(conn, provider_id=provider_id)
    encrypted = encrypt_api_key(api_key, revision=revision, encoded_key=encryption_key)
    discovered_by_platform_id = discovered_model_mapping(upstream_model_ids, provider_id)
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
    await conn.execute("update model_gateway_revisions set active = false where active = true and provider_id = %s", (provider_id,))
    await conn.execute(
        """
        insert into model_gateway_revisions(
          revision, base_url, api_key_ciphertext, key_fingerprint, active, created_by, provider_id
        ) values (%s, %s, %s, %s, true, %s, %s)
        """,
        (revision, base_url, encrypted, key_fingerprint, actor_user_id, provider_id),
    )
    await conn.execute(
        "update model_catalog_entries set upstream_available = false where upstream_available = true and provider_id = %s",
        (provider_id,),
    )
    for order, (model_id, upstream_value) in enumerate(
        discovered_by_platform_id.items(), start=1
    ):
        await conn.execute(
            """
            insert into model_catalog_entries(
              model_id, upstream_model_id, display_name, provider, enabled,
              upstream_available, display_order, first_seen_revision, last_seen_revision,
              first_seen_at, last_seen_at, provider_id
            ) values (%s, %s, %s, %s, false, true, %s, %s, %s, now(), now(), %s)
            on conflict (model_id) do update
            set upstream_model_id = excluded.upstream_model_id,
                enabled = case when model_catalog_entries.archived_at is null then model_catalog_entries.enabled else false end,
                archived_at = null,
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
                provider_id,
            ),
        )
    return revision, await list_admin_models(conn, provider_id=provider_id)


async def list_admin_models(conn: AsyncConnection, *, provider_id: str | None = None) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select c.*, p.name as provider_name
        from model_catalog_entries c join model_providers p on p.id = c.provider_id
        where c.archived_at is null and p.archived_at is null
          and (%s::text is null or c.provider_id = %s)
        order by c.display_order, c.model_id
        """, (provider_id, provider_id),
    )
    return [admin_model_projection(row) for row in await cursor.fetchall()]


async def list_public_models(
    conn: AsyncConnection, *, user_id: str, tenant_id: str | None = None,
    usage_scene: str = "independent", group_id: str | None = None,
) -> dict[str, Any] | None:
    if tenant_id is None:
        raise ModelGroupPolicyError("model_policy_unavailable")
    from app.execution.infrastructure.model_groups import list_public_group_models
    return await list_public_group_models(
        conn, tenant_id=tenant_id, user_id=user_id, usage_scene=usage_scene,
        group_id=group_id,
    )
async def publish_models(
    conn: AsyncConnection,
    *,
    expected_revision: int | None,
    models: list[dict[str, Any]],
    provider_id: str = "legacy",
    **connection: Any,
) -> tuple[int, list[dict[str, Any]]]:
    await conn.execute("select pg_advisory_xact_lock(%s)", (_CONNECTION_LOCK_KEY,))
    cursor = await conn.execute(
        "select revision from model_gateway_revisions where active = true and provider_id = %s limit 1",
        (provider_id,),
    )
    active = await cursor.fetchone()
    if (int(active["revision"]) if active else None) != expected_revision:
        raise ValueError("model_catalog_revision_conflict")
    revision, _ = await activate_connection_and_sync(
        conn, upstream_model_ids=connection.pop("upstream_model_ids"), provider_id=provider_id, **connection
    )
    await conn.execute("update model_catalog_entries set enabled = false where provider_id = %s", (provider_id,))
    for model in models:
        cursor = await conn.execute(
            """
            update model_catalog_entries
            set display_name = %s, enabled = %s,
                display_order = %s, auto_compact_window = %s, max_output_tokens = %s
            where model_id = %s and upstream_model_id = %s
              and upstream_available = true and last_seen_revision = %s
            returning model_id
            """,
            (
                model["display_name"], model["enabled"],
                model["order"], model.get("auto_compact_window"),
                model.get("max_output_tokens"),
                model["id"], model["value"], revision,
            ),
        )
        if await cursor.fetchone() is None:
            raise ValueError("model_catalog_discovery_changed")
    return revision, await list_admin_models(conn, provider_id=provider_id)


async def resolve_run_model(
    conn: AsyncConnection,
    *,
    model_id: str | None,
    model_value: str | None,
    user_id: str,
    tenant_id: str | None = None,
    group_id: str | None = None,
    selection_mode: str = "auto",
    usage_scene: str = "independent",
) -> RunModelSelection | None:
    if tenant_id is None:
        raise ModelGroupPolicyError("model_policy_unavailable")
    if tenant_id is not None:
        await conn.execute("select pg_advisory_xact_lock_shared(%s)", (_CONNECTION_LOCK_KEY,))
        try:
            grouped = await resolve_group_model(
                conn, tenant_id=tenant_id, user_id=user_id, group_id=group_id,
                model_id=model_id, model_value=model_value,
                selection_mode=selection_mode, usage_scene=usage_scene,
            )
        except ModelGroupPolicyError:
            raise
        return RunModelSelection(
            model_id=grouped.model_id,
            model_value=grouped.model_value,
            connection_revision=grouped.gateway_revision,
            auto_compact_window=grouped.auto_compact_window,
            max_output_tokens=grouped.max_output_tokens,
            group_id=grouped.group_id,
            group_code=grouped.group_code,
            group_type=grouped.group_type,
            group_revision=grouped.group_revision,
            selection_mode=grouped.selection_mode,
            usage_scene=usage_scene,
            policy_version=grouped.policy_version,
        )
class PostgresModelManagementRepository:
    async def providers(self, conn: AsyncConnection) -> list[dict[str, Any]]:
        from .model_providers import list_providers
        return await list_providers(conn)

    async def require_provider(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        return await require_provider(conn, **kwargs)

    async def create_provider(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from .model_providers import create_provider
        return await create_provider(conn, **kwargs)

    async def provider_impact(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from .model_providers import provider_impact
        return await provider_impact(conn, **kwargs)

    async def retire_provider(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from .model_providers import retire_provider
        return await retire_provider(conn, **kwargs)

    async def connection_projection(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        return await get_connection_projection(conn, **kwargs)

    async def active_connection(self, conn: AsyncConnection, **kwargs: Any) -> ActiveConnection | None:
        return await get_active_connection(conn, **kwargs)

    async def run_connection(self, conn: AsyncConnection, **kwargs: Any) -> ActiveConnection | None:
        return await get_run_connection(conn, **kwargs)

    async def run_model_completion_connection(
        self, conn: AsyncConnection, **kwargs: Any
    ) -> ActiveConnection | None:
        return await get_run_model_completion_connection(conn, **kwargs)

    async def admin_models(self, conn: AsyncConnection, **kwargs: Any) -> list[dict[str, Any]]:
        return await list_admin_models(conn, **kwargs)

    async def public_models(
        self, conn: AsyncConnection, **kwargs: Any
    ) -> dict[str, Any] | None:
        return await list_public_models(conn, **kwargs)

    async def activate_and_sync(self, conn: AsyncConnection, **kwargs: Any) -> Any:
        return await activate_connection_and_sync(conn, **kwargs)

    async def catalog_access(self, conn: AsyncConnection) -> dict[str, dict[str, Any]]:
        """Return saved display name and access per model for update inheritance."""

        cursor = await conn.execute(
            """
            select model_id, display_name
            from model_catalog_entries
            """
        )
        return {
            str(row["model_id"]): {
                "display_name": str(row["display_name"]),
            }
            for row in await cursor.fetchall()
        }

    async def publish_models(self, conn: AsyncConnection, **kwargs: Any) -> Any:
        return await publish_models(conn, **kwargs)

    async def resolve_run_model(
        self,
        conn: AsyncConnection,
        **kwargs: Any,
    ) -> RunModelSelection | None:
        return await resolve_run_model(conn, **kwargs)

    async def resolve_group_model(self, conn: AsyncConnection, **kwargs: Any) -> Any:
        return await resolve_group_model(conn, **kwargs)

    async def list_groups_for_user(self, conn: AsyncConnection, **kwargs: Any) -> list[dict[str, Any]]:
        from app.execution.infrastructure.model_groups import list_groups_for_user
        return await list_groups_for_user(conn, **kwargs)

    async def list_admin_groups(self, conn: AsyncConnection, **kwargs: Any) -> list[dict[str, Any]]:
        from app.execution.infrastructure.model_groups import list_admin_groups
        return await list_admin_groups(conn, **kwargs)

    async def get_admin_group_details(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any] | None:
        from app.execution.infrastructure.model_groups import get_admin_group_details
        return await get_admin_group_details(conn, **kwargs)

    async def create_group(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_groups import create_group
        return await create_group(conn, **kwargs)

    async def update_group(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_groups import update_group
        return await update_group(conn, **kwargs)

    async def archive_or_delete_group(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_groups import archive_or_delete_group
        return await archive_or_delete_group(conn, **kwargs)

    async def set_group_models(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_groups import set_group_models
        return await set_group_models(conn, **kwargs)

    async def set_group_members(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_groups import set_group_members
        return await set_group_members(conn, **kwargs)

    async def batch_group_models(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_group_batches import batch_group_models
        return await batch_group_models(conn, **kwargs)

    async def get_user_group_memberships(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any] | None:
        from app.execution.infrastructure.model_groups import get_user_group_memberships
        return await get_user_group_memberships(conn, **kwargs)

    async def set_user_group_memberships(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_groups import set_user_group_memberships
        return await set_user_group_memberships(conn, **kwargs)

    async def set_users_group_memberships_batch(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, Any]:
        from app.execution.infrastructure.model_groups import set_users_group_memberships_batch
        return await set_users_group_memberships_batch(conn, **kwargs)

    async def resolve_company_employee_user(self, conn: AsyncConnection, **kwargs: Any) -> dict[str, str]:
        from app.execution.infrastructure.model_groups import resolve_company_employee_user
        return await resolve_company_employee_user(conn, **kwargs)


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
        auto_compact_window=(
            int(row["auto_compact_window"])
            if row.get("auto_compact_window") is not None
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
