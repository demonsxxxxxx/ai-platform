"""Composition for model-control-plane and Run-snapshot services."""

from fastapi import APIRouter

from app.bootstrap.run_diagnostics import record_execution_debug
from app.auth import is_ai_admin, require_principal
from app.db import transaction
from app.execution.application.follow_up_completion import (
    FollowUpSuggestionService,
    configure_follow_up_suggestions,
)
from app.execution.application.model_control_plane import (
    ModelControlPlaneService,
    configure_model_control_plane,
)
from app.execution.infrastructure.model_management import (
    PostgresModelManagementRepository,
)
from app.execution.infrastructure.model_security import ModelEndpointSecurityAdapter
from app.execution.infrastructure.model_upstream import ModelUpstreamAdapter
from app.execution.transport import (
    build_model_management_router as build_execution_model_management_router,
)
from app.bootstrap.identity import build_company_employee_directory
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
    callback_token_matches,
)
from app.runs.application.model_snapshot import (
    RunModelSnapshotService,
    configure_run_model_snapshots,
)
from app.runs.application.execution_spec import configure_worker_dispatch_run_facts_loader
from app.runs.infrastructure.postgres import (
    PostgresRunModelSnapshotRepository,
    load_worker_dispatch_run_facts,
)
from app.settings import get_settings

_model_repository = PostgresModelManagementRepository()


def build_model_management_router() -> APIRouter:
    return build_execution_model_management_router(
        principal_dependency=require_principal,
        is_admin=is_ai_admin,
    )


def _model_attempt_capability_matches(
    *,
    run_id: str,
    attempt_id: str,
    provided_capability: str,
) -> bool:
    secret = str(get_settings().sandbox_callback_token or "")
    try:
        token_id = callback_token_id_for_binding(
            CallbackTokenBinding(run_id=run_id, attempt_id=attempt_id)
        )
    except ValueError:
        return False
    return bool(secret) and callback_token_matches(
        secret=secret,
        token_id=token_id,
        provided_token=provided_capability,
    )


def configure_model_services() -> None:
    settings_provider = get_settings
    configure_model_control_plane(
        ModelControlPlaneService(
            transaction_factory=transaction,
            settings_provider=settings_provider,
            repository=_model_repository,
            employee_directory=build_company_employee_directory(),
            security=ModelEndpointSecurityAdapter(),
            upstream=ModelUpstreamAdapter(),
            attempt_capability_verifier=_model_attempt_capability_matches,
            diagnostic_recorder=record_execution_debug,
        )
    )
    configure_follow_up_suggestions(
        FollowUpSuggestionService(
            transaction_factory=transaction,
            repository=_model_repository,
            upstream=ModelUpstreamAdapter(),
            settings_provider=settings_provider,
        )
    )
    configure_run_model_snapshots(
        RunModelSnapshotService(PostgresRunModelSnapshotRepository())
    )
    configure_worker_dispatch_run_facts_loader(load_worker_dispatch_run_facts)
