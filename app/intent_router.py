import re
from dataclasses import dataclass, field, replace
from typing import Literal

from app.capabilities import get_capability
from app.required_tool_contract import parse_required_tool_declaration

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ExecutionPolarity = Literal["affirmative", "non_execution", "unspecified"]


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
    required_tool: dict[str, str] | None = None
    execution_polarity: ExecutionPolarity = "unspecified"

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


_ZH_OPERATION = r"调用|使用|运行|执行|查询|检索|搜索|审核|审查|翻译"
_EN_OPERATION = r"call|use|run|invoke|query|search|execute|review|translate"
_ZH_EXECUTION_OBJECT = r"mcp|skill|技能|工具|知识库|sop|文档|文件|word"
_EN_EXECUTION_OBJECT = r"mcp|skill|tool|knowledge\s+base|sop|document|file|word"
_NEGATED_OPERATION = re.compile(
    rf"(?:不要|别|无需|不必|请勿)\s*(?:再\s*)?(?:{_ZH_OPERATION})"
    rf"|\b(?:do\s+not|don't|dont|never)\s+(?:{_EN_OPERATION})\b"
    rf"|\bwithout\s+(?:calling|using|running|invoking|querying|searching|executing)\b"
)
_EXPLANATION_ONLY = re.compile(
    rf"(?:只|仅)\s*(?:要|需|需要)?\s*(?:解释|说明|介绍)"
    rf"|(?:解释|说明|介绍)\s*(?:这个|该|已选)?\s*(?:{_ZH_EXECUTION_OBJECT})"
    rf"|(?:什么是|(?:{_ZH_EXECUTION_OBJECT})\s*(?:是)?什么|(?:{_ZH_EXECUTION_OBJECT})\s*什么意思)"
    rf"|\b(?:(?:only|just)\s+)?(?:explain|describe|define)\s+"
    rf"(?:an?\s+|the\s+)?(?:{_EN_EXECUTION_OBJECT})\b"
    rf"|\bwhat\s+is\s+(?:an?\s+|the\s+)?(?:{_EN_EXECUTION_OBJECT})\b"
)
_AFFIRMATIVE_OPERATION = re.compile(
    rf"(?:{_ZH_OPERATION})[^。！？!?]{{0,12}}(?:{_ZH_EXECUTION_OBJECT})"
    rf"|\b(?:{_EN_OPERATION})\b[^.!?]{{0,24}}\b(?:{_EN_EXECUTION_OBJECT})\b"
)
_SCOPED_KNOWLEDGE_QUERY = re.compile(
    r"(?:知识库|sop)\s*(?:里|中|内)[^。！？!?]+(?:是什么|怎么|如何|哪里|哪一)"
    r"|(?:账号|权限)[^。！？!?]*(?:申请|流程)[^。！？!?]*(?:怎么|如何)(?:做|办理|申请)"
)


def classify_execution_polarity(message: str) -> ExecutionPolarity:
    """Return current-turn polarity from grammatical intent, with veto precedence."""

    text = " ".join((message or "").lower().split())
    if _NEGATED_OPERATION.search(text) or _EXPLANATION_ONLY.search(text):
        return "non_execution"
    if _AFFIRMATIVE_OPERATION.search(text) or _SCOPED_KNOWLEDGE_QUERY.search(text):
        return "affirmative"
    return "unspecified"


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
    execution_polarity: ExecutionPolarity = "unspecified",
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
        execution_polarity=execution_polarity,
    )


def _suggestion(capability_id: str, reason: str) -> CapabilitySuggestion:
    capability = get_capability(capability_id)
    if capability is None:
        raise ValueError(f"unknown_capability:{capability_id}")
    return CapabilitySuggestion(capability_id=capability.capability_id, label=capability.label, reason=reason)


def confirm_capability(capability_id: str) -> IntentDecision:
    if capability_id == "document_review":
        return _selected(
            "document_review",
            capability_id,
            1.0,
            "用户确认按文档审核处理",
            confirmed_by_user=True,
            execution_polarity="affirmative",
        )
    if capability_id == "document_translation":
        return _selected(
            "document_translation",
            capability_id,
            1.0,
            "用户确认按文档翻译处理",
            confirmed_by_user=True,
            execution_polarity="affirmative",
        )
    if capability_id == "knowledge_answer":
        return _selected(
            "knowledge_answer",
            capability_id,
            1.0,
            "用户确认按知识库问答处理",
            confirmed_by_user=True,
            execution_polarity="affirmative",
        )
    if capability_id == "general_chat":
        return _selected(
            "general_chat",
            capability_id,
            1.0,
            "用户确认按普通分析处理",
            confirmed_by_user=True,
            execution_polarity="affirmative",
        )
    raise ValueError(f"unknown_capability:{capability_id}")


def fallback_to_general_chat(
    *, execution_polarity: ExecutionPolarity = "unspecified"
) -> IntentDecision:
    """Return the non-confirmed, safe default when an implicit route is unavailable."""

    return _selected(
        "general_chat",
        "general_chat",
        0.74,
        "已使用通用对话处理",
        execution_polarity=execution_polarity,
    )


def _with_required_tool(decision: IntentDecision, declaration: object) -> IntentDecision:
    payload = declaration.to_payload() if declaration is not None else None
    return replace(decision, required_tool=payload)


def route_intent(
    message: str,
    files: list[FileSummary],
    confirmed_capability_id: str | None = None,
    *,
    execution_polarity: ExecutionPolarity | None = None,
) -> IntentDecision:
    polarity = execution_polarity or classify_execution_polarity(message)
    required_tool = parse_required_tool_declaration(message)
    if polarity == "non_execution":
        return _with_required_tool(
            fallback_to_general_chat(execution_polarity=polarity),
            required_tool,
        )
    if confirmed_capability_id:
        return _with_required_tool(
            confirm_capability(confirmed_capability_id),
            required_tool,
        )

    text = (message or "").lower()
    has_docx = _has_docx(files)
    review_tokens = ("审核", "审查", "review", "qa")
    translate_tokens = ("翻译", "translate", "英文", "中文", "english", "chinese")
    knowledge_tokens = (
        "sop",
        "知识库",
        "制度",
        "流程",
        "规范",
        "账号",
        "权限",
        "申请",
        "knowledge base",
        "procedure",
        "policy",
        "access",
    )

    if has_docx and any(token in text for token in review_tokens):
        return _with_required_tool(
            _selected(
                "document_review",
                "document_review",
                0.92,
                "检测到 Word 文件和审核意图",
                execution_polarity=polarity,
            ),
            required_tool,
        )
    if has_docx and any(token in text for token in translate_tokens):
        return _with_required_tool(
            _selected(
                "document_translation",
                "document_translation",
                0.92,
                "检测到 Word 文件和翻译意图",
                execution_polarity=polarity,
            ),
            required_tool,
        )
    if (
        polarity == "affirmative"
        and not has_docx
        and any(token in text for token in knowledge_tokens)
    ):
        return _with_required_tool(
            _selected(
                "knowledge_answer",
                "knowledge_answer",
                0.82,
                "检测到知识库或 SOP 问答意图",
                execution_polarity=polarity,
            ),
            required_tool,
        )
    if not has_docx and _looks_like_long_task(text):
        return _with_required_tool(
            _selected(
                "long_task",
                "general_chat",
                0.78,
                "检测到需要多步骤执行的复杂任务",
                execution_polarity=polarity,
            ),
            required_tool,
        )
    if has_docx:
        return IntentDecision(
            status="needs_confirmation",
            intent="ambiguous_file_task",
            confidence=0.45,
            reason="检测到 Word 文件，但未明确是审核、翻译还是普通分析",
            selected_capability=None,
            agent_id=None,
            skill_id=None,
            execution_polarity=polarity,
            suggestions=[
                _suggestion("document_review", "审核这个 Word"),
                _suggestion("document_translation", "翻译这个 Word"),
                _suggestion("general_chat", "普通分析"),
            ],
        )
    return _with_required_tool(
        _selected(
            "general_chat",
            "general_chat",
            0.74,
            "未检测到文件型或知识库专属意图",
            execution_polarity=polarity,
        ),
        required_tool,
    )
