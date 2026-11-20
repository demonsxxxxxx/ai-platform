"""System-preinstalled MCP presentation and new-task defaults, not call authority."""

from __future__ import annotations

from typing import Any

from app.mcp.domain.tool_references import build_mcp_tool_reference, is_valid_mcp_public_tool_name

PLUGIN_METADATA_KEY = "mcp_plugin"


def plugin_configuration(metadata: Any, *, integration_kind: str = "mcp") -> dict[str, Any]:
    raw = metadata.get(PLUGIN_METADATA_KEY) if isinstance(metadata, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    presentation = {
        "display_name": str(raw.get("display_name") or ""),
        "description": str(raw.get("description") or ""),
        "user_authorization": "profile_drive" if raw.get("user_authorization") == "profile_drive" else "none",
    }
    if integration_kind == "enterprise_gateway":
        return presentation
    return {
        **presentation,
        "default_tools": [name for name in raw.get("default_tools", [])
                          if isinstance(name, str) and is_valid_mcp_public_tool_name(name)]
        if isinstance(raw.get("default_tools"), list) else [],
    }


def validate_plugin_configuration(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "display_name", "description", "catalog_mode", "user_authorization", "default_tools",
    }:
        raise ValueError("mcp_plugin_configuration_invalid")
    result = {}
    for key, limit in (("display_name", 128), ("description", 1000)):
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > limit or any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise ValueError("mcp_plugin_configuration_invalid")
        result[key] = text.strip()
    # Accept the old input field during client rollout; it no longer affects any read.
    if "catalog_mode" in value and value["catalog_mode"] not in ("protocol", "business_catalog"):
        raise ValueError("mcp_plugin_configuration_invalid")
    authorization = value.get("user_authorization", "none")
    if authorization not in ("none", "profile_drive"):
        raise ValueError("mcp_plugin_configuration_invalid")
    result["user_authorization"] = authorization
    tools = value.get("default_tools", [])
    if (not isinstance(tools, list) or len(tools) > 200
            or any(not isinstance(name, str) or not is_valid_mcp_public_tool_name(name) for name in tools)
            or len(tools) != len(set(tools))):
        raise ValueError("mcp_plugin_defaults_invalid")
    result["default_tools"] = list(tools)
    return result


def public_plugin_projection(name: str, metadata: Any) -> dict[str, Any]:
    config = plugin_configuration(metadata)
    return {
        "display_name": config["display_name"] or name,
        "description": config["description"],
        "installation": "system_preinstalled",
        "user_authorization": config["user_authorization"],
    }


def default_plugin_tool_references(distributions: list[dict[str, Any]]) -> list[str]:
    """Read defaults only; callers must authorize the resulting references."""
    return list(dict.fromkeys(
        build_mcp_tool_reference(str(row["capability_id"]), tool)
        for row in distributions
        if row.get("status") == "active" and row.get("visible_to_user")
        for tool in plugin_configuration(row.get("metadata_json"))["default_tools"]
    ))
