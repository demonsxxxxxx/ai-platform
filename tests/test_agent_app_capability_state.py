from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.agent_apps.capability_state import (
    bind_validated_controlled_skill_evidence,
    exact_invoked_skills,
    project_agent_capability_state,
)
from app.required_tool_contract import (
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
)


@pytest.mark.parametrize(
    "source",
    [
        "selected",
        "staged",
        "inferred",
        "executor_native",
        "",
    ],
)
def test_untrusted_skill_evidence_never_becomes_actually_invoked(source):
    payload = {
        "used_skills_source": source,
        "staged_skills": ["required-skill"],
        "used_skills": ["required-skill"],
        "sdk_used": True,
    }

    assert exact_invoked_skills(payload) == set()
    state = project_agent_capability_state(
        required_skill_id="required-skill",
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=0,
    )
    assert state.selected is True
    assert state.staged is True
    assert state.sdk_registered is True
    assert state.actually_invoked is False
    assert state.completed is False


def test_platform_controlled_runner_evidence_is_limited_to_the_fixed_staged_set():
    payload = {
        "used_skills_source": "platform_controlled_runner",
        "staged_skills": ["required-skill", "optional-skill"],
        "used_skills": ["required-skill", "unstaged-hostile-skill"],
        "sdk_used": False,
    }

    assert exact_invoked_skills(payload) == set()
    assert exact_invoked_skills(
        payload,
        trusted_controlled_skill_ids={"required-skill"},
    ) == {"required-skill"}
    state = project_agent_capability_state(
        required_skill_id="required-skill",
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=1,
        trusted_controlled_skill_ids={"required-skill"},
    )
    assert state.staged is True
    assert state.sdk_registered is False
    assert state.actually_invoked is True
    assert state.completed is True
    assert state.artifact_ready is True
    assert state.optional_not_invoked_count == 1


def test_custom_adapter_cannot_self_attest_controlled_skill_execution():
    binding = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
    }
    declaration = RequiredCapabilityDeclaration.from_authorized_subject(
        capability_kind="skill",
        canonical_identity="required-skill",
    )
    evidence = [
        asdict(
            RequiredCapabilityEvidence.from_controlled_runner(
                declaration=declaration,
                binding=binding,
                tool_call_id="controlled-call",
                lifecycle_phase=phase,
            )
        )
        for phase in ("invocation_requested", "completed")
    ]
    executor_payload = {
        "used_skills_source": "platform_controlled_runner",
        "staged_skills": ["required-skill"],
        "used_skills": ["required-skill"],
        "capability_evidence": evidence,
        "_trusted_controlled_skill_ids": ["required-skill"],
    }
    payload = SimpleNamespace(
        **binding,
        skill_manifests=[{"skill_id": "required-skill"}],
    )
    result = SimpleNamespace(result={}, executor_payload=executor_payload)

    sealed = bind_validated_controlled_skill_evidence(
        payload,
        result,
        binding["attempt_id"],
        object(),
    )

    assert "_trusted_controlled_skill_ids" not in sealed
    assert exact_invoked_skills(sealed) == set()


def test_exact_hook_evidence_is_limited_to_the_fixed_staged_set():
    payload = {
        "used_skills_source": "executor_hook",
        "staged_skills": ["required-skill", "optional-skill"],
        "used_skills": ["required-skill", "unstaged-hostile-skill"],
        "sdk_used": True,
    }

    assert exact_invoked_skills(payload) == {"required-skill"}
    state = project_agent_capability_state(
        required_skill_id="required-skill",
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=1,
    )
    assert state.actually_invoked is True
    assert state.completed is True
    assert state.artifact_ready is True
    assert state.optional_not_invoked_count == 1


def test_public_capability_projection_contains_only_safe_semantic_state():
    state = project_agent_capability_state(
        required_skill_id="required-skill",
        executor_payload={
            "used_skills_source": "executor_hook",
            "staged_skills": ["required-skill"],
            "used_skills": ["required-skill"],
            "sdk_used": True,
            "executor_hook": {"private_path": "C:/private/skill"},
            "command": "private-command",
        },
        run_succeeded=True,
        durable_artifact_count=0,
    )

    projection = state.public_projection()
    assert projection == {
        "selected": True,
        "staged": True,
        "sdk_registered": True,
        "actually_invoked": True,
        "completed": True,
        "artifact_ready": False,
        "optional_not_invoked_count": 0,
    }
    serialized = str(projection)
    for forbidden in (
        "required-skill",
        "used_skills_source",
        "executor_hook",
        "private_path",
        "private-command",
    ):
        assert forbidden not in serialized


def test_artifact_ready_requires_a_durable_artifact_record_count():
    payload = {
        "used_skills_source": "executor_hook",
        "staged_skills": ["required-skill"],
        "used_skills": ["required-skill"],
        "sdk_used": True,
        "message": "Created report.docx at C:/tmp/report.docx",
    }

    without_record = project_agent_capability_state(
        required_skill_id="required-skill",
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=0,
    )
    with_record = project_agent_capability_state(
        required_skill_id="required-skill",
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=1,
    )

    assert without_record.artifact_ready is False
    assert with_record.artifact_ready is True
