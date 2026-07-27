import pytest

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
