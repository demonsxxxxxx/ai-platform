from __future__ import annotations

SKILL_VERSION_DRAFT = "draft"
SKILL_VERSION_REVIEWED = "reviewed"
SKILL_VERSION_RELEASED = "released"
SKILL_VERSION_DISABLED = "disabled"
SKILL_VERSION_DEPRECATED = "deprecated"
SKILL_VERSION_LEGACY_ACTIVE = "active"

_ADMIN_MATERIALIZABLE_STATUSES = frozenset(
    {
        SKILL_VERSION_REVIEWED,
        SKILL_VERSION_RELEASED,
        SKILL_VERSION_LEGACY_ACTIVE,
    }
)
_RELEASABLE_STATUSES = _ADMIN_MATERIALIZABLE_STATUSES
_USER_RUNNABLE_STATUSES = frozenset(
    {
        SKILL_VERSION_RELEASED,
        SKILL_VERSION_LEGACY_ACTIVE,
    }
)


def normalize_skill_version_status(status: object) -> str:
    normalized = str(status or "").strip().lower()
    return normalized or SKILL_VERSION_DRAFT


def is_admin_materializable_status(status: object) -> bool:
    return normalize_skill_version_status(status) in _ADMIN_MATERIALIZABLE_STATUSES


def is_releasable_status(status: object) -> bool:
    return normalize_skill_version_status(status) in _RELEASABLE_STATUSES


def is_user_runnable_status(status: object) -> bool:
    return normalize_skill_version_status(status) in _USER_RUNNABLE_STATUSES
