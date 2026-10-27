from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.execution.infrastructure.model_management import activate_connection_and_sync, publish_models, resolve_run_model
from app.execution.domain.model_catalog import platform_model_id
from app.runs.infrastructure.postgres import replace_run_model_snapshot
from app.execution.infrastructure.model_security import api_key_fingerprint
from tests.support.db_transactions import event_loop_policy as event_loop_policy


POSTGRES_DSN_ENV = "AI_PLATFORM_AGENT_PROFILE_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(
        sql.SQL("set search_path to {}, public").format(sql.Identifier(schema_name))
    )


@pytest.mark.asyncio
async def test_model_resolution_shared_lock_allows_readers_and_blocks_activation() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_lock_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    connections = [
        await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
        for _ in range(3)
    ]
    first, second, activator = connections
    encryption_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    try:
        await first.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        for conn in connections:
            await _set_search_path(conn, schema_name)
        await first.execute(schema_sql)
        await first.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await first.execute(
            "insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'Admin A'), ('user-a', 'tenant-a', 'User A')"
        )
        await activate_connection_and_sync(
            first,
            base_url="https://gateway.example",
            api_key="first-key",
            key_fingerprint=api_key_fingerprint("first-key"),
            encryption_key=encryption_key,
            actor_user_id="admin-a",
            upstream_model_ids=["openai/gpt-5"],
        )
        model_id = platform_model_id("openai/gpt-5")
        await first.execute(
            """
            insert into model_groups(id, tenant_id, code, name, type)
            values ('group-a', 'tenant-a', 'alpha', 'Alpha', 'business')
            """
        )
        await first.execute(
            "insert into model_group_members(tenant_id, group_id, user_id, is_primary) values ('tenant-a', 'group-a', 'user-a', true)"
        )
        await first.execute(
            """
            insert into model_group_models(group_id, model_id, enabled)
            values ('group-a', %s, true)
            """,
            (model_id,),
        )
        await first.execute(
            """
            update model_catalog_entries
            set enabled = true, auto_compact_window = 200000, max_output_tokens = 2048
            """
        )

        activation_started = asyncio.Event()

        async def activate_successor():
            async with activator.transaction():
                activation_started.set()
                return await activate_connection_and_sync(
                    activator,
                    base_url="https://gateway-2.example",
                    api_key="second-key",
                    key_fingerprint=api_key_fingerprint("second-key"),
                    encryption_key=encryption_key,
                    actor_user_id="admin-a",
                    upstream_model_ids=["openai/gpt-5"],
                )

        async with first.transaction():
            first_selection = await resolve_run_model(
                first,
                model_id=None,
                model_value="openai/gpt-5",
                user_id="user-a",
                tenant_id="tenant-a",
                group_id="group-a",
                selection_mode="explicit",
            )
            async with second.transaction():
                second_selection = await asyncio.wait_for(
                    resolve_run_model(
                        second,
                        model_id=None,
                        model_value="openai/gpt-5",
                        user_id="user-a",
                        tenant_id="tenant-a",
                        group_id="group-a",
                        selection_mode="explicit",
                    ),
                    timeout=1,
                )
                activation = asyncio.create_task(activate_successor())
                await activation_started.wait()
                await asyncio.sleep(0.1)
                assert not activation.done()
            await asyncio.sleep(0.1)
            assert not activation.done()
            assert first_selection == second_selection

        revision, _models = await asyncio.wait_for(activation, timeout=2)
        assert revision == 2
        from app.execution.infrastructure.model_groups import update_group
        from app.execution.domain.model_groups import ModelGroupPolicyError
        # The shared admission lock must also fence group metadata/disable writes.
        disable_started = asyncio.Event()
        async def disable():
            async with activator.transaction():
                disable_started.set()
                return await update_group(
                    activator, tenant_id="tenant-a", group_id="group-a", name=None,
                    enabled=False, remark=None, sort_order=None, revision=1, confirm_impact=True,
                )
        async with first.transaction():
            await resolve_run_model(first, model_id=model_id, model_value=None, user_id="user-a",
                                    tenant_id="tenant-a", selection_mode="explicit", usage_scene="chat")
            disabling = asyncio.create_task(disable())
            await disable_started.wait()
            await asyncio.sleep(0.1)
            assert not disabling.done()
        assert (await asyncio.wait_for(disabling, timeout=2))["enabled"] is False
        with pytest.raises(ModelGroupPolicyError, match="group_disabled"):
            await resolve_run_model(first, model_id=model_id, model_value=None, user_id="user-a",
                                    tenant_id="tenant-a", selection_mode="explicit", usage_scene="chat")
    finally:
        await first.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name))
        )
        for conn in connections:
            await conn.close()


@pytest.mark.asyncio
async def test_publish_omitted_legacy_acl_fields_preserves_historical_values() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_publish_acl_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    encryption_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await conn.execute("insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'Admin A')")
        await activate_connection_and_sync(
            conn,
            base_url="https://gateway.example",
            api_key="first-key",
            key_fingerprint=api_key_fingerprint("first-key"),
            encryption_key=encryption_key,
            actor_user_id="admin-a",
            upstream_model_ids=["openai/gpt-5"],
        )
        await conn.execute(
            """
            update model_catalog_entries
            set access_mode = 'selected', allowed_user_ids = '["legacy-user"]'::jsonb,
                is_default = true, enabled = true
            where upstream_model_id = 'openai/gpt-5'
            """
        )
        await publish_models(
            conn,
            expected_revision=1,
            models=[{
                "id": platform_model_id("openai/gpt-5"), "value": "openai/gpt-5", "display_name": "GPT-5",
                "enabled": True, "order": 4, "auto_compact_window": 200000,
                "max_output_tokens": 2048,
            }],
            base_url="https://gateway-2.example",
            api_key="second-key",
            key_fingerprint=api_key_fingerprint("second-key"),
            encryption_key=encryption_key,
            actor_user_id="admin-a",
            upstream_model_ids=["openai/gpt-5"],
        )
        cursor = await conn.execute(
            "select access_mode, allowed_user_ids, is_default, enabled, display_order from model_catalog_entries where upstream_model_id = 'openai/gpt-5'"
        )
        assert await cursor.fetchone() == {
            "access_mode": "selected",
            "allowed_user_ids": ["legacy-user"],
            "is_default": True,
            "enabled": True,
            "display_order": 4,
        }
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_model_reauthorization_updates_child_and_failure_rolls_back() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_snapshot_rollback_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await conn.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')"
        )
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')"
        )
        await conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type)
            values ('agent-a', 'tenant-a', 'Agent A', 'chat')
            """
        )
        await conn.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A')
            """
        )
        await conn.execute(
            """
            insert into model_gateway_revisions(
              revision, base_url, api_key_ciphertext, key_fingerprint, created_by
            ) values (1, 'https://gateway.example', '\\x00', '0123456789abcdef', 'user-a')
            """
        )
        await conn.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id,
              execution_kind, status, model_id, model_value,
              model_gateway_revision, auto_compact_window, max_output_tokens
            ) values (
              'run-source', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
              'harness_chat', 'failed', 'model-default', 'openai/gpt-5',
              1, 200000, 2048
            )
            """
        )

        async with conn.transaction():
            await conn.execute(
                """
                insert into runs(
                  id, tenant_id, workspace_id, session_id, user_id, agent_id,
                  execution_kind, status, copied_from_run_id
                ) values (
                  'run-child', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                  'harness_chat', 'queued', 'run-source'
                )
                """
            )
            await replace_run_model_snapshot(
                conn, tenant_id="tenant-a", run_id="run-child",
                model_id="model-default", model_value="openai/gpt-5", connection_revision=1,
                auto_compact_window=200000, max_output_tokens=2048,
                group_id="group-a", group_code="alpha", group_type="business", group_revision=1,
                selection_mode="explicit", usage_scene="chat", policy_version="model-groups.v1",
            )

        inherited_cursor = await conn.execute(
            """
            select model_id, model_value, model_gateway_revision,
                   auto_compact_window, max_output_tokens
            from runs where tenant_id = 'tenant-a' and id = 'run-child'
            """
        )
        assert await inherited_cursor.fetchone() == {
            "model_id": "model-default",
            "model_value": "openai/gpt-5",
            "model_gateway_revision": 1,
            "auto_compact_window": 200000,
            "max_output_tokens": 2048,
        }

        with pytest.raises(ValueError, match="run_model_reauthorization_failed"):
            async with conn.transaction():
                await conn.execute(
                    """
                    insert into runs(
                      id, tenant_id, workspace_id, session_id, user_id, agent_id,
                      execution_kind, status
                    ) values (
                      'run-invalid', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a',
                      'harness_chat', 'queued'
                    )
                    """
                )
                await conn.execute(
                    """
                    insert into run_events(id, tenant_id, run_id, event_type, stage)
                    values ('event-invalid', 'tenant-a', 'run-invalid', 'run.created', 'admission')
                    """
                )
                await replace_run_model_snapshot(
                    conn, tenant_id="tenant-a", run_id="run-invalid",
                    model_id="model-default", model_value="openai/gpt-5", connection_revision=1,
                    auto_compact_window=200000, max_output_tokens=2048,
                    group_id="group-a", group_code="alpha", group_type="business", group_revision=1,
                    selection_mode="explicit", usage_scene="chat", policy_version="model-groups.v1",
                )

        child_cursor = await conn.execute(
            "select id from runs where tenant_id = 'tenant-a' and id = 'run-invalid'"
        )
        assert await child_cursor.fetchone() is None
        event_cursor = await conn.execute(
            "select id from run_events where tenant_id = 'tenant-a' and id = 'event-invalid'"
        )
        assert await event_cursor.fetchone() is None
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()
