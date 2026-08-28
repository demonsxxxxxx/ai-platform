from collections.abc import Awaitable, Callable
from typing import Any


LOCKED_RUN_SNAPSHOT_FIELDS = (
    "file_ids",
    "input",
    "executor_type",
    "skill_version",
    "release_decision",
    "skill_manifests",
    "context_snapshot_id",
    "context_snapshot",
    "model_id",
    "model_value",
    "agent_profile",
    "schema_version",
)
_RUN_MODEL_SNAPSHOT_FIELDS = ("model_id", "model_value", "model_gateway_revision")


def locked_run_payload_candidate(
    locked_run: object,
    *,
    run_identity: dict[str, str],
    harness_execution_kind: str,
) -> dict[str, Any] | None:
    if not isinstance(locked_run, dict):
        return None
    input_json = locked_run.get("input_json")
    if not isinstance(input_json, dict) or not isinstance(input_json.get("input"), dict):
        return None
    candidate = {
        **run_identity,
        **{field: input_json[field] for field in LOCKED_RUN_SNAPSHOT_FIELDS if field in input_json},
    }
    durable_model_id = locked_run.get("model_id")
    durable_model_value = locked_run.get("model_value")
    durable_gateway_revision = locked_run.get("model_gateway_revision")
    if any(
        value is not None
        for value in (durable_model_id, durable_model_value, durable_gateway_revision)
    ):
        if not (
            isinstance(durable_model_id, str)
            and durable_model_id
            and isinstance(durable_model_value, str)
            and durable_model_value
        ):
            return None
        candidate["model_id"] = durable_model_id
        candidate["model_value"] = durable_model_value
    elif not (
        isinstance(input_json.get("model_id"), str)
        and input_json["model_id"]
        and isinstance(input_json.get("model_value"), str)
        and input_json["model_value"]
    ):
        return None
    if (
        candidate.get("execution_kind") == harness_execution_kind
        and candidate.get("skill_id") == ""
    ):
        candidate["skill_id"] = None
    return candidate


async def with_locked_run_model_snapshot(
    locked_run: object,
    *,
    conn: Any,
    run_identity: dict[str, str],
    load_run_model_snapshot: Callable[..., Awaitable[dict[str, Any]]],
) -> object:
    if isinstance(locked_run, dict) and not any(
        field in locked_run for field in _RUN_MODEL_SNAPSHOT_FIELDS
    ):
        locked_run = {
            **locked_run,
            **await load_run_model_snapshot(
                conn,
                tenant_id=run_identity["tenant_id"],
                run_id=run_identity["run_id"],
            ),
        }
    return locked_run


RECONCILIATION_SNAPSHOT_SCHEMA_VERSION = (
    "ai-platform.executor-reconciliation-snapshot.v2"
)


def _non_secret_agent_profile(profile: object) -> dict[str, Any]:
    if not isinstance(profile, dict) or not profile:
        return {}
    skill_set = profile.get("skill_set")
    return {
        "agent_id": str(profile.get("agent_id") or ""),
        "revision": (
            int(profile["revision"])
            if isinstance(profile.get("revision"), int)
            and not isinstance(profile.get("revision"), bool)
            else 0
        ),
        "content_hash": str(profile.get("content_hash") or ""),
        "skill_set": [
            {
                "skill_id": str(item.get("skill_id") or ""),
                "expected_version": str(item.get("expected_version") or ""),
            }
            for item in skill_set or []
            if isinstance(item, dict)
        ],
    }


def _non_secret_tool_policy_subjects(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        subject = dict(item)
        subject.pop("mcp_server_config", None)
        projected.append(subject)
    return projected


def sandbox_reconciliation_payload(payload: Any) -> dict[str, Any]:
    """Persist a versioned snapshot with execution fields separated from metadata."""

    source_input = payload.input if isinstance(payload.input, dict) else {}
    return {
        "schema_version": RECONCILIATION_SNAPSHOT_SCHEMA_VERSION,
        "execution_payload": {
            "tenant_id": payload.tenant_id,
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "session_id": payload.session_id,
            "run_id": payload.run_id,
            "attempt_id": payload.attempt_id,
            "agent_id": payload.agent_id,
            "skill_id": payload.skill_id,
            "file_ids": [],
            "input": {
                **(
                    {
                        "_runtime_tool_policy_subjects": _non_secret_tool_policy_subjects(
                            source_input.get("_runtime_tool_policy_subjects")
                        )
                    }
                    if "_runtime_tool_policy_subjects" in source_input
                    else {}
                ),
                **(
                    {"platform_model_id": source_input["platform_model_id"]}
                    if "platform_model_id" in source_input
                    else {}
                ),
            },
            "execution_kind": payload.execution_kind,
            "trace_id": payload.trace_id,
            "skill_version": payload.skill_version,
            "release_decision": dict(payload.release_decision),
            "skill_manifests": [dict(item) for item in payload.skill_manifests],
            "context_snapshot_id": payload.context_snapshot_id,
            "model_id": payload.model_id,
            "model_value": payload.model_value,
            "schema_version": payload.schema_version,
            "agent_profile": _non_secret_agent_profile(payload.agent_profile),
        },
        "metadata": {"agent_profile_expected": bool(payload.agent_profile)},
    }


def restored_sandbox_run_payload(
    value: dict[str, Any],
    run_payload_factory: Callable[..., Any],
    result: dict[str, Any],
) -> Any:
    """Restore v1/v2 snapshots without allowing metadata into strict payloads."""

    serialized = dict(value)
    snapshot_schema_version = serialized.get("schema_version")
    is_versioned_snapshot = snapshot_schema_version == RECONCILIATION_SNAPSHOT_SCHEMA_VERSION
    if is_versioned_snapshot:
        execution_payload = serialized.get("execution_payload")
        metadata = serialized.get("metadata")
        if not isinstance(execution_payload, dict) or not isinstance(metadata, dict):
            raise ValueError("executor_reconciliation_snapshot_invalid")
        serialized = dict(execution_payload)
        agent_profile_expected = metadata.get("agent_profile_expected", False)
    else:
        agent_profile_expected = serialized.pop("agent_profile_expected", False)
    if not isinstance(agent_profile_expected, bool):
        raise ValueError("executor_reconciliation_agent_profile_expected_invalid")
    if is_versioned_snapshot and agent_profile_expected:
        # v2 snapshots intentionally carry identity-only profile metadata, never executable instructions.
        serialized["agent_profile"] = {}
    run_payload = run_payload_factory(**serialized)
    if agent_profile_expected and not run_payload.agent_profile:
        diagnostics = result.get("diagnostics")
        if not isinstance(diagnostics, list):
            diagnostics = []
            result["diagnostics"] = diagnostics
        if "agent_profile_transport_lost" not in diagnostics:
            diagnostics.append("agent_profile_transport_lost")
    return run_payload
