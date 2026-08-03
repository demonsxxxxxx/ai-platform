import asyncio
import copy
import json
from pathlib import Path

import pytest

from app.streaming import postgres
from app.streaming.authority import RunCursor, event_page, parse_last_event_id


def _row(sequence: int, event_type: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": f"evt-{sequence}",
        "sequence": sequence,
        "event_type": event_type,
        "stage": "answer" if event_type == "assistant_delta" else "runtime",
        "visible_to_user": True,
        "payload_json": {},
    }
    row.update(overrides)
    return row


def test_last_event_id_is_a_strict_run_bound_cursor_value():
    cursor = parse_last_event_id("run-a:42", run_id="run-a")

    assert cursor == RunCursor(run_id="run-a", sequence=42)
    assert cursor.event_id == "run-a:42"
    assert parse_last_event_id("run-b:42", run_id="run-a") is None
    assert parse_last_event_id("run-a:-1", run_id="run-a") is None
    assert parse_last_event_id("run-a:01", run_id="run-a") is None
    assert parse_last_event_id("evt-42", run_id="run-a") is None
    with pytest.raises(ValueError, match="run_cursor_sequence_invalid"):
        RunCursor(run_id="run-a", sequence=-1)
    with pytest.raises(ValueError, match="run_cursor_sequence_invalid"):
        RunCursor(run_id="run-a", sequence=True)


def test_postgres_adapter_is_not_coupled_to_app_repositories():
    source = Path(postgres.__file__).read_text(encoding="utf-8")

    assert "app.repositories" not in source


def test_postgres_adapter_is_explicitly_psycopg_only():
    source = Path(postgres.__file__).read_text(encoding="utf-8")

    assert "from psycopg import AsyncConnection" in source
    assert "asyncpg" not in source
    assert "RunEventSqlConnection" not in source


def test_page_advances_over_hidden_rows_without_exposing_payloads_or_duplicates():
    rows = [
        _row(7, "assistant_delta", payload_json={"delta": "one", "internal": "not public"}),
        _row(8, "executor_callback", visible_to_user=False),
        _row(9, "assistant_delta", visible_to_user=False, payload_json={"delta": "hidden"}),
        _row(10, "assistant_delta", payload_json={"delta": "two"}),
        _row(10, "assistant_delta", payload_json={"delta": "duplicate"}),
    ]
    page = event_page(
        cursor=RunCursor(run_id="run-a", sequence=6),
        rows=rows,
    )

    assert [(event.cursor.sequence, event.delta) for event in page.events] == [(7, "one"), (10, "two")]
    assert page.through_cursor == RunCursor(run_id="run-a", sequence=10)
    assert not hasattr(page.events[0], "row")
    replay = event_page(cursor=page.through_cursor, rows=rows)
    assert replay.events == ()
    assert replay.through_cursor == page.through_cursor


def test_terminal_control_is_delivered_only_after_the_page_drains_later_deltas():
    page = event_page(
        cursor=RunCursor(run_id="run-a", sequence=10),
        rows=[
            _row(11, "run_succeeded"),
            _row(12, "assistant_delta", payload_json={"delta": "durable after terminal"}),
        ],
    )

    assert [(event.cursor.sequence, event.delta) for event in page.events] == [(12, "durable after terminal")]
    assert page.terminal is not None
    assert page.terminal.cursor == RunCursor(run_id="run-a", sequence=11)
    assert page.terminal.drain_through == RunCursor(run_id="run-a", sequence=12)


def test_malformed_or_private_delta_fails_closed_while_the_cursor_advances():
    page = event_page(
        cursor=RunCursor(run_id="run-a", sequence=1),
        rows=[
            _row(2, "assistant_delta", payload_json={"private_payload": "secret", "delta": "never"}),
            _row(3, "assistant_delta", payload_json={"delta": 7}),
        ],
    )

    assert page.events == ()
    assert page.through_cursor == RunCursor(run_id="run-a", sequence=3)


class _Cursor:
    def __init__(self, row: dict[str, object] | None = None, rows: list[dict[str, object]] | None = None):
        self._row = row
        self._rows = rows or []

    async def fetchone(self) -> dict[str, object] | None:
        return self._row

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _UnitOfWork:
    """Explicit in-process SQL protocol fake; it never impersonates a repository."""

    def __init__(self, *, fail_event_insert: bool = False):
        self.cursor_next: dict[tuple[str, str], int] = {}
        self.batches: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self.drains: dict[tuple[str, str, str], str] = {}
        self.events: dict[str, dict[str, object]] = {}
        self.read_rows: list[dict[str, object]] = []
        self.fail_event_insert = fail_event_insert
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self._lock = asyncio.Lock()

    async def execute(self, statement: str, params: tuple[object, ...]) -> _Cursor:
        sql = " ".join(statement.lower().split())
        self.executed.append((sql, params))
        if "insert into run_event_cursors" in sql:
            self.cursor_next.setdefault((str(params[0]), str(params[1])), 1)
            return _Cursor()
        if "update run_event_cursors" in sql:
            async with self._lock:
                key = (str(params[0]), str(params[1]))
                sequence = self.cursor_next[key]
                self.cursor_next[key] = sequence + 1
            return _Cursor({"sequence": sequence})
        if "insert into run_event_batches" in sql:
            key = (str(params[1]), str(params[2]), str(params[3]), str(params[4]))
            if key in self.batches:
                return _Cursor()
            self.batches[key] = {
                "id": params[0],
                "event_ids_json": [],
                "first_sequence": None,
                "through_sequence": None,
            }
            return _Cursor({"id": str(params[0])})
        if "select id, event_ids_json, first_sequence, through_sequence" in sql:
            return _Cursor(self.batches[(str(params[0]), str(params[1]), str(params[2]), str(params[3]))])
        if "insert into run_events" in sql:
            if self.fail_event_insert:
                raise RuntimeError("write_failed")
            self.events[str(params[0])] = {"id": str(params[0]), "sequence": int(params[5])}
            return _Cursor()
        if sql.startswith("select sequence from run_events"):
            return _Cursor(self.events[str(params[1])])
        if "update run_event_batches" in sql:
            for receipt in self.batches.values():
                if receipt["id"] == params[3]:
                    receipt.update(
                        event_ids_json=json.loads(str(params[0])),
                        first_sequence=params[1],
                        through_sequence=params[2],
                    )
                    return _Cursor(receipt)
            raise AssertionError("unknown batch receipt")
        if "insert into run_event_terminal_drains" in sql:
            key = (str(params[0]), str(params[1]), str(params[2]))
            if key in self.drains:
                return _Cursor()
            self.drains[key] = str(params[3])
            return _Cursor({"batch_id": str(params[3])})
        if "select batch_id from run_event_terminal_drains" in sql:
            key = (str(params[0]), str(params[1]), str(params[2]))
            batch_id = self.drains.get(key)
            return _Cursor({"batch_id": batch_id} if batch_id else None)
        if "from run_events" in sql:
            return _Cursor(rows=self.read_rows)
        raise AssertionError(statement)

    async def transaction(self, operation):
        snapshot = copy.deepcopy((self.cursor_next, self.batches, self.drains, self.events))
        try:
            return await operation()
        except Exception:
            self.cursor_next, self.batches, self.drains, self.events = snapshot
            raise


def test_postgres_adapter_allocates_unique_sequences_without_repository_dependency():
    async def append_concurrently() -> list[int]:
        conn = _UnitOfWork()
        await asyncio.gather(
            *[
                postgres.append_event(
                    conn,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    event=postgres.LedgerEvent(
                        event_type="assistant_delta",
                        stage="answer",
                        payload={"delta": str(index)},
                    ),
                )
                for index in range(16)
            ]
        )
        return [int(row["sequence"]) for row in conn.events.values()]

    assert sorted(asyncio.run(append_concurrently())) == list(range(1, 17))


def test_batch_receipt_replay_and_rollback_are_owned_by_the_transaction_protocol():
    async def exercise() -> tuple[dict[str, object], dict[str, object], _UnitOfWork, _UnitOfWork]:
        conn = _UnitOfWork()
        first = await conn.transaction(
            lambda: postgres.append_batch(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                batch_id="batch-a",
                events=[postgres.LedgerEvent(event_type="assistant_delta", stage="answer", payload={"delta": "once"})],
            )
        )
        replay = await conn.transaction(
            lambda: postgres.append_batch(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                batch_id="batch-a",
                events=[],
            )
        )
        failing = _UnitOfWork(fail_event_insert=True)
        with pytest.raises(RuntimeError, match="write_failed"):
            await failing.transaction(
                lambda: postgres.append_batch(
                    failing,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="batch-a",
                    events=[
                        postgres.LedgerEvent(
                            event_type="assistant_delta", stage="answer", payload={"delta": "no receipt"}
                        )
                    ],
                )
            )
        return first, replay, conn, failing

    first, replay, conn, failing = asyncio.run(exercise())

    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.event_ids == first.event_ids
    assert len(conn.events) == 1
    assert failing.batches == {}
    assert failing.events == {}


def test_batch_receipt_identity_is_exact_across_tenant_run_and_attempt():
    async def append(
        conn: _UnitOfWork,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> postgres.BatchReceipt:
        return await conn.transaction(
            lambda: postgres.append_batch(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                batch_id="shared-batch",
                events=[postgres.LedgerEvent(event_type="assistant_delta", stage="answer")],
            )
        )

    async def exercise() -> tuple[postgres.BatchReceipt, postgres.BatchReceipt, list[postgres.BatchReceipt], _UnitOfWork]:
        conn = _UnitOfWork()
        first = await append(conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a")
        replay = await append(conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a")
        isolated = [
            await append(conn, tenant_id="tenant-b", run_id="run-a", attempt_id="attempt-a"),
            await append(conn, tenant_id="tenant-a", run_id="run-b", attempt_id="attempt-a"),
            await append(conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-b"),
        ]
        return first, replay, isolated, conn

    first, replay, isolated, conn = asyncio.run(exercise())

    assert first.duplicate is False
    assert replay.duplicate is True
    assert all(receipt.duplicate is False for receipt in isolated)
    assert len(conn.batches) == 4


def test_terminal_fence_is_idempotent_for_one_batch_and_rejects_a_competing_batch():
    async def exercise() -> tuple[dict[str, object], dict[str, object]]:
        conn = _UnitOfWork()
        first = await postgres.acquire_terminal_drain_fence(
            conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-a"
        )
        replay = await postgres.acquire_terminal_drain_fence(
            conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-a"
        )
        with pytest.raises(postgres.RunEventLedgerConflictError, match="terminal_drain_already_consumed"):
            await postgres.acquire_terminal_drain_fence(
                conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-b"
            )
        return first, replay

    first, replay = asyncio.run(exercise())

    assert first.duplicate is False
    assert replay.duplicate is True


def test_terminal_fence_keys_are_exact_across_tenant_run_and_attempt():
    async def claim(
        conn: _UnitOfWork,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        batch_id: str,
    ) -> postgres.TerminalDrainReceipt:
        return await postgres.acquire_terminal_drain_fence(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            batch_id=batch_id,
        )

    async def exercise() -> tuple[list[postgres.TerminalDrainReceipt], _UnitOfWork]:
        conn = _UnitOfWork()
        await claim(conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-a")
        with pytest.raises(postgres.RunEventLedgerConflictError, match="terminal_drain_already_consumed"):
            await claim(conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", batch_id="batch-b")
        isolated = [
            await claim(conn, tenant_id="tenant-b", run_id="run-a", attempt_id="attempt-a", batch_id="batch-b"),
            await claim(conn, tenant_id="tenant-a", run_id="run-b", attempt_id="attempt-a", batch_id="batch-b"),
            await claim(conn, tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-b", batch_id="batch-b"),
        ]
        return isolated, conn

    isolated, conn = asyncio.run(exercise())

    assert all(receipt.duplicate is False for receipt in isolated)
    assert len(conn.drains) == 4


def test_incremental_page_read_uses_the_run_bound_cursor_and_limit():
    async def read_page() -> tuple[list[dict[str, object]], _UnitOfWork]:
        conn = _UnitOfWork()
        conn.read_rows = [_row(9, "assistant_delta")]
        rows = await postgres.read_event_rows(
            conn,
            cursor=RunCursor(run_id="run-a", sequence=8),
            tenant_id="tenant-a",
            limit=25,
        )
        return list(rows), conn

    rows, conn = asyncio.run(read_page())

    assert rows == [_row(9, "assistant_delta")]
    statement, params = conn.executed[-1]
    assert "sequence > %s" in statement
    assert params == ("tenant-a", "run-a", 8, 25)
