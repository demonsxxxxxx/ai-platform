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
