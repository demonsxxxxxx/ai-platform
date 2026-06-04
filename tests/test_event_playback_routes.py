from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
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
    assert body["artifacts"][0]["lineage"] == {
        "source_run_id": "run-a",
        "source_event_id": "evt-6",
        "source_step_id": "step-a",
        "source_file_id": "file-a",
        "producer_kind": "subagent",
        "producer_role": "reviewer",
        "checkpoint_id": "checkpoint-a",
        "subagent_id": "subagent-a",
    }


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
        "decision_endpoint": "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        "decision_options": ["allow_once", "allow_for_run", "deny"],
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
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/playback", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["skill_id"] == "qa-file-reviewer"
    assert body["events"][0]["event_type"] == "worker_started"
    assert body["steps"][0]["mcp_tool_ids"] == ["write.docx"]
    assert body["steps"][0]["resource_limits"] == {"max_seconds": 60}
    assert body["steps"][0]["sandbox_mode"] == "ephemeral"
