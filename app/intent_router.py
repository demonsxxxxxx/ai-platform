from dataclasses import dataclass, field

from app.capabilities import get_capability


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class FileSummary:
    file_id: str
    name: str = ""
    content_type: str = ""


@dataclass(frozen=True)
class CapabilitySuggestion:
    capability_id: str
    label: str
    reason: str


@dataclass(frozen=True)
class IntentDecision:
    status: str
    intent: str
    confidence: float
    reason: str
    selected_capability: str | None
    agent_id: str | None
    skill_id: str | None
    confirmed_by_user: bool = False
    suggestions: list[CapabilitySuggestion] = field(default_factory=list)

    def as_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "selected_capability": self.selected_capability,
            "agent_id": self.agent_id,
            "skill_id": self.skill_id,
            "confirmed_by_user": self.confirmed_by_user,
            "suggestions": [
                {
                    "capability_id": item.capability_id,
                    "label": item.label,
                    "reason": item.reason,
                }
                for item in self.suggestions
            ],
        }


def _has_docx(files: list[FileSummary]) -> bool:
    return any(item.name.lower().endswith(".docx") or item.content_type.lower() == DOCX_MIME for item in files)


def _looks_like_long_task(text: str) -> bool:
    long_task_tokens = (
        "实现",
        "写代码",
        "改代码",
        "测试验证",
        "运行测试",
        "部署",
        "多步骤",
        "长任务",
        "生成文件",
        "调用工具",
        "沙箱",
        "mcp",
        "coding",
        "debug",
    )
    return any(token in text for token in long_task_tokens)


def _selected(
    intent: str,
    capability_id: str,
    confidence: float,
    reason: str,
    confirmed_by_user: bool = False,
) -> IntentDecision:
    capability = get_capability(capability_id)
    if capability is None:
        raise ValueError(f"unknown_capability:{capability_id}")
    return IntentDecision(
        status="selected",
        intent=intent,
        confidence=confidence,
        reason=reason,
        selected_capability=capability.capability_id,
        agent_id=capability.agent_id,
        skill_id=capability.skill_id,
        confirmed_by_user=confirmed_by_user,
    )


def _suggestion(capability_id: str, reason: str) -> CapabilitySuggestion:
    capability = get_capability(capability_id)
    if capability is None:
        raise ValueError(f"unknown_capability:{capability_id}")
    return CapabilitySuggestion(capability_id=capability.capability_id, label=capability.label, reason=reason)


def confirm_capability(capability_id: str) -> IntentDecision:
    if capability_id == "document_review":
        return _selected("document_review", capability_id, 1.0, "用户确认按文档审核处理", confirmed_by_user=True)
    if capability_id == "document_translation":
        return _selected("document_translation", capability_id, 1.0, "用户确认按文档翻译处理", confirmed_by_user=True)
    if capability_id == "knowledge_answer":
        return _selected("knowledge_answer", capability_id, 1.0, "用户确认按知识库问答处理", confirmed_by_user=True)
    if capability_id == "general_chat":
        return _selected("general_chat", capability_id, 1.0, "用户确认按普通分析处理", confirmed_by_user=True)
    raise ValueError(f"unknown_capability:{capability_id}")


def route_intent(
    message: str,
    files: list[FileSummary],
    confirmed_capability_id: str | None = None,
) -> IntentDecision:
    if confirmed_capability_id:
        return confirm_capability(confirmed_capability_id)

    text = (message or "").lower()
    has_docx = _has_docx(files)
    review_tokens = ("审核", "审查", "review", "qa")
    translate_tokens = ("翻译", "translate", "英文", "中文", "english", "chinese")
    knowledge_tokens = ("sop", "知识库", "制度", "流程", "规范", "账号", "权限", "申请")

    if has_docx and any(token in text for token in review_tokens):
        return _selected("document_review", "document_review", 0.92, "检测到 Word 文件和审核意图")
    if has_docx and any(token in text for token in translate_tokens):
        return _selected("document_translation", "document_translation", 0.92, "检测到 Word 文件和翻译意图")
    if not has_docx and any(token in text for token in knowledge_tokens):
        return _selected("knowledge_answer", "knowledge_answer", 0.82, "检测到知识库或 SOP 问答意图")
    if not has_docx and _looks_like_long_task(text):
        return _selected("long_task", "general_chat", 0.78, "检测到需要多步骤执行的复杂任务")
    if has_docx:
        return IntentDecision(
            status="needs_confirmation",
            intent="ambiguous_file_task",
            confidence=0.45,
            reason="检测到 Word 文件，但未明确是审核、翻译还是普通分析",
            selected_capability=None,
            agent_id=None,
            skill_id=None,
            suggestions=[
                _suggestion("document_review", "审核这个 Word"),
                _suggestion("document_translation", "翻译这个 Word"),
                _suggestion("general_chat", "普通分析"),
            ],
        )
    return _selected("general_chat", "general_chat", 0.74, "未检测到文件型或知识库专属意图")
