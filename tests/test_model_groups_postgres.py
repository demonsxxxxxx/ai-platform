from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.execution.infrastructure.model_group_batches import batch_group_models
from app.execution.infrastructure.model_groups import (
    ModelGroupPolicyError,
    ensure_default_group,
    resolve_group_model,
    set_group_members,
    set_group_models,
    set_user_group_memberships,
)

POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _prepare_schema(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
    await conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(schema_name)))
    await conn.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
    await conn.execute(
        "insert into tenants(id, name) values ('tenant-a', 'Tenant A'), ('tenant-b', 'Tenant B')"
    )
    await conn.execute(
        """
        insert into users(id, tenant_id, display_name, auth_source, external_id)
        values
          ('user-a', 'tenant-a', 'User A', 'company-login', 'W001'),
          ('user-b', 'tenant-a', 'User B', 'company-login', 'W002'),
          ('same-string', 'tenant-b', 'Other Tenant', 'company-login', 'W001')
        """
    )
    await conn.execute(
        """
        insert into model_gateway_revisions(
          revision, base_url, api_key_ciphertext, key_fingerprint, created_by, active
        ) values (1, 'https://gateway.example', '\\x00', '0123456789abcdef', 'user-a', true)
        """
    )
    await conn.execute(
        """
        insert into model_catalog_entries(
          model_id, upstream_model_id, display_name, provider, enabled,
          upstream_available, is_default, display_order, auto_compact_window,
          max_output_tokens, first_seen_revision, last_seen_revision
        ) values
          ('model-a', 'provider/model-a', 'Model A', 'compatible', true,
           true, false, 1, 200000, 2048, 1, 1),
          ('model-disabled', 'provider/model-disabled', 'Disabled', 'compatible', false,
           true, false, 2, 200000, 2048, 1, 1),
          ('model-b', 'provider/model-b', 'Model B', 'compatible', true,
           true, false, 3, 200000, 2048, 1, 1)
        """
    )


@pytest.mark.asyncio
async def test_model_groups_postgres_atomic_primary_and_tenant_scope() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_groups_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await _prepare_schema(conn, schema_name)
        await conn.execute(
            """
            insert into model_groups(id, tenant_id, code, name, type)
            values ('group-a', 'tenant-a', 'alpha', 'Alpha', 'business'),
                   ('group-b', 'tenant-a', 'beta', 'Beta', 'business')
            """
        )
        await set_user_group_memberships(
            conn, tenant_id="tenant-a", user_id="user-a",
            group_ids=["group-a", "group-b"], primary_group_id="group-b", expected_revision=1,
        )
        await set_user_group_memberships(
            conn, tenant_id="tenant-a", user_id="user-b",
            group_ids=["group-a"], primary_group_id=None, expected_revision=1,
        )
        cursor = await conn.execute(
            "select user_id, group_id, is_primary from model_group_members order by user_id, group_id"
        )
        assert await cursor.fetchall() == [
            {"user_id": "user-a", "group_id": "group-a", "is_primary": False},
            {"user_id": "user-a", "group_id": "group-b", "is_primary": True},
            {"user_id": "user-b", "group_id": "group-a", "is_primary": True},
        ]

        result = await set_group_members(
            conn, tenant_id="tenant-a", group_id="group-a", revision=3,
            members=[
                {"user_id": "user-a", "is_primary": True},
                {"user_id": "user-b", "is_primary": True},
            ],
        )
        assert result["revision"] == 4
        cursor = await conn.execute(
            "select user_id from model_group_members where group_id = 'group-a' and is_primary order by user_id"
        )
        assert [row["user_id"] for row in await cursor.fetchall()] == ["user-a", "user-b"]

        with pytest.raises(ModelGroupPolicyError, match="model_group_member_unknown_user"):
            await set_group_members(
                conn, tenant_id="tenant-a", group_id="group-a", revision=4,
                members=[{"user_id": "missing", "is_primary": True}],
            )
        cursor = await conn.execute(
            "select user_id from model_group_members where group_id = 'group-a' order by user_id"
        )
        assert [row["user_id"] for row in await cursor.fetchall()] == ["user-a", "user-b"]

        with pytest.raises(ModelGroupPolicyError, match="group_not_available"):
            await set_user_group_memberships(
                conn, tenant_id="tenant-b", user_id="same-string",
                group_ids=["group-a"], primary_group_id="group-a", expected_revision=1,
            )
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_model_groups_postgres_disabled_model_is_configurable_but_not_admitted() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_groups_models_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await _prepare_schema(conn, schema_name)
        await conn.execute(
            "insert into model_groups(id, tenant_id, code, name, type) values ('group-a', 'tenant-a', 'alpha', 'Alpha', 'business')"
        )
        await set_user_group_memberships(
            conn, tenant_id="tenant-a", user_id="user-a",
            group_ids=["group-a"], primary_group_id="group-a", expected_revision=1,
        )
        configured = await set_group_models(
            conn, tenant_id="tenant-a", group_id="group-a", revision=2,
            models=[{"model_id": "model-disabled", "enabled": True, "priority": 10, "weight": 100}],
        )
        assert configured["revision"] == 3
        same = await set_group_models(
            conn, tenant_id="tenant-a", group_id="group-a", revision=3,
            models=[{"model_id": "model-disabled", "enabled": True, "priority": 10, "weight": 100}],
        )
        assert same["revision"] == 3
        cursor = await conn.execute(
            "select enabled from model_group_models where group_id = 'group-a' and model_id = 'model-disabled'"
        )
        assert (await cursor.fetchone())["enabled"] is True

        with pytest.raises(ModelGroupPolicyError, match="model_not_available"):
            await resolve_group_model(
                conn, tenant_id="tenant-a", user_id="user-a", group_id="group-a",
                model_id="model-disabled", model_value="provider/model-disabled",
                selection_mode="explicit", usage_scene="independent",
            )

        await set_group_models(
            conn, tenant_id="tenant-a", group_id="group-a", revision=3,
            models=[{"model_id": "model-a", "enabled": True, "priority": 10, "weight": 100}],
        )
        selected = await resolve_group_model(
            conn, tenant_id="tenant-a", user_id="user-a", group_id="group-a",
            model_id="model-a", model_value="provider/model-a",
            selection_mode="explicit", usage_scene="independent",
        )
        assert selected.model_id == "model-a"
        assert selected.model_value == "provider/model-a"
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_model_groups_postgres_batch_preview_apply_rollback_and_revision_fence() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_groups_batch_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await _prepare_schema(conn, schema_name)
        await conn.execute(
            """
            insert into model_groups(id, tenant_id, code, name, type)
            values ('group-a', 'tenant-a', 'alpha', 'Alpha', 'business'),
                   ('group-b', 'tenant-a', 'beta', 'Beta', 'business')
            """
        )
        preview = await batch_group_models(
            conn, tenant_id="tenant-a", group_ids=["group-a"], model_ids=["model-a"],
            operation="add", expected_group_revisions={"group-a": 1}, preview=True,
        )
        assert preview["preview"] is True
        assert preview["groups"][0]["after"]["affected_model_ids"] == ["model-a"]
        assert await (await conn.execute("select count(*) as count from model_group_models")).fetchone() == {"count": 0}
        assert (await (await conn.execute("select revision from model_groups where id = 'group-a'")).fetchone())["revision"] == 1

        applied = await batch_group_models(
            conn, tenant_id="tenant-a", group_ids=["group-a"], model_ids=["model-a"],
            operation="add", expected_group_revisions={"group-a": 1}, preview=False,
        )
        assert applied["changed"] is True
        assert applied["groups"][0]["after"]["revision"] == 2
        assert await (await conn.execute("select model_id from model_group_models where group_id = 'group-a'")).fetchone() == {"model_id": "model-a"}

        with pytest.raises(ModelGroupPolicyError, match="model_policy_changed"):
            await batch_group_models(
                conn, tenant_id="tenant-a", group_ids=["group-a"], model_ids=["model-b"],
                operation="add", expected_group_revisions={"group-a": 1}, preview=False,
            )
        assert await (await conn.execute("select model_id from model_group_models where group_id = 'group-a'")).fetchone() == {"model_id": "model-a"}

        with pytest.raises(ModelGroupPolicyError, match="model_not_available"):
            async with conn.transaction():
                await batch_group_models(
                    conn, tenant_id="tenant-a", group_ids=["group-b"], model_ids=["missing-model"],
                    operation="add", expected_group_revisions={"group-b": 1}, preview=False,
                )
        assert await (await conn.execute("select count(*) as count from model_group_models where group_id = 'group-b'")).fetchone() == {"count": 0}
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_model_groups_postgres_default_business_exclusivity_is_tenant_scoped() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_groups_tenant_trigger_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await _prepare_schema(conn, schema_name)
        await conn.execute(
            """
            insert into model_groups(id, tenant_id, code, name, type)
            values ('default-a', 'tenant-a', 'default', '普通默认组', 'ordinary_default'),
                   ('business-b', 'tenant-b', 'beta', 'Beta', 'business')
            """
        )
        await conn.execute("insert into model_group_models(group_id, model_id) values ('default-a', 'model-a')")
        await conn.execute("insert into model_group_models(group_id, model_id) values ('business-b', 'model-a')")
        assert await (await conn.execute("select count(*) as count from model_group_models where model_id = 'model-a'")).fetchone() == {"count": 2}
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_model_groups_postgres_migration_apply_is_confirmed_and_idempotent(monkeypatch) -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_groups_migration_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await _prepare_schema(conn, schema_name)
        import tools.model_group_migration as migration

        @asynccontextmanager
        async def transaction():
            async with conn.transaction():
                yield conn

        monkeypatch.setattr(migration, "transaction", transaction)
        mapping = {
            "version": migration.VERSION,
            "groups": [{"tenant_id": "tenant-a", "code": "alpha", "name": "Alpha", "models": [{"model_id": "model-a"}]}],
            "users": [{"tenant_id": "tenant-a", "user_id": "user-a", "group_codes": ["alpha"], "primary_group_code": "alpha"}],
        }
        mapping["expected_state"] = (await migration.preview_mapping(mapping))["expected_state"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump(mapping, handle)
            mapping_path = Path(handle.name)
        try:
            first = await migration.apply_mapping(mapping_path)
            assert first["status"] == "applied"
            group_row = await (await conn.execute("select id, revision from model_groups where tenant_id = 'tenant-a' and code = 'alpha'")).fetchone()
            user_row = await (await conn.execute("select model_group_membership_revision from users where id = 'user-a'")).fetchone()
            second = await migration.apply_mapping(mapping_path)
            assert second["changed_groups"] == 0
            assert second["changed_users"] == 0
            assert await (await conn.execute("select revision from model_groups where id = %s", (group_row["id"],))).fetchone() == {"revision": group_row["revision"]}
            assert await (await conn.execute("select model_group_membership_revision from users where id = 'user-a'")).fetchone() == user_row
        finally:
            mapping_path.unlink(missing_ok=True)
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_model_groups_postgres_default_group_is_unique_and_implicit() -> None:
    dsn = _postgres_dsn()
    schema_name = f"model_groups_default_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await _prepare_schema(conn, schema_name)
        first = await ensure_default_group(conn, tenant_id="tenant-a")
        second = await ensure_default_group(conn, tenant_id="tenant-a")
        assert first == second
        cursor = await conn.execute(
            "select count(*) as count from model_group_members where tenant_id = 'tenant-a'"
        )
        assert (await cursor.fetchone())["count"] == 0
        from app.execution.infrastructure.model_groups import get_admin_group_details, list_admin_groups
        await conn.execute("insert into users(id, tenant_id, display_name, auth_source, external_id) values ('user-c', 'tenant-a', 'User C', 'company-login', 'W003')")
        admin_groups = await list_admin_groups(conn, tenant_id="tenant-a")
        default_summary = next(item for item in admin_groups if item["id"] == first)
        assert default_summary["members_are_implicit"] is True
        assert default_summary["member_count"] == 3
        details = await get_admin_group_details(conn, tenant_id="tenant-a", group_id=first)
        assert details["members_are_implicit"] is True
        assert details["member_count"] == 3
        assert details["members"] == []
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_membership_fences_both_editing_surfaces_and_requires_replacement_primary():
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = f"group_revisions_{uuid.uuid4().hex}"
    try:
        await _prepare_schema(conn, schema)
        await conn.execute("insert into model_groups(id,tenant_id,code,name,type) values ('a','tenant-a','a','A','business'),('b','tenant-a','b','B','business'),('c','tenant-a','c','C','business')")
        async with conn.transaction():
            first = await set_user_group_memberships(conn, tenant_id="tenant-a", user_id="user-a", group_ids=["a", "b"], primary_group_id="a", expected_revision=1)
        assert first["revision"] == 2
        for primary, expected in [(None, 2), ("b", 1)]:
            with pytest.raises(ModelGroupPolicyError):
                async with conn.transaction():
                    await set_user_group_memberships(conn, tenant_id="tenant-a", user_id="user-a", group_ids=["b", "c"], primary_group_id=primary, expected_revision=expected)
        with pytest.raises(ModelGroupPolicyError, match="model_policy_changed"):
            async with conn.transaction():
                await set_group_members(conn, tenant_id="tenant-a", group_id="a", revision=1, members=[])
        async with conn.transaction():
            await set_group_members(conn, tenant_id="tenant-a", group_id="b", revision=2, members=[{"user_id": "user-a", "is_primary": True}])
        with pytest.raises(ModelGroupPolicyError, match="revision_conflict"):
            async with conn.transaction():
                await set_user_group_memberships(conn, tenant_id="tenant-a", user_id="user-a", group_ids=["a"], primary_group_id="a", expected_revision=2)
        assert (await (await conn.execute("select revision from model_groups where id='a'")).fetchone())["revision"] == 3
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await conn.close()


@pytest.mark.asyncio
async def test_confirmed_migration_removes_covered_users_old_grants_and_configures_default(monkeypatch, tmp_path):
    import tools.model_group_migration as migration
    from app.execution.infrastructure.model_groups import list_public_group_models
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = f"group_migrate_full_{uuid.uuid4().hex}"
    try:
        await _prepare_schema(conn, schema)
        await conn.execute("insert into model_groups(id,tenant_id,code,name,type) values ('old','tenant-a','old','Old','business')")
        await conn.execute("insert into model_group_models(group_id,model_id) values ('old','model-b')")
        await conn.execute("insert into model_group_members(tenant_id,group_id,user_id,is_primary) values ('tenant-a','old','user-a',true),('tenant-a','old','user-b',true)")
        @asynccontextmanager
        async def transaction():
            async with conn.transaction():
                yield conn
        monkeypatch.setattr(migration, "transaction", transaction)
        mapping = {"version": migration.VERSION,
                   "groups": [{"tenant_id":"tenant-a", "code":"default", "name":"普通默认组", "models":[{"model_id":"model-a"}]}],
                   "users": [{"tenant_id":"tenant-a", "user_id":"user-a", "group_codes":[], "primary_group_code":None}]}
        preview = await migration.preview_mapping(mapping)
        change = next(c for c in preview["user_changes"] if c["user_id"] == "user-a")
        assert change["before_groups"] == {"old": True}
        assert change["after_groups"] == {}
        assert change["after_models"] == ["model-a"]
        assert preview["retained"]["users"] == [{"tenant_id":"tenant-a", "user_id":"user-b", "groups":{"old":True}, "models":["model-b"]}]
        assert preview["retained"]["groups"][0]["code"] == "old"
        assert preview["covered_tenants"] == ["tenant-a"]
        assert preview["outside_tenants"] == ["default", "tenant-b"]
        assert (await (await conn.execute("select count(*) as n from model_groups")).fetchone())["n"] == 1
        mapping["expected_state"] = preview["expected_state"]
        path = tmp_path / "mapping.json"
        path.write_text(json.dumps(mapping), encoding="utf-8")
        await conn.execute("update model_groups set revision=revision+1 where id='old'")
        with pytest.raises(ValueError, match="preview_changed"):
            await migration.apply_mapping(path)
        assert (await (await conn.execute("select count(*) as n from model_group_members")).fetchone())["n"] == 2
        mapping["expected_state"] = (await migration.preview_mapping(mapping))["expected_state"]
        path.write_text(json.dumps(mapping), encoding="utf-8")
        await conn.execute("insert into workspaces(id,tenant_id,name) values ('w','tenant-a','W')")
        await conn.execute("insert into agents(id,tenant_id,name,agent_type) values ('a','tenant-a','A','chat')")
        await conn.execute("insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title) values ('s','tenant-a','w','user-a','a','S')")
        await conn.execute("insert into runs(id,tenant_id,workspace_id,user_id,agent_id,session_id,execution_kind,status) values ('legacy-active','tenant-a','w','user-a','a','s','harness_chat','running')")
        with pytest.raises(ValueError, match="active_runs"):
            await migration.apply_mapping(path)
        await conn.execute("update runs set status='succeeded' where id='legacy-active'")
        await migration.apply_mapping(path)
        assert await (await conn.execute("select user_id from model_group_members order by user_id")).fetchall() == [{"user_id":"user-b"}]
        assert (await migration.apply_mapping(path))["changed_users"] == 0
        mapping.pop("expected_state")
        path.write_text(json.dumps(mapping), encoding="utf-8")
        with pytest.raises(ValueError, match="preview_changed"):
            await migration.apply_mapping(path)
        default = await ensure_default_group(conn, tenant_id="tenant-a")
        for scene in ("chat", "expert"):
            with pytest.raises(ModelGroupPolicyError):
                await list_public_group_models(conn, tenant_id="tenant-a", user_id="user-a", group_id=default, usage_scene=scene)
        with pytest.raises(ModelGroupPolicyError):
            await list_public_group_models(conn, tenant_id="tenant-a", user_id="user-b", group_id=default, usage_scene="independent")
        allowed = await resolve_group_model(conn, tenant_id="tenant-a", user_id="user-a", group_id=None, model_id="model-a", model_value="provider/model-a", selection_mode="explicit", usage_scene="skill")
        assert allowed.group_type == "ordinary_default"
        from app.execution.infrastructure.model_groups import list_groups_for_user
        await conn.execute("update model_groups set enabled=false where id='old'")
        disabled_membership = await list_groups_for_user(conn, tenant_id="tenant-a", user_id="user-b")
        assert len(disabled_membership) == 1
        assert disabled_membership[0]["id"] == "old"
        assert disabled_membership[0]["is_primary"] is True
        assert disabled_membership[0]["availability"] == "group_disabled"
        assert disabled_membership[0]["models"] == []
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await conn.close()


@pytest.mark.asyncio
async def test_group_snapshot_binding_replay_and_completion_boundaries():
    import base64
    from app.runs.infrastructure.postgres import bind_run_model, replace_run_model_snapshot
    from app.execution.infrastructure.model_management import get_run_model_completion_connection, get_run_connection
    from app.execution.infrastructure.model_security import encrypt_api_key
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = f"group_snapshot_{uuid.uuid4().hex}"
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    fields = dict(group_id="g", group_code="sales", group_type="business", group_revision=1,
                  selection_mode="explicit", usage_scene="chat", policy_version="model-groups.v1")
    model = dict(model_id="model-a", model_value="provider/model-a", connection_revision=1,
                 auto_compact_window=200000, max_output_tokens=2048)
    try:
        await _prepare_schema(conn, schema)
        await conn.execute("update model_gateway_revisions set api_key_ciphertext=%s", (encrypt_api_key("synthetic-key", revision=1, encoded_key=key),))
        await conn.execute("insert into workspaces(id,tenant_id,name) values ('w','tenant-a','W')")
        await conn.execute("insert into agents(id,tenant_id,name,agent_type) values ('a','tenant-a','A','chat')")
        await conn.execute("insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title) values ('s','tenant-a','w','user-a','a','S')")
        await conn.execute("insert into runs(id,tenant_id,workspace_id,user_id,agent_id,session_id,execution_kind,status,model_id,model_value) values ('old','tenant-a','w','user-a','a','s','harness_chat','failed','model-a','provider/model-a')")
        await conn.execute("insert into runs(id,tenant_id,workspace_id,user_id,agent_id,session_id,execution_kind,status,copied_from_run_id) values ('r','tenant-a','w','user-a','a','s','harness_chat','queued','old')")
        original = await (await conn.execute("select * from runs where id='old'")).fetchone()
        with pytest.raises(ValueError, match="snapshot_invalid"):
            await bind_run_model(conn, tenant_id="tenant-a", run_id="r", **model)
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute("update runs set model_group_id='g' where id='r'")
        await replace_run_model_snapshot(conn, tenant_id="tenant-a", run_id="r", **model, **fields)
        assert await (await conn.execute("select * from runs where id='old'")).fetchone() == original
        from app.conversations.infrastructure.postgres import get_authorized_lambchat_session
        restored = await get_authorized_lambchat_session(conn, tenant_id="tenant-a", user_id="user-a", session_id="s")
        assert restored["latest_model_selection"] == {"model_id":"model-a", "model":"provider/model-a", "group_id":"g"}
        assert await get_authorized_lambchat_session(conn, tenant_id="tenant-a", user_id="user-b", session_id="s") is None
        assert await get_authorized_lambchat_session(conn, tenant_id="tenant-b", user_id="user-a", session_id="s") is None
        for statement in ["model_group_type='ordinary_default',model_group_code='default'", "model_group_revision=0", "model_policy_version='unknown'"]:
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute("update runs set " + statement + " where id='r'")
        spec = {"schema_version":"ai-platform.execution-spec.v4", "tenant_id":"tenant-a", "workspace_id":"w",
                "user_id":"user-a", "session_id":"s", "run_id":"r", "agent_id":"a", "execution_kind":"harness_chat", "skill_id":None,
                "model_id":"model-a", "model_value":"provider/model-a",
                "model_gateway_revision":1, "model_auto_compact_window":200000, "model_max_output_tokens":2048,
                "model_group_id":"g", "model_group_code":"sales", "model_group_type":"business", "model_group_revision":1,
                "model_selection_mode":"explicit", "usage_scene":"chat", "model_policy_version":"model-groups.v1"}
        raw = json.dumps(spec)
        await conn.execute("""insert into run_attempts(id,tenant_id,run_id,ordinal,status,owner_kind,owner_id,queue_attempt_id,
            execution_spec_schema_version,execution_spec_json,execution_spec_canonical_json,execution_spec_sha256)
            values ('attempt','tenant-a','r',1,'created','queue_worker','worker','queue-attempt',%s,%s::jsonb,%s,
                    encode(sha256(convert_to(%s,'UTF8')),'hex'))""", (spec["schema_version"],raw,raw,raw))
        with pytest.raises(ValueError, match="reauthorization_failed"):
            await replace_run_model_snapshot(conn, tenant_id="tenant-a", run_id="r", **model, **fields)
        for status in ('queued', 'claimed', 'running', 'succeeded'):
            await conn.execute("update run_attempts set status=%s, owner_generation=owner_generation+1, finished_at=case when %s='succeeded' then now() else null end where id='attempt'", (status, status))
        async def completion(user="user-a"):
            return await get_run_model_completion_connection(conn, tenant_id="tenant-a", user_id=user, session_id="s", run_id="r", encryption_key=key)
        assert await completion() is not None
        assert await completion("user-b") is None
        await conn.execute("update runs set model_group_revision=2 where id='r'")
        assert await completion() is None
        await conn.execute("update runs set model_group_revision=1, status='running' where id='r'")
        await conn.execute("insert into sandbox_leases(id,tenant_id,workspace_id,user_id,session_id,run_id,attempt_id,sandbox_mode,lease_payload_json) values ('lease','tenant-a','w','user-a','s','r','attempt','test',%s::jsonb)", (json.dumps({"owner_generation": 5}),))
        async def gateway(attempt="attempt"):
            return await get_run_connection(conn, run_id="r", attempt_id=attempt, model_value="provider/model-a", encryption_key=key)
        assert await gateway() is not None
        assert await gateway("wrong") is None
        await conn.execute("update runs set model_group_revision=2 where id='r'")
        assert await gateway() is None
        await conn.execute("update runs set model_group_revision=1 where id='r'")
        default_id = await ensure_default_group(conn, tenant_id="tenant-a")
        with pytest.raises(ModelGroupPolicyError, match="model_group_active_runs"):
            await set_group_models(conn, tenant_id="tenant-a", group_id=default_id, revision=1,
                                   models=[{"model_id":"model-a"}])
        with pytest.raises(ModelGroupPolicyError, match="model_group_active_runs"):
            await batch_group_models(conn, tenant_id="tenant-a", group_ids=[default_id], model_ids=["model-a"],
                                     operation="add", expected_group_revisions={default_id:1})
        await conn.execute("update sandbox_leases set released_at=now() where id='lease'")
        assert await gateway() is None
        await conn.execute("update runs set status='succeeded' where id='r'")
        await conn.execute("update runs set status='running' where id='old'")
        await conn.execute("insert into model_groups(id,tenant_id,code,name,type) values ('legacy-group','tenant-a','legacy','Legacy','business')")
        await conn.execute("insert into model_group_models(group_id,model_id) values ('legacy-group','model-a')")
        with pytest.raises(ModelGroupPolicyError, match="model_group_active_runs"):
            await set_group_models(conn, tenant_id="tenant-a", group_id=default_id, revision=1,
                                   models=[{"model_id":"model-a"}])
        with pytest.raises(ModelGroupPolicyError, match="model_group_active_runs"):
            await batch_group_models(conn, tenant_id="tenant-a", group_ids=[default_id], model_ids=["model-a"],
                                     operation="replace", expected_group_revisions={default_id:1,"legacy-group":1})
        await conn.execute("update runs set status='succeeded' where id='old'")
        await conn.execute("delete from model_group_models where group_id='legacy-group'")
        converted = await set_group_models(conn, tenant_id="tenant-a", group_id=default_id, revision=1,
                                          models=[{"model_id":"model-a"}])
        assert converted["revision"] == 2
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await conn.close()
