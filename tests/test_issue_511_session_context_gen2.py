from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.models import QueueRunPayload
from app.runs.api import RunTerminalizationProgress
from app.routes import lambchat_compat
from app.routes.runs import (
    _compensate_enqueue_failure,
    copy_run,
    resume_run,
    retry_run,
    run_context_ref_from_snapshot_row,
)
from app.bootstrap.context import materialize_queued_worker_context_snapshot
from app.worker import _context_snapshot_ref_from_row
from app.worker_principal_authority import _payload_identity


async def _materialize_scoped_worker_snapshot(conn, payload):
    context, error_code = await materialize_queued_worker_context_snapshot(
        conn, payload=payload, run_identity=_payload_identity(payload),
        context_projector=_context_snapshot_ref_from_row,
    )
    assert error_code is None
    return context


@pytest.fixture(autouse=True)
def _stub_run_model_and_provider_lineage_for_route_fakes(monkeypatch):
    async def inherit_run_model(*_args, **_kwargs):
        return None

    async def release_provider_lineage(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.routes.runs.inherit_run_model", inherit_run_model)
    monkeypatch.setattr(
        "app.runs.application.provider_terminalization.release_provider_lineage",
        release_provider_lineage,
    )


@asynccontextmanager
async def _fake_transaction():
    yield object()


def _principal() -> AuthPrincipal:
    return AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a", roles=["admin"])


async def _default_enqueue_failure(*_args, **_kwargs):
    return RunTerminalizationProgress(completed=True, status="failed", did_transition=True)


_TEST_RUN_LIFECYCLE = SimpleNamespace(mark_run_enqueue_failed=_default_enqueue_failure)


def _stream_request(pending_admissions, event_persistence):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                run_stream_runtime=SimpleNamespace(
                    worker_capabilities=SimpleNamespace(
                        pending_admissions=pending_admissions,
                        event_persistence=event_persistence,
                    )
                ),
                run_lifecycle=_TEST_RUN_LIFECYCLE,
            )
        )
    )


@pytest.mark.asyncio
async def test_worker_missing_physical_snapshot_never_rebuilds_context(monkeypatch):
    payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="agent-a",
        skill_id="skill-a",
        executor_type="fake",
        skill_version="v1",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "v1",
        },
        skill_manifests=[{"skill_id": "skill-a", "content_hash": "v1"}],
        context_snapshot_id="ctx-missing",
        context_snapshot={"context_snapshot_id": "ctx-missing"},
    )
    calls = []

    async def missing_snapshot(_conn, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr("app.context.infrastructure.snapshot_postgres.get_context_snapshot_for_worker", missing_snapshot)

    context_ref = await _materialize_scoped_worker_snapshot(object(), payload)

    assert context_ref is None
    assert calls == [{
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "context_snapshot_id": "ctx-missing",
    }]


@pytest.mark.asyncio
async def test_worker_materializes_complete_snapshot_authorized_conversation(monkeypatch):
    payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        agent_id="agent-a",
        skill_id="skill-a",
        executor_type="fake",
        skill_version="v1",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "v1",
        },
        skill_manifests=[{"skill_id": "skill-a", "content_hash": "v1"}],
        context_snapshot_id="ctx-current",
        context_snapshot={"context_snapshot_id": "ctx-current"},
    )
    assistant = "analysis " + ("x" * 900) + "\nA. continue\nB. wait"
    rows = [
        {
            "id": "msg-user-prior",
            "run_id": "run-prior",
            "role": "user",
            "content": "Is this file enough?",
            "created_at": "2026-08-17T00:00:01Z",
        },
        {
            "id": "msg-assistant-prior",
            "run_id": "run-prior",
            "role": "assistant",
            "content": assistant,
            "created_at": "2026-08-17T00:00:02Z",
        },
        {
            "id": "msg-user-current",
            "run_id": "run-current",
            "role": "user",
            "content": "A",
            "created_at": "2026-08-17T00:00:03Z",
        },
    ]

    async def get_snapshot(_conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "schema_version": "ai-platform.context-snapshot.v1",
            "included_message_ids": [row["id"] for row in rows],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "payload_json": {},
        }

    async def list_messages(_conn, **kwargs):
        assert kwargs["limit"] == 3
        return rows

    monkeypatch.setattr(
        "app.context.infrastructure.snapshot_postgres.get_context_snapshot_for_worker",
        get_snapshot,
    )
    monkeypatch.setattr(
        "app.context.infrastructure.sources_postgres.list_scoped_context_messages",
        list_messages,
    )

    context_ref = await _materialize_scoped_worker_snapshot(object(), payload)

    assert context_ref is not None
    conversation = context_ref["conversation_context"]
    assert [message["role"] for message in conversation["messages"]] == [
        "user",
        "assistant",
    ]
    assert conversation["messages"][1]["content"] == assistant
    assert all(message["content"] != "A" for message in conversation["messages"])


@pytest.mark.asyncio
async def test_worker_rejects_incomplete_snapshot_message_materialization(monkeypatch):
    payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        agent_id="agent-a",
        skill_id="skill-a",
        executor_type="fake",
        skill_version="v1",
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": "v1",
        },
        skill_manifests=[{"skill_id": "skill-a", "content_hash": "v1"}],
        context_snapshot_id="ctx-current",
        context_snapshot={"context_snapshot_id": "ctx-current"},
    )

    async def get_snapshot(_conn, **kwargs):
        return {
            "id": kwargs["context_snapshot_id"],
            "schema_version": "ai-platform.context-snapshot.v1",
            "included_message_ids": ["msg-user", "msg-assistant"],
            "included_file_ids": [],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "payload_json": {},
        }

    async def list_messages(_conn, **_kwargs):
        return [
            {
                "id": "msg-user",
                "run_id": "run-prior",
                "role": "user",
                "content": "question",
                "created_at": "2026-08-17T00:00:01Z",
            }
        ]

    monkeypatch.setattr(
        "app.context.infrastructure.snapshot_postgres.get_context_snapshot_for_worker",
        get_snapshot,
    )
    monkeypatch.setattr(
        "app.context.infrastructure.sources_postgres.list_scoped_context_messages",
        list_messages,
    )

    assert (
        await _materialize_scoped_worker_snapshot(object(), payload)
        is None
    )


@pytest.mark.asyncio
async def test_run_enqueue_compensation_uses_the_durable_failed_transition(monkeypatch):
    calls = []

    async def mark_failed(_conn, **kwargs):
        calls.append(kwargs)
        return RunTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    class PendingAdmissions:
        async def prepare_pending_authority_in_transaction(
            self, _conn, *, tenant_id, run_id, attempt_id
        ):
            assert (tenant_id, run_id, attempt_id) == (
                "tenant-a",
                "run-a",
                "enqueue_failure_run-a",
            )
            calls.append({"authority_prepared": True})
            return object()

    class EventPersistence:
        async def append_terminal_row(self, _conn, *, tenant_id, run_id):
            assert (tenant_id, run_id) == ("tenant-a", "run-a")
            return "row-a"

    monkeypatch.setattr("app.routes.runs.transaction", _fake_transaction)
    monkeypatch.setattr(_TEST_RUN_LIFECYCLE, "mark_run_enqueue_failed", mark_failed)

    await _compensate_enqueue_failure(
        principal=_principal(),
        run_id="run-a",
        trace_id="trace-run-a",
        run_lifecycle=_TEST_RUN_LIFECYCLE,
        v4_capabilities=SimpleNamespace(
            pending_admissions=PendingAdmissions(),
            event_persistence=EventPersistence(),
        ),
    )

    assert calls == [
        {"authority_prepared": True},
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "run_id": "run-a",
            "trace_id": "trace-run-a",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "repository_method", "source_run_id"),
    [
        (copy_run, "copy_run_as_new_task", "run-copy-source"),
        (retry_run, "retry_run_as_new_task", "run-retry-source"),
        (resume_run, "resume_run_as_new_task", "run-resume-source"),
    ],
)
async def test_copied_run_ambiguous_enqueue_preserves_committed_child(
    monkeypatch,
    route,
    repository_method,
    source_run_id,
):
    """All routes retain the child when an external queue write is unconfirmed."""

    committed: list[list[tuple[str, str]]] = []

    class TransactionState:
        def __init__(self) -> None:
            self.pending: list[tuple[str, str]] = []

    @asynccontextmanager
    async def tracked_transaction():
        state = TransactionState()
        try:
            yield state
        except BaseException:
            raise
        else:
            committed.append(list(state.pending))

    async def allow_admission(_conn, **_kwargs):
        return None

    async def allow_reauthorization(_conn, **_kwargs):
        return None

    async def acquire_operation_lock(_conn, **_kwargs):
        return None

    async def no_existing_operation(_conn, **_kwargs):
        return None

    async def retryable_source(_conn, **_kwargs):
        return {"status": "failed", "error_code": None}

    async def record_operation(_conn, **_kwargs):
        return "event-operation"

    async def create_copied_run(conn, **_kwargs):
        conn.pending.append(("run_created", "run-enqueue-failure"))
        return {"run_id": "run-enqueue-failure", "session_id": "session-a"}

    async def prepared_queue_payload(_conn, **_kwargs):
        return {"run_id": "run-enqueue-failure"}

    async def fail_enqueue(_payload):
        raise RuntimeError("queue unavailable")

    async def no_queue_admission(_payload):
        return None

    async def mark_enqueue_failed(conn, **kwargs):
        conn.pending.append(("run_failed", str(kwargs["run_id"])))
        return RunTerminalizationProgress(
            completed=True,
            status="failed",
            did_transition=True,
        )

    class PendingAdmissions:
        async def prepare_pending_authority_in_transaction(
            self, conn, *, tenant_id, run_id, attempt_id
        ):
            assert tenant_id == "tenant-a"
            assert attempt_id == f"enqueue_failure_{run_id}"
            conn.pending.append(("authority", str(run_id)))
            return object()

    class EventPersistence:
        async def append_terminal_row(self, conn, *, tenant_id, run_id):
            assert tenant_id == "tenant-a"
            conn.pending.append(("terminal_row", str(run_id)))
            return "row-enqueue-failure"

    request = _stream_request(PendingAdmissions(), EventPersistence())

    monkeypatch.setattr("app.routes.runs.transaction", tracked_transaction)
    monkeypatch.setattr("app.routes.runs.enforce_user_active_run_limit", allow_admission)
    monkeypatch.setattr(
        "app.routes.runs._agent_profile_authority.reauthorize_pinned_run_for_replay",
        allow_reauthorization,
    )
    monkeypatch.setattr(
        "app.runs.infrastructure.control_operations_postgres.acquire_run_control_operation_lock",
        acquire_operation_lock,
    )
    monkeypatch.setattr(
        "app.runs.infrastructure.control_operations_postgres.get_run_control_operation",
        no_existing_operation,
    )
    monkeypatch.setattr(
        "app.runs.infrastructure.creation_postgres.get_authorized_run",
        retryable_source,
    )
    monkeypatch.setattr(
        "app.runs.infrastructure.control_operations_postgres.record_run_control_operation",
        record_operation,
    )
    monkeypatch.setattr(f"app.runs.infrastructure.replay_postgres.{repository_method}", create_copied_run)
    monkeypatch.setattr("app.routes.runs.prepare_copied_run_for_queue", prepared_queue_payload)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue)
    monkeypatch.setattr("app.routes.runs.read_queue_admission", no_queue_admission)
    monkeypatch.setattr(_TEST_RUN_LIFECYCLE, "mark_run_enqueue_failed", mark_enqueue_failed)

    if repository_method == "copy_run_as_new_task":
        result = await route(source_run_id, request=request, principal=_principal())
        assert result.run_id == "run-enqueue-failure"
        assert result.session_id == "session-a"
        assert result.status == "accepted_pending_enqueue"
        assert committed == [[("run_created", "run-enqueue-failure")], []]
    else:
        with pytest.raises(HTTPException) as exc_info:
            await route(source_run_id, request=request, principal=_principal())
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "queue_admission_unconfirmed"
        assert committed == [[("run_created", "run-enqueue-failure")]]


@pytest.mark.asyncio
async def test_legacy_only_session_has_no_implicit_current_status(monkeypatch):
    async def get_session(_conn, **_kwargs):
        return {"id": "session-a"}

    async def list_runs(_conn, **_kwargs):
        return [{"id": "run-legacy", "status": "running", "session_generation": None}]

    monkeypatch.setattr("app.routes.lambchat_compat.transaction", _fake_transaction)
    monkeypatch.setattr("app.conversations.infrastructure.postgres.get_authorized_lambchat_session", get_session)
    monkeypatch.setattr("app.conversations.infrastructure.session_queries_postgres.list_authorized_session_runs", list_runs)

    response = await lambchat_compat.chat_status("session-a", principal=_principal())

    assert response == {"session_id": "session-a", "run_id": None, "status": "idle", "raw_status": "idle"}


def test_public_run_context_projection_contains_only_allowlisted_window():
    projection = run_context_ref_from_snapshot_row(
        {
            "included_message_ids": ["msg-private"],
            "included_file_ids": ["file-private"],
            "included_artifact_ids": [],
            "included_memory_record_ids": [],
            "payload_json": {
                "context_snapshot_id": "ctx-private",
                "storage_key": "tenants/private/context.json",
                "context_manifest": {
                    "schema_version": "ai-platform.context-manifest.v1",
                    "selection": {
                        "status": "trimmed",
                        "history_candidate_count": 3,
                        "history_authorized_count": 2,
                        "history_omitted_count": 1,
                    },
                    "files": [
                        {"name": r"C:\uploads\approved-report.txt"},
                        {"name": "/private/object-store/报价😀.xlsx"},
                    ],
                },
            },
        }
    )

    assert projection == {
        "context_window": {
            "status": "trimmed",
            "selection_version": "conversation-turns-v1",
            "history_candidate_count": 3,
            "history_authorized_count": 2,
            "history_omitted_count": 1,
            "legacy_history_excluded": False,
            "selected_file_names": ["approved-report.txt", "报价😀.xlsx"],
        }
    }
