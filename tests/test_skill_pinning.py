import base64
import json

import pytest

from app import repositories as repository_module
import app.skills.dependencies as dependency_policy
from app.skills.dependencies import SkillDependencyPolicyError
from app.skills.pinning import (
    attach_skill_snapshot_governance,
    build_skill_snapshot_governance,
    SkillVersionMaterializationError,
    build_skill_version_dependency_manifest_pins,
    build_skill_manifest_pins,
    build_skill_version_manifest_pin,
    build_skill_version_policy_manifest_pins,
    build_uploaded_skill_manifest_pin,
    governed_locked_skill_version,
)
from app.skills.registry import BuiltinSkill, BuiltinSkillRegistry


def write_skill(root, name, description):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_build_skill_manifest_pins_includes_primary_dependencies_and_file_snapshots(tmp_path):
    write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    write_skill(tmp_path, "minimax-docx", "Manipulate Word documents.")
    (tmp_path / "qa-file-reviewer" / "references").mkdir()
    (tmp_path / "qa-file-reviewer" / "references" / "guide.md").write_text("review guide", encoding="utf-8")
    skills = BuiltinSkillRegistry(tmp_path).list_builtin_skills()

    pins = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={},
        builtin_skills=skills,
    )

    assert [item["skill_id"] for item in pins] == ["qa-file-reviewer", "minimax-docx"]
    assert pins[0]["version"] == pins[0]["content_hash"]
    assert pins[0]["dependency_ids"] == ["minimax-docx"]
    assert pins[0]["source"]["asset_dir"] == "qa-file-reviewer"
    assert [item["relative_path"] for item in pins[0]["files"]] == ["SKILL.md", "references/guide.md"]
    assert pins[0]["files"][0]["content_base64"]
    assert pins[0]["files"][0]["size_bytes"] == len(base64.b64decode(pins[0]["files"][0]["content_base64"]))
    assert pins[0]["allowed"] is True
    assert pins[0]["staged"] is False
    assert pins[0]["used"] is False
    assert pins[0]["builtin_tool_identities"] == ["Bash", "Write"]
    assert pins[1]["builtin_tool_identities"] == ["Bash", "Write"]


def test_builtin_tool_identity_snapshot_comes_from_server_skill_declaration_not_input(tmp_path):
    write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    write_skill(tmp_path, "minimax-docx", "Manipulate Word documents.")
    skills = BuiltinSkillRegistry(tmp_path).list_builtin_skills()

    pins = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={"builtin_tool_identities": ["WebFetch", "Agent"]},
        builtin_skills=skills,
    )

    assert pins[0]["builtin_tool_identities"] == ["Bash", "Write"]
    assert pins[1]["builtin_tool_identities"] == ["Bash", "Write"]


def test_snapshot_source_locks_canonical_builtin_tool_identities(tmp_path):
    write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    write_skill(tmp_path, "minimax-docx", "Manipulate Word documents.")
    skills = BuiltinSkillRegistry(tmp_path).list_builtin_skills()
    manifest = build_skill_manifest_pins(
        skill_id="qa-file-reviewer",
        input_payload={},
        builtin_skills=skills,
    )[0]

    expected = repository_module.run_skill_snapshot_source_json(manifest)
    reordered = {**manifest, "builtin_tool_identities": ["Write", "Bash", "Write"]}

    assert repository_module.run_skill_snapshot_source_json(reordered) == expected
    for forged_identity in ("Agent", "WebFetch"):
        with pytest.raises(repository_module.RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
            repository_module.run_skill_snapshot_source_json(
                {**manifest, "builtin_tool_identities": ["Bash", "Write", forged_identity]}
            )


def test_build_skill_snapshot_governance_summarizes_files_without_package_bytes():
    pin = {
        "skill_id": "qa-file-reviewer",
        "version": "hash-primary",
        "content_hash": "hash-primary",
        "source": {
            "kind": "uploaded",
            "storage_key": "tenants/default/skills/qa-file-reviewer/package.zip",
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

    assert governance["schema_version"] == "ai-platform.skill-pinned-snapshot-governance.v1"
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
    assert governance["does_not_close_b4_or_211"] is True
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


def test_build_skill_snapshot_governance_rejects_invalid_or_escaped_file_entries():
    base_manifest = {
        "skill_id": "qa-file-reviewer",
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


def test_build_skill_manifest_pins_keeps_ragflow_skill_as_single_zero_dependency_manifest(tmp_path):
    write_skill(tmp_path, "ragflow-knowledge-search", "Read-only SOP knowledge retrieval.")
    skills = BuiltinSkillRegistry(tmp_path).list_builtin_skills()

    pins = build_skill_manifest_pins(
        skill_id="ragflow-knowledge-search",
        input_payload={},
        builtin_skills=skills,
    )

    assert [item["skill_id"] for item in pins] == ["ragflow-knowledge-search"]
    assert pins[0]["version"] == pins[0]["content_hash"]
    assert pins[0]["dependency_ids"] == []
    assert pins[0]["source"]["asset_dir"] == "ragflow-knowledge-search"
    assert [item["relative_path"] for item in pins[0]["files"]] == ["SKILL.md"]
    assert pins[0]["allowed"] is True
    assert pins[0]["staged"] is False
    assert pins[0]["used"] is False


def test_build_skill_manifest_pins_rejects_public_skill_dependency(monkeypatch, tmp_path):
    write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    write_skill(tmp_path, "baoyu-translate", "Translate documents.")
    skills = BuiltinSkillRegistry(tmp_path).list_builtin_skills()
    monkeypatch.setattr(dependency_policy, "SKILL_DEPENDENCIES", {"qa-file-reviewer": ["baoyu-translate"]})

    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_not_internal"):
        build_skill_manifest_pins(
            skill_id="qa-file-reviewer",
            input_payload={},
            builtin_skills=skills,
        )


def test_build_skill_manifest_pins_returns_empty_for_non_builtin_skill(tmp_path):
    write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    skills = BuiltinSkillRegistry(tmp_path).list_builtin_skills()

    assert build_skill_manifest_pins(
        skill_id="general-chat",
        input_payload={},
        builtin_skills=skills,
    ) == []


def test_build_uploaded_skill_manifest_pin_uses_source_snapshot_files():
    files = [
        {"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5},
        {"relative_path": "references/guide.md", "content_base64": "Z3VpZGU=", "size_bytes": 5},
    ]

    pin = build_uploaded_skill_manifest_pin(
        {
            "skill_id": "qa-file-reviewer",
            "version": "hash-uploaded",
            "content_hash": "hash-uploaded",
            "description": "Review Word documents.",
            "source": {"kind": "uploaded", "storage_key": "package.zip", "files": files},
            "dependency_ids": ["minimax-docx"],
            "status": "active",
        }
    )

    assert pin == {
        "skill_id": "qa-file-reviewer",
        "description": "Review Word documents.",
        "version": "hash-uploaded",
        "content_hash": "hash-uploaded",
        "source": {"kind": "uploaded", "storage_key": "package.zip"},
            "files": files,
            "dependency_ids": ["minimax-docx"],
            "builtin_tool_identities": [],
            "allowed": True,
        "staged": False,
        "used": False,
    }


def test_build_skill_version_manifest_pin_uses_builtin_snapshot_files():
    files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]

    pin = build_skill_version_manifest_pin(
        {
            "skill_id": "qa-file-reviewer",
            "version": "hash-builtin",
            "content_hash": "hash-builtin",
            "description": "Review Word documents.",
            "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer", "version": "hash-builtin", "files": files},
            "dependency_ids": [],
            "status": "active",
        }
    )

    assert pin == {
        "skill_id": "qa-file-reviewer",
        "description": "Review Word documents.",
        "version": "hash-builtin",
        "content_hash": "hash-builtin",
        "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer", "version": "hash-builtin"},
            "files": files,
            "dependency_ids": [],
            "builtin_tool_identities": ["Bash", "Write"],
            "allowed": True,
        "staged": False,
        "used": False,
    }


def test_build_skill_version_policy_manifest_pins_rejects_stale_builtin_dependencies():
    files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]

    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        build_skill_version_policy_manifest_pins(
            {
                "skill_id": "qa-file-reviewer",
                "version": "hash-builtin",
                "content_hash": "hash-builtin",
                "description": "Review Word documents.",
                "source": {
                    "kind": "builtin",
                    "asset_dir": "qa-file-reviewer",
                    "version": "hash-builtin",
                    "files": files,
                },
                "dependency_ids": [],
                "status": "active",
            },
            available_skill_ids={"qa-file-reviewer", "minimax-docx"},
        )


def test_build_skill_version_policy_manifest_pins_rejects_stale_uploaded_dependencies():
    files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]

    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        build_skill_version_policy_manifest_pins(
            {
                "skill_id": "qa-file-reviewer",
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
            available_skill_ids={"qa-file-reviewer", "minimax-docx"},
        )


def test_build_skill_version_dependency_manifest_pins_uses_versioned_dependency_snapshot():
    dependency_files = [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}]

    pins = build_skill_version_dependency_manifest_pins(
        {
            "skill_id": "qa-file-reviewer",
            "version": "hash-primary",
            "content_hash": "hash-primary",
            "source": {
                "kind": "uploaded",
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_manifests": [
                    {
                        "skill_id": "minimax-docx",
                        "description": "Pinned DOCX helper",
                        "version": "hash-pinned-dependency",
                        "content_hash": "hash-pinned-dependency",
                        "source": {
                            "kind": "builtin",
                            "asset_dir": "minimax-docx",
                            "version": "hash-pinned-dependency",
                        },
                        "files": dependency_files,
                        "dependency_ids": [],
                        "allowed": True,
                        "staged": False,
                        "used": False,
                    }
                ],
            },
            "dependency_ids": ["minimax-docx"],
            "status": "active",
        }
    )

    assert pins[0]["skill_id"] == "minimax-docx"
    assert pins[0]["content_hash"] == "hash-pinned-dependency"
    assert pins[0]["source"] == {
        "kind": "builtin",
        "asset_dir": "minimax-docx",
        "version": "hash-pinned-dependency",
    }
    assert pins[0]["files"] == dependency_files


def test_build_skill_version_dependency_manifest_pins_rejects_missing_dependency_snapshot():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        build_skill_version_dependency_manifest_pins(
            {
                "skill_id": "qa-file-reviewer",
                "version": "hash-primary",
                "content_hash": "hash-primary",
                "source": {
                    "kind": "uploaded",
                    "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                },
                "dependency_ids": ["minimax-docx"],
                "status": "active",
            }
        )


def test_build_uploaded_skill_manifest_pin_rejects_missing_files():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        build_uploaded_skill_manifest_pin(
            {
                "skill_id": "qa-file-reviewer",
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
                "skill_id": "qa-file-reviewer",
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
            skill_id="qa-file-reviewer",
            skill_manifests=[],
            fallback_version="hash-release",
            release_policy_version="hash-release",
        )


def test_governed_locked_skill_version_rejects_primary_pin_that_differs_from_release_policy():
    with pytest.raises(SkillVersionMaterializationError, match="skill_version_not_materializable"):
        governed_locked_skill_version(
            skill_id="qa-file-reviewer",
            skill_manifests=[
                {
                    "skill_id": "qa-file-reviewer",
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
            skill_id="qa-file-reviewer",
            skill_manifests=[],
            fallback_version="db-version",
        )


def test_governed_locked_skill_version_keeps_legacy_pin_behavior_without_release_policy():
    assert (
        governed_locked_skill_version(
            skill_id="qa-file-reviewer",
            skill_manifests=[
                {
                    "skill_id": "qa-file-reviewer",
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
    skill_dir = write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    _symlink_or_skip(outside, skill_dir / "references-link.md")

    with pytest.raises(ValueError, match="symlink"):
        BuiltinSkillRegistry(tmp_path).list_builtin_skills()


def test_build_skill_manifest_pins_rejects_symlinked_files(tmp_path):
    skill_dir = write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    dependency_dir = write_skill(tmp_path, "minimax-docx", "Manipulate Word documents.")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    _symlink_or_skip(outside, skill_dir / "references-link.md")

    with pytest.raises(ValueError, match="symlink"):
        build_skill_manifest_pins(
            skill_id="qa-file-reviewer",
            input_payload={},
            builtin_skills=[
                BuiltinSkill(
                    name="qa-file-reviewer",
                    description="Review Word documents.",
                    path=skill_dir,
                    version="hash",
                    source={"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                    entry={"kind": "filesystem", "path": str(skill_dir)},
                ),
                BuiltinSkill(
                    name="minimax-docx",
                    description="Manipulate Word documents.",
                    path=dependency_dir,
                    version="hash-minimax",
                    source={"kind": "builtin", "asset_dir": "minimax-docx"},
                    entry={"kind": "filesystem", "path": str(dependency_dir)},
                )
            ],
        )


def test_build_skill_manifest_pins_rejects_oversized_file(monkeypatch, tmp_path):
    skill_dir = write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    write_skill(tmp_path, "minimax-docx", "Manipulate Word documents.")
    large = skill_dir / "large.bin"
    large.write_bytes(b"0123456789")
    monkeypatch.setattr("app.skills.pinning.MAX_SKILL_SNAPSHOT_FILE_BYTES", 8)

    with pytest.raises(ValueError, match="file too large"):
        build_skill_manifest_pins(
            skill_id="qa-file-reviewer",
            input_payload={},
            builtin_skills=BuiltinSkillRegistry(tmp_path).list_builtin_skills(),
        )


def test_build_skill_manifest_pins_rejects_oversized_total(monkeypatch, tmp_path):
    skill_dir = write_skill(tmp_path, "qa-file-reviewer", "Review Word documents.")
    write_skill(tmp_path, "minimax-docx", "Manipulate Word documents.")
    (skill_dir / "a.bin").write_bytes(b"12345")
    (skill_dir / "b.bin").write_bytes(b"67890")
    monkeypatch.setattr("app.skills.pinning.MAX_SKILL_SNAPSHOT_FILE_BYTES", 100)
    monkeypatch.setattr("app.skills.pinning.MAX_SKILL_SNAPSHOT_TOTAL_BYTES", 20)

    with pytest.raises(ValueError, match="snapshot too large"):
        build_skill_manifest_pins(
            skill_id="qa-file-reviewer",
            input_payload={},
            builtin_skills=BuiltinSkillRegistry(tmp_path).list_builtin_skills(),
        )
