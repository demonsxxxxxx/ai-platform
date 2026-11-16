import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.mcp import api as mcp_api
from app.mcp.domain.credential_binding import bound_mcp_credential_subject
from app.mcp.infrastructure import credential_callback
from app.runtime.sandbox.callback_tokens import derive_callback_token
from app.runtime.sandbox.contracts import build_trusted_callback_target
from app.sandbox.infrastructure import leases_postgres
from app.runs.infrastructure import postgres as runs_postgres


PATH = "/api/ai/runtime/callbacks/mcp-credential"
BINDING = {"tenant_id": "tenant-a", "user_id": "user-a", "grant_id": "a" * 64, "subject_epoch": 1}
REQUEST = {"run_id": "run-a", "attempt_id": "attempt-a", "callback_token_id": "cbt:run-a:attempt-a", "server_id": "gateway"}
TOKEN = derive_callback_token("synthetic-secret", REQUEST["callback_token_id"])


def route_case(monkeypatch):
    from app.routes import runtime_callbacks as callbacks

    state = SimpleNamespace(status="running", active=True, lease_id="lease-a", reads=[], depth=0, on_read=None,
                            subject=dict(BINDING), owner_token=REQUEST["callback_token_id"])

    class Transaction:
        async def __aenter__(self):
            state.depth += 1
            return object()

        async def __aexit__(self, *_args):
            state.depth -= 1

    async def run_identity(_conn, *, run_id, for_update=False):
        return {"id": run_id, "tenant_id": "tenant-a", "user_id": "user-a", "status": state.status,
                "workspace_id": "workspace-a", "session_id": "session-a", "agent_id": "agent-a"}

    async def current_leases(_conn, *, tenant_id, run_id, attempt_id):
        if not state.active or tenant_id != "tenant-a" or run_id != "run-a" or attempt_id != "attempt-a":
            return []
        return [{"id": state.lease_id, "attempt_id": "attempt-a", "lease_payload_json": {
            "attempt_id": "attempt-a", "callback_token_id": state.owner_token,
            "mcp_credential_subjects": {"gateway": state.subject},
        }}]

    async def credential(principal):
        assert state.depth == 0, "credential renewal must not hold database locks"
        state.reads.append(vars(principal))
        if state.on_read:
            state.on_read()
        return f"synthetic.jwt-{len(state.reads)}"

    monkeypatch.setattr(callbacks, "get_settings", lambda: SimpleNamespace(sandbox_callback_token="synthetic-secret"))
    monkeypatch.setattr(callbacks, "transaction", Transaction)
    monkeypatch.setattr(runs_postgres, "get_run_identity", run_identity)
    monkeypatch.setattr(leases_postgres, "list_current_sandbox_runtime_leases_for_attempt", current_leases)
    monkeypatch.setattr(mcp_api, "read_mcp_principal_jwt", credential)
    return state, TestClient(create_app())


def post(client, **changes):
    return client.post(PATH, headers={"X-AI-Platform-Callback-Token": TOKEN}, json={**REQUEST, **changes})


def test_mcp_credential_callback_reads_only_the_lease_bound_subject_and_never_caches(monkeypatch):
    state, client = route_case(monkeypatch)
    first, second = post(client), post(client)
    assert first.status_code == second.status_code == 200
    assert first.json() == {"jwt": "synthetic.jwt-1"}
    assert second.json() == {"jwt": "synthetic.jwt-2"}
    assert first.headers["cache-control"] == "private, no-store"
    assert first.headers["pragma"] == "no-cache"
    assert state.reads == [BINDING, BINDING]


@pytest.mark.parametrize("case,expected", [
    ("wrong_token", 401), ("unknown_server", 403), ("terminal", 409),
    ("inactive", 409), ("old_attempt", 409), ("old_owner", 409),
    ("other_user", 403), ("missing_grant", 403),
])
def test_mcp_credential_callback_rejects_unowned_requests_before_reading_credentials(monkeypatch, case, expected):
    state, client = route_case(monkeypatch)
    changes = {}
    if case == "wrong_token":
        response = client.post(PATH, headers={"X-AI-Platform-Callback-Token": "wrong"}, json=REQUEST)
    else:
        if case == "unknown_server":
            changes["server_id"] = "unselected"
        elif case == "terminal":
            state.status = "succeeded"
        elif case == "inactive":
            state.active = False
        elif case == "old_attempt":
            changes.update(attempt_id="attempt-old", callback_token_id="cbt:run-a:attempt-old")
        elif case == "old_owner":
            state.owner_token = "cbt:run-a:attempt-a:2"
        elif case == "other_user":
            state.subject["user_id"] = "user-b"
        elif case == "missing_grant":
            state.subject = {"tenant_id": "tenant-a", "user_id": "user-a"}
        if case == "old_attempt":
            response = client.post(PATH, headers={"X-AI-Platform-Callback-Token": derive_callback_token(
                "synthetic-secret", changes["callback_token_id"],
            )}, json={**REQUEST, **changes})
        else:
            response = post(client, **changes)
    assert response.status_code == expected
    assert state.reads == []
    assert "jwt" not in response.json()


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "grant_id", "subject_epoch", "legacy_jwt_id"])
def test_mcp_credential_callback_does_not_accept_caller_credential_bindings(monkeypatch, field):
    state, client = route_case(monkeypatch)
    assert post(client, **{field: "forged"}).status_code == 422
    assert state.reads == []


@pytest.mark.parametrize("change", ["cancel", "release", "replace_lease", "replace_grant", "replace_owner"])
def test_mcp_credential_callback_rechecks_ownership_after_credential_refresh(monkeypatch, change):
    state, client = route_case(monkeypatch)

    def during_refresh():
        if change == "cancel":
            state.status = "cancelled"
        elif change == "release":
            state.active = False
        elif change == "replace_lease":
            state.lease_id = "lease-b"
        elif change == "replace_grant":
            state.subject["grant_id"] = "b" * 64
        else:
            state.owner_token = "cbt:run-a:attempt-a:2"

    state.on_read = during_refresh
    response = post(client)
    assert response.status_code == 409
    assert "jwt" not in response.json()
    assert state.reads == [BINDING]


def test_mcp_credential_lease_projection_excludes_connection_material_and_rejects_cross_user_binding():
    from tests.test_claude_mcp_registration import subject

    item = subject()
    item["mcp_server_config"]["credential_subject"] = dict(BINDING)
    projected = mcp_api.mcp_credential_subjects_for_lease([item], tenant_id="tenant-a", user_id="user-a")
    assert projected == {"gateway": BINDING}
    assert "url" not in str(projected) and "headers" not in str(projected) and "synthetic.jwt" not in str(projected)
    with pytest.raises(mcp_api.McpRuntimeContextError, match="mcp_credential_binding_invalid"):
        mcp_api.mcp_credential_subjects_for_lease([item], tenant_id="tenant-a", user_id="user-b")
    item["active"] = False
    assert mcp_api.mcp_credential_subjects_for_lease([item], tenant_id="tenant-a", user_id="user-a") == {}
    legacy = {"tenant_id": "tenant-a", "user_id": "user-a", "legacy_jwt_id": "c" * 64}
    assert bound_mcp_credential_subject(legacy, tenant_id="tenant-a", user_id="user-a") == legacy


@pytest.mark.asyncio
async def test_mcp_credential_callback_clients_are_attempt_scoped_and_refresh_each_read(monkeypatch):
    requests = []

    async def handle(request):
        body = json.loads(request.content)
        requests.append((body, request.headers["X-AI-Platform-Callback-Token"]))
        await asyncio.sleep(0)
        return httpx.Response(200, json={"jwt": body["attempt_id"] + ".jwt"})

    monkeypatch.setattr(credential_callback, "AsyncClient", lambda **kwargs: httpx.AsyncClient(
        **kwargs, transport=httpx.MockTransport(handle),
    ))
    readers = [credential_callback.McpCredentialCallbackClient(
        callback_url=build_trusted_callback_target("http://platform.internal:8020").mcp_credential_url,
        callback_token="token-" + value, callback_token_id="cbt:run:" + value,
        run_id="run", attempt_id=value,
    ) for value in ("attempt-a", "attempt-b")]
    assert await asyncio.gather(*(reader.get("gateway") for reader in readers)) == ["attempt-a.jwt", "attempt-b.jwt"]
    assert await readers[0].get("gateway") == "attempt-a.jwt"
    assert len(requests) == 3
    assert all(token == "token-" + body["attempt_id"] for body, token in requests)
    assert all(set(body) == set(REQUEST) for body, _token in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_status", [None, 401, 503])
async def test_executor_entry_loads_selected_mcp_through_bound_platform_callback(monkeypatch, tmp_path, credential_status):
    from app.bootstrap.claude_mcp import prepare_claude_mcp
    from app.mcp.infrastructure import runtime
    from app.runtime.sandbox import executor_app
    from app.runtime.sandbox.contracts import ExecutorTaskRequest
    from tests.test_claude_mcp_registration import allow_local_peer, subject
    from tests.support.mcp_protocol_peers import local_mcp_peers
    from tests.test_sandbox_executor_app import callback_ack, sdk_result, task_payload
    from tests.test_claude_mcp_registration import connected_client

    state, platform = route_case(monkeypatch)
    if credential_status is not None:
        def unavailable():
            raise mcp_api.McpRuntimeContextError("mcp_principal_jwt_unavailable", status_code=credential_status)
        state.on_read = unavailable
    callback_requests = []

    async def callback(request):
        body = json.loads(request.content)
        callback_requests.append(body)
        assert request.url.path == PATH
        response = await asyncio.to_thread(platform.post, PATH, json=body, headers=dict(request.headers))
        return httpx.Response(response.status_code, json=response.json(), headers={"Content-Type": "application/json"})

    monkeypatch.setattr(credential_callback, "AsyncClient", lambda **kwargs: httpx.AsyncClient(
        **kwargs, transport=httpx.MockTransport(callback),
    ))
    monkeypatch.setattr(runtime, "get_mcp_principal_jwt_store", lambda: (_ for _ in ()).throw(
        AssertionError("executor Redis access is forbidden"),
    ))
    monkeypatch.setattr(executor_app, "get_settings", lambda: SimpleNamespace(claude_agent_sdk_enabled=True))
    monkeypatch.setattr(executor_app, "build_claude_session_store", lambda **_kwargs: None)
    listed_names = []

    async def sdk(**kwargs):
        item = kwargs["tool_policy_subjects"][0]
        registration = prepare_claude_mcp(
            {item["identity"]: item}, {"gateway": item["mcp_server_config"]},
            credential_resolver=kwargs["mcp_credential_resolver"],
        )
        options = SimpleNamespace()
        async with registration.activate(options):
            if credential_status is None:
                assert registration.unavailable == {}
                server = options.mcp_servers[registration.server_aliases["gateway"]]["instance"]
                async with connected_client(server) as session:
                    listed = await session.list_tools()
                    listed_names.extend(tool.name for tool in listed.tools)
            else:
                assert options.mcp_servers == {}
                assert registration.unavailable == {"gateway": "authorization_required" if credential_status == 401 else "transport_failure"}
        return sdk_result("Synthetic MCP loaded")

    monkeypatch.setattr(executor_app, "run_claude_agent_sdk", sdk)
    with local_mcp_peers() as peer:
        allow_local_peer(monkeypatch, peer.url)
        item = subject(url=peer.url + "/mcp")
        item["mcp_server_config"]["credential_subject"] = dict(BINDING)
        payload = task_payload()
        payload.update(attempt_id="attempt-a", callback_token_id=REQUEST["callback_token_id"], callback_token=TOKEN)
        payload["config"]["context_manifest"]["queue_attempt_id"] = "attempt-a"
        payload["config"]["tool_policy_subjects"] = [item]

        async def emit(_event):
            return None

        result = await executor_app._default_executor_runner(
            ExecutorTaskRequest.model_validate(payload), tmp_path, emit,
            callback_sender=lambda _url, body, _token: callback_ack(body),
        )
        assert result["status"] == "completed"
        assert all(value == BINDING for value in state.reads)
        assert all(value == REQUEST for value in callback_requests)
        if credential_status is None:
            assert len(listed_names) == 1 and len(state.reads) >= 3
            assert all(dict((key.lower(), value) for key, value in headers.items())["jwt-authorization"].startswith("Bearer synthetic.jwt-")
                       for _method, headers in peer.requests)
        else:
            assert listed_names == [] and peer.requests == []
            assert len(state.reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [
    ("redirect", 503), ("unauthorized", 401), ("revoked", 401), ("unavailable", 503),
    ("oversize", 503), ("extra_fields", 503), ("not_json", 503),
])
async def test_mcp_credential_callback_client_rejects_unsafe_responses_without_retry(monkeypatch, case, expected):
    calls = []

    async def handle(request):
        calls.append(request)
        if case == "redirect":
            return httpx.Response(307, headers={"Location": "http://elsewhere.invalid/private"})
        if case in {"unauthorized", "revoked", "unavailable"}:
            return httpx.Response({"unauthorized": 401, "revoked": 409, "unavailable": 503}[case], text="PRIVATE_ERROR")
        if case == "oversize":
            return httpx.Response(200, json={"jwt": "x" * (32 * 1024)})
        if case == "extra_fields":
            return httpx.Response(200, json={"jwt": "private.jwt", "other": "PRIVATE_ERROR"})
        return httpx.Response(200, text="PRIVATE_ERROR")

    monkeypatch.setattr(credential_callback, "AsyncClient", lambda **kwargs: httpx.AsyncClient(
        **kwargs, transport=httpx.MockTransport(handle),
    ))
    reader = credential_callback.McpCredentialCallbackClient(
        callback_url="http://platform.internal:8020" + PATH, callback_token=TOKEN,
        callback_token_id=REQUEST["callback_token_id"], run_id="run-a", attempt_id="attempt-a",
    )
    with pytest.raises(mcp_api.McpRuntimeContextError) as error:
        await reader.get("gateway")
    assert error.value.status_code == expected
    assert "PRIVATE_ERROR" not in str(error.value)
    assert len(calls) == 1
