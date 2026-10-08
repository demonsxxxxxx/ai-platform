from types import SimpleNamespace

import pytest

from app.skills.api import WorkerSkillDispatchAuthorization


@pytest.mark.asyncio
async def test_worker_skill_pin_revocation_blocks_current_skill_and_catalog_lookup():
    calls = []

    async def validate_snapshots(conn, **kwargs):
        calls.append("snapshot")

    async def revoked_replay(conn, **kwargs):
        calls.append("historical_pin")
        raise ValueError("revoked")

    async def should_not_load(*args, **kwargs):
        raise AssertionError("Revoked pin cannot resolve current Skill or catalog")

    service = WorkerSkillDispatchAuthorization(
        validate_snapshots=validate_snapshots,
        validate_replay=revoked_replay,
        resolve_skill=should_not_load,
        resolve_catalog=should_not_load,
        catalog_binding=lambda identity: identity,
        attach_catalog=lambda payload, **kwargs: payload,
        conflict_error=RuntimeError,
        authorization_error=ValueError,
        not_found_error=LookupError,
        catalog_error=KeyError,
        synthetic_skill_id="general-chat",
    )
    decision = await service.candidate(
        object(),
        payload=SimpleNamespace(
            skill_manifests=[], release_decision={}, agent_profile=None,
            skill_version="pin", executor_type="claude-agent-worker",
        ),
        run_identity={"tenant_id": "tenant", "run_id": "run", "skill_id": "skill"},
    )
    assert decision.denial_reason == "skill_historical_pin_revoked"
    assert calls == ["snapshot", "historical_pin"]
