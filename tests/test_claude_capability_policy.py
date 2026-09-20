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


def test_sandbox_local_tools_delegate_parameter_validation_to_the_sdk():
    subject = {
        "identity": "Bash",
        "execution_strategy": "sandbox_full_local",
        "parameter_validation": "sdk",
    }

    assert _parameters_match_subject(
        subject,
        "Bash",
        {
            "command": "pwd",
            "description": "inspect the workspace",
        },
    )
    assert not _parameters_match_subject(
        subject,
        "Bash",
        {"command": "pwd", "run_in_background": True},
    )
    for invalid_background in (1, "true", None):
        assert not _parameters_match_subject(
            subject,
            "Bash",
            {"command": "pwd", "run_in_background": invalid_background},
        )


def test_sandbox_local_parameter_delegation_does_not_apply_to_other_identities():
    subject = {
        "identity": "Agent",
        "execution_strategy": "sandbox_full_local",
        "parameter_validation": "sdk",
        "allowed_parameter_keys": ["agent"],
        "required_parameter_keys": ["agent"],
    }

    assert not _parameters_match_subject(
        subject,
        "Agent",
        {"agent": "reviewer", "opaque": "not allowed"},
    )


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        (
            "Read",
            {"file_path": "input.pdf", "offset": 1, "limit": 20, "pages": "1-3"},
        ),
        (
            "Grep",
            {
                "pattern": "TODO",
                "path": "inputs",
                "-A": 2,
                "-B": 1,
                "-C": 3,
                "-o": True,
                "type": "py",
            },
        ),
        (
            "Bash",
            {"command": "python --version", "timeout": 1000, "description": "version"},
        ),
        ("Glob", {"pattern": "inputs/**/*.py", "path": "."}),
    ],
)
def test_builtin_parameter_fallback_uses_the_canonical_sdk_contract(
    tool_name,
    tool_input,
):
    assert _parameters_match_subject({"identity": tool_name}, tool_name, tool_input)


@pytest.mark.parametrize(
    "tool_name",
    ["Read", "Glob", "Grep", "Bash"],
)
def test_builtin_parameter_fallback_still_rejects_unknown_keys(tool_name):
    required_input = {
        "Read": {"file_path": "input.txt"},
        "Glob": {"pattern": "*.py"},
        "Grep": {"pattern": "TODO"},
        "Bash": {"command": "pwd"},
    }[tool_name]

    assert not _parameters_match_subject(
        {"identity": tool_name},
        tool_name,
        {**required_input, "opaque": "not allowed"},
    )


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("Glob", {}),
    ],
)
def test_foreground_sdk_tools_keep_required_parameters(tool_name, tool_input):
    assert not _parameters_match_subject({"identity": tool_name}, tool_name, tool_input)


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("Bash", {"command": "pwd", "run_in_background": True}),
        ("Bash", {"command": "pwd", "dangerouslyDisableSandbox": True}),
    ],
)
def test_bash_background_and_sandbox_bypass_fields_remain_closed(
    tool_name,
    tool_input,
):
    assert not _parameters_match_subject({"identity": tool_name}, tool_name, tool_input)
