from app.projection_redaction import (
    default_skill_id_for_public_agent,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    redact_raw_skill_references,
    sanitize_user_control_input,
)


def test_public_agent_projection_maps_known_internal_agent_ids():
    assert public_agent_id_for_projection("qa-word-review", "qa-file-reviewer") == "document-review"
    assert public_agent_id_for_projection("sop-assistant", "ragflow-knowledge-search") == "knowledge-answer"
    assert public_agent_id_for_projection("baoyu-translate", "baoyu-translate") == "document-translation"
    assert public_agent_id_for_projection("general-agent", "general-chat") == "general-agent"


def test_public_agent_request_ids_map_back_to_internal_selectors():
    assert internal_agent_id_for_request("document-review") == "qa-word-review"
    assert default_skill_id_for_public_agent("document-review") == "qa-file-reviewer"
    assert internal_agent_id_for_request("document-translation") == "baoyu-translate"
    assert default_skill_id_for_public_agent("document-translation") == "baoyu-translate"
    assert internal_agent_id_for_request("knowledge-answer") == "sop-assistant"
    assert default_skill_id_for_public_agent("knowledge-answer") == "ragflow-knowledge-search"
    assert internal_agent_id_for_request("general-agent") == "general-agent"
    assert default_skill_id_for_public_agent("general-agent") is None


def test_redact_raw_skill_references_sanitizes_nested_agent_ids():
    payload = {
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "intent": {
            "agent_id": "sop-assistant",
            "skill_id": "ragflow-knowledge-search",
        },
    }

    redacted = redact_raw_skill_references(payload)

    assert redacted["agent_id"] == "document-review"
    assert redacted["capability_id"] == "document_review"
    assert redacted["intent"]["agent_id"] == "knowledge-answer"
    assert redacted["intent"]["capability_id"] == "knowledge_answer"
    assert "qa-word-review" not in str(redacted)
    assert "sop-assistant" not in str(redacted)
    assert "qa-file-reviewer" not in str(redacted)
    assert "ragflow-knowledge-search" not in str(redacted)


def test_sanitize_user_control_input_removes_server_owned_multi_agent_dispatch_metadata():
    payload = {
        "message": "run",
        "resume": {"copied_from_run_id": "run-forged"},
        "multi_agent_dispatch": {
            "orchestration_state": "awaiting_dispatch",
            "parent_run_id": "run-parent",
        },
        "nested": {
            "multi_agent_dispatch": {"parent_run_id": "run-nested"},
            "dispatch_state": "handed_off",
            "dispatch_child_run_id": "run-child",
            "copied_from_run_id": "run-parent",
            "parent_step_id": "step-code",
        },
    }

    sanitized = sanitize_user_control_input(payload)

    assert sanitized["message"] == "run"
    assert "resume" not in sanitized
    assert "multi_agent_dispatch" not in sanitized
    assert "multi_agent_dispatch" not in sanitized["nested"]
    assert "dispatch_state" not in sanitized["nested"]
    assert "dispatch_child_run_id" not in sanitized["nested"]
    assert "copied_from_run_id" not in sanitized["nested"]
    assert "parent_step_id" not in sanitized["nested"]
