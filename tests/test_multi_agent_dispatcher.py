import pytest

from app import multi_agent_dispatcher
from app.capability_distribution import CapabilityAuthorizationDenial
from app.repositories import RepositoryAuthorizationError, RepositoryConflictError


@pytest.fixture(autouse=True)
def enable_deferred_dispatcher_inner_flow_for_legacy_mechanics(monkeypatch, request):
    """Keep isolated future-flow mechanics covered without weakening the live guard test."""

    if request.node.name == "test_worker_dispatcher_is_deferred_before_config_or_candidate_scan":
        return
    monkeypatch.setattr(multi_agent_dispatcher, "_raise_multi_agent_dispatch_not_available", lambda: None)


def test_settings_accept_malformed_dispatcher_numeric_env_for_pass_level_fail_closed(monkeypatch):
    from app.settings import Settings

    monkeypatch.setenv("MULTI_AGENT_DISPATCH_WORKER_INTERVAL_SECONDS", "not-a-float")
    monkeypatch.setenv("MULTI_AGENT_DISPATCH_WORKER_LIMIT", "not-an-int")

    settings = Settings(_env_file=None)

    assert settings.multi_agent_dispatch_worker_interval_seconds == "not-a-float"
    assert settings.multi_agent_dispatch_worker_limit == "not-an-int"


@pytest.mark.asyncio
async def test_worker_dispatcher_skips_when_disabled(monkeypatch):
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
async def test_worker_dispatcher_is_deferred_before_config_or_candidate_scan(monkeypatch):
    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        default_tenant_id = "default"

    def forbidden_transaction():
        raise AssertionError("deferred dispatch must not open a transaction")

    async def forbidden_candidates(*_args, **_kwargs):
        raise AssertionError("deferred dispatch must not list candidates")

    monkeypatch.setattr(multi_agent_dispatcher, "transaction", forbidden_transaction)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        forbidden_candidates,
        raising=False,
    )

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(Settings(), now=10.0)

    assert result == []


@pytest.mark.asyncio
async def test_worker_dispatcher_dispatches_candidate_parent_and_enqueues_child(monkeypatch):
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
            "principal_roles": ["QA-Operator"],
            "principal_department_id": "qa",
            "auth_source": "session-token",
            "file_ids": [],
            "input": {"message": "build feature"},
            "executor_type": "claude-agent-worker",
            "skill_version": "0.1.0",
            "release_decision": {},
            "event_id": "evt-handoff",
            "child_event_id": "evt-child-created",
            "audit_id": "aud-handoff",
        }

    async def prepare_queue(conn, *, copied, principal, queue_principal=None, source, authorized_source_run_id=None):
        calls.append(
            (
                "prepare",
                copied["run_id"],
                principal.user_id,
                queue_principal.user_id,
                source,
                authorized_source_run_id,
            )
        )
        assert source == "worker_multi_agent_dispatcher"
        assert authorized_source_run_id == "run-parent"
        assert queue_principal.roles == ["qa-operator"]
        assert queue_principal.department_id == "qa"
        assert queue_principal.source == "session-token"
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

    async def prepare_queue(conn, *, copied, principal, queue_principal=None, source, authorized_source_run_id=None):
        assert authorized_source_run_id == "run-parent"
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
    assert calls == [("tx_enter",), ("tx_exit", True), ("tx_enter",), ("tx_exit", False)]


@pytest.mark.asyncio
async def test_worker_dispatcher_rolls_back_claim_and_child_when_queue_preparation_conflicts(monkeypatch):
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
            calls.append(("tx_exit", exc_type.__name__ if exc_type else None))
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
        calls.append(("claim", kwargs["step_key"]))
        return {
            "dispatch_id": "dispatch-code",
            "event_id": "evt-claim",
            "audit_id": "aud-claim",
            "step": {"id": "step-code", "step_key": "code"},
        }

    async def handoff(conn, **kwargs):
        calls.append(("handoff", kwargs["dispatch_id"]))
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
            "principal_roles": ["user"],
            "principal_department_id": "qa",
            "auth_source": "session-token",
        }

    async def reject_prepare(*args, **kwargs):
        calls.append(("prepare", kwargs["copied"]["run_id"]))
        raise RepositoryConflictError("skill_version_not_released")

    async def fail_enqueue(*args, **kwargs):
        raise AssertionError("rolled-back dispatch must not enqueue")

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
    monkeypatch.setattr(multi_agent_dispatcher, "prepare_copied_run_for_queue", reject_prepare)
    monkeypatch.setattr(multi_agent_dispatcher, "enqueue_run", fail_enqueue)

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(Settings(), now=10.0)

    assert result == [
        {"run_id": "run-parent", "status": "skipped", "reason": "skill_version_not_released"}
    ]
    assert calls == [
        ("tx_enter",),
        ("tx_exit", None),
        ("tx_enter",),
        ("claim", "code"),
        ("handoff", "dispatch-code"),
        ("prepare", "run-child"),
        ("tx_exit", "RepositoryConflictError"),
    ]


@pytest.mark.asyncio
async def test_worker_dispatcher_skips_revoked_owner_and_continues_batch(monkeypatch):
    from app import multi_agent_dispatcher

    calls = []

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 2
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def list_candidates(conn, *, tenant_id, limit):
        return ["run-revoked", "run-allowed"]

    async def dispatch_one(conn, *, tenant_id, run_id, principal, settings):
        calls.append(("dispatch", run_id))
        if run_id == "run-revoked":
            raise RepositoryAuthorizationError("capability_not_authorized")
        return {
            "run_id": run_id,
            "status": "queued",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
            "child_run_id": "run-child",
            "parent_step_id": "step-code",
            "queue_payload": {"tenant_id": tenant_id, "run_id": "run-child"},
        }

    async def enqueue(payload):
        calls.append(("enqueue", payload["run_id"]))
        return 5

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher, "transaction", lambda: Transaction())
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        list_candidates,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher, "_dispatch_one_ready_parent", dispatch_one)
    monkeypatch.setattr(multi_agent_dispatcher, "enqueue_run", enqueue)

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(Settings(), now=10.0)

    assert result == [
        {"run_id": "run-revoked", "status": "skipped", "reason": "capability_not_authorized"},
        {
            "run_id": "run-allowed",
            "status": "queued",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
            "child_run_id": "run-child",
            "queue_position": 5,
        },
    ]
    assert calls == [("dispatch", "run-revoked"), ("dispatch", "run-allowed"), ("enqueue", "run-child")]


@pytest.mark.asyncio
async def test_worker_dispatcher_audits_structured_owner_denial_after_rollback_once(monkeypatch):
    from app import multi_agent_dispatcher

    events = []
    transaction_count = 0

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"

    class Transaction:
        async def __aenter__(self):
            nonlocal transaction_count
            transaction_count += 1
            self.transaction_id = transaction_count
            events.append(("enter", self.transaction_id))
            return f"conn-{self.transaction_id}"

        async def __aexit__(self, exc_type, exc, tb):
            events.append(("exit", self.transaction_id, exc_type.__name__ if exc_type else None))
            return False

    denial = CapabilityAuthorizationDenial(
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        actor_department_id="QA",
        actor_roles=("qa-operator",),
        department_scope_ids=("finance",),
        role_scope_ids=("reviewer",),
        scope_mode="allowlist",
        decision_reason="department_not_allowed",
    )

    async def list_candidates(conn, *, tenant_id, limit):
        return ["run-revoked"]

    async def dispatch_one(conn, *, tenant_id, run_id, principal, settings):
        events.append(("dispatch", conn, run_id))
        raise RepositoryAuthorizationError("capability_not_authorized", denial=denial)

    async def get_run(conn, *, tenant_id, run_id, for_update=False):
        events.append(("owner", conn, tenant_id, run_id, for_update))
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "user_id": "persisted-owner",
            "principal_department_id": "QA",
            "principal_roles": ["qa-operator"],
            "auth_source": "session-token",
        }

    async def append_denial(conn, **kwargs):
        events.append(
            (
                "audit",
                conn,
                kwargs["user_id"],
                kwargs["source"],
                kwargs["error"].denial.capability_id,
            )
        )
        return "aud-denied"

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher, "transaction", lambda: Transaction())
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        list_candidates,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher, "_dispatch_one_ready_parent", dispatch_one)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "get_run", get_run, raising=False)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "append_capability_authorization_denial_audit",
        append_denial,
        raising=False,
    )

    result = await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(Settings(), now=10.0)

    assert result == [
        {"run_id": "run-revoked", "status": "skipped", "reason": "capability_not_authorized"}
    ]
    assert events == [
        ("enter", 1),
        ("exit", 1, None),
        ("enter", 2),
        ("dispatch", "conn-2", "run-revoked"),
        ("exit", 2, "RepositoryAuthorizationError"),
        ("enter", 3),
        ("owner", "conn-3", "default", "run-revoked", False),
        ("audit", "conn-3", "persisted-owner", "worker_multi_agent_dispatcher", "qa-file-reviewer"),
        ("exit", 3, None),
    ]


@pytest.mark.asyncio
async def test_worker_dispatcher_does_not_silence_structured_denial_audit_failure(monkeypatch):
    from app import multi_agent_dispatcher

    audit_calls = []

    class Settings:
        multi_agent_dispatch_worker_enabled = True
        multi_agent_dispatch_worker_interval_seconds = 30.0
        multi_agent_dispatch_worker_limit = 1
        multi_agent_dispatch_worker_user_id = "system:multi-agent-dispatcher"
        default_tenant_id = "default"

    class Transaction:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    denial = CapabilityAuthorizationDenial(
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        actor_department_id="QA",
        actor_roles=("qa-operator",),
        department_scope_ids=("finance",),
        role_scope_ids=(),
        scope_mode="allowlist",
        decision_reason="department_not_allowed",
    )

    async def list_candidates(conn, *, tenant_id, limit):
        return ["run-revoked"]

    async def dispatch_one(*args, **kwargs):
        raise RepositoryAuthorizationError("capability_not_authorized", denial=denial)

    async def get_run(*args, **kwargs):
        return {"user_id": "persisted-owner"}

    async def fail_audit(*args, **kwargs):
        audit_calls.append(kwargs)
        raise RuntimeError("denial_audit_unavailable")

    monkeypatch.setattr(multi_agent_dispatcher, "_next_multi_agent_dispatch_at", 0.0, raising=False)
    monkeypatch.setattr(multi_agent_dispatcher, "transaction", lambda: Transaction())
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "list_multi_agent_dispatch_candidate_run_ids",
        list_candidates,
        raising=False,
    )
    monkeypatch.setattr(multi_agent_dispatcher, "_dispatch_one_ready_parent", dispatch_one)
    monkeypatch.setattr(multi_agent_dispatcher.repositories, "get_run", get_run, raising=False)
    monkeypatch.setattr(
        multi_agent_dispatcher.repositories,
        "append_capability_authorization_denial_audit",
        fail_audit,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="denial_audit_unavailable"):
        await multi_agent_dispatcher.dispatch_multi_agent_ready_steps_for_worker(Settings(), now=10.0)

    assert len(audit_calls) == 1
