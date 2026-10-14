from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.runs.domain import diagnostics as diagnostics_contract
from app.runs.domain.diagnostics import RunDiagnosticsContractError, empty_run_diagnostics_payload
from app.runs.domain.run_performance import (
    BROWSER_LATENCY_MEASUREMENT,
    build_run_investigation,
    build_run_latency,
    elapsed_ms,
    merge_browser_latency,
)
from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.transport.browser_latency import BrowserRunLatencyInput

NOW = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
OBSERVED = NOW + timedelta(hours=1)


def run(**changes):
    return {
        "run_id": "run-a", "status": "succeeded", "agent_id": "agent-a",
        "created_at": NOW, "queued_at": NOW + timedelta(seconds=1),
        "started_at": NOW + timedelta(seconds=11), "finished_at": NOW + timedelta(seconds=41),
        **changes,
    }


def browser(duration=2500, **changes):
    return {
        "measurement": BROWSER_LATENCY_MEASUREMENT, "visibility": "visible",
        "duration_ms": duration, "received_at": OBSERVED.isoformat(), **changes,
    }


def event(kind, second, *, attempt="attempt-a", operation="op-a", **payload):
    return {
        "event_id": f"event-{kind}-{second}-{attempt}", "type": kind,
        "created_at": NOW + timedelta(seconds=second), "visible_to_user": True,
        "payload": {
            "operation_id": operation, "display_name": "文件读取", **payload,
            "__stream_v4": {"attempt_id": attempt},
        },
    }


def test_run_lifecycle_measurements_are_not_worker_or_sdk_stopwatches():
    measured = build_run_latency(
        run(), first_text_at=NOW + timedelta(seconds=16),
        browser_measurement=browser(18000), observed_at=OBSERVED,
    )
    assert {key: measured[key] for key in (
        "total_ms", "queue_ms", "execution_ms", "server_first_text_ms", "browser_first_text_ms"
    )} == {
        "total_ms": 41000, "queue_ms": 10000, "execution_ms": 30000,
        "server_first_text_ms": 16000, "browser_first_text_ms": 18000,
    }


def test_pending_missing_invalid_clocks_and_actual_zero_remain_distinct():
    pending = build_run_latency(run(status="queued", started_at=None, finished_at=None), observed_at=OBSERVED)
    assert pending["total_ms"] is None and pending["execution_ms"] is None
    assert pending["queue_ms"] is None and pending["elapsed_ms"] == 3600000
    assert elapsed_ms(NOW, NOW) == 0
    assert elapsed_ms(NOW, NOW - timedelta(seconds=1)) is None
    assert elapsed_ms(NOW.replace(tzinfo=None), NOW) is None
    assert build_run_latency(run(finished_at=OBSERVED + timedelta(seconds=1)), observed_at=OBSERVED)["total_ms"] is None
    assert build_run_latency(run(), first_text_at=OBSERVED, observed_at=OBSERVED)["server_first_text_ms"] is None


def test_overlapping_tool_intervals_are_not_added_and_sdk_is_reported_separately():
    evidence = [
        event("tool.started", 12), event("tool.started", 12, operation="op-b"),
        event("tool.completed", 22, duration_ms=10000),
        event("tool.completed", 22, operation="op-b", duration_ms=10000),
        event("model.completed", 40, duration_ms=29000),
    ]
    projected = build_run_investigation(
        run(), evidence, attempts=[{"attempt_id": "attempt-a", "ordinal": 1}], observed_at=OBSERVED,
    )
    tools = [item for item in projected["stages"] if item["source"] == "sdk_tool_hooks"]
    assert len(tools) == 2 and all(item["offset_ms"] == 12000 for item in tools)
    assert projected["execution_ms"] == 30000 and projected["durations_additive"] is False
    sdk = next(item for item in projected["stages"] if item["source"] == "sdk_reported")
    assert sdk["duration_ms"] == 29000 and sdk["started_at"] is None
    assert "模型" not in sdk["label"]


def test_attempts_with_reused_operation_ids_never_merge():
    evidence = [
        event("tool.started", 12, attempt="attempt-a"),
        event("tool.completed", 22, attempt="attempt-b", duration_ms=10000),
        event("tool.completed", 25, attempt="foreign-attempt", duration_ms=1),
    ]
    result = build_run_investigation(
        run(), evidence, attempts=[{"attempt_id": "attempt-a"}, {"attempt_id": "attempt-b"}], observed_at=OBSERVED,
    )
    tools = [item for item in result["stages"] if item["source"] == "sdk_tool_hooks"]
    assert len(tools) == 2
    assert tools[0]["status"] == "running" and tools[0]["finished_at"] is None
    assert tools[1]["started_at"] is None and tools[1]["attempt_id"] == "attempt-b"
    assert result["unbound_event_count"] == 1


def test_legacy_events_without_attempt_identity_are_not_paired():
    result = build_run_investigation(
        run(), [event("tool.started", 12, attempt=None), event("tool.completed", 22, attempt=None, duration_ms=9000)],
        observed_at=OBSERVED,
    )
    tools = [item for item in result["stages"] if item["source"] == "sdk_tool_hooks"]
    assert len(tools) == 2 and all(item["receipt_duration_ms"] is None for item in tools)
    assert len({item["stage_id"] for item in tools}) == 2


def test_browser_observation_is_first_wins_closed_and_never_a_failure():
    payload, changed = merge_browser_latency(None, browser(prompt="must-not-retain", source="model"))
    assert changed and payload["observations"] == []
    assert set(payload["browser_latency"]) == {"measurement", "duration_ms", "visibility", "received_at"}
    unchanged, changed = merge_browser_latency(payload, browser(9999))
    assert not changed and unchanged["browser_latency"]["duration_ms"] == 2500


def test_optional_browser_observation_cannot_evict_failure_evidence(monkeypatch):
    payload = empty_run_diagnostics_payload()
    payload["observations"] = [{"observation_id": "root", "runtime_diagnostics": {"sdk": {"exception_message": "earliest failure"}}}]
    payload["counts"]["retained_observations"] = 1
    original = deepcopy(payload)
    size = len(diagnostics_contract.serialize_run_diagnostics_payload(payload).encode())
    monkeypatch.setattr(diagnostics_contract, "RUN_DIAGNOSTICS_MAX_BYTES", size)
    with pytest.raises(RunDiagnosticsContractError, match="run_diagnostics_too_large"):
        merge_browser_latency(payload, browser())
    assert payload == original


def test_browser_first_cannot_reduce_a_later_failure_under_budget(monkeypatch):
    observation = {"observation_id": "later-fault", "runtime_diagnostics": {"sdk": {"exception_message": "full-fault-evidence" * 50}}}
    without_browser, _ = diagnostics_contract.merge_run_diagnostics(None, observation)
    budget = len(diagnostics_contract.serialize_run_diagnostics_payload(without_browser).encode())
    browser_first, _ = merge_browser_latency(None, browser())
    monkeypatch.setattr(diagnostics_contract, "RUN_DIAGNOSTICS_MAX_BYTES", budget)
    with_browser, _ = diagnostics_contract.merge_run_diagnostics(browser_first, observation)
    assert with_browser == without_browser
    assert "browser_latency" not in with_browser
    assert with_browser["coverage"] == "full" and with_browser["losses"] == []


@pytest.mark.parametrize("value", [True, -1, 1.5, 86400001, "2500"])
def test_browser_numeric_boundary_is_strict(value):
    with pytest.raises(ValidationError):
        BrowserRunLatencyInput(run_id="run-a", duration_ms=value)


class Persistence:
    def __init__(self):
        self.queries = []
        self.payload = None

    async def append_browser_latency(self, conn, **kwargs):
        self.payload, _ = merge_browser_latency(self.payload, kwargs["measurement"])
        return {"payload_json": self.payload}

    async def get_admin_monitor_report(self, conn, **kwargs):
        self.queries.append(kwargs)
        return {
            "runs": [{**run(), "total_ms": 41000}], "window": {"from_at": kwargs["from_at"], "to_at": kwargs["to_at"]},
            "counts": {"total": 2, "succeeded": 1, "failed": 1, "cancelled": 0, "queued": 0, "running": 0},
            "latency": {"total_ms": {"sample_count": 2, "p50": 40000, "p95": 41000, "p99": 41000}},
            "models": [], "limit": 50, "has_more": False, "order": "slow",
        }

    async def get_admin_monitor_metadata(self, conn, **kwargs):
        return {"run-a": {"latency_ms": 999, "session_title": "合同审核"}}

    async def get_admin_snapshot(self, conn, **kwargs):
        return {
            "run": run(), "result_json": {}, "attempts": [],
            "diagnostic": {"schema_version": diagnostics_contract.RUN_DIAGNOSTICS_SCHEMA_VERSION, "payload_json": self.payload} if self.payload else None,
        }

    async def get_admin_investigation_events(self, conn, **kwargs):
        return {"events": [], "events_truncated": False, "first_public_text_at": None}


def service(persistence):
    return RunDiagnosticsService(
        persistence=persistence, normalize_runtime_diagnostics=lambda value: value,
        runtime_diagnostics_schema_version="test", exception_log_fields=lambda error: {}, clock=lambda: OBSERVED,
    )


@pytest.mark.asyncio
async def test_monitor_uses_one_window_and_keeps_old_stopwatch_out_of_new_metrics():
    persistence = Persistence()
    result = await service(persistence).read_monitor(object(), tenant_id="tenant-a", model_id="model-a", model_gateway_revision=2)
    query = persistence.queries[0]
    assert query["from_at"] == OBSERVED - timedelta(hours=24) and query["to_at"] == OBSERVED
    assert query["tenant_id"] == "tenant-a" and query["model_gateway_revision"] == 2
    assert result["runs"][0]["latency"]["total_ms"] == 41000
    assert result["runs"][0]["latency_ms"] == 999
    assert result["success_rate"] == 0.5
    assert result["latency"]["browser_first_text_ms"]["p95"] is None


@pytest.mark.asyncio
async def test_browser_only_diagnostics_do_not_claim_an_exception_or_full_failure_coverage():
    persistence = Persistence()
    diagnostics = service(persistence)
    await diagnostics.record_browser_latency(object(), tenant_id="tenant-a", run_id="run-a", duration_ms=2500)
    projected = await diagnostics.read_admin(object(), tenant_id="tenant-a", run_id="run-a")
    assert projected["root"] is None and projected["coverage"] == "not_collected"
    assert projected["browser_latency"]["duration_ms"] == 2500
    investigated = await diagnostics.read_admin_investigation(object(), tenant_id="tenant-a", run_id="run-a")
    assert investigated["browser_first_text_ms"] == 2500
    assert investigated["comparison"]["missing_reason"] == "model_identity_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"hours": 169}, {"hours": True}, {"from_at": NOW},
    {"from_at": NOW, "to_at": NOW},
    {"from_at": NOW.replace(tzinfo=None), "to_at": OBSERVED},
    {"from_at": NOW, "to_at": OBSERVED + timedelta(seconds=1)},
])
async def test_invalid_monitor_windows_are_rejected_before_storage(changes):
    persistence = Persistence()
    with pytest.raises(ValueError, match="invalid_monitor_window"):
        await service(persistence).read_monitor(object(), tenant_id="tenant-a", **changes)
    assert persistence.queries == []
