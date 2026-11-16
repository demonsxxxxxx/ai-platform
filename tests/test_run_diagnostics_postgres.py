from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.platform.tracing import safe_exception_log_fields
from tests.support.db_transactions import event_loop_policy  # noqa: F401

from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.infrastructure.diagnostics_postgres import (
    PostgresRunDiagnosticsRepository,
)
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)


POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _seed_run(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')"
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'A')"
    )
    await conn.execute(
        "insert into agents(id, tenant_id, name, agent_type) values "
        "('agent-a', 'tenant-a', 'A', 'chat')"
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values "
        "('skill-a', 'A', '1', 'fake')"
    )
    await conn.execute(
        """
        insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title, status)
        values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A', 'active')
        """
    )
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status
        ) values (
          'run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a',
          'agent-a', 'skill-a', 'running'
        )
        """
    )


def _runtime_diagnostics(error_code: str) -> dict:
    return {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": error_code,
        "failure_source": "sdk_exception",
        "failure_stage": "model_wait",
        "sdk": {
            "exception_type": "TimeoutError",
            "exception_message": f"{error_code} detail",
        },
    }


@asynccontextmanager
async def _isolated_postgres_run():
    dsn = _postgres_dsn()
    schema_name = f"run_diagnostics_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    conn = None
    try:
        await admin.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await _seed_run(admin)
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        yield dsn, schema_name, admin, conn
    finally:
        if conn is not None:
            await conn.close()
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_postgres_run_diagnostics_are_tenant_scoped_bounded_and_idempotent():
    dsn = _postgres_dsn()
    schema_name = f"run_diagnostics_{uuid.uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    conn = None
    try:
        await admin.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema_name))
        )
        await admin.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await _seed_run(admin)
        conn = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
            persistence=PostgresRunDiagnosticsRepository(),
            normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
            runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        )
        first_result = {
            "message": "safe",
            "runtime_diagnostics": _runtime_diagnostics("provider_timeout"),
        }
        async with conn.transaction():
            public_result = await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_http_failure",
                result_json=first_result,
            )
            await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_http_failure",
                result_json=first_result,
            )
        async with conn.transaction():
            await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="executor_reconciler",
                stage="reconciliation",
                error_code="terminal_reconciliation_failed",
                result_json={
                    "runtime_diagnostics": _runtime_diagnostics(
                        "terminal_reconciliation_failed"
                    )
                },
            )
        async with conn.transaction():
            snapshot = await service.read_admin(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
            )
            foreign = await service.read_admin(
                conn,
                tenant_id="other-tenant",
                run_id="run-a",
            )
        cursor = await conn.execute(
            "select revision, payload_json from run_diagnostics "
            "where tenant_id = 'tenant-a' and run_id = 'run-a'"
        )
        stored = await cursor.fetchone()

        assert public_result == {"message": "safe"}
        assert stored["revision"] == 2
        assert len(stored["payload_json"]["observations"]) == 2
        assert snapshot["coverage"] == "full"
        assert snapshot["root"]["error_code"] == "provider_timeout"
        assert snapshot["handling"][-1]["error_code"] == (
            "terminal_reconciliation_failed"
        )
        assert foreign is None
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin.execute(
                "update run_diagnostics "
                "set payload_json = payload_json - 'schema_version' "
                "where tenant_id = 'tenant-a' and run_id = 'run-a'"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await admin.execute(
                "update run_diagnostics "
                "set payload_json = jsonb_set(payload_json, '{schema_version}', 'null'::jsonb) "
                "where tenant_id = 'tenant-a' and run_id = 'run-a'"
            )
    finally:
        if conn is not None:
            await conn.close()
        await admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await admin.close()


@pytest.mark.asyncio
async def test_postgres_diagnostic_constraint_failure_keeps_outer_terminal_write():
    async with _isolated_postgres_run() as (_dsn, _schema, admin, conn):
        await admin.execute(
            "alter table run_diagnostics add constraint reject_diagnostic_write "
            "check (revision < 0) not valid"
        )
        service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
            persistence=PostgresRunDiagnosticsRepository(write_timeout_seconds=0.1),
            normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
            runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        )

        async with conn.transaction():
            cursor = await conn.execute(
                "select current_setting('lock_timeout') as lock_timeout, "
                "current_setting('statement_timeout') as statement_timeout"
            )
            before = dict(await cursor.fetchone())
            public_result = await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_failure",
                result_json={
                    "message": "safe",
                    "runtime_diagnostics": _runtime_diagnostics("provider_timeout"),
                },
            )
            cursor = await conn.execute(
                "select current_setting('lock_timeout') as lock_timeout, "
                "current_setting('statement_timeout') as statement_timeout"
            )
            after = dict(await cursor.fetchone())
            await conn.execute(
                "update runs set status = 'failed', result_json = %s::jsonb "
                "where tenant_id = 'tenant-a' and id = 'run-a'",
                (json.dumps(public_result),),
            )

        cursor = await conn.execute(
            "select status, result_json from runs where id = 'run-a'"
        )
        run = await cursor.fetchone()
        cursor = await conn.execute("select count(*) as count from run_diagnostics")
        diagnostic_count = int((await cursor.fetchone())["count"])

        assert before == after
        assert run == {"status": "failed", "result_json": {"message": "safe"}}
        assert diagnostic_count == 0


@pytest.mark.asyncio
async def test_postgres_diagnostic_row_lock_timeout_keeps_outer_transaction_usable():
    async with _isolated_postgres_run() as (dsn, schema_name, _admin, conn):
        service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
            persistence=PostgresRunDiagnosticsRepository(write_timeout_seconds=0.05),
            normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
            runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        )
        async with conn.transaction():
            await service.capture_failure_result(
                conn,
                tenant_id="tenant-a",
                run_id="run-a",
                attempt_id="attempt-a",
                source="worker_executor",
                stage="terminalization",
                error_code="executor_failure",
                result_json={
                    "runtime_diagnostics": _runtime_diagnostics("provider_timeout")
                },
            )

        blocker = await psycopg.AsyncConnection.connect(
            dsn,
            options=f"-c search_path={schema_name}",
            row_factory=dict_row,
        )
        try:
            async with blocker.transaction():
                await blocker.execute(
                    "select revision from run_diagnostics "
                    "where tenant_id = 'tenant-a' and run_id = 'run-a' for update"
                )
                async with conn.transaction():
                    cursor = await conn.execute(
                        "select current_setting('lock_timeout') as lock_timeout, "
                        "current_setting('statement_timeout') as statement_timeout"
                    )
                    before = dict(await cursor.fetchone())
                    started_at = time.monotonic()
                    await service.capture_failure_result(
                        conn,
                        tenant_id="tenant-a",
                        run_id="run-a",
                        attempt_id="attempt-a",
                        source="executor_reconciler",
                        stage="terminalization",
                        error_code="terminal_reconciliation_failed",
                        result_json={
                            "runtime_diagnostics": _runtime_diagnostics(
                                "terminal_reconciliation_failed"
                            )
                        },
                    )
                    elapsed = time.monotonic() - started_at
                    cursor = await conn.execute(
                        "select current_setting('lock_timeout') as lock_timeout, "
                        "current_setting('statement_timeout') as statement_timeout"
                    )
                    after = dict(await cursor.fetchone())
                    await conn.execute(
                        "update runs set status = 'failed' "
                        "where tenant_id = 'tenant-a' and id = 'run-a'"
                    )
        finally:
            await blocker.close()

        cursor = await conn.execute(
            "select r.status, d.revision "
            "from runs r join run_diagnostics d on d.run_id = r.id "
            "where r.tenant_id = 'tenant-a' and r.id = 'run-a'"
        )
        state = await cursor.fetchone()

        assert before == after
        assert elapsed < 5
        assert state == {"status": "failed", "revision": 1}


async def _seed_detail_attempt(conn):
    spec = json.dumps({"schema_version": "test.execution.v1", "tenant_id": "tenant-a", "run_id": "run-a",
                       "workspace_id": "workspace-a", "user_id": "user-a", "session_id": "session-a",
                       "agent_id": "agent-a", "execution_kind": "skill", "skill_id": "skill-a"})
    await conn.execute("update runs set status='queued' where id='run-a'")
    await conn.execute("""insert into run_attempts(id,tenant_id,run_id,ordinal,status,owner_kind,owner_id,
        queue_attempt_id,execution_spec_schema_version,execution_spec_json,execution_spec_canonical_json,execution_spec_sha256)
        values('attempt-a','tenant-a','run-a',1,'created','queue_worker','worker-a','queue-a','test.execution.v1',
               %s::jsonb,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'))""", (spec, spec, spec))
    for state in ('queued', 'claimed', 'running'):
        await conn.execute("update run_attempts set status=%s,owner_generation=owner_generation+1 where id='attempt-a'", (state,))


@pytest.mark.asyncio
async def test_postgres_detail_bounded_answer_ledger_hidden_payloads_and_search_scope():
    from copy import deepcopy
    from app.bootstrap.admin_run_evidence import configure_admin_run_evidence
    from app.runs.infrastructure.admin_queries_postgres import get_admin_run_detail
    from app.streaming.infrastructure.admin_detail_postgres import read_admin_detail_events
    from app.runs.application.admin_public_result import build_admin_public_result
    configure_admin_run_evidence()
    async with _isolated_postgres_run() as (_dsn, _schema, _admin, conn):
        async with conn.transaction():
            await _seed_detail_attempt(conn)
            fixture = json.loads(Path('tests/fixtures/admin-part-message.json').read_text(encoding='utf-8'))
            for source in fixture['rows']:
                payload = deepcopy(source['payload_json'])
                payload['__stream_v4']['attempt_id'] = 'attempt-a'
                await conn.execute("""insert into run_events(id,tenant_id,run_id,sequence,event_type,stage,visible_to_user,payload_json)
                    values(%s,'tenant-a','run-a',%s,%s,%s,true,%s::jsonb)""",
                    (source['id'], source['sequence'], source['event_type'], source['stage'], json.dumps(payload)))
            await conn.execute("""insert into run_events(id,tenant_id,run_id,sequence,event_type,stage,visible_to_user,payload_json)
                values('hidden','tenant-a','run-a',8,'model.completed','agent_kernel',false,'{"stop_category":"PRIVATE"}')""")
            detail = await get_admin_run_detail(conn, tenant_id='tenant-a', run_id='run-a')
            assert detail['worker_execution']['response'] == '正常公开回答'
            assert detail['worker_execution']['model'] == {}
            for sequence, (kind, payload) in enumerate([
                ('message.delta', {'delta': 'FOREIGN_ANSWER'}),
                ('model.completed', {'stop_category': 'FOREIGN_STOP'}),
                ('tool.started', {'operation_id':'foreign','display_name':'FOREIGN_TOOL'}),
            ], 9):
                payload['__stream_v4'] = {'version':1,'message_id':'msg_foreign','attempt_id':'foreign-attempt'}
                await conn.execute("""insert into run_events(id,tenant_id,run_id,sequence,event_type,stage,visible_to_user,payload_json)
                    values(%s,'tenant-a','run-a',%s,%s,'agent_kernel',true,%s::jsonb)""",
                    ('foreign-'+str(sequence),sequence,kind,json.dumps(payload)))
            rejected = await read_admin_detail_events(conn, tenant_id='tenant-a',run_id='run-a')
            assert rejected['rejected_attempt_events'] == 3
            safe = build_admin_public_result(rejected,tenant_id='tenant-a',run_id='run-a')
            assert safe['response'] == '正常公开回答' and 'FOREIGN' not in json.dumps(safe,default=str)
            await conn.execute("delete from run_events where id like 'foreign-%'")
            assert detail['events'] == [] and detail['run']['input'] == detail['run']['result'] == {}
            assert 'PRIVATE' not in json.dumps(detail, default=str)
            assert await get_admin_run_detail(conn, tenant_id='foreign', run_id='run-a') is None
            await conn.execute("""insert into run_events(id,tenant_id,run_id,sequence,event_type,stage,visible_to_user,payload_json)
                select 'bulk-'||n,'tenant-a','run-a',n+8,'unknown','worker',true,jsonb_build_object('private','PRIVATE')
                from generate_series(1,10000) n""")
            bounded = await read_admin_detail_events(conn, tenant_id='tenant-a', run_id='run-a')
            assert bounded['truncated'] and len(bounded['rows']) <= 2000
            assert build_admin_public_result(bounded, tenant_id='tenant-a', run_id='run-a')['response'] == ''
            service = RunDiagnosticsService(persistence=PostgresRunDiagnosticsRepository(), exception_log_fields=safe_exception_log_fields,
                normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics, runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION)
            search = await service.search_admin_evidence(conn, tenant_id='tenant-a', run_id='run-a', query='PRIVATE')
            assert search['matches'] == [] and search['searched_source_count'] == 1000 and search['has_more']
            assert await service.compare_admin_runs(conn, tenant_id='foreign', left_run_id='run-a', right_run_id='run-a') is None
            comparison = await service.compare_admin_runs(conn, tenant_id='tenant-a', left_run_id='run-a', right_run_id='run-a')
            assert all(metric['difference'] is None for metric in comparison['metrics'])
            assert comparison['results']['left']['coverage']['truncated']


@pytest.mark.asyncio
async def test_postgres_model_call_final_binding_survives_terminalization_and_stays_private():
    from app.bootstrap.admin_run_evidence import configure_admin_run_evidence
    from app.runs.infrastructure.model_call_evidence_postgres import append_model_call_evidence, get_recorded_model_call_binding
    configure_admin_run_evidence()
    async with _isolated_postgres_run() as (_dsn, _schema, _admin, conn):
        async with conn.transaction():
            await _seed_detail_attempt(conn)
            call = {'call_id': 'mcall_' + 'a' * 32, 'state': 'started'}
            await append_model_call_evidence(conn, tenant_id='tenant-a', run_id='run-a', attempt_id='attempt-a', evidence=call)
            await conn.execute("update run_attempts set status='succeeded',owner_generation=owner_generation+1,finished_at=now() where id='attempt-a'")
            assert await PostgresRunDiagnosticsRepository().get_execution_debug_binding(conn, run_id='run-a', attempt_id='attempt-a') is None
            binding = await get_recorded_model_call_binding(conn, run_id='run-a', attempt_id='attempt-a', call_id=call['call_id'])
            assert binding['tenant_id'] == 'tenant-a'
            assert await get_recorded_model_call_binding(conn, run_id='run-a', attempt_id='another', call_id=call['call_id']) is None
            await append_model_call_evidence(conn, tenant_id='tenant-a', run_id='run-a', attempt_id='attempt-a', evidence={**call,'state':'completed'})
            cursor = await conn.execute("select sequence,visible_to_user from run_events order by sequence")
            rows = list(await cursor.fetchall())
            assert len(rows) == 2 and all(not row['visible_to_user'] for row in rows)
            assert rows[0]['sequence'] < rows[1]['sequence']


@pytest.mark.asyncio
async def test_postgres_run_artifact_and_admitted_input_file_bindings_reject_other_scopes():
    from app.artifacts.infrastructure.admin_run_access_postgres import get_admin_run_artifact
    from app.files.infrastructure.admin_run_inputs_postgres import read_admin_run_input_files
    async with _isolated_postgres_run() as (_dsn, _schema, _admin, conn):
        async with conn.transaction():
            await conn.execute("""insert into artifacts(id,tenant_id,run_id,artifact_type,label,content_type,storage_key,size_bytes)
                values('artifact-a','tenant-a','run-a','pdf','report.pdf','application/pdf','PRIVATE',10)""")
            assert (await get_admin_run_artifact(conn, tenant_id='tenant-a', run_id='run-a', artifact_id='artifact-a'))['id'] == 'artifact-a'
            assert await get_admin_run_artifact(conn, tenant_id='foreign', run_id='run-a', artifact_id='artifact-a') is None
            assert await get_admin_run_artifact(conn, tenant_id='tenant-a', run_id='run-b', artifact_id='artifact-a') is None
            await conn.execute("""insert into files(id,tenant_id,workspace_id,user_id,session_id,run_id,original_name,content_type,storage_key,size_bytes,sha256)
                values('file-a','tenant-a','workspace-a','user-a','session-a','run-a','input.pdf','application/pdf','PRIVATE-input',10,'synthetic')""")
            await conn.execute("""insert into run_context_snapshots(id,tenant_id,workspace_id,user_id,session_id,run_id,included_file_ids)
                values('snapshot-a','tenant-a','workspace-a','user-a','session-a','run-a','["file-a"]')""")
            await conn.execute("""update runs set context_snapshot_id='snapshot-a',input_json='{"context_snapshot_id":"snapshot-a","context_snapshot":{"context_snapshot_id":"snapshot-a"}}' where id='run-a'""")
            references = await read_admin_run_input_files(conn, tenant_id='tenant-a', run_id='run-a')
            assert references[0]['file_id'] == 'file-a' and 'PRIVATE' not in json.dumps(references)
            assert await read_admin_run_input_files(conn, tenant_id='foreign', run_id='run-a') == []
            await conn.execute("update sessions set status='deleted' where id='session-a'")
            assert await get_admin_run_artifact(conn, tenant_id='tenant-a', run_id='run-a', artifact_id='artifact-a') is None


@pytest.mark.asyncio
async def test_postgres_run_trigger_is_atomic_private_and_not_read_from_historical_input():
    from app.bootstrap.admin_run_evidence import configure_admin_run_evidence
    from app.runs.infrastructure.creation_postgres import create_run
    from app.runs.application.admin_run_inspection import build_admin_run_inspection
    configure_admin_run_evidence()
    async with _isolated_postgres_run() as (_dsn, _schema, _admin, conn):
        async with conn.transaction():
            repository = PostgresRunDiagnosticsRepository()
            await conn.execute("""update runs set input_json='{"_trusted_run_source":{"version":1,"source":"copy"}}'
                where id='run-a'""")
            historical = await repository.get_admin_inspection_facts(conn, tenant_id='tenant-a', run_id='run-a')
            assert '_trusted_run_source' not in historical['input']
            for source in ('chat_api', 'direct_skill', 'copy', 'retry', 'resume'):
                run_id = await create_run(conn, tenant_id='tenant-a', workspace_id='workspace-a',
                    session_id='session-a', user_id='user-a', agent_id='agent-a', skill_id='skill-a',
                    input_json={'_trusted_run_source': {'version': 1, 'source': 'copy'}}, trigger_source=source)
                facts = await repository.get_admin_inspection_facts(conn, tenant_id='tenant-a', run_id=run_id)
                inspection = build_admin_run_inspection({'run_id': run_id}, skills=[],
                    relationships=facts['relationships'], input_value=facts['input'])
                assert inspection['trigger'] == {'kind': source, 'coverage': 'recorded'}
                cursor = await conn.execute("""select r.input_json, e.visible_to_user, e.payload_json from runs r
                    join run_events e on e.tenant_id=r.tenant_id and e.run_id=r.id
                    where r.id=%s and e.event_type='run_trigger_recorded'""", (run_id,))
                rows = list(await cursor.fetchall())
                assert len(rows) == 1 and rows[0]['visible_to_user'] is False
                assert rows[0]['input_json'] == {}
                assert rows[0]['payload_json'] == {'version': 1, 'source': source}
            with pytest.raises(ValueError, match='run_trigger_source_invalid'):
                await create_run(conn, tenant_id='tenant-a', workspace_id='workspace-a',
                    session_id='session-a', user_id='user-a', agent_id='agent-a', skill_id='skill-a',
                    input_json={}, run_id='invalid-trigger', trigger_source='client_invented')
            assert await (await conn.execute("select 1 from runs where id='invalid-trigger'")).fetchone() is None


@pytest.mark.asyncio
async def test_postgres_search_never_loads_private_or_oversized_payloads_and_advances_cursor():
    async with _isolated_postgres_run() as (_dsn, _schema, _admin, conn):
        async with conn.transaction():
            payload = json.dumps({'private': 'PRIVATE_LARGE' * 10000})
            for sequence, visible in ((1, True), (2, False)):
                await conn.execute("""insert into run_events(id,tenant_id,run_id,sequence,event_type,stage,visible_to_user,payload_json)
                    values(%s,'tenant-a','run-a',%s,'custom','worker',%s,%s::jsonb)""",
                    ('large-'+str(sequence),sequence,visible,payload))
            repository = PostgresRunDiagnosticsRepository()
            rows = await repository.get_admin_evidence_page(conn,tenant_id='tenant-a',run_id='run-a',after_sequence=0,limit=1001)
            assert [row['payload_json'] for row in rows] == [{},{}]
            assert [row['payload_omitted'] for row in rows] == [True,False]
            service = RunDiagnosticsService(persistence=repository,exception_log_fields=safe_exception_log_fields,
                normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION)
            result = await service.search_admin_evidence(conn,tenant_id='tenant-a',run_id='run-a',query='PRIVATE_LARGE')
            assert result['matches'] == [] and result['payload_omitted_count'] == 1
            assert result['next_after_sequence'] == 2
