from pydantic import Field, field_validator

from app.agent_apps.domain.profile_definition import normalize_agent_profile_display_items
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
    """Agent Profile request contract owned by the Agent Apps API."""

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
