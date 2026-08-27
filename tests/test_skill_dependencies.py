import json

import pytest

from app.skills.dependencies import (
    SkillDependencyPolicyError,
    skill_dependency_policy,
    validate_skill_dependency_ids,
)


def test_skill_dependency_policy_does_not_infer_dependencies_from_skill_id():
    available = {"qa-file-reviewer", "minimax-docx", "baoyu-translate"}

    assert skill_dependency_policy("qa-file-reviewer", available) == {
        "skill_id": "qa-file-reviewer",
        "public": True,
        "internal_dependency": False,
        "dependency_ids": [],
        "dependency_details": [],
    }


def test_skill_dependency_policy_marks_internal_dependency_skill_not_public():
    available = {"minimax-docx"}

    assert skill_dependency_policy("minimax-docx", available) == {
        "skill_id": "minimax-docx",
        "public": False,
        "internal_dependency": True,
        "dependency_ids": [],
        "dependency_details": [],
    }


def test_declared_internal_dependency_is_validated_and_projected():
    available = {"qa-file-reviewer", "minimax-docx"}

    assert validate_skill_dependency_ids("qa-file-reviewer", ["minimax-docx"], available) == ["minimax-docx"]
    assert skill_dependency_policy("qa-file-reviewer", available, ["minimax-docx"])["dependency_details"] == [
        {
            "skill_id": "minimax-docx",
            "status": "allowed",
            "reason": "declared_internal_dependency",
            "public": False,
            "internal_dependency": True,
            "available": True,
        }
    ]


def test_declared_dependency_must_be_available():
    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_missing"):
        validate_skill_dependency_ids("qa-file-reviewer", ["minimax-docx"], {"qa-file-reviewer"})


def test_declared_dependency_must_be_internal():
    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_not_internal"):
        validate_skill_dependency_ids(
            "qa-file-reviewer",
            ["baoyu-translate"],
            {"qa-file-reviewer", "baoyu-translate"},
        )

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_not_allowed"):
        validate_skill_dependency_ids(
            "qa-file-reviewer",
            ["custom-helper"],
            {"qa-file-reviewer", "custom-helper"},
        )


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
