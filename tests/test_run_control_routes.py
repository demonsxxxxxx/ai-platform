from contextlib import asynccontextmanager
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import RepositoryAuthorizationError, RepositoryConflictError


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


class EmptyPropagationCursor:
    async def fetchone(self):
        return None

    async def fetchall(self):
        return []


class EmptyPropagationConnection:
    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        if normalized.startswith("select child.id") and "from runs child" in normalized:
            return EmptyPropagationCursor()
        if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
            return EmptyPropagationCursor()
        raise AssertionError(f"unexpected fake transaction sql: {normalized}")


@asynccontextmanager
async def fake_transaction():
    yield EmptyPropagationConnection()


@pytest.fixture(autouse=True)
def allow_existing_run_control_route_tests_to_stub_auth_snapshot_update(monkeypatch):
    async def update_auth_snapshot(*_args, **_kwargs):
        return None

    async def authorize_capabilities(*_args, **kwargs):
        return {"skill_id": kwargs["skill_id"], "executor_type": "claude-agent-worker"}

    async def authorize_persisted_run(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.routes.runs.repositories.update_run_auth_snapshot",
        update_auth_snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.runs.repositories.authorize_run_capabilities",
        authorize_capabilities,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.runs._authorize_persisted_run_for_queue",
        authorize_persisted_run,
        raising=False,
    )


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
                    "completed_step_checkpoints": {
                        "code": {
                            "checkpoint_id": "checkpoint-code",
                            "source_step_id": "step-code-source",
                            "copied_from_run_id": "run_old",
                        }
                    },
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

    async def fake_get_queue_insight(tenant_id, **_kwargs):
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

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        return 0

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
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
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
    assert calls[0] == ("admit", "default", "user-a", 3)
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
    governance = queued_payload["skill_manifests"][0]["snapshot_governance"]
    assert governance["schema_version"] == "ai-platform.skill-pinned-snapshot-governance.v1"
    assert governance["snapshot_source"] == "platform_release_lock"
    assert governance["does_not_close_b4_or_211"] is True
    serialized_governance = json.dumps(governance, ensure_ascii=False)
    assert "release_decision" not in serialized_governance
    assert "content_base64" not in serialized_governance
    assert "hash-old" not in serialized_governance
    assert "track" not in serialized_governance
    assert "rollout" not in serialized_governance
    assert queued_payload["context_snapshot_id"] == "ctx_copy_route"
    assert queued_payload["context_snapshot"]["source"] == "copy_run"
    assert queued_payload["input"]["resume"]["completed_step_outputs"] == {"code": "code output"}
    assert queued_payload["input"]["resume"]["completed_step_checkpoints"] == {
        "code": {
            "checkpoint_id": "checkpoint-code",
            "source_step_id": "step-code-source",
            "copied_from_run_id": "run_old",
        }
    }
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
    assert step_calls[0]["payload_json"]["checkpoint_id"] == "checkpoint-code"
    assert step_calls[0]["payload_json"]["source_step_id"] == "step-code-source"
    assert step_calls[0]["payload_json"]["copied_from_run_id"] == "run_old"
    assert "checkpoint_reused" not in step_calls[0]["payload_json"]
    assert "output" not in step_calls[0]["payload_json"]
    assert step_calls[1]["payload_json"]["depends_on"] == ["code"]
    assert step_calls[1]["payload_json"]["sandbox_mode"] == "ephemeral"
    assert step_calls[1]["payload_json"]["browser_enabled"] is True
    assert step_calls[1]["payload_json"]["resource_limits"] == {"max_tool_calls": 3}


def test_copy_run_rejects_when_user_active_run_limit_is_reached(monkeypatch):
    calls = []

    class LimitSettings:
        max_active_runs_per_user = 1

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        raise RepositoryConflictError("user_active_run_limit_exceeded")

    async def fail_copy_run_as_new_task(*args, **kwargs):
        calls.append(("copy", kwargs))
        raise AssertionError("copy must not create a copied run after admission rejection")

    async def fail_enqueue_run(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("copy must not enqueue after admission rejection")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.get_settings", lambda: LimitSettings())
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fail_copy_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-source/copy", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "user_active_run_limit_exceeded"
    assert calls == [("admit", "default", "user-a", 1)]


@pytest.mark.asyncio
async def test_seed_copied_run_steps_preserves_server_owned_producer_source_run(monkeypatch):
    from app.routes.runs import seed_copied_run_steps

    calls = []

    async def fake_upsert_run_step(conn, **kwargs):
        calls.append(kwargs)
        return f"step-{kwargs['step_key']}"

    monkeypatch.setattr("app.routes.runs.repositories.upsert_run_step", fake_upsert_run_step)

    await seed_copied_run_steps(
        object(),
        tenant_id="default",
        run_id="run-copy",
        source="copy_run",
        copied_input={
            "multi_agent_steps": [{"step_key": "code", "role": "coding"}],
            "resume": {
                "copied_from_run_id": "run-source",
                "completed_step_outputs": {"code": "code output"},
                "completed_step_checkpoints": {
                    "code": {
                        "checkpoint_id": "checkpoint-code",
                        "source_step_id": "step-code-source",
                        "copied_from_run_id": "run-original",
                    }
                },
            },
        },
    )

    assert calls[0]["payload_json"]["checkpoint_id"] == "checkpoint-code"
    assert calls[0]["payload_json"]["source_step_id"] == "step-code-source"
    assert calls[0]["payload_json"]["copied_from_run_id"] == "run-original"


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
                "resume": {
                    "copied_from_run_id": run_id,
                    "completed_step_outputs": {"retry-code": "retry code output"},
                    "completed_step_checkpoints": {
                        "retry-code": {
                            "checkpoint_id": "checkpoint-retry-code",
                            "source_step_id": "step-retry-source",
                            "copied_from_run_id": run_id,
                        }
                    },
                },
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
        assert kwargs["source_run_id"] == "run-failed"
        return {"context_snapshot_id": "ctx-retry", "source": "retry_run"}

    async def fake_enqueue_run(payload):
        calls["enqueue"].append(payload)
        return 3

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        return {"tenant_id": tenant_id}

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls["active_limit"] = (tenant_id, user_id, limit)
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
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
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
    assert calls["active_limit"] == ("default", "user-a", 3)
    assert calls["enqueue"][0]["run_id"] == "run-retry"
    assert calls["enqueue"][0]["context_snapshot_id"] == "ctx-retry"
    assert calls["step"][0]["payload_json"]["seeded_from"] == "retry_run"
    assert calls["step"][0]["payload_json"]["checkpoint_id"] == "checkpoint-retry-code"
    assert calls["step"][0]["payload_json"]["source_step_id"] == "step-retry-source"
    assert calls["step"][0]["payload_json"]["copied_from_run_id"] == "run-failed"


def test_retry_run_rejects_when_user_active_run_limit_is_reached(monkeypatch):
    calls = []

    class LimitSettings:
        max_active_runs_per_user = 1

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        raise RepositoryConflictError("user_active_run_limit_exceeded")

    async def fail_retry_run_as_new_task(*args, **kwargs):
        calls.append(("retry", kwargs))
        raise AssertionError("retry must not create a copied run after admission rejection")

    async def fail_enqueue_run(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("retry must not enqueue after admission rejection")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.get_settings", lambda: LimitSettings())
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fail_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-failed/retry", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "user_active_run_limit_exceeded"
    assert calls == [("admit", "default", "user-a", 1)]


def test_retry_run_returns_capability_not_authorized_for_stale_source_capability(monkeypatch):
    from app.repositories import RepositoryNotFoundError

    calls = []

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        return 0

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("retry", tenant_id, user_id, run_id))
        raise RepositoryNotFoundError("agent_or_skill_not_found")

    async def fail_enqueue_run(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("retry must not enqueue stale source capabilities")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue_run)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post("/api/ai/runs/run-failed/retry", headers=headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "capability_not_authorized"
    assert calls == [("admit", "default", "user-a", 3), ("retry", "default", "user-a", "run-failed")]


def test_retry_run_rejects_non_retryable_source_without_enqueue(monkeypatch):
    from app.repositories import RepositoryConflictError

    calls = []

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        return 0

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("retry", tenant_id, user_id, run_id))
        raise RepositoryConflictError("status_not_retryable")

    async def fake_enqueue_run(payload):
        calls.append(("enqueue", payload))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-running/retry", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "status_not_retryable"
    assert calls == [("admit", "default", "user-a", 3), ("retry", "default", "user-a", "run-running")]


def test_retry_run_returns_not_found_without_enqueue(monkeypatch):
    calls = []

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        return 0

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("retry", tenant_id, user_id, run_id))
        return None

    async def fake_enqueue_run(payload):
        calls.append(("enqueue", payload))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/missing-run/retry", headers=headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "run_not_found"
    assert calls == [("admit", "default", "user-a", 3), ("retry", "default", "user-a", "missing-run")]


def test_resume_run_creates_queued_resume_from_checkpointed_source(monkeypatch):
    calls = {"resume": [], "enqueue": [], "context": [], "step": []}

    async def fake_resume_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls["resume"].append((tenant_id, user_id, run_id))
        return {
            "session_id": "ses-old",
            "run_id": "run-resume-new",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "workspace_id": "default",
            "file_ids": [],
            "input": {
                "message": "resume",
                "copied_from_run_id": run_id,
                "multi_agent_steps": [
                    {"step_key": "code", "role": "coding"},
                    {"step_key": "verify", "role": "test", "depends_on": ["code"]},
                ],
                "resume": {
                    "copied_from_run_id": run_id,
                    "completed_step_outputs": {"code": "code output"},
                    "completed_step_checkpoints": {
                        "code": {
                            "checkpoint_id": "checkpoint-code",
                            "source_step_id": "step-code-source",
                            "copied_from_run_id": run_id,
                        }
                    },
                },
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
        assert input_payload["resume"]["completed_step_outputs"] == {"code": "code output"}
        assert release_policy_version == ""
        return [{"skill_id": skill_id, "content_hash": "hash-a"}]

    async def fake_update_run_input_skill_version(conn, **kwargs):
        return None

    async def fake_append_event(conn, **kwargs):
        return None

    async def fake_record_context(conn, **kwargs):
        calls["context"].append(kwargs)
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_resume_route",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_upsert_run_step(conn, **kwargs):
        calls["step"].append(kwargs)
        return f"step-{kwargs['step_key']}"

    async def fake_enqueue_run(payload):
        calls["enqueue"].append(payload)
        return 2

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        return {"tenant_id": tenant_id, "reason": "workers_busy"}

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls["active_limit"] = (tenant_id, user_id, limit)
        return 0

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.resume_run_as_new_task", fake_resume_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.runs.repositories.upsert_run_step", fake_upsert_run_step)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-failed/resume", headers=headers())

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-resume-new",
        "session_id": "ses-old",
        "status": "queued",
        "queue_position": 2,
        "queue_insight": {"tenant_id": "default", "reason": "workers_busy"},
    }
    assert calls["resume"] == [("default", "user-a", "run-failed")]
    assert calls["active_limit"] == ("default", "user-a", 3)
    assert calls["context"][0]["source"] == "resume_run"
    assert calls["context"][0]["source_run_id"] == "run-failed"
    assert calls["enqueue"][0]["context_snapshot_id"] == "ctx_resume_route"
    assert calls["enqueue"][0]["context_snapshot"]["source"] == "resume_run"
    assert calls["enqueue"][0]["input"]["resume"]["completed_step_outputs"] == {"code": "code output"}
    assert [(item["step_key"], item["status"]) for item in calls["step"]] == [
        ("code", "pending"),
        ("verify", "pending"),
    ]
    assert calls["step"][0]["payload_json"]["seeded_from"] == "resume_run"
    assert calls["step"][0]["payload_json"]["checkpoint_reuse_pending"] is True
    assert calls["step"][0]["payload_json"]["checkpoint_id"] == "checkpoint-code"
    assert calls["step"][0]["payload_json"]["source_step_id"] == "step-code-source"
    assert calls["step"][1]["payload_json"]["depends_on"] == ["code"]


def test_resume_run_rejects_source_without_checkpoint_outputs(monkeypatch):
    from app.repositories import RepositoryConflictError

    calls = []

    async def fake_resume_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("resume", tenant_id, user_id, run_id))
        raise RepositoryConflictError("no_checkpoint_outputs")

    async def fake_enqueue_run(payload):
        calls.append(("enqueue", payload))
        return 1

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        return 0

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.resume_run_as_new_task", fake_resume_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-no-checkpoint/resume", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "no_checkpoint_outputs"
    assert calls == [
        ("admit", "default", "user-a", 3),
        ("resume", "default", "user-a", "run-no-checkpoint"),
    ]


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

    async def fake_get_queue_insight(tenant_id, **_kwargs):
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

    async def fake_queue_insight(status, tenant_id, **_kwargs):
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
        "href": "/api/ai/runs/run-ready/resume",
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
    assert body["multi_agent"] is None
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

    async def fake_queue_insight(status, tenant_id, **_kwargs):
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


def test_run_control_readiness_projects_multi_agent_dependency_gates(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {
                        "step_key": "plan",
                        "role": "planner",
                        "title": "Plan",
                        "skill_ids": ["qa-file-reviewer"],
                    },
                    {
                        "step_key": "code",
                        "role": "coder",
                        "title": "Code",
                        "depends_on": ["plan"],
                        "resource_limits": {"max_tool_calls": 3},
                    },
                    {
                        "step_key": "verify",
                        "role": "qa-file-reviewer verifier",
                        "title": "qa-file-reviewer verification",
                        "depends_on": ["code"],
                        "sandbox_mode": "ephemeral",
                    },
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {
                    "output": "plan output",
                    "skill_ids": ["qa-file-reviewer"],
                    "resource_limits": {"max_seconds": 60},
                    "sandbox_mode": "ephemeral",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["plan"],
                    "mcp_tool_ids": ["write.docx"],
                    "worker_path": "/tmp/runtime/worker.py",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-verify",
                "run_id": run_id,
                "step_key": "verify",
                "step_kind": "agent",
                "status": "pending",
                "title": "qa-file-reviewer verification",
                "role": "qa-file-reviewer verifier",
                "sequence": 3,
                "payload_json": {"depends_on": ["code"], "sandbox_mode": "ephemeral"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_queue_insight(status, tenant_id, **_kwargs):
        return {"tenant_id": tenant_id, "queued": 0, "running": 1}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["multi_agent"]["enabled"] is True
    assert body["multi_agent"]["execution_mode"] == "multi_agent"
    assert body["multi_agent"]["counts"] == {
        "configured": 3,
        "recorded": 3,
        "completed": 1,
        "ready": 1,
        "blocked": 1,
        "missing_dependencies": 0,
        "hidden_dependencies": 0,
    }
    assert body["multi_agent"]["gates"]["dispatch"] == {
        "enabled": False,
        "reason": "admin_only_dispatch",
        "method": None,
        "href": None,
    }
    assert body["multi_agent"]["steps"] == [
        {
            "step_key": "plan",
            "step_id": "step-plan",
            "title": "Plan",
            "role": "planner",
            "sequence": 1,
            "status": "succeeded",
            "depends_on": [],
            "dependency_statuses": [],
            "ready": False,
            "blocked_reason": "terminal_step",
            "source": "recorded",
        },
        {
            "step_key": "code",
            "step_id": "step-code",
            "title": "Code",
            "role": "coder",
            "sequence": 2,
            "status": "pending",
            "depends_on": ["plan"],
            "dependency_statuses": [{"step_key": "plan", "status": "succeeded"}],
            "ready": True,
            "blocked_reason": None,
            "source": "recorded",
        },
        {
            "step_key": "verify",
            "step_id": "step-verify",
            "title": "verify",
            "role": None,
            "sequence": 3,
            "status": "pending",
            "depends_on": ["code"],
            "dependency_statuses": [{"step_key": "code", "status": "pending"}],
            "ready": False,
            "blocked_reason": "waiting_on_dependencies",
            "source": "recorded",
        },
    ]
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump
    assert "skill_ids" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "mcp_tool_ids" not in public_dump
    assert "/tmp/runtime" not in public_dump


def test_run_control_readiness_enables_admin_multi_agent_dispatch_gate(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "title": "Plan"},
                    {"step_key": "code", "title": "Code", "depends_on": ["plan"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {"output": "plan output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {"depends_on": ["plan"]},
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

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=admin_headers())

    assert response.status_code == 200
    assert response.json()["multi_agent"]["gates"]["dispatch"] == {
        "enabled": True,
        "reason": "ready_steps_available",
        "method": "POST",
        "href": "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
    }


def test_run_control_readiness_keeps_admin_dispatch_gate_closed_for_unsafe_ready_step(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "qa-file-reviewer", "title": "Review"},
                    {"step_key": "blocked", "title": "Blocked", "depends_on": ["qa-file-reviewer"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-review",
                "run_id": run_id,
                "step_key": "qa-file-reviewer",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Review",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {"output": "review output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-blocked",
                "run_id": run_id,
                "step_key": "blocked",
                "step_kind": "agent",
                "status": "pending",
                "title": "Blocked",
                "role": "worker",
                "sequence": 2,
                "payload_json": {"depends_on": ["qa-file-reviewer"]},
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

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["multi_agent"]["counts"]["ready"] == 1
    assert body["multi_agent"]["gates"]["dispatch"] == {
        "enabled": False,
        "reason": "no_safe_ready_steps",
        "method": None,
        "href": None,
    }


def test_run_control_readiness_keeps_admin_dispatch_gate_closed_for_terminal_run(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="failed")
        row["input_json"] = {
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "title": "Plan"},
                    {"step_key": "code", "title": "Code", "depends_on": ["plan"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {"output": "plan output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {"depends_on": ["plan"]},
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

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["multi_agent"]["counts"]["ready"] == 1
    assert body["multi_agent"]["gates"]["dispatch"] == {
        "enabled": False,
        "reason": "run_not_dispatchable",
        "method": None,
        "href": None,
    }


def test_run_control_readiness_blocks_hidden_multi_agent_dependencies(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "safe", "title": "Safe", "role": "worker"},
                    {"step_key": "blocked", "title": "Blocked", "depends_on": ["qa-file-reviewer"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-safe",
                "run_id": run_id,
                "step_key": "safe",
                "step_kind": "agent",
                "status": "pending",
                "title": "Safe",
                "role": "worker",
                "sequence": 1,
                "payload_json": {},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-blocked",
                "run_id": run_id,
                "step_key": "blocked",
                "step_kind": "agent",
                "status": "pending",
                "title": "Blocked",
                "role": "worker",
                "sequence": 2,
                "payload_json": {"depends_on": ["qa-file-reviewer"]},
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

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["multi_agent"]["enabled"] is True
    assert body["multi_agent"]["execution_mode"] == "multi_agent"
    assert body["multi_agent"]["counts"]["ready"] == 1
    assert body["multi_agent"]["counts"]["blocked"] == 1
    assert body["multi_agent"]["counts"]["hidden_dependencies"] == 1
    assert body["multi_agent"]["steps"][1]["depends_on"] == []
    assert body["multi_agent"]["steps"][1]["dependency_statuses"] == [
        {"step_key": None, "status": "hidden", "reason": "unsafe_dependency"}
    ]
    assert body["multi_agent"]["steps"][1]["ready"] is False
    assert body["multi_agent"]["steps"][1]["blocked_reason"] == "hidden_dependencies"
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump


def test_run_control_readiness_hides_dirty_multi_agent_execution_mode(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "message": "legacy configured steps",
                "execution_mode": "qa-file-reviewer:/tmp/runtime/secret-token",
                "multi_agent_steps": [{"step_key": "legacy", "title": "Legacy"}],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["multi_agent"] is None
    public_dump = str(body)
    assert "qa-file-reviewer" not in public_dump
    assert "/tmp/runtime" not in public_dump
    assert "secret-token" not in public_dump


def test_run_control_readiness_does_not_enable_non_multi_agent_configured_steps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "message": "legacy configured steps",
                "execution_mode": "single_agent",
                "multi_agent_steps": [{"step_key": "legacy", "title": "Legacy"}],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    assert response.json()["multi_agent"] is None


def test_admin_multi_agent_dispatch_claim_records_ledger_event_and_audit(monkeypatch):
    calls = []

    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        assert tenant_id == "default"
        assert run_id == "run-ready"
        assert for_update is True
        row = readiness_run_row(status="running")
        row["trace_id"] = "trace-ready"
        row["input_json"] = {
            "input": {
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "title": "Plan"},
                    {"step_key": "code", "title": "Code", "role": "coder", "depends_on": ["plan"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        if any(item[0] == "claim" for item in calls):
            return [
                {
                    "id": "step-code",
                    "run_id": run_id,
                    "step_key": "code",
                    "step_kind": "agent",
                    "status": "running",
                    "title": "Code",
                    "role": "coder",
                    "sequence": 2,
                    "payload_json": {
                        "depends_on": ["plan"],
                        "dispatch_state": "claimed",
                        "dispatch_kind": "subagent",
                    },
                    "started_at": None,
                    "finished_at": None,
                    "created_at": None,
                    "updated_at": None,
                }
            ]
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {"output": "plan output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {"depends_on": ["plan"]},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_claim(conn, **kwargs):
        calls.append(("claim", kwargs))
        return {
            "dispatch_id": "dispatch-code",
            "event_id": "evt-code",
            "audit_id": "aud-code",
            "step": {
                "id": "step-code",
                "run_id": "run-ready",
                "step_key": "code",
                "step_kind": "agent",
                "status": "running",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["plan"],
                    "dispatch_state": "claimed",
                    "dispatch_kind": "subagent",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr(
        "app.routes.runs.get_settings",
        lambda: type("S", (), {"multi_agent_dispatch_lease_ttl_seconds": 123})(),
    )
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fake_claim, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
        json={"step_key": "code"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.multi-agent-dispatch-claim.v1"
    assert body["status"] == "claimed"
    assert body["dispatch_id"] == "dispatch-code"
    assert body["event_id"] == "evt-code"
    assert body["audit_id"] == "aud-code"
    assert body["step"]["status"] == "running"
    assert body["step"]["payload"]["dispatch_state"] == "claimed"
    assert calls == [
        (
            "claim",
            {
                "tenant_id": "default",
                "run_id": "run-ready",
                "claimed_by": "admin-a",
                "trace_id": "trace-ready",
                "step_key": "code",
                "step_kind": "agent",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "depends_on": ["plan"],
                "lease_ttl_seconds": 123,
            },
        )
    ]


def test_admin_multi_agent_dispatch_claim_rejects_unsafe_dependency_without_writes(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "safe", "title": "Safe"},
                    {"step_key": "blocked", "title": "Blocked", "depends_on": ["qa-file-reviewer"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-safe",
                "run_id": run_id,
                "step_key": "safe",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Safe",
                "role": "worker",
                "sequence": 1,
                "payload_json": {"output": "safe output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fail_claim(*args, **kwargs):
        raise AssertionError("unsafe dependency must not be claimed")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fail_claim, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
        json={"step_key": "blocked"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "unsafe_step_reference"


def test_multi_agent_dispatch_claim_requires_admin(monkeypatch):
    async def fail_get_run(*args, **kwargs):
        raise AssertionError("non-admin claim must fail before loading the run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fail_get_run, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
        json={"step_key": "code"},
        headers=headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_required"


def test_multi_agent_dispatch_tick_requires_admin(monkeypatch):
    async def fail_get_run(*args, **kwargs):
        raise AssertionError("ordinary user must fail before dispatch tick")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fail_get_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/multi-agent/dispatch/tick", headers=headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_required"


def test_multi_agent_dispatch_tick_revocation_denies_before_claim_child_or_enqueue(monkeypatch):
    calls = []
    run = {
        "id": "run-parent",
        "tenant_id": "default",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "principal_roles": ["qa_operator", "user"],
        "principal_department_id": "qa",
        "auth_source": "session-token",
        "status": "running",
        "input_json": {
            "input": {
                "multi_agent_steps": [
                    {"step_key": "inspect", "mcpToolIds": ["revoked-tool"]},
                ]
            }
        },
    }

    @asynccontextmanager
    async def tick_transaction():
        yield object()

    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        calls.append(("get_run", tenant_id, run_id, for_update))
        return run

    async def deny_persisted(conn, *, tenant_id, run_id, run=None):
        calls.append(("authorize", tenant_id, run_id, run))
        raise RepositoryAuthorizationError("capability_not_authorized")

    async def fail_dispatch(*args, **kwargs):
        calls.append(("dispatch", kwargs))
        raise AssertionError("revoked parent must not dispatch")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", tick_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run, raising=False)
    monkeypatch.setattr("app.routes.runs._authorize_persisted_run_for_queue", deny_persisted, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fail_dispatch, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fail_dispatch, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fail_dispatch, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_dispatch, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/multi-agent/dispatch/tick", headers=admin_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "capability_not_authorized"
    assert calls == [
        ("get_run", "default", "run-parent", True),
        ("authorize", "default", "run-parent", run),
    ]


def test_multi_agent_dispatch_tick_rejects_when_no_ready_step(monkeypatch):
    @asynccontextmanager
    async def tick_transaction():
        yield object()

    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        assert (tenant_id, run_id, for_update) == ("default", "run-parent", True)
        return {
            "id": "run-parent",
            "tenant_id": "default",
            "trace_id": "trace-parent",
            "status": "running",
            "input_json": {
                "input": {
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [
                        {"step_key": "code", "role": "coder", "depends_on": ["plan"]},
                    ],
                }
            },
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        assert (tenant_id, run_id) == ("default", "run-parent")
        return []

    async def fail_claim(*args, **kwargs):
        raise AssertionError("no-ready tick must not claim a step")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", tick_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fail_claim, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/multi-agent/dispatch/tick", headers=admin_headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "no_ready_steps"


@pytest.mark.parametrize(
    "unsafe_step_key",
    [
        "qa-file-reviewer",
        "private_payload",
        "a" * 64,
        "/app/private.py",
    ],
)
def test_multi_agent_dispatch_tick_rejects_when_only_ready_step_is_unsafe(monkeypatch, unsafe_step_key):
    @asynccontextmanager
    async def tick_transaction():
        yield object()

    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        assert (tenant_id, run_id, for_update) == ("default", "run-parent", True)
        return {
            "id": "run-parent",
            "tenant_id": "default",
            "trace_id": "trace-parent",
            "status": "running",
            "input_json": {
                "input": {
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [
                        {"step_key": unsafe_step_key, "role": "coder", "depends_on": []},
                    ],
                }
            },
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        assert (tenant_id, run_id) == ("default", "run-parent")
        return []

    async def fail_claim(*args, **kwargs):
        raise AssertionError("unsafe tick must not claim a step")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", tick_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fail_claim, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/multi-agent/dispatch/tick", headers=admin_headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "no_safe_ready_steps"


def test_multi_agent_dispatch_tick_auth_snapshot_claims_handoffs_and_enqueues_next_ready_step(monkeypatch):
    calls = []

    @asynccontextmanager
    async def tick_transaction():
        yield object()

    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        calls.append(("get_run", tenant_id, run_id, for_update))
        return {
            "id": "run-parent",
            "tenant_id": "default",
            "trace_id": "trace-parent",
            "status": "running",
            "input_json": {
                "input": {
                    "message": "build feature",
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [
                        {"step_key": "plan", "role": "planner", "depends_on": []},
                        {"step_key": "code", "role": "coder", "depends_on": ["plan"]},
                    ],
                }
            },
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        calls.append(("list_steps", tenant_id, run_id))
        return [
            {
                "id": "step-plan",
                "run_id": "run-parent",
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "output": "safe plan",
                    "checkpoint_id": "checkpoint_step-plan",
                    "source_step_id": "step-plan",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_claim(conn, **kwargs):
        calls.append(("claim", kwargs))
        assert kwargs["tenant_id"] == "default"
        assert kwargs["run_id"] == "run-parent"
        assert kwargs["claimed_by"] == "admin-a"
        assert kwargs["step_key"] == "code"
        assert kwargs["depends_on"] == ["plan"]
        return {
            "dispatch_id": "dispatch-code",
            "event_id": "evt-claim",
            "audit_id": "aud-claim",
            "step": {
                "id": "step-code",
                "run_id": "run-parent",
                "step_key": "code",
                "step_kind": "agent",
                "status": "running",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["plan"],
                    "dispatch_state": "claimed",
                    "dispatch_id": "dispatch-code",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        }

    async def fake_handoff(conn, **kwargs):
        calls.append(("handoff", kwargs))
        assert kwargs == {
            "tenant_id": "default",
            "parent_run_id": "run-parent",
            "dispatch_id": "dispatch-code",
            "handed_off_by": "admin-a",
            "active_run_admission_limit": 3,
        }
        return {
            "child_run_id": "run-child",
            "run_id": "run-child",
            "parent_step_id": "step-code",
            "step_key": "code",
            "user_id": "user-a",
            "session_id": "session-a",
            "workspace_id": "default",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "principal_roles": ["qa_operator", "user"],
            "principal_department_id": "qa",
            "auth_source": "session-token",
            "file_ids": [],
            "input": {"message": "build feature"},
            "executor_type": "claude-agent-worker",
            "skill_version": "0.1.0",
            "release_decision": {},
            "event_id": "evt-handoff",
            "child_event_id": "evt-child-created",
            "audit_id": "aud-handoff",
        }

    async def fake_prepare(conn, *, copied, principal, queue_principal=None, source, authorized_source_run_id=None):
        calls.append(
            (
                "prepare",
                copied["run_id"],
                principal.user_id,
                queue_principal.user_id,
                queue_principal.roles,
                queue_principal.department_id,
                queue_principal.source,
                source,
                authorized_source_run_id,
            )
        )
        assert source == "multi_agent_dispatch_tick"
        assert authorized_source_run_id == "run-parent"
        return {"run_id": copied["run_id"], "context_snapshot_id": "ctx-child"}

    async def fake_enqueue(payload):
        calls.append(("enqueue", payload))
        return 7

    async def fake_queue_insight(tenant_id, **_kwargs):
        calls.append(("queue_insight", tenant_id))
        return {"queued": 1}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr(
        "app.routes.runs.get_settings",
        lambda: type("S", (), {"multi_agent_dispatch_lease_ttl_seconds": 300, "max_active_runs_per_user": 3})(),
    )
    monkeypatch.setattr("app.routes.runs.transaction", tick_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fake_claim, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fake_handoff, raising=False)
    monkeypatch.setattr("app.routes.runs.prepare_copied_run_for_queue", fake_prepare, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue, raising=False)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_queue_insight, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/multi-agent/dispatch/tick", headers=admin_headers())

    assert response.status_code == 200
    assert response.json() == {
        "contract_version": "ai-platform.multi-agent-dispatch-tick.v1",
        "parent_run_id": "run-parent",
        "dispatch_id": "dispatch-code",
        "step_key": "code",
        "step_id": "step-code",
        "status": "queued",
        "child_run_id": "run-child",
        "session_id": "session-a",
        "queue_position": 7,
        "queue_insight": {"queued": 1},
        "claim_event_id": "evt-claim",
        "claim_audit_id": "aud-claim",
        "handoff_event_id": "evt-handoff",
        "child_event_id": "evt-child-created",
        "handoff_audit_id": "aud-handoff",
    }
    assert [item[0] for item in calls] == [
        "get_run",
        "list_steps",
        "claim",
        "handoff",
        "prepare",
        "enqueue",
        "queue_insight",
    ]
    prepare = next(item for item in calls if item[0] == "prepare")
    assert prepare[3:7] == ("user-a", ["qa_operator", "user"], "qa", "session-token")


def test_admin_multi_agent_dispatch_claim_maps_repository_conflict_to_409(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "title": "Plan"},
                    {"step_key": "code", "title": "Code", "depends_on": ["plan"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {"output": "plan output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {"depends_on": ["plan"]},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_claim(conn, **kwargs):
        raise RepositoryConflictError("dispatch_step_not_persisted")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fake_claim, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
        json={"step_key": "code"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "dispatch_step_not_persisted"


def test_multi_agent_dispatch_hidden_claim_event_is_absent_from_ordinary_events(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["id"] = run_id
        return row

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        return [
            {
                "id": "evt-visible",
                "trace_id": "trace-ready",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "run_started",
                "stage": "control",
                "message": "started",
                "severity": "info",
                "visible_to_user": True,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {"visible_to_user": True},
                "created_at": None,
            },
            {
                "id": "evt-claim",
                "trace_id": "trace-ready",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 2,
                "event_type": "agent_step_started",
                "stage": "agent",
                "message": "Multi-agent step dispatch claimed",
                "severity": "info",
                "visible_to_user": False,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "visible_to_user": False,
                    "step_key": "code",
                    "dispatch_id": "dispatch-code",
                },
                "created_at": None,
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/events", headers=headers())

    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["event_id"] for event in events] == ["evt-visible"]
    assert "dispatch-code" not in str(events)


def test_multi_agent_dispatch_handoff_requires_admin(monkeypatch):
    async def fail_handoff(*args, **kwargs):
        raise AssertionError("non-admin handoff must fail before repository writes")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fail_handoff, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims/dispatch-code/handoff",
        headers=headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_required"


def test_multi_agent_dispatch_handoff_revocation_denies_before_child_or_enqueue(monkeypatch):
    calls = []

    async def deny_persisted(conn, *, tenant_id, run_id, run=None):
        calls.append(("authorize", tenant_id, run_id, run))
        raise RepositoryAuthorizationError("capability_not_authorized")

    async def fail_child(*args, **kwargs):
        calls.append(("child", kwargs))
        raise AssertionError("revoked parent must not create a dispatch child")

    async def fail_enqueue(*args, **kwargs):
        calls.append(("enqueue", kwargs))
        raise AssertionError("revoked parent must not enqueue")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs._authorize_persisted_run_for_queue", deny_persisted, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fail_child, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-parent/multi-agent/dispatch/claims/dispatch-code/handoff",
        headers=admin_headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "capability_not_authorized"
    assert calls == [("authorize", "default", "run-parent", None)]


def test_admin_multi_agent_dispatch_handoff_creates_owner_child_run_and_enqueues(monkeypatch):
    calls = {"handoff": [], "context": [], "queue": [], "step": [], "auth_snapshot": []}

    async def fake_handoff(conn, *, tenant_id, parent_run_id, dispatch_id, handed_off_by, active_run_admission_limit):
        calls["handoff"].append((tenant_id, parent_run_id, dispatch_id, handed_off_by, active_run_admission_limit))
        return {
            "parent_run_id": parent_run_id,
            "parent_step_id": "step-code",
            "step_key": "code",
            "dispatch_id": dispatch_id,
            "child_run_id": "run-child",
            "session_id": "ses-owner",
            "workspace_id": "default",
            "user_id": "user-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "principal_roles": ["qa_operator", "user"],
            "principal_department_id": "qa",
            "auth_source": "session-token",
            "file_ids": ["file-a"],
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [{"step_key": "code", "role": "coder", "depends_on": ["plan"]}],
                "multi_agent_dispatch": {
                    "parent_run_id": parent_run_id,
                    "parent_step_id": "step-code",
                    "step_key": "code",
                    "dispatch_id": dispatch_id,
                },
                "resume": {
                    "copied_from_run_id": parent_run_id,
                    "completed_step_outputs": {"plan": "plan output"},
                    "completed_step_checkpoints": {
                        "plan": {
                            "checkpoint_id": "checkpoint-plan",
                            "source_step_id": "step-plan",
                            "copied_from_run_id": parent_run_id,
                        }
                    },
                },
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
            "event_id": "evt-handoff",
            "child_event_id": "evt-child",
            "audit_id": "aud-handoff",
        }

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        assert skill_id == "general-chat"
        assert input_payload["multi_agent_dispatch"]["dispatch_id"] == "dispatch-code"
        assert release_policy_version == ""
        return [{"skill_id": skill_id, "content_hash": "hash-a"}]

    async def fake_record_initial_context_snapshot(conn, **kwargs):
        calls["context"].append(kwargs)
        return {"context_snapshot_id": "ctx-child", "source": kwargs["source"]}

    async def fake_update_run_input_skill_version(conn, **kwargs):
        calls["skill_version"] = kwargs

    async def fake_update_run_auth_snapshot(conn, **kwargs):
        calls["auth_snapshot"].append(kwargs)

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_seed_copied_run_steps(conn, **kwargs):
        calls["step"].append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"].append(payload)
        return 4

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        return {"tenant_id": tenant_id, "reason": "worker_available"}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fake_handoff, raising=False)
    monkeypatch.setattr("app.routes.runs._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_initial_context_snapshot)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr(
        "app.routes.runs.repositories.update_run_auth_snapshot",
        fake_update_run_auth_snapshot,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.seed_copied_run_steps", fake_seed_copied_run_steps)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims/dispatch-code/handoff",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "contract_version": "ai-platform.multi-agent-dispatch-handoff.v1",
        "parent_run_id": "run-ready",
        "dispatch_id": "dispatch-code",
        "step_key": "code",
        "step_id": "step-code",
        "status": "queued",
        "child_run_id": "run-child",
        "session_id": "ses-owner",
        "queue_position": 4,
        "queue_insight": {"tenant_id": "default", "reason": "worker_available"},
        "event_id": "evt-handoff",
        "child_event_id": "evt-child",
        "audit_id": "aud-handoff",
    }
    assert calls["handoff"] == [("default", "run-ready", "dispatch-code", "admin-a", 3)]
    assert calls["context"][0]["source"] == "multi_agent_dispatch_handoff"
    assert calls["context"][0]["source_run_id"] == "run-ready"
    assert calls["context"][0]["user_id"] == "user-a"
    assert calls["context"][0]["run_id"] == "run-child"
    assert calls["queue"][0]["user_id"] == "user-a"
    assert calls["queue"][0]["run_id"] == "run-child"
    assert calls["queue"][0]["context_snapshot_id"] == "ctx-child"
    assert calls["queue"][0]["context_snapshot"]["source"] == "multi_agent_dispatch_handoff"
    assert calls["step"][0]["source"] == "multi_agent_dispatch_handoff"
    assert calls["auth_snapshot"] == [
        {
            "tenant_id": "default",
            "run_id": "run-child",
            "principal_roles": ["qa_operator", "user"],
            "principal_department_id": "qa",
            "auth_source": "session-token",
        }
    ]


def test_admin_multi_agent_dispatch_handoff_rejects_duplicate_without_enqueue(monkeypatch):
    async def fake_handoff(conn, *, tenant_id, parent_run_id, dispatch_id, handed_off_by, active_run_admission_limit):
        raise RepositoryConflictError("dispatch_already_handed_off")

    async def fail_enqueue(payload):
        raise AssertionError("duplicate handoff must not enqueue")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fake_handoff, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims/dispatch-code/handoff",
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "dispatch_already_handed_off"


def test_admin_multi_agent_dispatch_handoff_commits_release_on_admission_conflict(monkeypatch):
    tx_clean_exits = []

    class Transaction:
        async def __aenter__(self):
            return EmptyPropagationConnection()

        async def __aexit__(self, exc_type, exc, tb):
            tx_clean_exits.append(exc_type is None)
            return False

    def transaction_with_exit_probe():
        return Transaction()

    async def fake_handoff(conn, *, tenant_id, parent_run_id, dispatch_id, handed_off_by, active_run_admission_limit):
        raise RepositoryConflictError("user_active_run_limit_exceeded")

    async def fail_enqueue(payload):
        raise AssertionError("admission conflict must not enqueue")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", transaction_with_exit_probe)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fake_handoff, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims/dispatch-code/handoff",
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "user_active_run_limit_exceeded"
    assert tx_clean_exits == [True]


def test_admin_multi_agent_dispatch_handoff_rejects_expired_claim_without_enqueue(monkeypatch):
    async def fake_handoff(conn, *, tenant_id, parent_run_id, dispatch_id, handed_off_by, active_run_admission_limit):
        raise RepositoryConflictError("dispatch_claim_expired")

    async def fail_enqueue(payload):
        raise AssertionError("expired handoff must not enqueue")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.create_multi_agent_dispatch_child_run", fake_handoff, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims/dispatch-code/handoff",
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "dispatch_claim_expired"


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


def test_run_checkpoint_audit_redacts_fingerprint_step_key_for_ordinary_user(monkeypatch):
    unsafe_hash = "a" * 64
    unsafe_step_key = f"build-{unsafe_hash}"

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return resume_manifest_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-build",
                "run_id": run_id,
                "step_key": unsafe_step_key,
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Build fingerprinted step",
                "role": "builder",
                "sequence": 1,
                "payload_json": {"output": "reusable output must not leak"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-resume/checkpoints/audit", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["uncheckpointed_reusable_steps"] == [
        {
            "step_id": "step-build",
            "step_key": "step-build",
            "status": "succeeded",
            "reason": "missing_checkpoint_id",
        }
    ]
    public_dump = str(body)
    assert unsafe_step_key not in public_dump
    assert unsafe_hash not in public_dump
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

    async def fake_get_queue_insight(tenant_id, **_kwargs):
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
    persisted_json = next(
        item
        for sql, params in conn.executed
        if sql.startswith("insert into runs")
        for item in params
        if isinstance(item, str) and item.startswith("{")
    )
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
    persisted_json = next(
        item
        for sql, params in conn.executed
        if sql.startswith("insert into runs")
        for item in params
        if isinstance(item, str) and item.startswith("{")
    )
    assert json.loads(persisted_json)["skill_version"] == "hash-old"
    assert json.loads(persisted_json)["release_decision"]["selected_track"] == "previous"


@pytest.mark.asyncio
async def test_copy_run_as_new_task_auth_snapshot_persists_trace_contract_and_principal(monkeypatch):
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
            "principal_roles": ["qa_operator", "user"],
            "principal_department_id": "qa",
            "auth_source": "session-token",
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
    assert "principal_roles, principal_department_id, auth_source" in sql
    assert copied["run_id"] in params
    assert any(str(item).startswith("trace_") for item in params)
    assert "ai-platform.run.v1" in params
    assert "ai-platform.executor-result.v1" in params
    assert json.dumps(["qa_operator", "user"], ensure_ascii=False) in params
    assert "qa" in params
    assert "session-token" in params
    assert copied["principal_roles"] == ["qa_operator", "user"]
    assert copied["principal_department_id"] == "qa"
    assert copied["auth_source"] == "session-token"


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
                            "id": "step-code-source",
                            "step_key": "code",
                            "payload_json": {
                                "output": "code output",
                                "checkpoint_id": "checkpoint-code",
                                "metadata": {"runner": "claude_agent_sdk"},
                            },
                        },
                        {
                            "id": "step-docs-source",
                            "step_key": "docs",
                            "payload_json": {"output": "docs output", "checkpoint_id": "checkpoint-docs"},
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
        "completed_step_checkpoints": {
            "code": {
                "checkpoint_id": "checkpoint-code",
                "source_step_id": "step-code-source",
                "copied_from_run_id": "run_old",
            },
            "docs": {
                "checkpoint_id": "checkpoint-docs",
                "source_step_id": "step-docs-source",
                "copied_from_run_id": "run_old",
            },
        },
    }


@pytest.mark.asyncio
async def test_resume_run_as_new_task_rejects_active_source_without_copy(monkeypatch):
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

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        assert for_update is True
        return {
            "id": run_id,
            "status": "running",
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "resume"}},
        }

    async def fail_copy_run_as_new_task(*args, **kwargs):
        raise AssertionError("active source must not be copied for resume")

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.copy_run_as_new_task", fail_copy_run_as_new_task)
    conn = RecordingConnection()

    with pytest.raises(repositories.RepositoryConflictError, match="active_run"):
        await repositories.resume_run_as_new_task(
            conn,
            tenant_id="default",
            user_id="user-a",
            run_id="run_active",
        )

    assert not any("insert into runs" in sql for sql, _params in conn.executed)


@pytest.mark.asyncio
async def test_resume_run_as_new_task_rejects_source_without_completed_outputs(monkeypatch):
    from app import repositories

    class FakeCursor:
        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

    class RecordingConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, params):
            self.executed.append((" ".join(sql.split()), params))
            return FakeCursor()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        assert for_update is True
        return {
            "id": run_id,
            "status": "failed",
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "resume"}},
        }

    async def fail_copy_run_as_new_task(*args, **kwargs):
        raise AssertionError("source without checkpoint outputs must not be copied for resume")

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories.copy_run_as_new_task", fail_copy_run_as_new_task)
    conn = RecordingConnection()

    with pytest.raises(repositories.RepositoryConflictError, match="no_checkpoint_outputs"):
        await repositories.resume_run_as_new_task(
            conn,
            tenant_id="default",
            user_id="user-a",
            run_id="run_failed",
        )

    assert any("from run_steps" in sql for sql, _params in conn.executed)
    assert not any("insert into runs" in sql for sql, _params in conn.executed)


@pytest.mark.asyncio
async def test_resume_run_as_new_task_rejects_when_resume_is_already_active(monkeypatch):
    from app import repositories

    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        assert for_update is True
        return {
            "id": run_id,
            "status": "failed",
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {"input": {"message": "resume"}},
        }

    async def fake_completed_steps_for_resume(conn, *, tenant_id, run_id):
        calls.append(("completed", tenant_id, run_id))
        return {"code": "code output"}, {
            "code": {
                "checkpoint_id": "checkpoint-code",
                "source_step_id": "step-code-source",
                "copied_from_run_id": run_id,
            }
        }

    async def fake_get_active_resume_for_source_run(conn, *, tenant_id, user_id, run_id):
        calls.append(("active_resume", tenant_id, user_id, run_id))
        return {"id": "run-resume-active", "status": "queued"}

    async def fail_copy_run_as_new_task(*args, **kwargs):
        raise AssertionError("active resume source must not be copied again")

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories._completed_steps_for_resume", fake_completed_steps_for_resume)
    monkeypatch.setattr("app.repositories.get_active_resume_for_source_run", fake_get_active_resume_for_source_run, raising=False)
    monkeypatch.setattr("app.repositories.copy_run_as_new_task", fail_copy_run_as_new_task)

    with pytest.raises(repositories.RepositoryConflictError, match="resume_already_active"):
        await repositories.resume_run_as_new_task(
            object(),
            tenant_id="default",
            user_id="user-a",
            run_id="run-failed",
        )

    assert calls == [
        ("completed", "default", "run-failed"),
        ("active_resume", "default", "user-a", "run-failed"),
    ]


@pytest.mark.asyncio
async def test_resume_run_as_new_task_records_resume_events_and_audit(monkeypatch):
    from app import repositories

    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        assert for_update is True
        return {
            "id": run_id,
            "status": "failed",
            "workspace_id": "default",
            "session_id": "ses_old",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "trace_id": "trace-source",
            "input_json": {"input": {"message": "resume"}},
        }

    async def fake_completed_steps_for_resume(conn, *, tenant_id, run_id):
        calls.append(("completed", tenant_id, run_id))
        return {"code": "code output"}, {
            "code": {
                "checkpoint_id": "checkpoint-code",
                "source_step_id": "step-code-source",
                "copied_from_run_id": run_id,
            }
        }

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls.append(("copy", tenant_id, user_id, run_id))
        return {
            "session_id": "ses-old",
            "run_id": "run-resume-new",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "workspace_id": "default",
            "file_ids": [],
            "input": {"message": "resume", "resume": {"completed_step_outputs": {"code": "code output"}}},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-a",
            "release_policy_version": "",
            "release_decision": {},
        }

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))

    async def fake_get_active_resume_for_source_run(conn, *, tenant_id, user_id, run_id):
        calls.append(("active_resume", tenant_id, user_id, run_id))
        return None

    monkeypatch.setattr("app.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.repositories._completed_steps_for_resume", fake_completed_steps_for_resume)
    monkeypatch.setattr("app.repositories.get_active_resume_for_source_run", fake_get_active_resume_for_source_run)
    monkeypatch.setattr("app.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    copied = await repositories.resume_run_as_new_task(
        object(),
        tenant_id="default",
        user_id="user-a",
        run_id="run-failed",
    )

    assert copied["run_id"] == "run-resume-new"
    assert ("completed", "default", "run-failed") in calls
    assert ("active_resume", "default", "user-a", "run-failed") in calls
    assert ("copy", "default", "user-a", "run-failed") in calls
    events = [item[1] for item in calls if item[0] == "event"]
    assert [event["event_type"] for event in events] == ["resume_requested", "run_resume_created"]
    assert events[0]["run_id"] == "run-failed"
    assert events[0]["trace_id"] == "trace-source"
    assert events[0]["payload"]["new_run_id"] == "run-resume-new"
    assert events[1]["run_id"] == "run-resume-new"
    assert events[1]["payload"]["copied_from_run_id"] == "run-failed"
    audits = [item[1] for item in calls if item[0] == "audit"]
    assert audits == [
        {
            "tenant_id": "default",
            "user_id": "user-a",
            "action": "run.resume",
            "target_type": "run",
            "target_id": "run-failed",
            "trace_id": "trace-source",
            "payload_json": {
                "source_run_id": "run-failed",
                "new_run_id": "run-resume-new",
                "source_status": "failed",
            },
        }
    ]


@pytest.mark.asyncio
async def test_copy_run_as_new_task_drops_user_controlled_resume_when_no_verified_outputs(monkeypatch):
    from app import repositories

    class FakeCursor:
        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

    class StepConnection:
        async def execute(self, sql, params):
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
                    "resume": {
                        "copied_from_run_id": "run-other",
                        "completed_step_outputs": {"code": "forged output"},
                        "completed_step_checkpoints": {
                            "code": {
                                "checkpoint_id": "checkpoint-forged",
                                "source_step_id": "step-forged",
                                "copied_from_run_id": "run-other",
                            }
                        },
                    },
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

    assert copied["input"]["copied_from_run_id"] == "run_old"
    assert "resume" not in copied["input"]


@pytest.mark.asyncio
async def test_copy_run_as_new_task_preserves_chained_checkpoint_producer_lineage(monkeypatch):
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
                            "id": "step-reused-mid",
                            "step_key": "code",
                            "payload_json": {
                                "output": "code output",
                                "checkpoint_id": "checkpoint-code",
                                "source_step_id": "step-code-original",
                                "copied_from_run_id": "run_original",
                            },
                        }
                    ]
                )
            return FakeCursor()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "workspace_id": "default",
            "session_id": "ses_mid",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "input_json": {
                "input": {
                    "message": "build feature",
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [{"step_key": "code", "role": "coding"}],
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
        run_id="run_mid",
    )

    assert copied["input"]["resume"]["completed_step_checkpoints"] == {
        "code": {
            "checkpoint_id": "checkpoint-code",
            "source_step_id": "step-code-original",
            "copied_from_run_id": "run_original",
        }
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
async def test_retry_run_as_new_task_auth_snapshot_records_retry_events_and_audit(monkeypatch):
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
            "principal_roles": ["qa_operator", "user"],
            "principal_department_id": "qa",
            "auth_source": "session-token",
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
    assert copied["principal_roles"] == ["qa_operator", "user"]
    assert copied["principal_department_id"] == "qa"
    assert copied["auth_source"] == "session-token"
    assert calls[0] == ("source_lock", "default", "user-a", "run-failed", True)
    assert calls[1] == ("active_retry", "default", "user-a", "run-failed")
    event_types = [call[1]["event_type"] for call in calls if call[0] == "event"]
    assert event_types == ["retry_requested", "run_retry_created"]
    audit = [call[1] for call in calls if call[0] == "audit"][0]
    assert audit["action"] == "run.retry"
    assert audit["target_id"] == "run-failed"
    assert audit["payload_json"]["new_run_id"] == "run-new"


@pytest.mark.asyncio
async def test_create_multi_agent_dispatch_child_run_records_parent_child_events_and_audit(monkeypatch):
    from app import repositories
    import json

    calls = []

    parent_run = {
        "id": "run-parent",
        "tenant_id": "default",
        "workspace_id": "default",
        "session_id": "ses-owner",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "principal_roles": ["qa_operator", "user"],
        "principal_department_id": "qa",
        "auth_source": "session-token",
        "trace_id": "trace-parent",
        "status": "running",
        "input_json": {
            "workerPath": "/var/lib/ai-platform/private-worker.py",
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "mcpToolIds": ["tool-global"],
                "resume": {"completed_step_outputs": {"code": "forged"}},
                "multi_agent_dispatch": {"dispatch_id": "forged"},
                "multi_agent_steps": [
                    {"step_key": "plan", "role": "planner", "mcp_tool_ids": ["tool-plan"]},
                    {
                        "step_key": "code",
                        "role": "coder",
                        "depends_on": ["plan"],
                        "mcpToolIds": ["tool-code"],
                    },
                ],
            },
            "file_ids": ["file-a"],
        },
    }
    claimed_step = {
        "id": "step-code",
        "run_id": "run-parent",
        "step_key": "code",
        "step_kind": "agent",
        "status": "running",
        "title": "Code",
        "role": "coder",
        "sequence": 2,
        "payload_json": {
            "depends_on": ["plan"],
            "dispatch_state": "claimed",
            "dispatch_id": "dispatch-code",
            "dispatch_lease_expires_at": "2999-01-01T00:00:00+00:00",
        },
    }
    all_steps = [
        {
            "id": "step-plan",
            "run_id": "run-parent",
            "step_key": "plan",
            "step_kind": "agent",
            "status": "succeeded",
            "title": "Plan",
            "role": "planner",
            "sequence": 1,
            "payload_json": {
                "output": "plan output",
                "checkpoint_id": "checkpoint-plan",
                "source_step_id": "step-plan",
            },
        },
        claimed_step,
    ]

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, workspace_id") and "from runs" in normalized:
                return Cursor(row=parent_run)
            if "from run_steps" in normalized and "payload_json->>'dispatch_id'" in normalized:
                return Cursor(row=claimed_step)
            if normalized.startswith("select id, run_id, step_key"):
                return Cursor(rows=all_steps)
            if normalized.startswith("insert into runs"):
                return Cursor()
            if normalized.startswith("update run_steps"):
                return Cursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        assert (tenant_id, agent_id, skill_id) == ("default", "general-agent", "general-chat")
        return {"executor_type": "claude-agent-worker", "skill_version": "hash-a"}

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['event_type']}"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-handoff"

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admission", tenant_id, (user_id, limit)))
        return 0

    monkeypatch.setattr("app.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.enforce_user_active_run_admission", fake_enforce_user_active_run_admission)

    copied = await repositories.create_multi_agent_dispatch_child_run(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        dispatch_id="dispatch-code",
        handed_off_by="admin-a",
        active_run_admission_limit=0,
    )

    assert copied["child_run_id"].startswith("run")
    assert copied["run_id"] == copied["child_run_id"]
    assert copied["user_id"] == "user-a"
    assert copied["session_id"] == "ses-owner"
    assert ("admission", "default", ("user-a", 0)) in calls
    insert_sql, insert_params = next((sql, params) for kind, sql, params in calls if kind == "sql" and sql.startswith("insert into runs"))
    assert "copied_from_run_id" in insert_sql
    assert "principal_roles, principal_department_id, auth_source" in insert_sql
    assert json.dumps(["qa_operator", "user"], ensure_ascii=False) in insert_params
    assert "qa" in insert_params
    assert "session-token" in insert_params
    persisted_input = json.loads(next(item for item in insert_params if isinstance(item, str) and item.startswith("{")))
    assert persisted_input["copied_from_run_id"] == "run-parent"
    assert persisted_input["input"]["multi_agent_dispatch"]["dispatch_id"] == "dispatch-code"
    assert persisted_input["input"]["resume"]["completed_step_outputs"] == {"plan": "plan output"}
    assert persisted_input["input"]["resume"]["completed_step_checkpoints"]["plan"]["checkpoint_id"] == "checkpoint-plan"
    assert persisted_input["input"]["multi_agent_steps"] == [
        {
            "step_key": "code",
            "role": "coder",
            "title": "Code",
            "depends_on": ["plan"],
            "mcp_tool_ids": ["tool-code"],
        }
    ]
    assert persisted_input["input"]["mcp_tool_ids"] == ["tool-global"]
    persisted_dump = json.dumps(persisted_input, ensure_ascii=False)
    assert "tool-plan" not in persisted_dump
    assert "forged" not in persisted_dump
    assert "private-worker" not in persisted_dump
    assert copied["principal_roles"] == ["qa_operator", "user"]
    assert copied["principal_department_id"] == "qa"
    assert copied["auth_source"] == "session-token"
    assert copied["input"]["mcp_tool_ids"] == ["tool-global"]
    assert copied["input"]["multi_agent_steps"][0]["mcp_tool_ids"] == ["tool-code"]
    assert "tool-plan" not in json.dumps(copied["input"], ensure_ascii=False)
    update_sql, update_params = next((sql, params) for kind, sql, params in calls if kind == "sql" and sql.startswith("update run_steps"))
    assert "payload_json = payload_json || %s::jsonb" in update_sql
    update_payload = json.loads(update_params[0])
    assert update_payload["dispatch_child_run_id"] == copied["child_run_id"]
    assert update_payload["dispatch_state"] == "handed_off"
    assert update_params[1:4] == ("default", "step-code", "dispatch-code")
    event_types = [item[1]["event_type"] for item in calls if item[0] == "event"]
    assert event_types == ["multi_agent_dispatch_handoff", "run_multi_agent_child_created"]
    audit = next(item[1] for item in calls if item[0] == "audit")
    assert audit["action"] == "run.multi_agent.dispatch.handoff"
    assert audit["target_id"] == "step-code"
    assert audit["payload_json"]["child_run_id"] == copied["child_run_id"]
    assert audit["payload_json"]["admin_user_id"] == "admin-a"


@pytest.mark.asyncio
async def test_create_multi_agent_dispatch_child_run_enforces_owner_admission(monkeypatch):
    from app import repositories

    calls = []
    parent_run = {
        "id": "run-parent",
        "tenant_id": "default",
        "workspace_id": "default",
        "session_id": "ses-owner",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "trace_id": "trace-parent",
        "status": "running",
        "input_json": {"input": {"execution_mode": "multi_agent"}},
    }
    claimed_step = {
        "id": "step-code",
        "run_id": "run-parent",
        "step_key": "code",
        "step_kind": "agent",
        "status": "running",
        "title": "Code",
        "role": "coder",
        "sequence": 2,
        "payload_json": {
            "dispatch_state": "claimed",
            "dispatch_id": "dispatch-code",
            "dispatch_lease_expires_at": "2999-01-01T00:00:00+00:00",
        },
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, workspace_id") and "from runs" in normalized:
                return Cursor(row=parent_run)
            if "from run_steps" in normalized and "payload_json->>'dispatch_id'" in normalized:
                return Cursor(row=claimed_step)
            if normalized.startswith("update run_steps"):
                return Cursor(row={"id": "step-code", "step_key": "code"})
            if normalized.startswith("insert into runs"):
                raise AssertionError("child run insert must not happen after admission rejection")
            return Cursor()

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admission", tenant_id, user_id, limit))
        raise repositories.RepositoryConflictError("user_active_run_limit_exceeded")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-release"

    monkeypatch.setattr("app.repositories.enforce_user_active_run_admission", fake_enforce_user_active_run_admission)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    with pytest.raises(repositories.RepositoryConflictError, match="user_active_run_limit_exceeded"):
        await repositories.create_multi_agent_dispatch_child_run(
            FakeConnection(),
            tenant_id="default",
            parent_run_id="run-parent",
            dispatch_id="dispatch-code",
            handed_off_by="admin-a",
            active_run_admission_limit=3,
        )

    assert ("admission", "default", "user-a", 3) in calls
    release_sql = next(call[1] for call in calls if call[0] == "sql" and call[1].startswith("update run_steps"))
    assert "status = 'pending'" in release_sql
    audit = next(call[1] for call in calls if call[0] == "audit")
    assert audit["action"] == "run.multi_agent.dispatch.claim_released"
    assert not any(call[0] == "sql" and call[1].startswith("insert into runs") for call in calls)


@pytest.mark.asyncio
async def test_create_multi_agent_dispatch_child_run_builds_single_step_resume_input(monkeypatch):
    from app import repositories

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id, workspace_id") and "from runs" in normalized:
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "workspace_id": "default",
                        "session_id": "ses-owner",
                        "user_id": "user-a",
                        "agent_id": "general-agent",
                        "skill_id": "general-chat",
                        "trace_id": "trace-parent",
                        "status": "running",
                        "input_json": {
                            "input": {
                                "message": "build feature",
                                "execution_mode": "multi_agent",
                                "multi_agent_steps": [
                                    {"step_key": "plan", "role": "planner"},
                                    {"step_key": "code", "role": "coder", "depends_on": ["plan"]},
                                    {"step_key": "verify", "role": "verifier", "depends_on": ["code"]},
                                ],
                            }
                        },
                    }
                )
            if "from run_steps" in normalized and "payload_json->>'dispatch_id'" in normalized:
                return Cursor(
                    row={
                        "id": "step-code",
                        "run_id": "run-parent",
                        "step_key": "code",
                        "step_kind": "agent",
                        "status": "running",
                        "title": "Code",
                        "role": "coder",
                        "sequence": 2,
                        "payload_json": {
                            "depends_on": ["plan"],
                            "dispatch_state": "claimed",
                            "dispatch_id": "dispatch-code",
                            "dispatch_lease_expires_at": "2999-01-01T00:00:00+00:00",
                        },
                    }
                )
            if normalized.startswith("select id, run_id, step_key"):
                return Cursor(
                    rows=[
                        {
                            "id": "step-plan",
                            "run_id": "run-parent",
                            "step_key": "plan",
                            "step_kind": "agent",
                            "status": "succeeded",
                            "title": "Plan",
                            "role": "planner",
                            "sequence": 1,
                            "payload_json": {"output": "plan output"},
                        },
                    ]
                )
            return Cursor()

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"executor_type": "claude-agent-worker", "skill_version": "hash-a"}

    async def fake_append_event(conn, **kwargs):
        return f"evt-{kwargs['event_type']}"

    async def fake_append_audit_log(conn, **kwargs):
        return "aud-handoff"

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        return 0

    monkeypatch.setattr("app.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.enforce_user_active_run_admission", fake_enforce_user_active_run_admission)

    copied = await repositories.create_multi_agent_dispatch_child_run(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        dispatch_id="dispatch-code",
        handed_off_by="admin-a",
        active_run_admission_limit=3,
    )

    assert [step["step_key"] for step in copied["input"]["multi_agent_steps"]] == ["code"]
    assert copied["input"]["multi_agent_steps"][0]["depends_on"] == ["plan"]
    assert copied["input"]["resume"]["completed_step_outputs"] == {"plan": "plan output"}
    assert "verify" not in str(copied["input"])


@pytest.mark.asyncio
async def test_create_multi_agent_dispatch_child_run_rejects_malformed_claim_lease_without_insert(monkeypatch):
    from app import repositories

    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(normalized)
            if normalized.startswith("select id, tenant_id, workspace_id") and "from runs" in normalized:
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "workspace_id": "default",
                        "session_id": "ses-owner",
                        "user_id": "user-a",
                        "agent_id": "general-agent",
                        "skill_id": "general-chat",
                        "trace_id": "trace-parent",
                        "status": "running",
                        "input_json": {"input": {"execution_mode": "multi_agent"}},
                    }
                )
            if "from run_steps" in normalized and "payload_json->>'dispatch_id'" in normalized:
                return Cursor(
                    row={
                        "id": "step-code",
                        "run_id": "run-parent",
                        "step_key": "code",
                        "step_kind": "agent",
                        "status": "running",
                        "title": "Code",
                        "role": "coder",
                        "sequence": 2,
                        "payload_json": {
                            "dispatch_state": "claimed",
                            "dispatch_id": "dispatch-code",
                            "dispatch_lease_expires_at": "2026-99-99Tbad",
                        },
                    }
                )
            raise AssertionError(f"unexpected sql after malformed lease: {normalized}")

    with pytest.raises(repositories.RepositoryConflictError, match="dispatch_claim_lease_invalid"):
        await repositories.create_multi_agent_dispatch_child_run(
            FakeConnection(),
            tenant_id="default",
            parent_run_id="run-parent",
            dispatch_id="dispatch-code",
            handed_off_by="admin-a",
            active_run_admission_limit=3,
        )

    assert not any(sql.startswith("insert into runs") for sql in calls)


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_success_updates_parent_step_event_and_audit(monkeypatch):
    from app import repositories
    import json

    calls = []

    child_run = {
        "id": "run-child",
        "tenant_id": "default",
        "copied_from_run_id": "run-parent",
        "trace_id": "trace-child",
        "status": "succeeded",
        "input_json": {
            "input": {
                "multi_agent_dispatch": {
                    "parent_run_id": "run-parent",
                    "parent_step_id": "step-code",
                    "step_key": "code",
                    "dispatch_id": "dispatch-code",
                }
            }
        },
    }
    parent_step = {
        "id": "step-code",
        "run_id": "run-parent",
        "step_key": "code",
        "step_kind": "agent",
        "status": "running",
        "title": "Code",
        "role": "coder",
        "sequence": 2,
        "payload_json": {
            "dispatch_id": "dispatch-code",
            "dispatch_state": "handed_off",
            "dispatch_child_run_id": "run-child",
        },
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(row=child_run)
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(row=parent_step)
            if normalized.startswith("update run_steps"):
                return Cursor(row={"id": "step-code"})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-reconcile"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-reconcile"

    async def fake_finalize_parent(conn, **kwargs):
        return None

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.finalize_multi_agent_parent_run_if_ready", fake_finalize_parent)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={
            "message": "child output",
            "executor_payload": {"private_payload": "must not copy"},
            "artifacts": [{"storage_key": "private/object"}],
        },
    )

    assert result == {
        "parent_run_id": "run-parent",
        "parent_step_id": "step-code",
        "child_run_id": "run-child",
        "step_key": "code",
        "status": "succeeded",
        "dispatch_state": "completed",
        "event_id": "evt-reconcile",
        "audit_id": "aud-reconcile",
    }
    update_sql, update_params = next(
        (sql, params) for kind, sql, params in calls if kind == "sql" and sql.startswith("update run_steps")
    )
    assert "dispatch_child_run_id" in update_sql
    assert "dispatch_state' = 'handed_off'" in update_sql
    update_payload = json.loads(update_params[0])
    assert update_payload["dispatch_state"] == "completed"
    assert update_payload["dispatch_child_status"] == "succeeded"
    assert update_payload["output"] == "child output"
    assert update_payload["checkpoint_id"] == "checkpoint_step-code"
    assert update_payload["source_step_id"] == "step-code"
    assert "default" in update_params
    assert "run-parent" in update_params
    assert "step-code" in update_params
    assert "dispatch-code" in update_params
    assert "run-child" in update_params
    event = next(item[1] for item in calls if item[0] == "event")
    assert event["run_id"] == "run-parent"
    assert event["event_type"] == "multi_agent_dispatch_reconciled"
    assert event["visible_to_user"] is False
    audit = next(item[1] for item in calls if item[0] == "audit")
    assert audit["action"] == "run.multi_agent.dispatch.reconcile"
    assert audit["target_id"] == "step-code"


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_failure_does_not_copy_private_payload(monkeypatch):
    from app import repositories
    import json

    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(
                    row={
                        "id": "run-child",
                        "tenant_id": "default",
                        "copied_from_run_id": "run-parent",
                        "trace_id": "trace-child",
                        "status": "failed",
                        "input_json": {
                            "input": {
                                "multi_agent_dispatch": {
                                    "parent_run_id": "run-parent",
                                    "parent_step_id": "step-code",
                                    "step_key": "code",
                                    "dispatch_id": "dispatch-code",
                                }
                            }
                        },
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(
                    row={
                        "id": "step-code",
                        "run_id": "run-parent",
                        "step_key": "code",
                        "step_kind": "agent",
                        "status": "running",
                        "title": "Code",
                        "role": "coder",
                        "sequence": 2,
                        "payload_json": {
                            "dispatch_id": "dispatch-code",
                            "dispatch_state": "handed_off",
                            "dispatch_child_run_id": "run-child",
                        },
                    }
                )
            if normalized.startswith("update run_steps"):
                return Cursor(row={"id": "step-code"})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-reconcile"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-reconcile"

    async def fake_finalize_parent(conn, **kwargs):
        return None

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.finalize_multi_agent_parent_run_if_ready", fake_finalize_parent)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="failed",
        result_json={
            "message": "failed at /var/lib/private",
            "executor_payload": {"worker_path": "/app/private.py"},
            "private_payload": {"secret": "abc"},
            "storage_key": "tenant/default/private",
        },
        error_code="executor_failed",
        error_message="failed at /var/lib/private",
    )

    assert result["status"] == "failed"
    assert result["dispatch_state"] == "failed"
    update_sql, update_params = next(
        (sql, params) for kind, sql, params in calls if kind == "sql" and sql.startswith("update run_steps")
    )
    assert "status = %s" in update_sql
    update_payload = json.loads(update_params[0])
    payload_dump = json.dumps(update_payload, ensure_ascii=False).lower()
    assert update_payload["dispatch_state"] == "failed"
    assert update_payload["dispatch_child_status"] == "failed"
    assert update_payload["error_code"] == "executor_failed"
    assert update_payload["error"] == "child_run_failed"
    assert "executor_payload" not in payload_dump
    assert "private_payload" not in payload_dump
    assert "worker_path" not in payload_dump
    assert "storage_key" not in payload_dump
    assert "/var/" not in payload_dump
    assert "/app/" not in payload_dump


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_ignores_forged_unmatched_relationship(monkeypatch):
    from app import repositories

    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(
                    row={
                        "id": "run-child",
                        "tenant_id": "default",
                        "copied_from_run_id": "run-parent",
                        "trace_id": "trace-child",
                        "status": "succeeded",
                        "input_json": {
                            "input": {
                                "multi_agent_dispatch": {
                                    "parent_run_id": "run-other",
                                    "parent_step_id": "step-code",
                                    "step_key": "code",
                                    "dispatch_id": "dispatch-code",
                                }
                            }
                        },
                    }
                )
            raise AssertionError(f"forged relationship must not continue to writes: {normalized}")

    async def fake_append_event(conn, **kwargs):
        raise AssertionError("forged relationship must not append events")

    async def fake_append_audit_log(conn, **kwargs):
        raise AssertionError("forged relationship must not append audit")

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={"message": "forged"},
    )

    assert result is None
    assert not any(item[1].startswith("update run_steps") for item in calls if item[0] == "sql")


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_requires_persisted_terminal_child_status(monkeypatch):
    from app import repositories

    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(
                    row={
                        "id": "run-child",
                        "tenant_id": "default",
                        "copied_from_run_id": "run-parent",
                        "trace_id": "trace-child",
                        "status": "running",
                        "input_json": {
                            "input": {
                                "multi_agent_dispatch": {
                                    "parent_run_id": "run-parent",
                                    "parent_step_id": "step-code",
                                    "step_key": "code",
                                    "dispatch_id": "dispatch-code",
                                }
                            }
                        },
                    }
                )
            raise AssertionError(f"non-terminal child must not load parent step: {normalized}")

    async def fake_append_event(conn, **kwargs):
        raise AssertionError("non-terminal child must not append events")

    async def fake_append_audit_log(conn, **kwargs):
        raise AssertionError("non-terminal child must not append audit")

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={"message": "not terminal yet"},
    )

    assert result is None
    assert not any(item[1].startswith("update run_steps") for item in calls if item[0] == "sql")


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_sanitizes_unsafe_error_code(monkeypatch):
    from app import repositories
    import json

    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(
                    row={
                        "id": "run-child",
                        "tenant_id": "default",
                        "copied_from_run_id": "run-parent",
                        "trace_id": "trace-child",
                        "status": "failed",
                        "input_json": {
                            "input": {
                                "multi_agent_dispatch": {
                                    "parent_run_id": "run-parent",
                                    "parent_step_id": "step-code",
                                    "step_key": "code",
                                    "dispatch_id": "dispatch-code",
                                }
                            }
                        },
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(
                    row={
                        "id": "step-code",
                        "run_id": "run-parent",
                        "step_key": "code",
                        "step_kind": "agent",
                        "status": "running",
                        "title": "Code",
                        "role": "coder",
                        "sequence": 2,
                        "payload_json": {
                            "dispatch_id": "dispatch-code",
                            "dispatch_state": "handed_off",
                            "dispatch_child_run_id": "run-child",
                        },
                    }
                )
            if normalized.startswith("update run_steps"):
                return Cursor(row={"id": "step-code"})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-reconcile"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-reconcile"

    async def fake_finalize_parent(conn, **kwargs):
        return None

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.finalize_multi_agent_parent_run_if_ready", fake_finalize_parent)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="failed",
        result_json={"message": "safe failure"},
        error_code="/app/private.py?api_key=secret",
        error_message="safe failure",
    )

    assert result["status"] == "failed"
    update_params = next(params for kind, sql, params in calls if kind == "sql" and sql.startswith("update run_steps"))
    update_payload = json.loads(update_params[0])
    assert update_payload["error_code"] == "child_run_failed"
    payload_dump = json.dumps(update_payload, ensure_ascii=False).lower()
    assert "/app/" not in payload_dump
    assert "api_key" not in payload_dump
    assert "secret" not in payload_dump


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_skips_event_and_audit_when_update_is_stale(monkeypatch):
    from app import repositories

    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(
                    row={
                        "id": "run-child",
                        "tenant_id": "default",
                        "copied_from_run_id": "run-parent",
                        "trace_id": "trace-child",
                        "status": "succeeded",
                        "input_json": {
                            "input": {
                                "multi_agent_dispatch": {
                                    "parent_run_id": "run-parent",
                                    "parent_step_id": "step-code",
                                    "step_key": "code",
                                    "dispatch_id": "dispatch-code",
                                }
                            }
                        },
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(
                    row={
                        "id": "step-code",
                        "run_id": "run-parent",
                        "step_key": "code",
                        "step_kind": "agent",
                        "status": "running",
                        "title": "Code",
                        "role": "coder",
                        "sequence": 2,
                        "payload_json": {
                            "dispatch_id": "dispatch-code",
                            "dispatch_state": "handed_off",
                            "dispatch_child_run_id": "run-child",
                        },
                    }
                )
            if normalized.startswith("update run_steps"):
                return Cursor(row=None)
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        raise AssertionError("stale update must not append events")

    async def fake_append_audit_log(conn, **kwargs):
        raise AssertionError("stale update must not append audit")

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={"message": "child output"},
    )

    assert result is None


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_success_writes_public_result_event_and_audit(monkeypatch):
    from app import repositories
    import json

    calls = []
    parent_run = {
        "id": "run-parent",
        "tenant_id": "default",
        "copied_from_run_id": None,
        "trace_id": "trace-parent",
        "status": "running",
        "cancel_requested_at": None,
        "input_json": {
            "input": {
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "role": "planner", "depends_on": []},
                    {"step_key": "code", "role": "coder", "depends_on": ["plan"]},
                ],
            }
        },
    }
    parent_steps = [
        {
            "id": "step-plan",
            "run_id": "run-parent",
            "step_key": "plan",
            "step_kind": "agent",
            "status": "succeeded",
            "title": "Plan",
            "role": "planner",
            "sequence": 1,
            "payload_json": {
                "depends_on": [],
                "dispatch_state": "completed",
                "dispatch_child_run_id": "run-child-plan",
                "output": "safe plan",
                "checkpoint_id": "checkpoint_step-plan",
                "source_step_id": "step-plan",
                "executor_payload": {"private_payload": "hidden"},
                "storage_key": "tenant/default/private/object",
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
        {
            "id": "step-code",
            "run_id": "run-parent",
            "step_key": "code",
            "step_kind": "agent",
            "status": "succeeded",
            "title": "Code",
            "role": "coder",
            "sequence": 2,
            "payload_json": {
                "depends_on": ["plan"],
                "dispatch_state": "completed",
                "dispatch_child_run_id": "run-child-code",
                "output": "a" * 64,
                "checkpoint_id": "checkpoint_step-code",
                "source_step_id": "step-code",
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
    ]

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
                return Cursor(row=parent_run)
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(rows=parent_steps)
            if normalized.startswith("select child.id"):
                return Cursor(rows=[])
            if normalized.startswith("update runs"):
                return Cursor(row={"id": "run-parent", "status": "succeeded"})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-parent-finalized"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-parent-finalized"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        triggered_by_child_run_id="run-child-code",
    )

    assert result == {
        "parent_run_id": "run-parent",
        "status": "succeeded",
        "event_id": "evt-parent-finalized",
        "audit_id": "aud-parent-finalized",
        "counts": {"total": 2, "succeeded": 2, "failed": 0, "cancelled": 0},
    }
    update_params = next(params for kind, sql, params in calls if kind == "sql" and sql.startswith("update runs"))
    result_payload = json.loads(update_params[1])
    assert result_payload["message"] == "Multi-agent run succeeded"
    assert result_payload["multi_agent"]["status"] == "succeeded"
    assert result_payload["multi_agent"]["triggered_by_child_run_id"] == "run-child-code"
    assert result_payload["multi_agent"]["steps"][0]["output"] == "safe plan"
    dumped = json.dumps(result_payload, ensure_ascii=False)
    assert "private_payload" not in dumped
    assert "storage_key" not in dumped
    assert "a" * 64 not in dumped
    event = next(item[1] for item in calls if item[0] == "event")
    assert event["event_type"] == "multi_agent_parent_finalized"
    assert event["visible_to_user"] is False
    audit = next(item[1] for item in calls if item[0] == "audit")
    assert audit["action"] == "run.multi_agent.parent.finalize"
    assert audit["target_id"] == "run-parent"


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_failure_and_cancel_statuses(monkeypatch):
    from app import repositories

    calls = []
    statuses_seen = []

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, parent_statuses):
            self.parent_statuses = parent_statuses

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "copied_from_run_id": None,
                        "trace_id": "trace-parent",
                        "status": "running",
                        "cancel_requested_at": self.parent_statuses.get("cancel_requested_at"),
                        "input_json": {"input": {"execution_mode": "multi_agent", "multi_agent_steps": [{"step_key": "step-a"}]}},
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(rows=self.parent_statuses["steps"])
            if normalized.startswith("select child.id"):
                return Cursor(rows=[])
            if normalized.startswith("update runs"):
                statuses_seen.append(params[0])
                return Cursor(row={"id": "run-parent", "status": params[0]})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['payload']['status']}"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return f"aud-{kwargs['payload_json']['status']}"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    failed = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(
            {
                "cancel_requested_at": None,
                "steps": [
                    {
                        "id": "step-a",
                        "run_id": "run-parent",
                        "step_key": "step-a",
                        "step_kind": "agent",
                        "status": "failed",
                        "title": "Step A",
                        "role": "coder",
                        "sequence": 1,
                        "payload_json": {"error_code": "child_run_failed", "error": "safe failure"},
                        "started_at": None,
                        "finished_at": None,
                        "created_at": None,
                        "updated_at": None,
                    }
                ],
            }
        ),
        tenant_id="default",
        parent_run_id="run-parent",
    )
    cancelled = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(
            {
                "cancel_requested_at": "2026-06-06T00:00:00+00:00",
                "steps": [
                    {
                        "id": "step-a",
                        "run_id": "run-parent",
                        "step_key": "step-a",
                        "step_kind": "agent",
                        "status": "succeeded",
                        "title": "Step A",
                        "role": "coder",
                        "sequence": 1,
                        "payload_json": {"output": "done"},
                        "started_at": None,
                        "finished_at": None,
                        "created_at": None,
                        "updated_at": None,
                    }
                ],
            }
        ),
        tenant_id="default",
        parent_run_id="run-parent",
    )

    assert failed["status"] == "failed"
    assert cancelled["status"] == "cancelled"
    assert statuses_seen == ["failed", "cancelled"]


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_blocks_active_children_and_non_multi_agent(monkeypatch):
    from app import repositories

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, *, execution_mode="multi_agent", active_children=None):
            self.execution_mode = execution_mode
            self.active_children = active_children or []
            self.active_child_sql = None

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "copied_from_run_id": None,
                        "trace_id": "trace-parent",
                        "status": "running",
                        "cancel_requested_at": None,
                        "input_json": {"input": {"execution_mode": self.execution_mode, "multi_agent_steps": [{"step_key": "step-a"}]}},
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(
                    rows=[
                        {
                            "id": "step-a",
                            "run_id": "run-parent",
                            "step_key": "step-a",
                            "step_kind": "agent",
                            "status": "succeeded",
                            "title": "Step A",
                            "role": "coder",
                            "sequence": 1,
                            "payload_json": {"output": "done"},
                            "started_at": None,
                            "finished_at": None,
                            "created_at": None,
                            "updated_at": None,
                        }
                    ]
                )
            if normalized.startswith("select child.id"):
                self.active_child_sql = normalized
                return Cursor(rows=self.active_children)
            if normalized.startswith("update runs"):
                raise AssertionError("blocked parent must not be finalized")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("blocked parent must not emit event")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    active_child_conn = FakeConnection(active_children=[{"id": "run-child", "status": "queued"}])
    active_child_result = await repositories.finalize_multi_agent_parent_run_if_ready(
        active_child_conn,
        tenant_id="default",
        parent_run_id="run-parent",
    )
    non_multi_agent_result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(execution_mode="single_agent"),
        tenant_id="default",
        parent_run_id="run-parent",
    )

    assert active_child_result is None
    assert active_child_conn.active_child_sql is not None
    assert "join run_steps" not in active_child_conn.active_child_sql
    assert non_multi_agent_result is None


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_blocks_open_dispatch_state(monkeypatch):
    from app import repositories

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "copied_from_run_id": None,
                        "trace_id": "trace-parent",
                        "status": "running",
                        "cancel_requested_at": None,
                        "input_json": {"input": {"execution_mode": "multi_agent", "multi_agent_steps": [{"step_key": "step-a"}]}},
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(
                    rows=[
                        {
                            "id": "step-a",
                            "run_id": "run-parent",
                            "step_key": "step-a",
                            "step_kind": "agent",
                            "status": "succeeded",
                            "title": "Step A",
                            "role": "coder",
                            "sequence": 1,
                            "payload_json": {
                                "dispatch_state": "handed_off",
                                "dispatch_child_run_id": "run-child",
                                "output": "done",
                            },
                            "started_at": None,
                            "finished_at": None,
                            "created_at": None,
                            "updated_at": None,
                        }
                    ]
                )
            if normalized.startswith("select child.id"):
                return Cursor(rows=[])
            if normalized.startswith("update runs"):
                raise AssertionError("open dispatch state must block parent finalization")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("open dispatch state must not emit event")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
    )

    assert result is None


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_blocks_missing_configured_step(monkeypatch):
    from app import repositories

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "copied_from_run_id": None,
                        "trace_id": "trace-parent",
                        "status": "running",
                        "cancel_requested_at": None,
                        "input_json": {
                            "input": {
                                "execution_mode": "multi_agent",
                                "multi_agent_steps": [
                                    {"step_key": "plan"},
                                    {"step_key": "code"},
                                ],
                            }
                        },
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(
                    rows=[
                        {
                            "id": "step-plan",
                            "run_id": "run-parent",
                            "step_key": "plan",
                            "step_kind": "agent",
                            "status": "succeeded",
                            "title": "Plan",
                            "role": "planner",
                            "sequence": 1,
                            "payload_json": {"output": "plan done"},
                            "started_at": None,
                            "finished_at": None,
                            "created_at": None,
                            "updated_at": None,
                        },
                        {
                            "id": "step-extra",
                            "run_id": "run-parent",
                            "step_key": "extra",
                            "step_kind": "agent",
                            "status": "succeeded",
                            "title": "Extra",
                            "role": "coder",
                            "sequence": 2,
                            "payload_json": {"output": "extra done"},
                            "started_at": None,
                            "finished_at": None,
                            "created_at": None,
                            "updated_at": None,
                        },
                    ]
                )
            if normalized.startswith("select child.id"):
                return Cursor(rows=[])
            if normalized.startswith("update runs"):
                raise AssertionError("missing configured step must block parent finalization")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("missing configured step must not emit event")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
    )

    assert result is None


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_blocks_duplicate_or_malformed_configured_steps(monkeypatch):
    from app import repositories

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, configured_steps):
            self.configured_steps = configured_steps

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "copied_from_run_id": None,
                        "trace_id": "trace-parent",
                        "status": "running",
                        "cancel_requested_at": None,
                        "input_json": {
                            "input": {
                                "execution_mode": "multi_agent",
                                "multi_agent_steps": self.configured_steps,
                            }
                        },
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                raise AssertionError("malformed configured steps must block before loading persisted steps")
            if normalized.startswith("update runs"):
                raise AssertionError("malformed configured steps must not finalize parent")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("malformed configured steps must not emit event")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    for configured_steps in (
        [{"step_key": "plan"}, {"step_key": "plan"}],
        [{"role": "planner"}],
        ["not-a-step"],
        "step-a",
        {"step_key": "step-a"},
    ):
        result = await repositories.finalize_multi_agent_parent_run_if_ready(
            FakeConnection(configured_steps),
            tenant_id="default",
            parent_run_id="run-parent",
        )
        assert result is None


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_blocks_ordinary_copied_run_and_uses_skip_locked(monkeypatch):
    from app import repositories

    parent_select_sql = ""

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            nonlocal parent_select_sql
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id") and "cancel_requested_at" in normalized:
                parent_select_sql = normalized.lower()
                return Cursor(
                    row={
                        "id": "run-copied",
                        "tenant_id": "default",
                        "copied_from_run_id": "run-source",
                        "trace_id": "trace-copied",
                        "status": "running",
                        "cancel_requested_at": None,
                        "input_json": {
                            "input": {
                                "execution_mode": "multi_agent",
                                "multi_agent_steps": [{"step_key": "step-a"}],
                            }
                        },
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                raise AssertionError("ordinary copied run must be rejected before loading steps")
            if normalized.startswith("update runs"):
                raise AssertionError("ordinary copied run must not be finalized")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("ordinary copied run must not emit event")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-copied",
    )

    assert result is None
    assert "for update skip locked" in parent_select_sql


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_success_invokes_parent_rollup(monkeypatch):
    from app import repositories

    calls = []
    child_run = {
        "id": "run-child",
        "tenant_id": "default",
        "copied_from_run_id": "run-parent",
        "trace_id": "trace-child",
        "status": "succeeded",
        "input_json": {
            "input": {
                "multi_agent_dispatch": {
                    "parent_run_id": "run-parent",
                    "parent_step_id": "step-code",
                    "step_key": "code",
                    "dispatch_id": "dispatch-code",
                }
            }
        },
    }
    parent_step = {
        "id": "step-code",
        "run_id": "run-parent",
        "step_key": "code",
        "step_kind": "agent",
        "status": "running",
        "title": "Code",
        "role": "coder",
        "sequence": 2,
        "payload_json": {
            "dispatch_id": "dispatch-code",
            "dispatch_state": "handed_off",
            "dispatch_child_run_id": "run-child",
        },
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(row=child_run)
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(row=parent_step)
            if normalized.startswith("update run_steps"):
                return Cursor(row={"id": "step-code"})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        return "evt-reconcile"

    async def fake_append_audit_log(conn, **kwargs):
        return "aud-reconcile"

    async def fake_finalize(conn, **kwargs):
        calls.append(kwargs)
        return {"parent_run_id": "run-parent", "status": "succeeded"}

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.finalize_multi_agent_parent_run_if_ready", fake_finalize, raising=False)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={"message": "child output"},
    )

    assert result["status"] == "succeeded"
    assert calls == [
        {
            "tenant_id": "default",
            "parent_run_id": "run-parent",
            "triggered_by_child_run_id": "run-child",
        }
    ]


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_stale_update_does_not_invoke_parent_rollup(monkeypatch):
    from app import repositories

    child_run = {
        "id": "run-child",
        "tenant_id": "default",
        "copied_from_run_id": "run-parent",
        "trace_id": "trace-child",
        "status": "succeeded",
        "input_json": {
            "input": {
                "multi_agent_dispatch": {
                    "parent_run_id": "run-parent",
                    "parent_step_id": "step-code",
                    "step_key": "code",
                    "dispatch_id": "dispatch-code",
                }
            }
        },
    }
    parent_step = {
        "id": "step-code",
        "run_id": "run-parent",
        "step_key": "code",
        "step_kind": "agent",
        "status": "running",
        "title": "Code",
        "role": "coder",
        "sequence": 2,
        "payload_json": {
            "dispatch_id": "dispatch-code",
            "dispatch_state": "handed_off",
            "dispatch_child_run_id": "run-child",
        },
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(row=child_run)
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(row=parent_step)
            if normalized.startswith("update run_steps"):
                return Cursor(row=None)
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_finalize(conn, **kwargs):
        raise AssertionError("stale update must not finalize parent")

    monkeypatch.setattr("app.repositories.finalize_multi_agent_parent_run_if_ready", fail_finalize, raising=False)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={"message": "child output"},
    )

    assert result is None


@pytest.mark.asyncio
async def test_propagate_multi_agent_parent_cancel_cancels_server_owned_children(monkeypatch):
    from app import repositories
    import json

    calls = []
    child_rows = [
        {
            "id": "run-child-queued",
            "status": "queued",
            "trace_id": "trace-child-queued",
            "cancel_requested_at": None,
            "input_json": {
                "input": {
                    "command": "cat /app/private.py",
                    "worker_path": "/var/lib/ai-platform/private-worker.py",
                    "api_key": "sk-test-secret",
                    "storage_key": "tenant/default/private/object",
                    "multi_agent_dispatch": {
                        "parent_run_id": "run-parent",
                        "parent_step_id": "step-code",
                        "step_key": "code",
                        "dispatch_id": "dispatch-code",
                    }
                }
            },
            "parent_step_id": "step-code",
            "step_key": "code",
            "parent_step_payload_json": {
                "dispatch_state": "handed_off",
                "dispatch_child_run_id": "run-child-queued",
                "dispatch_id": "dispatch-code",
            },
        },
        {
            "id": "run-child-running",
            "status": "running",
            "trace_id": "trace-child-running",
            "cancel_requested_at": None,
            "input_json": {
                "input": {
                    "command": "cat /app/private.py",
                    "worker_path": "/var/lib/ai-platform/private-worker.py",
                    "api_key": "sk-test-secret",
                    "storage_key": "tenant/default/private/object",
                    "multi_agent_dispatch": {
                        "parent_run_id": "run-parent",
                        "parent_step_id": "step-review",
                        "step_key": "review",
                        "dispatch_id": "dispatch-review",
                    }
                }
            },
            "parent_step_id": "step-review",
            "step_key": "review",
            "parent_step_payload_json": {
                "dispatch_state": "handed_off",
                "dispatch_child_run_id": "run-child-running",
                "dispatch_id": "dispatch-review",
            },
        },
    ]

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select child.id"):
                assert "child.copied_from_run_id = %s" in normalized
                assert "payload_json->>'dispatch_child_run_id'" in normalized
                assert "payload_json->>'dispatch_state'" in normalized
                return Cursor(rows=child_rows)
            if normalized.startswith("update runs"):
                child_id = params[2]
                status = "cancelled" if child_id == "run-child-queued" else "running"
                return Cursor(row={"id": child_id, "status": status, "trace_id": f"trace-{child_id}"})
            if normalized.startswith("update run_steps"):
                return Cursor()
            if normalized.startswith("select * from sandbox_leases"):
                return Cursor(
                    rows=[
                        {
                            "id": "lease-running",
                            "run_id": "run-child-running",
                            "trace_id": "trace-lease",
                        }
                    ]
                    if params[1] == "run-child-running"
                    else []
                )
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['event_type']}-{kwargs['run_id']}"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return f"aud-{kwargs['target_id']}"

    async def fake_reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"event_id": "evt-reconcile", "audit_id": "aud-reconcile"}

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.reconcile_multi_agent_child_run_terminal_state", fake_reconcile)

    result = await repositories.propagate_multi_agent_parent_cancel(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        requested_by="user-a",
    )

    assert result["child_run_ids"] == ["run-child-queued", "run-child-running"]
    assert result["queued_child_run_ids"] == ["run-child-queued"]
    assert result["running_child_run_ids"] == ["run-child-running"]
    assert result["active_sandbox_leases"] == [
        {"id": "lease-running", "run_id": "run-child-running", "trace_id": "trace-lease"}
    ]
    assert any(call[0] == "reconcile" and call[1]["child_run_id"] == "run-child-queued" for call in calls)
    dump = json.dumps([call[1] for call in calls if call[0] in {"event", "audit"}], ensure_ascii=False)
    assert "private_payload" not in dump
    assert "storage_key" not in dump
    assert "cat /app/private.py" not in dump
    assert "/var/lib/ai-platform" not in dump
    assert "sk-test-secret" not in dump


@pytest.mark.asyncio
async def test_propagate_multi_agent_parent_cancel_ignores_non_server_owned_copies(monkeypatch):
    from app import repositories

    class Cursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select child.id"):
                assert "child.copied_from_run_id = %s" in normalized
                assert "payload_json->>'dispatch_child_run_id'" in normalized
                return Cursor()
            raise AssertionError(f"unexpected write for ordinary copied run: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("ordinary copied runs must not emit cancel propagation events")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    result = await repositories.propagate_multi_agent_parent_cancel(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        requested_by="user-a",
    )

    assert result == {
        "child_run_ids": [],
        "queued_child_run_ids": [],
        "running_child_run_ids": [],
        "active_sandbox_leases": [],
        "event_ids": [],
        "audit_ids": [],
    }


@pytest.mark.asyncio
async def test_claim_multi_agent_dispatch_step_writes_step_event_and_audit(monkeypatch):
    from app import repositories

    calls = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            calls.append(("sql", " ".join(sql.split()), params))
            normalized = " ".join(sql.split())
            if normalized.startswith("insert into run_steps"):
                return Cursor(row={"id": "step-code"})
            if "payload_json = payload_json - 'dispatch_expired_at'" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-code"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-code"

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        calls.append(("list_steps", tenant_id, run_id))
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "running",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["plan"],
                    "dispatch_state": "claimed",
                    "dispatch_kind": "subagent",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.list_run_steps", fake_list_run_steps)

    result = await repositories.claim_multi_agent_dispatch_step(
        FakeConnection(),
        tenant_id="default",
        run_id="run-ready",
        claimed_by="admin-a",
        trace_id="trace-ready",
        step_key="code",
        step_kind="agent",
        title="Code",
        role="coder",
        sequence=2,
        depends_on=["plan"],
        lease_ttl_seconds=900,
    )

    assert result["event_id"] == "evt-code"
    assert result["audit_id"] == "aud-code"
    assert result["step"]["payload_json"]["dispatch_state"] == "claimed"
    insert_call = next(call for call in calls if call[0] == "sql" and call[1].startswith("insert into run_steps"))
    insert_payload = json.loads(insert_call[2][8])
    assert insert_payload["depends_on"] == ["plan"]
    assert insert_payload["dispatch_state"] == "claimed"
    assert insert_payload["dispatch_kind"] == "subagent"
    assert insert_payload["dispatch_id"].startswith("dispatch")
    assert insert_payload["dispatch_claimed_by"] == "admin-a"
    assert insert_payload["dispatch_claimed_at"]
    assert insert_payload["dispatch_lease_expires_at"]
    assert "where run_steps.status = 'pending'" in insert_call[1]
    assert (
        "coalesce(run_steps.payload_json->>'dispatch_state', '') not in ('claimed', 'handed_off')"
        in insert_call[1]
    )
    assert "returning id" in insert_call[1]
    clear_call = next(call for call in calls if call[0] == "sql" and "dispatch_expired_at" in call[1])
    assert "payload_json = payload_json - 'dispatch_expired_at'" in clear_call[1]
    assert clear_call[2] == ("default", "step-code")
    event_call = next(call[1] for call in calls if call[0] == "event")
    assert event_call["event_type"] == "agent_step_started"
    assert event_call["visible_to_user"] is False
    assert event_call["payload"]["dispatch_state"] == "claimed"
    audit_call = next(call[1] for call in calls if call[0] == "audit")
    assert audit_call["action"] == "run.multi_agent.dispatch.claim"
    assert audit_call["target_id"] == "step-code"
    assert audit_call["payload_json"]["result_status"] == "claimed"


@pytest.mark.asyncio
async def test_claim_multi_agent_dispatch_step_rejects_stale_non_pending_race(monkeypatch):
    from app import repositories

    calls = []

    class Cursor:
        async def fetchone(self):
            return None

    class FakeConnection:
        async def execute(self, sql, params):
            calls.append(("sql", " ".join(sql.split()), params))
            if "insert into run_steps" in sql:
                return Cursor()
            raise AssertionError(f"stale claim must stop after conditional claim sql: {' '.join(sql.split())}")

    async def fail_append_event(*args, **kwargs):
        raise AssertionError("stale claim must not emit an event")

    async def fail_append_audit(*args, **kwargs):
        raise AssertionError("stale claim must not emit audit")

    async def fail_list_steps(*args, **kwargs):
        raise AssertionError("stale claim must not list steps after failed conditional claim")

    monkeypatch.setattr("app.repositories.append_event", fail_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fail_append_audit)
    monkeypatch.setattr("app.repositories.list_run_steps", fail_list_steps)

    with pytest.raises(repositories.RepositoryConflictError, match="dispatch_step_not_pending"):
        await repositories.claim_multi_agent_dispatch_step(
            FakeConnection(),
            tenant_id="default",
            run_id="run-ready",
            claimed_by="admin-a",
            trace_id="trace-ready",
            step_key="code",
            step_kind="agent",
            title="Code",
            role="coder",
            sequence=2,
            depends_on=["plan"],
            lease_ttl_seconds=900,
        )

    assert calls and calls[0][1].startswith("insert into run_steps")
    assert "where run_steps.status = 'pending'" in calls[0][1]
    assert "returning id" in calls[0][1]


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


def test_cancel_run_finalizes_multi_agent_parent_after_cancel_propagation(monkeypatch):
    calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        calls.append(("propagate", tenant_id, parent_run_id, requested_by, requested_by_role))
        return {"queued_child_run_ids": [], "active_sandbox_leases": []}

    async def fake_finalize(conn, *, tenant_id, parent_run_id, triggered_by_child_run_id=None):
        calls.append(("finalize", tenant_id, parent_run_id, triggered_by_child_run_id))
        return {"parent_run_id": parent_run_id, "status": "cancelled"}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 0

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr("app.routes.runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.finalize_multi_agent_parent_run_if_ready", fake_finalize, raising=False)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", fake_remove_queued_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert calls == [
        ("propagate", "default", "run-parent", "user-a", None),
        ("finalize", "default", "run-parent", None),
        ("remove", "default", "run-parent"),
    ]


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


def test_cancel_run_removes_propagated_queued_child_payloads(monkeypatch):
    calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        assert (tenant_id, parent_run_id, requested_by, requested_by_role) == ("default", "run-parent", "user-a", None)
        return {"queued_child_run_ids": ["run-child-queued"], "active_sandbox_leases": []}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr("app.routes.runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", fake_remove_queued_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/cancel", headers=headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"
    assert calls == [("remove", "default", "run-child-queued")]


def test_cancel_run_removes_propagated_queued_child_payloads_before_sandbox_failure(monkeypatch):
    calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        return {
            "queued_child_run_ids": ["run-child-queued"],
            "active_sandbox_leases": [sandbox_lease_row(run_id="run-child-running", user_id="user-a")],
        }

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr("app.routes.runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", fake_remove_queued_run, raising=False)
    monkeypatch.setattr("app.routes.runs.create_container_provider", lambda provider_name=None: FailingSandboxProvider(), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/cancel", headers=headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert calls == [("remove", "default", "run-child-queued")]


def test_cancel_run_stops_child_sandbox_when_propagated_queue_cleanup_fails(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        return {
            "queued_child_run_ids": ["run-child-queued"],
            "active_sandbox_leases": [sandbox_lease_row(run_id="run-child-running", user_id="user-a")],
        }

    async def fail_remove_queued_run(*, tenant_id, run_id):
        raise RuntimeError(f"redis unavailable for {run_id}")

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr("app.routes.runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr("app.routes.runs.repositories.release_stopped_sandbox_leases_for_cancel", fake_release_stopped_sandbox_leases_for_cancel, raising=False)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", fail_remove_queued_run, raising=False)
    monkeypatch.setattr("app.routes.runs.create_container_provider", lambda provider_name=None: RecordingSandboxProvider(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/cancel", headers=headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "queue_cleanup_failed"
    assert calls[0][1].run_id == "run-child-running"
    assert release_calls == [
        {
            "tenant_id": "default",
            "run_id": "run-child-running",
            "reason": "cancel_requested",
            "lease_ids": ["lease-run-child-running"],
            "trace_id": None,
        }
    ]


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


def test_admin_cancel_run_finalizes_multi_agent_parent_after_cancel_propagation(monkeypatch):
    calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        calls.append(("propagate", tenant_id, parent_run_id, requested_by, requested_by_role))
        return {"queued_child_run_ids": [], "active_sandbox_leases": []}

    async def fake_finalize(conn, *, tenant_id, parent_run_id, triggered_by_child_run_id=None):
        calls.append(("finalize", tenant_id, parent_run_id, triggered_by_child_run_id))
        return {"parent_run_id": parent_run_id, "status": "cancelled"}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 0

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr("app.routes.admin_runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.repositories.finalize_multi_agent_parent_run_if_ready", fake_finalize, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.remove_queued_run", fake_remove_queued_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run-parent/cancel", headers=admin_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert calls == [
        ("propagate", "default", "run-parent", "admin-a", "admin"),
        ("finalize", "default", "run-parent", None),
        ("remove", "default", "run-parent"),
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


def test_admin_cancel_run_releases_propagated_child_sandbox_lease(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        assert (tenant_id, parent_run_id, requested_by, requested_by_role) == ("default", "run-parent", "admin-a", "admin")
        return {
            "queued_child_run_ids": [],
            "active_sandbox_leases": [sandbox_lease_row(run_id="run-child-running", user_id="target-user")],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr("app.routes.admin_runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runs.create_container_provider", lambda provider_name=None: RecordingSandboxProvider(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run-parent/cancel", headers=admin_headers())

    assert response.status_code == 200
    assert calls[0][1].run_id == "run-child-running"
    assert release_calls == [
        {
            "tenant_id": "default",
            "run_id": "run-child-running",
            "reason": "admin_cancel_requested",
            "lease_ids": ["lease-run-child-running"],
            "trace_id": None,
            "requested_by_role": "admin",
        }
    ]


def test_admin_cancel_run_removes_propagated_queued_child_payloads_before_sandbox_failure(monkeypatch):
    calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        assert requested_by_role == "admin"
        return {
            "queued_child_run_ids": ["run-child-queued"],
            "active_sandbox_leases": [sandbox_lease_row(run_id="run-child-running", user_id="target-user")],
        }

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr("app.routes.admin_runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.remove_queued_run", fake_remove_queued_run, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.create_container_provider", lambda provider_name=None: FailingSandboxProvider(), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run-parent/cancel", headers=admin_headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "sandbox_runtime_cleanup_failed"
    assert calls == [("remove", "default", "run-child-queued")]


def test_admin_cancel_run_stops_child_sandbox_when_propagated_queue_cleanup_fails(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested"}

    async def fake_propagate(conn, *, tenant_id, parent_run_id, requested_by, requested_by_role=None):
        assert requested_by_role == "admin"
        return {
            "queued_child_run_ids": ["run-child-queued"],
            "active_sandbox_leases": [sandbox_lease_row(run_id="run-child-running", user_id="target-user")],
        }

    async def fail_remove_queued_run(*, tenant_id, run_id):
        raise RuntimeError(f"redis unavailable for {run_id}")

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr("app.routes.admin_runs.repositories.propagate_multi_agent_parent_cancel", fake_propagate, raising=False)
    monkeypatch.setattr(
        "app.routes.admin_runs.repositories.release_stopped_sandbox_leases_for_cancel",
        fake_release_stopped_sandbox_leases_for_cancel,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_runs.remove_queued_run", fail_remove_queued_run, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.create_container_provider", lambda provider_name=None: RecordingSandboxProvider(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run-parent/cancel", headers=admin_headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "queue_cleanup_failed"
    assert calls[0][1].run_id == "run-child-running"
    assert release_calls == [
        {
            "tenant_id": "default",
            "run_id": "run-child-running",
            "reason": "admin_cancel_requested",
            "lease_ids": ["lease-run-child-running"],
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
