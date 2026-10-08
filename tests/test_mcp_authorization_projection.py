from app.control_plane_contracts import sanitize_public_text
from app.mcp.api import (
    authorized_mcp_registration_input,
    mcp_capability_subject,
    mcp_tool_lifecycle_status,
)


def test_mcp_subject_requires_usable_metadata_and_removes_private_label():
    tool = {
        "tool_id": "corp-search::query",
        "server_id": "corp-search",
        "allowed_tools": ["query"],
        "name": "private /tmp/secret",
        "registry_status": "active",
        "policy_status": "active",
        "server_status": "active",
        "effective_status": "active",
        "transport_type": "http",
        "endpoint": "",
        "auth_mode": "none",
    }
    subject = mcp_capability_subject(
        tool, distribution_usable=False, sanitize_label=sanitize_public_text
    )
    assert subject["distributed"] is False
    assert subject["public_tool_label"] == "corp-search::query"
    assert mcp_tool_lifecycle_status(tool) == "active"
    assert mcp_tool_lifecycle_status({**tool, "server_status": "disabled"}) == "disabled"
    assert mcp_capability_subject(
        {**tool, "endpoint": "https://user:secret@example.test"},
        distribution_usable=True,
        sanitize_label=sanitize_public_text,
    ) is None


def test_authorized_mcp_registration_intersects_and_deduplicates_both_selectors():
    subjects = [{"identity": "mcp__safe"}]
    source = {
        "mcp_tool_ids": ["safe", "unknown"],
        "mcpToolIds": ["safe", "other"],
        "message": "hello",
    }

    projected = authorized_mcp_registration_input(
        source,
        allowed_entries=[{"tool_id": "safe"}, {"tool_id": "other"}],
        tool_policy_subjects=subjects,
    )

    assert projected == {
        "mcp_tool_ids": ["safe", "other"],
        "message": "hello",
        "_runtime_tool_policy_subjects": subjects,
    }
    assert source["mcp_tool_ids"] == ["safe", "unknown"]


def test_no_selector_does_not_invent_mcp_tool_registration():
    assert authorized_mcp_registration_input(
        {"message": "hello"}, allowed_entries=[], tool_policy_subjects=[]
    ) == {"message": "hello", "_runtime_tool_policy_subjects": []}
