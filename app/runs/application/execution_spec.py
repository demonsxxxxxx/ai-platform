"""Runs-owned compiler input projection for worker dispatch."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    ExecutionSpec,
    ExecutionSpecError,
    compile_execution_spec,
)


class AuthorizedQueuePayload(Protocol):
    """Queue fields remaining after worker reauthorization."""

    schema_version: str
    skill_id: str | None
    file_ids: list[str]
    input: dict[str, Any]
    executor_type: str
    skill_version: str | None
    release_decision: dict[str, Any]
    skill_manifests: list[dict[str, Any]]
    model_id: str | None
    model_value: str | None
    agent_profile: dict[str, Any] | None


def compile_execution_spec_for_dispatch(
    *,
    run_identity: Mapping[str, Any],
    queue_payload: AuthorizedQueuePayload,
    trace_id: str,
    context_snapshot_id: str,
    context_snapshot: dict[str, Any],
    context_pack: dict[str, Any],
) -> ExecutionSpec:
    """Compile worker-authorized fields through the single Runs-owned codec."""

    identity_skill_id = str(run_identity.get("skill_id") or "") or None
    queue_skill_id = str(queue_payload.skill_id or "") or None
    if queue_skill_id != identity_skill_id:
        raise ExecutionSpecError("execution_spec_skill_identity_mismatch")

    return compile_execution_spec(
        {
            "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
            "run_payload_schema_version": queue_payload.schema_version,
            "tenant_id": run_identity["tenant_id"],
            "workspace_id": run_identity["workspace_id"],
            "user_id": run_identity["user_id"],
            "session_id": run_identity["session_id"],
            "run_id": run_identity["run_id"],
            "agent_id": run_identity["agent_id"],
            "execution_kind": run_identity["execution_kind"],
            "skill_id": identity_skill_id,
            "file_ids": queue_payload.file_ids,
            "input": queue_payload.input,
            "executor_type": queue_payload.executor_type,
            "trace_id": trace_id,
            "skill_version": queue_payload.skill_version or "",
            "release_decision": queue_payload.release_decision,
            "skill_manifests": queue_payload.skill_manifests,
            "context_snapshot_id": context_snapshot_id,
            "context_snapshot": context_snapshot,
            "context_pack": context_pack,
            "model_id": queue_payload.model_id or "",
            "model_value": queue_payload.model_value or "",
            "agent_profile": queue_payload.agent_profile or {},
        }
    )
