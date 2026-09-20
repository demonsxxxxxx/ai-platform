"""Composition for model-control-plane and Run-snapshot services."""

from fastapi import APIRouter
from functools import partial

from app.auth import is_ai_admin, require_principal
from app.db import transaction
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
from app.runs.application.provider_terminalization import configure_terminal_checkpoint_dependencies
from app.runs.infrastructure.postgres import (
    PostgresRunModelSnapshotRepository,
    load_worker_dispatch_run_facts,
    update_terminal_run_checkpoint_counts,
)
from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size
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
            security=ModelEndpointSecurityAdapter(),
            upstream=ModelUpstreamAdapter(),
            attempt_capability_verifier=_model_attempt_capability_matches,
        )
    )
    configure_run_model_snapshots(
        RunModelSnapshotService(PostgresRunModelSnapshotRepository())
    )
    configure_worker_dispatch_run_facts_loader(load_worker_dispatch_run_facts)
    configure_terminal_checkpoint_dependencies(
        update_counts=update_terminal_run_checkpoint_counts,
        validate_result=partial(
            ensure_json_size, max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large",
        ),
    )
