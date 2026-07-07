import json

import pytest

import app.skills.dependencies as dependency_policy
from app.skills.dependencies import (
    SkillDependencyPolicyError,
    skill_dependency_ids,
    skill_dependency_policy,
    with_skill_dependencies,
)


def test_skill_dependencies_expand_only_available_internal_dependencies():
    available = {"qa-file-reviewer", "minimax-docx", "baoyu-translate"}

    assert skill_dependency_ids("qa-file-reviewer", available) == ["minimax-docx"]
    assert with_skill_dependencies(["qa-file-reviewer"], available) == ["qa-file-reviewer", "minimax-docx"]


def test_skill_dependency_policy_projects_public_skill_internal_dependency():
    available = {"qa-file-reviewer", "minimax-docx", "baoyu-translate"}

    assert skill_dependency_policy("qa-file-reviewer", available) == {
        "skill_id": "qa-file-reviewer",
        "public": True,
        "internal_dependency": False,
        "dependency_ids": ["minimax-docx"],
        "dependency_details": [
            {
                "skill_id": "minimax-docx",
                "status": "allowed",
                "reason": "declared_internal_dependency",
                "public": False,
                "internal_dependency": True,
                "available": True,
            }
        ],
    }


def test_skill_dependency_policy_projects_audit_finding_rca_as_public_skill_without_dependencies():
    available = {"audit-finding-rca"}

    assert skill_dependency_policy("audit-finding-rca", available) == {
        "skill_id": "audit-finding-rca",
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


def test_skill_dependencies_do_not_invent_missing_dependencies():
    available = {"qa-file-reviewer"}

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_missing"):
        skill_dependency_ids("qa-file-reviewer", available)

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_missing"):
        with_skill_dependencies(["qa-file-reviewer"], available)


def test_skill_dependencies_reject_public_skill_as_dependency(monkeypatch):
    monkeypatch.setattr(dependency_policy, "SKILL_DEPENDENCIES", {"qa-file-reviewer": ["baoyu-translate"]})

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_not_internal"):
        skill_dependency_ids("qa-file-reviewer", {"qa-file-reviewer", "baoyu-translate"})

    assert skill_dependency_policy("qa-file-reviewer", {"qa-file-reviewer", "baoyu-translate"})[
        "dependency_details"
    ] == [
        {
            "skill_id": "baoyu-translate",
            "status": "blocked",
            "reason": "skill_dependency_not_internal",
            "public": True,
            "internal_dependency": False,
            "available": True,
        }
    ]


def test_skill_dependencies_reject_unknown_internal_dependency(monkeypatch):
    monkeypatch.setattr(dependency_policy, "SKILL_DEPENDENCIES", {"qa-file-reviewer": ["custom-helper"]})

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_not_allowed"):
        skill_dependency_ids("qa-file-reviewer", {"qa-file-reviewer", "custom-helper"})

    assert skill_dependency_policy("qa-file-reviewer", {"qa-file-reviewer", "custom-helper"})[
        "dependency_details"
    ] == [
        {
            "skill_id": "custom-helper",
            "status": "blocked",
            "reason": "skill_dependency_not_allowed",
            "public": False,
            "internal_dependency": False,
            "available": True,
        }
    ]


def test_skill_dependencies_reject_self_dependency(monkeypatch):
    monkeypatch.setattr(dependency_policy, "SKILL_DEPENDENCIES", {"qa-file-reviewer": ["qa-file-reviewer"]})

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_cycle"):
        with_skill_dependencies(["qa-file-reviewer"], {"qa-file-reviewer"})

    assert skill_dependency_policy("qa-file-reviewer", {"qa-file-reviewer"})["dependency_details"] == [
        {
            "skill_id": "qa-file-reviewer",
            "status": "blocked",
            "reason": "skill_dependency_cycle",
            "public": True,
            "internal_dependency": False,
            "available": True,
        }
    ]


def test_skill_dependency_policy_reports_missing_dependency():
    assert skill_dependency_policy("qa-file-reviewer", {"qa-file-reviewer"})["dependency_details"] == [
        {
            "skill_id": "minimax-docx",
            "status": "blocked",
            "reason": "skill_dependency_missing",
            "public": False,
            "internal_dependency": True,
            "available": False,
        }
    ]


def test_skill_dependencies_reject_path_like_dependency_without_projecting_raw_value(monkeypatch):
    malicious_dependency_id = "../runtime/.claude/skills/token=secret"
    monkeypatch.setattr(dependency_policy, "SKILL_DEPENDENCIES", {"qa-file-reviewer": [malicious_dependency_id]})

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_invalid_id") as exc_info:
        skill_dependency_ids("qa-file-reviewer", {"qa-file-reviewer", malicious_dependency_id})

    assert malicious_dependency_id not in str(exc_info.value)
    policy = skill_dependency_policy("qa-file-reviewer", {"qa-file-reviewer", malicious_dependency_id})

    assert policy["dependency_ids"] == ["[invalid-skill-id]"]
    assert policy["dependency_details"] == [
        {
            "skill_id": "[invalid-skill-id]",
            "status": "blocked",
            "reason": "skill_dependency_invalid_id",
            "public": False,
            "internal_dependency": False,
            "available": False,
        }
    ]
    assert malicious_dependency_id not in json.dumps(policy)
