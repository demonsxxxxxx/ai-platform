"""Assemble Sandbox lifecycle operations against the existing runtime adapters."""

from app import execution_boundary
from app.db import transaction
from app.identity.infrastructure import audit_postgres, postgres as identity_postgres
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runtime.sandbox import opensandbox_policy
from app.runtime.sandbox.contracts import ContainerLease
from app.sandbox.api import SandboxCleanupOutcomes, SandboxRuntimeCleanup
from app.sandbox.application.runtime_handle import PersistedRuntimeHandleRecovery
from app.settings import get_settings
from app.streaming.infrastructure import run_events_postgres
from app.kernel.identifiers import assert_safe_id


def build_sandbox_cleanup_outcomes() -> SandboxCleanupOutcomes:
    return SandboxCleanupOutcomes(
        append_event=lambda *args, **kwargs: run_events_postgres.append_event(*args, **kwargs),
        append_audit_log=lambda *args, **kwargs: audit_postgres.append_audit_log(*args, **kwargs),
    )


def build_sandbox_runtime_cleanup() -> SandboxRuntimeCleanup:
    return SandboxRuntimeCleanup(
        transaction_factory=lambda: transaction(),
        lease_repository=sandbox_lease_repository,
        outcomes=build_sandbox_cleanup_outcomes(),
        append_event=lambda *args, **kwargs: run_events_postgres.append_event(*args, **kwargs),
        append_audit=lambda *args, **kwargs: audit_postgres.append_audit_log(*args, **kwargs),
        list_tenant_ids=lambda conn: identity_postgres.list_tenant_ids(conn),
        handle_recovery=PersistedRuntimeHandleRecovery(
            lease_factory=ContainerLease,
            assert_safe_id=assert_safe_id,
            settings=lambda: get_settings(),
            egress_policy=execution_boundary,
            opensandbox_policy=opensandbox_policy,
        ),
        settings=lambda: get_settings(),
    )
