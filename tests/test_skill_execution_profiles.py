from types import SimpleNamespace

import pytest

from app import worker
from app.executors.claude_agent_sdk_runner import _with_execution_profile_skill_tools
from app.skills.execution_profiles import (
    NATIVE_COMMAND_ISOLATION,
    OPEN_SANDBOX_GOVERNED_COMMAND_ISOLATION,
    OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE,
    SDK_NATIVE,
    SDK_RESTRICTED,
    SkillExecutionProfileError,
    canonical_skill_execution_profile,
    resolve_skill_execution_profile,
    sdk_skill_tool_admission_for_execution_profile,
)
from app.skills.pinning import build_skill_version_manifest_pin


_SDK_NATIVE_TOOLS = ["Read", "Glob", "LS", "Bash", "Write", "Edit"]
_OPEN_SANDBOX_SAFE_TOOLS = ["Read", "Glob", "LS", "Write", "Edit"]
def _builtin_skill_version(skill_id: str, *, status: str = "released") -> dict[str, object]:
    version = f"hash-{skill_id}"
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "description": "Explicit builtin Skill",
        "source": {
            "kind": "builtin",
            "asset_dir": skill_id,
            "version": version,
            "files": [
                {
                    "relative_path": "SKILL.md",
                    "content_base64": "c2tpbGw=",
                    "size_bytes": 5,
                }
            ],
        },
        "dependency_ids": [],
        "status": status,
    }


def _worker_subjects(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    skill_id = str(manifest["skill_id"])
    subjects = worker._builtin_capability_subjects(
        payload=SimpleNamespace(skill_manifests=[manifest], input={}),
        run_identity={"skill_id": skill_id},
        skill={"skill_id": skill_id, "skill_status": "active"},
        skill_decision=SimpleNamespace(usable=True),
    )
    return {str(subject["identity"]): subject for subject in subjects}


def test_released_builtin_pin_uses_generic_sdk_native_profile():
    manifest = build_skill_version_manifest_pin(_builtin_skill_version("rollout-script-runner"))

    profile = canonical_skill_execution_profile(manifest)
    subjects = _worker_subjects(manifest)

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == _SDK_NATIVE_TOOLS
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION
    assert set(subjects) == {*_SDK_NATIVE_TOOLS, "Skill"}
    assert subjects["Bash"]["execution_strategy"] == SDK_NATIVE


def test_legacy_pin_without_execution_profile_is_rejected():
    with pytest.raises(
        SkillExecutionProfileError,
        match="run_skill_snapshot_execution_profile_mismatch",
    ):
        canonical_skill_execution_profile(
            {
                "skill_id": "document-helper",
                "source": {"kind": "builtin", "asset_dir": "document-helper"},
            }
        )


@pytest.mark.parametrize("lifecycle_status", ["released", "reviewed", "active"])
def test_trusted_builtin_lifecycle_uses_same_sdk_native_profile(lifecycle_status: str):
    profile = resolve_skill_execution_profile(
        skill_id="any-reviewed-skill",
        source_kind="builtin",
        lifecycle_status=lifecycle_status,
    )

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == _SDK_NATIVE_TOOLS
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION


@pytest.mark.parametrize("skill_id", ["qa-file-reviewer", "general-chat"])
def test_concrete_skill_id_does_not_select_a_special_execution_strategy(skill_id: str):
    profile = resolve_skill_execution_profile(
        skill_id=skill_id,
        source_kind="builtin",
        lifecycle_status="released",
    )

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == _SDK_NATIVE_TOOLS


def test_reviewed_uploaded_skill_uses_same_sdk_native_profile():
    profile = resolve_skill_execution_profile(
        skill_id="reviewed-upload",
        source_kind="uploaded",
        lifecycle_status="reviewed",
    )

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == _SDK_NATIVE_TOOLS
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION


def test_governed_opensandbox_excludes_unbrokered_bash_for_a_bound_authorized_skill():
    admission = sdk_skill_tool_admission_for_execution_profile(
        execution_profile=OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE,
        bound_skill_id="reviewed-upload",
        staged_skill_ids=["reviewed-upload"],
        authorized_skill_ids={"reviewed-upload"},
    )

    assert admission is not None
    assert admission.tool_names == tuple(_OPEN_SANDBOX_SAFE_TOOLS)
    assert admission.command_isolation == OPEN_SANDBOX_GOVERNED_COMMAND_ISOLATION
    assert (
        sdk_skill_tool_admission_for_execution_profile(
            execution_profile="opensandbox_trusted_internal",
            bound_skill_id="reviewed-upload",
            staged_skill_ids=["reviewed-upload"],
            authorized_skill_ids={"reviewed-upload"},
        )
        is None
    )


def test_governed_opensandbox_profile_removes_preexisting_unbrokered_bash_subject():
    admission = sdk_skill_tool_admission_for_execution_profile(
        execution_profile=OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE,
        bound_skill_id="reviewed-upload",
        staged_skill_ids=["reviewed-upload"],
        authorized_skill_ids={"reviewed-upload"},
    )
    assert admission is not None
    skill_subject = {
        "identity": "Skill",
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
    }
    subjects = {
        "Skill": skill_subject,
        **{
            tool_name: {**skill_subject, "identity": tool_name}
            for tool_name in _SDK_NATIVE_TOOLS
        },
    }

    resolved = _with_execution_profile_skill_tools(subjects, admission=admission)

    assert set(resolved) == {"Skill", *_OPEN_SANDBOX_SAFE_TOOLS}
    assert "Bash" not in resolved


def test_governed_opensandbox_profile_bounds_general_chat_without_a_skill_subject():
    admission = sdk_skill_tool_admission_for_execution_profile(
        execution_profile=OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE,
        bound_skill_id=None,
        staged_skill_ids=[],
        authorized_skill_ids=set(),
    )
    assert admission is not None
    subjects = {
        tool_name: {"identity": tool_name}
        for tool_name in _SDK_NATIVE_TOOLS
    }

    resolved = _with_execution_profile_skill_tools(subjects, admission=admission)

    assert set(resolved) == set(_OPEN_SANDBOX_SAFE_TOOLS)
    assert "Bash" not in resolved


@pytest.mark.parametrize("bound_skill_id", ["", 7, [], {}])
def test_governed_opensandbox_profile_rejects_invalid_bound_skill_identity(
    bound_skill_id,
):
    assert (
        sdk_skill_tool_admission_for_execution_profile(
            execution_profile=OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE,
            bound_skill_id=bound_skill_id,
            staged_skill_ids=[],
            authorized_skill_ids=set(),
        )
        is None
    )


@pytest.mark.parametrize(
    ("skill_id", "source_kind", "lifecycle_status"),
    [
        ("qa-file-reviewer", "builtin", "draft"),
        ("qa-file-reviewer", "builtin", "disabled"),
        ("qa-file-reviewer", "builtin", "deprecated"),
        ("unknown-source", "external", "released"),
    ],
)
def test_unknown_or_nonrunnable_skill_profile_is_restricted(
    skill_id: str,
    source_kind: str,
    lifecycle_status: str,
):
    profile = resolve_skill_execution_profile(
        skill_id=skill_id,
        source_kind=source_kind,
        lifecycle_status=lifecycle_status,
    )

    assert profile["strategy"] == SDK_RESTRICTED
    assert profile["builtin_tool_identities"] == []
    assert profile["command_isolation"] == "none"
