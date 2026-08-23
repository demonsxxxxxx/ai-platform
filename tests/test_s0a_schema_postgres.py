import hashlib
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories
from app.platform.postgres.errors import RepositoryConflictError
from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    compile_execution_spec,
)
from app.runs.infrastructure.postgres import create_run_attempt, transition_run_attempt


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))


def _execution_spec():
    return compile_execution_spec(
        {
            "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
            "run_payload_schema_version": "ai-platform.run-payload.v1",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "execution_kind": "skill",
            "skill_id": "skill-a",
            "file_ids": [],
            "input": {"message": "hello"},
            "executor_type": "fake",
            "trace_id": "trace-a",
            "skill_version": "version-a",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "selected_version": "version-a",
            },
            "skill_manifests": [
                {"skill_id": "skill-a", "content_hash": "version-a"}
            ],
            "context_snapshot_id": "context-a",
            "context_snapshot": {"context_snapshot_id": "context-a"},
            "context_pack": {},
            "model_id": "model-a",
            "model_value": "model-a",
            "agent_profile": {},
        }
    )


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
                'attempt_id',
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
            "attempt_id",
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

        await conn.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status
            ) values (
              'run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a',
              'agent-a', 'skill-a', 'queued'
            )
            """
        )
        execution_spec = _execution_spec()
        canonical_json = execution_spec.canonical_json.decode("utf-8")
        spec_sql_params = (
            canonical_json,
            canonical_json,
            execution_spec.spec_sha256,
        )
        created_attempt = await create_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            ordinal=1,
            owner_kind="queue_worker",
            owner_id="worker-a",
            queue_attempt_id="queue-attempt-a",
            execution_spec=execution_spec,
        )
        assert created_attempt["status"] == "created"
        queued_attempt = await transition_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            expected_status="created",
            requested_status="queued",
            expected_owner_kind="queue_worker",
            expected_owner_id="worker-a",
            expected_owner_generation=1,
            queue_message_id="queue-message-a",
        )
        assert queued_attempt["owner_generation"] == 2
        claimed_attempt = await transition_run_attempt(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            expected_status="queued",
            requested_status="claimed",
            expected_owner_kind="queue_worker",
            expected_owner_id="worker-a",
            expected_owner_generation=2,
        )
        assert claimed_attempt["owner_generation"] == 3
        await conn.execute(
            "update runs set status = 'succeeded', finished_at = now() where id = 'run-a'"
        )
        with pytest.raises(
            RepositoryConflictError,
            match="run_attempt_transition_conflict",
        ):
            await transition_run_attempt(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                expected_status="claimed",
                requested_status="failed",
                expected_owner_kind="queue_worker",
                expected_owner_id="worker-a",
                expected_owner_generation=3,
                terminal_reason="legacy_terminal_conflict",
            )
        conflict_cursor = await conn.execute(
            """
            select runs.status as run_status, run_attempts.status as attempt_status
            from runs
            join run_attempts on run_attempts.run_id = runs.id
            where runs.tenant_id = 'tenant-a' and runs.id = 'run-a'
            """
        )
        assert await conflict_cursor.fetchone() == {
            "run_status": "succeeded",
            "attempt_status": "claimed",
        }
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  queue_attempt_id, execution_spec_schema_version,
                  execution_spec_json, execution_spec_canonical_json,
                  execution_spec_sha256
                ) values (
                  'attempt-wrong-parent', 'tenant-a', 'run-a', 2, 'created',
                  'queue_worker', 'worker-parent', 'queue-attempt-parent',
                  'ai-platform.execution-spec.v1', %s::jsonb, %s, %s
                )
                """,
                spec_sql_params,
            )
        await conn.execute(
            "update runs set status = 'queued', finished_at = null where id = 'run-a'"
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  queue_attempt_id, execution_spec_schema_version,
                  execution_spec_json, execution_spec_canonical_json,
                  execution_spec_sha256, finished_at, terminal_reason
                ) values (
                  'attempt-terminal', 'tenant-a', 'run-a', 2, 'failed',
                  'queue_worker', 'worker-terminal', 'queue-attempt-terminal',
                  'ai-platform.execution-spec.v1', %s::jsonb, %s, %s,
                  now(), 'bypassed_state_machine'
                )
                """,
                spec_sql_params,
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  queue_attempt_id, execution_spec_schema_version,
                  execution_spec_json, execution_spec_canonical_json,
                  execution_spec_sha256
                ) values (
                  'attempt-digest-drift', 'tenant-a', 'run-a', 2, 'created',
                  'queue_worker', 'worker-digest', 'queue-attempt-digest',
                  'ai-platform.execution-spec.v1', %s::jsonb, %s, %s
                )
                """,
                (canonical_json, canonical_json, "0" * 64),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  queue_attempt_id, execution_spec_schema_version,
                  execution_spec_json, execution_spec_canonical_json,
                  execution_spec_sha256
                ) values (
                  'attempt-json-drift', 'tenant-a', 'run-a', 2, 'created',
                  'queue_worker', 'worker-json', 'queue-attempt-json',
                  'ai-platform.execution-spec.v1', %s::jsonb, %s, %s
                )
                """,
                (
                    canonical_json,
                    "{}",
                    hashlib.sha256(b"{}").hexdigest(),
                ),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  queue_attempt_id, execution_spec_schema_version,
                  execution_spec_json, execution_spec_canonical_json,
                  execution_spec_sha256
                ) values (
                  'attempt-empty-owner', 'tenant-a', 'run-a', 2, 'created',
                  'queue_worker', '', 'queue-attempt-empty',
                  'ai-platform.execution-spec.v1', %s::jsonb, %s, %s
                )
                """,
                spec_sql_params,
            )
        with pytest.raises(psycopg.errors.UniqueViolation):
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  queue_attempt_id, execution_spec_schema_version,
                  execution_spec_json, execution_spec_canonical_json,
                  execution_spec_sha256
                ) values (
                  'attempt-b', 'tenant-a', 'run-a', 2, 'created', 'queue_worker',
                  'worker-b', 'queue-attempt-b', 'ai-platform.execution-spec.v1',
                  %s::jsonb, %s, %s
                )
                """,
                spec_sql_params,
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
            update run_attempts
            set status = 'expired', owner_generation = 4
            where tenant_id = 'tenant-a' and id = 'attempt-a'
                """
            )
        await conn.execute(
            """
            update run_attempts
            set status = 'expired',
                owner_kind = 'reconciler',
                owner_id = 'reconciler-a',
                owner_generation = 4
            where tenant_id = 'tenant-a' and id = 'attempt-a'
            """
        )
        projected_cursor = await conn.execute(
            "select status from runs where tenant_id = 'tenant-a' and id = 'run-a'"
        )
        assert (await projected_cursor.fetchone())["status"] == "running"
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                update run_attempts
                set status = 'succeeded',
                    owner_generation = 5,
                    finished_at = now()
                where tenant_id = 'tenant-a' and id = 'attempt-a'
                """
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                update run_attempts
                set status = 'failed', owner_generation = 4
                where tenant_id = 'tenant-a' and id = 'attempt-a'
                """
            )
        await conn.execute(
            """
            update run_attempts
            set status = 'failed', owner_generation = 5, finished_at = now()
            where tenant_id = 'tenant-a' and id = 'attempt-a'
            """
        )
        projected_cursor = await conn.execute(
            "select status from runs where tenant_id = 'tenant-a' and id = 'run-a'"
        )
        assert (await projected_cursor.fetchone())["status"] == "failed"
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                """
                update run_attempts
                set error_code = 'late_rewrite'
                where tenant_id = 'tenant-a' and id = 'attempt-a'
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
