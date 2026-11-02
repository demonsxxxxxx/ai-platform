"""Logical retirement preserves history and atomically rehomes sole memberships."""
from __future__ import annotations

import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from test_model_groups_postgres import _postgres_dsn, _prepare_schema
from app.execution.infrastructure.model_groups import (
    archive_or_delete_group, ensure_default_group, get_admin_group_details,
    list_admin_groups, list_groups_for_user, ModelGroupPolicyError,
    set_group_members, set_group_models, set_user_group_memberships, update_group,
)
from app.execution.infrastructure.model_group_batches import batch_group_models


@pytest.mark.asyncio
async def test_logical_delete_migrates_members_preserves_history_and_rejects_stale_writes():
    conn = await psycopg.AsyncConnection.connect(_postgres_dsn(), autocommit=True, row_factory=dict_row)
    schema = 'retire_' + uuid.uuid4().hex
    try:
        await _prepare_schema(conn, schema)
        default = await ensure_default_group(conn, tenant_id='tenant-a')
        await conn.execute("""insert into model_groups(id,tenant_id,code,name,type,enabled) values
            ('source','tenant-a','source','Source','business',true),
            ('target','tenant-a','target','Target','business',true),
            ('disabled','tenant-a','disabled','Disabled','business',false),
            ('foreign','tenant-b','foreign','Foreign','business',true)""")
        await conn.execute("insert into model_group_members(tenant_id,group_id,user_id,is_primary) values ('tenant-a','source','user-a',true)")
        await conn.execute("insert into model_group_models(group_id,model_id) values ('source','model-a'),('target','model-a')")
        await conn.execute("update model_groups set default_model_id='model-a' where id in ('source','target')")
        await conn.execute("insert into workspaces(id,tenant_id,name) values ('w','tenant-a','W')")
        await conn.execute("insert into agents(id,tenant_id,name,agent_type) values ('a','tenant-a','A','chat')")
        await conn.execute("insert into sessions(id,tenant_id,workspace_id,user_id,agent_id,title) values ('s','tenant-a','w','user-a','a','S')")
        await conn.execute("""insert into runs(id,tenant_id,workspace_id,user_id,agent_id,session_id,execution_kind,status,model_group_id,model_group_code,model_group_type,model_group_revision,model_selection_mode,usage_scene,model_policy_version)
            values ('r','tenant-a','w','user-a','a','s','harness_chat','running','source','source','business',1,'explicit','chat','model-groups.v1')""")
        async def retire(**kwargs):
            async with conn.transaction():
                return await archive_or_delete_group(conn, tenant_id='tenant-a', group_id='source', revision=1, delete=True, **kwargs)
        with pytest.raises(ModelGroupPolicyError, match='model_group_active_runs') as error:
            await retire(target_group_id='target', target_group_revision=1)
        assert error.value.details == {'active_run_count': 1}
        assert (await get_admin_group_details(conn, tenant_id='tenant-a', group_id='source'))['active_run_count'] == 1
        await conn.execute("update runs set status='succeeded' where id='r'")
        history = await (await conn.execute("select to_jsonb(r) as snapshot from runs r where id='r'")).fetchone()
        for kwargs, code in [({}, 'model_group_migration_required'),
            ({'target_group_id':'target','target_group_revision':1,'confirm_default_fallback':True}, 'choice_conflict'),
            ({'target_group_id':'target'}, 'target_revision_required'),
            ({'target_group_id':'target','target_group_revision':2}, 'target_revision_conflict')]:
            with pytest.raises(ModelGroupPolicyError, match=code):
                await retire(**kwargs)
        for target in ('source', 'disabled', 'foreign', default, 'missing'):
            with pytest.raises(ModelGroupPolicyError, match='migration_target_invalid'):
                await retire(target_group_id=target, target_group_revision=1)
        # A stale source revision cannot mutate any relationship.
        async with conn.transaction():
            with pytest.raises(ModelGroupPolicyError, match='revision_conflict'):
                await archive_or_delete_group(conn, tenant_id='tenant-a', group_id='source', revision=2, delete=True, confirm_default_fallback=True)
        assert (await (await conn.execute("select group_id from model_group_members where user_id='user-a'")).fetchone())['group_id'] == 'source'
        # A database failure after members move rolls the entire operation back.
        await conn.execute("""create function reject_retire() returns trigger language plpgsql as $$
            begin if new.archived_at is not null then raise exception 'synthetic_retire_failure'; end if; return new; end $$""")
        await conn.execute("create trigger reject_retire before update on model_groups for each row execute function reject_retire()")
        with pytest.raises(psycopg.errors.RaiseException, match='synthetic_retire_failure'):
            await retire(target_group_id='target', target_group_revision=1)
        assert (await (await conn.execute("select group_id from model_group_members where user_id='user-a'")).fetchone())['group_id'] == 'source'
        assert (await (await conn.execute("select revision from model_groups where id='target'")).fetchone())['revision'] == 1
        assert (await (await conn.execute("select model_group_membership_revision from users where id='user-a'")).fetchone())['model_group_membership_revision'] == 1
        await conn.execute('drop trigger reject_retire on model_groups')
        result = await retire(target_group_id='target', target_group_revision=1)
        assert result['deleted'] and result['revision'] == 2 and result['migrated_member_count'] == 1
        source = await get_admin_group_details(conn, tenant_id='tenant-a', group_id='source')
        assert source['archived_at'] and not source['enabled'] and source['default_model_id'] is None
        assert source['models'] == [] and source['members'] == []
        assert 'source' not in {g['id'] for g in await list_admin_groups(conn, tenant_id='tenant-a')}
        assert [g['id'] for g in await list_groups_for_user(conn, tenant_id='tenant-a', user_id='user-a')] == ['target']
        assert (await (await conn.execute("select revision from model_groups where id='target'")).fetchone())['revision'] == 2
        assert (await (await conn.execute("select model_group_membership_revision from users where id='user-a'")).fetchone())['model_group_membership_revision'] == 2
        assert (await (await conn.execute("select count(*) as n from model_catalog_entries where model_id='model-a'")).fetchone())['n'] == 1
        assert (await (await conn.execute("select count(*) as n from model_group_models where group_id='target'")).fetchone())['n'] == 1
        assert await (await conn.execute("select to_jsonb(r) as snapshot from runs r where id='r'")).fetchone() == history
        for operation in (
            lambda: retire(confirm_default_fallback=True),
            lambda: update_group(conn, tenant_id='tenant-a', group_id='source', name=None, enabled=True, remark=None, sort_order=None, revision=2),
            lambda: set_group_models(conn, tenant_id='tenant-a', group_id='source', revision=2, models=[]),
            lambda: set_group_members(conn, tenant_id='tenant-a', group_id='source', revision=2, members=[]),
            lambda: set_user_group_memberships(conn, tenant_id='tenant-a', user_id='user-a', expected_revision=2, group_ids=['source'], primary_group_id='source'),
            lambda: batch_group_models(conn, tenant_id='tenant-a', group_ids=['source'], model_ids=['model-a'], operation='add', expected_group_revisions={'source':2}, preview=False),
        ):
            with pytest.raises(ModelGroupPolicyError):
                async with conn.transaction():
                    await operation()
        # The mapping tool cannot recreate or assign members to an archived identity.
        from tools import model_group_migration as migration
        for raw in (
            {"version": migration.VERSION, "groups": [{"tenant_id": "tenant-a", "code": "source", "name": "Source", "models": []}], "users": []},
            {"version": migration.VERSION, "groups": [], "users": [{"tenant_id": "tenant-a", "user_id": "user-b", "group_codes": ["source"], "primary_group_code": "source"}]},
        ):
            with pytest.raises(ValueError, match="group_not_available"):
                async with conn.transaction():
                    await migration._prepare(conn, migration._mapping(raw))
        # Disable is reversible and preserves member/model associations.
        await update_group(conn, tenant_id='tenant-a', group_id='target', name=None, enabled=False, remark=None, sort_order=None, revision=2)
        assert (await get_admin_group_details(conn, tenant_id='tenant-a', group_id='target'))['member_count'] == 1
        await update_group(conn, tenant_id='tenant-a', group_id='target', name=None, enabled=True, remark=None, sort_order=None, revision=3)
        async with conn.transaction():
            await archive_or_delete_group(conn, tenant_id='tenant-a', group_id='target', revision=4, delete=False, confirm_default_fallback=True)
        assert (await list_groups_for_user(conn, tenant_id='tenant-a', user_id='user-a'))[0]['id'] == default
        assert (await (await conn.execute("select count(*) as n from model_group_members where user_id='user-a'")).fetchone())['n'] == 0
        with pytest.raises(ModelGroupPolicyError, match='model_default_group_immutable'):
            async with conn.transaction():
                await archive_or_delete_group(conn, tenant_id='tenant-a', group_id=default, revision=1, delete=True)
        # Empty groups can be retired without an unnecessary member decision.
        async with conn.transaction():
            await archive_or_delete_group(conn, tenant_id='tenant-a', group_id='disabled', revision=1, delete=True)
    finally:
        await conn.execute('set search_path to public')
        await conn.execute(sql.SQL('drop schema {} cascade').format(sql.Identifier(schema)))
        await conn.close()
