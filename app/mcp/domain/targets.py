from collections.abc import Mapping

from app.mcp.domain.errors import McpRuntimeContextError
from app.mcp.domain.identifiers import assert_safe_mcp_id


def normalize_mcp_targets(
    value: Mapping[str, object] | None,
) -> dict[str, tuple[str, ...]]:
    """Return a fail-closed, server-grouped capability target map."""

    normalized: dict[str, tuple[str, ...]] = {}
    for raw_server_id, raw_tool_names in (value or {}).items():
        try:
            server_id = assert_safe_mcp_id(str(raw_server_id), "mcp_server_id")
        except ValueError as exc:
            raise McpRuntimeContextError(
                "mcp_target_selection_invalid",
                status_code=403,
            ) from exc
        if not isinstance(raw_tool_names, (list, tuple)):
            raise McpRuntimeContextError("mcp_target_selection_invalid", status_code=403)
        tool_names = tuple(
            dict.fromkeys(
                name
                for item in raw_tool_names
                if isinstance(item, str)
                for name in (item.strip(),)
                if name and "\x00" not in name and "\r" not in name and "\n" not in name
            )
        )
        if not tool_names:
            raise McpRuntimeContextError("mcp_target_selection_invalid", status_code=403)
        normalized[server_id] = tool_names
    return normalized


def mcp_targets_from_policy_subjects(
    value: object,
) -> dict[str, tuple[str, ...]]:
    """Derive targets only from worker-authorized canonical subjects."""

    targets: dict[str, list[str]] = {}
    if not isinstance(value, (list, tuple)):
        return {}
    for subject in value:
        if not isinstance(subject, dict):
            continue
        identity = str(subject.get("identity") or "")
        server_id = str(subject.get("mcp_server") or "")
        tool_name = str(subject.get("mcp_tool") or "")
        if not identity.startswith("mcp__") or not server_id or not tool_name:
            continue
        targets.setdefault(server_id, []).append(tool_name)
    return normalize_mcp_targets(targets)


def mcp_targets_from_reconciliation_snapshot(
    value: object,
) -> dict[str, tuple[str, ...]]:
    """Read only canonical MCP subjects from a persisted v1/v2 Run snapshot."""

    if not isinstance(value, dict):
        return {}
    execution_payload = value.get("execution_payload")
    persisted_payload = execution_payload if isinstance(execution_payload, dict) else value
    run_input = persisted_payload.get("input")
    if not isinstance(run_input, dict):
        return {}
    return mcp_targets_from_policy_subjects(
        run_input.get("_runtime_tool_policy_subjects")
    )
