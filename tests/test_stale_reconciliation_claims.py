from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import fields
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import os
import json
from typing import Any
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from redis.asyncio import Redis

from app import queue
from app import schema_migrations
from app.bootstrap.context import configure_context_services
from app.execution.application.stale_run_reconciliation import StaleRunReconciliationService
from app.bootstrap import execution_stale_run_reconciliation as stale_reconciliation_bootstrap
from app.models import QueueRunPayload
from app import worker_main
from app.execution.application.stale_terminalization import stage_stale_run_reconciliation
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.platform.tracing import standard_trace_id
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
from app.runs.domain.execution_spec import EXECUTION_SPEC_SCHEMA_VERSION, compile_execution_spec
from app.runs.infrastructure.attempt_settlement_projection_postgres import (
    activate_attempt_settlement_storage,
    prepare_attempt_settlement_storage_cutover,
)
from app.runs.infrastructure.attempt_settlement_retirement_postgres import (
    prepare_attempt_settlement_retirement,
    retire_attempt_settlement_legacy_columns,
)
from app.runs.infrastructure.attempt_settlement_writer_cutover_postgres import (
    activate_attempt_settlement_writer,
    prepare_attempt_settlement_writer_cutover,
)
from app.runs.infrastructure.lifecycle_postgres import (
    PostgresRunLifecyclePersistence,
    claim_stale_run_reconciliation,
    list_stale_run_reconciliation_candidates,
    require_run_result_size,
    release_stale_run_reconciliation_claim,
    stale_run_reconciliation_claim_is_current,
)
from app.runs.infrastructure.postgres import (
    PostgresRunCancellationPersistence,
    create_run_attempt,
    prepare_stale_run_attempt_reconciliation,
    transition_run_attempt,
)
from app.runs.infrastructure import postgres as run_attempt_persistence
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.streaming.infrastructure.run_events_postgres import append_event


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.row if isinstance(self.row, list) else []


class _Connection:
    def __init__(self, rows: list[dict[str, Any] | None]):
        self.rows = list(rows)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...] = ()):
        statement = " ".join(query.split()).lower()
        self.calls.append((statement, params))
        if statement == "select storage_version from run_attempt_settlement_storage where singleton = true":
            return _Cursor({"storage_version": "sandbox_lease.v1"})
        return _Cursor(self.rows.pop(0) if self.rows else None)


def _execution_spec(*, tenant_id: str, run_id: str, suffix: str):
    return compile_execution_spec(
        {
            "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
            "run_payload_schema_version": "ai-platform.run-payload.v1",
            "tenant_id": tenant_id,
            "workspace_id": f"workspace-{suffix}",
            "user_id": f"user-{suffix}",
            "session_id": f"session-{suffix}",
            "run_id": run_id,
            "agent_id": f"agent-{suffix}",
            "execution_kind": "skill",
            "skill_id": f"skill-{suffix}",
            "file_ids": [],
            "input": {"message": "stale reconciliation"},
            "executor_type": "fake",
            "trace_id": f"trace-{suffix}",
            "skill_version": "version-a",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "selected_version": "version-a",
            },
            "skill_manifests": [
                {"skill_id": f"skill-{suffix}", "content_hash": "version-a"}
            ],
            "context_snapshot_id": f"context-{suffix}",
            "context_snapshot": {"context_snapshot_id": f"context-{suffix}"},
            "context_pack": {},
            "model_id": "model-a",
            "model_value": "model-a",
            "agent_profile": {},
        }
    )


async def _seed_running_attempt(
    conn: psycopg.AsyncConnection,
    *,
    suffix: str,
    queue_attempt_id: str | None = None,
    queue_message_id: str | None = None,
):
    tenant_id = f"tenant-{suffix}"
    run_id = f"run-{suffix}"
    worker_id = f"worker-{suffix}"
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    await conn.execute("insert into tenants(id, name) values (%s, %s)", (tenant_id, suffix))
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values (%s, %s, %s)",
        (f"workspace-{suffix}", tenant_id, suffix),
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
        (f"user-{suffix}", tenant_id, suffix),
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) values (%s, %s, %s, 'chat')",
        (f"agent-{suffix}", tenant_id, suffix),
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values (%s, %s, '1', 'fake')",
        (f"skill-{suffix}", suffix),
    )
    await conn.execute(
        """insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
           values (%s, %s, %s, %s, %s, %s)""",
        (
            f"session-{suffix}", tenant_id, f"workspace-{suffix}",
            f"user-{suffix}", f"agent-{suffix}", suffix,
        ),
    )
    await conn.execute(
        """insert into runs(
             id, tenant_id, workspace_id, session_id, user_id, agent_id,
             skill_id, status, created_at, queued_at, started_at
           ) values (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s)""",
        (
            run_id, tenant_id, f"workspace-{suffix}", f"session-{suffix}",
            f"user-{suffix}", f"agent-{suffix}", f"skill-{suffix}", old, old, old,
        ),
    )
    attempt_id = f"attempt-{suffix}"
    await create_run_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        ordinal=1,
        owner_kind="queue_worker",
        owner_id=worker_id,
        queue_attempt_id=queue_attempt_id or f"queue-attempt-{suffix}",
        execution_spec=_execution_spec(tenant_id=tenant_id, run_id=run_id, suffix=suffix),
    )
    for expected, requested, generation, lease in (
        ("created", "queued", 1, old),
        ("queued", "claimed", 2, old),
        ("claimed", "running", 3, old),
    ):
        await transition_run_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            expected_status=expected,
            requested_status=requested,
            expected_owner_kind="queue_worker",
            expected_owner_id=worker_id,
            expected_owner_generation=generation,
            queue_message_id=(
                queue_message_id if queue_message_id is not None else "a" * 64
            ) if requested == "queued" else None,
            last_heartbeat_at=old,
            lease_expires_at=lease,
        )
    await conn.execute(
        "update runs set status = 'running' where tenant_id = %s and id = %s",
        (tenant_id, run_id),
    )
    return tenant_id, run_id, attempt_id, worker_id, old


def _run_lifecycle_service() -> RunLifecycleService:
    return RunLifecycleService(
        persistence=PostgresRunLifecyclePersistence(),
        append_event=_noop,
        append_audit_log=_noop,
        validate_result_size=require_run_result_size,
        sanitize_payload=sanitize_public_payload,
        sanitize_text=sanitize_public_text,
        make_trace_id=standard_trace_id,
    )


@pytest.mark.asyncio
async def test_postgres_stale_candidates_bind_attempt_and_exclude_queued_delivery():
    conn = _Connection([[]])
    rows = await list_stale_run_reconciliation_candidates(
        conn,
        stale_after_seconds=900,
        cancel_requested_after_seconds=5,
        limit=25,
        postgres_claim_mode=True,
    )

    statement, params = conn.calls[1]
    assert rows == []
    assert "runs.status = 'running'" in statement
    assert "latest_attempt.id as expected_attempt_id" in statement
    assert "latest_attempt.owner_generation" in statement
    assert "claim_expires_at > clock_timestamp()" in statement
    assert "lease.status = 'active'" in statement
    assert "executor_reconciliation_status is distinct from 'finalized'" in statement
    assert "attempt.lease_expires_at > clock_timestamp()" in statement
    assert "claim.claim_deadline_at > clock_timestamp()" in statement
    assert "for update of runs skip locked" in statement
    assert params == (5, 900, 25)


@pytest.mark.asyncio
async def test_claim_locks_run_then_attempt_and_replaces_only_an_expired_claim():
    candidate = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "run_id": "run-a",
        "status": "running",
        "expected_attempt_id": "attempt-a",
        "expected_owner_generation": 4,
    }
    conn = _Connection(
        [
            {
                "id": "run-a", "tenant_id": "tenant-a", "status": "running",
                "cancel_requested_at": None, "terminalization_target": None,
                "terminalization_reason": None, "stale": True,
                "stale_before": "2026-07-21T11:00:00Z",
            },
            {
                "id": "attempt-a", "owner_generation": 4, "status": "running",
                "lease_live": False,
            },
            {"active_lease": False},
            {"pending_terminal_receipt": False},
            {"delivery_inflight": False},
            {
                "tenant_id": "tenant-a", "run_id": "run-a",
                "expected_attempt_id": "attempt-a", "expected_owner_generation": 4,
                "claim_token": uuid.uuid4(), "claimed_by": "worker-a",
                "claim_deadline_at": "2026-07-21T11:05:00Z",
            },
        ]
    )

    claim = await claim_stale_run_reconciliation(
        conn,
        candidate=candidate,
        worker_id="worker-a",
        stale_after_seconds=900,
        cancel_requested_after_seconds=5,
        claim_ttl_seconds=300,
    )

    assert claim is not None
    assert claim["expected_attempt_id"] == "attempt-a"
    assert claim["expected_owner_generation"] == 4
    assert claim["stale_before"] == "2026-07-21T11:00:00Z"
    assert "for update of runs" in conn.calls[1][0]
    assert "for update" in conn.calls[2][0]
    assert "on conflict (tenant_id, run_id) do update" in conn.calls[-1][0]
    assert "claim_deadline_at <= clock_timestamp()" in conn.calls[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_kind", "owner_id", "generation"),
    [("queue_worker", "worker-a", 6), ("reconciler", "other-owner", 6),
     ("reconciler", "stale-run-maintenance", 6), ("reconciler", "cancel-cleanup", 7)],
)
async def test_stale_claim_rejects_other_live_owners_and_changed_handoff_generation(
    owner_kind, owner_id, generation,
):
    candidate = {
        "tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-a",
        "run_id": "run-a", "status": "running", "expected_attempt_id": "attempt-a",
        "expected_owner_generation": 6,
    }
    conn = _Connection([
        {"id": "run-a", "status": "running", "cancel_requested_at": "now",
         "terminalization_target": None, "stale": True},
        {"id": "attempt-a", "owner_generation": generation, "status": "cancel_requested",
         "owner_kind": owner_kind, "owner_id": owner_id, "lease_live": True},
    ])
    assert await claim_stale_run_reconciliation(
        conn, candidate=candidate, worker_id="stale-owner", stale_after_seconds=60,
        cancel_requested_after_seconds=5, claim_ttl_seconds=300,
    ) is None
    assert not any("insert into run_stale_reconciliation_claims" in query for query, _ in conn.calls)


@pytest.mark.asyncio
async def test_stale_reconciliation_bootstrap_preserves_schema_fail_closed_gate(monkeypatch):
    async def missing_schema():
        raise RuntimeError("schema_not_current")

    def service_must_not_build(*_args, **_kwargs):
        raise AssertionError("schema-incomplete maintenance must remain blocked")

    monkeypatch.setattr(stale_reconciliation_bootstrap, "require_schema_current", missing_schema)
    monkeypatch.setattr(
        stale_reconciliation_bootstrap,
        "build_stale_run_reconciliation_service",
        service_must_not_build,
    )

    with pytest.raises(RuntimeError, match="schema_not_current"):
        await stale_reconciliation_bootstrap.reconcile_worker_stale_runs_with_postgres_claims(
            object(),
            v4_capabilities=object(),
            attempt_lifecycle=object(),
            lifecycle=object(),
            worker_id="worker-a",
            cancel_requested_after_seconds=5,
        )


@pytest.mark.asyncio
async def test_worker_stale_reconciliation_always_uses_pg_claim_bootstrap(monkeypatch):
    calls = []

    async def postgres_owner(*_args, **kwargs):
        calls.append(("postgres_owner", kwargs["worker_id"]))
        return []

    monkeypatch.setattr(worker_main, "default_worker_id", lambda: "worker-a")
    monkeypatch.setattr(worker_main, "reconcile_worker_stale_runs_with_postgres_claims", postgres_owner)

    result = await worker_main.reconcile_stale_runs_for_worker(
        object(),
        v4_capabilities=object(),
        attempt_lifecycle=object(),
        lifecycle=object(),
    )

    assert result == []
    assert calls == [("postgres_owner", "worker-a")]


@pytest.mark.asyncio
async def test_real_postgres_claim_takeover_fences_old_attempt_writeback():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"stale_run_claim_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        suffix = uuid.uuid4().hex[:12]
        async with transaction_factory() as conn:
            tenant_id, run_id, attempt_id, worker_id, old = await _seed_running_attempt(
                conn, suffix=suffix
            )

        async with transaction_factory() as conn:
            candidates = await list_stale_run_reconciliation_candidates(
                conn,
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                limit=10,
                postgres_claim_mode=True,
            )
        candidate = next(row for row in candidates if row["run_id"] == run_id)

        async def take_claim(owner: str):
            async with transaction_factory() as conn:
                return await claim_stale_run_reconciliation(
                    conn,
                    candidate=candidate,
                    worker_id=owner,
                    stale_after_seconds=60,
                    cancel_requested_after_seconds=5,
                    claim_ttl_seconds=300,
                )

        first, second = await asyncio.gather(take_claim("worker-a"), take_claim("worker-b"))
        claims = [claim for claim in (first, second) if claim is not None]
        assert len(claims) == 1
        old_claim = claims[0]
        assert old_claim["expected_attempt_id"] == attempt_id
        assert old_claim["expected_owner_generation"] == candidate["expected_owner_generation"]

        async with transaction_factory() as conn:
            staged = await stage_stale_run_reconciliation(
                _run_lifecycle_service(),
                conn,
                tenant_id=tenant_id,
                workspace_id=f"workspace-{suffix}",
                user_id=f"user-{suffix}",
                run_id=run_id,
                expected_status="running",
                stale_before=old_claim["stale_before"],
                terminal_status="failed",
                error_code="stale_run_interrupted",
                error_message="stale",
                claim_token=str(old_claim["claim_token"]),
                expected_attempt_id=attempt_id,
                expected_owner_generation=int(old_claim["expected_owner_generation"]),
                append_event=_noop,
                append_audit_log=_noop,
            )
            assert staged is not None
            prepared = await prepare_stale_run_attempt_reconciliation(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                terminal_status="failed",
                reconciler_id="stale-run-maintenance",
            )
            assert prepared is not None and prepared["status"] == "expired"
            assert int(prepared["owner_generation"]) == int(old_claim["expected_owner_generation"]) + 1

        with pytest.raises(Exception, match="run_attempt_transition_conflict"):
            async with transaction_factory() as conn:
                await transition_run_attempt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    expected_status="running",
                    requested_status="failed",
                    expected_owner_kind="queue_worker",
                    expected_owner_id=worker_id,
                    expected_owner_generation=int(old_claim["expected_owner_generation"]),
                    terminal_reason="stale_owner_writeback",
                )

        async with transaction_factory() as conn:
            await conn.execute(
                """update run_stale_reconciliation_claims
                   set claimed_at = clock_timestamp() - interval '2 seconds',
                       claim_deadline_at = clock_timestamp() - interval '1 second'
                   where tenant_id = %s and run_id = %s""",
                (tenant_id, run_id),
            )
        async with transaction_factory() as conn:
            retry_candidate = next(
                row
                for row in await list_stale_run_reconciliation_candidates(
                    conn,
                    stale_after_seconds=60,
                    cancel_requested_after_seconds=5,
                    limit=10,
                    postgres_claim_mode=True,
                )
                if row["run_id"] == run_id
            )
        async with transaction_factory() as conn:
            new_claim = await claim_stale_run_reconciliation(
                conn,
                candidate=retry_candidate,
                worker_id="worker-c",
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                claim_ttl_seconds=300,
            )
            assert new_claim is not None
            assert new_claim["expected_attempt_id"] == attempt_id
            assert int(new_claim["expected_owner_generation"]) == int(old_claim["expected_owner_generation"]) + 1
            resumed = await stage_stale_run_reconciliation(
                _run_lifecycle_service(),
                conn,
                tenant_id=tenant_id,
                workspace_id=f"workspace-{suffix}",
                user_id=f"user-{suffix}",
                run_id=run_id,
                expected_status="running",
                stale_before=new_claim["stale_before"],
                terminal_status="failed",
                error_code="stale_run_interrupted",
                error_message="stale",
                claim_token=str(new_claim["claim_token"]),
                expected_attempt_id=attempt_id,
                expected_owner_generation=int(new_claim["expected_owner_generation"]),
                append_event=_noop,
                append_audit_log=_noop,
            )
            assert resumed is not None and resumed["already_staged"] is True
            resumed_attempt = await prepare_stale_run_attempt_reconciliation(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                terminal_status="failed",
                reconciler_id="stale-run-maintenance",
            )
            assert resumed_attempt is not None and resumed_attempt["status"] == "expired"
            assert not await stale_run_reconciliation_claim_is_current(conn, claim=old_claim)
            assert await stale_run_reconciliation_claim_is_current(conn, claim=new_claim)
            assert not await release_stale_run_reconciliation_claim(conn, claim=old_claim)
            assert await release_stale_run_reconciliation_claim(conn, claim=new_claim)
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_postgres_candidate_scan_skips_unclaimable_rows_before_limit():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"stale_run_fair_scan_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        fixtures = {}
        async with transaction_factory() as conn:
            for index, kind in enumerate(("active", "receipt", "live_attempt", "claim", "eligible")):
                suffix = f"fair-{index}-{uuid.uuid4().hex[:8]}"
                tenant_id, run_id, attempt_id, worker_id, old = await _seed_running_attempt(
                    conn, suffix=suffix
                )
                stale_at = old - timedelta(hours=5 - index)
                await conn.execute(
                    "update runs set created_at=%s, queued_at=%s, started_at=%s where id=%s",
                    (stale_at, stale_at, stale_at, run_id),
                )
                fixtures[kind] = (tenant_id, run_id, attempt_id, worker_id)

            tenant_id, run_id, attempt_id, _worker_id = fixtures["active"]
            await conn.execute(
                """insert into sandbox_leases(
                     id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
                     sandbox_mode, status
                   ) values (%s, %s, %s, %s, %s, %s, %s, 'ephemeral', 'active')""",
                (
                    "lease-active", tenant_id, run_id.replace("run-", "workspace-", 1),
                    run_id.replace("run-", "user-", 1),
                    run_id.replace("run-", "session-", 1), run_id, attempt_id,
                ),
            )

            tenant_id, run_id, attempt_id, _worker_id = fixtures["receipt"]
            workspace_id = run_id.replace("run-", "workspace-")
            user_id = run_id.replace("run-", "user-")
            session_id = run_id.replace("run-", "session-")
            await conn.execute(
                """insert into sandbox_leases(
                     id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
                     sandbox_mode, status, executor_terminal_json, executor_reconciliation_status
                   ) values (%s, %s, %s, %s, %s, %s, %s, 'ephemeral', 'released',
                             '{"status":"failed"}'::jsonb, 'pending')""",
                ("lease-receipt", tenant_id, workspace_id, user_id, session_id, run_id, attempt_id),
            )

            tenant_id, run_id, _attempt_id, _worker_id = fixtures["live_attempt"]
            await conn.execute(
                "update run_attempts set lease_expires_at=clock_timestamp()+interval '10 minutes' where tenant_id=%s and run_id=%s",
                (tenant_id, run_id),
            )

            tenant_id, run_id, attempt_id, _worker_id = fixtures["claim"]
            attempt = await (await conn.execute(
                "select owner_generation from run_attempts where tenant_id=%s and run_id=%s",
                (tenant_id, run_id),
            )).fetchone()
            await conn.execute(
                """insert into run_stale_reconciliation_claims(
                     tenant_id, run_id, expected_attempt_id, expected_owner_generation,
                     claim_token, claimed_by, claim_deadline_at
                   ) values (%s, %s, %s, %s, %s, 'existing-worker',
                             clock_timestamp()+interval '10 minutes')""",
                (tenant_id, run_id, attempt_id, attempt["owner_generation"], uuid.uuid4()),
            )

        async with transaction_factory() as conn:
            candidates = await list_stale_run_reconciliation_candidates(
                conn,
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                limit=1,
                postgres_claim_mode=True,
            )
        assert [row["run_id"] for row in candidates] == [fixtures["eligible"][1]]
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_legacy_stale_owner_cancels_released_expired_attempt_without_receipt(monkeypatch):
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"stale_run_legacy_cancel_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        suffix = uuid.uuid4().hex[:12]
        async with transaction_factory() as conn:
            tenant_id, run_id, attempt_id, _worker_id, old = await _seed_running_attempt(
                conn, suffix=suffix
            )
            marker = await (await conn.execute(
                "select storage_version from run_attempt_settlement_storage where singleton=true"
            )).fetchone()
            assert marker["storage_version"] == "sandbox_lease.v1"
            await conn.execute(
                "update runs set cancel_requested_at=%s, terminalization_target='cancel_requested' "
                "where tenant_id=%s and id=%s",
                (old, tenant_id, run_id),
            )
            await conn.execute(
                "update run_attempts set lease_expires_at=%s where tenant_id=%s and id=%s",
                (old, tenant_id, attempt_id),
            )
            await conn.execute(
                "insert into sandbox_leases(id, tenant_id, workspace_id, user_id, session_id, "
                "run_id, attempt_id, sandbox_mode, provider, status, executor_status) "
                "values (%s, %s, %s, %s, %s, %s, %s, 'ephemeral', 'fake', 'released', 'pending')",
                (
                    f"lease-{suffix}", tenant_id, f"workspace-{suffix}",
                    f"user-{suffix}", f"session-{suffix}", run_id, attempt_id,
                ),
            )

        async def current_schema():
            async with transaction_factory() as conn:
                status = await schema_migrations.schema_status(conn)
            assert status["ready"] is True
            return status

        monkeypatch.setattr(stale_reconciliation_bootstrap, "transaction", transaction_factory)
        monkeypatch.setattr(stale_reconciliation_bootstrap, "require_schema_current", current_schema)
        configure_context_services()
        results = await stale_reconciliation_bootstrap.reconcile_worker_stale_runs_with_postgres_claims(
            SimpleNamespace(
                stale_run_reconciliation_limit=10,
                stale_run_reconciliation_seconds=60,
                stale_run_reconciliation_claim_ttl_seconds=300,
            ),
            lifecycle=_run_lifecycle_service(),
            attempt_lifecycle=RunAttemptLifecycleService(persistence=run_attempt_persistence),
            v4_capabilities=None,
            worker_id="legacy-cancel-owner",
            cancel_requested_after_seconds=5,
        )
        assert any(
            row["run_id"] == run_id and row["status"] == "cancelled" and row["did_transition"]
            for row in results
        )
        async with transaction_factory() as conn:
            run = await (await conn.execute(
                "select status from runs where tenant_id=%s and id=%s", (tenant_id, run_id)
            )).fetchone()
            attempt = await (await conn.execute(
                "select status from run_attempts where tenant_id=%s and run_id=%s and id=%s",
                (tenant_id, run_id, attempt_id),
            )).fetchone()
            lease = await (await conn.execute(
                "select status, executor_status, executor_terminal_json, executor_reconciliation_status "
                "from sandbox_leases where tenant_id=%s and run_id=%s and attempt_id=%s",
                (tenant_id, run_id, attempt_id),
            )).fetchone()
            assert run["status"] == "cancelled"
            assert attempt["status"] == "cancelled"
            assert lease["status"] == "released"
            assert lease["executor_status"] == "pending"
            assert lease["executor_terminal_json"] is None
            assert lease["executor_reconciliation_status"] == "waiting_terminal"
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reader_version", "rejection"),
    [("sandbox_lease.v1", None), ("attempt.v1", None),
     ("sandbox_lease.v1", "pending_receipt"), ("attempt.v1", "pending_receipt"),
     ("sandbox_lease.v1", "missing_marker"), ("sandbox_lease.v1", "active_resource"),
     ("sandbox_lease.v1", "missing_evidence"), ("sandbox_lease.v1", "wrong_generation")],
)
async def test_real_postgres_cancel_handoff_recovers_after_release_commit(
    reader_version, rejection,
):
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"cancel_handoff_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        suffix = uuid.uuid4().hex[:12]
        async with transaction_factory() as conn:
            tenant_id, run_id, attempt_id, _worker_id, old = await _seed_running_attempt(
                conn, suffix=suffix
            )
            await conn.execute(
                "update run_attempts set lease_expires_at=clock_timestamp()+interval '15 minutes', "
                "last_heartbeat_at=clock_timestamp() where tenant_id=%s and id=%s",
                (tenant_id, attempt_id),
            )
            before = await (await conn.execute(
                "select lease_expires_at, last_heartbeat_at, "
                "lease_expires_at > clock_timestamp() as lease_live from run_attempts "
                "where tenant_id=%s and id=%s", (tenant_id, attempt_id)
            )).fetchone()
            assert before["lease_live"] is True
            attempt_row = await run_attempt_persistence.request_run_attempt_cancel(
                conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            )
            assert attempt_row is not None
            generation = int(attempt_row["owner_generation"])
            await conn.execute(
                "update runs set cancel_requested_at=%s, terminalization_target=null "
                "where tenant_id=%s and id=%s",
                (old, tenant_id, run_id),
            )
            if rejection == "missing_marker":
                await conn.execute(
                    "delete from run_attempt_settlement_storage where singleton=true"
                )
            else:
                await conn.execute(
                    "update run_attempt_settlement_storage set storage_version=%s where singleton=true",
                    (reader_version,),
                )
            await conn.execute(
                """
                insert into sandbox_leases(
                    id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
                    sandbox_mode, provider, status, lease_payload_json, executor_status
                ) values (%s, %s, %s, %s, %s, %s, %s, 'ephemeral', 'fake', 'released', %s::jsonb, 'pending')
                """,
                (
                    f"lease-{suffix}", tenant_id, f"workspace-{suffix}",
                    f"user-{suffix}", f"session-{suffix}", run_id, attempt_id,
                    json.dumps({"attempt_id": attempt_id, "owner_generation": generation - 1}),
                ),
            )
            if rejection == "active_resource":
                await conn.execute(
                    "update sandbox_leases set status='active' where id=%s",
                    (f"lease-{suffix}",),
                )
            elif rejection == "missing_evidence":
                await conn.execute(
                    "update sandbox_leases set lease_payload_json='{}'::jsonb where id=%s",
                    (f"lease-{suffix}",),
                )
            if rejection == "pending_receipt" and reader_version == "sandbox_lease.v1":
                await conn.execute(
                    "update sandbox_leases set executor_status='completed', "
                    "executor_terminal_json=%s::jsonb, "
                    "executor_reconciliation_status='waiting_terminal' "
                    "where tenant_id=%s and id=%s",
                    (json.dumps({"status": "succeeded"}), tenant_id, f"lease-{suffix}"),
                )
            if rejection == "pending_receipt" and reader_version == "attempt.v1":
                await conn.execute(
                    """
                    insert into run_attempt_settlements(
                      tenant_id, run_id, attempt_id, mapping_state, source_lease_id,
                      receipt_identity, receipt_json, receipt_sha256, receipt_owner_generation,
                      dispatch_state, accepted_context_json, attempt_status_snapshot,
                      attempt_owner_generation_snapshot, run_status_snapshot,
                      settlement_phase, settlement_attempt_count
                    ) values (%s, %s, %s, 'mapped', %s, 'receipt-a',
                      %s::jsonb, %s, %s, 'accepted',
                      %s::jsonb, 'cancel_requested', %s, 'running', 'pending', 1)
                    """,
                    (tenant_id, run_id, attempt_id, f"lease-{suffix}",
                     json.dumps({"status": "succeeded"}), "a" * 64, generation - 1,
                     json.dumps({"accepted": True}), generation),
                )

        persistence = PostgresRunCancellationPersistence(
            attempt_lifecycle=RunAttemptLifecycleService(persistence=run_attempt_persistence),
            append_event=append_event,
            append_audit_log=append_audit_log,
            list_active_sandbox_leases=lambda *_args, **_kwargs: [],
        )
        from app.runs.application.cancellation import CancelRequestResult, RunCancellationPostProcessor

        async def _noop():
            return None

        async def _empty_list():
            return []

        async def handoff(**kwargs):
            if rejection == "wrong_generation":
                kwargs["expected_owner_generation"] += 1
            async with transaction_factory() as conn:
                return await persistence.handoff_cancelled_attempt(conn, **kwargs)

        async def released_cleanup(_leases, **_kwargs):
            return [{"id": f"lease-{suffix}"}]

        first_processor = RunCancellationPostProcessor(
            queue_cleanup=lambda **_kwargs: _noop(),
            sandbox_cleanup=SimpleNamespace(cleanup_cancelled_run_leases=released_cleanup),
            provider_factory=object(), transaction_factory=transaction_factory,
            admit_stream=lambda *_args, **_kwargs: _noop(),
            publish_event=lambda *_args, **_kwargs: _noop(), warn=lambda *_args: None,
            handoff_cancelled_attempt=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("handoff interrupted")),
        )
        receipt = CancelRequestResult(
            run_id, "cancel_requested", None, ({"id": f"lease-{suffix}"},), None,
            attempt_id, generation,
        )
        with pytest.raises(RuntimeError, match="handoff interrupted"):
            await first_processor.post_cancel(
                receipt, tenant_id=tenant_id, user_id=f"user-{suffix}",
                requested_by_role="owner", reason="cancel_requested", worker_capabilities=None,
            )

        repeat_processor = RunCancellationPostProcessor(
            queue_cleanup=lambda **_kwargs: _noop(),
            sandbox_cleanup=SimpleNamespace(cleanup_cancelled_run_leases=lambda *_args, **_kwargs: _empty_list()),
            provider_factory=object(), transaction_factory=transaction_factory,
            admit_stream=lambda *_args, **_kwargs: _noop(),
            publish_event=lambda *_args, **_kwargs: _noop(), warn=lambda *_args: None,
            handoff_cancelled_attempt=handoff,
        )
        if rejection == "missing_marker":
            with pytest.raises(RuntimeError, match="marker_invalid"):
                await repeat_processor.post_cancel(
                    CancelRequestResult(run_id, "cancel_requested", None, (), None, attempt_id, generation),
                    tenant_id=tenant_id, user_id=f"user-{suffix}",
                    requested_by_role="owner", reason="cancel_requested", worker_capabilities=None,
                )
        else:
            await repeat_processor.post_cancel(
                CancelRequestResult(run_id, "cancel_requested", None, (), None, attempt_id, generation),
                tenant_id=tenant_id, user_id=f"user-{suffix}",
                requested_by_role="owner", reason="cancel_requested", worker_capabilities=None,
            )
        async with transaction_factory() as conn:
            final = await (await conn.execute(
                "select owner_kind, owner_id, owner_generation, lease_expires_at, last_heartbeat_at "
                "from run_attempts where tenant_id=%s and id=%s",
                (tenant_id, attempt_id),
            )).fetchone()
            assert final["lease_expires_at"] == before["lease_expires_at"]
            assert final["last_heartbeat_at"] == before["last_heartbeat_at"]
            if rejection:
                assert final["owner_kind"] == "queue_worker"
                assert final["owner_id"] == "worker-" + suffix
                assert int(final["owner_generation"]) == generation
            else:
                assert final["owner_kind"] == "reconciler"
                assert final["owner_id"] == "cancel-cleanup"
                assert int(final["owner_generation"]) == generation + 1
                candidates = await list_stale_run_reconciliation_candidates(
                    conn, stale_after_seconds=60, cancel_requested_after_seconds=5,
                    limit=10, postgres_claim_mode=True,
                )
                candidate = next(row for row in candidates if row["run_id"] == run_id)
                assert await claim_stale_run_reconciliation(
                    conn, candidate={**candidate, "expected_owner_generation": generation},
                    worker_id="stale-owner", stale_after_seconds=60,
                    cancel_requested_after_seconds=5, claim_ttl_seconds=300,
                ) is None
                from app.platform.postgres.errors import RepositoryConflictError
                with pytest.raises(RepositoryConflictError, match="worker_authority_stale"):
                    await run_attempt_persistence.assert_worker_run_attempt_current(
                        conn, tenant_id=tenant_id, run_id=run_id,
                        queue_attempt_id=f"queue-attempt-{suffix}", worker_id=_worker_id,
                    )
        if not rejection:
            configure_context_services()
            service = StaleRunReconciliationService(
                lifecycle=_run_lifecycle_service(),
                attempt_lifecycle=RunAttemptLifecycleService(persistence=run_attempt_persistence),
                v4_capabilities=None, transaction_factory=transaction_factory,
                append_event=append_event, append_audit_log=append_audit_log,
            )
            outcomes = await service.reconcile_postgres_claims(
                worker_id="stale-owner", limit=10, stale_after_seconds=60,
                cancel_requested_after_seconds=5, claim_ttl_seconds=300,
            )
            assert any(row["run_id"] == run_id and row["status"] == "cancelled"
                       and row["did_transition"] for row in outcomes)
            async with transaction_factory() as conn:
                result = await (await conn.execute(
                    "select r.status as run_status, a.status as attempt_status "
                    "from runs r join run_attempts a on a.run_id=r.id and a.tenant_id=r.tenant_id "
                    "where r.tenant_id=%s and r.id=%s and a.id=%s",
                    (tenant_id, run_id, attempt_id),
                )).fetchone()
                assert result == {"run_status": "cancelled", "attempt_status": "cancelled"}
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_cancel_handoff_reader_marker_rejects_unsupported_version():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"cancel_handoff_invalid_marker_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        async with transaction_factory() as conn:
            await conn.execute("savepoint invalid_marker")
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "update run_attempt_settlement_storage set storage_version='invalid' where singleton=true"
                )
            await conn.execute("rollback to savepoint invalid_marker")
            marker = await (await conn.execute(
                "select storage_version from run_attempt_settlement_storage where singleton=true"
            )).fetchone()
            assert marker["storage_version"] == "sandbox_lease.v1"
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_two_connection_run_attempt_claim_writeback_uses_run_first_lock_order():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"stale_run_lock_order_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        suffix = uuid.uuid4().hex[:12]
        async with transaction_factory() as conn:
            tenant_id, run_id, attempt_id, worker_id, _old = await _seed_running_attempt(
                conn, suffix=suffix
            )
        async with transaction_factory() as conn:
            candidate = next(
                row
                for row in await list_stale_run_reconciliation_candidates(
                    conn,
                    stale_after_seconds=60,
                    cancel_requested_after_seconds=5,
                    limit=10,
                    postgres_claim_mode=True,
                )
                if row["run_id"] == run_id
            )
        async with transaction_factory() as conn:
            claim = await claim_stale_run_reconciliation(
                conn,
                candidate=candidate,
                worker_id="worker-finalizer",
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                claim_ttl_seconds=300,
            )
            assert claim is not None
            await prepare_stale_run_attempt_reconciliation(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                terminal_status="failed",
                reconciler_id="stale-run-maintenance",
            )

        run_locked = asyncio.Event()
        contender_started = asyncio.Event()

        async def finalize_run_first():
            async with transaction_factory() as conn:
                await conn.execute(
                    "select id from runs where tenant_id=%s and id=%s for update",
                    (tenant_id, run_id),
                )
                run_locked.set()
                await asyncio.wait_for(contender_started.wait(), timeout=3)
                assert await stale_run_reconciliation_claim_is_current(
                    conn, claim=claim, for_update=True
                )
                await conn.execute(
                    "update runs set status='failed' where tenant_id=%s and id=%s and status='running'",
                    (tenant_id, run_id),
                )
                await transition_run_attempt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    expected_status="expired",
                    requested_status="failed",
                    expected_owner_kind="reconciler",
                    expected_owner_id="stale-run-maintenance",
                    expected_owner_generation=int(claim["expected_owner_generation"]) + 1,
                    terminal_reason="run_failed",
                )

        async def competing_finalizer():
            await asyncio.wait_for(run_locked.wait(), timeout=3)
            async with transaction_factory() as conn:
                contender_started.set()
                current = await stale_run_reconciliation_claim_is_current(
                    conn, claim=claim, for_update=True
                )
                if current:
                    # This is the old claim-first lock order: after taking the
                    # claim, a final writer proceeds to lock the Run.
                    await conn.execute(
                        "select id from runs where tenant_id=%s and id=%s for update",
                        (tenant_id, run_id),
                    )
                return current

        finalize_task = asyncio.create_task(finalize_run_first())
        contender_task = asyncio.create_task(competing_finalizer())
        await asyncio.wait_for(asyncio.gather(finalize_task, contender_task), timeout=6)
        assert contender_task.result() is False

        async with transaction_factory() as conn:
            run = await (await conn.execute(
                "select status from runs where tenant_id=%s and id=%s", (tenant_id, run_id)
            )).fetchone()
            attempt = await (await conn.execute(
                "select status, owner_generation from run_attempts where tenant_id=%s and run_id=%s and id=%s",
                (tenant_id, run_id, attempt_id),
            )).fetchone()
            assert run["status"] == "failed"
            assert attempt["status"] == "failed"
            assert not await stale_run_reconciliation_claim_is_current(conn, claim=claim)
        with pytest.raises(Exception, match="run_attempt_transition_conflict"):
            async with transaction_factory() as conn:
                await transition_run_attempt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    expected_status="running",
                    requested_status="failed",
                    expected_owner_kind="queue_worker",
                    expected_owner_id=worker_id,
                    expected_owner_generation=int(candidate["expected_owner_generation"]),
                    terminal_reason="late_executor_writeback",
                )
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_stale_run_terminalization_after_legacy_receipt_columns_retired():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"stale_run_retired_receipts_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        assert (await prepare_attempt_settlement_storage_cutover(transaction_factory))["status"] == "ready"
        async with transaction_factory() as conn:
            reader = await activate_attempt_settlement_storage(
                conn,
                operator_confirmations={
                    "run_and_sandbox_writers_drained": True,
                    "all_source_writers_n40_compatible": True,
                    "real_sdk_acceptance_complete": True,
                    "real_sandbox_lifecycle_acceptance_complete": True,
                    "fourteen_lifecycle_scenarios_complete": True,
                },
            )
            assert reader["status"] == "activated"
        assert (await prepare_attempt_settlement_writer_cutover(transaction_factory))["status"] == "ready"
        async with transaction_factory() as conn:
            writer = await activate_attempt_settlement_writer(
                conn,
                operator_confirmations={
                    "legacy_writers_drained": True,
                    "inflight_settlement_and_cleanup_operations_drained": True,
                    "real_sdk_acceptance_complete": True,
                    "real_sandbox_lifecycle_acceptance_complete": True,
                    "fourteen_lifecycle_scenarios_complete": True,
                },
            )
            assert writer["status"] == "activated"
            assert (await prepare_attempt_settlement_retirement(conn, schema_authority=schema_migrations))["status"] == "ready"
        async with transaction_factory() as conn:
            retired = await retire_attempt_settlement_legacy_columns(
                conn, schema_authority=schema_migrations,
                operator_confirmations={
                    "legacy_binaries_stopped": True,
                    "runs_and_resource_claims_drained": True,
                    "backup_restore_drill_complete": True,
                },
                minimum_compatible_version="attempt.v1 stale reconciliation regression",
            )
            assert retired["status"] == "retired"

        async with transaction_factory() as conn:
            legacy_columns = await (
                await conn.execute(
                    "select attname from pg_attribute where attrelid = 'sandbox_leases'::regclass "
                    "and attnum > 0 and not attisdropped"
                )
            ).fetchall()
            names = {row["attname"] for row in legacy_columns}
            assert names.isdisjoint(schema_migrations.LEGACY_ATTEMPT_SETTLEMENT_COLUMNS)

            suffix = uuid.uuid4().hex[:10]
            tenant_id, run_id, attempt_id, _worker, _old = await _seed_running_attempt(
                conn, suffix=f"retired-ok-{suffix}"
            )
            pending_tenant, pending_run, pending_attempt, _worker, _old = await _seed_running_attempt(
                conn, suffix=f"retired-receipt-{suffix}"
            )
            candidates = await list_stale_run_reconciliation_candidates(
                conn,
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                limit=20,
                postgres_claim_mode=True,
            )
        assert any(row["run_id"] == run_id for row in candidates)
        pending_candidate = next(row for row in candidates if row["run_id"] == pending_run)

        async with transaction_factory() as conn:
            old_claim = await claim_stale_run_reconciliation(
                conn,
                candidate=pending_candidate,
                worker_id="pre-receipt-worker",
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                claim_ttl_seconds=300,
            )
            assert old_claim is not None
            await conn.execute(
                """insert into sandbox_leases(
                     id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
                     sandbox_mode, status, executor_status, executor_terminal_received_at
                   ) values (%s, %s, %s, %s, %s, %s, %s, 'ephemeral', 'released',
                             'completed', clock_timestamp())""",
                (
                    f"lease-pending-{suffix}", pending_tenant,
                    f"workspace-retired-receipt-{suffix}",
                    f"user-retired-receipt-{suffix}",
                    f"session-retired-receipt-{suffix}", pending_run, pending_attempt,
                ),
            )
            assert not await stale_run_reconciliation_claim_is_current(conn, claim=old_claim)
            assert await claim_stale_run_reconciliation(
                conn,
                candidate=pending_candidate,
                worker_id="post-receipt-worker",
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                claim_ttl_seconds=300,
            ) is None

        lifecycle = RunLifecycleService(
            persistence=PostgresRunLifecyclePersistence(),
            append_event=append_event,
            append_audit_log=append_audit_log,
            validate_result_size=require_run_result_size,
            sanitize_payload=sanitize_public_payload,
            sanitize_text=sanitize_public_text,
            make_trace_id=standard_trace_id,
        )
        configure_context_services()
        service = StaleRunReconciliationService(
            lifecycle=lifecycle,
            attempt_lifecycle=RunAttemptLifecycleService(persistence=run_attempt_persistence),
            v4_capabilities=None,
            transaction_factory=transaction_factory,
            append_event=append_event,
            append_audit_log=append_audit_log,
        )
        results = await service.reconcile_postgres_claims(
            worker_id="post-retirement-reconciler",
            limit=20,
            stale_after_seconds=60,
            cancel_requested_after_seconds=5,
            claim_ttl_seconds=300,
        )
        assert any(
            row["run_id"] == run_id and row["status"] == "failed" and row["did_transition"]
            for row in results
        )
        assert not any(row["run_id"] == pending_run for row in results)
        async with transaction_factory() as conn:
            terminal = await (await conn.execute(
                "select status from runs where tenant_id=%s and id=%s", (tenant_id, run_id)
            )).fetchone()
            attempt = await (await conn.execute(
                "select status from run_attempts where tenant_id=%s and run_id=%s and id=%s",
                (tenant_id, run_id, attempt_id),
            )).fetchone()
            assert terminal["status"] == "failed"
            assert attempt["status"] == "failed"
            stale_events = await (await conn.execute(
                "select count(*)::int as count from run_events "
                "where tenant_id=%s and run_id=%s and event_type='stale_run_reconciled'",
                (tenant_id, run_id),
            )).fetchone()
            stale_audits = await (await conn.execute(
                "select count(*)::int as count from audit_logs "
                "where tenant_id=%s and target_id=%s and action='run.stale.reconcile'",
                (tenant_id, run_id),
            )).fetchone()
            assert stale_events["count"] == 1
            assert stale_audits["count"] == 1

        assert await service.reconcile_postgres_claims(
            worker_id="post-retirement-reconciler",
            limit=20,
            stale_after_seconds=60,
            cancel_requested_after_seconds=5,
            claim_ttl_seconds=300,
        ) == []
    finally:
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.asyncio
async def test_real_redis_lease_heartbeat_loses_postgres_attempt_cas_after_claim(monkeypatch):
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    redis_url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    if not redis_url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    schema = f"stale_run_redis_cas_{uuid.uuid4().hex}"
    suffix = uuid.uuid4().hex[:12]
    tenant_id = f"tenant-{suffix}"
    run_id = f"run-{suffix}"
    worker_id = f"worker-{suffix}"
    queue_prefix = f"ai-platform:test:stale-claim:{suffix}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    redis = Redis.from_url(redis_url, decode_responses=True)
    redis_keys: tuple[str, ...] = ()

    class Settings:
        queue_key_prefix = queue_prefix
        queue_lease_scan_limit = 10
        worker_heartbeat_ttl_seconds = 60

    async def get_redis():
        return Redis.from_url(redis_url, decode_responses=True)

    monkeypatch.setattr(queue, "get_settings", lambda: Settings())
    monkeypatch.setattr(queue, "get_redis", get_redis)
    try:
        await admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))

        @asynccontextmanager
        async def transaction_factory():
            conn = await psycopg.AsyncConnection.connect(
                dsn, options=f"-c search_path={schema}", row_factory=dict_row
            )
            try:
                async with conn.transaction():
                    yield conn
            finally:
                await conn.close()

        async def index_connection_factory():
            return await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, options=f"-c search_path={schema}", row_factory=dict_row
            )

        await schema_migrations.apply_migrations(
            transaction_factory=transaction_factory,
            index_connection_factory=index_connection_factory,
        )
        payload = QueueRunPayload(
            tenant_id=tenant_id,
            workspace_id=f"workspace-{suffix}",
            user_id=f"user-{suffix}",
            session_id=f"session-{suffix}",
            run_id=run_id,
            agent_id=f"agent-{suffix}",
            skill_id=f"skill-{suffix}",
            file_ids=[],
            input={"message": "redis and postgres stale owner race"},
            executor_type="fake",
            skill_version="version-a",
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "selected_version": "version-a",
            },
            skill_manifests=[
                {
                    "schema_version": "ai-platform.skill-materialization-ref.v1",
                    "skill_id": f"skill-{suffix}",
                    "version": "version-a",
                    "content_hash": "version-a",
                    "materialization_sha256": "a" * 64,
                }
            ],
        )
        await queue.enqueue_run(payload.model_dump())
        message = await queue.lease_run(worker_id=worker_id, lease_scan_limit=10)
        assert message is not None
        keys = queue.get_queue_keys()
        redis_keys = tuple(getattr(keys, field.name) for field in fields(keys))

        async with transaction_factory() as conn:
            tenant_id, run_id, attempt_id, seeded_worker_id, _old = await _seed_running_attempt(
                conn,
                suffix=suffix,
                queue_attempt_id=message.attempt_id,
                queue_message_id=message.queue_message_id,
            )
            assert seeded_worker_id == worker_id
            attempt_identity = await (await conn.execute(
                """select queue_attempt_id, queue_message_id
                   from run_attempts where tenant_id=%s and run_id=%s and id=%s""",
                (tenant_id, run_id, attempt_id),
            )).fetchone()
            assert attempt_identity == {
                "queue_attempt_id": message.attempt_id,
                "queue_message_id": message.queue_message_id,
            }
            candidates = await list_stale_run_reconciliation_candidates(
                conn,
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                limit=10,
                postgres_claim_mode=True,
            )
        candidate = next(row for row in candidates if row["run_id"] == run_id)

        async with transaction_factory() as conn:
            claim = await claim_stale_run_reconciliation(
                conn,
                candidate=candidate,
                worker_id="stale-run-claimer",
                stale_after_seconds=60,
                cancel_requested_after_seconds=5,
                claim_ttl_seconds=300,
            )
            assert claim is not None
            reconciled_attempt = await prepare_stale_run_attempt_reconciliation(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                terminal_status="failed",
                reconciler_id="stale-run-maintenance",
            )
            assert reconciled_attempt is not None
            assert reconciled_attempt["status"] == "expired"
            assert reconciled_attempt["owner_kind"] == "reconciler"

        redis_heartbeat = await queue.heartbeat_run(
            message.message_id,
            worker_id=worker_id,
        )
        assert redis_heartbeat.status == "heartbeat"
        assert redis_heartbeat.heartbeat_at is not None
        from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService
        from app.runs.infrastructure import postgres as run_attempt_persistence
        from app.platform.postgres.errors import RepositoryConflictError

        heartbeat_at = datetime.fromtimestamp(redis_heartbeat.heartbeat_at, tz=timezone.utc)
        with pytest.raises(RepositoryConflictError, match="run_attempt_worker_authority_stale"):
            async with transaction_factory() as conn:
                await RunAttemptLifecycleService(persistence=run_attempt_persistence).heartbeat_worker(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    queue_attempt_id=message.attempt_id,
                    queue_message_id=message.queue_message_id,
                    worker_id=worker_id,
                    last_heartbeat_at=heartbeat_at,
                    lease_expires_at=heartbeat_at + timedelta(seconds=60),
                )
        assert await redis.lrange(keys.processing, 0, -1) == [message.raw]
        async with transaction_factory() as conn:
            # The old Worker loses execution authority; the current reconciler
            # must retain its claim so it can finish the same Attempt.
            assert await stale_run_reconciliation_claim_is_current(conn, claim=claim)
    finally:
        if redis_keys:
            await redis.delete(*redis_keys)
        await admin.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        await admin.close()
        await redis.aclose()


async def _noop(*_args, **_kwargs):
    return None
