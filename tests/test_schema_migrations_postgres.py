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


def _index_connection_factory(dsn: str, schema_name: str):
    async def factory():
        return await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )

    return factory


@pytest.mark.asyncio
async def test_real_postgres_concurrent_migrations_use_one_global_lock_and_ledger_row():
    dsn = _postgres_dsn()
    schema_name = f"schema_migration_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)

        first, second = await asyncio.gather(
            schema_migrations.apply_migrations(
                transaction_factory=factory,
                index_connection_factory=index_factory,
            ),
            schema_migrations.apply_migrations(
                transaction_factory=factory,
                index_connection_factory=index_factory,
            ),
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
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage_sql",
    [
        "alter table runs drop column authz_policy_version",
        "alter table artifacts drop constraint chk_artifacts_lifecycle_state",
        "drop index idx_messages_tenant_session_created",
    ],
)
async def test_real_postgres_readiness_rejects_missing_critical_contract(damage_sql):
    dsn = _postgres_dsn()
    schema_name = f"schema_contract_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(damage_sql)

        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)

        assert status["ready"] is False
        assert status["contracts_current"] is False
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_readiness_rejects_index_ledger_checksum_drift():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_ledger_drift_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(
            """
            update schema_index_migrations
            set checksum_sha256 = repeat('0', 64)
            where index_name = 'idx_messages_tenant_session_created'
            """
        )

        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)

        assert status["ready"] is False
        assert status["index_ledger_current"] is False
        assert status["concurrent_index_definitions_current"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_readiness_rejects_and_migration_removes_orphan_index_ledger_row():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_ledger_orphan_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(
            """
            insert into schema_index_migrations(
              index_name, target_version, checksum_sha256, state, attempts
            ) values ('idx_orphan_interrupted', 'old-version', repeat('0', 64), 'building', 1)
            """
        )

        async with factory() as conn:
            damaged = await schema_migrations.schema_status(conn)
        assert damaged["ready"] is False
        assert damaged["index_ledger_current"] is False

        repaired = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        assert repaired["status"] == "applied"
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
        cursor = await admin.execute(
            "select count(*) as count from schema_index_migrations where index_name = 'idx_orphan_interrupted'"
        )
        assert (await cursor.fetchone())["count"] == 0
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_readiness_rejects_and_migration_repairs_wrong_index_definition():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_definition_drift_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute("drop index idx_messages_tenant_session_created")
        await admin.execute("create index idx_messages_tenant_session_created on messages(id)")

        async with factory() as conn:
            damaged = await schema_migrations.schema_status(conn)

        assert damaged["ready"] is False
        assert damaged["indexes_current"] is True
        assert damaged["concurrent_index_definitions_current"] is False

        repaired = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        assert repaired["status"] == "applied"
        async with factory() as conn:
            assert (await schema_migrations.schema_status(conn))["ready"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_concurrent_index_phase_recovers_after_ledger_interruption():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_resume_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute(
            """
            update schema_index_migrations
            set state = 'building', completed_at = null
            where index_name = 'idx_messages_tenant_session_created'
            """
        )

        result = await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )

        assert result["status"] == "applied"
        async with factory() as conn:
            status = await schema_migrations.schema_status(conn)
        assert status["ready"] is True
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_concurrent_index_build_does_not_block_message_writes():
    dsn = _postgres_dsn()
    schema_name = f"schema_index_writer_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    blocker = None
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        factory = _transaction_factory(dsn, schema_name)
        index_factory = _index_connection_factory(dsn, schema_name)
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=index_factory,
        )
        await admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await admin.execute("insert into users(id, tenant_id, display_name) values ('writer-user', 'default', 'Writer')")
        await admin.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('writer-agent', 'default', 'Writer', 'chat')"
        )
        await admin.execute(
            """
            insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
            values ('writer-session', 'default', 'default', 'writer-user', 'writer-agent', 'Writer', 'active')
            """
        )
        await admin.execute("drop index idx_messages_tenant_session_created")
        await admin.execute(
            """
            update schema_index_migrations
            set state = 'building', completed_at = null
            where index_name = 'idx_messages_tenant_session_created'
            """
        )

        blocker = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        blocker_tx = blocker.transaction()
        await blocker_tx.__aenter__()
        await blocker.execute(
            """
            insert into messages(id, tenant_id, session_id, role, content)
            values ('message-blocker', 'default', 'writer-session', 'user', 'blocker')
            """
        )
        migration_task = asyncio.create_task(
            schema_migrations.apply_migrations(
                transaction_factory=factory,
                index_connection_factory=index_factory,
            )
        )
        await asyncio.sleep(0.1)
        assert not migration_task.done()

        writer = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with writer.transaction():
                await asyncio.wait_for(
                    writer.execute(
                        """
                        insert into messages(id, tenant_id, session_id, role, content)
                        values ('message-writer', 'default', 'writer-session', 'user', 'writer')
                        """
                    ),
                    timeout=1,
                )
        finally:
            await writer.close()

        await blocker_tx.__aexit__(None, None, None)
        await blocker.close()
        blocker = None
        await asyncio.wait_for(migration_task, timeout=5)
        cursor = await admin.execute("select count(*) as count from messages")
        assert (await cursor.fetchone())["count"] == 2
    finally:
        if blocker is not None:
            await blocker.close()
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        await admin.close()
