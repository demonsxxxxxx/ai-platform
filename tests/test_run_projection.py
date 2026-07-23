from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.auth import AuthPrincipal
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import AgentEvent
from app.run_projection import artifact_card, progress_for_status, run_event_response, run_step_response


def principal(**overrides):
    values = {
        "user_id": "user-a",
        "display_name": "User A",
        "tenant_id": "tenant-a",
    }
    values.update(overrides)
    return AuthPrincipal(**values)


def event_row(event_type: str, *, payload_json=None, sequence: int = 1):
    return {
        "id": f"evt-{sequence}",
        "trace_id": "trace_run_a",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": sequence,
        "event_type": event_type,
        "stage": "executor-private-stage",
        "message": "executor-private-message",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "latency_ms": None,
        "input_token_count": 0,
        "output_token_count": 0,
        "total_token_count": 0,
        "estimated_cost_minor": 0,
        "payload_json": payload_json or {},
        "created_at": None,
    }


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
    assert event["stage"] == "status"
    assert event["message"] == "已安排协同任务。"
    assert event["payload"] == {"activity": {"category": "status", "status": "running"}}
    assert "review" not in str(event)
    assert "dispatch-private" not in str(event)
    assert "run-parent" not in str(event)

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
    assert step["id"] == "step-a"
    assert step["step_key"] == "step-a"
    assert step["title"] == "步骤已取消"
    assert step["role"] is None
    assert step["payload"] == {}
    assert "public_note" not in str(step)
    assert "review" not in str(step)
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
    assert card["label"] == "reviewed_docx"
    assert card["lineage"] == {}
    assert card["manifest"] == {}
    assert "source_run_id" not in str(card)
    assert "source_file_id" not in str(card)
    assert "storage_key" not in str(card)

    admin_step = run_step_response(
        {
            "id": "step-a",
            "run_id": "run-a",
            "step_key": "review",
            "step_kind": "agent",
            "status": "canceled",
            "title": "Review",
            "role": "reviewer",
            "sequence": 1,
            "payload_json": {"public_note": "ok", "dispatch_id": "dispatch-private"},
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
        principal=principal(roles=["admin"]),
    )
    assert admin_step["step_key"] == "review"
    assert admin_step["role"] == "reviewer"
    assert admin_step["payload"] == {"public_note": "ok", "dispatch_id": "dispatch-private"}


def test_artifact_card_uses_stored_filename_for_xlsx_preview_eligibility():
    valid = artifact_card(
        {
            "id": "artifact-valid",
            "artifact_type": "spreadsheet",
            "label": "misleading.xlsm",
            "storage_key": "private/export.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "manifest_json": {},
            "created_at": None,
        }
    )
    legacy = artifact_card(
        {
            "id": "artifact-legacy",
            "artifact_type": "spreadsheet",
            "label": "misleading.xlsx",
            "storage_key": "private/export.xlsm",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "manifest_json": {},
            "created_at": None,
        }
    )

    assert valid["preview_url"] == "/api/ai/artifacts/artifact-valid/preview"
    assert legacy["preview_url"] is None


def test_projection_keeps_terminal_tool_permission_events_as_fixed_activity():
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
    assert event["stage"] == "policy"
    assert event["message"] == "权限请求已结束。"
    assert event["payload"] == {"activity": {"category": "policy", "status": "completed"}}
    assert "tool_permission_card" not in str(event["payload"])
    assert "tpr-terminal" not in str(event)
    assert "decision_endpoint" not in str(event)


@pytest.mark.parametrize(
    ("event_type", "projected_type", "stage", "message", "status"),
    [
        (
            "intent_detected",
            "intent_detected",
            "preparation",
            "正在准备受控运行请求。",
            "running",
        ),
        (
            "skill_selected",
            "capability_selected",
            "capability",
            "已加载授权处理能力。",
            "completed",
        ),
    ],
)
def test_public_progress_projection_uses_fixed_safe_details(
    event_type,
    projected_type,
    stage,
    message,
    status,
):
    forbidden = (
        "raw prompt amber-lantern",
        "powershell -Command private",
        "C:\\private\\runtime",
        "secret-token-value",
    )
    event = run_event_response(
        "run-a",
        event_row(
            event_type,
            payload_json={
                "prompt": forbidden[0],
                "command": forbidden[1],
                "runtime_path": forbidden[2],
                "private_payload": {"token": forbidden[3]},
                "visible_to_user": True,
            },
        ),
        principal=principal(),
    )

    assert event["event_type"] == projected_type
    assert event["stage"] == stage
    assert event["message"] == message
    assert event["payload"] == {"activity": {"category": stage, "status": status}}
    assert all(term not in str(event) for term in forbidden)


def test_run_started_heartbeat_is_liveness_not_meaningful_progress():
    heartbeat = run_event_response(
        "run-a",
        event_row("run_started", payload_json={"heartbeat": True, "visible_to_user": True}),
        principal=principal(),
    )
    meaningful_start = run_event_response(
        "run-a",
        event_row("run_started", payload_json={"heartbeat": False, "visible_to_user": True}, sequence=2),
        principal=principal(),
    )

    assert heartbeat["event_type"] == "heartbeat"
    assert heartbeat["stage"] == "liveness"
    assert heartbeat["message"] == "任务仍在运行。"
    assert heartbeat["payload"] == {
        "activity": {"category": "liveness", "status": "running", "meaningful": False}
    }
    assert meaningful_start["event_type"] == "run_started"
    assert meaningful_start["stage"] == "execution"
    assert meaningful_start["payload"] == {
        "activity": {"category": "execution", "status": "running"}
    }


def test_event_bridge_maps_valid_events_and_fails_closed_for_admin_and_unknown_events():
    progress = AgentEvent(
        type="run_started",
        message="Runtime started",
        payload={"visible_to_user": True},
        admin_only=False,
    )
    private = AgentEvent(
        type="runtime_container_started",
        message="Sandbox executor container started",
        payload={"container_id": "private-container"},
        admin_only=True,
    )
    unknown = SimpleNamespace(
        type="future_executor_private_event",
        message="secret executor message",
        payload={"runtime_path": "C:\\private\\runtime", "token": "secret-token"},
        admin_only=False,
    )

    assert agent_event_to_executor_event(progress) == {
        "event_type": "run_started",
        "stage": "runtime",
        "message": "Runtime started",
        "payload": {"visible_to_user": True},
    }
    assert agent_event_to_executor_event(private)["payload"] == {
        "container_id": "private-container",
        "visible_to_user": False,
        "admin_only": True,
    }
    assert agent_event_to_executor_event(unknown) == {  # type: ignore[arg-type]
        "event_type": "executor_private_event",
        "stage": "runtime",
        "message": "",
        "payload": {"visible_to_user": False, "admin_only": True},
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
    assert event["payload"] == {}
    if event_type == "run_failed":
        assert event["event_type"] == "error"
        assert event["error_code"] == "run_failed"
        assert event["message"] == "任务未能完成。请稍后重试；如问题持续，请联系管理员。"
    else:
        assert event["event_type"] == "run_cancelled"
        assert event["error_code"] is None
        assert event["message"] == "任务已取消。取消前已产生的公开内容仍会保留。"
    assert "safe durable result" not in str(event)
    assert "safe terminal error" not in str(event)

    admin_event = run_event_response(
        "run-a",
        {
            "id": f"admin-{event_type}",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 51,
            "event_type": event_type,
            "stage": "worker",
            "message": "Run failed" if event_type == "run_failed" else "任务已取消",
            "severity": "error",
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
                "result": {"message": "safe durable result"},
                "error_message": "safe terminal error" if event_type == "run_failed" else None,
            },
            "created_at": None,
        },
        principal=principal(roles=["admin"]),
    )
    assert admin_event["payload"]["result"] == {"message": "safe durable result"}
    if event_type == "run_failed":
        assert admin_event["payload"]["error_message"] == "safe terminal error"


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
