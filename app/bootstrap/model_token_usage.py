"""Compose the Runs model-token ledger capability."""
from app.context.infrastructure.provider_epochs import read_usage_provider_session
from app.runs.api import ModelTokenUsageService
from app.runs.infrastructure.model_token_usage import PostgresModelTokenUsage


def build_model_token_usage_service():
    return ModelTokenUsageService(PostgresModelTokenUsage(provider_session=read_usage_provider_session))
