from types import SimpleNamespace

import pytest

from app.agent_apps.api import reauthorize_worker_locked_profile, worker_profile_snapshot_matches


@pytest.mark.asyncio
async def test_locked_profile_reauthorization_limits_mcp_to_current_admission():
    payload = SimpleNamespace(
        agent_profile={"revision": 2, "content_hash": "hash", "skill_set": []},
        skill_manifests=[], executor_type="claude-agent-worker",
        execution_kind="skill", input={"mcp_tool_ids": ["allowed", "revoked"]},
    )
    payload.model_copy = lambda *, update: SimpleNamespace(**{**vars(payload), **update})

    async def reauthorize(conn, **kwargs):
        assert kwargs["revision"] == 2
        return SimpleNamespace(mcp_tool_ids=("allowed",))

    result, reason = await reauthorize_worker_locked_profile(
        object(), payload=payload, principal=object(),
        run_identity={"agent_id": "profile"},
        reauthorize=reauthorize, snapshot_matches=lambda *_: True,
        extract_mcp_tool_ids=lambda source: source["mcp_tool_ids"],
    )
    assert reason is None
    assert result.input["mcp_tool_ids"] == ["allowed"]
    assert payload.input["mcp_tool_ids"] == ["allowed", "revoked"]


def test_locked_profile_snapshot_fail_closes_invalid_mcp_selector_before_equality():
    profile = {"instructions": "pinned"}
    admission = SimpleNamespace(private_execution_input=profile, mcp_tool_ids=())
    options = {
        "payload_profile": profile,
        "payload_input": {"mcp_tool_ids": ["tool"]},
        "admission": admission,
        "selector_errors": (ValueError,),
    }
    assert worker_profile_snapshot_matches(
        **options, validate_mcp_selector=lambda _: ["tool"]
    ) is True

    def reject_selector(_):
        raise ValueError("invalid selector")

    assert worker_profile_snapshot_matches(
        **options, validate_mcp_selector=reject_selector
    ) is False
    assert worker_profile_snapshot_matches(
        **{**options, "payload_profile": {"instructions": "changed"}},
        validate_mcp_selector=lambda _: ["tool"],
    ) is False
