"""Scoped checkpoint ancestry and checksum regression checks."""

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.bootstrap import context as context_bootstrap

from app.context.application import checkpoint_build as checkpoint_build_app
from app.context.application.checkpoint_build import ConversationCheckpointBuilder
from app.context.application.worker_snapshot import materialize_worker_context_snapshot
from app.context.domain.conversation_authority import ConversationSourceChain

from app.context.infrastructure.checkpoint_build_postgres import (
    assert_checkpoint_lease,
    claim_checkpoint_build,
    complete_checkpoint_build,
    load_checkpoint_build_source,
    save_checkpoint_progress,
)
from app.context.infrastructure.checkpoints_postgres import (
    load_ready_checkpoint, load_checkpoint_usage_for_run,
)
from app.runs.application import provider_terminalization as runs_terminal_app
from app.runs.infrastructure.postgres import update_terminal_run_checkpoint_counts
from app.platform.postgres.limits import RUN_RESULT_MAX_BYTES, ensure_json_size

_SCOPE = {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "user_id": "user-a",
          "session_id": "session-a", "agent_id": "agent-a"}


@pytest.mark.asyncio
async def test_checkpoint_preflight_requires_queue_scope_before_model_calls(monkeypatch):
    receipt = ConversationSourceChain(_SCOPE, 1, "run-current", None).receipt()
    calls = []

    class Repository:
        async def load_source(self, _conn, **kwargs):
            calls.append(("load", kwargs["run_id"]))
            return {**_SCOPE, "conversation_authority_json": receipt}

    class Builder:
        _repository = Repository()

        async def prepare(self, **kwargs):
            calls.append(("prepare", kwargs["run_id"]))
            return "ccp-bound"

    async def not_ready(_conn, **_kwargs):
        calls.append(("match", "run-current"))
        return False

    @asynccontextmanager
    async def transaction():
        yield object()

    monkeypatch.setattr(checkpoint_build_app, "_builder", Builder())
    monkeypatch.setattr(checkpoint_build_app, "matching_ready_provider_epoch", not_ready)
    kwargs = dict(transaction_factory=transaction, tenant_id="tenant-a", run_id="run-current",
                  context_snapshot_id="ctx-current", executor_type="claude-agent-worker",
                  reconciliation=False)
    with pytest.raises(ValueError, match="conversation_checkpoint_queue_identity_mismatch"):
        await checkpoint_build_app.prepare_checkpoint_for_run(
            **kwargs, queue_identity={**_SCOPE, "user_id": "forged"},
        )
    assert calls == [("load", "run-current")]
    assert await checkpoint_build_app.prepare_checkpoint_for_run(
        **kwargs, queue_identity=_SCOPE,
    ) == "ccp-bound"
    assert calls[1:] == [("load", "run-current"), ("match", "run-current"),
                         ("prepare", "run-current")]


@pytest.mark.asyncio
async def test_checkpoint_usage_scopes_run_and_counts_tokens(monkeypatch):
    usage_conn = Connection([{"run_count": 1, "input_tokens": 4200,
                              "output_tokens": 24}])
    usage = await load_checkpoint_usage_for_run(
        usage_conn, tenant_id="tenant-a", run_id="run-current",
    )
    sql, params = usage_conn.calls[0]
    assert params == ("tenant-a", "run-current")
    assert "checkpoint.owner_run_id = runs.id" in sql
    assert "checkpoint.user_id = runs.user_id" in sql
    assert "checkpoint.cost_usd" not in sql
    assert usage["input_tokens"] == 4200
    assert usage["output_tokens"] == 24
    with pytest.raises(ValueError, match="conversation_checkpoint_usage_run_unavailable"):
        await load_checkpoint_usage_for_run(
            Connection([{"run_count": 0}]), tenant_id="tenant-a", run_id="missing",
        )

    async def load_usage(_conn, **kwargs):
        assert kwargs == {"tenant_id": "tenant-a", "run_id": "run-current"}
        return usage

    monkeypatch.setattr(runs_terminal_app, "load_checkpoint_usage_for_run", load_usage)
    monkeypatch.setattr(runs_terminal_app, "_update_checkpoint_counts", update_terminal_run_checkpoint_counts)
    monkeypatch.setattr(runs_terminal_app, "_validate_terminal_result", lambda value: ensure_json_size(
        value, max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large",
    ))
    result = {"message": "done", "token_counts": {"input": 11, "output": 13, "total": 24}}
    conn = Connection([{"id": "run-current"}])
    merged = await runs_terminal_app.commit_terminal_checkpoint_usage(
        conn, tenant_id="tenant-a", run_id="run-current", result_json=result,
    )
    assert merged["token_counts"] == {"input": 4211, "output": 37, "total": 4248}
    assert "cost" not in merged
    sql, params = conn.calls[0]
    assert "status in ('failed', 'cancelled')" in sql and sql.count("%s") == len(params)
    assert params[1:4] == (4211, 37, 4248)


@pytest.mark.asyncio
async def test_bootstrap_checkpoint_is_prepared_outside_transaction_and_bound_to_materializer(monkeypatch):
    active = 0
    calls = []

    @asynccontextmanager
    async def transaction():
        nonlocal active
        active += 1
        try:
            yield object()
        finally:
            active -= 1

    async def prepare(**kwargs):
        assert active == 0 and kwargs["queue_identity"] == {**_SCOPE, "run_id": "run-current"}
        calls.append("prepare")
        return "ccp-bound"

    async def materialize(_conn, **kwargs):
        assert kwargs["identity"] == {**_SCOPE, "run_id": "run-current", "engine": "claude"}
        assert kwargs["prepared_checkpoint_id"] == "ccp-bound"
        calls.append("materialize")
        return {"context_snapshot_id": "ctx-current"}

    monkeypatch.setattr(context_bootstrap, "prepare_checkpoint_for_run", prepare)
    monkeypatch.setattr(context_bootstrap, "materialize_worker_context_snapshot", materialize)
    payload = SimpleNamespace(tenant_id="tenant-a", run_id="run-current",
                              context_snapshot_id="ctx-current", executor_type="claude-agent-worker")
    identity = {**_SCOPE, "run_id": "run-current"}
    checkpoint_id, failed = await context_bootstrap.prepare_worker_checkpoint(
        transaction_factory=transaction, payload=payload, principal=object(),
        reconciliation=False, queue_identity=identity,
    )
    assert checkpoint_id == "ccp-bound" and not failed
    assert await context_bootstrap.materialize_queued_worker_context_snapshot(
        object(), payload=payload, run_identity=identity,
        context_projector=lambda row: row, prepared_checkpoint_id=checkpoint_id,
    ) == {"context_snapshot_id": "ctx-current"}
    assert calls == ["prepare", "materialize"]

    async def upstream_unavailable(**_kwargs):
        raise RuntimeError("provider_unavailable")

    monkeypatch.setattr(context_bootstrap, "prepare_checkpoint_for_run", upstream_unavailable)
    assert await context_bootstrap.prepare_worker_checkpoint(
        transaction_factory=transaction, payload=payload, principal=object(),
        reconciliation=False, queue_identity=identity,
    ) == (None, True)
    assert await context_bootstrap.prepare_worker_checkpoint(
        transaction_factory=transaction, payload=payload, principal=None,
        reconciliation=False, queue_identity=identity,
    ) == (None, False)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((sql, params))
        return Cursor(self.rows)


def row(id, predecessor, start, end, summary):
    return {**_SCOPE, "id": id, "predecessor_checkpoint_id": predecessor,
            "owner_run_id": "run-source", "source_snapshot_id": "ctx-source",
            "state": "ready", "range_start_created_at": start,
            "range_start_id": "msg-001" if predecessor is None else "msg-003",
            "range_end_created_at": end, "range_end_id": "msg-002" if predecessor is None else "msg-004",
            "covered_message_count": 2, "covered_turn_count": 1,
            "through_session_generation": 4, "summary_text": summary,
            "summary_sha256": hashlib.sha256(summary.encode()).hexdigest(),
            "source_sha256": "a" * 64}


@pytest.mark.asyncio
async def test_ready_checkpoint_loads_scoped_predecessors_and_rejects_mutable_or_missing_chain():
    now = datetime(2026, 9, 15, tzinfo=UTC)
    newest = row("ccp-new", "ccp-old", now + timedelta(minutes=2),
                 now + timedelta(minutes=3), "summary of both turns")
    root = row("ccp-old", None, now, now + timedelta(minutes=1), "summary of first turn")
    conn = Connection([newest, root])
    base = await load_ready_checkpoint(conn, scope=_SCOPE, run_id="run-current", checkpoint_id="ccp-new")
    assert base["message_count"] == 4 and base["range_start"]["id"] == "msg-001"
    assert base["range_end"]["id"] == "msg-004" and base["summary_text"] == newest["summary_text"]
    assert conn.calls[0][1] == (*_SCOPE.values(), "run-current", "ccp-new", "ccp-new")
    assert "current.session_generation" in conn.calls[0][0] and "prior.tenant_id" in conn.calls[0][0]
    for damaged in ([{**newest, "summary_text": "tampered"}, root],
                    [newest], [{**newest, "tenant_id": "other"}, root],
                    [{**newest, "range_start_created_at": now}, root]):
        with pytest.raises(ValueError, match="conversation_checkpoint_chain_invalid"):
            await load_ready_checkpoint(Connection(damaged), scope=_SCOPE,
                                        run_id="run-current", checkpoint_id="ccp-new")


@pytest.mark.asyncio
async def test_checkpoint_claim_and_progress_are_short_scoped_lease_transactions():
    conn = Connection([{"id": "ccp-test", "builder_lease_id": "cbl-test"}])
    source = await load_checkpoint_build_source(conn, tenant_id="tenant-a", run_id="run-current",
                                               context_snapshot_id="ctx-current")
    assert source["id"] == "ccp-test"
    claim = await claim_checkpoint_build(
        conn, scope=_SCOPE, run_id="run-current", source_snapshot_id="ctx-current",
        build_key_sha256="a" * 64, source_sha256="b" * 64,
        predecessor_checkpoint_id=None, predecessor_sha256="c" * 64,
    )
    assert claim["id"] == "ccp-test"
    await assert_checkpoint_lease(conn, checkpoint_id="ccp-test", lease_id="cbl-test",
                                  run_id="run-current")
    boundaries = {"created_at": "2026-09-15T00:00:00+00:00", "id": "msg-001"}
    await save_checkpoint_progress(
        conn, checkpoint_id="ccp-test", lease_id="cbl-test", run_id="run-current",
        source_sha256="c" * 64, covered_message_count=2, covered_turn_count=1,
        range_start=boundaries, range_end=boundaries, summary="short summary",
        summary_sha256=hashlib.sha256(b"short summary").hexdigest(),
        input_tokens=120, output_tokens=24,
    )
    await complete_checkpoint_build(conn, checkpoint_id="ccp-test", lease_id="cbl-test",
                                    run_id="run-current", source_sha256="b" * 64)
    for sql, params in conn.calls:
        assert sql.count("%s") == len(params)
        assert "runs.status = 'queued'" in sql or "runs.status = 'queued'" in sql.lower()
    blocked = Connection([])
    with pytest.raises(ValueError, match="conversation_checkpoint_builder_fenced"):
        await assert_checkpoint_lease(blocked, checkpoint_id="ccp-test", lease_id="old-lease",
                                      run_id="run-current")


@pytest.mark.asyncio
async def test_checkpoint_builder_compacts_old_complete_turns_outside_transactions_and_preserves_tail():
    rows = [
        {"id": f"msg-{index:03d}", "run_id": f"run-{index:03d}", "role": "user",
         "content": f"goal constraint {index}: " + "x" * 128,
         "created_at": f"2026-09-15T00:{index // 60:02d}:{index % 60:02d}+00:00",
         "session_generation": index + 1}
        for index in range(82)
    ]
    chain = ConversationSourceChain(_SCOPE, 84, "run-current", "msg-current")
    chain.add_page(rows)
    receipt = chain.receipt()
    assert sum(len(row["content"]) for row in rows) > 8192
    active_transactions = 0

    @asynccontextmanager
    async def transaction():
        nonlocal active_transactions
        active_transactions += 1
        try:
            yield object()
        finally:
            active_transactions -= 1

    class Repository:
        state = "building"
        progress = None

        async def load_source(self, _conn, **_kwargs):
            return {**_SCOPE, "session_generation": 84, "model_id": "model-a",
                    "model_value": "claude-custom", "model_gateway_revision": 3,
                    "max_input_tokens": 32000, "max_output_tokens": 2048,
                    "conversation_authority_json": receipt,
                    "included_message_ids": ["msg-current"],
                    "current_user_text": "continue", "input_message": "continue"}

        async def claim(self, _conn, **kwargs):
            assert kwargs["source_sha256"] == receipt["source_sha256"]
            return {"id": "ccp-built", "builder_lease_id": "cbl-built",
                    "covered_message_count": 0, "summary_text": None}

        async def assert_lease(self, _conn, **kwargs):
            assert kwargs["checkpoint_id"] == "ccp-built" and self.state == "building"

        async def save_progress(self, _conn, **kwargs):
            assert self.progress is None
            self.progress = kwargs

        async def complete(self, _conn, **kwargs):
            assert self.progress is not None and kwargs["source_sha256"] == receipt["source_sha256"]
            self.state = "ready"

    repository = Repository()
    model_calls = []

    async def page(_conn, **kwargs):
        remaining = [row for row in rows if kwargs["after_id"] is None or row["id"] > kwargs["after_id"]]
        return remaining[:kwargs["limit"]]

    async def checkpoint(_conn, *, scope, run_id, checkpoint_id=None):
        assert scope == _SCOPE and run_id == "run-current"
        if checkpoint_id is None:
            return None
        assert checkpoint_id == "ccp-built" and repository.state == "ready"
        progress = repository.progress
        return {"id": checkpoint_id, "scope": _SCOPE,
                "owner_run_id": "run-current", "source_snapshot_id": "ctx-current",
                "predecessor_checkpoint_id": None,
                "source_sha256": progress["source_sha256"],
                "message_count": progress["covered_message_count"],
                "summary_text": progress["summary"], "summary_sha256": progress["summary_sha256"],
                "range_start": progress["range_start"], "range_end": progress["range_end"],
                "through_session_generation": 84}

    async def count(*, run_id, source_text):
        assert run_id == "run-current" and active_transactions == 0
        model_calls.append("count")
        return len(source_text.encode()) // 2

    async def summarize(*, run_id, source_text):
        assert run_id == "run-current" and active_transactions == 0
        assert "goal constraint 0" in source_text and "goal constraint 73" in source_text
        assert "goal constraint 74" not in source_text
        model_calls.append("summarize")
        return {"summary": "Earlier goal and explicit constraints survive.",
                "input_tokens": 4200, "output_tokens": 24}

    builder = ConversationCheckpointBuilder(
        repository=repository, page_loader=page, checkpoint_loader=checkpoint,
        count_tokens=count, summarize=summarize,
    )
    checkpoint_id = await builder.prepare(
        transaction_factory=transaction, tenant_id="tenant-a", run_id="run-current",
        context_snapshot_id="ctx-current",
    )
    assert checkpoint_id == "ccp-built" and repository.state == "ready"
    assert repository.progress["covered_message_count"] == 74
    assert repository.progress["covered_turn_count"] == 74
    assert repository.progress["range_end"]["id"] == "msg-073"
    assert model_calls.count("summarize") == 1 and active_transactions == 0

    async def snapshot(_conn, **_kwargs):
        return {"id": "ctx-current", "included_message_ids": ["msg-current"],
                "included_file_ids": [], "conversation_authority_json": receipt}

    result = await materialize_worker_context_snapshot(
        object(), identity={**_SCOPE, "run_id": "run-current", "engine": "claude"},
        context_snapshot_id="ctx-current", snapshot_loader=snapshot,
        message_loader=lambda *_args, **_kwargs: None, history_page_loader=page,
        checkpoint_loader=checkpoint,
        context_projector=lambda value: {"context_snapshot_id": value["id"]},
        prepared_checkpoint_id=checkpoint_id,
    )
    assert result is not None
    assert result["conversation_context"]["message_count"] == 82
    assert result["conversation_context"]["checkpoint_id"] == "ccp-built"
    assert result["conversation_context"]["selected_message_count"] == 8
    assert result["conversation_context"]["messages"][0]["message_id"] == "msg-074"
    rows[74] = {**rows[74], "content": "tampered"}
    assert await materialize_worker_context_snapshot(
        object(), identity={**_SCOPE, "run_id": "run-current", "engine": "claude"},
        context_snapshot_id="ctx-current", snapshot_loader=snapshot,
        message_loader=lambda *_args, **_kwargs: None, history_page_loader=page,
        checkpoint_loader=checkpoint,
        context_projector=lambda value: {"context_snapshot_id": value["id"]},
        prepared_checkpoint_id=checkpoint_id,
    ) is None


@pytest.mark.asyncio
async def test_checkpoint_redelivery_replays_committed_building_prefix_without_model_side_effects():
    rows = [
        {"id": f"msg-{index:03d}", "run_id": f"run-{index:03d}", "role": "user",
         "content": "constraint " + "x" * 128,
         "created_at": f"2026-09-15T00:00:{index:02d}+00:00", "session_generation": index + 1}
        for index in range(10)
    ]
    chain = ConversationSourceChain(_SCOPE, 12, "run-current", "msg-current")
    chain.add_page(rows)
    receipt = chain.receipt()

    @asynccontextmanager
    async def transaction():
        yield object()

    class Repository:
        saved = None
        first_response_lost = True
        state = "building"

        async def load_source(self, _conn, **_kwargs):
            return {**_SCOPE, "session_generation": 12, "model_id": "model-a",
                    "model_value": "claude-custom", "model_gateway_revision": 3,
                    "max_input_tokens": 32000, "max_output_tokens": 2048,
                    "conversation_authority_json": receipt, "included_message_ids": ["msg-current"],
                    "current_user_text": "continue", "input_message": "continue"}

        async def claim(self, _conn, **kwargs):
            if self.saved is None:
                return {"id": "ccp-built", "builder_lease_id": "cbl-built",
                        "covered_message_count": 0, "summary_text": None}
            saved = self.saved
            return {"id": "ccp-built", "builder_lease_id": "cbl-reclaimed",
                    "covered_message_count": saved["covered_message_count"],
                    "covered_turn_count": saved["covered_turn_count"],
                    "source_sha256": saved["source_sha256"],
                    "range_end_id": saved["range_end"]["id"],
                    "summary_text": saved["summary"], "summary_sha256": saved["summary_sha256"]}

        async def assert_lease(self, _conn, **_kwargs):
            assert self.state == "building"

        async def save_progress(self, _conn, **kwargs):
            self.saved = kwargs
            if self.first_response_lost:
                self.first_response_lost = False
                raise RuntimeError("saved-but-response-lost")

        async def complete(self, _conn, **kwargs):
            assert self.saved is not None and kwargs["source_sha256"] == receipt["source_sha256"]
            self.state = "ready"

    repo = Repository()
    async def page(_conn, **kwargs):
        return [row for row in rows if kwargs["after_id"] is None or row["id"] > kwargs["after_id"]][:4]

    async def checkpoint(_conn, *, scope, run_id, checkpoint_id=None):
        assert scope == _SCOPE and run_id == "run-current"
        if checkpoint_id is None:
            return None
        assert repo.state == "ready"
        return {"id": "ccp-built", "source_sha256": repo.saved["source_sha256"]}

    model_calls = []
    async def count(*, run_id, source_text):
        assert run_id == "run-current"
        return len(source_text.encode()) // 2

    async def summarize(*, run_id, source_text):
        assert run_id == "run-current" and "constraint" in source_text
        model_calls.append(source_text)
        return {"summary": "Earlier constraints survive.", "input_tokens": 100,
                "output_tokens": 12}

    builder = ConversationCheckpointBuilder(repository=repo, page_loader=page,
        checkpoint_loader=checkpoint, count_tokens=count, summarize=summarize)
    fields = dict(transaction_factory=transaction, tenant_id="tenant-a", run_id="run-current",
                  context_snapshot_id="ctx-current")
    with pytest.raises(RuntimeError, match="saved-but-response-lost"):
        await builder.prepare(**fields)
    assert repo.saved["covered_message_count"] == 2
    assert await builder.prepare(**fields) == "ccp-built"
    assert len(model_calls) == 1 and repo.state == "ready"
