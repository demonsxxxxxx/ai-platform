"""Compatibility seam for callers migrated to :mod:`app.agent_apps`."""

from app import repositories
from app.agent_apps.api import pin_agent_skill_set
from app.agent_apps.authority import (
    AgentProfileAdmission,
    AgentProfileAuthority,
    profile_public_projection,
    reject_profile_selector_conflicts,
)
from app.skills.pinning import attach_skill_snapshot_governance, governed_locked_skill_version
from app.skills.release_policy import (
    release_decision_payload_for_locked_version,
    resolve_rollout_skill_decision,
)


_authority = AgentProfileAuthority()

__all__ = [
    "AgentProfileAdmission",
    "get_public_profile",
    "list_admin_profiles",
    "list_public_profiles",
    "pin_profile_skill_set_for_admission",
    "profile_public_projection",
    "publish_draft",
    "reauthorize_bound_profile_for_worker_dispatch",
    "reauthorize_pinned_run_for_replay",
    "reject_profile_selector_conflicts",
    "resolve_bound_profile_for_submission",
    "resolve_profile_for_admission",
    "save_draft",
]


async def save_draft(conn, *, principal, definition, agent_id):
    """Save through the authoritative Agent Apps lifecycle module."""

    return await _authority.save_draft(conn, principal=principal, definition=definition, agent_id=agent_id)


async def publish_draft(conn, *, principal, agent_id, expected_revision):
    """Publish through the authoritative Agent Apps lifecycle module."""

    return await _authority.publish_draft(
        conn,
        principal=principal,
        agent_id=agent_id,
        expected_revision=expected_revision,
    )


async def list_admin_profiles(conn, *, principal):
    """List Builder profiles through the authoritative module."""

    return await _authority.list_admin(conn, principal=principal)


async def list_public_profiles(conn, *, principal, query=None, category=None):
    """List public profiles through the authoritative module."""

    return await _authority.list_public(conn, principal=principal, query=query, category=category)


async def get_public_profile(conn, *, principal, agent_id):
    """Get one current public profile through the authoritative authorization path."""

    return await _authority.get_public(conn, principal=principal, agent_id=agent_id)


async def pin_profile_skill_set_for_admission(
    conn, *, admission, input_payload, tenant_id, rollout_key, governed_manifest_pins
):
    """Bridge legacy Chat admission to the Agent Apps Skill Set use case."""

    return await pin_agent_skill_set(
        admission.skills,
        manifest_scope=conn,
        input_payload=input_payload,
        tenant_id=tenant_id,
        rollout_key=rollout_key,
        resolve_release_decision=resolve_rollout_skill_decision,
        governed_manifest_pins=governed_manifest_pins,
        locked_skill_version=governed_locked_skill_version,
        decision_payload_for_version=release_decision_payload_for_locked_version,
        attach_snapshot_governance=attach_skill_snapshot_governance,
        pin_mcp_tool_ids=repositories.pin_primary_skill_mcp_tool_ids,
        mcp_tool_ids_for_skill=repositories.run_mcp_tool_ids_for_skill,
        conflict_error=repositories.RepositoryConflictError,
    )


async def resolve_profile_for_admission(
    conn,
    *,
    principal,
    selection,
    submitted_request=None,
    query_agent_id=None,
) -> AgentProfileAdmission:
    """Resolve a current publication through the authoritative module."""

    authority_kwargs = {"principal": principal, "selection": selection}
    if submitted_request is not None:
        authority_kwargs["submitted_request"] = submitted_request
    if query_agent_id is not None:
        authority_kwargs["query_agent_id"] = query_agent_id
    return await _authority.resolve_for_admission(conn, **authority_kwargs)


async def resolve_bound_profile_for_submission(
    conn,
    *,
    principal,
    agent_id,
    revision,
    content_hash,
    submitted_request=None,
    query_agent_id=None,
) -> AgentProfileAdmission:
    """Resolve a durable conversation pin without moving it to a later publication."""

    authority_kwargs = {
        "principal": principal,
        "agent_id": agent_id,
        "revision": revision,
        "content_hash": content_hash,
    }
    if submitted_request is not None:
        authority_kwargs["submitted_request"] = submitted_request
    if query_agent_id is not None:
        authority_kwargs["query_agent_id"] = query_agent_id
    return await _authority.resolve_bound_for_submission(conn, **authority_kwargs)


async def reauthorize_pinned_run_for_replay(conn, *, principal, run_id) -> None:
    """Reauthorize a persisted run through the sole Agent Profile authority."""

    await _authority.reauthorize_pinned_run_for_replay(
        conn,
        principal=principal,
        run_id=run_id,
    )


async def reauthorize_bound_profile_for_worker_dispatch(
    conn,
    *,
    principal,
    agent_id,
    revision,
    content_hash,
):
    """Resolve current Profile authority and immutable execution input for a worker."""

    return await _authority.resolve_bound_for_worker_dispatch(
        conn,
        principal=principal,
        agent_id=agent_id,
        revision=revision,
        content_hash=content_hash,
    )
