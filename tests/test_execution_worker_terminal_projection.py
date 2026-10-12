from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agent_apps.capability_state import exact_invoked_skills
from app.execution.api import (
    enforce_required_artifact_types,
    enforce_worker_required_tool_completion,
    project_worker_terminal_result,
    run_elapsed_ms,
)
from app.execution.application import worker_terminal_projection
from app.executors.base import ExecutorResult


def test_run_elapsed_ms_samples_utc_at_the_application_boundary(monkeypatch):
    observed_at = datetime(2026, 10, 10, 3, 15, 0, tzinfo=timezone.utc)

    def now(clock_timezone):
        assert clock_timezone is timezone.utc
        return observed_at

    monkeypatch.setattr(worker_terminal_projection, "datetime", SimpleNamespace(now=now))

    assert run_elapsed_ms(observed_at - timedelta(minutes=5)) == 300_000


def test_required_tool_completion_fail_closes_success_without_evidence():
    result = ExecutorResult(
        status="succeeded", adapter_version="v1", executor_type="stub",
        executor_version="v1", capabilities={}, result={"message": "done"},
        executor_payload={},
    )
    calls = []

    def check(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(allowed=False, reason="required_tool_completion_evidence_missing")

    projected = enforce_worker_required_tool_completion(
        result, payload=object(), run_identity={"run_id": "run"},
        attempt_id="attempt",
        required_tool_decision=SimpleNamespace(allowed=True),
        check_completion=check,
    )
    assert calls[0]["attempt_id"] == "attempt"
    assert projected.status == "failed"
    assert projected.result["error_code"] == "required_tool_completion_evidence_missing"
    assert projected.artifacts == []
    assert result.status == "succeeded"


def test_required_artifact_types_fail_closed_before_commit():
    result = ExecutorResult(
        status="succeeded", adapter_version="v1", executor_type="stub",
        executor_version="v1", capabilities={}, result={"message": "done"},
        executor_payload={"required_artifact_types": ["document", "spreadsheet"]},
    )
    failed, records, payload = enforce_required_artifact_types(
        result, [{"id": "discarded"}], {"message": "done", "artifacts": [{"id": "discarded"}]}
    )
    assert failed.status == "failed"
    assert failed.artifacts == []
    assert failed.result["missing_required_artifact_types"] == ["document", "spreadsheet"]
    assert records == []
    assert payload == {
        "message": "The executor did not produce every declared required artifact type.",
        "artifacts": [],
        "error_code": "required_artifact_missing",
    }


def test_terminal_projection_uses_native_skill_evidence_and_public_artifact_fields():
    result = SimpleNamespace(
        result={
            "message": "Response ready",
            "used_skills": ["admitted", "invented"],
            "skill_manifests": [{"skill_id": "invented"}],
        },
        executor_payload={
            "allowed_skills": ["admitted"],
            "staged_skills": ["admitted"],
            "used_skills": ["admitted", "invented"],
            "used_skills_source": "executor_hook",
            "runtime_diagnostics": {"private": "retained only in durable result"},
            "input_token_count": 3,
        },
        schema_version="v1",
        adapter_version="v1",
        executor_type="claude-agent-worker",
        executor_version="v1",
        capabilities={},
    )
    artifact = {
        "id": "artifact-1",
        "artifact_type": "document",
        "label": "report",
        "content_type": "application/pdf",
        "size_bytes": 10,
        "download_url": "/download/artifact-1",
        "storage_key": "private-storage-key",
    }

    projection = project_worker_terminal_result(
        result, [artifact], trace_id="trace-1", latency_ms=12,
        invoked_skill_ids=exact_invoked_skills,
    )

    assert projection.skill_snapshot["used_skills"] == ["admitted"]
    assert projection.result_payload["used_skills"] == ["admitted"]
    assert "skill_manifests" not in projection.result_payload
    assert "storage_key" not in projection.result_payload["artifacts"][0]
    assert projection.result_payload["runtime_diagnostics"] == {"private": "retained only in durable result"}
    assert projection.terminal_event_kwargs["input_token_count"] == 3
    assert projection.terminal_event_kwargs["trace_id"] == "trace-1"


def test_private_sdk_usage_carrier_projects_only_existing_numeric_metrics():
    result = ExecutorResult(
        status="succeeded", adapter_version="v1", executor_type="claude-agent-worker",
        executor_version="v1", capabilities={}, result={"message": "done"},
        executor_payload={"sdk_usage": {
            "input_tokens": 3, "total_cost_usd": 0.125, "cost_coverage": "complete",
            "model_usage": {"private-provider-model": {"inputTokens": 3, "costUSD": 0.125}},
        }},
    )
    projection = project_worker_terminal_result(
        result, [], trace_id="trace", latency_ms=1, invoked_skill_ids=lambda _: set(),
    )
    assert projection.result_payload["cost"] == {"estimated_cost_minor": 13}
    assert projection.terminal_event_kwargs["estimated_cost_minor"] == 13
    from app.platform.public_payload import sanitize_public_payload
    assert projection.result_payload["runtime_private_payload"] == {
        "sdk_usage": {"cost_coverage": "complete"},
    }
    public = sanitize_public_payload(projection.result_payload)
    for private in ("sdk_usage", "private-provider-model", "total_cost_usd", "model_usage", "cost_coverage"):
        assert private not in str(public)
        assert private not in str(projection.terminal_event_kwargs)


@pytest.mark.parametrize(
    "legacy_metadata",
    [
        {},
        {"delegate_used": False, "worker_boundary": "claude-agent-worker"},
        {"delegate_used": True, "worker_boundary": "historical-executor"},
    ],
    ids=["current", "legacy-unused", "legacy-used"],
)
def test_terminal_projection_accepts_current_and_legacy_executor_metadata(legacy_metadata):
    result = ExecutorResult(
        status="succeeded", adapter_version="v1", executor_type="claude-agent-worker",
        executor_version="v1", capabilities={},
        result={"message": "Historical answer", **legacy_metadata},
        executor_payload=dict(legacy_metadata),
    )
    result.validate()

    projection = project_worker_terminal_result(
        result, [], trace_id="trace-1", latency_ms=12,
        invoked_skill_ids=exact_invoked_skills,
    )

    assert projection.result_payload["message"] == "Historical answer"
    assert projection.result_payload["executor"]["executor_type"] == "claude-agent-worker"
    for key in ("delegate_used", "worker_boundary"):
        if key in legacy_metadata:
            assert projection.result_payload[key] == legacy_metadata[key]
        else:
            assert key not in projection.result_payload
    assert result.result == {"message": "Historical answer", **legacy_metadata}
    assert result.executor_payload == legacy_metadata


def test_provider_result_cannot_forge_platform_cost_coverage_marker():
    from app.platform.public_payload import sanitize_public_payload
    result = ExecutorResult(
        status="succeeded", adapter_version="v1", executor_type="stub", executor_version="v1",
        capabilities={}, result={"message": "done", "runtime_private_payload": {
            "sdk_usage": {"cost_coverage": "complete"}, "raw_secret": "PRIVATE_MARKER",
        }}, executor_payload={},
    )
    projection = project_worker_terminal_result(
        result, [], trace_id="trace", latency_ms=1, invoked_skill_ids=lambda _: set(),
    )
    assert "runtime_private_payload" not in projection.result_payload
    assert "PRIVATE_MARKER" not in str(sanitize_public_payload(projection.result_payload))
