"""Compose Runs locked Worker authorization with owning capability services."""

from collections.abc import Callable
from functools import partial
from typing import Any

from pydantic import ValidationError

from app.agent_apps import api as agent_apps_api
from app.bootstrap.agent_profiles import worker_profile_snapshot_matches_authority
from app.bootstrap.identity import (
    build_worker_capability_audit_service,
    build_worker_distribution_authority,
)
from app.bootstrap.worker_capability_admission import build_worker_capability_admission_service
from app.bootstrap.worker_early_failure import build_worker_early_failure_service
from app.control_plane_contracts import RUN_EXECUTION_KIND_HARNESS_CHAT, standard_trace_id
from app.execution.api import (
    locked_run_payload_candidate,
    reconciliation_agent_profile_binding_matches,
    with_locked_run_model_snapshot,
)
from app.models import QueueRunPayload
from app.platform.postgres import errors as platform_errors
from app.runs import api as runs_api
from app.runs.infrastructure import capability_admission_postgres
from app.skills import api as skills_api
from app.skills.infrastructure import run_snapshots_postgres
from app.worker_principal_authority import _identity_mismatch_fields, _locked_run_identity


def payload_from_locked_run(
    locked_run: object, *, run_identity: dict[str, str],
) -> QueueRunPayload | None:
    candidate = locked_run_payload_candidate(
        locked_run, run_identity=run_identity,
        harness_execution_kind=RUN_EXECUTION_KIND_HARNESS_CHAT,
    )
    if candidate is None:
        return None
    try:
        return QueueRunPayload.model_validate(candidate)
    except ValidationError:
        return None


def locked_run_trace_id(payload: QueueRunPayload, locked_run: object) -> str:
    if isinstance(locked_run, dict) and locked_run.get("trace_id"):
        return str(locked_run["trace_id"])
    return standard_trace_id(payload.run_id)


def build_worker_locked_authorization(
    *, settings_provider: Callable[[], Any],
) -> runs_api.WorkerLockedAuthorizationService:
    audit_service = build_worker_capability_audit_service()
    failure_service = build_worker_early_failure_service()
    return runs_api.WorkerLockedAuthorizationService(
        snapshot=runs_api.WorkerLockedSnapshotService(
            identity=_locked_run_identity,
            mismatch_fields=_identity_mismatch_fields,
            load_model=with_locked_run_model_snapshot,
            model_loader=runs_api.load_run_model_snapshot,
            trace_id=locked_run_trace_id,
            parse_payload=payload_from_locked_run,
            profile_identity_valid=agent_apps_api.locked_agent_profile_identity_valid,
            reconciliation_profile_matches=reconciliation_agent_profile_binding_matches,
        ),
        skill_materialize=partial(
            skills_api.materialize_worker_locked_skill_snapshots,
            materialize=run_snapshots_postgres.materialize_run_skill_manifests,
            conflict_error=platform_errors.RepositoryConflictError,
        ),
        profile_authorize=partial(
            agent_apps_api.reauthorize_worker_locked_profile,
            reauthorize=agent_apps_api.reauthorize_bound_profile_for_worker_dispatch,
            snapshot_matches=worker_profile_snapshot_matches_authority,
            extract_mcp_tool_ids=capability_admission_postgres.extract_run_mcp_tool_ids,
        ),
        capability_authority=build_worker_capability_admission_service(
            settings_provider=settings_provider,
        ),
        audit_authority=audit_service,
        distribution_authority=build_worker_distribution_authority(),
        failures=failure_service,
    )
