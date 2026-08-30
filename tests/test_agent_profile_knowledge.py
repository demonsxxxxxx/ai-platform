from __future__ import annotations

from typing import Any

import pytest

from app.knowledge.api import (
    AgentProfileKnowledgeAuthorizationService,
    authorize_agent_profile_knowledge_sources as authorize_via_public_boundary,
    configure_agent_profile_knowledge_authorization,
)
from app.knowledge.infrastructure.agent_profiles import (
    PostgresAgentProfileKnowledgeAuthorizationRepository,
    authorize_agent_profile_knowledge_sources,
)
from app.models import AgentProfileDraftRequest
from app.platform.postgres.errors import (
    RepositoryAuthorizationError,
    RepositoryConflictError,
)


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def execute(self, _sql: str, _params: tuple[Any, ...]) -> _Cursor:
        return _Cursor(self.rows)


def _source_row(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "ks_finance",
        "authorization_version": 3,
        "status": "active",
        "connection_status": "active",
        "visibility": "restricted",
        "allowed_department_ids": ["dept-finance"],
        "allowed_roles": [],
        "allowed_user_ids": [],
        **overrides,
    }


def _draft(**overrides: Any) -> AgentProfileDraftRequest:
    return AgentProfileDraftRequest(
        name="财务专家",
        instructions="Use governed sources.",
        skill_set=[{"skill_id": "general-chat", "expected_version": "1.0.0"}],
        expected_draft_revision=0,
        **overrides,
    )


def test_agent_profile_request_rejects_duplicate_and_ninth_knowledge_source() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        _draft(
            knowledge_source_ids=["ks_a", "ks_a"],
            retrieval_profile_id="krp_default",
        )
    with pytest.raises(ValueError):
        _draft(
            knowledge_source_ids=[f"ks_{index}" for index in range(9)],
            retrieval_profile_id="krp_default",
        )


@pytest.mark.asyncio
async def test_agent_profile_authority_uses_the_configured_knowledge_public_boundary() -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    class Repository:
        async def authorize(self, conn: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
            calls.append((conn, kwargs))
            return ({"source_id": "ks_finance"},)

    configured = AgentProfileKnowledgeAuthorizationService(Repository())
    configure_agent_profile_knowledge_authorization(configured)
    try:
        connection = object()
        result = await authorize_via_public_boundary(
            connection,
            tenant_id="default",
            source_ids=["ks_finance"],
        )
    finally:
        configure_agent_profile_knowledge_authorization(
            AgentProfileKnowledgeAuthorizationService(
                PostgresAgentProfileKnowledgeAuthorizationRepository()
            )
        )

    assert result == ({"source_id": "ks_finance"},)
    assert calls == [
        (
            connection,
            {"tenant_id": "default", "source_ids": ["ks_finance"]},
        )
    ]


@pytest.mark.asyncio
async def test_authorized_builder_binding_returns_ordered_server_versions() -> None:
    bindings = await authorize_agent_profile_knowledge_sources(
        _Connection([_source_row()]),
        tenant_id="default",
        source_ids=["ks_finance"],
        retrieval_profile_id="krp_default",
        principal_user_id="admin-a",
        principal_department_id="dept-admin",
        principal_roles=["ai_admin"],
        is_admin=True,
        agent_visibility="restricted",
        agent_department_ids=["dept-finance"],
        agent_roles=[],
        agent_user_ids=[],
    )

    assert bindings == (
        {
            "source_id": "ks_finance",
            "source_authorization_version": 3,
            "ordinal": 0,
            "required": True,
            "retrieval_profile_id": "krp_default",
            "retrieval_profile_revision": 1,
        },
    )


@pytest.mark.asyncio
async def test_builder_rejects_agent_scope_wider_than_the_source_even_for_admin() -> None:
    with pytest.raises(
        RepositoryConflictError,
        match="agent_profile_knowledge_scope_incompatible",
    ):
        await authorize_agent_profile_knowledge_sources(
            _Connection([_source_row()]),
            tenant_id="default",
            source_ids=["ks_finance"],
            retrieval_profile_id="krp_default",
            principal_user_id="admin-a",
            principal_department_id="dept-admin",
            principal_roles=["ai_admin"],
            is_admin=True,
            agent_visibility="tenant",
            agent_department_ids=[],
            agent_roles=[],
            agent_user_ids=[],
        )


@pytest.mark.asyncio
async def test_run_principal_must_still_be_allowed_by_the_current_source_acl() -> None:
    with pytest.raises(
        RepositoryAuthorizationError,
        match="agent_profile_knowledge_source_not_authorized",
    ):
        await authorize_agent_profile_knowledge_sources(
            _Connection([_source_row()]),
            tenant_id="default",
            source_ids=["ks_finance"],
            retrieval_profile_id="krp_default",
            principal_user_id="employee-a",
            principal_department_id="dept-engineering",
            principal_roles=["user"],
            is_admin=False,
            agent_visibility="restricted",
            agent_department_ids=["dept-finance"],
            agent_roles=[],
            agent_user_ids=[],
        )
