from pathlib import Path
from types import MappingProxyType

import pytest

from app import repositories
from app import run_event_repository
from app.platform.postgres.errors import RepositoryConflictError
from app.streaming import postgres as ledger
from app.streaming.authority import RunCursor


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return []


class _Connection:
    def __init__(self):
        self.calls = []

    async def execute(self, statement, params=()):
        self.calls.append((statement, params))
        return _Cursor()


class _ScriptedCursor:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _ScriptedConnection:
    def __init__(self, results):
        self.results = list(results)

    async def execute(self, statement, params=()):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_append_event_uses_ledger_and_preserves_generic_conflict_identity(
    monkeypatch,
):
    conn = _Connection()
    observed = []

    async def append_one(_conn, *, tenant_id, run_id, event):
        observed.append((tenant_id, run_id, event))
        return ledger.EventReceipt("evt_1", RunCursor(run_id, 4))

    monkeypatch.setattr(run_event_repository._ledger, "append_event", append_one)

    event_id = await repositories.append_event(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        event_type="assistant_delta",
        stage="streaming",
        message="hello",
        payload={"delta": "hello"},
        visible_to_user=True,
    )

    assert event_id == "evt_1"
    assert observed == [
        (
            "tenant-a",
            "run-a",
            ledger.LedgerEvent(
                event_type="assistant_delta",
                stage="streaming",
                message="hello",
                payload={"delta": "hello"},
                visible_to_user=True,
            ),
        )
    ]
    assert repositories.RepositoryConflictError is RepositoryConflictError
    assert RepositoryConflictError is not ledger.RunEventLedgerConflictError


@pytest.mark.asyncio
async def test_append_event_record_returns_exact_post_commit_projection_facts(
    monkeypatch,
):
    async def append_one(_conn, *, tenant_id, run_id, event):
        assert tenant_id == "tenant-a"
        return ledger.EventReceipt(
            "evt-committed",
            RunCursor(run_id, 9),
            "2026-08-09T00:00:00Z",
        )

    monkeypatch.setattr(run_event_repository._ledger, "append_event", append_one)

    record = await repositories.append_event(
        _Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        event_type="execution_step",
        stage="execution",
        message="",
        payload={"visible_to_user": True},
        return_record=True,
    )

    assert record["id"] == "evt-committed"
    assert record["sequence"] == 9
    assert record["created_at"] == "2026-08-09T00:00:00Z"


@pytest.mark.asyncio
async def test_batch_receipt_and_terminal_fence_keep_existing_dict_contract(
    monkeypatch,
):
    conn = _Connection()

    async def append_batch(_conn, **_kwargs):
        return ledger.BatchReceipt(
            receipt_id="evb_1",
            event_ids=("evt_1", "evt_2"),
            first_cursor=RunCursor("run-a", 4),
            through_cursor=RunCursor("run-a", 5),
            duplicate=True,
            payload_digest="digest-a",
            projection_version="callback-receipt-v2.1",
            item_count=2,
            callback_received_at="2026-08-09T00:00:00Z",
        )

    async def fence(_conn, **_kwargs):
        return ledger.TerminalDrainReceipt(duplicate=False)

    monkeypatch.setattr(run_event_repository._ledger, "append_batch", append_batch)
    monkeypatch.setattr(
        run_event_repository._ledger, "acquire_terminal_drain_fence", fence
    )

    receipt = await repositories.append_event_batch(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-a",
        events=[
            {
                "event_type": "assistant_delta",
                "stage": "streaming",
                "message": "hello",
                "payload": {},
            }
        ],
    )
    terminal = await repositories.acquire_run_event_terminal_drain_fence(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-a",
    )

    assert receipt == {
        "accepted": True,
        "duplicate": True,
        "id": "evb_1",
        "event_ids_json": ["evt_1", "evt_2"],
        "first_sequence": 4,
        "through_sequence": 5,
        "callback_received_at": "2026-08-09T00:00:00Z",
    }
    assert terminal == {"accepted": True, "duplicate": False}


@pytest.mark.asyncio
async def test_legacy_receipt_upgrades_only_when_every_persisted_field_matches():
    event = ledger.LedgerEvent(
        event_type="execution_step",
        stage="execution",
        message="",
        payload={"visible_to_user": True, "safe": "bounded"},
    )
    legacy = {"id": "legacy-batch", "event_ids_json": ["evt-legacy"]}
    persisted = {
        "id": "evt-legacy",
        **ledger._persisted_event_shape(event, run_id="run-a"),
    }
    upgraded = {
        "id": "legacy-batch",
        "event_ids_json": ["evt-legacy"],
        "first_sequence": 4,
        "through_sequence": 4,
        "payload_digest": "new-digest",
        "projection_version": "callback-receipt-v2.1",
        "item_count": 1,
        "callback_received_at": "2026-08-09T00:00:00Z",
    }
    conn = _ScriptedConnection(
        [
            _ScriptedCursor(rows=[persisted]),
            _ScriptedCursor(row=upgraded),
        ]
    )

    result = await ledger._upgrade_matching_legacy_batch_receipt(
        conn,
        row=legacy,
        tenant_id="tenant-a",
        run_id="run-a",
        events=[event],
        payload_digest="new-digest",
        projection_version="callback-receipt-v2.1",
    )

    assert result == upgraded


@pytest.mark.asyncio
async def test_legacy_receipt_rejects_one_persisted_field_mismatch():
    event = ledger.LedgerEvent(
        event_type="execution_step",
        stage="execution",
        payload={"visible_to_user": True, "safe": "bounded"},
    )
    persisted = {
        "id": "evt-legacy",
        **ledger._persisted_event_shape(event, run_id="run-a"),
        "payload_json": {"visible_to_user": True, "safe": "changed"},
    }
    conn = _ScriptedConnection([_ScriptedCursor(rows=[persisted])])

    with pytest.raises(
        ledger.RunEventLedgerConflictError, match="run_event_batch_conflict"
    ):
        await ledger._upgrade_matching_legacy_batch_receipt(
            conn,
            row={"id": "legacy-batch", "event_ids_json": ["evt-legacy"]},
            tenant_id="tenant-a",
            run_id="run-a",
            events=[event],
            payload_digest="new-digest",
            projection_version="callback-receipt-v2.1",
        )


@pytest.mark.asyncio
async def test_batch_event_validation_is_strict_and_ledger_conflicts_only_are_translated(
    monkeypatch,
):
    conn = _Connection()
    called = False

    async def append_batch(_conn, **_kwargs):
        nonlocal called
        called = True
        raise ledger.RunEventLedgerConflictError("terminal_drain_already_consumed")

    monkeypatch.setattr(run_event_repository._ledger, "append_batch", append_batch)

    with pytest.raises(ValueError, match="run_event_payload_invalid"):
        await repositories.append_event_batch(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id="batch-a",
            events=[
                {
                    "event_type": "assistant_delta",
                    "stage": "streaming",
                    "message": "hello",
                    "payload": ["bad"],
                }
            ],
        )
    assert called is False

    with pytest.raises(
        repositories.RepositoryConflictError, match="terminal_drain_already_consumed"
    ):
        await repositories.append_event_batch(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id="batch-a",
            events=[
                {
                    "event_type": "assistant_delta",
                    "stage": "streaming",
                    "message": "hello",
                    "payload": {},
                }
            ],
        )


@pytest.mark.asyncio
async def test_terminal_lease_lookup_is_exactly_scoped_and_locked():
    conn = _Connection()

    await repositories.list_terminal_sandbox_runtime_leases_for_attempt(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        release_reason="run_completed",
    )

    statement, params = conn.calls[-1]
    assert "status = 'released'" in statement
    assert "release_reason = %s" in statement
    assert "for update" in statement
    assert params == ("tenant-a", "run-a", "attempt-a", "run_completed")


@pytest.mark.asyncio
async def test_list_run_events_delegates_to_the_durable_cursor_reader_without_sql(
    monkeypatch,
):
    conn = _Connection()
    observed = []
    adapter_rows = {
        0: (
            MappingProxyType(
                {"id": "evt-1", "sequence": 1, "event_type": "run_started"}
            ),
            MappingProxyType(
                {"id": "evt-2", "sequence": 2, "event_type": "assistant_delta"}
            ),
        ),
        7: (
            MappingProxyType(
                {"id": "evt-8", "sequence": 8, "event_type": "assistant_delta"}
            ),
        ),
    }

    async def read_rows(received_conn, *, tenant_id, cursor, limit):
        observed.append((received_conn, tenant_id, cursor, limit))
        return adapter_rows[cursor.sequence]

    monkeypatch.setattr(run_event_repository._ledger, "read_event_rows", read_rows)

    unbounded = await repositories.list_run_events(
        conn, tenant_id="tenant-a", run_id="run-a"
    )
    incremental = await repositories.list_run_events(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        after_sequence=7,
        limit=2,
    )

    assert observed == [
        (conn, "tenant-a", RunCursor("run-a", 0), None),
        (conn, "tenant-a", RunCursor("run-a", 7), 2),
    ]
    assert unbounded == [
        {"id": "evt-1", "sequence": 1, "event_type": "run_started"},
        {"id": "evt-2", "sequence": 2, "event_type": "assistant_delta"},
    ]
    assert incremental == [
        {"id": "evt-8", "sequence": 8, "event_type": "assistant_delta"}
    ]
    assert all(isinstance(row, dict) for row in [*unbounded, *incremental])
    assert conn.calls == []


def test_run_event_schema_declares_repairable_composite_ledger_authority():
    content = (
        Path(repositories.__file__).with_name("schema.sql").read_text(encoding="utf-8")
    )

    assert "unique (tenant_id, id)" in content
    assert "create table if not exists run_event_cursors" in content
    assert "create table if not exists run_event_batches" in content
    assert "create table if not exists run_event_terminal_drains" in content
    assert (
        "create unique index if not exists uq_run_events_tenant_run_sequence" in content
    )
    assert "foreign key (tenant_id, run_id) references runs(tenant_id, id)" in content
    assert "unique (tenant_id, run_id, attempt_id, batch_id)" in content
    assert "primary key (tenant_id, run_id, attempt_id)" in content
    assert "row_number() over" in content
    assert "lock table run_events in share row exclusive mode" in content
    assert "foreign key (tenant_id, run_id) references runs(tenant_id, id)" in content


def test_run_event_schema_locks_for_missing_current_schema_unique_index_before_repair():
    schema = (
        Path(repositories.__file__).with_name("schema.sql").read_text(encoding="utf-8")
    )
    migration = schema[
        schema.index("declare\n  unique_index_present boolean;") : schema.index(
            "create table if not exists run_tool_permission_requests"
        )
    ]

    assert (
        "to_regclass(format('%I.%I', current_schema(), 'uq_run_events_tenant_run_sequence'))"
        in migration
    )
    assert "indexes.indisunique" in migration
    assert "indexes.indisvalid" in migration
    assert migration.count("not unique_index_present or exists") == 2
    assert migration.index("not unique_index_present or exists") < migration.index(
        "lock table run_events in share row exclusive mode"
    )
    assert migration.index(
        "lock table run_events in share row exclusive mode"
    ) < migration.index("with affected_groups as")
    assert migration.index(
        "lock table run_events in share row exclusive mode"
    ) < migration.index(
        "create unique index if not exists uq_run_events_tenant_run_sequence"
    )
    assert migration.index(
        "create unique index if not exists uq_run_events_tenant_run_sequence"
    ) < migration.index("insert into run_event_cursors")


def test_run_event_schema_retains_every_a1_ledger_written_column():
    schema = (
        Path(repositories.__file__).with_name("schema.sql").read_text(encoding="utf-8")
    )
    ledger_source = Path(run_event_repository._ledger.__file__).read_text(
        encoding="utf-8"
    )
    required_columns = {
        "run_events": (
            "id",
            "tenant_id",
            "run_id",
            "trace_id",
            "schema_version",
            "sequence",
            "event_type",
            "stage",
            "message",
            "severity",
            "visible_to_user",
            "error_code",
            "latency_ms",
            "input_token_count",
            "output_token_count",
            "total_token_count",
            "estimated_cost_minor",
            "payload_json",
        ),
        "run_event_cursors": ("tenant_id", "run_id", "next_sequence", "updated_at"),
        "run_event_batches": (
            "id",
            "tenant_id",
            "run_id",
            "attempt_id",
            "batch_id",
            "event_ids_json",
            "first_sequence",
            "through_sequence",
            "callback_received_at",
            "durable_committed_at",
        ),
        "run_event_terminal_drains": ("tenant_id", "run_id", "attempt_id", "batch_id"),
    }

    for table, columns in required_columns.items():
        table_start = schema.index(f"create table if not exists {table}")
        table_end = schema.index("\n);", table_start)
        table_definition = schema[table_start:table_end]
        assert f"{table}" in ledger_source
        for column in columns:
            assert column in table_definition

    assert "durable_committed_at = now()" in ledger_source
    assert "callback_received_at timestamptz not null default now()" in schema
    assert "durable_committed_at timestamptz" in schema


def test_repository_dependency_direction_keeps_ledger_adapter_independent():
    implementation = Path(run_event_repository.__file__).read_text(encoding="utf-8")

    assert "from app import repositories" not in implementation
    assert "from app.streaming import postgres as _ledger" in implementation
