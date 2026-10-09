"""Agent Profile composition helpers."""

from collections.abc import Callable

from app.agent_apps.api import (
    configure_agent_profile_authority,
    configure_agent_profile_persistence,
    worker_profile_snapshot_matches,
)
from app.agent_apps.authority import AgentProfileAuthority
from app.agent_apps.infrastructure import postgres as agent_profile_persistence
from app.platform.postgres import errors as platform_errors
from app.runs.infrastructure import capability_admission_postgres as runs_capability_admission_postgres


def worker_profile_snapshot_matches_authority(payload, admission) -> bool:
    return worker_profile_snapshot_matches(
        payload_profile=payload.agent_profile,
        payload_input=payload.input,
        admission=admission,
        validate_mcp_selector=runs_capability_admission_postgres.extract_run_mcp_tool_ids,
        selector_errors=(
            platform_errors.RepositoryAuthorizationError,
            platform_errors.RepositoryConflictError,
        ),
    )


def configure_agent_profile_runtime() -> None:
    configure_agent_profile_persistence(agent_profile_persistence)
    configure_agent_profile_authority(AgentProfileAuthority)


def configure_agent_profile_routes(configure_favorites: Callable[..., None]) -> None:
    configure_agent_profile_runtime()
    configure_favorites(
        favorite_ids_loader=agent_profile_persistence.list_agent_profile_favorite_ids,
        favorite_setter=agent_profile_persistence.set_agent_profile_favorite,
    )
