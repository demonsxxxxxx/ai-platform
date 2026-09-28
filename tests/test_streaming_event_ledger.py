from __future__ import annotations

import json

import psycopg
import pytest

from app.streaming.application.callback_events_v4 import V4CallbackItem
from app.streaming.domain.run_events import RunCursor
from app.streaming.infrastructure import event_ledger_postgres as postgres
from app.streaming.infrastructure.v4 import append_callback_v4_rows
from app.streaming.redis import StreamAuthority
from tests.support.db_transactions import event_loop_policy as event_loop_policy
from tests.test_streaming_postgres import _connect, _temporary_ledger_schema


class _Result:
    def __init__(
        self,
        *,
        row: dict[str, object] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.row = row
        self.rows = rows or []

    async def fetchone(self) -> dict[str, object] | None:
        return self.row

    async def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _BatchConnection:
    """Small SQL protocol fake for checking adapter round trips."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.next_sequence: dict[tuple[str, str], int] = {}
        self.batches: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self.events: list[dict[str, object]] = []

    async def execute(self, statement: str, params: tuple[object, ...] = ()) -> _Result:
        normalized = " ".join(statement.lower().split())
        self.calls.append(normalized)
        if "insert into run_event_batches" in normalized:
            key = tuple(str(item) for item in params[1:5])
            if key in self.batches:
                return _Result()
            self.batches[key] = {
                "id": params[0],
                "event_ids_json": [],
                "first_sequence": None,
                "through_sequence": None,
                "payload_digest": params[5],
                "projection_version": params[6],
                "item_count": params[7],
                "callback_received_at": "2026-09-28T00:00:00Z",
            }
            return _Result(row={"id": str(params[0])})
        if "insert into run_event_cursors" in normalized:
            self.next_sequence.setdefault((str(params[0]), str(params[1])), 1)
            return _Result()
        if "update run_event_cursors" in normalized:
            if "next_sequence + %s" in normalized:
                count = int(params[0])
                tenant_id, run_id = str(params[1]), str(params[2])
            else:
                count = 1
                tenant_id, run_id = str(params[0]), str(params[1])
            key = (tenant_id, run_id)
            sequence = self.next_sequence[key]
            self.next_sequence[key] += count
            return _Result(row={"sequence": sequence})
        if "where id = any(%s::text[])" in normalized:
            wanted = {str(item) for item in params[0]}
            return _Result(
                rows=[event for event in self.events if str(event["id"]) in wanted]
            )
        if normalized.startswith("insert into run_events"):
            rows = []
            for offset in range(0, len(params), 18):
                values = params[offset : offset + 18]
                event = {
                    "id": str(values[0]),
                    "tenant_id": str(values[1]),
                    "run_id": str(values[2]),
                    "sequence": int(values[5]),
                    "event_type": str(values[6]),
                    "visible_to_user": values[10],
                    "payload_json": json.loads(str(values[17])),
                    "created_at": "2026-09-28T00:00:00Z",
                }
                self.events.append(event)
                rows.append(
                    {
                        "id": event["id"],
                        "sequence": event["sequence"],
                        "created_at": event["created_at"],
                    }
                )
            if len(rows) == 1:
                return _Result(row={"created_at": rows[0]["created_at"]})
            return _Result(rows=rows)
        if "select id, event_ids_json, first_sequence, through_sequence" in normalized:
            key = tuple(str(item) for item in params)
            return _Result(row=self.batches.get(key))
        if "update run_event_batches" in normalized:
            for receipt in self.batches.values():
                if receipt["id"] == params[3]:
                    receipt.update(
                        event_ids_json=json.loads(str(params[0])),
                        first_sequence=params[1],
                        through_sequence=params[2],
                    )
                    return _Result(row=receipt)
        raise AssertionError(f"unexpected SQL: {statement}")


def _events(count: int) -> tuple[postgres.LedgerEvent, ...]:
    return tuple(
        postgres.LedgerEvent(
            event_type="message.delta",
            stage="answer",
            payload={"delta": f"delta-{index}"},
        )
        for index in range(count)
    )


def _authority() -> StreamAuthority:
    return StreamAuthority(
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        tenant_scope="scope-a",
        stream_incarnation=2,
        state="confirmed",
        open_event_id="open-a",
        open_payload_bytes="{}",
        open_payload_digest="digest",
        authorization_epoch=1,
        revocation_state="active",
    )


def _callback_items(count: int) -> tuple[V4CallbackItem, ...]:
    return tuple(
        V4CallbackItem(
            callback_index=index,
            batch_index=index,
            event_type="message.delta",
            payload={"delta": f"public-{index}"},
            message_id="msg-public",
            source_run_id="run-a",
        )
        for index in range(count)
    )


@pytest.mark.asyncio
async def test_batch_sql_count_is_constant_and_receipts_keep_input_order():
    for count in (1, 100):
        conn = _BatchConnection()
        receipt = await postgres.append_batch(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id=f"batch-{count}",
            events=_events(count),
        )
        assert len(conn.calls) == 5
        assert (
            sum(
                statement.startswith("insert into run_events")
                for statement in conn.calls
            )
            == 1
        )
        assert receipt.first_cursor == RunCursor("run-a", 1)
        assert receipt.through_cursor == RunCursor("run-a", count)
        assert len(receipt.event_ids) == count
        assert [event["sequence"] for event in conn.events] == list(range(1, count + 1))
        assert [event["payload_json"]["delta"] for event in conn.events] == [
            f"delta-{index}" for index in range(count)
        ]

        replay = await postgres.append_batch(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            batch_id=f"batch-{count}",
            events=_events(count),
        )
        assert replay.duplicate is True
        assert replay.event_ids == receipt.event_ids
        assert len(conn.events) == count

        changed_events = _events(count)[:-1] + (
            postgres.LedgerEvent(
                event_type="message.delta",
                stage="answer",
                payload={"delta": "changed"},
            ),
        )
        with pytest.raises(
            postgres.RunEventLedgerConflictError, match="run_event_batch_conflict"
        ):
            await postgres.append_batch(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                batch_id=f"batch-{count}",
                events=changed_events,
            )


@pytest.mark.asyncio
async def test_callback_public_rows_use_one_lookup_and_one_ordered_insert_batch():
    conn = _BatchConnection()
    kwargs = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "batch_id": "public-batch",
        "authority": _authority(),
        "execution_lease_id": "lease-a",
    }
    items = _callback_items(100)

    rows = await append_callback_v4_rows(conn, items=items, **kwargs)

    assert len(conn.calls) == 4
    assert (
        sum("where id = any(%s::text[])" in statement for statement in conn.calls) == 1
    )
    assert (
        sum(statement.startswith("insert into run_events") for statement in conn.calls)
        == 1
    )
    assert [row["sequence"] for row in rows] == list(range(1, 101))
    assert [row["payload_json"]["delta"] for row in rows] == [
        f"public-{index}" for index in range(100)
    ]

    replay = await append_callback_v4_rows(conn, items=items, **kwargs)
    assert replay == rows
    assert len(conn.calls) == 5

    changed = items[:-1] + (
        V4CallbackItem(
            callback_index=99,
            batch_index=99,
            event_type="message.delta",
            payload={"delta": "changed"},
            message_id="msg-public",
            source_run_id="run-a",
        ),
    )
    with pytest.raises(ValueError, match="v4_callback_existing_row_conflict"):
        await append_callback_v4_rows(conn, items=changed, **kwargs)
    assert len(conn.calls) == 6
    assert len(conn.events) == 100


class _ExecuteCounter:
    def __init__(self, connection: psycopg.AsyncConnection) -> None:
        self.connection = connection
        self.statements: list[str] = []

    async def execute(self, statement: str, params: tuple[object, ...] = ()):
        self.statements.append(" ".join(statement.lower().split()))
        return await self.connection.execute(statement, params)


@pytest.mark.asyncio
async def test_real_postgres_batch_insert_is_ordered_contiguous_and_replayable():
    async with _temporary_ledger_schema() as (dsn, schema_name):
        connection = await _connect(dsn, schema_name)
        counted = _ExecuteCounter(connection)
        try:
            async with connection.transaction():
                first = await postgres.append_batch(
                    counted,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="batch-a",
                    events=_events(4),
                )
            assert len(counted.statements) == 5
            assert (
                sum(
                    item.startswith("insert into run_events")
                    for item in counted.statements
                )
                == 1
            )
            assert first.first_cursor == RunCursor("run-a", 1)
            assert first.through_cursor == RunCursor("run-a", 4)

            rows = await (
                await connection.execute(
                    "select id, sequence, payload_json from run_events order by sequence"
                )
            ).fetchall()
            assert [row["id"] for row in rows] == list(first.event_ids)
            assert [row["sequence"] for row in rows] == [1, 2, 3, 4]
            assert [row["payload_json"]["delta"] for row in rows] == [
                "delta-0",
                "delta-1",
                "delta-2",
                "delta-3",
            ]

            counted.statements.clear()
            async with connection.transaction():
                second = await postgres.append_batch(
                    counted,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="batch-b",
                    events=_events(2),
                )
            assert len(counted.statements) == 5
            assert second.first_cursor == RunCursor("run-a", 5)
            assert second.through_cursor == RunCursor("run-a", 6)

            counted.statements.clear()
            async with connection.transaction():
                replay = await postgres.append_batch(
                    counted,
                    tenant_id="tenant-a",
                    run_id="run-a",
                    attempt_id="attempt-a",
                    batch_id="batch-a",
                    events=_events(4),
                )
            assert replay.duplicate is True
            assert replay.event_ids == first.event_ids
            assert len(counted.statements) == 2

            counted.statements.clear()
            with pytest.raises(
                postgres.RunEventLedgerConflictError, match="run_event_batch_conflict"
            ):
                async with connection.transaction():
                    await postgres.append_batch(
                        counted,
                        tenant_id="tenant-a",
                        run_id="run-a",
                        attempt_id="attempt-a",
                        batch_id="batch-a",
                        events=_events(3)
                        + (
                            postgres.LedgerEvent(
                                event_type="message.delta",
                                stage="answer",
                                payload={"delta": "changed"},
                            ),
                        ),
                    )
            assert len(counted.statements) == 2
            count = await (
                await connection.execute("select count(*) as count from run_events")
            ).fetchone()
            assert count["count"] == 6
        finally:
            await connection.close()
