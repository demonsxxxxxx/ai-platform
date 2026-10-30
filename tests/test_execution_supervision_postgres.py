import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import schema_migrations
from app.execution.application.supervision import run_current_domain_work, run_scheduled_domain_work
from app.execution.application.run_delivery_intents import (
    QueueDeliveryReceipt,
    RunDeliveryIntents,
)
from app.execution.domain.supervision import (
    SupervisionCapacities,
    SupervisionDomain,
    select_supervision_candidates,
)
from app.execution.infrastructure import (
    run_delivery_intents_postgres as delivery_store,
    supervision_postgres,
)


async def _record_call(target, value="called"):
    target.append(value)
    return value


@asynccontextmanager
async def _isolated_schema():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"execution_supervision_case_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row
    )
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
        yield transaction_factory
    finally:
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema))
        )
        await admin.close()


async def _seed_run(
    transaction_factory,
    run_id: str,
    *,
    status: str = "queued",
    attempt_status: str | None = None,
) -> str | None:
    async with transaction_factory() as connection:
        await connection.execute(
            "insert into tenants(id, name) values ('tenant', 'Tenant') on conflict (id) do nothing"
        )
        await connection.execute(
            "insert into workspaces(id, tenant_id, name) values ('workspace', 'tenant', 'Workspace') on conflict (id) do nothing"
        )
        await connection.execute(
            "insert into users(id, tenant_id, display_name) values ('user', 'tenant', 'User') on conflict (id) do nothing"
        )
        await connection.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent', 'tenant', 'Agent', 'chat') on conflict (tenant_id, id) do nothing"
        )
        await connection.execute(
            "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) "
            "values ('session', 'tenant', 'workspace', 'user', 'agent', 'Session') "
            "on conflict (id) do nothing"
        )
        await connection.execute(
            "insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, "
            "execution_kind, status) values (%s, 'tenant', 'workspace', 'session', 'user', "
            "'agent', 'harness_chat', %s)",
            (run_id, status),
        )
        if attempt_status is None:
            return None
        spec_version = "ai-platform.execution-spec.v1"
        spec_json = json.dumps(
            {
                "schema_version": spec_version,
                "tenant_id": "tenant",
                "run_id": run_id,
                "workspace_id": "workspace",
                "user_id": "user",
                "session_id": "session",
                "agent_id": "agent",
                "execution_kind": "harness_chat",
                "skill_id": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        attempt_id = f"attempt-{run_id}"
        await connection.execute(
            """insert into run_attempts(
                 id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                 queue_attempt_id, execution_spec_schema_version, execution_spec_json,
                 execution_spec_canonical_json, execution_spec_sha256)
               values (%s, 'tenant', %s, 1, 'created', 'queue_worker', 'worker-a',
                 %s, %s, %s::jsonb, %s, %s)""",
            (
                attempt_id,
                run_id,
                f"queue-{attempt_id}",
                spec_version,
                spec_json,
                spec_json,
                sha256(spec_json.encode("utf-8")).hexdigest(),
            ),
        )
        transition_path = ["queued", "claimed", "running"]
        if attempt_status in {"succeeded", "failed", "cancelled"}:
            transition_path.append(attempt_status)
        elif attempt_status != "running":
            raise ValueError(f"unsupported attempt status: {attempt_status}")
        for next_status in transition_path:
            await connection.execute(
                """update run_attempts
                   set status = %s,
                       owner_generation = owner_generation + 1,
                       queue_message_id = case when status = 'created'
                         then 'message-' || id else queue_message_id end,
                       finished_at = case when %s in ('succeeded', 'failed', 'cancelled')
                         then clock_timestamp() else null end
                   where id = %s""",
                (next_status, next_status, attempt_id),
            )
        return attempt_id


async def _seed_delivery_intent(transaction_factory, run_id: str) -> None:
    payload_json = json.dumps(
        {"tenant_id": "tenant", "run_id": run_id, "session_id": "session"},
        sort_keys=True,
        separators=(",", ":"),
    )
    message_id = sha256(payload_json.encode("utf-8")).hexdigest()
    async with transaction_factory() as connection:
        await delivery_store.insert(
            connection,
            tenant_id="tenant",
            run_id=run_id,
            session_id="session",
            source="create_run",
            submission_id=None,
            user_message_id=None,
            payload_json=payload_json,
            message_id=message_id,
            payload_sha256=message_id,
        )


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
                unified_execution_enabled=True,
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
                unified_execution_enabled=False,
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


@pytest.mark.asyncio
async def test_delivery_shadow_eligibility_matches_attempt_readback_store_rules():
    async with _isolated_schema() as transaction_factory:
        for run_id, attempt_status in (
            ("queued-no-attempt", None),
            ("running-attempt", "running"),
            ("terminal-attempt", "succeeded"),
        ):
            await _seed_run(
                transaction_factory,
                run_id,
                attempt_status=attempt_status,
            )
            await _seed_delivery_intent(transaction_factory, run_id)

        observed_at = datetime.now(timezone.utc)
        async with transaction_factory() as connection:
            candidates = await supervision_postgres.observe_persisted_candidates(
                connection,
                now=observed_at,
                batch_limits={
                    SupervisionDomain.DELIVERY: 10,
                    SupervisionDomain.PROBE: 1,
                    SupervisionDomain.SETTLEMENT: 1,
                    SupervisionDomain.CLEANUP: 1,
                },
                probe_stale_seconds=45,
                settlement_claim_stale_seconds=300,
            )

    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    queued = by_id["tenant/queued-no-attempt"]
    running = by_id["tenant/running-attempt"]
    terminal = by_id["tenant/terminal-attempt"]
    assert queued.action == "recover_delivery"
    assert dict(queued.facts)["enqueue_permitted"] == "True"
    assert running.action == "readback_delivery_receipt"
    assert dict(running.facts)["run_status"] == "running"
    assert dict(running.facts)["attempt_status"] == "running"
    assert dict(running.facts)["enqueue_permitted"] == "False"
    assert terminal.action == "readback_delivery_receipt"
    assert dict(terminal.facts)["run_status"] == "succeeded"
    assert dict(terminal.facts)["attempt_status"] == "succeeded"
    assert dict(terminal.facts)["enqueue_permitted"] == "False"


@pytest.mark.asyncio
async def test_probe_observation_has_fair_cancel_and_normal_lanes():
    async with _isolated_schema() as transaction_factory:
        async def seed_probe(run_id: str, *, cancelled: bool = False) -> None:
            attempt_id = await _seed_run(
                transaction_factory, run_id, attempt_status="running"
            )
            async with transaction_factory() as connection:
                if cancelled:
                    await connection.execute(
                        "update runs set cancel_requested_at = clock_timestamp() "
                        "where tenant_id = 'tenant' and id = %s",
                        (run_id,),
                    )
                await connection.execute(
                    """insert into sandbox_leases(
                         id, tenant_id, workspace_id, user_id, session_id, run_id,
                         attempt_id, sandbox_mode, status, executor_status,
                         executor_heartbeat_at, executor_reconciliation_context_json)
                       values (%s, 'tenant', 'workspace', 'user', 'session', %s,
                         %s, 'chat', 'active', 'running',
                         clock_timestamp() - interval '1 hour', '{}'::jsonb)""",
                    (f"lease-{run_id}", run_id, attempt_id),
                )

        await seed_probe("normal-a")
        await seed_probe("normal-b")
        await seed_probe("normal-c")

        async def observe_probe_page():
            observed_at = datetime.now(timezone.utc)
            async with transaction_factory() as connection:
                return await supervision_postgres.observe_persisted_candidates(
                    connection,
                    now=observed_at,
                    batch_limits={
                        SupervisionDomain.DELIVERY: 1,
                        SupervisionDomain.PROBE: 1,
                        SupervisionDomain.SETTLEMENT: 1,
                        SupervisionDomain.CLEANUP: 1,
                    },
                    probe_stale_seconds=45,
                    settlement_claim_stale_seconds=300,
                )

        first_page = await observe_probe_page()
        first_probe = [item for item in first_page if item.domain is SupervisionDomain.PROBE]
        assert [item.candidate_id for item in first_probe] == ["lease-normal-a"]

        await seed_probe("cancel-a", cancelled=True)
        second_page = await observe_probe_page()
        second_probe = [item for item in second_page if item.domain is SupervisionDomain.PROBE]
        assert [item.candidate_id for item in second_probe] == [
            "lease-cancel-a",
            "lease-normal-b",
        ]
        assert [item.priority for item in second_probe] == [0, 100]
        assert len(select_supervision_candidates(
            second_probe,
            now=datetime.now(timezone.utc),
            capacities=SupervisionCapacities(delivery=1, probe=1, settlement=1, cleanup=1),
        )) == 1

        third_page = await observe_probe_page()
        third_probe = [item for item in third_page if item.domain is SupervisionDomain.PROBE]
        assert "lease-normal-c" in {item.candidate_id for item in third_probe}


@pytest.mark.asyncio
async def test_delivery_domain_claim_waits_for_failed_receipt_and_blocked_enqueue():
    async with _isolated_schema() as transaction_factory:
        for run_id in ("receipt-error", "blocked-enqueue"):
            await _seed_run(transaction_factory, run_id)
            await _seed_delivery_intent(transaction_factory, run_id)

        async with transaction_factory() as connection:
            await connection.execute(
                """create function fail_supervision_test_receipt()
                   returns trigger language plpgsql as $$
                   begin
                     if new.run_id = 'receipt-error' and new.state = 'delivered' then
                       raise exception 'supervision_test_receipt_failure';
                     end if;
                     return new;
                   end;
                   $$"""
            )
            await connection.execute(
                """create trigger fail_supervision_test_receipt
                   before update on run_delivery_intents
                   for each row execute function fail_supervision_test_receipt()"""
            )

        enqueue_started = asyncio.Event()
        finish_enqueue = asyncio.Event()
        receipt_database_error = asyncio.Event()
        receipt_errors: list[str] = []

        class Queue:
            async def readback(self, payload_json: str, *, message_id: str):
                run_id = json.loads(payload_json)["run_id"]
                if run_id == "receipt-error":
                    return QueueDeliveryReceipt(1, 1, message_id, "test")
                return None

            async def enqueue(self, payload_json: str, *, message_id: str):
                run_id = json.loads(payload_json)["run_id"]
                assert run_id == "blocked-enqueue"
                enqueue_started.set()
                await finish_enqueue.wait()
                return QueueDeliveryReceipt(2, 2, message_id, "test")

        class DeliveryStore:
            def __getattr__(self, name):
                return getattr(delivery_store, name)

            async def record_receipt(
                self, connection, *, intent, receipt, retry_after_seconds=900
            ):
                try:
                    return await delivery_store.record_receipt(
                        connection,
                        intent=intent,
                        receipt=receipt,
                        retry_after_seconds=retry_after_seconds,
                    )
                except psycopg.Error:
                    receipt_errors.append(intent.run_id)
                    receipt_database_error.set()
                    raise

        async def permanent_rejection_owner(*_args, **_kwargs):
            return True

        delivery = RunDeliveryIntents(
            enabled=True,
            store=DeliveryStore(),
            codec=object(),
            queue=Queue(),
            transaction=transaction_factory,
            scan_batch_size=2,
            claim_ttl_seconds=60,
            retry_base_seconds=1,
            retry_cap_seconds=10,
            permanent_rejection_owner=permanent_rejection_owner,
        )
        scan = asyncio.create_task(
            run_scheduled_domain_work(
                delivery.scan_once,
                transaction_factory=transaction_factory,
                store=supervision_postgres,
                domain=SupervisionDomain.DELIVERY,
                worker_id="legacy-delivery-worker",
                ttl_seconds=60,
                capacities=SupervisionCapacities(
                    delivery=1, probe=1, settlement=1, cleanup=1
                ),
                unified_execution_enabled=False,
            )
        )
        try:
            await asyncio.wait_for(enqueue_started.wait(), timeout=3)
            await asyncio.wait_for(receipt_database_error.wait(), timeout=3)
            assert receipt_errors == ["receipt-error"]
            assert not scan.done()

            async with transaction_factory() as connection:
                cursor = await connection.execute(
                    "select count(*) as count from execution_supervision_claims "
                    "where domain = 'delivery' and recovered_at is null"
                )
                assert (await cursor.fetchone())["count"] == 1
                draining_version = await supervision_postgres.request_domain_drain(
                    connection,
                    domain=SupervisionDomain.DELIVERY,
                    expected_version=1,
                )
                assert draining_version == 2
                assert await supervision_postgres.activate_unified_domain(
                    connection,
                    domain=SupervisionDomain.DELIVERY,
                    expected_version=draining_version,
                ) is None
        finally:
            finish_enqueue.set()
            if not scan.done():
                try:
                    await asyncio.wait_for(scan, timeout=5)
                except BaseException:
                    pass

        with pytest.raises(psycopg.Error, match="supervision_test_receipt_failure"):
            await scan
        async with transaction_factory() as connection:
            cursor = await connection.execute(
                "select count(*) as count from execution_supervision_claims "
                "where domain = 'delivery' and recovered_at is null"
            )
            assert (await cursor.fetchone())["count"] == 0
            assert await supervision_postgres.activate_unified_domain(
                connection,
                domain=SupervisionDomain.DELIVERY,
                expected_version=2,
            ) == 3
