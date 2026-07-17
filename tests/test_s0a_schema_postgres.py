import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


@pytest.mark.asyncio
async def test_s0a_schema_workspace_scope_and_runtime_handle_apply_idempotently():
    dsn = _postgres_dsn()
    schema_name = f"s0a_schema_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute(schema_sql)

        constraint_cursor = await conn.execute(
            """
            select conname, convalidated
            from pg_constraint
            where conname in ('fk_sessions_workspace_scope', 'fk_runs_workspace_scope')
              and conrelid in (to_regclass(%s), to_regclass(%s))
            order by conname
            """,
            (f"{schema_name}.sessions", f"{schema_name}.runs"),
        )
        assert await constraint_cursor.fetchall() == [
            {"conname": "fk_runs_workspace_scope", "convalidated": True},
            {"conname": "fk_sessions_workspace_scope", "convalidated": True},
        ]

        column_cursor = await conn.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = %s
              and table_name = 'sandbox_leases'
              and column_name in (
                'runtime_container_id',
                'runtime_container_name',
                'runtime_executor_url',
                'runtime_workspace_container_path',
                'runtime_handle_verified_at'
              )
            order by column_name
            """,
            (schema_name,),
        )
        assert [row["column_name"] for row in await column_cursor.fetchall()] == [
            "runtime_container_id",
            "runtime_container_name",
            "runtime_executor_url",
            "runtime_handle_verified_at",
            "runtime_workspace_container_path",
        ]

        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A'), ('tenant-b', 'Tenant B')")
        await conn.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A'), ('workspace-b', 'tenant-b', 'B')"
        )
        await conn.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')")
        await conn.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'Agent A', 'chat')"
        )
        await conn.execute(
            "insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1.0.0', 'fake')"
        )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                """
                insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
                values ('session-wrong', 'tenant-a', 'workspace-b', 'user-a', 'agent-a', 'Wrong')
                """
            )

        await conn.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
            values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'Valid')
            """
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                """
                insert into runs(
                  id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status
                ) values ('run-wrong', 'tenant-a', 'workspace-b', 'session-a', 'user-a', 'agent-a', 'skill-a', 'queued')
                """
            )

        # A first authenticated principal has no pre-existing users row. The
        # ledger's immediate user FK must therefore be provisioned before its
        # first claim, in the exact tenant scope of the principal.
        await repositories.ensure_submission_principal(
            conn,
            tenant_id="tenant-a",
            user_id="user-first-submission",
            display_name="First Submission User",
        )
        submission, created = await repositories.claim_chat_submission(
            conn,
            tenant_id="tenant-a",
            user_id="user-first-submission",
            submission_id=str(uuid.uuid4()),
            workspace_id="workspace-a",
            request_fingerprint_sha256="a" * 64,
        )
        assert created is True
        assert submission["user_id"] == "user-first-submission"
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()


@pytest.mark.asyncio
async def test_context_snapshot_member_eligibility_is_atomic_in_postgres():
    """Exercise #488's single-statement member checks against a real PostgreSQL schema."""
    dsn = _postgres_dsn()
    schema_name = f"context_snapshot_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_sql)
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await conn.execute("insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')")
        await conn.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')")
        await conn.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'Agent A', 'chat')"
        )
        await conn.execute("insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1.0.0', 'fake')")
        await conn.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
            values
              ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A'),
              ('session-other', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'Other')
            """
        )
        await conn.execute(
            """
            insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status)
            values
              ('run-prior', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'succeeded'),
              ('run-current', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'running'),
              ('run-other', 'tenant-a', 'workspace-a', 'session-other', 'user-a', 'agent-a', 'skill-a', 'succeeded')
            """
        )
        await conn.execute(
            """
            insert into messages(id, tenant_id, session_id, run_id, role, content)
            values
              ('msg-prior', 'tenant-a', 'session-a', 'run-prior', 'user', 'prior'),
              ('msg-null', 'tenant-a', 'session-a', null, 'user', 'unbound'),
              ('msg-other', 'tenant-a', 'session-other', 'run-other', 'user', 'other')
            """
        )
        await conn.execute(
            """
            insert into files(id, tenant_id, workspace_id, user_id, session_id, run_id, original_name, content_type, size_bytes, storage_key, sha256)
            values
              ('file-prior', 'tenant-a', 'workspace-a', 'user-a', 'session-a', 'run-prior', 'prior.txt', 'text/plain', 1, 'files/prior', 'a'),
              ('file-null', 'tenant-a', 'workspace-a', 'user-a', 'session-a', null, 'null.txt', 'text/plain', 1, 'files/null', 'b')
            """
        )
        await conn.execute(
            """
            insert into artifacts(id, tenant_id, run_id, artifact_type, label, content_type, storage_key, size_bytes, expires_at)
            values
              ('art-prior', 'tenant-a', 'run-prior', 'text', 'prior', 'text/plain', 'artifacts/prior', 1, statement_timestamp() + interval '1 day'),
              ('art-expired', 'tenant-a', 'run-prior', 'text', 'expired', 'text/plain', 'artifacts/expired', 1, statement_timestamp() - interval '1 second')
            """
        )
        await conn.execute(
            """
            insert into memory_records(id, tenant_id, workspace_id, user_id, agent_id, session_id, record_type, content, status, expires_at)
            values
              ('mem-prior', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'session-a', 'note', 'prior', 'active', statement_timestamp() + interval '1 day'),
              ('mem-inactive', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'session-a', 'note', 'inactive', 'inactive', statement_timestamp() + interval '1 day'),
              ('mem-expired', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'session-a', 'note', 'expired', 'active', statement_timestamp() - interval '1 second')
            """
        )

        common = {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-untrusted",
            "user_id": "user-a",
            "session_id": "session-untrusted",
            "run_id": "run-current",
            "trace_id": "trace-untrusted",
            "context_kind": "executor",
            "redaction_summary_json": {},
            "payload_json": {},
        }
        snapshot = await repositories.create_context_snapshot(
            conn,
            included_message_ids=["msg-prior"],
            included_file_ids=["file-prior"],
            included_artifact_ids=["art-prior"],
            included_memory_record_ids=["mem-prior"],
            **common,
        )
        assert snapshot["workspace_id"] == "workspace-a"
        assert snapshot["session_id"] == "session-a"

        invalid_batches = [
            ("message_without_source_run", "included_message_ids", ["msg-prior", "msg-null"]),
            ("message_other_session", "included_message_ids", ["msg-prior", "msg-other"]),
            ("file_without_source_run", "included_file_ids", ["file-prior", "file-null"]),
            ("expired_artifact", "included_artifact_ids", ["art-prior", "art-expired"]),
            ("inactive_memory", "included_memory_record_ids", ["mem-prior", "mem-inactive"]),
            ("expired_memory", "included_memory_record_ids", ["mem-prior", "mem-expired"]),
        ]
        for _case, field, member_ids in invalid_batches:
            material_ids = {
                "included_message_ids": ["msg-prior"],
                "included_file_ids": ["file-prior"],
                "included_artifact_ids": ["art-prior"],
                "included_memory_record_ids": ["mem-prior"],
            }
            material_ids[field] = member_ids
            with pytest.raises(repositories.RepositoryConflictError, match="context_snapshot_material_invalid"):
                await repositories.create_context_snapshot(conn, **common, **material_ids)
            count_cursor = await conn.execute("select count(*) as count from run_context_snapshots")
            assert (await count_cursor.fetchone())["count"] == 1
    finally:
        await conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await conn.close()
