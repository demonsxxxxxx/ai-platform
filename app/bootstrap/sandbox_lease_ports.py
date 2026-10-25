"""Bind Sandbox application persistence ports to their PostgreSQL adapter."""

from typing import cast

from app.sandbox.api import (
    SandboxExecutorCallbackLeasePort,
    SandboxExecutorReconciliationPort,
    SandboxLeaseTransportPort,
    SandboxRunLeaseReader,
    SandboxRuntimeLeasePort,
)
from app.sandbox.infrastructure import leases_postgres


def build_sandbox_runtime_lease_port() -> SandboxRuntimeLeasePort:
    return cast(SandboxRuntimeLeasePort, leases_postgres)


def build_sandbox_executor_callback_lease_port() -> SandboxExecutorCallbackLeasePort:
    return cast(SandboxExecutorCallbackLeasePort, leases_postgres)


def build_sandbox_lease_transport_port() -> SandboxLeaseTransportPort:
    return cast(SandboxLeaseTransportPort, leases_postgres)


def build_sandbox_executor_reconciliation_port() -> SandboxExecutorReconciliationPort:
    return cast(SandboxExecutorReconciliationPort, leases_postgres)


def build_sandbox_run_lease_reader() -> SandboxRunLeaseReader:
    return cast(SandboxRunLeaseReader, leases_postgres)
