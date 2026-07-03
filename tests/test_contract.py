from app.executors.runtime211 import Runtime211RunPayload
from app.main import create_app
from app.executors.base import RunPayload
from app.models import AgentApp, CreateRunRequest, QueueRunPayload, SkillDefinition
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
    paths = {route.path for route in app.routes}

    assert "/api/ai/health" in paths
    assert "/api/ai/admin/status" in paths
    assert "/api/ai/agent-apps" in paths
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


def test_runtime211_payload_keeps_platform_context():
    payload = Runtime211RunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        file_ids=["file-a"],
        input={"mode": "file"},
        skill_version="hash-primary",
        release_decision=release_decision("hash-primary"),
        skill_manifests=[primary_manifest("qa-file-reviewer", "hash-primary")],
    )

    assert payload.tenant_id == "tenant-a"
    assert payload.session_id == "session-a"
    assert payload.run_id == "run-a"
    assert payload.skill_id == "qa-file-reviewer"


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


def test_agent_app_contract_is_stable():
    app = AgentApp(
        app_id="translate",
        name="翻译",
        mode="chat_file",
        default_skill_id="baoyu-translate",
        allowed_input_types=["docx"],
        output_types=["translated_docx"],
    )
    assert app.default_skill_id == "baoyu-translate"
    assert app.mode == "chat_file"


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


def test_registry_includes_ragflow_executor():
    from app.executors.registry import AdapterRegistry

    adapter = AdapterRegistry().get("ragflow")

    assert adapter.executor_type == "ragflow"
