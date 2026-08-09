import pytest

from app.agent_apps.capability_state import (
    exact_hook_invoked_skills,
    project_agent_capability_state,
)


@pytest.mark.parametrize(
    "source",
    [
        "selected",
        "staged",
        "inferred",
        "executor_native",
        "platform_controlled_runner",
        "",
    ],
)
def test_non_hook_skill_evidence_never_becomes_actually_invoked(source):
    payload = {
        "used_skills_source": source,
        "staged_skills": ["required-skill"],
        "used_skills": ["required-skill"],
        "sdk_used": True,
    }

    assert exact_hook_invoked_skills(payload) == set()
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


def test_exact_hook_evidence_is_limited_to_the_fixed_staged_set():
    payload = {
        "used_skills_source": "executor_hook",
        "staged_skills": ["required-skill", "optional-skill"],
        "used_skills": ["required-skill", "unstaged-hostile-skill"],
        "sdk_used": True,
    }

    assert exact_hook_invoked_skills(payload) == {"required-skill"}
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
