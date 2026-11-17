"""Profile identity cannot enter generic Session/Run paths without admission."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.agent_apps.api import (
    agent_type_requires_profile_admission,
    locked_agent_profile_identity_valid,
    resolve_session_profile_admission_identity,
)
from app.agent_apps.authority import AgentProfileAuthority
from app.auth import AuthPrincipal
from app.models import ChatSessionRequest, ChatStreamRequest, CreateRunRequest
from app.routes import chat as chat_routes
from app.routes import runs as run_routes
from app.runs.api import WorkerLockedSnapshotService
from app.runs.infrastructure import capability_admission_postgres
from app.runs.infrastructure import creation_postgres
from app.runs.infrastructure import postgres as run_persistence
from app.skills.infrastructure import resolution_postgres


PRINCIPAL = AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a")


@asynccontextmanager
async def transaction():
    yield object()


def run_control_request() -> Request:
    return Request({
        "type": "http",
        "app": SimpleNamespace(state=SimpleNamespace(
            run_delivery_intents=None,
            run_stream_runtime=SimpleNamespace(worker_capabilities=SimpleNamespace()),
        )),
    })


def forbidden_write():
    return AsyncMock(side_effect=AssertionError("Profile rejection must precede work persistence"))


@pytest.mark.asyncio
async def test_generic_session_rejects_profile_identity_before_creating_session(monkeypatch):
    monkeypatch.setattr(chat_routes, "transaction", transaction)
    monkeypatch.setattr(
        chat_routes.agent_apps_catalog, "get_agent",
        AsyncMock(return_value={"id": "agt-restricted", "agent_type": "profile", "status": "active"}),
    )
    create_session = forbidden_write()
    monkeypatch.setattr(chat_routes.conversations_postgres, "create_session", create_session)
    monkeypatch.setattr(chat_routes.identity_postgres, "ensure_user", forbidden_write())
    with pytest.raises(HTTPException) as exc:
        await chat_routes.create_chat_session(ChatSessionRequest(agent_id="agt-restricted"), PRINCIPAL)
    assert (exc.value.status_code, exc.value.detail) == (409, "agent_profile_admission_required")
    create_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["draft", "published", "withdrawn"])
@pytest.mark.parametrize("roles", [[], ["ai_admin"]])
async def test_generic_run_rejects_profile_with_independently_authorized_skill(monkeypatch, lifecycle, roles):
    monkeypatch.setattr(run_routes, "transaction", transaction)
    authorized_skill = AsyncMock(return_value={
        "agent_id": "agt-restricted", "agent_type": "profile", "agent_status": "active",
        "lifecycle_status": lifecycle, "skill_id": "independent-skill",
    })
    monkeypatch.setattr(run_routes.runs_capability_admission, "authorize_selected_run_capabilities", authorized_skill)
    create_session, create_run = forbidden_write(), forbidden_write()
    monkeypatch.setattr(run_routes.conversations_postgres, "create_session", create_session)
    monkeypatch.setattr(run_routes.runs_creation, "create_run", create_run)
    monkeypatch.setattr(run_routes, "admit_skill_run", forbidden_write())
    monkeypatch.setattr(run_routes.files_run_bindings, "authorize_files_for_run", forbidden_write())
    principal = AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a", roles=roles)
    with pytest.raises(HTTPException) as exc:
        await run_routes.create_run(
            CreateRunRequest(agent_id="agt-restricted", selected_skill={"skill_id": "independent-skill", "expected_version": "version-a"}),
            SimpleNamespace(), principal,
        )
    assert (exc.value.status_code, exc.value.detail) == (409, "agent_profile_admission_required")
    assert authorized_skill.await_args.kwargs["agent_id"] == "agt-restricted"
    create_session.assert_not_awaited()
    create_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_query_selector", [False, True])
async def test_generic_chat_rejects_unpinned_profile_identity_before_files_or_run(monkeypatch, use_query_selector):
    monkeypatch.setattr(chat_routes, "transaction", transaction)
    monkeypatch.setattr(chat_routes.runs_postgres, "acquire_user_active_run_admission_lock", AsyncMock())
    monkeypatch.setattr(chat_routes.identity_postgres, "ensure_submission_principal", AsyncMock())
    monkeypatch.setattr(chat_routes, "resolve_chat_model_selection", AsyncMock(return_value=SimpleNamespace(
        model_id="model-a", model_value="provider/model-a",
    )))
    monkeypatch.setattr(chat_routes.runs_capability_admission, "authorize_selected_run_capabilities", AsyncMock(return_value={
        "agent_id": "agt-restricted", "agent_type": "profile", "skill_id": "independent-skill",
    }))
    monkeypatch.setattr(chat_routes, "_persist_pre_persistence_rejection", AsyncMock(return_value=None))
    file_selection, create_run = forbidden_write(), forbidden_write()
    monkeypatch.setattr(chat_routes, "select_authorized_run_file_snapshot", file_selection)
    monkeypatch.setattr(chat_routes.conversations_postgres, "create_session", forbidden_write())
    monkeypatch.setattr(chat_routes.runs_creation, "create_run", create_run)
    request = ChatStreamRequest(
        message="Run the independently authorized Skill",
        selected_skill={"skill_id": "independent-skill", "expected_version": "version-a"},
        **({} if use_query_selector else {"agent_id": "agt-restricted"}),
    )
    with pytest.raises(HTTPException) as exc:
        await chat_routes.chat_stream(
            request, SimpleNamespace(), agent_id="agt-restricted" if use_query_selector else None,
            principal=PRINCIPAL,
        )
    assert (exc.value.status_code, exc.value.detail) == (409, "agent_profile_admission_required")
    file_selection.assert_not_awaited()
    create_run.assert_not_awaited()


def locked_identity(agent_type):
    return {
        "agent_id": "agent-a", "agent_type": agent_type,
        "admitted_agent_profile_revision": None, "admitted_agent_profile_hash": None,
        "session_admitted_agent_profile_revision": None, "session_admitted_agent_profile_hash": None,
    }


@pytest.mark.parametrize("agent_type, expected", [("profile", False), ("chat", True), ("file", True), (None, False), ("", False)])
def test_empty_profile_pins_are_valid_only_for_known_non_profile_identity(agent_type, expected):
    assert locked_agent_profile_identity_valid({}, locked_identity(agent_type)) is expected
    assert agent_type_requires_profile_admission(agent_type) is (agent_type == "profile")


def test_missing_durable_type_cannot_be_inferred_from_queue_profile():
    locked = locked_identity("chat")
    locked.pop("agent_type")
    assert locked_agent_profile_identity_valid({}, locked) is False


@pytest.mark.parametrize("session, session_id, selected_id, expected", [
    (None, None, None, (None, None)),
    (None, None, "profile-a", ("profile-a", None)),
    ({}, "session-a", None, (None, None)),
    ({"admitted_agent_profile_revision": None}, "session-a", None, (None, None)),
    ({"admitted_agent_profile_revision": 0}, "session-a", None, (None, 0)),
    ({"admitted_agent_profile_revision": "2"}, "session-a", None, (None, "2")),
    ({"admitted_agent_profile_revision": 2}, None, "profile-a", ("profile-a", 2)),
])
def test_session_profile_selection_preserves_unbound_inputs(session, session_id, selected_id, expected):
    assert resolve_session_profile_admission_identity(
        session, session_id=session_id, selected_agent_id=selected_id,
    ) == expected


@pytest.mark.parametrize("purpose", ["conversation", "builder_test"])
@pytest.mark.parametrize("selected_id", [None, "profile-a"])
def test_session_profile_selection_restores_identity_without_advancing_revision(purpose, selected_id):
    session = {
        "agent_id": "profile-a", "purpose": purpose,
        "admitted_agent_profile_revision": 2, "admitted_agent_profile_hash": "a" * 64,
    }
    assert resolve_session_profile_admission_identity(
        session, session_id="session-a", selected_agent_id=selected_id,
    ) == ("profile-a", 2)


@pytest.mark.parametrize("revision, profile_hash, selected_id", [
    (None, None, "profile-a"),
    (0, "a" * 64, "profile-a"),
    ("2", "a" * 64, "profile-a"),
    (2, None, None),
    (2, "", None),
    (2, 3, "profile-a"),
    (2, "a" * 64, "profile-b"),
])
def test_session_profile_selection_rejects_unbound_or_conflicting_identity(revision, profile_hash, selected_id):
    session = {
        "agent_id": "profile-a", "admitted_agent_profile_revision": revision,
        "admitted_agent_profile_hash": profile_hash,
    }
    with pytest.raises(ValueError, match="^agent_profile_session_mismatch$"):
        resolve_session_profile_admission_identity(
            session, session_id="session-a", selected_agent_id=selected_id,
        )


def test_admitted_profile_keeps_independent_session_provenance():
    profile = {"agent_id": "agent-a", "revision": 7, "content_hash": "a" * 64}
    locked = {
        **locked_identity("profile"),
        "admitted_agent_profile_revision": 7, "admitted_agent_profile_hash": "a" * 64,
        "session_admitted_agent_profile_revision": 2, "session_admitted_agent_profile_hash": "b" * 64,
    }
    assert locked_agent_profile_identity_valid(profile, locked) is True
    assert locked_agent_profile_identity_valid(profile, {**locked, "agent_type": "chat"}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reconciliation", [False, True])
async def test_worker_snapshot_denies_unpinned_durable_profile_for_dispatch_and_reconciliation(reconciliation):
    async def load_model(locked, **kwargs):
        return locked

    resolver = WorkerLockedSnapshotService(
        identity=lambda payload, locked: {"run_id": "run-a"},
        mismatch_fields=lambda *args: [], load_model=load_model, model_loader=lambda *args: None,
        trace_id=lambda *args: "trace-a", parse_payload=lambda *args, **kwargs: SimpleNamespace(agent_profile=None),
        profile_identity_valid=locked_agent_profile_identity_valid,
        reconciliation_profile_matches=lambda *args: True,
    )
    snapshot = await resolver.resolve(
        object(), payload=SimpleNamespace(input={}), locked_run=locked_identity("profile"),
        trace_id="trace-a", reconciliation=reconciliation,
    )
    assert snapshot.valid is False


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row):
        self.row, self.calls = row, []

    async def execute(self, query, params):
        self.calls.append((" ".join(query.split()), params))
        return Cursor(self.row)


@pytest.mark.asyncio
@pytest.mark.parametrize("loader", [run_persistence.lock_queued_run_for_attempt, run_persistence.get_run])
async def test_worker_run_read_loads_same_tenant_agent_type_from_database(loader):
    conn = Connection({"id": "run-a", "agent_type": "profile"})
    row = await loader(conn, tenant_id="tenant-a", run_id="run-a")
    assert row["agent_type"] == "profile"
    query, params = conn.calls[0]
    assert "agents.agent_type" in query
    assert "agents.id = runs.agent_id and agents.tenant_id = runs.tenant_id" in query
    assert params == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_selected_skill_identity_retains_agent_type_for_ingress_policy():
    conn = Connection({
        "agent_id": "agent-a", "agent_type": "profile", "agent_status": "active",
        "skill_id": "independent-skill", "skill_status": "active", "skill_version_status": "released",
        "skill_version": "version-a", "skill_content_hash": "version-a", "executor_type": "claude-agent-worker",
    })
    row = await resolution_postgres.resolve_selected_skill(
        conn, tenant_id="tenant-a", agent_id="agent-a", skill_id="independent-skill",
    )
    assert row["agent_type"] == "profile"
    assert "agents.agent_type" in conn.calls[0][0]
    assert conn.calls[0][1] == ("independent-skill", "tenant-a", "agent-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_active", [False, True])
async def test_real_selected_capability_chain_keeps_profile_type_through_route(monkeypatch, policy_active):
    """Do not mock the resolver, capability authorization, rollout, or route gate."""
    conn = Connection({
        "agent_id": "agent-a", "agent_type": "profile", "agent_status": "active",
        "default_skill_id": "different-profile-skill", "skill_id": "independent-skill",
        "skill_status": "active", "skill_version_status": "released",
        "skill_version": "version-a", "skill_content_hash": "version-a",
        "executor_type": "claude-agent-worker",
        "release_policy_version": "version-a" if policy_active else None,
        "release_policy_rollout_percent": 100,
    })

    @asynccontextmanager
    async def local_transaction():
        yield conn

    monkeypatch.setattr(run_routes, "transaction", local_transaction)
    distribution = AsyncMock(return_value={
        "capability_kind": "skill", "capability_id": "independent-skill",
        "status": "active", "visible_to_user": True, "scope_mode": "allowlist",
        "department_ids": [], "allowed_roles": [], "metadata_json": {},
    })
    monkeypatch.setattr(capability_admission_postgres, "get_capability_distribution_row", distribution)
    exact_version = AsyncMock(return_value={
        "status": "released", "version": "version-a", "content_hash": "version-a",
    })
    monkeypatch.setattr(capability_admission_postgres, "get_effective_skill_version_for_policy", exact_version)
    create_session, create_run = forbidden_write(), forbidden_write()
    monkeypatch.setattr(run_routes.conversations_postgres, "create_session", create_session)
    monkeypatch.setattr(run_routes.runs_creation, "create_run", create_run)
    monkeypatch.setattr(run_routes, "admit_skill_run", forbidden_write())
    with pytest.raises(HTTPException) as exc:
        await run_routes.create_run(
            CreateRunRequest(agent_id="agent-a", selected_skill={
                "skill_id": "independent-skill", "expected_version": "version-a",
            }),
            SimpleNamespace(), PRINCIPAL,
        )
    assert (exc.value.status_code, exc.value.detail) == (409, "agent_profile_admission_required")
    assert "agents.agent_type" in conn.calls[0][0]
    assert conn.calls[0][1] == ("independent-skill", "tenant-a", "agent-a")
    distribution.assert_awaited_once()
    assert exact_version.await_count == int(policy_active)
    create_session.assert_not_awaited()
    create_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("for_update", [False, True])
async def test_replay_source_read_loads_same_tenant_agent_type(for_update):
    conn = Connection({"id": "run-a", "agent_type": "profile"})
    row = await creation_postgres.get_authorized_run(
        conn, tenant_id="tenant-a", user_id="user-a", run_id="run-a", for_update=for_update,
    )
    assert row["agent_type"] == "profile"
    query, params = conn.calls[0]
    assert "agents.agent_type" in query
    assert "agents.id = runs.agent_id and agents.tenant_id = runs.tenant_id" in query
    assert "sessions.user_id = runs.user_id" in query
    assert ("for update of runs" in query) is for_update
    assert params == ("tenant-a", "run-a", "user-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", ["chat", "file"])
async def test_replay_authority_preserves_ordinary_null_profile_pins(monkeypatch, agent_type):
    monkeypatch.setattr(creation_postgres, "get_authorized_run", AsyncMock(return_value={
        "id": "run-a", "agent_id": "agent-a", "agent_type": agent_type,
        "admitted_agent_profile_revision": None, "admitted_agent_profile_hash": None,
        "input_json": {},
    }))
    await AgentProfileAuthority().reauthorize_pinned_run_for_replay(
        object(), principal=PRINCIPAL, run_id="run-a",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["copy", "retry", "resume"])
@pytest.mark.parametrize("roles", [[], ["ai_admin"]])
async def test_replay_unpinned_profile_rejected_by_real_owner_before_child_write(monkeypatch, action, roles):
    monkeypatch.setattr(run_routes, "transaction", transaction)
    monkeypatch.setattr(run_routes, "enforce_user_active_run_limit", AsyncMock())
    monkeypatch.setattr(run_routes, "_agent_profile_authority", AgentProfileAuthority())
    source = AsyncMock(return_value={
        "id": "run-a", "agent_id": "agent-a", "agent_type": "profile",
        "admitted_agent_profile_revision": None, "admitted_agent_profile_hash": None,
        "input_json": {},
    })
    monkeypatch.setattr(creation_postgres, "get_authorized_run", source)
    monkeypatch.setattr(run_routes.runs_control_operations, "acquire_run_control_operation_lock", AsyncMock())
    monkeypatch.setattr(run_routes.runs_control_operations, "get_run_control_operation", AsyncMock(return_value=None))
    child_writes = []
    for name in ["copy_run_as_new_task", "retry_run_as_new_task", "resume_run_as_new_task"]:
        write = forbidden_write()
        child_writes.append(write)
        monkeypatch.setattr(run_routes.runs_replay, name, write)
    queue = forbidden_write()
    monkeypatch.setattr(run_routes, "enqueue_run", queue)
    principal = AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a", roles=roles)
    with pytest.raises(HTTPException) as exc:
        if action == "copy":
            await run_routes.copy_run("run-a", SimpleNamespace(), principal)
        else:
            await run_routes._mutate_run_control_child(
                run_id="run-a", action=action, operation_id=uuid4(),
                request=run_control_request(), principal=principal,
                v4_capabilities=SimpleNamespace(),
            )
    assert (exc.value.status_code, exc.value.detail) == (409, "agent_profile_admission_required")
    assert source.await_args.kwargs == {
        "tenant_id": "tenant-a", "user_id": "user-a", "run_id": "run-a",
    }
    for write in child_writes:
        write.assert_not_awaited()
    queue.assert_not_awaited()
