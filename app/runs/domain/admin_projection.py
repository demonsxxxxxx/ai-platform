"""Framework-neutral response contracts for the administrator Run projection."""

from typing import Any, Literal, NotRequired, TypedDict


class AdminRunSummaryResponse(TypedDict):
    run_id: str
    session_id: str
    user_id: NotRequired[str | None]
    workspace_id: str
    status: str
    agent_id: str
    execution_kind: Literal["harness_chat", "skill"]
    skill_id: NotRequired[str | None]
    created_at: NotRequired[Any | None]
    queued_at: NotRequired[Any | None]
    started_at: NotRequired[Any | None]
    finished_at: NotRequired[Any | None]
    cancel_requested_at: NotRequired[Any | None]
    cancel_requested_by: NotRequired[str | None]
    error_code: NotRequired[str | None]
    error_message: NotRequired[str | None]
    queue_position: NotRequired[int | None]
    queue_insight: NotRequired[dict[str, Any] | None]


class AdminRunListResponse(TypedDict):
    runs: list[AdminRunSummaryResponse]
    limit: int


class AdminRunDetailResponse(TypedDict):
    run: dict[str, Any]
    worker_execution: dict[str, Any]
    events: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    sandbox_leases: list[dict[str, Any]]
    skill_snapshots: list[dict[str, Any]]
    audit: list[dict[str, Any]]


__all__ = [
    "AdminRunDetailResponse",
    "AdminRunListResponse",
    "AdminRunSummaryResponse",
]
