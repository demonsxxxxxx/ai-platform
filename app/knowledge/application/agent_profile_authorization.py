"""Application boundary for server-resolved Agent Knowledge bindings."""

from __future__ import annotations

from typing import Any, Protocol


class AgentProfileKnowledgeAuthorizationRepository(Protocol):
    async def authorize(self, conn: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]: ...


class AgentProfileKnowledgeAuthorizationService:
    def __init__(self, repository: AgentProfileKnowledgeAuthorizationRepository) -> None:
        self._repository = repository

    async def authorize(self, conn: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
        return await self._repository.authorize(conn, **kwargs)


_service: AgentProfileKnowledgeAuthorizationService | None = None


def configure_agent_profile_knowledge_authorization(
    service: AgentProfileKnowledgeAuthorizationService,
) -> None:
    global _service
    _service = service


def _configured_service() -> AgentProfileKnowledgeAuthorizationService:
    if _service is None:
        raise RuntimeError("agent_profile_knowledge_authorization_not_configured")
    return _service


async def authorize_agent_profile_knowledge_sources(
    conn: Any,
    **kwargs: Any,
) -> tuple[dict[str, Any], ...]:
    return await _configured_service().authorize(conn, **kwargs)


__all__ = [
    "AgentProfileKnowledgeAuthorizationService",
    "authorize_agent_profile_knowledge_sources",
    "configure_agent_profile_knowledge_authorization",
]
