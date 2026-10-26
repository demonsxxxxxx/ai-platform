"""Real database wall-clock expiry while the external authority port is pending."""
# ruff: noqa: F401,F811 -- imported owning PostgreSQL fixture.
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from app.bootstrap.recovery_operations import _current_authority
from app.execution.api import RecoveryOperationError
from app.runs.api import RunInputConflict
from tests.test_mcp_public_operation_summary import DECLARATION
from tests.test_recovery_operations_postgres import operation_db


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["query", "started", "manual_answer"])
async def test_database_lease_expiry_during_principal_lookup_rolls_back_grants(operation_db, monkeypatch, operation):
    from app import principal_authority
    from app.context import api as context_api
    from app.mcp import api as mcp_api
    connect, original, inputs, source, current, key = operation_db
    subject = dict(identity=current.tool_identity, mcp_server="vendor", mcp_tool="write",
                   capability_id="vendor::write", write_capable=True, public_operation_summary=DECLARATION)
    recovery = dict(state="recovery_candidate", source_run_id=source.run_id,
                    source_attempt_id=source.attempt_id, provider_epoch_id="epoch", final_sequence=1)
    question_id = None
    async with connect() as conn, conn.transaction():
        await conn.execute("""
            alter table run_attempts add column execution_spec_json jsonb;
            create table sandbox_leases(tenant_id text, workspace_id text, user_id text, session_id text,
                run_id text, attempt_id text, status text, released_at timestamptz,
                expires_at timestamptz, lease_payload_json jsonb);
            create table mcp_servers(tenant_id text, name text);
            create table tenant_capability_distributions(tenant_id text, capability_kind text, capability_id text);
            insert into mcp_servers values('tenant-a','vendor');
            insert into tenant_capability_distributions values('tenant-a','mcp_server','vendor');
        """)
        for invocation, frozen in ((source, {}), (current, {"recovery": recovery})):
            await conn.execute("update run_attempts set execution_spec_json=%s where id=%s", (
                Jsonb({"input": {"_runtime_tool_policy_subjects": [subject]}, "context_pack": {"conversation_context": frozen}}), invocation.attempt_id))
        await conn.execute("update execution_recovery_invocations set public_summary='创建项目；项目名称：华东项目'")
        if operation in {"started", "manual_answer"}:
            question_id = (await original.query(conn, invocation=current))["question_id"]
        if operation == "started":
            await inputs.submit(conn, tenant_id=current.scope.tenant_id, user_id=current.scope.user_id,
                run_id=current.run_id, input_id=str(uuid4()), question_id=question_id, answers={"q0": "o2"})
        await conn.execute("insert into sandbox_leases values(%s,%s,%s,%s,%s,%s,'active',null,clock_timestamp()+interval '1 hour',%s)", (
            *current.scope.mapping().values(), current.run_id, current.attempt_id, Jsonb({"owner_generation": current.owner_generation})))
    entered, release = asyncio.Event(), asyncio.Event()
    async def principal(**kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(department_id="qa", department_ids=("company", "qa"), roles=["user"], permissions=[], source="test")
    async def authorize(*args, **kwargs):
        assert kwargs["principal_department_id"] == "qa"
        assert kwargs["principal_department_ids"] == ("company", "qa")
        return [{"public_operation_summary": DECLARATION}]
    async def native(*args, **kwargs):
        return {"project": {"name": "华东项目"}}
    monkeypatch.setattr(principal_authority, "resolve_current_principal", principal)
    monkeypatch.setattr(mcp_api, "authorize_selected_chat_mcp_tools", authorize)
    monkeypatch.setattr(context_api, "read_verified_native_invocation", native)
    service = replace(original, authorize_current=_current_authority)
    inputs = replace(inputs, on_manual_answer=service.on_manual_answer)
    async def act():
        async with connect() as conn, conn.transaction():
            # Transaction time precedes expiry. Row locks cannot freeze the wall clock.
            row = await (await conn.execute("update sandbox_leases set expires_at=clock_timestamp()+interval '1 second' returning expires_at, now() as transaction_time")).fetchone()
            expires_at = row["expires_at"]
            assert row["transaction_time"] < expires_at
            async def observe_expiry():
                await entered.wait()
                async with connect() as observer:
                    await observer.execute("select pg_sleep(greatest(0,extract(epoch from (%s::timestamptz-clock_timestamp())))+0.02)", (expires_at,))
                    assert (await (await observer.execute("select clock_timestamp()>%s as expired", (expires_at,))).fetchone())["expired"]
                release.set()
            observer_task = asyncio.create_task(observe_expiry())
            try:
                if operation == "query":
                    await service.query(conn, invocation=current)
                elif operation == "started":
                    await service.record_started(conn, invocation=current, tool_facts={"write_capable": True}, public_summary="当前调用")
                else:
                    await inputs.submit(conn, tenant_id=current.scope.tenant_id, user_id=current.scope.user_id,
                        run_id=current.run_id, input_id=str(uuid4()), question_id=question_id, answers={"q0": "o2"})
            finally:
                release.set()
                await asyncio.wait_for(observer_task, 5)
    error_code = "run_input_recovery_answer_invalid" if operation == "manual_answer" else "recovery_operation_authority_stale"
    with pytest.raises((RecoveryOperationError, RunInputConflict), match=error_code):
        await asyncio.wait_for(act(), 8)
    async with connect() as conn:
        binding = await service.persistence.get_binding(conn, invocation=current)
        row = await service.persistence.get_operation(conn, scope=current.scope, operation_key=key)
        assert not binding["started"]
        assert row["verified_outcome"] == ("not_effective" if operation == "started" else "unknown")
        assert binding["answer_input_id"] is not None if operation == "started" else binding["answer_input_id"] is None
        assert (await (await conn.execute("select count(*) as n from execution_recovery_operations")).fetchone())["n"] == 1
        assert (await (await conn.execute("select count(*) as n from run_inputs")).fetchone())["n"] == (1 if operation == "started" else 0)
        assert (await (await conn.execute("select count(*) as n from run_input_questions")).fetchone())["n"] == (0 if operation == "query" else 1)
