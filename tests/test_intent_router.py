import pytest

from app.intent_router import (
    FileSummary,
    classify_execution_polarity,
    fallback_to_general_chat,
    route_intent,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("", "unspecified"),
        ("请问账号流程", "unspecified"),
        ("如何规划这件事", "unspecified"),
        ("can you help with this", "unspecified"),
        ("什么是知识库", "non_execution"),
        ("如何使用 Skill（仅解释）", "non_execution"),
        ("can you explain MCP", "non_execution"),
        ("不要调用 MCP，但请使用 MCP", "non_execution"),
        ("请查询知识库中的账号权限申请流程", "affirmative"),
        ("Please search the knowledge base for the access process", "affirmative"),
        ("please use selected Skill now", "affirmative"),
        ("调用 MCP 搜索员工手册", "affirmative"),
    ],
)
def test_current_turn_execution_polarity(message, expected):
    assert classify_execution_polarity(message) == expected


def test_non_execution_vetoes_confirmed_capability():
    decision = route_intent(
        "不要调用知识库，只解释流程",
        [],
        confirmed_capability_id="knowledge_answer",
    )

    assert decision.execution_polarity == "non_execution"
    assert decision.selected_capability == "general_chat"
    assert decision.confirmed_by_user is False


def test_non_execution_vetoes_required_tool_declaration():
    decision = route_intent("不要执行 Bash 命令 pwd，只解释它", [])

    assert decision.execution_polarity == "non_execution"
    assert decision.selected_capability == "general_chat"
    assert decision.required_tool is None


def test_affirmative_confirmed_capability_does_not_require_bash():
    decision = route_intent(
        "请执行 Bash 命令 pwd",
        [],
        confirmed_capability_id="general_chat",
    )

    assert decision.execution_polarity == "affirmative"
    assert decision.confirmed_by_user is True
    assert decision.required_tool is None


def test_docx_review_routes_to_document_review():
    decision = route_intent(
        message="帮我审核这个 Word，按 QA 标准审查",
        files=[
            FileSummary(
                file_id="file_review",
                name="protocol.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        ],
    )

    assert decision.status == "selected"
    assert decision.intent == "document_review"
    assert decision.selected_capability == "document_review"
    assert decision.agent_id == "qa-word-review"
    assert decision.skill_id == "qa-file-reviewer"
    assert decision.confidence >= 0.85
    assert decision.confirmed_by_user is False


def test_docx_translation_routes_to_document_translation():
    decision = route_intent(
        message="translate this Word file to Chinese",
        files=[
            FileSummary(
                file_id="file_translate",
                name="source.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        ],
    )

    assert decision.status == "selected"
    assert decision.intent == "document_translation"
    assert decision.selected_capability == "document_translation"
    assert decision.agent_id == "baoyu-translate"
    assert decision.skill_id == "baoyu-translate"


def test_knowledge_question_routes_to_knowledge_answer():
    decision = route_intent(message="SOP 里账号权限申请流程是什么？", files=[])

    assert decision.status == "selected"
    assert decision.intent == "knowledge_answer"
    assert decision.selected_capability == "knowledge_answer"
    assert decision.agent_id == "sop-assistant"
    assert decision.skill_id == "ragflow-knowledge-search"


def test_plain_question_routes_to_general_chat():
    decision = route_intent(message="帮我写一段会议纪要", files=[])

    assert decision.status == "selected"
    assert decision.intent == "general_chat"
    assert decision.selected_capability == "general_chat"
    assert decision.agent_id == "general-agent"
    assert decision.skill_id is None


@pytest.mark.parametrize(
    "message",
    [
        "请执行 Bash 命令 pwd",
        "run Bash command pwd",
        "不要执行 Bash 命令 pwd",
        "解释一下 Bash 命令 pwd",
        "请执行 bash 命令 pwd",
        "请执行 Bashful 命令 pwd",
        "请执行 Bash.exe 命令 pwd",
    ],
)
def test_bash_text_does_not_create_a_required_capability(message):
    decision = route_intent(message=message, files=[])

    assert decision.intent in {"general_chat", "long_task"}
    assert decision.skill_id is None
    assert decision.required_tool is None


def test_implicit_route_fallback_uses_non_confirmed_general_chat_decision():
    decision = fallback_to_general_chat()

    assert decision.status == "selected"
    assert decision.intent == "general_chat"
    assert decision.selected_capability == "general_chat"
    assert decision.agent_id == "general-agent"
    assert decision.skill_id is None
    assert decision.reason == "已使用通用对话处理"
    assert decision.confirmed_by_user is False


def test_ambiguous_docx_request_returns_suggestions_without_run_selection():
    decision = route_intent(
        message="处理一下这个文件",
        files=[
            FileSummary(
                file_id="file_docx",
                name="ambiguous.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        ],
    )

    assert decision.status == "needs_confirmation"
    assert decision.selected_capability is None
    assert [item.capability_id for item in decision.suggestions] == [
        "document_review",
        "document_translation",
        "general_chat",
    ]
