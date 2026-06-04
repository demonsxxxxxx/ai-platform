from dataclasses import dataclass
from typing import Literal


CapabilityId = Literal["general_chat", "document_review", "document_translation", "knowledge_answer"]


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
    "document_review": CapabilityDefinition(
        capability_id="document_review",
        label="文档审核",
        description="审核 Word 文档并生成批注版 Word。",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input_modes=["docx"],
        output_modes=["reviewed_docx"],
    ),
    "document_translation": CapabilityDefinition(
        capability_id="document_translation",
        label="文档翻译",
        description="翻译 Word 文档并生成翻译版 Word。",
        agent_id="baoyu-translate",
        skill_id="baoyu-translate",
        input_modes=["docx"],
        output_modes=["translated_docx"],
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
