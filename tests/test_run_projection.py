from fastapi import HTTPException
import pytest

from app.auth import AuthPrincipal
from app.run_projection import artifact_card, progress_for_status, run_event_response, run_step_response


def principal(**overrides):
    values = {
        "user_id": "user-a",
        "display_name": "User A",
        "tenant_id": "tenant-a",
    }
    values.update(overrides)
    return AuthPrincipal(**values)


def test_projection_module_owns_run_progress_event_step_and_artifact_cards():
    assert progress_for_status("canceled") == 100

    event = run_event_response(
        "run-a",
        {
            "id": "evt-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 3,
            "event_type": "run_multi_agent_child_created",
            "stage": "control",
            "message": "child created",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": 7,
            "input_token_count": 1,
            "output_token_count": 2,
            "total_token_count": 3,
            "estimated_cost_minor": 4,
            "payload_json": {
                "visible_to_user": True,
                "step_key": "review",
                "dispatch_id": "dispatch-private",
                "parent_run_id": "run-parent",
            },
            "created_at": None,
        },
        principal=principal(),
    )
    assert event["event_type"] == "run_child_created"
    assert event["payload"] == {"visible_to_user": True, "step_key": "review"}
    assert "dispatch-private" not in str(event)

    step = run_step_response(
        {
            "id": "step-a",
            "run_id": "run-a",
            "step_key": "review",
            "step_kind": "agent",
            "status": "canceled",
            "title": "Review",
            "role": "reviewer",
            "sequence": 1,
            "payload_json": {
                "public_note": "ok",
                "dispatch_id": "dispatch-private",
                "resource_limits": {"max_seconds": 30},
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
        principal=principal(),
    )
    assert step["status"] == "cancelled"
    assert step["payload"] == {"public_note": "ok"}
    assert "resource_limits" not in step

    card = artifact_card(
        {
            "id": "artifact-a",
            "artifact_type": "reviewed_docx",
            "label": "reviewed.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "storage_key": "tenants/private/reviewed.docx",
            "size_bytes": 12,
            "manifest_version": "ai-platform.artifact-manifest.v1",
            "manifest_json": {
                "source_run_id": "run-private",
                "source_file_id": "file-a",
                "storage_key": "tenants/private/manifest.json",
            },
            "created_at": None,
        },
        principal=principal(),
    )
    assert card["preview_url"] == "/api/ai/artifacts/artifact-a/preview"
    assert "source_run_id" not in card["lineage"]
    assert "storage_key" not in str(card)


def test_projection_projects_terminal_tool_permission_cards_without_decision_controls():
    event = run_event_response(
        "run-a",
        {
            "id": "evt-terminal-permission",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 9,
            "event_type": "tool_permission_terminalized",
            "stage": "tool_policy",
            "message": "工具权限请求已终结",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": None,
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "estimated_cost_minor": 0,
            "payload_json": {
                "visible_to_user": True,
                "permission_request_id": "tpr-terminal",
                "tool_id": "Bash",
                "tool_call_id": "call-terminal",
                "action": "execute",
                "risk_level": "high",
                "write_capable": True,
                "status": "cancelled",
                "reason": "run_cancel_requested",
                "decision_endpoint": "/api/ai/runs/run-a/tool-permissions/tpr-terminal/decision",
                "decision_options": ["allow_once", "allow_for_run", "deny"],
            },
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["event_type"] == "tool_permission_card"
    assert event["payload"] == {
        "visible_to_user": True,
        "tool_permission_card": {
            "schema_version": "ai-platform.tool-permission-card.v1",
            "permission_request_id": "tpr-terminal",
            "run_id": "run-a",
            "tool_id": "Bash",
            "tool_call_id": "call-terminal",
            "action": "execute",
            "risk_level": "high",
            "write_capable": True,
            "reason": "run_cancel_requested",
            "status": "cancelled",
            "decision": None,
        },
    }


@pytest.mark.parametrize("event_type", ["run_failed", "run_cancelled"])
def test_projection_preserves_durable_terminalization_observability(event_type):
    event = run_event_response(
        "run-a",
        {
            "id": f"evt-{event_type}",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 51,
            "event_type": event_type,
            "stage": "worker" if event_type == "run_failed" else "control",
            "message": "Run failed" if event_type == "run_failed" else "任务已取消",
            "severity": "error" if event_type == "run_failed" else "warning",
            "visible_to_user": True,
            "error_code": "executor_failure" if event_type == "run_failed" else None,
            "latency_ms": 17,
            "input_token_count": 3,
            "output_token_count": 5,
            "total_token_count": 8,
            "estimated_cost_minor": 11,
            "payload_json": {
                "visible_to_user": True,
                "artifact_count": 2,
                "result_status": "failed" if event_type == "run_failed" else "cancelled",
                "result": {"message": "safe durable result"},
                "error_message": "safe terminal error" if event_type == "run_failed" else None,
            },
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["latency_ms"] == 17
    assert event["token_counts"] == {"input": 3, "output": 5, "total": 8}
    assert event["cost"] == {"estimated_cost_minor": 11}
    assert event["payload"]["artifact_count"] == 2
    assert event["payload"]["result"] == {"message": "safe durable result"}
    if event_type == "run_failed":
        assert event["error_code"] == "executor_failure"
        assert event["payload"]["error_message"] == "safe terminal error"


def test_projection_module_rejects_invalid_event_schema_version():
    with pytest.raises(HTTPException) as exc_info:
        run_event_response(
            "run-a",
            {
                "id": "evt-a",
                "trace_id": "trace_run_a",
                "event_type": "queued",
                "stage": "queue",
                "message": "queued",
                "payload_json": {},
                "created_at": None,
            },
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "invalid_event_schema_version"
