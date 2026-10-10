"""Compose Runs Worker capability admission with Skills, Identity and MCP."""

from functools import partial
from typing import Any, Callable

from app.auth import AuthPrincipal
from app.bootstrap.identity import build_worker_distribution_authority
from app.bootstrap.mcp import build_worker_mcp_dispatch_service
from app.bootstrap.skills import build_worker_skill_dispatch_authorization
from app.control_plane_contracts import RUN_EXECUTION_KIND_HARNESS_CHAT
from app.execution_boundary import decide_worker_execution_boundary
from app.platform.postgres import errors as platform_errors
from app.principal_authority import CURRENT_PRINCIPAL_DENIAL_REASON
from app.required_tool_contract import (
    builtin_capability_subjects,
    required_tool_authorization_for_run,
    with_boundary_sandbox_local_tool_subjects,
    with_harness_local_tool_subjects,
)
from app.runs import api as runs_api
from app.runs.infrastructure import capability_admission_postgres
from app.skills.execution_profiles import effective_skill_execution_profile


def build_worker_capability_admission_service(
    *, settings_provider: Callable[[], Any],
) -> runs_api.WorkerCapabilityAdmissionService:
    return runs_api.WorkerCapabilityAdmissionService(
        skill_authority=build_worker_skill_dispatch_authorization(),
        identity_authority=build_worker_distribution_authority(),
        mcp_authority=build_worker_mcp_dispatch_service(),
        required_tools=runs_api.WorkerRequiredToolPorts(
            boundary_decision=decide_worker_execution_boundary,
            harness_subjects=with_harness_local_tool_subjects,
            skill_subjects=partial(
                builtin_capability_subjects,
                canonical_manifest=effective_skill_execution_profile,
            ),
            sandbox_subjects=with_boundary_sandbox_local_tool_subjects,
            authorize_required=required_tool_authorization_for_run,
            sandbox_provider=lambda: settings_provider().sandbox_container_provider,
        ),
        extract_mcp_tool_ids=capability_admission_postgres.extract_run_mcp_tool_ids,
        skill_mcp_tool_ids=capability_admission_postgres.run_mcp_tool_ids_for_skill,
        selector_error=platform_errors.RepositoryAuthorizationError,
        principal_type=AuthPrincipal,
        missing_principal_reason=CURRENT_PRINCIPAL_DENIAL_REASON,
        harness_execution_kind=RUN_EXECUTION_KIND_HARNESS_CHAT,
    )
