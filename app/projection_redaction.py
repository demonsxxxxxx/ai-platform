from __future__ import annotations

from typing import Any

from app.control_plane_contracts import sanitize_public_payload


CAPABILITY_BY_SKILL_ID = {
    "general-chat": "general_chat",
    "qa-file-reviewer": "document_review",
    "baoyu-translate": "document_translation",
    "ragflow-knowledge-search": "knowledge_answer",
}

CAPABILITY_BY_AGENT_ID = {
    "general-agent": "general_chat",
    "qa-word-review": "document_review",
    "document-review": "document_review",
    "baoyu-translate": "document_translation",
    "sop-assistant": "knowledge_answer",
}

RAW_SKILL_KEYS = {"allowed_skills", "staged_skills", "used_skills"}
RAW_SKILL_ID_ALIASES = {
    "skillid",
    "defaultskillid",
    "selectedskillid",
    "requestedskillid",
    "preferredskillid",
    "resolvedskillid",
    "rawskillid",
}
RAW_SKILL_IDS_ALIASES = {
    "skillids",
    "defaultskillids",
    "selectedskillids",
    "requestedskillids",
    "preferredskillids",
    "resolvedskillids",
    "rawskillids",
}
RAW_SKILL_LIST_ALIASES = {"allowedskills", "stagedskills", "usedskills"}


def _normalized_key(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum()).lower()


def _value_for_alias(payload: dict[str, Any], aliases: set[str]) -> Any:
    for key, item in payload.items():
        if _normalized_key(key) in aliases:
            return item
    return None


def _is_raw_skill_id_key(normalized_key: str) -> bool:
    return normalized_key in RAW_SKILL_ID_ALIASES or normalized_key.endswith("skillid")


def _is_raw_skill_ids_key(normalized_key: str) -> bool:
    return normalized_key in RAW_SKILL_IDS_ALIASES or normalized_key.endswith("skillids")


def capability_id_from_skill(skill_id: object, agent_id: object | None = None) -> str | None:
    if isinstance(skill_id, str) and skill_id in CAPABILITY_BY_SKILL_ID:
        return CAPABILITY_BY_SKILL_ID[skill_id]
    if isinstance(agent_id, str) and agent_id in CAPABILITY_BY_AGENT_ID:
        return CAPABILITY_BY_AGENT_ID[agent_id]
    return None


def redact_raw_skill_references(value: Any, *, preserve_empty_skill_ids: bool = False) -> Any:
    if isinstance(value, list):
        return [redact_raw_skill_references(item, preserve_empty_skill_ids=preserve_empty_skill_ids) for item in value]
    if not isinstance(value, dict):
        return value

    redacted: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = _normalized_key(key)
        if _is_raw_skill_id_key(normalized_key):
            capability_id = capability_id_from_skill(
                item,
                _value_for_alias(value, {"agentid"}) or value.get("agent_id"),
            )
            if capability_id and "capability_id" not in value:
                redacted["capability_id"] = capability_id
            continue
        if _is_raw_skill_ids_key(normalized_key):
            if preserve_empty_skill_ids:
                redacted[key] = []
            continue
        if key in RAW_SKILL_KEYS or normalized_key in RAW_SKILL_LIST_ALIASES:
            continue
        redacted[key] = redact_raw_skill_references(item, preserve_empty_skill_ids=preserve_empty_skill_ids)
    return redacted


def sanitize_user_control_input(value: Any) -> dict[str, Any]:
    sanitized = sanitize_public_payload(redact_raw_skill_references(value))
    return sanitized if isinstance(sanitized, dict) else {}
