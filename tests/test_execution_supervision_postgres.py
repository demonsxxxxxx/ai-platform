import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import schema_migrations
from app.execution.domain.supervision import SupervisionDomain
from app.execution.infrastructure import supervision_postgres


@pytest.mark.asyncio
async def test_two_worker_claims_drain_before_domain_switch_and_expiry_stays_in_flight():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"execution_supervision_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            connection = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with connection.transaction():
                    yield connection
            finally:
                await connection.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn,
                autocommit=True,
                options=f"-c search_path={schema}",
                row_factory=dict_row,
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )

        async with transaction_factory() as connection:
            candidates = await supervision_postgres.observe_persisted_candidates(
                connection,
                now=datetime.now(timezone.utc),
                batch_limits={domain: 2 for domain in SupervisionDomain},
                probe_stale_seconds=45,
                settlement_claim_stale_seconds=300,
            )
            assert candidates == ()
            cursor = await connection.execute(
                "select count(*) as legacy_count from execution_supervision_domains where mode = 'legacy'"
            )
            assert (await cursor.fetchone())["legacy_count"] == len(SupervisionDomain)

        observed_ids = []
        page_query = """
          with due as (
            select candidate_id as scan_key, candidate_id,
                   'observe'::text as action,
                   '2026-10-25 12:00:00+00'::timestamptz as deadline_at,
                   100::integer as priority, '{}'::jsonb as facts
            from unnest(array['a', 'b', 'c', 'd', 'e']::text[]) as due_ids(candidate_id)
          )
          select * from due
          order by (scan_key > coalesce(%s, '')) desc, scan_key
          limit %s
        """
        for _ in range(3):
            async with transaction_factory() as connection:
                page = await supervision_postgres._scan_domain(
                    connection,
                    domain=SupervisionDomain.CLEANUP,
                    limit=2,
                    query=page_query,
                    parameters=(),
                )
                observed_ids.extend(candidate.candidate_id for candidate in page)
        assert len(set(observed_ids)) == 5

        async def claim(worker_id, ttl=60):
            async with transaction_factory() as connection:
                return await supervision_postgres.claim_domain_work(
                    connection,
                    domain=SupervisionDomain.DELIVERY,
                    owner_mode="legacy",
                    worker_id=worker_id,
                    ttl_seconds=ttl,
                )

        first, second = await asyncio.gather(claim("legacy-a"), claim("legacy-b"))
        assert first is not None and second is not None
        async with transaction_factory() as connection:
            drain_version = await supervision_postgres.request_domain_drain(
                connection,
                domain=SupervisionDomain.DELIVERY,
                expected_version=1,
            )
        assert drain_version == 2

        async with transaction_factory() as connection:
            assert not await supervision_postgres.claim_is_current(connection, first)
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.DELIVERY,
                expected_version=drain_version,
            ) is None
            assert await supervision_postgres.release_domain_work(connection, first)
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.DELIVERY,
                expected_version=drain_version,
            ) is None
            assert await supervision_postgres.release_domain_work(connection, second)
            activated_version = await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.DELIVERY,
                expected_version=drain_version,
            )
        assert activated_version == 3

        async with transaction_factory() as connection:
            assert await supervision_postgres.claim_domain_work(
                connection,
                domain=SupervisionDomain.DELIVERY,
                owner_mode="legacy",
                worker_id="legacy-too-late",
                ttl_seconds=60,
            ) is None
            unified = await supervision_postgres.claim_domain_work(
                connection,
                domain=SupervisionDomain.DELIVERY,
                owner_mode="unified",
                worker_id="unified-a",
                ttl_seconds=60,
            )
            assert unified is not None
            assert await supervision_postgres.claim_domain_work(
                connection,
                domain=SupervisionDomain.DELIVERY,
                owner_mode="unified",
                worker_id="unified-b",
                ttl_seconds=60,
            ) is None
            assert await supervision_postgres.release_domain_work(connection, unified)
            unified_drain = await supervision_postgres.request_domain_drain(
                connection,
                domain=SupervisionDomain.DELIVERY,
                expected_version=activated_version,
                expected_mode="unified",
            )
            assert unified_drain == 4
            assert await supervision_postgres.return_domain_to_legacy(
                connection,
                domain=SupervisionDomain.DELIVERY,
                expected_version=unified_drain,
            ) == 5

        async with transaction_factory() as connection:
            expired = await supervision_postgres.claim_domain_work(
                connection,
                domain=SupervisionDomain.PROBE,
                owner_mode="legacy",
                worker_id="probe-a",
                ttl_seconds=1,
            )
            assert expired is not None
            await connection.execute(
                "update execution_supervision_claims set started_at = clock_timestamp() - interval '2 seconds', expires_at = clock_timestamp() - interval '1 second' where claim_token = %s",
                (expired["claim_token"],),
            )
            assert not await supervision_postgres.claim_is_current(connection, expired)
            probe_drain = await supervision_postgres.request_domain_drain(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_version=1,
            )
            assert probe_drain == 2
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_version=probe_drain,
            ) is None
            assert await supervision_postgres.release_domain_work(connection, expired)
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_version=probe_drain,
            ) == 3
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()
