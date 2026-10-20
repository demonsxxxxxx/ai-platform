"""Selection policy and existing Chat/source-replay compatibility boundaries."""

from types import SimpleNamespace

import pytest

from app.identity.infrastructure.capability_distributions_postgres import _capability_not_authorized
from app.mcp import api as mcp_api
from app.mcp.application import runtime_registry
from app.auth import normalize_roles
from app.capability_distribution import CapabilityAccessContext, CapabilityDistributionSubject, resolve_capability_access
from app.tool_policy import evaluate_tool_policy
from app.mcp.application.selection_authorization import McpSelectionAuthorization
from app.platform.postgres.errors import RepositoryAuthorizationError, RepositoryConflictError


MODES = ("authorize_selected_chat", "authorize_available_chat", "authorize_selected_replay")


def tool(tool_id="gateway::search", **changes):
    return {
        "tool_id": tool_id, "server_id": "gateway", "allowed_tools": [tool_id.split("::")[-1]],
        "endpoint": "", "auth_mode": "none", "transport_type": "streamable_http",
        "effective_status": "active", "server_status": "active", "visible_to_user": True,
        "risk_level": "high", "write_capable": True, **changes,
    }


@pytest.fixture
def selection():
    state = SimpleNamespace(
        tools={"gateway::search": tool(), "gateway::other": tool("gateway::other")},
        distribution={"status": "active", "visible_to_user": True,
                      "department_ids": ["100"], "allowed_roles": ["user"]},
        error=None, calls=[],
    )

    async def get_tool(conn, **kwargs):
        state.calls.append(("tool", kwargs))
        return state.tools.get(kwargs["tool_id"]) if kwargs["tenant_id"] == "tenant-a" else None

    async def get_distribution(conn, **kwargs):
        state.calls.append(("distribution", kwargs))
        if state.error:
            raise state.error
        return state.distribution

    service = McpSelectionAuthorization(
        get_tool=get_tool, get_distribution=get_distribution,
        authorization_error=_capability_not_authorized, conflict_error=RepositoryConflictError,
        normalize_roles=normalize_roles, context_type=CapabilityAccessContext,
        subject_type=CapabilityDistributionSubject, resolve_access=resolve_capability_access,
        evaluate_policy=evaluate_tool_policy,
    )
    state.service = service
    state.arguments = dict(
        tenant_id="tenant-a", tool_ids=["gateway::search"], principal_department_id="qa",
        principal_department_ids=("100", "110"),
        principal_roles=["user"], is_admin=False, permissions=["chat:write"],
    )
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_selection_preserves_order_and_server_tenant_lookup(selection, mode):
    selection.arguments["tool_ids"] = ["gateway::other", "gateway::search"]
    result = await getattr(selection.service, mode)(object(), **selection.arguments)
    assert [entry["tool_id"] for entry in result] == selection.arguments["tool_ids"]
    assert all(kwargs["tenant_id"] == "tenant-a" for _, kwargs in selection.calls)
    assert [kwargs for kind, kwargs in selection.calls if kind == "distribution"] == [
        {"tenant_id": "tenant-a", "capability_kind": "mcp_server", "capability_id": "gateway"},
    ] * 2
    selection.calls.clear()
    selection.arguments["tool_ids"] = []
    assert await getattr(selection.service, mode)(object(), **selection.arguments) == []
    assert selection.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("case", ["missing", "metadata", "wrong_identity", "disabled", "hidden", "distribution", "department", "tenant"])
async def test_selection_denies_or_filters_without_granting_partial_selected_result(selection, mode, case):
    selection.arguments["tool_ids"] = ["gateway::other", "gateway::search"]
    if case == "missing":
        del selection.tools["gateway::search"]
    elif case == "metadata":
        selection.tools["gateway::search"]["auth_mode"] = "platform-managed"
    elif case == "wrong_identity":
        selection.tools["gateway::search"] = tool("gateway::other")
    elif case == "disabled":
        selection.tools["gateway::search"]["server_status"] = "disabled"
    elif case == "hidden":
        selection.tools["gateway::search"]["visible_to_user"] = False
    elif case == "distribution":
        selection.distribution = None
    elif case == "department":
        selection.arguments["principal_department_ids"] = ("200",)
    else:
        selection.arguments["tenant_id"] = "tenant-b"
    if mode == "authorize_available_chat":
        result = await getattr(selection.service, mode)(object(), **selection.arguments)
        assert [entry["tool_id"] for entry in result] == (
            [] if case in {"distribution", "department", "tenant"} else ["gateway::other"]
        )
    else:
        with pytest.raises(RepositoryAuthorizationError, match="capability_not_authorized"):
            await getattr(selection.service, mode)(object(), **selection.arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_duplicate_selection_retains_boundary_audit_identity_before_lookup(selection, mode):
    selection.arguments["tool_ids"] *= 2
    with pytest.raises(RepositoryAuthorizationError) as error:
        await getattr(selection.service, mode)(object(), **selection.arguments)
    assert error.value.denial.capability_id == (
        "gateway::search" if mode == "authorize_selected_replay" else "mcp_tool"
    )
    assert selection.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_corrupt_distribution_preserves_chat_conflict_and_replay_denial(selection, mode):
    original = RepositoryConflictError("capability_distribution_scope_invalid")
    selection.error = original
    expected = RepositoryAuthorizationError if mode == "authorize_selected_replay" else RepositoryConflictError
    with pytest.raises(expected) as error:
        await getattr(selection.service, mode)(object(), **selection.arguments)
    if mode == "authorize_selected_replay":
        assert error.value.denial.capability_id == "gateway::search"
        assert error.value.__cause__ is original
    else:
        assert error.value is original


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_explicit_admin_and_normalized_context_are_not_recomputed(selection, monkeypatch, mode):
    original = resolve_capability_access
    contexts = []

    def resolve(context, subject, **kwargs):
        contexts.append(context)
        return original(context, subject, **kwargs)

    monkeypatch.setattr(selection.service, "_resolve_access", resolve)
    selection.arguments.update(principal_roles=[" ADMIN "], is_admin=False, permissions=["read:only"])
    if mode == "authorize_available_chat":
        assert await getattr(selection.service, mode)(object(), **selection.arguments) == []
    else:
        with pytest.raises(RepositoryAuthorizationError):
            await getattr(selection.service, mode)(object(), **selection.arguments)
    assert contexts[-1].is_admin is False
    assert contexts[-1].roles == ["admin"]
    assert contexts[-1].permissions == ["read:only"]
    selection.arguments.update(principal_roles=["guest"], principal_department_id="outside", is_admin=True)
    assert await getattr(selection.service, mode)(object(), **selection.arguments) == [tool()]
    assert contexts[-1].is_admin is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_tool_policy_is_not_bypassed_after_distribution_allows(selection, monkeypatch, mode):
    subjects = []

    def deny(*, tool):
        subjects.append(tool)
        return SimpleNamespace(allowed=False)

    monkeypatch.setattr(selection.service, "_evaluate_policy", deny)
    if mode == "authorize_available_chat":
        assert await getattr(selection.service, mode)(object(), **selection.arguments) == []
    else:
        with pytest.raises(RepositoryAuthorizationError):
            await getattr(selection.service, mode)(object(), **selection.arguments)
    assert subjects[0]["distributed"] is True
    assert subjects[0]["mcp_server"] == "gateway"
    assert subjects[0]["mcp_tool"] == "search"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    "authorize_selected_chat_mcp_tools", "authorize_available_chat_mcp_tools",
    "authorize_selected_replay_mcp_tools",
])
async def test_public_api_uses_composed_selection_and_preserves_error_mapping(monkeypatch, operation):
    from app.bootstrap import mcp as bootstrap

    class Cursor:
        async def fetchone(self):
            return {"name": "gateway", "transport": "streamable_http", "status": "active"}

    class Connection:
        async def execute(self, sql, params):
            assert "from mcp_servers" in sql
            assert params == ("tenant-a", "gateway")
            return Cursor()

    conflict = RepositoryConflictError("capability_distribution_scope_invalid")

    async def distribution(*args, **kwargs):
        assert kwargs["tenant_id"] == "tenant-a"
        raise conflict

    monkeypatch.setattr(bootstrap.mcp_runtime, "configure_runtime_dependencies", lambda **_: None)
    monkeypatch.setattr(bootstrap.mcp_runtime, "get_mcp_principal_jwt_store", lambda: object())
    monkeypatch.setattr(bootstrap.identity_capability_distributions_postgres, "get_capability_distribution_row", distribution)
    monkeypatch.setattr(runtime_registry, "_services", bootstrap._McpRuntimeServices())
    expected = RepositoryAuthorizationError if "replay" in operation else RepositoryConflictError
    with pytest.raises(expected):
        await getattr(mcp_api, operation)(
            Connection(), tenant_id="tenant-a", tool_ids=["gateway::search"],
            principal_department_id="qa", principal_roles=["user"], is_admin=False, permissions=[],
        )


@pytest.mark.asyncio
async def test_queue_preparation_keeps_current_principal_after_source_replay(monkeypatch):
    from app.auth import AuthPrincipal
    from app.control_plane_contracts import HARNESS_CHAT_EXECUTOR_TYPE
    from app.routes import runs
    from app.runs.infrastructure import replay_postgres

    assert replay_postgres.authorize_selected_replay_mcp_tools is mcp_api.authorize_selected_replay_mcp_tools
    calls = []
    denial = RepositoryAuthorizationError("capability_not_authorized")

    async def current_selection(_conn, **kwargs):
        calls.append(kwargs)
        raise denial

    async def no_skills(*args, **kwargs):
        return []

    monkeypatch.setattr(runs, "authorize_selected_chat_mcp_tools", current_selection)
    monkeypatch.setattr(runs.skills_run_snapshots, "materialize_run_skill_manifests", no_skills)
    principal = AuthPrincipal(
        user_id="user-a", display_name="User", tenant_id="tenant-a",
        department_id="current-department", roles=["admin"], permissions=["current:permission"],
    )
    with pytest.raises(RepositoryAuthorizationError) as error:
        await runs.prepare_copied_run_for_queue(
            object(), copied={
                "run_id": "child", "execution_kind": "harness_chat",
                "executor_type": HARNESS_CHAT_EXECUTOR_TYPE,
                "input": {"mcp_tool_ids": ["gateway::search"]},
                "principal_roles": ["historical-user"], "principal_department_id": "historical",
            }, principal=principal, source="copy_run", authorized_source_run_id="source",
        )
    assert error.value is denial
    assert calls == [{
        "tenant_id": "tenant-a", "tool_ids": ["gateway::search"],
        "principal_department_id": "current-department", "principal_department_ids": (), "principal_roles": ["admin"],
        "is_admin": True, "permissions": ["current:permission"],
    }]
