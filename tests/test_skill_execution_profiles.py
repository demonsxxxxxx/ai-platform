from types import SimpleNamespace

import pytest

from app.skills.execution_profiles import (
    NATIVE_COMMAND_ISOLATION,
    SANDBOX_FULL_LOCAL,
    SDK_NATIVE,
    SDK_RESTRICTED,
    canonical_skill_execution_profile,
    effective_skill_execution_profile,
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


def test_explicit_builtin_v1_pin_keeps_snapshot_profile_but_runtime_uses_full_local():
    manifest = build_skill_version_manifest_pin(_builtin_skill_version("rollout-script-runner"))

    profile = canonical_skill_execution_profile(manifest)
    runtime_profile = effective_skill_execution_profile(manifest)
    subjects = _worker_subjects(manifest)

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == ["Bash"]
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION
    assert runtime_profile["strategy"] == SANDBOX_FULL_LOCAL
    assert set(subjects) == {"Skill"}
    assert subjects["Skill"]["execution_strategy"] == SANDBOX_FULL_LOCAL


def test_legacy_no_profile_builtin_dependency_keeps_pre_rollout_authority():
    legacy_dependency = {
        "skill_id": "document-helper",
        "source": {"kind": "builtin", "asset_dir": "document-helper"},
    }
    newly_built_pin = build_skill_version_manifest_pin(_builtin_skill_version("document-helper"))

    legacy_profile = canonical_skill_execution_profile(legacy_dependency)
    new_profile = canonical_skill_execution_profile(newly_built_pin)
    legacy_runtime_profile = effective_skill_execution_profile(legacy_dependency)
    new_runtime_profile = effective_skill_execution_profile(newly_built_pin)

    assert legacy_profile["strategy"] == SDK_RESTRICTED
    assert legacy_profile["builtin_tool_identities"] == []
    assert legacy_profile["command_isolation"] == "none"
    assert legacy_runtime_profile["strategy"] == SDK_RESTRICTED
    assert new_profile["strategy"] == SDK_NATIVE
    assert new_profile["builtin_tool_identities"] == ["Bash"]
    assert new_profile["command_isolation"] == NATIVE_COMMAND_ISOLATION
    assert new_runtime_profile["strategy"] == SANDBOX_FULL_LOCAL


@pytest.mark.parametrize("lifecycle_status", ["released", "reviewed", "active"])
def test_trusted_explicit_builtin_lifecycle_keeps_v1_native_snapshot(lifecycle_status: str):
    profile = resolve_skill_execution_profile(
        skill_id="rollout-script-runner",
        source_kind="builtin",
        lifecycle_status=lifecycle_status,
    )

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == ["Bash"]
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION


def test_retired_qa_builtin_uses_only_the_generic_sdk_profile():
    manifest = build_skill_version_manifest_pin(_builtin_skill_version("qa-file-reviewer"))

    profile = canonical_skill_execution_profile(manifest)
    runtime_profile = effective_skill_execution_profile(manifest)
    subjects = _worker_subjects(manifest)

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == ["Bash"]
    assert profile["command_isolation"] == NATIVE_COMMAND_ISOLATION
    assert runtime_profile["strategy"] == SANDBOX_FULL_LOCAL
    assert set(subjects) == {"Skill"}
    assert subjects["Skill"]["execution_strategy"] == SANDBOX_FULL_LOCAL


def test_historical_controlled_v1_profile_is_decoded_but_runs_through_sandbox():
    manifest = build_skill_version_manifest_pin(_builtin_skill_version("qa-file-reviewer"))
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

    decoded = canonical_skill_execution_profile(manifest)
    runtime_profile = effective_skill_execution_profile(manifest)
    subjects = _worker_subjects(manifest)

    assert decoded == historical_profile
    assert runtime_profile["strategy"] == SANDBOX_FULL_LOCAL
    assert runtime_profile["command_isolation"] == "real-sandbox-boundary-v1"
    assert set(subjects) == {"Skill"}
    assert subjects["Skill"]["execution_strategy"] == SANDBOX_FULL_LOCAL


def test_reviewed_uploaded_skill_keeps_v1_native_snapshot():
    profile = resolve_skill_execution_profile(
        skill_id="reviewed-upload",
        source_kind="uploaded",
        lifecycle_status="reviewed",
    )

    assert profile["strategy"] == SDK_NATIVE
    assert profile["builtin_tool_identities"] == ["Read", "Glob", "LS", "Bash", "Write", "Edit", "Grep"]
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
