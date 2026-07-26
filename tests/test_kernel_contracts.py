import pytest
from pydantic import ValidationError

from app.runtime.kernel_contracts import AgentEvent


@pytest.mark.parametrize(
    "event_type",
    ["capability_invoking", "capability_completed", "capability_failed"],
)
def test_capability_lifecycle_facts_are_typed_agent_events(event_type):
    event = AgentEvent(
        type=event_type,
        message="Capability lifecycle update",
        payload={"capability": {"kind": "mcp", "name": "Tenant Search", "status": "failed"}},
    )

    assert event.type == event_type


def test_unknown_capability_event_type_fails_closed():
    with pytest.raises(ValidationError, match="Unsupported agent event type"):
        AgentEvent(type="capability_started", payload={})
