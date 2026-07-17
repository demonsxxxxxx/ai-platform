from app.intent_router import FileSummary, fallback_to_general_chat, route_intent


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
    assert decision.skill_id == "general-chat"


def test_implicit_route_fallback_uses_non_confirmed_general_chat_decision():
    decision = fallback_to_general_chat()

    assert decision.status == "selected"
    assert decision.intent == "general_chat"
    assert decision.selected_capability == "general_chat"
    assert decision.agent_id == "general-agent"
    assert decision.skill_id == "general-chat"
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
