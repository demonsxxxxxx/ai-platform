import pytest

from app.skills.lifecycle import (
    SKILL_VERSION_DEPRECATED,
    SKILL_VERSION_DISABLED,
    SKILL_VERSION_DRAFT,
    SKILL_VERSION_LEGACY_ACTIVE,
    SKILL_VERSION_RELEASED,
    SKILL_VERSION_REVIEWED,
    is_admin_materializable_status,
    is_releasable_status,
    is_user_runnable_status,
    normalize_skill_version_status,
)
from app.skills.pinning import (
    SkillVersionMaterializationError,
    build_skill_version_manifest_pin,
)


def test_skill_version_lifecycle_status_helpers_keep_legacy_active_compatible():
    assert SKILL_VERSION_DRAFT == "draft"
    assert SKILL_VERSION_REVIEWED == "reviewed"
    assert SKILL_VERSION_RELEASED == "released"
    assert SKILL_VERSION_DISABLED == "disabled"
    assert SKILL_VERSION_DEPRECATED == "deprecated"
    assert SKILL_VERSION_LEGACY_ACTIVE == "active"
    assert normalize_skill_version_status("") == "draft"
    assert normalize_skill_version_status(" Released ") == "released"
    assert is_admin_materializable_status("reviewed") is True
    assert is_admin_materializable_status("released") is True
    assert is_admin_materializable_status("active") is True
    assert is_releasable_status("reviewed") is True
    assert is_releasable_status("released") is True
    assert is_releasable_status("active") is True
    assert is_user_runnable_status("released") is True
    assert is_user_runnable_status("active") is True
    assert is_user_runnable_status("draft") is False
    assert is_user_runnable_status("reviewed") is False
    assert is_user_runnable_status("disabled") is False
    assert is_user_runnable_status("deprecated") is False


def test_skill_version_manifest_pin_accepts_released_reviewed_active_and_rejects_draft_disabled_deprecated():
    base = {
        "skill_id": "qa-file-reviewer",
        "version": "hash-reviewed",
        "content_hash": "hash-reviewed",
        "description": "Reviewed Skill",
        "source": {
            "kind": "uploaded",
            "files": [
                {
                    "relative_path": "SKILL.md",
                    "content_base64": "c2tpbGw=",
                    "size_bytes": 5,
                }
            ],
        },
        "dependency_ids": [],
    }

    for status in ("released", "reviewed", "active"):
        assert build_skill_version_manifest_pin({**base, "status": status})["version"] == "hash-reviewed"

    for status in ("draft", "disabled", "deprecated"):
        with pytest.raises(SkillVersionMaterializationError):
            build_skill_version_manifest_pin({**base, "status": status})
