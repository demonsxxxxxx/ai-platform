from pathlib import Path

import pytest

from app import repositories
from app import run_event_repository
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


@pytest.mark.asyncio
async def test_append_event_uses_ledger_and_preserves_generic_conflict_identity(monkeypatch):
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
    assert repositories.RepositoryConflictError is not ledger.RunEventLedgerConflictError


@pytest.mark.asyncio
async def test_batch_receipt_and_terminal_fence_keep_existing_dict_contract(monkeypatch):
    conn = _Connection()

    async def append_batch(_conn, **_kwargs):
        return ledger.BatchReceipt(
            receipt_id="evb_1",
            event_ids=("evt_1", "evt_2"),
            first_cursor=RunCursor("run-a", 4),
            through_cursor=RunCursor("run-a", 5),
            duplicate=True,
        )

    async def fence(_conn, **_kwargs):
        return ledger.TerminalDrainReceipt(duplicate=False)

    monkeypatch.setattr(run_event_repository._ledger, "append_batch", append_batch)
    monkeypatch.setattr(run_event_repository._ledger, "acquire_terminal_drain_fence", fence)

    receipt = await repositories.append_event_batch(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        batch_id="batch-a",
        events=[{"event_type": "assistant_delta", "stage": "streaming", "message": "hello", "payload": {}}],
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
    }
    assert terminal == {"accepted": True, "duplicate": False}


@pytest.mark.asyncio
async def test_batch_event_validation_is_strict_and_ledger_conflicts_only_are_translated(monkeypatch):
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
            events=[{"event_type": "assistant_delta", "stage": "streaming", "message": "hello", "payload": ["bad"]}],
        )
    assert called is False

    with pytest.raises(repositories.RepositoryConflictError, match="terminal_drain_already_consumed"):
        await repositories.append_event_batch(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id="batch-a",
            events=[{"event_type": "assistant_delta", "stage": "streaming", "message": "hello", "payload": {}}],
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
async def test_list_run_events_keeps_unbounded_legacy_read_semantics():
    conn = _Connection()

    assert await repositories.list_run_events(conn, tenant_id="tenant-a", run_id="run-a") == []

    statement, params = conn.calls[-1]
    assert "sequence > %s" not in statement
    assert "limit %s" not in statement
    assert params == ("tenant-a", "run-a")


def test_run_event_schema_declares_repairable_composite_ledger_authority():
    content = Path(repositories.__file__).with_name("schema.sql").read_text(encoding="utf-8")

    assert "unique (tenant_id, id)" in content
    assert "create table if not exists run_event_cursors" in content
    assert "create table if not exists run_event_batches" in content
    assert "create table if not exists run_event_terminal_drains" in content
    assert "create unique index if not exists uq_run_events_tenant_run_sequence" in content
    assert "foreign key (tenant_id, run_id) references runs(tenant_id, id)" in content
    assert "unique (tenant_id, run_id, attempt_id, batch_id)" in content
    assert "primary key (tenant_id, run_id, attempt_id)" in content
    assert "row_number() over" in content
    assert "lock table run_events in share row exclusive mode" in content
    assert "foreign key (tenant_id, run_id) references runs(tenant_id, id)" in content


def test_run_event_schema_retains_every_a1_ledger_written_column():
    schema = Path(repositories.__file__).with_name("schema.sql").read_text(encoding="utf-8")
    ledger_source = Path(run_event_repository._ledger.__file__).read_text(encoding="utf-8")
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
