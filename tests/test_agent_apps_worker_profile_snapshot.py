from types import SimpleNamespace

import pytest

from app.agent_apps.api import reauthorize_worker_locked_profile, worker_profile_snapshot_matches


@pytest.mark.asyncio
async def test_locked_profile_reauthorization_limits_mcp_to_current_admission():
    payload = SimpleNamespace(
        agent_profile={"revision": 2, "content_hash": "hash", "skill_set": [], "default_mcp_tool_ids": ["gateway::old-default"]},
        skill_manifests=[], executor_type="claude-agent-worker",
        execution_kind="skill", input={"mcp_tool_ids": ["allowed", "gateway::old-default", "revoked"]},
    )
    payload.model_copy = lambda *, update: SimpleNamespace(**{**vars(payload), **update})

    async def reauthorize(conn, **kwargs):
        assert kwargs["revision"] == 2
        assert kwargs["pinned_default_mcp_tool_ids"] == ("gateway::old-default",)
        return SimpleNamespace(mcp_tool_ids=("allowed", "gateway::old-default", "gateway::new-default"))

    result, reason = await reauthorize_worker_locked_profile(
        object(), payload=payload, principal=object(),
        run_identity={"agent_id": "profile"},
        reauthorize=reauthorize, snapshot_matches=lambda *_: True,
        extract_mcp_tool_ids=lambda source: source["mcp_tool_ids"],
    )
    assert reason is None
    assert result.input["mcp_tool_ids"] == ["allowed", "gateway::old-default"]
    assert payload.input["mcp_tool_ids"] == ["allowed", "gateway::old-default", "revoked"]


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
