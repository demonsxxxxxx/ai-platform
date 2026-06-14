from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import RepositoryNotFoundError
from app.routes.context import (
    MEMORY_PREVIEW_URL_DECODE_DEPTH,
    _memory_delete_response,
    _memory_policy_response,
    _memory_response,
)
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
    data = headers()
    data["X-AI-Roles"] = "admin"
    data["X-AI-User-ID"] = "admin-a"
    data["X-AI-User-Name"] = "Admin A"
    return data


def url_encode_layers(value: str, layers: int) -> str:
    encoded = value
    for _ in range(layers):
        encoded = quote(encoded, safe="")
    return encoded


def test_memory_public_projections_map_internal_agent_ids():
    row = {
        "id": "mem-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "qa-word-review",
        "session_id": "session-a",
        "record_type": "session_summary",
        "content": "safe",
        "metadata_json": {},
        "status": "active",
        "expires_at": None,
        "deleted_at": None,
        "created_at": None,
        "updated_at": None,
    }
    policy = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "qa-word-review",
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "redaction_mode": "strict",
        "source": "default",
        "reason": "",
        "updated_by": "",
        "updated_at": None,
    }

    memory = _memory_response(row)
    deleted = _memory_delete_response(row)
    policy_payload = _memory_policy_response(policy)

    assert memory["agent_id"] == "document-review"
    assert deleted["agent_id"] == "document-review"
    assert policy_payload["agent_id"] == "document-review"
    assert policy_payload["redaction_mode"] == "strict"
    assert "qa-word-review" not in str(memory)
    assert "qa-word-review" not in str(deleted)
    assert "qa-word-review" not in str(policy_payload)


def test_memory_policy_projection_treats_invalid_stored_redaction_mode_as_strict():
    policy = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "redaction_mode": "off",
        "source": "stored",
        "reason": "manual dirty row",
        "updated_by": "admin-a",
        "updated_at": "2026-06-05T00:00:00Z",
    }

    assert _memory_policy_response(policy)["redaction_mode"] == "strict"


def test_memory_policy_projection_treats_blank_stored_redaction_mode_as_strict():
    policy = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "redaction_mode": "",
        "source": "stored",
        "reason": "manual dirty row",
        "updated_by": "admin-a",
        "updated_at": "2026-06-05T00:00:00Z",
    }

    assert _memory_policy_response(policy)["redaction_mode"] == "strict"


def test_create_context_snapshot_records_snapshot_and_event(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("tenant-a", "user-a", "run-a")
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {
            "id": "ctx-a",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": kwargs["trace_id"],
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": kwargs["context_kind"],
            "included_message_ids": kwargs["included_message_ids"],
            "included_file_ids": kwargs["included_file_ids"],
            "included_artifact_ids": kwargs["included_artifact_ids"],
            "included_memory_record_ids": kwargs["included_memory_record_ids"],
            "redaction_summary_json": kwargs["redaction_summary_json"],
            "payload_json": kwargs["payload_json"],
        }

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.routes.context.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/context/snapshots",
        headers=headers(),
        json={
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "included_file_ids": ["file-a"],
            "included_artifact_ids": ["art-a"],
            "included_memory_record_ids": ["mem-a"],
            "redaction_summary": {"secrets": 0},
            "payload": {"window": "current"},
        },
    )

    assert response.status_code == 200
    body = response.json()["context_snapshot"]
    assert body["context_snapshot_id"] == "ctx-a"
    assert body["payload"]["referenced_materials"] == {
        "message_count": 1,
        "file_count": 1,
        "artifact_count": 1,
        "memory_record_count": 1,
    }
    assert body["payload"]["used_context_summary"] == {
        "source": "manual_context_snapshot",
        "input_keys": ["attachments", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert body["payload"]["latest_artifact_version"] is None
    assert body["payload"]["execution_tier"] == "sdk_only_writing"
    assert datetime.fromisoformat(body["payload"]["context_pack_generated_at"].replace("Z", "+00:00"))
    assert "included_message_ids" not in body
    assert "included_file_ids" not in body
    assert "included_artifact_ids" not in body
    assert "included_memory_record_ids" not in body
    serialized = response.text
    assert "msg-a" not in serialized
    assert "file-a" not in serialized
    assert "art-a" not in serialized
    assert "mem-a" not in serialized
    assert calls[0][0] == "snapshot"
    assert calls[0][1]["included_message_ids"] == ["msg-a"]
    assert calls[0][1]["included_file_ids"] == ["file-a"]
    assert calls[0][1]["included_artifact_ids"] == ["art-a"]
    assert calls[0][1]["included_memory_record_ids"] == ["mem-a"]
    assert calls[1][1]["event_type"] == "context_snapshot_created"


def test_context_snapshot_response_omits_raw_material_ids_from_public_projection(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return [
            {
                "id": "ctx-public",
                "tenant_id": tenant_id,
                "workspace_id": "workspace-a",
                "user_id": user_id,
                "session_id": "session-a",
                "run_id": run_id,
                "trace_id": "trace-a",
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_kind": "executor",
                "included_message_ids": ["msg-sensitive"],
                "included_file_ids": ["file-sensitive"],
                "included_artifact_ids": ["artifact-sensitive"],
                "included_memory_record_ids": ["memory-sensitive"],
                "redaction_summary_json": {},
                "payload_json": {"window": "current"},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/context/snapshots", headers=headers())

    assert response.status_code == 200
    body = response.json()["context_snapshots"][0]
    assert "included_message_ids" not in body
    assert "included_file_ids" not in body
    assert "included_artifact_ids" not in body
    assert "included_memory_record_ids" not in body
    assert body["payload"]["referenced_materials"] == {
        "message_count": 1,
        "file_count": 1,
        "artifact_count": 1,
        "memory_record_count": 1,
    }
    serialized = response.text
    assert "msg-sensitive" not in serialized
    assert "file-sensitive" not in serialized
    assert "artifact-sensitive" not in serialized
    assert "memory-sensitive" not in serialized


def test_context_snapshot_response_preserves_stored_safe_summary_metadata(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return [
            {
                "id": "ctx-stored-summary",
                "tenant_id": tenant_id,
                "workspace_id": "workspace-a",
                "user_id": user_id,
                "session_id": "session-a",
                "run_id": run_id,
                "trace_id": "trace-a",
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_kind": "executor",
                "included_message_ids": ["msg-a"],
                "included_file_ids": ["file-a"],
                "included_artifact_ids": [],
                "included_memory_record_ids": [],
                "redaction_summary_json": {},
                "payload_json": {
                    "memory_policy": {
                        "source": "stored",
                        "memory_enabled": False,
                        "long_term_memory_enabled": False,
                        "retention_days": 30,
                    },
                    "used_context_summary": {
                        "source": "runs_api",
                        "input_keys": ["message", "attachments", "raw_storage_key"],
                        "memory_policy_source": "stored",
                        "long_term_memory_read": True,
                    },
                    "execution_tier": "document_worker",
                    "latest_artifact_version": "v7",
                    "context_pack_version": "v9",
                    "context_pack_generated_at": "2026-06-12T01:23:45Z",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/context/snapshots", headers=headers())

    assert response.status_code == 200
    payload = response.json()["context_snapshots"][0]["payload"]
    assert payload["used_context_summary"] == {
        "source": "runs_api",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": True,
    }
    assert payload["execution_tier"] == "document_worker"
    assert payload["latest_artifact_version"] == "v7"
    assert payload["context_pack_version"] == "v9"
    assert payload["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = response.text.lower()
    assert "raw_storage_key" not in serialized


def test_context_snapshot_response_preserves_safe_top_level_legacy_source(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return [
            {
                "id": "ctx-chat-stream",
                "tenant_id": tenant_id,
                "workspace_id": "workspace-a",
                "user_id": user_id,
                "session_id": "session-a",
                "run_id": run_id,
                "trace_id": "trace-a",
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_kind": "executor",
                "included_message_ids": ["msg-a"],
                "included_file_ids": [],
                "included_artifact_ids": [],
                "included_memory_record_ids": [],
                "redaction_summary_json": {},
                "payload_json": {
                    "source": "chat_stream",
                    "message": "hello",
                    "context_pack_generated_at": "2026-06-12T01:23:45Z",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/context/snapshots", headers=headers())

    assert response.status_code == 200
    payload = response.json()["context_snapshots"][0]["payload"]
    assert payload["used_context_summary"] == {
        "source": "chat_stream",
        "input_keys": ["message"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert payload["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = response.text.lower()
    assert "stored_context_snapshot" not in serialized


def test_create_context_snapshot_rejects_forged_public_provenance_and_forbidden_aliases(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {
            "id": "ctx-forged",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": kwargs["trace_id"],
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": kwargs["context_kind"],
            "included_message_ids": kwargs["included_message_ids"],
            "included_file_ids": kwargs["included_file_ids"],
            "included_artifact_ids": kwargs["included_artifact_ids"],
            "included_memory_record_ids": kwargs["included_memory_record_ids"],
            "redaction_summary_json": kwargs["redaction_summary_json"],
            "payload_json": kwargs["payload_json"],
        }

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-forged"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.routes.context.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/context/snapshots",
        headers=headers(),
        json={
            "context_kind": "executor",
            "included_message_ids": ["msg-a"],
            "redaction_summary": {
                "memory_policy_source": "trusted-policy",
                "long_term_memory_read": True,
            },
            "payload": {
                "window": "current",
                "source": "forged_top_level_source",
                "raw_storage_key": "not-a-path-but-still-forbidden",
                "sandbox_workdir": "relative-workdir",
                "used_context_summary": {
                    "source": "forged_executor_injection",
                    "input_keys": ["raw_storage_key"],
                    "memory_policy_source": "forged-policy",
                    "long_term_memory_read": True,
                },
                "referenced_materials": {
                    "message_count": 99,
                    "file_count": 99,
                    "artifact_count": 99,
                    "memory_record_count": 99,
                },
                "execution_tier": "heavy_sandbox",
                "context_pack_generated_at": "1999-01-01T00:00:00Z",
                "latest_artifact_version": "forged-artifact-version",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()["context_snapshot"]["payload"]
    assert payload["window"] == "current"
    assert payload["referenced_materials"] == {
        "message_count": 1,
        "file_count": 0,
        "artifact_count": 0,
        "memory_record_count": 0,
    }
    assert payload["used_context_summary"] == {
        "source": "manual_context_snapshot",
        "input_keys": ["window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert payload["execution_tier"] == "sdk_only_writing"
    assert payload["latest_artifact_version"] is None
    assert datetime.fromisoformat(payload["context_pack_generated_at"].replace("Z", "+00:00"))
    assert payload["context_pack_generated_at"] != "1999-01-01T00:00:00Z"
    serialized = response.text.lower()
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "not-a-path-but-still-forbidden" not in serialized
    assert "relative-workdir" not in serialized
    assert "forged_top_level_source" not in serialized
    assert "forged_executor_injection" not in serialized
    assert "forged-policy" not in serialized
    assert "heavy_sandbox" not in serialized
    assert "forged-artifact-version" not in serialized


def test_create_context_snapshot_redacts_payload_before_persisting(monkeypatch):
    calls = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {
            "id": "ctx-redacted",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs["run_id"],
            "trace_id": kwargs["trace_id"],
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_kind": kwargs["context_kind"],
            "included_message_ids": kwargs["included_message_ids"],
            "included_file_ids": kwargs["included_file_ids"],
            "included_artifact_ids": kwargs["included_artifact_ids"],
            "included_memory_record_ids": kwargs["included_memory_record_ids"],
            "redaction_summary_json": kwargs["redaction_summary_json"],
            "payload_json": kwargs["payload_json"],
        }

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-redacted"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.routes.context.repositories.append_event", fake_append_event)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-a/context/snapshots",
        headers=headers(),
        json={
            "context_kind": "executor",
            "redaction_summary": {
                "source": "manual",
                "client_secret": "client-secret-context",
            },
            "payload": {
                "window": "current",
                "api_key": "sk-context-secret",
                "runtime_path": "/var/lib/ai-platform/run-a",
                "nested": {
                    "note": "smoke-secret-token",
                    "summary": "authorization: Bearer context-bearer alice@example.com",
                },
            },
        },
    )

    assert response.status_code == 200
    persisted_payload = calls[0][1]["payload_json"]
    persisted_summary = calls[0][1]["redaction_summary_json"]
    assert persisted_payload["window"] == "current"
    assert persisted_payload["nested"]["note"] == "[redacted-secret]"
    assert persisted_summary == {"source": "manual"}
    serialized = str(persisted_payload) + str(persisted_summary) + response.text
    assert "sk-context-secret" not in serialized
    assert "smoke-secret-token" not in serialized
    assert "client-secret-context" not in serialized
    assert "context-bearer" not in serialized
    assert "alice@example.com" not in serialized
    assert "/var/lib/ai-platform/run-a" not in serialized


def test_list_context_snapshots_redacts_legacy_dirty_payload_and_summary(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return [
            {
                "id": "ctx-dirty",
                "tenant_id": tenant_id,
                "workspace_id": "workspace-a",
                "user_id": user_id,
                "session_id": "session-a",
                "run_id": run_id,
                "trace_id": "trace-a",
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_kind": "executor",
                "included_message_ids": [],
                "included_file_ids": [],
                "included_artifact_ids": [],
                "included_memory_record_ids": [],
                "redaction_summary_json": {
                    "source": "legacy",
                    "client_secret": "client-secret-legacy",
                    "note": "authorization: Bearer legacy-bearer",
                },
                "payload_json": {
                    "window": "legacy",
                    "api_key": "sk-legacy-context",
                    "runtime_path": "/var/lib/ai-platform/run-a",
                    "nested": {"email": "alice@example.com", "safe": "kept"},
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/context/snapshots", headers=headers())

    assert response.status_code == 200
    body = response.json()["context_snapshots"][0]
    assert body["payload"]["window"] == "legacy"
    assert body["payload"]["nested"] == {"email": "[redacted-email]", "safe": "kept"}
    assert body["payload"]["referenced_materials"] == {
        "message_count": 0,
        "file_count": 0,
        "artifact_count": 0,
        "memory_record_count": 0,
    }
    assert body["payload"]["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["nested", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert body["payload"]["latest_artifact_version"] is None
    assert body["payload"]["execution_tier"] == "sdk_only_writing"
    assert datetime.fromisoformat(body["payload"]["context_pack_generated_at"].replace("Z", "+00:00"))
    assert body["redaction_summary"] == {
        "source": "legacy",
        "note": "authorization=[redacted-secret]",
    }
    serialized = response.text
    assert "client-secret-legacy" not in serialized
    assert "legacy-bearer" not in serialized
    assert "sk-legacy-context" not in serialized
    assert "/var/lib/ai-platform/run-a" not in serialized
    assert "alice@example.com" not in serialized


def test_list_context_snapshots_regenerates_dirty_legacy_provenance(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return [
            {
                "id": "ctx-dirty-provenance",
                "tenant_id": tenant_id,
                "workspace_id": "workspace-a",
                "user_id": user_id,
                "session_id": "session-a",
                "run_id": run_id,
                "trace_id": "trace-a",
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_kind": "executor",
                "included_message_ids": ["msg-a"],
                "included_file_ids": [],
                "included_artifact_ids": [],
                "included_memory_record_ids": [],
                "redaction_summary_json": {},
                "payload_json": {
                    "window": "legacy",
                    "used_context_summary": {
                        "source": "legacy",
                        "input_keys": ["window", "private_payload", "storage_key"],
                        "memory_policy_source": "stored",
                        "long_term_memory_read": True,
                    },
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/context/snapshots", headers=headers())

    assert response.status_code == 200
    payload = response.json()["context_snapshots"][0]["payload"]
    assert payload["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    serialized = response.text.lower()
    assert "private_payload" not in serialized
    assert "storage_key" not in serialized


def test_list_context_snapshots_replaces_malformed_legacy_provenance(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id, "workspace_id": "workspace-a", "session_id": "session-a", "trace_id": "trace-a"}

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return [
            {
                "id": "ctx-malformed-provenance",
                "tenant_id": tenant_id,
                "workspace_id": "workspace-a",
                "user_id": user_id,
                "session_id": "session-a",
                "run_id": run_id,
                "trace_id": "trace-a",
                "schema_version": "ai-platform.context-snapshot.v1",
                "context_kind": "executor",
                "included_message_ids": ["msg-a", "msg-b"],
                "included_file_ids": ["file-a"],
                "included_artifact_ids": [],
                "included_memory_record_ids": [],
                "redaction_summary_json": {},
                "payload_json": {
                    "window": "legacy",
                    "used_context_summary": "not-a-dict",
                    "referenced_materials": {
                        "message_ids": ["msg-a"],
                        "raw_storage_key": "storage://secret",
                    },
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.context.repositories.list_context_snapshots", fake_list_context_snapshots)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-a/context/snapshots", headers=headers())

    assert response.status_code == 200
    payload = response.json()["context_snapshots"][0]["payload"]
    assert payload["referenced_materials"] == {
        "message_count": 2,
        "file_count": 1,
        "artifact_count": 0,
        "memory_record_count": 0,
    }
    assert payload["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["attachments", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    serialized = response.text.lower()
    assert "raw_storage_key" not in serialized
    assert "storage://secret" not in serialized
    assert "msg-a" not in serialized.replace('"msg-a"', "")


def test_create_memory_record_requires_session_id_before_writing(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_create_memory_record(conn, **kwargs):
        raise AssertionError("session-scoped memory must not be written without session_id")

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "default",
            "reason": "",
            "updated_by": "",
            "updated_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fake_create_memory_record)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "general-agent",
            "record_type": "session_summary",
            "content": "User prefers concise answers.",
            "metadata": {"source": "test"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "memory_session_id_required"
    assert calls == []


def test_create_memory_record_response_redacts_legacy_secret_like_content_and_metadata(monkeypatch):
    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_ensure_user(conn, **kwargs):
        return None

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "source": "default",
            "reason": "",
            "updated_by": "",
            "updated_at": None,
        }

    async def fake_create_memory_record(conn, **kwargs):
        return {
            "id": "mem-create-secret",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "session_id": kwargs["session_id"],
            "record_type": kwargs["record_type"],
            "content": (
                "authorization: Bearer sk-live-create "
                "{\"api_key\":\"sk-live-create-json\"} alice@example.com "
                "client_secret=client-secret-text openai_api_key=sk-openai-text id_token=id-token-text "
                "{\"client_secret\":\"client-secret-json\",\"openai_api_key\":\"sk-openai-json\",\"id_token\":\"id-token-json\"}"
            ),
            "metadata_json": {
                "source": "test",
                "client_secret": "client-secret-value",
                "openai_api_key": "sk-openai-value",
                "id_token": "id-token-value",
                "nested": {
                    "note": (
                        "authorization: Bearer nested-bearer-token "
                        "{\"client_secret\":\"client-secret-json-meta\",\"openai_api_key\":\"sk-openai-json-meta\","
                        "\"id_token\":\"id-token-json-meta\"}"
                    )
                },
            },
            "status": "active",
            "expires_at": "2026-07-03T12:00:00Z",
            "deleted_at": None,
            "created_at": "2026-06-03T12:00:00Z",
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fake_create_memory_record)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "general-agent",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "stored value is returned by fake repository",
            "metadata": {"source": "test"},
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_record"]
    serialized = response.text
    assert "sk-live-create" not in serialized
    assert "sk-live-create-json" not in serialized
    assert "client-secret-value" not in serialized
    assert "sk-openai-value" not in serialized
    assert "id-token-value" not in serialized
    assert "client-secret-text" not in serialized
    assert "sk-openai-text" not in serialized
    assert "id-token-text" not in serialized
    assert "client-secret-json" not in serialized
    assert "sk-openai-json" not in serialized
    assert "id-token-json" not in serialized
    assert "nested-bearer-token" not in serialized
    assert "client-secret-json-meta" not in serialized
    assert "sk-openai-json-meta" not in serialized
    assert "id-token-json-meta" not in serialized
    assert "alice@example.com" not in serialized
    assert "authorization=[redacted-secret] [redacted-secret]" not in serialized
    assert body["content"].startswith("authorization=")
    assert "[redacted-secret]" in body["content"]
    assert "[redacted-email]" in body["content"]
    assert body["metadata"]["source"] == "test"
    assert body["metadata"]["client_secret"] == "[redacted-secret]"


def test_create_memory_record_applies_effective_policy_retention_days(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "strict",
            "source": "stored",
            "reason": "short retention",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fake_create_memory_record(conn, **kwargs):
        assert kwargs["retention_days"] == 30
        assert kwargs["redaction_mode"] == "strict"
        calls.append(("memory", kwargs))
        return {
            "id": "mem-retention",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "session_id": kwargs["session_id"],
            "record_type": kwargs["record_type"],
            "content": kwargs["content"],
            "metadata_json": kwargs["metadata_json"],
            "status": "active",
            "expires_at": "2026-07-02T12:00:00Z",
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fake_create_memory_record)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "general-agent",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "Retain this for policy duration.",
            "metadata": {"source": "test"},
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_record"]
    assert body["memory_record_id"] == "mem-retention"
    assert body["expires_at"] == "2026-07-02T12:00:00Z"
    assert any(call[0] == "session" for call in calls)
    assert any(call[0] == "policy" for call in calls)
    assert any(call[0] == "memory" for call in calls)


def test_create_memory_record_denies_write_when_memory_policy_disabled_and_audits(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fail_create_memory_record(conn, **kwargs):
        raise AssertionError("disabled memory policy must block memory writes")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-policy"

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fail_create_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "general-agent",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "Do not store this.",
            "metadata": {"token": "hidden"},
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "memory_policy_disabled"
    assert calls[2][0] == "session"
    assert calls[3][0] == "policy"
    assert calls[4][0] == "audit"
    assert calls[4][1]["action"] == "memory.record.create_denied"
    assert calls[4][1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "agent_id": "general-agent",
        "session_id": "session-a",
        "record_type": "session_summary",
        "reason": "memory_policy_disabled",
    }
    assert "Do not store this" not in str(calls)
    assert "hidden" not in str(calls)


def test_create_memory_record_maps_public_agent_id_before_session_policy_and_audit(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "qa-word-review"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        assert kwargs["agent_id"] == "qa-word-review"
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "agent opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fail_create_memory_record(conn, **kwargs):
        raise AssertionError("disabled memory policy must block public-agent memory writes")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fail_create_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "document-review",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "Do not store this.",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "memory_policy_disabled"
    assert calls[2] == ("session", "tenant-a", "user-a", "session-a")
    assert calls[3][0] == "policy"
    assert calls[4][1]["payload_json"]["agent_id"] == "document-review"
    assert "qa-word-review" not in response.text


def test_create_memory_record_maps_public_agent_id_for_success_response(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "qa-word-review"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        assert kwargs["agent_id"] == "qa-word-review"
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "source": "stored",
            "reason": "",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fake_create_memory_record(conn, **kwargs):
        calls.append(("memory", kwargs))
        assert kwargs["agent_id"] == "qa-word-review"
        return {
            "id": "mem-public-success",
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "session_id": kwargs["session_id"],
            "record_type": kwargs["record_type"],
            "content": kwargs["content"],
            "metadata_json": kwargs["metadata_json"],
            "status": "active",
            "expires_at": "2026-07-02T12:00:00Z",
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fake_create_memory_record)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "document-review",
            "session_id": "session-a",
            "record_type": "task_note",
            "content": "Store this.",
            "metadata": {"source": "test"},
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_record"]
    assert body["agent_id"] == "document-review"
    assert calls[4][0] == "memory"
    assert "qa-word-review" not in response.text


def test_create_memory_record_omitted_agent_id_projects_session_agent_in_denied_audit(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "qa-word-review"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        assert kwargs["agent_id"] == "qa-word-review"
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "session agent opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fail_create_memory_record(conn, **kwargs):
        raise AssertionError("disabled memory policy must block omitted-agent public session writes")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fail_create_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "Do not store this.",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "memory_policy_disabled"
    assert calls[2] == ("session", "tenant-a", "user-a", "session-a")
    assert calls[3][0] == "policy"
    assert calls[4][1]["payload_json"]["agent_id"] == "document-review"
    assert "qa-word-review" not in str(calls[4][1]["payload_json"])


def test_create_memory_record_uses_session_agent_for_policy_when_agent_id_is_omitted(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        assert kwargs["agent_id"] == "general-agent"
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "agent opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fail_create_memory_record(conn, **kwargs):
        raise AssertionError("omitting agent_id must not bypass agent-scoped disabled policy")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.create_memory_record", fail_create_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "Do not store this.",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "memory_policy_disabled"
    assert calls[2] == ("session", "tenant-a", "user-a", "session-a")
    assert calls[3][0] == "policy"
    assert calls[4][1]["payload_json"]["agent_id"] == "general-agent"


def test_create_memory_record_rejects_agent_session_mismatch(monkeypatch):
    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_ensure_user(conn, **kwargs):
        return None

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fail_policy(conn, **kwargs):
        raise AssertionError("agent/session mismatch must be rejected before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fail_policy)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/memory/records",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "other-agent",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "Mismatch.",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "session_not_found"


def test_delete_memory_record_soft_deletes_and_writes_audit(monkeypatch):
    calls = []

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_delete_memory_record(conn, *, tenant_id, workspace_id, user_id, agent_id, session_id, record_id):
        calls.append(("delete", tenant_id, workspace_id, user_id, agent_id, session_id, record_id))
        return {
            "id": record_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": "general-agent",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "User prefers concise answers.",
            "metadata_json": {"source": "test"},
            "status": "deleted",
            "deleted_at": "2026-06-02T12:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fake_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.delete(
        "/api/ai/memory/records/mem-a?workspace_id=workspace-a&agent_id=general-agent&session_id=session-a&reason=user-requested",
        headers=headers(),
    )

    assert response.status_code == 200
    body = response.json()["memory_record"]
    assert body["memory_record_id"] == "mem-a"
    assert body["status"] == "deleted"
    assert calls[0] == ("session", "tenant-a", "user-a", "session-a")
    assert calls[1] == ("delete", "tenant-a", "workspace-a", "user-a", "general-agent", "session-a", "mem-a")
    assert calls[2][1]["action"] == "memory.record.deleted"
    assert calls[2][1]["target_type"] == "memory_record"
    assert calls[2][1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "agent_id": "general-agent",
        "session_id": "session-a",
        "record_type": "session_summary",
        "reason": "user-requested",
    }


def test_delete_memory_record_maps_public_agent_id_before_session_delete_and_audit(monkeypatch):
    calls = []

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "qa-word-review"}

    async def fake_delete_memory_record(conn, *, tenant_id, workspace_id, user_id, agent_id, session_id, record_id):
        calls.append(("delete", tenant_id, workspace_id, user_id, agent_id, session_id, record_id))
        return {
            "id": record_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "record_type": "task_note",
            "content": "deleted content",
            "metadata_json": {"source": "test"},
            "status": "deleted",
            "deleted_at": "2026-06-03T12:00:00Z",
            "created_at": "2026-06-03T11:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-delete"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fake_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.delete(
        "/api/ai/memory/records/mem-a?workspace_id=workspace-a&agent_id=document-review&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 200
    body = response.json()["memory_record"]
    assert body["agent_id"] == "document-review"
    assert calls[1] == ("delete", "tenant-a", "workspace-a", "user-a", "qa-word-review", "session-a", "mem-a")
    assert calls[2][1]["payload_json"]["agent_id"] == "document-review"
    assert "qa-word-review" not in response.text


def test_delete_memory_record_rejects_agent_session_mismatch_before_delete_or_audit(monkeypatch):
    calls = []

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "qa-word-review"}

    async def fail_delete_memory_record(conn, **kwargs):
        raise AssertionError("agent/session mismatch must not delete memory records")

    async def fail_append_audit_log(conn, **kwargs):
        raise AssertionError("agent/session mismatch must not write delete audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fail_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fail_append_audit_log)
    client = TestClient(create_app())

    response = client.delete(
        "/api/ai/memory/records/mem-a?workspace_id=workspace-a&agent_id=general-agent&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "session_not_found"
    assert calls == [("session", "tenant-a", "user-a", "session-a")]


def test_delete_memory_record_requires_session_scope(monkeypatch):
    async def fail_delete_memory_record(conn, **kwargs):
        raise AssertionError("memory delete must not query without explicit session scope")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fail_delete_memory_record)
    client = TestClient(create_app())

    response = client.delete("/api/ai/memory/records/mem-a?workspace_id=workspace-a", headers=headers())

    assert response.status_code == 400
    assert response.json()["detail"] == "memory_session_id_required"


def test_list_memory_records_rejects_unsafe_query_ids_with_422(monkeypatch):
    async def fail_list_memory_records(conn, **kwargs):
        raise AssertionError("unsafe query ids must fail before repository access")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.list_memory_records", fail_list_memory_records)
    client = TestClient(create_app())

    bad_workspace = client.get(
        "/api/ai/memory/records?workspace_id=bad%20id&session_id=session-a",
        headers=headers(),
    )
    bad_agent = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&agent_id=bad%20id&session_id=session-a",
        headers=headers(),
    )
    bad_session = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&session_id=bad%20id",
        headers=headers(),
    )

    assert bad_workspace.status_code == 422
    assert bad_workspace.json()["detail"] == "workspace_id contains unsupported characters"
    assert bad_agent.status_code == 422
    assert bad_agent.json()["detail"] == "agent_id contains unsupported characters"
    assert bad_session.status_code == 422
    assert bad_session.json()["detail"] == "session_id contains unsupported characters"


def test_delete_memory_record_rejects_unsafe_ids_with_422(monkeypatch):
    async def fail_delete_memory_record(conn, **kwargs):
        raise AssertionError("unsafe ids must fail before repository access")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fail_delete_memory_record)
    client = TestClient(create_app())

    bad_record = client.delete(
        "/api/ai/memory/records/bad%20id?workspace_id=workspace-a&session_id=session-a",
        headers=headers(),
    )
    bad_workspace = client.delete(
        "/api/ai/memory/records/mem-a?workspace_id=bad%20id&session_id=session-a",
        headers=headers(),
    )
    bad_agent = client.delete(
        "/api/ai/memory/records/mem-a?workspace_id=workspace-a&agent_id=bad%20id&session_id=session-a",
        headers=headers(),
    )
    bad_session = client.delete(
        "/api/ai/memory/records/mem-a?workspace_id=workspace-a&session_id=bad%20id",
        headers=headers(),
    )

    assert bad_record.status_code == 422
    assert bad_record.json()["detail"] == "record_id contains unsupported characters"
    assert bad_workspace.status_code == 422
    assert bad_workspace.json()["detail"] == "workspace_id contains unsupported characters"
    assert bad_agent.status_code == 422
    assert bad_agent.json()["detail"] == "agent_id contains unsupported characters"
    assert bad_session.status_code == 422
    assert bad_session.json()["detail"] == "session_id contains unsupported characters"


def test_list_memory_records_requires_session_scope(monkeypatch):
    async def fail_list_memory_records(conn, **kwargs):
        raise AssertionError("memory list must not query without explicit session scope")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.list_memory_records", fail_list_memory_records)
    client = TestClient(create_app())

    response = client.get("/api/ai/memory/records?workspace_id=workspace-a", headers=headers())

    assert response.status_code == 400
    assert response.json()["detail"] == "memory_session_id_required"


def test_list_memory_records_returns_empty_when_memory_policy_disabled(monkeypatch):
    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fail_list_memory_records(conn, **kwargs):
        raise AssertionError("disabled memory policy must block memory reads")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.list_memory_records", fail_list_memory_records)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&agent_id=general-agent&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"memory_records": []}


def test_list_memory_records_maps_public_agent_id_before_session_policy(monkeypatch):
    calls = []

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "qa-word-review"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        assert kwargs["agent_id"] == "qa-word-review"
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "agent opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fail_list_memory_records(conn, **kwargs):
        raise AssertionError("disabled memory policy must block public-agent memory reads")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.list_memory_records", fail_list_memory_records)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&agent_id=document-review&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"memory_records": []}
    assert calls[0] == ("session", "tenant-a", "user-a", "session-a")
    assert calls[1][0] == "policy"


def test_list_memory_records_maps_public_agent_id_for_non_empty_response(monkeypatch):
    calls = []

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "qa-word-review"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        assert kwargs["agent_id"] == "qa-word-review"
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fake_list_memory_records(conn, **kwargs):
        calls.append(("list", kwargs))
        assert kwargs["agent_id"] == "qa-word-review"
        return [
            {
                "id": "mem-public-list",
                "tenant_id": kwargs["tenant_id"],
                "workspace_id": kwargs["workspace_id"],
                "user_id": kwargs["user_id"],
                "agent_id": kwargs["agent_id"],
                "session_id": kwargs["session_id"],
                "record_type": "task_note",
                "content": "Stored note.",
                "metadata_json": {"source": "test"},
                "status": "active",
                "expires_at": "2026-07-02T12:00:00Z",
                "created_at": "2026-06-02T12:00:00Z",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.list_memory_records", fake_list_memory_records)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&agent_id=document-review&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 200
    body = response.json()["memory_records"]
    assert body[0]["agent_id"] == "document-review"
    assert calls[2][0] == "list"
    assert "qa-word-review" not in response.text


def test_list_memory_records_uses_session_agent_for_policy_when_agent_id_is_omitted(monkeypatch):
    calls = []

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        calls.append(("session", tenant_id, user_id, session_id))
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        assert kwargs["agent_id"] == "general-agent"
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "stored",
            "reason": "agent opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fail_list_memory_records(conn, **kwargs):
        raise AssertionError("omitting agent_id must not bypass agent-scoped disabled policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.list_memory_records", fail_list_memory_records)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"memory_records": []}
    assert calls[0] == ("session", "tenant-a", "user-a", "session-a")
    assert calls[1][0] == "policy"


def test_list_memory_records_redacts_legacy_secret_like_content_and_metadata(monkeypatch):
    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "source": "default",
            "reason": "",
            "updated_by": "",
            "updated_at": None,
        }

    async def fake_list_memory_records(conn, **kwargs):
        return [
            {
                "id": "mem-legacy-secret",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "User api_key=sk-live-123 password: hidden-password email alice@example.com",
                "metadata_json": {
                    "source": "test",
                    "api_key": "sk-live-456",
                    "nested": {"token": "hidden-token", "note": "password=hidden-password-2"},
                },
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.routes.context.repositories.list_memory_records", fake_list_memory_records)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 200
    body = response.json()
    serialized = response.text
    assert "sk-live-123" not in serialized
    assert "sk-live-456" not in serialized
    assert "hidden-password" not in serialized
    assert "hidden-token" not in serialized
    assert "alice@example.com" not in serialized
    assert body["memory_records"][0]["content"].count("[redacted-secret]") == 2
    assert "[redacted-email]" in body["memory_records"][0]["content"]
    assert body["memory_records"][0]["metadata"]["source"] == "test"


def test_list_memory_records_rejects_agent_session_mismatch(monkeypatch):
    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fail_policy(conn, **kwargs):
        raise AssertionError("agent/session mismatch must be rejected before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fail_policy)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/memory/records?workspace_id=workspace-a&agent_id=other-agent&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "session_not_found"


def test_admin_delete_memory_record_response_does_not_expose_content_or_metadata(monkeypatch):
    async def fake_admin_delete_memory_record(conn, *, tenant_id, workspace_id, record_id):
        return {
            "id": record_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": "user-b",
            "agent_id": "general-agent",
            "session_id": "session-b",
            "record_type": "user_preference",
            "content": "secret preference body",
            "metadata_json": {"token": "hidden"},
            "status": "deleted",
            "deleted_at": "2026-06-02T12:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        return "audit-admin"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.admin_delete_memory_record", fake_admin_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.delete(
        "/api/ai/admin/memory/records/mem-b?workspace_id=workspace-a",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()["memory_record"]
    assert body["memory_record_id"] == "mem-b"
    assert "content" not in body
    assert "metadata" not in body
    assert "secret preference body" not in response.text
    assert "hidden" not in response.text


def test_developer_role_cannot_admin_delete_memory_record(monkeypatch):
    async def fail_admin_delete_memory_record(conn, **kwargs):
        raise AssertionError("developer role must not reach admin memory delete repository")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.admin_delete_memory_record", fail_admin_delete_memory_record)
    client = TestClient(create_app())
    developer_headers = admin_headers()
    developer_headers["X-AI-Roles"] = "developer"

    response = client.delete(
        "/api/ai/admin/memory/records/mem-b?workspace_id=workspace-a",
        headers=developer_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_memory_admin"


def test_delete_memory_record_already_deleted_returns_404_without_audit(monkeypatch):
    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_delete_memory_record(conn, *, tenant_id, workspace_id, user_id, agent_id, session_id, record_id):
        return None

    async def fail_audit(conn, **kwargs):
        raise AssertionError("already deleted memory record must not write duplicate audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fake_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fail_audit)
    client = TestClient(create_app())

    response = client.delete(
        "/api/ai/memory/records/mem-a?workspace_id=workspace-a&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "memory_record_not_found"


def test_delete_memory_record_redacts_secret_like_reason_from_audit(monkeypatch):
    calls = []

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_delete_memory_record(conn, *, tenant_id, workspace_id, user_id, agent_id, session_id, record_id):
        return {
            "id": record_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": "general-agent",
            "session_id": "session-a",
            "record_type": "session_summary",
            "content": "User prefers concise answers.",
            "metadata_json": {},
            "status": "deleted",
            "deleted_at": "2026-06-02T12:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(kwargs)
        return "audit-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fake_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.delete(
        (
            "/api/ai/memory/records/mem-a?workspace_id=workspace-a&session_id=session-a&reason=cleanup%20"
            "token=hidden-token%20password:%20hidden-password%20"
            "client_secret=client-secret%20openai_api_key=sk-openai%20id_token=id-token"
        ),
        headers=headers(),
    )

    assert response.status_code == 200
    reason = calls[0]["payload_json"]["reason"]
    assert reason == "cleanup token=[redacted-secret] password=[redacted-secret] client_secret=[redacted-secret] openai_api_key=[redacted-secret] id_token=[redacted-secret]"
    assert "hidden-token" not in str(calls[0])
    assert "hidden-password" not in str(calls[0])
    assert "client-secret" not in str(calls[0])
    assert "sk-openai" not in str(calls[0])
    assert "id-token" not in str(calls[0])


def test_delete_memory_record_returns_404_for_foreign_or_missing_record(monkeypatch):
    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        return {"id": session_id, "workspace_id": "workspace-a", "agent_id": "general-agent"}

    async def fake_delete_memory_record(conn, *, tenant_id, workspace_id, user_id, agent_id, session_id, record_id):
        return None

    async def fail_audit(conn, **kwargs):
        raise AssertionError("missing memory record must not write audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.get_authorized_session", fake_get_authorized_session)
    monkeypatch.setattr("app.routes.context.repositories.delete_memory_record", fake_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fail_audit)
    client = TestClient(create_app())

    response = client.delete(
        "/api/ai/memory/records/mem-foreign?workspace_id=workspace-a&session_id=session-a",
        headers=headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "memory_record_not_found"


def test_admin_delete_memory_record_soft_deletes_same_tenant_record_and_writes_audit(monkeypatch):
    calls = []

    async def fake_admin_delete_memory_record(conn, *, tenant_id, workspace_id, record_id):
        calls.append(("delete", tenant_id, workspace_id, record_id))
        return {
            "id": record_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": "user-b",
            "agent_id": "general-agent",
            "session_id": "session-b",
            "record_type": "user_preference",
            "content": "Use short answers.",
            "metadata_json": {"source": "test"},
            "status": "deleted",
            "deleted_at": "2026-06-02T12:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-admin"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.admin_delete_memory_record", fake_admin_delete_memory_record)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.delete(
        (
            "/api/ai/admin/memory/records/mem-b?workspace_id=workspace-a&reason=retention-cleanup%20"
            "client_secret=client-secret%20openai_api_key=sk-openai%20id_token=id-token"
        ),
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()["memory_record"]
    assert body["memory_record_id"] == "mem-b"
    assert body["user_id"] == "user-b"
    assert body["status"] == "deleted"
    assert calls[0] == ("delete", "tenant-a", "workspace-a", "mem-b")
    assert calls[1][1]["user_id"] == "admin-a"
    assert calls[1][1]["action"] == "admin.memory.record.deleted"
    assert calls[1][1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "target_user_id": "user-b",
        "agent_id": "general-agent",
        "session_id": "session-b",
        "record_type": "user_preference",
        "reason": "retention-cleanup client_secret=[redacted-secret] openai_api_key=[redacted-secret] id_token=[redacted-secret]",
    }
    assert "client-secret" not in str(calls[1])
    assert "sk-openai" not in str(calls[1])
    assert "id-token" not in str(calls[1])


def test_admin_cleanup_expired_memory_records_soft_deletes_and_audits_without_content(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_cleanup_expired_memory_records(conn, *, tenant_id, workspace_id, limit):
        calls.append(("cleanup", tenant_id, workspace_id, limit))
        return [
            {
                "id": "mem-expired",
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": "user-b",
                "agent_id": "general-agent",
                "session_id": "session-b",
                "record_type": "session_summary",
                "content": "Expired secret content",
                "metadata_json": {"api_key": "hidden"},
                "status": "deleted",
                "expires_at": "2026-06-01T12:00:00Z",
                "deleted_at": "2026-06-03T12:00:00Z",
                "created_at": "2026-05-31T12:00:00Z",
            }
        ]

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-retention"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.cleanup_expired_memory_records", fake_cleanup_expired_memory_records)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/retention/cleanup?workspace_id=workspace-a&limit=25",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_count"] == 1
    assert body["memory_records"] == [
        {
            "memory_record_id": "mem-expired",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-b",
            "agent_id": "general-agent",
            "session_id": "session-b",
            "record_type": "session_summary",
            "status": "deleted",
            "deleted_at": "2026-06-03T12:00:00Z",
            "created_at": "2026-05-31T12:00:00Z",
        }
    ]
    assert calls[0] == ("workspace", "tenant-a", "workspace-a")
    assert calls[1] == ("cleanup", "tenant-a", "workspace-a", 25)
    assert calls[2][1]["action"] == "admin.memory.retention.cleanup"
    assert calls[2][1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "deleted_count": 1,
        "memory_record_ids": ["mem-expired"],
        "target_user_ids": ["user-b"],
        "reason": "retention_expired",
    }
    assert "Expired secret content" not in response.text
    assert "hidden" not in response.text
    assert "Expired secret content" not in str(calls[2])
    assert "hidden" not in str(calls[2])


def test_admin_cleanup_expired_memory_records_rejects_non_memory_admin(monkeypatch):
    async def fail_cleanup_expired_memory_records(conn, **kwargs):
        raise AssertionError("non memory admin must not cleanup expired memory records")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.cleanup_expired_memory_records", fail_cleanup_expired_memory_records)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/retention/cleanup?workspace_id=workspace-a",
        headers=headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_memory_admin"


def test_admin_cleanup_expired_memory_records_rejects_invalid_limit(monkeypatch):
    async def fail_cleanup_expired_memory_records(conn, **kwargs):
        raise AssertionError("invalid cleanup limit must fail before deleting records")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.cleanup_expired_memory_records", fail_cleanup_expired_memory_records)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/ai/admin/memory/retention/cleanup?workspace_id=workspace-a&limit=0",
        headers=admin_headers(),
    )

    assert response.status_code == 422


def test_admin_cleanup_expired_memory_records_returns_404_for_missing_workspace(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))
        raise RepositoryNotFoundError("workspace_not_found")

    async def fail_cleanup_expired_memory_records(conn, **kwargs):
        raise AssertionError("missing workspace must not cleanup expired memory records")

    async def fail_append_audit_log(conn, **kwargs):
        raise AssertionError("missing workspace cleanup must not write audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.cleanup_expired_memory_records", fail_cleanup_expired_memory_records)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fail_append_audit_log)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/ai/admin/memory/retention/cleanup?workspace_id=missing-workspace&limit=25",
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "workspace_not_found"
    assert calls == [("workspace", "tenant-a", "missing-workspace")]


def test_admin_list_memory_records_returns_operational_projection_without_content(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_list_admin_memory_records(conn, *, tenant_id, workspace_id, user_id, status, limit):
        calls.append(("list", tenant_id, workspace_id, user_id, status, limit))
        return [
            {
                "id": "mem-ops",
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": "user-b",
                "agent_id": "general-agent",
                "session_id": "session-b",
                "record_type": "session_summary",
                "content": "Hidden operator content with client_secret=secret",
                "metadata_json": {"api_key": "hidden-key", "source": "test"},
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
                "updated_at": "2026-06-03T12:30:00Z",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_records", fake_list_admin_memory_records, raising=False)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/memory/records?workspace_id=workspace-a&user_id=user-b&status=active&limit=25",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "memory_records": [
            {
                "memory_record_id": "mem-ops",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-b",
                "agent_id": "general-agent",
                "session_id": "session-b",
                "record_type": "session_summary",
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
                "updated_at": "2026-06-03T12:30:00Z",
            }
        ],
        "summary": {
            "workspace_id": "workspace-a",
            "status": "active",
            "returned_count": 1,
            "limit": 25,
        },
    }
    assert calls == [
        ("workspace", "tenant-a", "workspace-a"),
        ("list", "tenant-a", "workspace-a", "user-b", "active", 25),
    ]
    assert "content" not in response.text
    assert "metadata" not in response.text
    assert "client_secret" not in response.text
    assert "hidden-key" not in response.text


def test_admin_list_memory_records_rejects_non_memory_admin(monkeypatch):
    async def fail_list_admin_memory_records(conn, **kwargs):
        raise AssertionError("non memory admin must not reach admin memory projection")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_records", fail_list_admin_memory_records, raising=False)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/memory/records?workspace_id=workspace-a",
        headers=headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_memory_admin"


def test_admin_list_memory_records_rejects_unsafe_query_ids_with_422(monkeypatch):
    async def fail_list_admin_memory_records(conn, **kwargs):
        raise AssertionError("unsafe query ids must fail before repository access")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_records", fail_list_admin_memory_records, raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    bad_user = client.get(
        "/api/ai/admin/memory/records?workspace_id=workspace-a&user_id=../bad",
        headers=admin_headers(),
    )
    bad_workspace = client.get(
        "/api/ai/admin/memory/records?workspace_id=../bad",
        headers=admin_headers(),
    )

    assert bad_user.status_code == 422
    assert bad_user.json()["detail"] == "user_id contains unsupported characters"
    assert bad_workspace.status_code == 422
    assert bad_workspace.json()["detail"] == "workspace_id contains unsupported characters"


def test_admin_list_memory_policies_returns_operational_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_list_admin_memory_policies(conn, *, tenant_id, workspace_id, user_id, agent_id, limit):
        calls.append(("policies", tenant_id, workspace_id, user_id, agent_id, limit))
        return [
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "agent_id": "qa-word-review",
                "memory_enabled": False,
                "long_term_memory_enabled": True,
                "retention_days": 30,
                "redaction_mode": "strict",
                "source": "stored",
                "reason": "user opt-out client_secret=[redacted-secret]",
                "updated_by": "user-b",
                "updated_at": "2026-06-05T00:00:00Z",
                "content": "must not leak",
                "metadata_json": {"api_key": "sk-secret"},
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_policies", fake_list_admin_memory_policies, raising=False)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/memory/policies?workspace_id=workspace-a&user_id=user-b&limit=25",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "memory_policies": [
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-b",
                "agent_id": "document-review",
                "memory_enabled": False,
                "long_term_memory_enabled": False,
                "retention_days": 30,
                "redaction_mode": "strict",
                "source": "stored",
                "reason": "user opt-out client_secret=[redacted-secret]",
                "updated_by": "user-b",
                "updated_at": "2026-06-05T00:00:00Z",
            }
        ],
        "summary": {
            "workspace_id": "workspace-a",
            "user_id": "user-b",
            "agent_id": None,
            "returned_count": 1,
            "limit": 25,
        },
    }
    assert calls == [
        ("workspace", "tenant-a", "workspace-a"),
        ("policies", "tenant-a", "workspace-a", "user-b", None, 25),
    ]
    assert "must not leak" not in response.text
    assert "sk-secret" not in response.text
    assert "qa-word-review" not in response.text


def test_admin_list_memory_policies_rejects_non_memory_admin(monkeypatch):
    async def fail_list_admin_memory_policies(conn, **kwargs):
        raise AssertionError("non-admin must not reach policy inventory repository")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_policies", fail_list_admin_memory_policies, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/memory/policies?workspace_id=workspace-a", headers=headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_memory_admin"


def test_admin_list_memory_policies_rejects_unsafe_query_ids_with_422(monkeypatch):
    async def fail_list_admin_memory_policies(conn, **kwargs):
        raise AssertionError("unsafe query ids must fail before policy inventory access")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_policies", fail_list_admin_memory_policies, raising=False)
    client = TestClient(create_app())

    bad_workspace = client.get("/api/ai/admin/memory/policies?workspace_id=../bad", headers=admin_headers())
    bad_user = client.get("/api/ai/admin/memory/policies?workspace_id=workspace-a&user_id=../bad", headers=admin_headers())
    bad_agent = client.get("/api/ai/admin/memory/policies?workspace_id=workspace-a&agent_id=../bad", headers=admin_headers())

    assert bad_workspace.status_code == 422
    assert bad_workspace.json()["detail"] == "workspace_id contains unsupported characters"
    assert bad_user.status_code == 422
    assert bad_user.json()["detail"] == "user_id contains unsupported characters"
    assert bad_agent.status_code == 422
    assert bad_agent.json()["detail"] == "agent_id contains unsupported characters"


def test_admin_list_memory_policies_returns_404_for_missing_or_foreign_workspace(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))
        raise RepositoryNotFoundError("workspace_not_found")

    async def fail_list_admin_memory_policies(conn, **kwargs):
        raise AssertionError("missing or foreign workspace must not reach policy inventory")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_policies", fail_list_admin_memory_policies, raising=False)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/memory/policies?workspace_id=foreign-workspace",
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "workspace_not_found"
    assert calls == [("workspace", "tenant-a", "foreign-workspace")]


def test_admin_list_memory_policies_returns_404_for_missing_or_foreign_agent(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        return None

    async def fail_list_admin_memory_policies(conn, **kwargs):
        raise AssertionError("missing or foreign agent must not reach policy inventory")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_policies", fail_list_admin_memory_policies, raising=False)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/memory/policies?workspace_id=workspace-a&agent_id=document-review",
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "agent_not_found"
    assert calls == [
        ("workspace", "tenant-a", "workspace-a"),
        ("agent", "tenant-a", "qa-word-review"),
    ]


def test_update_memory_policy_allows_user_self_opt_out_and_audits(monkeypatch):
    calls = []
    raw_openai = "sk-strictaudit1234567890"
    raw_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJhdWRpdCJ9.signature1234567890"

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_set_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": kwargs["memory_enabled"],
            "long_term_memory_enabled": kwargs["long_term_memory_enabled"],
            "retention_days": kwargs["retention_days"],
            "redaction_mode": kwargs["redaction_mode"],
            "source": "stored",
            "reason": kwargs["reason"],
            "updated_by": kwargs["updated_by"],
            "updated_at": "2026-06-05T00:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-self-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fake_set_memory_policy, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "strict",
            "reason": (
                "self opt-out client_secret=client-secret openai_api_key=sk-openai id_token=id-token "
                f"{raw_openai} {raw_jwt}"
            ),
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_policy"]
    assert body["user_id"] == "user-a"
    assert body["memory_enabled"] is False
    assert body["long_term_memory_enabled"] is False
    assert body["redaction_mode"] == "strict"
    assert (
        body["reason"]
        == "self opt-out client_secret=[redacted-secret] openai_api_key=[redacted-secret] "
        "id_token=[redacted-secret] [redacted-secret] [redacted-secret]"
    )
    assert calls[0] == ("workspace", "tenant-a", "workspace-a")
    assert calls[1] == (
        "user",
        {"tenant_id": "tenant-a", "user_id": "user-a", "display_name": "User A"},
    )
    assert calls[2][1]["user_id"] == "user-a"
    assert calls[2][1]["updated_by"] == "user-a"
    assert calls[2][1]["redaction_mode"] == "strict"
    assert calls[3][1]["action"] == "memory.policy.updated"
    assert calls[3][1]["payload_json"]["target_user_id"] == "user-a"
    assert calls[3][1]["payload_json"]["redaction_mode"] == "strict"
    assert "client-secret" not in str(calls)
    assert "sk-openai" not in str(calls)
    assert "id-token" not in str(calls)
    assert raw_openai not in str(calls)
    assert raw_jwt not in str(calls)


def test_update_memory_policy_rejects_long_term_enable_for_user(monkeypatch):
    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("long-term memory must remain fail-closed for user self policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "memory_enabled": True,
            "long_term_memory_enabled": True,
            "retention_days": 90,
            "reason": "enable long term",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "long_term_memory_not_available"


def test_update_memory_policy_rejects_unsafe_body_ids_with_422(monkeypatch):
    async def fail_ensure_workspace(conn, **kwargs):
        raise AssertionError("unsafe body ids must fail before workspace validation")

    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("unsafe body ids must fail before repository write")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fail_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    bad_workspace = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "../bad",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "reason": "unsafe workspace",
        },
    )
    bad_agent = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "../bad",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "reason": "unsafe agent",
        },
    )

    assert bad_workspace.status_code == 422
    assert bad_agent.status_code == 422


def test_update_memory_policy_rejects_invalid_redaction_mode_before_write(monkeypatch):
    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("invalid redaction mode must fail before repository write")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "off",
            "reason": "invalid mode",
        },
    )

    assert response.status_code == 422


def test_update_memory_policy_returns_404_for_missing_or_foreign_workspace(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))
        raise RepositoryNotFoundError("workspace_not_found")

    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("missing or foreign workspace must not write user memory policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "foreign-workspace",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "reason": "workspace scoped opt-out",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "workspace_not_found"
    assert calls == [("workspace", "tenant-a", "foreign-workspace")]


def test_update_memory_policy_returns_404_for_missing_or_foreign_agent(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        return None

    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("missing or foreign agent must not write user memory policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "missing-agent",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "reason": "agent scoped opt-out",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "agent_not_found"
    assert calls == [
        ("workspace", "tenant-a", "workspace-a"),
        ("agent", "tenant-a", "missing-agent"),
    ]


def test_get_memory_policy_maps_public_agent_id_before_lookup(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        return {"id": agent_id, "tenant_id": tenant_id, "status": "active"}

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "strict",
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "user-a",
            "updated_at": "2026-06-05T00:00:00Z",
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/memory/policy?workspace_id=workspace-a&agent_id=document-review",
        headers=headers(),
    )

    assert response.status_code == 200
    body = response.json()["memory_policy"]
    assert body["agent_id"] == "document-review"
    assert body["redaction_mode"] == "strict"
    assert calls == [
        ("workspace", "tenant-a", "workspace-a"),
        ("agent", "tenant-a", "qa-word-review"),
        (
            "policy",
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "qa-word-review",
            },
        )
    ]
    assert "qa-word-review" not in response.text


def test_get_memory_policy_returns_404_for_missing_or_foreign_workspace(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))
        raise RepositoryNotFoundError("workspace_not_found")

    async def fail_get_effective_memory_policy(conn, **kwargs):
        raise AssertionError("missing or foreign workspace must not read memory policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fail_get_effective_memory_policy)
    client = TestClient(create_app())

    response = client.get("/api/ai/memory/policy?workspace_id=foreign-workspace", headers=headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "workspace_not_found"
    assert calls == [("workspace", "tenant-a", "foreign-workspace")]


def test_get_memory_policy_rejects_unsafe_query_ids_with_422(monkeypatch):
    async def fail_get_effective_memory_policy(conn, **kwargs):
        raise AssertionError("unsafe memory policy query ids must fail before repository access")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.get_effective_memory_policy", fail_get_effective_memory_policy)
    client = TestClient(create_app())

    bad_workspace = client.get("/api/ai/memory/policy?workspace_id=../bad", headers=headers())
    bad_agent = client.get("/api/ai/memory/policy?workspace_id=workspace-a&agent_id=../bad", headers=headers())

    assert bad_workspace.status_code == 422
    assert bad_workspace.json()["detail"] == "workspace_id contains unsupported characters"
    assert bad_agent.status_code == 422
    assert bad_agent.json()["detail"] == "agent_id contains unsupported characters"


def test_update_memory_policy_maps_public_agent_id_before_writing(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_ensure_user(conn, **kwargs):
        calls.append(("user", kwargs))

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        if agent_id == "qa-word-review":
            return {"id": agent_id, "tenant_id": tenant_id, "status": "active"}
        return None

    async def fake_set_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": kwargs["memory_enabled"],
            "long_term_memory_enabled": kwargs["long_term_memory_enabled"],
            "retention_days": kwargs["retention_days"],
            "redaction_mode": kwargs["redaction_mode"],
            "source": "stored",
            "reason": kwargs["reason"],
            "updated_by": kwargs["updated_by"],
            "updated_at": "2026-06-05T00:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-public-agent-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fake_set_memory_policy, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "document-review",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "strict",
            "reason": "agent scoped opt-out",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_policy"]
    assert body["agent_id"] == "document-review"
    assert body["redaction_mode"] == "strict"
    assert ("agent", "tenant-a", "qa-word-review") in calls
    policy_call = next(call for call in calls if call[0] == "policy")
    audit_call = next(call for call in calls if call[0] == "audit")
    assert policy_call[1]["agent_id"] == "qa-word-review"
    assert policy_call[1]["redaction_mode"] == "strict"
    assert audit_call[1]["payload_json"]["agent_id"] == "document-review"
    assert "qa-word-review" not in response.text


def test_admin_set_memory_policy_maps_public_agent_id_before_writing(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_get_user(conn, *, tenant_id, user_id):
        calls.append(("target_user", tenant_id, user_id))
        return {"id": user_id, "tenant_id": tenant_id, "display_name": "User A", "status": "active"}

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        if agent_id == "qa-word-review":
            return {"id": agent_id, "tenant_id": tenant_id, "status": "active"}
        return None

    async def fake_set_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": kwargs["memory_enabled"],
            "long_term_memory_enabled": kwargs["long_term_memory_enabled"],
            "retention_days": kwargs["retention_days"],
            "redaction_mode": kwargs["redaction_mode"],
            "source": "stored",
            "reason": kwargs["reason"],
            "updated_by": kwargs["updated_by"],
            "updated_at": "2026-06-05T00:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-admin-public-agent-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_user", fake_get_user, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fake_set_memory_policy, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/memory/policies/user-a",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "document-review",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "strict",
            "reason": "admin agent scoped opt-out",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_policy"]
    assert body["agent_id"] == "document-review"
    assert body["redaction_mode"] == "strict"
    assert ("agent", "tenant-a", "qa-word-review") in calls
    policy_call = next(call for call in calls if call[0] == "policy")
    audit_call = next(call for call in calls if call[0] == "audit")
    assert policy_call[1]["agent_id"] == "qa-word-review"
    assert policy_call[1]["redaction_mode"] == "strict"
    assert audit_call[1]["payload_json"]["agent_id"] == "document-review"
    assert "qa-word-review" not in response.text


def test_admin_set_memory_policy_rejects_long_term_enable_until_governance_complete(monkeypatch):
    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("long-term memory must remain closed until retention/redaction governance is complete")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/memory/policies/user-a",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "general-agent",
            "memory_enabled": True,
            "long_term_memory_enabled": True,
            "retention_days": 90,
            "reason": "enable",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "long_term_memory_not_available"


def test_admin_set_memory_policy_rejects_invalid_redaction_mode_before_write(monkeypatch):
    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("invalid redaction mode must fail before admin repository write")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/memory/policies/user-a",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "off",
            "reason": "invalid mode",
        },
    )

    assert response.status_code == 422


def test_admin_set_memory_policy_returns_404_for_missing_target_user(monkeypatch):
    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_get_user(conn, *, tenant_id, user_id):
        return None

    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("missing target user must not write memory policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_user", fake_get_user, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/memory/policies/missing-user",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "user_not_found"


def test_admin_set_memory_policy_returns_404_for_missing_or_foreign_agent(monkeypatch):
    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_get_user(conn, *, tenant_id, user_id):
        return {"id": user_id, "tenant_id": tenant_id, "display_name": "User A", "status": "active"}

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        return None

    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("missing or foreign agent must not write memory policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_user", fake_get_user, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/memory/policies/user-a",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "foreign-agent",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "agent_not_found"


def test_admin_set_memory_policy_updates_policy_and_writes_audit(monkeypatch):
    calls = []
    raw_openai = "sk-adminstrictaudit1234567890"
    raw_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJhZG1pbiJ9.signature1234567890"

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_set_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": kwargs["memory_enabled"],
            "long_term_memory_enabled": kwargs["long_term_memory_enabled"],
            "retention_days": kwargs["retention_days"],
            "redaction_mode": kwargs["redaction_mode"],
            "source": "stored",
            "reason": kwargs["reason"],
            "updated_by": kwargs["updated_by"],
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-policy"

    async def fake_get_user(conn, *, tenant_id, user_id):
        calls.append(("target_user", tenant_id, user_id))
        return {"id": user_id, "tenant_id": tenant_id, "display_name": "User A", "status": "active"}

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        return {"id": agent_id, "tenant_id": tenant_id, "status": "active"}

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_user", fake_get_user, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fake_set_memory_policy, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/admin/memory/policies/user-a",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "general-agent",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "redaction_mode": "strict",
            "reason": (
                "opt out token=hidden client_secret=client-secret openai_api_key=sk-openai id_token=id-token "
                f"{raw_openai} {raw_jwt}"
            ),
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_policy"]
    assert body["user_id"] == "user-a"
    assert body["memory_enabled"] is False
    assert body["long_term_memory_enabled"] is False
    assert body["retention_days"] == 30
    assert body["redaction_mode"] == "strict"
    assert (
        body["reason"]
        == "opt out token=[redacted-secret] client_secret=[redacted-secret] openai_api_key=[redacted-secret] "
        "id_token=[redacted-secret] [redacted-secret] [redacted-secret]"
    )
    assert calls[0] == ("workspace", "tenant-a", "workspace-a")
    assert calls[1] == ("target_user", "tenant-a", "user-a")
    assert calls[2] == ("agent", "tenant-a", "general-agent")
    assert (
        calls[3][1]["reason"]
        == "opt out token=[redacted-secret] client_secret=[redacted-secret] openai_api_key=[redacted-secret] "
        "id_token=[redacted-secret] [redacted-secret] [redacted-secret]"
    )
    assert calls[3][1]["redaction_mode"] == "strict"
    assert calls[4][1]["action"] == "admin.memory.policy.updated"
    assert calls[4][1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "target_user_id": "user-a",
        "agent_id": "general-agent",
        "memory_enabled": False,
        "long_term_memory_enabled": False,
        "retention_days": 30,
        "redaction_mode": "strict",
        "reason": (
            "opt out token=[redacted-secret] client_secret=[redacted-secret] openai_api_key=[redacted-secret] "
            "id_token=[redacted-secret] [redacted-secret] [redacted-secret]"
        ),
    }
    assert "hidden" not in str(calls)
    assert "client-secret" not in str(calls)
    assert "sk-openai" not in str(calls)
    assert "id-token" not in str(calls)
    assert raw_openai not in str(calls)
    assert raw_jwt not in str(calls)


def test_admin_preview_memory_redaction_returns_safe_projection_and_writes_audit(monkeypatch):
    calls = []
    raw_openai = "sk-previewstrict1234567890"
    raw_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJwcmV2aWV3In0.signature1234567890"
    raw_private_marker = "private_payload={raw}"
    raw_storage_marker = "raw_storage_key=s3://bucket/key storage_key=s3://bucket/key"
    raw_sandbox_marker = "sandbox_workdir=/tmp/ai-platform-sandbox-workspaces/tenant/run"
    raw_camel_marker = "rawStorageKey=s3://bucket/key executorPrivatePayload={raw} sandboxWorkdir=workspace/run"

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        return {"id": agent_id, "tenant_id": tenant_id, "status": "active"}

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "document-review",
            "redaction_mode": "strict",
            "content": (
                f"Draft summary token=hidden {raw_openai} {raw_jwt} "
                f"{raw_private_marker} {raw_storage_marker} {raw_sandbox_marker} {raw_camel_marker}"
            ),
            "metadata": {
                "api_key": "metadata-secret",
                "nested": {
                    "contact": "owner@example.com",
                    "runtime": f"{raw_private_marker} {raw_storage_marker} {raw_sandbox_marker} {raw_camel_marker}",
                },
            },
            "reason": (
                f"preview client_secret=client-secret {raw_openai} "
                f"{raw_storage_marker} {raw_camel_marker}"
            ),
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["schema_version"] == "ai-platform.memory-redaction-preview.v1"
    assert body["tenant_id"] == "tenant-a"
    assert body["workspace_id"] == "workspace-a"
    assert body["agent_id"] == "document-review"
    assert body["redaction_mode"] == "strict"
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {
        "api_key": "[redacted-secret]",
        "nested": {
            "contact": "[redacted-email]",
            "runtime": "[redacted-private]",
        },
    }
    assert body["reason_preview"] == "[redacted-private]"
    assert body["changes"] == {
        "content_redacted": True,
        "metadata_redacted": True,
        "reason_redacted": True,
    }
    assert body["audit"] == {
        "action": "admin.memory.redaction.previewed",
        "audit_id": "audit-redaction-preview",
    }
    audit_call = next(call for call in calls if call[0] == "audit")
    assert audit_call[1]["action"] == "admin.memory.redaction.previewed"
    assert audit_call[1]["target_type"] == "memory_redaction_policy"
    assert audit_call[1]["target_id"] == "workspace-a"
    assert audit_call[1]["payload_json"] == {
        "workspace_id": "workspace-a",
        "agent_id": "document-review",
        "redaction_mode": "strict",
        "content_redacted": True,
        "metadata_redacted": True,
        "reason_redacted": True,
        "reason": "[redacted-private]",
    }
    assert calls[0] == ("workspace", "tenant-a", "workspace-a")
    assert calls[1] == ("agent", "tenant-a", "qa-word-review")
    serialized = str(response.json()) + str(calls)
    assert "qa-word-review" not in response.text
    assert "metadata-secret" not in serialized
    assert "owner@example.com" not in serialized
    assert "client-secret" not in serialized
    assert "hidden" not in serialized
    assert raw_openai not in serialized
    assert raw_jwt not in serialized
    assert raw_private_marker not in serialized
    assert raw_storage_marker not in serialized
    assert raw_sandbox_marker not in serialized
    assert raw_camel_marker not in serialized
    assert "private_payload" not in serialized
    assert "raw_storage_key" not in serialized
    assert "storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "rawStorageKey" not in serialized
    assert "executorPrivatePayload" not in serialized
    assert "sandboxWorkdir" not in serialized
    assert "/tmp/ai-platform-sandbox-workspaces" not in serialized


def test_admin_preview_memory_redaction_standard_mode_still_blocks_provider_secrets_from_projection(monkeypatch):
    calls = []
    raw_openai = "sk-standardpreview1234567890"
    raw_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJzdGFuZGFyZCJ9.signature1234567890"

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-standard-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": f"standard preview {raw_openai} {raw_jwt}",
            "reason": f"standard reason {raw_openai} {raw_jwt}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["redaction_mode"] == "standard"
    assert body["content_preview"] == "standard preview [redacted-secret] [redacted-secret]"
    assert body["reason_preview"] == "standard reason [redacted-secret] [redacted-secret]"
    audit_call = calls[0][1]
    assert audit_call["payload_json"]["redaction_mode"] == "standard"
    assert audit_call["payload_json"]["reason"] == "standard reason [redacted-secret] [redacted-secret]"
    serialized = str(response.json()) + str(calls)
    assert raw_openai not in serialized
    assert raw_jwt not in serialized


def test_admin_preview_memory_redaction_redacts_camel_case_private_markers_from_text_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-camel-marker-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": "rawStorageKey=s3://bucket/key executorPrivatePayload={raw} sandboxWorkdir=workspace/run",
            "reason": "storageKey=s3://bucket/key runtimePrivatePayload={raw} privatePayload={raw}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert "rawStorageKey" not in serialized
    assert "storageKey" not in serialized
    assert "executorPrivatePayload" not in serialized
    assert "runtimePrivatePayload" not in serialized
    assert "privatePayload" not in serialized
    assert "sandboxWorkdir" not in serialized


def test_admin_preview_memory_redaction_redacts_object_storage_values_from_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-storage-value-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": "raw object key s3://bucket/private/key",
            "metadata": {"note": "minio://tenant/private/key"},
            "reason": "storage probe s3://bucket/private/key",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"note": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert "s3://" not in serialized
    assert "minio://" not in serialized
    assert "bucket/private/key" not in serialized
    assert "tenant/private/key" not in serialized


def test_admin_preview_memory_redaction_redacts_relative_skill_storage_keys_from_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-relative-storage-redaction-preview"

    storage_key = "skills/general-chat/versions/hash123/package.zip"
    custom_storage_key = "skills/custom-skill/versions/hash456/package.zip"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": f"skill package {storage_key}",
            "metadata": {"package": custom_storage_key},
            "reason": f"operator package probe {storage_key}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"package": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert storage_key not in serialized
    assert custom_storage_key not in serialized
    assert "skills/general-chat" not in serialized
    assert "skills/custom-skill" not in serialized
    assert "package.zip" not in serialized


def test_admin_preview_memory_redaction_redacts_url_encoded_storage_values_from_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-encoded-storage-redaction-preview"

    encoded_object_key = "s3%3A%2F%2Fbucket%2Fprivate%2Fkey"
    encoded_package_key = "skills%2Fcustom-skill%2Fversions%2Fhash456%2Fpackage.zip"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": f"encoded object key {encoded_object_key}",
            "metadata": {"package": encoded_package_key},
            "reason": f"encoded package probe {encoded_package_key}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"package": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert encoded_object_key not in serialized
    assert encoded_package_key not in serialized
    assert "s3://" not in serialized
    assert "bucket/private/key" not in serialized
    assert "skills/custom-skill" not in serialized


def test_admin_preview_memory_redaction_redacts_deep_url_encoded_storage_values_from_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-deep-encoded-storage-redaction-preview"

    deep_encoded_object_key = "s3%25253A%25252F%25252Fbucket%25252Fprivate%25252Fkey"
    deep_encoded_package_key = "skills%25252Fcustom-skill%25252Fversions%25252Fhash456%25252Fpackage.zip"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": f"deep encoded object key {deep_encoded_object_key}",
            "metadata": {"package": deep_encoded_package_key},
            "reason": f"deep encoded package probe {deep_encoded_package_key}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"package": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert deep_encoded_object_key not in serialized
    assert deep_encoded_package_key not in serialized
    assert "s3://" not in serialized
    assert "bucket/private/key" not in serialized
    assert "skills/custom-skill" not in serialized


def test_admin_preview_memory_redaction_redacts_url_encoded_internal_ids_from_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-encoded-internal-id-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": "encoded internal id general%2Dchat",
            "metadata": {"skill": "qa%2Dfile%2Dreviewer"},
            "reason": "encoded internal id ragflow%2Dknowledge%2Dsearch",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"skill": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert "general%2Dchat" not in serialized
    assert "qa%2Dfile%2Dreviewer" not in serialized
    assert "ragflow%2Dknowledge%2Dsearch" not in serialized
    assert "general-chat" not in serialized
    assert "qa-file-reviewer" not in serialized
    assert "ragflow-knowledge-search" not in serialized


def test_admin_preview_memory_redaction_redacts_url_encoded_secret_like_values_from_projection(monkeypatch):
    calls = []
    encoded_openai = "sk%2Dencodedsecret1234567890"
    encoded_jwt = "eyJhbGciOiJIUzI1NiJ9%2EeyJhdWQiOiJ0ZXN0In0%2Esignature1234567890"
    encoded_bearer = "Bearer%20abc.def.secretToken123456"

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-encoded-secret-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": f"encoded provider key {encoded_openai}",
            "metadata": {"jwt": encoded_jwt},
            "reason": f"encoded bearer {encoded_bearer}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"jwt": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert encoded_openai not in serialized
    assert encoded_jwt not in serialized
    assert encoded_bearer not in serialized
    assert "sk-encodedsecret1234567890" not in serialized
    assert "signature1234567890" not in serialized
    assert "abc.def.secretToken123456" not in serialized


def test_admin_preview_memory_redaction_redacts_form_encoded_secret_like_values_from_projection(monkeypatch):
    calls = []
    encoded_bearer = "Bearer+abc.def.secretToken123456"

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-form-encoded-secret-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": f"encoded auth value {encoded_bearer}",
            "metadata": {"note": encoded_bearer},
            "reason": f"encoded auth value {encoded_bearer}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"note": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert encoded_bearer not in serialized
    assert "Bearer abc.def.secretToken123456" not in serialized
    assert "abc.def.secretToken123456" not in serialized


def test_admin_preview_memory_redaction_fails_closed_when_url_decode_budget_exhausts(monkeypatch):
    calls = []
    over_budget_layers = MEMORY_PREVIEW_URL_DECODE_DEPTH + 1
    over_budget_object_key = url_encode_layers("s3://bucket/private/key", over_budget_layers)
    over_budget_package_key = url_encode_layers("skills/custom-skill/versions/hash456/package.zip", over_budget_layers)

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-over-budget-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": f"over budget storage {over_budget_object_key}",
            "metadata": {"package": over_budget_package_key},
            "reason": f"over budget package {over_budget_package_key}",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == "[redacted-private]"
    assert body["metadata_preview"] == {"package": "[redacted-private]"}
    assert body["reason_preview"] == "[redacted-private]"
    assert calls[0][1]["payload_json"]["reason"] == "[redacted-private]"
    serialized = str(response.json()) + str(calls)
    assert over_budget_object_key not in serialized
    assert over_budget_package_key not in serialized
    assert "s3://" not in serialized
    assert "skills/custom-skill" not in serialized


def test_admin_preview_memory_redaction_rejects_url_encoded_private_metadata_keys(monkeypatch):
    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        return "audit-encoded-metadata-key-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "metadata": {
                "raw%5Fstorage%5Fkey": "safe",
                "sandbox%5Fworkdir": "safe",
                "general%2Dchat": "safe",
                "public_note": "safe",
            },
        },
    )

    assert response.status_code == 200
    metadata = response.json()["memory_redaction_preview"]["metadata_preview"]
    assert metadata == {"public_note": "safe"}
    serialized = str(response.json())
    assert "raw%5Fstorage%5Fkey" not in serialized
    assert "sandbox%5Fworkdir" not in serialized
    assert "general%2Dchat" not in serialized
    assert "raw_storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "general-chat" not in serialized


def test_admin_preview_memory_redaction_redacts_internal_agent_and_skill_ids_from_text_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-internal-id-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "content": "uses general-chat qa-word-review qa-file-reviewer baoyu-translate sop-assistant",
            "reason": "operator note general-chat qa-word-review qa-file-reviewer baoyu-translate sop-assistant",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_redaction_preview"]
    assert body["content_preview"] == (
        "uses [redacted-internal-id] [redacted-internal-id] [redacted-internal-id] "
        "[redacted-internal-id] [redacted-internal-id]"
    )
    assert body["reason_preview"] == (
        "operator note [redacted-internal-id] [redacted-internal-id] [redacted-internal-id] "
        "[redacted-internal-id] [redacted-internal-id]"
    )
    assert calls[0][1]["payload_json"]["reason"] == body["reason_preview"]
    serialized = str(response.json()) + str(calls)
    assert "general-chat" not in serialized
    assert "qa-word-review" not in serialized
    assert "qa-file-reviewer" not in serialized
    assert "baoyu-translate" not in serialized
    assert "sop-assistant" not in serialized


def test_admin_preview_memory_redaction_redacts_metadata_text_projection(monkeypatch):
    calls = []
    raw_openai = "sk-metadatapreview1234567890"
    raw_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtZXRhIn0.signature1234567890"

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-metadata-redaction-preview"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "standard",
            "metadata": {
                "note": f"{raw_openai} {raw_jwt}",
                "agent": "qa-word-review",
                "skill": "qa-file-reviewer",
                "nested": {
                    "translator": "baoyu-translate",
                    "knowledge": "sop-assistant ragflow-knowledge-search",
                    "storage": "raw_storage_key=s3://bucket/key rawStorageKey=s3://bucket/key",
                    "private": "executorPrivatePayload={raw} executor_payload={raw}",
                },
                "raw_storage_key": "s3://bucket/key",
                "storage_key": "s3://bucket/key",
                "sandbox_workdir": "/tmp/ai-platform-sandbox-workspaces/t",
                "rawStorageKey": "s3://bucket/key",
                "sandboxWorkdir": "/tmp/ai-platform-sandbox-workspaces/t",
                "executorPrivatePayload": "raw",
            },
        },
    )

    assert response.status_code == 200
    metadata = response.json()["memory_redaction_preview"]["metadata_preview"]
    assert metadata == {
        "note": "[redacted-secret] [redacted-secret]",
        "agent": "[redacted-internal-id]",
        "skill": "[redacted-internal-id]",
            "nested": {
                "translator": "[redacted-internal-id]",
                "knowledge": "[redacted-internal-id] [redacted-internal-id]",
                "storage": "[redacted-private]",
                "private": "[redacted-private]",
            },
    }
    serialized = str(response.json()) + str(calls)
    assert raw_openai not in serialized
    assert raw_jwt not in serialized
    assert "qa-word-review" not in serialized
    assert "qa-file-reviewer" not in serialized
    assert "baoyu-translate" not in serialized
    assert "sop-assistant" not in serialized
    assert "ragflow-knowledge-search" not in serialized
    assert "raw_storage_key" not in serialized
    assert "storage_key" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "rawStorageKey" not in serialized
    assert "sandboxWorkdir" not in serialized
    assert "executorPrivatePayload" not in serialized
    assert "executor_payload" not in serialized
    assert "/tmp/ai-platform-sandbox-workspaces" not in serialized


def test_admin_preview_memory_redaction_denies_ordinary_user_before_side_effects(monkeypatch):
    async def fail_append_audit_log(conn, **kwargs):
        raise AssertionError("ordinary users must not write redaction preview audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fail_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "strict",
            "content": "token=hidden",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_memory_admin"


def test_admin_preview_memory_redaction_rejects_invalid_mode_before_audit(monkeypatch):
    async def fail_append_audit_log(conn, **kwargs):
        raise AssertionError("invalid redaction preview mode must fail before audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fail_append_audit_log)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/memory/redaction/preview",
        headers=admin_headers(),
        json={
            "workspace_id": "workspace-a",
            "redaction_mode": "off",
            "content": "token=hidden",
        },
    )

    assert response.status_code == 422


def test_admin_list_memory_policies_returns_same_tenant_public_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        return {"id": agent_id, "tenant_id": tenant_id, "status": "active"}

    async def fake_list_admin_memory_policies(conn, *, tenant_id, workspace_id, user_id, agent_id, limit):
        calls.append(("policies", tenant_id, workspace_id, user_id, agent_id, limit))
        return [
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": "user-b",
                "agent_id": "qa-word-review",
                "memory_enabled": False,
                "long_term_memory_enabled": True,
                "retention_days": 14,
                "redaction_mode": "strict",
                "source": "stored",
                "reason": "admin note client_secret=client-secret",
                "updated_by": "admin-a",
                "updated_at": "2026-06-05T10:10:00Z",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr(
        "app.routes.context.repositories.list_admin_memory_policies",
        fake_list_admin_memory_policies,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/memory/policies?workspace_id=workspace-a&user_id=user-b&agent_id=document-review&limit=25",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["memory_policies"] == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-b",
            "agent_id": "document-review",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 14,
            "redaction_mode": "strict",
            "source": "stored",
            "reason": "admin note client_secret=[redacted-secret]",
            "updated_by": "admin-a",
            "updated_at": "2026-06-05T10:10:00Z",
        }
    ]
    assert body["summary"] == {
        "workspace_id": "workspace-a",
        "user_id": "user-b",
        "agent_id": "document-review",
        "returned_count": 1,
        "limit": 25,
    }
    assert calls == [
        ("workspace", "tenant-a", "workspace-a"),
        ("agent", "tenant-a", "qa-word-review"),
        ("policies", "tenant-a", "workspace-a", "user-b", "qa-word-review", 25),
    ]
    assert "client-secret" not in response.text
    assert "qa-word-review" not in response.text
