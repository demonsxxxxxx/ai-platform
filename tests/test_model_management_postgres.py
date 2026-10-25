from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.execution.application.model_control_plane import ModelControlPlaneService
from app.execution.infrastructure.model_management import (
    PostgresModelManagementRepository,
    activate_connection_and_sync,
    list_public_models,
    model_access_excludes_user,
    resolve_run_model,
)
from app.execution.infrastructure.model_security import ModelEndpointSecurityAdapter, api_key_fingerprint
from app.identity.api import CompanyEmployee, CompanyEmployeeDirectoryService
from app.runs.infrastructure.postgres import inherit_run_model
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
            "insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'Admin A')"
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
        await first.execute(
            """
            update model_catalog_entries
            set enabled = true, is_default = true,
                auto_compact_window = 200000, max_output_tokens = 2048
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
            )
            async with second.transaction():
                second_selection = await asyncio.wait_for(
                    resolve_run_model(
                        second,
                        model_id=None,
                        model_value="openai/gpt-5",
                        user_id="user-a",
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
    finally:
        await first.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name))
        )
        for conn in connections:
            await conn.close()


@pytest.mark.asyncio
async def test_model_inheritance_updates_child_and_failure_rolls_back() -> None:
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
            await inherit_run_model(
                conn,
                tenant_id="tenant-a",
                source_run_id="run-source",
                child_run_id="run-child",
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

        with pytest.raises(ValueError, match="run_model_child_source_mismatch"):
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
                await inherit_run_model(
                    conn,
                    tenant_id="tenant-a",
                    source_run_id="run-source",
                    child_run_id="run-invalid",
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


async def _acl_schema_connection():
    """Create an isolated schema for per-model access checks."""

    dsn = _postgres_dsn()
    schema_name = f"model_acl_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row
    )
    await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
    await _set_search_path(conn, schema_name)
    await conn.execute(schema_sql)
    await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
    await conn.execute("insert into tenants(id, name) values ('tenant-b', 'Tenant B')")
    for user_id, tenant_id in (
        ("user-a", "tenant-a"),
        ("user-b", "tenant-a"),
        ("user-foreign", "tenant-b"),
    ):
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
            (user_id, tenant_id, user_id),
        )
    await conn.execute(
        """
        insert into model_gateway_revisions(
          revision, base_url, api_key_ciphertext, key_fingerprint, active, created_by
        ) values (1, 'https://gateway.example', '\\x00', '0123456789abcdef', true, 'user-a')
        """
    )
    return conn, schema_name


async def _drop_acl_schema(conn, schema_name: str) -> None:
    await conn.execute(
        sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name))
    )
    await conn.close()


@pytest.mark.asyncio
async def test_unconfigured_catalog_rows_default_to_all_users_and_stay_listable() -> None:
    """A new row without explicit access configuration retains shared availability."""

    conn, schema_name = await _acl_schema_connection()
    try:
        # Omitting ACL fields must use the database defaults, not a restriction.
        await conn.execute(
            """
            insert into model_catalog_entries(
              model_id, upstream_model_id, display_name, provider, enabled,
              upstream_available, is_default, display_order, auto_compact_window,
              max_output_tokens, first_seen_revision, last_seen_revision
            ) values (
              'legacy-model', 'openai/gpt-5', 'openai/gpt-5', 'compatible', true,
              true, true, 1, 200000, 2048, 1, 1
            )
            """
        )
        cursor = await conn.execute(
            "select access_mode, allowed_user_ids from model_catalog_entries"
            " where model_id = 'legacy-model'"
        )
        assert await cursor.fetchone() == {"access_mode": "all", "allowed_user_ids": []}

        for user_id in ("user-a", "user-b", "user-foreign"):
            selection = await resolve_run_model(
                conn, model_id="legacy-model", model_value=None, user_id=user_id
            )
            assert selection is not None and selection.model_id == "legacy-model"
    finally:
        await _drop_acl_schema(conn, schema_name)


@pytest.mark.asyncio
async def test_persisted_selection_filters_list_and_resolution_by_user() -> None:
    conn, schema_name = await _acl_schema_connection()
    try:
        await conn.execute(
            """
            insert into model_catalog_entries(
              model_id, upstream_model_id, display_name, provider, enabled,
              upstream_available, is_default, display_order, auto_compact_window,
              max_output_tokens, access_mode, allowed_user_ids,
              first_seen_revision, last_seen_revision
            ) values (
              'open-model', 'openai/gpt-5', 'Open', 'compatible', true,
              true, true, 1, 200000, 2048, 'all', '[]'::jsonb, 1, 1
            ), (
              'restricted-model', 'openai/gpt-6', 'Restricted', 'compatible', true,
              true, false, 2, 200000, 2048, 'selected', '["user-a"]'::jsonb, 1, 1
            )
            """
        )

        allowed = await resolve_run_model(
            conn, model_id="restricted-model", model_value=None, user_id="user-a"
        )
        assert allowed is not None and allowed.model_id == "restricted-model"

        # Direct id and direct upstream value are both refused for an excluded user.
        for model_id, model_value in (
            ("restricted-model", None),
            (None, "openai/gpt-6"),
        ):
            with pytest.raises(ValueError, match="model_id_not_available"):
                await resolve_run_model(
                    conn, model_id=model_id, model_value=model_value, user_id="user-b"
                )

        # The default-model path also respects the list.
        default_for_b = await resolve_run_model(
            conn, model_id=None, model_value=None, user_id="user-b"
        )
        assert default_for_b is not None and default_for_b.model_id == "open-model"

        public_for_a = await list_public_models(conn, user_id="user-a")
        assert public_for_a is not None
        assert [model["id"] for model in public_for_a["models"]] == [
            "open-model", "restricted-model",
        ]
        public_for_b = await list_public_models(conn, user_id="user-b")
        assert public_for_b is not None
        assert [model["id"] for model in public_for_b["models"]] == ["open-model"]
        # The default must remain usable by the filtered principal.
        assert public_for_b["default_model_id"] == "open-model"
        # No user list leaks into the ordinary projection.
        assert all("allowed_user_ids" not in model for model in public_for_b["models"])
    finally:
        await _drop_acl_schema(conn, schema_name)


@pytest.mark.asyncio
async def test_publication_roundtrip_preserves_access_and_rolls_back_invalid_grants() -> None:
    conn, schema_name = await _acl_schema_connection()
    encryption_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

    @asynccontextmanager
    async def transaction():
        async with conn.transaction():
            yield conn

    class CatalogUpstream:
        def request(self, **_kwargs):
            return b'{"data":[{"id":"publication-model"}]}'

        def parse_model_ids(self, _response):
            return ["publication-model"]

    async def read_employees():
        return [CompanyEmployee("employee-never-logged-in", "Company employee")]

    service = ModelControlPlaneService(
        transaction_factory=transaction,
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key=encryption_key,
            model_connection_allowed_internal_hosts="",
        ),
        repository=PostgresModelManagementRepository(),
        employee_directory=CompanyEmployeeDirectoryService(read_employees, tenant_id="tenant-a"),
        security=ModelEndpointSecurityAdapter(),
        upstream=CatalogUpstream(),
        attempt_capability_verifier=lambda **_kwargs: False,
    )
    entry = {
        "id": "publication-model", "value": "publication-model",
        "enabled": True, "is_default": True, "order": 1,
        "auto_compact_window": 200000, "max_output_tokens": 2048,
    }

    async def publish(revision, **fields):
        return await service.publish(
            base_url="https://gateway.example", api_key="synthetic-key",
            expected_revision=revision, models=[{**entry, **fields}],
            actor_user_id="user-a", tenant_id="tenant-a",
        )

    try:
        result = await publish(
            1, display_name="Published name", access_mode="selected", allowed_user_ids=["employee-never-logged-in"],
        )
        assert result["revision"] == 2
        assert result["models"][0]["label"] == "Published name"
        assert result["models"][0]["allowed_user_ids"] == ["employee-never-logged-in"]
        assert (await list_public_models(conn, user_id="user-b"))["models"] == []

        cursor = await conn.execute("select id from users where id = 'employee-never-logged-in'")
        assert await cursor.fetchone() is None
        assert await resolve_run_model(conn, user_id="employee-never-logged-in", model_id="publication-model", model_value=None)

        # Older clients omit ACL/name fields: neither saved value may be erased.
        result = await publish(2)
        assert result["revision"] == 3
        assert result["models"][0]["access_mode"] == "selected"
        assert result["models"][0]["allowed_user_ids"] == ["employee-never-logged-in"]
        assert result["models"][0]["label"] == "Published name"

        for users in ([], ["missing-user"], ["user-foreign"]):
            with pytest.raises(ValueError, match="model_user_access_(requires_users|unknown_user)"):
                await publish(3, access_mode="selected", allowed_user_ids=users)
            cursor = await conn.execute("select revision from model_gateway_revisions where active")
            assert (await cursor.fetchone())["revision"] == 3
            cursor = await conn.execute("select allowed_user_ids from model_catalog_entries")
            assert (await cursor.fetchone())["allowed_user_ids"] == ["employee-never-logged-in"]

        result = await publish(3, display_name=" ", access_mode="all", allowed_user_ids=[])
        assert result["models"][0]["label"] == "publication-model"
        assert result["models"][0]["access_mode"] == "all"
        assert result["models"][0]["allowed_user_ids"] == []
        assert (await list_public_models(conn, user_id="user-b"))["default_model_id"] == "publication-model"
    finally:
        await _drop_acl_schema(conn, schema_name)


@pytest.mark.asyncio
async def test_database_rejects_an_explicitly_empty_selection() -> None:
    conn, schema_name = await _acl_schema_connection()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                insert into model_catalog_entries(
                  model_id, upstream_model_id, display_name, provider, enabled,
                  upstream_available, is_default, display_order, auto_compact_window,
                  max_output_tokens, access_mode, allowed_user_ids,
                  first_seen_revision, last_seen_revision
                ) values (
                  'empty-model', 'openai/gpt-7', 'Empty', 'compatible', false,
                  true, false, 3, 200000, 2048, 'selected', '[]'::jsonb, 1, 1
                )
                """
            )
    finally:
        await _drop_acl_schema(conn, schema_name)


@pytest.mark.asyncio
async def test_child_run_keeps_its_frozen_model_after_the_list_changes(monkeypatch) -> None:
    """Revocation gates new admission; an inherited child keeps its snapshot."""

    from fastapi import HTTPException
    from app.routes import runs as run_routes

    # Use the real database ACL lookup at the route's public composition seam.
    monkeypatch.setattr(run_routes, "model_access_excludes_user", model_access_excludes_user)

    conn, schema_name = await _acl_schema_connection()
    try:
        await conn.execute(
            "insert into workspaces(id, tenant_id, name)"
            " values ('workspace-a', 'tenant-a', 'A')"
        )
        await conn.execute(
            "insert into agents(id, tenant_id, name, agent_type)"
            " values ('agent-a', 'tenant-a', 'Agent A', 'chat')"
        )
        await conn.execute(
            "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)"
            " values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A')"
        )
        await conn.execute(
            """
            insert into model_catalog_entries(
              model_id, upstream_model_id, display_name, provider, enabled,
              upstream_available, is_default, display_order, auto_compact_window,
              max_output_tokens, access_mode, allowed_user_ids,
              first_seen_revision, last_seen_revision
            ) values (
              'frozen-model', 'openai/gpt-8', 'Frozen', 'compatible', true,
              true, true, 1, 200000, 2048, 'all', '[]'::jsonb, 1, 1
            )
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
              'harness_chat', 'failed', 'frozen-model', 'openai/gpt-8',
              1, 200000, 2048
            )
            """
        )

        # Revoke the model for this user after the source Run was admitted.
        await conn.execute(
            """
            update model_catalog_entries
            set access_mode = 'selected', allowed_user_ids = '["user-b"]'::jsonb
            where model_id = 'frozen-model'
            """
        )

        # The already-admitted Run keeps its frozen snapshot.
        frozen_cursor = await conn.execute(
            "select model_id, model_value, model_gateway_revision"
            " from runs where id = 'run-source'"
        )
        assert await frozen_cursor.fetchone() == {
            "model_id": "frozen-model",
            "model_value": "openai/gpt-8",
            "model_gateway_revision": 1,
        }

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
            await inherit_run_model(
                conn,
                tenant_id="tenant-a",
                source_run_id="run-source",
                child_run_id="run-child",
            )

        # A new child inherits the exact frozen snapshot...
        child_cursor = await conn.execute(
            """
            select model_id, model_value, model_gateway_revision,
                   auto_compact_window, max_output_tokens
            from runs where tenant_id = 'tenant-a' and id = 'run-child'
            """
        )
        assert await child_cursor.fetchone() == {
            "model_id": "frozen-model",
            "model_value": "openai/gpt-8",
            "model_gateway_revision": 1,
            "auto_compact_window": 200000,
            "max_output_tokens": 2048,
        }
        # ...while the admission gate refuses the revoked user and admits the new one.
        assert await model_access_excludes_user(
            conn, model_id="frozen-model", user_id="user-a"
        )
        assert not await model_access_excludes_user(
            conn, model_id="frozen-model", user_id="user-b"
        )
        # The route's actual child-admission guard rolls the whole transaction back.
        with pytest.raises(HTTPException) as denied:
            async with conn.transaction():
                await conn.execute(
                    "insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, "
                    "execution_kind, status, copied_from_run_id) values "
                    "('run-denied', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', "
                    "'harness_chat', 'queued', 'run-source')"
                )
                await inherit_run_model(
                    conn, tenant_id="tenant-a", source_run_id="run-source", child_run_id="run-denied",
                )
                await run_routes._require_inherited_model_access(
                    conn, snapshot={"model_id": "frozen-model"},
                    principal=SimpleNamespace(user_id="user-a"),
                )
        assert denied.value.status_code == 403
        denied_cursor = await conn.execute("select id from runs where id = 'run-denied'")
        assert await denied_cursor.fetchone() is None

        # A catalog row removed after admission must not reopen the gate.
        await conn.execute(
            "delete from model_catalog_entries where model_id = 'frozen-model'"
        )
        assert await model_access_excludes_user(
            conn, model_id="frozen-model", user_id="user-b"
        )
    finally:
        await _drop_acl_schema(conn, schema_name)
