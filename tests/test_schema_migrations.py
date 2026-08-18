import asyncio
from contextlib import asynccontextmanager
import json

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
                    "access_method": migration.access_method,
                    "column_names": list(migration.column_names),
                    "descending": list(migration.descending),
                    "opclass_names": list(migration.opclass_names),
                    "predicate": migration.predicate_expression or None,
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
        if normalized.startswith("delete from schema_index_migrations"):
            expected_names = set(json.loads(params[0]))
            removed = [name for name in self.state.index_ledger if name not in expected_names]
            for name in removed:
                del self.state.index_ledger[name]
            return FakeCursor(None if not removed else {"index_name": removed[0]})
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
    assert schema_migrations.TARGET_SCHEMA_VERSION == "2026.08.17.3"
    assert schema_migrations.CRITICAL_RELATIONS == (
        "schema_migrations",
        "schema_index_migrations",
        "runs",
        "run_skill_materializations",
        "run_events",
        "messages",
        "files",
        "artifacts",
        "object_deletion_outbox",
        "audit_logs",
        "sandbox_leases",
    )
    assert (
        "agent_profile_revisions",
        "skill_set",
        "jsonb",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "agent_profile_revisions",
        "avatar_seed",
        "text",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "agent_profile_revisions",
        "supported_file_types",
        "jsonb",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "runs",
        "chk_runs_execution_skill_identity",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_state",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "files",
        "chk_files_lifecycle_state",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_target",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_target_state",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "object_deletion_outbox",
        "object_deletion_outbox_file_id_fkey",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "sandbox_leases",
        "chk_sandbox_leases_executor_status",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "sandbox_leases",
        "chk_sandbox_leases_executor_reconciliation_status",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert schema_migrations.CRITICAL_TRIGGERS == (
        (
            "agent_profile_revisions",
            "trg_agent_profile_legacy_insert_compatibility",
            "agent_profile_legacy_insert_compatibility",
            7,
        ),
        (
            "agent_profile_revisions",
            "trg_agent_profile_legacy_insert_reconcile",
            "agent_profile_legacy_insert_reconcile",
            5,
        ),
    )
    trigger_contract = schema_migrations._critical_trigger_contract()
    assert [item[:4] for item in trigger_contract] == list(schema_migrations.CRITICAL_TRIGGERS)
    assert all(item[4].startswith("\ndeclare") or item[4].startswith("\nbegin") for item in trigger_contract)
    assert all(item[4].endswith("end ") for item in trigger_contract)
    assert all("\n" in item[4] for item in trigger_contract)
    assert schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS == (
        (
            "files",
            "chk_files_lifecycle_state",
            "c",
            "CHECK (lifecycle_state = ANY (ARRAY["
            "'active'::text, 'delete_pending'::text, 'deleted'::text]))",
        ),
        (
            "object_deletion_outbox",
            "chk_object_deletion_outbox_state",
            "c",
            "CHECK (state = ANY (ARRAY["
            "'pending'::text, 'processing'::text, 'failed'::text, "
            "'dead_letter'::text, 'deleted'::text, 'file_pending'::text, "
            "'file_processing'::text, 'file_failed'::text, 'file_dead_letter'::text, "
            "'file_deleted'::text]))",
        ),
        (
            "object_deletion_outbox",
            "chk_object_deletion_outbox_target_state",
            "c",
            "CHECK (target_type = 'artifact'::text AND (state = ANY (ARRAY["
            "'pending'::text, 'processing'::text, 'failed'::text, 'dead_letter'::text, "
            "'deleted'::text])) OR target_type = 'file'::text AND (state = ANY (ARRAY["
            "'file_pending'::text, 'file_processing'::text, 'file_failed'::text, "
            "'file_dead_letter'::text, 'file_deleted'::text])))",
        ),
        (
            "object_deletion_outbox",
            "chk_object_deletion_outbox_target",
            "c",
            "CHECK (target_type = 'artifact'::text AND artifact_id IS NOT NULL "
            "AND file_id IS NULL OR target_type = 'file'::text AND artifact_id IS NULL "
            "AND file_id IS NOT NULL)",
        ),
        (
            "object_deletion_outbox",
            "object_deletion_outbox_file_id_fkey",
            "f",
            "FOREIGN KEY (file_id) REFERENCES files(id)",
        ),
        (
            "sandbox_leases",
            "chk_sandbox_leases_executor_status",
            "c",
            "CHECK (executor_status = ANY (ARRAY["
            "'pending'::text, 'accepted'::text, 'running'::text, "
            "'completed'::text, 'failed'::text, 'cancelled'::text]))",
        ),
        (
            "sandbox_leases",
            "chk_sandbox_leases_executor_reconciliation_status",
            "c",
            "CHECK (executor_reconciliation_status = ANY (ARRAY["
            "'waiting_terminal'::text, 'pending'::text, 'claimed'::text, "
            "'retry'::text, 'finalized'::text]))",
        ),
    )
    assert (
        "object_deletion_outbox",
        "lease_generation",
        "int8",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "sandbox_leases",
        "executor_terminal_json",
        "jsonb",
        False,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "sandbox_leases",
        "executor_status",
        "text",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "idx_sandbox_leases_attempt",
        False,
    ) in schema_migrations.CRITICAL_INDEXES
    migrations = {item.name: item for item in schema_migrations.CONCURRENT_INDEX_MIGRATIONS}
    assert migrations["idx_object_deletion_outbox_claim"].predicate_expression == (
        "state = 'pending' or state = 'processing' or state = 'failed' "
        "or state = 'file_pending' or state = 'file_processing' or state = 'file_failed'"
    )
    assert migrations[
        "idx_object_deletion_outbox_artifact_storage_live"
    ].predicate_expression == (
        "target_type = 'artifact' and state <> 'deleted'"
    )
    assert migrations["uq_object_deletion_outbox_file"].unique is True
    assert migrations["uq_object_deletion_outbox_file"].predicate_expression == (
        "target_type = 'file' and file_id is not null"
    )
    assert {
        name: (migrations[name].access_method, migrations[name].opclass_names)
        for name in (
            "idx_runs_input_json_gin",
            "idx_messages_metadata_json_gin",
            "idx_run_context_snapshots_file_ids_gin",
            "idx_artifacts_manifest_json_gin",
        )
    } == {
        "idx_runs_input_json_gin": ("gin", ("jsonb_path_ops",)),
        "idx_messages_metadata_json_gin": ("gin", ("jsonb_path_ops",)),
        "idx_run_context_snapshots_file_ids_gin": ("gin", ("jsonb_ops",)),
        "idx_artifacts_manifest_json_gin": ("gin", ("jsonb_path_ops",)),
    }


def test_profile_file_type_retirement_keeps_additive_rollback_storage_only():
    schema = " ".join(schema_migrations.schema_sql().split()).lower()

    assert schema_migrations.schema_checksum() == (
        "092002ab939029ced0d4d1b93536e9184142a98800db65bc56b05d747aaab48e"
    )
    assert (
        "alter table agent_profile_revisions add column if not exists "
        "supported_file_types jsonb not null default '[]'::jsonb"
    ) in schema
    assert "rename column supported_file_types" not in schema
    assert "drop column supported_file_types" not in schema
    assert "legacy_supported_file_types" not in schema


def test_sandbox_executor_async_terminal_columns_are_additive():
    schema = " ".join(schema_migrations.schema_sql().split()).lower()

    for column in (
        "executor_status text",
        "executor_heartbeat_at timestamptz",
        "executor_terminal_json jsonb",
        "executor_terminal_received_at timestamptz",
        "executor_reconciliation_context_json jsonb",
        "executor_reconciliation_status text",
        "executor_reconciliation_claim_token text",
        "executor_reconciliation_claimed_at timestamptz",
        "executor_reconciliation_attempt_count integer",
        "executor_reconciliation_error text",
        "executor_reconciled_at timestamptz",
    ):
        assert f"alter table sandbox_leases add column if not exists {column}" in schema
