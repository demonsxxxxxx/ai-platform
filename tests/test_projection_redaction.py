from app.projection_redaction import (
    default_skill_id_for_public_agent,
    internal_agent_id_for_request,
    public_agent_id_for_projection,
    redact_raw_skill_references,
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
