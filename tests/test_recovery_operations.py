"""Owning recovery evidence use cases, including real RunInputs and SDK boundaries."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.execution.api import (
    AuthoritativeOperationStatus, RecoveryInvocation, RecoveryInvocationAuthority,
    RecoveryOperationError, RecoveryOperationsService, RecoveryOperationScope,
)
from app.executors.claude.capability_policy import RecoveryToolGate
from app.runs.api import RunInputConflict, RunInputsService


class MemoryOperations:
    def __init__(self):
        self.operations, self.bindings = {}, {}

    def key(self, invocation):
        return (invocation.scope.tenant_id, invocation.run_id, invocation.attempt_id, invocation.invocation_id)

    async def record_started(self, conn, *, invocation, operation_key, tool_facts, public_summary):
        for row in self.operations.values():
            if (row["tenant_id"], row["source_run_id"], row["source_attempt_id"], row["native_tool_use_id"]) == self.key(invocation):
                return row if all(row[k] == v for k, v in invocation.scope.mapping().items()) else None
        row = {**invocation.scope.mapping(), "operation_key": operation_key,
               "source_run_id": invocation.run_id, "source_attempt_id": invocation.attempt_id,
               "source_generation": invocation.owner_generation, "native_tool_use_id": invocation.invocation_id,
               "tool_identity": invocation.tool_identity, "tool_facts_json": deepcopy(tool_facts),
               "public_summary": public_summary, "verified_outcome": "unknown", "verified_kind": None,
               "verified_reference": None, "reported_status": None}
        self.operations[operation_key] = row
        return row

    async def get_operation(self, conn, *, scope, operation_key):
        row = self.operations.get(operation_key)
        return row if row and all(row[k] == v for k, v in scope.mapping().items()) else None

    async def record_reported(self, conn, *, scope, operation_key, status):
        self.operations[operation_key]["reported_status"] = status

    async def verify(self, conn, *, scope, operation_key, outcome, kind, reference):
        row = await self.get_operation(conn, scope=scope, operation_key=operation_key)
        if row["verified_outcome"] != "succeeded":
            row.update(verified_outcome=outcome, verified_kind=kind, verified_reference=reference)
        return row

    async def insert_binding(self, conn, *, invocation, operation_key, frozen_binding_ref, public_summary):
        self.bindings.setdefault(self.key(invocation), {
            **invocation.mapping(), "operation_key": operation_key, "frozen_binding_ref": frozen_binding_ref,
            "public_summary": public_summary, "question_id": None, "question_expires_at": None,
            "authorization": "pending", "answer_input_id": None, "started": False, "closed": False,
        })

    async def get_binding(self, conn, *, invocation, for_update=False):
        row = self.bindings.get(self.key(invocation))
        return row if row and all(row[k] == v for k, v in invocation.mapping().items()) else None

    async def attach_question(self, conn, *, invocation, question_id, expires_at):
        self.bindings[self.key(invocation)].update(question_id=question_id, question_expires_at=expires_at)

    async def find_question(self, conn, **scope):
        return next((row for row in self.bindings.values() if all(row[k] == v for k, v in scope.items())), None)

    async def authorize_answer(self, conn, *, invocation, input_id, authorization):
        self.bindings[self.key(invocation)].update(answer_input_id=input_id, authorization=authorization)

    async def mark_started(self, conn, *, invocation):
        self.bindings[self.key(invocation)]["started"] = True


class MemoryInputs:
    def __init__(self):
        self.inputs, self.questions = {}, {}
        self.owner, self.active = "user-a", True
        self.published = asyncio.Event()

    async def get_owner_run(self, conn, *, tenant_id, user_id, run_id, **kwargs):
        if tenant_id != "tenant-a" or user_id != self.owner or run_id != "recovery-run":
            return None
        return dict(id=run_id, tenant_id=tenant_id, user_id=user_id, status="running" if self.active else "cancelled", cancel_requested_at=None)

    async def get_current_attempt_id(self, conn, **kwargs): return "recovery-attempt"
    async def get_session(self, conn, **kwargs): return {"state": "open"}
    async def get_input(self, conn, *, input_id, **kwargs): return self.inputs.get(input_id)
    async def get_question(self, conn, *, question_id, **kwargs): return self.questions.get(question_id)
    async def publish_question(self, conn, *, question_id, questions, **kwargs):
        row = self.questions.setdefault(question_id, {**kwargs, "question_id": question_id, "questions": questions, "status": "pending", "created_at": datetime.now(timezone.utc)})
        self.published.set()
        return row
    async def insert_input(self, conn, **kwargs):
        row = {**kwargs, "status": "queued"}
        self.inputs[kwargs["input_id"]] = row
        return row
    async def set_question_status(self, conn, *, question_id, status, **kwargs):
        self.questions[question_id]["status"] = status
        return True


async def scenario():
    scope = RecoveryOperationScope("tenant-a", "workspace-a", "user-a", "session-a")
    source = RecoveryInvocation(scope, "source-run", "source-attempt", 1, "source-native", "mcp__vendor__write")
    current = RecoveryInvocation(scope, "recovery-run", "recovery-attempt", 2, "current-native", source.tool_identity)
    store, input_store = MemoryOperations(), MemoryInputs()
    active, approved = {source, current}, set()
    clock = [datetime.now(timezone.utc)]
    async def authority(conn, *, invocation):
        return RecoveryInvocationAuthority(invocation.run_id == "recovery-run") if invocation in active else None
    async def binding_authority(conn, *, invocation, operation, frozen_binding_ref):
        return (invocation, operation["operation_key"], frozen_binding_ref) in approved
    inputs = RunInputsService(input_store, lambda value: value)
    service = RecoveryOperationsService(store, authority, binding_authority, inputs.publish_platform_question,
                                        lambda value: value, clock=lambda: clock[0])
    inputs = replace(inputs, on_manual_answer=service.on_manual_answer)
    facts = {"write_capable": True, "registered_tool_revision": 7}
    receipt = await service.record_started(None, invocation=source, tool_facts=facts, public_summary="创建合成测试条目")
    approved.add((current, receipt["operation_key"], "host-frozen-join"))
    await service.bind_invocation(None, invocation=current, operation_key=receipt["operation_key"],
                                  frozen_binding_ref="host-frozen-join", public_summary="重试创建合成测试条目")
    return SimpleNamespace(service=service, inputs=inputs, store=store, input_store=input_store,
                           source=source, current=current, active=active, approved=approved,
                           operation_key=receipt["operation_key"], facts=facts, clock=clock)


async def answer(s, option, *, source="manual", user_id="user-a", question_id=None):
    result = await s.service.query(None, invocation=s.current)
    return await s.inputs.submit(None, tenant_id="tenant-a", user_id=user_id, run_id="recovery-run",
                                 input_id=str(uuid4()), question_id=question_id or result["question_id"],
                                 answers={"q0": option}, source=source)


@pytest.mark.asyncio
async def test_reported_success_remains_unknown_and_host_issues_opaque_identity():
    s = await scenario()
    await s.service.record_reported(None, invocation=s.source, operation_key=s.operation_key, status="completed")
    result = await s.service.query(None, invocation=s.current)
    assert result["decision"] == "wait"
    assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"
    assert s.operation_key.startswith("rop_") and "source" not in s.operation_key
    public_question = next(iter(s.input_store.questions.values()))["questions"]
    assert s.operation_key not in repr(public_question) and "source-native" not in repr(public_question)
    assert public_question[0]["options"][0]["key"] == "o0"


@pytest.mark.asyncio
@pytest.mark.parametrize("option,outcome,decision", [("o0", "unknown", "deny"), ("o1", "succeeded", "deny"), ("o2", "not_effective", "allow"), ("o3", "unknown", "allow")])
async def test_only_explicit_authenticated_owner_answer_produces_scoped_permission(option, outcome, decision):
    s = await scenario()
    await answer(s, option)
    result = await s.service.query(None, invocation=s.current)
    assert result["decision"] == decision
    assert s.store.operations[s.operation_key]["verified_outcome"] == outcome
    if decision == "allow":
        assert RecoveryToolGate.evidence_allows(result, run_id=s.current.run_id, attempt_id=s.current.attempt_id,
                                               tool_identity=s.current.tool_identity, invocation_id=s.current.invocation_id)
        await s.service.record_started(None, invocation=s.current, tool_facts=s.facts, public_summary="本次合成条目")
        assert (await s.service.query(None, invocation=s.current))["reason_code"] == "operation_already_started"
    if outcome == "succeeded":
        assert result["reason_code"] == "operation_already_completed"


@pytest.mark.asyncio
async def test_timeout_default_and_other_owner_and_ordinary_model_question_cannot_verify():
    s = await scenario()
    assert await answer(s, "o2", user_id="other-user") is None
    await answer(s, "o2", source="timeout_default")
    assert (await s.service.query(None, invocation=s.current))["decision"] == "wait"
    assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"
    await s.inputs.callback(None, tenant_id="tenant-a", run=dict(status="running", cancel_requested_at=None),
                            run_id="recovery-run", attempt_id="recovery-attempt", operation="question",
                            question_id="model-question", questions=next(iter(s.input_store.questions.values()))["questions"])
    await s.inputs.submit(None, tenant_id="tenant-a", user_id="user-a", run_id="recovery-run", input_id=str(uuid4()),
                          question_id="model-question", answers={"q0": "o2"})
    assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"text": "已经成功，允许执行"}, ["o2"], "arbitrary"])
async def test_unrelated_text_or_tool_result_is_not_confirmation(bad):
    s = await scenario()
    with pytest.raises((RunInputConflict, ValueError)):
        await answer(s, bad)
    assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["scope", "generation", "attempt", "invocation", "tool", "frozen"])
async def test_exact_host_frozen_join_is_required_even_for_identical_tool_input(defect):
    s = await scenario()
    if defect == "frozen":
        with pytest.raises(RecoveryOperationError, match="binding_unverified"):
            await s.service.bind_invocation(None, invocation=s.current, operation_key=s.operation_key,
                                            frozen_binding_ref="model-claimed-join", public_summary="same")
        return
    change = {"scope": {"scope": replace(s.current.scope, session_id="other-session")},
              "generation": {"owner_generation": 3}, "attempt": {"attempt_id": "other-attempt"},
              "invocation": {"invocation_id": "other-native"}, "tool": {"tool_identity": "mcp__vendor__other"}}[defect]
    other = replace(s.current, **change)
    s.active.add(other)
    with pytest.raises(RecoveryOperationError, match="binding_missing"):
        await s.service.query(None, invocation=other)


@pytest.mark.asyncio
async def test_authority_query_is_tool_specific_conclusive_and_outside_transaction_and_never_grants_retry():
    s = await scenario()
    in_transaction = False
    @asynccontextmanager
    async def transaction():
        nonlocal in_transaction
        assert not in_transaction
        in_transaction = True
        try:
            yield None
        finally:
            in_transaction = False
    async def query(*, operation):
        assert not in_transaction and operation["native_tool_use_id"] == "source-native"
        return AuthoritativeOperationStatus("not_effective", "vendor-terminal-receipt", True)
    assert await s.service.probe_authority(transaction, scope=s.current.scope, operation_key=s.operation_key) is False
    service = replace(s.service, authoritative_queries={s.source.tool_identity: query})
    assert await service.probe_authority(transaction, scope=s.current.scope, operation_key=s.operation_key) is True
    assert (await service.query(None, invocation=s.current))["decision"] == "wait"
    await answer(s, "o2")
    assert (await service.query(None, invocation=s.current))["decision"] == "allow"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [{"outcome": "succeeded", "conclusive": True}, AuthoritativeOperationStatus("not_effective", "nonterminal", False)])
async def test_untyped_or_nonconclusive_authority_result_does_not_verify(status):
    s = await scenario()
    @asynccontextmanager
    async def transaction(): yield None
    async def query(**kwargs): return status
    service = replace(s.service, authoritative_queries={s.source.tool_identity: query})
    assert await service.probe_authority(transaction, scope=s.current.scope, operation_key=s.operation_key) is False
    assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"


@pytest.mark.asyncio
async def test_success_is_monotone_and_current_authorization_never_replays_completed_source():
    s = await scenario()
    await s.service.query(None, invocation=s.current)
    @asynccontextmanager
    async def transaction(): yield None
    result = [AuthoritativeOperationStatus("succeeded", "authoritative-success", True)]
    async def query(**kwargs): return result[0]
    service = replace(s.service, authoritative_queries={s.source.tool_identity: query})
    await service.probe_authority(transaction, scope=s.current.scope, operation_key=s.operation_key)
    question_id = next(iter(s.input_store.questions))
    await s.inputs.submit(None, tenant_id="tenant-a", user_id="user-a", run_id="recovery-run", input_id=str(uuid4()),
                          question_id=question_id, answers={"q0": "o3"})
    result[0] = AuthoritativeOperationStatus("not_effective", "later-disagreement", True)
    await service.probe_authority(transaction, scope=s.current.scope, operation_key=s.operation_key)
    evidence = await service.query(None, invocation=s.current)
    assert evidence["reason_code"] == "operation_already_completed"
    with pytest.raises(RecoveryOperationError, match="not_authorized"):
        await service.record_started(None, invocation=s.current, tool_facts=s.facts, public_summary="do not replay")
    assert s.store.operations[s.operation_key]["verified_outcome"] == "succeeded"


@pytest.mark.asyncio
async def test_expired_or_revoked_confirmation_never_opens_the_gate():
    s = await scenario()
    question = await s.service.query(None, invocation=s.current)
    s.clock[0] += timedelta(seconds=301)
    assert (await s.service.query(None, invocation=s.current))["reason_code"] == "operation_confirmation_expired"
    with pytest.raises(RunInputConflict):
        await s.inputs.submit(None, tenant_id="tenant-a", user_id="user-a", run_id="recovery-run", input_id=str(uuid4()), question_id=question["question_id"], answers={"q0": "o2"})
    s.active.remove(s.current)
    with pytest.raises(RecoveryOperationError, match="authority_stale"):
        await s.service.query(None, invocation=s.current)


@pytest.mark.asyncio
@pytest.mark.parametrize("option,decision", [("o2", "allow"), ("o1", "deny")])
async def test_sdk_boundary_waits_for_authenticated_run_input_service_confirmation(monkeypatch, tmp_path, option, decision):
    import sys
    from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
    from tests.test_claude_agent_sdk_runner import _fake_sdk, _settings, _subject
    from tests.support.claude_mcp import install_mcp_sessions
    s = await scenario()
    install_mcp_sessions(monkeypatch)
    captured = {}
    identity = s.current.tool_identity
    sdk = _fake_sdk(captured, hook_invocations=[("PreToolUse", {"tool_name": identity, "tool_input": {}}, s.current.invocation_id)], emit_answer=False)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.get_settings", _settings)
    subject = _subject(server_id="vendor", tool_name="write")
    subject["write_capable"] = True
    async def query(**kwargs):
        assert (kwargs["run_id"], kwargs["attempt_id"], kwargs["invocation_id"]) == (s.current.run_id, s.current.attempt_id, s.current.invocation_id)
        return await s.service.query(None, invocation=s.current)
    async def ack(*args): return True
    task = asyncio.create_task(run_claude_agent_sdk(
        prompt="synthetic", cwd=tmp_path, skill_id=None, tool_policy_subjects=[subject],
        run_id=s.current.run_id, attempt_id=s.current.attempt_id, execution_policy="sandbox_brokered",
        recovery_tool_gate={"state": "inspection", "allowed_tool_identities": [identity], "released_tool_identities": []},
        recovery_tool_gate_query=query, on_agent_event=ack, on_capability_evidence=ack,
    ))
    try:
        await asyncio.wait_for(s.input_store.published.wait(), 2)
        assert not task.done() and captured.get("hook_results", []) == []
        await answer(s, option)
        await asyncio.wait_for(task, 3)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    output = captured["hook_results"][0][1]["hookSpecificOutput"]
    assert output["permissionDecision"] == decision
    if decision == "deny":
        assert "已确认完成" in output["permissionDecisionReason"] and "只读检查" in output["permissionDecisionReason"]
    assert len(captured["sdk_user_messages"]) == 1
    assert captured["sdk_user_messages"][0]["message"]["content"] == "synthetic"


@pytest.mark.asyncio
async def test_started_ack_is_idempotent_but_native_identity_cannot_change_facts_or_generation():
    s = await scenario()
    again = await s.service.record_started(None, invocation=s.source, tool_facts=s.facts, public_summary="创建合成测试条目")
    assert again["operation_key"] == s.operation_key
    with pytest.raises(RecoveryOperationError, match="started_conflict"):
        await s.service.record_started(None, invocation=s.source, tool_facts={"write_capable": False}, public_summary="创建合成测试条目")
    replacement = replace(s.source, owner_generation=9)
    s.active.add(replacement)
    with pytest.raises(RecoveryOperationError, match="started_conflict"):
        await s.service.record_started(None, invocation=replacement, tool_facts=s.facts, public_summary="创建合成测试条目")
    assert s.store.operations[s.operation_key]["source_generation"] == 1


def test_identity_release_and_success_receipt_cannot_authorize_a_repeated_write():
    identity = "mcp__vendor__write"
    gate = RecoveryToolGate.from_mapping({"state": "inspection", "allowed_tool_identities": [identity], "released_tool_identities": [identity]})
    assert gate.denial(identity, {"write_capable": True}) == "recovery_effect_unconfirmed"
    evidence = dict(schema_version="ai-platform.recovery-operation-evidence.v1", decision="allow",
                    evidence_kind="platform_receipt", operation_key="rop_" + uuid4().hex,
                    run_id="r", attempt_id="a", tool_identity=identity, invocation_id="i",
                    permission_basis="retry_not_effective", verified_outcome="succeeded")
    for basis in ("retry_not_effective", "current_invocation_authorized"):
        evidence["permission_basis"] = basis
        assert not gate.evidence_allows(evidence, run_id="r", attempt_id="a", tool_identity=identity, invocation_id="i")
    evidence.update(verified_outcome={"untrusted": "not_effective"})
    assert not gate.evidence_allows(evidence, run_id="r", attempt_id="a", tool_identity=identity, invocation_id="i")


@pytest.mark.asyncio
@pytest.mark.parametrize("option,outcome", [("o0", "unknown"), ("o1", "succeeded"), ("o2", "not_effective")])
async def test_source_confirmation_verifies_original_without_authorizing_any_invocation(option, outcome):
    from app.execution.api import RecoverySourceConfirmation
    s = await scenario()
    confirmation = RecoverySourceConfirmation(s.current.scope, s.current.run_id, s.current.attempt_id,
        s.current.owner_generation, s.operation_key, s.current.tool_identity)
    s.active.add(confirmation)
    s.approved.add((confirmation, s.operation_key, "source-epoch"))
    service = replace(s.service, authorize_current=lambda *args, **kwargs: source_authority(s, **kwargs))
    inputs = replace(s.inputs, on_manual_answer=service.on_manual_answer)
    result = await service.publish_source_confirmation(None, confirmation=confirmation, frozen_binding_ref="source-epoch")
    repeated = await service.publish_source_confirmation(None, confirmation=confirmation, frozen_binding_ref="source-epoch")
    assert result["question_id"] == repeated["question_id"]
    question = s.input_store.questions[result["question_id"]]["questions"][0]
    assert "当前暂停" not in question["question"] and "不会执行或重试" in question["question"]
    assert [item["key"] for item in question["options"]] == ["o0", "o1", "o2"]
    with pytest.raises(RecoveryOperationError, match="explicit_answer_required"):
        await service.on_manual_answer(None, tenant_id="tenant-a", user_id="user-a", run_id="recovery-run",
            attempt_id="recovery-attempt", input_id=str(uuid4()), question_id=result["question_id"],
            answers={"q0": "o3"}, source="manual")
    assert s.store.operations[s.operation_key]["verified_outcome"] == "unknown"
    await inputs.submit(None, tenant_id="tenant-a", user_id="user-a", run_id="recovery-run",
        input_id=str(uuid4()), question_id=result["question_id"], answers={"q0": option})
    assert s.store.operations[s.operation_key]["verified_outcome"] == outcome
    binding = await s.store.get_binding(None, invocation=confirmation)
    assert binding["authorization"] == "hold" and not binding["started"]
    async def read_manual(conn, *, invocation, input_id):
        return s.input_store.inputs.get(input_id)
    service = replace(service, read_manual_answer=read_manual)
    assert await service.source_confirmation_fact(None, confirmation=confirmation) is None
    receipt = s.input_store.inputs[binding["answer_input_id"]]
    receipt["status"] = "applied"
    fact = await service.source_confirmation_fact(None, confirmation=confirmation)
    if option == "o0":
        assert fact is None
    else:
        assert fact.mapping() == {"public_summary": "创建项目；项目名称：合成项目", "outcome": outcome,
                                  "provenance": "authenticated_owner_confirmation"}
        for key, wrong in (("source", "timeout_default"), ("attempt_id", "other-attempt"),
                           ("question_id", "ordinary-sdk-question"), ("kind", "text")):
            original = receipt[key]
            receipt[key] = wrong
            assert await service.source_confirmation_fact(None, confirmation=confirmation) is None
            receipt[key] = original
        original = s.store.operations[s.operation_key]["verified_reference"]
        s.store.operations[s.operation_key]["verified_reference"] = "other-answer"
        assert await service.source_confirmation_fact(None, confirmation=confirmation) is None
        s.store.operations[s.operation_key]["verified_reference"] = original
        source_binding = s.store.bindings.pop(s.store.key(confirmation))
        assert await service.source_confirmation_fact(None, confirmation=confirmation) is None
        s.store.bindings[s.store.key(confirmation)] = source_binding
        s.active.remove(confirmation)
        with pytest.raises(RecoveryOperationError, match="authority_stale"):
            await service.source_confirmation_fact(None, confirmation=confirmation)
        s.active.add(confirmation)
    assert (await service.query(None, invocation=confirmation))["decision"] == "deny"
    assert (await s.service.query(None, invocation=s.current))["decision"] != "allow"
    with pytest.raises(RecoveryOperationError, match="not_authorized"):
        await service.record_started(None, invocation=confirmation, tool_facts=s.facts, public_summary="never execute")
    assert len(s.store.operations) == 1


async def source_authority(s, *, invocation, **kwargs):
    return RecoveryInvocationAuthority(True, public_summary="创建项目；项目名称：合成项目") if invocation in s.active else None
