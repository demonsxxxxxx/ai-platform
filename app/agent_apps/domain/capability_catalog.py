from dataclasses import dataclass
from typing import Literal


CapabilityId = Literal["general_chat"]


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: CapabilityId
    label: str
    agent_id: str
    skill_id: str | None


CAPABILITIES: dict[str, CapabilityDefinition] = {
    "general_chat": CapabilityDefinition(
        capability_id="general_chat",
        label="通用聊天",
        agent_id="general-agent",
        skill_id=None,
    ),
}


def get_capability(capability_id: str) -> CapabilityDefinition | None:
    return CAPABILITIES.get(capability_id)
