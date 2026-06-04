import pytest

from app.context_builder import initial_context_summary, record_initial_context_snapshot


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_records_effective_memory_policy_without_reading_long_term_memory(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-policy"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-policy"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={"message": "hello"},
        message_ids=["msg-a"],
        file_ids=[],
        source="runs_api",
    )

    assert calls[0] == (
        "policy",
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "agent_id": "general-agent",
        },
    )
    snapshot = calls[1][1]
    assert snapshot["included_memory_record_ids"] == []
    assert snapshot["redaction_summary_json"] == {
        "input_payload_stored": False,
        "raw_skill_selector_stored": False,
        "long_term_memory_read": False,
        "memory_policy_source": "stored",
        "memory_enabled": False,
        "long_term_memory_enabled": False,
        "retention_days": 30,
    }
    assert snapshot["payload_json"]["memory_policy"] == {
        "source": "stored",
        "memory_enabled": False,
        "long_term_memory_enabled": False,
        "retention_days": 30,
    }
    assert context_ref["memory_policy"]["memory_enabled"] is False
    assert context_ref["memory_record_count"] == 0


def test_initial_context_summary_clamps_long_term_memory_policy_projection():
    summary = initial_context_summary(
        source="chat",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={"message": "hello"},
        message_ids=[],
        file_ids=[],
        memory_policy={
            "source": "stored",
            "memory_enabled": True,
            "long_term_memory_enabled": True,
            "retention_days": 90,
        },
    )

    assert summary["memory_policy"]["memory_enabled"] is True
    assert summary["memory_policy"]["long_term_memory_enabled"] is False
