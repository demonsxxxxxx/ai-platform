"""Versioned, serialized PostgreSQL schema application and readiness checks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from app.db import SCHEMA_PATH, close_pool, connect, transaction


TARGET_SCHEMA_VERSION = "2026.08.10.1"
MIGRATION_LOCK_ID = 7_226_391_831_505_901_103
INDEX_MIGRATION_LOCK_ID = 7_226_391_831_505_901_104
CRITICAL_RELATIONS = (
    "schema_migrations",
    "schema_index_migrations",
    "runs",
    "run_events",
    "messages",
    "files",
    "artifacts",
    "object_deletion_outbox",
    "audit_logs",
)
CRITICAL_COLUMNS = (
    ("runs", "authz_policy_version", "int4", True),
    ("runs", "authority_source", "text", True),
    ("runs", "authority_checked_at", "timestamptz", False),
    ("messages", "content", "text", True),
    ("messages", "metadata_json", "jsonb", True),
    ("files", "storage_key", "text", True),
    ("files", "lifecycle_state", "text", True),
    ("files", "delete_requested_at", "timestamptz", False),
    ("files", "deleted_at", "timestamptz", False),
    ("artifacts", "lifecycle_state", "text", True),
    ("artifacts", "expires_at", "timestamptz", False),
    ("object_deletion_outbox", "state", "text", True),
    ("object_deletion_outbox", "dead_letter_at", "timestamptz", False),
    ("object_deletion_outbox", "reconcile_required", "bool", True),
    ("object_deletion_outbox", "file_id", "text", False),
    ("audit_logs", "payload_json", "jsonb", True),
)
CRITICAL_CONSTRAINTS = (
    ("runs", "fk_runs_workspace_scope"),
    ("runs", "fk_runs_session_scope"),
    ("files", "chk_files_lifecycle_state"),
    ("artifacts", "chk_artifacts_lifecycle_state"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_state"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_target"),
)


@dataclass(frozen=True)
class ConcurrentIndexMigration:
    name: str
    sql: str
    table_name: str
    column_names: tuple[str, ...]
    descending: tuple[bool, ...]
    predicate_fragments: tuple[str, ...] = ()
    unique: bool = False

    @property
    def checksum_sha256(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


CONCURRENT_INDEX_MIGRATIONS = (
    ConcurrentIndexMigration(
        "idx_messages_tenant_session_created",
        "create index concurrently if not exists idx_messages_tenant_session_created "
        "on messages(tenant_id, session_id, created_at asc, id asc)",
        "messages",
        ("tenant_id", "session_id", "created_at", "id"),
        (False, False, False, False),
    ),
    ConcurrentIndexMigration(
        "idx_files_tenant_owner_session_created",
        "create index concurrently if not exists idx_files_tenant_owner_session_created "
        "on files(tenant_id, workspace_id, user_id, session_id, created_at desc, id desc)",
        "files",
        ("tenant_id", "workspace_id", "user_id", "session_id", "created_at", "id"),
        (False, False, False, False, True, True),
    ),
    ConcurrentIndexMigration(
        "idx_artifacts_tenant_run_created",
        "create index concurrently if not exists idx_artifacts_tenant_run_created "
        "on artifacts(tenant_id, run_id, created_at desc, id desc)",
        "artifacts",
        ("tenant_id", "run_id", "created_at", "id"),
        (False, False, True, True),
    ),
    ConcurrentIndexMigration(
        "idx_artifacts_expired_cleanup",
        "create index concurrently if not exists idx_artifacts_expired_cleanup "
        "on artifacts(expires_at asc, created_at asc, id asc) "
        "where lifecycle_state = 'active' and expires_at is not null",
        "artifacts",
        ("expires_at", "created_at", "id"),
        (False, False, False),
        ("lifecycle_state = 'active'", "expires_at is not null"),
    ),
    ConcurrentIndexMigration(
        "idx_audit_logs_tenant_created",
        "create index concurrently if not exists idx_audit_logs_tenant_created "
        "on audit_logs(tenant_id, created_at desc, id desc)",
        "audit_logs",
        ("tenant_id", "created_at", "id"),
        (False, True, True),
    ),
)
CRITICAL_INDEXES = (
    *((migration.name, migration.unique) for migration in CONCURRENT_INDEX_MIGRATIONS),
    ("uq_run_events_tenant_run_sequence", True),
)


class SchemaMigrationError(RuntimeError):
    """The installed schema cannot be proven compatible with this build."""


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def schema_checksum(sql: str | None = None) -> str:
    core_sql = sql if sql is not None else schema_sql()
    index_contract = "\n".join(
        f"{migration.name}:{migration.checksum_sha256}" for migration in CONCURRENT_INDEX_MIGRATIONS
    )
    return hashlib.sha256(f"{core_sql}\n-- concurrent-index-contract\n{index_contract}".encode()).hexdigest()


async def _ensure_ledger(conn: Any) -> None:
    await conn.execute(
        """
        create table if not exists schema_migrations (
          version text primary key,
          checksum_sha256 text not null,
          applied_at timestamptz not null default now()
        )
        """
    )
    await conn.execute(
        """
        create table if not exists schema_index_migrations (
          index_name text primary key,
          target_version text not null,
          checksum_sha256 text not null,
          state text not null check (state in ('building', 'ready', 'failed')),
          attempts integer not null default 0,
          last_error_code text,
          started_at timestamptz,
          completed_at timestamptz,
          updated_at timestamptz not null default now()
        )
        """
    )


async def _default_index_connection_factory() -> Any:
    conn = await connect()
    await conn.set_autocommit(True)
    return conn


async def _acquire_coordinator_lock(conn: Any) -> None:
    """Poll a session lock without leaving a waiter transaction that blocks concurrent index DDL."""

    while True:
        cursor = await conn.execute(
            "select pg_try_advisory_lock(%s) as acquired",
            (INDEX_MIGRATION_LOCK_ID,),
        )
        row = await cursor.fetchone() or {}
        if bool(row.get("acquired")):
            return
        await asyncio.sleep(0.05)


async def _index_is_ready(conn: Any, migration: ConcurrentIndexMigration) -> bool:
    cursor = await conn.execute(
        """
        select coalesce(indexes.indisvalid and indexes.indisready, false) as ready,
               coalesce(indexes.indisunique, false) as is_unique,
               relations.relname as table_name,
               array(
                 select attributes.attname
                 from unnest(indexes.indkey::smallint[]) with ordinality keys(attnum, position)
                 join pg_attribute attributes
                   on attributes.attrelid = indexes.indrelid
                  and attributes.attnum = keys.attnum
                 where keys.position <= indexes.indnkeyatts
                 order by keys.position
               ) as column_names,
               array(
                 select (options.option_value & 1) = 1
                 from unnest(indexes.indoption::smallint[])
                   with ordinality options(option_value, position)
                 where options.position <= indexes.indnkeyatts
                 order by options.position
               ) as descending,
               pg_get_expr(indexes.indpred, indexes.indrelid) as predicate
        from pg_index indexes
        join pg_class relations on relations.oid = indexes.indrelid
        where indexes.indexrelid = to_regclass(%s)
        """,
        (migration.name,),
    )
    row = await cursor.fetchone()
    if not row or not row.get("ready") or bool(row.get("is_unique")) != migration.unique:
        return False
    if row.get("table_name") != migration.table_name:
        return False
    if tuple(row.get("column_names") or ()) != migration.column_names:
        return False
    if tuple(bool(item) for item in row.get("descending") or ()) != migration.descending:
        return False
    predicate = " ".join(
        str(row.get("predicate") or "")
        .lower()
        .replace("::text", "")
        .replace("(", " ")
        .replace(")", " ")
        .split()
    )
    return all(
        " ".join(fragment.lower().split()) in predicate
        for fragment in migration.predicate_fragments
    ) and bool(predicate) == bool(migration.predicate_fragments)


async def _apply_concurrent_indexes(conn: Any) -> bool:
    applied = False
    await _ensure_ledger(conn)
    for migration in CONCURRENT_INDEX_MIGRATIONS:
        cursor = await conn.execute(
            """
            select target_version, checksum_sha256, state
            from schema_index_migrations
            where index_name = %s
            """,
            (migration.name,),
        )
        ledger_row = await cursor.fetchone()
        index_ready = await _index_is_ready(conn, migration)
        if (
            ledger_row is not None
            and ledger_row.get("target_version") == TARGET_SCHEMA_VERSION
            and ledger_row.get("checksum_sha256") == migration.checksum_sha256
            and ledger_row.get("state") == "ready"
            and index_ready
        ):
            continue
        await conn.execute(
            """
            insert into schema_index_migrations(
              index_name, target_version, checksum_sha256, state, attempts, started_at,
              completed_at, last_error_code, updated_at
            ) values (%s, %s, %s, 'building', 1, now(), null, null, now())
            on conflict (index_name) do update set
              target_version = excluded.target_version,
              checksum_sha256 = excluded.checksum_sha256,
              state = 'building',
              attempts = schema_index_migrations.attempts + 1,
              started_at = now(),
              completed_at = null,
              last_error_code = null,
              updated_at = now()
            """,
            (migration.name, TARGET_SCHEMA_VERSION, migration.checksum_sha256),
        )
        try:
            if not index_ready:
                await conn.execute(f"drop index concurrently if exists {migration.name}")
            await conn.execute(migration.sql)
            if not await _index_is_ready(conn, migration):
                raise SchemaMigrationError("schema_index_not_valid")
        except Exception as exc:
            await conn.execute(
                """
                update schema_index_migrations
                set state = 'failed', last_error_code = %s, updated_at = now()
                where index_name = %s
                """,
                (type(exc).__name__[:120], migration.name),
            )
            raise SchemaMigrationError(f"schema_index_migration_failed:{migration.name}") from exc
        await conn.execute(
            """
            update schema_index_migrations
            set state = 'ready', completed_at = now(), last_error_code = null, updated_at = now()
            where index_name = %s and target_version = %s and checksum_sha256 = %s
            """,
            (migration.name, TARGET_SCHEMA_VERSION, migration.checksum_sha256),
        )
        applied = True
    cleanup = await conn.execute(
        """
        delete from schema_index_migrations
        where index_name not in (
          select jsonb_array_elements_text(%s::jsonb)
        )
        returning index_name
        """,
        (json.dumps([migration.name for migration in CONCURRENT_INDEX_MIGRATIONS]),),
    )
    if await cleanup.fetchone() is not None:
        applied = True
    return applied


async def apply_migrations(
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]] = transaction,
    index_connection_factory: Callable[[], Awaitable[Any]] = _default_index_connection_factory,
) -> dict[str, object]:
    """Apply additive core schema and resumable concurrent indexes."""

    sql = schema_sql()
    checksum = schema_checksum(sql)
    core_applied = False
    coordinator = await index_connection_factory()
    locked = False
    try:
        await _acquire_coordinator_lock(coordinator)
        locked = True
        async with transaction_factory() as conn:
            await conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            await _ensure_ledger(conn)
            cursor = await conn.execute(
                "select checksum_sha256 from schema_migrations where version = %s",
                (TARGET_SCHEMA_VERSION,),
            )
            row = await cursor.fetchone()
            if row is not None:
                if str(row.get("checksum_sha256") or "") != checksum:
                    raise SchemaMigrationError("schema_migration_checksum_mismatch")
            else:
                await conn.execute(sql)
                await conn.execute(
                    """
                    insert into schema_migrations(version, checksum_sha256)
                    values (%s, %s)
                    """,
                    (TARGET_SCHEMA_VERSION, checksum),
                )
                core_applied = True
        indexes_applied = await _apply_concurrent_indexes(coordinator)
    finally:
        if locked:
            await coordinator.execute("select pg_advisory_unlock(%s)", (INDEX_MIGRATION_LOCK_ID,))
        await coordinator.close()
    return {
        "status": "applied" if core_applied or indexes_applied else "current",
        "version": TARGET_SCHEMA_VERSION,
        "checksum_sha256": checksum,
    }


def _json_contract(rows: tuple[tuple[Any, ...], ...], names: tuple[str, ...]) -> str:
    return json.dumps([dict(zip(names, row, strict=True)) for row in rows], separators=(",", ":"))


async def schema_status(conn: Any) -> dict[str, object]:
    checksum = schema_checksum()
    index_ledger_contract = tuple(
        (
            migration.name,
            TARGET_SCHEMA_VERSION,
            migration.checksum_sha256,
        )
        for migration in CONCURRENT_INDEX_MIGRATIONS
    )
    relation_cursor = await conn.execute(
        """
        select coalesce(bool_and(to_regclass(relation_name) is not null), false) as current
        from jsonb_array_elements_text(%s::jsonb) relation_name
        """,
        (json.dumps(CRITICAL_RELATIONS),),
    )
    column_cursor = await conn.execute(
        """
        select coalesce(bool_and(
          attributes.attname is not null
          and types.typname = expected.type_name
          and attributes.attnotnull = expected.not_null
        ), false) as current
        from jsonb_to_recordset(%s::jsonb)
          as expected(relation_name text, column_name text, type_name text, not_null boolean)
        left join pg_attribute attributes
          on attributes.attrelid = to_regclass(expected.relation_name)
         and attributes.attname = expected.column_name
         and attributes.attnum > 0
         and not attributes.attisdropped
        left join pg_type types on types.oid = attributes.atttypid
        """,
        (_json_contract(CRITICAL_COLUMNS, ("relation_name", "column_name", "type_name", "not_null")),),
    )
    constraint_cursor = await conn.execute(
        """
        select coalesce(bool_and(constraints.oid is not null and constraints.convalidated), false) as current
        from jsonb_to_recordset(%s::jsonb) as expected(relation_name text, constraint_name text)
        left join pg_constraint constraints
          on constraints.conrelid = to_regclass(expected.relation_name)
         and constraints.conname = expected.constraint_name
        """,
        (_json_contract(CRITICAL_CONSTRAINTS, ("relation_name", "constraint_name")),),
    )
    index_cursor = await conn.execute(
        """
        select coalesce(bool_and(
          indexes.indexrelid is not null
          and indexes.indisvalid
          and indexes.indisready
          and indexes.indisunique = expected.is_unique
        ), false) as current
        from jsonb_to_recordset(%s::jsonb) as expected(index_name text, is_unique boolean)
        left join pg_index indexes on indexes.indexrelid = to_regclass(expected.index_name)
        """,
        (_json_contract(CRITICAL_INDEXES, ("index_name", "is_unique")),),
    )
    ledger_cursor = await conn.execute(
        """
        with expected_indexes as (
          select *
          from jsonb_to_recordset(%s::jsonb)
            as expected(index_name text, target_version text, checksum_sha256 text)
        )
        select
          exists (
            select 1 from schema_migrations
            where version = %s and checksum_sha256 = %s
          ) as ledger_current,
          (
            select count(*)
            from expected_indexes expected
            join schema_index_migrations installed
              on installed.index_name = expected.index_name
             and installed.target_version = expected.target_version
             and installed.checksum_sha256 = expected.checksum_sha256
             and installed.state = 'ready'
          ) = (select count(*) from expected_indexes)
          and not exists (
            select 1
            from schema_index_migrations installed
            left join expected_indexes expected
              on expected.index_name = installed.index_name
             and expected.target_version = installed.target_version
             and expected.checksum_sha256 = installed.checksum_sha256
             and installed.state = 'ready'
            where expected.index_name is null
          ) as index_ledger_current
        """,
        (
            _json_contract(
                index_ledger_contract,
                ("index_name", "target_version", "checksum_sha256"),
            ),
            TARGET_SCHEMA_VERSION,
            checksum,
        ),
    )
    relation_row = await relation_cursor.fetchone() or {}
    column_row = await column_cursor.fetchone() or {}
    constraint_row = await constraint_cursor.fetchone() or {}
    index_row = await index_cursor.fetchone() or {}
    ledger_row = await ledger_cursor.fetchone() or {}
    relations_current = bool(relation_row.get("current"))
    columns_current = bool(column_row.get("current"))
    constraints_current = bool(constraint_row.get("current"))
    indexes_current = bool(index_row.get("current"))
    concurrent_index_definitions_current = all(
        [await _index_is_ready(conn, migration) for migration in CONCURRENT_INDEX_MIGRATIONS]
    )
    contracts_current = all(
        (
            relations_current,
            columns_current,
            constraints_current,
            indexes_current,
            concurrent_index_definitions_current,
        )
    )
    ready = (
        bool(ledger_row.get("ledger_current"))
        and bool(ledger_row.get("index_ledger_current"))
        and contracts_current
    )
    return {
        "ready": ready,
        "target_version": TARGET_SCHEMA_VERSION,
        "checksum_sha256": checksum,
        "ledger_current": bool(ledger_row.get("ledger_current")),
        "index_ledger_current": bool(ledger_row.get("index_ledger_current")),
        "contracts_current": contracts_current,
        "relations_current": relations_current,
        "columns_current": columns_current,
        "constraints_current": constraints_current,
        "indexes_current": indexes_current,
        "concurrent_index_definitions_current": concurrent_index_definitions_current,
    }


async def require_schema_current() -> dict[str, object]:
    async with transaction() as conn:
        status = await schema_status(conn)
    if not status["ready"]:
        raise SchemaMigrationError("schema_not_current")
    return status


async def _run_cli(command: str) -> int:
    try:
        if command == "apply":
            result = await apply_migrations()
        else:
            async with transaction() as conn:
                result = await schema_status(conn)
        print(json.dumps(result, sort_keys=True))
        return 0 if command == "apply" or bool(result["ready"]) else 1
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Platform PostgreSQL schema lifecycle")
    parser.add_argument("command", choices=("apply", "status"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_cli(args.command)))


if __name__ == "__main__":
    main()
