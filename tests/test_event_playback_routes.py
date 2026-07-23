from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.auth import AuthPrincipal
from app.main import create_app
from app.run_projection import public_terminal_detail, run_event_response
from app.settings import Settings


@asynccontextmanager
async def fake_transaction():
    yield object()


def headers():
    return {
        "X-AI-User-ID": "user-a",
        "X-AI-User-Name": "User A",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "tenant-a",
    }


def admin_headers():
    return {
        **headers(),
        "X-AI-Roles": "admin",
    }


def run_row():
    return {
        "id": "run-a",
        "session_id": "session-a",
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "schema_version": "ai-platform.run.v1",
        "executor_schema_version": "ai-platform.executor-result.v1",
        "status": "running",
        "trace_id": "trace-a",
        "input_json": {"message": "review", "skill_id": "qa-file-reviewer"},
        "result_json": {},
        "error_code": None,
        "error_message": None,
    }


def event_row(
    *,
    event_id: str,
    sequence: int,
    event_type: str,
    stage: str = "agent",
    visible_to_user: bool = True,
    payload: dict | None = None,
    message: str = "公开进度",
):
    return {
        "id": event_id,
        "trace_id": "trace-a",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": sequence,
        "event_type": event_type,
        "stage": stage,
        "message": message,
        "severity": "info",
        "visible_to_user": visible_to_user,
        "error_code": None,
        "latency_ms": None,
        "input_token_count": 0,
        "output_token_count": 0,
        "total_token_count": 0,
        "estimated_cost_minor": 0,
        "payload_json": payload or {},
        "created_at": None,
    }


def artifact_row():
    return {
        "id": "artifact-a",
        "trace_id": "trace-a",
        "artifact_type": "docx",
        "label": "reviewed.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "storage_key": "tenants/tenant-a/workspaces/default/runs/run-a/reviewed.docx",
        "size_bytes": 123,
        "manifest_version": "ai-platform.artifact-manifest.v1",
        "manifest_json": {
            "storage_key": "tenants/tenant-a/private/reviewed.docx",
            "skill_id": "qa-file-reviewer",
            "safe_label": "reviewed.docx",
            "source_run_id": "run-a",
            "source_event_id": "evt-6",
            "source_step_id": "step-a",
            "source_file_id": "file-a",
            "producer_kind": "subagent",
            "producer_role": "reviewer",
            "checkpoint_id": "checkpoint-a",
            "subagent_id": "subagent-a",
            "command_sha256": "b" * 64,
        },
        "created_at": None,
    }


def step_row():
    return {
        "id": "step-a",
        "run_id": "run-a",
        "step_key": "review",
        "step_kind": "agent",
        "status": "running",
        "title": "审核 Word",
        "role": "reviewer",
        "sequence": 1,
        "payload_json": {
            "mcp_tool_ids": ["write.docx"],
            "resource_limits": {"max_seconds": 60},
            "sandbox_mode": "ephemeral",
            "work_dir": "/tmp/runtime/.claude/skills",
            "public_note": "正在审核",
        },
        "started_at": None,
        "finished_at": None,
        "created_at": None,
        "updated_at": None,
    }


async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
    return []


def test_public_terminal_detail_maps_only_fixed_actionable_categories():
    assert public_terminal_detail("failed", "claude_agent_sdk_runtime_error") == {
        "detail_kind": "failed",
        "detail_code": "model_service_unavailable",
        "message": "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
    }
    assert public_terminal_detail("failed", "executor_deadline_exceeded") == {
        "detail_kind": "failed",
        "detail_code": "run_timeout",
        "message": "任务执行超时。请缩小任务范围后重试。",
    }
    assert public_terminal_detail("failed", "secret_token_at_/home/private") == {
        "detail_kind": "failed",
        "detail_code": "run_failed",
        "message": "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    }
    assert public_terminal_detail("canceled") == {
        "detail_kind": "cancelled",
        "detail_code": "run_cancelled",
        "message": "任务已取消。取消前已产生的公开内容仍会保留。",
    }
    assert public_terminal_detail("succeeded", "claude_agent_sdk_runtime_error") is None


def test_run_event_response_projects_fixed_public_terminal_code_and_message():
    row = event_row(
        event_id="evt-error",
        sequence=9,
        event_type="error",
        stage="executor",
        payload={
            "visible_to_user": True,
            "private_payload": {"token": "secret"},
            "runtime_path": "/home/private/runtime.log",
            "result": {
                "message": "opaque executor diagnosis violet-lantern",
                "sdk_error": "adapter response violet-lantern",
                "error": {"message": "nested violet-lantern"},
            },
        },
        message="opaque executor diagnosis violet-lantern",
    )
    row["severity"] = "error"
    row["error_code"] = "claude_agent_sdk_runtime_error"
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
    )

    projected = run_event_response("run-a", row, principal=principal)

    assert projected["error_code"] == "model_service_unavailable"
    assert projected["message"] == "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。"
    assert projected["payload"] == {}
    serialized = str(projected)
    assert "claude_agent_sdk_runtime_error" not in serialized
    assert "secret" not in serialized
    assert "/home/private" not in serialized
    assert "violet-lantern" not in serialized


def test_run_event_response_keeps_nonterminal_public_activity():
    row = event_row(
        event_id="evt-progress",
        sequence=8,
        event_type="assistant_delta",
        stage="answer",
        payload={
            "delta": "已完成公开部分；",
            "source": "worker_answer_delta_v1",
            "visible_to_user": True,
            "severity": "info",
        },
        message="公开进度",
    )
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="tenant-a",
        roles=["user"],
    )

    projected = run_event_response("run-a", row, principal=principal)

    assert projected["message"] == "公开进度"
    assert projected["payload"] == row["payload_json"]


def test_failed_run_event_and_playback_routes_replace_unmarked_executor_diagnostics(monkeypatch):
    raw_terms = (
        "command=render-report --private-param=amber",
        "provider-model=solstice-3 sdk diagnostic",
        "reasoning-draft request-id=orchid digest=0123456789abcdef",
        "url=https://executor.internal.example.invalid/v1",
    )
    failed_run = run_row()
    failed_run.update(
        {
            "status": "failed",
            "result_json": {
                "message": raw_terms[0],
                "sdk_error": raw_terms[1],
                "error": {"message": raw_terms[2]},
            },
            "error_code": "claude_agent_sdk_runtime_error",
            "error_message": raw_terms[3],
        }
    )
    failed_event = event_row(
        event_id="evt-terminal",
        sequence=9,
        event_type="error",
        stage="executor",
        payload={
            "result": {
                "message": raw_terms[0],
                "sdk_error": raw_terms[1],
                "error": {"message": raw_terms[2]},
            },
            "error_message": raw_terms[3],
            "visible_to_user": True,
        },
        message=raw_terms[0],
    )
    failed_event["severity"] = "error"
    failed_event["error_code"] = "claude_agent_sdk_runtime_error"

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return failed_run

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        return [failed_event]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    events_response = client.get("/api/ai/runs/run-a/events", headers=headers())
    stream_response = client.get("/api/ai/runs/run-a/events/stream", headers=headers())
    playback_response = client.get("/api/ai/runs/run-a/playback", headers=headers())

    assert events_response.status_code == 200
    assert stream_response.status_code == 200
    assert playback_response.status_code == 200
    event = events_response.json()["events"][0]
    assert event["error_code"] == "model_service_unavailable"
    assert event["message"] == "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。"
    assert event["payload"] == {}
    playback = playback_response.json()
    assert playback["run"]["error_code"] == "model_service_unavailable"
    assert playback["run"]["error_message"] == event["message"]
    assert playback["events"][0] == event
    for rendered in (events_response.text, stream_response.text, playback_response.text):
        assert all(term not in rendered for term in raw_terms)


def test_run_events_route_supports_sequence_replay_cursor(monkeypatch):
    calls = {}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return {
            "id": run_id,
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        calls["event_args"] = {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "after_sequence": after_sequence,
            "limit": limit,
        }
        return [
            {
                "id": "evt-8",
                "trace_id": "trace-a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 8,
                "event_type": "assistant_delta",
                "stage": "agent",
                "message": "hello",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {"delta": "hello"},
                "created_at": None,
            },
            {
                "id": "evt-9",
                "trace_id": "trace-a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 9,
                "event_type": "worker_started",
                "stage": "worker",
                "message": "hidden",
                "severity": "info",
                "visible_to_user": False,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {"visible_to_user": False, "private_payload": {"token": "secret"}},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/events?after_sequence=7&limit=10", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["after_sequence"] == 7
    assert body["next_after_sequence"] == 9
    assert [item["id"] for item in body["events"]] == ["evt-8"]
    assert body["events"][0]["sequence"] == 8
    assert calls["event_args"] == {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "after_sequence": 7,
        "limit": 10,
    }


def test_run_playback_projection_redacts_ordinary_user_timeline(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return run_row()

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        assert after_sequence == 5
        assert limit == 50
        return [
            event_row(
                event_id="evt-6",
                sequence=6,
                event_type="skill_selected",
                payload={
                    "visible_to_user": True,
                    "storage_key": "tenants/tenant-a/private.docx",
                    "command_sha256": "a" * 64,
                    "mcp_tool_ids": ["write.docx"],
                    "used_skills_source": "executor_hook",
                    "public_note": "ok",
                },
                message=".claude/skills/qa-file-reviewer/scripts/run_qa_review.py",
            ),
            event_row(
                event_id="evt-7",
                sequence=7,
                event_type="checkpoint_created",
                stage="checkpoint",
                payload={
                    "visible_to_user": True,
                    "checkpoint_id": "checkpoint-a",
                    "source_step_id": "step-a",
                    "subagent_id": "subagent-a",
                    "skill_id": "qa-file-reviewer",
                    "storage_key": "tenants/tenant-a/private/checkpoint-a",
                    "command_sha256": "c" * 64,
                },
                message="Checkpoint created for reviewer",
            ),
            event_row(
                event_id="evt-8",
                sequence=8,
                event_type="subagent_completed",
                stage="subagent",
                payload={
                    "visible_to_user": True,
                    "subagent_id": "subagent-a",
                    "role": "reviewer",
                    "source_step_id": "step-a",
                    "skill_id": "qa-file-reviewer",
                    "used_skills_source": "executor_hook",
                    "work_dir": "/tmp/runtime/.claude/skills",
                },
                message="Reviewer subagent completed",
            ),
            event_row(
                event_id="evt-9",
                sequence=9,
                event_type="worker_started",
                visible_to_user=False,
                payload={"visible_to_user": False, "private_payload": {"token": "secret"}},
            ),
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [artifact_row()]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [step_row()]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/playback?after_sequence=5&limit=50", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-playback.v1"
    assert body["run"]["run_id"] == "run-a"
    assert body["run"]["skill_id"] is None
    assert body["run"]["capability_id"] == "document_review"
    assert body["next_after_sequence"] == 9
    assert [item["entry_type"] for item in body["timeline"]] == ["event", "event", "event", "artifact"]
    assert [item["event_type"] for item in body["events"]] == [
        "capability_selected",
        "checkpoint_created",
        "subagent_completed",
    ]
    assert body["events"][1]["payload"] == {
        "capability_id": "document_review",
        "checkpoint_id": "checkpoint-a",
        "source_step_id": "step-a",
        "subagent_id": "subagent-a",
        "visible_to_user": True,
    }
    assert body["events"][2]["payload"] == {
        "capability_id": "document_review",
        "role": "reviewer",
        "source_step_id": "step-a",
        "subagent_id": "subagent-a",
        "visible_to_user": True,
    }
    public_dump = str(body)
    assert "evt-9" not in public_dump
    assert "storage_key" not in public_dump
    assert "command_sha256" not in public_dump
    assert "mcp_tool_ids" not in public_dump
    assert "used_skills_source" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert ".claude/skills" not in public_dump
    assert "/tmp/" not in public_dump
    assert "qa-file-reviewer" not in public_dump
    assert body["steps"][0]["payload"] == {"public_note": "正在审核"}
    assert body["artifacts"][0]["download_url"] == "/api/ai/artifacts/artifact-a/download"
    assert body["artifacts"][0]["preview_url"] == "/api/ai/artifacts/artifact-a/preview"
    assert body["artifacts"][0]["lineage"] == {
        "source_event_id": "evt-6",
        "source_step_id": "step-a",
        "source_file_id": "file-a",
        "producer_kind": "subagent",
        "producer_role": "reviewer",
        "checkpoint_id": "checkpoint-a",
        "subagent_id": "subagent-a",
    }


def test_run_provenance_snapshot_links_steps_checkpoints_and_artifacts(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return run_row()

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [artifact_row()]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        row = step_row()
        row["payload_json"] = {
            **row["payload_json"],
            "checkpoint_id": "checkpoint-a",
            "checkpoint_reused": True,
            "subagent_id": "subagent-a",
            "depends_on": ["plan"],
            "output": "sanitized reviewer output",
        }
        row["status"] = "succeeded"
        return [row]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/provenance", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-provenance.v1"
    assert body["run"]["run_id"] == "run-a"
    assert body["run"]["skill_id"] is None
    assert body["graph"]["counts"] == {
        "steps": 1,
        "artifacts": 1,
        "checkpoints": 1,
        "subagents": 1,
    }
    assert body["subagents"] == [
        {
            "subagent_id": "subagent-a",
            "role": "reviewer",
            "step_ids": ["step-a"],
            "statuses": ["succeeded"],
            "checkpoint_ids": ["checkpoint-a"],
            "artifact_ids": ["artifact-a"],
        }
    ]
    assert body["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-a",
            "step_ids": ["step-a"],
            "artifact_ids": ["artifact-a"],
            "reused": True,
        }
    ]
    assert body["artifact_tree"][0]["artifact_id"] == "artifact-a"
    assert body["artifact_tree"][0]["produced_by_step_id"] == "step-a"
    assert body["artifact_tree"][0]["checkpoint_id"] == "checkpoint-a"
    assert body["artifact_tree"][0]["subagent_id"] == "subagent-a"
    public_dump = str(body)
    assert "storage_key" not in public_dump
    assert "command_sha256" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "/tmp/" not in public_dump
    assert "qa-file-reviewer" not in public_dump


def test_run_provenance_snapshot_projects_operational_artifact_tree(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return run_row()

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [artifact_row()]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        row = step_row()
        row["payload_json"] = {
            **row["payload_json"],
            "checkpoint_id": "checkpoint-a",
            "checkpoint_reused": True,
            "subagent_id": "subagent-a",
            "output": "sanitized reviewer output",
        }
        row["status"] = "succeeded"
        return [row]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/provenance", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_tree"] == [
        {
            "node_id": "artifact-a",
            "node_kind": "artifact",
            "artifact_id": "artifact-a",
            "artifact_type": "docx",
            "label": "reviewed.docx",
            "produced_by_step_id": "step-a",
            "source_step_id": "step-a",
            "parent_id": "step-a",
            "parent_kind": "step",
            "children_ids": [],
            "producer_kind": "subagent",
            "producer_role": "reviewer",
            "checkpoint_id": "checkpoint-a",
            "subagent_id": "subagent-a",
            "lineage": {
                "source_event_id": "evt-6",
                "source_step_id": "step-a",
                "source_file_id": "file-a",
                "producer_kind": "subagent",
                "producer_role": "reviewer",
                "checkpoint_id": "checkpoint-a",
                "subagent_id": "subagent-a",
            },
            "gaps": [],
        }
    ]
    assert body["graph"]["edges"] == [
        {"source_id": "step-a", "target_id": "checkpoint-a", "edge_kind": "step_checkpoint"},
        {"source_id": "subagent-a", "target_id": "step-a", "edge_kind": "subagent_step"},
        {"source_id": "step-a", "target_id": "artifact-a", "edge_kind": "produced_artifact"},
        {"source_id": "checkpoint-a", "target_id": "artifact-a", "edge_kind": "checkpoint_artifact"},
        {"source_id": "subagent-a", "target_id": "artifact-a", "edge_kind": "subagent_artifact"},
    ]
    assert body["graph"]["gaps"] == []


def test_run_provenance_snapshot_reports_artifact_tree_gaps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return run_row()

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        row = artifact_row()
        row["id"] = "artifact-orphan"
        row["manifest_json"] = {
            "schema_version": "ai-platform.artifact-manifest.v1",
            "artifact_type": "docx",
            "source_run_id": "run-a",
            "source_step_id": "step-missing",
            "producer_kind": "agent",
            "checkpoint_id": "checkpoint-orphan",
        }
        return [row]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/provenance", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_tree"][0]["artifact_id"] == "artifact-orphan"
    assert body["artifact_tree"][0]["source_step_id"] == "step-missing"
    assert body["artifact_tree"][0]["produced_by_step_id"] is None
    assert body["artifact_tree"][0]["parent_id"] == "checkpoint-orphan"
    assert body["artifact_tree"][0]["parent_kind"] == "checkpoint"
    assert body["artifact_tree"][0]["gaps"] == ["producer_step_missing"]
    assert body["graph"]["gaps"] == [
        {"node_id": "artifact-orphan", "node_kind": "artifact", "gaps": ["producer_step_missing"]}
    ]
    assert body["graph"]["edges"] == [
        {"source_id": "checkpoint-orphan", "target_id": "artifact-orphan", "edge_kind": "checkpoint_artifact"}
    ]
    public_dump = str(body)
    assert "storage_key" not in public_dump
    assert "qa-file-reviewer" not in public_dump


def test_run_provenance_snapshot_fail_closes_dirty_artifact_lineage_graph_ids(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return run_row()

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        row = artifact_row()
        row["manifest_json"] = {"schema_version": "ai-platform.artifact-manifest.v1", "artifact_type": "docx"}
        return [row]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    def dirty_artifact_card(row, principal=None):
        return {
            "id": "artifact-a",
            "artifact_id": "artifact-a",
            "artifact_type": "docx",
            "label": "reviewed.docx",
            "lineage": {
                "source_step_id": "qa-file-reviewer-step",
                "checkpoint_id": "qa-file-reviewer-checkpoint",
                "subagent_id": "qa-file-reviewer-subagent",
                "producer_kind": "agent",
                "storage_key": "tenants/tenant-a/private/reviewed.docx",
                "runtime_private_payload": {"token": "secret-token"},
                "command_sha256": "a" * 64,
            },
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.run_provenance.artifact_card", dirty_artifact_card)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/provenance", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_tree"] == [
        {
            "node_id": "artifact-a",
            "node_kind": "artifact",
            "artifact_id": "artifact-a",
            "artifact_type": "docx",
            "label": "reviewed.docx",
            "produced_by_step_id": None,
            "source_step_id": None,
            "parent_id": None,
            "parent_kind": None,
            "children_ids": [],
            "producer_kind": "agent",
            "producer_role": None,
            "checkpoint_id": None,
            "subagent_id": None,
            "lineage": {"producer_kind": "agent"},
            "gaps": [
                "artifact_checkpoint_unsafe",
                "artifact_source_step_unsafe",
                "artifact_subagent_unsafe",
            ],
        }
    ]
    assert body["graph"]["edges"] == []
    assert body["graph"]["gaps"] == [
        {
            "node_id": "artifact-a",
            "node_kind": "artifact",
            "gaps": [
                "artifact_checkpoint_unsafe",
                "artifact_source_step_unsafe",
                "artifact_subagent_unsafe",
            ],
        }
    ]
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump
    assert "storage_key" not in public_dump
    assert "runtime_private_payload" not in public_dump
    assert "secret-token" not in public_dump
    assert "command_sha256" not in public_dump


def test_run_provenance_snapshot_rejects_unsafe_step_graph_ids(monkeypatch):
    unsafe_hash = "a" * 64

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return run_row()

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        row = step_row()
        row["payload_json"] = {
            **row["payload_json"],
            "checkpoint_id": unsafe_hash,
            "checkpoint_reused": True,
            "subagent_id": "qa-file-reviewer",
            "private_payload": {"token": "secret-token"},
            "runtime_private_payload": {"token": "secret-token"},
            "executor_payload": {"token": "secret-token"},
        }
        row["status"] = "succeeded"
        return [row]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/provenance", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["graph"]["counts"] == {
        "steps": 1,
        "artifacts": 0,
        "checkpoints": 0,
        "subagents": 0,
    }
    assert body["checkpoints"] == []
    assert body["subagents"] == []
    assert body["steps"][0]["payload"] == {"public_note": "正在审核"}
    public_dump = str(body)
    assert unsafe_hash not in public_dump
    assert "qa-file-reviewer" not in public_dump
    assert "private_payload" not in public_dump
    assert "runtime_private_payload" not in public_dump
    assert "executor_payload" not in public_dump
    assert "secret-token" not in public_dump


def test_run_provenance_snapshot_returns_not_found_for_unauthorized_run(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "missing-run")
        return None

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        raise AssertionError("artifacts must not be listed when run is unauthorized")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        raise AssertionError("steps must not be listed when run is unauthorized")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/missing-run/provenance", headers=headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "run_not_found"}


def test_run_playback_projects_tool_permission_card_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return run_row()

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        return [
            event_row(
                event_id="evt-10",
                sequence=10,
                event_type="tool_permission_requested",
                stage="tool_policy",
                payload={
                    "visible_to_user": True,
                    "permission_request_id": "tpr-a",
                    "tool_id": "bash",
                    "tool_call_id": "call-a",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                    "reason": "需要运行写入命令",
                    "command": "python write_business_system.py --id 123",
                    "raw_command": "python write_business_system.py --id 123",
                    "command_sha256": "a" * 64,
                    "fingerprint": "bash:write-system",
                },
                message="工具调用需要权限决策",
            )
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/playback", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["events"][0]["event_type"] == "tool_permission_card"
    card = body["events"][0]["payload"]["tool_permission_card"]
    assert card == {
        "schema_version": "ai-platform.tool-permission-card.v1",
        "permission_request_id": "tpr-a",
        "run_id": "run-a",
        "tool_id": "bash",
        "tool_call_id": "call-a",
        "action": "execute",
        "risk_level": "high",
        "write_capable": True,
        "reason": "需要运行写入命令",
        "status": "pending",
        "decision": None,
    }
    public_dump = str(body)
    assert "write_business_system" not in public_dump
    assert "command_sha256" not in public_dump
    assert "fingerprint" not in public_dump


def test_run_events_redacts_malformed_tool_permission_internal_payloads(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        return [
            event_row(
                event_id="evt-malformed-tool-permission",
                sequence=12,
                event_type="tool_permission_requested",
                stage="tool_policy",
                payload={
                    "visible_to_user": True,
                    "tool_id": "bash",
                    "tool_call_id": "call-a",
                    "request_payload": {"query": "SOP", "token": "smoke-secret-token"},
                    "decision_payload": {"token": "smoke-secret-token"},
                    "reason": "legacy payload without request id",
                },
                message="工具调用需要权限决策",
            )
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/events", headers=headers())

    assert response.status_code == 200
    public_dump = response.text
    assert "request_payload" not in public_dump
    assert "decision_payload" not in public_dump
    assert "smoke-secret-token" not in public_dump


def test_run_events_projects_tool_permission_decision_card_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        return [
            event_row(
                event_id="evt-11",
                sequence=11,
                event_type="tool_permission_decided",
                stage="tool_policy",
                payload={
                    "visible_to_user": True,
                    "permission_request_id": "tpr-a",
                    "tool_id": "bash",
                    "tool_call_id": "call-a",
                    "action": "execute",
                    "risk_level": "high",
                    "write_capable": True,
                    "reason": "允许本轮执行",
                    "status": "decided",
                    "decision": "allow_once",
                    "command": "python write_business_system.py --id 123",
                    "raw_command": "python write_business_system.py --id 123",
                    "command_sha256": "a" * 64,
                    "fingerprint": "bash:write-system",
                },
                message="工具权限已决策",
            )
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/events", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["events"][0]["event_type"] == "tool_permission_card"
    card = body["events"][0]["payload"]["tool_permission_card"]
    assert card["risk_level"] == "high"
    assert card["write_capable"] is True
    assert card["status"] == "decided"
    assert card["decision"] == "allow_once"
    public_dump = str(body)
    assert "write_business_system" not in public_dump
    assert "command_sha256" not in public_dump
    assert "fingerprint" not in public_dump


def test_run_playback_projection_keeps_admin_runtime_controls(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return run_row()

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        return [
            event_row(
                event_id="evt-1",
                sequence=1,
                event_type="worker_started",
                visible_to_user=False,
                payload={"visible_to_user": False, "command_sha256": "a" * 64},
            )
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [artifact_row()]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [step_row()]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/playback", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["skill_id"] == "qa-file-reviewer"
    assert body["events"][0]["event_type"] == "worker_started"
    assert body["steps"][0]["mcp_tool_ids"] == ["write.docx"]
    assert body["steps"][0]["resource_limits"] == {"max_seconds": 60}
    assert body["steps"][0]["sandbox_mode"] == "ephemeral"
