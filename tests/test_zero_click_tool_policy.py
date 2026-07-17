from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from app.tool_policy import evaluate_tool_policy


def policy_tool(**overrides: object) -> dict[str, object]:
    tool: dict[str, object] = {
        "requested_identity": "Read",
        "declared_identities": ["Read"],
        "registered": True,
        "declared": True,
        "active": True,
        "distributed": True,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "risk_level": "low",
        "write_capable": False,
    }
    tool.update(overrides)
    return tool


def test_declared_active_read_and_write_capabilities_allow_without_approval_state():
    read = evaluate_tool_policy(tool=policy_tool())
    write = evaluate_tool_policy(
        tool=policy_tool(requested_identity="Write", declared_identities=["Write"], risk_level="high", write_capable=True)
    )

    assert (read.outcome, write.outcome) == ("allow", "allow")
    assert read.reason == "tool_policy_allowed"
    assert write.reason == "tool_policy_allowed"
    assert not hasattr(read, "permission_request_id")
    assert not hasattr(write, "decision")


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"requested_identity": ""}, "tool_identity_malformed"),
        ({"requested_identity": "mcp__server"}, "tool_identity_malformed"),
        ({"requested_identity": "mcp__server__tool__suffix", "declared_identities": ["mcp__server__tool"]}, "tool_identity_malformed"),
        ({"requested_identity": "mcp__server__search_extra", "declared_identities": ["mcp__server__search"]}, "tool_identity_undeclared"),
        ({"registered": False}, "tool_not_registered"),
        ({"declared": False}, "tool_identity_undeclared"),
        ({"active": False}, "tool_not_active"),
        ({"distributed": False}, "tool_not_distributed"),
        ({"identity_authorized": False}, "tool_identity_not_authorized"),
        ({"object_authorized": False}, "tool_object_not_authorized"),
        ({"parameters_authorized": False}, "tool_parameters_not_authorized"),
    ],
)
def test_invalid_or_out_of_scope_capabilities_deny_synchronously(overrides, reason):
    decision = evaluate_tool_policy(tool=policy_tool(**overrides))

    assert decision.outcome == "deny"
    assert decision.reason == reason


def test_exact_prefixed_and_adapter_bare_identity_resolve_to_one_declaration():
    prefixed = evaluate_tool_policy(
        tool=policy_tool(
            requested_identity="mcp__context__read_context_file",
            declared_identities=["mcp__context__read_context_file"],
        )
    )
    bare = evaluate_tool_policy(
        tool=policy_tool(
            requested_identity="mcp__context__read_context_file",
            declared_identities=["mcp__context__read_context_file"],
            adapter_original_identity="read_context_file",
        )
    )

    assert (prefixed.outcome, bare.outcome) == ("allow", "allow")
    assert prefixed.canonical_identity == bare.canonical_identity == "mcp__context__read_context_file"


def test_runtime_approval_write_routes_fail_closed_before_any_repository_mutation(monkeypatch):
    async def fail_create(*args, **kwargs):
        raise AssertionError("removed route must not create a permission row")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.tool_permissions.repositories.create_tool_permission_request", fail_create)
    client = TestClient(create_app())
    headers = {
        "X-AI-User-ID": "user-a",
        "X-AI-User-Name": "user-a",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "tenant-a",
    }

    requested = client.post(
        "/api/ai/runs/run-a/tool-permissions/request",
        headers=headers,
        json={"tool_id": "tool-a"},
    )
    decided = client.post(
        "/api/ai/runs/run-a/tool-permissions/tpr-a/decision",
        headers=headers,
        json={"decision": "deny"},
    )

    assert requested.status_code == decided.status_code == 410
    assert requested.json()["detail"] == decided.json()["detail"] == "tool_permission_runtime_approval_removed"


@pytest.mark.asyncio
async def test_legacy_callback_resolver_is_a_non_mutating_fail_closed_shim():
    from app.executors.claude_agent_worker import resolve_claude_sdk_tool_permission

    outcome = await resolve_claude_sdk_tool_permission(tool_name="Bash", run_id="run-a")

    assert outcome == {"allowed": False, "reason": "tool_permission_runtime_approval_removed"}


def test_no_active_production_permission_request_producer_or_sandbox_callback_sender_remains():
    root = Path(__file__).resolve().parents[1]
    production_files = [
        root / "app/executors/claude_agent_worker.py",
        root / "app/routes/tool_permissions.py",
        root / "app/runtime/sandbox/executor_app.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in production_files)

    assert ".create_tool_permission_request(" not in combined
    assert "/runtime/callbacks/tool-permission" not in (root / "app/runtime/sandbox/executor_app.py").read_text(encoding="utf-8")
    runner_sources = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "app/executors/claude_agent_sdk_runner.py",
            "app/executors/claude_agent_worker.py",
            "app/runtime/sandbox/executor_app.py",
        )
    )
    assert "on_tool_permission" not in runner_sources
    assert "get_exact_tool_permission_decision(" not in runner_sources
    assert "consume_tool_permission_decision(" not in runner_sources
