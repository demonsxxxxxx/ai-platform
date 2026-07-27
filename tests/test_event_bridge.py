import pytest

from app.public_execution import public_execution_event_from_row
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import AgentEvent


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
