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

    monkeypatch.setattr(
        supervision.supervision_postgres,
        "read_domain_control",
        _async_value({"mode": mode}),
    )
    monkeypatch.setattr(
        supervision.supervision_postgres,
        "claim_domain_work",
        _async_value(claim),
    )
    monkeypatch.setattr(
        supervision.supervision_postgres,
        "claim_is_current",
        _async_value(True),
    )
    monkeypatch.setattr(
        supervision.supervision_postgres,
        "release_domain_work",
        _async_value(released.append(claim)),
    )

    async def action_owner():
        called.append(mode)
        return "owner-result"

    result = await supervision.run_current_domain_work(
        action_owner,
        transaction_factory=Transaction,
        domain=SupervisionDomain.DELIVERY,
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

    monkeypatch.setattr(
        supervision.supervision_postgres,
        "read_domain_control",
        _async_value({"mode": "draining"}),
    )

    async def action_owner():
        raise AssertionError("draining_domain_must_not_claim")

    result = await supervision.run_current_domain_work(
        action_owner,
        transaction_factory=Transaction,
        domain=SupervisionDomain.SETTLEMENT,
        worker_id="worker-a",
        ttl_seconds=3600,
    )
    assert result is None


def _async_value(value):
    async def resolve(*_args, **_kwargs):
        return value

    return resolve
