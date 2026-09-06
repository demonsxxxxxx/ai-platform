from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app.context.infrastructure.provider_sessions as provider_sessions_repository
from app.context.api import materialize_worker_context_snapshot as api_materialize_worker_context_snapshot
from app.context.application.provider_sessions import ProviderSessionUseCases
from app.context.application.worker_snapshot import materialize_worker_context_snapshot
from app.context.infrastructure.provider_sessions import (
    append_provider_session_entries,
    claim_provider_session_writer,
)
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_BATCH_COUNT,
    ProviderSessionConflictError,
    ProviderSessionContinuityError,
    claude_provider_session_id_for_session,
    normalize_provider_entry_batch,
    normalize_provider_subpath,
    select_provider_conversation_context,
)


def test_provider_session_schema_contract_is_scoped_and_idempotent():
    schema = " ".join(Path("app/schema.sql").read_text(encoding="utf-8").lower().split())

    assert "create table if not exists provider_session_bindings (" in schema
    assert "create table if not exists provider_session_entries (" in schema
    assert (
        "foreign key ( tenant_id, workspace_id, user_id, session_id, agent_id ) "
        "references sessions(tenant_id, workspace_id, user_id, id, agent_id) "
        "on delete cascade"
    ) in schema
    assert "unique (tenant_id, provider_session_id, subpath, sequence)" in schema
    assert (
        "create unique index if not exists uq_provider_session_entries_sdk_uuid "
        "on provider_session_entries(tenant_id, provider_session_id, subpath, sdk_entry_uuid) "
        "where sdk_entry_uuid is not null and sdk_entry_uuid <> ''"
    ) in schema
    assert (
        "create index if not exists idx_provider_session_entries_order "
        "on provider_session_entries(tenant_id, provider_session_id, subpath, sequence)"
    ) in schema


def test_provider_identity_is_stable_for_one_session_and_scoped():
    common = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "agent_id": "agent-a",
    }
    first = claude_provider_session_id_for_session(**common)
    assert first == claude_provider_session_id_for_session(**common)
    assert first != claude_provider_session_id_for_session(**{**common, "session_id": "session-b"})
    assert first != claude_provider_session_id_for_session(**{**common, "tenant_id": "tenant-b"})


def test_provider_entries_are_opaque_bounded_and_main_subpath_is_normalized():
    assert normalize_provider_subpath(None) is None
    assert normalize_provider_subpath("") is None
    assert normalize_provider_subpath(" worker/subagent ") == "worker/subagent"

    entries, total_bytes = normalize_provider_entry_batch(
        [{"type": "assistant", "uuid": "entry-1", "content": [{"text": "ok"}]}],
        subpath="",
    )
    assert entries[0].entry["content"] == [{"text": "ok"}]
    assert entries[0].sdk_entry_uuid == "entry-1"
    assert entries[0].subpath is None
    assert total_bytes > 0

    with pytest.raises(ProviderSessionContinuityError, match="batch_too_large"):
        normalize_provider_entry_batch([{}] * (MAX_PROVIDER_SESSION_BATCH_COUNT + 1))


def test_provider_transcript_replaces_only_reconstructed_conversation():
    reconstructed = {
        "schema_version": "ai-platform.executor-conversation-context.v1",
        "messages": [{"role": "user", "content": "prior"}],
        "selected_message_count": 1,
        "selected_turn_count": 1,
        "dropped_turn_count": 2,
        "estimated_bytes": 42,
        "max_history_bytes": 8192,
    }
    assert select_provider_conversation_context(
        reconstructed, has_main_transcript=False
    ) is reconstructed
    resumed = select_provider_conversation_context(reconstructed, has_main_transcript=True)
    assert resumed == {
        "schema_version": "ai-platform.executor-conversation-context.v1",
        "messages": [],
        "selected_message_count": 0,
        "selected_turn_count": 0,
        "dropped_turn_count": 0,
        "estimated_bytes": 0,
        "max_history_bytes": 8192,
    }


@pytest.mark.asyncio
async def test_worker_context_skips_message_load_after_committed_provider_transcript():
    calls: list[str] = []

    async def snapshot_loader(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "ctx-a", "included_message_ids": ["message-a"]}

    async def message_loader(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        calls.append("messages")
        return [{"id": "message-a", "role": "user", "content": "prior"}]

    async def provider_transcript_loader(*_args: Any, **_kwargs: Any) -> bool:
        calls.append("provider")
        return True

    result = await materialize_worker_context_snapshot(
        object(),
        identity={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "engine": "claude",
        },
        context_snapshot_id="ctx-a",
        snapshot_loader=snapshot_loader,
        message_loader=message_loader,
        context_projector=lambda row: {"context_snapshot_id": row["id"]},
        provider_transcript_loader=provider_transcript_loader,
    )
    assert result is not None
    assert result["conversation_context"]["messages"] == []
    assert result["conversation_context"]["provider_session_resume_required"] is True
    assert "provider_session_resume_required" not in result
    assert calls == ["provider"]


@pytest.mark.asyncio
async def test_context_api_auto_provider_loader_strips_run_id_before_repository_lookup(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def provider_session_state(_conn, **kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr("app.context.api.provider_session_has_main_transcript", provider_session_state)

    async def snapshot_loader(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "ctx-a", "included_message_ids": ["message-a"]}

    async def message_loader(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("provider continuation should omit reconstructed messages")

    result = await api_materialize_worker_context_snapshot(
        object(),
        identity={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "engine": "claude",
        },
        context_snapshot_id="ctx-a",
        snapshot_loader=snapshot_loader,
        message_loader=message_loader,
        context_projector=lambda row: {"context_snapshot_id": row["id"]},
    )

    assert result is not None
    assert result["conversation_context"]["messages"] == []
    assert result["conversation_context"]["provider_session_resume_required"] is True
    assert "provider_session_resume_required" not in result
    assert calls == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "agent_id": "agent-a",
            "engine": "claude",
        }
    ]


@pytest.mark.asyncio
async def test_context_api_keeps_platform_conversation_for_non_claude_identity(monkeypatch):
    calls: list[str] = []

    async def provider_session_state(*_args: Any, **_kwargs: Any) -> bool:
        calls.append("provider")
        return True

    monkeypatch.setattr("app.context.api.provider_session_has_main_transcript", provider_session_state)

    async def snapshot_loader(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": "ctx-a", "included_message_ids": ["message-a"]}

    async def message_loader(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        calls.append("messages")
        return [{"id": "message-a", "role": "user", "content": "prior"}]

    result = await api_materialize_worker_context_snapshot(
        object(),
        identity={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "engine": "",
        },
        context_snapshot_id="ctx-a",
        snapshot_loader=snapshot_loader,
        message_loader=message_loader,
        context_projector=lambda row: {"context_snapshot_id": row["id"]},
    )

    assert result is not None
    assert result["conversation_context"]["messages"]
    assert result["conversation_context"]["provider_session_resume_required"] is False
    assert calls == ["messages"]


class _RecordingCursor:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ):
        self._row = row
        self._rows = rows or []

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _RecordingConnection:
    def __init__(
        self,
        binding: dict[str, Any],
        entries: list[dict[str, Any]] | None = None,
    ):
        self.binding = binding
        self.entries = entries or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: Any = ()) -> _RecordingCursor:
        values = tuple(params)
        self.calls.append((query, values))
        normalized = query.lower()
        if "select 1 as authorized" in normalized:
            return _RecordingCursor({"authorized": 1})
        if "from provider_session_entries" in normalized:
            return _RecordingCursor(rows=self.entries)
        if "insert into provider_session_entries" in normalized:
            return _RecordingCursor(
                {
                    "id": "entry-row",
                    "provider_session_id": self.binding["provider_session_id"],
                    "subpath": "",
                    "sequence": 1,
                    "sdk_entry_uuid": "entry-1",
                    "entry_json": {"uuid": "entry-1"},
                }
            )
        if "set next_sequence" in normalized:
            return _RecordingCursor({"next_sequence": 2})
        if "provider_session_bindings" in normalized:
            return _RecordingCursor(self.binding)
        raise AssertionError(f"unhandled SQL: {query}")


def _repository_binding() -> dict[str, Any]:
    return {
        "provider_session_id": claude_provider_session_id_for_session(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            agent_id="agent-a",
        ),
        "context_epoch": 1,
        "next_sequence": 1,
    }


def _assert_lease_fencing_and_parameter_counts(
    connection: _RecordingConnection,
) -> None:
    assert connection.calls
    for query, params in connection.calls:
        assert query.count("%s") == len(params), (query, params)
        if "sandbox_leases" not in query:
            continue
        lower_query = query.lower()
        lease_predicates = lower_query.count("from sandbox_leases")
        assert lower_query.count("status = 'active'") == lease_predicates
        assert lower_query.count("released_at is null") == lease_predicates
        assert lower_query.count("expires_at is null or") == lease_predicates
        assert "status = any" not in lower_query


@pytest.mark.asyncio
async def test_repository_append_sql_fences_expiry_and_matches_parameters():
    connection = _RecordingConnection(_repository_binding())
    await append_provider_session_entries(
        connection,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="agent-a",
        run_id="run-a",
        attempt_id="attempt-a",
        entries=[{"uuid": "entry-1"}],
    )
    _assert_lease_fencing_and_parameter_counts(connection)
    update_calls = [
        (query, params)
        for query, params in connection.calls
        if "set next_sequence" in query.lower()
    ]
    assert len(update_calls) == 1
    assert update_calls[0][0].count("%s") == len(update_calls[0][1]) == 11
    assert update_calls[0][1][-2:] == ("run-a", "attempt-a")


@pytest.mark.asyncio
async def test_repository_append_rejects_existing_uuid_with_different_payload():
    connection = _RecordingConnection(
        _repository_binding(),
        entries=[
            {
                "id": "stored-row",
                "provider_session_id": _repository_binding()["provider_session_id"],
                "subpath": "",
                "sequence": 1,
                "sdk_entry_uuid": "entry-1",
                "entry_json": {"uuid": "entry-1", "content": "stored"},
            }
        ],
    )

    with pytest.raises(ProviderSessionConflictError, match="provider_session_entry_conflict"):
        await append_provider_session_entries(
            connection,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            agent_id="agent-a",
            run_id="run-a",
            attempt_id="attempt-a",
            entries=[{"uuid": "entry-1", "content": "different"}],
        )
    _assert_lease_fencing_and_parameter_counts(connection)


@pytest.mark.asyncio
async def test_repository_append_enforces_cumulative_subpath_limits_and_idempotency(monkeypatch):
    monkeypatch.setattr(provider_sessions_repository, "MAX_PROVIDER_SESSION_ENTRIES", 1)
    monkeypatch.setattr(provider_sessions_repository, "MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES", 1024)
    binding = _repository_binding()
    existing = {
        "id": "branch-row",
        "provider_session_id": binding["provider_session_id"],
        "subpath": "branch",
        "sequence": 1,
        "sdk_entry_uuid": "entry-1",
        "entry_json": {"uuid": "entry-1"},
    }
    connection = _RecordingConnection(binding, entries=[existing])

    rows = await append_provider_session_entries(
        connection,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="agent-a",
        run_id="run-a",
        attempt_id="attempt-a",
        subpath="branch",
        entries=[{"uuid": "entry-1"}],
    )
    assert rows == [existing]
    assert not any("insert into provider_session_entries" in query.lower() for query, _ in connection.calls)
    _assert_lease_fencing_and_parameter_counts(connection)

    with pytest.raises(ProviderSessionConflictError, match="provider_session_transcript_too_large"):
        await append_provider_session_entries(
            _RecordingConnection(binding, entries=[existing]),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            agent_id="agent-a",
            run_id="run-a",
            attempt_id="attempt-a",
            subpath="another-branch",
            entries=[{"uuid": "entry-2"}],
        )

    connection = _RecordingConnection(_repository_binding())
    await claim_provider_session_writer(
        connection,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="agent-a",
        run_id="run-a",
        attempt_id="attempt-a",
    )
    _assert_lease_fencing_and_parameter_counts(connection)
    assert len(connection.calls) == 1
    assert connection.calls[0][0].count("%s") == len(connection.calls[0][1]) == 13


@pytest.mark.asyncio
async def test_provider_session_use_case_owns_callback_sequence_and_scope():
    calls: list[tuple[str, dict[str, Any]]] = []

    class Repository:
        async def ensure_binding(self, _conn: object, **kwargs: Any):
            calls.append(("ensure", kwargs))
            return {}

        async def claim_writer(self, _conn: object, **kwargs: Any):
            calls.append(("claim", kwargs))
            return {}

        async def append_entries(self, _conn: object, **kwargs: Any):
            calls.append(("append", kwargs))
            return [{"sequence": 1}]

        async def list_entries(self, _conn: object, **kwargs: Any):
            raise AssertionError(kwargs)

        async def list_subpaths(self, _conn: object, **kwargs: Any):
            raise AssertionError(kwargs)

        async def has_main_transcript(self, _conn: object, **kwargs: Any):
            raise AssertionError(kwargs)

    result = await ProviderSessionUseCases(Repository()).execute_callback(
        object(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="agent-a",
        run_id="run-a",
        attempt_id="attempt-a",
        provider_session_id="provider-a",
        action="append",
        entries=[{"uuid": "entry-a"}],
        subpath=None,
    )

    assert result.entry_count == 1
    assert result.entries == ()
    assert [name for name, _ in calls] == ["ensure", "claim", "append"]
    assert "provider_session_id" not in calls[0][1]
    assert calls[1][1]["provider_session_id"] == "provider-a"
    assert calls[2][1]["entries"] == [{"uuid": "entry-a"}]
