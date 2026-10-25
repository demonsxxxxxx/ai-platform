"""Assemble Sandbox lease ports with their PostgreSQL and Runs dependencies."""

from typing import cast

from app.sandbox.api import (
    SandboxExecutorCallbackLeasePort,
    SandboxExecutorReconciliationPort,
    SandboxLeaseTransportPort,
    SandboxRunLeaseReader,
    SandboxRuntimeLeasePort,
)
from app.sandbox.application.attempt_settlement_shadow import (
    AttemptSettlementShadowLeaseRepository,
)
from app.sandbox.infrastructure import leases_postgres
from app.bootstrap.attempt_settlement_projection import (
    build_attempt_settlement_projection_service,
)
from app.settings import get_settings


def _settlement_shadow_enabled() -> bool:
    return bool(get_settings().run_attempt_settlement_shadow_enabled)


def _build_shadowed_repository() -> AttemptSettlementShadowLeaseRepository:
    return AttemptSettlementShadowLeaseRepository(
        repository=leases_postgres,
        settlement_projection=build_attempt_settlement_projection_service(),
        enabled=_settlement_shadow_enabled,
    )


_shadowed_repository = _build_shadowed_repository()


def build_sandbox_runtime_lease_port() -> SandboxRuntimeLeasePort:
    return cast(SandboxRuntimeLeasePort, _shadowed_repository)


def build_sandbox_executor_callback_lease_port() -> SandboxExecutorCallbackLeasePort:
    return cast(SandboxExecutorCallbackLeasePort, _shadowed_repository)


def build_sandbox_lease_transport_port() -> SandboxLeaseTransportPort:
    return cast(SandboxLeaseTransportPort, _shadowed_repository)


def build_sandbox_executor_reconciliation_port() -> SandboxExecutorReconciliationPort:
    return cast(SandboxExecutorReconciliationPort, _shadowed_repository)


def build_sandbox_run_lease_reader() -> SandboxRunLeaseReader:
    return cast(SandboxRunLeaseReader, leases_postgres)
