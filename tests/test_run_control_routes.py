from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


@asynccontextmanager
async def fake_transaction():
    yield object()


def headers():
    return {
        "x-ai-user-id": "user-a",
        "x-ai-user-name": "User A",
        "x-ai-tenant-id": "default",
        "x-ai-roles": "user",
        "x-ai-gateway-secret": "test-secret",
    }


def admin_headers():
    values = headers()
    values["x-ai-user-id"] = "admin-a"
    values["x-ai-user-name"] = "Admin A"
    values["x-ai-roles"] = "admin"
    return values


def sandbox_lease_row(
    *,
    run_id: str = "run_active",
    lease_id: str | None = None,
    user_id: str = "user-a",
    provider: str = "fake",
    lease_payload_json: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = lease_payload_json or {
        "container_id": f"exec-{run_id}",
        "container_name": f"executor-exec-{run_id}",
        "executor_url": "http://executor.test",
        "workspace_host_path": f"C:/runtime/{run_id}/workspace",
        "workspace_container_path": "/workspace",
    }
    return {
        "id": lease_id or f"lease-{run_id}",
        "tenant_id": "default",
        "workspace_id": "workspace-a",
        "user_id": user_id,
        "session_id": "session-a",
        "run_id": run_id,
        "trace_id": f"trace-{run_id}",
        "sandbox_mode": "ephemeral",
        "provider": provider,
        "status": "active",
        "browser_enabled": False,
        "lease_payload_json": payload,
    }


class RecordingSandboxProvider:
    def __init__(self, calls):
        self.calls = calls

    async def stop(self, lease, *, reason):
        self.calls.append(("stop", lease, reason))
        return type("StopResult", (), {"container_id": lease.container_id, "status": "stopped", "message": reason})()


class FailingSandboxProvider:
    async def stop(self, lease, *, reason):
        return type(
            "StopResult",
            (),
            {"container_id": lease.container_id, "status": "failed", "message": "Container stop failed"},
        )()


class ProviderByName:
    def __init__(self, calls):
        self.calls = calls

    def __call__(self, provider_name=None):
        if provider_name == "docker":
            return FailingSandboxProvider()
        return RecordingSandboxProvider(self.calls)


class EmptyBuiltinRegistry:
    def __init__(self, root):
        self.root = root

    def list_builtin_skills(self):
        return []


async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
    assert tenant_id == "default"
    return {
        "agent_id": agent_id,
        "skill_id": skill_id,
        "executor_type": "claude-agent-worker",
        "skill_version": "2.0.0",
    }


def test_sse_heartbeat_event_shape():
    from app.routes.runs import sse

    text = sse("heartbeat", {"run_id": "run_a", "status": "running"}, event_id="run_a:heartbeat:1")

    assert "event: heartbeat" in text
    assert '"status": "running"' in text


def test_copy_run_creates_new_queued_run(monkeypatch):
    calls = []

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append((tenant_id, user_id, run_id))
        return {
            "session_id": "ses_new",
            "run_id": "run_new",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "workspace_id": "default",
            "file_ids": [],
            "input": {
                "message": "hello",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "code", "role": "coding", "title": "实现代码"},
                    {
                        "step_key": "verify",
                        "role": "test",
                        "title": "验证结果",
                        "depends_on": ["code"],
                        "sandbox_mode": "ephemeral",
                        "browser_enabled": True,
                        "resource_limits": {"max_tool_calls": 3},
                    },
                ],
                "resume": {
                    "copied_from_run_id": "run_old",
                    "completed_step_outputs": {"code": "code output"},
                },
            },
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-old",
            "release_policy_version": "hash-old",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-old",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_enqueue_run(payload):
        calls.append(("queue", payload))
        return 1

    async def fake_get_queue_insight(tenant_id):
        assert tenant_id == "default"
        return {
            "tenant_id": tenant_id,
            "queued": 3,
            "running": 1,
            "worker_capacity": 1,
            "reason": "worker_capacity_full",
        }

    async def fake_upsert_run_step(conn, **kwargs):
        calls.append(("step", kwargs))
        return f"step-{kwargs['step_key']}"

    async def fake_update_run_input_skill_version(conn, **kwargs):
        calls.append(("skill_version", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))

    async def fake_record_context(conn, **kwargs):
        calls.append(("context", kwargs))
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_copy_route",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert skill_id == "general-chat"
        assert version == "hash-old"
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "General chat",
            "source": {
                "kind": "uploaded",
                "storage_key": "tenants/default/skills/general-chat/versions/hash-old/package.zip",
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.BuiltinSkillRegistry", EmptyBuiltinRegistry)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.repositories.upsert_run_step", fake_upsert_run_step)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_old/copy", headers=headers())

    assert response.status_code == 200
    assert response.json()["run_id"] == "run_new"
    assert response.json()["queue_position"] == 1
    assert response.json()["queue_insight"] == {
        "tenant_id": "default",
        "queued": 3,
        "running": 1,
        "worker_capacity": 1,
        "reason": "worker_capacity_full",
    }
    queued_payload = calls[-1][1]
    assert queued_payload["run_id"] == "run_new"
    assert queued_payload["skill_version"] == "hash-old"
    assert queued_payload["skill_manifests"][0]["source"]["kind"] == "uploaded"
    assert queued_payload["context_snapshot_id"] == "ctx_copy_route"
    assert queued_payload["context_snapshot"]["source"] == "copy_run"
    assert queued_payload["input"]["resume"]["completed_step_outputs"] == {"code": "code output"}
    context_calls = [item[1] for item in calls if item[0] == "context"]
    assert context_calls[0]["source"] == "copy_run"
    assert context_calls[0]["tenant_id"] == "default"
    assert context_calls[0]["workspace_id"] == "default"
    assert context_calls[0]["user_id"] == "user-a"
    assert context_calls[0]["session_id"] == "ses_new"
    assert context_calls[0]["run_id"] == "run_new"
    assert context_calls[0]["agent_id"] == "general-agent"
    assert context_calls[0]["skill_id"] == "general-chat"
    assert context_calls[0]["input_payload"] == queued_payload["input"]
    assert context_calls[0]["message_ids"] == []
    assert context_calls[0]["file_ids"] == []
    skill_version_calls = [item[1] for item in calls if item[0] == "skill_version"]
    assert skill_version_calls[0]["skill_version"] == "hash-old"
    step_calls = [item[1] for item in calls if item[0] == "step"]
    assert [(item["step_key"], item["status"]) for item in step_calls] == [
        ("code", "pending"),
        ("verify", "pending"),
    ]
    assert step_calls[0]["payload_json"]["checkpoint_reuse_pending"] is True
    assert "checkpoint_reused" not in step_calls[0]["payload_json"]
    assert "output" not in step_calls[0]["payload_json"]
    assert step_calls[1]["payload_json"]["depends_on"] == ["code"]
    assert step_calls[1]["payload_json"]["sandbox_mode"] == "ephemeral"
    assert step_calls[1]["payload_json"]["browser_enabled"] is True
    assert step_calls[1]["payload_json"]["resource_limits"] == {"max_tool_calls": 3}


def test_retry_run_creates_queued_retry_from_failed_source(monkeypatch):
    calls = {"retry": [], "enqueue": [], "step": []}

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls["retry"].append((tenant_id, user_id, run_id))
        return {
            "session_id": "ses-old",
            "run_id": "run-retry",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "workspace_id": "default",
            "file_ids": [],
            "input": {
                "message": "retry",
                "copied_from_run_id": run_id,
                "multi_agent_steps": [{"step_key": "retry-code", "role": "coding"}],
            },
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-a",
            "release_policy_version": "",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-a",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        assert skill_id == "general-chat"
        assert input_payload["copied_from_run_id"] == "run-failed"
        assert release_policy_version == ""
        return [{"skill_id": skill_id, "content_hash": "hash-a"}]

    async def fake_record_initial_context_snapshot(conn, **kwargs):
        assert kwargs["source"] == "retry_run"
        return {"context_snapshot_id": "ctx-retry", "source": "retry_run"}

    async def fake_enqueue_run(payload):
        calls["enqueue"].append(payload)
        return 3

    async def fake_get_queue_insight(tenant_id):
        return {"tenant_id": tenant_id}

    async def fake_count_active_runs_for_user(conn, *, tenant_id, user_id):
        calls["active_limit"] = (tenant_id, user_id)
        return 0

    async def fake_update_run_input_skill_version(conn, **kwargs):
        return None

    async def fake_append_event(conn, **kwargs):
        return None

    async def fake_upsert_run_step(conn, **kwargs):
        calls["step"].append(kwargs)
        return f"step-{kwargs['step_key']}"

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.count_active_runs_for_user", fake_count_active_runs_for_user)
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_initial_context_snapshot)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.repositories.upsert_run_step", fake_upsert_run_step)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-failed/retry", headers=headers())

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-retry"
    assert response.json()["session_id"] == "ses-old"
    assert response.json()["status"] == "queued"
    assert response.json()["queue_position"] == 3
    assert calls["retry"] == [("default", "user-a", "run-failed")]
    assert calls["active_limit"] == ("default", "user-a")
    assert calls["enqueue"][0]["run_id"] == "run-retry"
    assert calls["enqueue"][0]["context_snapshot_id"] == "ctx-retry"
    assert calls["step"][0]["payload_json"]["seeded_from"] == "retry_run"


def test_retry_run_rejects_when_user_active_run_limit_is_reached(monkeypatch):
    calls = []

    class LimitSettings:
        max_active_runs_per_user = 1

    async def fake_count_active_runs_for_user(conn, *, tenant_id, user_id):
        calls.append(("count", tenant_id, user_id))
        return 1

    async def fail_retry_run_as_new_task(*args, **kwargs):
        calls.append(("retry", kwargs))
        raise AssertionError("retry must not create a copied run after admission rejection")

    async def fail_enqueue_run(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("retry must not enqueue after admission rejection")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.get_settings", lambda: LimitSettings())
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.count_active_runs_for_user", fake_count_active_runs_for_user)
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fail_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-failed/retry", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "user_active_run_limit_exceeded"
    assert calls == [("count", "default", "user-a")]


def test_retry_run_returns_not_found_for_stale_source_capability(monkeypatch):
    from app.repositories import RepositoryNotFoundError

    calls = []

    async def fake_count_active_runs_for_user(conn, *, tenant_id, user_id):
        calls.append(("count", tenant_id, user_id))
        return 0

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("retry", tenant_id, user_id, run_id))
        raise RepositoryNotFoundError("agent_or_skill_not_found")

    async def fail_enqueue_run(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("retry must not enqueue stale source capabilities")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.count_active_runs_for_user", fake_count_active_runs_for_user)
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue_run)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post("/api/ai/runs/run-failed/retry", headers=headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "agent_or_skill_not_found"
    assert calls == [("count", "default", "user-a"), ("retry", "default", "user-a", "run-failed")]


def test_retry_run_rejects_non_retryable_source_without_enqueue(monkeypatch):
    from app.repositories import RepositoryConflictError

    calls = []

    async def fake_count_active_runs_for_user(conn, *, tenant_id, user_id):
        calls.append(("count", tenant_id, user_id))
        return 0

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("retry", tenant_id, user_id, run_id))
        raise RepositoryConflictError("status_not_retryable")

    async def fake_enqueue_run(payload):
        calls.append(("enqueue", payload))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.count_active_runs_for_user", fake_count_active_runs_for_user)
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-running/retry", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "status_not_retryable"
    assert calls == [("count", "default", "user-a"), ("retry", "default", "user-a", "run-running")]


def test_retry_run_returns_not_found_without_enqueue(monkeypatch):
    calls = []

    async def fake_count_active_runs_for_user(conn, *, tenant_id, user_id):
        calls.append(("count", tenant_id, user_id))
        return 0

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("retry", tenant_id, user_id, run_id))
        return None

    async def fake_enqueue_run(payload):
        calls.append(("enqueue", payload))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.count_active_runs_for_user", fake_count_active_runs_for_user)
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/missing-run/retry", headers=headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"
    assert calls == [("count", "default", "user-a"), ("retry", "default", "user-a", "missing-run")]


def test_copy_run_plan_previews_reused_and_rerun_steps(monkeypatch):
    calls = {}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-old",
            "workspace_id": "default",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {
                "input": {
                    "message": "build feature",
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [
                        {"step_key": "code", "role": "coding", "title": "实现代码"},
                        {"step_key": "verify", "role": "test", "title": "验证结果", "depends_on": ["code"]},
                    ],
                }
            },
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "step_key": "code",
                "role": "coding",
                "title": "实现代码",
                "status": "succeeded",
                "payload_json": {"output": "code output"},
            },
            {
                "step_key": "verify",
                "role": "test",
                "title": "验证结果",
                "status": "failed",
                "payload_json": {"error": "tests failed"},
            },
        ]

    async def fake_get_queue_insight(tenant_id):
        calls["queue_insight_tenant_id"] = tenant_id
        return {
            "tenant_id": tenant_id,
            "depths": {
                "tenant_queued": 2,
                "tenant_processing": 1,
            },
            "workers": {"active": 1},
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run_old/copy/plan", headers=headers())

    assert response.status_code == 200
    data = response.json()
    assert data["requires_confirmation"] is True
    assert data["confirmation_card"]["title"] == "确认恢复执行"
    assert data["confirmation_card"]["summary"] == "将复制为新任务，复用 1 个已完成步骤，重跑 1 个未完成步骤。"
    assert data["confirmation_card"]["steps"] == [
        {
            "step_key": "code",
            "role": "已完成 · 将复用",
            "title": "实现代码",
            "depends_on": [],
        },
        {
            "step_key": "verify",
            "role": "失败 · 将重跑",
            "title": "验证结果",
            "depends_on": ["code"],
        },
    ]
    assert data["queue_insight"]["tenant_id"] == "default"
    assert data["queue_insight"]["depths"]["tenant_queued"] == 2
    assert data["queue_insight"]["workers"]["active"] == 1
    assert calls["queue_insight_tenant_id"] == "default"
    assert data["confirmation_card"]["skills"] == [{"capability_id": "general_chat", "label": "通用聊天"}]
    assert "resource_limits" not in data["confirmation_card"]
    assert "skill_id" not in str(data["confirmation_card"])


def readiness_run_row(*, status="failed", cancel_requested_at=None, error_message=None):
    return {
        "id": "run-ready",
        "session_id": "ses-ready",
        "workspace_id": "default",
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "schema_version": "ai-platform.run.v1",
        "executor_schema_version": "ai-platform.executor-result.v1",
        "status": status,
        "trace_id": "trace-ready",
        "input_json": {"message": "review", "skill_id": "qa-file-reviewer"},
        "result_json": {},
        "error_code": None,
        "error_message": error_message,
        "cancel_requested_at": cancel_requested_at,
        "cancel_requested_by": None,
    }


def test_run_control_readiness_enables_resume_from_checkpoint_outputs(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-ready")
        return readiness_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Code",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "output": "raw reusable output must not leak",
                    "skill_ids": ["qa-file-reviewer"],
                    "resource_limits": {"max_seconds": 60},
                    "sandbox_mode": "ephemeral",
                    "work_dir": "/tmp/runtime",
                    "private_payload": {"token": "secret-token"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-test",
                "run_id": run_id,
                "step_key": "test",
                "step_kind": "agent",
                "status": "failed",
                "title": "Test",
                "role": "verifier",
                "sequence": 2,
                "payload_json": {"error": "tests failed"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_queue_insight(status, tenant_id):
        raise AssertionError("queue insight should only be loaded for queued runs")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-control-readiness.v1"
    assert body["run"]["run_id"] == "run-ready"
    assert body["run"]["skill_id"] is None
    assert body["actions"]["cancel"] == {
        "enabled": False,
        "reason": "terminal_run",
        "method": "POST",
        "href": "/api/ai/runs/run-ready/cancel",
    }
    assert body["actions"]["resume"] == {
        "enabled": True,
        "reason": "checkpoint_outputs_available",
        "method": "POST",
        "href": "/api/ai/runs/run-ready/copy",
    }
    assert body["actions"]["retry"] == {
        "enabled": True,
        "reason": "retry_available",
        "method": "POST",
        "href": "/api/ai/runs/run-ready/retry",
    }
    assert body["checkpoint_candidates"] == [
        {
            "step_id": "step-code",
            "step_key": "code",
            "status": "succeeded",
            "title": "Code",
            "role": "coding",
            "sequence": 1,
            "reusable": True,
            "reason": "output_available",
        }
    ]
    assert body["queue_insight"] is None
    public_dump = str(body)
    assert "raw reusable output" not in public_dump
    assert "qa-file-reviewer" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "/tmp/" not in public_dump
    assert "private_payload" not in public_dump
    assert "secret-token" not in public_dump


def test_run_control_readiness_redacts_raw_skill_ids_from_public_scalars(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return readiness_run_row(
            status="failed",
            error_message="qa-file-reviewer failed in qa-word-review",
        )

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-skill",
                "run_id": run_id,
                "step_key": "qa-file-reviewer",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "qa-file-reviewer",
                "role": "qa-word-review",
                "sequence": 1,
                "payload_json": {"output": "reusable"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["error_message"] == "run_failed"
    assert body["checkpoint_candidates"] == [
        {
            "step_id": "step-skill",
            "step_key": "step-skill",
            "status": "succeeded",
            "title": "step-skill",
            "role": None,
            "sequence": 1,
            "reusable": True,
            "reason": "output_available",
        }
    ]
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump
    assert "qa-word-review" not in public_dump


def test_run_control_readiness_enables_cancel_and_includes_queue_insight(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return readiness_run_row(status="queued")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def fake_queue_insight(status, tenant_id):
        assert (status, tenant_id) == ("queued", "default")
        return {"tenant_id": tenant_id, "queued": 2, "running": 1}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["cancel"]["enabled"] is True
    assert body["actions"]["cancel"]["reason"] == "cancel_available"
    assert body["actions"]["resume"]["enabled"] is False
    assert body["actions"]["resume"]["reason"] == "active_run"
    assert body["actions"]["retry"]["reason"] == "status_not_retryable"
    assert body["queue_insight"] == {"tenant_id": "default", "queued": 2, "running": 1}


def test_run_control_readiness_returns_not_found_without_loading_steps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "missing-run")
        return None

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        raise AssertionError("steps must not be listed for missing run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/missing-run/control/readiness", headers=headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "run_not_found"}


def resume_manifest_run_row(*, status="queued", error_message=None):
    return {
        "id": "run-resume",
        "session_id": "ses-resume",
        "workspace_id": "default",
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "schema_version": "ai-platform.run.v1",
        "executor_schema_version": "ai-platform.executor-result.v1",
        "status": status,
        "trace_id": "trace-resume",
        "input_json": {"message": "resume", "skill_id": "qa-file-reviewer"},
        "result_json": {},
        "error_code": None,
        "error_message": error_message,
        "cancel_requested_at": None,
        "cancel_requested_by": None,
    }


def test_run_resume_manifest_projects_copied_reuse_intent(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id) == ("default", "user-a")
        if run_id == "run-old":
            return {**resume_manifest_run_row(status="failed"), "id": "run-old"}
        assert run_id == "run-resume"
        return resume_manifest_run_row()

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": "run-old",
                    "depends_on": [],
                    "output": "raw source output must not leak",
                    "skill_ids": ["qa-file-reviewer"],
                    "resource_limits": {"max_seconds": 60},
                    "sandbox_mode": "ephemeral",
                    "private_payload": {"token": "secret-token"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-test",
                "run_id": run_id,
                "step_key": "test",
                "step_kind": "agent",
                "status": "pending",
                "title": "Test",
                "role": "verifier",
                "sequence": 2,
                "payload_json": {"depends_on": ["code"]},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-resume-manifest.v1"
    assert body["run"]["run_id"] == "run-resume"
    assert body["run"]["skill_id"] is None
    assert body["source_run_id"] == "run-old"
    assert body["resume_enabled"] is True
    assert body["reason"] == "reuse_pending"
    assert body["counts"] == {
        "total": 2,
        "reuse_pending": 1,
        "rerun": 1,
        "pending": 2,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    assert body["steps"] == [
        {
            "step_id": "step-code",
            "step_key": "code",
            "status": "pending",
            "title": "Code",
            "role": "coding",
            "sequence": 1,
            "depends_on": [],
            "reuse_intent": "reuse_pending",
            "source_run_id": "run-old",
        },
        {
            "step_id": "step-test",
            "step_key": "test",
            "status": "pending",
            "title": "Test",
            "role": "verifier",
            "sequence": 2,
            "depends_on": ["code"],
            "reuse_intent": "rerun",
            "source_run_id": None,
        },
    ]
    public_dump = str(body)
    assert "raw source output" not in public_dump
    assert "qa-file-reviewer" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "private_payload" not in public_dump
    assert "secret-token" not in public_dump


def test_run_resume_manifest_redacts_raw_skill_ids_from_public_scalars(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        if run_id == "run-old":
            return {**resume_manifest_run_row(status="failed"), "id": "run-old"}
        return resume_manifest_run_row(status="failed", error_message="qa-file-reviewer failed in qa-word-review")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-skill",
                "run_id": run_id,
                "step_key": "qa-file-reviewer",
                "step_kind": "agent",
                "status": "pending",
                "title": "qa-file-reviewer",
                "role": "qa-word-review",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": "run-old",
                    "depends_on": ["qa-file-reviewer"],
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["error_message"] == "run_failed"
    assert body["steps"] == [
        {
            "step_id": "step-skill",
            "step_key": "step-skill",
            "status": "pending",
            "title": "step-skill",
            "role": None,
            "sequence": 1,
            "depends_on": [],
            "reuse_intent": "reuse_pending",
            "source_run_id": "run-old",
        }
    ]
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump
    assert "qa-word-review" not in public_dump


def test_run_resume_manifest_rejects_unsafe_source_run_id_and_public_scalars(monkeypatch):
    fingerprint = "a" * 64

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row()

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-unsafe",
                "run_id": run_id,
                "step_key": fingerprint,
                "step_kind": "agent",
                "status": "pending",
                "title": "C:/agent-workspaces/run-a/output.txt",
                "role": "tenants/default/private/storage-key",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": "tenants/default/private/run-old",
                    "depends_on": [fingerprint, "/home/xinlin.jiang/runtime/run-a", "tenants/default/private/file"],
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["source_run_id"] is None
    assert body["resume_enabled"] is True
    assert body["steps"] == [
        {
            "step_id": "step-unsafe",
            "step_key": "step-unsafe",
            "status": "pending",
            "title": "step-unsafe",
            "role": None,
            "sequence": 1,
            "depends_on": [],
            "reuse_intent": "reuse_pending",
            "source_run_id": None,
        }
    ]
    public_dump = str(body)
    assert fingerprint not in public_dump
    assert "agent-workspaces" not in public_dump
    assert "tenants/default" not in public_dump
    assert "/home/" not in public_dump


def test_run_resume_manifest_hides_safe_shaped_unauthorized_source_run_id(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        calls.append((tenant_id, user_id, run_id))
        if run_id == "run-resume":
            return resume_manifest_run_row()
        if run_id == "run-other-user":
            return None
        raise AssertionError(f"unexpected authorized run lookup: {run_id}")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_reuse_pending": True,
                    "copied_from_run_id": "run-other-user",
                    "depends_on": [],
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert calls == [
        ("default", "user-a", "run-resume"),
        ("default", "user-a", "run-other-user"),
    ]
    assert body["source_run_id"] is None
    assert body["steps"][0]["source_run_id"] is None
    assert "run-other-user" not in str(body)


def test_run_resume_manifest_keeps_non_failed_error_message_empty_after_redaction(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="queued", error_message="qa-file-reviewer queued diagnostic")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["error_message"] == ""
    assert "qa-file-reviewer" not in str(body)


def test_run_resume_manifest_returns_disabled_state_for_normal_run(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="running")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-normal",
                "run_id": run_id,
                "step_key": "normal",
                "step_kind": "agent",
                "status": "running",
                "title": "Normal",
                "role": "worker",
                "sequence": 1,
                "payload_json": {"depends_on": []},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/resume/manifest", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["source_run_id"] is None
    assert body["resume_enabled"] is False
    assert body["reason"] == "no_reuse_pending"
    assert body["counts"]["total"] == 1
    assert body["counts"]["reuse_pending"] == 0
    assert body["counts"]["rerun"] == 1


def test_run_resume_manifest_returns_not_found_without_loading_steps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "missing-run")
        return None

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        raise AssertionError("steps must not be listed for missing run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/missing-run/resume/manifest", headers=headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "run_not_found"}


def test_run_checkpoint_audit_projects_materialization_without_private_payload(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-a")
        return {
            "id": "run-a",
            "session_id": "ses-a",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "failed",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "trace_id": "trace-a",
            "input_json": {},
            "result_json": {},
            "cancel_requested_at": None,
            "cancel_requested_by": None,
            "error_code": None,
            "error_message": "qa-file-reviewer wrote /tmp/private-output",
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "qa-file-reviewer-step",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "qa-file-reviewer produced C:/runtime/private",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_id": "checkpoint-a",
                    "checkpoint_reused": True,
                    "output": "raw checkpoint output must not leak",
                    "resource_limits": {"max_tool_calls": 99},
                    "sandbox_mode": "ephemeral",
                    "command_sha256": "a" * 64,
                    "private_payload": {"storage_key": "tenants/default/private"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-report",
                "trace_id": "trace-a",
                "artifact_type": "reviewed_docx",
                "label": "Reviewed report",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "storage_key": "tenants/default/runs/run-a/artifacts/report.docx",
                "size_bytes": 12,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "source_step_id": "step-code",
                    "checkpoint_id": "checkpoint-a",
                    "producer_kind": "agent",
                    "producer_role": "reviewer",
                    "local_path": "/tmp/private/report.docx",
                    "skill_id": "qa-file-reviewer",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-checkpoint-audit.v1"
    assert body["run"]["run_id"] == "run-a"
    assert body["run"]["skill_id"] is None
    assert body["counts"] == {
        "checkpoints": 1,
        "resume_reusable": 1,
        "artifact_materialized": 1,
        "step_only": 0,
        "artifact_only": 0,
        "incomplete": 0,
        "gaps": 0,
        "uncheckpointed_reusable_steps": 0,
    }
    assert body["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-a",
            "audit_state": "materialized",
            "resume_reusable": True,
            "artifact_materialized": True,
            "step_ids": ["step-code"],
            "artifact_ids": ["artifact-report"],
            "reuse": {"pending": 0, "reused": 1},
            "gaps": [],
        }
    ]
    public_dump = str(body)
    assert "raw checkpoint output" not in public_dump
    assert "storage_key" not in public_dump
    assert "command_sha256" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "private_payload" not in public_dump
    assert "/tmp/" not in public_dump
    assert "C:/runtime" not in public_dump
    assert "qa-file-reviewer" not in public_dump


def test_run_checkpoint_audit_reports_artifact_only_and_uncheckpointed_step_gaps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-uncheckpointed",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Review",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {"output": "reusable but no checkpoint id"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-orphan",
                "trace_id": "trace-resume",
                "artifact_type": "reviewed_docx",
                "label": "Orphan artifact",
                "content_type": "application/octet-stream",
                "storage_key": "tenants/default/runs/run-resume/artifacts/orphan.docx",
                "size_bytes": 1,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "checkpoint_id": "checkpoint-orphan",
                    "source_step_id": "step-missing",
                    "producer_kind": "agent",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["artifact_only"] == 1
    assert body["counts"]["gaps"] == 2
    assert body["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-orphan",
            "audit_state": "artifact_only",
            "resume_reusable": False,
            "artifact_materialized": True,
            "step_ids": [],
            "artifact_ids": ["artifact-orphan"],
            "reuse": {"pending": 0, "reused": 0},
            "gaps": ["producer_step_missing"],
        }
    ]
    assert body["uncheckpointed_reusable_steps"] == [
        {
            "step_id": "step-uncheckpointed",
            "step_key": "review",
            "status": "succeeded",
            "reason": "missing_checkpoint_id",
        }
    ]


def test_run_checkpoint_audit_redacts_raw_skill_reference_in_checkpoint_id(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-unsafe",
                "run_id": run_id,
                "step_key": "qa-file-reviewer-step",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Unsafe checkpoint",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {
                    "checkpoint_id": "checkpoint-qa-file-reviewer",
                    "output": "reusable output must not leak",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-unsafe",
                "trace_id": "trace-resume",
                "artifact_type": "reviewed_docx",
                "label": "Unsafe artifact",
                "content_type": "application/octet-stream",
                "storage_key": "tenants/default/runs/run-resume/artifacts/unsafe.docx",
                "size_bytes": 1,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "checkpoint_id": "checkpoint-qa-file-reviewer",
                    "source_step_id": "step-unsafe",
                    "producer_kind": "agent",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["checkpoints"] == []
    assert body["uncheckpointed_reusable_steps"] == [
        {
            "step_id": "step-unsafe",
            "step_key": "step-unsafe",
            "status": "succeeded",
            "reason": "missing_checkpoint_id",
        }
    ]
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump
    assert "checkpoint-qa-file-reviewer" not in public_dump
    assert "reusable output" not in public_dump


def test_run_checkpoint_audit_reports_step_only_incomplete_and_producer_mismatch(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-reusable",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Code",
                "role": "coder",
                "sequence": 1,
                "payload_json": {"checkpoint_id": "checkpoint-a", "output": "reusable"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-pending",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "pending",
                "title": "Verify",
                "role": "verifier",
                "sequence": 2,
                "payload_json": {"checkpoint_id": "checkpoint-a"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-other",
                "run_id": run_id,
                "step_key": "other",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Other",
                "role": "coder",
                "sequence": 3,
                "payload_json": {"checkpoint_id": "checkpoint-b", "output": "other reusable"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-incomplete",
                "run_id": run_id,
                "step_key": "blocked",
                "step_kind": "agent",
                "status": "failed",
                "title": "Blocked",
                "role": "verifier",
                "sequence": 4,
                "payload_json": {"checkpoint_id": "checkpoint-empty"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-wrong",
                "trace_id": "trace-resume",
                "artifact_type": "reviewed_docx",
                "label": "Wrong checkpoint artifact",
                "content_type": "application/octet-stream",
                "storage_key": "tenants/default/runs/run-resume/artifacts/wrong.docx",
                "size_bytes": 1,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "checkpoint_id": "checkpoint-a",
                    "source_step_id": "step-other",
                    "producer_kind": "agent",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    checkpoints = {item["checkpoint_id"]: item for item in response.json()["checkpoints"]}
    assert checkpoints["checkpoint-a"] == {
        "checkpoint_id": "checkpoint-a",
        "audit_state": "step_only",
        "resume_reusable": True,
        "artifact_materialized": False,
        "step_ids": ["step-pending", "step-reusable"],
        "artifact_ids": ["artifact-wrong"],
        "reuse": {"pending": 0, "reused": 0},
        "gaps": ["producer_checkpoint_mismatch"],
    }
    assert checkpoints["checkpoint-empty"] == {
        "checkpoint_id": "checkpoint-empty",
        "audit_state": "incomplete",
        "resume_reusable": False,
        "artifact_materialized": False,
        "step_ids": ["step-incomplete"],
        "artifact_ids": [],
        "reuse": {"pending": 0, "reused": 0},
        "gaps": ["no_reusable_output"],
    }


def test_run_checkpoint_audit_requires_valid_artifact_source_step_for_materialization(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-reusable",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Code",
                "role": "coder",
                "sequence": 1,
                "payload_json": {"checkpoint_id": "checkpoint-a", "output": "reusable"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-no-source",
                "trace_id": "trace-resume",
                "artifact_type": "reviewed_docx",
                "label": "No source artifact",
                "content_type": "application/octet-stream",
                "storage_key": "tenants/default/runs/run-resume/artifacts/no-source.docx",
                "size_bytes": 1,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "checkpoint_id": "checkpoint-a",
                    "producer_kind": "agent",
                },
                "created_at": None,
            },
            {
                "id": "artifact-unsafe-source",
                "trace_id": "trace-resume",
                "artifact_type": "reviewed_docx",
                "label": "Unsafe source artifact",
                "content_type": "application/octet-stream",
                "storage_key": "tenants/default/runs/run-resume/artifacts/unsafe-source.docx",
                "size_bytes": 1,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "checkpoint_id": "checkpoint-a",
                    "source_step_id": "qa-file-reviewer-step",
                    "producer_kind": "agent",
                },
                "created_at": None,
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    assert response.json()["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-a",
            "audit_state": "step_only",
            "resume_reusable": True,
            "artifact_materialized": False,
            "step_ids": ["step-reusable"],
            "artifact_ids": ["artifact-no-source", "artifact-unsafe-source"],
            "reuse": {"pending": 0, "reused": 0},
            "gaps": ["artifact_source_step_missing", "artifact_source_step_unsafe"],
        }
    ]


def test_run_checkpoint_audit_missing_producer_does_not_materialize_existing_checkpoint(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-reusable",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Code",
                "role": "coder",
                "sequence": 1,
                "payload_json": {"checkpoint_id": "checkpoint-a", "output": "reusable"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-missing-producer",
                "trace_id": "trace-resume",
                "artifact_type": "reviewed_docx",
                "label": "Missing producer artifact",
                "content_type": "application/octet-stream",
                "storage_key": "tenants/default/runs/run-resume/artifacts/missing-producer.docx",
                "size_bytes": 1,
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "manifest_json": {
                    "schema_version": "ai-platform.artifact-manifest.v1",
                    "artifact_type": "reviewed_docx",
                    "checkpoint_id": "checkpoint-a",
                    "source_step_id": "step-missing",
                    "producer_kind": "agent",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    assert response.json()["checkpoints"] == [
        {
            "checkpoint_id": "checkpoint-a",
            "audit_state": "step_only",
            "resume_reusable": True,
            "artifact_materialized": False,
            "step_ids": ["step-reusable"],
            "artifact_ids": ["artifact-missing-producer"],
            "reuse": {"pending": 0, "reused": 0},
            "gaps": ["producer_step_missing"],
        }
    ]


def test_run_checkpoint_audit_returns_not_found_without_loading_steps_or_artifacts(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return None

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        raise AssertionError("steps must not load for missing run")

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        raise AssertionError("artifacts must not load for missing run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/missing-run/checkpoints/audit", headers=headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"


def test_copy_run_plan_redacts_runtime_private_step_titles_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-old",
            "workspace_id": "default",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "build feature"}},
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "step_key": "review",
                "role": "runtime211 worker /var/lib/ai-platform/run-a",
                "title": "failed in /home/xinlin.jiang/qa-review-queue-runtime/out.log",
                "status": "failed",
                "payload_json": {"error": "failed in /var/lib/ai-platform/private.log"},
            }
        ]

    async def fake_get_queue_insight(tenant_id):
        return {"tenant_id": tenant_id}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run_old/copy/plan", headers=headers())

    assert response.status_code == 200
    card = response.json()["confirmation_card"]
    assert card["steps"][0]["title"] == "review"
    assert "runtime211" not in str(card)
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(card)
    assert "/var/lib/ai-platform" not in str(card)


@pytest.mark.asyncio
async def test_copy_run_as_new_task_returns_full_execution_input_for_queue(monkeypatch):
    from app import repositories

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))
            class Cursor:
                async def fetchone(self):
                    return None

                async def fetchall(self):
                    return []

            return Cursor()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {
                "skillIds": ["qa-file-reviewer"],
                "allowedSkills": ["qa-file-reviewer"],
                "workerPath": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
                "executorPayload": {"cwd": "/var/lib/ai-platform/run_old"},
                "input": {
                    "message": "build feature",
                    "executor_type": "embedded-poco-kernel",
                    "skill_ids": ["qa-file-reviewer"],
                    "skillIds": ["qa-file-reviewer"],
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [
                        {
                            "step_key": "code",
                            "stepKey": "code",
                            "role": "coding",
                            "skill_ids": ["qa-file-reviewer"],
                            "skillIds": ["qa-file-reviewer"],
                            "executor_type": "embedded-poco-kernel",
                            "workerPath": "/var/lib/ai-platform/run_old",
                        },
                        {"step_key": "verify", "role": "test", "depends_on": ["code"]},
                    ],
                },
                "executor_type": "embedded-poco-kernel",
            },
        }

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        assert (tenant_id, agent_id, skill_id) == ("default", "general-agent", "general-chat")
        return {"executor_type": "claude-agent-worker", "skill_version": "2.0.0"}

    monkeypatch.setattr("app.repositories.resolve_agent_skill", fake_resolve_agent_skill)

    conn = RecordingConnection()
    copied = await repositories.copy_run_as_new_task(
        conn,
        tenant_id="default",
        user_id="user-a",
        run_id="run_old",
    )

    assert copied["input"]["execution_mode"] == "multi_agent"
    assert copied["input"]["multi_agent_steps"][1]["step_key"] == "verify"
    assert copied["input"]["copied_from_run_id"] == "run_old"
    assert copied["executor_type"] == "claude-agent-worker"
    assert copied["skill_version"] == "2.0.0"
    assert copied["release_policy_version"] == ""
    assert "executor_type" not in copied["input"]
    assert "skill_ids" not in copied["input"]
    assert "skillIds" not in copied["input"]
    assert "executor_type" not in copied["input"]["multi_agent_steps"][0]
    assert "skill_ids" not in copied["input"]["multi_agent_steps"][0]
    assert "skillIds" not in copied["input"]["multi_agent_steps"][0]
    assert "workerPath" not in copied["input"]["multi_agent_steps"][0]
    persisted_json = next(params[10] for sql, params in conn.executed if sql.startswith("insert into runs"))
    assert "skillIds" not in persisted_json
    assert "allowedSkills" not in persisted_json
    assert "workerPath" not in persisted_json
    assert "executorPayload" not in persisted_json
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in persisted_json
    assert "/var/lib/ai-platform" not in persisted_json


@pytest.mark.asyncio
async def test_copy_run_as_new_task_uses_rollout_selected_previous_version(monkeypatch):
    from app import repositories
    import json

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))

            class Cursor:
                async def fetchone(self):
                    return None

                async def fetchall(self):
                    return []

            return Cursor()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "retry"}, "executor_type": "embedded-poco-kernel"},
        }

    async def fake_resolve_rollout_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        assert (tenant_id, agent_id, skill_id) == ("default", "general-agent", "general-chat")
        return {
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-new",
            "release_policy_version": "hash-new",
            "release_policy_previous_version": "hash-old",
            "release_policy_rollout_percent": 0,
        }

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.resolve_agent_skill", fake_resolve_rollout_agent_skill)
    conn = RecordingConnection()

    copied = await repositories.copy_run_as_new_task(
        conn,
        tenant_id="default",
        user_id="user-a",
        run_id="run_old",
    )

    assert copied["skill_version"] == "hash-old"
    assert copied["release_policy_version"] == "hash-old"
    assert copied["release_decision"]["selected_version"] == "hash-old"
    assert copied["release_decision"]["selected_track"] == "previous"
    persisted_json = next(params[10] for sql, params in conn.executed if sql.startswith("insert into runs"))
    assert json.loads(persisted_json)["skill_version"] == "hash-old"
    assert json.loads(persisted_json)["release_decision"]["selected_track"] == "previous"


@pytest.mark.asyncio
async def test_copy_run_as_new_task_persists_g2_trace_and_contract_columns(monkeypatch):
    from app import repositories

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))

            class Cursor:
                async def fetchone(self):
                    return None

                async def fetchall(self):
                    return []

            return Cursor()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "retry"}, "executor_type": "embedded-poco-kernel"},
        }

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    conn = RecordingConnection()

    copied = await repositories.copy_run_as_new_task(
        conn,
        tenant_id="default",
        user_id="user-a",
        run_id="run_old",
    )

    run_inserts = [item for item in conn.executed if "insert into runs" in item[0]]
    assert len(run_inserts) == 1
    sql, params = run_inserts[0]
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "executor_schema_version" in sql
    assert copied["run_id"] in params
    assert any(str(item).startswith("trace_") for item in params)
    assert "ai-platform.run.v1" in params
    assert "ai-platform.executor-result.v1" in params


@pytest.mark.asyncio
async def test_copy_run_as_new_task_adds_session_message_anchor_for_history(monkeypatch):
    from app import repositories
    import json

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))

            class Cursor:
                async def fetchone(self):
                    return None

                async def fetchall(self):
                    return []

            return Cursor()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {
                "input": {
                    "message": "build feature",
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [
                        {"step_key": "code", "role": "coding"},
                        {"step_key": "verify", "role": "test", "depends_on": ["code"]},
                    ],
                },
                "executor_type": "embedded-poco-kernel",
            },
        }

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    conn = RecordingConnection()

    copied = await repositories.copy_run_as_new_task(
        conn,
        tenant_id="default",
        user_id="user-a",
        run_id="run_old",
    )

    message_inserts = [item for item in conn.executed if "insert into messages" in item[0]]
    assert len(message_inserts) == 1
    _, params = message_inserts[0]
    assert params[2] == "ses_old"
    assert params[3] == copied["run_id"]
    assert params[4] == "assistant"
    assert "复制为新任务" in params[5]
    metadata = json.loads(params[6])
    assert metadata["type"] == "copy_run_anchor"
    assert metadata["copied_from_run_id"] == "run_old"


@pytest.mark.asyncio
async def test_copy_run_as_new_task_adds_completed_step_outputs_to_resume(monkeypatch):
    from app import repositories

    class FakeCursor:
        def __init__(self, rows=None):
            self.rows = rows or []

        async def fetchone(self):
            return None

        async def fetchall(self):
            return self.rows

    class StepConnection:
        async def execute(self, sql, params):
            normalized_sql = " ".join(sql.split())
            if "from run_steps" in normalized_sql:
                return FakeCursor(
                    [
                        {
                            "step_key": "code",
                            "payload_json": {
                                "output": "code output",
                                "metadata": {"runner": "claude_agent_sdk"},
                            },
                        },
                        {
                            "step_key": "docs",
                            "payload_json": {"output": "docs output"},
                        },
                    ]
                )
            return FakeCursor()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {
                "input": {
                    "message": "build feature",
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [
                        {"step_key": "code", "role": "coding"},
                        {"step_key": "docs", "role": "doc"},
                        {"step_key": "verify", "role": "test", "depends_on": ["code", "docs"]},
                    ],
                },
                "executor_type": "embedded-poco-kernel",
            },
        }

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.resolve_agent_skill", fake_resolve_agent_skill)

    copied = await repositories.copy_run_as_new_task(
        StepConnection(),
        tenant_id="default",
        user_id="user-a",
        run_id="run_old",
    )

    assert copied["input"]["resume"] == {
        "copied_from_run_id": "run_old",
        "completed_step_outputs": {
            "code": "code output",
            "docs": "docs output",
        },
    }


@pytest.mark.asyncio
async def test_retry_run_as_new_task_rejects_non_retryable_status(monkeypatch):
    from app import repositories

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        assert for_update is True
        return {
            "id": run_id,
            "status": "running",
            "workspace_id": "default",
            "session_id": "ses-old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "retry"}},
        }

    async def fail_copy(*args, **kwargs):
        raise AssertionError("non-retryable source must not be copied")

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.copy_run_as_new_task", fail_copy)

    with pytest.raises(repositories.RepositoryConflictError, match="status_not_retryable"):
        await repositories.retry_run_as_new_task(object(), tenant_id="default", user_id="user-a", run_id="run-running")


@pytest.mark.asyncio
async def test_retry_run_as_new_task_rejects_when_retry_is_already_active(monkeypatch):
    from app import repositories

    class ActiveRetryCursor:
        async def fetchone(self):
            return {"id": "run-retry-active", "status": "queued"}

    class ActiveRetryConnection:
        def __init__(self):
            self.queries = []

        async def execute(self, sql, params):
            self.queries.append((" ".join(sql.split()), params))
            if "copied_from_run_id = %s" in " ".join(sql.split()):
                return ActiveRetryCursor()
            raise AssertionError(f"unexpected query before active retry rejection: {sql}")

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        assert for_update is True
        return {
            "id": run_id,
            "status": "failed",
            "workspace_id": "default",
            "session_id": "ses-old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "retry"}},
        }

    async def fail_copy(*args, **kwargs):
        raise AssertionError("active retry source must not be copied again")

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.copy_run_as_new_task", fail_copy)
    conn = ActiveRetryConnection()

    with pytest.raises(repositories.RepositoryConflictError, match="retry_already_active"):
        await repositories.retry_run_as_new_task(conn, tenant_id="default", user_id="user-a", run_id="run-failed")

    sql, params = conn.queries[0]
    assert "status in ('queued', 'running')" in sql
    assert params == ("default", "user-a", "run-failed")


@pytest.mark.asyncio
async def test_retry_run_as_new_task_records_retry_events_and_audit(monkeypatch):
    from app import repositories

    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        calls.append(("source_lock", tenant_id, user_id, run_id, for_update))
        return {
            "id": run_id,
            "status": "failed",
            "trace_id": "trace-old",
            "workspace_id": "default",
            "session_id": "ses-old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "retry"}},
        }

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses-old",
            "run_id": "run-new",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "workspace_id": "default",
            "file_ids": [],
            "input": {"copied_from_run_id": run_id},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-a",
            "release_policy_version": "",
            "release_decision": {},
        }

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    async def fake_get_active_retry_for_source_run(conn, *, tenant_id, user_id, run_id):
        calls.append(("active_retry", tenant_id, user_id, run_id))
        return None

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr(
        "app.repositories.get_active_retry_for_source_run",
        fake_get_active_retry_for_source_run,
        raising=False,
    )
    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    copied = await repositories.retry_run_as_new_task(
        object(),
        tenant_id="default",
        user_id="user-a",
        run_id="run-failed",
    )

    assert copied["run_id"] == "run-new"
    assert calls[0] == ("source_lock", "default", "user-a", "run-failed", True)
    assert calls[1] == ("active_retry", "default", "user-a", "run-failed")
    event_types = [call[1]["event_type"] for call in calls if call[0] == "event"]
    assert event_types == ["retry_requested", "run_retry_created"]
    audit = [call[1] for call in calls if call[0] == "audit"][0]
    assert audit["action"] == "run.retry"
    assert audit["target_id"] == "run-failed"
    assert audit["payload_json"]["new_run_id"] == "run-new"


def test_cancel_run_records_platform_cancel_request(monkeypatch):
    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_active/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"


def test_cancel_run_stops_active_sandbox_runtime_before_db_release(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [sandbox_lease_row(run_id=run_id, user_id=user_id)],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(
        conn,
        *,
        tenant_id,
        run_id,
        reason,
        lease_ids,
        trace_id=None,
        requested_by_role=None,
    ):
        release_calls.append(
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "reason": reason,
                "lease_ids": lease_ids,
                "trace_id": trace_id,
                "requested_by_role": requested_by_role,
            }
        )
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr(
        "app.routes.runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.create_container_provider", lambda provider_name=None: RecordingSandboxProvider(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_active/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": "run_active", "session_id": None, "status": "cancel_requested"}
    assert len(calls) == 1
    _, lease, reason = calls[0]
    assert lease.container_id == "exec-run_active"
    assert lease.container_name == "executor-exec-run_active"
    assert lease.tenant_id == "default"
    assert lease.user_id == "user-a"
    assert reason == "cancel_requested"
    assert release_calls == [
        {
            "tenant_id": "default",
            "run_id": "run_active",
            "reason": "cancel_requested",
            "lease_ids": ["lease-run_active"],
            "trace_id": None,
            "requested_by_role": None,
        }
    ]


def test_cancel_run_ignores_user_controlled_sandbox_container_payload(monkeypatch):
    calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [
                sandbox_lease_row(
                    run_id=run_id,
                    user_id=user_id,
                    lease_payload_json={
                        "container_id": "api",
                        "container_name": "ai-platform-api",
                        "executor_url": "http://attacker.invalid",
                        "workspace_host_path": "/host",
                        "workspace_container_path": "/workspace",
                        "labels": {"ai-platform.owner": "not-sandbox-runtime"},
                    },
                )
            ],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr(
        "app.routes.runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.create_container_provider", lambda provider_name=None: RecordingSandboxProvider(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_active/cancel", headers=headers())

    assert response.status_code == 200
    _, lease, _ = calls[0]
    assert lease.container_id == "exec-run_active"
    assert lease.container_name == "executor-exec-run_active"
    assert lease.executor_url == "http://sandbox-runtime.invalid"
    assert lease.workspace_host_path == ""
    assert lease.labels == {}


def test_cancel_run_surfaces_sandbox_runtime_stop_failure(monkeypatch):
    release_calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [sandbox_lease_row(run_id=run_id, user_id=user_id, lease_payload_json={})],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr(
        "app.routes.runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.create_container_provider", lambda provider_name=None: FailingSandboxProvider(), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_active/cancel", headers=headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert release_calls == []


def test_cancel_run_surfaces_unsupported_sandbox_provider_without_db_release(monkeypatch):
    release_calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [sandbox_lease_row(run_id=run_id, user_id=user_id, provider="podman")],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr(
        "app.routes.runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.create_container_provider", lambda provider_name=None: RecordingSandboxProvider([]), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_active/cancel", headers=headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert release_calls == []


def test_cancel_run_releases_successfully_stopped_leases_before_reporting_mixed_cleanup_failure(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [
                sandbox_lease_row(run_id=run_id, lease_id="lease-stopped", user_id=user_id),
                sandbox_lease_row(run_id=run_id, lease_id="lease-failed", user_id=user_id, provider="docker"),
            ],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr(
        "app.routes.runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.create_container_provider", ProviderByName(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_active/cancel", headers=headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert release_calls == [
        {
            "tenant_id": "default",
            "run_id": "run_active",
            "reason": "cancel_requested",
            "lease_ids": ["lease-stopped"],
            "trace_id": None,
        }
    ]


def test_cancel_queued_run_removes_queued_payload(monkeypatch):
    calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancelled"}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", fake_remove_queued_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_queued/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert calls == [("remove", "default", "run_queued")]


def test_admin_cancel_run_stops_active_sandbox_runtime_before_db_release(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [sandbox_lease_row(run_id=run_id, user_id="target-user")],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(
        conn,
        *,
        tenant_id,
        run_id,
        reason,
        lease_ids,
        trace_id=None,
        requested_by_role=None,
    ):
        release_calls.append(
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "reason": reason,
                "lease_ids": lease_ids,
                "trace_id": trace_id,
                "requested_by_role": requested_by_role,
            }
        )
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runs.create_container_provider",
        lambda provider_name=None: RecordingSandboxProvider(calls),
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_active/cancel", headers=admin_headers())

    assert response.status_code == 200
    assert response.json() == {"run_id": "run_active", "session_id": None, "status": "cancel_requested"}
    assert len(calls) == 1
    _, lease, reason = calls[0]
    assert lease.container_id == "exec-run_active"
    assert lease.user_id == "target-user"
    assert reason == "admin_cancel_requested"
    assert release_calls == [
        {
            "tenant_id": "default",
            "run_id": "run_active",
            "reason": "admin_cancel_requested",
            "lease_ids": ["lease-run_active"],
            "trace_id": None,
            "requested_by_role": "admin",
        }
    ]


def test_admin_cancel_run_surfaces_sandbox_runtime_stop_failure(monkeypatch):
    release_calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [sandbox_lease_row(run_id=run_id, user_id="target-user", lease_payload_json={})],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.admin_runs.create_container_provider",
        lambda provider_name=None: FailingSandboxProvider(),
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_active/cancel", headers=admin_headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert release_calls == []


def test_admin_cancel_run_releases_successfully_stopped_leases_before_reporting_mixed_cleanup_failure(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [
                sandbox_lease_row(run_id=run_id, lease_id="lease-admin-stopped", user_id="target-user"),
                sandbox_lease_row(run_id=run_id, lease_id="lease-admin-unsupported", user_id="target-user", provider="podman"),
            ],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runs.create_container_provider", ProviderByName(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run_active/cancel", headers=admin_headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert release_calls == [
        {
            "tenant_id": "default",
            "run_id": "run_active",
            "reason": "admin_cancel_requested",
            "lease_ids": ["lease-admin-stopped"],
            "trace_id": None,
            "requested_by_role": "admin",
        }
    ]


@pytest.mark.asyncio
async def test_request_run_cancel_closes_pending_steps_when_owner_cancels_queued_run(monkeypatch):
    from app import repositories

    calls = []

    class FakeCursor:
        async def fetchone(self):
            return {"id": "run_queued", "status": "cancelled"}

        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs"):
                return FakeCursor()
            if normalized.startswith("update run_steps"):
                return FakeCursor()
            if normalized.startswith("select * from sandbox_leases"):
                return FakeCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    result = await repositories.request_run_cancel(
        FakeConnection(),
        tenant_id="default",
        user_id="user-a",
        run_id="run_queued",
    )

    assert result == {"run_id": "run_queued", "status": "cancelled"}
    step_updates = [call for call in calls if isinstance(call[0], str) and call[0].startswith("update run_steps")]
    assert len(step_updates) == 1
    assert "status in ('pending', 'running')" in step_updates[0][0]
    assert step_updates[0][1] == ("default", "run_queued")


@pytest.mark.asyncio
async def test_request_run_cancel_defers_active_sandbox_lease_release_until_cleanup_success(monkeypatch):
    from app import repositories

    calls = []

    class RunCursor:
        async def fetchone(self):
            return {"id": "run_active", "status": "running", "trace_id": "trace_run_active"}

    class LeaseCursor:
        async def fetchall(self):
            return [{"id": "lease-a", "trace_id": "trace_lease_a"}]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs"):
                return RunCursor()
            if normalized.startswith("select * from sandbox_leases"):
                return LeaseCursor()
            if normalized.startswith("update sandbox_leases"):
                raise AssertionError("cancel request must not release sandbox leases before runtime cleanup")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    result = await repositories.request_run_cancel(
        FakeConnection(),
        tenant_id="default",
        user_id="user-a",
        run_id="run_active",
    )

    assert result == {
        "run_id": "run_active",
        "status": "cancel_requested",
        "trace_id": "trace_run_active",
        "active_sandbox_leases": [{"id": "lease-a", "trace_id": "trace_lease_a"}],
    }
    lease_reads = [call for call in calls if isinstance(call[0], str) and call[0].startswith("select * from sandbox_leases")]
    assert len(lease_reads) == 1
    assert "status = 'active'" in lease_reads[0][0]
    assert lease_reads[0][1] == ("default", "run_active")
    assert not any(item[0] == "event" and item[1]["event_type"] == "sandbox_lease_released" for item in calls)


@pytest.mark.asyncio
async def test_request_run_cancel_allows_cancelled_run_with_active_sandbox_lease_for_cleanup_retry(monkeypatch):
    from app import repositories

    calls = []

    class RunCursor:
        async def fetchone(self):
            return {"id": "run_queued", "status": "cancelled", "trace_id": "trace_run_queued"}

    class LeaseCursor:
        async def fetchall(self):
            return [{"id": "lease-queued", "trace_id": "trace_lease_queued"}]

    class EmptyCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs"):
                assert "status = 'cancelled'" in normalized
                assert "exists ( select 1 from sandbox_leases" in normalized
                assert "sandbox_leases.status = 'active'" in normalized
                return RunCursor()
            if normalized.startswith("update run_steps"):
                return EmptyCursor()
            if normalized.startswith("select * from sandbox_leases"):
                return LeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    result = await repositories.request_run_cancel(
        FakeConnection(),
        tenant_id="default",
        user_id="user-a",
        run_id="run_queued",
    )

    assert result == {
        "run_id": "run_queued",
        "status": "cancelled",
        "trace_id": "trace_run_queued",
        "active_sandbox_leases": [{"id": "lease-queued", "trace_id": "trace_lease_queued"}],
    }


@pytest.mark.asyncio
async def test_release_stopped_sandbox_leases_for_cancel_releases_only_stopped_lease_ids_and_emits_events(monkeypatch):
    from app import repositories

    calls = []

    class LeaseCursor:
        async def fetchall(self):
            return [{"id": "lease-a", "trace_id": "trace_lease_a"}]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                assert "id = any(%s)" in normalized
                assert params == ("cancel_requested", "default", "run_active", ["lease-a"])
                return LeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    released = await repositories.release_stopped_sandbox_leases_for_cancel(
        FakeConnection(),
        tenant_id="default",
        run_id="run_active",
        reason="cancel_requested",
        lease_ids=["lease-a"],
        trace_id="trace_run_active",
    )

    assert released == [{"id": "lease-a", "trace_id": "trace_lease_a"}]
    lease_updates = [call for call in calls if isinstance(call[0], str) and call[0].startswith("update sandbox_leases")]
    assert len(lease_updates) == 1
    assert "status = 'active'" in lease_updates[0][0]
    assert lease_updates[0][1] == ("cancel_requested", "default", "run_active", ["lease-a"])
    release_event = next(item for item in calls if item[0] == "event" and item[1]["event_type"] == "sandbox_lease_released")
    assert release_event[1]["trace_id"] == "trace_lease_a"
    assert release_event[1]["payload"] == {
        "visible_to_user": True,
        "lease_id": "lease-a",
        "reason": "cancel_requested",
    }


def test_cancel_running_run_does_not_remove_queued_payload(monkeypatch):
    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        raise AssertionError("running cancellation must not mutate queued list")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", fake_remove_queued_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run_active/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"


@pytest.mark.asyncio
async def test_list_admin_runs_is_tenant_scoped_and_parameterized():
    from app import repositories

    class FakeCursor:
        async def fetchall(self):
            return [
                {
                    "run_id": "run_a",
                    "session_id": "ses_a",
                    "user_id": "user-a",
                    "workspace_id": "default",
                    "status": "running",
                    "agent_id": "general-agent",
                    "skill_id": "general-chat",
                    "created_at": None,
                    "queued_at": None,
                    "started_at": None,
                    "finished_at": None,
                    "cancel_requested_at": "2026-05-27T01:02:03Z",
                    "cancel_requested_by": "admin-a",
                    "error_code": None,
                    "error_message": None,
                }
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            assert "where tenant_id = %s" in normalized
            assert "or user_id = %s)" in normalized
            assert "or status = %s)" in normalized
            assert "%s::text is null" in normalized
            assert params == ("default", "user-a", "user-a", "running", "running", 25)
            return FakeCursor()

    rows = await repositories.list_admin_runs(
        FakeConnection(),
        tenant_id="default",
        user_id="user-a",
        status="running",
        limit=25,
    )

    assert rows[0]["run_id"] == "run_a"
    assert rows[0]["user_id"] == "user-a"
    assert rows[0]["cancel_requested_at"] == "2026-05-27T01:02:03Z"
    assert rows[0]["cancel_requested_by"] == "admin-a"


@pytest.mark.asyncio
async def test_admin_run_detail_includes_multi_agent_steps(monkeypatch):
    from app import repositories

    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "failed",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "created_at": None,
            "queued_at": None,
            "started_at": None,
            "finished_at": None,
            "cancel_requested_at": "2026-05-27T01:02:03Z",
            "cancel_requested_by": "admin-a",
            "input_json": {},
            "result_json": {},
            "error_code": None,
            "error_message": "verify failed",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "failed",
                "title": "验证结果",
                "role": "verify",
                "sequence": 2,
                "payload_json": {"error": "测试未通过"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    class FakeAuditCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            return FakeAuditCursor()

    monkeypatch.setattr("app.repositories.get_run", fake_get_run)
    monkeypatch.setattr("app.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.repositories.list_run_steps", fake_list_run_steps)

    detail = await repositories.get_admin_run_detail(FakeConnection(), tenant_id="default", run_id="run-a")

    assert detail["run"]["cancel_requested_at"] == "2026-05-27T01:02:03Z"
    assert detail["run"]["cancel_requested_by"] == "admin-a"
    assert detail["steps"] == [
        {
            "step_id": "step-verify",
            "run_id": "run-a",
            "step_key": "verify",
            "step_kind": "agent",
            "status": "failed",
            "title": "验证结果",
            "role": "verify",
            "sequence": 2,
            "payload": {"error": "测试未通过"},
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_request_admin_run_cancel_does_not_filter_target_user_and_audits(monkeypatch):
    from app import repositories

    calls = []

    class FakeCursor:
        async def fetchone(self):
            return {"id": "run_active", "status": "running", "user_id": "target-user", "trace_id": "trace_run_active"}

        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs"):
                assert "and user_id =" not in normalized
                assert params == ("admin-a", "default", "run_active")
                return FakeCursor()
            if normalized.startswith("select * from sandbox_leases"):
                return FakeCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.request_admin_run_cancel(
        FakeConnection(),
        tenant_id="default",
        admin_user_id="admin-a",
        run_id="run_active",
    )

    assert result == {"run_id": "run_active", "status": "cancel_requested"}
    assert (
        "event",
        {
            "tenant_id": "default",
            "run_id": "run_active",
            "event_type": "cancel_requested",
            "stage": "control",
            "message": "管理员已请求取消",
            "trace_id": "trace_run_active",
            "payload": {
                "visible_to_user": True,
                "severity": "warning",
                "requested_by": "admin-a",
                "requested_by_role": "admin",
                "target_user_id": "target-user",
            },
        },
    ) in calls
    assert (
        "audit",
        {
            "tenant_id": "default",
            "user_id": "admin-a",
            "action": "admin.run.cancel",
            "target_type": "run",
            "target_id": "run_active",
            "trace_id": "trace_run_active",
            "payload_json": {
                "run_id": "run_active",
                "target_user_id": "target-user",
                "result_status": "cancel_requested",
            },
        },
    ) in calls


@pytest.mark.asyncio
async def test_request_admin_run_cancel_closes_pending_steps_when_queued_cancelled(monkeypatch):
    from app import repositories

    calls = []

    class FakeCursor:
        async def fetchone(self):
            return {"id": "run_queued", "status": "cancelled", "user_id": "target-user", "trace_id": "trace_run_queued"}

        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs"):
                return FakeCursor()
            if normalized.startswith("update run_steps"):
                return FakeCursor()
            if normalized.startswith("select * from sandbox_leases"):
                return FakeCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.request_admin_run_cancel(
        FakeConnection(),
        tenant_id="default",
        admin_user_id="admin-a",
        run_id="run_queued",
    )

    assert result == {"run_id": "run_queued", "status": "cancelled"}
    step_updates = [call for call in calls if isinstance(call[0], str) and call[0].startswith("update run_steps")]
    assert len(step_updates) == 1
    assert "status in ('pending', 'running')" in step_updates[0][0]
    assert step_updates[0][1] == ("default", "run_queued")


@pytest.mark.asyncio
async def test_request_admin_run_cancel_defers_active_sandbox_lease_release_until_cleanup_success(monkeypatch):
    from app import repositories

    calls = []

    class RunCursor:
        async def fetchone(self):
            return {"id": "run_active", "status": "running", "user_id": "target-user", "trace_id": "trace_run_active"}

    class LeaseCursor:
        async def fetchall(self):
            return [{"id": "lease-admin", "trace_id": None}]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs"):
                return RunCursor()
            if normalized.startswith("select * from sandbox_leases"):
                return LeaseCursor()
            if normalized.startswith("update sandbox_leases"):
                raise AssertionError("admin cancel request must not release sandbox leases before runtime cleanup")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.request_admin_run_cancel(
        FakeConnection(),
        tenant_id="default",
        admin_user_id="admin-a",
        run_id="run_active",
    )

    assert result == {
        "run_id": "run_active",
        "status": "cancel_requested",
        "trace_id": "trace_run_active",
        "active_sandbox_leases": [{"id": "lease-admin", "trace_id": None}],
    }
    lease_reads = [call for call in calls if isinstance(call[0], str) and call[0].startswith("select * from sandbox_leases")]
    assert len(lease_reads) == 1
    assert lease_reads[0][1] == ("default", "run_active")
    assert not any(item[0] == "event" and item[1]["event_type"] == "sandbox_lease_released" for item in calls)


@pytest.mark.asyncio
async def test_request_admin_run_cancel_allows_cancelled_run_with_active_sandbox_lease_for_cleanup_retry(monkeypatch):
    from app import repositories

    calls = []

    class RunCursor:
        async def fetchone(self):
            return {"id": "run_queued", "status": "cancelled", "user_id": "target-user", "trace_id": "trace_run_queued"}

    class LeaseCursor:
        async def fetchall(self):
            return [{"id": "lease-admin-queued", "trace_id": None}]

    class EmptyCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update runs"):
                assert "status = 'cancelled'" in normalized
                assert "exists ( select 1 from sandbox_leases" in normalized
                assert "sandbox_leases.status = 'active'" in normalized
                assert "and user_id =" not in normalized
                return RunCursor()
            if normalized.startswith("update run_steps"):
                return EmptyCursor()
            if normalized.startswith("select * from sandbox_leases"):
                return LeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.request_admin_run_cancel(
        FakeConnection(),
        tenant_id="default",
        admin_user_id="admin-a",
        run_id="run_queued",
    )

    assert result == {
        "run_id": "run_queued",
        "status": "cancelled",
        "trace_id": "trace_run_queued",
        "active_sandbox_leases": [{"id": "lease-admin-queued", "trace_id": None}],
    }


@pytest.mark.asyncio
async def test_release_stopped_sandbox_leases_for_admin_cancel_emits_admin_role(monkeypatch):
    from app import repositories

    calls = []

    class LeaseCursor:
        async def fetchall(self):
            return [{"id": "lease-admin", "trace_id": None}]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return LeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    released = await repositories.release_stopped_sandbox_leases_for_cancel(
        FakeConnection(),
        tenant_id="default",
        run_id="run_active",
        reason="admin_cancel_requested",
        lease_ids=["lease-admin"],
        trace_id="trace_run_active",
        requested_by_role="admin",
    )

    assert released == [{"id": "lease-admin", "trace_id": None}]
    lease_updates = [call for call in calls if isinstance(call[0], str) and call[0].startswith("update sandbox_leases")]
    assert len(lease_updates) == 1
    assert lease_updates[0][1] == ("admin_cancel_requested", "default", "run_active", ["lease-admin"])
    release_event = next(item for item in calls if item[0] == "event" and item[1]["event_type"] == "sandbox_lease_released")
    assert release_event[1]["trace_id"] == "trace_run_active"
    assert release_event[1]["payload"] == {
        "visible_to_user": True,
        "lease_id": "lease-admin",
        "reason": "admin_cancel_requested",
        "requested_by_role": "admin",
    }
