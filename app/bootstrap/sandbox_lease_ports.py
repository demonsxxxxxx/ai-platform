"""Assemble Sandbox lease ports with their PostgreSQL and Runs dependencies."""

from typing import cast

from app.sandbox.api import (
    SandboxExecutorCallbackLeasePort,
    SandboxExecutorReconciliationPort,
    SandboxLeaseTransportPort,
    SandboxRunLeaseReader,
    SandboxRuntimeLeasePort,
)
from app.sandbox.application.attempt_settlement import (
    AttemptSettlementLeaseRepository,
)
from app.sandbox.infrastructure import leases_postgres
from app.bootstrap.attempt_settlement_projection import (
    build_attempt_settlement_projection_service,
)


_settlement_repository = AttemptSettlementLeaseRepository(
    repository=leases_postgres,
    settlement_projection=build_attempt_settlement_projection_service(),
)


def build_sandbox_runtime_lease_port() -> SandboxRuntimeLeasePort:
    return cast(SandboxRuntimeLeasePort, _settlement_repository)


def build_sandbox_executor_callback_lease_port() -> SandboxExecutorCallbackLeasePort:
    return cast(SandboxExecutorCallbackLeasePort, _settlement_repository)


def build_sandbox_lease_transport_port() -> SandboxLeaseTransportPort:
    return cast(SandboxLeaseTransportPort, _settlement_repository)


def build_sandbox_executor_reconciliation_port() -> SandboxExecutorReconciliationPort:
    return cast(SandboxExecutorReconciliationPort, _settlement_repository)


def build_sandbox_run_lease_reader() -> SandboxRunLeaseReader:
    return cast(SandboxRunLeaseReader, _settlement_repository)
