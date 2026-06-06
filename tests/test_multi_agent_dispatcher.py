import pytest

from app.repositories import RepositoryConflictError


def test_settings_accept_malformed_dispatcher_numeric_env_for_pass_level_fail_closed(monkeypatch):
    from app.settings import Settings

    monkeypatch.setenv("MULTI_AGENT_DISPATCH_WORKER_INTERVAL_SECONDS", "not-a-float")
    monkeypatch.setenv("MULTI_AGENT_DISPATCH_WORKER_LIMIT", "not-an-int")

    settings = Settings(_env_file=None)

    assert settings.multi_agent_dispatch_worker_interval_seconds == "not-a-float"
    assert settings.multi_agent_dispatch_worker_limit == "not-an-int"


@pytest.mark.asyncio
async def test_worker_dispatcher_skips_when_disabled(monkeypatch):
    from app import multi_agent_dispatcher

    class Settings:
        multi_agent_dispatch_worker_enabled = False
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"

    async def fail_list_candidates(*args, **kwargs):
        raise AssertionError("disabled dispatcher must not scan parent runs")

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        fail_list_candidates,
        raising=False,
    )

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(
        Settings(),
        now=10.0,
    )

    assert result == []


@pytest.mark.asyncio
async def test_worker_dispatcher_dispatches_candidate_parent_and_enqueues_child(monkeypatch):
    from app import multi_agent_dispatcher

    calls = []

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"
        multi_agent_dispatch_lease_ttl_seconds = 300
        max_active_runs_per_user = 3

    class Transaction:
        async def __aenter__(self):
            calls.append(("tx_enter",))
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            calls.append(("tx_exit", exc_type is None))
            return False

    async def list_candidates(conn, *, tenant_id, limit):
        calls.append(("list_candidates", conn, tenant_id, limit))
        return ["run-parent"]

    async def get_run(conn, *, tenant_id, run_id, for_update=False):
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

    async def list_steps(conn, *, tenant_id, run_id):
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

    async def claim(conn, **kwargs):
        calls.append(("claim", kwargs))
        assert kwargs["claimed_by"] == "system:multi-agent-dispatcher"
        assert kwargs["step_key"] == "code"
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
                "payload_json": {"dispatch_state": "claimed"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        }

    async def handoff(conn, **kwargs):
        calls.append(("handoff", kwargs))
        assert kwargs == {
            "tenant_id": "default",
            "parent_run_id": "run-parent",
            "dispatch_id": "dispatch-code",
            "handed_off_by": "system:multi-agent-dispatcher",
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
            "file_ids": [],
            "input": {"message": "build feature"},
            "executor_type": "claude-agent-worker",
            "skill_version": "0.1.0",
            "release_decision": {},
            "event_id": "evt-handoff",
            "child_event_id": "evt-child-created",
            "audit_id": "aud-handoff",
        }

    async def prepare_queue(conn, *, copied, principal, queue_principal=None, source):
        calls.append(("prepare", copied["run_id"], principal.user_id, queue_principal.user_id, source))
        assert source == "worker_multi_agent_dispatcher"
        return {"tenant_id": "default", "run_id": copied["run_id"], "context_snapshot_id": "ctx-child"}

    async def enqueue(payload):
        calls.append(("enqueue", payload))
        return 4

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher, "transaction", lambda: Transaction())
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        list_candidates,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "get_run", get_run, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "list_run_steps", list_steps, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "claim_multi_agent_dispatch_step", claim, raising=False)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "create_multi_agent_dispatch_child_run",
        handoff,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher, "prepare_copied_run_for_queue", prepare_queue)
    monkeypatch.setattr(multi_agent_dispatcher, "enqueue_run", enqueue)

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(
        Settings(),
        now=10.0,
    )

    assert result == [
        {
            "run_id": "run-parent",
            "status": "queued",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
            "child_run_id": "run-child",
            "queue_position": 4,
            "claim_event_id": "evt-claim",
            "claim_audit_id": "aud-claim",
            "handoff_event_id": "evt-handoff",
            "child_event_id": "evt-child-created",
            "handoff_audit_id": "aud-handoff",
        }
    ]
    assert [item[0] for item in calls] == [
        "tx_enter",
        "list_candidates",
        "tx_exit",
        "tx_enter",
        "get_run",
        "list_steps",
        "claim",
        "handoff",
        "prepare",
        "tx_exit",
        "enqueue",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,limit", [("not-a-number", 1), ("nan", 1), ("inf", 1), (30.0, "not-a-number")])
async def test_worker_dispatcher_disables_pass_on_invalid_interval_or_limit(monkeypatch, interval, limit):
    from app import multi_agent_dispatcher

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = interval
        multi_agent_dispatch_worker_limit = limit
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"

    async def fail_list_candidates(*args, **kwargs):
        raise AssertionError("invalid dispatcher settings must not scan parent runs")

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        fail_list_candidates,
        raising=False,
    )

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(
        Settings(),
        now=10.0,
    )

    assert result == []


@pytest.mark.asyncio
async def test_worker_dispatcher_compensates_child_handoff_when_enqueue_fails(monkeypatch):
    from app import multi_agent_dispatcher

    calls = []

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"
        multi_agent_dispatch_lease_ttl_seconds = 300
        max_active_runs_per_user = 3

    class Transaction:
        async def __aenter__(self):
            calls.append(("tx_enter",))
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            calls.append(("tx_exit", exc_type is None))
            return False

    async def list_candidates(conn, *, tenant_id, limit):
        return ["run-parent"]

    async def get_run(conn, *, tenant_id, run_id, for_update=False):
        return {
            "id": "run-parent",
            "tenant_id": "default",
            "trace_id": "trace-parent",
            "status": "running",
            "input_json": {
                "input": {
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [{"step_key": "code", "depends_on": []}],
                }
            },
        }

    async def list_steps(conn, *, tenant_id, run_id):
        return []

    async def claim(conn, **kwargs):
        return {
            "dispatch_id": "dispatch-code",
            "event_id": "evt-claim",
            "audit_id": "aud-claim",
            "step": {"id": "step-code", "payload_json": {"dispatch_state": "claimed"}},
        }

    async def handoff(conn, **kwargs):
        return {
            "child_run_id": "run-child",
            "run_id": "run-child",
            "parent_run_id": "run-parent",
            "parent_step_id": "step-code",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
            "user_id": "user-a",
            "session_id": "session-a",
            "workspace_id": "default",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "file_ids": [],
            "input": {"message": "build feature"},
            "executor_type": "claude-agent-worker",
            "skill_version": "0.1.0",
            "release_decision": {},
            "event_id": "evt-handoff",
            "child_event_id": "evt-child-created",
            "audit_id": "aud-handoff",
        }

    async def prepare_queue(conn, *, copied, principal, queue_principal=None, source):
        return {"tenant_id": "default", "run_id": copied["run_id"], "context_snapshot_id": "ctx-child"}

    async def enqueue(payload):
        calls.append(("enqueue", payload["run_id"]))
        raise RuntimeError("redis down")

    async def compensate(conn, **kwargs):
        calls.append(("compensate", kwargs))
        return {"child_run_id": kwargs["child_run_id"], "parent_step_id": kwargs["parent_step_id"]}

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher, "transaction", lambda: Transaction())
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        list_candidates,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "get_run", get_run, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "list_run_steps", list_steps, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "claim_multi_agent_dispatch_step", claim, raising=False)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "create_multi_agent_dispatch_child_run",
        handoff,
        raising=False,
    )
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "mark_multi_agent_dispatch_enqueue_failed",
        compensate,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher, "prepare_copied_run_for_queue", prepare_queue)
    monkeypatch.setattr(multi_agent_dispatcher, "enqueue_run", enqueue)

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(
        Settings(),
        now=10.0,
    )

    assert result == [
        {
            "run_id": "run-parent",
            "status": "enqueue_failed",
            "reason": "redis down",
            "child_run_id": "run-child",
            "parent_step_id": "step-code",
            "compensated": True,
        }
    ]
    assert ("enqueue", "run-child") in calls
    compensate_call = next(item for item in calls if item[0] == "compensate")
    assert compensate_call[1]["tenant_id"] == "default"
    assert compensate_call[1]["parent_run_id"] == "run-parent"
    assert compensate_call[1]["parent_step_id"] == "step-code"
    assert compensate_call[1]["dispatch_id"] == "dispatch-code"
    assert compensate_call[1]["child_run_id"] == "run-child"


@pytest.mark.asyncio
async def test_worker_dispatcher_skips_conflicted_candidate_without_enqueue(monkeypatch):
    from app import multi_agent_dispatcher

    calls = []

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"
        multi_agent_dispatch_lease_ttl_seconds = 300

    class Transaction:
        async def __aenter__(self):
            calls.append(("tx_enter",))
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            calls.append(("tx_exit", exc_type is None))
            return False

    async def list_candidates(conn, *, tenant_id, limit):
        return ["run-parent"]

    async def get_run(conn, *, tenant_id, run_id, for_update=False):
        return {
            "id": "run-parent",
            "tenant_id": "default",
            "trace_id": "trace-parent",
            "status": "running",
            "input_json": {
                "input": {
                    "execution_mode": "multi_agent",
                    "multi_agent_steps": [{"step_key": "code", "depends_on": []}],
                }
            },
        }

    async def list_steps(conn, *, tenant_id, run_id):
        return []

    async def claim(conn, **kwargs):
        raise RepositoryConflictError("dispatch_step_not_pending")

    async def enqueue(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("conflicted dispatch must not enqueue a child run")

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher, "transaction", lambda: Transaction())
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        list_candidates,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "get_run", get_run, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "list_run_steps", list_steps, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "claim_multi_agent_dispatch_step", claim, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher, "enqueue_run", enqueue)

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(
        Settings(),
        now=10.0,
    )

    assert result == [{"run_id": "run-parent", "status": "skipped", "reason": "dispatch_step_not_pending"}]
    assert calls == [("tx_enter",), ("tx_exit", True), ("tx_enter",), ("tx_exit", True)]
