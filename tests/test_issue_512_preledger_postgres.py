"""Real-PostgreSQL interleavings for #512's pre-ledger recovery claim."""

import asyncio
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import repositories


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
_TENANT_ID = "tenant-a"
_USER_ID = "user-a"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
    await conn.commit()


async def _backend_pid(conn: psycopg.AsyncConnection) -> int:
    cursor = await conn.execute("select pg_backend_pid() as pid")
    return int((await cursor.fetchone())["pid"])


async def _wait_until_blocked(
    observer: psycopg.AsyncConnection,
    *,
    waiter_pid: int,
    blocker_pid: int,
) -> None:
    """Observe PostgreSQL's lock graph instead of relying on wall-clock sleeps."""

    for _ in range(200):
        cursor = await observer.execute(
            "select %s = any(pg_blocking_pids(%s)) as is_blocked",
            (blocker_pid, waiter_pid),
        )
        if (await cursor.fetchone())["is_blocked"]:
            return
        await asyncio.sleep(0)
    raise AssertionError("second claim never blocked on the first transaction")


async def _claim(
    conn: psycopg.AsyncConnection,
    *,
    submission_id: str,
    fingerprint: str,
) -> tuple[dict[str, object], bool]:
    return await repositories.claim_chat_submission(
        conn,
        tenant_id=_TENANT_ID,
        user_id=_USER_ID,
        submission_id=submission_id,
        workspace_id=None,
        request_fingerprint_sha256=fingerprint,
    )


async def _started_claim(
    started: asyncio.Event,
    conn: psycopg.AsyncConnection,
    *,
    submission_id: str,
    fingerprint: str,
) -> tuple[dict[str, object], bool]:
    started.set()
    return await _claim(conn, submission_id=submission_id, fingerprint=fingerprint)


@pytest.mark.asyncio
async def test_preledger_recovery_claim_interleavings_are_atomic_in_postgres():
    """One unique ledger row decides all original/recovery races in the database."""

    dsn = _postgres_dsn()
    schema_name = f"issue_512_{uuid.uuid4().hex}"
    schema_sql = Path("app/schema.sql").read_text(encoding="utf-8")
    observer = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    first: psycopg.AsyncConnection | None = None
    second: psycopg.AsyncConnection | None = None
    waiter_tasks: list[asyncio.Task[tuple[dict[str, object], bool]]] = []
    original_fingerprint = "a" * 64
    recovery_fingerprint = "b" * 64
    try:
        await observer.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await observer.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await observer.execute(schema_sql)
        await observer.execute(
            "insert into tenants(id, name) values (%s, %s)",
            (_TENANT_ID, "Tenant A"),
        )
        await observer.execute(
            "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
            (_USER_ID, _TENANT_ID, "User A"),
        )
        first = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        second = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
        await _set_search_path(first, schema_name)
        await _set_search_path(second, schema_name)
        first_pid = await _backend_pid(first)
        second_pid = await _backend_pid(second)

        # The original request claims and finalizes before recovery can commit:
        # recovery observes that immutable original fingerprint and cannot retire it.
        original_wins_id = str(uuid.uuid4())
        _, created = await _claim(
            first,
            submission_id=original_wins_id,
            fingerprint=original_fingerprint,
        )
        assert created is True
        await repositories.finalize_chat_submission(
            first,
            tenant_id=_TENANT_ID,
            user_id=_USER_ID,
            submission_id=original_wins_id,
            state="accepted_pending_enqueue",
            outcome_json={
                "session_id": "session-original",
                "run_id": "run-original",
                "status": "accepted_pending_enqueue",
                "submission_id": original_wins_id,
            },
        )
        started = asyncio.Event()
        recovery_waiter = asyncio.create_task(
            _started_claim(
                started,
                second,
                submission_id=original_wins_id,
                fingerprint=recovery_fingerprint,
            )
        )
        waiter_tasks.append(recovery_waiter)
        await started.wait()
        await _wait_until_blocked(observer, waiter_pid=second_pid, blocker_pid=first_pid)
        assert not recovery_waiter.done()
        await first.commit()
        observed_original, created = await asyncio.wait_for(recovery_waiter, timeout=2)
        assert created is False
        assert observed_original["request_fingerprint_sha256"] == original_fingerprint
        assert observed_original["state"] == "accepted_pending_enqueue"
        await second.commit()

        # A recovery tombstone that commits first is equally immutable: the
        # delayed original path may observe it but must not create a run row.
        recovery_wins_id = str(uuid.uuid4())
        _, created = await _claim(
            first,
            submission_id=recovery_wins_id,
            fingerprint=recovery_fingerprint,
        )
        assert created is True
        await repositories.finalize_chat_submission(
            first,
            tenant_id=_TENANT_ID,
            user_id=_USER_ID,
            submission_id=recovery_wins_id,
            state="rejected_before_persist",
            submission_disposition="rejected_before_persist",
            rejection_code="chat_submission_retired_before_ledger",
        )
        started = asyncio.Event()
        original_waiter = asyncio.create_task(
            _started_claim(
                started,
                second,
                submission_id=recovery_wins_id,
                fingerprint=original_fingerprint,
            )
        )
        waiter_tasks.append(original_waiter)
        await started.wait()
        await _wait_until_blocked(observer, waiter_pid=second_pid, blocker_pid=first_pid)
        assert not original_waiter.done()
        await first.commit()
        observed_recovery, created = await asyncio.wait_for(original_waiter, timeout=2)
        assert created is False
        assert observed_recovery["request_fingerprint_sha256"] == recovery_fingerprint
        assert observed_recovery["state"] == "rejected_before_persist"
        await second.commit()

        # A rolled-back original claim leaves no ledger row. Only then may the
        # blocked recovery claim create and finalize the tombstone.
        original_rolls_back_id = str(uuid.uuid4())
        _, created = await _claim(
            first,
            submission_id=original_rolls_back_id,
            fingerprint=original_fingerprint,
        )
        assert created is True
        started = asyncio.Event()
        recovery_after_rollback = asyncio.create_task(
            _started_claim(
                started,
                second,
                submission_id=original_rolls_back_id,
                fingerprint=recovery_fingerprint,
            )
        )
        waiter_tasks.append(recovery_after_rollback)
        await started.wait()
        await _wait_until_blocked(observer, waiter_pid=second_pid, blocker_pid=first_pid)
        assert not recovery_after_rollback.done()
        await first.rollback()
        _, created = await asyncio.wait_for(recovery_after_rollback, timeout=2)
        assert created is True
        await repositories.finalize_chat_submission(
            second,
            tenant_id=_TENANT_ID,
            user_id=_USER_ID,
            submission_id=original_rolls_back_id,
            state="rejected_before_persist",
            submission_disposition="rejected_before_persist",
            rejection_code="chat_submission_retired_before_ledger",
        )
        await second.commit()
        final_recovery = await repositories.get_chat_submission(
            observer,
            tenant_id=_TENANT_ID,
            user_id=_USER_ID,
            submission_id=original_rolls_back_id,
        )
        assert final_recovery is not None
        assert final_recovery["request_fingerprint_sha256"] == recovery_fingerprint
        assert final_recovery["state"] == "rejected_before_persist"
    finally:
        for task in waiter_tasks:
            if not task.done():
                task.cancel()
        if waiter_tasks:
            await asyncio.gather(*waiter_tasks, return_exceptions=True)
        for conn in (first, second):
            if conn is not None:
                try:
                    await conn.rollback()
                finally:
                    await conn.close()
        await observer.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await observer.close()
