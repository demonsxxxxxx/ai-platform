"""Validate the locked Worker Profile against current Profile admission."""

from collections.abc import Awaitable, Callable
from typing import Any


async def reauthorize_worker_locked_profile(
    conn: Any, *, payload: Any, principal: Any, run_identity: dict[str, str],
    reauthorize: Callable[..., Awaitable[Any]],
    snapshot_matches: Callable[..., bool],
    extract_mcp_tool_ids: Callable[..., Any],
) -> tuple[Any, str | None]:
    if not payload.agent_profile or principal is None:
        return payload, None
    admission = await reauthorize(
        conn, principal=principal, agent_id=run_identity["agent_id"],
        revision=int(payload.agent_profile["revision"]),
        content_hash=str(payload.agent_profile["content_hash"]),
        pinned_skill_set=payload.agent_profile.get("skill_set"),
        pinned_manifests=payload.skill_manifests,
        pinned_executor_type=(payload.executor_type or "claude-agent-worker"),
        execution_kind=payload.execution_kind,
    )
    if admission is None:
        return payload, "profile_not_authorized"
    if not snapshot_matches(payload, admission):
        return payload, "profile_snapshot_invalid"
    admitted_mcp_tool_ids = tuple(admission.mcp_tool_ids)
    queued_mcp_tool_ids = tuple(extract_mcp_tool_ids(payload.input))
    admitted_mcp_tool_ids = tuple(
        item for item in queued_mcp_tool_ids if item in admitted_mcp_tool_ids
    )
    if queued_mcp_tool_ids != admitted_mcp_tool_ids:
        local_input = dict(payload.input)
        local_input["mcp_tool_ids"] = list(admitted_mcp_tool_ids)
        payload = payload.model_copy(update={"input": local_input})
    return payload, None


def worker_profile_snapshot_matches(
    *,
    payload_profile: dict[str, Any] | None,
    payload_input: dict[str, Any],
    admission: Any,
    validate_mcp_selector: Callable[..., Any],
    selector_errors: tuple[type[Exception], ...],
) -> bool:
    private_execution_input = getattr(admission, "private_execution_input", None)
    authority_mcp_tool_ids = getattr(admission, "mcp_tool_ids", None)
    if not isinstance(private_execution_input, dict) or not isinstance(authority_mcp_tool_ids, tuple):
        return False
    try:
        validate_mcp_selector(payload_input)
    except selector_errors:
        return False
    return payload_profile == private_execution_input
