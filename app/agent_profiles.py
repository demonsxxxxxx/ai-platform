"""Compatibility seam for callers migrated to :mod:`app.agent_apps`."""

from app.agent_apps.authority import (
    AgentProfileAdmission,
    AgentProfileAuthority,
    profile_public_projection,
    reject_profile_selector_conflicts,
)


_authority = AgentProfileAuthority()

__all__ = [
    "AgentProfileAdmission",
    "get_public_profile",
    "list_admin_profiles",
    "list_public_profiles",
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


async def reauthorize_pinned_run_for_replay(
    conn, *, principal, run_id
) -> AgentProfileAdmission | None:
    """Reauthorize a persisted run through the sole Agent Profile authority."""

    return await _authority.reauthorize_pinned_run_for_replay(
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
