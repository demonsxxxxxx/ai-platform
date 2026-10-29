"""Recovery authority must outlive an awaited company-principal lookup."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.bootstrap.recovery_operations import _current_authority
from app.execution.api import RecoveryOperationError
from tests.test_mcp_public_operation_summary import DECLARATION
from tests.test_recovery_operations import scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["query", "started", "manual_answer"])
@pytest.mark.parametrize("expires_during_lookup", [True, False])
async def test_pending_principal_lookup_rechecks_live_authority_before_any_grant(monkeypatch, operation, expires_during_lookup):
    from app import principal_authority
    from app.context import api as context_api
    from app.mcp import api as mcp_api

    s = await scenario()
    subject = dict(identity=s.current.tool_identity, mcp_server="vendor", mcp_tool="write",
                   capability_id="vendor::write", write_capable=True, public_operation_summary=DECLARATION)
    recovery = dict(state="recovery_candidate", source_run_id=s.source.run_id,
                    source_attempt_id=s.source.attempt_id, provider_epoch_id="epoch", final_sequence=1)
    binding = next(iter(s.store.bindings.values()))
    binding["public_summary"] = "创建项目；项目名称：华东项目"
    question_id = None
    if operation == "manual_answer":
        question_id = (await s.service.query(None, invocation=s.current))["question_id"]
    if operation == "started":
        binding["authorization"] = "retry"
        s.store.operations[s.operation_key]["verified_outcome"] = "not_effective"
    before_outcome = s.store.operations[s.operation_key]["verified_outcome"]
    before_questions = len(s.input_store.questions)
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

    class Conn:
        expired = False
        final_checks = 0

        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            async def row():
                if "join sandbox_leases lease" in query:
                    self.final_checks += 1
                    assert "clock_timestamp()" in query
                    assert "attempt.owner_generation" in query
                    return None if self.expired else {"exists": 1}
                if "from runs owner" in query:
                    return {"frozen": {"recovery": recovery}, "subjects": [subject]}
                if "as subjects" in query:
                    return {"subjects": [subject]}
                return {"exists": 1}
            return SimpleNamespace(fetchone=row)

    conn = Conn()
    service = replace(s.service, authorize_current=_current_authority)
    async def act():
        if operation == "query":
            return await service.query(conn, invocation=s.current)
        if operation == "started":
            return await service.record_started(conn, invocation=s.current, tool_facts=s.facts, public_summary="当前操作")
        return await service.on_manual_answer(conn, tenant_id="tenant-a", user_id="user-a",
            run_id=s.current.run_id, attempt_id=s.current.attempt_id, input_id=str(uuid4()),
            question_id=question_id, answers={"q0": "o2"}, source="manual")

    task = asyncio.create_task(act())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        conn.expired = expires_during_lookup
        release.set()
        if expires_during_lookup:
            with pytest.raises(RecoveryOperationError, match="recovery_operation_authority_stale"):
                await task
            assert len(s.input_store.questions) == before_questions
            assert len(s.store.operations) == 1
            assert not binding["started"] and binding["answer_input_id"] is None
            assert s.store.operations[s.operation_key]["verified_outcome"] == before_outcome
        else:
            result = await task
            if operation == "query":
                assert result["decision"] == "wait" and result["question_id"]
            elif operation == "started":
                assert binding["started"] and len(s.store.operations) == 2
            else:
                assert binding["authorization"] == "retry"
                assert s.store.operations[s.operation_key]["verified_outcome"] == "not_effective"
        assert conn.final_checks == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
