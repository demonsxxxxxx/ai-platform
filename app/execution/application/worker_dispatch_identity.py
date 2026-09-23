from __future__ import annotations

from typing import Any

from app import repositories
from app.auth import AuthPrincipal, normalize_roles
from app.control_plane_contracts import RUN_EXECUTION_KIND_HARNESS_CHAT, standard_trace_id
from app.models import QueueRunPayload
from app.validation import assert_canonical_sha256


def locked_agent_profile_identity_valid(
    agent_profile: dict[str, Any],
    locked_run: object,
) -> bool:
    if not isinstance(locked_run, dict):
        return False
    pin_fields = (
        "admitted_agent_profile_revision",
        "admitted_agent_profile_hash",
        "session_admitted_agent_profile_revision",
        "session_admitted_agent_profile_hash",
    )
    if not all(field in locked_run for field in pin_fields):
        return False
    pinned_revision = locked_run.get("admitted_agent_profile_revision")
    pinned_hash = locked_run.get("admitted_agent_profile_hash")
    session_pinned_revision = locked_run.get("session_admitted_agent_profile_revision")
    session_pinned_hash = locked_run.get("session_admitted_agent_profile_hash")
    if not agent_profile:
        return all(
            value is None
            for value in (
                pinned_revision,
                pinned_hash,
                session_pinned_revision,
                session_pinned_hash,
            )
        )
    try:
        if (
            not isinstance(pinned_revision, int)
            or isinstance(pinned_revision, bool)
            or pinned_revision < 1
            or not isinstance(session_pinned_revision, int)
            or isinstance(session_pinned_revision, bool)
            or session_pinned_revision < 1
        ):
            return False
        assert_canonical_sha256(pinned_hash, "agent_profile_hash_invalid")
        assert_canonical_sha256(session_pinned_hash, "agent_profile_hash_invalid")
    except ValueError:
        return False
    return (
        agent_profile.get("agent_id") == locked_run.get("agent_id")
        and agent_profile.get("revision") == pinned_revision
        and agent_profile.get("content_hash") == pinned_hash
        and pinned_revision == session_pinned_revision
        and pinned_hash == session_pinned_hash
    )


def agent_profile_snapshot_matches_authority(
    payload: QueueRunPayload,
    admission: object,
) -> bool:
    private_execution_input = getattr(admission, "private_execution_input", None)
    authority_mcp_tool_ids = getattr(admission, "mcp_tool_ids", None)
    if (
        not isinstance(private_execution_input, dict)
        or not isinstance(authority_mcp_tool_ids, tuple)
    ):
        return False
    try:
        queued_mcp_tool_ids = tuple(repositories.extract_run_mcp_tool_ids(payload.input))
    except (
        repositories.RepositoryAuthorizationError,
        repositories.RepositoryConflictError,
    ):
        return False
    if queued_mcp_tool_ids != authority_mcp_tool_ids:
        return False
    expected = dict(private_execution_input)
    if payload.execution_kind != RUN_EXECUTION_KIND_HARNESS_CHAT:
        authority_skill = getattr(admission, "skill", None)
        if (
            not isinstance(authority_skill, dict)
            or str(authority_skill.get("skill_id") or "") != str(payload.skill_id or "")
            or str(authority_skill.get("skill_version") or "")
            != str(payload.skill_version or "")
            or not payload.skill_version
        ):
            return False
    return payload.agent_profile == expected


def locked_run_trace_id(payload: QueueRunPayload, locked_run: object) -> str:
    if isinstance(locked_run, dict) and locked_run.get("trace_id"):
        return str(locked_run["trace_id"])
    return standard_trace_id(payload.run_id)


def locked_run_principal(
    locked_run: object,
    run_identity: dict[str, str],
) -> AuthPrincipal:
    locked = locked_run if isinstance(locked_run, dict) else {}
    raw_roles = locked.get("principal_roles")
    roles = normalize_roles(raw_roles if isinstance(raw_roles, (list, tuple, set)) else [])
    return AuthPrincipal(
        user_id=run_identity["user_id"],
        display_name=run_identity["user_id"],
        tenant_id=run_identity["tenant_id"],
        department_id=str(locked.get("principal_department_id") or ""),
        roles=roles,
        permissions=[],
        source=str(locked.get("auth_source") or ""),
    )


def locked_run_is_multi_agent_child(locked_run: object) -> bool:
    """Read the durable child-dispatch marker when the queue snapshot is unusable."""

    if not isinstance(locked_run, dict):
        return False
    input_json = locked_run.get("input_json")
    input_payload = input_json.get("input") if isinstance(input_json, dict) else None
    return isinstance(input_payload, dict) and isinstance(
        input_payload.get("multi_agent_dispatch"), dict
    )
