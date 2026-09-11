import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.agent_apps.infrastructure import postgres as profile_repository
from app.platform.postgres.errors import RepositoryConflictError


POSTGRES_DSN_ENV = "AI_PLATFORM_AGENT_PROFILE_TEST_DSN"

CANONICAL_PROFILE_SCHEMA_SQL = """
create table tenants (
  id text primary key,
  name text not null
);

create table users (
  id text primary key,
  tenant_id text not null references tenants(id),
  display_name text not null
);

create table skills (
  id text primary key,
  name text not null,
  version text not null,
  executor_type text not null
);

create table agents (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  agent_type text not null,
  description text not null default '',
  default_skill_id text references skills(id),
  status text not null default 'active',
  constraint uq_agents_tenant_id unique (tenant_id, id)
);

create table agent_profile_revisions (
  tenant_id text not null references tenants(id),
  agent_id text not null,
  revision bigint not null check (revision > 0),
  revision_status text not null check (revision_status in ('draft', 'published', 'withdrawn')),
  name text not null,
  description text not null default '',
  starter_prompts jsonb not null default '[]'::jsonb,
  instructions text not null,
  skill_set jsonb not null check (jsonb_typeof(skill_set) = 'array'),
  mcp_tool_ids jsonb not null default '[]'::jsonb,
  content_hash text not null,
  avatar_ref text not null,
  avatar_seed text not null,
  market_tags jsonb not null default '[]'::jsonb,
  visibility text not null check (visibility in ('tenant', 'restricted')),
  allowed_department_ids jsonb not null default '[]'::jsonb,
  allowed_roles jsonb not null default '[]'::jsonb,
  allowed_user_ids jsonb not null default '[]'::jsonb,
  created_by text references users(id),
  created_at timestamptz not null default now(),
  published_by text references users(id),
  published_at timestamptz,
  published_from_revision bigint,
  withdrawn_from_revision bigint,
  constraint fk_agent_profile_revisions_tenant_agent
    foreign key (tenant_id, agent_id) references agents(tenant_id, id),
  constraint uq_agent_profile_revision_content
    unique (tenant_id, agent_id, revision, content_hash),
  primary key (tenant_id, agent_id, revision)
);

create table agent_profiles (
  tenant_id text not null references tenants(id),
  agent_id text not null,
  lifecycle_status text not null check (lifecycle_status in ('draft', 'published', 'withdrawn')),
  latest_revision bigint not null check (latest_revision > 0),
  published_revision bigint,
  published_hash text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, agent_id),
  constraint fk_agent_profiles_tenant_agent
    foreign key (tenant_id, agent_id) references agents(tenant_id, id),
  constraint chk_agent_profiles_publication check (
    (lifecycle_status = 'published' and published_revision is not null and published_hash is not null)
    or
    (lifecycle_status <> 'published' and published_revision is null and published_hash is null)
  ),
  constraint fk_agent_profiles_current_publication
    foreign key (tenant_id, agent_id, published_revision, published_hash)
    references agent_profile_revisions(tenant_id, agent_id, revision, content_hash)
);

create table runs (
  id text primary key,
  tenant_id text not null references tenants(id),
  agent_id text not null,
  status text not null
);
"""


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        if os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true":
            raise RuntimeError(f"{POSTGRES_DSN_ENV} must be configured in GitHub Actions")
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


def test_postgres_dsn_fails_closed_in_github_actions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(POSTGRES_DSN_ENV, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(RuntimeError, match=f"^{POSTGRES_DSN_ENV} must be configured"):
        _postgres_dsn()


async def _append_revision(
    conn: psycopg.AsyncConnection,
    *,
    status: str,
    expected_previous_revision: int,
    content_hash: str,
):
    return await profile_repository.create_agent_profile_revision(
        conn,
        tenant_id="tenant-a",
        agent_id="agt_support",
        status=status,
        name="Support assistant",
        description="Approved support help.",
        starter_prompts=["Summarize this request"],
        instructions="Use authorized information only.",
        skill_set=[{"skill_id": "support"}],
        mcp_tool_ids=[],
        content_hash=content_hash,
        created_by="admin-a",
        published_by="admin-a" if status == "published" else None,
        expected_previous_revision=expected_previous_revision,
        published_from_revision=1 if status == "published" else None,
        withdrawn_from_revision=2 if status == "withdrawn" else None,
        avatar_ref="builtin:assistant",
        avatar_seed="agt-support",
        market_tags=["support"],
        visibility="tenant",
        allowed_department_ids=[],
        allowed_roles=[],
        allowed_user_ids=[],
    )


@pytest.mark.asyncio
async def test_canonical_profile_lifecycle_persists_and_queries_exact_publication():
    dsn = _postgres_dsn()
    schema_name = f"agent_profile_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await conn.execute(CANONICAL_PROFILE_SCHEMA_SQL)
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'Admin A')"
        )
        await conn.execute(
            "insert into skills(id, name, version, executor_type) "
            "values ('support', 'Support', 'v1', 'claude-agent-worker')"
        )
        await profile_repository.ensure_agent_profile_identity(
            conn,
            tenant_id="tenant-a",
            agent_id="agt_support",
            name="Support assistant",
            default_skill_id="support",
        )

        draft = await _append_revision(
            conn,
            status="draft",
            expected_previous_revision=0,
            content_hash="a" * 64,
        )
        await profile_repository.record_agent_profile_draft(
            conn,
            tenant_id="tenant-a",
            agent_id="agt_support",
            revision=1,
            expected_previous_revision=0,
        )
        assert draft["revision"] == 1
        assert await profile_repository.get_current_published_agent_profile(
            conn, tenant_id="tenant-a", agent_id="agt_support"
        ) is None

        published = await _append_revision(
            conn,
            status="published",
            expected_previous_revision=1,
            content_hash="b" * 64,
        )
        await profile_repository.record_agent_profile_publication(
            conn,
            tenant_id="tenant-a",
            agent_id="agt_support",
            revision=2,
            content_hash="b" * 64,
        )
        current = await profile_repository.get_current_published_agent_profile(
            conn, tenant_id="tenant-a", agent_id="agt_support"
        )
        assert current is not None
        assert current["revision"] == published["revision"] == 2
        assert current["skill_set"] == [{"skill_id": "support"}]
        assert current["market_tags"] == ["support"]

        rows = await profile_repository.list_current_published_agent_profiles(
            conn,
            tenant_id="tenant-a",
            query="support",
        )
        assert [row["agent_id"] for row in rows] == ["agt_support"]

        withdrawn = await _append_revision(
            conn,
            status="withdrawn",
            expected_previous_revision=2,
            content_hash="c" * 64,
        )
        await profile_repository.record_agent_profile_withdrawal(
            conn,
            tenant_id="tenant-a",
            agent_id="agt_support",
            revision=withdrawn["revision"],
        )
        assert await profile_repository.get_current_published_agent_profile(
            conn, tenant_id="tenant-a", agent_id="agt_support"
        ) is None
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_canonical_profile_revision_fence_rejects_stale_writer():
    dsn = _postgres_dsn()
    schema_name = f"agent_profile_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await conn.execute(CANONICAL_PROFILE_SCHEMA_SQL)
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'Admin A')"
        )
        await conn.execute(
            "insert into skills(id, name, version, executor_type) "
            "values ('support', 'Support', 'v1', 'claude-agent-worker')"
        )
        await profile_repository.ensure_agent_profile_identity(
            conn,
            tenant_id="tenant-a",
            agent_id="agt_support",
            name="Support assistant",
            default_skill_id="support",
        )
        await _append_revision(
            conn,
            status="draft",
            expected_previous_revision=0,
            content_hash="a" * 64,
        )

        with pytest.raises(RepositoryConflictError, match="agent_profile_revision_stale"):
            await _append_revision(
                conn,
                status="draft",
                expected_previous_revision=0,
                content_hash="b" * 64,
            )
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()
