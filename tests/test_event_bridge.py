import pytest

from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import AgentEvent


@pytest.mark.parametrize(
    ("event_type", "status"),
    [("capability_completed", "completed"), ("capability_failed", "failed")],
)
def test_capability_terminal_events_bridge_without_private_fallback(event_type, status):
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
