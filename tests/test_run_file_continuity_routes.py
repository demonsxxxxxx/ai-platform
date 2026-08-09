import base64

import pytest

from app import repositories as repository_module
from app.auth import AuthPrincipal
from app.models import ChatStreamRequest, CreateRunRequest
from app.routes import runs as runs_module


def _principal() -> AuthPrincipal:
    return AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
    )


def _replay_manifest(skill_id: str, version: str) -> dict[str, object]:
    content = f"---\nname: {skill_id}\ndescription: Pinned skill\n---\n\n# {skill_id}\n".encode()
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "skill_id": skill_id,
        "description": "Pinned skill",
        "version": version,
        "content_hash": version,
        "source": {"kind": "builtin", "asset_dir": skill_id},
        "files": [
            {
                "relative_path": "SKILL.md",
                "content_base64": encoded,
                "size_bytes": len(content),
            }
        ],
        "dependency_ids": [],
        "mcp_tool_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }


def test_run_and_chat_file_inputs_preserve_more_than_eight_current_files_with_shared_cap():
    file_ids = [f"file-{index}" for index in range(9)]

    assert CreateRunRequest(
        workspace_id="workspace-a",
        agent_id="general-agent",
        capability_id="general_chat",
        file_ids=file_ids,
    ).file_ids == file_ids
    assert ChatStreamRequest(message="review all files", file_ids=file_ids).file_ids == file_ids

    with pytest.raises(ValueError):
        CreateRunRequest(
            workspace_id="workspace-a",
            agent_id="general-agent",
            capability_id="general_chat",
            file_ids=[f"file-{index}" for index in range(33)],
        )
    with pytest.raises(ValueError):
        ChatStreamRequest(
            message="too many files",
            file_ids=[f"file-{index}" for index in range(33)],
        )
    with pytest.raises(ValueError):
        ChatStreamRequest(message="duplicate", file_ids=["file-a", "file-a"])


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["copy_run", "retry_run", "resume_run"])
async def test_replay_queue_preparation_preserves_prior_file_through_child_snapshot(
    monkeypatch,
    source,
):
    calls = {}
    skill_version = "hash-v1"
    copied = {
        "session_id": "session-a",
        "run_id": f"run-{source}",
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "workspace_id": "workspace-a",
        "file_ids": ["file-prior"],
        "input": {"message": "continue review", "copied_from_run_id": "run-source"},
        "executor_type": "claude-agent-worker",
        "skill_version": skill_version,
        "release_decision": {
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": False,
            "selected_version": skill_version,
            "selected_track": "manifest_pin",
        },
        "skill_manifests": [_replay_manifest("qa-file-reviewer", skill_version)],
    }

    async def allow(*_args, **_kwargs):
        return None

    async def record_context(_conn, **kwargs):
        calls["context"] = kwargs
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": f"ctx-{source}",
            "source": source,
            "message_count": 0,
            "file_count": 1,
            "memory_record_count": 0,
        }

    async def update_input(_conn, **kwargs):
        calls["execution_snapshot"] = kwargs["execution_snapshot"]

    monkeypatch.setattr(repository_module, "authorize_replay_run_capabilities", allow)
    monkeypatch.setattr(repository_module, "update_run_auth_snapshot", allow)
    monkeypatch.setattr(repository_module, "append_event", allow)
    monkeypatch.setattr(repository_module, "update_run_input_execution_snapshot", update_input)
    monkeypatch.setattr(runs_module, "record_initial_context_snapshot", record_context)

    queue_payload = await runs_module.prepare_copied_run_for_queue(
        object(),
        copied=copied,
        principal=_principal(),
        source=source,
        authorized_source_run_id="run-source",
    )

    assert calls["context"]["file_ids"] == ["file-prior"]
    assert calls["context"]["source"] == source
    assert calls["context"]["source_run_id"] == "run-source"
    assert queue_payload["file_ids"] == ["file-prior"]
    assert queue_payload["context_snapshot_id"] == f"ctx-{source}"
    assert queue_payload["context_snapshot"]["source"] == source
    assert calls["execution_snapshot"]["file_ids"] == ["file-prior"]
    assert calls["execution_snapshot"]["context_snapshot_id"] == f"ctx-{source}"
