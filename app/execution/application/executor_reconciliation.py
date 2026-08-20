from collections.abc import Callable
from typing import Any


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
                key: source_input[key]
                for key in ("_runtime_tool_policy_subjects", "platform_model_id")
                if key in source_input
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
