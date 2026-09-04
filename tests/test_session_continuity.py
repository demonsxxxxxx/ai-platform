from uuid import UUID

import pytest

from app.execution.api import claude_provider_session_dispatch, sdk_session_id_for_run


def _payload(*, tenant_id="tenant-a", workspace_id="workspace-a", user_id="user-a", session_id="session-a", agent_id="agent-a"):
    return type(
        "Payload",
        (),
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
        },
    )()


def test_sdk_session_id_for_run_is_distinct_per_immutable_run():
    first_worker_id = sdk_session_id_for_run("run-a")
    restarted_worker_id = sdk_session_id_for_run("run-a")

    assert first_worker_id == restarted_worker_id
    assert first_worker_id != sdk_session_id_for_run("run-b")
    UUID(first_worker_id)


def test_sdk_session_id_for_run_requires_an_immutable_run_identity():
    with pytest.raises(ValueError, match="run_id_required_for_sdk_session"):
        sdk_session_id_for_run("")


def _provider_dispatch(payload, *, resume_required=False):
    return claude_provider_session_dispatch(
        payload,
        {"conversation_context": {"provider_session_resume_required": resume_required}},
    )


def test_sdk_session_id_is_stable_for_one_platform_session_across_runs_and_restarts():
    first_run_id = _provider_dispatch(_payload())["sdk_session_id"]
    restarted_worker_id = _provider_dispatch(_payload())["sdk_session_id"]

    assert first_run_id == restarted_worker_id
    UUID(first_run_id)


def test_sdk_session_id_is_distinct_between_platform_sessions():
    assert _provider_dispatch(_payload(session_id="session-a"))["sdk_session_id"] != _provider_dispatch(
        _payload(session_id="session-b")
    )["sdk_session_id"]


def test_sdk_session_id_requires_a_complete_platform_session_identity():
    with pytest.raises(ValueError, match="provider_session_scope_invalid"):
        _provider_dispatch(_payload(agent_id=""))


def test_provider_resume_marker_is_strict_and_owned_by_execution_dispatch():
    assert _provider_dispatch(_payload(), resume_required=True)[
        "provider_session_resume_required"
    ] is True
    with pytest.raises(ValueError, match="provider_session_resume_required_invalid"):
        claude_provider_session_dispatch(
            _payload(),
            {"conversation_context": {"provider_session_resume_required": "true"}},
        )
