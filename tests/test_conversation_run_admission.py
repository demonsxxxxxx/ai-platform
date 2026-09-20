from __future__ import annotations

import pytest

from app.conversations.application.run_admission import (
    ConversationRunAdmissionError,
    create_admitted_run,
)
from app.knowledge.api import KnowledgeError


def _run_create_kwargs() -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "agent_id": "agent-a",
        "authz_policy_version": 4,
        "admitted_agent_profile_revision": 7,
        "admitted_agent_profile_hash": "a" * 64,
    }


def _execution_input() -> dict[str, object]:
    return {
        "knowledge_enabled": True,
        "knowledge_source_ids": ["ksrc-a"],
        "retrieval_profile_id": "krp-default",
        "knowledge_bindings": [
            {
                "source_id": "ksrc-a",
                "source_authorization_version": 3,
                "ordinal": 0,
                "required": True,
                "retrieval_profile_id": "krp-default",
                "retrieval_profile_revision": 2,
            }
        ],
    }


@pytest.mark.asyncio
async def test_create_admitted_run_uses_one_connection_for_run_and_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object, dict[str, object]]] = []
    conn = object()

    async def create_run(received_conn: object, **kwargs: object) -> str:
        calls.append(("run", received_conn, kwargs))
        return "run-a"

    async def admit(received_conn: object, **kwargs: object) -> dict[str, object]:
        calls.append(("snapshot", received_conn, kwargs))
        return {"content_hash": "b" * 64}

    monkeypatch.setattr(
        "app.conversations.application.run_admission.admit_run_knowledge",
        admit,
    )
    run_id = await create_admitted_run(
        conn,
        create_run,
        _run_create_kwargs(),
        _execution_input(),
    )

    assert run_id == "run-a"
    assert [call[0] for call in calls] == ["run", "snapshot"]
    assert calls[0][1] is conn
    assert calls[1] == (
        "snapshot",
        conn,
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "profile_revision": 7,
            "profile_content_hash": "a" * 64,
            "principal_policy_version": 4,
            "knowledge_source_ids": ["ksrc-a"],
            "retrieval_profile_id": "krp-default",
            "knowledge_bindings": _execution_input()["knowledge_bindings"],
        },
    )


@pytest.mark.asyncio
async def test_create_admitted_run_sanitizes_knowledge_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_run(_conn: object, **_kwargs: object) -> str:
        return "run-a"

    async def reject(_conn: object, **_kwargs: object) -> dict[str, object]:
        raise KnowledgeError("unsafe provider detail: credential")

    monkeypatch.setattr(
        "app.conversations.application.run_admission.admit_run_knowledge",
        reject,
    )
    with pytest.raises(ConversationRunAdmissionError) as exc_info:
        await create_admitted_run(
            object(),
            create_run,
            _run_create_kwargs(),
            _execution_input(),
        )

    assert exc_info.value.code == "knowledge_snapshot_unavailable"
    assert str(exc_info.value) == "knowledge_snapshot_unavailable"


@pytest.mark.asyncio
async def test_create_admitted_run_skips_snapshot_when_agent_knowledge_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def create_run(_conn: object, **_kwargs: object) -> str:
        calls.append("run")
        return "run-a"

    async def unexpected_admit(_conn: object, **_kwargs: object) -> dict[str, object]:
        calls.append("snapshot")
        return {}

    monkeypatch.setattr(
        "app.conversations.application.run_admission.admit_run_knowledge",
        unexpected_admit,
    )

    run_id = await create_admitted_run(
        object(),
        create_run,
        _run_create_kwargs(),
        {"knowledge_enabled": False},
    )

    assert run_id == "run-a"
    assert calls == ["run"]
