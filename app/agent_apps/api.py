from types import SimpleNamespace

from app.agent_apps.application.skill_set_pinning import pin_agent_skill_set
from app.agent_apps.domain.profile_definition import (
    discard_legacy_agent_profile_model_id,
    normalize_agent_avatar_seed,
    normalize_agent_profile_display_items,
    normalize_agent_skill_set as _normalize_agent_skill_set,
    safe_agent_avatar_seed,
)
from app.skills.api import is_internal_dependency_skill


def agent_profile_contracts():
    from pydantic import Field, field_validator

    from app.models import (
        AgentProfileAdminListResponse as LegacyAgentProfileAdminListResponse,
        AgentProfileAdminProjection as LegacyAgentProfileAdminProjection,
        AgentProfileCatalogResponse as LegacyAgentProfileCatalogResponse,
        AgentProfileDraftRequest as LegacyAgentProfileDraftRequest,
        AgentProfileDraftTestRequest as LegacyAgentProfileDraftTestRequest,
        AgentProfileHistoryResponse as LegacyAgentProfileHistoryResponse,
        AgentProfileMutationResponse as LegacyAgentProfileMutationResponse,
        AgentProfilePublicProjection as LegacyAgentProfilePublicProjection,
    )

    class AgentProfileDraftRequest(LegacyAgentProfileDraftRequest):
        market_tag: str = Field(default="", max_length=80)

        @field_validator("market_tag")
        @classmethod
        def normalize_market_tag(cls, value: str) -> str:
            normalized = value.strip()
            if not normalized:
                return ""
            return normalize_agent_profile_display_items(
                [normalized], "market_tag", item_limit=80
            )[0]

    class AgentProfileDraftTestRequest(LegacyAgentProfileDraftTestRequest):
        definition: AgentProfileDraftRequest

    class AgentProfilePublicProjection(LegacyAgentProfilePublicProjection):
        market_tag: str = ""
        is_favorite: bool = False

    class AgentProfileAdminProjection(LegacyAgentProfileAdminProjection):
        market_tag: str = ""

    class AgentProfileCatalogResponse(LegacyAgentProfileCatalogResponse):
        agent_profiles: list[AgentProfilePublicProjection] = Field(default_factory=list)

    class AgentProfileAdminListResponse(LegacyAgentProfileAdminListResponse):
        agent_profiles: list[AgentProfileAdminProjection] = Field(default_factory=list)

    class AgentProfileMutationResponse(LegacyAgentProfileMutationResponse):
        agent_profile: AgentProfileAdminProjection

    class AgentProfileHistoryResponse(LegacyAgentProfileHistoryResponse):
        agent_profiles: list[AgentProfileAdminProjection] = Field(default_factory=list)

    return SimpleNamespace(
        AgentProfileAdminListResponse=AgentProfileAdminListResponse,
        AgentProfileAdminProjection=AgentProfileAdminProjection,
        AgentProfileCatalogResponse=AgentProfileCatalogResponse,
        AgentProfileDraftRequest=AgentProfileDraftRequest,
        AgentProfileDraftTestRequest=AgentProfileDraftTestRequest,
        AgentProfileHistoryResponse=AgentProfileHistoryResponse,
        AgentProfileMutationResponse=AgentProfileMutationResponse,
        AgentProfilePublicProjection=AgentProfilePublicProjection,
    )


def normalize_agent_skill_set(skill_set, selected_skill):
    return _normalize_agent_skill_set(
        skill_set,
        selected_skill,
        is_internal_dependency_skill,
    )

__all__ = [
    "agent_profile_contracts",
    "discard_legacy_agent_profile_model_id",
    "normalize_agent_avatar_seed",
    "normalize_agent_profile_display_items",
    "normalize_agent_skill_set",
    "pin_agent_skill_set",
    "safe_agent_avatar_seed",
]
