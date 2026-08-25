"""Composition for model-control-plane and Run-snapshot services."""

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
from app.runs.application.model_snapshot import (
    RunModelSnapshotService,
    configure_run_model_snapshots,
)
from app.runs.infrastructure.postgres import PostgresRunModelSnapshotRepository
from app.settings import get_settings


def configure_model_services() -> None:
    configure_model_control_plane(
        ModelControlPlaneService(
            transaction_factory=transaction,
            settings_provider=get_settings,
            repository=PostgresModelManagementRepository(),
            legacy_catalog=LegacyModelCatalogAdapter(),
            security=ModelEndpointSecurityAdapter(),
            upstream=ModelUpstreamAdapter(),
        )
    )
    configure_run_model_snapshots(
        RunModelSnapshotService(PostgresRunModelSnapshotRepository())
    )
