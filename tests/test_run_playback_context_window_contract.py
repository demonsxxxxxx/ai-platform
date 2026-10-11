"""The backend route and mounted frontend share the same public wire fixtures."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import AuthPrincipal
from app.routes import runs


WINDOW_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "run_playback_context_windows.json").read_text(encoding="utf-8")
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["complete", "trimmed", "degraded"])
async def test_playback_route_matches_frontend_context_window_fixture(monkeypatch, status):
    expected = WINDOW_FIXTURES[status]
    window = expected["context_ref"]["context_window"]
    principal = AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a")
    bound_reads = []

    @asynccontextmanager
    async def transaction():
        yield SimpleNamespace(execute=object())

    async def authorized_run(_conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return {
            "id": run_id,
            "workspace_id": "workspace-a",
            "session_id": "session-a",
            "agent_id": "general-agent",
            "skill_id": "skill-private",
            "status": "succeeded",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
            # This older embedded snapshot is not a playback authority.
            "input_json": {"context_snapshot": {"context_manifest": {
                "selection": {"status": "complete", "history_candidate_count": 999},
                "files": [{"id": "file-unbound-private", "name": "unbound.txt"}],
            }}},
            "result_json": {},
        }

    async def bound_snapshot(_conn, **scope):
        bound_reads.append(scope)
        if status == "degraded":
            return None
        return {
            "id": "ctx-bound-private",
            "included_message_ids": ["message-private"],
            "included_file_ids": ["file-private"],
            "included_artifact_ids": ["artifact-private"],
            "payload_json": {
                "context_manifest": {
                    "selection": {key: value for key, value in window.items() if key != "selected_file_names"},
                    "files": [
                        {"id": "file-private", "name": f"C:\\private\\{name}"}
                        for name in window["selected_file_names"]
                    ],
                },
                "runtime_path": "/private/runtime",
                "skill_ids": ["skill-private"],
            },
        }

    async def empty_rows(*_args, **_kwargs):
        return []

    async def reject_latest(*_args, **_kwargs):
        raise AssertionError("playback may not read an unbound latest snapshot")

    monkeypatch.setattr(runs, "transaction", transaction)
    monkeypatch.setattr(runs.runs_creation, "get_authorized_run", authorized_run)
    monkeypatch.setattr(runs.streaming_run_events, "list_run_events", empty_rows)
    monkeypatch.setattr(runs.artifacts_records, "list_run_artifacts", empty_rows)
    monkeypatch.setattr(runs.runs_steps, "list_run_steps", empty_rows)
    monkeypatch.setattr(runs.context_snapshot_postgres, "get_bound_executor_context_snapshot", bound_snapshot)
    monkeypatch.setattr(runs.context_snapshot_postgres, "list_context_snapshots", reject_latest)

    response = await runs.get_run_playback("run-a", principal=principal)

    assert {key: response[key] for key in expected} == expected
    assert bound_reads == [{
        "tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-a",
        "session_id": "session-a", "run_id": "run-a",
    }]
    projected = json.dumps(response["context_ref"], ensure_ascii=False)
    for private_value in ("private", "skill_ids", "runtime_path", "payload_json", "unbound.txt", "999"):
        assert private_value not in projected
