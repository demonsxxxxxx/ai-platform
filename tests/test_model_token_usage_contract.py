from datetime import date, datetime, timezone

import pytest

from app.runs.domain.model_token_usage import ModelUsageSnapshot, normalize_model_usage_snapshot, cumulative_deltas, token_totals, window_utc
from app.execution.infrastructure.harness.claude.result_usage import ClaudeResultUsage
import app.sandbox.infrastructure.leases_postgres as sandbox_lease_reader


def counts(n):
    return {"input_tokens": n, "cache_read_input_tokens": n + 1, "cache_creation_input_tokens": n + 2, "output_tokens": n + 3}


def test_snapshot_contract_keeps_unknown_baseline_unknown_and_rejects_counts():
    snapshot = ModelUsageSnapshot(scope_id="run_attempt", observation_seq=1, baseline="unknown", models={}, coverage="unavailable", reason="baseline_unknown")
    assert snapshot.models == {}
    with pytest.raises(ValueError):
        ModelUsageSnapshot(scope_id="run_attempt", observation_seq=1, baseline="unknown", models={"m": counts(1)}, coverage="partial", reason="baseline_unknown")


def test_deltas_reject_missing_or_regressed_model_without_negative_tokens():
    assert cumulative_deltas({"a": counts(10)}, {"a": counts(12)})["a"]["input_tokens"] == 2
    assert cumulative_deltas({"a": counts(10)}, {"a": counts(9)}) is None
    assert cumulative_deltas({"a": counts(10)}, {"b": counts(12)}) is None


def test_totals_distinguish_input_and_total():
    assert token_totals(counts(1)) == {"input_tokens": 1, "cache_read_input_tokens": 2, "cache_creation_input_tokens": 3, "output_tokens": 4, "input_total": 6, "total_tokens": 10}


def test_beijing_window_is_half_open_across_midnight():
    start, end, lower, upper = window_utc(date(2026, 10, 20), date(2026, 10, 20))
    assert (start, end) == (date(2026, 10, 20), date(2026, 10, 20))
    assert lower == datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
    assert upper == datetime(2026, 10, 20, 16, tzinfo=timezone.utc)


def test_sdk_snapshot_has_stable_scope_and_independent_coverage():
    usage = ClaudeResultUsage(session_id="sdk", prompt="start", scope_id="run_attempt")
    assert usage.model_usage_snapshot(terminal=False)["coverage"] == "unavailable"


def test_actual_two_models_ignore_cost_and_terminal_does_not_add_counts():
    from types import SimpleNamespace
    models = {m: {"inputTokens":1,"cacheReadInputTokens":2,"cacheCreationInputTokens":3,"outputTokens":4} for m in ("a","b")}
    usage = ClaudeResultUsage(session_id="sdk", prompt="start")
    usage.observe_result(SimpleNamespace(session_id="sdk", model_usage=models, usage={}, total_cost_usd=None))
    middle = usage.model_usage_snapshot(terminal=False)
    final = usage.model_usage_snapshot(terminal=True)
    assert middle["models"] == final["models"]
    assert middle["coverage"] == "partial" and final["coverage"] == "reported_final"
    assert final["observation_seq"] > middle["observation_seq"]
    assert final["scope_id"] == middle["scope_id"]
    assert final["scope_id"] != ClaudeResultUsage(session_id="sdk",prompt="start").model_usage_snapshot(terminal=False)["scope_id"]
    normalize_model_usage_snapshot(final)


def test_resume_missing_baseline_never_recounts_history_and_restored_tokens_do_not_require_cost():
    from types import SimpleNamespace
    model = {"a": {"inputTokens":5,"cacheReadInputTokens":2,"cacheCreationInputTokens":3,"outputTokens":4}}
    message = SimpleNamespace(session_id="sdk",model_usage=model,usage={},total_cost_usd=None)
    unknown = ClaudeResultUsage(session_id="sdk",prompt="continue",resumed_entries=[{"type":"user"}])
    unknown.observe_result(message)
    value = unknown.model_usage_snapshot(terminal=True)
    assert value["baseline"] == "unknown" and value["models"] == {} and value["coverage"] == "unavailable"
    restored = ClaudeResultUsage(session_id="sdk",prompt="continue",resumed_entries=[{"type":"cost-state","sessionId":"sdk","modelUsage":model}])
    restored.observe_result(message)
    value = restored.model_usage_snapshot(terminal=True)
    assert value["baseline"] == "restored" and value["coverage"] == "reported_final"
    assert set(value["models"]["a"].values()) == {0}


@pytest.mark.parametrize("subtype", ["success", "error_max_turns"])
def test_failed_and_success_reports_have_same_token_contract(subtype):
    from types import SimpleNamespace
    usage = ClaudeResultUsage(session_id="sdk",prompt="start")
    usage.observe_result(SimpleNamespace(session_id="sdk",subtype=subtype,model_usage={"a":{"inputTokens":0,"outputTokens":0,"cacheReadInputTokens":0,"cacheCreationInputTokens":0}},usage={},total_cost_usd=None))
    value = usage.model_usage_snapshot(terminal=True)
    assert value["coverage"] == "reported_final"
    assert set(value["models"]["a"].values()) == {0}


def test_snapshot_never_leaks_to_public_payload():
    from app.platform.public_payload import sanitize_public_payload
    assert "model_usage_snapshot" not in sanitize_public_payload({"model_usage_snapshot":{"scope_id":"private"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate,expired", [(False,False),(True,False),(False,True)])
async def test_callback_ledger_uses_receipt_transaction_and_final_lease_fence(monkeypatch, duplicate, expired):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.routes import runtime_callbacks as route
    from app.runtime.sandbox.contracts import ExecutorCallbackEvent
    state = []
    connection = object()
    now = datetime.now(timezone.utc)

    @asynccontextmanager
    async def transaction():
        try:
            yield connection
        except Exception:
            state.clear()
            raise

    async def authority(conn, **kwargs):
        assert conn is connection
        return {"tenant_id":"platform-tenant"}, {"id":"lease"}

    async def receipt(conn, **kwargs):
        assert conn is connection
        return {"duplicate":duplicate,"id":"receipt","callback_received_at":now}

    async def heartbeat(conn, **kwargs):
        return {"id":"lease"}

    async def recheck(conn, **kwargs):
        if expired:
            raise HTTPException(409,"expired")

    class Usage:
        async def record(self, conn, **kwargs):
            assert conn is connection
            assert kwargs["tenant_id"] == "platform-tenant"
            assert kwargs["recorded_at"] is now and kwargs["receipt_id"] == "receipt"
            state.append(kwargs)

    monkeypatch.setattr(route,"transaction",transaction)
    monkeypatch.setattr(route,"_lock_callback_authority",authority)
    monkeypatch.setattr(route,"_recheck_callback_authority",recheck)
    monkeypatch.setattr(route.streaming_run_events,"append_event_batch",receipt)
    monkeypatch.setattr(sandbox_lease_reader,"record_sandbox_executor_heartbeat",heartbeat)
    monkeypatch.setattr(route,"get_settings",lambda:SimpleNamespace(sandbox_lease_ttl_seconds=30))
    callback = ExecutorCallbackEvent(session_id="s",run_id="r",attempt_id="a",callback_token_id="cbt:r:a",batch_id="b",status="running",progress=20,
        model_usage_snapshot=dict(scope_id="scope",observation_seq=1,baseline="fresh",models={"a":counts(1)},coverage="partial"))
    if expired:
        with pytest.raises(HTTPException):
            await route.record_executor_callback(callback,capabilities=SimpleNamespace(),model_token_usage=Usage(), lease_port=sandbox_lease_reader)
        assert state == []
    else:
        await route.record_executor_callback(callback,capabilities=SimpleNamespace(),model_token_usage=Usage(), lease_port=sandbox_lease_reader)
        assert len(state) == (0 if duplicate else 1)


def test_restore_snapshot_persists_exact_baseline_and_initial_fresh_identity_is_optional():
    model = {"a": {"inputTokens":5,"cacheReadInputTokens":2,"cacheCreationInputTokens":3,"outputTokens":4}}
    restored = ClaudeResultUsage(session_id="provider",prompt="continue",resumed_entries=[
        {"type":"cost-state","sessionId":"provider","modelUsage":model}])
    value = restored.model_usage_snapshot(terminal=False)
    assert value["provider_session_id"] == "provider"
    assert value["baseline_models"] == {"a":{"input_tokens":5,"cache_read_input_tokens":2,
        "cache_creation_input_tokens":3,"output_tokens":4}}
    fresh = ClaudeResultUsage(session_id=None,prompt="start").model_usage_snapshot(terminal=False)
    assert fresh["baseline_models"] == {} and fresh["provider_session_id"] is None
    normalize_model_usage_snapshot(fresh)


@pytest.mark.parametrize("invalid", [True, False, -1, 2**63, 1.0, "1", None])
@pytest.mark.parametrize("field", ["observation_seq", "input_tokens", "baseline_input_tokens"])
def test_domain_and_wire_snapshot_reject_non_strict_or_unbounded_counters(field, invalid):
    from app.runtime.sandbox.contracts import ModelUsageSnapshotPayload
    value = dict(scope_id="scope", observation_seq=1, baseline="restored", models={"a":counts(1)},
                 baseline_models={"a":counts(1)}, coverage="partial")
    if field == "observation_seq":
        value[field] = invalid
    elif field == "baseline_input_tokens":
        value["baseline_models"]["a"]["input_tokens"] = invalid
    else:
        value["models"]["a"][field] = invalid
    for validate in (normalize_model_usage_snapshot, ModelUsageSnapshotPayload.model_validate):
        with pytest.raises(ValueError):
            validate(value)


@pytest.mark.parametrize("patch", [
    {"extra":1}, {"scope_id":"invalid scope"}, {"observation_seq":0},
    {"provider_session_id":"x"*129}, {"provider_session_id":1}, {"reason":"unbounded_reason"},
    {"models":{"a":{**counts(1),"extra":0}}}, {"models":{"bad\nname":counts(1)}},
    {"models":{str(i):counts(1) for i in range(33)}},
    {"baseline_models":{str(i):counts(1) for i in range(33)}},
    {"baseline":"fresh","baseline_models":{"a":counts(1)}},
    {"baseline":"unknown"}, {"coverage":"reported_final","reason":"report_missing"},
    {"coverage":"reported_final","models":{}},
])
def test_domain_and_wire_snapshot_keep_existing_identity_and_coverage_invariants(patch):
    from app.runtime.sandbox.contracts import ModelUsageSnapshotPayload
    value = dict(scope_id="scope",observation_seq=1,baseline="restored",models={"a":counts(1)},coverage="partial")
    value.update(patch)
    for validate in (normalize_model_usage_snapshot, ModelUsageSnapshotPayload.model_validate):
        with pytest.raises(ValueError):
            validate(value)


def test_wire_snapshot_roundtrip_is_standard_domain_value_and_preserves_bigint():
    from dataclasses import asdict, is_dataclass
    from app.runtime.sandbox.contracts import ModelUsageSnapshotPayload
    value = dict(scope_id="scope",observation_seq=2**63-1,baseline="restored",
                 models={"a":{key:2**63-1 for key in counts(1)}},baseline_models={},
                 provider_session_id="provider",coverage="reported_final",reason=None)
    wire = ModelUsageSnapshotPayload.model_validate(value)
    domain = normalize_model_usage_snapshot(wire.model_dump())
    assert is_dataclass(domain) and asdict(domain) == value


def test_usage_domain_imports_only_standard_library():
    import ast
    import sys
    from pathlib import Path
    tree = ast.parse(Path("app/runs/domain/model_token_usage.py").read_text(encoding="utf-8"))
    imports = [node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert set(imports) <= sys.stdlib_module_names
