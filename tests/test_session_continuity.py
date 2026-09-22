from uuid import UUID

import pytest

from app.execution.api import claude_provider_session_dispatch


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


def _provider_dispatch(payload, *, execution_mode="native_resume", provider_session_id="00000000-0000-4000-8000-000000000001"):
    del payload
    return claude_provider_session_dispatch(
        None,
        {"conversation_context": {
            "execution_mode": execution_mode,
            "provider_epoch_id": "pe_epoch",
            "provider_session_id": provider_session_id,
            "source_sha256": "a" * 64,
        }},
    )


def test_provider_dispatch_uses_the_frozen_epoch_provider_identity():
    first_run_id = _provider_dispatch(_payload())["sdk_session_id"]
    restarted_worker_id = _provider_dispatch(_payload())["sdk_session_id"]

    assert first_run_id == restarted_worker_id
    UUID(first_run_id)


def test_provider_dispatch_preserves_the_bound_provider_identity():
    assert _provider_dispatch(_payload(), provider_session_id="00000000-0000-4000-8000-000000000001")["sdk_session_id"] != _provider_dispatch(
        _payload(session_id="session-b"),
        provider_session_id="00000000-0000-4000-8000-000000000002",
    )["sdk_session_id"]


def test_provider_dispatch_requires_the_current_epoch_contract():
    with pytest.raises(ValueError, match="provider_session_identity_invalid"):
        claude_provider_session_dispatch(
            _payload(), {"conversation_context": {"execution_mode": "native_resume"}},
        )


def test_provider_dispatch_rejects_retired_platform_bootstrap_mode():
    with pytest.raises(ValueError, match="provider_session_execution_mode_invalid"):
        _provider_dispatch(_payload(), execution_mode="platform_bootstrap")


def test_provider_resume_marker_is_derived_from_the_frozen_mode():
    assert _provider_dispatch(_payload(), execution_mode="native_resume")[
        "provider_session_resume_required"
    ] is True
    assert claude_provider_session_dispatch(
        _payload(), {"conversation_context": {
            "execution_mode": "empty_start",
            "provider_epoch_id": "pe_epoch",
            "provider_session_id": "00000000-0000-4000-8000-000000000001",
            "source_sha256": "a" * 64,
        }},
    )["provider_session_resume_required"] is False
