"""Runs-owned compiler input projection for worker dispatch."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol

from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION_V2,
    ExecutionSpec,
    ExecutionSpecError,
    compile_execution_spec,
)


_dispatch_run_facts_loader: Callable[..., Any] | None = None


def configure_worker_dispatch_run_facts_loader(loader: Callable[..., Any]) -> None:
    global _dispatch_run_facts_loader
    _dispatch_run_facts_loader = loader


async def worker_dispatch_fence(
    conn: Any, *, run_identity: Mapping[str, str], locked_run: Mapping[str, Any],
    context_snapshot_id: str, reconciliation: bool,
    run_facts_loader: Callable[..., Any] | None = None,
) -> str:
    loader = run_facts_loader or _dispatch_run_facts_loader
    if loader is None:
        raise RuntimeError("worker_dispatch_run_facts_loader_not_configured")
    row = await loader(
        conn, tenant_id=run_identity["tenant_id"], run_id=run_identity["run_id"],
    )
    if row is None or row["status"] != ("running" if reconciliation else "queued") or row["cancel_requested_at"] is not None:
        return "stale"
    if any(str(row[key] or "") != run_identity[key] for key in
           ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id",
            "execution_kind", "skill_id")):
        return "invalid"
    if (row["id"] != run_identity["run_id"]
        or row["context_snapshot_id"] != context_snapshot_id
        or any(row[key] != locked_run[key] for key in
               ("model_id", "model_value", "model_gateway_revision",
                "max_input_tokens", "max_output_tokens"))):
        return "invalid"
    return "ready"


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


def _v2_model_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(snapshot, Mapping):
        return None
    values = {
        "model_gateway_revision": snapshot.get("model_gateway_revision"),
        "model_max_input_tokens": snapshot.get("max_input_tokens"),
        "model_max_output_tokens": snapshot.get("max_output_tokens"),
    }
    if any(value is None for value in values.values()):
        if any(value is not None for value in values.values()):
            raise ExecutionSpecError("execution_spec_model_snapshot_invalid")
        return None
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in values.values()
    ):
        raise ExecutionSpecError("execution_spec_model_snapshot_invalid")
    return values


def compile_execution_spec_for_dispatch(
    *,
    run_identity: Mapping[str, Any],
    queue_payload: AuthorizedQueuePayload,
    trace_id: str,
    context_snapshot_id: str,
    context_snapshot: dict[str, Any],
    context_pack: dict[str, Any],
    run_model_snapshot: Mapping[str, Any] | None = None,
) -> ExecutionSpec:
    """Compile worker-authorized fields through the single Runs-owned codec."""

    identity_skill_id = str(run_identity.get("skill_id") or "") or None
    queue_skill_id = str(queue_payload.skill_id or "") or None
    if queue_skill_id != identity_skill_id:
        raise ExecutionSpecError("execution_spec_skill_identity_mismatch")

    model_snapshot = _v2_model_snapshot(run_model_snapshot)
    if model_snapshot is None:
        raise ExecutionSpecError("execution_spec_model_snapshot_missing")
    if (
        run_model_snapshot.get("model_id") != queue_payload.model_id
        or run_model_snapshot.get("model_value") != queue_payload.model_value
    ):
        raise ExecutionSpecError("execution_spec_model_snapshot_mismatch")
    spec_payload: dict[str, Any] = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION_V2,
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
    spec_payload.update(model_snapshot)
    return compile_execution_spec(spec_payload)
