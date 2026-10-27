"""Runs-owned compiler input projection for worker dispatch."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol

from app.runs.domain.execution_spec import (
    EXECUTION_SPEC_SCHEMA_VERSION_V3,
    EXECUTION_SPEC_SCHEMA_VERSION_V4,
    ExecutionSpec,
    ExecutionSpecError,
    compile_execution_spec,
)


_dispatch_run_facts_loader: Callable[..., Any] | None = None


def model_auto_compact_window(spec: Mapping[str, Any]) -> int | None:
    """Project a validated frozen spec through the historical model policy."""

    if spec.get("schema_version") in {EXECUTION_SPEC_SCHEMA_VERSION_V3, EXECUTION_SPEC_SCHEMA_VERSION_V4}:
        return spec["model_auto_compact_window"]
    # Resolve only for legacy projection; the context APIs also compose Runs.
    from app.execution.api import legacy_input_capacity_to_auto_compact_window

    return legacy_input_capacity_to_auto_compact_window(
        spec.get("model_max_input_tokens")
    )


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
                "auto_compact_window", "max_output_tokens"))
        or any(row.get(key) != locked_run.get(key) for key in
               ("model_group_id", "model_group_code", "model_group_type",
                "model_group_revision", "model_selection_mode", "usage_scene",
                "model_policy_version"))):
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
        "model_auto_compact_window": snapshot.get("auto_compact_window"),
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
    group_snapshot_keys = (
        "model_group_id", "model_group_code", "model_group_type", "model_group_revision",
        "model_selection_mode", "usage_scene", "model_policy_version",
    )
    has_group_snapshot = any(run_model_snapshot.get(key) is not None for key in group_snapshot_keys)
    if has_group_snapshot and any(
        run_model_snapshot.get(key) is None for key in group_snapshot_keys
    ):
        raise ExecutionSpecError("execution_spec_model_group_snapshot_invalid")
    spec_payload: dict[str, Any] = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION_V4 if has_group_snapshot else EXECUTION_SPEC_SCHEMA_VERSION_V3,
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
    if has_group_snapshot:
        spec_payload.update({
            "model_group_id": run_model_snapshot["model_group_id"],
            "model_group_code": run_model_snapshot["model_group_code"],
            "model_group_type": run_model_snapshot["model_group_type"],
            "model_group_revision": run_model_snapshot["model_group_revision"],
            "model_selection_mode": run_model_snapshot["model_selection_mode"],
            "usage_scene": run_model_snapshot["usage_scene"],
            "model_policy_version": run_model_snapshot["model_policy_version"],
        })
    return compile_execution_spec(spec_payload)
