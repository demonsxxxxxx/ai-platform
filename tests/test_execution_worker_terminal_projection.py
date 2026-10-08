from types import SimpleNamespace

from app.agent_apps.capability_state import exact_invoked_skills
from app.execution.api import (
    enforce_required_artifact_types,
    enforce_worker_required_tool_completion,
    project_worker_terminal_result,
)
from app.executors.base import ExecutorResult


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
