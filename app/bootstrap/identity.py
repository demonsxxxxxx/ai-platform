from fastapi import APIRouter

from app.auth import is_ai_admin, require_principal
from app.control_plane_contracts import sanitize_public_text
from app.db import transaction
from app.identity.api import AdminUserDiagnosticsService, ProfileMetadataService
from app.identity.infrastructure.admin_user_diagnostics_postgres import (
    PostgresAdminUserDiagnosticsStore,
)
from app.identity.infrastructure.postgres import PostgresProfileMetadataStore
from app.identity.transport.admin_users import build_admin_users_router as build_router
from app.identity.transport.profile import build_profile_router


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
