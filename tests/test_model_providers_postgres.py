"""Supplier identity, independent routing and non-destructive retirement."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import json
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from test_model_groups_postgres import _postgres_dsn, _prepare_schema
from app.execution.domain.model_catalog import platform_model_id
from app.execution.infrastructure.model_management import (
    get_active_connection, get_run_connection, list_admin_models, publish_models, resolve_run_model,
)
from app.execution.infrastructure.model_providers import create_provider, list_providers, provider_impact, retire_provider
from app.execution.infrastructure.model_security import api_key_fingerprint
from app.execution.infrastructure.model_group_batches import batch_group_models
from app.execution.infrastructure.model_groups import ensure_default_group, set_group_models, set_user_group_memberships

KEY = base64.urlsafe_b64encode(b"p" * 32).decode()


async def publish(conn, provider_id, *, revision=None, secret="synthetic-key", values=("shared-model",)):
    async with conn.transaction():
        return await publish_models(conn, provider_id=provider_id, expected_revision=revision,
            base_url="https://shared.example", api_key=secret, key_fingerprint=api_key_fingerprint(secret),
            encryption_key=KEY, actor_user_id="user-a", upstream_model_ids=list(values),
            models=[{"id": platform_model_id(value, provider_id), "value": value, "display_name": value,
                "enabled": True, "order": index + 1, "auto_compact_window": 100000, "max_output_tokens": 2048}
                for index, value in enumerate(values)])


@pytest.mark.asyncio
async def test_supplier_identity_survives_default_batch_and_mapping_paths(monkeypatch, tmp_path):
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = "supplier_group_paths_" + uuid.uuid4().hex
    try:
        await _prepare_schema(conn, schema)
        a = await create_provider(conn, name="Supplier A")
        b = await create_provider(conn, name="Supplier B")
        ra, _ = await publish(conn, a["id"])
        rb, _ = await publish(conn, b["id"])
        ma, mb = (platform_model_id("shared-model", provider["id"]) for provider in (a, b))

        default_id = await ensure_default_group(conn, tenant_id="tenant-a")
        configured_default = await set_group_models(
            conn, tenant_id="tenant-a", group_id=default_id, revision=1,
            models=[{"model_id": ma, "enabled": False}, {"model_id": mb, "enabled": True}],
        )
        assert configured_default["default_model_id"] == mb
        async with conn.transaction():
            selected = await resolve_run_model(
                conn, tenant_id="tenant-a", user_id="user-a", group_id=default_id,
                model_id=None, model_value=None, selection_mode=None, usage_scene="expert",
            )
        assert selected is not None
        assert (selected.model_id, selected.connection_revision, selected.selection_mode) == (mb, rb, "explicit")

        await conn.execute("insert into model_groups(id,tenant_id,code,name,type) values ('batch','tenant-a','batch','Batch','business')")
        await set_group_models(
            conn, tenant_id="tenant-a", group_id="batch", revision=1,
            models=[{"model_id": ma}], default_model_id=ma,
        )
        preview = await batch_group_models(
            conn, tenant_id="tenant-a", group_ids=["batch"], model_ids=[mb], operation="add",
            expected_group_revisions={"batch": 2}, preview=True,
        )
        assert preview["groups"][0]["after"]["default_model_id"] == ma
        await batch_group_models(
            conn, tenant_id="tenant-a", group_ids=["batch"], model_ids=[mb], operation="add",
            expected_group_revisions={"batch": 2},
        )
        batch_providers = await (await conn.execute(
            "select c.provider_id from model_group_models gm join model_catalog_entries c on c.model_id = gm.model_id where gm.group_id = 'batch' order by c.provider_id"
        )).fetchall()
        assert {row["provider_id"] for row in batch_providers} == {a["id"], b["id"]}

        import tools.model_group_migration as migration

        @asynccontextmanager
        async def transaction():
            async with conn.transaction():
                yield conn

        monkeypatch.setattr(migration, "transaction", transaction)
        mapping = {
            "version": migration.VERSION,
            "groups": [{"tenant_id": "tenant-a", "code": "mapped", "name": "Mapped", "models": [{"model_id": mb}]}],
            "users": [],
        }
        preview = await migration.preview_mapping(mapping)
        assert {row["provider_id"] for row in preview["expected_state"]["catalog"] if row["model_id"] in {ma, mb}} == {a["id"], b["id"]}
        mapping["expected_state"] = preview["expected_state"]
        mapping_path = tmp_path / "provider-model-group-mapping.json"
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
        assert (await migration.apply_mapping(mapping_path))["status"] == "applied"
        mapped_group = await (await conn.execute(
            "select g.default_model_id, c.provider_id from model_groups g join model_group_models gm on gm.group_id = g.id join model_catalog_entries c on c.model_id = gm.model_id where g.code = 'mapped'"
        )).fetchone()
        assert mapped_group == {"default_model_id": mb, "provider_id": b["id"]}
        inventory = await migration.inventory()
        assert {row["model_id"]: row["provider_id"] for row in inventory["catalog"] if row["model_id"] in {ma, mb}} == {ma: a["id"], mb: b["id"]}
    finally:
        await conn.execute("set search_path to public")
        await conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await conn.close()


@pytest.mark.asyncio
async def test_suppliers_same_url_same_model_keep_separate_credentials_and_group_routes():
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = "suppliers_" + uuid.uuid4().hex
    try:
        await _prepare_schema(conn, schema)
        a = await create_provider(conn, name="Supplier A")
        b = await create_provider(conn, name="Supplier B")
        unconfigured = await create_provider(conn, name="Not configured")
        initial_summaries = {row["id"]: row for row in await list_providers(conn)}
        for provider in (a, b, unconfigured):
            summary = initial_summaries[provider["id"]]
            assert summary["revision"] is None
            assert summary["configured"] is False
            assert summary["model_count"] == 0
            assert summary["enabled_model_count"] == 0
            assert summary["missing_model_count"] == 0

        ra, _ = await publish(
            conn, a["id"], secret="synthetic-a",
            values=("shared-model", "unused", "archived-model"),
        )
        rb, _ = await publish(conn, b["id"], secret="synthetic-b")
        archived_model = platform_model_id("archived-model", a["id"])
        missing_model = platform_model_id("unused", a["id"])
        # The missing model remains enabled, so enabled and missing counts overlap.
        await conn.execute(
            "update model_catalog_entries set upstream_available = false where model_id = %s",
            (missing_model,),
        )
        missing_state = await (await conn.execute(
            "select enabled, upstream_available from model_catalog_entries where model_id = %s",
            (missing_model,),
        )).fetchone()
        assert missing_state == {"enabled": True, "upstream_available": False}
        await conn.execute(
            "update model_catalog_entries set enabled = true, upstream_available = false, archived_at = now() where model_id = %s",
            (archived_model,),
        )
        summaries = {row["id"]: row for row in await list_providers(conn)}
        assert set(summaries[a["id"]]) == {
            "id", "name", "revision", "configured", "model_count",
            "enabled_model_count", "missing_model_count",
        }
        assert summaries[a["id"]]["revision"] == ra
        assert summaries[a["id"]]["configured"] is True
        assert summaries[a["id"]]["model_count"] == 2
        assert summaries[a["id"]]["enabled_model_count"] == 2
        assert summaries[a["id"]]["missing_model_count"] == 1
        assert summaries[b["id"]]["revision"] == rb
        assert summaries[b["id"]]["configured"] is True
        assert summaries[b["id"]]["model_count"] == 1
        assert summaries[b["id"]]["enabled_model_count"] == 1
        assert summaries[b["id"]]["missing_model_count"] == 0
        assert summaries[unconfigured["id"]]["configured"] is False
        assert summaries[unconfigured["id"]]["model_count"] == 0
        assert summaries[unconfigured["id"]]["enabled_model_count"] == 0
        assert summaries[unconfigured["id"]]["missing_model_count"] == 0
        ma, mb = (platform_model_id("shared-model", p["id"]) for p in (a, b))
        assert ma != mb
        await conn.execute("insert into model_groups(id,tenant_id,code,name,type) values ('mixed','tenant-a','mixed','Mixed','business')")
        await set_user_group_memberships(conn, tenant_id="tenant-a", user_id="user-a", group_ids=["mixed"], primary_group_id="mixed", expected_revision=1)
        await set_group_models(conn, tenant_id="tenant-a", group_id="mixed", revision=2,
            models=[{"model_id": ma}, {"model_id": mb}], default_model_id=ma)
        for model, revision, secret in ((ma, ra, "synthetic-a"), (mb, rb, "synthetic-b")):
            async with conn.transaction():
                selected = await resolve_run_model(conn, tenant_id="tenant-a", user_id="user-a", group_id="mixed",
                    model_id=model, model_value="shared-model", selection_mode="explicit", usage_scene="chat")
            assert selected.connection_revision == revision
            connection = await get_active_connection(conn, provider_id=a["id"] if model == ma else b["id"], encryption_key=KEY)
            assert connection.api_key == secret
        with pytest.raises(ValueError, match="model_selection_conflict"):
            async with conn.transaction():
                await resolve_run_model(conn, tenant_id="tenant-a", user_id="user-a", group_id="mixed",
                    model_id=None, model_value="shared-model", selection_mode="explicit", usage_scene="chat")
        before_b = await list_admin_models(conn, provider_id=b["id"])
        await conn.execute("insert into workspaces(id,tenant_id,name) values ('w','tenant-a','W')")
        await conn.execute("insert into agents(id,tenant_id,name,agent_type) values ('agent','tenant-a','A','chat')")
        await conn.execute("insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title) values ('s','tenant-a','w','user-a','agent','S')")
        await conn.execute("""insert into runs(id,tenant_id,workspace_id,user_id,agent_id,session_id,execution_kind,status,
            model_id,model_value,model_gateway_revision,auto_compact_window,max_output_tokens,
            model_group_id,model_group_code,model_group_type,model_group_revision,model_selection_mode,usage_scene,model_policy_version)
            values ('pinned','tenant-a','w','user-a','agent','s','harness_chat','queued',%s,'shared-model',%s,100000,2048,
            'mixed','mixed','business',3,'explicit','chat','model-groups.v1')""", (ma, ra))
        spec = {"schema_version": "ai-platform.execution-spec.v4", "tenant_id": "tenant-a", "workspace_id": "w", "user_id": "user-a", "session_id": "s", "run_id": "pinned", "agent_id": "agent", "execution_kind": "harness_chat", "skill_id": None,
            "model_id": ma, "model_value": "shared-model",
            "model_gateway_revision": ra, "model_auto_compact_window": 100000, "model_max_output_tokens": 2048,
            "model_group_id": "mixed", "model_group_code": "mixed", "model_group_type": "business", "model_group_revision": 3,
            "model_selection_mode": "explicit", "usage_scene": "chat", "model_policy_version": "model-groups.v1"}
        raw = json.dumps(spec)
        await conn.execute("""insert into run_attempts(id,tenant_id,run_id,ordinal,status,owner_kind,owner_id,queue_attempt_id,
            execution_spec_schema_version,execution_spec_json,execution_spec_canonical_json,execution_spec_sha256)
            values ('pinned-attempt','tenant-a','pinned',1,'created','queue_worker','worker','queue-attempt',%s,%s::jsonb,%s,
              encode(sha256(convert_to(%s,'UTF8')),'hex'))""", (spec["schema_version"], raw, raw, raw))
        for status in ('queued', 'claimed', 'running'):
            await conn.execute("update run_attempts set status=%s, owner_generation=owner_generation+1 where id='pinned-attempt'", (status,))
        await conn.execute("""insert into sandbox_leases(id,tenant_id,workspace_id,user_id,session_id,run_id,attempt_id,sandbox_mode,lease_payload_json)
            values ('pinned-lease','tenant-a','w','user-a','s','pinned','pinned-attempt','test','{"owner_generation":4}')""")
        frozen = await (await conn.execute("select execution_spec_canonical_json,execution_spec_sha256 from run_attempts where id='pinned-attempt'")).fetchone()
        ra2, _ = await publish(conn, a["id"], revision=ra, secret="synthetic-a-rotated", values=("replacement",))
        active_impact = await provider_impact(conn, provider_id=a["id"], missing_only=True, tenant_id="tenant-b")
        assert active_impact["cleanable_count"] == 1
        assert active_impact["blocked_models"][0]["active_run_count"] == 1
        assert active_impact["blocked_models"][0]["groups"] == [], "never expose another tenant's group name"
        pinned = await get_run_connection(conn, run_id="pinned", attempt_id="pinned-attempt", model_value="shared-model", encryption_key=KEY)
        assert pinned.revision == ra and pinned.api_key == "synthetic-a"
        assert await (await conn.execute("select execution_spec_canonical_json,execution_spec_sha256 from run_attempts where id='pinned-attempt'")).fetchone() == frozen
        await conn.execute("update run_attempts set status='succeeded',finished_at=now(),owner_generation=owner_generation+1 where id='pinned-attempt'")
        await conn.execute("update runs set status='succeeded' where id='pinned'")
        await conn.execute("update sandbox_leases set released_at=now() where id='pinned-lease'")
        assert ra2 > rb
        assert await list_admin_models(conn, provider_id=b["id"]) == before_b
        assert (await get_active_connection(conn, provider_id=b["id"], encryption_key=KEY)).api_key == "synthetic-b"
        # Old models remain editable/deletable with their supplier, never reassigned to B.
        old = next(m for m in await list_admin_models(conn, provider_id=a["id"]) if m["id"] == ma)
        assert not old["available"]
        assert (await provider_impact(conn, provider_id=a["id"]))["group_count"] == 1
        impact = await provider_impact(conn, provider_id=a["id"], missing_only=True, tenant_id="tenant-a")
        assert impact["model_count"] == 2 and impact["cleanable_count"] == 1
        assert impact["blocked_models"][0]["id"] == ma
        assert impact["blocked_models"][0]["groups"][0]["name"] == "Mixed"
        async with conn.transaction():
            partial = await retire_provider(conn, provider_id=a["id"], expected_revision=ra2, confirm=True, missing_only=True)
        assert partial["model_count"] == 1 and partial["retained_count"] == 1
        assert ma in {m["id"] for m in await list_admin_models(conn, provider_id=a["id"])}
        with pytest.raises(ValueError, match="model_provider_in_use"):
            async with conn.transaction():
                await retire_provider(conn, provider_id=a["id"], expected_revision=ra2, confirm=True, missing_only=True)
        await set_group_models(conn, tenant_id="tenant-a", group_id="mixed", revision=3,
            models=[{"model_id": mb}], default_model_id=mb)
        async with conn.transaction():
            cleaned = await retire_provider(conn, provider_id=a["id"], expected_revision=ra2, confirm=True, missing_only=True)
        assert cleaned["model_count"] == 1
        assert ma not in {m["id"] for m in await list_admin_models(conn, provider_id=a["id"])}
        assert (await get_active_connection(conn, provider_id=a["id"], encryption_key=KEY)).revision == ra2
        assert await list_admin_models(conn, provider_id=b["id"]) == before_b
    finally:
        await conn.execute("set search_path to public")
        await conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await conn.close()


@pytest.mark.asyncio
async def test_delete_supplier_blocks_references_and_active_runs_and_preserves_terminal_history():
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = "supplier_delete_" + uuid.uuid4().hex
    try:
        await _prepare_schema(conn, schema)
        p = await create_provider(conn, name="Retire me")
        revision, _ = await publish(conn, p["id"])
        model = platform_model_id("shared-model", p["id"])
        await conn.execute("insert into model_groups(id,tenant_id,code,name,type) values ('g','tenant-a','g','G','business')")
        await set_group_models(conn, tenant_id="tenant-a", group_id="g", revision=1, models=[{"model_id": model}], default_model_id=model)
        async def retire(**kwargs):
            async with conn.transaction():
                return await retire_provider(conn, provider_id=p["id"], expected_revision=revision, **kwargs)
        impact = await provider_impact(conn, provider_id=p["id"])
        assert impact["group_count"] == impact["default_group_count"] == 1
        with pytest.raises(ValueError, match="model_provider_in_use"):
            await retire(confirm=True)
        await set_group_models(conn, tenant_id="tenant-a", group_id="g", revision=2, models=[])
        await conn.execute("insert into workspaces(id,tenant_id,name) values ('w','tenant-a','W')")
        await conn.execute("insert into agents(id,tenant_id,name,agent_type) values ('agent','tenant-a','A','chat')")
        await conn.execute("insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title) values ('s','tenant-a','w','user-a','agent','S')")
        await conn.execute("""insert into runs(id,tenant_id,workspace_id,user_id,agent_id,session_id,execution_kind,status,
            model_id,model_value,model_gateway_revision,auto_compact_window,max_output_tokens)
            values ('r','tenant-a','w','user-a','agent','s','harness_chat','running',%s,'shared-model',%s,100000,2048)""", (model, revision))
        with pytest.raises(ValueError, match="model_provider_in_use"):
            await retire(confirm=True)
        assert (await provider_impact(conn, provider_id=p["id"]))["active_run_count"] == 1
        await conn.execute("update runs set status='succeeded' where id='r'")
        for terminal in ("cancelled", "canceled", "dead-letter", "dead_letter", "dead-lettered"):
            await conn.execute("update runs set status=%s where id='r'", (terminal,))
            assert (await provider_impact(conn, provider_id=p["id"]))["active_run_count"] == 0
        history = await (await conn.execute("select to_jsonb(r) as value from runs r where id='r'")).fetchone()
        gateway = await (await conn.execute("select revision,api_key_ciphertext from model_gateway_revisions where revision=%s", (revision,))).fetchone()
        with pytest.raises(ValueError, match="confirmation_required"):
            await retire(confirm=False)
        assert await retire(confirm=True) == {"id": p["id"], "deleted": True}
        assert p["id"] not in {r["id"] for r in await list_providers(conn)}
        assert await list_admin_models(conn, provider_id=p["id"]) == []
        assert await get_active_connection(conn, provider_id=p["id"], encryption_key=KEY) is None
        assert await (await conn.execute("select to_jsonb(r) as value from runs r where id='r'")).fetchone() == history
        assert await (await conn.execute("select revision,api_key_ciphertext from model_gateway_revisions where revision=%s", (revision,))).fetchone() == gateway
        with pytest.raises(ValueError, match="provider_not_found"):
            await publish(conn, p["id"], revision=None)
        assert (await list_admin_models(conn, provider_id="legacy"))[0]["id"] == "model-a"
    finally:
        await conn.execute("set search_path to public")
        await conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await conn.close()


@pytest.mark.asyncio
async def test_upgrade_single_provider_preserves_model_ids_and_gateway_bytes_and_is_repeatable():
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = "supplier_upgrade_" + uuid.uuid4().hex
    try:
        await _prepare_schema(conn, schema)
        # Reconstruct the prior single-provider columns/indexes without changing its rows.
        await conn.execute("drop index uq_model_gateway_provider_active; drop index uq_model_catalog_provider_upstream")
        await conn.execute("alter table model_gateway_revisions drop column provider_id")
        await conn.execute("alter table model_catalog_entries drop column provider_id; alter table model_catalog_entries drop column archived_at")
        await conn.execute("drop table model_providers")
        await conn.execute("create unique index uq_model_gateway_active on model_gateway_revisions(active) where active=true")
        await conn.execute("alter table model_catalog_entries add constraint model_catalog_entries_upstream_model_id_key unique(upstream_model_id)")
        original = await (await conn.execute("select model_id,upstream_model_id,first_seen_revision,last_seen_revision from model_catalog_entries order by model_id")).fetchall()
        gateway = await (await conn.execute("select revision,api_key_ciphertext from model_gateway_revisions")).fetchall()
        for _ in range(2):
            await conn.execute(Path("app/schema.sql").read_text(encoding="utf8"))
        assert await (await conn.execute("select model_id,upstream_model_id,first_seen_revision,last_seen_revision from model_catalog_entries order by model_id")).fetchall() == original
        assert await (await conn.execute("select revision,api_key_ciphertext from model_gateway_revisions")).fetchall() == gateway
        assert all(m["provider_id"] == "legacy" for m in await list_admin_models(conn))
        assert len(await list_providers(conn)) == 1
    finally:
        await conn.execute("set search_path to public")
        await conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        await conn.close()
