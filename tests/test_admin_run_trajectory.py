from contextlib import asynccontextmanager

import pytest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.runs.application.admin_run_inspection import build_admin_run_inspection
from app.runs.application.admin_evidence_search import search_admin_evidence
from app.runs.application.admin_run_comparison import build_admin_run_comparison
from app.runs.application.diagnostics import RunDiagnosticsService

from fastapi.testclient import TestClient

from app.main import create_app
from app.runs.application.admin_trajectory import project_admin_trajectory_page


def _row(sequence, event_type, payload, *, visible=True, metadata=None):
    return {
        "id": f"evt_{sequence}",
        "sequence": sequence,
        "event_type": event_type,
        "stage": "agent_kernel",
        "visible_to_user": visible,
        "payload_json": {**payload, "__stream_v4": {"version": 1, **(metadata or {})}},
        "created_at": "2026-09-23T00:00:00Z",
    }


def test_trajectory_projects_typed_committed_events_without_private_payloads():
    rows = [
        _row(1, "message.delta", {"delta": "secret fragment"}, metadata={"attempt_id": "attempt_a", "message_id": "msg_a", "execution_lease_id": "lease_private"}),
        _row(2, "tool.started", {"operation_id": "op_a", "category": "read", "display_name": "Read"}, metadata={"attempt_id": "attempt_a"}),
        _row(3, "tool.completed", {"operation_id": "op_a", "category": "read", "display_name": "Read", "duration_ms": 12, "result_summary": "Read completed"}, metadata={"attempt_id": "attempt_a"}),
        _row(4, "tool.failed", {"operation_id": "op_b", "category": "execute", "display_name": "Execute", "duration_ms": 2, "failure_category": "timeout"}),
        _row(5, "tool.completed", {"operation_id": "op_private", "category": "read", "display_name": "Private", "duration_ms": 1}, visible=False),
        _row(6, "tool.completed", {"operation_id": "op_invalid", "category": "read", "display_name": "Read", "duration_ms": 1, "raw_output": "secret"}),
        _row(7, "custom.executor.event", {"raw_output": "secret"}),
        {**_row(8, "message.delta", {"delta": "legacy"}), "stage": "legacy"},
    ]

    result = project_admin_trajectory_page(rows, sanitize_text=lambda value: value)

    assert [event["kind"] for event in result["events"]] == ["message", "action", "observation", "error"]
    assert [event["sequence"] for event in result["events"]] == [1, 2, 3, 4]
    assert result["events"][0]["attempt_id"] == "attempt_a"
    assert result["events"][1]["operation_id"] == result["events"][2]["operation_id"] == "op_a"
    assert result["omitted"] == {"private": 1, "unsupported": 2, "invalid": 1}
    serialized = str(result)
    assert "secret fragment" not in serialized
    assert "raw_output" not in serialized
    assert "lease_private" not in serialized
    assert "op_private" not in serialized


def test_trajectory_does_not_accept_unknown_fields_or_forged_event_ids():
    rows = [
        _row(1, "run.failed", {"terminal_event_id": "terminal_a", "hydrate_required": True, "projection_version": "ai-platform.chat-public-projection.v1", "code": "run_failed", "default_message": "safe", "detail": None}),
        {**_row(2, "message.started", {}), "id": "evt/unsafe"},
        _row(3, "message.delta", {"delta": "x", "prompt": "private"}),
    ]
    result = project_admin_trajectory_page(rows, sanitize_text=lambda value: value)
    assert len(result["events"]) == 1
    assert result["events"][0]["kind"] == "error"
    assert result["events"][0]["outcome"] == "run_failed"
    assert result["omitted"]["invalid"] == 2


@asynccontextmanager
async def _transaction():
    yield object()


def _headers(role="admin", tenant="tenant_a"):
    return {
        "x-ai-user-id": "user_a",
        "x-ai-user-name": "Admin A",
        "x-ai-tenant-id": tenant,
        "x-ai-roles": role,
        "x-ai-gateway-secret": "test-secret",
    }


def _auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


def test_admin_trajectory_route_is_read_only_tenant_scoped_and_paginated(monkeypatch):
    calls = []

    async def get_run(_conn, *, tenant_id, run_id):
        calls.append(("run", tenant_id, run_id))
        return {"id": run_id} if tenant_id == "tenant_a" else None

    async def list_events(_conn, *, tenant_id, run_id, after_sequence, limit):
        calls.append(("events", tenant_id, run_id, after_sequence, limit))
        return [
            _row(11, "message.started", {}),
            _row(12, "message.delta", {"delta": "private text"}),
        ]

    monkeypatch.setattr("app.auth.get_settings", _auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", _transaction)
    monkeypatch.setattr("app.runs.infrastructure.postgres.get_run", get_run)
    monkeypatch.setattr("app.streaming.infrastructure.run_events_postgres.list_run_events", list_events)
    client = TestClient(create_app())

    denied = client.get("/api/ai/admin/runs/run_a/trajectory", headers=_headers("user"))
    assert denied.status_code == 403
    missing = client.get("/api/ai/admin/runs/run_a/trajectory", headers=_headers(tenant="tenant_b"))
    assert missing.status_code == 404
    response = client.get("/api/ai/admin/runs/run_a/trajectory?after_sequence=10&limit=1", headers=_headers())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["contract_version"] == "ai-platform.admin-run-trajectory.v1"
    assert response.json()["has_more"] is True
    assert response.json()["next_after_sequence"] == 11
    assert [event["sequence"] for event in response.json()["events"]] == [11]
    assert calls == [
        ("run", "tenant_b", "run_a"),
        ("run", "tenant_a", "run_a"),
        ("events", "tenant_a", "run_a", 10, 2),
    ]



def test_inspection_uses_closed_input_and_persisted_relationships_only():
    run = {"run_id": "run_a", "model_id": "mdl-a", "model_gateway_revision": 7,
           "admitted_agent_profile_revision": 3, "admitted_agent_profile_hash": "a" * 64,
           "copied_from_run_id": "unverified-parent"}
    value = build_admin_run_inspection(run, skills=[{"skill_id": "skill-a", "skill_version": "v1"}],
        input_value={"input": {"_thinking_effort": "high", "prompt": "PRIVATE"}, "file_ids": ["file-a"],
                     "_trusted_run_source": {"version": 1, "source": "chat_api"}},
        relationships=[{"source_run_id": "run_old", "child_run_id": "run_a", "action": "retry", "status": "failed"},
                       {"source_run_id": "foreign", "child_run_id": "foreign-child", "action": "resume"}])
    assert value["input"]["fields"] == {"thinking_effort": "high", "attachment_count": 1}
    assert value["configuration"]["profile_revision"] == 3
    assert value["history"]["source_run_id"] == "run_old"
    assert len(value["history"]["relationships"]) == 1
    assert value["trigger"] == {"kind": "chat_api", "coverage": "recorded"}
    assert "PRIVATE" not in str(value) and "unverified-parent" not in str(value)


def test_evidence_search_projects_before_matching_and_scopes_attempts():
    rows = [_row(1, "message.delta", {"delta": "PRIVATE_SECRET"}),
            _row(2, "tool.failed", {"operation_id": "op_a", "category": "execute", "display_name": "Execute", "duration_ms": 0, "failure_category": "timeout"}, metadata={"attempt_id": "attempt_a"}),
            _row(3, "tool.failed", {"operation_id": "op_b", "category": "execute", "display_name": "Execute", "duration_ms": 0, "failure_category": "timeout"}, metadata={"attempt_id": "attempt_b"})]
    assert not search_admin_evidence(rows=rows, query="PRIVATE_SECRET", attempt_id=None, limit=50, diagnostics=None)["matches"]
    found = search_admin_evidence(rows=rows, query="timeout", attempt_id="attempt_a", limit=50, diagnostics=None)
    assert [item["sequence"] for item in found["matches"]] == [2]
    assert found["matches"][0]["context_after_sequence"] == 0


def test_explicit_comparison_never_diffs_unknown_cost_or_incompatible_usage():
    now = datetime.now(timezone.utc) - timedelta(days=1)
    run = {"run_id": "run_a", "status": "succeeded", "created_at": now,
           "queued_at": now, "started_at": now, "finished_at": now + timedelta(seconds=2)}
    left = {"run": run, "inspection": {"configuration": {"model_id": "mdl-a", "model_gateway_revision": 1}},
            "usage": {"total_tokens": 0, "estimated_cost_minor": 0}, "artifacts": []}
    right = {**left, "run": {**run, "run_id": "run_b", "finished_at": now + timedelta(seconds=3)},
             "inspection": {"configuration": {"model_id": "mdl-b", "model_gateway_revision": 2}}}
    result = build_admin_run_comparison(left, right)
    metrics = {item["name"]: item for item in result["metrics"]}
    assert result["selection"] == "explicit"
    assert metrics["total_ms"]["difference"] == 1000
    assert metrics["total_tokens"]["left"] == 0 and metrics["total_tokens"]["difference"] is None
    assert metrics["estimated_cost_minor"]["reason"] == "cost_unit_not_recorded"


@pytest.mark.asyncio
async def test_evidence_service_reads_no_source_for_inaccessible_run_and_bounds_scan():
    calls = []
    async def snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return None if kwargs["tenant_id"] == "foreign" else {"run": {"run_id": "run_a", "workspace_id": "w", "status": "failed"}, "attempts": []}
    async def page(conn, **kwargs):
        calls.append(("page", kwargs))
        return [_row(i, "message.started", {}) for i in range(1,1002)]
    service = RunDiagnosticsService(persistence=SimpleNamespace(get_admin_snapshot=snapshot, get_admin_evidence_page=page),
        normalize_runtime_diagnostics=lambda value: {}, runtime_diagnostics_schema_version="v1", exception_log_fields=lambda error: {})
    assert await service.search_admin_evidence(None, tenant_id="foreign", run_id="run_a", query="x") is None
    assert len(calls) == 1
    result = await service.search_admin_evidence(None, tenant_id="tenant_a", run_id="run_a", query="no-match")
    assert result["searched_source_count"] == 1000 and result["has_more"] is True
    assert result["next_after_sequence"] == 1000
    assert calls[-1][1]["limit"] == 1001


def test_search_and_comparison_routes_reject_ordinary_users_before_query():
    client = TestClient(create_app())
    from app.auth import require_principal
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: SimpleNamespace(roles=["user"], tenant_id="tenant_a", user_id="user_a")
    client = TestClient(app)
    for path, body in [("evidence-search", {"query": "timeout"}), ("comparison", {"right_run_id": "run_b"})]:
        assert client.post("/api/ai/admin/runs/run_a/" + path, json=body).status_code == 403
