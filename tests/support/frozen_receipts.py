"""Synthetic frozen receipt facts shared by focused recovery contract tests."""
from app.runs.domain.execution_spec import ExecutionSpec


SCOPE = {"workspace_id": "workspace-a", "user_id": "user-a", "session_id": "session-a", "agent_id": "agent-a"}
RESTORE = {
    "tenant_id": "tenant-a", "run_id": "run-a", "attempt_id": "attempt-a",
    "lease_id": "lease-a", "claim_token": "claim-a", "expected_owner_generation": 4,
    "scope": SCOPE,
}


def frozen_spec(**overrides):
    """Compile a v3 spec; pass `legacy_v2_input_capacity` for the frozen v2 shape."""

    v2_input_capacity = overrides.pop("legacy_v2_input_capacity", None)
    payload = {
        "schema_version": "ai-platform.execution-spec.v3",
        "run_payload_schema_version": "ai-platform.run-payload.v2",
        "tenant_id": "tenant-a", "run_id": "run-a", **SCOPE,
        "execution_kind": "harness_chat", "skill_id": None,
        "file_ids": [], "input": {"message": "original current user"},
        "executor_type": "claude-agent-worker", "trace_id": "trace-a",
        "skill_version": "", "release_decision": {}, "skill_manifests": [],
        "context_snapshot_id": "context-a",
        "context_snapshot": {"context_snapshot_id": "context-a"},
        "context_pack": {"conversation_context": {
            "execution_mode": "native_resume", "provider_epoch_id": "epoch-a",
            "provider_session_id": "00000000-0000-4000-8000-000000000001",
            "source_sha256": "a" * 64, "messages": [],
            "selected_message_count": 0, "selected_turn_count": 0,
        }},
        "model_id": "model-a", "model_value": "claude-test",
        "model_gateway_revision": 3, "model_auto_compact_window": 200000,
        "model_max_output_tokens": 4096, "agent_profile": {},
    }
    if v2_input_capacity is not None:
        # Historical v2 attempts froze an input capacity, not a window.
        payload.pop("model_auto_compact_window")
        payload["schema_version"] = "ai-platform.execution-spec.v2"
        payload["model_max_input_tokens"] = v2_input_capacity
    return ExecutionSpec.compile({**payload, **overrides})


def frozen_attempt(spec=None, **overrides):
    spec = spec or frozen_spec()
    data = spec.to_mapping()
    return {
        "id": "attempt-a", "tenant_id": data["tenant_id"], "run_id": data["run_id"],
        "status": "running", "owner_kind": "queue_worker", "owner_id": "worker-original",
        "owner_generation": 4, "queue_attempt_id": "qat-a",
        "execution_spec_json": data, "execution_spec_canonical_json": spec.canonical_json.decode(),
        "execution_spec_sha256": spec.spec_sha256, "execution_spec_schema_version": data["schema_version"],
        "run_cancel_requested_at": None,
        **{"run_" + key: data[key] for key in (
            "execution_kind", "skill_id", "executor_type", "context_snapshot_id", "model_id", "model_value",
            "model_gateway_revision",
        )},
        "run_auto_compact_window": data.get(
            "model_auto_compact_window",
            # A historical v2 spec froze an input capacity the runner clamped.
            min(1_000_000, max(100_000, data.get("model_max_input_tokens") or 100_000)),
        ),
        "run_max_output_tokens": data["model_max_output_tokens"], **overrides,
    }


def frozen_run(row=None):
    row = row or frozen_attempt()
    return {
        "id": row["run_id"], "tenant_id": row["tenant_id"], **SCOPE,
        "status": "running", "cancel_requested_at": row.get("run_cancel_requested_at"),
        **{key.removeprefix("run_"): value for key, value in row.items() if key.startswith("run_")},
    }
