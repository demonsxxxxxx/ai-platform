from contextlib import asynccontextmanager
import base64
import hashlib
import json

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.models import CreateRunRequest
from app.repositories import RepositoryConflictError
from app.routes.health import admin_status
from app.routes.files import download_artifact, upload_file
from app.routes.runs import (
    _governed_skill_manifest_pins,
    artifact_card,
    copy_run,
    create_run,
    get_run,
    get_run_events,
    get_run_playback,
    get_run_steps,
    multi_agent_snapshot_from_steps,
    progress_for_status,
    resolve_run_selector,
    run_playback_summary,
    run_event_response,
    run_step_response,
)
from app.skills.registry import BuiltinSkill


RUN_SCHEMA_FIELDS = {
    "schema_version": "ai-platform.run.v1",
    "executor_schema_version": "ai-platform.executor-result.v1",
}
EVENT_SCHEMA_FIELDS = {"schema_version": "ai-platform.event-envelope.v1"}


@asynccontextmanager
async def fake_transaction():
    yield object()


def principal(**overrides):
    values = {
        "user_id": "user-a",
        "display_name": "User A",
        "tenant_id": "tenant-a",
    }
    values.update(overrides)
    return AuthPrincipal(**values)


def snapshot_manifest(skill_id, *, description="Pinned skill", source=None):
    content = f"---\nname: {skill_id}\ndescription: {description}\n---\n\n# {skill_id}\n".encode("utf-8")
    files = [
        {
            "relative_path": "SKILL.md",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "size_bytes": len(content),
        }
    ]
    digest = hashlib.sha256()
    path = b"SKILL.md"
    digest.update(len(path).to_bytes(8, "big"))
    digest.update(path)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
    version = digest.hexdigest()
    return {
        "skill_id": skill_id,
        "description": description,
        "version": version,
        "content_hash": version,
        "source": source or {"kind": "builtin", "asset_dir": skill_id, "version": version},
        "files": files,
        "dependency_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }


@pytest.fixture(autouse=True)
def default_active_run_count(monkeypatch):
    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        return 0

    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )


@pytest.fixture(autouse=True)
def default_run_steps(monkeypatch):
    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr(
        "app.routes.runs.repositories.list_run_steps",
        fake_list_run_steps,
        raising=False,
    )


@pytest.fixture(autouse=True)
def default_context_snapshot(monkeypatch):
    async def fake_record_initial_context_snapshot(conn, **kwargs):
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_test",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    monkeypatch.setattr(
        "app.routes.runs.record_initial_context_snapshot",
        fake_record_initial_context_snapshot,
        raising=False,
    )


def skill(**overrides):
    values = {
        "executor_type": "runtime211",
        "skill_version": "1.0.0",
        "input_modes": [],
    }
    values.update(overrides)
    return values


def uploaded_skill_version_row(
    skill_id="qa-file-reviewer",
    version="hash-uploaded",
    files=None,
    dependency_ids=None,
    dependency_manifests=None,
):
    if skill_id == "qa-file-reviewer" and dependency_ids is None:
        dependency_ids = ["minimax-docx"]
    if skill_id == "qa-file-reviewer" and dependency_manifests is None:
        dependency_manifests = [snapshot_manifest("minimax-docx", description="Pinned DOCX helper")]
    source = {
        "kind": "uploaded",
        "storage_key": f"tenants/tenant-a/skills/{skill_id}/versions/{version}/package.zip",
        "files": files
        if files is not None
        else [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
    }
    if dependency_manifests is not None:
        source["dependency_manifests"] = dependency_manifests
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "description": "Uploaded skill",
        "source": source,
        "dependency_ids": dependency_ids if dependency_ids is not None else [],
        "status": "active",
        "created_by": "admin-a",
        "created_at": None,
    }


def builtin_snapshot_skill_version_row(
    skill_id="qa-file-reviewer",
    version="hash-builtin",
    files=None,
    dependency_ids=None,
    dependency_manifests=None,
):
    source = {
        "kind": "builtin",
        "asset_dir": skill_id,
        "version": version,
        "files": files
        if files is not None
        else [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
    }
    if dependency_manifests is not None:
        source["dependency_manifests"] = dependency_manifests
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "description": "Builtin snapshot skill",
        "source": source,
        "dependency_ids": dependency_ids if dependency_ids is not None else [],
        "status": "active",
        "created_by": "admin-a",
        "created_at": None,
    }


class EmptyBuiltinRegistry:
    def __init__(self, root):
        self.root = root

    def list_builtin_skills(self):
        return []


class PolicyBuiltinRegistry:
    def __init__(self, root):
        self.root = root

    def list_builtin_skills(self):
        return [
            type("SkillRef", (), {"name": "qa-file-reviewer"})(),
            type("SkillRef", (), {"name": "minimax-docx"})(),
        ]


@pytest.mark.asyncio
async def test_governed_skill_manifest_pins_uses_stored_dependency_snapshots_even_when_live_primary_matches(monkeypatch):
    live_primary = {
        "skill_id": "qa-file-reviewer",
        "description": "Live QA review",
        "version": "hash-current",
        "content_hash": "hash-current",
        "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer", "version": "hash-current"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": ["minimax-docx"],
        "allowed": True,
        "staged": False,
        "used": False,
    }
    live_dependency = {
        "skill_id": "minimax-docx",
        "description": "Live DOCX helper",
        "version": "hash-live-dependency",
        "content_hash": "hash-live-dependency",
        "source": {"kind": "builtin", "asset_dir": "minimax-docx", "version": "hash-live-dependency"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }
    pinned_dependency = snapshot_manifest("minimax-docx", description="Pinned DOCX helper")

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert skill_id == "qa-file-reviewer"
        assert version == "hash-current"
        return builtin_snapshot_skill_version_row(
            skill_id=skill_id,
            version=version,
            dependency_ids=["minimax-docx"],
            dependency_manifests=[pinned_dependency],
        )

    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", lambda skill_id, input_payload: [live_primary, live_dependency])
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )

    pins = await _governed_skill_manifest_pins(
        object(),
        skill_id="qa-file-reviewer",
        input_payload={},
        release_policy_version="hash-current",
    )

    assert [item["skill_id"] for item in pins] == ["qa-file-reviewer", "minimax-docx"]
    assert pins[1]["content_hash"] == pinned_dependency["content_hash"]


def test_progress_for_status_is_stable_for_frontend_polling():
    assert progress_for_status("queued") == 10
    assert progress_for_status("running") == 55
    assert progress_for_status("succeeded") == 100
    assert progress_for_status("failed") == 100
    assert progress_for_status("canceled") == 100
    assert progress_for_status("unknown") == 0


def test_run_event_response_uses_standard_envelope():
    event = run_event_response(
        "run-a",
        {
            "id": "evt-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 3,
            "event_type": "queued",
            "stage": "queue",
            "message": "queued",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": 7,
            "input_token_count": 1,
            "output_token_count": 2,
            "total_token_count": 3,
            "estimated_cost_minor": 4,
            "payload_json": {"storage_key": "/tmp/secret", "visible_to_user": True},
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["schema_version"] == "ai-platform.event-envelope.v1"
    assert event["sequence"] == 3
    assert event["trace_id"] == "trace_run_a"
    assert event["run_id"] == "run-a"
    assert event["type"] == "queued"
    assert event["stage"] == "queue"
    assert event["severity"] == "info"
    assert event["visible_to_user"] is True
    assert event["latency_ms"] == 7
    assert event["token_counts"] == {"input": 1, "output": 2, "total": 3}
    assert event["cost"] == {"estimated_cost_minor": 4}
    assert "storage_key" not in str(event)


def test_run_event_response_redacts_dispatch_control_metadata_for_ordinary_user():
    event = run_event_response(
        "run-child",
        {
            "id": "evt-child",
            "trace_id": "trace_child",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 1,
            "event_type": "run_multi_agent_child_created",
            "stage": "control",
            "message": "Multi-agent child run created",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": None,
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "estimated_cost_minor": 0,
            "payload_json": {
                "visible_to_user": True,
                "copied_from_run_id": "run-parent",
                "parent_step_id": "step-code",
                "step_key": "code",
                "dispatch_id": "dispatch-code",
            },
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["payload"] == {"visible_to_user": True, "step_key": "code"}
    public_dump = str(event)
    assert "dispatch-code" not in public_dump
    assert "run-parent" not in public_dump
    assert "step-code" not in public_dump


def test_run_event_response_aliases_multi_agent_child_created_for_ordinary_user():
    row = {
        "id": "evt-child",
        "trace_id": "trace_child",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": 1,
        "event_type": "run_multi_agent_child_created",
        "stage": "control",
        "message": "Multi-agent child run created",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "latency_ms": None,
        "input_token_count": 0,
        "output_token_count": 0,
        "total_token_count": 0,
        "estimated_cost_minor": 0,
        "payload_json": {
            "visible_to_user": True,
            "copied_from_run_id": "run-parent",
            "parent_run_id": "run-parent-root",
            "parent_step_id": "step-code",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
        },
        "created_at": None,
    }

    event = run_event_response("run-child", row, principal=principal())
    admin_event = run_event_response("run-child", row, principal=principal(roles=["admin"]))

    assert event["event_type"] == "run_child_created"
    assert event["type"] == "run_child_created"
    assert admin_event["event_type"] == "run_multi_agent_child_created"
    assert event["payload"] == {"visible_to_user": True, "step_key": "code"}
    public_dump = str(event)
    assert "dispatch-code" not in public_dump
    assert "run-parent" not in public_dump
    assert "run-parent-root" not in public_dump
    assert "step-code" not in public_dump


def test_run_step_response_redacts_dispatch_control_metadata_for_ordinary_user():
    step = run_step_response(
        {
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
                "dispatch_state": "handed_off",
                "dispatch_kind": "subagent",
                "dispatch_id": "dispatch-code",
                "dispatch_claimed_by": "admin-a",
                "dispatch_claimed_at": "2026-06-06T01:02:03+00:00",
                "dispatch_lease_expires_at": "2026-06-06T01:17:03+00:00",
                "dispatch_child_run_id": "run-child",
                "dispatch_handed_off_at": "2026-06-06T01:03:03+00:00",
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
        principal=principal(),
    )

    assert step["payload"] == {"depends_on": ["plan"]}
    public_dump = str(step)
    assert "dispatch" not in public_dump
    assert "run-child" not in public_dump


def test_run_event_response_rejects_missing_schema_version():
    with pytest.raises(HTTPException) as exc_info:
        run_event_response(
            "run-a",
            {
                "id": "evt-a",
                "trace_id": "trace_run_a",
                "event_type": "queued",
                "stage": "queue",
                "message": "queued",
                "payload_json": {"visible_to_user": True},
                "created_at": None,
            },
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "invalid_event_schema_version"


def test_run_event_response_redacts_runtime_private_error_code_for_ordinary_user():
    event = run_event_response(
        "run-a",
        {
            "id": "evt-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "event_type": "error",
            "stage": "worker",
            "message": "failed in /home/xinlin.jiang/qa-review-queue-runtime/out.log",
            "severity": "error",
            "visible_to_user": True,
            "error_code": "runtime211_stream_error",
            "latency_ms": None,
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "estimated_cost_minor": 0,
            "payload_json": {"workerPath": "/var/lib/ai-platform/run-a"},
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["error_code"] == "run_failed"
    assert event["message"] == ""
    assert "runtime211" not in str(event)
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(event)
    assert "/var/lib/ai-platform" not in str(event)


def test_run_event_response_redacts_secret_like_error_code_for_admin():
    event = run_event_response(
        "run-a",
        {
            "id": "evt-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "event_type": "error",
            "stage": "worker",
            "message": "failed",
            "severity": "error",
            "visible_to_user": True,
            "error_code": "executor_failure token=admin-code-token",
            "latency_ms": None,
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "estimated_cost_minor": 0,
            "payload_json": {},
            "created_at": None,
        },
        principal=principal(roles=["admin"]),
    )

    assert event["error_code"] == "executor_failure token=[redacted-secret]"
    assert "admin-code-token" not in str(event)


def test_run_playback_summary_redacts_secret_like_error_code_for_admin():
    summary = run_playback_summary(
        {
            "id": "run-a",
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "failed",
            "error_code": "executor_failure token=playback-code-token",
            "error_message": "failed token=playback-message-token",
        },
        principal=principal(roles=["admin"]),
    )

    assert summary["error_code"] == "executor_failure token=[redacted-secret]"
    assert summary["error_message"] == "failed token=[redacted-secret]"
    assert "playback-code-token" not in str(summary)
    assert "playback-message-token" not in str(summary)


def test_run_playback_summary_projects_public_agent_id_for_ordinary_user():
    summary = run_playback_summary(
        {
            "id": "run-a",
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "running",
            "error_code": None,
            "error_message": None,
        },
        principal=principal(),
    )

    assert summary["agent_id"] == "document-review"
    assert summary["capability_id"] == "document_review"
    assert "qa-word-review" not in str(summary)


@pytest.mark.asyncio
async def test_get_run_playback_includes_safe_context_provenance(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "session-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "succeeded",
            "input_json": {
                "context_snapshot_id": "ctx-private",
                "context_snapshot": {
                    "context_snapshot_id": "ctx-private",
                    "source": "stored_context_snapshot",
                    "referenced_materials": {
                        "file_count": 2,
                        "message_count": 1,
                        "memory_record_count": 3,
                        "artifact_count": 4,
                        "artifact_ids": ["artifact-private"],
                        "file_ids": ["file-private"],
                    },
                    "used_context_summary": {
                        "source": "stored_context_snapshot",
                        "input_keys": [
                            "attachments",
                            "message",
                            "storage_key",
                            "copied_from_run_id",
                            "source_run_id",
                            "parent_run_id",
                        ],
                        "file_count": 2,
                        "message_count": 1,
                        "memory_record_count": 3,
                        "artifact_count": 4,
                        "raw_path": "/workspace/private",
                    },
                    "execution_tier": "document_worker",
                    "latest_artifact_version": "v7",
                    "context_pack_version": "v3",
                    "context_pack_generated_at": "2026-06-12T01:23:45Z",
                    "storage_key": "tenants/private/context.json",
                    "runtime_path": "/tmp/private",
                    "work_dir": "/workspace/private",
                    "payload": {"secret": True},
                    "manifest": {"storage_key": "tenants/private/manifest.json"},
                    "manifest_json": {"artifact_ids": ["artifact-private"]},
                },
            },
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=200):
        return []

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_context_snapshots", fake_list_context_snapshots)

    response = await get_run_playback("run-a", principal=principal())

    assert response["context_ref"] == {
        "source": "stored_context_snapshot",
        "referenced_materials": {
            "message_count": 1,
            "file_count": 2,
            "artifact_count": 4,
            "memory_record_count": 3,
        },
        "used_context_summary": {
            "source": "stored_context_snapshot",
            "input_keys": ["attachments", "message"],
            "memory_policy_source": "not_recorded",
            "long_term_memory_read": False,
        },
        "latest_artifact_version": "v7",
        "execution_tier": "document_worker",
        "context_pack_version": "v3",
        "context_pack_generated_at": "2026-06-12T01:23:45Z",
    }
    serialized = json.dumps(response["context_ref"], ensure_ascii=False)
    for private_fragment in [
        "ctx-private",
        "file-private",
        "artifact-private",
        "storage_key",
        "copied_from_run_id",
        "source_run_id",
        "parent_run_id",
        "runtime_path",
        "work_dir",
        "payload",
        "manifest",
        "manifest_json",
        "/workspace/private",
        "tenants/private",
    ]:
        assert private_fragment not in serialized


@pytest.mark.asyncio
async def test_get_run_playback_prefers_latest_context_snapshot_projection(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "session-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "general-chat",
            "skill_id": "general-chat",
            "status": "queued",
            "input_json": {
                "context_snapshot": {
                    "referenced_materials": {
                        "message_count": 0,
                        "file_count": 0,
                        "artifact_count": 0,
                        "memory_record_count": 0,
                    },
                    "used_context_summary": {
                        "source": "runs_api",
                        "input_keys": ["task"],
                        "memory_policy_source": "default",
                        "long_term_memory_read": False,
                    },
                    "execution_tier": "sdk_only_writing",
                    "context_pack_version": "v1",
                    "context_pack_generated_at": "2026-06-18T00:00:00Z",
                }
            },
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, after_sequence=None, limit=200):
        return []

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def fake_list_context_snapshots(conn, *, tenant_id, user_id, run_id):
        return [
            {
                "id": "ctx-latest",
                "schema_version": "ai-platform.context-snapshot.v1",
                "tenant_id": tenant_id,
                "workspace_id": "default",
                "user_id": user_id,
                "session_id": "session-a",
                "run_id": run_id,
                "trace_id": "trace-run-a",
                "context_kind": "executor",
                "included_message_ids": [],
                "included_file_ids": [],
                "included_artifact_ids": [],
                "included_memory_record_ids": ["mem-a"],
                "redaction_summary_json": {"mode": "strict"},
                "payload_json": {
                    "task": "b1-memory-context-smoke",
                    "memory": "public bounded summary only",
                    "referenced_materials": {
                        "message_count": 0,
                        "file_count": 0,
                        "artifact_count": 0,
                        "memory_record_count": 1,
                    },
                    "used_context_summary": {
                        "source": "manual_context_snapshot",
                        "input_keys": ["memory", "task"],
                        "memory_policy_source": "not_recorded",
                        "long_term_memory_read": False,
                    },
                    "execution_tier": "sdk_only_writing",
                    "context_pack_version": "v1",
                    "context_pack_generated_at": "2026-06-18T01:00:00Z",
                    "memory_record_ids": ["mem-private"],
                    "storage_key": "tenants/private/context.json",
                },
                "created_at": "2026-06-18T01:00:00Z",
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.list_context_snapshots", fake_list_context_snapshots)

    response = await get_run_playback("run-a", principal=principal())

    assert response["context_ref"]["referenced_materials"]["memory_record_count"] == 1
    assert response["context_ref"]["used_context_summary"]["source"] == "manual_context_snapshot"
    serialized = json.dumps(response["context_ref"], ensure_ascii=False)
    assert "ctx-latest" not in serialized
    assert "mem-private" not in serialized
    assert "storage_key" not in serialized


def test_run_event_response_sanitizes_runtime_envelope_for_ordinary_user():
    event = run_event_response(
        "run-a",
        {
            "id": "evt-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "event_type": "legacy_runtime211_direct_executor_denied",
            "stage": "worker",
            "message": "failed in /home/xinlin.jiang/qa-review-queue-runtime/out.log",
            "severity": "warning",
            "visible_to_user": True,
            "payload_json": {"visible_to_user": True},
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["event_type"] == "status"
    assert event["type"] == "status"
    assert event["stage"] == "status"
    assert event["message"] == ""
    assert "runtime211" not in str(event)
    assert "worker" not in str(event)
    assert "qa-review-queue-runtime" not in str(event)


def test_run_event_response_redacts_secret_like_payload_for_ordinary_user():
    event = run_event_response(
        "run-a",
        {
            "id": "evt-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "event_type": "status",
            "stage": "agent",
            "message": (
                "callback authorization: Bearer bearer-token-123 user@example.com "
                "https://example.com/doc clientsecret=compact-client-secret "
                "passwordhash=compact-password-hash secretkey=compact-secret-key "
                "clientcredentialblob=compact-client-credential "
                "secretarysecret=compact-secretary-secret "
                "authorizationbearer=compact-authorization-bearer "
                "privatekey=compact-private-key "
                "bearer=compact-bearer "
                "authkey=compact-auth-key"
            ),
            "severity": "warning",
            "visible_to_user": True,
            "payload_json": {
                "note": (
                    "api_key=sk-live client_secret=client-secret githubtoken=compact-github-token "
                    "passworddigest=compact-password-digest secretvalue=compact-secret-value "
                    "servicecredentialsjson=compact-service-credentials "
                    "awsaccesskeyid=compact-aws-access-key"
                ),
                "url": "https://example.com/doc",
                "homepage": "http://example.com/home",
                "headers": {"Authorization": "Bearer nested-bearer-token"},
                "openai_api_key": "sk-openai",
                "openaiapikey": "sk-openai-compact",
                "passwordhash": "password-hash-compact",
                "secretkey": "secret-key-compact",
                "secretarysecret": "secretary-secret-compact",
                "clientsecretarysecret": "client-secretary-secret-compact",
                "authorizationbearer": "authorization-bearer-compact",
                "authorizationvalue": "authorization-value-compact",
                "accesskeyid": "access-key-id-compact",
                "awsaccesskeyid": "aws-access-key-id-compact",
                "privatekey": "private-key-compact",
                "sshprivatekey": "ssh-private-key-compact",
                "bearer": "bearer-compact",
                "bearervalue": "bearer-value-compact",
                "bearerkey": "bearer-key-compact",
                "authkey": "auth-key-compact",
                "authheader": "auth-header-compact",
                "authvalue": "auth-value-compact",
                "authstatus": "approved",
                "input_token_count": 12,
                "output_token_count": 8,
                "total_token_count": 20,
                "remaining_token_budget": 100,
                "oauth_authorization_status": "approved",
                "secretary_name": "Jane",
                "client_secretary_name": "Jane Doe",
                "tokenizer": "cl100k_base",
                "publickey": "public-key-visible",
                "clientcredentialblob": "client-credential-secret-compact",
                "servicecredentialsjson": "service-credentials-secret-compact",
                "authorizationheader": "Bearer authorization-header-compact",
                "credentialblob": "credential-secret-compact",
                "token_count_github_token": "ghp-count-secret",
                "token_usage_slack_token": "slack-usage-secret",
                "safe": "done",
            },
            "created_at": None,
        },
        principal=principal(),
    )

    serialized = str(event)
    assert event["message"] == (
        "callback authorization=[redacted-secret] [redacted-email] "
        "https://example.com/doc clientsecret=[redacted-secret] "
        "passwordhash=[redacted-secret] secretkey=[redacted-secret] "
        "clientcredentialblob=[redacted-secret] "
        "secretarysecret=[redacted-secret] "
        "authorizationbearer=[redacted-secret] "
        "privatekey=[redacted-secret] "
        "bearer=[redacted-secret] "
        "authkey=[redacted-secret]"
    )
    assert event["payload"] == {
        "note": (
            "api_key=[redacted-secret] client_secret=[redacted-secret] githubtoken=[redacted-secret] "
            "passworddigest=[redacted-secret] secretvalue=[redacted-secret] "
            "servicecredentialsjson=[redacted-secret] "
            "awsaccesskeyid=[redacted-secret]"
        ),
        "url": "https://example.com/doc",
        "homepage": "http://example.com/home",
        "headers": {},
        "authstatus": "approved",
        "input_token_count": 12,
        "output_token_count": 8,
        "total_token_count": 20,
        "remaining_token_budget": 100,
        "oauth_authorization_status": "approved",
        "secretary_name": "Jane",
        "client_secretary_name": "Jane Doe",
        "tokenizer": "cl100k_base",
        "publickey": "public-key-visible",
        "safe": "done",
    }
    assert "bearer-token-123" not in serialized
    assert "nested-bearer-token" not in serialized
    assert "user@example.com" not in serialized
    assert "sk-openai" not in serialized
    assert "sk-openai-compact" not in serialized
    assert "client-secret" not in serialized
    assert "compact-client-secret" not in serialized
    assert "compact-github-token" not in serialized
    assert "compact-password-hash" not in serialized
    assert "compact-secret-key" not in serialized
    assert "compact-password-digest" not in serialized
    assert "compact-secret-value" not in serialized
    assert "compact-secretary-secret" not in serialized
    assert "compact-authorization-bearer" not in serialized
    assert "compact-private-key" not in serialized
    assert "compact-bearer" not in serialized
    assert "compact-auth-key" not in serialized
    assert "compact-aws-access-key" not in serialized
    assert "compact-client-credential" not in serialized
    assert "compact-service-credentials" not in serialized
    assert "password-hash-compact" not in serialized
    assert "secret-key-compact" not in serialized
    assert "secretary-secret-compact" not in serialized
    assert "client-secretary-secret-compact" not in serialized
    assert "authorization-bearer-compact" not in serialized
    assert "authorization-value-compact" not in serialized
    assert "access-key-id-compact" not in serialized
    assert "aws-access-key-id-compact" not in serialized
    assert "private-key-compact" not in serialized
    assert "ssh-private-key-compact" not in serialized
    assert "bearer-compact" not in serialized
    assert "bearer-value-compact" not in serialized
    assert "bearer-key-compact" not in serialized
    assert "auth-key-compact" not in serialized
    assert "auth-header-compact" not in serialized
    assert "auth-value-compact" not in serialized
    assert "approved" in serialized
    assert "public-key-visible" in serialized
    assert "client-credential-secret-compact" not in serialized
    assert "service-credentials-secret-compact" not in serialized
    assert "authorization-header-compact" not in serialized
    assert "credential-secret-compact" not in serialized
    assert "ghp-count-secret" not in serialized
    assert "slack-usage-secret" not in serialized


def test_artifact_card_redacts_legacy_manifest_worker_paths():
    card = artifact_card(
        {
            "id": "art-a",
            "artifact_type": "reviewed_docx",
            "label": "批注 Word",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "storage_key": "tenants/default/runs/run-a/artifacts/1/reviewed.docx",
            "size_bytes": 10,
            "manifest_version": "ai-platform.artifact-manifest.v1",
            "manifest_json": {
                "source_run_id": "run-source",
                "source_file_id": "file-a",
                "local_path": "/tmp/worker/output.docx",
                "nested": {"storage_key": "tenants/default/private.docx"},
            },
            "created_at": None,
        }
    )

    assert card["manifest"]["schema_version"] == "ai-platform.artifact-manifest.v1"
    assert "source_run_id" not in card["lineage"]
    assert card["manifest"]["source_file_id"] == "file-a"
    assert "storage_key" not in str(card)
    assert "/tmp/" not in str(card)


def test_artifact_card_redacts_secret_like_admin_label():
    card = artifact_card(
        {
            "id": "art-a",
            "artifact_type": "reviewed_docx",
            "label": "批注 Word token=artifact-label-token",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": 10,
            "manifest_version": "ai-platform.artifact-manifest.v1",
            "manifest_json": {},
            "created_at": None,
        },
        principal=principal(roles=["admin"]),
    )

    assert card["label"] == "批注 Word token=[redacted-secret]"
    assert "artifact-label-token" not in str(card)


def test_artifact_card_exposes_preview_url_only_for_allowlisted_content_type():
    previewable = artifact_card(
        {
            "id": "art-preview",
            "artifact_type": "reviewed_docx",
            "label": "批注 Word",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": 10,
            "manifest_version": "ai-platform.artifact-manifest.v1",
            "manifest_json": {},
            "created_at": None,
        }
    )
    blocked = artifact_card(
        {
            "id": "art-zip",
            "artifact_type": "archive",
            "label": "archive.zip",
            "content_type": "application/zip",
            "size_bytes": 10,
            "manifest_version": "ai-platform.artifact-manifest.v1",
            "manifest_json": {},
            "created_at": None,
        }
    )

    assert previewable["preview_url"] == "/api/ai/artifacts/art-preview/preview"
    assert blocked["preview_url"] is None


@pytest.mark.asyncio
async def test_admin_status_exposes_queue_depths(monkeypatch):
    async def fake_get_queue_status():
        return {
            "depths": {
                "queued": 3,
                "processing": 1,
                "dead_letter": 2,
            },
            "keys": {
                "queued": "ai-platform:runs:queued",
                "processing": "ai-platform:runs:processing",
                "dead_letter": "ai-platform:runs:dead-letter",
            },
        }

    monkeypatch.setattr("app.routes.health.get_queue_status", fake_get_queue_status)

    response = await admin_status(principal=principal(roles=["developer"]))

    assert response["status"] == "ok"
    assert response["queue"]["depths"]["queued"] == 3
    assert response["queue"]["depths"]["processing"] == 1
    assert response["queue"]["depths"]["dead_letter"] == 2


@pytest.mark.asyncio
async def test_admin_status_rejects_normal_user(monkeypatch):
    async def fake_get_queue_status():
        raise AssertionError("normal users must not read queue status")

    monkeypatch.setattr("app.routes.health.get_queue_status", fake_get_queue_status)

    with pytest.raises(Exception) as exc_info:
        await admin_status(principal=principal(roles=["user"]))

    assert getattr(exc_info.value, "status_code", None) == 403
    assert getattr(exc_info.value, "detail", None) == "not_ai_admin"


@pytest.mark.asyncio
async def test_download_artifact_streams_file_bytes(monkeypatch):
    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        assert user_id == "user-a"
        return {
            "id": artifact_id,
            "storage_key": "tenants/tenant-a/runs/run-a/demo_reviewed.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "label": "批注 Word",
        }

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            return b"docx-bytes"

    monkeypatch.setattr("app.routes.files.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.ObjectStorage", FakeStorage)

    response = await download_artifact("art-a", principal=principal())

    assert response.body == b"docx-bytes"
    assert "filename*=UTF-8''demo_reviewed.docx" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_download_artifact_denied_does_not_read_storage(monkeypatch):
    async def fake_get_authorized_artifact(conn, *, tenant_id, user_id, artifact_id):
        assert tenant_id == "tenant-a"
        assert user_id == "user-a"
        assert artifact_id == "art-b"
        return None

    class ForbiddenStorage:
        def get_bytes(self, *, storage_key):
            raise AssertionError("storage must not be read for unauthorized artifacts")

    monkeypatch.setattr("app.routes.files.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.files.get_authorized_artifact", fake_get_authorized_artifact)
    monkeypatch.setattr("app.routes.files.ObjectStorage", ForbiddenStorage)

    with pytest.raises(Exception) as exc_info:
        await download_artifact("art-b", principal=principal())

    assert getattr(exc_info.value, "status_code", None) == 404
    assert getattr(exc_info.value, "detail", None) == "artifact_not_found"


@pytest.mark.asyncio
async def test_upload_file_rejects_cross_user_session(monkeypatch):
    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        assert user_id == "user-a"
        assert display_name == "User A"

    async def fake_get_authorized_session(conn, *, tenant_id, user_id, session_id):
        assert user_id == "user-a"
        return None

    class FakeUpload:
        filename = "demo.docx"
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        async def read(self):
            return b"should-not-be-read"

    monkeypatch.setattr("app.routes.files.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.files.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.files.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.files.get_authorized_session", fake_get_authorized_session)

    with pytest.raises(Exception) as exc_info:
        await upload_file(
            file=FakeUpload(),
            workspace_id="default",
            session_id="ses_b",
            principal=principal(),
        )

    assert getattr(exc_info.value, "status_code", None) == 404
    assert getattr(exc_info.value, "detail", None) == "session_not_found"


@pytest.mark.asyncio
async def test_upload_file_response_does_not_expose_storage_key(monkeypatch):
    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        return None

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        return None

    async def fake_create_file(conn, **kwargs):
        assert kwargs["storage_key"].startswith("tenants/tenant-a/")

    class FakeUpload:
        filename = "demo.docx"
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        async def read(self):
            return b"docx-bytes"

    class FakeStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            return type(
                "Stored",
                (),
                {
                    "storage_key": storage_key,
                    "sha256": "sha-a",
                    "size_bytes": len(content),
                },
            )()

    monkeypatch.setattr("app.routes.files.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.files.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.files.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.files.create_file", fake_create_file)
    monkeypatch.setattr("app.routes.files.ObjectStorage", FakeStorage)
    monkeypatch.setattr("app.routes.files.new_id", lambda prefix: "file_uploaded")

    response = await upload_file(file=FakeUpload(), workspace_id="default", session_id=None, principal=principal())
    payload = response.model_dump()

    assert payload == {"file_id": "file_uploaded", "sha256": "sha-a", "size_bytes": 10}
    assert "storage_key" not in payload


@pytest.mark.asyncio
async def test_get_run_includes_artifacts_events_and_progress(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert user_id == "user-a"
        return {
            "id": run_id,
            "session_id": "session-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "succeeded",
            "input_json": {"input": {"mode": "file"}},
            "result_json": {"message": "done"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "artifact-a",
                "artifact_type": "reviewed_docx",
                "label": "批注 Word",
                "storage_key": "tenants/tenant-a/runs/run-a/reviewed.docx",
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "event-a",
                **EVENT_SCHEMA_FIELDS,
                "event_type": "status",
                "stage": "worker",
                "message": "Run succeeded",
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    response = await get_run("run-a", principal=principal())

    assert response.progress == 100
    assert response.artifacts[0]["id"] == "artifact-a"
    assert response.events[0]["stage"] == "status"


def test_get_run_http_projection_returns_null_skill_id_for_ordinary_user(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "succeeded",
            "input_json": {},
            "result_json": {"message": "done"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_id"] is None
    assert "executor_schema_version" not in payload or payload["executor_schema_version"] is None
    assert payload["capability_id"] == "general_chat"


@pytest.mark.asyncio
async def test_get_run_rejects_missing_contract_versions(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "succeeded",
            "input_json": {},
            "result_json": {"message": "done"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    with pytest.raises(HTTPException) as exc_info:
        await get_run("run-a", principal=principal())

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "invalid_run_contract"


@pytest.mark.asyncio
async def test_get_run_redacts_raw_skill_references_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.run.v1",
            "status": "running",
            "input_json": {
                "skill_id": "qa-file-reviewer",
                "executor_type": "embedded-poco-kernel",
                "skill_version": "internal-version",
                "release_decision": {
                    "schema_version": "ai-platform.skill-release-decision.v1",
                    "policy_active": True,
                    "selected_version": "hash-internal",
                    "current_version": "hash-current",
                    "previous_version": "hash-previous",
                    "selected_track": "previous",
                    "bucket": 73,
                },
                "worker_path": "/app/worker.py",
                "multi_agent_dispatch": {
                    "orchestration_state": "awaiting_dispatch",
                    "source": "worker",
                    "worker_id": "worker-private",
                },
                "input": {
                    "message": "审核",
                    "skill_ids": ["qa-file-reviewer"],
                    "multi_agent_dispatch": {"parent_run_id": "run-forged"},
                    "multi_agent_steps": [{"step_key": "review", "skill_ids": ["qa-file-reviewer"]}],
                },
            },
            "result_json": {
                "message": (
                    "Command executed: python "
                    ".claude/skills/baoyu-translate/scripts/run_translation.py "
                    "input.docx output --target-language English"
                ),
                "sdk_session_id": "sdk-private",
                "worker_boundary": "claude-sdk",
                "delegate_used": True,
                "allowed_skills": ["qa-file-reviewer"],
                "used_skills": ["qa-file-reviewer"],
                "executor": {
                    "adapter_version": "private-adapter",
                    "executor_type": "claude-agent-worker",
                    "executor_version": "private-executor",
                },
                "executor_payload": {"worker_path": "/app/worker.py"},
            },
            "error_code": "runtime211_stream_error",
            "error_message": "failed in /home/xinlin.jiang/qa-review-queue-runtime/out.log",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "event-a",
                **EVENT_SCHEMA_FIELDS,
                "event_type": "skill_selected",
                "stage": "planning",
                "message": (
                    "Command executed: python "
                    ".claude/skills/baoyu-translate/scripts/run_translation.py "
                    "input.docx output"
                ),
                "payload_json": {
                    "skill_id": "qa-file-reviewer",
                    "skill_ids": ["qa-file-reviewer"],
                    "delta": (
                        "Generated at output/reviewed.docx via "
                        ".claude/skills/baoyu-translate/scripts/run_translation.py"
                    ),
                    "visible_to_user": True,
                },
                "created_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
                {
                    "id": "art-a",
                    "artifact_type": "translated_docx",
                    "label": "/tmp/workspace/output/reviewed.docx",
                    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "size_bytes": 123,
                    "manifest_json": {
                        "skill_id": "baoyu-translate",
                        "runtime_path": "/tmp/ai-platform-agent-workspaces/default/run-a",
                        "used_skills_source": "executor_hook",
                        "workspace_output": "output/reviewed.docx",
                        "storage_key": "tenants/default/workspaces/default/runs/run-a/output/reviewed.docx",
                        "public_note": "ready",
                },
                "manifest_version": "ai-platform.artifact-manifest.v1",
                "created_at": None,
            }
        ]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "running",
                "title": "Review",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {
                    "skill_ids": ["qa-file-reviewer"],
                    "worker_path": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
                    "runtimePrivatePayload": {"token": "hidden"},
                    "executor_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    response = await get_run("run-a", principal=principal())

    assert response.agent_id == "document-review"
    assert response.skill_id is None
    assert "skill_id" not in response.model_dump(exclude_none=True)
    assert response.capability_id == "document_review"
    assert response.trace_id == "trace_run_a"
    assert response.contract_version == "ai-platform.run.v1"
    assert "skill_id" not in response.input
    assert "skill_ids" not in response.input["input"]
    assert "executor_type" not in response.input
    assert "skill_version" not in response.input
    assert "release_decision" not in response.input
    assert "hash-current" not in str(response.input)
    assert "hash-previous" not in str(response.input)
    assert "bucket" not in str(response.input)
    assert "worker_path" not in str(response.input)
    assert "skill_ids" not in response.input["input"]["multi_agent_steps"][0]
    assert "multi_agent_dispatch" not in str(response.input)
    assert "allowed_skills" not in response.result
    assert "used_skills" not in response.result
    assert "executor_type" not in str(response.result)
    assert "adapter_version" not in str(response.result)
    assert "worker_path" not in str(response.result)
    assert "sdk_session_id" not in str(response.result)
    assert "worker_boundary" not in str(response.result)
    assert "delegate_used" not in str(response.result)
    public_dump = str(response.model_dump())
    assert "qa-word-review" not in public_dump
    assert ".claude/skills" not in public_dump
    assert "run_translation.py" not in public_dump
    assert "Command executed" not in public_dump
    assert "workspace_output" not in public_dump
    assert "output/reviewed.docx" not in public_dump
    assert "/tmp/workspace" not in public_dump
    assert "runtime_path" not in public_dump
    assert "used_skills_source" not in public_dump
    assert "executor_hook" not in public_dump
    assert "skill_id" not in response.events[0]["payload"]
    assert "skill_ids" not in response.events[0]["payload"]
    assert "skill_ids" not in response.steps[0]
    assert "skill_ids" not in response.steps[0]["payload"]
    assert "worker_path" not in str(response.steps[0])
    assert "runtimePrivatePayload" not in str(response.steps[0])
    assert "executor_payload" not in str(response.steps[0])
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(response.steps[0])
    assert "/var/lib/ai-platform" not in str(response.steps[0])
    assert response.error_code == "run_failed"
    assert response.error_message == ""


@pytest.mark.asyncio
async def test_get_run_redacts_ragflow_internal_reference_ids_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "sop-assistant",
            "skill_id": "ragflow-knowledge-search",
            "trace_id": "trace_run_rag",
            "schema_version": "ai-platform.run.v1",
            "status": "succeeded",
            "input_json": {
                "message": "SOP 怎么查？",
                "dataset_ids": ["dataset-secret"],
            },
            "result_json": {
                "message": "根据知识库检索结果，相关内容如下：",
                "answer": "根据知识库检索结果，相关内容如下：",
                "dataset_ids": ["dataset-secret"],
                "references": [
                    {
                        "document_name": "QA-SOP.docx",
                        "document_id": "doc-secret",
                        "dataset_id": "dataset-secret",
                        "chunk_id": "chunk-secret",
                        "content": "SOP 正文片段",
                    }
                ],
                "ragflow_payload": {
                    "data": {"dataset_id": "dataset-secret", "chunk_id": "chunk-secret"},
                },
            },
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    response = await get_run("run-rag", principal=principal())

    public_dump = str(response.model_dump())
    assert response.capability_id == "knowledge_answer"
    assert response.skill_id is None
    assert "QA-SOP.docx" in public_dump
    assert "SOP 正文片段" in public_dump
    assert "dataset_ids" not in public_dump
    assert "dataset_id" not in public_dump
    assert "dataset-secret" not in public_dump
    assert "document_id" not in public_dump
    assert "doc-secret" not in public_dump
    assert "chunk_id" not in public_dump
    assert "chunk-secret" not in public_dump
    assert "ragflow_payload" not in public_dump


@pytest.mark.asyncio
async def test_get_run_keeps_raw_skill_references_for_admin(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "running",
            "input_json": {"input": {"skill_ids": ["qa-file-reviewer"]}},
            "result_json": {"used_skills": ["qa-file-reviewer"]},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "running",
                "title": "Review",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {"skill_ids": ["qa-file-reviewer"]},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    response = await get_run("run-a", principal=principal(roles=["admin"]))

    assert response.skill_id == "qa-file-reviewer"
    assert response.input["input"]["skill_ids"] == ["qa-file-reviewer"]
    assert response.result["used_skills"] == ["qa-file-reviewer"]
    assert response.steps[0]["skill_ids"] == ["qa-file-reviewer"]


@pytest.mark.asyncio
async def test_get_run_redacts_secret_and_runtime_payload_for_admin(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "failed",
            "input_json": {
                "input": {"message": "审核", "api_key": "sk-admin-input"},
                "workerPath": "/var/lib/ai-platform/run-a/worker.py",
                "skill_ids": ["qa-file-reviewer"],
            },
            "result_json": {
                "message": "failed client_secret=admin-result-secret",
                "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                "used_skills": ["qa-file-reviewer"],
            },
            "error_code": "executor_failure token=admin-code-token",
            "error_message": "failed token=admin-error-token /var/lib/ai-platform/run-a/out.log",
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "failed",
                "title": "Review",
                "role": "reviewer",
                "sequence": 1,
                "payload_json": {
                    "skill_ids": ["qa-file-reviewer"],
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    "note": "client_secret=admin-step-secret",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-a",
                **EVENT_SCHEMA_FIELDS,
                "sequence": 1,
                "trace_id": "trace-a",
                "event_type": "error",
                "stage": "worker",
                "message": "failed token=admin-event-token",
                "severity": "error",
                "visible_to_user": True,
                "error_code": "executor_failure",
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    "summary": "client_secret=admin-event-secret",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    response = await get_run("run-a", principal=principal(roles=["admin"]))

    assert response.skill_id == "qa-file-reviewer"
    assert response.input["skill_ids"] == ["qa-file-reviewer"]
    assert response.result["used_skills"] == ["qa-file-reviewer"]
    assert response.steps[0]["skill_ids"] == ["qa-file-reviewer"]
    serialized = response.model_dump_json()
    assert "sk-admin-input" not in serialized
    assert "admin-result-secret" not in serialized
    assert "admin-code-token" not in serialized
    assert "admin-error-token" not in serialized
    assert "admin-event-token" not in serialized
    assert "admin-event-secret" not in serialized
    assert "admin-step-secret" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "runtime_private_payload" not in serialized


@pytest.mark.asyncio
async def test_get_run_returns_product_event_and_artifact_cards(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "status": "running",
            "input_json": {"input": {"message": "审核"}},
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "art-a",
                "artifact_type": "reviewed_docx",
                "label": "批注 Word",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "storage_key": "tenants/default/workspaces/default/sessions/ses-a/runs/run-a/artifacts/1/reviewed.docx",
                "size_bytes": 1234,
                "manifest_json": {"source_file_id": "file-a"},
                "created_at": None,
            }
        ]

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-a",
                **EVENT_SCHEMA_FIELDS,
                "event_type": "skill_selected",
                "stage": "planning",
                "message": "Using qa-file-reviewer",
                "payload_json": {
                    "skill_id": "qa-file-reviewer",
                    "visible_to_user": True,
                    "severity": "info",
                },
                "created_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    response = await get_run("run-a", principal=principal())

    assert response.events[0]["type"] == "capability_selected"
    assert response.events[0]["visible_to_user"] is True
    assert response.events[0]["severity"] == "info"
    assert response.artifacts[0]["artifact_id"] == "art-a"
    assert response.artifacts[0]["download_url"] == "/api/ai/artifacts/art-a/download"
    assert "storage_key" not in response.artifacts[0]


@pytest.mark.asyncio
async def test_get_run_includes_multi_agent_snapshot_from_run_steps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "cancelled",
            "input_json": {"input": {"message": "build feature"}},
            "result_json": {"message": "任务已取消"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "cancelled",
                "title": "planning agent",
                "role": "planning",
                "sequence": 1,
                "payload_json": {},
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
                "status": "cancelled",
                "title": "coding agent",
                "role": "coding",
                "sequence": 2,
                "payload_json": {},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    response = await get_run("run-a", principal=principal())

    multi_agent = response.result["multi_agent"]
    assert multi_agent["counts"]["total"] == 2
    assert multi_agent["counts"]["cancelled"] == 2
    assert [step["status"] for step in multi_agent["steps"]] == ["cancelled", "cancelled"]
    assert [step["step_key"] for step in response.steps] == ["plan", "code"]
    assert response.result["message"] == "任务已取消"


@pytest.mark.asyncio
async def test_get_run_exposes_cancel_request_metadata(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "running",
            "input_json": {"input": {"message": "long task"}},
            "result_json": {},
            "cancel_requested_at": "2026-05-27T06:12:00Z",
            "cancel_requested_by": "user-a",
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    response = await get_run("run-a", principal=principal())

    assert response.cancel_requested_at == "2026-05-27T06:12:00Z"
    assert response.cancel_requested_by == "user-a"


@pytest.mark.asyncio
async def test_get_run_normalizes_legacy_canceled_status(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "canceled",
            "input_json": {"input": {"message": "cancel"}},
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    response = await get_run("run-a", principal=principal())

    assert response.status == "cancelled"
    assert response.progress == 100


@pytest.mark.asyncio
async def test_get_run_includes_queue_insight_while_queued(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "queued",
            "input_json": {"input": {"message": "long task"}},
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_get_queue_insight(tenant_id, **kwargs):
        assert tenant_id == "tenant-a"
        assert kwargs == {"user_id": "user-a"}
        return {"tenant_id": tenant_id, "reason": "worker_capacity_full"}

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        assert tenant_id == "tenant-a"
        assert run_id == "run-a"
        return 4

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight, raising=False)
    monkeypatch.setattr("app.routes.runs.get_run_queue_position", fake_get_run_queue_position, raising=False)

    response = await get_run("run-a", principal=principal(tenant_id="tenant-a"))

    assert response.queue_insight == {"tenant_id": "tenant-a", "reason": "worker_capacity_full"}
    assert response.queue_position == 4


@pytest.mark.asyncio
async def test_get_run_omits_queue_insight_after_queue_wait(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "running",
            "input_json": {"input": {"message": "long task"}},
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        raise AssertionError("queue insight should only be read for queued runs")

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight, raising=False)

    response = await get_run("run-a", principal=principal(tenant_id="tenant-a"))

    assert response.queue_insight is None


@pytest.mark.asyncio
async def test_get_run_denied_does_not_list_artifacts_or_events(monkeypatch):
    touched = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert tenant_id == "tenant-a"
        assert user_id == "user-b"
        assert run_id == "run-a"
        return None

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        touched.append("artifacts")
        return []

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        touched.append("events")
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    with pytest.raises(Exception) as exc_info:
        await get_run("run-a", principal=principal(user_id="user-b"))

    assert getattr(exc_info.value, "status_code", None) == 404
    assert getattr(exc_info.value, "detail", None) == "run_not_found"
    assert touched == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schema_fields", "expected_detail"),
    [
        ({"executor_schema_version": "ai-platform.executor-result.v1"}, "invalid_run_contract"),
        ({"schema_version": "ai-platform.run.v1"}, "invalid_executor_result_schema_version"),
    ],
)
async def test_get_run_events_validates_run_contract_before_listing_events(monkeypatch, schema_fields, expected_detail):
    touched = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "status": "running",
            **schema_fields,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        touched.append("events")
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)

    with pytest.raises(HTTPException) as exc_info:
        await get_run_events("run-a", principal=principal())

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == expected_detail
    assert touched == []


@pytest.mark.asyncio
async def test_get_run_steps_returns_authorized_multi_agent_steps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "status": "running",
        }

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "coding-1",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "coding agent",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "role": "coding",
                    "depends_on": ["plan"],
                    "skill_ids": ["qa-file-reviewer"],
                    "mcp_tool_ids": ["ragflow-knowledge-search"],
                    "resource_limits": {"max_tool_calls": 3},
                    "sandbox_mode": "ephemeral",
                    "browser_enabled": True,
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    response = await get_run_steps("run-a", principal=principal())

    assert response["run_id"] == "run-a"
    assert response["steps"][0]["step_key"] == "coding-1"
    assert response["steps"][0]["status"] == "succeeded"
    assert response["steps"][0]["role"] == "coding"
    assert "skill_ids" not in response["steps"][0]
    assert "mcp_tool_ids" not in response["steps"][0]
    assert "resource_limits" not in response["steps"][0]
    assert "sandbox_mode" not in response["steps"][0]
    assert "browser_enabled" not in response["steps"][0]
    assert "mcp_tool_ids" not in response["steps"][0]["payload"]
    assert "resource_limits" not in response["steps"][0]["payload"]
    assert "sandbox_mode" not in response["steps"][0]["payload"]
    assert "browser_enabled" not in response["steps"][0]["payload"]


@pytest.mark.asyncio
async def test_get_run_steps_redacts_raw_skill_references_for_ordinary_user(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {"id": run_id}

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "running",
                "title": "failed in /home/xinlin.jiang/qa-review-queue-runtime/out.log",
                "role": "runtime211 worker /var/lib/ai-platform/run-a",
                "sequence": 1,
                "payload_json": {
                    "skill_ids": ["qa-file-reviewer"],
                    "worker_path": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    response = await get_run_steps("run-a", principal=principal())

    assert "skill_ids" not in response["steps"][0]
    assert "skill_ids" not in response["steps"][0]["payload"]
    assert "worker_path" not in str(response["steps"][0])
    assert "runtime_private_payload" not in str(response["steps"][0])
    assert "/home/xinlin.jiang/qa-review-queue-runtime" not in str(response["steps"][0])
    assert "/var/lib/ai-platform" not in str(response["steps"][0])
    assert "runtime211" not in str(response["steps"][0])
    assert response["steps"][0]["title"] == "review"
    assert response["steps"][0]["role"] is None


@pytest.mark.asyncio
async def test_get_run_steps_denied_does_not_list_steps(monkeypatch):
    touched = []

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return None

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        touched.append("steps")
        return []

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    with pytest.raises(Exception) as exc_info:
        await get_run_steps("run-a", principal=principal(user_id="user-b"))

    assert getattr(exc_info.value, "status_code", None) == 404
    assert getattr(exc_info.value, "detail", None) == "run_not_found"
    assert touched == []


def test_run_event_stream_filters_hidden_bad_schema_event_before_response_conversion(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "succeeded",
            "result_json": {"message": "done"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-hidden-bad-schema",
                "event_type": "status",
                "stage": "worker",
                "message": "internal malformed diagnostic",
                "payload_json": {"visible_to_user": False},
                "created_at": None,
            }
        ]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert "internal malformed diagnostic" not in response.text
    assert "event: done" in response.text


def test_run_event_stream_reports_visible_bad_schema_event_without_crashing(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "running",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-visible-bad-schema",
                "event_type": "status",
                "stage": "worker",
                "message": "visible malformed diagnostic",
                "payload_json": {"visible_to_user": True},
                "created_at": None,
            }
        ]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"error": "invalid_event_schema_version"' in response.text
    assert "event: done" in response.text


def test_run_event_stream_emits_existing_events(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "succeeded",
            "result_json": {"message": "done"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return [
            {
                "id": "evt-a",
                **EVENT_SCHEMA_FIELDS,
                "event_type": "queued",
                "stage": "queue",
                "message": "Run queued by .claude/skills/baoyu-translate/scripts/run_translation.py",
                "payload_json": {
                    "visible_to_user": True,
                    "used_skills_source": "executor_hook",
                    "runtime_path": "/tmp/ai-platform-agent-workspaces/default/run-a",
                    "delta": "created output/reviewed.docx",
                },
                "created_at": None,
            },
            {
                "id": "evt-internal",
                **EVENT_SCHEMA_FIELDS,
                "event_type": "status",
                "stage": "worker",
                "message": "internal diagnostic",
                "payload_json": {"visible_to_user": False},
                "created_at": None,
            }
        ]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert "event: run_event" in response.text
    assert '"type": "queued"' in response.text
    assert ".claude/skills" not in response.text
    assert "run_translation.py" not in response.text
    assert "used_skills_source" not in response.text
    assert "executor_hook" not in response.text
    assert "runtime_path" not in response.text
    assert "/tmp/ai-platform-agent-workspaces" not in response.text
    assert "output/reviewed.docx" not in response.text
    assert "internal diagnostic" not in response.text
    assert "event: done" in response.text


def test_run_event_stream_emits_multi_agent_step_snapshot(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "failed",
            "result_json": {"message": "blocked"},
            "error_code": "multi_agent_dependency_blocked",
            "error_message": "blocked",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "coding agent completed",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "output": "code output",
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
                "status": "failed",
                "title": "test agent blocked",
                "role": "test",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["missing"],
                    "missing_dependencies": ["missing"],
                    "error_code": "multi_agent_dependency_blocked",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert "event: multi_agent_snapshot" in response.text
    assert '"counts": {"total": 2, "pending": 0, "succeeded": 1, "failed": 1, "running": 0, "cancelled": 0, "reused": 0, "blocked": 1}' in response.text
    assert '"step_key": "verify"' in response.text
    assert '"missing_dependencies": ["missing"]' in response.text


def test_run_event_stream_normalizes_canceled_status_to_cancelled(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "canceled",
            "result_json": {"message": "cancelled"},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert '"status": "cancelled"' in response.text
    assert '"status": "canceled"' not in response.text


def test_multi_agent_snapshot_normalizes_legacy_canceled_step_status():
    snapshot = multi_agent_snapshot_from_steps(
        "run-a",
        [
            {
                "id": "step-a",
                "run_id": "run-a",
                "step_key": "verify",
                "step_kind": "agent",
                "status": "canceled",
                "title": "Verify",
                "role": "test",
                "sequence": 1,
                "payload_json": {},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ],
    )

    assert snapshot["steps"][0]["status"] == "cancelled"
    assert snapshot["counts"]["cancelled"] == 1


def test_multi_agent_snapshot_redacts_parent_finalized_private_payload():
    principal = AuthPrincipal(user_id="user-a", display_name="User", tenant_id="default", roles=["user"], source="test")
    snapshot = multi_agent_snapshot_from_steps(
        "run-parent",
        [
            {
                "id": "step-code",
                "run_id": "run-parent",
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Code",
                "role": "coder",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "dispatch_state": "completed",
                    "dispatch_child_run_id": "run-child",
                    "output": "safe output",
                    "private_payload": "hidden",
                    "storage_key": "tenant/default/private/object",
                    "worker_path": "/app/private.py",
                    "command_sha256": "a" * 64,
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ],
        principal=principal,
    )

    assert snapshot["counts"]["succeeded"] == 1
    dumped = json.dumps(snapshot, ensure_ascii=False)
    assert "safe output" in dumped
    assert "private_payload" not in dumped
    assert "storage_key" not in dumped
    assert "/app/private.py" not in dumped
    assert "command_sha256" not in dumped


def test_run_event_stream_heartbeat_includes_queue_insight_while_queued(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    calls = {"run": 0}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        calls["run"] += 1
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "queued" if calls["run"] == 1 else "succeeded",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        return {"tenant_id": tenant_id, "reason": "worker_capacity_full"}

    async def fake_get_run_queue_position(*, tenant_id, run_id):
        assert tenant_id == "tenant-a"
        assert run_id == "run-a"
        return 4

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight, raising=False)
    monkeypatch.setattr("app.routes.runs.get_run_queue_position", fake_get_run_queue_position, raising=False)
    monkeypatch.setattr("app.routes.runs.asyncio.sleep", no_sleep)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert "event: heartbeat" in response.text
    assert '"queue_position": 4' in response.text
    assert '"queue_insight": {"tenant_id": "tenant-a", "reason": "worker_capacity_full"}' in response.text


def test_run_event_stream_heartbeat_includes_cancel_request_metadata(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    calls = {"run": 0}

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        calls["run"] += 1
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "running" if calls["run"] == 1 else "succeeded",
            "cancel_requested_at": "2026-05-27T06:12:00Z",
            "cancel_requested_by": "admin-a",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.asyncio.sleep", no_sleep)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert "event: heartbeat" in response.text
    assert '"cancel_requested_at": "2026-05-27T06:12:00Z"' in response.text
    assert '"cancel_requested_by": "admin-a"' in response.text


def test_run_event_stream_uses_configured_long_task_heartbeat_window(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def auth_settings():
        return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()

    def stream_settings():
        return type("S", (), {"run_event_stream_max_heartbeats": 2})()

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            **RUN_SCHEMA_FIELDS,
            "status": "running",
            "result_json": {},
            "error_code": None,
            "error_message": None,
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id):
        return []

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        return {"tenant_id": tenant_id, "reason": "workers_busy"}

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.get_settings", stream_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_events", fake_list_run_events)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.get_queue_insight", fake_get_queue_insight, raising=False)
    monkeypatch.setattr("app.routes.runs.asyncio.sleep", no_sleep)

    client = TestClient(create_app())
    response = client.get(
        "/api/ai/runs/run-a/events/stream",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "tenant-a",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 200
    assert response.text.count("event: heartbeat") == 2
    assert '"error": "stream_timeout"' in response.text
    assert '"status": "timeout"' in response.text


def test_create_run_request_user_id_is_optional_legacy_field():
    request = CreateRunRequest(
        workspace_id="default",
        agent_id="qa-word-review",
        capability_id="document_review",
    )

    assert request.user_id is None


def test_resolve_run_selector_accepts_public_agent_ids_for_public_capabilities():
    agent_id, skill_id = resolve_run_selector(
        CreateRunRequest(
            workspace_id="default",
            agent_id="document-review",
            capability_id="document_review",
        ),
        principal=principal(),
    )

    assert (agent_id, skill_id) == ("qa-word-review", "qa-file-reviewer")


@pytest.mark.asyncio
async def test_create_run_ensures_user_before_session(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill()

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        calls.append(("ensure_user", tenant_id, user_id, display_name))

    async def fake_create_session(conn, **kwargs):
        calls.append(("create_session", kwargs["user_id"]))
        return "ses_1"

    async def fake_create_run(conn, **kwargs):
        return kwargs["run_id"]

    async def fake_bind_files_to_run(conn, **kwargs):
        calls.append(("bind_files_to_run", kwargs["user_id"]))

    async def noop(*args, **kwargs):
        return None

    async def fake_enqueue_run(payload):
        calls.append(("enqueue", payload["user_id"], payload["tenant_id"], payload["run_id"]))
        return 1

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.runs.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)

    response = await create_run(
        CreateRunRequest(
            tenant_id="forged-tenant",
            workspace_id="default",
            user_id="forged-user",
            agent_id="qa-word-review",
            capability_id="document_review",
        ),
        principal=principal(user_id="phaseb-smoke", display_name="Phase B Smoke", tenant_id="default"),
    )

    assert response.run_id.startswith("run_")
    assert calls[:2] == [
        ("ensure_user", "default", "phaseb-smoke", "Phase B Smoke"),
        ("create_session", "phaseb-smoke"),
    ]
    assert ("bind_files_to_run", "phaseb-smoke") in calls
    assert any(item[0:3] == ("enqueue", "phaseb-smoke", "default") and item[3].startswith("run_") for item in calls)


@pytest.mark.asyncio
async def test_create_run_strips_user_controlled_server_owned_metadata(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(executor_type="claude-agent-worker", skill_version="hash-a")

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        return 0

    async def fake_ensure_user(conn, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return kwargs["session_id"]

    async def fake_create_run(conn, **kwargs):
        calls["create_run_input"] = kwargs["input_json"]["input"]
        return kwargs["run_id"]

    async def fake_bind_files_to_run(conn, **kwargs):
        return None

    async def fake_record_initial_context_snapshot(conn, **kwargs):
        calls["context_input"] = kwargs["input_payload"]
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx-test",
            "source": kwargs["source"],
            "message_count": 0,
            "file_count": 0,
            "memory_record_count": 0,
        }

    async def fake_append_event(conn, **kwargs):
        return None

    async def fake_enqueue_run(payload):
        calls["queue_input"] = payload["input"]
        return 1

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        calls["manifest_input"] = input_payload
        return [{"skill_id": skill_id, "content_hash": "hash-a"}]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.runs.repositories.bind_files_to_run", fake_bind_files_to_run)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_initial_context_snapshot)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)

    response = await create_run(
        CreateRunRequest(
            workspace_id="default",
            agent_id="general-agent",
            capability_id="general_chat",
            input={
                "message": "run with forged resume",
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
                "multi_agent_dispatch": {
                    "orchestration_state": "awaiting_dispatch",
                    "parent_run_id": "run-other",
                    "dispatch_id": "dispatch-forged",
                },
            },
        ),
        principal=principal(user_id="user-a", tenant_id="tenant-a"),
    )

    assert response.status == "queued"
    for key in ("manifest_input", "create_run_input", "context_input", "queue_input"):
        assert calls[key]["message"] == "run with forged resume"
        assert "resume" not in calls[key]
        assert "multi_agent_dispatch" not in calls[key]


@pytest.mark.asyncio
async def test_create_run_rejects_file_skill_without_files(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(input_modes=["docx"])

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)

    with pytest.raises(Exception) as exc_info:
        await create_run(
            CreateRunRequest(
                workspace_id="default",
                agent_id="baoyu-translate",
                capability_id="document_translation",
                file_ids=[],
            ),
            principal=principal(),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "file_required_for_skill"


@pytest.mark.asyncio
async def test_create_run_rejects_when_user_active_run_limit_is_reached(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append("resolve")
        return skill()

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        raise RepositoryConflictError("user_active_run_limit_exceeded")

    async def fail_create_session(*args, **kwargs):
        calls.append("create_session")
        raise AssertionError("session must not be created after admission rejection")

    class LimitSettings:
        max_active_runs_per_user = 3

    monkeypatch.setattr("app.routes.runs.get_settings", lambda: LimitSettings())
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fail_create_session)

    with pytest.raises(Exception) as exc_info:
        await create_run(
            CreateRunRequest(workspace_id="default", agent_id="general-agent", capability_id="general_chat"),
            principal=principal(user_id="user-limit", tenant_id="tenant-a"),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "user_active_run_limit_exceeded"
    assert calls == ["resolve", ("admit", "tenant-a", "user-limit", 3)]


@pytest.mark.asyncio
async def test_create_run_rejects_raw_skill_selector_for_ordinary_user(monkeypatch):
    async def fail_resolve_agent_skill(*args, **kwargs):
        raise AssertionError("raw skill selector must be rejected before skill resolution")

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fail_resolve_agent_skill)

    with pytest.raises(Exception) as exc_info:
        await create_run(
            CreateRunRequest(workspace_id="default", agent_id="qa-word-review", skill_id="qa-file-reviewer"),
            principal=principal(),
        )

    assert getattr(exc_info.value, "status_code", None) == 403
    assert getattr(exc_info.value, "detail", None) == "raw_skill_selector_forbidden"


@pytest.mark.asyncio
async def test_create_run_strips_nested_raw_skill_selectors_for_ordinary_user(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(executor_type="claude-agent-worker", skill_version="2.0.0")

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_1"

    async def fake_create_run(conn, **kwargs):
        calls["run_input"] = kwargs["input_json"]["input"]
        return "run_1"

    async def fake_enqueue_run(payload):
        calls["queue_input"] = payload["input"]
        calls["queue_payload"] = payload
        return 1

    async def fake_record_context(conn, **kwargs):
        calls["context"] = kwargs
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_run_clean",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.runs.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", noop)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)

    response = await create_run(
        CreateRunRequest(
            workspace_id="default",
            agent_id="general-agent",
            capability_id="general_chat",
            input={
                "message": "run",
                "skill_ids": ["qa-file-reviewer"],
                "executor_type": "runtime211",
                "multi_agent_steps": [
                    {
                        "step_key": "review",
                        "skill_ids": ["qa-file-reviewer"],
                        "worker_path": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
                    }
                ],
            },
        ),
        principal=principal(),
    )

    assert response.run_id == "run_1"
    assert "skill_ids" not in calls["run_input"]
    assert "executor_type" not in calls["run_input"]
    assert "skill_ids" not in calls["run_input"]["multi_agent_steps"][0]
    assert "worker_path" not in calls["run_input"]["multi_agent_steps"][0]
    assert calls["queue_input"] == calls["run_input"]
    assert calls["queue_payload"]["skill_version"] == calls["queue_payload"]["skill_manifests"][0]["content_hash"]
    assert calls["queue_payload"]["skill_manifests"][0]["skill_id"] == "general-chat"
    assert calls["queue_payload"]["release_decision"]["selected_version"] == calls["queue_payload"]["skill_version"]
    assert calls["queue_payload"]["release_decision"]["selected_track"] == "manifest_pin"
    assert calls["context"]["source"] == "runs_api"
    assert calls["context"]["input_payload"] == calls["run_input"]
    assert calls["context"]["message_ids"] == []
    assert calls["context"]["file_ids"] == []
    assert calls["queue_payload"]["context_snapshot_id"] == "ctx_run_clean"
    assert calls["queue_payload"]["context_snapshot"]["source"] == "runs_api"


@pytest.mark.asyncio
async def test_create_run_uses_primary_pin_hash_as_locked_skill_version(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(executor_type="claude-agent-worker", skill_version="db-version")

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_pin"

    async def fake_create_run(conn, **kwargs):
        calls["create_run"] = kwargs
        return "run_pin"

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 1

    def fake_skill_manifest_pins(skill_id, input_payload):
        assert skill_id == "qa-file-reviewer"
        return [
            {
                "skill_id": "qa-file-reviewer",
                "version": "hash-pin",
                "content_hash": "hash-pin",
                "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                "files": [],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.runs.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    response = await create_run(
        CreateRunRequest(
            workspace_id="default",
            agent_id="qa-word-review",
            capability_id="document_review",
            input={"message": "审核"},
        ),
        principal=principal(),
    )

    assert response.run_id == "run_pin"
    assert calls["create_run"]["input_json"]["skill_version"] == "hash-pin"
    assert calls["create_run"]["input_json"]["release_decision"]["selected_version"] == "hash-pin"
    assert calls["create_run"]["input_json"]["release_decision"]["selected_track"] == "manifest_pin"
    assert calls["queue"]["skill_version"] == "hash-pin"
    assert calls["queue"]["release_decision"]["selected_version"] == "hash-pin"
    assert calls["queue"]["skill_manifests"][0]["content_hash"] == "hash-pin"
    assert any(event["payload"]["skill_version"] == "hash-pin" for event in calls["events"])


@pytest.mark.asyncio
async def test_create_run_uses_rollout_selected_previous_version(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(
            executor_type="claude-agent-worker",
            skill_version="hash-new",
            release_policy_version="hash-new",
            release_policy_previous_version="hash-old",
            release_policy_rollout_percent=0,
        )

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_rollout"

    async def fake_create_run(conn, **kwargs):
        calls["create_run"] = kwargs
        return "run_rollout"

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 1

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [
            {
                "skill_id": skill_id,
                "version": "hash-new",
                "content_hash": "hash-new",
                "source": {"kind": "builtin", "asset_dir": skill_id},
                "files": [],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert version == "hash-old"
        return uploaded_skill_version_row(skill_id=skill_id, version=version)

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.runs.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    response = await create_run(
        CreateRunRequest(
            workspace_id="default",
            agent_id="qa-word-review",
            capability_id="document_review",
            input={"message": "审核"},
        ),
        principal=principal(user_id="user-rollout"),
    )

    assert response.run_id == "run_rollout"
    assert calls["create_run"]["input_json"]["skill_version"] == "hash-old"
    assert calls["create_run"]["input_json"]["release_decision"]["selected_version"] == "hash-old"
    assert calls["create_run"]["input_json"]["release_decision"]["selected_track"] == "previous"
    assert calls["create_run"]["input_json"]["release_decision"]["rollout_percent"] == 0
    assert calls["queue"]["skill_version"] == "hash-old"
    assert calls["queue"]["release_decision"]["selected_track"] == "previous"
    assert calls["queue"]["skill_manifests"][0]["content_hash"] == "hash-old"
    assert any(event["payload"]["skill_version"] == "hash-old" for event in calls["events"])
    assert any(
        event["event_type"] == "skill_release_decision"
        and event["payload"]["selected_version"] == "hash-old"
        and event["payload"]["visible_to_user"] is False
        for event in calls["events"]
    )


@pytest.mark.asyncio
async def test_create_run_rejects_release_policy_version_that_differs_from_primary_pin(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(
            executor_type="claude-agent-worker",
            skill_version="old-release-version",
            release_policy_version="old-release-version",
        )

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [
            {
                "skill_id": "qa-file-reviewer",
                "version": "current-hash",
                "content_hash": "current-hash",
                "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                "files": [],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        return None

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    with pytest.raises(Exception) as exc_info:
        await create_run(
            CreateRunRequest(
                workspace_id="default",
                agent_id="qa-word-review",
                capability_id="document_review",
                input={"message": "审核"},
            ),
            principal=principal(),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_materializable"


@pytest.mark.asyncio
async def test_create_run_uses_uploaded_release_policy_manifest(monkeypatch, tmp_path):
    calls = {}
    dependency_dir = tmp_path / "minimax-docx"
    dependency_dir.mkdir()
    dependency_skill_md = "---\nname: minimax-docx\ndescription: DOCX helper\n---\n\n# minimax-docx\n"
    (dependency_dir / "SKILL.md").write_text(dependency_skill_md, encoding="utf-8")
    dependency_version = "hash-live-dependency"
    pinned_dependency_manifest = snapshot_manifest("minimax-docx", description="Pinned DOCX helper")

    class DependencyBuiltinRegistry:
        def __init__(self, root):
            self.root = root

        def list_builtin_skills(self):
            return [
                BuiltinSkill(
                    name="minimax-docx",
                    description="DOCX helper",
                    path=dependency_dir,
                    version=dependency_version,
                    source={"kind": "builtin", "asset_dir": "minimax-docx", "version": dependency_version},
                    entry={"kind": "filesystem", "path": str(dependency_dir)},
                )
            ]

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(
            executor_type="claude-agent-worker",
            skill_version="hash-uploaded",
            release_policy_version="hash-uploaded",
        )

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert skill_id == "qa-file-reviewer"
        assert version == "hash-uploaded"
        return uploaded_skill_version_row(
            skill_id=skill_id,
            version=version,
            dependency_ids=["minimax-docx"],
            dependency_manifests=[pinned_dependency_manifest],
        )

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_uploaded"

    async def fake_create_run(conn, **kwargs):
        calls["create_run"] = kwargs
        return "run_uploaded"

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 1

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.BuiltinSkillRegistry", DependencyBuiltinRegistry)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.runs.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)

    response = await create_run(
        CreateRunRequest(
            workspace_id="default",
            agent_id="qa-word-review",
            capability_id="document_review",
            input={"message": "审核"},
        ),
        principal=principal(),
    )

    assert response.run_id == "run_uploaded"
    assert calls["create_run"]["input_json"]["skill_version"] == "hash-uploaded"
    assert calls["queue"]["skill_version"] == "hash-uploaded"
    assert [item["skill_id"] for item in calls["queue"]["skill_manifests"]] == ["qa-file-reviewer", "minimax-docx"]
    assert calls["queue"]["skill_manifests"][0]["source"]["kind"] == "uploaded"
    assert calls["queue"]["skill_manifests"][0]["files"][0]["relative_path"] == "SKILL.md"
    assert calls["queue"]["skill_manifests"][1]["content_hash"] == pinned_dependency_manifest["content_hash"]
    assert calls["queue"]["skill_manifests"][1]["files"][0]["relative_path"] == "SKILL.md"
    assert any(event["payload"]["skill_version"] == "hash-uploaded" for event in calls["events"])


@pytest.mark.asyncio
async def test_create_run_uses_builtin_snapshot_release_policy_manifest(monkeypatch):
    calls = {}

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(
            executor_type="claude-agent-worker",
            skill_version="hash-new",
            release_policy_version="hash-new",
            release_policy_previous_version="hash-old-builtin",
            release_policy_rollout_percent=0,
        )

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [
            {
                "skill_id": skill_id,
                "version": "hash-new",
                "content_hash": "hash-new",
                "source": {"kind": "builtin", "asset_dir": skill_id, "version": "hash-new"},
                "files": [],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert version == "hash-old-builtin"
        return builtin_snapshot_skill_version_row(
            skill_id=skill_id,
            version=version,
            dependency_ids=["minimax-docx"],
            dependency_manifests=[snapshot_manifest("minimax-docx", description="Pinned DOCX helper")],
        )

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(conn, **kwargs):
        return "ses_builtin_snapshot"

    async def fake_create_run(conn, **kwargs):
        calls["create_run"] = kwargs
        return "run_builtin_snapshot"

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 1

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fake_create_run)
    monkeypatch.setattr("app.routes.runs.repositories.bind_files_to_run", noop)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    response = await create_run(
        CreateRunRequest(
            workspace_id="default",
            agent_id="qa-word-review",
            capability_id="document_review",
            input={"message": "审核"},
        ),
        principal=principal(user_id="user-rollout-builtin"),
    )

    assert response.run_id == "run_builtin_snapshot"
    assert calls["create_run"]["input_json"]["skill_version"] == "hash-old-builtin"
    assert calls["queue"]["skill_version"] == "hash-old-builtin"
    assert calls["queue"]["skill_manifests"][0]["content_hash"] == "hash-old-builtin"
    assert calls["queue"]["skill_manifests"][0]["source"]["kind"] == "builtin"
    assert calls["queue"]["skill_manifests"][0]["files"][0]["relative_path"] == "SKILL.md"
    assert any(event["payload"]["skill_version"] == "hash-old-builtin" for event in calls["events"])


@pytest.mark.asyncio
async def test_create_run_prevalidates_queue_payload_before_persisting(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(executor_type="claude-agent-worker", skill_version="hash-primary")

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        return [{"skill_id": skill_id, "content_hash": "hash-primary"}]

    async def noop(*args, **kwargs):
        return None

    async def fake_create_session(*args, **kwargs):
        return "ses_invalid"

    async def fail_create_run(*args, **kwargs):
        raise AssertionError("run must not be created when queue payload validation fails")

    class RejectingQueueRunPayload:
        @classmethod
        def model_validate(cls, payload):
            class QueuePayloadError(ValueError):
                def errors(self):
                    return [
                        {
                            "loc": ("release_decision",),
                            "type": "value_error",
                            "msg": "bad token=run-secret-token at /var/lib/ai-platform/private/run.log",
                        }
                    ]

            raise QueuePayloadError("queue_payload_invalid")

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr("app.routes.runs._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)
    monkeypatch.setattr("app.routes.runs.repositories.ensure_user", noop)
    monkeypatch.setattr("app.routes.runs.repositories.create_session", fake_create_session)
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fail_create_run)
    monkeypatch.setattr("app.routes.runs.QueueRunPayload", RejectingQueueRunPayload, raising=False)

    with pytest.raises(Exception) as exc_info:
        await create_run(
            CreateRunRequest(
                workspace_id="default",
                agent_id="qa-word-review",
                capability_id="document_review",
                input={"message": "审核"},
            ),
            principal=principal(),
        )

    assert getattr(exc_info.value, "status_code", None) == 500
    assert getattr(exc_info.value, "detail", None) == {
        "code": "queue_payload_invalid",
        "errors": [
            {
                "loc": ["release_decision"],
                "type": "value_error",
                "message": "validation_error",
            }
        ],
    }
    assert "run-secret-token" not in str(exc_info.value.detail)
    assert "/var/lib/ai-platform/private/run.log" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_create_run_rejects_unsafe_principal_user_id_before_persistence(monkeypatch):
    calls = []

    @asynccontextmanager
    async def fail_transaction():
        calls.append("transaction")
        raise AssertionError("unsafe principal user_id should fail before opening a transaction")
        yield object()

    monkeypatch.setattr("app.routes.runs.transaction", fail_transaction)

    with pytest.raises(HTTPException) as exc_info:
        await create_run(
            CreateRunRequest(
                workspace_id="default",
                agent_id="qa-word-review",
                capability_id="document_review",
                input={"message": "审核"},
            ),
            principal=principal(user_id="../alice@example.test"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid_principal_user_id"
    assert calls == []


@pytest.mark.asyncio
async def test_create_run_rejects_uploaded_release_policy_without_snapshot_files(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(
            executor_type="claude-agent-worker",
            skill_version="hash-uploaded",
            release_policy_version="hash-uploaded",
        )

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        return uploaded_skill_version_row(skill_id=skill_id, version=version, files=[])

    async def fail_create_run(*args, **kwargs):
        raise AssertionError("run must not be created when uploaded snapshot cannot be materialized")

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.BuiltinSkillRegistry", PolicyBuiltinRegistry)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs.repositories.create_run", fail_create_run)

    with pytest.raises(Exception) as exc_info:
        await create_run(
            CreateRunRequest(
                workspace_id="default",
                agent_id="qa-word-review",
                capability_id="document_review",
                input={"message": "审核"},
            ),
            principal=principal(),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_materializable"


@pytest.mark.asyncio
async def test_create_run_maps_skill_snapshot_materialization_error_to_conflict(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return skill(
            executor_type="claude-agent-worker",
            skill_version="hash-release",
            release_policy_version="hash-release",
        )

    class FakeRegistry:
        def __init__(self, root):
            self.root = root

        def list_builtin_skills(self):
            return [object()]

    def fail_build_skill_manifest_pins(**kwargs):
        raise ValueError("skill snapshot too large")

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        return None

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs.BuiltinSkillRegistry", FakeRegistry)
    monkeypatch.setattr("app.routes.runs.build_skill_manifest_pins", fail_build_skill_manifest_pins)

    with pytest.raises(Exception) as exc_info:
        await create_run(
            CreateRunRequest(
                workspace_id="default",
                agent_id="qa-word-review",
                capability_id="document_review",
                input={"message": "审核"},
            ),
            principal=principal(),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_materializable"


@pytest.mark.asyncio
async def test_copy_run_uses_primary_pin_hash_as_locked_skill_version(monkeypatch):
    calls = {}

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses_copy",
            "run_id": "run_copy",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "workspace_id": "default",
            "file_ids": ["file_1"],
            "input": {"message": "继续审核", "copied_from_run_id": run_id},
            "executor_type": "claude-agent-worker",
            "skill_version": "db-version",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "db-version",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_update_run_input_skill_version(conn, *, tenant_id, run_id, skill_version):
        calls["update"] = {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "skill_version": skill_version,
        }

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_record_context(conn, **kwargs):
        calls["context"] = kwargs
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_copy",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_seed_copied_run_steps(*args, **kwargs):
        calls["seed"] = kwargs

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 2

    async def fake_queue_insight_for_status(status, tenant_id, **_kwargs):
        return {"status": status, "tenant_id": tenant_id}

    def fake_skill_manifest_pins(skill_id, input_payload):
        assert skill_id == "qa-file-reviewer"
        assert input_payload == {"message": "继续审核", "copied_from_run_id": "run_source"}
        return [
            {
                "skill_id": "qa-file-reviewer",
                "version": "hash-pin",
                "content_hash": "hash-pin",
                "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                "files": [],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.runs.seed_copied_run_steps", fake_seed_copied_run_steps)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight_for_status)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    response = await copy_run("run_source", principal=principal())

    assert response.run_id == "run_copy"
    assert calls["update"]["skill_version"] == "hash-pin"
    assert calls["context"]["source"] == "copy_run"
    assert calls["context"]["source_run_id"] == "run_source"
    assert calls["context"]["tenant_id"] == "tenant-a"
    assert calls["context"]["workspace_id"] == "default"
    assert calls["context"]["user_id"] == "user-a"
    assert calls["context"]["session_id"] == "ses_copy"
    assert calls["context"]["run_id"] == "run_copy"
    assert calls["context"]["agent_id"] == "qa-word-review"
    assert calls["context"]["skill_id"] == "qa-file-reviewer"
    assert calls["context"]["input_payload"] == {"message": "继续审核", "copied_from_run_id": "run_source"}
    assert calls["context"]["message_ids"] == []
    assert calls["context"]["file_ids"] == ["file_1"]
    assert calls["queue"]["context_snapshot_id"] == "ctx_copy"
    assert calls["queue"]["context_snapshot"]["source"] == "copy_run"
    assert calls["queue"]["skill_version"] == "hash-pin"
    assert calls["queue"]["skill_manifests"][0]["content_hash"] == "hash-pin"
    assert any(event["payload"]["skill_version"] == "hash-pin" for event in calls["events"])


@pytest.mark.asyncio
async def test_copy_run_ignores_unsafe_source_run_id_for_followup_context(monkeypatch):
    calls = {}
    unsafe_hash_like_run_id = "a" * 64

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses_copy",
            "run_id": "run_copy",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "workspace_id": "default",
            "file_ids": ["file_1"],
            "input": {
                "message": "继续审核",
                "copied_from_run_id": unsafe_hash_like_run_id,
                "resume": {"copied_from_run_id": unsafe_hash_like_run_id},
            },
            "executor_type": "claude-agent-worker",
            "skill_version": "db-version",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "db-version",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_update_run_input_skill_version(conn, *, tenant_id, run_id, skill_version):
        calls["update"] = skill_version

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_record_context(conn, **kwargs):
        calls["context"] = kwargs
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_copy",
            "source": kwargs["source"],
            "message_count": 0,
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_seed_copied_run_steps(*args, **kwargs):
        calls["seed"] = kwargs

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 2

    async def fake_queue_insight_for_status(status, tenant_id, **_kwargs):
        return {"status": status, "tenant_id": tenant_id}

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [{"skill_id": skill_id, "content_hash": "hash-pin"}]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.runs.seed_copied_run_steps", fake_seed_copied_run_steps)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight_for_status)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    response = await copy_run("run_source", principal=principal())

    assert response.run_id == "run_copy"
    assert calls["context"]["source"] == "copy_run"
    assert calls["context"]["source_run_id"] == "run_source"
    assert calls["queue"]["context_snapshot_id"] == "ctx_copy"
    assert calls["queue"]["skill_version"] == "hash-pin"


@pytest.mark.asyncio
async def test_copy_run_uses_authorized_route_source_when_copied_input_lacks_source_id(monkeypatch):
    calls = {}

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses_copy",
            "run_id": "run_copy",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "workspace_id": "default",
            "file_ids": ["file_1"],
            "input": {"message": "继续审核"},
            "executor_type": "claude-agent-worker",
            "skill_version": "db-version",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "db-version",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_update_run_input_skill_version(conn, *, tenant_id, run_id, skill_version):
        calls["update"] = skill_version

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_record_context(conn, **kwargs):
        calls["context"] = kwargs
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_copy",
            "source": kwargs["source"],
            "message_count": 0,
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_seed_copied_run_steps(*args, **kwargs):
        calls["seed"] = kwargs

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 2

    async def fake_queue_insight_for_status(status, tenant_id, **_kwargs):
        return {"status": status, "tenant_id": tenant_id}

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [{"skill_id": skill_id, "content_hash": "hash-pin"}]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.runs.seed_copied_run_steps", fake_seed_copied_run_steps)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight_for_status)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    response = await copy_run("run_source", principal=principal())

    assert response.run_id == "run_copy"
    assert calls["context"]["source"] == "copy_run"
    assert calls["context"]["source_run_id"] == "run_source"
    assert calls["queue"]["context_snapshot_id"] == "ctx_copy"


@pytest.mark.asyncio
async def test_copy_run_prefers_authorized_route_source_over_payload_source_id(monkeypatch):
    calls = {}

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses_copy",
            "run_id": "run_copy",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "workspace_id": "default",
            "file_ids": ["file_1"],
            "input": {"message": "继续审核", "copied_from_run_id": "run_older_ancestor"},
            "executor_type": "claude-agent-worker",
            "skill_version": "db-version",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "db-version",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_update_run_input_skill_version(conn, *, tenant_id, run_id, skill_version):
        calls["update"] = skill_version

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_record_context(conn, **kwargs):
        calls["context"] = kwargs
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_copy",
            "source": kwargs["source"],
            "message_count": 0,
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    async def fake_seed_copied_run_steps(*args, **kwargs):
        calls["seed"] = kwargs

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 2

    async def fake_queue_insight_for_status(status, tenant_id, **_kwargs):
        return {"status": status, "tenant_id": tenant_id}

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [{"skill_id": skill_id, "content_hash": "hash-pin"}]

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_context)
    monkeypatch.setattr("app.routes.runs.seed_copied_run_steps", fake_seed_copied_run_steps)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight_for_status)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    response = await copy_run("run_source", principal=principal())

    assert response.run_id == "run_copy"
    assert calls["context"]["source"] == "copy_run"
    assert calls["context"]["source_run_id"] == "run_source"
    assert calls["queue"]["context_snapshot_id"] == "ctx_copy"


@pytest.mark.asyncio
async def test_copy_run_prevalidates_queue_payload_before_seeding_reused_steps(monkeypatch):
    calls = {}

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses_copy",
            "run_id": "run_copy",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "workspace_id": "default",
            "file_ids": ["file_1"],
            "input": {
                "message": "继续审核",
                "resume": {
                    "copied_from_run_id": "run_source",
                    "completed_step_outputs": {"inspect": "done"},
                },
            },
            "executor_type": "claude-agent-worker",
            "skill_version": "db-version",
        }

    async def fake_update_run_input_skill_version(conn, *, tenant_id, run_id, skill_version):
        calls["update"] = skill_version

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fail_seed_copied_run_steps(*args, **kwargs):
        raise AssertionError("copy-run steps must not be seeded before queue payload validation")

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [{"skill_id": skill_id, "content_hash": "hash-pin"}]

    class RejectingQueueRunPayload:
        @classmethod
        def model_validate(cls, payload):
            class QueuePayloadError(ValueError):
                def errors(self):
                    return [
                        {
                            "loc": ("skill_manifests", 0),
                            "type": "value_error",
                            "msg": "bad token=copy-secret-token at /var/lib/ai-platform/private/copy.log",
                        }
                    ]

            raise QueuePayloadError("queue_payload_invalid")

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.seed_copied_run_steps", fail_seed_copied_run_steps)
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)
    monkeypatch.setattr("app.routes.runs.QueueRunPayload", RejectingQueueRunPayload, raising=False)

    with pytest.raises(Exception) as exc_info:
        await copy_run("run_source", principal=principal())

    assert getattr(exc_info.value, "status_code", None) == 500
    assert getattr(exc_info.value, "detail", None) == {
        "code": "queue_payload_invalid",
        "errors": [
            {
                "loc": ["skill_manifests", 0],
                "type": "value_error",
                "message": "validation_error",
            }
        ],
    }
    assert "copy-secret-token" not in str(exc_info.value.detail)
    assert "/var/lib/ai-platform/private/copy.log" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_copy_run_rejects_unsafe_principal_user_id_before_copy_persistence(monkeypatch):
    calls = []

    @asynccontextmanager
    async def fail_transaction():
        calls.append("transaction")
        raise AssertionError("unsafe principal user_id should fail before copy persistence")
        yield object()

    monkeypatch.setattr("app.routes.runs.transaction", fail_transaction)

    with pytest.raises(HTTPException) as exc_info:
        await copy_run("run_source", principal=principal(user_id="../alice@example.test"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid_principal_user_id"
    assert calls == []


@pytest.mark.asyncio
async def test_copy_run_uses_uploaded_release_policy_manifest(monkeypatch):
    calls = {}

    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses_copy",
            "run_id": "run_copy",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "workspace_id": "default",
            "file_ids": ["file_1"],
            "input": {"message": "继续审核"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-uploaded",
            "release_policy_version": "hash-uploaded",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-uploaded",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        assert skill_id == "qa-file-reviewer"
        assert version == "hash-uploaded"
        return uploaded_skill_version_row(skill_id=skill_id, version=version)

    async def fake_update_run_input_skill_version(conn, *, tenant_id, run_id, skill_version):
        calls["update"] = {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "skill_version": skill_version,
        }

    async def fake_append_event(conn, **kwargs):
        calls.setdefault("events", []).append(kwargs)

    async def fake_seed_copied_run_steps(*args, **kwargs):
        calls["seed"] = kwargs

    async def fake_enqueue_run(payload):
        calls["queue"] = payload
        return 2

    async def fake_queue_insight_for_status(status, tenant_id, **_kwargs):
        return {"status": status, "tenant_id": tenant_id}

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.BuiltinSkillRegistry", PolicyBuiltinRegistry)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs.repositories.update_run_input_skill_version", fake_update_run_input_skill_version)
    monkeypatch.setattr("app.routes.runs.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.routes.runs.seed_copied_run_steps", fake_seed_copied_run_steps)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight_for_status)

    response = await copy_run("run_source", principal=principal())

    assert response.run_id == "run_copy"
    assert calls["update"]["skill_version"] == "hash-uploaded"
    assert calls["queue"]["skill_version"] == "hash-uploaded"
    assert calls["queue"]["skill_manifests"][0]["source"]["kind"] == "uploaded"
    assert calls["queue"]["skill_manifests"][0]["files"][0]["relative_path"] == "SKILL.md"
    assert any(event["payload"]["skill_version"] == "hash-uploaded" for event in calls["events"])


@pytest.mark.asyncio
async def test_copy_run_rejects_when_original_skill_version_cannot_be_materialized(monkeypatch):
    async def fake_copy_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        return {
            "session_id": "ses_copy",
            "run_id": "run_copy",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "workspace_id": "default",
            "file_ids": ["file_1"],
            "input": {"message": "继续审核"},
            "executor_type": "claude-agent-worker",
            "skill_version": "old-release-version",
            "release_policy_version": "old-release-version",
        }

    def fake_skill_manifest_pins(skill_id, input_payload):
        return [
            {
                "skill_id": "qa-file-reviewer",
                "version": "current-hash",
                "content_hash": "current-hash",
                "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                "files": [],
                "dependency_ids": [],
                "allowed": True,
                "staged": False,
                "used": False,
            }
        ]

    async def fake_get_effective_skill_version_for_policy(conn, *, skill_id, version):
        return None

    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fake_copy_run_as_new_task)
    monkeypatch.setattr(
        "app.routes.runs.repositories.get_effective_skill_version_for_policy",
        fake_get_effective_skill_version_for_policy,
    )
    monkeypatch.setattr("app.routes.runs._skill_manifest_pins", fake_skill_manifest_pins)

    with pytest.raises(Exception) as exc_info:
        await copy_run("run_source", principal=principal())

    assert getattr(exc_info.value, "status_code", None) == 409
    assert getattr(exc_info.value, "detail", None) == "skill_version_not_materializable"
