import asyncio
from contextlib import asynccontextmanager
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import schema_migrations


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


def _transaction_factory(dsn: str, schema_name: str):
    @asynccontextmanager
    async def factory():
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with conn.transaction():
                yield conn
        finally:
            await conn.close()

    return factory


@pytest.mark.asyncio
async def test_real_postgres_concurrent_migrations_use_one_global_lock_and_ledger_row():
    dsn = _postgres_dsn()
    schema_name = f"schema_migration_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)

        first, second = await asyncio.gather(
            schema_migrations.apply_migrations(transaction_factory=factory),
            schema_migrations.apply_migrations(transaction_factory=factory),
        )

        assert {first["status"], second["status"]} == {"applied", "current"}
        cursor = await admin.execute(
            sql.SQL(
                "select version, checksum_sha256 from {}.schema_migrations"
            ).format(sql.Identifier(schema_name))
        )
        assert await cursor.fetchall() == [
            {
                "version": schema_migrations.TARGET_SCHEMA_VERSION,
                "checksum_sha256": schema_migrations.schema_checksum(),
            }
        ]
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
