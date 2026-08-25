import asyncio
from contextlib import asynccontextmanager
import json

import pytest

from app import schema_migrations


# Exact 2026.08.27.1 ledger checksum at the remote PR predecessor 829acfcd.
REMOTE_SUCCESSOR_ACTIVATION_CHECKSUM = (
    "d474b751d6fb6bff75cbbb8f3c482cb42f38ac462c116313baeccfc2c247fef7"
)


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


@pytest.mark.asyncio
async def test_base_schema_ledger_advances_to_terminal_reconciliation_schema():
    state = SharedMigrationState()
    state.ledger["2026.08.18.1"] = (
        "f4972e68f15ed1c3663cd3696bb2471e4503fbe23753617a6556887bc5075415"
    )

    result = await schema_migrations.apply_migrations(
        transaction_factory=transaction_factory(state),
        index_connection_factory=index_connection_factory(state),
    )

    assert result["status"] == "applied"
    assert state.schema_execute_count == 1
    assert state.ledger["2026.08.18.1"] == (
        "f4972e68f15ed1c3663cd3696bb2471e4503fbe23753617a6556887bc5075415"
    )
    assert state.ledger[schema_migrations.TARGET_SCHEMA_VERSION] == (
        schema_migrations.schema_checksum()
    )


@pytest.mark.asyncio
async def test_pending_admission_schema_advances_to_current_schema():
    state = SharedMigrationState()
    predecessor_checksum = "9f80933b643ad71c23f416e8ad2a52b3890efba83ec16e990a66979662b93d20"
    state.ledger[schema_migrations.V4_PENDING_ADMISSION_SCHEMA_VERSION] = (
        predecessor_checksum
    )

    result = await schema_migrations.apply_migrations(
        transaction_factory=transaction_factory(state),
        index_connection_factory=index_connection_factory(state),
    )

    assert result["status"] == "applied"
    assert state.schema_execute_count == 1
    assert state.ledger[schema_migrations.V4_PENDING_ADMISSION_SCHEMA_VERSION] == (
        predecessor_checksum
    )
    assert state.ledger[schema_migrations.TARGET_SCHEMA_VERSION] == (
        schema_migrations.schema_checksum()
    )


@pytest.mark.asyncio
async def test_successor_activation_schema_advances_to_concurrent_due_index_schema():
    state = SharedMigrationState()
    state.ledger[schema_migrations.V4_SUCCESSOR_ACTIVATION_SCHEMA_VERSION] = (
        REMOTE_SUCCESSOR_ACTIVATION_CHECKSUM
    )

    result = await schema_migrations.apply_migrations(
        transaction_factory=transaction_factory(state),
        index_connection_factory=index_connection_factory(state),
    )

    assert result["status"] == "applied"
    assert state.schema_execute_count == 1
    assert state.ledger[schema_migrations.V4_SUCCESSOR_ACTIVATION_SCHEMA_VERSION] == (
        REMOTE_SUCCESSOR_ACTIVATION_CHECKSUM
    )
    assert state.ledger[schema_migrations.TARGET_SCHEMA_VERSION] == (
        schema_migrations.schema_checksum()
    )


def test_schema_contract_names_are_bounded_and_include_lifecycle_tables():
    assert schema_migrations.TARGET_SCHEMA_VERSION == "2026.08.27.2"
    assert schema_migrations.CRITICAL_RELATIONS == (
        "schema_migrations",
        "schema_index_migrations",
        "runs",
        "run_attempts",
        "run_skill_materializations",
        "run_events",
        "sse_stream_authorities",
        "sse_stream_rebuild_items",
        "messages",
        "files",
        "artifacts",
        "object_deletion_outbox",
        "audit_logs",
        "sandbox_leases",
    )
    assert (
        "sessions",
        "title_source",
        "text",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
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
        "sessions",
        "chk_sessions_title_source",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "runs",
        "chk_runs_execution_skill_identity",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_attempts",
        "execution_spec_sha256",
        "text",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "run_attempts",
        "execution_spec_canonical_json",
        "text",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "run_attempts",
        "chk_run_attempts_status",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_attempts",
        "chk_run_attempts_required_identity",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_attempts",
        "chk_run_attempts_spec_canonical_json",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_attempts",
        "chk_run_attempts_terminal_time",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_attempts",
        "run_attempts_tenant_id_run_id_ordinal_key",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_attempts",
        "run_attempts_tenant_id_run_id_queue_attempt_id_key",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_attempts",
        "lease_expires_at",
        "timestamptz",
        False,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "run_attempts",
        "terminal_reason",
        "text",
        True,
    ) in schema_migrations.CRITICAL_COLUMNS
    assert (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_state",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_events",
        "chk_run_events_stream_publication_state",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    assert (
        "run_events",
        "chk_run_events_stream_publication_claim",
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
            "run_attempts",
            "trg_run_attempt_transition_guard",
            "ai_platform_guard_run_attempt_transition",
            23,
        ),
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
            "run_attempts",
            "fk_run_attempts_run",
            "f",
            "FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id)",
        ),
        (
            "run_attempts",
            "chk_run_attempts_ordinal",
            "c",
            "CHECK (ordinal > 0)",
        ),
        (
            "run_attempts",
            "chk_run_attempts_owner_generation",
            "c",
            "CHECK (owner_generation > 0)",
        ),
        (
            "run_attempts",
            "chk_run_attempts_status",
            "c",
            "CHECK (status = ANY (ARRAY["
            "'created'::text, 'queued'::text, 'claimed'::text, 'running'::text, "
            "'cancel_requested'::text, 'expired'::text, 'succeeded'::text, "
            "'failed'::text, 'cancelled'::text]))",
        ),
        (
            "run_attempts",
            "chk_run_attempts_owner_kind",
            "c",
            "CHECK (owner_kind = ANY (ARRAY["
            "'queue_worker'::text, 'reconciler'::text, 'operator'::text]))",
        ),
        (
            "run_attempts",
            "chk_run_attempts_spec_sha256",
            "c",
            "CHECK (execution_spec_sha256 ~ '^[0-9a-f]{64}$'::text "
            "AND execution_spec_sha256 = encode("
            "sha256(convert_to(execution_spec_canonical_json, 'UTF8'::name)), "
            "'hex'::text))",
        ),
        (
            "run_attempts",
            "chk_run_attempts_required_identity",
            "c",
            "CHECK (id <> ''::text AND owner_id <> ''::text "
            "AND queue_attempt_id <> ''::text "
            "AND execution_spec_schema_version <> ''::text "
            "AND (queue_message_id IS NULL OR queue_message_id <> ''::text))",
        ),
        (
            "run_attempts",
            "chk_run_attempts_spec_json",
            "c",
            "CHECK (jsonb_typeof(execution_spec_json) = 'object'::text "
            "AND (execution_spec_json ->> 'schema_version'::text) "
            "= execution_spec_schema_version)",
        ),
        (
            "run_attempts",
            "chk_run_attempts_spec_canonical_json",
            "c",
            "CHECK (execution_spec_canonical_json <> ''::text "
            "AND execution_spec_canonical_json::jsonb = execution_spec_json)",
        ),
        (
            "run_attempts",
            "chk_run_attempts_terminal_time",
            "c",
            "CHECK ((status = ANY (ARRAY['succeeded'::text, 'failed'::text, "
            "'cancelled'::text])) AND finished_at IS NOT NULL "
            "OR (status <> ALL (ARRAY['succeeded'::text, 'failed'::text, "
            "'cancelled'::text])) AND finished_at IS NULL)",
        ),
        (
            "run_attempts",
            "run_attempts_tenant_id_run_id_ordinal_key",
            "u",
            "UNIQUE (tenant_id, run_id, ordinal)",
        ),
        (
            "run_attempts",
            "run_attempts_tenant_id_run_id_queue_attempt_id_key",
            "u",
            "UNIQUE (tenant_id, run_id, queue_attempt_id)",
        ),
        (
            "run_events",
            "chk_run_events_stream_publication_state",
            "c",
            "CHECK (stream_publication_state IS NULL OR (stream_publication_state = ANY (ARRAY["
            "'pending'::text, 'published'::text, 'suppressed'::text])))",
        ),
        (
            "run_events",
            "chk_run_events_stream_publication_claim",
            "c",
            "CHECK (stream_publication_claim_token IS NULL AND "
            "stream_publication_claim_expires_at IS NULL OR "
            "stream_publication_claim_token IS NOT NULL AND "
            "stream_publication_claim_expires_at IS NOT NULL)",
        ),
        (
            "sse_stream_authorities",
            "chk_sse_stream_authority_open_format",
            "c",
            "CHECK (open_event_id <> ''::text AND open_payload_bytes <> ''::text "
            "AND open_payload_digest ~ '^[0-9a-f]{64}$'::text)",
        ),
        (
            "sse_stream_authorities",
            "chk_sse_stream_authority_pending_confirmation",
            "c",
            "CHECK (state = 'admission_pending'::text AND admission_confirmed_at IS NULL "
            "OR state <> 'admission_pending'::text AND admission_confirmed_at IS NOT NULL)",
        ),
        (
            "sse_stream_rebuilds",
            "chk_sse_stream_rebuild_identity",
            "c",
            "CHECK (id <> ''::text AND attempt_id <> ''::text "
            "AND successor_open_event_id <> ''::text AND successor_open_bytes <> ''::text "
            "AND source_authority_fingerprint ~ '^[0-9a-f]{64}$'::text "
            "AND successor_open_digest ~ '^[0-9a-f]{64}$'::text "
            "AND claim_token_digest ~ '^[0-9a-f]{64}$'::text)",
        ),
        (
            "sse_stream_rebuilds",
            "chk_sse_stream_rebuild_authority",
            "c",
            "CHECK (source_incarnation > 0 AND successor_incarnation > source_incarnation "
            "AND source_authorization_epoch > 0 "
            "AND successor_authorization_epoch > source_authorization_epoch)",
        ),
        (
            "sse_stream_rebuilds",
            "chk_sse_stream_rebuild_origin",
            "c",
            "CHECK (origin_incarnation > 0 AND origin_incarnation <= source_incarnation "
            "AND origin_authorization_epoch > 0 "
            "AND origin_authorization_epoch <= source_authorization_epoch)",
        ),
        (
            "sse_stream_rebuilds",
            "chk_sse_stream_rebuild_progress",
            "c",
            "CHECK (source_cursor_sequence >= source_through_sequence "
            "AND source_through_sequence > 0 AND item_count > 0 "
            "AND built_through_sequence >= 0 "
            "AND built_through_sequence <= source_through_sequence)",
        ),
        (
            "sse_stream_rebuilds",
            "chk_sse_stream_rebuild_state",
            "c",
            "CHECK (state = ANY (ARRAY['building'::text, 'ready'::text, "
            "'cutover'::text, 'aborted'::text, 'expired'::text]))",
        ),
        (
            "sse_stream_rebuilds",
            "chk_sse_stream_rebuild_receipt",
            "c",
            "CHECK (receipt_entry_count IS NULL AND receipt_open_event_id IS NULL "
            "AND receipt_terminal_event_id IS NULL AND receipt_end_event_id IS NULL "
            "AND receipt_last_redis_id IS NULL AND receipt_last_envelope_bytes IS NULL "
            "AND receipt_last_envelope_digest IS NULL AND receipt_digest IS NULL "
            "OR receipt_entry_count IS NOT NULL "
            "AND receipt_entry_count = (item_count + 2) "
            "AND receipt_open_event_id IS NOT NULL AND receipt_open_event_id <> ''::text "
            "AND receipt_terminal_event_id IS NOT NULL "
            "AND receipt_terminal_event_id <> ''::text "
            "AND receipt_end_event_id IS NOT NULL AND receipt_end_event_id <> ''::text "
            "AND receipt_last_redis_id IS NOT NULL "
            "AND receipt_last_redis_id ~ '^[0-9]+-[0-9]+$'::text "
            "AND receipt_last_envelope_bytes IS NOT NULL "
            "AND receipt_last_envelope_bytes <> ''::text "
            "AND receipt_last_envelope_digest IS NOT NULL "
            "AND receipt_last_envelope_digest ~ '^[0-9a-f]{64}$'::text "
            "AND receipt_digest IS NOT NULL "
            "AND receipt_digest ~ '^[0-9a-f]{64}$'::text)",
        ),
        (
            "sse_stream_rebuilds",
            "fk_sse_stream_rebuild_authority",
            "f",
            "FOREIGN KEY (tenant_id, run_id) "
            "REFERENCES sse_stream_authorities(tenant_id, run_id)",
        ),
        (
            "sse_stream_rebuild_items",
            "sse_stream_rebuild_items_pkey",
            "p",
            "PRIMARY KEY (rebuild_id, sequence)",
        ),
        (
            "sse_stream_rebuild_items",
            "chk_sse_stream_rebuild_item",
            "c",
            "CHECK (sequence > 0 AND event_id <> ''::text AND event_type <> ''::text "
            "AND canonical_envelope_bytes <> ''::text "
            "AND envelope_digest ~ '^[0-9a-f]{64}$'::text)",
        ),
        (
            "sse_stream_rebuild_items",
            "chk_sse_stream_rebuild_item_redis_id",
            "c",
            "CHECK (redis_id IS NULL OR redis_id ~ '^[0-9]+-[0-9]+$'::text)",
        ),
        (
            "sse_stream_rebuild_items",
            "fk_sse_stream_rebuild_item_operation",
            "f",
            "FOREIGN KEY (rebuild_id) REFERENCES sse_stream_rebuilds(id)",
        ),
        (
            "sse_stream_rebuild_items",
            "uq_sse_stream_rebuild_item_event",
            "u",
            "UNIQUE (rebuild_id, event_id)",
        ),
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
            "'retry'::text, 'finalized'::text, 'failed'::text]))",
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
    assert ("uq_run_attempts_one_open", True) in schema_migrations.CRITICAL_INDEXES
    static_indexes = {
        definition.name: definition
        for definition in schema_migrations.STATIC_INDEX_DEFINITIONS
    }
    assert static_indexes["uq_run_attempts_one_open"].column_names == (
        "tenant_id",
        "run_id",
    )
    assert static_indexes["uq_run_attempts_one_open"].predicate_expression == (
        "status = any array['created', 'queued', 'claimed', 'running', "
        "'cancel_requested', 'expired']"
    )
    assert static_indexes["idx_run_attempts_run_created"].descending == (
        False,
        False,
        True,
    )
    assert static_indexes["idx_run_attempts_lease_reconcile"].predicate_expression == (
        "status = any array['claimed', 'running', 'cancel_requested', 'expired']"
    )
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


def test_every_critical_run_attempt_constraint_has_an_exact_definition():
    critical = {
        constraint_name
        for relation_name, constraint_name in schema_migrations.CRITICAL_CONSTRAINTS
        if relation_name == "run_attempts"
    }
    defined = {
        constraint_name
        for relation_name, constraint_name, _, _ in schema_migrations.CRITICAL_CONSTRAINT_DEFINITIONS
        if relation_name == "run_attempts"
    }

    assert defined == critical


def test_profile_file_type_retirement_keeps_additive_rollback_storage_only():
    schema = " ".join(schema_migrations.schema_sql().split()).lower()

    assert schema_migrations.schema_checksum() == (
        "9bd4a01cc1db6cdbe445a4a1b4258bfd3513ca34ca6a6381d98ba97006ed2de2"
    )
    assert (
        "alter table agent_profile_revisions add column if not exists "
        "supported_file_types jsonb not null default '[]'::jsonb"
    ) in schema
    assert "rename column supported_file_types" not in schema
    assert "drop column supported_file_types" not in schema
    assert "legacy_supported_file_types" not in schema


def test_v4_publication_schema_is_additive_and_index_is_concurrent_only():
    schema = " ".join(schema_migrations.schema_sql().split()).lower()
    for column in (
        "stream_publication_state text",
        "stream_publication_attempts integer",
        "stream_publication_next_attempt_at timestamptz",
        "stream_publication_redis_id text",
        "stream_publication_last_error text",
        "stream_publication_claim_token text",
        "stream_publication_claim_expires_at timestamptz",
    ):
        assert column in schema
    assert "create index if not exists idx_run_events_stream_publication_retry" not in schema
    assert "create index if not exists idx_run_events_stream_publication_claim" not in schema
    assert "chk_run_events_stream_publication_claim" in schema
    retry_migration = next(
        item
        for item in schema_migrations.CONCURRENT_INDEX_MIGRATIONS
        if item.name == "idx_run_events_stream_publication_retry"
    )
    assert retry_migration.predicate_expression == (
        "visible_to_user = true and stream_publication_state = 'pending'"
    )
    assert retry_migration.sql.endswith(
        "where visible_to_user = true and stream_publication_state = 'pending'"
    )
    claim_migration = next(
        item
        for item in schema_migrations.CONCURRENT_INDEX_MIGRATIONS
        if item.name == "idx_run_events_stream_publication_claim"
    )
    assert claim_migration.column_names == ("tenant_id", "run_id", "sequence", "id")
    assert claim_migration.predicate_expression == (
        "visible_to_user = true and stream_publication_state = 'pending' "
        "and payload_json ? '__stream_v4'"
    )


def test_v4_successor_rebuild_schema_is_additive_and_claim_fenced():
    schema = " ".join(schema_migrations.schema_sql().split()).lower()
    assert "create table if not exists sse_stream_rebuilds" in schema
    assert "create table if not exists sse_stream_rebuild_items" in schema
    assert "successor_incarnation > source_incarnation" in schema
    assert "successor_authorization_epoch > source_authorization_epoch" in schema
    assert "claim_token_digest ~ '^[0-9a-f]{64}$'" in schema
    assert "receipt_entry_count = item_count + 2" in schema
    assert "receipt_digest is not null" in schema
    assert "drop constraint if exists chk_sse_stream_rebuild_receipt" in schema
    assert "where state in ('building', 'ready')" in schema
    assert (
        "sse_stream_rebuilds",
        "chk_sse_stream_rebuild_state",
    ) in schema_migrations.CRITICAL_CONSTRAINTS
    static_indexes = {
        definition.name: definition
        for definition in schema_migrations.STATIC_INDEX_DEFINITIONS
    }
    assert static_indexes["uq_sse_stream_rebuild_active"].unique is True
    assert static_indexes["uq_sse_stream_rebuild_active"].predicate_expression == (
        "state = any array['building', 'ready']"
    )
    assert static_indexes["uq_sse_stream_rebuild_item_event"].unique is True


def test_schema_upgrade_delegates_v4_index_and_repairs_confirmation_history():
    schema = schema_migrations.schema_sql()
    due_index = next(
        migration
        for migration in schema_migrations.CONCURRENT_INDEX_MIGRATIONS
        if migration.name == "idx_run_events_v4_due_scope"
    )
    repair_confirmation = schema.index(
        "update sse_stream_authorities\nset admission_confirmed_at = coalesce("
    )
    add_confirmation_constraint = schema.index(
        "add constraint chk_sse_stream_authority_pending_confirmation"
    )

    assert "create index if not exists idx_run_events_v4_due_scope" not in schema
    assert due_index.sql.startswith(
        "create index concurrently if not exists idx_run_events_v4_due_scope"
    )
    assert due_index.column_names == ("tenant_id", "run_id", "sequence")
    assert repair_confirmation < add_confirmation_constraint


@pytest.mark.asyncio
async def test_v4_successor_rollback_removes_only_dormant_snapshot_tables():
    class FakeResult:
        async def fetchone(self):
            return None

    class FakeRollbackConnection:
        def __init__(self) -> None:
            self.statements: list[tuple[str, object]] = []

        async def execute(self, statement: str, params: object = None):
            self.statements.append((" ".join(statement.lower().split()), params))
            return FakeResult()

    conn = FakeRollbackConnection()
    await schema_migrations.rollback_v4_successor_rebuild_migration(conn)
    assert [statement for statement, _ in conn.statements[1:3]] == [
        "drop table if exists sse_stream_rebuild_items",
        "drop table if exists sse_stream_rebuilds",
    ]
    assert conn.statements[3][1] == (
        schema_migrations.V4_SUCCESSOR_REBUILD_SCHEMA_VERSION,
        schema_migrations.V4_SUCCESSOR_ACTIVATION_SCHEMA_VERSION,
        schema_migrations.TARGET_SCHEMA_VERSION,
    )
    assert all("run_events" not in statement for statement, _ in conn.statements)
    assert all("sse_stream_authorities" not in statement for statement, _ in conn.statements)


@pytest.mark.asyncio
async def test_v4_successor_rollback_rejects_activated_lineage():
    class ActivatedResult:
        async def fetchone(self):
            return {"exists": 1}

    class ActivatedConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement: str, params: object = None):
            self.statements.append(" ".join(statement.lower().split()))
            return ActivatedResult()

    conn = ActivatedConnection()
    with pytest.raises(
        schema_migrations.SchemaMigrationError,
        match="v4_successor_rebuild_rollback_cutover_exists",
    ):
        await schema_migrations.rollback_v4_successor_rebuild_migration(conn)
    assert conn.statements == [
        "select 1 from sse_stream_rebuilds where state = 'cutover' limit 1"
    ]


@pytest.mark.asyncio
async def test_v4_rollback_removes_only_publication_bookkeeping():
    class FakeRollbackConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.event_facts = [{"id": "evt4_fact", "sequence": 9}]

        async def execute(self, statement: str, params: object = None) -> None:
            self.statements.append(" ".join(statement.lower().split()))

    conn = FakeRollbackConnection()
    await schema_migrations.rollback_v4_publication_migration(conn)
    assert conn.event_facts == [{"id": "evt4_fact", "sequence": 9}]
    assert any("drop index if exists idx_run_events_stream_publication_claim" in item for item in conn.statements)
    assert any("drop index if exists idx_run_events_stream_publication_retry" in item for item in conn.statements)
    assert any("delete from schema_index_migrations" in item for item in conn.statements)
    assert any("delete from schema_migrations" in item for item in conn.statements)
    assert any("drop constraint if exists chk_run_events_stream_publication_claim" in item for item in conn.statements)
    assert any("drop constraint if exists chk_run_events_stream_publication_state" in item for item in conn.statements)
    assert any("drop column if exists stream_publication_claim_token" in item for item in conn.statements)
    assert any("drop column if exists stream_publication_claim_expires_at" in item for item in conn.statements)
    assert any("drop column if exists stream_publication_state" in item for item in conn.statements)
    assert all("delete from run_events" not in item for item in conn.statements)


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
        "executor_terminal_reconciliation_attempt_count integer",
        "executor_reconciliation_error text",
        "executor_reconciled_at timestamptz",
    ):
        assert f"alter table sandbox_leases add column if not exists {column}" in schema
    assert (
        "check (executor_reconciliation_status in "
        "('waiting_terminal', 'pending', 'claimed', 'retry', 'finalized', 'failed'))"
    ) in schema
