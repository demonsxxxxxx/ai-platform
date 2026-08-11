import re
from dataclasses import dataclass, field, replace
from typing import Literal

from app.required_tool_contract import parse_required_tool_declaration


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


_ZH_OPERATION = r"调用|使用|运行|执行|查询|检索|搜索|处理"
_EN_OPERATION = r"call|use|run|invoke|query|search|execute|process"
_ZH_EXECUTION_OBJECT = r"mcp|skill|技能|工具|知识库|文档|文件"
_EN_EXECUTION_OBJECT = r"mcp|skill|tool|knowledge\s+base|document|file"
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


def classify_execution_polarity(message: str) -> ExecutionPolarity:
    """Classify only whether this turn asks for execution, never which workflow to run."""

    text = " ".join((message or "").lower().split())
    if _NEGATED_OPERATION.search(text) or _EXPLANATION_ONLY.search(text):
        return "non_execution"
    if _AFFIRMATIVE_OPERATION.search(text):
        return "affirmative"
    return "unspecified"


def _looks_like_long_task(text: str) -> bool:
    return any(
        token in text
        for token in (
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
    )


def _general_decision(
    *,
    intent: str = "general_chat",
    reason: str = "已交由 Agent 自主处理",
    confidence: float = 0.74,
    confirmed_by_user: bool = False,
    execution_polarity: ExecutionPolarity = "unspecified",
) -> IntentDecision:
    return IntentDecision(
        status="selected",
        intent=intent,
        confidence=confidence,
        reason=reason,
        selected_capability="general_chat",
        agent_id="general-agent",
        skill_id="general-chat",
        confirmed_by_user=confirmed_by_user,
        execution_polarity=execution_polarity,
    )


def confirm_capability(capability_id: str) -> IntentDecision:
    if capability_id != "general_chat":
        raise ValueError(f"unknown_capability:{capability_id}")
    return _general_decision(
        reason="用户确认由通用 Agent 处理",
        confidence=1.0,
        confirmed_by_user=True,
        execution_polarity="affirmative",
    )


def fallback_to_general_chat(
    *, execution_polarity: ExecutionPolarity = "unspecified"
) -> IntentDecision:
    return _general_decision(execution_polarity=execution_polarity)


def route_intent(
    message: str,
    files: list[FileSummary],
    confirmed_capability_id: str | None = None,
    *,
    execution_polarity: ExecutionPolarity | None = None,
) -> IntentDecision:
    """Route to one general Agent; the SDK decides whether an available Skill applies."""

    _ = files
    polarity = execution_polarity or classify_execution_polarity(message)
    if polarity == "non_execution":
        return fallback_to_general_chat(execution_polarity=polarity)
    decision = (
        confirm_capability(confirmed_capability_id)
        if confirmed_capability_id
        else _general_decision(
            intent="long_task" if _looks_like_long_task((message or "").lower()) else "general_chat",
            reason="已交由 Agent 自主选择可用工具和 Skill",
            confidence=0.78 if _looks_like_long_task((message or "").lower()) else 0.74,
            execution_polarity=polarity,
        )
    )
    declaration = parse_required_tool_declaration(message)
    return replace(
        decision,
        required_tool=declaration.to_payload() if declaration is not None else None,
    )
