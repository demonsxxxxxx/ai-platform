import base64
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import repositories as repository_module
from app.auth import AuthPrincipal
from app.models import ChatStreamRequest
from app.routes.chat import chat_stream as _route_chat_stream


_TEST_STREAM_REQUEST = SimpleNamespace(
    app=SimpleNamespace(
        state=SimpleNamespace(
            run_stream_runtime=SimpleNamespace(worker_capabilities=object())
        )
    )
)


async def chat_stream(*args, **kwargs):
    kwargs.setdefault("http_request", _TEST_STREAM_REQUEST)
    return await _route_chat_stream(*args, **kwargs)


@asynccontextmanager
async def fake_transaction():
    yield object()


def principal(**overrides):
    values = {"user_id": "user-a", "display_name": "User A", "tenant_id": "tenant-a"}
    values.update(overrides)
    return AuthPrincipal(**values)


def snapshot_manifest(skill_id, *, description="Pinned skill"):
    content = f"---\nname: {skill_id}\ndescription: {description}\n---\n\n# {skill_id}\n".encode()
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
        "source": {"kind": "builtin", "asset_dir": skill_id, "version": version},
        "files": files,
        "dependency_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }


@pytest.fixture(autouse=True)
def default_chat_stream_dependencies(monkeypatch):
    async def no_submission(*_args, **_kwargs):
        return None

    async def authorize_files(*_args, **_kwargs):
        return None

    async def ensure_workspace(*_args, **_kwargs):
        return None

    async def ensure_submission_principal(*_args, **_kwargs):
        return None

    async def no_latest_run_input(*_args, **_kwargs):
        return None

    async def fake_acquire_user_active_run_admission_lock(conn, *, tenant_id, user_id):
        return None

    async def fake_enforce_user_active_run_admission_under_lock(conn, *, tenant_id, user_id, limit):
        return 0

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        return 0

    async def fake_get_queue_insight(tenant_id, **_kwargs):
        return {
            "tenant_id": tenant_id,
            "reason": "queued_behind_existing_work",
            "depths": {"tenant_queued": 0, "tenant_processing": 0},
            "workers": {"active": 0},
            "capacity": {"available_worker_slots": None},
        }

    async def fake_record_initial_context_snapshot(conn, **kwargs):
        return {
            "schema_version": "ai-platform.context-snapshot.v1",
            "context_snapshot_id": "ctx_chat_test",
            "source": kwargs["source"],
            "message_count": len(kwargs.get("message_ids") or []),
            "file_count": len(kwargs.get("file_ids") or []),
            "memory_record_count": 0,
        }

    monkeypatch.setattr(
        "app.routes.chat.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr(repository_module, "get_chat_submission", no_submission)
    monkeypatch.setattr(repository_module, "authorize_files_for_run", authorize_files, raising=False)
    monkeypatch.setattr(
        repository_module,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace,
        raising=False,
    )
    monkeypatch.setattr(
        repository_module,
        "ensure_submission_principal",
        ensure_submission_principal,
    )
    monkeypatch.setattr(
        repository_module,
        "get_latest_authorized_session_run_input",
        no_latest_run_input,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.acquire_user_active_run_admission_lock",
        fake_acquire_user_active_run_admission_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.routes.chat.repositories.enforce_user_active_run_admission_under_lock",
        fake_enforce_user_active_run_admission_under_lock,
        raising=False,
    )
    monkeypatch.setattr("app.routes.chat.get_queue_insight", fake_get_queue_insight, raising=False)
    monkeypatch.setattr(
        "app.routes.chat.record_initial_context_snapshot",
        fake_record_initial_context_snapshot,
        raising=False,
    )


@pytest.mark.asyncio
async def test_chat_stream_explicit_selected_skill_survives_scoped_negative_prompt(monkeypatch):
    calls = {}
    manifests = {
        skill_id: snapshot_manifest(skill_id)
        for skill_id in ("general-chat", "internal-comms")
    }
    selected_version = manifests["internal-comms"]["version"]

    def skill_row(skill_id):
        manifest = manifests[skill_id]
        return {
            "agent_id": "general-agent",
            "default_skill_id": "general-chat",
            "skill_id": skill_id,
            "skill_display_label": skill_id,
            "executor_type": "claude-agent-worker",
            "skill_version": manifest["version"],
            "skill_content_hash": manifest["content_hash"],
            "input_modes": ["chat"],
        }

    async def authorize_selected(*_args, **kwargs):
        calls["selected_authorization"] = {
            "agent_id": kwargs["agent_id"],
            "skill_id": kwargs["skill_id"],
            "expected_version": kwargs["expected_version"],
        }
        return skill_row(kwargs["skill_id"])

    async def authorize_default(*_args, **kwargs):
        calls["default_authorization"] = {
            "agent_id": kwargs["agent_id"],
            "skill_id": kwargs["skill_id"],
        }
        return skill_row(kwargs["skill_id"])

    async def governed_manifests(*_args, **kwargs):
        calls["manifest_skill_id"] = kwargs["skill_id"]
        return [dict(manifests[kwargs["skill_id"]])]

    async def create_session(*_args, **_kwargs):
        return "ses-explicit-skill"

    async def create_run(*_args, **kwargs):
        calls["run"] = {
            "agent_id": kwargs["agent_id"],
            "skill_id": kwargs["skill_id"],
            "skill_manifests": kwargs["input_json"]["skill_manifests"],
        }
        return "run-explicit-skill"

    async def insert_creation_snapshots(*_args, **kwargs):
        calls["snapshot_manifests"] = kwargs["skill_manifests"]

    async def append_message(*_args, **kwargs):
        calls["message_metadata"] = kwargs["metadata_json"]
        return "msg-explicit-skill"

    async def append_event(*_args, **kwargs):
        calls.setdefault("events", []).append(
            (kwargs["event_type"], kwargs.get("payload", {}))
        )
        return f"evt-{len(calls['events'])}"

    async def enqueue(payload):
        calls["queue_payload"] = payload
        return 1

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(
        repository_module,
        "authorize_selected_run_capabilities",
        authorize_selected,
    )
    monkeypatch.setattr(repository_module, "authorize_run_capabilities", authorize_default)
    monkeypatch.setattr("app.routes.chat._governed_skill_manifest_pins", governed_manifests)
    monkeypatch.setattr(repository_module, "ensure_user", noop)
    monkeypatch.setattr(repository_module, "create_session", create_session)
    monkeypatch.setattr(repository_module, "create_run", create_run)
    monkeypatch.setattr(
        repository_module,
        "insert_run_skill_snapshots_at_creation",
        insert_creation_snapshots,
    )
    monkeypatch.setattr(repository_module, "append_message", append_message)
    monkeypatch.setattr(repository_module, "bind_files_to_run", noop)
    monkeypatch.setattr(repository_module, "append_event", append_event)
    monkeypatch.setattr("app.routes.chat.enqueue_run", enqueue)

    response = await chat_stream(
        ChatStreamRequest(
            message=(
                "Run internal-comms Skill for this announcement; "
                "do not use MCP or create files."
            ),
            selected_skill={
                "skill_id": "internal-comms",
                "expected_version": selected_version,
            },
        ),
        principal=principal(department_id="communications", roles=["employee"]),
    )

    assert response.status == "queued"
    assert calls.get("selected_authorization") == {
        "agent_id": "general-agent",
        "skill_id": "internal-comms",
        "expected_version": selected_version,
    }
    assert "default_authorization" not in calls
    assert calls["manifest_skill_id"] == "internal-comms"
    assert calls["run"]["skill_id"] == "internal-comms"
    assert calls["run"]["skill_manifests"][0]["skill_id"] == "internal-comms"
    assert calls["snapshot_manifests"][0]["skill_id"] == "internal-comms"
    assert calls["queue_payload"]["skill_id"] == "internal-comms"
    assert calls["queue_payload"]["skill_version"] == selected_version
    assert calls["queue_payload"]["skill_manifests"][0]["skill_id"] == "internal-comms"
    selected_events = [
        payload for event_type, payload in calls["events"] if event_type == "skill_selected"
    ]
    assert len(selected_events) == 1
    assert selected_events[0]["agent_id"] == "general-agent"
    assert selected_events[0]["skill_id"] == "internal-comms"
    assert selected_events[0]["skill_version"] == selected_version
    assert calls["message_metadata"]["locked_skill"] == {"label": "internal-comms"}
    assert "internal-comms" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_chat_stream_explicit_selected_skill_denial_precedes_side_effects(monkeypatch):
    calls = []

    async def deny_selected(*_args, **kwargs):
        calls.append(("authorize", kwargs["skill_id"], kwargs["expected_version"]))
        raise repository_module.RepositoryAuthorizationError("capability_not_authorized")

    async def forbidden_side_effect(*_args, **_kwargs):
        raise AssertionError("denied explicit selected Skill must not create side effects")

    monkeypatch.setattr("app.routes.chat.transaction", fake_transaction)
    monkeypatch.setattr(
        repository_module,
        "authorize_selected_run_capabilities",
        deny_selected,
    )
    monkeypatch.setattr(repository_module, "authorize_run_capabilities", forbidden_side_effect)
    monkeypatch.setattr(repository_module, "create_run", forbidden_side_effect)
    monkeypatch.setattr(
        repository_module,
        "insert_run_skill_snapshots_at_creation",
        forbidden_side_effect,
    )
    monkeypatch.setattr(repository_module, "append_message", forbidden_side_effect)
    monkeypatch.setattr(repository_module, "append_event", forbidden_side_effect)
    monkeypatch.setattr("app.routes.chat.enqueue_run", forbidden_side_effect)

    with pytest.raises(HTTPException) as exc_info:
        await chat_stream(
            ChatStreamRequest(
                message=(
                    "Run internal-comms Skill for this announcement; "
                    "do not use MCP or create files."
                ),
                selected_skill={
                    "skill_id": "internal-comms",
                    "expected_version": "hash-internal-comms-v1",
                },
            ),
            principal=principal(department_id="finance", roles=["employee"]),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "capability_not_authorized"
    assert calls == [("authorize", "internal-comms", "hash-internal-comms-v1")]
