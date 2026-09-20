from __future__ import annotations

import pytest

from app.agent_profile_execution_validation import (
    validate_agent_profile_execution_input,
)
from app.knowledge.api import KnowledgeError
from app.knowledge.application.run_admission import RunKnowledgeAdmissionService


def _binding() -> dict[str, object]:
    return {
        "source_id": "ksrc_policy",
        "source_authorization_version": 1,
        "ordinal": 0,
        "required": True,
        "retrieval_profile_id": "krp_default",
        "retrieval_profile_revision": 1,
    }


def test_agent_execution_snapshot_preserves_only_canonical_knowledge_bindings() -> None:
    binding = _binding()
    profile = {
        "agent_id": "agent-knowledge-runtime",
        "revision": 1,
        "content_hash": "a" * 64,
        "instructions": "Use admitted evidence.",
        "skill_set": [
            {
                "skill_id": "skill-knowledge-runtime",
                "expected_version": "1.0.0",
            }
        ],
        "knowledge_enabled": True,
        "knowledge_source_ids": ["ksrc_policy"],
        "retrieval_profile_id": "krp_default",
        "knowledge_bindings": [binding],
    }

    validated = validate_agent_profile_execution_input(
        profile,
        agent_id="agent-knowledge-runtime",
        execution_kind="skill",
        skill_id="skill-knowledge-runtime",
        skill_version="1.0.0",
    )

    assert validated["knowledge_source_ids"] == ["ksrc_policy"]
    assert validated["knowledge_enabled"] is True
    assert validated["retrieval_profile_id"] == "krp_default"
    assert validated["knowledge_bindings"] == [binding]
    invalid = dict(profile)
    invalid["knowledge_bindings"] = [{**binding, "secret_ref": "forbidden"}]
    with pytest.raises(ValueError, match="knowledge_snapshot_profile_mismatch"):
        validate_agent_profile_execution_input(
            invalid,
            agent_id="agent-knowledge-runtime",
            execution_kind="skill",
            skill_id="skill-knowledge-runtime",
            skill_version="1.0.0",
        )


def test_agent_execution_snapshot_requires_explicit_enabled_state_to_match_payload() -> None:
    base = {
        "agent_id": "agent-knowledge-runtime",
        "revision": 1,
        "content_hash": "a" * 64,
        "instructions": "Use admitted evidence.",
        "skill_set": [
            {
                "skill_id": "skill-knowledge-runtime",
                "expected_version": "1.0.0",
            }
        ],
        "knowledge_enabled": False,
    }
    validated = validate_agent_profile_execution_input(
        base,
        agent_id="agent-knowledge-runtime",
        execution_kind="skill",
        skill_id="skill-knowledge-runtime",
        skill_version="1.0.0",
    )
    assert validated["knowledge_enabled"] is False
    assert "knowledge_source_ids" not in validated

    with pytest.raises(ValueError, match="knowledge_snapshot_profile_mismatch"):
        validate_agent_profile_execution_input(
            {
                **base,
                "knowledge_source_ids": ["ksrc_policy"],
                "retrieval_profile_id": "krp_default",
                "knowledge_bindings": [_binding()],
            },
            agent_id="agent-knowledge-runtime",
            execution_kind="skill",
            skill_id="skill-knowledge-runtime",
            skill_version="1.0.0",
        )


@pytest.mark.asyncio
async def test_run_knowledge_admission_service_forwards_only_valid_bindings() -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    class Repository:
        async def create_run_snapshot_from_bindings(
            self, conn: object, **kwargs: object
        ) -> dict[str, object]:
            calls.append((conn, kwargs))
            return {"content_hash": "b" * 64}

    service = RunKnowledgeAdmissionService(Repository())
    conn = object()
    binding = _binding()

    stored = await service.admit(
        conn,
        tenant_id="tenant-knowledge-runtime",
        run_id="run-knowledge-runtime",
        agent_id="agent-knowledge-runtime",
        profile_revision=1,
        profile_content_hash="a" * 64,
        principal_policy_version=1,
        knowledge_source_ids=["ksrc_policy"],
        retrieval_profile_id="krp_default",
        knowledge_bindings=[binding],
    )

    assert stored == {"content_hash": "b" * 64}
    assert calls == [
        (
            conn,
            {
                "tenant_id": "tenant-knowledge-runtime",
                "run_id": "run-knowledge-runtime",
                "agent_id": "agent-knowledge-runtime",
                "profile_revision": 1,
                "profile_content_hash": "a" * 64,
                "principal_policy_version": 1,
                "knowledge_source_ids": ("ksrc_policy",),
                "retrieval_profile_id": "krp_default",
                "knowledge_bindings": (binding,),
            },
        )
    ]
    with pytest.raises(KnowledgeError, match="knowledge_snapshot_profile_mismatch"):
        await service.admit(
            conn,
            tenant_id="tenant-knowledge-runtime",
            run_id="run-knowledge-runtime",
            agent_id="agent-knowledge-runtime",
            profile_revision=1,
            profile_content_hash="a" * 64,
            principal_policy_version=1,
            knowledge_source_ids=[],
            retrieval_profile_id="krp_default",
            knowledge_bindings=[],
        )
