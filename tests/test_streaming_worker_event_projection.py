import pytest

from app.streaming.api import append_worker_user_event


@pytest.mark.asyncio
async def test_worker_user_event_adds_safe_defaults_without_duplicate_terminal():
    observed = []

    async def append(conn, **fields):
        observed.append(fields)

    await append_worker_user_event(
        object(), append_event=append, tenant_id="tenant", run_id="run",
        event_type="artifact_ready", stage="artifact", message="ready",
        payload={"artifact_type": "document"}, trace_id="trace", input_token_count=0,
    )
    await append_worker_user_event(
        object(), append_event=append, tenant_id="tenant", run_id="run",
        event_type="run_failed", stage="worker", message="failed",
    )
    assert len(observed) == 1
    assert observed[0]["payload"] == {
        "visible_to_user": True, "severity": "info", "artifact_type": "document"
    }
    assert observed[0]["trace_id"] == "trace"
    assert observed[0]["input_token_count"] == 0
