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
        self.ledger = {}
        self.schema_execute_count = 0


class FakeMigrationConnection:
    def __init__(self, state):
        self.state = state
        self.locked = False

    async def execute(self, statement, params=None):
        normalized = " ".join(str(statement).split()).lower()
        if normalized.startswith("select pg_advisory_xact_lock"):
            await self.state.lock.acquire()
            self.locked = True
            return FakeCursor(None)
        if normalized.startswith("create table if not exists schema_migrations"):
            return FakeCursor(None)
        if normalized.startswith("select checksum_sha256 from schema_migrations"):
            checksum = self.state.ledger.get(params[0])
            return FakeCursor(None if checksum is None else {"checksum_sha256": checksum})
        if normalized.startswith("insert into schema_migrations"):
            self.state.ledger[params[0]] = params[1]
            return FakeCursor(None)
        if str(statement) == schema_migrations.schema_sql():
            self.state.schema_execute_count += 1
            await asyncio.sleep(0)
            return FakeCursor(None)
        raise AssertionError(normalized)


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
        schema_migrations.apply_migrations(transaction_factory=transaction_factory(state)),
        schema_migrations.apply_migrations(transaction_factory=transaction_factory(state)),
    )

    assert {first["status"], second["status"]} == {"applied", "current"}
    assert state.schema_execute_count == 1
    assert state.ledger[schema_migrations.TARGET_SCHEMA_VERSION] == schema_migrations.schema_checksum()


@pytest.mark.asyncio
async def test_migration_checksum_mismatch_fails_closed_without_schema_execution():
    state = SharedMigrationState()
    state.ledger[schema_migrations.TARGET_SCHEMA_VERSION] = "0" * 64

    with pytest.raises(schema_migrations.SchemaMigrationError, match="schema_migration_checksum_mismatch"):
        await schema_migrations.apply_migrations(transaction_factory=transaction_factory(state))

    assert state.schema_execute_count == 0


def test_schema_contract_names_are_bounded_and_include_lifecycle_tables():
    assert schema_migrations.CRITICAL_RELATIONS == (
        "schema_migrations",
        "runs",
        "run_events",
        "messages",
        "files",
        "artifacts",
        "audit_logs",
    )
