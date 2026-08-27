import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import agent_conversation_repository, repositories
from app.execution.api import RunModelSelection


POSTGRES_DSN_ENV = "AI_PLATFORM_AGENT_PROFILE_TEST_DSN"
POSTGRES_CONCURRENCY_TIMEOUT_SECONDS = 5
_TEST_CHAT_STREAM_REQUEST = SimpleNamespace(
    app=SimpleNamespace(
        state=SimpleNamespace(
            run_stream_runtime=SimpleNamespace(worker_capabilities=object()),
        ),
    ),
)


async def _resolve_legacy_chat_model(_conn, *, selection):
    assert selection is None
    return RunModelSelection(
        model_id="model-a",
        model_value="provider-model-a",
        connection_revision=None,
    )


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
  status text not null check (status in ('draft', 'published')),
  revision_status text not null check (revision_status in ('draft', 'published', 'withdrawn')),
  name text not null,
  description text not null default '',
  welcome_message text not null default '',
  starter_prompts jsonb not null default '[]'::jsonb,
  capability_summary text not null default '',
  recommended_tasks jsonb not null default '[]'::jsonb,
  supported_input_types jsonb not null default '["text"]'::jsonb,
  supported_file_types jsonb not null default '[]'::jsonb,
  expected_outputs jsonb not null default '[]'::jsonb,
  permissions_and_data_access_notice text not null default '',
  instructions text not null,
  model_id text not null,
  skill_id text not null references skills(id),
  skill_version text not null,
  skill_set jsonb not null default '[]'::jsonb,
  mcp_tool_ids jsonb not null default '[]'::jsonb,
  content_hash text not null,
  avatar_ref text not null
    check (avatar_ref in ('builtin:agent', 'builtin:assistant', 'builtin:document', 'builtin:research')),
  avatar_asset_id text,
  avatar_seed text not null default '',
  category text not null
    check (category in ('general', 'support', 'writing', 'research', 'operations')),
  visibility text not null,
  allowed_department_ids jsonb not null,
  allowed_roles jsonb not null,
  allowed_user_ids jsonb not null,
  legacy_compatibility_write boolean not null default false,
  created_by text references users(id),
  created_at timestamptz not null default now(),
  published_by text references users(id),
  published_at timestamptz,
  published_from_revision bigint,
  withdrawn_from_revision bigint,
  constraint fk_agent_profile_revisions_tenant_agent
    foreign key (tenant_id, agent_id) references agents(tenant_id, id),
  constraint chk_agent_profile_revisions_visibility
    check (visibility in ('tenant', 'restricted')),
  constraint uq_agent_profile_revision_publication
    unique (tenant_id, agent_id, revision, content_hash, revision_status),
  primary key (tenant_id, agent_id, revision)
);

create index idx_agent_profile_revisions_published
  on agent_profile_revisions(tenant_id, agent_id, revision desc)
  where revision_status = 'published';

create unique index idx_agent_profile_revisions_published_from_draft
  on agent_profile_revisions(tenant_id, agent_id, published_from_revision)
  where revision_status = 'published' and published_from_revision is not null;
"""


PRE_701_PROFILE_DOWNGRADE_SQL = """
drop trigger if exists trg_agent_profile_legacy_insert_reconcile on agent_profile_revisions;
drop trigger if exists trg_agent_profile_legacy_insert_compatibility on agent_profile_revisions;
drop function if exists agent_profile_legacy_insert_reconcile();
drop function if exists agent_profile_legacy_insert_compatibility();
drop table if exists agent_profiles;
drop index if exists idx_agent_profile_revisions_published;
drop index if exists idx_agent_profile_revisions_published_from_draft;
alter table agent_profile_revisions drop constraint if exists uq_agent_profile_revision_publication;
alter table agent_profile_revisions drop constraint if exists agent_profile_revisions_status_check;
alter table agent_profile_revisions drop column if exists revision_status;
alter table agent_profile_revisions drop column if exists avatar_ref;
alter table agent_profile_revisions drop column if exists category;
alter table agent_profile_revisions drop column if exists visibility;
alter table agent_profile_revisions drop column if exists allowed_department_ids;
alter table agent_profile_revisions drop column if exists allowed_roles;
alter table agent_profile_revisions drop column if exists allowed_user_ids;
alter table agent_profile_revisions drop column if exists legacy_compatibility_write;
alter table agent_profile_revisions drop column if exists withdrawn_from_revision;
alter table agent_profile_revisions add constraint agent_profile_revisions_status_check
  check (status in ('draft', 'published'));
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
        if os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true":
            raise RuntimeError(f"{POSTGRES_DSN_ENV} must be configured in GitHub Actions")
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


def test_postgres_dsn_fails_closed_in_github_actions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(POSTGRES_DSN_ENV, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(RuntimeError, match=f"^{POSTGRES_DSN_ENV} must be configured"):
        _postgres_dsn()


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


async def _wait_for_event_or_task(
    event: asyncio.Event,
    task: asyncio.Task,
) -> None:
    """Surface an admission failure instead of hiding it behind an event timeout."""

    event_waiter = asyncio.create_task(event.wait())
    try:
        done, _pending = await asyncio.wait(
            {event_waiter, task},
            timeout=POSTGRES_CONCURRENCY_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            await task
            raise AssertionError("admission completed before reaching the paused queue")
        if event_waiter not in done:
            raise TimeoutError("admission did not reach the paused queue")
    finally:
        if not event_waiter.done():
            event_waiter.cancel()
            await asyncio.gather(event_waiter, return_exceptions=True)


async def _agent_profile_storage_projection(
    conn: psycopg.AsyncConnection,
    *,
    tenant_id: str,
) -> tuple[list[dict], list[dict]]:
    """Snapshot aggregate timestamps and the full immutable revision projection."""

    profiles_cursor = await conn.execute(
        """
        select tenant_id, agent_id, lifecycle_status, latest_revision,
               published_revision, published_hash, published_status,
               created_at, updated_at, xmin::text as storage_version
        from agent_profiles
        where tenant_id = %s
        order by agent_id
        """,
        (tenant_id,),
    )
    profiles = await profiles_cursor.fetchall()
    revisions_cursor = await conn.execute(
        """
        select tenant_id, agent_id, revision, status, revision_status,
               name, description, welcome_message, starter_prompts,
               capability_summary, recommended_tasks, supported_input_types,
               supported_file_types, expected_outputs,
               permissions_and_data_access_notice, instructions, model_id,
               skill_id, skill_version, mcp_tool_ids, content_hash, avatar_ref,
               avatar_asset_id, category, visibility, allowed_department_ids,
               allowed_roles, allowed_user_ids, legacy_compatibility_write,
               created_by, created_at, published_by, published_at,
               published_from_revision, withdrawn_from_revision,
               xmin::text as storage_version
        from agent_profile_revisions
        where tenant_id = %s
        order by agent_id, revision
        """,
        (tenant_id,),
    )
    return profiles, await revisions_cursor.fetchall()


def _profile_chat_manifest(skill_id: str) -> dict[str, object]:
    """Build one valid immutable Skill pin for the real Chat persistence mirror."""

    content = f"---\nname: {skill_id}\ndescription: Profile chat test\n---\n\n# {skill_id}\n".encode()
    digest = hashlib.sha256()
    path = b"SKILL.md"
    digest.update(len(path).to_bytes(8, "big"))
    digest.update(path)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
    version = digest.hexdigest()
    return {
        "skill_id": skill_id,
        "description": "Profile chat test",
        "version": version,
        "content_hash": version,
        "source": {"kind": "builtin", "asset_dir": skill_id, "version": version},
        "files": [
            {
                "relative_path": "SKILL.md",
                "content_base64": base64.b64encode(content).decode("ascii"),
                "size_bytes": len(content),
            }
        ],
        "dependency_ids": [],
        "mcp_tool_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }


def _canonical_profile_hash(
    *,
    agent_id: str,
    name: str,
    description: str,
    instructions: str,
    model_id: str,
    skill_id: str,
    skill_version: str,
) -> str:
    from app.agent_apps.authority import _draft_from_row, _revision_hash

    row = {
        "agent_id": agent_id,
        "revision": 1,
        "name": name,
        "description": description,
        "instructions": instructions,
        "model_id": model_id,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "skill_set": [
            {"skill_id": skill_id, "expected_version": skill_version}
        ],
        "mcp_tool_ids": [],
        "avatar_ref": "builtin:agent",
        "category": "general",
        "visibility": "tenant",
        "allowed_department_ids": [],
        "allowed_roles": [],
        "allowed_user_ids": [],
    }
    return _revision_hash(_draft_from_row(row))


def _canonical_profile_storage_lists() -> tuple[str, str]:
    from app.agent_apps.authority import (
        _ROLLING_LEGACY_SUPPORTED_FILE_TYPES,
        _ROLLING_LEGACY_SUPPORTED_INPUT_TYPES,
    )

    return (
        json.dumps(_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES),
        json.dumps(_ROLLING_LEGACY_SUPPORTED_FILE_TYPES),
    )


async def _seed_profile_chat_storage(
    conn: psycopg.AsyncConnection,
    *,
    skill_version: str,
) -> str:
    """Seed the minimum real storage graph for a profile-bound Chat submission."""

    profile_hash = _canonical_profile_hash(
        agent_id="agt_profile_chat",
        name="Profile Chat Agent",
        description="Published profile for Chat locking",
        instructions="private profile chat instructions",
        model_id="model-a",
        skill_id="profile-chat-skill",
        skill_version=skill_version,
    )

    await conn.execute(
        "insert into tenants(id, name) values (%s, %s)",
        ("tenant-profile-chat", "Profile Chat tenant"),
    )
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values (%s, %s, %s)",
        ("workspace-profile-chat", "tenant-profile-chat", "Profile Chat workspace"),
    )
    await conn.execute(
        """
        insert into users(id, tenant_id, display_name)
        values (%s, %s, %s), (%s, %s, %s)
        """,
        (
            "user-profile-chat",
            "tenant-profile-chat",
            "Profile Chat user",
            "admin-profile-chat",
            "tenant-profile-chat",
            "Profile Chat admin",
        ),
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values (%s, %s, %s, %s)",
        ("profile-chat-skill", "Profile Chat skill", skill_version, "claude-agent-worker"),
    )
    await conn.execute(
        """
        insert into agents(id, tenant_id, name, agent_type, default_skill_id)
        values (%s, %s, %s, 'profile', %s)
        """,
        ("agt_profile_chat", "tenant-profile-chat", "Profile Chat Agent", "profile-chat-skill"),
    )
    await conn.execute(
        """
        insert into agent_profile_revisions(
          tenant_id, agent_id, revision, status, revision_status,
          name, description, instructions, model_id, skill_id,
          skill_version, skill_set, mcp_tool_ids,
          supported_input_types, supported_file_types, content_hash, avatar_ref,
          avatar_seed, category, visibility, allowed_department_ids, allowed_roles,
          allowed_user_ids, legacy_compatibility_write, created_by,
          published_by, published_at, published_from_revision
        ) values (
          %s, %s, 1, 'published', 'published',
          %s, %s, %s, %s, %s,
          %s, jsonb_build_array(jsonb_build_object('skill_id', %s::text, 'expected_version', %s::text)),
          '[]'::jsonb, %s::jsonb, %s::jsonb, %s, 'builtin:agent',
          %s, 'general', 'tenant', '[]'::jsonb, '[]'::jsonb,
          '[]'::jsonb, false, %s, %s, now(), 1
        )
        """,
        (
            "tenant-profile-chat",
            "agt_profile_chat",
            "Profile Chat Agent",
            "Published profile for Chat locking",
            "private profile chat instructions",
            "model-a",
            "profile-chat-skill",
            skill_version,
            "profile-chat-skill",
            skill_version,
            *_canonical_profile_storage_lists(),
            profile_hash,
            "agt_profile_chat",
            "admin-profile-chat",
            "admin-profile-chat",
        ),
    )
    await conn.execute(
        """
        insert into agent_profiles(
          tenant_id, agent_id, lifecycle_status, latest_revision,
          published_revision, published_hash, published_status
        ) values (%s, %s, 'published', 1, 1, %s, 'published')
        """,
        ("tenant-profile-chat", "agt_profile_chat", profile_hash),
    )
    return profile_hash


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
async def test_postgres_agent_conversation_duplicate_start_is_exactly_once(monkeypatch):
    """Prove duplicate Start atomicity with two real PostgreSQL connections."""

    from app.agent_apps.authority import AgentProfileAuthority
    from app.auth import AuthPrincipal
    from app.models import SelectedAgentProfileRequest

    dsn = _postgres_dsn()
    schema_name = f"agent_conversation_start_{uuid.uuid4().hex}"
    first_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    second_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    observer_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    manifest = _profile_chat_manifest("profile-chat-skill")
    try:
        await first_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        for conn in (first_conn, second_conn, observer_conn):
            await _set_search_path(conn, schema_name)
        await first_conn.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await _seed_profile_chat_storage(first_conn, skill_version=str(manifest["content_hash"]))

        authority = AgentProfileAuthority()

        async def validate(*_args, **_kwargs):
            return (
                {
                    "skill_id": "profile-chat-skill",
                    "skill_version": str(manifest["content_hash"]),
                },
                {"id": "model-a", "value": "provider-model-a"},
            )

        monkeypatch.setattr(authority, "_validate_definition", validate)
        principal = AuthPrincipal(
            user_id="user-profile-chat",
            display_name="Profile Chat user",
            tenant_id="tenant-profile-chat",
            roles=["user"],
        )
        selection = SelectedAgentProfileRequest(
            agent_id="agt_profile_chat",
            expected_revision=1,
        )
        operation_id = uuid.UUID("7ea93033-30f5-40ea-8a33-2f3c6e7b21c4")

        async def start(conn: psycopg.AsyncConnection, *, title: str = ""):
            async with conn.transaction():
                return await authority.create_conversation(
                    conn,
                    principal=principal,
                    workspace_id="workspace-profile-chat",
                    selection=selection,
                    title=title,
                    operation_id=operation_id,
                )

        first, second = await asyncio.gather(start(first_conn), start(second_conn))
        assert first.session_id == second.session_id == f"ses_agent_{operation_id.hex}"

        session_count_cursor = await observer_conn.execute(
            "select count(*) as count from sessions where id = %s",
            (first.session_id,),
        )
        audit_count_cursor = await observer_conn.execute(
            """
            select count(*) as count
            from audit_logs
            where action = 'agent_conversation.created'
              and payload_json->>'session_id' = %s
            """,
            (first.session_id,),
        )
        assert (await session_count_cursor.fetchone())["count"] == 1
        assert (await audit_count_cursor.fetchone())["count"] == 1

        replay = await start(first_conn)
        assert replay.session_id == first.session_id
        with pytest.raises(repositories.RepositoryConflictError, match="agent_conversation_operation_conflict"):
            await start(second_conn, title="Different title")
    finally:
        try:
            await observer_conn.close()
            await second_conn.close()
            await first_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await first_conn.close()


@pytest.mark.asyncio
async def test_postgres_agent_history_projects_only_legacy_default_titles():
    dsn = _postgres_dsn()
    schema_name = f"agent_history_title_{uuid.uuid4().hex}"
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
        profile_hash = await _seed_profile_chat_storage(
            conn,
            skill_version="version-profile-chat",
        )

        async def create_history_session(
            session_id: str,
            title: str,
            title_source: str,
        ) -> None:
            await repositories.create_session(
                conn,
                tenant_id="tenant-profile-chat",
                workspace_id="workspace-profile-chat",
                agent_id="agt_profile_chat",
                user_id="user-profile-chat",
                title=title,
                title_source=title_source,
                session_id=session_id,
                admitted_agent_profile_revision=1,
                admitted_agent_profile_hash=profile_hash,
            )

        for session_id, title, title_source in (
            ("ses-legacy-default", "Profile Chat Agent", "initial"),
            ("ses-generated", "Generated task title", "generated"),
            ("ses-user-renamed", "User-selected title", "user"),
            ("ses-migrated-custom", "Legacy custom title", "initial"),
            ("ses-empty", "Profile Chat Agent", "initial"),
        ):
            await create_history_session(session_id, title, title_source)

        blank_task = " \n\t "
        first_task = "Investigate\nbatch variance and prepare a detailed remediation plan"
        await conn.execute(
            """
            insert into messages(
              id, tenant_id, session_id, run_id, role, content, metadata_json, created_at
            ) values
              ('msg-legacy-assistant', 'tenant-profile-chat', 'ses-legacy-default', null,
               'assistant', 'Assistant text must not become the title', '{}'::jsonb,
               '2026-08-20T00:00:00Z'::timestamptz),
              ('msg-legacy-blank', 'tenant-profile-chat', 'ses-legacy-default', null,
               'user', %s, '{}'::jsonb, '2026-08-20T00:00:30Z'::timestamptz),
              ('msg-legacy-first', 'tenant-profile-chat', 'ses-legacy-default', null,
               'user', %s, '{}'::jsonb, '2026-08-20T00:01:00Z'::timestamptz),
              ('msg-legacy-later', 'tenant-profile-chat', 'ses-legacy-default', null,
               'user', 'Later user task must not replace the first', '{}'::jsonb,
               '2026-08-20T00:02:00Z'::timestamptz),
              ('msg-generated', 'tenant-profile-chat', 'ses-generated', null,
               'user', 'Generated title must win', '{}'::jsonb,
               '2026-08-20T00:03:00Z'::timestamptz),
              ('msg-user-renamed', 'tenant-profile-chat', 'ses-user-renamed', null,
               'user', 'User rename must win', '{}'::jsonb,
               '2026-08-20T00:04:00Z'::timestamptz),
              ('msg-migrated-custom', 'tenant-profile-chat', 'ses-migrated-custom', null,
               'user', 'A migrated custom title must win', '{}'::jsonb,
               '2026-08-20T00:05:00Z'::timestamptz)
            """,
            (blank_task, first_task),
        )

        rows = await agent_conversation_repository.list_authorized_agent_conversations(
            conn,
            tenant_id="tenant-profile-chat",
            user_id="user-profile-chat",
            agent_id="agt_profile_chat",
            revision=1,
            cursor=None,
            limit=10,
        )

        assert {row["id"]: row["title"] for row in rows} == {
            "ses-legacy-default": first_task.replace("\n", " ")[:32],
            "ses-generated": "Generated task title",
            "ses-user-renamed": "User-selected title",
            "ses-migrated-custom": "Legacy custom title",
            "ses-empty": "Profile Chat Agent",
        }
    finally:
        try:
            await conn.execute(
                sql.SQL("drop schema if exists {} cascade").format(
                    sql.Identifier(schema_name)
                )
            )
        finally:
            await conn.close()


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


@pytest.mark.asyncio
async def test_postgres_profile_lock_is_held_through_queue_admission(monkeypatch):
    """Prove real aggregate-row contention while the queue adapter is paused."""

    from app.agent_apps.authority import AgentProfileAuthority
    from app.auth import AuthPrincipal
    from app.routes.runs import _ensure_run_control_queue_admission

    dsn = _postgres_dsn()
    schema_name = f"agent_profile_queue_lock_{uuid.uuid4().hex}"
    admission_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    lifecycle_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    observer_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    release_queue = asyncio.Event()
    queue_entered = asyncio.Event()
    manifest = _profile_chat_manifest("profile-skill")
    locked_skill_version = str(manifest["content_hash"])
    profile_hash = _canonical_profile_hash(
        agent_id="agt_profile",
        name="Profile agent",
        description="Published profile",
        instructions="private profile instructions",
        model_id="model-a",
        skill_id="profile-skill",
        skill_version=locked_skill_version,
    )
    admission_task = None
    withdrawal_task = None
    try:
        await admission_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        for conn in (admission_conn, lifecycle_conn, observer_conn):
            await _set_search_path(conn, schema_name)
        await admission_conn.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await admission_conn.execute(
            "insert into tenants(id, name) values (%s, %s)",
            ("tenant-profile", "Profile tenant"),
        )
        await admission_conn.execute(
            "insert into workspaces(id, tenant_id, name) values (%s, %s, %s)",
            ("workspace-profile", "tenant-profile", "Profile workspace"),
        )
        await admission_conn.execute(
            """
            insert into users(id, tenant_id, display_name)
            values (%s, %s, %s), (%s, %s, %s)
            """,
            (
                "user-profile",
                "tenant-profile",
                "Profile user",
                "admin-profile",
                "tenant-profile",
                "Profile admin",
            ),
        )
        await admission_conn.execute(
            "insert into skills(id, name, version, executor_type) values (%s, %s, %s, %s)",
            ("profile-skill", "Profile skill", locked_skill_version, "claude-agent-worker"),
        )
        await admission_conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type, default_skill_id)
            values (%s, %s, %s, 'profile', %s)
            """,
            ("agt_profile", "tenant-profile", "Profile agent", "profile-skill"),
        )
        await admission_conn.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, status, revision_status,
              name, description, instructions, model_id, skill_id,
              skill_version, skill_set, mcp_tool_ids,
              supported_input_types, supported_file_types, content_hash, avatar_ref,
              avatar_seed, category, visibility, allowed_department_ids, allowed_roles,
              allowed_user_ids, legacy_compatibility_write, created_by,
              published_by, published_at, published_from_revision
            ) values (
              %s, %s, 1, 'published', 'published',
              %s, %s, %s, %s, %s,
              %s, jsonb_build_array(jsonb_build_object('skill_id', %s::text, 'expected_version', %s::text)),
              '[]'::jsonb, %s::jsonb, %s::jsonb, %s, 'builtin:agent',
              %s, 'general', 'tenant', '[]'::jsonb, '[]'::jsonb,
              '[]'::jsonb, false, %s, %s, now(), 1
            )
            """,
            (
                "tenant-profile",
                "agt_profile",
                "Profile agent",
                "Published profile",
                "private profile instructions",
                "model-a",
                "profile-skill",
                locked_skill_version,
                "profile-skill",
                locked_skill_version,
                *_canonical_profile_storage_lists(),
                profile_hash,
                "agt_profile",
                "admin-profile",
                "admin-profile",
            ),
        )
        await admission_conn.execute(
            """
            insert into agent_profiles(
              tenant_id, agent_id, lifecycle_status, latest_revision,
              published_revision, published_hash, published_status
            ) values (%s, %s, 'published', 1, 1, %s, 'published')
            """,
            ("tenant-profile", "agt_profile", profile_hash),
        )
        await admission_conn.execute(
            """
            insert into sessions(
              id, tenant_id, workspace_id, user_id, agent_id, title,
              admitted_agent_profile_revision, admitted_agent_profile_hash
            ) values (%s, %s, %s, %s, %s, %s, 1, %s)
            """,
            (
                "ses-profile",
                "tenant-profile",
                "workspace-profile",
                "user-profile",
                "agt_profile",
                "Profile session",
                profile_hash,
            ),
        )
        execution_snapshot = {
            "input": {"message": "queued profile run", "mcp_tool_ids": []},
            "file_ids": [],
            "executor_type": "claude-agent-worker",
            "skill_version": locked_skill_version,
            "release_decision": {"selected_version": locked_skill_version},
            "skill_manifests": repositories.skill_manifest_refs([manifest]),
            "model_id": "model-a",
            "model_value": "provider-model-a",
            "agent_profile": {
                "agent_id": "agt_profile",
                "revision": 1,
                "content_hash": profile_hash,
                "instructions": "private profile instructions",
                "skill_set": [
                    {
                        "skill_id": "profile-skill",
                        "expected_version": locked_skill_version,
                    }
                ],
            },
        }
        await admission_conn.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id,
              skill_id, status, input_json, admitted_agent_profile_revision,
              admitted_agent_profile_hash
            ) values (%s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb, 1, %s)
            """,
            (
                "run-profile",
                "tenant-profile",
                "workspace-profile",
                "ses-profile",
                "user-profile",
                "agt_profile",
                "profile-skill",
                json.dumps(execution_snapshot),
                profile_hash,
            ),
        )
        await repositories.insert_run_skill_snapshots_at_creation(
            admission_conn,
            tenant_id="tenant-profile",
            run_id="run-profile",
            skill_manifests=[manifest],
            release_decision={"selected_version": locked_skill_version},
        )

        async def validate_definition(_self, _conn, *, principal, agent_id, definition):
            assert principal.tenant_id == "tenant-profile"
            assert agent_id == "agt_profile"
            assert definition.selected_skill.skill_id == "profile-skill"
            return (
                {
                    "skill_id": "profile-skill",
                    "skill_version": locked_skill_version,
                    "executor_type": "claude-agent-worker",
                },
                {"id": "model-a", "value": "provider-model-a"},
            )

        @asynccontextmanager
        async def admission_transaction():
            async with admission_conn.transaction():
                yield admission_conn

        async def paused_enqueue(payload):
            assert payload["run_id"] == "run-profile"
            queue_entered.set()
            await release_queue.wait()
            return 1

        monkeypatch.setattr(AgentProfileAuthority, "_validate_definition", validate_definition)
        monkeypatch.setattr("app.routes.runs.transaction", admission_transaction)
        monkeypatch.setattr("app.routes.runs.enqueue_run", paused_enqueue)

        admission_pid_cursor = await admission_conn.execute("select pg_backend_pid() as pid")
        admission_pid = (await admission_pid_cursor.fetchone())["pid"]
        lifecycle_pid_cursor = await lifecycle_conn.execute("select pg_backend_pid() as pid")
        lifecycle_pid = (await lifecycle_pid_cursor.fetchone())["pid"]

        user = AuthPrincipal(
            user_id="user-profile",
            display_name="Profile user",
            tenant_id="tenant-profile",
            roles=["user"],
        )
        admin = AuthPrincipal(
            user_id="admin-profile",
            display_name="Profile admin",
            tenant_id="tenant-profile",
            roles=["admin"],
        )
        admission_task = asyncio.create_task(
            _ensure_run_control_queue_admission(
                {"run_id": "run-profile"},
                check_existing=False,
                principal=user,
            )
        )
        await _wait_for_event_or_task(queue_entered, admission_task)
        authority_evidence_cursor = await observer_conn.execute(
            """
            select runs.input_json->'agent_profile' as agent_profile,
                   runs.input_json->'skill_manifests' as manifest_refs,
                   snapshots.skill_version, snapshots.content_hash,
                   snapshots.source_json,
                   materializations.materialization_sha256,
                   materializations.manifest_json
            from runs
            join run_skill_snapshots as snapshots
              on snapshots.tenant_id = runs.tenant_id
             and snapshots.run_id = runs.id
             and snapshots.skill_id = runs.skill_id
            join run_skill_materializations as materializations
              on materializations.tenant_id = snapshots.tenant_id
             and materializations.run_id = snapshots.run_id
             and materializations.skill_id = snapshots.skill_id
            where runs.tenant_id = %s and runs.id = %s
            """,
            ("tenant-profile", "run-profile"),
        )
        assert await authority_evidence_cursor.fetchone() == {
            "agent_profile": execution_snapshot["agent_profile"],
            "manifest_refs": execution_snapshot["skill_manifests"],
            "skill_version": locked_skill_version,
            "content_hash": locked_skill_version,
            "source_json": repositories.run_skill_snapshot_source_json(
                manifest,
                release_decision={"selected_version": locked_skill_version},
            ),
            "materialization_sha256": repositories.skill_manifest_materialization_sha256(
                manifest
            ),
            "manifest_json": manifest,
        }

        async def withdraw_profile():
            async with lifecycle_conn.transaction():
                return await AgentProfileAuthority().unpublish(
                    lifecycle_conn,
                    principal=admin,
                    agent_id="agt_profile",
                    expected_revision=1,
                )

        withdrawal_task = asyncio.create_task(withdraw_profile())

        async def wait_for_row_block() -> None:
            while True:
                cursor = await observer_conn.execute(
                    "select pg_blocking_pids(%s) as blockers",
                    (lifecycle_pid,),
                )
                blockers = (await cursor.fetchone())["blockers"]
                if admission_pid in blockers:
                    return
                await asyncio.sleep(0.02)

        await asyncio.wait_for(
            wait_for_row_block(),
            timeout=POSTGRES_CONCURRENCY_TIMEOUT_SECONDS,
        )
        assert not withdrawal_task.done()
        release_queue.set()
        admission = await asyncio.wait_for(
            admission_task,
            timeout=POSTGRES_CONCURRENCY_TIMEOUT_SECONDS,
        )
        withdrawn, _audit_id = await asyncio.wait_for(
            withdrawal_task,
            timeout=POSTGRES_CONCURRENCY_TIMEOUT_SECONDS,
        )
        assert admission.source == "idempotent_enqueue"
        assert withdrawn.status == "withdrawn"
        aggregate_cursor = await observer_conn.execute(
            """
            select lifecycle_status, published_revision
            from agent_profiles
            where tenant_id = %s and agent_id = %s
            """,
            ("tenant-profile", "agt_profile"),
        )
        assert await aggregate_cursor.fetchone() == {
            "lifecycle_status": "withdrawn",
            "published_revision": None,
        }
    finally:
        release_queue.set()
        pending = [task for task in (admission_task, withdrawal_task) if task is not None and not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            await observer_conn.close()
            await lifecycle_conn.close()
            await admission_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await admission_conn.close()


@pytest.mark.parametrize(
    "submission_id",
    [None, "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4"],
    ids=["unkeyed", "keyed"],
)
@pytest.mark.asyncio
async def test_postgres_chat_persistence_is_committed_before_profile_queue_dispatch(
    monkeypatch,
    submission_id,
):
    """Exercise real Chat authority and persistence while the queue adapter is paused."""

    from app.agent_apps.authority import AgentProfileAuthority
    from app.auth import AuthPrincipal
    from app.models import ChatStreamRequest, SelectedAgentProfileRequest
    from app.routes.chat import chat_stream

    dsn = _postgres_dsn()
    schema_name = f"agent_profile_chat_queue_{uuid.uuid4().hex}"
    admission_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    lifecycle_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    observer_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    manifest = _profile_chat_manifest("profile-chat-skill")
    release_queue = asyncio.Event()
    queue_entered = asyncio.Event()
    admission_task = None
    withdrawal_task = None
    try:
        await admission_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        for conn in (admission_conn, lifecycle_conn, observer_conn):
            await _set_search_path(conn, schema_name)
        await admission_conn.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        profile_hash = await _seed_profile_chat_storage(
            admission_conn,
            skill_version=str(manifest["content_hash"]),
        )

        @asynccontextmanager
        async def admission_transaction():
            async with admission_conn.transaction():
                yield admission_conn

        async def authorize_profile_skill(_conn, **kwargs):
            assert kwargs["agent_id"] == "agt_profile_chat"
            assert kwargs["skill_id"] == "profile-chat-skill"
            return {
                "skill_id": "profile-chat-skill",
                "skill_version": str(manifest["content_hash"]),
                "skill_content_hash": str(manifest["content_hash"]),
                "executor_type": "claude-agent-worker",
                "input_modes": [],
            }

        async def governed_manifest(*_args, **_kwargs):
            return [dict(manifest)]

        async def paused_enqueue(payload):
            assert payload["agent_profile"] == {
                "agent_id": "agt_profile_chat",
                "revision": 1,
                "content_hash": profile_hash,
                "instructions": "private profile chat instructions",
                "skill_set": [
                    {
                        "skill_id": "profile-chat-skill",
                        "expected_version": str(manifest["content_hash"]),
                    }
                ],
            }
            queue_entered.set()
            await release_queue.wait()
            return 1

        async def queue_insight(*_args, **_kwargs):
            return {}

        async def no_existing_queue_admission(*_args, **_kwargs):
            return None

        monkeypatch.setattr("app.routes.chat.transaction", admission_transaction)
        monkeypatch.setattr(
            "app.routes.chat.resolve_chat_model_selection",
            _resolve_legacy_chat_model,
        )
        monkeypatch.setattr(repositories, "authorize_selected_run_capabilities", authorize_profile_skill)
        monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", governed_manifest)
        monkeypatch.setattr("app.routes.chat.read_queue_admission", no_existing_queue_admission)
        monkeypatch.setattr("app.routes.chat.enqueue_run", paused_enqueue)
        monkeypatch.setattr("app.routes.chat.get_queue_insight", queue_insight)

        admission_pid_cursor = await admission_conn.execute("select pg_backend_pid() as pid")
        admission_pid = (await admission_pid_cursor.fetchone())["pid"]
        lifecycle_pid_cursor = await lifecycle_conn.execute("select pg_backend_pid() as pid")
        lifecycle_pid = (await lifecycle_pid_cursor.fetchone())["pid"]
        user = AuthPrincipal(
            user_id="user-profile-chat",
            display_name="Profile Chat user",
            tenant_id="tenant-profile-chat",
            roles=["user"],
        )
        admin = AuthPrincipal(
            user_id="admin-profile-chat",
            display_name="Profile Chat admin",
            tenant_id="tenant-profile-chat",
            roles=["admin"],
        )
        admission_task = asyncio.create_task(
            chat_stream(
                ChatStreamRequest(
                    workspace_id="workspace-profile-chat",
                    message="run the published profile",
                    selected_agent_profile=SelectedAgentProfileRequest(
                        agent_id="agt_profile_chat",
                        expected_revision=1,
                    ),
                    submission_id=submission_id,
                ),
                http_request=_TEST_CHAT_STREAM_REQUEST,
                principal=user,
            )
        )
        await _wait_for_event_or_task(queue_entered, admission_task)

        committed_cursor = await observer_conn.execute(
            "select count(*) as count from runs where tenant_id = %s",
            ("tenant-profile-chat",),
        )
        assert (await committed_cursor.fetchone())["count"] == 1
        pending_cursor = await observer_conn.execute(
            """
            select state
            from chat_submissions
            where tenant_id = %s and run_id is not null
            """,
            ("tenant-profile-chat",),
        )
        assert await pending_cursor.fetchone() == {"state": "accepted_pending_enqueue"}

        async def withdraw_profile():
            async with lifecycle_conn.transaction():
                return await AgentProfileAuthority().unpublish(
                    lifecycle_conn,
                    principal=admin,
                    agent_id="agt_profile_chat",
                    expected_revision=1,
                )

        withdrawal_task = asyncio.create_task(withdraw_profile())

        async def wait_for_profile_lock() -> None:
            while True:
                cursor = await observer_conn.execute(
                    "select pg_blocking_pids(%s) as blockers",
                    (lifecycle_pid,),
                )
                if admission_pid in (await cursor.fetchone())["blockers"]:
                    return
                await asyncio.sleep(0.02)

        await asyncio.wait_for(
            wait_for_profile_lock(),
            timeout=POSTGRES_CONCURRENCY_TIMEOUT_SECONDS,
        )
        assert not withdrawal_task.done()
        release_queue.set()
        response = await asyncio.wait_for(
            admission_task,
            timeout=POSTGRES_CONCURRENCY_TIMEOUT_SECONDS,
        )
        withdrawn, _audit_id = await asyncio.wait_for(
            withdrawal_task,
            timeout=POSTGRES_CONCURRENCY_TIMEOUT_SECONDS,
        )

        assert response.status == "queued"
        assert response.submission_id is not None
        assert withdrawn.status == "withdrawn"
        persisted_cursor = await observer_conn.execute(
            """
            select runs.status, runs.admitted_agent_profile_revision,
                   runs.admitted_agent_profile_hash,
                   sessions.admitted_agent_profile_revision as session_revision,
                   sessions.admitted_agent_profile_hash as session_hash
            from runs
            join sessions on sessions.tenant_id = runs.tenant_id and sessions.id = runs.session_id
            where runs.tenant_id = %s and runs.id = %s
            """,
            ("tenant-profile-chat", response.run_id),
        )
        assert await persisted_cursor.fetchone() == {
            "status": "queued",
            "admitted_agent_profile_revision": 1,
            "admitted_agent_profile_hash": profile_hash,
            "session_revision": 1,
            "session_hash": profile_hash,
        }
        submission_cursor = await observer_conn.execute(
            "select submission_id::text, state from chat_submissions where tenant_id = %s and run_id = %s",
            ("tenant-profile-chat", response.run_id),
        )
        assert await submission_cursor.fetchone() == {
            "submission_id": response.submission_id,
            "state": "queued",
        }
    finally:
        release_queue.set()
        pending = [task for task in (admission_task, withdrawal_task) if task is not None and not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            await observer_conn.close()
            await lifecycle_conn.close()
            await admission_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await admission_conn.close()


@pytest.mark.asyncio
async def test_postgres_profile_queue_dispatch_is_not_emitted_after_producer_rollback(monkeypatch):
    """A failed producer commit cannot emit a worker-visible queue payload."""

    from app.auth import AuthPrincipal
    from app.models import ChatStreamRequest, SelectedAgentProfileRequest
    from app.routes.chat import chat_stream
    from fastapi import HTTPException

    dsn = _postgres_dsn()
    schema_name = f"agent_profile_chat_rollback_{uuid.uuid4().hex}"
    producer_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    observer_conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    manifest = _profile_chat_manifest("profile-chat-skill")
    enqueued_payloads: list[dict[str, object]] = []
    try:
        await producer_conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        for conn in (producer_conn, observer_conn):
            await _set_search_path(conn, schema_name)
        await producer_conn.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await _seed_profile_chat_storage(
            producer_conn,
            skill_version=str(manifest["content_hash"]),
        )

        @asynccontextmanager
        async def rolled_back_transaction():
            async with producer_conn.transaction(force_rollback=True):
                yield producer_conn
            raise RuntimeError("forced producer commit failure")

        async def authorize_profile_skill(_conn, **_kwargs):
            return {
                "skill_id": "profile-chat-skill",
                "skill_version": str(manifest["content_hash"]),
                "skill_content_hash": str(manifest["content_hash"]),
                "executor_type": "claude-agent-worker",
                "input_modes": [],
            }

        async def governed_manifest(*_args, **_kwargs):
            return [dict(manifest)]

        async def capture_enqueue(payload):
            enqueued_payloads.append(payload)
            return 1

        monkeypatch.setattr("app.routes.chat.transaction", rolled_back_transaction)
        monkeypatch.setattr(
            "app.routes.chat.resolve_chat_model_selection",
            _resolve_legacy_chat_model,
        )
        monkeypatch.setattr(repositories, "authorize_selected_run_capabilities", authorize_profile_skill)
        monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", governed_manifest)
        monkeypatch.setattr("app.routes.chat.enqueue_run", capture_enqueue)

        with pytest.raises(HTTPException) as exc_info:
            await chat_stream(
                ChatStreamRequest(
                    workspace_id="workspace-profile-chat",
                    message="enqueue before forced rollback",
                    selected_agent_profile=SelectedAgentProfileRequest(
                        agent_id="agt_profile_chat",
                        expected_revision=1,
                    ),
                ),
                http_request=_TEST_CHAT_STREAM_REQUEST,
                principal=AuthPrincipal(
                    user_id="user-profile-chat",
                    display_name="Profile Chat user",
                    tenant_id="tenant-profile-chat",
                    roles=["user"],
                ),
            )
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail["code"] == "chat_submission_internal_error"
        assert "forced producer commit failure" not in json.dumps(exc_info.value.detail)
        assert enqueued_payloads == []

        persisted_cursor = await observer_conn.execute(
            "select count(*) as run_count from runs where tenant_id = %s",
            ("tenant-profile-chat",),
        )
        assert (await persisted_cursor.fetchone())["run_count"] == 0
        submission_cursor = await observer_conn.execute(
            "select count(*) as submission_count from chat_submissions where tenant_id = %s",
            ("tenant-profile-chat",),
        )
        assert (await submission_cursor.fetchone())["submission_count"] == 0
    finally:
        try:
            await observer_conn.close()
            await producer_conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await producer_conn.close()


@pytest.mark.asyncio
async def test_postgres_pre_701_upgrade_and_old_binary_rollback_redeploy_converge():
    """Exercise populated legacy migration and app-binary rollback against the retained schema."""

    from app.agent_apps import profile_acl_allows
    from app.auth import AuthPrincipal

    dsn = _postgres_dsn()
    schema_name = f"agent_profile_legacy_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute(PRE_701_PROFILE_DOWNGRADE_SQL)
        await conn.execute(
            "insert into tenants(id, name) values (%s, %s)",
            ("tenant-legacy", "Legacy Tenant"),
        )
        await conn.execute(
            """
            insert into users(id, tenant_id, display_name)
            values (%s, %s, %s), (%s, %s, %s)
            """,
            (
                "admin-legacy",
                "tenant-legacy",
                "Legacy Admin",
                "user-legacy",
                "tenant-legacy",
                "Legacy User",
            ),
        )
        await conn.execute(
            "insert into skills(id, name, version, executor_type) values (%s, %s, %s, %s)",
            ("legacy-skill", "Legacy skill", "version-a", "fake"),
        )
        await conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type, default_skill_id)
            values
              ('agt_legacy', 'tenant-legacy', 'Legacy visible', 'profile', 'legacy-skill'),
              ('agt_restricted', 'tenant-legacy', 'Malformed visibility', 'profile', 'legacy-skill'),
              ('agt_withdrawn', 'tenant-legacy', 'Later withdrawn', 'profile', 'legacy-skill')
            """
        )
        await conn.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, status, name, description, instructions,
              model_id, skill_id, skill_version, mcp_tool_ids, content_hash,
              created_by, published_by, published_at
            ) values
              ('tenant-legacy', 'agt_legacy', 1, 'published', 'Legacy visible', '', 'private legacy',
               'model-a', 'legacy-skill', 'version-a', '[]'::jsonb, %s,
               'admin-legacy', 'admin-legacy', now()),
              ('tenant-legacy', 'agt_restricted', 1, 'published', 'Malformed visibility', '', 'private restricted',
               'model-a', 'legacy-skill', 'version-a', '[]'::jsonb, %s,
               'admin-legacy', 'admin-legacy', now()),
              ('tenant-legacy', 'agt_withdrawn', 1, 'published', 'Later withdrawn', '', 'private withdrawn',
               'model-a', 'legacy-skill', 'version-a', '[]'::jsonb, %s,
               'admin-legacy', 'admin-legacy', now())
            """,
            ("a" * 64, "b" * 64, "c" * 64),
        )
        # NULL identifies a row that predates the column. Only the explicitly
        # malformed value is repaired to restricted.
        await conn.execute("alter table agent_profile_revisions add column visibility text")
        await conn.execute(
            "update agent_profile_revisions set visibility = 'malformed' where agent_id = 'agt_restricted'"
        )

        await conn.execute(schema_sql)
        await conn.execute(schema_sql)

        migrated = await conn.execute(
            """
            select agent_id, revision_status, status, visibility, legacy_compatibility_write
            from agent_profile_revisions
            where tenant_id = 'tenant-legacy'
            order by agent_id, revision
            """
        )
        assert await migrated.fetchall() == [
            {
                "agent_id": "agt_legacy",
                "revision_status": "published",
                "status": "published",
                "visibility": "tenant",
                "legacy_compatibility_write": True,
            },
            {
                "agent_id": "agt_restricted",
                "revision_status": "published",
                "status": "draft",
                "visibility": "restricted",
                "legacy_compatibility_write": False,
            },
            {
                "agent_id": "agt_withdrawn",
                "revision_status": "published",
                "status": "published",
                "visibility": "tenant",
                "legacy_compatibility_write": True,
            },
        ]
        principal = AuthPrincipal(
            user_id="user-legacy",
            display_name="Legacy User",
            tenant_id="tenant-legacy",
            roles=["user"],
        )
        current_rows = await repositories.list_current_published_agent_profiles(
            conn,
            tenant_id="tenant-legacy",
        )
        visible_ids = {
            str(row["agent_id"])
            for row in current_rows
            if profile_acl_allows(row, principal=principal)
        }
        assert "agt_legacy" in visible_ids
        assert "agt_restricted" not in visible_ids

        # A current backend draft occupies revision 2. The rolled-back binary
        # retries that revision without new columns; the trigger preserves the
        # immutable row, mints revision 3, and atomically moves the pointer.
        async with conn.transaction():
            current_draft = await repositories.create_agent_profile_revision(
                conn,
                tenant_id="tenant-legacy",
                agent_id="agt_legacy",
                status="draft",
                name="Current draft",
                description="",
                instructions="private current draft",
                model_id="model-a",
                skill_id="legacy-skill",
                skill_version="version-a",
                mcp_tool_ids=[],
                content_hash="d" * 64,
                created_by="admin-legacy",
                expected_previous_revision=1,
                visibility="tenant",
            )
            await repositories.record_agent_profile_draft(
                conn,
                tenant_id="tenant-legacy",
                agent_id="agt_legacy",
                revision=int(current_draft["revision"]),
            )
        old_publish = await conn.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, status, name, description, instructions,
              model_id, skill_id, skill_version, mcp_tool_ids, content_hash,
              created_by, published_by, published_at, published_from_revision
            ) values (
              'tenant-legacy', 'agt_legacy', 2, 'published', 'Rollback publish', '', 'private rollback',
              'model-a', 'legacy-skill', 'version-a', '[]'::jsonb, %s,
              'admin-legacy', 'admin-legacy', now(), 2
            )
            returning revision, revision_status, status, content_hash, visibility
            """,
            ("e" * 64,),
        )
        assert await old_publish.fetchone() == {
            "revision": 3,
            "revision_status": "published",
            "status": "published",
            "content_hash": "e" * 64,
            "visibility": "tenant",
        }
        immutable_draft = await conn.execute(
            "select content_hash, revision_status from agent_profile_revisions where agent_id = 'agt_legacy' and revision = 2"
        )
        assert await immutable_draft.fetchone() == {
            "content_hash": "d" * 64,
            "revision_status": "draft",
        }

        # A rolled-back draft write must reconstruct an accidentally missing
        # aggregate without silently replacing its valid current publication.
        await conn.execute(
            "delete from agent_profiles where tenant_id = 'tenant-legacy' and agent_id = 'agt_legacy'"
        )
        old_draft = await conn.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, status, name, description, instructions,
              model_id, skill_id, skill_version, mcp_tool_ids, content_hash, created_by
            ) values (
              'tenant-legacy', 'agt_legacy', 4, 'draft', 'Rollback draft', '', 'private rollback draft',
              'model-a', 'legacy-skill', 'version-a', '[]'::jsonb, %s, 'admin-legacy'
            )
            returning revision, revision_status, status
            """,
            ("3" * 64,),
        )
        assert await old_draft.fetchone() == {
            "revision": 4,
            "revision_status": "draft",
            "status": "draft",
        }
        recovered_after_old_draft = await conn.execute(
            """
            select lifecycle_status, latest_revision, published_revision,
                   published_hash, published_status
            from agent_profiles
            where tenant_id = 'tenant-legacy' and agent_id = 'agt_legacy'
            """
        )
        expected_legacy_recovery = {
            "lifecycle_status": "published",
            "latest_revision": 4,
            "published_revision": 3,
            "published_hash": "e" * 64,
            "published_status": "published",
        }
        assert await recovered_after_old_draft.fetchone() == expected_legacy_recovery

        # Migration-time reconstruction converges to the same exact pointer.
        await conn.execute(
            "delete from agent_profiles where tenant_id = 'tenant-legacy' and agent_id = 'agt_legacy'"
        )
        await conn.execute(schema_sql)
        recovered_after_redeploy = await conn.execute(
            """
            select lifecycle_status, latest_revision, published_revision,
                   published_hash, published_status
            from agent_profiles
            where tenant_id = 'tenant-legacy' and agent_id = 'agt_legacy'
            """
        )
        assert await recovered_after_redeploy.fetchone() == expected_legacy_recovery

        restricted_old_publish = await conn.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, status, name, description, instructions,
              model_id, skill_id, skill_version, mcp_tool_ids, content_hash,
              created_by, published_by, published_at, published_from_revision
            ) values (
              'tenant-legacy', 'agt_restricted', 2, 'published', 'Rollback restricted', '', 'private rollback',
              'model-a', 'legacy-skill', 'version-a', '[]'::jsonb, %s,
              'admin-legacy', 'admin-legacy', now(), 1
            )
            returning revision_status, status, visibility
            """,
            ("f" * 64,),
        )
        assert await restricted_old_publish.fetchone() == {
            "revision_status": "draft",
            "status": "draft",
            "visibility": "restricted",
        }

        async with conn.transaction():
            withdrawn = await repositories.create_agent_profile_revision(
                conn,
                tenant_id="tenant-legacy",
                agent_id="agt_withdrawn",
                status="withdrawn",
                name="Later withdrawn",
                description="",
                instructions="private withdrawn",
                model_id="model-a",
                skill_id="legacy-skill",
                skill_version="version-a",
                mcp_tool_ids=[],
                content_hash="c" * 64,
                created_by="admin-legacy",
                expected_previous_revision=1,
                withdrawn_from_revision=1,
                visibility="tenant",
            )
            await repositories.record_agent_profile_withdrawal(
                conn,
                tenant_id="tenant-legacy",
                agent_id="agt_withdrawn",
                revision=int(withdrawn["revision"]),
            )
            post_withdrawal_draft = await repositories.create_agent_profile_revision(
                conn,
                tenant_id="tenant-legacy",
                agent_id="agt_withdrawn",
                status="draft",
                name="Post-withdrawal draft",
                description="",
                instructions="private post-withdrawal draft",
                model_id="model-a",
                skill_id="legacy-skill",
                skill_version="version-a",
                mcp_tool_ids=[],
                content_hash="1" * 64,
                created_by="admin-legacy",
                expected_previous_revision=2,
                visibility="tenant",
            )
            await repositories.record_agent_profile_draft(
                conn,
                tenant_id="tenant-legacy",
                agent_id="agt_withdrawn",
                revision=int(post_withdrawal_draft["revision"]),
            )
        # Rebuilding a missing aggregate must retain a terminal withdrawal even
        # when a newer draft exists in immutable history.
        await conn.execute(
            "delete from agent_profiles where tenant_id = 'tenant-legacy' and agent_id = 'agt_withdrawn'"
        )
        await conn.execute(schema_sql)
        reconstructed_withdrawal = await conn.execute(
            """
            select lifecycle_status, latest_revision, published_revision
            from agent_profiles
            where tenant_id = 'tenant-legacy' and agent_id = 'agt_withdrawn'
            """
        )
        assert await reconstructed_withdrawal.fetchone() == {
            "lifecycle_status": "withdrawn",
            "latest_revision": 3,
            "published_revision": None,
        }

        # Repeat without the aggregate so the compatibility trigger itself must
        # derive the withdrawal before processing a rolled-back writer.
        await conn.execute(
            "delete from agent_profiles where tenant_id = 'tenant-legacy' and agent_id = 'agt_withdrawn'"
        )
        withdrawn_old_publish = await conn.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, status, name, description, instructions,
              model_id, skill_id, skill_version, mcp_tool_ids, content_hash,
              created_by, published_by, published_at, published_from_revision
            ) values (
              'tenant-legacy', 'agt_withdrawn', 4, 'published', 'Rollback withdrawn', '', 'private rollback',
              'model-a', 'legacy-skill', 'version-a', '[]'::jsonb, %s,
              'admin-legacy', 'admin-legacy', now(), 3
            )
            returning revision_status, status, visibility
            """,
            ("2" * 64,),
        )
        assert await withdrawn_old_publish.fetchone() == {
            "revision_status": "draft",
            "status": "draft",
            "visibility": "tenant",
        }
        withdrawn_after_old_write = await conn.execute(
            """
            select lifecycle_status, latest_revision, published_revision
            from agent_profiles
            where tenant_id = 'tenant-legacy' and agent_id = 'agt_withdrawn'
            """
        )
        assert await withdrawn_after_old_write.fetchone() == {
            "lifecycle_status": "withdrawn",
            "latest_revision": 4,
            "published_revision": None,
        }

        before_profiles, before_revisions = await _agent_profile_storage_projection(
            conn,
            tenant_id="tenant-legacy",
        )

        await conn.execute(schema_sql)
        first_profiles, first_revisions = await _agent_profile_storage_projection(
            conn,
            tenant_id="tenant-legacy",
        )
        assert (first_profiles, first_revisions) == (before_profiles, before_revisions)

        await conn.execute(schema_sql)
        second_profiles, second_revisions = await _agent_profile_storage_projection(
            conn,
            tenant_id="tenant-legacy",
        )
        assert (second_profiles, second_revisions) == (before_profiles, before_revisions)
        profile_state = {row["agent_id"]: row for row in before_profiles}
        assert profile_state["agt_legacy"]["published_revision"] == 3
        assert profile_state["agt_legacy"]["latest_revision"] == 4
        assert profile_state["agt_restricted"]["published_revision"] == 1
        assert profile_state["agt_withdrawn"]["lifecycle_status"] == "withdrawn"
        assert profile_state["agt_withdrawn"]["published_revision"] is None
        old_reader = await conn.execute(
            """
            select distinct agent_id
            from agent_profile_revisions
            where tenant_id = 'tenant-legacy' and status = 'published'
            order by agent_id
            """
        )
        assert await old_reader.fetchall() == [{"agent_id": "agt_legacy"}]
        legacy_statuses = await conn.execute(
            """
            select distinct status
            from agent_profile_revisions
            where tenant_id = 'tenant-legacy'
            order by status
            """
        )
        assert {row["status"] for row in await legacy_statuses.fetchall()} <= {
            "draft",
            "published",
        }
        withdrawn_latest = await conn.execute(
            """
            select status
            from agent_profile_revisions
            where tenant_id = 'tenant-legacy' and agent_id = 'agt_withdrawn'
            order by revision desc
            limit 1
            """
        )
        assert await withdrawn_latest.fetchone() == {"status": "draft"}
    finally:
        try:
            await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await conn.close()
