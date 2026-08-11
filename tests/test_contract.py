import asyncio

from app.main import create_app
from app.routes import health as health_routes
from app.executors.base import RunPayload
from app.models import CreateRunRequest, QueueRunPayload, SkillDefinition
from app.product_events import initial_run_event_specs
from app.control_plane_contracts import sanitize_public_payload
from app.repositories import new_id
from fastapi.testclient import TestClient


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


def release_decision(version: str) -> dict:
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": False,
        "selected_version": version,
        "selected_track": "manifest_pin",
    }


def primary_manifest(skill_id: str, version: str) -> dict:
    return {"skill_id": skill_id, "content_hash": version}


def test_generated_ids_are_prefixed_and_unique():
    first = new_id("run")
    second = new_id("run")

    assert first.startswith("run_")
    assert second.startswith("run_")
    assert first != second


def test_create_run_request_uses_file_ids_contract_only():
    fields = set(CreateRunRequest.model_fields)

    assert "file_ids" in fields
    assert "files" not in fields


def test_app_registers_platform_routes():
    app = create_app()
    paths = set(app.openapi()["paths"])

    assert "/api/ai/health" in paths
    assert "/api/ai/ready" in paths
    assert "/api/ai/admin/status" in paths
    assert "/api/ai/agent-apps" not in paths
    assert "/api/ai/agent-profiles" in paths
    assert "/api/ai/files" in paths
    assert "/api/ai/runs" in paths
    assert "/api/ai/runs/{run_id}" in paths
    assert "/api/ai/runs/{run_id}/events" in paths
    assert "/api/ai/artifacts/{artifact_id}/download" in paths
    assert "/api/ai/artifacts/{artifact_id}/preview" in paths


def test_app_allows_browser_cors_for_frontend_cutover():
    client = TestClient(create_app())

    response = client.get("/api/ai/health", headers={"Origin": "http://10.56.0.211:8080"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://10.56.0.211:8080"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_health_reports_dynamic_runtime_commit(monkeypatch):
    commit = "7" * 40
    monkeypatch.setenv("AI_PLATFORM_RUNTIME_COMMIT", commit)

    response = TestClient(create_app()).get("/api/ai/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "runtime_commit": commit}


def test_readiness_requires_postgresql_and_redis(monkeypatch):
    commit = "8" * 40

    async def available():
        return None

    monkeypatch.setenv("AI_PLATFORM_RUNTIME_COMMIT", commit)
    monkeypatch.setattr(health_routes, "_probe_postgresql", available)
    monkeypatch.setattr(health_routes, "_probe_schema", available)
    monkeypatch.setattr(health_routes, "_probe_redis", available)

    response = TestClient(create_app()).get("/api/ai/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "runtime_commit": commit,
        "dependencies": {
            "postgresql": "ok",
            "schema": "ok",
            "target_schema_version": health_routes.TARGET_SCHEMA_VERSION,
            "redis": "ok",
        },
    }


def test_readiness_fails_closed_without_exposing_dependency_errors(monkeypatch):
    async def postgresql_unavailable():
        raise RuntimeError("secret database detail")

    async def redis_available():
        return None

    monkeypatch.setattr(health_routes, "_probe_postgresql", postgresql_unavailable)
    monkeypatch.setattr(health_routes, "_probe_schema", postgresql_unavailable)
    monkeypatch.setattr(health_routes, "_probe_redis", redis_available)

    response = TestClient(create_app()).get("/api/ai/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["dependencies"] == {
        "postgresql": "unavailable",
        "schema": "unavailable",
        "target_schema_version": health_routes.TARGET_SCHEMA_VERSION,
        "redis": "ok",
    }
    assert "secret database detail" not in response.text


def test_readiness_times_out_each_dependency(monkeypatch):
    class Settings:
        datastore_readiness_timeout_seconds = 0.001

    async def unavailable_before_timeout():
        raise RuntimeError("down")

    async def hangs():
        await asyncio.sleep(1)

    monkeypatch.setattr(health_routes, "get_settings", lambda: Settings())
    monkeypatch.setattr(health_routes, "_probe_postgresql", unavailable_before_timeout)
    monkeypatch.setattr(health_routes, "_probe_schema", hangs)
    monkeypatch.setattr(health_routes, "_probe_redis", hangs)

    response = TestClient(create_app()).get("/api/ai/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"] == {
        "postgresql": "unavailable",
        "schema": "unavailable",
        "target_schema_version": health_routes.TARGET_SCHEMA_VERSION,
        "redis": "unavailable",
    }


def test_run_request_rejects_unsafe_ids():
    try:
        CreateRunRequest(agent_id="../bad", skill_id="qa-file-reviewer")
    except ValueError as exc:
        assert "unsupported characters" in str(exc)
    else:
        raise AssertionError("unsafe agent_id should fail validation")


def test_queue_payload_requires_release_decision_and_executor_type():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": ["file-a"],
                "input": {"mode": "file"},
                "executor_type": "claude-agent-worker",
            }
        )
    except ValueError as exc:
        assert "release_decision_required" in str(exc)
    else:
        raise AssertionError("queue payload should reject missing release decision")

    payload = QueueRunPayload.model_validate(
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "file_ids": ["file-a"],
            "input": {"mode": "file"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-primary",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-primary",
                "selected_track": "manifest_pin",
            },
            "skill_manifests": [{"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"}],
        }
    )
    assert payload.executor_type == "claude-agent-worker"
    assert payload.schema_version == "ai-platform.run-payload.v1"
    payload_with_context = QueueRunPayload.model_validate(
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "file_ids": ["file-a"],
            "input": {"mode": "file"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-primary",
            "release_decision": release_decision("hash-primary"),
            "skill_manifests": [primary_manifest("qa-file-reviewer", "hash-primary")],
            "context_snapshot_id": "ctx_primary",
            "context_snapshot": {
                "schema_version": "ai-platform.context-snapshot.v1",
                "source": "runs_api",
                "message_count": 0,
                "file_count": 1,
            },
        }
    )
    assert payload_with_context.context_snapshot_id == "ctx_primary"
    assert payload_with_context.context_snapshot["source"] == "runs_api"
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"mode": "file"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": release_decision("hash-primary"),
                "skill_manifests": [primary_manifest("qa-file-reviewer", "hash-primary")],
                "context_snapshot_id": "../ctx",
            }
        )
    except ValueError as exc:
        assert "unsupported characters" in str(exc)
    else:
        raise AssertionError("queue payload should reject unsafe context_snapshot_id")
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "executor_type": "claude-agent-worker",
                "files": [{"file_id": "file-a"}],
            }
        )
    except ValueError as exc:
        assert "Extra inputs are not permitted" in str(exc)
    else:
        raise AssertionError("queue payload should reject legacy files field")


def test_queue_payload_accepts_skillless_harness_chat_v2():
    payload = QueueRunPayload.model_validate(
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "general-agent",
            "execution_kind": "harness_chat",
            "skill_id": None,
            "file_ids": ["file-a"],
            "input": {"message": "summarize the attachment"},
            "executor_type": "claude-agent-worker",
            "schema_version": "ai-platform.run-payload.v2",
        }
    )

    assert payload.execution_kind == "harness_chat"
    assert payload.skill_id is None
    assert payload.skill_version is None
    assert payload.release_decision == {}
    assert payload.skill_manifests == []


def test_queue_payload_keeps_legacy_v1_general_chat_replay_shape():
    payload = QueueRunPayload.model_validate(
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-legacy",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "file_ids": [],
            "input": {"message": "historical replay"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-legacy",
            "release_decision": release_decision("hash-legacy"),
            "skill_manifests": [primary_manifest("general-chat", "hash-legacy")],
            "schema_version": "ai-platform.run-payload.v1",
        }
    )

    assert payload.execution_kind == "skill"
    assert payload.skill_id == "general-chat"
    assert payload.schema_version == "ai-platform.run-payload.v1"


def test_queue_payload_rejects_skill_authority_on_harness_chat():
    base = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
        "execution_kind": "harness_chat",
        "skill_id": None,
        "file_ids": [],
        "input": {"message": "hello"},
        "executor_type": "claude-agent-worker",
        "schema_version": "ai-platform.run-payload.v2",
    }
    invalid_overrides = (
        ({"skill_id": "general-chat"}, "harness_chat_skill_id_forbidden"),
        ({"skill_version": "hash-a"}, "harness_chat_skill_version_forbidden"),
        (
            {"release_decision": release_decision("hash-a")},
            "harness_chat_release_decision_forbidden",
        ),
        (
            {"skill_manifests": [{"skill_id": "general-chat"}]},
            "harness_chat_skill_manifests_forbidden",
        ),
        ({"executor_type": "embedded-poco"}, "harness_chat_executor_invalid"),
        (
            {"schema_version": "ai-platform.run-payload.v1"},
            "harness_chat_payload_schema_version_invalid",
        ),
    )

    for override, expected_error in invalid_overrides:
        try:
            QueueRunPayload.model_validate({**base, **override})
        except ValueError as exc:
            assert expected_error in str(exc)
        else:
            raise AssertionError(f"harness payload should reject {override}")


def test_run_payload_accepts_skillless_harness_chat_v2():
    payload = RunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        agent_id="general-agent",
        execution_kind="harness_chat",
        skill_id=None,
        file_ids=["file-a"],
        input={"message": "summarize"},
        schema_version="ai-platform.run-payload.v2",
    )

    assert payload.execution_kind == "harness_chat"
    assert payload.skill_id is None


def test_run_payload_rejects_skill_identity_on_harness_chat():
    try:
        RunPayload(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            attempt_id="attempt-a",
            agent_id="general-agent",
            execution_kind="harness_chat",
            skill_id="general-chat",
            file_ids=[],
            input={"message": "hello"},
            schema_version="ai-platform.run-payload.v2",
        )
    except ValueError as exc:
        assert "harness_chat_skill_id_forbidden" in str(exc)
    else:
        raise AssertionError("harness RunPayload must not carry a Skill identity")


def test_harness_initial_events_contain_no_skill_fact():
    events = initial_run_event_specs(
        agent_id="general-agent",
        execution_kind="harness_chat",
        skill_id=None,
        skill_version=None,
        executor_type="claude-agent-worker",
        file_ids=["file-a"],
        source="test",
    )

    assert [event["event_type"] for event in events] == ["queued", "file_bound"]
    assert all(event["payload"]["execution_kind"] == "harness_chat" for event in events)
    assert all("skill_id" not in event["payload"] for event in events)
    assert all("skill_version" not in event["payload"] for event in events)


def test_skill_initial_events_keep_exact_skill_fact():
    events = initial_run_event_specs(
        agent_id="qa-word-review",
        execution_kind="skill",
        skill_id="qa-file-reviewer",
        skill_version="hash-a",
        executor_type="claude-agent-worker",
        file_ids=[],
        source="test",
    )

    assert [event["event_type"] for event in events] == ["queued", "skill_selected"]
    assert all(event["payload"]["skill_id"] == "qa-file-reviewer" for event in events)
    assert all(event["payload"]["skill_version"] == "hash-a" for event in events)


def test_queue_run_payload_rejects_unsupported_schema_version():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"mode": "file"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": release_decision("hash-primary"),
                "skill_manifests": [primary_manifest("qa-file-reviewer", "hash-primary")],
                "schema_version": "legacy.v0",
            }
        )
    except ValueError as exc:
        assert "run_payload_schema_version_invalid" in str(exc)
    else:
        raise AssertionError("queue payload should reject unsupported schema version")


def test_queue_payload_accepts_email_style_principal_user_id():
    payload = QueueRunPayload.model_validate(
        {
            "tenant_id": "frc-test-a",
            "workspace_id": "frc_test_a_default",
            "user_id": "alice@example.test",
            "session_id": "ses_frc",
            "run_id": "run_frc",
            "agent_id": "frc_agent_83ebaed7aa4c5f49",
            "skill_id": "general-chat",
            "file_ids": [],
            "input": {"message": "hello"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-general-chat",
            "release_decision": release_decision("hash-general-chat"),
            "skill_manifests": [primary_manifest("general-chat", "hash-general-chat")],
        }
    )

    assert payload.user_id == "alice@example.test"


def test_queue_payload_rejects_path_like_principal_user_id():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "frc-test-a",
                "workspace_id": "frc_test_a_default",
                "user_id": "../alice@example.test",
                "session_id": "ses_frc",
                "run_id": "run_frc",
                "agent_id": "frc_agent_83ebaed7aa4c5f49",
                "skill_id": "general-chat",
                "file_ids": [],
                "input": {"message": "hello"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-general-chat",
                "release_decision": release_decision("hash-general-chat"),
                "skill_manifests": [primary_manifest("general-chat", "hash-general-chat")],
            }
        )
    except ValueError as exc:
        assert "user_id contains unsupported characters" in str(exc)
    else:
        raise AssertionError("path-like user_id should fail validation")


def test_run_payload_rejects_missing_release_decision():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
        )
    except ValueError as exc:
        assert "release_decision_required" in str(exc)
    else:
        raise AssertionError("run payload should reject missing release decision")


def test_run_payload_rejects_unsupported_schema_version():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            skill_version="hash-primary",
            release_decision=release_decision("hash-primary"),
            skill_manifests=[primary_manifest("qa-file-reviewer", "hash-primary")],
            schema_version="legacy.v0",
        )
    except ValueError as exc:
        assert "run_payload_schema_version_invalid" in str(exc)
    else:
        raise AssertionError("run payload should reject unsupported schema version")


def test_queue_run_payload_accepts_skill_manifest_pins():
    payload = QueueRunPayload.model_validate(
        {
            "tenant_id": "default",
            "workspace_id": "default",
            "user_id": "user-a",
            "session_id": "ses_1",
            "run_id": "run_1",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "file_ids": ["file_1"],
            "input": {"message": "审核文件"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-primary",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": True,
                "selected_version": "hash-primary",
                "selected_track": "current",
                "rollout_percent": 25,
                "bucket": 12,
            },
            "skill_manifests": [
                {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-primary",
                    "content_hash": "hash-primary",
                    "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                    "dependency_ids": ["minimax-docx"],
                    "allowed": True,
                },
                {
                    "skill_id": "minimax-docx",
                    "version": "hash-dependency",
                    "content_hash": "hash-dependency",
                    "source": {"kind": "builtin", "asset_dir": "minimax-docx"},
                    "dependency_ids": [],
                    "allowed": True,
                },
            ],
        }
    )

    assert payload.skill_version == "hash-primary"
    assert payload.release_decision["selected_track"] == "current"
    assert payload.skill_manifests[0]["skill_id"] == "qa-file-reviewer"
    assert payload.skill_manifests[1]["content_hash"] == "hash-dependency"


def test_queue_run_payload_rejects_release_decision_that_does_not_match_skill_version():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"message": "审核文件"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": {
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "policy_active": True,
                    "selected_version": "hash-other",
                    "selected_track": "current",
                },
            }
        )
    except ValueError as exc:
        assert "release_decision_selected_version_mismatch" in str(exc)
    else:
        raise AssertionError("queue payload should reject mismatched release decision")


def test_queue_run_payload_rejects_release_decision_without_locked_skill_version():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"message": "审核文件"},
                "executor_type": "claude-agent-worker",
                "release_decision": {
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "policy_active": True,
                    "selected_version": "hash-primary",
                    "selected_track": "current",
                },
            }
        )
    except ValueError as exc:
        assert "release_decision_skill_version_required" in str(exc)
    else:
        raise AssertionError("queue payload should reject release decision without skill_version")


def test_queue_run_payload_rejects_release_decision_without_primary_manifest():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"message": "审核文件"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": {
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "policy_active": True,
                    "selected_version": "hash-primary",
                    "selected_track": "current",
                },
                "skill_manifests": [],
            }
        )
    except ValueError as exc:
        assert "release_decision_primary_manifest_missing" in str(exc)
    else:
        raise AssertionError("queue payload should reject release decision without primary manifest")


def test_queue_run_payload_rejects_release_decision_with_dependency_only_manifest():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"message": "审核文件"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": {
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "policy_active": True,
                    "selected_version": "hash-primary",
                    "selected_track": "current",
                },
                "skill_manifests": [{"skill_id": "minimax-docx", "content_hash": "hash-dependency"}],
            }
        )
    except ValueError as exc:
        assert "release_decision_primary_manifest_missing" in str(exc)
    else:
        raise AssertionError("queue payload should reject dependency-only manifests")


def test_queue_run_payload_rejects_duplicate_skill_manifests():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"message": "审核文件"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": {
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "policy_active": False,
                    "selected_version": "hash-primary",
                    "selected_track": "manifest_pin",
                },
                "skill_manifests": [
                    {"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"},
                    {"skill_id": "qa-file-reviewer", "content_hash": "hash-other"},
                ],
            }
        )
    except ValueError as exc:
        assert "release_decision_duplicate_skill_manifest" in str(exc)
    else:
        raise AssertionError("queue payload should reject duplicate skill manifests")


def test_queue_run_payload_rejects_canonical_duplicate_skill_manifests():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"message": "审核文件"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": release_decision("hash-primary"),
                "skill_manifests": [
                    {"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"},
                    {"skill_id": " qa-file-reviewer ", "content_hash": "hash-other"},
                ],
            }
        )
    except ValueError as exc:
        assert "release_decision_duplicate_skill_manifest" in str(exc)
    else:
        raise AssertionError("queue payload should reject canonical duplicate skill manifests")


def test_run_payload_rejects_duplicate_skill_manifests():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            skill_version="hash-primary",
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-primary",
                "selected_track": "manifest_pin",
            },
            skill_manifests=[
                {"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"},
                {"skill_id": "qa-file-reviewer", "content_hash": "hash-other"},
            ],
        )
    except ValueError as exc:
        assert "release_decision_duplicate_skill_manifest" in str(exc)
    else:
        raise AssertionError("run payload should reject duplicate skill manifests")


def test_run_payload_rejects_canonical_duplicate_skill_manifests():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            skill_version="hash-primary",
            release_decision=release_decision("hash-primary"),
            skill_manifests=[
                {"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"},
                {"skill_id": " qa-file-reviewer ", "content_hash": "hash-other"},
            ],
        )
    except ValueError as exc:
        assert "release_decision_duplicate_skill_manifest" in str(exc)
    else:
        raise AssertionError("run payload should reject canonical duplicate skill manifests")


def test_queue_run_payload_rejects_release_decision_with_primary_manifest_hash_mismatch():
    try:
        QueueRunPayload.model_validate(
            {
                "tenant_id": "default",
                "workspace_id": "default",
                "user_id": "user-a",
                "session_id": "ses_1",
                "run_id": "run_1",
                "agent_id": "qa-word-review",
                "skill_id": "qa-file-reviewer",
                "file_ids": [],
                "input": {"message": "审核文件"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-primary",
                "release_decision": {
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "policy_active": True,
                    "selected_version": "hash-primary",
                    "selected_track": "current",
                },
                "skill_manifests": [{"skill_id": "qa-file-reviewer", "content_hash": "hash-other"}],
            }
        )
    except ValueError as exc:
        assert "release_decision_primary_manifest_mismatch" in str(exc)
    else:
        raise AssertionError("queue payload should reject primary manifest hash mismatch")


def test_run_payload_rejects_release_decision_that_does_not_match_skill_version():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            skill_version="hash-primary",
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": True,
                "selected_version": "hash-other",
                "selected_track": "current",
            },
        )
    except ValueError as exc:
        assert "release_decision_selected_version_mismatch" in str(exc)
    else:
        raise AssertionError("run payload should reject mismatched release decision")


def test_run_payload_rejects_release_decision_without_locked_skill_version():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": True,
                "selected_version": "hash-primary",
                "selected_track": "current",
            },
        )
    except ValueError as exc:
        assert "release_decision_skill_version_required" in str(exc)
    else:
        raise AssertionError("run payload should reject release decision without skill_version")


def test_run_payload_rejects_release_decision_without_primary_manifest():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            skill_version="hash-primary",
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": True,
                "selected_version": "hash-primary",
                "selected_track": "current",
            },
            skill_manifests=[],
        )
    except ValueError as exc:
        assert "release_decision_primary_manifest_missing" in str(exc)
    else:
        raise AssertionError("run payload should reject release decision without primary manifest")


def test_run_payload_rejects_release_decision_with_dependency_only_manifest():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            skill_version="hash-primary",
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": True,
                "selected_version": "hash-primary",
                "selected_track": "current",
            },
            skill_manifests=[{"skill_id": "minimax-docx", "content_hash": "hash-dependency"}],
        )
    except ValueError as exc:
        assert "release_decision_primary_manifest_missing" in str(exc)
    else:
        raise AssertionError("run payload should reject dependency-only manifests")


def test_run_payload_rejects_release_decision_with_primary_manifest_hash_mismatch():
    try:
        RunPayload(
            tenant_id="default",
            workspace_id="default",
            user_id="user-a",
            session_id="ses_1",
            run_id="run_1",
            attempt_id="attempt_1",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
            file_ids=[],
            input={"message": "审核文件"},
            skill_version="hash-primary",
            release_decision={
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": True,
                "selected_version": "hash-primary",
                "selected_track": "current",
            },
            skill_manifests=[{"skill_id": "qa-file-reviewer", "content_hash": "hash-other"}],
        )
    except ValueError as exc:
        assert "release_decision_primary_manifest_mismatch" in str(exc)
    else:
        raise AssertionError("run payload should reject primary manifest hash mismatch")


def test_public_payload_redacts_flattened_release_decision_fields():
    payload = sanitize_public_payload(
        {
            "fallback_version": "hash-fallback",
            "policy_active": True,
            "channel": "stable",
            "message": "visible",
        }
    )

    assert payload == {"message": "visible"}


def test_public_payload_redacts_skill_manifest_hashes():
    payload = sanitize_public_payload(
        {
            "message": "visible",
            "skill_manifests": [
                {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-primary",
                    "content_hash": "hash-primary",
                }
            ],
            "nested": {"content_hash": "hash-nested"},
        }
    )

    assert payload == {"message": "visible", "nested": {}}


def test_run_payload_contract_includes_trace_and_schema_version():
    payload = RunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        attempt_id="attempt-a",
        agent_id="general-agent",
        skill_id="general-chat",
        file_ids=[],
        input={"message": "hello"},
        trace_id="trace_a",
        skill_version="hash-primary",
        release_decision=release_decision("hash-primary"),
        skill_manifests=[primary_manifest("general-chat", "hash-primary")],
        context_snapshot_id="ctx_trace",
        context_snapshot={"source": "runs_api"},
    )

    assert payload.trace_id == "trace_a"
    assert payload.schema_version == "ai-platform.run-payload.v1"
    assert payload.context_snapshot_id == "ctx_trace"
    assert payload.context_snapshot["source"] == "runs_api"


def test_run_payload_carries_skill_manifest_pins():
    payload = RunPayload(
        tenant_id="default",
        workspace_id="default",
        user_id="user-a",
        session_id="ses_1",
        run_id="run_1",
        attempt_id="attempt_1",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        file_ids=["file_1"],
        input={"message": "审核文件"},
        skill_version="hash-primary",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "policy_active": True,
            "selected_version": "hash-primary",
            "selected_track": "current",
        },
        skill_manifests=[{"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"}],
    )

    assert payload.skill_version == "hash-primary"
    assert payload.release_decision["selected_version"] == "hash-primary"
    assert payload.skill_manifests == [{"skill_id": "qa-file-reviewer", "content_hash": "hash-primary"}]


def test_skill_definition_contract_is_stable():
    skill = SkillDefinition(
        skill_id="baoyu-translate",
        version="1.0.0",
        executor_type="claude-agent-worker",
        input_schema={"file_ids": ["string"]},
        output_schema={"artifacts": ["translated_docx"]},
    )
    assert skill.executor_type == "claude-agent-worker"


def test_default_registry_does_not_expose_runtime211_direct_executor():
    from app.executors.registry import AdapterRegistry

    try:
        AdapterRegistry().get("runtime211")
    except KeyError as exc:
        assert "runtime211" in str(exc)
    else:
        raise AssertionError("runtime211 must not be available as a default direct executor")


def test_default_registry_keeps_ragflow_behind_the_harness_mcp_boundary():
    from app.executors.registry import AdapterRegistry

    try:
        AdapterRegistry().get("ragflow")
    except KeyError as exc:
        assert "ragflow" in str(exc)
    else:
        raise AssertionError("RAGFlow must be an MCP tool, not a second default executor")
