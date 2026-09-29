from dataclasses import dataclass
from typing import Literal


CapabilityId = Literal["general_chat", "knowledge_answer"]


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: CapabilityId
    label: str
    description: str
    agent_id: str
    skill_id: str | None
    input_modes: list[str]
    output_modes: list[str]
    user_visible: bool = True


CAPABILITIES: dict[str, CapabilityDefinition] = {
    "general_chat": CapabilityDefinition(
        capability_id="general_chat",
        label="通用聊天",
        description="回答普通问题，支持连续对话。",
        agent_id="general-agent",
        skill_id=None,
        input_modes=["chat"],
        output_modes=["answer"],
    ),
    "knowledge_answer": CapabilityDefinition(
        capability_id="knowledge_answer",
        label="知识库问答",
        description="基于公司知识库和 SOP 检索回答。",
        agent_id="sop-assistant",
        skill_id="ragflow-knowledge-search",
        input_modes=["chat"],
        output_modes=["answer", "citations"],
    ),
}


def get_capability(capability_id: str) -> CapabilityDefinition | None:
    return CAPABILITIES.get(capability_id)
