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
from app.execution.application.supervision import run_current_domain_work, run_scheduled_domain_work
from app.execution.domain.supervision import SupervisionCapacities, SupervisionDomain
from app.execution.infrastructure import supervision_postgres


async def _record_call(target, value="called"):
    target.append(value)
    return value


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

        priority_query = """
          with due as (
            select priority::text || '.' || candidate_id as scan_key, candidate_id,
                   'probe_executor'::text as action,
                   '2026-10-25 12:00:00+00'::timestamptz as deadline_at,
                   priority, '{}'::jsonb as facts
            from (values ('cancel'::text, 0), ('normal-a'::text, 100),
                         ('normal-b'::text, 100)) as due_ids(candidate_id, priority)
          )
          select * from due
          order by (scan_key > coalesce(%s, '')) desc, scan_key
          limit %s
        """
        priority_ids = []
        for _ in range(4):
            async with transaction_factory() as connection:
                page = await supervision_postgres._scan_domain(
                    connection,
                    domain=SupervisionDomain.PROBE,
                    limit=1,
                    query=priority_query,
                    parameters=(),
                )
                priority_ids.extend(item.candidate_id for item in page)
        assert priority_ids[0] == "cancel"
        assert set(priority_ids) == {"cancel", "normal-a", "normal-b"}

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

        async def scheduled(operation, worker_id):
            return await run_scheduled_domain_work(
                operation,
                transaction_factory=transaction_factory,
                store=supervision_postgres,
                domain=SupervisionDomain.CLEANUP,
                worker_id=worker_id,
                ttl_seconds=60,
                capacities=SupervisionCapacities(
                    delivery=1, probe=1, settlement=1, cleanup=1
                ),
                supervision_enabled=True,
            )

        legacy_started = []
        legacy_completed = []
        both_legacy_started = asyncio.Event()
        finish_legacy = asyncio.Event()

        async def legacy_owner(worker_id):
            async def action():
                legacy_started.append(worker_id)
                if len(legacy_started) == 2:
                    both_legacy_started.set()
                await finish_legacy.wait()
                legacy_completed.append(worker_id)
                return worker_id

            return await scheduled(action, worker_id)

        legacy_tasks = [
            asyncio.create_task(legacy_owner("legacy-a")),
            asyncio.create_task(legacy_owner("legacy-b")),
        ]
        await asyncio.wait_for(both_legacy_started.wait(), timeout=2)
        async with transaction_factory() as connection:
            cleanup_drain = await supervision_postgres.request_domain_drain(
                connection,
                domain=SupervisionDomain.CLEANUP,
                expected_version=1,
            )
            assert cleanup_drain == 2
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.CLEANUP,
                expected_version=cleanup_drain,
            ) is None

        draining_calls = []
        assert await scheduled(
            lambda: _record_call(draining_calls), "draining-worker"
        ) is None
        assert draining_calls == []
        finish_legacy.set()
        assert sorted(await asyncio.gather(*legacy_tasks)) == ["legacy-a", "legacy-b"]
        assert sorted(legacy_completed) == ["legacy-a", "legacy-b"]
        async with transaction_factory() as connection:
            unified_version = await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.CLEANUP,
                expected_version=cleanup_drain,
            )
        assert unified_version == 3

        unified_started = asyncio.Event()
        finish_unified = asyncio.Event()
        unified_calls = []

        async def unified_owner(worker_id):
            async def action():
                unified_calls.append(worker_id)
                unified_started.set()
                await finish_unified.wait()
                return worker_id

            return await scheduled(action, worker_id)

        unified_a = asyncio.create_task(unified_owner("unified-a"))
        await asyncio.wait_for(unified_started.wait(), timeout=2)
        old_mode_calls = []
        async def old_legacy_path():
            old_mode_calls.append("legacy")

        async with transaction_factory() as connection:
            assert await run_current_domain_work(
                old_legacy_path,
                transaction_factory=transaction_factory,
                store=supervision_postgres,
                domain=SupervisionDomain.CLEANUP,
                expected_mode="legacy",
                worker_id="legacy-too-late",
                ttl_seconds=60,
            ) is None
        unified_b = asyncio.create_task(unified_owner("unified-b"))
        assert await asyncio.wait_for(unified_b, timeout=2) is None
        assert unified_calls == ["unified-a"]
        assert old_mode_calls == []
        finish_unified.set()
        assert await asyncio.wait_for(unified_a, timeout=2) == "unified-a"
        assert await scheduled(
            lambda: _record_call(unified_calls, "unified-b"), "unified-b"
        ) == "unified-b"

        async with transaction_factory() as connection:
            legacy_drain = await supervision_postgres.request_domain_drain(
                connection,
                domain=SupervisionDomain.CLEANUP,
                expected_version=unified_version,
                expected_mode="unified",
            )
            assert legacy_drain == 4
            assert await supervision_postgres.return_domain_to_legacy(
                connection,
                domain=SupervisionDomain.CLEANUP,
                expected_version=legacy_drain,
            ) == 5
        assert await scheduled(
            lambda: _record_call(legacy_completed, "legacy-return"), "legacy-return"
        ) == "legacy-return"

        async with transaction_factory() as connection:
            await connection.execute(
                "update execution_supervision_domains set mode = 'unified', owner_version = 2 where domain = 'probe'"
            )
            expired = await supervision_postgres.claim_domain_work(
                connection,
                domain=SupervisionDomain.PROBE,
                owner_mode="unified",
                worker_id="probe-a",
                ttl_seconds=1,
            )
            assert expired is not None
            await connection.execute(
                "update execution_supervision_claims set started_at = clock_timestamp() - interval '2 seconds', expires_at = clock_timestamp() - interval '1 second' where claim_token = %s",
                (expired["claim_token"],),
            )
            assert not await supervision_postgres.claim_is_current(connection, expired)
            assert await supervision_postgres.claim_domain_work(
                connection,
                domain=SupervisionDomain.PROBE,
                owner_mode="unified",
                worker_id="probe-b",
                ttl_seconds=60,
            ) is None
            probe_drain = await supervision_postgres.request_domain_drain(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_version=2,
                expected_mode="unified",
            )
            assert probe_drain == 3
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_version=probe_drain,
            ) is None
            with pytest.raises(ValueError, match="evidence_required"):
                await supervision_postgres.recover_expired_domain_claim(
                    connection,
                    domain=SupervisionDomain.PROBE,
                    expected_domain_version=probe_drain,
                    expected_claim_owner_version=2,
                    claim_token=expired["claim_token"],
                    worker_id="probe-a",
                    operator_id="operator-a",
                    stop_evidence="",
                )
            wrong_fences = (
                {"domain": SupervisionDomain.SETTLEMENT},
                {"claim_token": uuid.uuid4()},
                {"worker_id": "probe-other"},
            )
            for mismatch in wrong_fences:
                arguments = {
                    "domain": SupervisionDomain.PROBE,
                    "expected_domain_version": probe_drain,
                    "expected_claim_owner_version": 2,
                    "claim_token": expired["claim_token"],
                    "worker_id": "probe-a",
                    "operator_id": "operator-a",
                    "stop_evidence": "worker pod probe-a terminated",
                }
                arguments.update(mismatch)
                assert await supervision_postgres.recover_expired_domain_claim(
                    connection, **arguments
                ) is None
            assert await supervision_postgres.recover_expired_domain_claim(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_domain_version=2,
                expected_claim_owner_version=2,
                claim_token=expired["claim_token"],
                worker_id="probe-a",
                operator_id="operator-a",
                stop_evidence="worker pod probe-a terminated",
            ) is None
            assert await supervision_postgres.recover_expired_domain_claim(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_domain_version=probe_drain,
                expected_claim_owner_version=1,
                claim_token=expired["claim_token"],
                worker_id="probe-a",
                operator_id="operator-a",
                stop_evidence="worker pod probe-a terminated",
            ) is None
            recovered = await supervision_postgres.recover_expired_domain_claim(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_domain_version=probe_drain,
                expected_claim_owner_version=2,
                claim_token=expired["claim_token"],
                worker_id="probe-a",
                operator_id="operator-a",
                stop_evidence="worker pod probe-a terminated",
            )
            assert recovered is not None
            assert recovered["recovered_by"] == "operator-a"
            assert recovered["recovery_evidence"] == "worker pod probe-a terminated"
            assert not await supervision_postgres.release_domain_work(connection, expired)
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.PROBE,
                expected_version=probe_drain,
            ) == 4
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()
