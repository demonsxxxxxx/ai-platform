import asyncio
from contextlib import asynccontextmanager
import hashlib
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


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
# Exact 2026.08.27.1 ledger checksum at the remote PR predecessor 829acfcd.
REMOTE_SUCCESSOR_ACTIVATION_CHECKSUM = (
    "d474b751d6fb6bff75cbbb8f3c482cb42f38ac462c116313baeccfc2c247fef7"
)
REMOTE_DUE_INDEX_SQL = """create index if not exists idx_run_events_v4_due_scope
  on run_events(tenant_id, run_id, sequence)
  where visible_to_user = true
    and payload_json ? '__stream_v4'
    and stream_publication_state = 'pending';


"""
REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_CHECKSUM = (
    "14941c07a273f8924fb289876ac887879f8a8d5cc2a5a8d95bb9252e1ea40d90"
)
REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_COMMIT = (
    "33f3ab0163cd05c412e2a3d25d5859a935a359a6"
)
MODEL_CONTROL_PLANE_SCHEMA_FRAGMENTS = (
    """create table if not exists model_gateway_revisions (
  revision bigint primary key,
  base_url text not null,
  api_key_ciphertext bytea not null,
  key_fingerprint text not null,
  active boolean not null default false,
  created_by text not null,
  created_at timestamptz not null default now(),
  constraint chk_model_gateway_revision_positive check (revision > 0),
  constraint chk_model_gateway_base_url check (length(base_url) between 1 and 2048),
  constraint chk_model_gateway_key_fingerprint check (key_fingerprint ~ '^[0-9a-f]{16}$')
);
create unique index if not exists uq_model_gateway_active
  on model_gateway_revisions(active) where active = true;

create table if not exists model_catalog_entries (
  model_id text primary key,
  upstream_model_id text not null unique,
  display_name text not null,
  provider text not null default 'custom',
  enabled boolean not null default false,
  upstream_available boolean not null default true,
  is_default boolean not null default false,
  display_order integer not null default 0,
  first_seen_revision bigint not null references model_gateway_revisions(revision),
  last_seen_revision bigint not null references model_gateway_revisions(revision),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  constraint chk_model_catalog_id check (model_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
  constraint chk_model_catalog_upstream_id check (
    length(upstream_model_id) between 1 and 512
    and upstream_model_id = btrim(upstream_model_id)
  ),
  constraint chk_model_catalog_display_name check (length(display_name) between 1 and 160),
  constraint chk_model_catalog_default_enabled check (not is_default or enabled)
);
create unique index if not exists uq_model_catalog_default
  on model_catalog_entries(is_default) where is_default = true;

""",
    """  model_id text,
  model_value text,
  model_gateway_revision bigint,
""",
    """alter table runs add column if not exists model_id text;
alter table runs add column if not exists model_value text;
alter table runs add column if not exists model_gateway_revision bigint;
""",
    """  if not exists (select 1 from pg_constraint where conrelid = 'runs'::regclass and conname = 'fk_runs_model_gateway_revision') then
    alter table runs add constraint fk_runs_model_gateway_revision
      foreign key (model_gateway_revision) references model_gateway_revisions(revision);
  end if;
""",
)
CURRENT_RUN_ATTEMPT_HEARTBEAT_MONOTONICITY_SQL = """create or replace function ai_platform_guard_run_attempt_heartbeat_monotonicity()
returns trigger
language plpgsql
as $$
begin
  if old.last_heartbeat_at is not null
     and (
       new.last_heartbeat_at is null
       or new.last_heartbeat_at < old.last_heartbeat_at
     ) then
    raise exception 'run_attempt_heartbeat_regression' using errcode = '23514';
  end if;
  if old.lease_expires_at is not null
     and (
       new.lease_expires_at is null
       or new.lease_expires_at < old.lease_expires_at
     ) then
    raise exception 'run_attempt_lease_expiry_regression' using errcode = '23514';
  end if;
  return new;
end $$;

drop trigger if exists trg_run_attempt_heartbeat_monotonicity_guard on run_attempts;
create trigger trg_run_attempt_heartbeat_monotonicity_guard
before update on run_attempts
for each row execute function ai_platform_guard_run_attempt_heartbeat_monotonicity();

"""
CURRENT_RUN_ATTEMPT_OWNER_GUARD_SQL = """    if new.owner_generation is not distinct from old.owner_generation
       and new.owner_kind is not distinct from old.owner_kind
       and new.owner_id is not distinct from old.owner_id then
      return new;
    end if;
    if old.status = 'cancel_requested'
       and new.owner_kind = 'reconciler'
       and new.owner_generation = old.owner_generation + 1
       and (
         new.owner_kind is distinct from old.owner_kind
         or new.owner_id is distinct from old.owner_id
       ) then
      return new;
    end if;
    raise exception 'run_attempt_owner_transition_invalid' using errcode = '23514';
"""
REMOTE_RUN_ATTEMPT_OWNER_GUARD_SQL = """    if new.owner_generation is distinct from old.owner_generation
       or new.owner_kind is distinct from old.owner_kind
       or new.owner_id is distinct from old.owner_id then
      raise exception 'run_attempt_owner_transition_invalid' using errcode = '23514';
    end if;
    return new;
"""
CONFIRMATION_HISTORY_REPAIR_SQL = """update sse_stream_authorities
set admission_confirmed_at = coalesce(
  admission_confirmed_at,
  admission_created_at,
  updated_at,
  clock_timestamp()
)
where state <> 'admission_pending'
  and admission_confirmed_at is null;

update sse_stream_authorities
set admission_confirmed_at = null
where state = 'admission_pending'
  and admission_confirmed_at is not null;

"""
EXPERT_MARKET_SCHEMA_FRAGMENTS = (
    "  market_tag text not null default '',\n",
    """create table if not exists agent_profile_favorites (
  tenant_id text not null references tenants(id),
  user_id text not null references users(id),
  agent_id text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, user_id, agent_id),
  foreign key (tenant_id, agent_id) references agents(tenant_id, id)
);

create index if not exists idx_agent_profile_favorites_user
  on agent_profile_favorites(tenant_id, user_id, created_at desc);

""",
    "alter table agent_profile_revisions add column if not exists market_tag text not null default '';\n",
)


def _remote_successor_activation_schema_sql() -> str:
    current_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    assert current_sql.count(CURRENT_RUN_ATTEMPT_HEARTBEAT_MONOTONICITY_SQL) == 1
    current_sql = current_sql.replace(
        CURRENT_RUN_ATTEMPT_HEARTBEAT_MONOTONICITY_SQL,
        "",
    )
    for fragment in MODEL_CONTROL_PLANE_SCHEMA_FRAGMENTS:
        assert current_sql.count(fragment) == 1
        current_sql = current_sql.replace(fragment, "")
    for fragment in EXPERT_MARKET_SCHEMA_FRAGMENTS:
        assert current_sql.count(fragment) == 1
        current_sql = current_sql.replace(fragment, "")
    assert current_sql.count(CURRENT_RUN_ATTEMPT_OWNER_GUARD_SQL) == 1
    current_sql = current_sql.replace(
        CURRENT_RUN_ATTEMPT_OWNER_GUARD_SQL,
        REMOTE_RUN_ATTEMPT_OWNER_GUARD_SQL,
    )
    trace_column_sql = (
        "alter table run_events add column if not exists trace_id text not null default '';"
    )
    assert current_sql.count(trace_column_sql) == 1
    assert current_sql.count(CONFIRMATION_HISTORY_REPAIR_SQL) == 1
    remote_sql = current_sql.replace(
        trace_column_sql,
        REMOTE_DUE_INDEX_SQL + trace_column_sql,
    ).replace(CONFIRMATION_HISTORY_REPAIR_SQL, "")
    remote_index_contract = "\n".join(
        f"{migration.name}:{migration.checksum_sha256}"
        for migration in schema_migrations.CONCURRENT_INDEX_MIGRATIONS
        if migration.name != "idx_run_events_v4_due_scope"
    )
    remote_checksum = hashlib.sha256(
        f"{remote_sql}\n-- concurrent-index-contract\n{remote_index_contract}".encode()
    ).hexdigest()
    assert remote_checksum == REMOTE_SUCCESSOR_ACTIVATION_CHECKSUM
    return remote_sql


def _remote_run_attempt_reconciler_takeover_schema_sql() -> str:
    current_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    assert current_sql.count(CURRENT_RUN_ATTEMPT_HEARTBEAT_MONOTONICITY_SQL) == 1
    for fragment in EXPERT_MARKET_SCHEMA_FRAGMENTS:
        assert current_sql.count(fragment) == 1
        current_sql = current_sql.replace(fragment, "")
    remote_sql = current_sql.replace(
        CURRENT_RUN_ATTEMPT_HEARTBEAT_MONOTONICITY_SQL,
        "",
    )
    assert (
        schema_migrations.schema_checksum(remote_sql)
        == REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_CHECKSUM
    )
    return remote_sql


def test_remote_successor_activation_schema_checksum_remains_pinned() -> None:
    assert _remote_successor_activation_schema_sql()


def test_remote_run_attempt_reconciler_takeover_checksum_remains_pinned() -> None:
    assert _remote_run_attempt_reconciler_takeover_schema_sql()


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


def _load_exact_base_schema_migrations(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    module_source = subprocess.run(
        [
            "git",
            "show",
            f"{REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_COMMIT}:app/schema_migrations.py",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    schema_source = subprocess.run(
        [
            "git",
            "show",
            f"{REMOTE_RUN_ATTEMPT_RECONCILER_TAKEOVER_COMMIT}:app/schema.sql",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    module_path = tmp_path / "exact_base_schema_migrations.py"
    schema_path = tmp_path / "exact_base_schema.sql"
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
                    schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS
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
async def test_real_postgres_upgrade_installs_run_attempt_heartbeat_monotonicity_guard():
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
        await admin.execute(_remote_run_attempt_reconciler_takeover_schema_sql())
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
async def test_real_postgres_candidate_preserves_exact_base_schema_readiness(
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
        assert exact_base_status["ready"] is True
        assert exact_base_status["triggers_current"] is True
        assert exact_base_status["index_ledger_current"] is True
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
async def test_real_postgres_upgrade_restores_v4_publication_schema_and_confirmation_history():
    dsn = _postgres_dsn()
    schema_name = f"schema_v4_upgrade_{uuid.uuid4().hex}"
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
        await admin.execute(_remote_successor_activation_schema_sql())
        await admin.execute(
            """
            insert into schema_migrations(version, checksum_sha256)
            values (%s, %s)
            """,
            (
                schema_migrations.V4_SUCCESSOR_ACTIVATION_SCHEMA_VERSION,
                REMOTE_SUCCESSOR_ACTIVATION_CHECKSUM,
            ),
        )
        await admin.execute(
            "insert into users(id, tenant_id, display_name) values ('v4-user', 'default', 'V4')"
        )
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('v4-agent', 'default', 'V4', 'chat')"
        )
        await admin.execute(
            "insert into skills(id, name, version, executor_type) values ('v4-skill', 'V4', '1', 'fake')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
            values ('v4-session', 'default', 'default', 'v4-user', 'v4-agent', 'V4', 'archived')
            """
        )
        await admin.execute(
            """
            insert into runs(
              id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status
            ) values (
              'v4-run', 'default', 'default', 'v4-session', 'v4-user',
              'v4-agent', 'v4-skill', 'running'
            )
            """
        )
        await admin.execute(
            """
            insert into sse_stream_authorities(
              tenant_id, run_id, attempt_id, design_id, projection_version,
              tenant_scope, stream_incarnation, state, open_event_id,
              open_payload_bytes, open_payload_digest, admission_confirmed_at
            ) values (
              'default', 'v4-run', 'v4-attempt', 'v4', 'public-stream-v4',
              'scope-v4', 1, 'confirmed', 'open-v4', '{}', repeat('a', 64), now()
            )
            """
        )
        await admin.execute(
            "alter table sse_stream_authorities drop constraint chk_sse_stream_authority_pending_confirmation"
        )
        await admin.execute(
            "update sse_stream_authorities set admission_confirmed_at = null where run_id = 'v4-run'"
        )

        factory = _transaction_factory(dsn, schema_name)
        result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=_index_connection_factory(dsn, schema_name),
        )

        assert result["status"] == "applied"
        ledger_rows = await admin.execute(
            "select version, checksum_sha256 from schema_migrations order by version"
        )
        assert await ledger_rows.fetchall() == [
            {
                "version": schema_migrations.V4_SUCCESSOR_ACTIVATION_SCHEMA_VERSION,
                "checksum_sha256": REMOTE_SUCCESSOR_ACTIVATION_CHECKSUM,
            },
            {
                "version": schema_migrations.TARGET_SCHEMA_VERSION,
                "checksum_sha256": schema_migrations.schema_checksum(),
            },
        ]
        columns = await admin.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = current_schema()
              and table_name = 'run_events'
              and column_name like 'stream_publication_%'
            order by column_name
            """
        )
        assert {row["column_name"] for row in await columns.fetchall()} == {
            "stream_publication_attempts",
            "stream_publication_claim_expires_at",
            "stream_publication_claim_token",
            "stream_publication_last_error",
            "stream_publication_next_attempt_at",
            "stream_publication_redis_id",
            "stream_publication_state",
        }
        index_row = await (
            await admin.execute(
                "select to_regclass('idx_run_events_v4_due_scope') is not null as present"
            )
        ).fetchone()
        assert index_row == {"present": True}
        authority_row = await (
            await admin.execute(
                "select admission_confirmed_at is not null as repaired from sse_stream_authorities where run_id = 'v4-run'"
            )
        ).fetchone()
        assert authority_row == {"repaired": True}
        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)
            assert status["ready"] is True, {
                key: value
                for key, value in status.items()
                if key.endswith("_current") and value is not True
            }
    finally:
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
                   outbox.lease_generation, files.lifecycle_state
            from object_deletion_outbox outbox
            cross join files
            where outbox.id = 'legacy-outbox' and files.id = 'legacy-file'
            """
        )
        assert await cursor.fetchone() == {
            "target_type": "artifact",
            "artifact_id": "legacy-artifact",
            "file_id": None,
            "lease_generation": 0,
            "lifecycle_state": "active",
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
        "alter table run_events drop constraint chk_run_events_stream_publication_claim",
        "alter table files drop constraint chk_files_lifecycle_state",
        "alter table artifacts drop constraint chk_artifacts_lifecycle_state",
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
        "drop trigger trg_agent_profile_legacy_insert_compatibility on agent_profile_revisions",
        "drop trigger trg_agent_profile_legacy_insert_reconcile on agent_profile_revisions",
        "drop trigger trg_run_attempt_transition_guard on run_attempts",
        "drop trigger trg_run_attempt_heartbeat_monotonicity_guard on run_attempts",
        """
        drop trigger trg_run_attempt_transition_guard on run_attempts;
        create trigger trg_run_attempt_transition_guard
          before update on run_attempts
          for each row execute function ai_platform_guard_run_attempt_transition()
        """,
        "alter table agent_profile_revisions enable always trigger trg_agent_profile_legacy_insert_compatibility",
        """
        create or replace function agent_profile_legacy_insert_reconcile()
        returns trigger language plpgsql as $$ begin return null; end $$
        """,
        """
        drop trigger trg_agent_profile_legacy_insert_reconcile on agent_profile_revisions;
        create trigger trg_agent_profile_legacy_insert_reconcile
          after insert on agent_profile_revisions
          for each row when (false)
          execute function agent_profile_legacy_insert_reconcile()
        """,
        "alter function agent_profile_legacy_insert_reconcile() set search_path to pg_catalog",
        "alter function agent_profile_legacy_insert_reconcile() security definer",
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
        alter table run_events drop constraint chk_run_events_stream_publication_claim;
        alter table run_events add constraint chk_run_events_stream_publication_claim
          check (stream_publication_claim_token is null)
        """,
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
