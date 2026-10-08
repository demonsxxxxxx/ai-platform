"""Project one executor result before the authoritative terminal transaction."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.execution.application.worker_answer_persistence import (
    assistant_artifact_metadata,
    sanitize_assistant_message,
)
from app.execution.application.worker_skill_evidence import skill_snapshot_from_result
from app.execution.domain.worker_observability import (
    event_observability_kwargs,
    executor_observability,
)


class WorkerExecutorResult(Protocol):
    """Executor facts consumed by the Runs-owned result transaction."""

    status: str
    result: dict[str, Any]
    executor_payload: dict[str, Any]

    @property
    def artifacts(self) -> Sequence[object]: ...


def enforce_worker_required_tool_completion(
    result: Any, *, payload: Any, run_identity: dict[str, str],
    attempt_id: str, required_tool_decision: Any,
    check_completion: Callable[..., Any],
) -> Any:
    completion = check_completion(
        payload=payload, run_identity=run_identity,
        attempt_id=attempt_id, authorization=required_tool_decision,
        executor_payload=result.executor_payload,
    )
    if result.status != "succeeded" or completion.allowed:
        return result
    return replace(
        result, status="failed", artifacts=[],
        result={
            **result.result,
            "message": "Required execution capability evidence is unavailable.",
            "error_code": completion.reason,
        },
    )


@dataclass(frozen=True)
class WorkerTerminalProjection:
    result_payload: dict[str, Any]
    skill_snapshot: dict[str, list[str]]
    terminal_event_kwargs: dict[str, Any]


def worker_assistant_metadata(
    artifact_records: list[dict[str, Any]],
    result: Any,
    assistant_message_metadata: dict[str, Any],
    skill_snapshot: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        **assistant_artifact_metadata(artifact_records),
        "executor_type": result.executor_type,
        "adapter_version": result.adapter_version,
        **assistant_message_metadata,
        "skills": skill_snapshot,
    }


def enforce_required_artifact_types(
    result: Any,
    artifact_records: list[dict[str, Any]],
    result_payload: dict[str, Any],
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    required_types = {
        str(value)
        for value in result.executor_payload.get("required_artifact_types", [])
        if isinstance(value, str) and value
    }
    produced_types = {artifact.artifact_type for artifact in result.artifacts}
    missing = required_types - produced_types
    if result.status != "succeeded" or not missing:
        return result, artifact_records, result_payload
    error_code = "required_artifact_missing"
    error_message = "The executor did not produce every declared required artifact type."
    return (
        replace(
            result,
            status="failed",
            artifacts=[],
            result={
                **result.result,
                "message": error_message,
                "error_code": error_code,
                "missing_required_artifact_types": sorted(missing),
            },
        ),
        [],
        {**result_payload, "message": error_message, "error_code": error_code, "artifacts": []},
    )


def project_worker_terminal_result(
    result: Any,
    artifact_records: list[dict[str, Any]],
    *,
    trace_id: str,
    latency_ms: int,
    invoked_skill_ids: Callable[[dict[str, Any]], set[str]],
) -> WorkerTerminalProjection:
    observability = executor_observability(result.executor_payload, latency_ms=latency_ms)
    event_metrics = event_observability_kwargs(observability, result.executor_payload)
    terminal_event_kwargs = {"trace_id": trace_id, **event_metrics} if event_metrics else {}
    skill_snapshot = skill_snapshot_from_result(result, invoked_skill_ids=invoked_skill_ids)
    persisted_result = {
        key: value
        for key, value in (
            result.result
            | ({"runtime_diagnostics": result.executor_payload["runtime_diagnostics"]}
               if "runtime_diagnostics" in result.executor_payload else {})
        ).items()
        if key not in {"skill_manifests", "used_skills", "used_skills_source", "inferred_used_skills"}
    }
    if "used_skills" in result.result or "used_skills" in result.executor_payload:
        persisted_result["used_skills"] = skill_snapshot["used_skills"]
    result_payload = {
        **persisted_result,
        **observability,
        "message": sanitize_assistant_message(str(result.result.get("message") or "")),
        "artifacts": [
            {
                "id": item["id"],
                "artifact_type": item["artifact_type"],
                "label": item["label"],
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "download_url": item["download_url"],
            }
            for item in artifact_records
        ],
        "executor": {
            "schema_version": result.schema_version,
            "adapter_version": result.adapter_version,
            "executor_type": result.executor_type,
            "executor_version": result.executor_version,
            "capabilities": result.capabilities,
        },
    }
    if skill_snapshot:
        result_payload["skills"] = skill_snapshot
    return WorkerTerminalProjection(result_payload, skill_snapshot, terminal_event_kwargs)
