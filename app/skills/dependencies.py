from app.validation import assert_safe_id


INVALID_DEPENDENCY_ID = "[invalid-skill-id]"


class SkillDependencyPolicyError(ValueError):
    pass


def _safe_dependency_id(dependency_id: str) -> str | None:
    try:
        return assert_safe_id(dependency_id, "dependency_id")
    except ValueError:
        return None


def _assert_dependency_allowed(
    skill_id: str,
    dependency_id: str,
    available_skill_ids: set[str],
) -> None:
    if _safe_dependency_id(dependency_id) is None:
        raise SkillDependencyPolicyError("skill_dependency_invalid_id")
    if dependency_id == skill_id:
        raise SkillDependencyPolicyError(f"skill_dependency_cycle: {skill_id}")
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
            "available": False,
        }

    reason = "declared_dependency"
    status = "allowed"
    if dependency_id == skill_id:
        reason = "skill_dependency_cycle"
        status = "blocked"
    elif dependency_id not in available_skill_ids:
        reason = "skill_dependency_missing"
        status = "blocked"

    return {
        "skill_id": safe_dependency_id,
        "status": status,
        "reason": reason,
        "available": safe_dependency_id in available_skill_ids,
    }


def validate_skill_dependency_ids(
    skill_id: str,
    dependency_ids: list[str],
    available_skill_ids: set[str],
) -> list[str]:
    validated: list[str] = []
    for dependency_id in dependency_ids:
        _assert_dependency_allowed(skill_id, dependency_id, available_skill_ids)
        if dependency_id in validated:
            raise SkillDependencyPolicyError(f"skill_dependency_duplicate: {dependency_id}")
        validated.append(dependency_id)
    return validated


def skill_dependency_policy(
    skill_id: str,
    available_skill_ids: set[str],
    dependency_ids: list[str] | None = None,
) -> dict[str, object]:
    declared_dependency_ids = dependency_ids or []
    dependency_details = [
        _dependency_policy_detail(skill_id, dependency_id, available_skill_ids)
        for dependency_id in declared_dependency_ids
    ]
    return {
        "skill_id": skill_id,
        "dependency_ids": [str(detail["skill_id"]) for detail in dependency_details],
        "dependency_details": dependency_details,
    }
