"""Compose the Runs model-token ledger capability."""
from app.auth import is_ai_admin, require_principal
from app.db import transaction
from app.context.infrastructure.provider_epochs import read_usage_provider_session
from app.runs.transport.model_token_usage import build_model_token_usage_router
from app.runs.api import ModelTokenUsageService
from app.runs.infrastructure.model_token_usage import PostgresModelTokenUsage


def build_model_token_usage_service():
    return ModelTokenUsageService(PostgresModelTokenUsage(provider_session=read_usage_provider_session))


def build_model_token_usage_admin_router(service: ModelTokenUsageService):
    return build_model_token_usage_router(
        service=service, transaction_factory=transaction,
        principal_dependency=require_principal, is_admin=is_ai_admin,
    )
