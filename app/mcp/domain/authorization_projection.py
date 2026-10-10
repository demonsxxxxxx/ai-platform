"""Project the authorized MCP registration into the current run input."""

from typing import Any, Callable

from app.mcp.domain.tool_references import mcp_runtime_metadata_usable


def mcp_tool_lifecycle_status(tool: dict[str, Any]) -> str:
    if (
        str(tool.get("effective_status") or "disabled") == "active"
        and str(tool.get("server_status") or "disabled") == "active"
        and bool(tool.get("visible_to_user", True))
    ):
        return "active"
    return "disabled"


def mcp_capability_subject(
    tool: dict[str, Any],
    *,
    distribution_usable: bool,
    sanitize_label: Callable[[Any], str],
) -> dict[str, Any] | None:
    server_id = str(tool.get("server_id") or "")
    tool_id = str(tool.get("tool_id") or "")
    allowed_tools = tool.get("allowed_tools")
    if not mcp_runtime_metadata_usable(tool):
        return None
    tool_identifier = allowed_tools[0]
    subject: dict[str, Any] = {
        "identity": f"mcp__{server_id}__{tool_identifier}",
        "mcp_server": server_id,
        "mcp_tool": tool_identifier,
        "public_tool_label": (sanitize_label(tool.get("name")) or tool_id)[:120],
        "public_tool_category": "mcp",
        "registered": True,
        "declared": True,
        "active": all(
            str(tool.get(key) or "") == "active"
            for key in ("registry_status", "policy_status", "server_status")
        ),
        "distributed": distribution_usable,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "risk_level": str(tool.get("risk_level") or "low"),
        "write_capable": bool(tool.get("write_capable")),
        "parameter_delegation": "external_mcp",
    }
    subject.update(capability_id=tool_id)
    return subject


def authorized_mcp_registration_input(
    run_input: dict[str, Any],
    *,
    allowed_entries: list[dict[str, Any]],
    tool_policy_subjects: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed_tool_ids = {
        str(entry.get("tool_id") or "").strip()
        for entry in allowed_entries
        if str(entry.get("tool_id") or "").strip()
    }
    rebuilt = dict(run_input)
    requested: list[str] = []
    selector_present = False
    for key in ("mcp_tool_ids", "mcpToolIds"):
        if key not in run_input:
            continue
        selector_present = True
        for value in run_input[key]:
            tool_id = str(value).strip()
            if tool_id and tool_id in allowed_tool_ids and tool_id not in requested:
                requested.append(tool_id)
        rebuilt.pop(key, None)
    if selector_present:
        rebuilt["mcp_tool_ids"] = requested
    rebuilt["_runtime_tool_policy_subjects"] = tool_policy_subjects
    return rebuilt
