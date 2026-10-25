import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.execution.application import supervision
from app.execution.domain.supervision import (
    SupervisionCandidate,
    SupervisionCapacities,
    SupervisionDomain,
    select_supervision_candidates,
)
from app.execution.infrastructure.supervision_postgres import _candidate
from app.settings import Settings


NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def candidate(domain, candidate_id, *, action="observe", deadline=None, priority=100):
    return SupervisionCandidate(
        domain=domain,
        candidate_id=candidate_id,
        action=action,
        deadline_at=deadline or NOW,
        priority=priority,
    )


def test_deadline_selection_has_independent_domain_budgets_and_cancel_priority():
    candidates = [
        candidate(SupervisionDomain.PROBE, "normal"),
        candidate(SupervisionDomain.PROBE, "cancel", priority=0),
        candidate(SupervisionDomain.PROBE, "later", priority=200),
        candidate(SupervisionDomain.DELIVERY, "delivery-a"),
        candidate(SupervisionDomain.SETTLEMENT, "receipt"),
        candidate(SupervisionDomain.CLEANUP, "cleanup"),
        candidate(
            SupervisionDomain.DELIVERY,
            "not-due",
            deadline=NOW + timedelta(seconds=1),
        ),
    ]

    selected = select_supervision_candidates(
        candidates,
        now=NOW,
        capacities=SupervisionCapacities(delivery=1, probe=2, settlement=1, cleanup=1),
    )

    assert [(item.domain, item.candidate_id) for item in selected] == [
        (SupervisionDomain.DELIVERY, "delivery-a"),
        (SupervisionDomain.PROBE, "cancel"),
        (SupervisionDomain.PROBE, "normal"),
        (SupervisionDomain.SETTLEMENT, "receipt"),
        (SupervisionDomain.CLEANUP, "cleanup"),
    ]


def test_candidate_requires_explicit_timezone_aware_deadline():
    with pytest.raises(ValueError, match="timezone_aware"):
        candidate(
            SupervisionDomain.PROBE,
            "naive",
            deadline=datetime(2026, 10, 25, 12),
        )


def test_postgres_candidate_preserves_cancel_priority_zero():
    observed = _candidate(
        {
            "candidate_id": "lease-a",
            "action": "probe_executor",
            "deadline_at": NOW,
            "priority": 0,
            "facts": {},
        },
        SupervisionDomain.PROBE,
    )
    assert observed.priority == 0


def test_capacities_follow_current_validated_owner_settings_independently():
    budgets = supervision.capacities_from_settings(SimpleNamespace(
        supervision_delivery_capacity=13,
        supervision_probe_capacity=4,
        supervision_settlement_capacity=3,
        supervision_cleanup_capacity=17,
    ))
    assert (budgets.delivery, budgets.probe, budgets.settlement, budgets.cleanup) == (
        13,
        4,
        3,
        17,
    )
    assert supervision.batches_from_settings(SimpleNamespace(
        run_delivery_intent_scan_batch_size=11,
        object_delete_batch_limit=19,
    )) == {
        SupervisionDomain.DELIVERY: 11,
        SupervisionDomain.PROBE: 1,
        SupervisionDomain.SETTLEMENT: 1,
        SupervisionDomain.CLEANUP: 19,
    }


def test_existing_probe_deadline_is_a_validated_setting_with_the_same_default():
    settings = Settings(_env_file=None)
    assert settings.executor_heartbeat_stale_seconds == 45
    assert settings.executor_reconciliation_claim_stale_seconds == 300
    assert settings.supervision_claim_ttl_seconds == 3600
    assert (settings.supervision_delivery_capacity,
            settings.supervision_probe_capacity,
            settings.supervision_settlement_capacity,
            settings.supervision_cleanup_capacity) == (1, 1, 2, 1)
    assert settings.execution_supervision_enabled is False
    assert settings.execution_supervision_shadow_enabled is False
    with pytest.raises(ValidationError):
        Settings(_env_file=None, executor_heartbeat_stale_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "unified"])
async def test_current_persistent_owner_wraps_existing_action_owner(monkeypatch, mode):
    claim = {"domain": "delivery", "owner_mode": mode, "owner_version": 2}
    released = []
    called = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    async def release(_connection, actual_claim):
        released.append(actual_claim)
        return True

    store = SimpleNamespace(
        read_domain_control=_async_value({"mode": mode}),
        claim_domain_work=_async_value(claim),
        claim_is_current=_async_value(True),
        release_domain_work=release,
    )

    async def action_owner():
        called.append(mode)
        return "owner-result"

    result = await supervision.run_current_domain_work(
        action_owner,
        transaction_factory=Transaction,
        store=store,
        domain=SupervisionDomain.DELIVERY,
        expected_mode=mode,
        worker_id="worker-a",
        ttl_seconds=3600,
    )
    assert result == "owner-result"
    assert called == [mode]
    assert released == [claim]


@pytest.mark.asyncio
async def test_draining_domain_fails_closed_without_calling_current_action_owner(monkeypatch):
    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    store = SimpleNamespace(read_domain_control=_async_value({"mode": "draining"}))

    async def action_owner():
        raise AssertionError("draining_domain_must_not_claim")

    result = await supervision.run_current_domain_work(
        action_owner,
        transaction_factory=Transaction,
        store=store,
        domain=SupervisionDomain.SETTLEMENT,
        expected_mode="legacy",
        worker_id="worker-a",
        ttl_seconds=3600,
    )
    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected_calls", [("legacy", ["legacy"]), ("unified", ["unified"]), ("draining", [])])
async def test_scheduled_action_routes_only_through_persisted_domain_mode(mode, expected_calls):
    claim = {"domain": "delivery", "owner_mode": mode, "owner_version": 2}
    released = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    async def release(_connection, actual_claim):
        released.append(actual_claim)
        return True

    store = SimpleNamespace(
        read_domain_control=_async_value({"mode": mode}),
        claim_domain_work=_async_value(claim if mode != "draining" else None),
        claim_is_current=_async_value(True),
        release_domain_work=release,
    )
    called = []

    async def action_owner():
        called.append(mode)
        return "action-result"

    result = await supervision.run_scheduled_domain_work(
        action_owner,
        transaction_factory=Transaction,
        store=store,
        domain=SupervisionDomain.DELIVERY,
        worker_id="worker-a",
        ttl_seconds=60,
        supervision_enabled=True,
    )
    assert called == expected_calls
    assert result == ("action-result" if expected_calls else None)
    assert released == ([claim] if expected_calls else [])


@pytest.mark.asyncio
async def test_persisted_unified_mode_does_not_run_legacy_operation():
    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    called = []
    store = SimpleNamespace(read_domain_control=_async_value({"mode": "unified"}))

    async def legacy_action():
        called.append(True)

    result = await supervision.run_current_domain_work(
        legacy_action,
        transaction_factory=Transaction,
        store=store,
        domain=SupervisionDomain.DELIVERY,
        expected_mode="legacy",
        worker_id="legacy-worker",
        ttl_seconds=60,
    )
    assert result is None
    assert called == []


@pytest.mark.asyncio
async def test_domain_claim_stays_owned_until_cancelled_action_finishes():
    claim = {"domain": "cleanup", "owner_mode": "unified", "owner_version": 2}
    action_started = asyncio.Event()
    finish_action = asyncio.Event()
    released = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    async def release(_connection, actual_claim):
        released.append(actual_claim)
        return True

    store = SimpleNamespace(
        read_domain_control=_async_value({"mode": "unified"}),
        claim_domain_work=_async_value(claim),
        claim_is_current=_async_value(True),
        release_domain_work=release,
    )

    async def action_owner():
        action_started.set()
        await finish_action.wait()
        return "finished"

    task = asyncio.create_task(
        supervision.run_current_domain_work(
            action_owner,
            transaction_factory=Transaction,
            store=store,
            domain=SupervisionDomain.CLEANUP,
            expected_mode="unified",
            worker_id="worker-a",
            ttl_seconds=60,
        )
    )
    await action_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert released == []

    finish_action.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released == [claim]


@pytest.mark.asyncio
async def test_shadow_observation_returns_full_scan_page_before_action_capacity():
    page = tuple(candidate(SupervisionDomain.DELIVERY, str(index)) for index in range(25))

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class Store:
        async def observe_persisted_candidates(self, _connection, **_kwargs):
            return page

    result = await supervision.observe_supervision_once(
        transaction_factory=Transaction,
        store=Store(),
        settings=SimpleNamespace(
            run_delivery_intent_scan_batch_size=25,
            object_delete_batch_limit=50,
            executor_heartbeat_stale_seconds=45,
            executor_reconciliation_claim_stale_seconds=300,
        ),
        now=NOW,
    )
    assert len(result) == 25
    assert len(select_supervision_candidates(
        result,
        now=NOW,
        capacities=SupervisionCapacities(delivery=1, probe=1, settlement=1, cleanup=1),
    )) == 1


@pytest.mark.asyncio
async def test_worker_domain_operation_preserves_legacy_path_when_supervision_disabled(monkeypatch):
    from app.bootstrap.execution_supervision import run_worker_domain_operation

    async def unexpected_supervision(*_args, **_kwargs):
        raise AssertionError("disabled_supervision_must_not_read_persisted_gate")

    monkeypatch.setattr(
        "app.bootstrap.execution_supervision.run_current_domain_work",
        unexpected_supervision,
    )

    async def legacy_action():
        return "legacy-action-result"

    assert await run_worker_domain_operation(
        legacy_action,
        settings=SimpleNamespace(execution_supervision_enabled=False),
        domain=SupervisionDomain.DELIVERY,
        expected_mode="legacy",
        worker_id="worker-a",
    ) == "legacy-action-result"


def test_operator_recovery_cli_requires_exact_fence_and_stop_evidence_inputs():
    from tools.execution_supervision import _parser

    args = _parser().parse_args([
        "recover-claim", "probe",
        "--expected-domain-version", "3",
        "--expected-claim-owner-version", "2",
        "--claim-token", "17a16868-fbcc-4738-9961-2c22a83042b5",
        "--worker-id", "worker-a",
        "--operator-id", "operator-a",
        "--stop-evidence", "pod worker-a has terminated",
    ])
    assert args.domain == "probe"
    assert args.expected_domain_version == 3
    assert args.expected_claim_owner_version == 2
    assert args.worker_id == "worker-a"
    assert args.stop_evidence == "pod worker-a has terminated"


@pytest.mark.asyncio
async def test_domain_switch_cli_refuses_while_supervision_is_disabled(monkeypatch):
    from tools import execution_supervision

    async def unexpected_transaction():
        raise AssertionError("disabled_switch_must_not_touch_database")

    monkeypatch.setattr(execution_supervision, "transaction", unexpected_transaction)
    monkeypatch.setattr(
        execution_supervision,
        "get_settings",
        lambda: SimpleNamespace(execution_supervision_enabled=False),
    )
    args = execution_supervision._parser().parse_args(
        ["activate-unified", "delivery", "--expected-version", "2"]
    )
    with pytest.raises(RuntimeError, match="execution_supervision_disabled"):
        await execution_supervision._run(args)


def _async_value(value):
    async def resolve(*_args, **_kwargs):
        return value

    return resolve
