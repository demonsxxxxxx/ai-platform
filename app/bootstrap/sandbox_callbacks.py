"""Bind Sandbox callback authority to the existing durable identity queries."""

from app.runs.infrastructure import postgres as runs_postgres
from app.runs.api import is_terminal_run_status
from app.sandbox.api import SandboxCallbackAuthority
from app.sandbox.infrastructure import leases_postgres


def build_sandbox_callback_authority() -> SandboxCallbackAuthority:
    return SandboxCallbackAuthority(
        is_terminal_run_status=is_terminal_run_status,
        get_run_identity=lambda *args, **kwargs: runs_postgres.get_run_identity(*args, **kwargs),
        list_current_leases=lambda *args, **kwargs: leases_postgres.list_current_sandbox_runtime_leases_for_attempt(
            *args, **kwargs
        ),
        list_provider_tail_leases=lambda *args, **kwargs: leases_postgres.list_cancelling_provider_tail_leases_for_attempt(
            *args, **kwargs
        ),
        list_terminal_tail_leases=lambda *args, **kwargs: leases_postgres.list_cancelling_executor_terminal_leases_for_attempt(
            *args, **kwargs
        ),
    )
