from app.conversations.api import FileSummary
import pytest

from app.intent_router import (
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
        confirmed_capability_id="general_chat",
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


def test_docx_review_request_uses_general_chat():
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
    assert decision.intent == "general_chat"
    assert decision.selected_capability == "general_chat"
    assert decision.agent_id == "general-agent"
    assert decision.skill_id is None
    assert decision.confirmed_by_user is False


def test_knowledge_question_uses_general_chat_without_implicit_skill():
    decision = route_intent(message="SOP 里账号权限申请流程是什么？", files=[])

    assert decision.status == "selected"
    assert decision.intent == "general_chat"
    assert decision.selected_capability == "general_chat"
    assert decision.agent_id == "general-agent"
    assert decision.skill_id is None


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


def test_ambiguous_docx_request_does_not_require_confirmation():
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

    assert decision.status == "selected"
    assert decision.selected_capability == "general_chat"
    assert decision.agent_id == "general-agent"
    assert decision.skill_id is None
    assert decision.suggestions == []


def test_retired_capability_confirmation_falls_back_to_general_chat():
    decision = route_intent(
        message="处理一下这个文件",
        files=[],
        confirmed_capability_id="document_review",
    )

    assert decision.status == "selected"
    assert decision.selected_capability == "general_chat"
    assert decision.agent_id == "general-agent"
    assert decision.skill_id is None


def _intent_file_row(**overrides):
    return {
        "tenant_id": "tenant-a", "workspace_id": "workspace-owned",
        "user_id": "user-a", "session_id": "ses-owned", "run_id": None,
        "original_name": "stored.docx", "content_type": "application/docx",
        **overrides,
    }


async def _resolve_test_intent_files(get_file, **overrides):
    from app.conversations.api import resolve_intent_files

    return await resolve_intent_files(
        **{
            "conn": object(), "file_ids": ["file_a"], "attachments": [],
            "tenant_id": "tenant-a", "user_id": "user-a",
            "session_id": "ses-owned", "workspace_id": "workspace-owned",
            "get_file": get_file, **overrides,
        }
    )


@pytest.mark.parametrize("field", ["tenant_id", "workspace_id", "user_id", "session_id", "run_id"])
@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.asyncio
async def test_intent_file_enrichment_rejects_mismatched_or_missing_scope(field, missing):
    row = _intent_file_row(**{field: "other"})
    if missing:
        del row[field]

    async def get_file(*args, **kwargs):
        return row

    assert await _resolve_test_intent_files(get_file) == []


@pytest.mark.parametrize("session_id", [None, "", "ses-owned"])
@pytest.mark.asyncio
async def test_intent_file_enrichment_uses_effective_continuation_workspace(session_id):
    async def get_file(*args, **kwargs):
        return _intent_file_row(session_id=session_id)

    assert await _resolve_test_intent_files(get_file) == [
        FileSummary("file_a", "stored.docx", "application/docx")
    ]
    assert await _resolve_test_intent_files(get_file, workspace_id="workspace-other") == []


@pytest.mark.asyncio
async def test_intent_file_enrichment_preserves_read_order_connection_and_input_precedence():
    conn = object()
    calls = []
    attachments = [
        {"file_id": "file_named", "name": "client.txt"},
        {"key": "file_mime", "mimeType": "text/plain"},
        {"id": "file_empty"}, {"key": "file_empty"},
        {"id": "external", "filename": "external.txt"},
        {"name": "anonymous.txt"},
    ]
    original = [dict(item) for item in attachments]
    ids = ["file_empty", "file_named", "file_b", "file_b"]

    async def get_file(connection, *, tenant_id, file_id):
        assert connection is conn
        calls.append((tenant_id, file_id))
        return _intent_file_row()

    result = await _resolve_test_intent_files(
        get_file, conn=conn, file_ids=ids, attachments=attachments,
    )
    assert calls == [("tenant-a", "file_empty"), ("tenant-a", "file_b")]
    assert result == [
        FileSummary("file_named", "client.txt"), FileSummary("file_mime", "", "text/plain"),
        FileSummary("file_empty", "stored.docx", "application/docx"),
        FileSummary("file_empty", "stored.docx", "application/docx"),
        FileSummary("external", "external.txt"), FileSummary("", "anonymous.txt"),
        FileSummary("file_b", "stored.docx", "application/docx"),
    ]
    assert attachments == original
    assert ids == ["file_empty", "file_named", "file_b", "file_b"]


@pytest.mark.asyncio
async def test_intent_file_enrichment_missing_rows_and_legacy_row_aliases():
    calls = []

    async def get_file(conn, *, tenant_id, file_id):
        calls.append(file_id)
        if file_id == "file_missing":
            return None
        return _intent_file_row(original_name="", content_type="", name="alias.txt", mime_type="text/plain")

    assert await _resolve_test_intent_files(get_file, file_ids=["file_missing", "file_missing", "file_alias"]) == [
        FileSummary("file_alias", "alias.txt", "text/plain")
    ]
    assert calls == ["file_missing", "file_alias"]


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_intent_file_enrichment_preserves_failure_identity_and_stops_reads(cancel):
    import asyncio

    failure = asyncio.CancelledError() if cancel else RuntimeError("read failed")
    calls = []

    async def get_file(conn, *, tenant_id, file_id):
        calls.append(file_id)
        raise failure

    with pytest.raises(type(failure)) as raised:
        await _resolve_test_intent_files(get_file, file_ids=["file_a", "file_b"])
    assert raised.value is failure
    assert calls == ["file_a"]
