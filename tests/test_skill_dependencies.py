import json

import pytest

from app.skills.dependencies import (
    SkillDependencyPolicyError,
    skill_dependency_policy,
    validate_skill_dependency_ids,
)


def test_skill_dependency_policy_does_not_classify_skill_ids():
    available = {"qa-file-reviewer", "minimax-docx"}

    assert skill_dependency_policy("qa-file-reviewer", available) == {
        "skill_id": "qa-file-reviewer",
        "dependency_ids": [],
        "dependency_details": [],
    }


def test_skill_dependency_policy_does_not_classify_former_internal_dependency_names():
    available = {"minimax-docx"}

    assert skill_dependency_policy("minimax-docx", available) == {
        "skill_id": "minimax-docx",
        "dependency_ids": [],
        "dependency_details": [],
    }


def test_declared_dependency_is_validated_and_projected_without_name_policy():
    available = {"qa-file-reviewer", "minimax-docx"}

    assert validate_skill_dependency_ids("qa-file-reviewer", ["minimax-docx"], available) == ["minimax-docx"]
    assert skill_dependency_policy("qa-file-reviewer", available, ["minimax-docx"])["dependency_details"] == [
        {
            "skill_id": "minimax-docx",
            "status": "allowed",
            "reason": "declared_dependency",
            "available": True,
        }
    ]


def test_declared_dependency_must_be_available():
    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_missing"):
        validate_skill_dependency_ids("qa-file-reviewer", ["minimax-docx"], {"qa-file-reviewer"})


def test_any_available_safe_skill_name_can_be_a_dependency():
    available = {"qa-file-reviewer", "ragflow-knowledge-search", "custom-helper"}
    assert validate_skill_dependency_ids(
        "qa-file-reviewer",
        ["ragflow-knowledge-search", "custom-helper"],
        available,
    ) == ["ragflow-knowledge-search", "custom-helper"]


def test_declared_dependency_rejects_cycles_and_duplicates():
    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_cycle"):
        validate_skill_dependency_ids("qa-file-reviewer", ["qa-file-reviewer"], {"qa-file-reviewer"})

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_duplicate"):
        validate_skill_dependency_ids(
            "qa-file-reviewer",
            ["minimax-docx", "minimax-docx"],
            {"qa-file-reviewer", "minimax-docx"},
        )


def test_declared_dependency_rejects_path_like_value_without_projecting_raw_value():
    malicious_dependency_id = "../runtime/.claude/skills/token=secret"

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_invalid_id") as exc_info:
        validate_skill_dependency_ids(
            "qa-file-reviewer",
            [malicious_dependency_id],
            {"qa-file-reviewer", malicious_dependency_id},
        )

    assert malicious_dependency_id not in str(exc_info.value)
    policy = skill_dependency_policy(
        "qa-file-reviewer",
        {"qa-file-reviewer", malicious_dependency_id},
        [malicious_dependency_id],
    )
    assert policy["dependency_ids"] == ["[invalid-skill-id]"]
    assert malicious_dependency_id not in json.dumps(policy)
