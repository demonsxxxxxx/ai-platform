"""Compose Context application use cases with PostgreSQL adapters."""

from app.context.application.provider_sessions import (
    ProviderSessionUseCases,
    configure_provider_session_use_cases,
)
from app.context.infrastructure.provider_sessions import PostgresProviderSessionRepository


def configure_context_services() -> None:
    configure_provider_session_use_cases(
        ProviderSessionUseCases(PostgresProviderSessionRepository())
    )


__all__ = ["configure_context_services"]
