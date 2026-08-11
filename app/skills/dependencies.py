from pathlib import Path

from app.settings import get_settings
from app.skills.registry import BuiltinSkillRegistry, parse_skill_markdown_front_matter
from app.validation import assert_safe_id


INVALID_DEPENDENCY_ID = "[invalid-skill-id]"


class SkillDependencyPolicyError(ValueError):
    pass


def _safe_dependency_id(dependency_id: str) -> str | None:
    try:
        return assert_safe_id(dependency_id, "dependency_id")
    except ValueError:
        return None


def _builtin_skill_metadata(skill_id: str) -> dict[str, str]:
    try:
        safe_id = assert_safe_id(skill_id, "skill_id")
    except ValueError:
        return {}
    root = Path(get_settings().platform_skills_root).resolve(strict=False)
    manifest = (root / safe_id / "SKILL.md").resolve(strict=False)
    try:
        manifest.relative_to(root)
    except ValueError:
        return {}
    if not manifest.is_file() or manifest.is_symlink():
        return {}
    try:
        return parse_skill_markdown_front_matter(manifest.read_text(encoding="utf-8"))
    except OSError:
        return {}


def _declared_dependency_ids(skill_id: str) -> list[str]:
    raw = _builtin_skill_metadata(skill_id).get("dependencies", "")
    if not raw:
        return []
    dependency_ids = [item.strip() for item in raw.split(",") if item.strip()]
    if len(dependency_ids) != len(set(dependency_ids)):
        raise SkillDependencyPolicyError("skill_dependency_duplicate")
    return dependency_ids


def is_workbench_skill_public(skill_id: str) -> bool:
    """Uploaded Skills are public by distribution; builtins may declare internal visibility."""

    return _builtin_skill_metadata(skill_id).get("visibility", "public") != "internal"


def public_builtin_skill_ids() -> list[str]:
    """Return public built-in Skill IDs from repository-owned manifests."""

    root = Path(get_settings().platform_skills_root).resolve(strict=False)
    return [
        skill.name
        for skill in BuiltinSkillRegistry(root).list_builtin_skills()
        if is_workbench_skill_public(skill.name)
    ]


def _is_internal_dependency(skill_id: str) -> bool:
    return _builtin_skill_metadata(skill_id).get("visibility") == "internal"


def _assert_dependency_allowed(
    skill_id: str,
    dependency_id: str,
    available_skill_ids: set[str],
) -> None:
    if _safe_dependency_id(dependency_id) is None:
        raise SkillDependencyPolicyError("skill_dependency_invalid_id")
    if dependency_id == skill_id:
        raise SkillDependencyPolicyError(f"skill_dependency_cycle: {skill_id}")
    if not _is_internal_dependency(dependency_id):
        raise SkillDependencyPolicyError(f"skill_dependency_not_internal: {dependency_id}")
    if dependency_id not in available_skill_ids:
        raise SkillDependencyPolicyError(f"skill_dependency_missing: {dependency_id}")


def _dependency_policy_detail(
    skill_id: str,
    dependency_id: str,
    available_skill_ids: set[str],
) -> dict[str, object]:
    safe_dependency_id = _safe_dependency_id(dependency_id)
    if safe_dependency_id is None:
        return {
            "skill_id": INVALID_DEPENDENCY_ID,
            "status": "blocked",
            "reason": "skill_dependency_invalid_id",
            "public": False,
            "internal_dependency": False,
            "available": False,
        }

    internal = _is_internal_dependency(safe_dependency_id)
    available = safe_dependency_id in available_skill_ids
    reason = "declared_internal_dependency"
    status = "allowed"
    if safe_dependency_id == skill_id:
        reason = "skill_dependency_cycle"
        status = "blocked"
    elif not internal:
        reason = "skill_dependency_not_internal"
        status = "blocked"
    elif not available:
        reason = "skill_dependency_missing"
        status = "blocked"

    return {
        "skill_id": safe_dependency_id,
        "status": status,
        "reason": reason,
        "public": is_workbench_skill_public(safe_dependency_id),
        "internal_dependency": internal,
        "available": available,
    }


def skill_dependency_ids(skill_id: str, available_skill_ids: set[str]) -> list[str]:
    dependency_ids: list[str] = []
    for dependency_id in _declared_dependency_ids(skill_id):
        _assert_dependency_allowed(skill_id, dependency_id, available_skill_ids)
        dependency_ids.append(dependency_id)
    return dependency_ids


def skill_dependency_policy(skill_id: str, available_skill_ids: set[str]) -> dict[str, object]:
    dependency_details = [
        _dependency_policy_detail(skill_id, dependency_id, available_skill_ids)
        for dependency_id in _declared_dependency_ids(skill_id)
    ]
    return {
        "skill_id": skill_id,
        "public": is_workbench_skill_public(skill_id),
        "internal_dependency": _is_internal_dependency(skill_id),
        "dependency_ids": [str(detail["skill_id"]) for detail in dependency_details],
        "dependency_details": dependency_details,
    }


def with_skill_dependencies(selected: list[str], available_skill_ids: set[str]) -> list[str]:
    expanded: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(skill_id: str) -> None:
        if skill_id in visiting:
            raise SkillDependencyPolicyError(f"skill_dependency_cycle: {skill_id}")
        if skill_id in visited:
            return
        visiting.add(skill_id)
        if skill_id in available_skill_ids:
            expanded.append(skill_id)
        for dependency_id in skill_dependency_ids(skill_id, available_skill_ids):
            visit(dependency_id)
        visiting.remove(skill_id)
        visited.add(skill_id)

    for skill_id in selected:
        visit(skill_id)
    return expanded
