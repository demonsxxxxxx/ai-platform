import asyncio
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories


POSTGRES_DSN_ENV = "AI_PLATFORM_AGENT_PROFILE_TEST_DSN"
REQUIRED_SCHEMA_SQL = """
create table tenants (
  id text primary key,
  name text not null,
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table users (
  id text primary key,
  tenant_id text not null references tenants(id),
  display_name text not null,
  email text,
  external_id text,
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table skills (
  id text primary key,
  name text not null,
  version text not null,
  description text not null default '',
  input_modes jsonb not null default '[]'::jsonb,
  output_modes jsonb not null default '[]'::jsonb,
  executor_type text not null,
  config_json jsonb not null default '{}'::jsonb,
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table agents (
  id text primary key,
  tenant_id text not null references tenants(id),
  name text not null,
  agent_type text not null,
  description text not null default '',
  default_skill_id text references skills(id),
  status text not null default 'active',
  created_at timestamptz not null default now(),
  constraint uq_agents_tenant_id unique (tenant_id, id)
);

create table agent_profile_revisions (
  tenant_id text not null references tenants(id),
  agent_id text not null,
  revision bigint not null check (revision > 0),
  status text not null check (status in ('draft', 'published', 'withdrawn')),
  name text not null,
  description text not null default '',
  instructions text not null,
  model_id text not null,
  skill_id text not null references skills(id),
  skill_version text not null,
  mcp_tool_ids jsonb not null default '[]'::jsonb,
  content_hash text not null,
  avatar_ref text not null default 'builtin:agent',
  category text not null default 'general',
  visibility text not null default 'tenant',
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
  primary key (tenant_id, agent_id, revision)
);

create index idx_agent_profile_revisions_published
  on agent_profile_revisions(tenant_id, agent_id, revision desc)
  where status = 'published';

create unique index idx_agent_profile_revisions_published_from_draft
  on agent_profile_revisions(tenant_id, agent_id, published_from_revision)
  where status = 'published' and published_from_revision is not null;
"""


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


@pytest.mark.asyncio
async def test_create_agent_profile_revision_persists_draft_and_publish_in_postgres():
    dsn = _postgres_dsn()
    schema_name = f"agent_profile_{uuid.uuid4().hex}"
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(REQUIRED_SCHEMA_SQL)
        await conn.execute("insert into tenants(id, name) values (%s, %s)", ("tenant-a", "Tenant A"))
        await conn.execute(
            """
            insert into users(id, tenant_id, display_name)
            values (%s, %s, %s), (%s, %s, %s)
            """,
            ("creator-a", "tenant-a", "Creator A", "publisher-a", "tenant-a", "Publisher A"),
        )
        await conn.execute(
            "insert into skills(id, name, version, executor_type) values (%s, %s, %s, %s)",
            ("general-chat", "General chat", "version-a", "fake"),
        )
        await conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type, default_skill_id)
            values (%s, %s, %s, %s, %s)
            """,
            ("agt_support", "tenant-a", "Support assistant", "profile", "general-chat"),
        )

        async with conn.transaction():
            draft = await repositories.create_agent_profile_revision(
                conn,
                tenant_id="tenant-a",
                agent_id="agt_support",
                status="draft",
                name="Support assistant",
                description="Approved support helper.",
                instructions="Private draft instruction",
                model_id="model-a",
                skill_id="general-chat",
                skill_version="version-a",
                mcp_tool_ids=["mcp-draft"],
                content_hash="a" * 64,
                created_by="creator-a",
                published_by=None,
                expected_previous_revision=0,
                published_from_revision=None,
            )

        async with conn.transaction():
            timestamp_cursor = await conn.execute("select now() as server_timestamp")
            server_timestamp = (await timestamp_cursor.fetchone())["server_timestamp"]
            published = await repositories.create_agent_profile_revision(
                conn,
                tenant_id="tenant-a",
                agent_id="agt_support",
                status="published",
                name="Support assistant",
                description="Approved support helper.",
                instructions="Private published instruction",
                model_id="model-a",
                skill_id="general-chat",
                skill_version="version-a",
                mcp_tool_ids=["mcp-published"],
                content_hash="b" * 64,
                created_by="creator-a",
                published_by="publisher-a",
                expected_previous_revision=1,
                published_from_revision=1,
            )

        assert draft["revision"] == 1
        assert draft["status"] == "draft"
        assert draft["mcp_tool_ids"] == ["mcp-draft"]
        assert draft["content_hash"] == "a" * 64
        assert draft["published_at"] is None
        assert published["revision"] == 2
        assert published["status"] == "published"
        assert published["mcp_tool_ids"] == ["mcp-published"]
        assert published["content_hash"] == "b" * 64
        assert published["published_at"] == server_timestamp

        rows_cursor = await conn.execute(
            """
            select revision, status, mcp_tool_ids, content_hash, created_by,
                   published_by, published_at, published_from_revision
            from agent_profile_revisions
            where tenant_id = %s and agent_id = %s
            order by revision
            """,
            ("tenant-a", "agt_support"),
        )
        rows = await rows_cursor.fetchall()
        assert rows == [
            {
                "revision": 1,
                "status": "draft",
                "mcp_tool_ids": ["mcp-draft"],
                "content_hash": "a" * 64,
                "created_by": "creator-a",
                "published_by": None,
                "published_at": None,
                "published_from_revision": None,
            },
            {
                "revision": 2,
                "status": "published",
                "mcp_tool_ids": ["mcp-published"],
                "content_hash": "b" * 64,
                "created_by": "creator-a",
                "published_by": "publisher-a",
                "published_at": server_timestamp,
                "published_from_revision": 1,
            },
        ]
    finally:
        try:
            await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_postgres_profile_revision_fence_serializes_real_concurrent_publishers():
    """Exercise PostgreSQL advisory-lock contention, not the in-memory mirror."""

    dsn = _postgres_dsn()
    schema_name = f"agent_profile_lock_{uuid.uuid4().hex}"
    first_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    second_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await first_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(first_conn, schema_name)
        await _set_search_path(second_conn, schema_name)
        await first_conn.execute(REQUIRED_SCHEMA_SQL)
        await first_conn.execute("insert into tenants(id, name) values (%s, %s)", ("tenant-a", "Tenant A"))
        await first_conn.execute(
            "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
            ("publisher-a", "tenant-a", "Publisher A"),
        )
        await first_conn.execute(
            "insert into skills(id, name, version, executor_type) values (%s, %s, %s, %s)",
            ("general-chat", "General chat", "version-a", "fake"),
        )
        await first_conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type, default_skill_id)
            values (%s, %s, %s, %s, %s)
            """,
            ("agt_support", "tenant-a", "Support assistant", "profile", "general-chat"),
        )
        async with first_conn.transaction():
            await repositories.create_agent_profile_revision(
                first_conn,
                tenant_id="tenant-a",
                agent_id="agt_support",
                status="draft",
                name="Support assistant",
                description="Approved support helper.",
                instructions="Private draft instruction",
                model_id="model-a",
                skill_id="general-chat",
                skill_version="version-a",
                mcp_tool_ids=[],
                content_hash="a" * 64,
                created_by="publisher-a",
                expected_previous_revision=0,
            )

        async def publish(conn: psycopg.AsyncConnection, content_hash: str):
            try:
                async with conn.transaction():
                    return await repositories.create_agent_profile_revision(
                        conn,
                        tenant_id="tenant-a",
                        agent_id="agt_support",
                        status="published",
                        name="Support assistant",
                        description="Approved support helper.",
                        instructions="Private published instruction",
                        model_id="model-a",
                        skill_id="general-chat",
                        skill_version="version-a",
                        mcp_tool_ids=[],
                        content_hash=content_hash,
                        created_by="publisher-a",
                        published_by="publisher-a",
                        expected_previous_revision=1,
                        published_from_revision=1,
                    )
            except repositories.RepositoryConflictError as exc:
                return exc

        outcomes = await asyncio.gather(
            publish(first_conn, "b" * 64),
            publish(second_conn, "c" * 64),
        )

        assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, repositories.RepositoryConflictError) for outcome in outcomes) == 1
        cursor = await first_conn.execute(
            "select revision, status from agent_profile_revisions order by revision"
        )
        assert await cursor.fetchall() == [
            {"revision": 1, "status": "draft"},
            {"revision": 2, "status": "published"},
        ]
    finally:
        try:
            await second_conn.close()
            await first_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await first_conn.close()


@pytest.mark.asyncio
async def test_postgres_schema_repairs_fail_closed_and_enforces_current_publication():
    dsn = _postgres_dsn()
    schema_name = f"agent_profile_schema_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute(schema_sql)
        await conn.execute("insert into tenants(id, name) values (%s, %s)", ("tenant-profile", "Tenant"))
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
            ("publisher-profile", "tenant-profile", "Publisher"),
        )
        await conn.execute(
            "insert into skills(id, name, version, executor_type) values (%s, %s, %s, %s)",
            ("profile-skill", "Profile skill", "version-a", "fake"),
        )
        await conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type, default_skill_id)
            values (%s, %s, %s, 'profile', %s)
            """,
            ("agt_profile", "tenant-profile", "Profile", "profile-skill"),
        )
        await conn.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, status, name, instructions, model_id,
              skill_id, skill_version, content_hash, visibility, created_by
            ) values
              (%s, %s, 1, 'draft', 'Profile', 'draft', 'model-a', %s, 'version-a', %s, 'restricted', %s),
              (%s, %s, 2, 'published', 'Profile', 'published', 'model-a', %s, 'version-a', %s, 'tenant', %s)
            """,
            (
                "tenant-profile",
                "agt_profile",
                "profile-skill",
                "a" * 64,
                "publisher-profile",
                "tenant-profile",
                "agt_profile",
                "profile-skill",
                "b" * 64,
                "publisher-profile",
            ),
        )
        await conn.execute(
            """
            insert into agent_profiles(
              tenant_id, agent_id, lifecycle_status, latest_revision,
              published_revision, published_hash, published_status
            ) values (%s, %s, 'published', 2, 2, %s, 'published')
            """,
            ("tenant-profile", "agt_profile", "b" * 64),
        )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                """
                update agent_profiles
                set published_revision = 1, published_hash = %s, published_status = 'published'
                where tenant_id = %s and agent_id = %s
                """,
                ("a" * 64, "tenant-profile", "agt_profile"),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                "update agent_profiles set published_hash = %s where tenant_id = %s and agent_id = %s",
                ("c" * 64, "tenant-profile", "agt_profile"),
            )

        await conn.execute("alter table agent_profile_revisions drop constraint chk_agent_profile_revisions_visibility")
        await conn.execute("alter table agent_profiles drop constraint fk_agent_profiles_current_publication")
        await conn.execute("alter table agent_profiles drop constraint chk_agent_profiles_publication")
        await conn.execute(
            "update agent_profile_revisions set visibility = 'unknown' where tenant_id = %s and agent_id = %s and revision = 1",
            ("tenant-profile", "agt_profile"),
        )
        await conn.execute(
            """
            update agent_profiles
            set lifecycle_status = 'published', published_revision = 1,
                published_hash = %s, published_status = 'published'
            where tenant_id = %s and agent_id = %s
            """,
            ("a" * 64, "tenant-profile", "agt_profile"),
        )

        await conn.execute(schema_sql)
        await conn.execute(schema_sql)

        repaired_visibility = await conn.execute(
            "select visibility from agent_profile_revisions where tenant_id = %s and agent_id = %s and revision = 1",
            ("tenant-profile", "agt_profile"),
        )
        assert await repaired_visibility.fetchone() == {"visibility": "restricted"}
        repaired_profile = await conn.execute(
            """
            select lifecycle_status, published_revision, published_hash, published_status
            from agent_profiles where tenant_id = %s and agent_id = %s
            """,
            ("tenant-profile", "agt_profile"),
        )
        assert await repaired_profile.fetchone() == {
            "lifecycle_status": "withdrawn",
            "published_revision": None,
            "published_hash": None,
            "published_status": None,
        }
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                "update agent_profile_revisions set visibility = 'unknown' where tenant_id = %s and agent_id = %s",
                ("tenant-profile", "agt_profile"),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                """
                update agent_profiles
                set lifecycle_status = 'published', published_revision = 1,
                    published_hash = %s, published_status = 'published'
                where tenant_id = %s and agent_id = %s
                """,
                ("a" * 64, "tenant-profile", "agt_profile"),
            )
    finally:
        try:
            await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await conn.close()
