from typing import Any

from app.control_plane_contracts import (
    HARNESS_CHAT_AGENT_ID,
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    RUN_EXECUTION_KIND_SKILL,
)
from app.validation import (
    MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS,
    assert_canonical_sha256,
    assert_safe_id,
)


def validate_agent_profile_execution_input(
    value: dict[str, Any],
    *,
    agent_id: str,
    execution_kind: str,
    skill_id: str | None,
    skill_version: str | None,
) -> dict[str, Any]:
    profile_agent_id = value.get("agent_id")
    revision = value.get("revision")
    content_hash = value.get("content_hash")
    instructions = value.get("instructions")
    raw_skill_set = value.get("skill_set")
    if not isinstance(raw_skill_set, list) or not raw_skill_set:
        raw_skill_set = [
            {
                "skill_id": value.get("required_skill_id") or skill_id,
                "expected_version": value.get("required_skill_version") or skill_version,
            }
        ]
    skill_set: list[dict[str, str]] = []
    for item in raw_skill_set:
        if not isinstance(item, dict):
            raise ValueError("agent_profile_skill_set_invalid")
        item_skill_id = assert_safe_id(item.get("skill_id"), "agent_profile.skill_set.skill_id")
        item_version = assert_safe_id(
            item.get("expected_version"),
            "agent_profile.skill_set.expected_version",
        )
        skill_set.append({"skill_id": item_skill_id, "expected_version": item_version})
    if len({item["skill_id"] for item in skill_set}) != len(skill_set):
        raise ValueError("agent_profile_skill_set_invalid")
    if not isinstance(profile_agent_id, str):
        raise ValueError("agent_profile_agent_id_invalid")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("agent_profile_revision_invalid")
    assert_canonical_sha256(content_hash, "agent_profile_hash_invalid")
    if (
        not isinstance(instructions, str)
        or not instructions
        or len(instructions) > MAX_SERVER_OWNED_SYSTEM_PROMPT_CHARS
    ):
        raise ValueError("agent_profile_instructions_invalid")
    if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        if (
            agent_id == HARNESS_CHAT_AGENT_ID
            or profile_agent_id != agent_id
            or skill_set[0]["skill_id"] != LEGACY_SYNTHETIC_CHAT_SKILL_ID
        ):
            raise ValueError("agent_profile_harness_identity_invalid")
    elif execution_kind == RUN_EXECUTION_KIND_SKILL:
        if profile_agent_id != agent_id:
            raise ValueError("agent_profile_agent_id_invalid")
        if skill_set[0]["skill_id"] != str(skill_id or ""):
            raise ValueError("agent_profile_required_skill_invalid")
        if skill_set[0]["expected_version"] != str(skill_version or ""):
            raise ValueError("agent_profile_required_skill_version_invalid")
    else:
        raise ValueError("agent_profile_execution_kind_invalid")
    return {
        "agent_id": assert_safe_id(profile_agent_id, "agent_profile.agent_id"),
        "revision": revision,
        "content_hash": content_hash,
        "instructions": instructions,
        "skill_set": skill_set,
    }
