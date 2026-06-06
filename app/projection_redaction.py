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

PUBLIC_AGENT_ID_BY_CAPABILITY = {
    "general_chat": "general-agent",
    "document_review": "document-review",
    "document_translation": "document-translation",
    "knowledge_answer": "knowledge-answer",
}

INTERNAL_AGENT_ID_BY_PUBLIC_ID = {
    "document-review": "qa-word-review",
    "document-translation": "baoyu-translate",
    "knowledge-answer": "sop-assistant",
}

DEFAULT_SKILL_ID_BY_PUBLIC_AGENT_ID = {
    "document-review": "qa-file-reviewer",
    "document-translation": "baoyu-translate",
    "knowledge-answer": "ragflow-knowledge-search",
}

RAW_SKILL_KEYS = {"allowed_skills", "staged_skills", "used_skills"}
SERVER_OWNED_CONTROL_KEYS = {
    "copiedfromrunid",
    "dispatchchildrunid",
    "dispatchclaimedat",
    "dispatchclaimedby",
    "dispatchhandedoffat",
    "dispatchid",
    "dispatchkind",
    "dispatchleaseexpiresat",
    "dispatchstate",
    "multiagentdispatch",
    "parentrunid",
    "parentstepid",
    "resume",
}
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
AGENT_ID_ALIASES = {"agentid", "selectedagentid", "requestedagentid", "resolvedagentid", "rawagentid"}


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
    """Map internal skill or agent ids to public capability ids."""
    if isinstance(skill_id, str) and skill_id in CAPABILITY_BY_SKILL_ID:
        return CAPABILITY_BY_SKILL_ID[skill_id]
    if isinstance(agent_id, str) and agent_id in CAPABILITY_BY_AGENT_ID:
        return CAPABILITY_BY_AGENT_ID[agent_id]
    return None


def public_agent_id_for_projection(agent_id: object, skill_id: object | None = None) -> str | None:
    """Return the public-facing agent id for user-visible projections."""
    if not isinstance(agent_id, str) or not agent_id:
        return None
    capability_id = capability_id_from_skill(skill_id, agent_id)
    if capability_id:
        return PUBLIC_AGENT_ID_BY_CAPABILITY.get(capability_id)
    if agent_id not in CAPABILITY_BY_SKILL_ID and agent_id not in CAPABILITY_BY_AGENT_ID:
        return agent_id
    return None


def internal_agent_id_for_request(agent_id: object) -> str | None:
    """Map public frontend agent ids back to internal executable agent ids."""
    if not isinstance(agent_id, str) or not agent_id:
        return None
    return INTERNAL_AGENT_ID_BY_PUBLIC_ID.get(agent_id, agent_id)


def default_skill_id_for_public_agent(agent_id: object) -> str | None:
    """Return the internal default skill for a public frontend agent id."""
    if not isinstance(agent_id, str) or not agent_id:
        return None
    return DEFAULT_SKILL_ID_BY_PUBLIC_AGENT_ID.get(agent_id)


def redact_raw_skill_references(value: Any, *, preserve_empty_skill_ids: bool = False) -> Any:
    """Remove raw skill ids from nested user-visible payloads."""
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
        if normalized_key in AGENT_ID_ALIASES:
            public_agent_id = public_agent_id_for_projection(item, _value_for_alias(value, RAW_SKILL_ID_ALIASES) or value.get("skill_id"))
            if public_agent_id:
                redacted[key] = public_agent_id
            continue
        redacted[key] = redact_raw_skill_references(item, preserve_empty_skill_ids=preserve_empty_skill_ids)
    return redacted


def strip_server_owned_control_metadata(value: Any) -> Any:
    """Remove server-owned control metadata from user-controlled/public payloads."""
    if isinstance(value, list):
        return [strip_server_owned_control_metadata(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if _normalized_key(key) in SERVER_OWNED_CONTROL_KEYS:
            continue
        cleaned[key] = strip_server_owned_control_metadata(item)
    return cleaned


def sanitize_user_control_input(value: Any) -> dict[str, Any]:
    """Sanitize user-controlled input before public projection or replay."""
    sanitized = sanitize_public_payload(strip_server_owned_control_metadata(redact_raw_skill_references(value)))
    return sanitized if isinstance(sanitized, dict) else {}
