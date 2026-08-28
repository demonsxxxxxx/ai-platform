"""Composition for model-control-plane and Run-snapshot services."""

from fastapi import APIRouter

from app.auth import is_ai_admin, require_principal
from app.db import transaction
from app.execution.application.model_control_plane import (
    ModelControlPlaneService,
    configure_model_control_plane,
)
from app.execution.infrastructure.model_legacy_catalog import LegacyModelCatalogAdapter
from app.execution.infrastructure.model_management import (
    PostgresModelManagementRepository,
)
from app.execution.infrastructure.model_security import ModelEndpointSecurityAdapter
from app.execution.infrastructure.model_upstream import ModelUpstreamAdapter
from app.execution.transport import (
    build_model_management_router as build_execution_model_management_router,
)
from app.model_catalog import build_model_catalog, resolve_model_selection
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
    callback_token_matches,
)
from app.runs.application.model_snapshot import (
    RunModelSnapshotService,
    configure_run_model_snapshots,
)
from app.runs.infrastructure.postgres import PostgresRunModelSnapshotRepository
from app.settings import get_settings


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
    configure_model_control_plane(
        ModelControlPlaneService(
            transaction_factory=transaction,
            settings_provider=get_settings,
            repository=PostgresModelManagementRepository(),
            legacy_catalog=LegacyModelCatalogAdapter(
                settings_provider=get_settings,
                build_catalog=build_model_catalog,
                resolve_selection=resolve_model_selection,
            ),
            security=ModelEndpointSecurityAdapter(),
            upstream=ModelUpstreamAdapter(),
            attempt_capability_verifier=_model_attempt_capability_matches,
        )
    )
    configure_run_model_snapshots(
        RunModelSnapshotService(PostgresRunModelSnapshotRepository())
    )
