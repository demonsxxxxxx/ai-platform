"""Composition root for the External Knowledge control plane."""

from fastapi import APIRouter

from app.auth import PLATFORM_ROLE_TAXONOMY, is_ai_admin, require_principal
from app.db import transaction
from app.department_directory import (
    DepartmentDirectoryError,
    fetch_department_directory,
    validate_distribution_department_authorities,
)
from app.knowledge.application import (
    AgentProfileKnowledgeAuthorizationService,
    KnowledgeControlPlane,
    KnowledgeProviderPermitPool,
    KnowledgeRuntimeService,
    RunKnowledgeAdmissionService,
    configure_agent_profile_knowledge_authorization,
    configure_knowledge_control_plane,
    configure_knowledge_runtime,
    configure_run_knowledge_admission,
)
from app.knowledge.domain import KnowledgeError
from app.identity.api import list_active_user_ids
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


async def _validate_knowledge_role_authorities(
    _tenant_id: str,
    values: list[str],
) -> list[str]:
    if any(value not in PLATFORM_ROLE_TAXONOMY for value in values):
        raise KnowledgeError("knowledge_source_acl_identity_invalid")
    return values


async def _validate_knowledge_user_authorities(
    tenant_id: str,
    values: list[str],
) -> list[str]:
    try:
        async with transaction() as conn:
            resolved = await list_active_user_ids(
                conn,
                tenant_id=tenant_id,
                user_ids=values,
            )
    except Exception as exc:
        raise KnowledgeError(
            "knowledge_source_acl_identity_authority_unavailable"
        ) from exc
    if tuple(values) != resolved:
        raise KnowledgeError("knowledge_source_acl_identity_invalid")
    return list(resolved)


def configure_knowledge_services() -> None:
    configure_agent_profile_knowledge_authorization(
        AgentProfileKnowledgeAuthorizationService(
            PostgresAgentProfileKnowledgeAuthorizationRepository()
        )
    )
    runtime_repository = PostgresKnowledgeRuntimeRepository()
    credential_vault = KnowledgeCredentialVault(
        PlatformCredentialVault(settings_provider=get_settings)
    )
    ragflow_provider = RagFlowKnowledgeProvider(settings_provider=get_settings)
    audit_writer = PostgresAuditWriter()
    configure_run_knowledge_admission(
        RunKnowledgeAdmissionService(runtime_repository)
    )
    configure_knowledge_runtime(
        KnowledgeRuntimeService(
            transaction_factory=transaction,
            repository=runtime_repository,
            credential_vault=credential_vault,
            audit_writer=audit_writer,
            providers=(ragflow_provider,),
            permit_pool=KnowledgeProviderPermitPool(
                get_settings().knowledge_provider_max_concurrency_per_connection
            ),
        )
    )
    configure_knowledge_control_plane(
        KnowledgeControlPlane(
            transaction_factory=transaction,
            settings_provider=get_settings,
            repository=PostgresKnowledgeRepository(),
            credential_vault=credential_vault,
            audit_writer=audit_writer,
            providers=(ragflow_provider,),
            department_authority_validator=_validate_knowledge_department_authorities,
            role_authority_validator=_validate_knowledge_role_authorities,
            user_authority_validator=_validate_knowledge_user_authorities,
        )
    )


def build_knowledge_router() -> APIRouter:
    return build_knowledge_admin_router(
        principal_dependency=require_principal,
        is_admin=is_ai_admin,
    )
