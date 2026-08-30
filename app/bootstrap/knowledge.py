"""Composition root for the External Knowledge control plane."""

from fastapi import APIRouter

from app.auth import is_ai_admin, require_principal
from app.db import transaction
from app.department_directory import fetch_department_directory
from app.knowledge.application import KnowledgeControlPlane, configure_knowledge_control_plane
from app.knowledge.infrastructure import PostgresKnowledgeRepository
from app.knowledge.infrastructure.providers import RagFlowCatalogProvider
from app.knowledge.transport import build_knowledge_admin_router
from app.platform.credentials import PlatformCredentialVault
from app.platform.audit import PostgresAuditWriter
from app.settings import get_settings


def configure_knowledge_services() -> None:
    configure_knowledge_control_plane(
        KnowledgeControlPlane(
            transaction_factory=transaction,
            settings_provider=get_settings,
            repository=PostgresKnowledgeRepository(),
            credential_vault=PlatformCredentialVault(settings_provider=get_settings),
            audit_writer=PostgresAuditWriter(),
            providers=(RagFlowCatalogProvider(settings_provider=get_settings),),
            department_directory_provider=fetch_department_directory,
        )
    )


def build_knowledge_router() -> APIRouter:
    return build_knowledge_admin_router(
        principal_dependency=require_principal,
        is_admin=is_ai_admin,
    )
