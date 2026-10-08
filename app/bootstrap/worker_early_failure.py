"""Bind Runs early Worker failures to Identity and Streaming writers."""

from app.auth import AuthPrincipal, normalize_roles
from app.bootstrap.identity import (
    build_worker_capability_audit_service,
    build_worker_distribution_authority,
)
from app.execution.api import predispatch_failure_result
from app.runs import api as runs_api
from app.streaming.infrastructure import run_events_postgres


def _locked_run_principal(locked_run: object, run_identity: dict[str, str]) -> AuthPrincipal:
    locked = locked_run if isinstance(locked_run, dict) else {}
    raw_roles = locked.get("principal_roles")
    roles = normalize_roles(raw_roles if isinstance(raw_roles, (list, tuple, set)) else [])
    return AuthPrincipal(
        user_id=run_identity["user_id"], display_name=run_identity["user_id"],
        tenant_id=run_identity["tenant_id"],
        department_id=str(locked.get("principal_department_id") or ""),
        roles=roles, permissions=[], source=str(locked.get("auth_source") or ""),
    )


def build_worker_early_failure_service() -> runs_api.WorkerEarlyFailureService:
    return runs_api.WorkerEarlyFailureService(
        fail_result=predispatch_failure_result,
        append_event=run_events_postgres.append_event,
        record_denial_audit=build_worker_capability_audit_service().record_denial,
        locked_principal=_locked_run_principal,
        denied_capability=build_worker_distribution_authority().denied,
    )
