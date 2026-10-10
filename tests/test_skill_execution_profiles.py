from types import SimpleNamespace

import pytest

from app.required_tool_contract import builtin_capability_subjects
from app.skills.execution_profiles import (
    NATIVE_COMMAND_ISOLATION,
    SANDBOX_FULL_LOCAL,
    SDK_NATIVE,
    SDK_RESTRICTED,
    SkillExecutionProfileError,
    canonical_skill_execution_profile,
    effective_skill_execution_profile,
    resolve_skill_execution_profile,
)
from app.skills.pinning import (
    build_skill_version_manifest_pin,
    build_uploaded_skill_manifest_pin,
)


def _skill_version(
    skill_id: str,
    *,
    status: str = "released",
    source_kind: str = "builtin",
) -> dict[str, object]:
    version = f"hash-{skill_id}"
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "description": "Explicit builtin Skill",
        "source": {
            "kind": source_kind,
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
    subjects = builtin_capability_subjects(
        payload=SimpleNamespace(skill_manifests=[manifest], input={}),
        run_identity={"skill_id": skill_id},
        skill={"skill_id": skill_id, "skill_status": "active"},
        skill_decision=SimpleNamespace(usable=True),
        canonical_manifest=effective_skill_execution_profile,
    )
    return {str(subject["identity"]): subject for subject in subjects}


def test_reviewed_uploaded_v1_pin_keeps_snapshot_profile_but_runtime_uses_full_local():
    manifest = build_uploaded_skill_manifest_pin(
        _skill_version("reviewed-upload", status="reviewed", source_kind="uploaded")
    )

    profile = canonical_skill_execution_profile(manifest)
    runtime_profile = effective_skill_execution_profile(manifest)
    subjects = _worker_subjects(manifest)

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == [
        "Read", "Glob", "LS", "Bash", "Write", "Edit", "Grep"
    ]
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION
    assert runtime_profile["strategy"] == SANDBOX_FULL_LOCAL
    assert set(subjects) == {"Skill"}
    assert subjects["Skill"]["execution_strategy"] == SANDBOX_FULL_LOCAL


def test_builtin_pins_are_restricted_and_legacy_missing_profile_is_rejected():
    missing_profile_dependency = {
        "skill_id": "retired-repository-skill",
        "source": {"kind": "builtin", "asset_dir": "retired-repository-skill"},
    }
    historic_pin = build_skill_version_manifest_pin(_skill_version("retired-repository-skill"))

    historic_profile = canonical_skill_execution_profile(historic_pin)
    historic_runtime_profile = effective_skill_execution_profile(historic_pin)

    assert historic_profile["strategy"] == SDK_RESTRICTED
    assert historic_profile["builtin_tool_identities"] == []
    assert historic_profile["command_isolation"] == "none"
    assert historic_runtime_profile["strategy"] == SDK_RESTRICTED
    for resolve in (canonical_skill_execution_profile, effective_skill_execution_profile):
        with pytest.raises(SkillExecutionProfileError, match="run_skill_snapshot_execution_profile_mismatch"):
            resolve(missing_profile_dependency)


@pytest.mark.parametrize("lifecycle_status", ["released", "reviewed", "active"])
def test_repository_builtin_lifecycle_never_grants_runtime_tools(lifecycle_status: str):
    profile = resolve_skill_execution_profile(
        skill_id="retired-repository-skill",
        source_kind="builtin",
        lifecycle_status=lifecycle_status,
    )

    assert profile["strategy"] == SDK_RESTRICTED
    assert profile["builtin_tool_identities"] == []
    assert profile["command_isolation"] == "none"


def test_historical_controlled_profile_cannot_be_reactivated_as_sdk():
    manifest = build_skill_version_manifest_pin(
        _skill_version("retired-repository-skill")
    )
    historical_profile = {
        "schema_version": "ai-platform.skill-execution-profile.v1",
        "strategy": "platform_controlled",
        "trust_basis": "repository_builtin",
        "builtin_tool_identities": ["Bash", "Write"],
        "workspace_contract": "ai-platform.skill-workspace.v1",
        "command_isolation": "minimal-environment-v1",
    }
    manifest["execution_profile"] = historical_profile
    manifest["builtin_tool_identities"] = ["Bash", "Write"]

    for resolve in (canonical_skill_execution_profile, effective_skill_execution_profile):
        with pytest.raises(SkillExecutionProfileError, match="run_skill_snapshot_execution_profile_mismatch"):
            resolve(manifest)


@pytest.mark.parametrize(
    ("skill_id", "source_kind", "lifecycle_status"),
    [
        ("retired-repository-skill", "builtin", "released"),
        ("unreviewed-upload", "uploaded", "draft"),
        ("unreviewed-upload", "uploaded", "disabled"),
        ("unreviewed-upload", "uploaded", "deprecated"),
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
