import app.platform.postgres.errors as _owner_platform_postgres_errors
import app.skills.infrastructure.postgres as _owner_skills_infrastructure_postgres
import json

import pytest

from app.skills.application import skill_markdown
from app.skills.infrastructure.skill_markdown_yaml import load_skill_markdown_metadata
from app.skills.pinning import (
    attach_skill_snapshot_governance,
    build_skill_manifest_ref,
    validate_skill_manifest_refs,
    build_skill_snapshot_governance,
    SkillVersionMaterializationError,
    build_skill_version_manifest_pin,
    build_skill_version_policy_manifest_pins,
    build_uploaded_skill_manifest_pin,
    governed_locked_skill_version,
)
from app.skills.registry import BuiltinSkillRegistry


@pytest.fixture(autouse=True)
def configured_skill_markdown_loader(monkeypatch):
    monkeypatch.setattr(skill_markdown, "_yaml_metadata_loader", load_skill_markdown_metadata)


def write_skill(root, name, description):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_snapshot_source_locks_canonical_uploaded_tool_identities():
    manifest = build_uploaded_skill_manifest_pin(
        {
            "skill_id": "example-skill",
            "version": "hash-uploaded",
            "content_hash": "hash-uploaded",
            "description": "Review Word documents.",
            "source": {
                "kind": "uploaded",
                "storage_key": "package.zip",
                "files": [
                    {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}
                ],
            },
            "dependency_ids": [],
            "status": "released",
        }
    )

    expected = _owner_skills_infrastructure_postgres.run_skill_snapshot_source_json(manifest)
    duplicated = {**manifest, "builtin_tool_identities": manifest["builtin_tool_identities"] * 2}

    assert _owner_skills_infrastructure_postgres.run_skill_snapshot_source_json(duplicated) == expected
    for forged_identity in ("Agent", "WebFetch"):
        with pytest.raises(_owner_platform_postgres_errors.RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
            _owner_skills_infrastructure_postgres.run_skill_snapshot_source_json(
                {**manifest, "builtin_tool_identities": manifest["builtin_tool_identities"] + [forged_identity]}
            )


def test_build_skill_snapshot_governance_summarizes_files_without_package_bytes():
    pin = {
        "skill_id": "example-skill",
        "version": "hash-primary",
        "content_hash": "hash-primary",
        "source": {
            "kind": "uploaded",
            "storage_key": "tenants/default/skills/example-skill/package.zip",
        },
        "files": [
            {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5},
            {"relative_path": "references/guide.md", "content_base64": "Z3VpZGU=", "size_bytes": 5},
        ],
        "dependency_ids": ["minimax-docx"],
    }

    governance = build_skill_snapshot_governance(
        pin,
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": True,
            "selected_track": "current",
            "rollout_percent": 100,
            "channel": "stable",
            "selected_version": "hash-primary",
        },
    )

    assert governance["schema_version"] == "ai-platform.skill-pinned-snapshot-governance.v2"
    assert governance["snapshot_source"] == "platform_release_lock"
    assert governance["release_lock"] == {
        "schema_version": "ai-platform.skill-release-decision.v1",
        "mode": "release_policy",
    }
    assert governance["manifest"] == {
        "source_kind": "uploaded",
        "selected_file_count": 2,
    }
    assert governance["dependency_evidence"] == {
        "status": "review_required",
        "ref": "skill_dependency_policy",
        "dependency_count": 1,
    }
    assert [item["relative_path"] for item in governance["selected_files"]] == [
        "SKILL.md",
        "references/guide.md",
    ]
    assert governance["selected_files"][0]["sha256"] == (
        "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df"
    )
    assert governance["does_not_close_b4_or_deployed_runtime_acceptance"] is True
    serialized = json.dumps(governance, ensure_ascii=False)
    assert "content_base64" not in serialized
    assert "storage_key" not in serialized
    assert "release_decision" not in serialized
    assert "selected_version" not in serialized
    assert "hash-primary" not in serialized
    assert "track" not in serialized
    assert "rollout" not in serialized


def test_attach_skill_snapshot_governance_keeps_existing_manifest_fields_immutable():
    manifest = {
        "skill_id": "general-chat",
        "version": "hash-general",
        "content_hash": "hash-general",
        "source": {"kind": "builtin", "asset_dir": "general-chat"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "Y2hhdA==", "size_bytes": 4}],
        "dependency_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }

    attached = attach_skill_snapshot_governance(
        [manifest],
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": False,
            "selected_track": "manifest_pin",
        },
    )

    assert attached[0] is not manifest
    assert "snapshot_governance" not in manifest
    assert attached[0]["source"] == manifest["source"]
    assert attached[0]["files"] == manifest["files"]
    assert attached[0]["snapshot_governance"]["release_lock"]["mode"] == "manifest_pin"
    assert attached[0]["snapshot_governance"]["dependency_evidence"]["status"] == "not_required"


def test_skill_manifest_ref_binds_complete_package_without_file_content():
    manifest = attach_skill_snapshot_governance(
        [
            {
                "skill_id": "example-skill",
                "version": "hash-primary",
                "content_hash": "hash-primary",
                "source": {"kind": "uploaded", "storage_key": "private/package.zip"},
                "files": [
                    {
                        "relative_path": "SKILL.md",
                        "content_base64": "c2tpbGw=",
                        "size_bytes": 5,
                    }
                ],
                "dependency_ids": [],
                "mcp_tool_ids": [],
                "execution_profile": {"strategy": "sdk_native"},
                "builtin_tool_identities": ["Read"],
            }
        ]
    )[0]

    ref = build_skill_manifest_ref(manifest)

    assert ref == {
        "schema_version": "ai-platform.skill-materialization-ref.v1",
        "skill_id": "example-skill",
        "version": "hash-primary",
        "content_hash": "hash-primary",
        "materialization_sha256": ref["materialization_sha256"],
    }
    assert len(ref["materialization_sha256"]) == 64
    serialized = json.dumps(ref, ensure_ascii=False)
    assert "files" not in ref
    assert "content_base64" not in serialized
    assert "storage_key" not in serialized

    changed = {
        **manifest,
        "files": [{**manifest["files"][0], "content_base64": "ZGlmZmVyZW50"}],
    }
    assert build_skill_manifest_ref(changed)["materialization_sha256"] != ref[
        "materialization_sha256"
    ]


def test_skill_manifest_ref_transport_rejects_full_mixed_and_malformed_payloads():
    valid = {
        "schema_version": "ai-platform.skill-materialization-ref.v1",
        "skill_id": "example-skill",
        "version": "hash-primary",
        "content_hash": "hash-primary",
        "materialization_sha256": "a" * 64,
    }

    assert validate_skill_manifest_refs([valid]) == [valid]
    for invalid in (
        [{"skill_id": "example-skill", "files": []}],
        [valid, {"skill_id": "dependency", "files": []}],
        [{**valid, "unexpected": True}],
        [{**valid, "content_hash": "different"}],
        [{**valid, "materialization_sha256": "A" * 64}],
        [valid, dict(valid)],
    ):
        with pytest.raises(
            SkillVersionMaterializationError,
            match="skill_version_not_materializable",
        ):
            validate_skill_manifest_refs(invalid)


def test_build_skill_snapshot_governance_rejects_invalid_or_escaped_file_entries():
    base_manifest = {
        "skill_id": "example-skill",
        "version": "hash-primary",
        "content_hash": "hash-primary",
        "source": {"kind": "uploaded"},
        "dependency_ids": [],
    }

    for files in (
        [{"relative_path": "../SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        [{"relative_path": "/absolute/SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        [{"relative_path": "C:/Users/release/SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        [{"relative_path": "references/..", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        [{"relative_path": "SKILL.md", "content_base64": "[not-base64]", "size_bytes": 5}],
        [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": "not-an-int"}],
        [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": -1}],
        [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 999}],
    ):
        with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
            build_skill_snapshot_governance({**base_manifest, "files": files})


def test_build_uploaded_skill_manifest_pin_uses_source_snapshot_files():
    files = [
        {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5},
        {"relative_path": "references/guide.md", "content_base64": "Z3VpZGU=", "size_bytes": 5},
    ]

    pin = build_uploaded_skill_manifest_pin(
        {
            "skill_id": "example-skill",
            "version": "hash-uploaded",
            "content_hash": "hash-uploaded",
            "description": "Review Word documents.",
            "source": {"kind": "uploaded", "storage_key": "package.zip", "files": files},
            "dependency_ids": ["minimax-docx"],
            "status": "reviewed",
        }
    )

    assert pin["source"] == {"kind": "uploaded", "storage_key": "package.zip"}
    assert pin["files"] == files
    assert pin["dependency_ids"] == ["minimax-docx"]
    assert pin["lifecycle_status"] == "reviewed"
    assert pin["execution_profile"] == {
        "schema_version": "ai-platform.skill-execution-profile.v1",
        "strategy": "sdk_native",
        "trust_basis": "admin_reviewed_release",
        "builtin_tool_identities": [
            "Read",
            "Glob",
            "LS",
            "Bash",
            "Write",
            "Edit",
            "Grep",
        ],
        "workspace_contract": "ai-platform.skill-workspace.v1",
        "command_isolation": "sibling-tool-sandbox-v1",
    }
    assert pin["builtin_tool_identities"] == pin["execution_profile"]["builtin_tool_identities"]


def test_build_uploaded_skill_manifest_pin_rejects_overlong_utf8_path_component():
    files = [
        {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5},
        {
            "relative_path": f"references/{'测' * 85}.md",
            "content_base64": "Z3VpZGU=",
            "size_bytes": 5,
        },
    ]

    with pytest.raises(
        SkillVersionMaterializationError,
        match="skill_version_not_materializable",
    ):
        build_uploaded_skill_manifest_pin(
            {
                "skill_id": "example-skill",
                "version": "hash-uploaded",
                "content_hash": "hash-uploaded",
                "description": "Review Word documents.",
                "source": {
                    "kind": "uploaded",
                    "storage_key": "package.zip",
                    "files": files,
                },
                "dependency_ids": [],
                "status": "reviewed",
            }
        )


def test_build_skill_version_manifest_pin_preserves_historical_builtin_source():
    files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]

    pin = build_skill_version_manifest_pin(
        {
            "skill_id": "example-skill",
            "version": "hash-builtin",
            "content_hash": "hash-builtin",
            "description": "Review Word documents.",
            "source": {"kind": "builtin", "asset_dir": "example-skill", "version": "hash-builtin", "files": files},
            "dependency_ids": [],
            "status": "released",
        }
    )

    assert pin["source"] == {"kind": "builtin", "asset_dir": "example-skill", "version": "hash-builtin"}
    assert pin["files"] == files
    assert pin["lifecycle_status"] == "released"
    assert pin["execution_profile"] == {
        "schema_version": "ai-platform.skill-execution-profile.v1",
        "strategy": "sdk_restricted",
        "trust_basis": "legacy_or_unreviewed",
        "builtin_tool_identities": [],
        "workspace_contract": "ai-platform.skill-workspace.v1",
        "command_isolation": "none",
    }
    assert pin["builtin_tool_identities"] == []


def test_snapshot_source_rejects_forged_uploaded_execution_profile():
    files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]
    manifest = build_uploaded_skill_manifest_pin(
        {
            "skill_id": "example-skill",
            "version": "hash-uploaded",
            "content_hash": "hash-uploaded",
            "description": "Review Word documents.",
            "source": {"kind": "uploaded", "storage_key": "package.zip", "files": files},
            "dependency_ids": [],
            "status": "released",
        }
    )

    forged = {
        **manifest,
        "execution_profile": {
            **manifest["execution_profile"],
            "strategy": "sandbox_full_local",
            "trust_basis": "repository_builtin",
        },
    }

    with pytest.raises(_owner_platform_postgres_errors.RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
        _owner_skills_infrastructure_postgres.run_skill_snapshot_source_json(forged)


def test_build_skill_version_policy_manifest_pins_accepts_zero_dependency_builtin_version():
    files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]

    pins = build_skill_version_policy_manifest_pins(
        {
            "skill_id": "example-skill",
            "version": "hash-builtin",
            "content_hash": "hash-builtin",
            "description": "Review Word documents.",
            "source": {
                "kind": "builtin",
                "asset_dir": "example-skill",
                "version": "hash-builtin",
                "files": files,
            },
            "dependency_ids": [],
            "status": "active",
        },
        available_skill_ids={"example-skill", "minimax-docx"},
    )

    assert [pin["skill_id"] for pin in pins] == ["example-skill"]
    assert pins[0]["dependency_ids"] == []


def test_build_skill_version_policy_manifest_pins_accepts_zero_dependency_uploaded_version():
    files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]

    pins = build_skill_version_policy_manifest_pins(
        {
            "skill_id": "example-skill",
            "version": "hash-uploaded",
            "content_hash": "hash-uploaded",
            "description": "Review Word documents.",
            "source": {
                "kind": "uploaded",
                "storage_key": "package.zip",
                "files": files,
            },
            "dependency_ids": [],
            "status": "active",
        },
        available_skill_ids={"example-skill", "minimax-docx"},
    )

    assert [pin["skill_id"] for pin in pins] == ["example-skill"]
    assert pins[0]["dependency_ids"] == []


def test_build_uploaded_skill_manifest_pin_rejects_missing_files():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        build_uploaded_skill_manifest_pin(
            {
                "skill_id": "example-skill",
                "version": "hash-uploaded",
                "content_hash": "hash-uploaded",
                "description": "Review Word documents.",
                "source": {"kind": "uploaded", "storage_key": "package.zip"},
                "dependency_ids": [],
                "status": "active",
            }
        )


def test_build_uploaded_skill_manifest_pin_rejects_inactive_version():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        build_uploaded_skill_manifest_pin(
            {
                "skill_id": "example-skill",
                "version": "hash-uploaded",
                "content_hash": "hash-uploaded",
                "description": "Review Word documents.",
                "source": {
                    "kind": "uploaded",
                    "storage_key": "package.zip",
                    "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                },
                "dependency_ids": [],
                "status": "disabled",
            }
        )


def test_governed_locked_skill_version_requires_primary_pin_when_release_policy_exists():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        governed_locked_skill_version(
            skill_id="example-skill",
            skill_manifests=[],
            fallback_version="hash-release",
            release_policy_version="hash-release",
        )


def test_governed_locked_skill_version_rejects_primary_pin_that_differs_from_release_policy():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        governed_locked_skill_version(
            skill_id="example-skill",
            skill_manifests=[
                {
                    "skill_id": "example-skill",
                    "version": "current-hash",
                    "content_hash": "current-hash",
                }
            ],
            fallback_version="hash-release",
            release_policy_version="hash-release",
        )


def test_governed_locked_skill_version_requires_primary_pin_without_release_policy():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        governed_locked_skill_version(
            skill_id="example-skill",
            skill_manifests=[],
            fallback_version="db-version",
        )


def test_governed_locked_skill_version_keeps_legacy_pin_behavior_without_release_policy():
    assert (
        governed_locked_skill_version(
            skill_id="example-skill",
            skill_manifests=[
                {
                    "skill_id": "example-skill",
                    "version": "current-hash",
                    "content_hash": "current-hash",
                }
            ],
            fallback_version="db-version",
        )
        == "current-hash"
    )


def _symlink_or_skip(target, link):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation not available: {exc}")


def test_builtin_skill_registry_rejects_symlinked_files(tmp_path):
    skill_dir = write_skill(tmp_path, "example-skill", "Review Word documents.")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    _symlink_or_skip(outside, skill_dir / "references-link.md")

    with pytest.raises(ValueError, match="symlink"):
        BuiltinSkillRegistry(tmp_path).list_builtin_skills()
