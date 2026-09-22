import asyncio
from contextlib import asynccontextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import schema_migrations
from tests.support.db_transactions import event_loop_policy as event_loop_policy


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_CHECKSUM = (
    "14941c07a273f8924fb289876ac887879f8a8d5cc2a5a8d95bb9252e1ea40d90"
)
REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_COMMIT = (
    "33f3ab0163cd05c412e2a3d25d5859a935a359a6"
)
REPOSITORY_SKILL_RETIREMENT_BASE_COMMIT = (
    "e198bf7ca3b94b10ffaa90a4a615529ceab4b612"
)
REPOSITORY_SKILL_RETIREMENT_BASE_CHECKSUM = (
    "a8aeca36bdc095c451f00ac9dc358c90528df2f837b5888b9af9249c6ef67019"
)


def _schema_source_at_commit(commit: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        ["git", "show", f"{commit}:app/schema.sql"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout


def _remote_run_attempt_reconciler_takeover_schema_sql(tmp_path: Path) -> str:
    exact_base = _load_exact_base_schema_migrations(tmp_path)
    try:
        remote_sql = exact_base.schema_sql()
        assert exact_base.schema_checksum(remote_sql) == REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_CHECKSUM
        return remote_sql
    finally:
        sys.modules.pop(exact_base.__name__, None)


def test_remote_run_attempt_reconciler_takeover_checksum_remains_pinned(tmp_path: Path) -> None:
    assert _remote_run_attempt_reconciler_takeover_schema_sql(tmp_path)


def test_repository_skill_retirement_base_checksum_remains_pinned(
    tmp_path: Path,
) -> None:
    exact_base = _load_schema_migrations_at_commit(
        tmp_path,
        REPOSITORY_SKILL_RETIREMENT_BASE_COMMIT,
    )
    try:
        assert exact_base.TARGET_SCHEMA_VERSION == "2026.09.16.1"
        assert exact_base.schema_checksum() == REPOSITORY_SKILL_RETIREMENT_BASE_CHECKSUM
    finally:
        sys.modules.pop(exact_base.__name__, None)


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


def _transaction_factory(dsn: str, schema_name: str):
    @asynccontextmanager
    async def factory():
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with conn.transaction():
                yield conn
        finally:
            await conn.close()

    return factory


def _index_connection_factory(dsn: str, schema_name: str):
    async def factory():
        return await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )

    return factory


def _load_schema_migrations_at_commit(tmp_path: Path, commit: str):
    root = Path(__file__).resolve().parents[1]
    module_source = subprocess.run(
        [
            "git",
            "show",
            f"{commit}:app/schema_migrations.py",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    schema_source = _schema_source_at_commit(commit)
    module_path = tmp_path / f"schema_migrations_{commit[:12]}.py"
    schema_path = tmp_path / f"schema_{commit[:12]}.sql"
    module_path.write_text(module_source, encoding="utf-8")
    schema_path.write_text(schema_source, encoding="utf-8")
    module_name = f"exact_base_schema_migrations_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    module.SCHEMA_PATH = schema_path
    return module


def _load_exact_base_schema_migrations(tmp_path: Path):
    return _load_schema_migrations_at_commit(
        tmp_path,
        REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_COMMIT,
    )


@pytest.mark.asyncio
async def test_real_postgres_concurrent_migrations_use_one_global_lock_and_ledger_row():
    dsn = _postgres_dsn()
    schema_name = f"schema_migration_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)

        first, second = await asyncio.gather(
            schema_migrations.apply_migrations(
                transaction_factory=factory,
                index_connection_factory=index_factory,
            ),
            schema_migrations.apply_migrations(
                transaction_factory=factory,
                index_connection_factory=index_factory,
            ),
        )

        assert {first["status"], second["status"]} == {"applied", "current"}
        cursor = await admin.execute(
            sql.SQL(
                "select version, checksum_sha256 from {}.schema_migrations"
            ).format(sql.Identifier(schema_name))
        )
        assert await cursor.fetchall() == [
            {
                "version": schema_migrations.TARGET_SCHEMA_VERSION,
                "checksum_sha256": schema_migrations.schema_checksum(),
            }
        ]
        cursor = await admin.execute(
            """
            select pg_get_constraintdef(oid, true) as definition
            from pg_constraint
            where conrelid = to_regclass(%s)
              and conname = 'chk_run_attempts_terminal_time'
            """,
            (f"{schema_name}.run_attempts",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert "status <> ALL" in row["definition"]
        assert "NOT (status = ANY" not in row["definition"]
        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)
            definition_mismatches = []
            if not status["constraint_definitions_current"]:
                for relation_name, constraint_name, constraint_type, definition in (
                    schema_migrations.MODEL_CRITICAL_CONSTRAINT_DEFINITIONS
                    + schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS
                ):
                    cursor = await conn.execute(
                        """
                        select constraints.contype::text as constraint_type,
                               pg_get_constraintdef(constraints.oid, true) as definition
                        from pg_constraint constraints
                        where constraints.conrelid = to_regclass(%s)
                          and constraints.conname = %s
                        """,
                        (relation_name, constraint_name),
                    )
                    row = await cursor.fetchone()
                    if (
                        row is None
                        or row["constraint_type"] != constraint_type
                        or "".join(str(row["definition"]).lower().split())
                        != "".join(definition.lower().split())
                    ):
                        definition_mismatches.append(
                            {
                                "name": constraint_name,
                                "actual": None if row is None else row["definition"],
                            }
                        )
            assert status["ready"] is True, "\n".join(
                f"{item['name']}: {item['actual']}"
                for item in definition_mismatches
            )
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_upgrade_installs_run_attempt_heartbeat_monotonicity_guard(tmp_path: Path):
    dsn = _postgres_dsn()
    schema_name = f"schema_attempt_heartbeat_upgrade_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(_remote_run_attempt_reconciler_takeover_schema_sql(tmp_path))
        await admin.execute(
            """
            insert into schema_migrations(version, checksum_sha256)
            values (%s, %s)
            """,
            (
                schema_migrations.RUN_ATTEMPT_RECONCILER_TAKEOVER_SCHEMA_VERSION,
                REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_CHECKSUM,
            ),
        )

        factory = _transaction_factory(dsn, schema_name)
        result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=_index_connection_factory(dsn, schema_name),
        )

        assert result["status"] == "applied"
        ledger_rows = await (
            await admin.execute(
                "select version, checksum_sha256 from schema_migrations order by version"
            )
        ).fetchall()
        assert ledger_rows == [
            {
                "version": schema_migrations.RUN_ATTEMPT_RECONCILER_TAKEOVER_SCHEMA_VERSION,
                "checksum_sha256": REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_CHECKSUM,
            },
            {
                "version": schema_migrations.TARGET_SCHEMA_VERSION,
                "checksum_sha256": schema_migrations.schema_checksum(),
            },
        ]
        trigger_definition = await (
            await admin.execute(
                """
                select pg_get_functiondef(
                  to_regprocedure(%s)
                ) as definition
                """,
                (
                    f"{schema_name}.ai_platform_guard_run_attempt_heartbeat_monotonicity()",
                ),
            )
        ).fetchone()
        assert trigger_definition is not None
        assert "run_attempt_heartbeat_regression" in trigger_definition["definition"]
        assert "run_attempt_lease_expiry_regression" in trigger_definition["definition"]
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
    finally:
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_cutover_rejects_an_older_binary_after_migration(
    tmp_path: Path,
):
    dsn = _postgres_dsn()
    schema_name = f"schema_exact_base_compatibility_{uuid.uuid4().hex}"
    exact_base = _load_exact_base_schema_migrations(tmp_path)
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        base_result = await exact_base.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        assert base_result["version"] == exact_base.TARGET_SCHEMA_VERSION
        async with factory() as conn:
            assert (await exact_base.schema_status(conn))["ready"] is True

        candidate_result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        assert candidate_result["version"] == schema_migrations.TARGET_SCHEMA_VERSION
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
            exact_base_status = await exact_base.schema_status(conn)
        assert exact_base_status["ready"] is False
        assert exact_base_status["index_ledger_current"] is False
        ledger_versions = await (
            await admin.execute(
                sql.SQL(
                    "select distinct target_version from {}.schema_index_migrations"
                ).format(sql.Identifier(schema_name))
            )
        ).fetchall()
        assert ledger_versions == [
            {
                "target_version": schema_migrations.CONCURRENT_INDEX_LEDGER_SCHEMA_VERSION,
            }
        ]
    finally:
        sys.modules.pop(exact_base.__name__, None)
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_repository_skill_retirement_upgrades_exact_main_and_preserves_history(
    tmp_path: Path,
):
    dsn = _postgres_dsn()
    schema_name = f"schema_repository_skill_retirement_{uuid.uuid4().hex}"
    exact_base = _load_schema_migrations_at_commit(
        tmp_path,
        REPOSITORY_SKILL_RETIREMENT_BASE_COMMIT,
    )
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    uploaded_version = "a" * 64
    try:
        assert exact_base.TARGET_SCHEMA_VERSION == "2026.09.16.1"
        assert exact_base.schema_checksum() == REPOSITORY_SKILL_RETIREMENT_BASE_CHECKSUM
        await admin.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema_name))
        )
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        base_result = await exact_base.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        assert base_result["version"] == exact_base.TARGET_SCHEMA_VERSION

        await admin.execute(
            """
            insert into skill_versions(
              id, skill_id, version, content_hash, description, source_json,
              dependency_ids, status, created_by
            ) values (
              'skv_uploaded_qa', 'qa-file-reviewer', %s, %s, 'Uploaded package',
              '{"kind":"uploaded","files":[{"relative_path":"SKILL.md"}]}'::jsonb,
              '[]'::jsonb, 'released', 'migration-test'
            )
            """,
            (uploaded_version, uploaded_version),
        )
        await admin.execute(
            """
            update skills
            set version = %s, description = 'Uploaded catalog', status = 'active'
            where id = 'qa-file-reviewer'
            """,
            (uploaded_version,),
        )
        await admin.execute(
            """
            insert into tenants(id, name) values
              ('other', 'Previous-only Tenant'),
              ('mixed', 'Mixed Rollout Tenant')
            """
        )
        await admin.execute(
            """
            insert into skill_release_policies(
              id, tenant_id, skill_id, channel, current_version, previous_version,
              rollout_percent, status, promoted_by
            ) values
              (
                'skr_uploaded_qa', 'default', 'qa-file-reviewer', 'stable', %s,
                null, 50, 'active', 'migration-test'
              ),
              (
                'skr_repository_qa', 'other', 'qa-file-reviewer', 'stable', '0.1.0',
                %s, 0, 'active', 'migration-test'
              ),
              (
                'skr_mixed_qa', 'mixed', 'qa-file-reviewer', 'stable', %s,
                '0.1.0', 50, 'active', 'migration-test'
              )
            """,
            (uploaded_version, uploaded_version, uploaded_version),
        )
        await admin.execute(
            """
            update tenant_workbench_skills
            set status = 'active', visible_to_user = true
            where tenant_id = 'default' and skill_id = 'qa-file-reviewer'
            """
        )
        await admin.execute(
            """
            insert into tenant_workbench_skills(
              tenant_id, skill_id, status, visible_to_user
            ) values
              ('other', 'qa-file-reviewer', 'active', true),
              ('mixed', 'qa-file-reviewer', 'active', true)
            """
        )
        await admin.execute(
            """
            insert into tenant_capability_distributions(
              id, tenant_id, capability_kind, capability_id, status, visible_to_user
            ) values
              (
                'tcd_uploaded_qa', 'default', 'skill',
                'qa-file-reviewer', 'active', true
              ),
              (
                'tcd_repository_qa', 'other', 'skill',
                'qa-file-reviewer', 'active', true
              ),
              (
                'tcd_mixed_qa', 'mixed', 'skill',
                'qa-file-reviewer', 'active', true
              )
            """
        )
        await admin.execute(
            """
            insert into agents(id, tenant_id, name, agent_type, status)
            values
              ('custom-repository-profile', 'other', 'Repository profile', 'chat', 'active'),
              ('custom-mixed-profile', 'mixed', 'Mixed profile', 'chat', 'active'),
              ('custom-uploaded-profile', 'default', 'Uploaded profile', 'chat', 'active')
            """
        )
        await admin.execute(
            """
            insert into agent_profile_revisions(
              tenant_id, agent_id, revision, revision_status, name, instructions,
              skill_set, content_hash, avatar_ref, avatar_seed, visibility,
              allowed_department_ids, allowed_roles, allowed_user_ids
            ) values
              (
                'other', 'custom-repository-profile', 1, 'published',
                'Repository profile', 'Use the tenant release.',
                '[{"skill_id":"qa-file-reviewer"}]'::jsonb,
                'repository-profile-hash', 'builtin:agent', 'repository-profile',
                'tenant', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb
              ),
              (
                'mixed', 'custom-mixed-profile', 1, 'published',
                'Mixed profile', 'Use the tenant release.',
                '[{"skill_id":"qa-file-reviewer"}]'::jsonb,
                'mixed-profile-hash', 'builtin:agent', 'mixed-profile',
                'tenant', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb
              ),
              (
                'default', 'custom-uploaded-profile', 1, 'published',
                'Uploaded profile', 'Use the tenant release.',
                '[{"skill_id":"qa-file-reviewer"}]'::jsonb,
                'uploaded-profile-hash', 'builtin:agent', 'uploaded-profile',
                'tenant', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb
              )
            """
        )
        await admin.execute(
            """
            insert into agent_profiles(
              tenant_id, agent_id, lifecycle_status, latest_revision,
              published_revision, published_hash
            ) values
              (
                'other', 'custom-repository-profile', 'published', 1, 1,
                'repository-profile-hash'
              ),
              (
                'mixed', 'custom-mixed-profile', 'published', 1, 1,
                'mixed-profile-hash'
              ),
              (
                'default', 'custom-uploaded-profile', 'published', 1, 1,
                'uploaded-profile-hash'
              )
            """
        )
        await admin.execute(
            """
            insert into users(id, tenant_id, display_name)
            values ('retirement-history-user', 'default', 'History User')
            """
        )
        await admin.execute(
            """
            insert into sessions(
              id, tenant_id, workspace_id, user_id, agent_id, title, status
            ) values (
              'retirement-history-session', 'default', 'default',
              'retirement-history-user', 'qa-word-review', 'History', 'archived'
            )
            """
        )
        await admin.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id,
              skill_id, status, result_json
            ) values (
              'retirement-history-run', 'default', 'default',
              'retirement-history-session', 'retirement-history-user',
              'qa-word-review', 'qa-file-reviewer', 'succeeded',
              '{"preserved":"run"}'::jsonb
            )
            """
        )
        await admin.execute(
            """
            insert into run_skill_snapshots(
              id, tenant_id, run_id, skill_id, skill_version, content_hash,
              source_json, allowed, staged, used
            ) values (
              'retirement-history-snapshot', 'default', 'retirement-history-run',
              'qa-file-reviewer', '0.1.0', '0.1.0',
              '{"kind":"schema-seed","preserved":"snapshot"}'::jsonb,
              true, true, true
            )
            """
        )
        await admin.execute(
            """
            insert into run_skill_materializations(
              tenant_id, run_id, skill_id, materialization_sha256, manifest_json
            ) values (
              'default', 'retirement-history-run', 'qa-file-reviewer', %s,
              '{"preserved":"materialization"}'::jsonb
            )
            """,
            ("b" * 64,),
        )
        await admin.execute(
            """
            insert into run_context_snapshots(
              id, tenant_id, workspace_id, user_id, session_id, run_id, payload_json
            ) values (
              'retirement-history-context', 'default', 'default',
              'retirement-history-user', 'retirement-history-session',
              'retirement-history-run', '{"preserved":"context"}'::jsonb
            )
            """
        )
        await admin.execute(
            """
            insert into audit_logs(
              id, tenant_id, action, target_type, target_id, payload_json
            ) values (
              'retirement-history-audit', 'default', 'historical_action',
              'skill', 'qa-file-reviewer', '{"preserved":"audit"}'::jsonb
            )
            """
        )

        result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )

        assert result["status"] == "applied"
        assert result["version"] == schema_migrations.TARGET_SCHEMA_VERSION
        async with factory() as conn:
            assert (await exact_base.schema_status(conn))["ready"] is True
        ledger_rows = await (
            await admin.execute(
                "select version, checksum_sha256 from schema_migrations order by version"
            )
        ).fetchall()
        assert ledger_rows == [
            {
                "version": exact_base.TARGET_SCHEMA_VERSION,
                "checksum_sha256": REPOSITORY_SKILL_RETIREMENT_BASE_CHECKSUM,
            },
            {
                "version": schema_migrations.TARGET_SCHEMA_VERSION,
                "checksum_sha256": schema_migrations.schema_checksum(),
            },
        ]
        assert await (
            await admin.execute(
                """
                select version, description, status
                from skills where id = 'qa-file-reviewer'
                """
            )
        ).fetchone() == {
            "version": uploaded_version,
            "description": "Uploaded catalog",
            "status": "active",
        }
        version_rows = await (
            await admin.execute(
                """
                select version, source_json->>'kind' as source_kind, status
                from skill_versions
                where skill_id = 'qa-file-reviewer'
                order by version
                """
            )
        ).fetchall()
        assert version_rows == [
            {"version": "0.1.0", "source_kind": "schema-seed", "status": "inactive"},
            {"version": uploaded_version, "source_kind": "uploaded", "status": "released"},
        ]
        release_policy_rows = await (
            await admin.execute(
                """
                select tenant_id, current_version, previous_version,
                       rollout_percent, status
                from skill_release_policies
                where skill_id = 'qa-file-reviewer' and channel = 'stable'
                order by tenant_id
                """
            )
        ).fetchall()
        assert release_policy_rows == [
            {
                "tenant_id": "default",
                "current_version": uploaded_version,
                "previous_version": None,
                "rollout_percent": 50,
                "status": "active",
            },
            {
                "tenant_id": "mixed",
                "current_version": uploaded_version,
                "previous_version": "0.1.0",
                "rollout_percent": 50,
                "status": "disabled",
            },
            {
                "tenant_id": "other",
                "current_version": "0.1.0",
                "previous_version": uploaded_version,
                "rollout_percent": 0,
                "status": "disabled",
            },
        ]
        workbench_rows = await (
            await admin.execute(
                """
                select tenant_id, status, visible_to_user
                from tenant_workbench_skills
                where skill_id = 'qa-file-reviewer'
                  and tenant_id in ('default', 'mixed', 'other')
                order by tenant_id
                """
            )
        ).fetchall()
        assert workbench_rows == [
            {"tenant_id": "default", "status": "active", "visible_to_user": True},
            {"tenant_id": "mixed", "status": "disabled", "visible_to_user": False},
            {"tenant_id": "other", "status": "disabled", "visible_to_user": False},
        ]
        distribution_rows = await (
            await admin.execute(
                """
                select tenant_id, status, visible_to_user
                from tenant_capability_distributions
                where capability_kind = 'skill'
                  and capability_id = 'qa-file-reviewer'
                  and tenant_id in ('default', 'mixed', 'other')
                order by tenant_id
                """
            )
        ).fetchall()
        assert distribution_rows == [
            {"tenant_id": "default", "status": "active", "visible_to_user": True},
            {"tenant_id": "mixed", "status": "disabled", "visible_to_user": False},
            {"tenant_id": "other", "status": "disabled", "visible_to_user": False},
        ]
        agent_rows = await (
            await admin.execute(
                """
                select id, status from agents
                where id in (
                  'custom-mixed-profile',
                  'custom-repository-profile',
                  'custom-uploaded-profile'
                )
                order by id
                """
            )
        ).fetchall()
        assert agent_rows == [
            {"id": "custom-mixed-profile", "status": "inactive"},
            {"id": "custom-repository-profile", "status": "inactive"},
            {"id": "custom-uploaded-profile", "status": "active"},
        ]
        assert await (
            await admin.execute(
                """
                select
                  runs.result_json,
                  snapshots.source_json,
                  materializations.manifest_json,
                  contexts.payload_json,
                  audits.payload_json as audit_payload_json
                from runs
                join run_skill_snapshots snapshots
                  on snapshots.tenant_id = runs.tenant_id
                 and snapshots.run_id = runs.id
                join run_skill_materializations materializations
                  on materializations.tenant_id = runs.tenant_id
                 and materializations.run_id = runs.id
                 and materializations.skill_id = snapshots.skill_id
                join run_context_snapshots contexts
                  on contexts.tenant_id = runs.tenant_id
                 and contexts.run_id = runs.id
                join audit_logs audits
                  on audits.tenant_id = runs.tenant_id
                 and audits.id = 'retirement-history-audit'
                where runs.id = 'retirement-history-run'
                """
            )
        ).fetchone() == {
            "result_json": {"preserved": "run"},
            "source_json": {"kind": "schema-seed", "preserved": "snapshot"},
            "manifest_json": {"preserved": "materialization"},
            "payload_json": {"preserved": "context"},
            "audit_payload_json": {"preserved": "audit"},
        }
    finally:
        sys.modules.pop(exact_base.__name__, None)
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_upgrade_preserves_legacy_artifact_outbox_identity():
    dsn = _postgres_dsn()
    schema_name = f"schema_file_outbox_upgrade_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await admin.execute(
            "insert into users(id, tenant_id, display_name) values ('legacy-user', 'default', 'Legacy')"
        )
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('legacy-agent', 'default', 'Legacy', 'chat')"
        )
        await admin.execute(
            "insert into skills(id, name, version, executor_type) values ('legacy-skill', 'Legacy', '1', 'fake')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
            values ('legacy-session', 'default', 'default', 'legacy-user', 'legacy-agent', 'Legacy', 'archived')
            """
        )
        await admin.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status
            ) values (
              'legacy-run', 'default', 'default', 'legacy-session', 'legacy-user',
              'legacy-agent', 'legacy-skill', 'succeeded'
            )
            """
        )
        await admin.execute(
            """
            insert into files(
              id, tenant_id, workspace_id, user_id, original_name, content_type,
              size_bytes, storage_key, sha256
            ) values (
              'legacy-file', 'default', 'default', 'legacy-user', 'legacy.txt',
              'text/plain', 1, 'legacy/file', 'a'
            )
            """
        )
        await admin.execute(
            "alter table artifacts drop constraint chk_artifacts_run_owner"
        )
        await admin.execute("alter table artifacts alter column run_id set not null")
        await admin.execute(
            """
            insert into artifacts(
              id, tenant_id, run_id, artifact_type, label, content_type,
              storage_key, size_bytes, lifecycle_state, delete_requested_at
            ) values (
              'legacy-artifact', 'default', 'legacy-run', 'text', 'Legacy',
              'text/plain', 'legacy/artifact', 1, 'delete_pending', now()
            )
            """
        )
        await admin.execute(
            """
            insert into object_deletion_outbox(
              id, tenant_id, target_type, artifact_id, file_id, storage_key, state
            ) values (
              'legacy-outbox', 'default', 'artifact', 'legacy-artifact', null,
              'legacy/artifact', 'pending'
            )
            """
        )
        await admin.execute("drop index if exists uq_object_deletion_outbox_file")
        await admin.execute(
            "alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_target"
        )
        await admin.execute(
            "alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_target_state"
        )
        await admin.execute(
            "alter table object_deletion_outbox drop constraint object_deletion_outbox_file_id_fkey"
        )
        await admin.execute(
            "alter table object_deletion_outbox drop column file_id, drop column target_type, drop column lease_generation"
        )
        await admin.execute("alter table object_deletion_outbox alter column artifact_id set not null")
        await admin.execute("alter table files drop constraint chk_files_lifecycle_state")
        await admin.execute(
            "alter table files drop column lifecycle_state, drop column delete_requested_at, drop column deleted_at"
        )
        await admin.execute(
            """
            create index idx_object_deletion_outbox_claim
            on object_deletion_outbox(state, available_at asc, created_at asc, id asc)
            where state in ('pending', 'processing', 'failed')
            """
        )
        await admin.execute(
            """
            insert into schema_migrations(version, checksum_sha256)
            values ('2026.08.12.1', repeat('1', 64))
            on conflict (version) do nothing
            """
        )

        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )

        assert result["status"] == "applied"
        cursor = await admin.execute(
            """
            select outbox.target_type, outbox.artifact_id, outbox.file_id,
                   outbox.lease_generation, files.lifecycle_state,
                   artifacts.run_id as artifact_run_id,
                   artifacts.lifecycle_state as artifact_lifecycle_state,
                   artifacts.manifest_json ->> 'retention_artifact_cleanup'
                     as retention_artifact_cleanup,
                   artifacts.manifest_json ->> 'deletion_owner_run_id'
                     as deletion_owner_run_id
            from object_deletion_outbox outbox
            cross join files
            cross join artifacts
            where outbox.id = 'legacy-outbox' and files.id = 'legacy-file'
              and artifacts.id = 'legacy-artifact'
            """
        )
        assert await cursor.fetchone() == {
            "target_type": "artifact",
            "artifact_id": "legacy-artifact",
            "file_id": None,
            "lease_generation": 0,
            "lifecycle_state": "active",
            "artifact_run_id": None,
            "artifact_lifecycle_state": "delete_pending",
            "retention_artifact_cleanup": "true",
            "deletion_owner_run_id": "legacy-run",
        }
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_upgrade_namespaces_every_legacy_file_outbox_state():
    dsn = _postgres_dsn()
    schema_name = f"schema_file_state_upgrade_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await admin.execute(
            "insert into users(id, tenant_id, display_name) values ('state-user', 'default', 'State')"
        )
        await admin.execute(
            "alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_target_state"
        )
        await admin.execute(
            "alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_state"
        )
        await admin.execute(
            """
            alter table object_deletion_outbox add constraint chk_object_deletion_outbox_state
              check (state in ('pending', 'processing', 'failed', 'dead_letter', 'deleted'))
            """
        )
        await admin.execute(
            """
            insert into files(
              id, tenant_id, workspace_id, user_id, original_name, content_type,
              size_bytes, storage_key, sha256, lifecycle_state, delete_requested_at, deleted_at
            ) values
              ('state-pending', 'default', 'default', 'state-user', 'p', 'text/plain', 1, 'state/p', 'p', 'delete_pending', now(), null),
              ('state-processing', 'default', 'default', 'state-user', 'q', 'text/plain', 1, 'state/q', 'q', 'delete_pending', now(), null),
              ('state-failed', 'default', 'default', 'state-user', 'f', 'text/plain', 1, 'state/f', 'f', 'delete_pending', now(), null),
              ('state-dead-letter', 'default', 'default', 'state-user', 'd', 'text/plain', 1, 'state/d', 'd', 'delete_pending', now(), null),
              ('state-deleted', 'default', 'default', 'state-user', 'x', 'text/plain', 1, 'state/x', 'x', 'deleted', now(), now())
            """
        )
        await admin.execute(
            """
            insert into object_deletion_outbox(
              id, tenant_id, target_type, artifact_id, file_id, storage_key, state
            ) values
              ('out-state-pending', 'default', 'file', null, 'state-pending', 'state/p', 'pending'),
              ('out-state-processing', 'default', 'file', null, 'state-processing', 'state/q', 'processing'),
              ('out-state-failed', 'default', 'file', null, 'state-failed', 'state/f', 'failed'),
              ('out-state-dead-letter', 'default', 'file', null, 'state-dead-letter', 'state/d', 'dead_letter'),
              ('out-state-deleted', 'default', 'file', null, 'state-deleted', 'state/x', 'deleted')
            """
        )
        await admin.execute(
            """
            insert into schema_migrations(version, checksum_sha256)
            values ('2026.08.12.2', repeat('2', 64))
            """
        )

        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )

        assert result["status"] == "applied"
        cursor = await admin.execute("select id, state from object_deletion_outbox order by id")
        assert {row["id"]: row["state"] for row in await cursor.fetchall()} == {
            "out-state-dead-letter": "file_dead_letter",
            "out-state-deleted": "file_deleted",
            "out-state-failed": "file_failed",
            "out-state-pending": "file_pending",
            "out-state-processing": "file_processing",
        }
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage_sql",
    [
        "alter table runs drop column authz_policy_version",
        "alter table files drop constraint chk_files_lifecycle_state",
        "alter table artifacts drop constraint chk_artifacts_lifecycle_state",
        "alter table artifacts drop constraint chk_artifacts_run_owner",
        "alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_target",
        "alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_target_state",
        "alter table object_deletion_outbox drop column lease_generation",
        "alter table run_attempts drop column lease_expires_at",
        "alter table run_attempts drop constraint run_attempts_tenant_id_run_id_ordinal_key",
        "alter table run_attempts drop constraint run_attempts_tenant_id_run_id_queue_attempt_id_key",
        "drop index idx_messages_tenant_session_created",
        "drop index idx_runs_input_json_gin",
        "drop index idx_object_deletion_outbox_artifact_storage_live",
        "drop index uq_object_deletion_outbox_file",
        "drop trigger trg_run_attempt_transition_guard on run_attempts",
        "drop trigger trg_run_attempt_heartbeat_monotonicity_guard on run_attempts",
        """
        drop trigger trg_run_attempt_transition_guard on run_attempts;
        create trigger trg_run_attempt_transition_guard
          before update on run_attempts
          for each row execute function ai_platform_guard_run_attempt_transition()
        """,
    ],
)
async def test_real_postgres_readiness_rejects_missing_critical_contract(damage_sql):
    dsn = _postgres_dsn()
    schema_name = f"schema_contract_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(damage_sql)

        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)

        assert status["ready"] is False
        assert status["contracts_current"] is False
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage_sql",
    [
        """
        alter table files drop constraint chk_files_lifecycle_state;
        alter table files add constraint chk_files_lifecycle_state
          check (lifecycle_state in ('active', 'deleted'))
        """,
        """
        alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_target;
        alter table object_deletion_outbox add constraint chk_object_deletion_outbox_target
          check (artifact_id is not null or file_id is not null)
        """,
        """
        alter table object_deletion_outbox drop constraint chk_object_deletion_outbox_target_state;
        alter table object_deletion_outbox add constraint chk_object_deletion_outbox_target_state
          check (target_type = 'artifact' or state = 'file_pending')
        """,
        """
        alter table object_deletion_outbox
          drop constraint object_deletion_outbox_file_id_fkey;
        alter table object_deletion_outbox
          add constraint object_deletion_outbox_file_id_fkey
          foreign key (file_id) references artifacts(id)
        """,
        """
        alter table run_attempts drop constraint chk_run_attempts_ordinal;
        alter table run_attempts add constraint chk_run_attempts_ordinal
          check (ordinal >= 0)
        """,
        """
        alter table run_attempts drop constraint chk_run_attempts_owner_generation;
        alter table run_attempts add constraint chk_run_attempts_owner_generation
          check (owner_generation >= 0)
        """,
        """
        alter table run_attempts drop constraint fk_run_attempts_run;
        alter table run_attempts add constraint fk_run_attempts_run
          foreign key (run_id) references runs(id)
        """,
        """
        alter table run_attempts drop constraint chk_run_attempts_spec_sha256;
        alter table run_attempts add constraint chk_run_attempts_spec_sha256
          check (execution_spec_sha256 ~ '^[0-9a-f]{64}$')
        """,
    ],
)
async def test_real_postgres_readiness_rejects_wrong_critical_constraint_definition(damage_sql):
    dsn = _postgres_dsn()
    schema_name = f"schema_constraint_definition_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(damage_sql)

        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)

        assert status["ready"] is False
        assert status["constraints_current"] is True
        assert status["constraint_definitions_current"] is False
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_readiness_rejects_index_ledger_checksum_drift():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_ledger_drift_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(
            """
            update schema_index_migrations
            set checksum_sha256 = repeat('0', 64)
            where index_name = 'idx_messages_tenant_session_created'
            """
        )

        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)

        assert status["ready"] is False
        assert status["index_ledger_current"] is False
        assert status["concurrent_index_definitions_current"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_readiness_rejects_and_migration_removes_orphan_index_ledger_row():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_ledger_orphan_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(
            """
            insert into schema_index_migrations(
              index_name, target_version, checksum_sha256, state, attempts
            ) values ('idx_orphan_interrupted', 'old-version', repeat('0', 64), 'building', 1)
            """
        )

        async with factory() as conn:
            damaged = await schema_migrations.schema_status(conn)
        assert damaged["ready"] is False
        assert damaged["index_ledger_current"] is False

        repaired = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        assert repaired["status"] == "applied"
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
        cursor = await admin.execute(
            "select count(*) as count from schema_index_migrations where index_name = 'idx_orphan_interrupted'"
        )
        assert (await cursor.fetchone())["count"] == 0
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("index_name", "replacement_sql"),
    [
        (
            "idx_messages_tenant_session_created",
            "create index idx_messages_tenant_session_created on messages(id)",
        ),
        (
            "idx_runs_input_json_gin",
            "create index idx_runs_input_json_gin on runs(input_json)",
        ),
        (
            "idx_runs_input_json_gin",
            "create index idx_runs_input_json_gin on runs using gin (input_json)",
        ),
        (
            "idx_object_deletion_outbox_claim",
            "create index idx_object_deletion_outbox_claim "
            "on object_deletion_outbox(state, available_at, created_at, id) "
            "where (state = 'pending' or state = 'processing' or state = 'failed' "
            "or state = 'file_pending' or state = 'file_processing' or state = 'file_failed') "
            "and tenant_id = 'default'",
        ),
    ],
)
async def test_real_postgres_readiness_rejects_and_migration_repairs_wrong_index_definition(
    index_name,
    replacement_sql,
):
    dsn = _postgres_dsn()
    schema_name = f"schema_index_definition_drift_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("drop index {}").format(sql.Identifier(index_name)))
        await admin.execute(replacement_sql)

        async with factory() as conn:
            damaged = await schema_migrations.schema_status(conn)

        assert damaged["ready"] is False
        assert damaged["indexes_current"] is True
        assert damaged["concurrent_index_definitions_current"] is False

        repaired = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        assert repaired["status"] == "applied"
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("index_name", "replacement_sql"),
    [
        (
            "uq_run_attempts_one_open",
            "create unique index uq_run_attempts_one_open on run_attempts(id) "
            "where status = 'running'",
        ),
        (
            "idx_run_attempts_lease_reconcile",
            "create index idx_run_attempts_lease_reconcile on run_attempts(id)",
        ),
    ],
)
async def test_real_postgres_readiness_rejects_wrong_static_index_definition(
    index_name,
    replacement_sql,
):
    dsn = _postgres_dsn()
    schema_name = f"schema_static_index_drift_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(sql.SQL("drop index {}").format(sql.Identifier(index_name)))
        await admin.execute(replacement_sql)

        async with factory() as conn:
            damaged = await schema_migrations.schema_status(conn)

        assert damaged["ready"] is False
        assert damaged["indexes_current"] is True
        assert damaged["static_index_definitions_current"] is False
    finally:
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_concurrent_index_phase_recovers_after_ledger_interruption():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_resume_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(
            """
            update schema_index_migrations
            set state = 'building', completed_at = null
            where index_name = 'idx_messages_tenant_session_created'
            """
        )

        result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )

        assert result["status"] == "applied"
        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)
        assert status["ready"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_concurrent_index_build_does_not_block_message_writes():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_writer_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    blocker = None
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute("insert into users(id, tenant_id, display_name) values ('writer-user', 'default', 'Writer')")
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('writer-agent', 'default', 'Writer', 'chat')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
            values ('writer-session', 'default', 'default', 'writer-user', 'writer-agent', 'Writer', 'active')
            """
        )
        await admin.execute("drop index idx_messages_tenant_session_created")
        await admin.execute(
            """
            update schema_index_migrations
            set state = 'building', completed_at = null
            where index_name = 'idx_messages_tenant_session_created'
            """
        )

        blocker = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        blocker_tx = blocker.transaction()
        await blocker_tx.__aenter__()
        await blocker.execute(
            """
            insert into messages(id, tenant_id, session_id, role, content)
            values ('message-blocker', 'default', 'writer-session', 'user', 'blocker')
            """
        )
        migration_task = asyncio.create_task(
            schema_migrations.apply_migrations(
                transaction_factory=factory,
                index_connection_factory=index_factory,
            )
        )
        await asyncio.sleep(0.1)
        assert not migration_task.done()

        writer = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with writer.transaction():
                await asyncio.wait_for(
                    writer.execute(
                        """
                        insert into messages(id, tenant_id, session_id, role, content)
                        values ('message-writer', 'default', 'writer-session', 'user', 'writer')
                        """
                    ),
                    timeout=1,
                )
        finally:
            await writer.close()

        await blocker_tx.__aexit__(None, None, None)
        await blocker.close()
        blocker = None
        await asyncio.wait_for(migration_task, timeout=5)
        cursor = await admin.execute("select count(*) as count from messages")
        assert (await cursor.fetchone())["count"] == 2
    finally:
        if blocker is not None:
            await blocker.close()
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
