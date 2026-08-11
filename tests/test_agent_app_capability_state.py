import pytest

from app.agent_apps.capability_state import (
    exact_attempted_skills,
    exact_invoked_skills,
    project_agent_capability_state,
)


@pytest.mark.parametrize(
    "source",
    ["selected", "staged", "inferred", "executor_native", "platform_controlled_runner", ""],
)
def test_untrusted_skill_claim_never_becomes_an_actual_invocation(source):
    payload = {
        "used_skills_source": source,
        "staged_skills": ["bound-skill"],
        "used_skills": ["bound-skill"],
        "sdk_used": True,
    }

    assert exact_invoked_skills(payload) == set()
    state = project_agent_capability_state(
        bound_skill_ids={"bound-skill"},
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=0,
    )
    assert state.bound is True
    assert state.staged is True
    assert state.sdk_registered is True
    assert state.actually_invoked is False
    assert state.completed is False
    assert state.optional_not_invoked_count == 1


def test_exact_sdk_hook_claim_is_limited_to_the_staged_set():
    payload = {
        "used_skills_source": "executor_hook",
        "staged_skills": ["bound-skill", "optional-skill"],
        "used_skills": ["bound-skill", "unstaged-hostile-skill"],
        "sdk_used": True,
    }

    assert exact_invoked_skills(payload) == {"bound-skill"}
    state = project_agent_capability_state(
        bound_skill_ids={"bound-skill", "optional-skill"},
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=1,
    )
    assert state.actually_invoked is True
    assert state.completed is True
    assert state.artifact_ready is True
    assert state.optional_not_invoked_count == 0


def test_failed_sdk_skill_call_is_attempted_but_not_completed():
    payload = {
        "used_skills_source": "none",
        "staged_skills": ["bound-skill"],
        "used_skills": [],
        "sdk_used": True,
        "capability_evidence_validated": True,
        "capability_evidence": [
            {
                "capability_kind": "skill",
                "canonical_identity": "bound-skill",
                "tool_call_id": "call-1",
                "lifecycle_phase": "invocation_requested",
            },
            {
                "capability_kind": "skill",
                "canonical_identity": "bound-skill",
                "tool_call_id": "call-1",
                "lifecycle_phase": "failed",
            },
        ],
    }

    assert exact_attempted_skills(payload) == {"bound-skill"}
    state = project_agent_capability_state(
        bound_skill_ids={"bound-skill"},
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=0,
    )

    assert state.actually_invoked is True
    assert state.completed is False
    assert state.optional_not_invoked_count == 0
    assert state.invocation_attempt_count == 1
    assert state.invocation_completed_count == 0
    assert state.invocation_failed_count == 1
    assert state.partial_failure is False


def test_recovered_skill_invocation_preserves_partial_failure_counts():
    payload = {
        "used_skills_source": "executor_hook",
        "staged_skills": ["bound-skill"],
        "used_skills": ["bound-skill"],
        "sdk_used": True,
        "capability_evidence_validated": True,
        "capability_evidence": [
            {
                "capability_kind": "skill",
                "canonical_identity": "bound-skill",
                "tool_call_id": "call-1",
                "lifecycle_phase": "invocation_requested",
            },
            {
                "capability_kind": "skill",
                "canonical_identity": "bound-skill",
                "tool_call_id": "call-1",
                "lifecycle_phase": "failed",
            },
            {
                "capability_kind": "skill",
                "canonical_identity": "bound-skill",
                "tool_call_id": "call-2",
                "lifecycle_phase": "invocation_requested",
            },
            {
                "capability_kind": "skill",
                "canonical_identity": "bound-skill",
                "tool_call_id": "call-2",
                "lifecycle_phase": "completed",
            },
        ],
    }

    state = project_agent_capability_state(
        bound_skill_ids={"bound-skill"},
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=0,
    )

    assert state.actually_invoked is True
    assert state.completed is True
    assert state.invocation_attempt_count == 2
    assert state.invocation_completed_count == 1
    assert state.invocation_failed_count == 1
    assert state.partial_failure is True


def test_dependency_invocation_counts_for_the_expert_capability_group():
    payload = {
        "used_skills_source": "executor_hook",
        "staged_skills": ["primary-skill", "dependency-skill"],
        "used_skills": ["dependency-skill"],
        "sdk_used": True,
        "capability_evidence_validated": True,
        "capability_evidence": [
            {
                "capability_kind": "skill",
                "canonical_identity": "dependency-skill",
                "tool_call_id": "dependency-call-1",
                "lifecycle_phase": "invocation_requested",
            },
            {
                "capability_kind": "skill",
                "canonical_identity": "dependency-skill",
                "tool_call_id": "dependency-call-1",
                "lifecycle_phase": "completed",
            },
        ],
    }

    state = project_agent_capability_state(
        bound_skill_ids={"primary-skill", "dependency-skill"},
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=0,
    )

    assert state.actually_invoked is True
    assert state.completed is True
    assert state.optional_not_invoked_count == 0
    assert state.invocation_attempt_count == 1
    assert state.invocation_completed_count == 1


def test_public_capability_projection_contains_only_safe_semantic_state():
    state = project_agent_capability_state(
        bound_skill_ids={"bound-skill"},
        executor_payload={
            "used_skills_source": "executor_hook",
            "staged_skills": ["bound-skill"],
            "used_skills": ["bound-skill"],
            "sdk_used": True,
            "executor_hook": {"private_path": "C:/private/skill"},
            "command": "private-command",
        },
        run_succeeded=True,
        durable_artifact_count=0,
    )

    projection = state.public_projection()
    assert projection == {
        "bound": True,
        "staged": True,
        "sdk_registered": True,
        "actually_invoked": True,
        "completed": True,
        "artifact_ready": False,
        "optional_not_invoked_count": 0,
        "invocation_attempt_count": 0,
        "invocation_completed_count": 0,
        "invocation_failed_count": 0,
        "partial_failure": False,
    }
    serialized = str(projection)
    for forbidden in (
        "bound-skill",
        "used_skills_source",
        "executor_hook",
        "private_path",
        "private-command",
    ):
        assert forbidden not in serialized


def test_artifact_ready_depends_only_on_a_durable_artifact_record():
    payload = {
        "used_skills_source": "executor_hook",
        "staged_skills": ["bound-skill"],
        "used_skills": [],
        "sdk_used": True,
    }

    without_record = project_agent_capability_state(
        bound_skill_ids={"bound-skill"},
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=0,
    )
    with_record = project_agent_capability_state(
        bound_skill_ids={"bound-skill"},
        executor_payload=payload,
        run_succeeded=True,
        durable_artifact_count=1,
    )

    assert without_record.artifact_ready is False
    assert with_record.artifact_ready is True
    assert with_record.completed is False
