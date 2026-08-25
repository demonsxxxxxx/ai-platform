from app.execution.application.adapter_run import (
    WorkerRunCancelled,
    submit_run_until_cancelled,
    time,
)
from app.execution.application.skill_invocation_evidence import (
    SkillInvocationEvidenceBinder,
)
from app.execution.application.context_file_diagnostics import (
    context_file_failure_event_fields,
    context_file_failure_event_payload,
    context_file_failure_log_extra,
    validated_context_file_diagnostic,
)
from app.execution.application.executor_reconciliation import (
    restored_sandbox_run_payload,
    sandbox_reconciliation_payload,
)
from app.execution.application.stale_terminalization import (
    stage_stale_run_reconciliation,
)

from typing import Any

from app.execution.application.model_control_plane import configured_model_control_plane
from app.execution.application.model_selection import (
    RunModelSelection as RunModelSelection,
)
from app.execution.application.model_selection import (
    parse_requested_model_selection as parse_requested_model_selection,
)


async def list_public_models(conn: Any) -> dict[str, object]:
    return await configured_model_control_plane().public_models(conn)


async def resolve_chat_model_selection(
    conn: Any,
    *,
    selection: dict[str, str] | None,
) -> RunModelSelection | None:
    return await configured_model_control_plane().resolve_selection(
        conn,
        selection=selection,
    )

__all__ = [
    "RunModelSelection",
    "SkillInvocationEvidenceBinder",
    "WorkerRunCancelled",
    "context_file_failure_event_fields",
    "context_file_failure_event_payload",
    "context_file_failure_log_extra",
    "restored_sandbox_run_payload",
    "sandbox_reconciliation_payload",
    "stage_stale_run_reconciliation",
    "submit_run_until_cancelled",
    "list_public_models",
    "parse_requested_model_selection",
    "resolve_chat_model_selection",
    "time",
    "validated_context_file_diagnostic",
]
