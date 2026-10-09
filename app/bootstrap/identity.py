from fastapi import APIRouter

from app.auth import is_ai_admin, require_principal
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityAccessDecision,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    resolve_capability_access,
)
from app.control_plane_contracts import sanitize_public_text
from app.db import transaction
from app.identity.api import (
    AdminUserDiagnosticsService,
    ProfileMetadataService,
    WorkerCapabilityAuditService,
    WorkerDistributionAuthority,
)
from app.identity.infrastructure import audit_postgres as identity_audit_postgres
from app.identity.infrastructure import capability_distributions_postgres as identity_distribution_postgres
from app.platform.postgres import errors as platform_errors
from app.identity.infrastructure.admin_user_diagnostics_postgres import (
    PostgresAdminUserDiagnosticsStore,
)
from app.identity.infrastructure.postgres import PostgresProfileMetadataStore
from app.identity.transport.admin_users import build_admin_users_router as build_router
from app.identity.transport.profile import build_profile_router


def build_worker_distribution_authority() -> WorkerDistributionAuthority:
    return WorkerDistributionAuthority(
        get_distribution=identity_distribution_postgres.get_capability_distribution_row,
        context_type=CapabilityAccessContext,
        subject_type=CapabilityDistributionSubject,
        decision_type=CapabilityAccessDecision,
        resolve_access=resolve_capability_access,
        is_admin=is_ai_admin,
        conflict_error=platform_errors.RepositoryConflictError,
    )


def build_worker_capability_audit_service() -> WorkerCapabilityAuditService:
    return WorkerCapabilityAuditService(
        append_audit=identity_audit_postgres.append_audit_log,
        distribution_audit_payload=capability_distribution_audit_payload,
    )


def build_identity_profile_router() -> APIRouter:
    return build_profile_router(
        service=ProfileMetadataService(PostgresProfileMetadataStore(transaction)),
        principal_dependency=require_principal,
    )


def build_admin_users_router() -> APIRouter:
    return build_router(
        service=AdminUserDiagnosticsService(
            PostgresAdminUserDiagnosticsStore(transaction),
            sanitize_text=sanitize_public_text,
        ),
        principal_dependency=require_principal,
        is_admin=is_ai_admin,
    )
