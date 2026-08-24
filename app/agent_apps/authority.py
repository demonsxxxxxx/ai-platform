"""Deep Agent Profile aggregate authority behind route and repository adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app import repositories
from app.agent_apps.api import safe_agent_avatar_seed
from app.auth import AuthPrincipal, is_ai_admin, normalize_roles
from app.chat_session_projection import session_response
from app.control_plane_contracts import standard_trace_id
from app.model_catalog import resolve_model_selection
from app.mcp.api import parse_mcp_tool_reference
from app.models import (
    AgentConversationIdentity,
    AgentProfileAdminProjection,
    AgentProfileDraftRequest,
    AgentProfilePublicProjection,
    ChatSessionResponse,
    ChatStreamRequest,
    SelectedAgentProfileRequest,
    SelectedSkillRequest,
)
from app.settings import get_settings


_AVATAR_REFS = {"builtin:agent", "builtin:assistant", "builtin:document", "builtin:research"}
_CATEGORIES = {"general", "support", "writing", "research", "operations"}
_VISIBILITIES = {"tenant", "restricted"}
_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES = ["text", "file"]
_ROLLING_LEGACY_SUPPORTED_FILE_TYPES = [
    "application/*",
    "audio/*",
    "chemical/*",
    "font/*",
    "image/*",
    "message/*",
    "model/*",
    "multipart/*",
    "text/*",
    "video/*",
]
_PRESENCE_AWARE_PROFILE_FIELDS = (
    "welcome_message",
    "starter_prompts",
    "capability_summary",
    "recommended_tasks",
    "supported_input_types",
    "expected_outputs",
    "permissions_and_data_access_notice",
    "avatar_ref",
    "avatar_asset_id",
    "avatar_seed",
    "category",
    "visibility",
    "allowed_department_ids",
    "allowed_roles",
    "allowed_user_ids",
)
_PROFILE_TRANSPORT_SELECTOR_PATHS = frozenset(
    {
        "$.agent_options",
        "$.agent_options.model",
        "$.agent_options.model_id",
        "$.disabled_mcp_tools",
        "$.disabled_skills",
        "$.enabled_skills",
        "$.selected_agent_profile",
        "$.selected_mcp_tool_ids",
    }
)
_PROFILE_TRANSPORT_AGENT_OPTION_KEYS = frozenset(
    {"enable_thinking", "model", "model_id"}
)


@dataclass(frozen=True)
class AgentProfileAdmission:
    """Fully reauthorized immutable profile revision admitted to one Chat run."""

    agent_id: str
    revision: int
    content_hash: str
    skill: dict[str, Any]
    model: dict[str, str]
    mcp_tool_ids: tuple[str, ...]
    private_execution_input: dict[str, Any]
    public_identity: AgentConversationIdentity
    skills: tuple[dict[str, Any], ...] = ()
    configured_mcp_tool_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """Normalize old one-Skill constructors into the governed Skill Set."""

        normalized = self.skills or (self.skill,)
        object.__setattr__(self, "skills", normalized)
        object.__setattr__(self, "skill", normalized[0])
        if self.configured_mcp_tool_ids is None:
            object.__setattr__(self, "configured_mcp_tool_ids", self.mcp_tool_ids)


def _safe_avatar_ref(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _AVATAR_REFS else "builtin:agent"


def _safe_avatar_seed(value: Any, *, fallback: str) -> str:
    return safe_agent_avatar_seed(value, fallback=fallback)


def _safe_category(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _CATEGORIES else "general"


def _safe_visibility(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _VISIBILITIES else "restricted"


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if isinstance(item, str) and item.strip()))


def _mcp_tool_ids(row: dict[str, Any]) -> list[str]:
    raw = row.get("mcp_tool_ids")
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    return list(dict.fromkeys(raw))


def _effective_mcp_tool_ids(
    row: dict[str, Any],
    *,
    skills: tuple[dict[str, Any], ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    configured = tuple(_mcp_tool_ids(row))
    effective: list[str] = []
    normalized_input = {"mcp_tool_ids": list(configured)}
    for skill in skills:
        for tool_id in repositories.run_mcp_tool_ids_for_skill(skill, normalized_input):
            if tool_id not in effective:
                effective.append(tool_id)
    return configured, tuple(effective)


def _skill_set(row: dict[str, Any]) -> list[SelectedSkillRequest]:
    raw = row.get("skill_set")
    if not isinstance(raw, list) or not raw:
        raw = [
            {
                "skill_id": row.get("skill_id"),
                "expected_version": row.get("skill_version"),
            }
        ]
    try:
        skills = [SelectedSkillRequest.model_validate(item) for item in raw]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid") from exc
    if len({skill.skill_id for skill in skills}) != len(skills):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    return skills


def profile_acl_allows(row: dict[str, Any], *, principal: AuthPrincipal) -> bool:
    """Apply the immutable profile ACL before any public projection or use admission."""

    if is_ai_admin(principal):
        return True
    if _safe_visibility(row.get("visibility")) == "tenant":
        return True
    if principal.user_id in _safe_string_list(row.get("allowed_user_ids")):
        return True
    allowed_departments = set(_safe_string_list(row.get("allowed_department_ids")))
    if allowed_departments and principal.department_id not in allowed_departments:
        return False
    allowed_roles = set(normalize_roles(_safe_string_list(row.get("allowed_roles"))))
    if allowed_roles and not allowed_roles.intersection(normalize_roles(principal.roles)):
        return False
    return bool(allowed_departments or allowed_roles)


def profile_public_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Return the only Agent Profile card/detail fields available to ordinary users."""

    return {
        "agent_id": str(row["agent_id"]),
        "expected_revision": int(row["revision"]),
        "name": str(row["name"]),
        "description": str(row.get("description") or ""),
        "welcome_message": str(row.get("welcome_message") or ""),
        "starter_prompts": _safe_string_list(row.get("starter_prompts")),
        "capability_summary": str(row.get("capability_summary") or ""),
        "recommended_tasks": _safe_string_list(row.get("recommended_tasks")),
        "supported_input_types": list(_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES),
        "expected_outputs": _safe_string_list(row.get("expected_outputs")),
        "permissions_and_data_access_notice": str(
            row.get("permissions_and_data_access_notice") or ""
        ),
        "avatar_ref": _safe_avatar_ref(row.get("avatar_ref")),
        "avatar_seed": _safe_avatar_seed(row.get("avatar_seed"), fallback=str(row["agent_id"])),
        "category": _safe_category(row.get("category")),
        "published_at": row.get("published_at"),
    }


def conversation_identity_projection(row: dict[str, Any]) -> AgentConversationIdentity:
    """Project safe immutable identity retained by a recovered Agent Conversation."""

    public = profile_public_projection(row)
    return AgentConversationIdentity(
        agent_id=public["agent_id"],
        revision=public["expected_revision"],
        name=public["name"],
        description=public["description"],
        welcome_message=public["welcome_message"],
        starter_prompts=public["starter_prompts"],
        capability_summary=public["capability_summary"],
        recommended_tasks=public["recommended_tasks"],
        supported_input_types=public["supported_input_types"],
        expected_outputs=public["expected_outputs"],
        permissions_and_data_access_notice=public["permissions_and_data_access_notice"],
        avatar_ref=public["avatar_ref"],
        avatar_seed=public["avatar_seed"],
        category=public["category"],
        published_at=public["published_at"],
    )


def _revision_hash(definition: AgentProfileDraftRequest) -> str:
    """Hash every execution and public-lifecycle field under a canonical serialization."""

    material = {
        "name": definition.name,
        "description": definition.description,
        "welcome_message": definition.welcome_message,
        "starter_prompts": definition.starter_prompts,
        "capability_summary": definition.capability_summary,
        "recommended_tasks": definition.recommended_tasks,
        "supported_input_types": definition.supported_input_types,
        # Old workers still hash and enforce this physical column during a rolling
        # upgrade. Keep it broad and server-owned; it is no longer a product field.
        "supported_file_types": list(_ROLLING_LEGACY_SUPPORTED_FILE_TYPES),
        "expected_outputs": definition.expected_outputs,
        "permissions_and_data_access_notice": definition.permissions_and_data_access_notice,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_set": [skill.model_dump(mode="json") for skill in definition.skill_set],
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "avatar_asset_id": definition.avatar_asset_id,
        "avatar_seed": definition.avatar_seed,
        "category": definition.category,
        "visibility": definition.visibility,
        "allowed_department_ids": definition.allowed_department_ids,
        "allowed_roles": definition.allowed_roles,
        "allowed_user_ids": definition.allowed_user_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _legacy_skill_set_revision_hash(
    definition: AgentProfileDraftRequest,
    *,
    legacy_supported_input_types: list[str] | None = None,
    legacy_supported_file_types: list[str] | None = None,
) -> str:
    """Recompute hashes written before the profile file whitelist was retired."""

    material = {
        "name": definition.name,
        "description": definition.description,
        "welcome_message": definition.welcome_message,
        "starter_prompts": definition.starter_prompts,
        "capability_summary": definition.capability_summary,
        "recommended_tasks": definition.recommended_tasks,
        "supported_input_types": legacy_supported_input_types or definition.supported_input_types,
        "supported_file_types": legacy_supported_file_types or [],
        "expected_outputs": definition.expected_outputs,
        "permissions_and_data_access_notice": definition.permissions_and_data_access_notice,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_set": [skill.model_dump(mode="json") for skill in definition.skill_set],
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "avatar_asset_id": definition.avatar_asset_id,
        "avatar_seed": definition.avatar_seed,
        "category": definition.category,
        "visibility": definition.visibility,
        "allowed_department_ids": definition.allowed_department_ids,
        "allowed_roles": definition.allowed_roles,
        "allowed_user_ids": definition.allowed_user_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _omitted_file_type_skill_set_revision_hash(
    definition: AgentProfileDraftRequest,
    *,
    legacy_supported_input_types: list[str] | None = None,
    legacy_avatar_seed: str | None = None,
) -> str:
    """Recompute the brief Skill Set contract that omitted the retired key."""

    material = {
        "name": definition.name,
        "description": definition.description,
        "welcome_message": definition.welcome_message,
        "starter_prompts": definition.starter_prompts,
        "capability_summary": definition.capability_summary,
        "recommended_tasks": definition.recommended_tasks,
        "supported_input_types": (
            legacy_supported_input_types
            if legacy_supported_input_types is not None
            else definition.supported_input_types
        ),
        "expected_outputs": definition.expected_outputs,
        "permissions_and_data_access_notice": definition.permissions_and_data_access_notice,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_set": [skill.model_dump(mode="json") for skill in definition.skill_set],
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "avatar_asset_id": definition.avatar_asset_id,
        "avatar_seed": (
            legacy_avatar_seed
            if legacy_avatar_seed is not None
            else definition.avatar_seed
        ),
        "category": definition.category,
        "visibility": definition.visibility,
        "allowed_department_ids": definition.allowed_department_ids,
        "allowed_roles": definition.allowed_roles,
        "allowed_user_ids": definition.allowed_user_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _legacy_revision_hash(
    definition: AgentProfileDraftRequest,
    *,
    legacy_supported_input_types: list[str] | None = None,
    legacy_supported_file_types: list[str] | None = None,
) -> str:
    """Recompute the pre-Skill-Set hash for exact one-Skill compatibility."""

    if len(definition.skill_set) != 1:
        return ""
    primary = definition.skill_set[0]
    material = {
        "name": definition.name,
        "description": definition.description,
        "welcome_message": definition.welcome_message,
        "starter_prompts": definition.starter_prompts,
        "capability_summary": definition.capability_summary,
        "recommended_tasks": definition.recommended_tasks,
        "supported_input_types": legacy_supported_input_types or definition.supported_input_types,
        "supported_file_types": legacy_supported_file_types or [],
        "expected_outputs": definition.expected_outputs,
        "permissions_and_data_access_notice": definition.permissions_and_data_access_notice,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_id": primary.skill_id,
        "skill_version": primary.expected_version,
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "avatar_asset_id": definition.avatar_asset_id,
        "category": definition.category,
        "visibility": definition.visibility,
        "allowed_department_ids": definition.allowed_department_ids,
        "allowed_roles": definition.allowed_roles,
        "allowed_user_ids": definition.allowed_user_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _pre_avatar_seed_skill_set_revision_hash(
    definition: AgentProfileDraftRequest,
    *,
    legacy_supported_input_types: list[str] | None = None,
    legacy_supported_file_types: list[str] | None = None,
) -> str:
    """Recompute the Skill Set hash written before avatar seeds were introduced."""

    material = {
        "name": definition.name,
        "description": definition.description,
        "welcome_message": definition.welcome_message,
        "starter_prompts": definition.starter_prompts,
        "capability_summary": definition.capability_summary,
        "recommended_tasks": definition.recommended_tasks,
        "supported_input_types": legacy_supported_input_types or definition.supported_input_types,
        "supported_file_types": legacy_supported_file_types or [],
        "expected_outputs": definition.expected_outputs,
        "permissions_and_data_access_notice": definition.permissions_and_data_access_notice,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_set": [skill.model_dump(mode="json") for skill in definition.skill_set],
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "avatar_asset_id": definition.avatar_asset_id,
        "category": definition.category,
        "visibility": definition.visibility,
        "allowed_department_ids": definition.allowed_department_ids,
        "allowed_roles": definition.allowed_roles,
        "allowed_user_ids": definition.allowed_user_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _lifecycle_revision_hash(definition: AgentProfileDraftRequest) -> str:
    """Recompute the first ACL-aware one-Skill profile hash."""

    if len(definition.skill_set) != 1:
        return ""
    primary = definition.skill_set[0]
    material = {
        "name": definition.name,
        "description": definition.description,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_id": primary.skill_id,
        "skill_version": primary.expected_version,
        "mcp_tool_ids": definition.mcp_tool_ids,
        "avatar_ref": definition.avatar_ref,
        "category": definition.category,
        "visibility": definition.visibility,
        "allowed_department_ids": definition.allowed_department_ids,
        "allowed_roles": definition.allowed_roles,
        "allowed_user_ids": definition.allowed_user_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mvp_revision_hash(definition: AgentProfileDraftRequest) -> str:
    """Recompute the original one-Skill profile hash."""

    if len(definition.skill_set) != 1:
        return ""
    primary = definition.skill_set[0]
    material = {
        "name": definition.name,
        "description": definition.description,
        "instructions": definition.instructions,
        "model_id": definition.model_id,
        "skill_id": primary.skill_id,
        "skill_version": primary.expected_version,
        "mcp_tool_ids": definition.mcp_tool_ids,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _avatar_seed_is_historical_default(row: dict[str, Any]) -> bool:
    return row.get("avatar_seed") in {None, ""}


def _is_empty_historical_json_list(value: Any) -> bool:
    return value is None or value == []


def _strict_hash_string_list(
    row: dict[str, Any],
    field: str,
    *,
    allow_missing: bool,
) -> list[str] | None:
    raw = row.get(field)
    if raw is None and allow_missing:
        return None
    if not isinstance(raw, list):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    normalized = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if normalized != raw or len(normalized) != len(set(normalized)):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    return normalized


def _strict_hash_skill_set_shape(row: dict[str, Any]) -> bool:
    raw = row.get("skill_set")
    if raw is None or raw == []:
        return False
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    skills = [SelectedSkillRequest.model_validate(item) for item in raw]
    canonical = [skill.model_dump(mode="json") for skill in skills]
    if raw != canonical or len({skill.skill_id for skill in skills}) != len(skills):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    return True


def _strict_hash_row_shape(row: dict[str, Any]) -> tuple[dict[str, list[str] | None], bool]:
    optional_fields = {
        "starter_prompts",
        "recommended_tasks",
        "supported_input_types",
        "legacy_supported_file_types",
        "expected_outputs",
        "allowed_department_ids",
        "allowed_roles",
        "allowed_user_ids",
    }
    lists = {
        field: _strict_hash_string_list(row, field, allow_missing=True)
        for field in optional_fields
    }
    lists["mcp_tool_ids"] = _strict_hash_string_list(
        row,
        "mcp_tool_ids",
        allow_missing=False,
    )
    supported_input_types = lists["supported_input_types"]
    if supported_input_types is not None and (
        not supported_input_types
        or len(supported_input_types) > 2
        or any(value not in {"text", "file"} for value in supported_input_types)
    ):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    for field, allowed in (
        ("avatar_ref", _AVATAR_REFS),
        ("category", _CATEGORIES),
        ("visibility", _VISIBILITIES),
    ):
        raw = row.get(field)
        if raw is not None and (not isinstance(raw, str) or raw not in allowed):
            raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    raw_avatar_seed = row.get("avatar_seed")
    if raw_avatar_seed is not None and (
        not isinstance(raw_avatar_seed, str)
        or len(raw_avatar_seed) > 128
        or any(ord(character) < 32 for character in raw_avatar_seed)
    ):
        raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    return lists, _strict_hash_skill_set_shape(row)


def _post_lifecycle_fields_are_historical_defaults(row: dict[str, Any]) -> bool:
    return (
        str(row.get("welcome_message") or "") == ""
        and _is_empty_historical_json_list(row.get("starter_prompts"))
        and str(row.get("capability_summary") or "") == ""
        and _is_empty_historical_json_list(row.get("recommended_tasks"))
        and row.get("supported_input_types") == ["text"]
        and _is_empty_historical_json_list(row.get("legacy_supported_file_types"))
        and _is_empty_historical_json_list(row.get("expected_outputs"))
        and str(row.get("permissions_and_data_access_notice") or "") == ""
        and row.get("avatar_asset_id") in {None, ""}
        and _avatar_seed_is_historical_default(row)
    )


def _pre_lifecycle_fields_are_historical_defaults(row: dict[str, Any]) -> bool:
    return (
        row.get("avatar_ref") in {None, "builtin:agent"}
        and row.get("category") in {None, "general"}
        and row.get("visibility") in {None, "tenant"}
        and _is_empty_historical_json_list(row.get("allowed_department_ids"))
        and _is_empty_historical_json_list(row.get("allowed_roles"))
        and _is_empty_historical_json_list(row.get("allowed_user_ids"))
    )


def _revision_hash_matches(row: dict[str, Any], content_hash: str) -> bool:
    if len(content_hash) != 64 or any(character not in "0123456789abcdef" for character in content_hash):
        return False
    raw_lists, has_skill_set = _strict_hash_row_shape(row)
    definition = _draft_from_row(row)
    for field in (
        "starter_prompts",
        "recommended_tasks",
        "expected_outputs",
        "allowed_department_ids",
        "allowed_roles",
        "allowed_user_ids",
        "mcp_tool_ids",
    ):
        raw = raw_lists[field]
        if raw is not None and raw != getattr(definition, field):
            raise HTTPException(status_code=409, detail="agent_profile_revision_invalid")
    legacy_supported_input_types = raw_lists["supported_input_types"]
    legacy_supported_file_types = raw_lists["legacy_supported_file_types"]
    current_shape = has_skill_set and all(
        raw_lists[field] is not None
        for field in (
            "starter_prompts",
            "recommended_tasks",
            "supported_input_types",
            "legacy_supported_file_types",
            "expected_outputs",
            "allowed_department_ids",
            "allowed_roles",
            "allowed_user_ids",
        )
    )
    enterprise_shape = all(
        raw_lists[field] is not None
        for field in (
            "starter_prompts",
            "recommended_tasks",
            "supported_input_types",
            "legacy_supported_file_types",
            "expected_outputs",
            "allowed_department_ids",
            "allowed_roles",
            "allowed_user_ids",
        )
    )
    raw_avatar_seed = row.get("avatar_seed")
    if (
        current_shape
        and raw_avatar_seed == definition.avatar_seed
        and legacy_supported_input_types == list(_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES)
        and legacy_supported_file_types == list(_ROLLING_LEGACY_SUPPORTED_FILE_TYPES)
        and content_hash == _revision_hash(definition)
    ):
        return True
    if current_shape and content_hash == _omitted_file_type_skill_set_revision_hash(
        definition,
        legacy_supported_input_types=legacy_supported_input_types,
        legacy_avatar_seed=str(raw_avatar_seed or ""),
    ):
        return True
    if (
        current_shape
        and raw_avatar_seed == definition.avatar_seed
        and content_hash
        == _legacy_skill_set_revision_hash(
            definition,
            legacy_supported_input_types=legacy_supported_input_types,
            legacy_supported_file_types=legacy_supported_file_types,
        )
    ):
        return True
    if not _avatar_seed_is_historical_default(row):
        return False
    if current_shape and content_hash == _pre_avatar_seed_skill_set_revision_hash(
        definition,
        legacy_supported_input_types=legacy_supported_input_types,
        legacy_supported_file_types=legacy_supported_file_types,
    ):
        return True
    if (
        enterprise_shape
        and content_hash
        == _legacy_revision_hash(
            definition,
            legacy_supported_input_types=legacy_supported_input_types,
            legacy_supported_file_types=legacy_supported_file_types,
        )
    ):
        return True
    if not _post_lifecycle_fields_are_historical_defaults(row):
        return False
    if content_hash == _lifecycle_revision_hash(definition):
        return True
    return (
        _pre_lifecycle_fields_are_historical_defaults(row)
        and content_hash == _mvp_revision_hash(definition)
    )


def _draft_from_row(row: dict[str, Any]) -> AgentProfileDraftRequest:
    return AgentProfileDraftRequest(
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        welcome_message=str(row.get("welcome_message") or ""),
        starter_prompts=_safe_string_list(row.get("starter_prompts")),
        capability_summary=str(row.get("capability_summary") or ""),
        recommended_tasks=_safe_string_list(row.get("recommended_tasks")),
        supported_input_types=list(_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES),
        expected_outputs=_safe_string_list(row.get("expected_outputs")),
        permissions_and_data_access_notice=str(
            row.get("permissions_and_data_access_notice") or ""
        ),
        instructions=str(row["instructions"]),
        model_id=str(row["model_id"]),
        skill_set=_skill_set(row),
        mcp_tool_ids=_mcp_tool_ids(row),
        avatar_ref=_safe_avatar_ref(row.get("avatar_ref")),
        avatar_asset_id=(str(row.get("avatar_asset_id")) if row.get("avatar_asset_id") else None),
        avatar_seed=_safe_avatar_seed(row.get("avatar_seed"), fallback=str(row["agent_id"])),
        category=_safe_category(row.get("category")),
        visibility=_safe_visibility(row.get("visibility")),
        allowed_department_ids=_safe_string_list(row.get("allowed_department_ids")),
        allowed_roles=_safe_string_list(row.get("allowed_roles")),
        allowed_user_ids=_safe_string_list(row.get("allowed_user_ids")),
        expected_draft_revision=int(row["revision"]),
    )


def _merge_omitted_profile_fields(
    definition: AgentProfileDraftRequest,
    *,
    prior_row: dict[str, Any],
) -> AgentProfileDraftRequest:
    """Preserve legacy-client metadata omissions while honoring explicit empty ACLs."""

    prior = _draft_from_row(prior_row)
    updates = {
        field: getattr(prior, field)
        for field in _PRESENCE_AWARE_PROFILE_FIELDS
        if field not in definition.model_fields_set
    }
    return definition.model_copy(update=updates) if updates else definition


def _admin_projection(row: dict[str, Any]) -> AgentProfileAdminProjection:
    return AgentProfileAdminProjection(
        agent_id=str(row["agent_id"]),
        revision=int(row["revision"]),
        published_revision=(
            int(row["published_revision"])
            if row.get("published_revision") is not None
            else None
        ),
        status=str(row["status"]),
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        welcome_message=str(row.get("welcome_message") or ""),
        starter_prompts=_safe_string_list(row.get("starter_prompts")),
        capability_summary=str(row.get("capability_summary") or ""),
        recommended_tasks=_safe_string_list(row.get("recommended_tasks")),
        supported_input_types=list(_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES),
        expected_outputs=_safe_string_list(row.get("expected_outputs")),
        permissions_and_data_access_notice=str(
            row.get("permissions_and_data_access_notice") or ""
        ),
        instructions=str(row["instructions"]),
        model_id=str(row["model_id"]),
        skill_set=_skill_set(row),
        selected_skill=_skill_set(row)[0],
        mcp_tool_ids=_mcp_tool_ids(row),
        avatar_ref=_safe_avatar_ref(row.get("avatar_ref")),
        avatar_asset_id=(str(row.get("avatar_asset_id")) if row.get("avatar_asset_id") else None),
        avatar_seed=_safe_avatar_seed(row.get("avatar_seed"), fallback=str(row["agent_id"])),
        category=_safe_category(row.get("category")),
        visibility=_safe_visibility(row.get("visibility")),
        allowed_department_ids=_safe_string_list(row.get("allowed_department_ids")),
        allowed_roles=_safe_string_list(row.get("allowed_roles")),
        allowed_user_ids=_safe_string_list(row.get("allowed_user_ids")),
        content_hash=str(row["content_hash"]),
        created_at=row.get("created_at"),
        published_at=row.get("published_at"),
    )


class AgentProfileAuthority:
    """Own lifecycle, public discovery, admission, and safe conversation recovery."""

    def _require_admin(self, principal: AuthPrincipal) -> None:
        if not is_ai_admin(principal):
            raise HTTPException(status_code=403, detail="not_ai_admin")

    async def _ensure_principal_user(self, conn, *, principal: AuthPrincipal) -> None:
        """Provision an unseen principal and reject a conflicting tenant identity."""

        try:
            await repositories.ensure_submission_principal(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name or principal.user_id,
            )
        except repositories.RepositoryAuthorizationError as exc:
            raise HTTPException(status_code=403, detail="principal_not_authorized") from exc

    async def _validate_definition(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        definition: AgentProfileDraftRequest,
    ) -> tuple[tuple[dict[str, Any], ...], dict[str, str]]:
        """Revalidate current model, Skill, and MCP authorization for a definition."""

        if definition.avatar_asset_id:
            avatar_asset = await repositories.get_file(
                conn,
                tenant_id=principal.tenant_id,
                file_id=definition.avatar_asset_id,
            )
            if (
                avatar_asset is None
                or str(avatar_asset.get("user_id") or "") != principal.user_id
                or not str(avatar_asset.get("content_type") or "").startswith("image/")
                or int(avatar_asset.get("size_bytes") or 0) <= 0
                or int(avatar_asset.get("size_bytes") or 0) > 5 * 1024 * 1024
            ):
                raise HTTPException(status_code=400, detail="agent_profile_avatar_asset_invalid")
        try:
            model = resolve_model_selection(definition.model_id, get_settings())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="agent_profile_model_not_available") from exc
        try:
            skills = tuple(
                [
                    await repositories.authorize_selected_run_capabilities(
                        conn,
                        tenant_id=principal.tenant_id,
                        agent_id=agent_id,
                        skill_id=selected_skill.skill_id,
                        expected_version=selected_skill.expected_version,
                        rollout_key=principal.user_id,
                        normalized_input={},
                        principal_department_id=principal.department_id,
                        principal_roles=principal.roles,
                        is_admin=is_ai_admin(principal),
                        permissions=principal.permissions,
                    )
                    for selected_skill in definition.skill_set
                ]
            )
            for tool_reference in definition.mcp_tool_ids:
                server_id, _public_tool_name = parse_mcp_tool_reference(tool_reference)
                server = await repositories.get_mcp_server_registry_entry(
                    conn,
                    tenant_id=principal.tenant_id,
                    name=server_id,
                )
                if server is None:
                    raise HTTPException(
                        status_code=403,
                        detail="agent_profile_capability_not_available",
                    )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="agent_profile_mcp_reference_invalid") from exc
        except repositories.RepositoryConflictError as exc:
            raise HTTPException(status_code=409, detail="agent_profile_revision_stale") from exc
        except repositories.RepositoryAuthorizationError as exc:
            raise HTTPException(status_code=403, detail="agent_profile_capability_not_available") from exc
        return skills, model

    async def save_draft(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        definition: AgentProfileDraftRequest,
        agent_id: str | None,
    ) -> tuple[AgentProfileAdminProjection, str]:
        """Append a draft revision and advance the one authoritative profile aggregate."""

        self._require_admin(principal)
        if agent_id is None and definition.expected_draft_revision != 0:
            raise HTTPException(status_code=409, detail="agent_profile_create_revision_invalid")
        if agent_id is not None and definition.expected_draft_revision < 1:
            raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
        resolved_agent_id = agent_id or repositories.new_id("agt")
        await self._ensure_principal_user(conn, principal=principal)
        await repositories.acquire_agent_profile_lifecycle_lock(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=resolved_agent_id,
        )
        if agent_id is not None:
            prior_row = await repositories.get_agent_profile_revision(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=resolved_agent_id,
                revision=definition.expected_draft_revision,
            )
            if prior_row is None:
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            definition = _merge_omitted_profile_fields(definition, prior_row=prior_row)
        if not definition.avatar_seed:
            definition = definition.model_copy(update={"avatar_seed": resolved_agent_id})
        await repositories.ensure_agent_profile_identity(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=resolved_agent_id,
            name=definition.name,
            default_skill_id=definition.skill_set[0].skill_id,
        )
        row = await repositories.create_agent_profile_revision(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=resolved_agent_id,
            status="draft",
            name=definition.name,
            description=definition.description,
            instructions=definition.instructions,
            model_id=definition.model_id,
            skill_id=definition.skill_set[0].skill_id,
            skill_version=definition.skill_set[0].expected_version,
            skill_set=[skill.model_dump(mode="json") for skill in definition.skill_set],
            mcp_tool_ids=definition.mcp_tool_ids,
            welcome_message=definition.welcome_message,
            starter_prompts=definition.starter_prompts,
            capability_summary=definition.capability_summary,
            recommended_tasks=definition.recommended_tasks,
            supported_input_types=list(_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES),
            legacy_supported_file_types=list(_ROLLING_LEGACY_SUPPORTED_FILE_TYPES),
            expected_outputs=definition.expected_outputs,
            permissions_and_data_access_notice=definition.permissions_and_data_access_notice,
            avatar_ref=definition.avatar_ref,
            avatar_asset_id=definition.avatar_asset_id,
            avatar_seed=definition.avatar_seed,
            category=definition.category,
            visibility=definition.visibility,
            allowed_department_ids=definition.allowed_department_ids,
            allowed_roles=definition.allowed_roles,
            allowed_user_ids=definition.allowed_user_ids,
            content_hash=_revision_hash(definition),
            created_by=principal.user_id,
            expected_previous_revision=definition.expected_draft_revision,
        )
        aggregate = await repositories.record_agent_profile_draft(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=resolved_agent_id,
            revision=int(row["revision"]),
        )
        row = {
            **row,
            "published_revision": (
                aggregate.get("published_revision") if aggregate is not None else None
            ),
        }
        audit_id = await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="agent_profile.draft_saved",
            target_type="agent_profile",
            target_id=resolved_agent_id,
            trace_id=standard_trace_id(resolved_agent_id),
            payload_json={"revision": int(row["revision"]), "content_hash": str(row["content_hash"])},
        )
        return _admin_projection(row), audit_id

    async def publish_draft(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        expected_revision: int,
    ) -> tuple[AgentProfileAdminProjection, str]:
        """Publish a revalidated immutable copy and move the aggregate publication pointer."""

        self._require_admin(principal)
        await self._ensure_principal_user(conn, principal=principal)
        await repositories.acquire_agent_profile_lifecycle_lock(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
        draft_row = await repositories.get_agent_profile_revision(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=expected_revision,
            status="draft",
        )
        if draft_row is None:
            raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
        source_content_hash = str(draft_row.get("content_hash") or "")
        self._require_revision_integrity(draft_row)
        definition = _draft_from_row(draft_row)
        await self._validate_definition(conn, principal=principal, agent_id=agent_id, definition=definition)
        row = await repositories.create_agent_profile_revision(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            status="published",
            name=definition.name,
            description=definition.description,
            instructions=definition.instructions,
            model_id=definition.model_id,
            skill_id=definition.skill_set[0].skill_id,
            skill_version=definition.skill_set[0].expected_version,
            skill_set=[skill.model_dump(mode="json") for skill in definition.skill_set],
            mcp_tool_ids=definition.mcp_tool_ids,
            welcome_message=definition.welcome_message,
            starter_prompts=definition.starter_prompts,
            capability_summary=definition.capability_summary,
            recommended_tasks=definition.recommended_tasks,
            supported_input_types=list(_ROLLING_LEGACY_SUPPORTED_INPUT_TYPES),
            legacy_supported_file_types=list(_ROLLING_LEGACY_SUPPORTED_FILE_TYPES),
            expected_outputs=definition.expected_outputs,
            permissions_and_data_access_notice=definition.permissions_and_data_access_notice,
            avatar_ref=definition.avatar_ref,
            avatar_asset_id=definition.avatar_asset_id,
            avatar_seed=definition.avatar_seed or agent_id,
            category=definition.category,
            visibility=definition.visibility,
            allowed_department_ids=definition.allowed_department_ids,
            allowed_roles=definition.allowed_roles,
            allowed_user_ids=definition.allowed_user_ids,
            content_hash=_revision_hash(definition),
            created_by=principal.user_id,
            published_by=principal.user_id,
            expected_previous_revision=expected_revision,
            published_from_revision=expected_revision,
        )
        await repositories.record_agent_profile_publication(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=int(row["revision"]),
            content_hash=str(row["content_hash"]),
        )
        row = {**row, "published_revision": int(row["revision"])}
        audit_id = await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="agent_profile.published",
            target_type="agent_profile",
            target_id=agent_id,
            trace_id=standard_trace_id(agent_id),
            payload_json={
                "revision": int(row["revision"]),
                "content_hash": str(row["content_hash"]),
                "published_from_content_hash": source_content_hash,
            },
        )
        return _admin_projection(row), audit_id

    async def unpublish(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        expected_revision: int,
    ) -> tuple[AgentProfileAdminProjection, str]:
        """Withdraw the current publication while preserving immutable version history."""

        self._require_admin(principal)
        await self._ensure_principal_user(conn, principal=principal)
        await repositories.acquire_agent_profile_lifecycle_lock(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
        aggregate = await repositories.get_agent_profile_aggregate(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            for_update=True,
        )
        if (
            aggregate is None
            or str(aggregate.get("lifecycle_status") or "") != "published"
            or int(aggregate.get("published_revision") or 0) != expected_revision
        ):
            raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
        published_row = await repositories.get_agent_profile_revision(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=expected_revision,
            status="published",
        )
        if published_row is None:
            raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
        latest_revision = int(aggregate["latest_revision"])
        authoring_row = published_row
        if latest_revision != expected_revision:
            authoring_row = await repositories.get_agent_profile_revision(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=agent_id,
                revision=latest_revision,
            )
            if authoring_row is None:
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
        authoring_content_hash = str(authoring_row.get("content_hash") or "")
        self._require_revision_integrity(authoring_row)
        definition = _draft_from_row(authoring_row)
        row = await repositories.create_agent_profile_revision(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            status="withdrawn",
            name=definition.name,
            description=definition.description,
            instructions=definition.instructions,
            model_id=definition.model_id,
            skill_id=definition.skill_set[0].skill_id,
            skill_version=definition.skill_set[0].expected_version,
            skill_set=[skill.model_dump(mode="json") for skill in definition.skill_set],
            mcp_tool_ids=definition.mcp_tool_ids,
            welcome_message=definition.welcome_message,
            starter_prompts=definition.starter_prompts,
            capability_summary=definition.capability_summary,
            recommended_tasks=definition.recommended_tasks,
            supported_input_types=_safe_string_list(
                authoring_row.get("supported_input_types")
            )
            or ["text"],
            legacy_supported_file_types=_safe_string_list(
                authoring_row.get("legacy_supported_file_types")
            ),
            expected_outputs=definition.expected_outputs,
            permissions_and_data_access_notice=definition.permissions_and_data_access_notice,
            avatar_ref=definition.avatar_ref,
            avatar_asset_id=definition.avatar_asset_id,
            # Preserve the persisted value exactly: historical hashes intentionally
            # omit avatar_seed and use an empty value as their schema marker.
            avatar_seed=authoring_row["avatar_seed"],
            category=definition.category,
            visibility=definition.visibility,
            allowed_department_ids=definition.allowed_department_ids,
            allowed_roles=definition.allowed_roles,
            allowed_user_ids=definition.allowed_user_ids,
            content_hash=authoring_content_hash,
            created_by=principal.user_id,
            expected_previous_revision=latest_revision,
            withdrawn_from_revision=expected_revision,
        )
        await repositories.record_agent_profile_withdrawal(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=int(row["revision"]),
        )
        audit_id = await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="agent_profile.unpublished",
            target_type="agent_profile",
            target_id=agent_id,
            trace_id=standard_trace_id(agent_id),
            payload_json={"revision": expected_revision, "withdrawn_revision": int(row["revision"])},
        )
        return _admin_projection({**row, "published_revision": None}), audit_id

    async def validate_draft(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        definition: AgentProfileDraftRequest,
        agent_id: str | None,
    ) -> str:
        """Validate unsaved or saved drafts without creating a revision or execution run."""

        self._require_admin(principal)
        await self._ensure_principal_user(conn, principal=principal)
        validation_agent_id = agent_id
        if validation_agent_id is not None:
            if definition.expected_draft_revision < 1:
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            await repositories.acquire_agent_profile_lifecycle_lock(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=validation_agent_id,
            )
            aggregate = await repositories.get_agent_profile_aggregate(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=validation_agent_id,
                for_update=True,
            )
            if (
                aggregate is None
                or int(aggregate.get("latest_revision") or 0)
                != definition.expected_draft_revision
            ):
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            prior_row = await repositories.get_agent_profile_revision(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=validation_agent_id,
                revision=definition.expected_draft_revision,
                status="draft",
            )
            if prior_row is None:
                raise HTTPException(status_code=409, detail="agent_profile_revision_stale")
            definition = _merge_omitted_profile_fields(definition, prior_row=prior_row)
        if validation_agent_id is None:
            validation_agent_id = await repositories.get_tenant_profile_validation_agent(
                conn,
                tenant_id=principal.tenant_id,
            )
        if not validation_agent_id:
            raise HTTPException(status_code=409, detail="agent_profile_validation_unavailable")
        await self._validate_definition(
            conn,
            principal=principal,
            agent_id=validation_agent_id,
            definition=definition,
        )
        return await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="agent_profile.draft_validated",
            target_type="agent_profile",
            target_id=agent_id or "draft-preview",
            trace_id=standard_trace_id(agent_id or "draft-preview"),
            payload_json={"content_hash": _revision_hash(definition)},
        )

    async def list_admin(self, conn, *, principal: AuthPrincipal) -> list[AgentProfileAdminProjection]:
        """List latest same-tenant revisions for the Builder without public redaction."""

        self._require_admin(principal)
        rows = await repositories.list_latest_agent_profile_revisions(conn, tenant_id=principal.tenant_id)
        return [_admin_projection(row) for row in rows]

    async def list_history(self, conn, *, principal: AuthPrincipal, agent_id: str) -> list[AgentProfileAdminProjection]:
        """Return the immutable same-tenant lifecycle history for one profile identity."""

        self._require_admin(principal)
        rows = await repositories.list_agent_profile_revision_history(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
        return [_admin_projection(row) for row in rows]

    async def list_public(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        query: str | None = None,
        category: str | None = None,
    ) -> list[AgentProfilePublicProjection]:
        """Return only current published profiles visible and usable by this principal."""

        rows = await repositories.list_current_published_agent_profiles(
            conn,
            tenant_id=principal.tenant_id,
            query=query,
            category=category,
        )
        visible: list[AgentProfilePublicProjection] = []
        for row in rows:
            try:
                await self._authorize_public_row(conn, principal=principal, row=row)
            except HTTPException:
                continue
            visible.append(AgentProfilePublicProjection.model_validate(profile_public_projection(row)))
        return visible

    async def get_public(self, conn, *, principal: AuthPrincipal, agent_id: str) -> AgentProfilePublicProjection:
        """Return public detail only when it passes the same list authorization path."""

        row = await repositories.get_current_published_agent_profile(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="agent_profile_not_found")
        try:
            await self._authorize_public_row(conn, principal=principal, row=row)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="agent_profile_not_found") from exc
        return AgentProfilePublicProjection.model_validate(profile_public_projection(row))

    async def _authorize_public_row(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        row: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        self._require_revision_integrity(row)
        if not profile_acl_allows(row, principal=principal):
            raise HTTPException(status_code=403, detail="agent_profile_not_authorized")
        return await self._validate_definition(
            conn,
            principal=principal,
            agent_id=str(row["agent_id"]),
            definition=_draft_from_row(row),
        )

    async def resolve_for_admission(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        selection: SelectedAgentProfileRequest,
        submitted_request: ChatStreamRequest | None = None,
        query_agent_id: str | None = None,
    ) -> AgentProfileAdmission:
        """Lock and reauthorize exactly the current published revision for one submission."""

        row = await repositories.get_current_published_agent_profile(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=selection.agent_id,
            expected_revision=selection.expected_revision,
            for_update=True,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="agent_profile_not_available")
        admission = await self._admission_from_row(conn, principal=principal, row=row)
        if submitted_request is not None:
            self.reject_profile_selector_conflicts(
                submitted_request,
                active=True,
                query_agent_id=query_agent_id,
                admission=admission,
                allow_default_query_agent=True,
            )
        return admission

    async def resolve_bound_for_submission(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        revision: int,
        content_hash: str,
        submitted_request: ChatStreamRequest | None = None,
        query_agent_id: str | None = None,
    ) -> AgentProfileAdmission:
        """Reauthorize a conversation's immutable publication while its Agent is live."""

        row = await repositories.get_bound_published_agent_profile(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            revision=revision,
            content_hash=content_hash,
            for_update=True,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="agent_profile_not_available")
        current_row = await repositories.get_current_published_agent_profile(
            conn,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
        )
        if current_row is None:
            raise HTTPException(status_code=409, detail="agent_profile_not_available")
        admission = await self._admission_from_row(
            conn,
            principal=principal,
            row=row,
            acl_row=current_row,
        )
        if submitted_request is not None:
            self.reject_profile_selector_conflicts(
                submitted_request,
                active=True,
                query_agent_id=query_agent_id,
                admission=admission,
            )
        return admission

    async def resolve_bound_for_worker_dispatch(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        agent_id: str,
        revision: int,
        content_hash: str,
    ) -> AgentProfileAdmission | None:
        """Reauthorize a pinned Profile for dispatch without leaking HTTP errors."""

        try:
            row = await repositories.get_bound_published_agent_profile(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=agent_id,
                revision=revision,
                content_hash=content_hash,
                for_update=True,
            )
            if row is None:
                return None
            current_row = await repositories.get_current_published_agent_profile(
                conn,
                tenant_id=principal.tenant_id,
                agent_id=agent_id,
            )
            if current_row is None:
                return None
            return await self._admission_from_row(
                conn,
                principal=principal,
                row=row,
                acl_row=current_row,
            )
        except (HTTPException, KeyError, TypeError, ValueError):
            return None

    async def _admission_from_row(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        row: dict[str, Any],
        acl_row: dict[str, Any] | None = None,
    ) -> AgentProfileAdmission:
        """Reauthorize current capabilities and build private/public admission views."""

        self._require_revision_integrity(row)
        current_acl_row = acl_row or row
        if current_acl_row is not row:
            self._require_revision_integrity(current_acl_row)
        if not profile_acl_allows(current_acl_row, principal=principal):
            raise HTTPException(status_code=403, detail="agent_profile_not_authorized")
        validated_skills, model = await self._validate_definition(
            conn,
            principal=principal,
            agent_id=str(row["agent_id"]),
            definition=_draft_from_row(row),
        )
        skills = (
            validated_skills
            if isinstance(validated_skills, tuple)
            else (validated_skills,)
        )
        agent_id = str(row["agent_id"])
        revision = int(row["revision"])
        content_hash = str(row["content_hash"])
        configured_mcp_tool_ids, effective_mcp_tool_ids = _effective_mcp_tool_ids(
            row,
            skills=skills,
        )
        return AgentProfileAdmission(
            agent_id=agent_id,
            revision=revision,
            content_hash=content_hash,
            skill=skills[0],
            skills=skills,
            model=model,
            mcp_tool_ids=effective_mcp_tool_ids,
            private_execution_input={
                "agent_id": agent_id,
                "revision": revision,
                "content_hash": content_hash,
                "instructions": str(row["instructions"]),
                "skill_set": [skill.model_dump(mode="json") for skill in _skill_set(row)],
            },
            public_identity=conversation_identity_projection(row),
            configured_mcp_tool_ids=configured_mcp_tool_ids,
        )

    @staticmethod
    def _require_revision_integrity(row: dict[str, Any]) -> None:
        content_hash = str(row.get("content_hash") or "")
        try:
            matches = _revision_hash_matches(row, content_hash)
        except (HTTPException, KeyError, TypeError, ValueError):
            matches = False
        if not matches:
            raise HTTPException(
                status_code=409,
                detail="agent_profile_revision_integrity_mismatch",
            )

    async def reauthorize_pinned_run_for_replay(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        run_id: str,
    ) -> None:
        """Reauthorize one persisted profile run before copy, retry, or resume side effects."""

        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise repositories.RepositoryNotFoundError("run_not_found")
        input_json = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
        snapshot = repositories.copied_run_execution_snapshot(input_json)
        revision, content_hash = repositories.admitted_agent_profile_pins_for_copy(run, snapshot)
        if revision is None:
            return
        admission = await self.resolve_bound_for_submission(
            conn,
            principal=principal,
            agent_id=str(run.get("agent_id") or ""),
            revision=revision,
            content_hash=str(content_hash or ""),
        )
        profile_snapshot = snapshot.get("agent_profile")
        execution_input = snapshot.get("input") if isinstance(snapshot.get("input"), dict) else {}
        try:
            execution_mcp_tool_ids = tuple(repositories.extract_run_mcp_tool_ids(execution_input))
        except (
            repositories.RepositoryAuthorizationError,
            repositories.RepositoryConflictError,
        ) as exc:
            raise repositories.RepositoryConflictError("agent_profile_snapshot_invalid") from exc
        expected_profile_snapshot = dict(admission.private_execution_input)
        snapshot_skill_version = str(snapshot.get("skill_version") or "")
        authority_skill_id = str(admission.skill.get("skill_id") or "")
        governed_profile_snapshot = isinstance(profile_snapshot, dict) and (
            isinstance(profile_snapshot.get("skill_set"), list)
            or "required_skill_id" in profile_snapshot
            or "required_skill_version" in profile_snapshot
        )
        governed_mcp_tool_ids: tuple[str, ...] | None = None
        if governed_profile_snapshot:
            try:
                skill_manifests = await repositories.materialize_run_skill_manifests(
                    conn,
                    tenant_id=principal.tenant_id,
                    run_id=run_id,
                    skill_manifest_refs=(
                        snapshot["skill_manifests"]
                        if "skill_manifests" in snapshot
                        else []
                    ),
                )
            except repositories.RepositoryConflictError as exc:
                raise repositories.RepositoryConflictError(
                    "agent_profile_snapshot_invalid"
                ) from exc
            if isinstance(profile_snapshot, dict) and (
                "required_skill_id" in profile_snapshot
                or "required_skill_version" in profile_snapshot
            ):
                expected_profile_snapshot.pop("skill_set", None)
                expected_profile_snapshot.update(
                    {
                        "required_skill_id": authority_skill_id,
                        "required_skill_version": snapshot_skill_version,
                    }
                )
            primary_manifest = next(
                (
                    manifest
                    for manifest in skill_manifests
                    if isinstance(manifest, dict)
                    and str(manifest.get("skill_id") or "") == authority_skill_id
                ),
                None,
            )
            manifest_skill_version = (
                str(primary_manifest.get("content_hash") or primary_manifest.get("version") or "")
                if isinstance(primary_manifest, dict)
                else ""
            )
            release_decision = snapshot.get("release_decision")
            release_skill_version = (
                str(release_decision.get("selected_version") or "")
                if isinstance(release_decision, dict)
                else ""
            )
            manifest_versions = {
                str(manifest.get("skill_id") or ""): str(
                    manifest.get("content_hash") or manifest.get("version") or ""
                )
                for manifest in skill_manifests
                if isinstance(manifest, dict)
            }
            authority_skill_versions = (
                {
                    str(skill.get("skill_id") or ""): str(skill.get("skill_version") or "")
                    for skill in admission.skills
                }
                if isinstance(profile_snapshot, dict)
                and isinstance(profile_snapshot.get("skill_set"), list)
                else {}
            )
            try:
                repositories.require_replay_source_identity(
                    pinned_version=snapshot_skill_version,
                    pinned_executor_type=str(snapshot.get("executor_type") or ""),
                    release_decision=release_decision if isinstance(release_decision, dict) else {},
                    skill_manifests=skill_manifests,
                )
                governed_mcp_tool_ids = tuple(
                    await repositories.validate_replay_skill_manifests(
                        conn,
                        skill_id=authority_skill_id,
                        pinned_version=snapshot_skill_version,
                        pinned_executor_type=str(snapshot.get("executor_type") or ""),
                        skill_manifests=skill_manifests,
                        skill_set=(
                            profile_snapshot.get("skill_set")
                            if isinstance(profile_snapshot, dict)
                            and isinstance(profile_snapshot.get("skill_set"), list)
                            else None
                        ),
                    )
                )
            except (
                repositories.RepositoryAuthorizationError,
                repositories.RepositoryConflictError,
            ) as exc:
                raise repositories.RepositoryConflictError("agent_profile_snapshot_invalid") from exc
            skill_version_matches = (
                bool(snapshot_skill_version)
                and snapshot_skill_version == manifest_skill_version == release_skill_version
                and all(
                    manifest_versions.get(skill_id) == version
                    for skill_id, version in authority_skill_versions.items()
                )
                and governed_mcp_tool_ids == admission.mcp_tool_ids
            )
        else:
            skill_version_matches = snapshot_skill_version == str(
                admission.skill.get("skill_version") or ""
            )
        if (
            profile_snapshot != expected_profile_snapshot
            or str(run.get("skill_id") or "") != str(admission.skill.get("skill_id") or "")
            or not skill_version_matches
            or str(snapshot.get("executor_type") or "")
            != str(admission.skill.get("executor_type") or "")
            or str(snapshot.get("model_id") or "") != str(admission.model.get("id") or "")
            or str(snapshot.get("model_value") or "") != str(admission.model.get("value") or "")
            or execution_mcp_tool_ids != admission.mcp_tool_ids
        ):
            raise repositories.RepositoryConflictError("agent_profile_snapshot_invalid")

    async def create_conversation(
        self,
        conn,
        *,
        principal: AuthPrincipal,
        workspace_id: str,
        selection: SelectedAgentProfileRequest,
        title: str,
        session_id: str | None = None,
        purpose: str = "conversation",
        operation_id: UUID | None = None,
    ) -> ChatSessionResponse:
        """Atomically bind a new conversation to one current published profile revision/hash."""

        if purpose not in {"conversation", "builder_test"}:
            raise HTTPException(status_code=400, detail="agent_conversation_purpose_invalid")
        if purpose == "builder_test":
            self._require_admin(principal)
        if operation_id is not None and (purpose != "conversation" or session_id is not None):
            raise HTTPException(status_code=400, detail="agent_conversation_operation_invalid")
        await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
        await self._ensure_principal_user(conn, principal=principal)
        if operation_id is not None:
            session_id = f"ses_agent_{operation_id.hex}"
            existing = await repositories.get_authorized_session_projection(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=session_id,
            )
            if existing is not None:
                response = session_response(existing)
                expected_title = title or (
                    response.agent_conversation.name if response.agent_conversation is not None else ""
                )
                if (
                    str(existing.get("workspace_id") or "") != workspace_id
                    or str(existing.get("agent_id") or "") != selection.agent_id
                    or response.agent_conversation is None
                    or response.agent_conversation.revision != selection.expected_revision
                    or response.purpose != purpose
                    or response.title != expected_title
                ):
                    raise repositories.RepositoryConflictError("agent_conversation_operation_conflict")
                return response
        admission = await self.resolve_for_admission(conn, principal=principal, selection=selection)
        resolved_title = title or admission.public_identity.name
        create_session_kwargs: dict[str, Any] = {
            "tenant_id": principal.tenant_id,
            "workspace_id": workspace_id,
            "user_id": principal.user_id,
            "agent_id": admission.agent_id,
            "title": resolved_title,
            "admitted_agent_profile_revision": admission.revision,
            "admitted_agent_profile_hash": admission.content_hash,
        }
        if session_id is not None:
            create_session_kwargs["session_id"] = session_id
            create_session_kwargs["return_created"] = True
        if purpose != "conversation":
            create_session_kwargs["purpose"] = purpose
        created = True
        created_session = await repositories.create_session(conn, **create_session_kwargs)
        if session_id is not None:
            session_id, created = created_session
        else:
            session_id = created_session
        if created:
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action=(
                    "agent_profile.test_conversation_created"
                    if purpose == "builder_test"
                    else "agent_conversation.created"
                ),
                target_type="agent_profile",
                target_id=admission.agent_id,
                trace_id=standard_trace_id(session_id),
                payload_json={
                    "revision": admission.revision,
                    "session_id": session_id,
                    "purpose": purpose,
                },
            )
        return ChatSessionResponse(
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=admission.agent_id,
            title=resolved_title,
            purpose=purpose,
            agent_conversation=admission.public_identity,
        )

    @staticmethod
    def reject_profile_selector_conflicts(
        request: ChatStreamRequest,
        *,
        active: bool | None = None,
        query_agent_id: str | None = None,
        admission: AgentProfileAdmission | None = None,
        allow_default_query_agent: bool = False,
    ) -> None:
        """Accept only transport values that cannot alter a resolved profile admission."""

        if active is None:
            active = request.selected_agent_profile is not None
        if not active:
            return
        selector_paths = set(request.profile_capability_selector_paths())
        if admission is None:
            if query_agent_id is not None or selector_paths:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            return

        allowed_query_agent_ids = {admission.agent_id}
        if allow_default_query_agent:
            allowed_query_agent_ids.add("general-agent")
        if query_agent_id is not None and query_agent_id not in allowed_query_agent_ids:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if selector_paths - _PROFILE_TRANSPORT_SELECTOR_PATHS:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")

        submitted_fields = request.model_fields_set
        if "agent_options" in submitted_fields:
            if not isinstance(request.agent_options, dict):
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            if set(request.agent_options) - _PROFILE_TRANSPORT_AGENT_OPTION_KEYS:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            if request.agent_options.get("enable_thinking", "off") != "off":
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            submitted_model_id = request.agent_options.get("model_id")
            if submitted_model_id is not None and submitted_model_id != admission.model["id"]:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
            submitted_model_value = request.agent_options.get("model")
            if submitted_model_value is not None and submitted_model_value != admission.model["value"]:
                raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "disabled_skills" in submitted_fields and request.disabled_skills:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "enabled_skills" in submitted_fields and request.enabled_skills:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "disabled_mcp_tools" in submitted_fields and request.disabled_mcp_tools:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "selected_agent_profile" in submitted_fields and (
            request.selected_agent_profile is None
            or request.selected_agent_profile.agent_id != admission.agent_id
            or request.selected_agent_profile.expected_revision != admission.revision
        ):
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")
        if "selected_mcp_tool_ids" in submitted_fields and request.selected_mcp_tool_ids:
            raise HTTPException(status_code=400, detail="agent_profile_selector_conflict")


def reject_profile_selector_conflicts(
    request: ChatStreamRequest,
    *,
    active: bool | None = None,
    query_agent_id: str | None = None,
) -> None:
    """Compatibility delegate for the authoritative profile selector policy."""

    AgentProfileAuthority.reject_profile_selector_conflicts(
        request,
        active=active,
        query_agent_id=query_agent_id,
    )
