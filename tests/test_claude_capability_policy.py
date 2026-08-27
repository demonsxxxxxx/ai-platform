from __future__ import annotations

import math

import pytest

from app.executors.claude.capability_policy import (
    _extract_skill_names_from_tool_input,
    _parameters_match_subject,
)


def _skill_subject(*skill_names: str) -> dict[str, object]:
    return {
        "identity": "Skill",
        "allowed_parameter_keys": ["skill"],
        "required_parameter_keys": ["skill"],
        "allowed_skill_names": list(skill_names),
    }


def test_skill_accepts_bounded_opaque_fields_for_an_authorized_identity():
    subject = _skill_subject("skill-a")

    assert _parameters_match_subject(
        subject,
        "Skill",
        {
            "skill": "skill-a",
            "args": "current task context",
            "context": {"skill": "not-an-authority-field", "items": [1, True, None]},
        },
    )


def test_skill_rejects_identity_outside_the_authorized_set():
    assert not _parameters_match_subject(
        _skill_subject("skill-a"),
        "Skill",
        {"skill": "skill-b", "args": "current task context"},
    )


@pytest.mark.parametrize(
    "tool_input",
    [
        {},
        {"skill": ""},
        {"skill": " skill-a "},
        {"skill": 1},
        {"args": {"skill": "skill-a"}},
    ],
)
def test_skill_requires_an_exact_top_level_authorized_identity(tool_input):
    assert not _parameters_match_subject(
        _skill_subject("skill-a"),
        "Skill",
        tool_input,
    )


def test_opaque_fields_cannot_add_skill_invocation_evidence():
    tool_input = {
        "skill": "skill-a",
        "args": {
            "skill": "skill-b",
            "selectedSkillName": "skill-b",
        },
    }

    assert _extract_skill_names_from_tool_input(
        tool_input,
        {"skill-a", "skill-b"},
    ) == ["skill-a"]
    assert _extract_skill_names_from_tool_input(
        {"args": {"skill": "skill-a"}},
        {"skill-a"},
    ) == []


def test_skill_rejects_oversized_overdeep_and_non_json_payloads():
    subject = _skill_subject("skill-a")
    nested: object = "value"
    for _ in range(17):
        nested = {"nested": nested}
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    assert not _parameters_match_subject(
        subject,
        "Skill",
        {"skill": "skill-a", "args": "x" * (64 * 1024)},
    )
    assert not _parameters_match_subject(
        subject,
        "Skill",
        {"skill": "skill-a", "args": nested},
    )
    assert not _parameters_match_subject(
        subject,
        "Skill",
        {"skill": "skill-a", "args": object()},
    )
    assert not _parameters_match_subject(
        subject,
        "Skill",
        {"skill": "skill-a", "args": math.nan},
    )
    assert not _parameters_match_subject(
        subject,
        "Skill",
        {"skill": "skill-a", "args": "\ud800"},
    )
    assert not _parameters_match_subject(
        subject,
        "Skill",
        {"skill": "skill-a", "args": cyclic},
    )


def test_skill_keeps_subject_object_constraints():
    subject = {
        **_skill_subject("skill-a"),
        "object_constraints": {"tenant_scope": "tenant-a"},
    }

    assert _parameters_match_subject(
        subject,
        "Skill",
        {
            "skill": "skill-a",
            "tenant_scope": "tenant-a",
            "args": "current task context",
        },
    )
    assert not _parameters_match_subject(
        subject,
        "Skill",
        {
            "skill": "skill-a",
            "tenant_scope": "tenant-b",
            "args": "current task context",
        },
    )


def test_non_skill_tools_keep_strict_parameter_key_authorization():
    subject = {
        "identity": "Read",
        "allowed_parameter_keys": ["file_path"],
        "required_parameter_keys": ["file_path"],
    }

    assert not _parameters_match_subject(
        subject,
        "Read",
        {"file_path": "input.txt", "opaque": "not allowed"},
    )
