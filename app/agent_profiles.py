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
    "list_admin_profiles",
    "list_public_profiles",
    "profile_public_projection",
    "publish_draft",
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


async def resolve_profile_for_admission(conn, *, principal, selection) -> AgentProfileAdmission:
    """Resolve a current publication through the authoritative module."""

    return await _authority.resolve_for_admission(conn, principal=principal, selection=selection)


async def resolve_bound_profile_for_submission(
    conn,
    *,
    principal,
    agent_id,
    revision,
    content_hash,
) -> AgentProfileAdmission:
    """Resolve a durable conversation pin without moving it to a later publication."""

    return await _authority.resolve_bound_for_submission(
        conn,
        principal=principal,
        agent_id=agent_id,
        revision=revision,
        content_hash=content_hash,
    )


async def reauthorize_pinned_run_for_replay(conn, *, principal, run_id) -> None:
    """Reauthorize a persisted run through the sole Agent Profile authority."""

    await _authority.reauthorize_pinned_run_for_replay(
        conn,
        principal=principal,
        run_id=run_id,
    )
