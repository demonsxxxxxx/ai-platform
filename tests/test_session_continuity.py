import asyncio

import pytest

from app.session_continuity import InMemorySessionContinuityStore, SessionContinuity


@pytest.mark.asyncio
async def test_session_continuity_reuses_same_sdk_session_for_same_scope_and_serializes_writes():
    continuity = SessionContinuity(InMemorySessionContinuityStore())
    first = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="claude-sonnet",
    )
    second = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="claude-sonnet",
    )

    assert first.sdk_session_id == second.sdk_session_id
    assert first.forked is False
    order: list[str] = []

    async def run_with_lock(name: str, delay: float) -> None:
        async with continuity.sdk_session_lock(first.lock_key):
            order.append(f"{name}:start")
            await asyncio.sleep(delay)
            order.append(f"{name}:end")

    await asyncio.gather(run_with_lock("a", 0.01), run_with_lock("b", 0))

    assert order == ["a:start", "a:end", "b:start", "b:end"]


@pytest.mark.asyncio
async def test_session_continuity_forks_parallel_exploration_and_isolates_lock_key():
    continuity = SessionContinuity(InMemorySessionContinuityStore())
    base = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="claude-sonnet",
    )
    fork = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="claude-sonnet",
        fork_reason="parallel_exploration",
    )

    assert fork.forked is True
    assert fork.sdk_session_id != base.sdk_session_id
    assert fork.lock_key != base.lock_key
    assert "parallel_exploration" in fork.sdk_session_id


@pytest.mark.asyncio
async def test_session_continuity_changes_model_or_skill_to_new_resume_scope():
    continuity = SessionContinuity(InMemorySessionContinuityStore())
    base = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="claude-sonnet",
    )
    different_model = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="claude-opus",
    )
    different_skill = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="qa-file-reviewer",
        model_key="claude-sonnet",
    )

    assert different_model.sdk_session_id != base.sdk_session_id
    assert different_skill.sdk_session_id != base.sdk_session_id
