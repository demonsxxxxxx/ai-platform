from dataclasses import dataclass
from typing import Literal


CapabilityId = Literal["general_chat"]


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: CapabilityId
    label: str
    description: str
    agent_id: str
    skill_id: str
    input_modes: list[str]
    output_modes: list[str]
    user_visible: bool = True


CAPABILITIES: dict[str, CapabilityDefinition] = {
    "general_chat": CapabilityDefinition(
        capability_id="general_chat",
        label="通用聊天",
        description="回答普通问题，支持连续对话。",
        agent_id="general-agent",
        skill_id="general-chat",
        input_modes=["chat"],
        output_modes=["answer"],
    ),
}


def get_capability(capability_id: str) -> CapabilityDefinition | None:
    return CAPABILITIES.get(capability_id)
