from types import SimpleNamespace

import pytest

from app.skills.execution_profiles import (
    CONTROLLED_COMMAND_ISOLATION,
    NATIVE_COMMAND_ISOLATION,
    PLATFORM_CONTROLLED,
    SDK_NATIVE,
    SDK_RESTRICTED,
    canonical_skill_execution_profile,
    resolve_skill_execution_profile,
)
from app.skills.pinning import build_skill_version_manifest_pin
from app import worker


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


def test_explicit_builtin_pin_grants_only_native_bash_to_worker_subject():
    manifest = build_skill_version_manifest_pin(_builtin_skill_version("rollout-script-runner"))

    profile = canonical_skill_execution_profile(manifest)
    subjects = _worker_subjects(manifest)

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == ["Bash"]
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION
    assert set(subjects) == {"Bash", "Skill"}
    assert subjects["Bash"]["execution_strategy"] == SDK_NATIVE
    assert subjects["Bash"]["command_isolation"] == NATIVE_COMMAND_ISOLATION
    assert not {"Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Agent"} & set(subjects)


@pytest.mark.parametrize("lifecycle_status", ["released", "reviewed", "active"])
def test_trusted_explicit_builtin_lifecycle_grants_native_bash(lifecycle_status: str):
    profile = resolve_skill_execution_profile(
        skill_id="rollout-script-runner",
        source_kind="builtin",
        lifecycle_status=lifecycle_status,
    )

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == ["Bash"]
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION


def test_platform_controlled_builtin_retains_controlled_strategy_and_existing_tools():
    profile = resolve_skill_execution_profile(
        skill_id="qa-file-reviewer",
        source_kind="builtin",
        lifecycle_status="released",
    )

    assert profile["strategy"] == PLATFORM_CONTROLLED
    assert profile["builtin_tool_identities"] == ["Bash", "Write"]
    assert profile["command_isolation"] == CONTROLLED_COMMAND_ISOLATION


def test_reviewed_uploaded_skill_retains_native_bash_isolation():
    profile = resolve_skill_execution_profile(
        skill_id="reviewed-upload",
        source_kind="uploaded",
        lifecycle_status="reviewed",
    )

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == ["Read", "Glob", "LS", "Bash", "Write", "Edit"]
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION


@pytest.mark.parametrize(
    ("skill_id", "source_kind", "lifecycle_status"),
    [
        ("general-chat", "builtin", "released"),
        ("qa-file-reviewer", "builtin", "draft"),
        ("qa-file-reviewer", "builtin", "disabled"),
        ("qa-file-reviewer", "builtin", "deprecated"),
        ("unknown-source", "external", "released"),
    ],
)
def test_implicit_unknown_or_nonrunnable_skill_profile_grants_no_bash(
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
