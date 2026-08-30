"""Composition root for the External Knowledge control plane."""

from fastapi import APIRouter

from app.auth import is_ai_admin, require_principal
from app.db import transaction
from app.department_directory import (
    DepartmentDirectoryError,
    fetch_department_directory,
    validate_distribution_department_authorities,
)
from app.knowledge.application import (
    AgentProfileKnowledgeAuthorizationService,
    KnowledgeControlPlane,
    RunKnowledgeAdmissionService,
    configure_agent_profile_knowledge_authorization,
    configure_knowledge_control_plane,
    configure_run_knowledge_admission,
)
from app.knowledge.domain import KnowledgeError
from app.knowledge.infrastructure import (
    KnowledgeCredentialVault,
    PostgresAgentProfileKnowledgeAuthorizationRepository,
    PostgresKnowledgeRepository,
    PostgresKnowledgeRuntimeRepository,
)
from app.knowledge.infrastructure.providers import RagFlowKnowledgeProvider
from app.knowledge.transport import build_knowledge_admin_router
from app.platform.audit import PostgresAuditWriter
from app.platform.credentials import PlatformCredentialVault
from app.settings import get_settings


async def _validate_knowledge_department_authorities(values: list[str]) -> list[str]:
    try:
        directory = await fetch_department_directory()
        return validate_distribution_department_authorities(values, directory)
    except DepartmentDirectoryError as exc:
        code = str(exc)
        if code == "capability_distribution_department_authority_invalid":
            raise KnowledgeError("knowledge_source_acl_identity_invalid") from exc
        raise KnowledgeError("knowledge_source_acl_identity_authority_unavailable") from exc


def configure_knowledge_services() -> None:
    configure_agent_profile_knowledge_authorization(
        AgentProfileKnowledgeAuthorizationService(
            PostgresAgentProfileKnowledgeAuthorizationRepository()
        )
    )
    configure_run_knowledge_admission(
        RunKnowledgeAdmissionService(PostgresKnowledgeRuntimeRepository())
    )
    configure_knowledge_control_plane(
        KnowledgeControlPlane(
            transaction_factory=transaction,
            settings_provider=get_settings,
            repository=PostgresKnowledgeRepository(),
            credential_vault=KnowledgeCredentialVault(
                PlatformCredentialVault(settings_provider=get_settings)
            ),
            audit_writer=PostgresAuditWriter(),
            providers=(RagFlowKnowledgeProvider(settings_provider=get_settings),),
            department_authority_validator=_validate_knowledge_department_authorities,
        )
    )


def build_knowledge_router() -> APIRouter:
    return build_knowledge_admin_router(
        principal_dependency=require_principal,
        is_admin=is_ai_admin,
    )
