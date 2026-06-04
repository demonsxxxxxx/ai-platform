from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.settings import get_settings


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def connect() -> AsyncConnection:
    settings = get_settings()
    return await AsyncConnection.connect(settings.database_url, row_factory=dict_row)


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    conn = await connect()
    try:
        async with conn.transaction():
            yield conn
    finally:
        await conn.close()


async def apply_schema() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with transaction() as conn:
        await conn.execute(sql)
