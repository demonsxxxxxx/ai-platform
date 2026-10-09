from types import SimpleNamespace

import pytest

from app.runs.api import (
    WorkerLockedSnapshotService,
    WorkerLockedAuthorizationService,
)


@pytest.mark.asyncio
async def test_locked_snapshot_checks_profile_against_restored_model_before_authorization():
    calls = []
    source = {"trace_id": "original"}
    restored = {"trace_id": "restored"}
    payload = SimpleNamespace(input={"message": "current"})
    locked_payload = SimpleNamespace(agent_profile={"revision": 1})

    async def load_model(row, *, conn, run_identity, load_run_model_snapshot):
        calls.append("model")
        assert row is source
        return restored

    def profile_valid(profile, row):
        calls.append("profile")
        assert row is restored and profile == {"revision": 1}
        return False

    resolver = WorkerLockedSnapshotService(
        identity=lambda payload, row: {"run_id": "run"},
        mismatch_fields=lambda payload, identity: [],
        load_model=load_model,
        model_loader=lambda *args: None,
        trace_id=lambda payload, row: row["trace_id"],
        parse_payload=lambda row, *, run_identity: locked_payload,
        profile_identity_valid=profile_valid,
        reconciliation_profile_matches=lambda *args: True,
    )
    snapshot = await resolver.resolve(
        object(), payload=payload, locked_run=source,
        trace_id="queue", reconciliation=False,
    )
    assert calls == ["model", "profile"]
    assert snapshot.locked_run is restored
    assert snapshot.trace_id == "restored"
    assert snapshot.payload is locked_payload
    assert snapshot.valid is False


@pytest.mark.asyncio
async def test_locked_run_without_valid_snapshot_fails_before_skill_and_capability_admission():
    calls = []

    async def load_model(locked, **kwargs):
        calls.append("load_model")
        return locked

    async def fail_snapshot(conn, **kwargs):
        calls.append("fail_snapshot")
        assert kwargs["run_identity"]["run_id"] == "run"
        return SimpleNamespace(
            payload=kwargs["payload"],
            outcome=SimpleNamespace(status="failed", error_code="invalid_snapshot", error_message="safe"),
        )

    async def should_not_run(*args, **kwargs):
        raise AssertionError("Invalid snapshot cannot authorize a capability")

    service = WorkerLockedAuthorizationService(
        snapshot=WorkerLockedSnapshotService(
            identity=lambda payload, locked: {"run_id": "run"},
            mismatch_fields=lambda *args: [],
            load_model=load_model,
            model_loader=should_not_run,
            trace_id=lambda *args: "trace",
            parse_payload=lambda *args, **kwargs: None,
            profile_identity_valid=lambda *args: True,
            reconciliation_profile_matches=lambda *args: True,
        ),
        skill_materialize=should_not_run,
        profile_authorize=should_not_run,
        capability_authority=SimpleNamespace(authorize=should_not_run),
        audit_authority=SimpleNamespace(record_admission=should_not_run),
        distribution_authority=SimpleNamespace(denied=should_not_run),
        failures=SimpleNamespace(
            pre_dispatch_error=should_not_run,
            invalid_snapshot=fail_snapshot,
            capability_denial=should_not_run,
        ),
    )
    decision = await service.authorize(
        object(), payload=SimpleNamespace(run_id="run"), locked_run={"status": "queued"},
        current_principal=None, attempt_authority=object(), capabilities=object(),
        attempt_id="attempt", trace_id="trace", reconciliation=False,
    )
    assert decision.outcome.error_code == "invalid_snapshot"
    assert decision.publish_after_commit is True
    assert calls == ["load_model", "fail_snapshot"]
