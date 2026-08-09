import asyncio
from contextlib import asynccontextmanager

import pytest

from app import schema_migrations


class FakeCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class SharedMigrationState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.index_lock = asyncio.Lock()
        self.ledger = {}
        self.index_ledger = {}
        self.indexes = set()
        self.schema_execute_count = 0
        self.index_execute_count = 0


class FakeMigrationConnection:
    def __init__(self, state):
        self.state = state
        self.locked = False

    async def execute(self, statement, params=None):
        normalized = " ".join(str(statement).split()).lower()
        if str(statement) == schema_migrations.schema_sql():
            self.state.schema_execute_count += 1
            await asyncio.sleep(0)
            return FakeCursor(None)
        if normalized.startswith("select pg_advisory_xact_lock"):
            await self.state.lock.acquire()
            self.locked = True
            return FakeCursor(None)
        if normalized.startswith("create table if not exists schema_migrations"):
            return FakeCursor(None)
        if normalized.startswith("create table if not exists schema_index_migrations"):
            return FakeCursor(None)
        if normalized.startswith("select checksum_sha256 from schema_migrations"):
            checksum = self.state.ledger.get(params[0])
            return FakeCursor(None if checksum is None else {"checksum_sha256": checksum})
        if normalized.startswith("insert into schema_migrations"):
            self.state.ledger[params[0]] = params[1]
            return FakeCursor(None)
        raise AssertionError(normalized)


class FakeIndexConnection:
    def __init__(self, state):
        self.state = state
        self.locked = False

    async def execute(self, statement, params=None):
        normalized = " ".join(str(statement).split()).lower()
        if normalized.startswith("select pg_try_advisory_lock"):
            if self.state.index_lock.locked():
                return FakeCursor({"acquired": False})
            await self.state.index_lock.acquire()
            self.locked = True
            return FakeCursor({"acquired": True})
        if normalized.startswith("select pg_advisory_unlock"):
            self.state.index_lock.release()
            self.locked = False
            return FakeCursor({"pg_advisory_unlock": True})
        if normalized.startswith("create table if not exists schema_migrations"):
            return FakeCursor(None)
        if normalized.startswith("create table if not exists schema_index_migrations"):
            return FakeCursor(None)
        if normalized.startswith("select target_version, checksum_sha256, state"):
            return FakeCursor(self.state.index_ledger.get(params[0]))
        if "from pg_index indexes" in normalized:
            migration = next(
                item
                for item in schema_migrations.CONCURRENT_INDEX_MIGRATIONS
                if item.name == params[0]
            )
            return FakeCursor(
                {
                    "ready": True,
                    "is_unique": migration.unique,
                    "table_name": migration.table_name,
                    "column_names": list(migration.column_names),
                    "descending": list(migration.descending),
                    "predicate": " and ".join(migration.predicate_fragments) or None,
                }
                if params[0] in self.state.indexes
                else None
            )
        if normalized.startswith("insert into schema_index_migrations"):
            current = self.state.index_ledger.get(params[0], {})
            self.state.index_ledger[params[0]] = {
                "target_version": params[1],
                "checksum_sha256": params[2],
                "state": "building",
                "attempts": int(current.get("attempts") or 0) + 1,
            }
            return FakeCursor(None)
        if normalized.startswith("drop index concurrently"):
            self.state.indexes.discard(normalized.rsplit(" ", 1)[-1])
            return FakeCursor(None)
        for migration in schema_migrations.CONCURRENT_INDEX_MIGRATIONS:
            if str(statement) == migration.sql:
                self.state.indexes.add(migration.name)
                self.state.index_execute_count += 1
                return FakeCursor(None)
        if normalized.startswith("update schema_index_migrations"):
            index_name = params[-1] if "state = 'failed'" in normalized else params[0]
            self.state.index_ledger[index_name]["state"] = (
                "failed" if "state = 'failed'" in normalized else "ready"
            )
            return FakeCursor(None)
        raise AssertionError(normalized)

    async def close(self):
        if self.locked:
            self.state.index_lock.release()
            self.locked = False


def index_connection_factory(state):
    async def factory():
        return FakeIndexConnection(state)

    return factory


def transaction_factory(state):
    @asynccontextmanager
    async def factory():
        conn = FakeMigrationConnection(state)
        try:
            yield conn
        finally:
            if conn.locked:
                state.lock.release()

    return factory


@pytest.mark.asyncio
async def test_concurrent_migrations_serialize_and_apply_schema_once():
    state = SharedMigrationState()
    first, second = await asyncio.gather(
        schema_migrations.apply_migrations(
            transaction_factory=transaction_factory(state),
            index_connection_factory=index_connection_factory(state),
        ),
        schema_migrations.apply_migrations(
            transaction_factory=transaction_factory(state),
            index_connection_factory=index_connection_factory(state),
        ),
    )

    assert {first["status"], second["status"]} == {"applied", "current"}
    assert state.schema_execute_count == 1
    assert state.index_execute_count == len(schema_migrations.CONCURRENT_INDEX_MIGRATIONS)
    assert state.ledger[schema_migrations.TARGET_SCHEMA_VERSION] == schema_migrations.schema_checksum()


@pytest.mark.asyncio
async def test_migration_checksum_mismatch_fails_closed_without_schema_execution():
    state = SharedMigrationState()
    state.ledger[schema_migrations.TARGET_SCHEMA_VERSION] = "0" * 64

    with pytest.raises(schema_migrations.SchemaMigrationError, match="schema_migration_checksum_mismatch"):
        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory(state),
            index_connection_factory=index_connection_factory(state),
        )

    assert state.schema_execute_count == 0


def test_schema_contract_names_are_bounded_and_include_lifecycle_tables():
    assert schema_migrations.CRITICAL_RELATIONS == (
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
    assert (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_state",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
