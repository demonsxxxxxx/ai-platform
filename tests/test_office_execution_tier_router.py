from app.office_execution_tier import route_office_execution_tier


def test_routes_lightweight_writing_to_sdk_only_without_sandbox():
    decision = route_office_execution_tier(
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={"message": "Rewrite this paragraph in a concise office tone."},
        file_ids=[],
    )

    assert decision == {
        "execution_tier": "sdk_only_writing",
        "uses_sandbox_by_default": False,
        "reason": "lightweight_office_writing",
    }


def test_routes_document_generation_to_document_worker_without_sandbox():
    decision = route_office_execution_tier(
        agent_id="baoyu-translate",
        skill_id="baoyu-translate",
        input_payload={"message": "Translate this DOCX and return a reviewed Word document."},
        file_ids=["file-a"],
    )

    assert decision == {
        "execution_tier": "document_worker",
        "uses_sandbox_by_default": False,
        "reason": "document_processing_skill",
    }


def test_routes_risky_tool_or_browser_work_to_heavy_sandbox():
    decision = route_office_execution_tier(
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={
            "message": "Run this Python script and open the browser to verify the page.",
            "sandbox_mode": "ephemeral",
        },
        file_ids=[],
    )

    assert decision == {
        "execution_tier": "heavy_sandbox",
        "uses_sandbox_by_default": True,
        "reason": "explicit_sandbox_or_risky_tooling",
    }


def test_attachments_do_not_force_lightweight_office_task_into_sandbox():
    decision = route_office_execution_tier(
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={"message": "Summarize the attached meeting notes."},
        file_ids=["file-a"],
    )

    assert decision["execution_tier"] == "sdk_only_writing"
    assert decision["uses_sandbox_by_default"] is False
