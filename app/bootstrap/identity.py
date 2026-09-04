from fastapi import APIRouter

from app.auth import require_principal
from app.db import transaction
from app.identity.api import ProfileMetadataService
from app.identity.infrastructure.postgres import PostgresProfileMetadataStore
from app.identity.transport.profile import build_profile_router


def build_identity_profile_router() -> APIRouter:
    return build_profile_router(
        service=ProfileMetadataService(PostgresProfileMetadataStore(transaction)),
        principal_dependency=require_principal,
    )
