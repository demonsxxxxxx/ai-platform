from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import socket
import threading
import urllib.parse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.runtime.sandbox.opensandbox_attestation import _OpenSandboxAttestor, _TransportResponse
from services.opensandbox_gateway.adapters import (
    BrokerPolicy,
    InMemoryLifecycleTransport,
    InMemoryRuntimeAdapter,
    InMemoryStateStore,
    SQLiteStateStore,
    MailboxBroker,
)
from services.opensandbox_gateway.gateway import (
    API_KEY_HEADER,
    CAPABILITY_VERSION,
    CONTRACT_VERSION,
    ROUTE_HEADER,
    GatewayApplication,
    GatewayConfig,
    GatewayError,
    Request,
    Response,
    RuntimeEvidence,
)
from services.opensandbox_gateway.helper import _dispatch as helper_dispatch
from services.opensandbox_gateway.server import _BoundedThreadingHTTPServer, _GatewayHandler


IMAGE = "registry.example/executor@sha256:" + "1" * 64
API_KEY = "lifecycle-" + "a" * 32
CAPABILITY_TOKEN = "capability-" + "b" * 32
PUBLIC_AUTHORITY = "10.56.1.72:8443"


def gateway_config() -> GatewayConfig:
    return GatewayConfig(
        lifecycle_api_key=API_KEY,
        capability_bearer_token=CAPABILITY_TOKEN,
        record_signing_key=b"record-key-" + b"c" * 40,
        proof_key_id="s72-proof-key-v1",
        profile_id="s72-runsc-none-v1",
        public_authority=PUBLIC_AUTHORITY,
        lifecycle_endpoint="http://127.0.0.1:8080",
        executor_image=IMAGE,
        runtime_subject="s72/runsc/release-20260706.0",
        gateway_policy_subject="s72/gateway/strict-egress-v1",
        callback_boundary_subject="ai-platform/callbacks/v1",
        deny_audit_subject="s72/gateway/deny-audit-v1",
        deny_counter_subject="s72/gateway/deny-counter-v1",
        callback_upstream_base="https://api.internal.example",
        openai_upstream_base="https://models.internal.example/openai/v1",
        anthropic_upstream_base="https://models.internal.example/anthropic/v1",
    )


def create_payload(config: GatewayConfig, suffix: str = "one", workspace: str | None = None) -> dict[str, object]:
    scope = {
        "tenant_id": f"tenant-{suffix}",
        "workspace_id": f"workspace-{suffix}",
        "user_id": f"user-{suffix}",
        "session_id": f"session-{suffix}",
        "run_id": f"run-{suffix}",
        "attempt_id": f"attempt-{suffix}",
    }
    expires = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    metadata = {
        "ai-platform.owner": "sandbox-runtime",
        **{f"ai-platform.{key}": value for key, value in scope.items()},
        "ai-platform.sandbox_mode": "agent",
        "ai-platform.browser_enabled": "false",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.executor.requested_image": IMAGE,
        "ai-platform.executor.requested_image_digest": IMAGE.rsplit("@", 1)[1],
        "ai-platform.executor.user": "1000:1000",
        "ai-platform.executor.uid": "1000",
        "ai-platform.executor.gid": "1000",
        "ai-platform.executor.identity_evidence": "authenticated-runtime-endpoint",
        "ai-platform.external_egress.profile_version": "v1",
        "ai-platform.external_egress.profile_id": config.profile_id,
        "ai-platform.external_egress.endpoint_sha256": hashlib.sha256(f"https://{PUBLIC_AUTHORITY}".encode()).hexdigest(),
        "ai-platform.external_egress.runtime_identity": "runsc",
        "ai-platform.runtime_subject": config.runtime_subject,
        "ai-platform.external_egress.gateway_policy_subject": config.gateway_policy_subject,
        "ai-platform.external_egress.callback_boundary_subject": config.callback_boundary_subject,
        "ai-platform.external_egress.deny_audit_subject": config.deny_audit_subject,
        "ai-platform.external_egress.deny_counter_subject": config.deny_counter_subject,
        "ai-platform.external_egress.profile_requested_image": IMAGE,
        "ai-platform.external_egress.profile_requested_image_digest": IMAGE.rsplit("@", 1)[1],
        "ai-platform.external_egress.profile_expires_at": expires,
        "ai-platform.skill_mount.required": "false",
        "ai-platform.skill_mount.fingerprint": "",
    }
    env = {
        "AI_PLATFORM_SESSION_ID": scope["session_id"],
        "AI_PLATFORM_RUN_ID": scope["run_id"],
        "AI_PLATFORM_ATTEMPT_ID": scope["attempt_id"],
        "AI_PLATFORM_CALLBACK_BASE_URL": config.callback_upstream_base,
        "SANDBOX_CALLBACK_BASE_URL": config.callback_upstream_base,
        "AI_PLATFORM_EXECUTOR_AUTH_TOKEN": "executor-" + "d" * 32,
        "OPENAI_BASE_URL": config.openai_upstream_base,
        "OPENAI_API_KEY": "test-openai-secret",
        "ANTHROPIC_BASE_URL": config.anthropic_upstream_base,
        "ANTHROPIC_AUTH_TOKEN": "test-anthropic-secret",
    }
    return {
        "image": {"image": IMAGE},
        "timeout": 1800,
        "entrypoint": list(config.executor_entrypoint),
        "env": env,
        "metadata": metadata,
        "resourceLimits": {"cpu": "1", "memory": "512Mi", "pids": "128"},
        "secureAccess": False,
        "volumes": [
            {
                "name": "workspace",
                "mountPath": "/workspace",
                "readOnly": False,
                "host": {"path": workspace or f"/data/opensandbox/workspaces/{suffix}"},
            }
        ],
    }


def application() -> tuple[GatewayApplication, InMemoryLifecycleTransport, InMemoryRuntimeAdapter, InMemoryStateStore]:
    config = gateway_config()
    lifecycle = InMemoryLifecycleTransport()
    runtime = InMemoryRuntimeAdapter()
    store = InMemoryStateStore()
    return GatewayApplication(config, lifecycle, runtime, store), lifecycle, runtime, store


def call(app: GatewayApplication, method: str, target: str, body: object | bytes = b"", headers: dict[str, str] | None = None) -> Response:
    if not isinstance(body, bytes):
        body = json.dumps(body, separators=(",", ":")).encode()
    values = {API_KEY_HEADER: API_KEY}
    values.update(headers or {})
    return app.handle(Request(method, target, values, body))


def decoded(response: Response) -> dict[str, object]:
    return json.loads(response.body)


def multipart_lease_upload(record, boundary: str = "lease-boundary") -> bytes:
    metadata = json.dumps(
        {
            "path": "/workspace/.ai-platform-opensandbox-lease.json",
            "owner": "1000",
            "group": "1000",
            "mode": "0600",
        },
        separators=(",", ":"),
    )
    content = json.dumps(
        {"schema_version": "ai-platform.opensandbox-lease.v1", **record.scope},
        separators=(",", ":"),
    )
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\nContent-Type: application/json\r\n\r\n{metadata}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\".ai-platform-opensandbox-lease.json\"\r\nContent-Type: application/json\r\n\r\n{content}\r\n"
        f"--{boundary}--\r\n"
    ).encode()


def test_create_rewrites_only_broker_bases_and_returns_exact_attestation() -> None:
    app, lifecycle, runtime, _ = application()
    config = gateway_config()
    response = call(app, "POST", "/v1/sandboxes", create_payload(config))
    assert response.status == 201
    sandbox_id = decoded(response)["id"]
    sent = json.loads(lifecycle.requests[0][2])
    assert sent["env"]["AI_PLATFORM_CALLBACK_BASE_URL"] == "http://127.0.0.1:18888"
    assert sent["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:18888/model/openai"
    assert "test-openai-secret" not in repr(runtime.evidence)
    attestation = call(app, "GET", f"/v1/sandboxes/{sandbox_id}/attestation")
    assert attestation.status == 200
    value = decoded(attestation)
    assert value == {
        "contract_version": CONTRACT_VERSION,
        "provider": "opensandbox",
        "sandbox_id": sandbox_id,
        "scope_labels": {
            "tenant_id": "tenant-one",
            "workspace_id": "workspace-one",
            "user_id": "user-one",
            "session_id": "session-one",
            "run_id": "run-one",
            "attempt_id": "attempt-one",
            "lease_id": f"opensandbox:opensandbox-run-one-attempt-one:{sandbox_id}",
        },
        "runtime": {"identity": "runsc", "subject": config.runtime_subject},
        "network": {"mode": "none", "default_deny": True},
        "security": {"no_new_privileges": True},
        "image": {"subject": IMAGE, "digest": IMAGE.rsplit("@", 1)[1]},
        "host_path_policy": {"subject": "scoped-workspace-only", "unscoped_host_paths_allowed": False},
        "subjects": {
            "gateway_policy": config.gateway_policy_subject,
            "callback_boundary": config.callback_boundary_subject,
            "capability": config.profile_id,
            "deny_audit": config.deny_audit_subject,
            "deny_counter": config.deny_counter_subject,
        },
        "signed_profile": {"id": config.profile_id, "version": "v1", "proof_key_id": config.proof_key_id},
    }


def test_payload_is_accepted_by_merged_ai_platform_attestor() -> None:
    app, _, _, _ = application()
    config = gateway_config()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(config)))["id"]
    body = call(app, "GET", f"/v1/sandboxes/{sandbox_id}/attestation").body
    endpoint = f"https://{PUBLIC_AUTHORITY}/v1/sandboxes/{sandbox_id}/attestation"
    attestor = _OpenSandboxAttestor(
        base_url=f"https://{PUBLIC_AUTHORITY}",
        api_key=API_KEY,
        path_template="/v1/sandboxes/{sandbox_id}/attestation",
        contract_version=CONTRACT_VERSION,
        timeout_seconds=1,
        runtime_subject=config.runtime_subject,
        gateway_policy_subject=config.gateway_policy_subject,
        callback_boundary_subject=config.callback_boundary_subject,
        proof_key_id=config.proof_key_id,
        transport=lambda *_: _TransportResponse(200, endpoint, body),
    )
    capability = SimpleNamespace(
        runtime_identity="runsc",
        runtime_subject=config.runtime_subject,
        gateway_policy_subject=config.gateway_policy_subject,
        callback_boundary_subject=config.callback_boundary_subject,
        requested_image=IMAGE,
        requested_image_digest=IMAGE.rsplit("@", 1)[1],
        profile_id=config.profile_id,
        deny_audit_subject=config.deny_audit_subject,
        deny_counter_subject=config.deny_counter_subject,
    )
    request = SimpleNamespace(
        tenant_id="tenant-one",
        workspace_id="workspace-one",
        user_id="user-one",
        session_id="session-one",
        run_id="run-one",
        attempt_id="attempt-one",
    )
    assert asyncio.run(attestor(capability, request, sandbox_id, {"id": sandbox_id})) is True


def test_auth_size_path_redirect_and_tls_fail_closed() -> None:
    app, lifecycle, _, store = application()
    config = gateway_config()
    assert app.handle(Request("GET", "/v1/sandboxes", {}, b"")).status == 401
    assert call(app, "POST", "/v1/sandboxes/%2e%2e/secret", b"").status == 400
    assert call(app, "POST", "/v1/sandboxes", b"x" * (config.max_body_bytes + 1)).status == 413
    lifecycle.redirect_path = "/v1/sandboxes"
    response = call(app, "POST", "/v1/sandboxes", create_payload(config))
    assert response.status == 502
    assert decoded(response)["error"]["code"] == "upstream_redirect_rejected"
    assert store.deny_count(config.deny_audit_subject) >= 4
    with pytest.raises(ValueError, match="HTTPS"):
        replace(config, callback_upstream_base="http://api.internal.example/callback").validate()


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: value.update(networkPolicy={"defaultAction": "allow"}), "network_policy_not_allowed"),
        (lambda value: value.update(image="registry.example/executor:latest"), "immutable_image_mismatch"),
        (lambda value: value["volumes"][0].update(host={"path": "/etc"}), "host_path_not_scoped"),
        (lambda value: value["env"].update(HTTPS_PROXY="https://proxy.example"), "proxy_environment_not_allowed"),
        (lambda value: value.update(entrypoint=["sh"]), "entrypoint_mismatch"),
    ],
)
def test_create_allowlist_negative_cases(mutator, code: str) -> None:
    app, _, _, _ = application()
    value = create_payload(gateway_config())
    mutator(value)
    response = call(app, "POST", "/v1/sandboxes", value)
    assert response.status == 400
    assert decoded(response)["error"]["code"] == code


def test_scope_reuse_workspace_conflict_and_idempotent_create() -> None:
    app, lifecycle, _, _ = application()
    config = gateway_config()
    value = create_payload(config)
    first = call(app, "POST", "/v1/sandboxes", value)
    second = call(app, "POST", "/v1/sandboxes", value)
    assert first.status == second.status == 201
    assert decoded(first) == decoded(second)
    assert sum(method == "POST" for method, _, _ in lifecycle.requests) == 1
    conflict = call(app, "POST", "/v1/sandboxes", create_payload(config, "two", "/data/opensandbox/workspaces/one"))
    assert conflict.status == 409
    assert decoded(conflict)["error"]["code"] == "workspace_scope_conflict"


def test_dispatch_revalidates_scope_and_rewrites_callback() -> None:
    app, _, runtime, _ = application()
    config = gateway_config()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(config)))["id"]
    endpoint = call(app, "GET", f"/v1/sandboxes/{sandbox_id}/endpoints/18000?use_server_proxy=true")
    token = decoded(endpoint)["headers"][ROUTE_HEADER]
    task = {
        "tenant_id": "tenant-one",
        "workspace_id": "workspace-one",
        "user_id": "user-one",
        "session_id": "session-one",
        "run_id": "run-one",
        "attempt_id": "attempt-one",
        "callback_base_url": config.callback_upstream_base,
        "callback_url": config.callback_upstream_base + "/api/ai/runtime/callbacks/executor",
    }
    response = call(
        app,
        "POST",
        f"/v1/sandboxes/{sandbox_id}/proxy/18000/v1/tasks/execute",
        task,
        {ROUTE_HEADER: token},
    )
    assert response.status == 200
    forwarded = json.loads(runtime.proxied[-1][2].body)
    assert forwarded["callback_base_url"] == "http://127.0.0.1:18888"
    assert forwarded["callback_url"] == "http://127.0.0.1:18888/api/ai/runtime/callbacks/executor"
    task["workspace_id"] = "other"
    denied = call(app, "POST", f"/v1/sandboxes/{sandbox_id}/proxy/18000/v1/tasks/execute", task, {ROUTE_HEADER: token})
    assert denied.status == 409
    assert call(app, "GET", f"/v1/sandboxes/{sandbox_id}/proxy/18000/debug", headers={ROUTE_HEADER: token}).status == 404
    assert call(app, "GET", f"/v1/sandboxes/{sandbox_id}/endpoints/18000?use_server_proxy=false").status == 400


def test_signature_metadata_runtime_drift_and_route_auth_are_rejected() -> None:
    app, lifecycle, runtime, store = application()
    config = gateway_config()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(config)))["id"]
    assert call(app, "GET", f"/v1/sandboxes/{sandbox_id}/proxy/18000/health", headers={ROUTE_HEADER: "wrong"}).status == 401
    record = store.get(sandbox_id)
    assert record is not None
    record.signature = "0" * 64
    assert call(app, "GET", f"/v1/sandboxes/{sandbox_id}/attestation").status == 409
    record.signature = app._sign_record(record)
    lifecycle.sandboxes[sandbox_id]["metadata"]["ai-platform.user_id"] = "other"
    assert call(app, "GET", f"/v1/sandboxes/{sandbox_id}/attestation").status == 409
    lifecycle.sandboxes[sandbox_id]["metadata"] = dict(record.metadata)
    old = runtime.evidence[sandbox_id]
    runtime.evidence[sandbox_id] = RuntimeEvidence(**{**old.__dict__, "network_mode": "bridge"})
    assert call(app, "GET", f"/v1/sandboxes/{sandbox_id}/attestation").status == 409


def test_live_root_user_is_rejected_before_attestation() -> None:
    app, _, runtime, _ = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    old = runtime.evidence[sandbox_id]
    runtime.evidence[sandbox_id] = RuntimeEvidence(**{**old.__dict__, "user": "0:0"})
    response = call(app, "GET", f"/v1/sandboxes/{sandbox_id}/attestation")
    assert response.status == 409
    assert decoded(response)["error"]["code"] == "runtime_attestation_drift"


def test_list_requires_scope_and_cancel_delete_are_idempotent() -> None:
    app, lifecycle, runtime, _ = application()
    config = gateway_config()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(config)))["id"]
    assert call(app, "GET", "/v1/sandboxes").status == 400
    metadata = "ai-platform.owner=sandbox-runtime&ai-platform.tenant_id=tenant-one"
    query = urllib.parse.urlencode((("metadata", metadata), ("page", "1"), ("pageSize", "20")))
    listed = call(app, "GET", f"/v1/sandboxes?{query}")
    assert decoded(listed)["items"][0]["id"] == sandbox_id
    assert call(app, "POST", f"/v1/sandboxes/{sandbox_id}/cancel").status == 204
    assert sandbox_id not in runtime.relays
    assert call(app, "DELETE", f"/v1/sandboxes/{sandbox_id}").status == 204
    assert sum(method == "DELETE" for method, _, _ in lifecycle.requests) == 1


def test_capability_auth_schema_signature_and_broker_policy() -> None:
    app, _, _, _ = application()
    assert app.handle(Request("GET", "/v1/capabilities/external-egress", {}, b"")).status == 401
    response = app.handle(Request("GET", "/v1/capabilities/external-egress", {"Authorization": f"Bearer {CAPABILITY_TOKEN}"}, b""))
    value = decoded(response)
    assert value["schema_version"] == CAPABILITY_VERSION
    assert value["opensandbox_endpoint"] == f"https://{PUBLIC_AUTHORITY}"
    assert len(value["profile_signature"]) == 64
    with pytest.raises(ValueError, match="pinned HTTPS"):
        BrokerPolicy({"version": 1, "targets": {name: {"base_url": "http://example", "expected_ips": []} for name in ("callback", "openai", "anthropic")}})


def test_sqlite_store_persists_only_sealed_non_secret_record(tmp_path) -> None:
    path = tmp_path / "state.sqlite3"
    config = gateway_config()
    lifecycle = InMemoryLifecycleTransport()
    runtime = InMemoryRuntimeAdapter()
    store = SQLiteStateStore(str(path))
    app = GatewayApplication(config, lifecycle, runtime, store)
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(config)))["id"]
    reopened = SQLiteStateStore(str(path)).get(sandbox_id)
    assert reopened is not None and len(reopened.signature) == 64
    raw = path.read_bytes()
    assert b"test-openai-secret" not in raw
    assert b"test-anthropic-secret" not in raw


def test_sqlite_workspace_reservation_is_cross_tenant_atomic_and_restart_reconciles(tmp_path) -> None:
    config = gateway_config()
    lifecycle = InMemoryLifecycleTransport()
    runtime = InMemoryRuntimeAdapter()
    state_path = tmp_path / "atomic.sqlite3"
    first = GatewayApplication(config, lifecycle, runtime, SQLiteStateStore(str(state_path)))
    second = GatewayApplication(config, lifecycle, runtime, SQLiteStateStore(str(state_path)))
    workspace = "/data/opensandbox/workspaces/shared"
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda item: call(item[0], "POST", "/v1/sandboxes", create_payload(config, item[1], workspace)),
                ((first, "one"), (second, "two")),
            )
        )
    assert sorted(response.status for response in responses) == [201, 409]
    assert sum(method == "POST" for method, _, _ in lifecycle.requests) == 1

    class CrashAfterCreate(InMemoryLifecycleTransport):
        crash_once = True

        def request(self, method: str, path: str, body: bytes = b"") -> Response:
            response = super().request(method, path, body)
            if method == "POST" and self.crash_once:
                self.crash_once = False
                raise RuntimeError("simulated post-create crash")
            return response

    crash_lifecycle = CrashAfterCreate()
    crash_path = tmp_path / "reconcile.sqlite3"
    crashed_store = SQLiteStateStore(str(crash_path))
    crashed = GatewayApplication(config, crash_lifecycle, InMemoryRuntimeAdapter(), crashed_store)
    assert call(crashed, "POST", "/v1/sandboxes", create_payload(config, "crash")).status == 500
    assert len(crashed_store.list({"state": "intent"})) == 1
    recovered_runtime = InMemoryRuntimeAdapter()
    recovered_store = SQLiteStateStore(str(crash_path))
    GatewayApplication(config, crash_lifecycle, recovered_runtime, recovered_store)
    active = recovered_store.list({"state": "active"})
    assert len(active) == len(crash_lifecycle.sandboxes) == 1
    assert active[0].sandbox_id in recovered_runtime.relays


def test_http_transport_rejects_te_ambiguous_length_and_enforces_handler_bound() -> None:
    app, _, _, _ = application()

    class Handler(_GatewayHandler):
        body_limit = gateway_config().max_body_bytes

    Handler.app = app
    server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, 2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def exchange(raw: bytes) -> bytes:
        connection = socket.create_connection(server.server_address, timeout=2)
        connection.sendall(raw)
        connection.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := connection.recv(4096):
            chunks.append(chunk)
        connection.close()
        return b"".join(chunks)

    try:
        te = exchange(b"POST /v1/sandboxes HTTP/1.1\r\nHost: 127.0.0.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n")
        missing = exchange(b"POST /v1/sandboxes HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        duplicate = exchange(b"POST /v1/sandboxes HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert b"400 Bad Request" in te and b"transfer_encoding_rejected" in te
    assert b"411 Length Required" in missing and b"content_length_required" in missing
    assert b"400 Bad Request" in duplicate and b"ambiguous_header" in duplicate

    bounded = _BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, 1)
    assert bounded._handler_slots.acquire(blocking=False)
    server_socket, client_socket = socket.socketpair()
    bounded.process_request(server_socket, ("127.0.0.1", 1))
    response = b""
    while chunk := client_socket.recv(4096):
        response += chunk
    client_socket.close()
    bounded.server_close()
    head, body = response.split(b"\r\n\r\n", 1)
    assert b"503 Service Unavailable" in head
    assert f"Content-Length: {len(body)}".encode() in head


def test_real_sdk_list_query_and_response_contract() -> None:
    from opensandbox.api.lifecycle.api.sandboxes import get_sandboxes
    from opensandbox.api.lifecycle.models.list_sandboxes_response import ListSandboxesResponse

    app, lifecycle, _, _ = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    lifecycle.sandboxes[sandbox_id].update(
        {
            "status": {"state": "Running"},
            "entrypoint": list(gateway_config().executor_entrypoint),
            "createdAt": "2026-07-24T00:00:00Z",
        }
    )
    metadata = "ai-platform.owner=sandbox-runtime&ai-platform.tenant_id=tenant-one"
    kwargs = get_sandboxes._get_kwargs(metadata=metadata, page=1, page_size=20)
    assert list(kwargs["params"]) == ["metadata", "page", "pageSize"]
    response = call(app, "GET", "/v1/sandboxes?" + urllib.parse.urlencode(kwargs["params"]))
    parsed = ListSandboxesResponse.from_dict(decoded(response))
    assert parsed.items[0].id == sandbox_id
    assert parsed.pagination.to_dict() == {
        "page": 1,
        "pageSize": 20,
        "totalItems": 1,
        "totalPages": 1,
        "hasNextPage": False,
    }


def test_literal_private_ip_and_workspace_file_proxy_contracts() -> None:
    with pytest.raises(ValueError, match="approved literal IP"):
        replace(gateway_config(), public_authority="sandbox-gateway.example:8443").validate()
    app, _, runtime, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    token = decoded(call(app, "GET", f"/v1/sandboxes/{sandbox_id}/endpoints/44772?use_server_proxy=true"))["headers"][ROUTE_HEADER]
    record = store.get(sandbox_id)
    assert record is not None
    upload = call(
        app,
        "POST",
        f"/v1/sandboxes/{sandbox_id}/proxy/44772/files/upload",
        multipart_lease_upload(record),
        {ROUTE_HEADER: token, "Content-Type": "multipart/form-data; boundary=lease-boundary"},
    )
    assert upload.status == 200
    download_query = urllib.parse.urlencode({"path": "/workspace/.ai-platform-opensandbox-lease.json"})
    assert call(
        app,
        "GET",
        f"/v1/sandboxes/{sandbox_id}/proxy/44772/files/download?{download_query}",
        headers={ROUTE_HEADER: token},
    ).status == 200
    traversal = urllib.parse.urlencode({"path": "../outside"})
    denied = call(
        app,
        "GET",
        f"/v1/sandboxes/{sandbox_id}/proxy/44772/files/download?{traversal}",
        headers={ROUTE_HEADER: token},
    )
    assert denied.status == 400
    assert len(runtime.proxied) == 2
    mailbox_source = inspect.getsource(MailboxBroker)
    assert "O_NOFOLLOW" in mailbox_source and "dir_fd=" in mailbox_source and "os.replace" in mailbox_source


def test_privileged_helper_is_narrow_and_public_unit_has_no_docker_access() -> None:
    app, _, runtime, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    request = {"version": 1, "operation": "stop_relay", "record": record.__dict__, "arguments": {}}
    assert helper_dispatch(request, gateway_config().record_signing_key, runtime) == {}
    with pytest.raises(GatewayError, match="helper_operation_not_allowed"):
        helper_dispatch({**request, "operation": "shell"}, gateway_config().record_signing_key, runtime)
    tampered = {**record.__dict__, "signature": "0" * 64}
    with pytest.raises(GatewayError, match="helper_record_invalid"):
        helper_dispatch({**request, "record": tampered}, gateway_config().record_signing_key, runtime)

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    public_unit = (root / "deploy/opensandbox/opensandbox-gateway.service").read_text(encoding="utf-8")
    helper_unit = (root / "deploy/opensandbox/opensandbox-gateway-helper.service").read_text(encoding="utf-8")
    install = (root / "deploy/opensandbox/install-s72.sh").read_text(encoding="utf-8")
    rollback = (root / "deploy/opensandbox/rollback-s72.sh").read_text(encoding="utf-8")
    assert "docker.sock" not in public_unit and "SupplementaryGroups=docker" not in public_unit
    assert "docker.sock" in helper_unit and "SupplementaryGroups=docker" in helper_unit
    assert all(marker in install for marker in ("releases", "current", "previous", "SOURCE_COMMIT", "systemctl restart"))
    assert all(marker in rollback for marker in ("previous", "current", "SOURCE_COMMIT", "systemctl restart"))
