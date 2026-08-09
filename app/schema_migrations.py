"""Versioned, serialized PostgreSQL schema application and readiness checks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from app.db import SCHEMA_PATH, close_pool, transaction


TARGET_SCHEMA_VERSION = "2026.08.09.1"
MIGRATION_LOCK_ID = 7_226_391_831_505_901_103
CRITICAL_RELATIONS = (
    "schema_migrations",
    "runs",
    "run_events",
    "messages",
    "files",
    "artifacts",
    "audit_logs",
)


class SchemaMigrationError(RuntimeError):
    """The installed schema cannot be proven compatible with this build."""


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def schema_checksum(sql: str | None = None) -> str:
    return hashlib.sha256((sql if sql is not None else schema_sql()).encode("utf-8")).hexdigest()


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


async def apply_migrations(
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]] = transaction,
) -> dict[str, object]:
    """Apply the additive schema exactly once under a cluster-wide lock."""

    sql = schema_sql()
    checksum = schema_checksum(sql)
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
            return {"status": "current", "version": TARGET_SCHEMA_VERSION, "checksum_sha256": checksum}
        await conn.execute(sql)
        await conn.execute(
            """
            insert into schema_migrations(version, checksum_sha256)
            values (%s, %s)
            """,
            (TARGET_SCHEMA_VERSION, checksum),
        )
    return {"status": "applied", "version": TARGET_SCHEMA_VERSION, "checksum_sha256": checksum}


async def schema_status(conn: Any) -> dict[str, object]:
    checksum = schema_checksum()
    relation_placeholders = ", ".join(["%s"] * len(CRITICAL_RELATIONS))
    cursor = await conn.execute(
        f"""
        select
          exists (
            select 1 from schema_migrations
            where version = %s and checksum_sha256 = %s
          ) as ledger_current,
          coalesce(bool_and(to_regclass(relation_name) is not null), false) as contracts_current
        from unnest(array[{relation_placeholders}]::text[]) relation_name
        """,
        (TARGET_SCHEMA_VERSION, checksum, *CRITICAL_RELATIONS),
    )
    row = await cursor.fetchone() or {}
    ready = bool(row.get("ledger_current")) and bool(row.get("contracts_current"))
    return {
        "ready": ready,
        "target_version": TARGET_SCHEMA_VERSION,
        "checksum_sha256": checksum,
        "ledger_current": bool(row.get("ledger_current")),
        "contracts_current": bool(row.get("contracts_current")),
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
