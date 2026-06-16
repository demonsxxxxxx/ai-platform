from app.validation import assert_safe_id


INVALID_DEPENDENCY_ID = "[invalid-skill-id]"

PUBLIC_WORKBENCH_SKILL_IDS = {
    "general-chat",
    "qa-file-reviewer",
    "baoyu-translate",
    "ragflow-knowledge-search",
    "ctd-32s73-stability-template-fill",
}

INTERNAL_DEPENDENCY_SKILL_IDS = {
    "minimax-docx",
    "reference-fact-extraction",
}

SKILL_DEPENDENCIES = {
    "qa-file-reviewer": ["minimax-docx"],
    "ctd-32s73-stability-template-fill": ["reference-fact-extraction"],
}


class SkillDependencyPolicyError(ValueError):
    pass


def _safe_dependency_id(dependency_id: str) -> str | None:
    try:
        return assert_safe_id(dependency_id, "dependency_id")
    except ValueError:
        return None


def is_workbench_skill_public(skill_id: str) -> bool:
    return skill_id in PUBLIC_WORKBENCH_SKILL_IDS


def _assert_dependency_allowed(skill_id: str, dependency_id: str, available_skill_ids: set[str]) -> None:
    if _safe_dependency_id(dependency_id) is None:
        raise SkillDependencyPolicyError("skill_dependency_invalid_id")
    if dependency_id == skill_id:
        raise SkillDependencyPolicyError(f"skill_dependency_cycle: {skill_id}")
    if dependency_id in PUBLIC_WORKBENCH_SKILL_IDS:
        raise SkillDependencyPolicyError(f"skill_dependency_not_internal: {dependency_id}")
    if dependency_id not in INTERNAL_DEPENDENCY_SKILL_IDS:
        raise SkillDependencyPolicyError(f"skill_dependency_not_allowed: {dependency_id}")
    if dependency_id not in available_skill_ids:
        raise SkillDependencyPolicyError(f"skill_dependency_missing: {dependency_id}")


def _dependency_policy_detail(skill_id: str, dependency_id: str, available_skill_ids: set[str]) -> dict[str, object]:
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

    reason = "declared_internal_dependency"
    status = "allowed"
    if dependency_id == skill_id:
        reason = "skill_dependency_cycle"
        status = "blocked"
    elif dependency_id in PUBLIC_WORKBENCH_SKILL_IDS:
        reason = "skill_dependency_not_internal"
        status = "blocked"
    elif dependency_id not in INTERNAL_DEPENDENCY_SKILL_IDS:
        reason = "skill_dependency_not_allowed"
        status = "blocked"
    elif dependency_id not in available_skill_ids:
        reason = "skill_dependency_missing"
        status = "blocked"

    return {
        "skill_id": safe_dependency_id,
        "status": status,
        "reason": reason,
        "public": safe_dependency_id in PUBLIC_WORKBENCH_SKILL_IDS,
        "internal_dependency": safe_dependency_id in INTERNAL_DEPENDENCY_SKILL_IDS,
        "available": safe_dependency_id in available_skill_ids,
    }


def skill_dependency_ids(skill_id: str, available_skill_ids: set[str]) -> list[str]:
    dependency_ids: list[str] = []
    for dependency_id in SKILL_DEPENDENCIES.get(skill_id, []):
        _assert_dependency_allowed(skill_id, dependency_id, available_skill_ids)
        dependency_ids.append(dependency_id)
    return dependency_ids


def skill_dependency_policy(skill_id: str, available_skill_ids: set[str]) -> dict[str, object]:
    dependency_ids: list[str] = []
    dependency_details: list[dict[str, object]] = []
    for dependency_id in SKILL_DEPENDENCIES.get(skill_id, []):
        detail = _dependency_policy_detail(skill_id, dependency_id, available_skill_ids)
        dependency_ids.append(str(detail["skill_id"]))
        dependency_details.append(detail)
    return {
        "skill_id": skill_id,
        "public": skill_id in PUBLIC_WORKBENCH_SKILL_IDS,
        "internal_dependency": skill_id in INTERNAL_DEPENDENCY_SKILL_IDS,
        "dependency_ids": dependency_ids,
        "dependency_details": dependency_details,
    }


def with_skill_dependencies(selected: list[str], available_skill_ids: set[str]) -> list[str]:
    expanded: list[str] = []
    for skill_id in selected:
        if skill_id in available_skill_ids and skill_id not in expanded:
            expanded.append(skill_id)
        for dependency_id in skill_dependency_ids(skill_id, available_skill_ids):
            if dependency_id not in expanded:
                expanded.append(dependency_id)
    return expanded
