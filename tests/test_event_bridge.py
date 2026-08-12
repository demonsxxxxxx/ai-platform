import pytest

from app.public_execution import (
    PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
    PublicExecutionV2Projector,
    public_execution_event_from_row,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import AgentEvent


def test_agent_event_to_executor_event_maps_runtime_event_to_worker_shape():
    event = AgentEvent(
        type="assistant_delta",
        message="partial answer",
        payload={"delta": "partial answer"},
    )

    converted = agent_event_to_executor_event(event)

    assert converted == {
        "event_type": "assistant_delta",
        "stage": "message",
        "message": "partial answer",
        "payload": {
            "delta": "partial answer",
            "visible_to_user": True,
        },
    }


def test_agent_event_to_executor_event_hides_admin_only_events_from_ordinary_users():
    event = AgentEvent(
        type="browser_snapshot",
        message="browser state captured",
        payload={"url": "https://example.test", "visible_to_user": True},
        admin_only=True,
    )

    converted = agent_event_to_executor_event(event)

    assert converted["event_type"] == "browser_snapshot"
    assert converted["stage"] == "browser"
    assert converted["payload"]["visible_to_user"] is False
    assert converted["payload"]["admin_only"] is True


@pytest.mark.parametrize("event_type", ["intent_detected", "skill_selected", "skill_used"])
def test_executor_progress_types_are_not_kernel_agent_events(event_type):
    with pytest.raises(ValueError, match=f"Unsupported agent event type: {event_type}"):
        AgentEvent(type=event_type)


def test_checkpoint_and_subagent_events_have_stable_stage_mapping():
    checkpoint = agent_event_to_executor_event(
        AgentEvent(type="checkpoint_created", message="checkpoint saved", payload={"checkpoint_id": "checkpoint-a"})
    )
    subagent = agent_event_to_executor_event(
        AgentEvent(type="subagent_completed", message="reviewer done", payload={"subagent_id": "subagent-a"})
    )

    assert checkpoint["stage"] == "checkpoint"
    assert checkpoint["payload"] == {"checkpoint_id": "checkpoint-a", "visible_to_user": True}
    assert subagent["stage"] == "subagent"
    assert subagent["payload"] == {"subagent_id": "subagent-a", "visible_to_user": True}


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("capability_invoking", "invoking"),
        ("capability_completed", "completed"),
        ("capability_failed", "failed"),
    ],
)
def test_capability_lifecycle_events_bridge_without_private_fallback(event_type, status):
    bridged = agent_event_to_executor_event(
        AgentEvent(
            type=event_type,
            message="Capability lifecycle update",
            payload={
                "capability": {"kind": "skill", "name": "Document review", "status": status}
            },
        )
    )

    assert bridged["event_type"] == event_type
    assert bridged["stage"] == "capability"
    assert bridged["payload"] == {
        "capability": {"kind": "skill", "name": "Document review", "status": status},
        "visible_to_user": True,
    }


def test_bridge_fails_closed_for_known_event_with_private_execution_fields():
    bridged = agent_event_to_executor_event(
        AgentEvent(
            type="tool_call_started",
            message="powershell -Command private-token",
            payload={
                "command": "powershell -Command private-token",
                "args": ["C:\\private\\workspace"],
                "private_payload": {"token": "private-token"},
            },
        )
    )

    assert bridged == {
        "event_type": "executor_private_event",
        "stage": "runtime",
        "message": "",
        "payload": {"visible_to_user": False, "admin_only": True},
    }


def test_bridge_accepts_typed_public_execution_projector_payload():
    projected = PublicExecutionV2Projector().project(
        {
            "invocation_id": "private-read-1",
            "tool_name": "Read",
            "lifecycle": "started",
        }
    )
    assert projected is not None

    bridged = agent_event_to_executor_event(
        AgentEvent(
            type=projected.event_type,
            message="",
            payload=projected.payload_json,
        )
    )

    assert bridged == {
        "event_type": projected.event_type,
        "stage": projected.payload_json["stage"],
        "message": "",
        "payload": projected.payload_json,
    }


@pytest.mark.parametrize(
    "payload_update",
    [
        {"schema_version": "ai-platform.public-execution-event.v999"},
        {"status": "completed"},
        {"presentation_kind": "write"},
        {"kind": "generation"},
        {"stage": "private-stage"},
        {"progress": {"current": 1, "total": 1}},
        {"command": "powershell -Command private-token"},
        {"answer": "private answer"},
        {"artifact": {"path": "C:\\private\\artifact"}},
        {"presentation_kind": ["read"]},
        {"kind": ["file_read"]},
        {"stage": ["file"]},
    ],
)
def test_bridge_fails_closed_for_malformed_v2_execution_payload(payload_update):
    projected = PublicExecutionV2Projector().project(
        {
            "invocation_id": "private-read-1",
            "tool_name": "Read",
            "lifecycle": "started",
        }
    )
    assert projected is not None
    payload = {**projected.payload_json, **payload_update}

    bridged = agent_event_to_executor_event(
        AgentEvent(type="execution_step", message="", payload=payload)
    )

    assert bridged == {
        "event_type": "executor_private_event",
        "stage": "runtime",
        "message": "",
        "payload": {"visible_to_user": False, "admin_only": True},
    }


@pytest.mark.parametrize(
    ("message", "admin_only"),
    [
        ("caller-controlled title", False),
        ("", True),
    ],
)
def test_bridge_fails_closed_when_v2_execution_envelope_is_not_public(
    message,
    admin_only,
):
    payload = {
        "schema_version": PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
        "step_id": "pex_opaque",
        "presentation_kind": "bash",
        "kind": "processing",
        "stage": "execution",
        "status": "running",
        "progress": {"current": 0, "total": 1},
    }

    bridged = agent_event_to_executor_event(
        AgentEvent(
            type="execution_step",
            message=message,
            payload=payload,
            admin_only=admin_only,
        )
    )

    assert bridged["event_type"] == "executor_private_event"


def test_readiness_failure_bridge_retains_safe_payload_for_admin_only():
    safe_payload = {
        "schema_version": "ai-platform.executor-readiness-evidence.v1",
        "run_id": "run-a",
        "attempt_id": "qat_test-runtime-attempt",
        "readiness_phase": "health_probe",
        "container_state": "exited",
        "exit_code": 137,
        "oom_killed": True,
        "published_port_observed": True,
        "health_outcome": "timeout",
        "elapsed_ms": 321,
    }
    bridged = agent_event_to_executor_event(
        AgentEvent(
            type="sandbox_executor_readiness_failed",
            message="Sandbox executor readiness failed",
            payload=safe_payload,
            admin_only=True,
        )
    )

    assert bridged == {
        "event_type": "sandbox_executor_readiness_failed",
        "stage": "runtime",
        "message": "Sandbox executor readiness failed",
        "payload": {**safe_payload, "visible_to_user": False, "admin_only": True},
    }
    assert public_execution_event_from_row(
        "run-a",
        {
            "id": "event-private-readiness",
            "sequence": 1,
            "event_type": bridged["event_type"],
            "payload_json": bridged["payload"],
            "created_at": None,
        },
    ) is None
