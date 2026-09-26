from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.skills.application.run_admission import (
    SkillRunAdmissionPorts,
    SkillRunAdmissionService,
    SkillRunVersionMismatch,
)
from app.skills.lifecycle import is_user_runnable_status
from app.skills.pinning import (
    SkillVersionMaterializationError,
    attach_skill_snapshot_governance,
    build_skill_version_policy_manifest_pins,
    governed_locked_skill_version,
)
from app.skills.release_policy import (
    release_decision_payload_for_locked_version,
    resolve_rollout_skill_decision,
)


@pytest.mark.asyncio
async def test_admission_selects_rollout_version_and_locks_matching_manifest():
    requested_versions = []
    skill_id = "qa-file-reviewer"
    files = [
        {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}
    ]

    async def get_skill(_conn, *, skill_id):
        return {"skill_id": skill_id, "version": "hash-current"}

    async def get_effective_skill_version(_conn, *, skill_id, version):
        requested_versions.append((skill_id, version))
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Review Word documents.",
            "source": {"kind": "builtin", "asset_dir": skill_id, "files": files},
            "dependency_ids": [],
            "status": "active",
        }

    service = SkillRunAdmissionService(
        SkillRunAdmissionPorts(
            catalog=SimpleNamespace(get_skill=get_skill),
            versions=SimpleNamespace(
                get_effective_skill_version_for_policy=get_effective_skill_version
            ),
            resolve_release_decision=resolve_rollout_skill_decision,
            release_decision_payload=release_decision_payload_for_locked_version,
            is_user_runnable_status=is_user_runnable_status,
            build_manifest_pins=build_skill_version_policy_manifest_pins,
            lock_skill_version=governed_locked_skill_version,
            attach_snapshot_governance=attach_skill_snapshot_governance,
            materialization_error=SkillVersionMaterializationError,
        )
    )
    admission = await service.admit(
        object(),
        skill={
            "skill_version": "hash-current",
            "release_policy_version": "hash-current",
            "release_policy_previous_version": "hash-previous",
            "release_policy_rollout_percent": 0,
        },
        skill_id=skill_id,
        input_payload={},
        tenant_id="tenant-a",
        rollout_key="user-a",
    )

    assert requested_versions == [(skill_id, "hash-previous")]
    assert admission.skill_version == "hash-previous"
    assert admission.release_decision["selected_track"] == "previous"
    assert admission.release_decision["selected_version"] == "hash-previous"
    assert len(admission.skill_manifests) == 1
    assert admission.skill_manifests[0]["version"] == "hash-previous"
    assert admission.skill_manifests[0]["content_hash"] == "hash-previous"
    assert admission.skill_manifests[0]["files"] == files
    assert "mcp_tool_ids" not in admission.skill_manifests[0]

    def fail_governance(*_args, **_kwargs):
        raise AssertionError("stale Skill version must fail before snapshot governance")

    stale_service = SkillRunAdmissionService(
        replace(
            service._ports,
            attach_snapshot_governance=fail_governance,
        )
    )
    with pytest.raises(SkillRunVersionMismatch):
        await stale_service.admit(
            object(),
            skill={
                "skill_version": "hash-current",
                "release_policy_version": "hash-current",
            },
            skill_id=skill_id,
            input_payload={},
            tenant_id="tenant-a",
            rollout_key="user-a",
            expected_version="hash-previous",
        )
