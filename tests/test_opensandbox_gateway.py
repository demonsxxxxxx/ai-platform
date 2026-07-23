from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import os
import pathlib
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import textwrap
import threading
import time
import urllib.parse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.runtime.sandbox.opensandbox_attestation import _OpenSandboxAttestor, _TransportResponse
import services.opensandbox_gateway.adapters as gateway_adapters
import services.opensandbox_gateway.helper as gateway_helper
from services.opensandbox_gateway.adapters import (
    BrokerPolicy,
    DockerRuntimeAdapter,
    HelperRuntimeAdapter,
    InMemoryLifecycleTransport,
    InMemoryRuntimeAdapter,
    InMemoryStateStore,
    SQLiteStateStore,
    LoopbackLifecycleTransport,
    MailboxBroker,
)
from services.opensandbox_gateway.gateway import (
    API_KEY_HEADER,
    CAPABILITY_VERSION,
    CONTRACT_VERSION,
    ROUTE_HEADER,
    DeadlineExceeded,
    GatewayApplication,
    GatewayConfig,
    GatewayError,
    MonotonicDeadline,
    Request,
    Response,
    RuntimeEvidence,
    operation_deadline,
)
from services.opensandbox_gateway.helper import _dispatch as helper_dispatch
from services.opensandbox_gateway.relay import RELAY_SOURCE
from services.opensandbox_gateway.server import (
    _BoundedThreadingHTTPServer,
    _GatewayHandler,
    _broker_loop,
    _verify_certificate_ip_san,
)


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
                "name": "ai-platform-workspace",
                "mountPath": "/workspace",
                "readOnly": False,
                "host": {
                    "path": workspace
                    or (
                        f"/data/opensandbox/workspaces/tenants/{scope['tenant_id']}"
                        f"/workspaces/{scope['workspace_id']}/users/{scope['user_id']}"
                        f"/sessions/{scope['session_id']}/runs/{scope['run_id']}/workspace"
                    )
                },
            }
        ],
    }


def application() -> tuple[GatewayApplication, InMemoryLifecycleTransport, InMemoryRuntimeAdapter, InMemoryStateStore]:
    config = gateway_config()
    lifecycle = InMemoryLifecycleTransport()
    runtime = InMemoryRuntimeAdapter()
    store = InMemoryStateStore()
    return GatewayApplication(config, lifecycle, runtime, store), lifecycle, runtime, store


def active_mailbox_store(records, denials):
    return SimpleNamespace(
        list=lambda _filters: records,
        record_deny=lambda subject, code: denials.append((subject, code)),
        begin_mailbox_claim=lambda _sandbox_id: "mailbox-fence-token",
        confirm_mailbox_outbound=lambda _sandbox_id, _token: True,
        end_mailbox_outbound=lambda _sandbox_id, _token: None,
    )


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
        "security": {"no_new_privileges": True, "user": "1000:1000", "uid": "1000", "gid": "1000"},
        "image": {"subject": IMAGE, "digest": IMAGE.rsplit("@", 1)[1]},
        "host_path_policy": {"subject": "scoped-workspace-only", "unscoped_host_paths_allowed": False},
        "subjects": {
            "gateway_policy": config.gateway_policy_subject,
            "callback_boundary": config.callback_boundary_subject,
            "capability": config.profile_id,
            "deny_audit": config.deny_audit_subject,
            "deny_counter": config.deny_counter_subject,
        },
        "signed_profile": {
            "id": config.profile_id,
            "version": "v1",
            "proof_key_id": config.proof_key_id,
            "profile_signature": value["signed_profile"]["profile_signature"],
        },
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
        proof_signing_key=config.record_signing_key.decode(),
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


def test_scope_segments_and_skill_mount_are_exactly_bound_to_canonical_workspace() -> None:
    app, lifecycle, _, _ = application()
    config = gateway_config()

    invalid_scope = create_payload(config)
    invalid_scope["metadata"]["ai-platform.tenant_id"] = "tenant/alias"
    rejected_scope = call(app, "POST", "/v1/sandboxes", invalid_scope)
    assert rejected_scope.status == 400
    assert decoded(rejected_scope)["error"]["code"] == "scope_invalid"

    exact = create_payload(config)
    workspace_path = exact["volumes"][0]["host"]["path"]
    exact["metadata"]["ai-platform.skill_mount.required"] = "true"
    exact["metadata"]["ai-platform.skill_mount.fingerprint"] = "f" * 64
    exact["volumes"].append(
        {
            "name": "ai-platform-claude-skills",
            "mountPath": "/workspace/.claude",
            "readOnly": True,
            "host": {"path": workspace_path + "/.claude"},
        }
    )
    assert call(app, "POST", "/v1/sandboxes", exact).status == 201
    sent = json.loads(next(body for method, _, body in lifecycle.requests if method == "POST"))
    assert sent["volumes"][1]["host"]["path"] == workspace_path + "/.claude"
    assert sent["volumes"][1]["mountPath"] == "/workspace/.claude"

    cross_tenant = create_payload(config, "two")
    cross_tenant["metadata"]["ai-platform.skill_mount.required"] = "true"
    cross_tenant["metadata"]["ai-platform.skill_mount.fingerprint"] = "e" * 64
    cross_tenant["volumes"].append(
        {
            "name": "ai-platform-claude-skills",
            "mountPath": "/workspace/.claude",
            "readOnly": True,
            "host": {"path": workspace_path + "/.claude"},
        }
    )
    rejected_mount = call(app, "POST", "/v1/sandboxes", cross_tenant)
    assert rejected_mount.status == 400
    assert decoded(rejected_mount)["error"]["code"] == "skill_mount_must_be_read_only"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    (
        (lambda value: value["metadata"].pop("ai-platform.skill_mount.required"), "create_metadata_invalid"),
        (
            lambda value: value["metadata"].update(
                {"ai-platform.skill_mount.required": "true", "ai-platform.skill_mount.fingerprint": ""}
            ),
            "skill_mount_fingerprint_invalid",
        ),
        (
            lambda value: value["metadata"].update(
                {"ai-platform.skill_mount.required": "true", "ai-platform.skill_mount.fingerprint": "F" * 64}
            ),
            "skill_mount_fingerprint_invalid",
        ),
    ),
)
def test_skill_mount_declaration_is_required_and_canonical(mutate, expected_code) -> None:
    app, _, _, _ = application()
    value = create_payload(gateway_config())
    mutate(value)
    response = call(app, "POST", "/v1/sandboxes", value)
    assert response.status == 400
    assert decoded(response)["error"]["code"] == expected_code


@pytest.mark.parametrize("case", ("required_false_extra", "required_missing_volume", "wrong_source", "writable"))
def test_skill_mount_declaration_and_volume_are_bidirectionally_bound(case) -> None:
    app, _, _, _ = application()
    value = create_payload(gateway_config())
    workspace_path = value["volumes"][0]["host"]["path"]
    skill_volume = {
        "name": "ai-platform-claude-skills",
        "mountPath": "/workspace/.claude",
        "readOnly": True,
        "host": {"path": workspace_path + "/.claude"},
    }
    if case != "required_false_extra":
        value["metadata"].update(
            {"ai-platform.skill_mount.required": "true", "ai-platform.skill_mount.fingerprint": "a" * 64}
        )
    if case != "required_missing_volume":
        value["volumes"].append(skill_volume)
    if case == "wrong_source":
        skill_volume["host"]["path"] = workspace_path + "/other/.claude"
    if case == "writable":
        skill_volume["readOnly"] = False

    response = call(app, "POST", "/v1/sandboxes", value)
    assert response.status == 400
    assert decoded(response)["error"]["code"] in {
        "skill_mount_declaration_mismatch",
        "skill_mount_must_be_read_only",
    }


def test_skill_mount_runtime_fingerprint_drift_fails_attestation() -> None:
    app, _, runtime, _ = application()
    value = create_payload(gateway_config())
    workspace_path = value["volumes"][0]["host"]["path"]
    value["metadata"].update(
        {"ai-platform.skill_mount.required": "true", "ai-platform.skill_mount.fingerprint": "a" * 64}
    )
    value["volumes"].append(
        {
            "name": "ai-platform-claude-skills",
            "mountPath": "/workspace/.claude",
            "readOnly": True,
            "host": {"path": workspace_path + "/.claude"},
        }
    )
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", value))["id"]
    runtime.evidence[sandbox_id] = replace(
        runtime.evidence[sandbox_id],
        skill_mount_fingerprint="b" * 64,
    )

    response = call(app, "GET", f"/v1/sandboxes/{sandbox_id}/attestation")
    assert response.status == 409
    assert decoded(response)["error"]["code"] == "runtime_attestation_drift"


def test_runtime_skill_mount_fingerprint_uses_exact_opened_source_inodes(monkeypatch) -> None:
    app, _, _, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    workspace = record.workspace_host_path
    record.metadata["ai-platform.skill_mount.required"] = "true"
    record.mounts.append(
        {"host": workspace + "/.claude", "mountPath": "/workspace/.claude", "readOnly": True}
    )
    nodes = {
        10: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=1, st_ino=2),
        11: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=3, st_ino=4),
        12: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=5, st_ino=6),
    }
    named_nodes = dict(nodes)
    monkeypatch.setattr(gateway_adapters.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(gateway_adapters, "_open_workspace_dirfd", lambda *_: 10)
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_: None)
    monkeypatch.setattr(
        gateway_adapters.os,
        "open",
        lambda name, *_args, dir_fd, **_kwargs: 11 if (name, dir_fd) == (".claude", 10) else 12,
    )
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda descriptor: nodes[descriptor])
    monkeypatch.setattr(
        gateway_adapters.os,
        "stat",
        lambda name, **kwargs: named_nodes[10 if name == workspace else 11 if name == ".claude" else 12],
    )
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _descriptor: None)
    expected = hashlib.sha256(b"1\x002\x003\x004\x005\x006").hexdigest()

    assert gateway_adapters._runtime_skill_mount_fingerprint(record, gateway_config().workspace_root) == expected
    nodes[12] = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=5, st_ino=99)
    with pytest.raises(GatewayError, match="skill_mount_runtime_drift"):
        gateway_adapters._runtime_skill_mount_fingerprint(record, gateway_config().workspace_root)


def test_scope_reuse_workspace_conflict_and_idempotent_create() -> None:
    app, lifecycle, _, _ = application()
    config = gateway_config()
    value = create_payload(config)
    first = call(app, "POST", "/v1/sandboxes", value)
    second = call(app, "POST", "/v1/sandboxes", value)
    assert first.status == second.status == 201
    assert decoded(first) == decoded(second)
    assert sum(method == "POST" for method, _, _ in lifecycle.requests) == 1
    aliased = create_payload(config, "two")
    aliased["volumes"][0]["host"]["path"] = create_payload(config, "one")["volumes"][0]["host"]["path"]
    conflict = call(app, "POST", "/v1/sandboxes", aliased)
    assert conflict.status == 400
    assert decoded(conflict)["error"]["code"] == "workspace_must_be_writable"

    changed_attempt = create_payload(config)
    changed_attempt["metadata"]["ai-platform.attempt_id"] = "attempt-two"
    changed_attempt["env"]["AI_PLATFORM_ATTEMPT_ID"] = "attempt-two"
    attempt_conflict = call(app, "POST", "/v1/sandboxes", changed_attempt)
    assert attempt_conflict.status == 409
    assert decoded(attempt_conflict)["error"]["code"] == "scope_conflict"
    assert sum(method == "POST" for method, _, _ in lifecycle.requests) == 1


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


def test_async_delete_keeps_durable_ownership_until_authoritative_not_found() -> None:
    class AsyncDelete(InMemoryLifecycleTransport):
        present_gets = 9

        def request(self, method: str, path: str, body: bytes = b"") -> Response:
            if method == "DELETE" and path.startswith("/v1/sandboxes/"):
                self.requests.append((method, path, body))
                return Response(202, {"content-length": "0"}, b"")
            if method == "GET" and path.startswith("/v1/sandboxes/") and self.present_gets > 0:
                self.requests.append((method, path, body))
                self.present_gets -= 1
                sandbox_id = path.rsplit("/", 1)[1]
                return Response.json(200, self.sandboxes[sandbox_id])
            if method == "GET" and path.startswith("/v1/sandboxes/"):
                self.sandboxes.pop(path.rsplit("/", 1)[1], None)
            return super().request(method, path, body)

    class TrackingRuntime(InMemoryRuntimeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.mailbox_cleanups: list[str] = []

        def cleanup_mailbox(self, record) -> None:
            self.mailbox_cleanups.append(record.sandbox_id)

    lifecycle = AsyncDelete()
    runtime = TrackingRuntime()
    store = InMemoryStateStore()
    app = GatewayApplication(gateway_config(), lifecycle, runtime, store)
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config(), "async-delete")))["id"]

    pending = call(app, "DELETE", f"/v1/sandboxes/{sandbox_id}")
    assert pending.status == 503
    assert decoded(pending)["error"]["code"] == "cleanup_pending"
    assert store.get(sandbox_id).state == "cleanup_pending"
    assert sandbox_id in runtime.relays
    assert runtime.mailbox_cleanups == []

    completed = call(app, "DELETE", f"/v1/sandboxes/{sandbox_id}")
    assert completed.status == 204
    assert store.get(sandbox_id).state == "deleted"
    assert sandbox_id not in runtime.relays
    assert runtime.mailbox_cleanups == [sandbox_id]
    assert call(app, "DELETE", f"/v1/sandboxes/{sandbox_id}").status == 204


def test_uncertain_delete_survives_restart_and_broker_excludes_pending(tmp_path) -> None:
    class UncertainDelete(InMemoryLifecycleTransport):
        available = False

        def request(self, method: str, path: str, body: bytes = b"") -> Response:
            if method == "DELETE" and path.startswith("/v1/sandboxes/") and not self.available:
                raise GatewayError(502, "upstream_unavailable")
            return super().request(method, path, body)

    lifecycle = UncertainDelete()
    runtime = InMemoryRuntimeAdapter()
    state_path = tmp_path / "async-delete.sqlite3"
    store = SQLiteStateStore(str(state_path))
    app = GatewayApplication(gateway_config(), lifecycle, runtime, store)
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config(), "restart-delete")))["id"]

    assert call(app, "DELETE", f"/v1/sandboxes/{sandbox_id}").status == 503
    assert store.get(sandbox_id).state == "cleanup_pending"
    assert store.begin_mailbox_claim(sandbox_id) is None
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 0.1, 1024)
    assert broker.poll_once() == 0

    lifecycle.available = True
    restarted_runtime = InMemoryRuntimeAdapter()
    restarted_runtime.relays.add(sandbox_id)
    restarted = GatewayApplication(gateway_config(), lifecycle, restarted_runtime, SQLiteStateStore(str(state_path)))
    assert restarted.store.get(sandbox_id).state == "deleted"
    assert sandbox_id not in restarted_runtime.relays


def test_claimed_mailbox_request_is_not_sent_after_delete_closes_admission(monkeypatch, tmp_path) -> None:
    store = SQLiteStateStore(str(tmp_path / "mailbox-fence.sqlite3"))
    app = GatewayApplication(gateway_config(), InMemoryLifecycleTransport(), InMemoryRuntimeAdapter(), store)
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config(), "claim-delete")))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 1.0, 1024)
    entry = ("1" * 32 + ".json", 1, 2, 10, 3)
    claimed_entry = (entry[0] + "." + "a" * 32 + ".claim", *entry[1:])
    claim_complete = threading.Event()
    permit_confirmation = threading.Event()
    broker_results = []
    delete_results = []
    sent = []

    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None

    def claim(_request_fd, _claim_fd, _entry):
        claim_complete.set()
        assert permit_confirmation.wait(2)
        return claimed_entry

    broker._claim_request = claim
    broker._process = lambda *_args: sent.append("sent") or {"status": 200, "headers": {}, "body": ""}
    broker._write_response = lambda *_args: None
    broker._unlink_request_if_identity = lambda *_args: True
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_args: None)

    broker_thread = threading.Thread(target=lambda: broker_results.append(broker._handle_entry(record, entry, False)))
    broker_thread.start()
    assert claim_complete.wait(2)
    delete_thread = threading.Thread(
        target=lambda: delete_results.append(call(app, "DELETE", f"/v1/sandboxes/{sandbox_id}"))
    )
    delete_thread.start()
    deadline = time.monotonic() + 2
    while store.get(sandbox_id).state != "cleanup_pending":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    permit_confirmation.set()
    broker_thread.join(2)
    delete_thread.join(2)

    assert not broker_thread.is_alive() and not delete_thread.is_alive()
    assert broker_results == [False]
    assert sent == []
    assert delete_results[0].status == 204
    assert store.get(sandbox_id).state == "deleted"


def test_started_mailbox_outbound_drains_before_delete_cleans_resources(monkeypatch) -> None:
    app, _, runtime, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config(), "drain-delete")))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 1.0, 1024)
    entry = ("2" * 32 + ".json", 1, 2, 10, 3)
    claimed_entry = (entry[0] + "." + "b" * 32 + ".claim", *entry[1:])
    outbound_started = threading.Event()
    permit_finish = threading.Event()
    outbound_finished = threading.Event()
    cleanup_calls = []
    broker_results = []
    delete_results = []

    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None
    broker._claim_request = lambda *_args: claimed_entry

    def process(*_args):
        outbound_started.set()
        assert permit_finish.wait(2)
        outbound_finished.set()
        return {"status": 200, "headers": {}, "body": ""}

    broker._process = process
    broker._write_response = lambda *_args: None
    broker._unlink_request_if_identity = lambda *_args: True
    runtime.cleanup_mailbox = lambda current: cleanup_calls.append(
        (current.sandbox_id, outbound_finished.is_set())
    )
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_args: None)

    broker_thread = threading.Thread(target=lambda: broker_results.append(broker._handle_entry(record, entry, False)))
    broker_thread.start()
    assert outbound_started.wait(2)
    delete_thread = threading.Thread(
        target=lambda: delete_results.append(call(app, "DELETE", f"/v1/sandboxes/{sandbox_id}"))
    )
    delete_thread.start()
    deadline = time.monotonic() + 2
    while store.get(sandbox_id).state != "cleanup_pending":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert delete_thread.is_alive()
    assert cleanup_calls == []
    permit_finish.set()
    broker_thread.join(2)
    delete_thread.join(2)

    assert broker_results == [True]
    assert delete_results[0].status == 204
    assert cleanup_calls == [(sandbox_id, True)]


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
    class BlockingCreate(InMemoryLifecycleTransport):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def request(self, method: str, path: str, body: bytes = b"") -> Response:
            if method == "POST" and path == "/v1/sandboxes":
                self.entered.set()
                assert self.release.wait(2)
            return super().request(method, path, body)

    lifecycle = BlockingCreate()
    runtime = InMemoryRuntimeAdapter()
    state_path = tmp_path / "atomic.sqlite3"
    first = GatewayApplication(config, lifecycle, runtime, SQLiteStateStore(str(state_path)))
    second = GatewayApplication(config, lifecycle, runtime, SQLiteStateStore(str(state_path)))
    payload = create_payload(config, "one")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(call, first, "POST", "/v1/sandboxes", payload)
        assert lifecycle.entered.wait(2)
        conflict = call(second, "POST", "/v1/sandboxes", payload)
        lifecycle.release.set()
        created = winner.result(timeout=2)
    assert created.status == 201
    assert conflict.status == 409
    assert decoded(conflict)["error"]["code"] == "reservation_in_progress"
    assert sum(method == "POST" for method, _, _ in lifecycle.requests) == 1
    resumed = call(second, "POST", "/v1/sandboxes", payload)
    assert resumed.status == 201 and decoded(resumed) == decoded(created)
    assert sum(method == "POST" for method, _, _ in lifecycle.requests) == 1

    restarted_runtime = InMemoryRuntimeAdapter()
    GatewayApplication(config, lifecycle, restarted_runtime, SQLiteStateStore(str(state_path)))
    assert decoded(created)["id"] in restarted_runtime.relays

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
    assert len(crashed_store.list({"state": "uncertain_create"})) == 1
    recovered_runtime = InMemoryRuntimeAdapter()
    recovered_store = SQLiteStateStore(str(crash_path))
    GatewayApplication(config, crash_lifecycle, recovered_runtime, recovered_store)
    active = recovered_store.list({"state": "active"})
    assert len(active) == len(crash_lifecycle.sandboxes) == 1
    assert active[0].sandbox_id in recovered_runtime.relays


def test_cleanup_pending_is_durable_until_delete_is_verified(tmp_path) -> None:
    config = gateway_config()

    class DeferredDelete(InMemoryLifecycleTransport):
        reject_delete = True

        def request(self, method: str, path: str, body: bytes = b"") -> Response:
            if method == "DELETE" and self.reject_delete:
                self.requests.append((method, path, body))
                return Response.json(500, {"error": "deferred"})
            return super().request(method, path, body)

    class RelayFailure(InMemoryRuntimeAdapter):
        def start_relay(self, record) -> None:
            raise RuntimeError("simulated relay crash")

    state_path = tmp_path / "cleanup.sqlite3"
    lifecycle = DeferredDelete()
    store = SQLiteStateStore(str(state_path))
    app = GatewayApplication(config, lifecycle, RelayFailure(), store)
    assert call(app, "POST", "/v1/sandboxes", create_payload(config, "cleanup")).status == 502
    pending = store.list({"state": "cleanup_pending"})
    assert len(pending) == 1 and pending[0].sandbox_id in lifecycle.sandboxes

    lifecycle.reject_delete = False
    metadata = "ai-platform.owner=sandbox-runtime&ai-platform.tenant_id=tenant-cleanup&ai-platform.attempt_id=attempt-cleanup"
    query = urllib.parse.urlencode((("metadata", metadata), ("page", "1"), ("pageSize", "100")))
    assert call(app, "GET", f"/v1/sandboxes?{query}").status == 200
    assert store.get(pending[0].sandbox_id).state == "deleted"
    assert pending[0].sandbox_id not in lifecycle.sandboxes


def test_online_reconciliation_restores_relay_and_cleans_later_page_orphans() -> None:
    app, lifecycle, runtime, _ = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    runtime.relays.remove(sandbox_id)
    metadata = "ai-platform.owner=sandbox-runtime&ai-platform.tenant_id=tenant-one&ai-platform.attempt_id=attempt-one"
    query = urllib.parse.urlencode((("metadata", metadata), ("page", "1"), ("pageSize", "100")))

    assert call(app, "GET", f"/v1/sandboxes?{query}").status == 200
    assert sandbox_id in runtime.relays

    orphan_metadata = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": "tenant-orphan",
        "ai-platform.workspace_id": "workspace-orphan",
        "ai-platform.user_id": "user-orphan",
        "ai-platform.session_id": "session-orphan",
        "ai-platform.run_id": "run-orphan",
        "ai-platform.attempt_id": "attempt-orphan",
    }
    for index in range(101):
        orphan_id = f"sandbox-orphan-{index}"
        lifecycle.sandboxes[orphan_id] = {"id": orphan_id, "status": "running", "metadata": orphan_metadata}
    orphan_filter = "ai-platform.owner=sandbox-runtime&ai-platform.tenant_id=tenant-orphan&ai-platform.attempt_id=attempt-orphan"
    orphan_query = urllib.parse.urlencode((("metadata", orphan_filter), ("page", "1"), ("pageSize", "100")))

    assert call(app, "GET", f"/v1/sandboxes?{orphan_query}").status == 200
    assert not any(sandbox_id.startswith("sandbox-orphan-") for sandbox_id in lifecycle.sandboxes)


@pytest.mark.parametrize("case", ("success", "config-mismatch", "root", "unavailable"))
def test_host_runtime_identity_probe_is_live_fixed_and_fail_closed(monkeypatch, case) -> None:
    app, _, _, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    adapter = DockerRuntimeAdapter(
        gateway_config().record_signing_key,
        1.0,
        2.0,
        1024 * 1024,
        gateway_config().workspace_root,
    )
    container_id = "a" * 64
    config_user = "0:0" if case == "root" else "1000:1000"
    live_user = "1001:1001" if case == "config-mismatch" else config_user
    inspect_payload = [{
        "Image": "image-id",
        "HostConfig": {"Runtime": "runsc", "NetworkMode": "none", "SecurityOpt": ["no-new-privileges:true"]},
        "Config": {
            "User": config_user,
            "Image": record.image,
            "Labels": record.metadata,
            "Env": [
                "AI_PLATFORM_EXECUTOR_AUTH_TOKEN=executor-" + "d" * 32,
                "AI_PLATFORM_CALLBACK_BASE_URL=http://127.0.0.1:18888",
                "SANDBOX_CALLBACK_BASE_URL=http://127.0.0.1:18888",
                "OPENAI_BASE_URL=http://127.0.0.1:18888/model/openai",
                "ANTHROPIC_BASE_URL=http://127.0.0.1:18888/model/anthropic",
            ],
        },
        "State": {"Running": True},
        "Mounts": [
            {"Type": "bind", "Source": item["host"], "Destination": item["mountPath"], "RW": not item["readOnly"]}
            for item in record.mounts
        ],
    }]

    def command(argv, **_kwargs):
        if argv[:3] == ["docker", "ps", "-aq"]:
            return container_id
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps(inspect_payload)
        if argv[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"RepoDigests": ["registry.example/image@" + record.image_digest]}])
        assert argv[:3] == ["docker", "exec", container_id]
        assert argv[3:6] == ["python3", "-c", gateway_adapters._RUNTIME_IDENTITY_PROBE_SOURCE]
        if case == "unavailable":
            raise GatewayError(502, "docker_runtime_unavailable")
        uid, gid = live_user.split(":")
        return json.dumps({"user": live_user, "uid": uid, "gid": gid, "relay_active": True})

    monkeypatch.setattr(adapter, "_command", command)
    if case == "success":
        evidence = adapter.verify(record)
        assert (evidence.user, evidence.uid, evidence.gid, evidence.relay_active) == (
            "1000:1000", "1000", "1000", True
        )
    else:
        with pytest.raises(GatewayError):
            adapter.verify(record)


def test_runtime_starts_relay_with_trusted_request_and_dispatch_budgets(monkeypatch) -> None:
    app, _, _, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config(), "relay-budget")))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    adapter = DockerRuntimeAdapter(
        gateway_config().record_signing_key,
        5.0,
        3600.0,
        1024 * 1024,
        gateway_config().workspace_root,
    )
    commands = []
    monkeypatch.setattr(gateway_adapters.os, "name", "posix")
    monkeypatch.setattr(gateway_adapters.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=lambda _name: SimpleNamespace(gr_gid=4321)))
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda _name: SimpleNamespace(pw_uid=1234)))
    monkeypatch.setattr(gateway_adapters, "_open_workspace_dirfd", lambda *_args: 7)
    monkeypatch.setattr(gateway_adapters, "_prepare_mailbox", lambda *_args: None)
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _fd: None)
    monkeypatch.setattr(adapter, "_container_id", lambda _sandbox_id: "a" * 64)
    monkeypatch.setattr(adapter, "_command", lambda argv, **_kwargs: commands.append(argv) or "")

    adapter.start_relay(record)

    assert commands[0][-3:] == ["5.0", "3600.0", "18888"]


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
    assert bounded._request_deadlines == {}
    bounded._handler_slots.release()
    assert bounded._handler_slots.acquire(blocking=False)
    bounded._handler_slots.release()


@pytest.mark.parametrize(
    "partial",
    (
        b"GET /healthz HTTP/1.1",
        b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow:",
        b"POST /v1/sandboxes HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 10\r\n\r\nx",
    ),
)
def test_absolute_request_deadline_closes_slowloris_and_releases_slot(partial: bytes) -> None:
    app, _, _, _ = application()

    class Handler(_GatewayHandler):
        body_limit = gateway_config().max_body_bytes

    Handler.app = app
    server = _BoundedThreadingHTTPServer(
        ("127.0.0.1", 0),
        Handler,
        1,
        request_deadline_seconds=0.1,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    slow = socket.create_connection(server.server_address, timeout=1)
    slow.sendall(partial)
    slow.settimeout(1)
    stop_trickle = threading.Event()

    def trickle() -> None:
        while not stop_trickle.wait(0.02):
            try:
                slow.sendall(b"x")
            except OSError:
                return

    sender = threading.Thread(target=trickle, daemon=True)
    sender.start()
    started = time.monotonic()
    try:
        chunks = []
        try:
            while chunk := slow.recv(4096):
                chunks.append(chunk)
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        timed_out = b"".join(chunks)
        assert timed_out == b"" or (b"408 Request Timeout" in timed_out and b"request_timeout" in timed_out)
        assert time.monotonic() - started < 0.8
        released = False
        release_deadline = time.monotonic() + 0.8
        while time.monotonic() < release_deadline:
            if server._handler_slots.acquire(blocking=False):
                server._handler_slots.release()
                released = True
                break
            time.sleep(0.01)
        assert released
        healthy = socket.create_connection(server.server_address, timeout=1)
        healthy.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        healthy.settimeout(1)
        response = b""
        while chunk := healthy.recv(4096):
            response += chunk
        healthy.close()
        assert b"200 OK" in response
        assert b"concurrency_limit_reached" not in response
    finally:
        stop_trickle.set()
        sender.join(timeout=1)
        slow.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_absolute_deadline_covers_tls_handshake() -> None:
    app, _, _, _ = application()

    class Handler(_GatewayHandler):
        body_limit = gateway_config().max_body_bytes

    Handler.app = app

    class FakeTlsSocket:
        def __init__(self, raw) -> None:
            self.raw = raw

        def do_handshake(self) -> None:
            self.raw.recv(1, socket.MSG_PEEK)

        def __getattr__(self, name):
            return getattr(self.raw, name)

    class BlockingTlsContext:
        @staticmethod
        def wrap_socket(raw, *, server_side: bool, do_handshake_on_connect: bool):
            assert server_side is True and do_handshake_on_connect is False
            return FakeTlsSocket(raw)

    server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, 2, request_deadline_seconds=0.1)
    server.tls_context = BlockingTlsContext()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = socket.create_connection(server.server_address, timeout=1)
    client.settimeout(1)
    started = time.monotonic()
    try:
        healthy = socket.create_connection(server.server_address, timeout=1)
        healthy.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        healthy.settimeout(1)
        response = b""
        while chunk := healthy.recv(4096):
            response += chunk
        healthy.close()
        assert b"200 OK" in response
        assert client.recv(1) == b""
        assert time.monotonic() - started < 0.8
    finally:
        client.close()
        server.shutdown()
        server.server_close()
    thread.join(timeout=2)


def test_exact_dispatch_uses_accept_time_total_budget_not_ingress_phase_cap() -> None:
    observed: list[float] = []

    class DispatchApp:
        def handle(self, request: Request) -> Response:
            observed.append(operation_deadline(1.0).remaining())
            time.sleep(0.12)
            return Response.json(200, {"ok": True})

    class Handler(_GatewayHandler):
        app = DispatchApp()
        body_limit = gateway_config().max_body_bytes

    server = _BoundedThreadingHTTPServer(
        ("127.0.0.1", 0),
        Handler,
        1,
        request_deadline_seconds=0.05,
        dispatch_deadline_seconds=0.4,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = socket.create_connection(server.server_address, timeout=1)
    try:
        client.sendall(
            b"POST /v1/sandboxes/sandbox-one/proxy/18000/v1/tasks/execute HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\nContent-Length: 0\r\n\r\n"
        )
        client.settimeout(1)
        response = b""
        while chunk := client.recv(4096):
            response += chunk
        assert b"200 OK" in response and b'{"ok":true}' in response
        assert observed and 0.15 < observed[0] <= 0.4
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_exact_dispatch_cannot_outlive_accept_time_total_budget() -> None:
    class DispatchApp:
        def handle(self, request: Request) -> Response:
            while True:
                operation_deadline(1.0).remaining()
                time.sleep(0.02)

    class Handler(_GatewayHandler):
        app = DispatchApp()
        body_limit = gateway_config().max_body_bytes

    server = _BoundedThreadingHTTPServer(
        ("127.0.0.1", 0),
        Handler,
        1,
        request_deadline_seconds=0.05,
        dispatch_deadline_seconds=0.18,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = socket.create_connection(server.server_address, timeout=1)
    started = time.monotonic()
    try:
        client.sendall(
            b"POST /v1/sandboxes/sandbox-one/proxy/18000/v1/tasks/execute HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\nContent-Length: 0\r\n\r\n"
        )
        client.settimeout(1)
        while client.recv(4096):
            pass
        assert time.monotonic() - started < 0.7
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_helper_server_uses_real_framing_and_one_bounded_connection_budget(monkeypatch) -> None:
    app, _, runtime, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    request = json.dumps(
        {"version": 1, "operation": "verify", "record": record.__dict__, "arguments": {}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    server_socket, client_socket = socket.socketpair()
    peer_option = getattr(socket, "SO_PEERCRED", 0xF00D)
    monkeypatch.setattr(gateway_helper.socket, "SO_PEERCRED", peer_option, raising=False)

    class PeerSocket:
        def getsockopt(self, *_args):
            return __import__("struct").pack("3i", 123, 1000, 1000)

        def __getattr__(self, name):
            return getattr(server_socket, name)

    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    worker = threading.Thread(
        target=gateway_helper._serve_connection,
        args=(PeerSocket(), 1000, gateway_config().record_signing_key, runtime, slots, 0.2, 0.5),
    )
    worker.start()
    try:
        frame = __import__("struct").pack("!I", len(request)) + request
        for split in (frame[:2], frame[2:7], frame[7:]):
            client_socket.sendall(split)
        client_socket.settimeout(1)
        size = __import__("struct").unpack("!I", gateway_adapters._recv_exact(client_socket, 4, MonotonicDeadline.after(1)))[0]
        response = json.loads(gateway_adapters._recv_exact(client_socket, size, MonotonicDeadline.after(1)))
        assert response["ok"] is True
        assert response["result"]["sandbox_id"] == sandbox_id
    finally:
        client_socket.close()
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert slots.acquire(blocking=False)
        slots.release()


def test_helper_real_framing_allows_only_cleanup_operations_for_cleanup_pending(monkeypatch) -> None:
    app, _, runtime, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    active = store.get(sandbox_id)
    assert active is not None
    pending = replace(active, state="cleanup_pending", signature="")
    pending.signature = app._sign_record(pending)
    peer_option = getattr(socket, "SO_PEERCRED", 0xF00D)
    monkeypatch.setattr(gateway_helper.socket, "SO_PEERCRED", peer_option, raising=False)

    def framed(operation: str, arguments: dict[str, object]) -> dict[str, object]:
        server_socket, client_socket = socket.socketpair()

        class PeerSocket:
            def getsockopt(self, *_args):
                return __import__("struct").pack("3i", 123, 1000, 1000)

            def __getattr__(self, name):
                return getattr(server_socket, name)

        payload = json.dumps(
            {"version": 1, "operation": operation, "record": pending.__dict__, "arguments": arguments},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        slots = threading.BoundedSemaphore(1)
        assert slots.acquire(blocking=False)
        worker = threading.Thread(
            target=gateway_helper._serve_connection,
            args=(PeerSocket(), 1000, gateway_config().record_signing_key, runtime, slots, 0.2, 0.5),
        )
        worker.start()
        try:
            client_socket.sendall(__import__("struct").pack("!I", len(payload)) + payload)
            size = __import__("struct").unpack(
                "!I",
                gateway_adapters._recv_exact(client_socket, 4, MonotonicDeadline.after(1)),
            )[0]
            return json.loads(gateway_adapters._recv_exact(client_socket, size, MonotonicDeadline.after(1)))
        finally:
            client_socket.close()
            worker.join(timeout=1)
            assert not worker.is_alive()

    for operation in ("stop_relay", "cleanup_mailbox"):
        assert framed(operation, {}) == {"ok": True, "result": {}}
    denied = {
        "verify": {},
        "start_relay": {},
        "proxy": {
            "port": 18000,
            "method": "POST",
            "target": "/v1/tasks/execute",
            "headers": {},
            "body": "",
        },
    }
    for operation, arguments in denied.items():
        response = framed(operation, arguments)
        assert response == {"ok": False, "status": 409, "code": "helper_record_invalid"}


@pytest.mark.parametrize("phase", ("headers", "body"))
def test_outbound_http_deadline_interrupts_trickled_response(monkeypatch, phase: str) -> None:
    progress = 0

    class SlowSocket:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def settimeout(self, _timeout: float) -> None:
            return None

        def shutdown(self, _how: int) -> None:
            self.closed.set()

        def close(self) -> None:
            self.closed.set()

    class SlowResponse:
        status = 200

        def __init__(self, connection) -> None:
            self.connection = connection

        def read(self, _size: int) -> bytes:
            nonlocal progress
            while not self.connection.sock.closed.wait(0.02):
                progress += 1
            raise OSError("closed")

        def getheaders(self):
            return []

    class SlowConnection:
        def __init__(self, *_args, **_kwargs) -> None:
            self.sock = SlowSocket()

        def request(self, *_args, **_kwargs) -> None:
            return None

        def getresponse(self):
            nonlocal progress
            if phase == "body":
                return SlowResponse(self)
            while not self.sock.closed.wait(0.02):
                progress += 1
            raise OSError("closed")

        def close(self) -> None:
            self.sock.close()

    monkeypatch.setattr(gateway_adapters.http.client, "HTTPConnection", SlowConnection)
    transport = LoopbackLifecycleTransport(0.1, 1024)
    started = time.monotonic()
    with pytest.raises(GatewayError) as raised:
        transport.request("GET", "/health")
    assert raised.value.code == "upstream_unavailable"
    assert progress >= 2
    assert time.monotonic() - started < 0.6


def test_pinned_https_connect_uses_approved_ip_without_dns(monkeypatch) -> None:
    connected: list[tuple[tuple[str, int], float]] = []

    class FakeSocket:
        def settimeout(self, _timeout: float) -> None:
            return None

    class FakeTlsSocket(FakeSocket):
        def do_handshake(self) -> None:
            return None

    class FakeTlsContext:
        def wrap_socket(self, raw, *, server_hostname: str, do_handshake_on_connect: bool):
            assert isinstance(raw, FakeSocket)
            assert server_hostname == "models.internal.example"
            assert do_handshake_on_connect is False
            return FakeTlsSocket()

    monkeypatch.setattr(
        gateway_adapters.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("DNS must not be consulted")),
    )
    monkeypatch.setattr(
        gateway_adapters.socket,
        "create_connection",
        lambda address, timeout: connected.append((address, timeout)) or FakeSocket(),
    )
    connection = gateway_adapters._PinnedHTTPSConnection(
        "models.internal.example",
        443,
        ("10.56.1.20",),
        MonotonicDeadline.after(0.5),
    )
    connection._context = FakeTlsContext()
    connection.connect()
    assert connected and connected[0][0] == ("10.56.1.20", 443)
    assert 0 < connected[0][1] <= 0.5


def test_helper_deadline_interrupts_trickled_response(monkeypatch) -> None:
    app, _, _, store = application()
    sandbox_id = decoded(call(app, "POST", "/v1/sandboxes", create_payload(gateway_config())))["id"]
    record = store.get(sandbox_id)
    assert record is not None
    progress = 0

    class SlowHelperSocket:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def settimeout(self, _timeout: float) -> None:
            return None

        def connect(self, _path: str) -> None:
            return None

        def sendall(self, _data: bytes) -> None:
            return None

        def recv(self, _size: int) -> bytes:
            nonlocal progress
            if self.closed.wait(0.03):
                raise OSError("closed")
            progress += 1
            return b"\x00"

        def shutdown(self, _how: int) -> None:
            self.closed.set()

        def close(self) -> None:
            self.closed.set()

    monkeypatch.setattr(gateway_adapters.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(gateway_adapters.socket, "socket", lambda *_args, **_kwargs: SlowHelperSocket())
    adapter = HelperRuntimeAdapter("/run/opensandbox-gateway/helper.sock", 0.1, 1024)
    started = time.monotonic()
    with pytest.raises(GatewayError) as raised:
        adapter.verify(record)
    assert raised.value.code == "runtime_helper_unavailable"
    assert progress >= 2
    assert time.monotonic() - started < 0.6


def test_mailbox_poll_consumes_one_shared_absolute_budget(monkeypatch) -> None:
    record = SimpleNamespace(sandbox_id="sandbox-one", workspace_host_path="/data/opensandbox/workspaces/one")
    denials: list[tuple[str, str]] = []
    store = active_mailbox_store([record], denials)
    monkeypatch.setattr(gateway_adapters.os, "name", "posix")
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_: None)
    names = ["1" * 32 + ".json", "2" * 32 + ".json"]
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 0.1, 1024)
    entries = [(name, 1, index + 1, 10, index + 1) for index, name in enumerate(names)]
    broker._snapshot_record = lambda *_: (len(entries), 20, entries)
    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None
    broker._claim_request = lambda _request_fd, _claim_fd, entry: entry
    broker._prune_claims = lambda _claim_fd: None
    broker._request_identity_matches = lambda *_: True
    broker._unlink_request_if_identity = lambda *_: True
    processed: list[str] = []
    responses: list[int] = []

    def process(_descriptor, name, _identity):
        processed.append(name)
        while True:
            time.sleep(0.02)
            operation_deadline(1.0).remaining()

    broker._process = process
    broker._write_response = lambda _descriptor, _name, value: responses.append(value["status"])
    started = time.monotonic()
    assert broker.poll_once() == 0
    assert time.monotonic() - started < 0.6
    assert processed == [names[0]]
    assert responses == []
    assert ("mailbox-broker", "broker_deadline_exceeded") in denials


def test_reconciliation_list_paginates_and_rejects_ambiguity() -> None:
    class PagedLifecycle(InMemoryLifecycleTransport):
        def __init__(self, pages) -> None:
            super().__init__()
            self.pages = pages

        def request(self, method: str, path: str, body: bytes = b"") -> Response:
            if method == "GET" and path.startswith("/v1/sandboxes?"):
                page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["page"][0])
                items, response_page, has_next = self.pages(page)
                return Response.json(
                    200,
                    {
                        "items": items,
                        "pagination": {"page": response_page, "pageSize": 100, "hasNextPage": has_next},
                    },
                )
            return super().request(method, path, body)

    config = gateway_config()
    pages = lambda page: ([{"id": f"sandbox-{page}"}], page, page < 2)
    app = GatewayApplication(config, PagedLifecycle(pages), InMemoryRuntimeAdapter(), InMemoryStateStore())
    assert [item["id"] for item in app._list_intent_sandboxes("intent-one")] == ["sandbox-1", "sandbox-2"]

    duplicate = lambda page: ([{"id": "sandbox-1"}], page, page < 2)
    duplicate_app = GatewayApplication(config, PagedLifecycle(duplicate), InMemoryRuntimeAdapter(), InMemoryStateStore())
    with pytest.raises(GatewayError, match="reservation_reconciliation_ambiguous"):
        duplicate_app._list_intent_sandboxes("intent-one")

    wrong_page = lambda page: ([], page + 1, False)
    wrong_page_app = GatewayApplication(config, PagedLifecycle(wrong_page), InMemoryRuntimeAdapter(), InMemoryStateStore())
    with pytest.raises(GatewayError, match="reservation_reconciliation_ambiguous"):
        wrong_page_app._list_intent_sandboxes("intent-one")

    never_ends = lambda page: ([], page, True)
    bounded_app = GatewayApplication(config, PagedLifecycle(never_ends), InMemoryRuntimeAdapter(), InMemoryStateStore())
    with pytest.raises(GatewayError, match="reservation_reconciliation_ambiguous"):
        bounded_app._list_intent_sandboxes("intent-one")


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


def test_literal_private_ip_certificate_san_and_workspace_file_proxy_contracts(monkeypatch) -> None:
    with pytest.raises(ValueError, match="approved literal IP"):
        replace(gateway_config(), public_authority="sandbox-gateway.example:8443").validate()
    monkeypatch.setattr(ssl._ssl, "_test_decode_cert", lambda _: {"subjectAltName": (("IP Address", "10.56.1.72"),)})
    _verify_certificate_ip_san("unused.pem", PUBLIC_AUTHORITY)
    monkeypatch.setattr(ssl._ssl, "_test_decode_cert", lambda _: {"subjectAltName": (("DNS", "sandbox-gateway.example"),)})
    with pytest.raises(ValueError, match="does not exactly match"):
        _verify_certificate_ip_san("unused.pem", PUBLIC_AUTHORITY)
    monkeypatch.setattr(
        ssl._ssl,
        "_test_decode_cert",
        lambda _: {"subjectAltName": (("IP Address", "10.56.1.72"), ("IP Address", "10.56.1.73"))},
    )
    with pytest.raises(ValueError, match="does not exactly match"):
        _verify_certificate_ip_san("unused.pem", PUBLIC_AUTHORITY)

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
    mailbox_source = "\n".join(
        (
            inspect.getsource(MailboxBroker),
            inspect.getsource(gateway_adapters._open_workspace_dirfd),
            inspect.getsource(gateway_adapters._revalidate_workspace_fd),
            inspect.getsource(gateway_adapters._remove_tree_at),
        )
    )
    assert all(marker in mailbox_source for marker in ("O_NOFOLLOW", "dir_fd=", "os.replace", "fstat", "st_ino", "st_dev"))

    for field, bad_value in (("owner", "0"), ("group", "0"), ("mode", "0644")):
        invalid = multipart_lease_upload(record).replace(
            f'"{field}":"{("0600" if field == "mode" else "1000")}"'.encode(),
            f'"{field}":"{bad_value}"'.encode(),
        )
        rejected = call(
            app,
            "POST",
            f"/v1/sandboxes/{sandbox_id}/proxy/44772/files/upload",
            invalid,
            {ROUTE_HEADER: token, "Content-Type": "multipart/form-data; boundary=lease-boundary"},
        )
        assert rejected.status == 400
    assert len(runtime.proxied) == 2

    for alias in (
        "/data/opensandbox/workspaces/tenants/tenant-one/./workspaces/workspace-one/users/user-one/sessions/session-one/runs/run-one/workspace",
        "/data/opensandbox/workspaces/tenants/tenant-one//workspaces/workspace-one/users/user-one/sessions/session-one/runs/run-one/workspace",
        "/data/opensandbox/workspaces/tenants/tenant-one/workspaces/workspace-one/users/user-one/sessions/session-one/runs/run-one/workspace/",
    ):
        rejected = call(app, "POST", "/v1/sandboxes", create_payload(gateway_config(), "one", alias))
        assert rejected.status == 400


def test_workspace_dirfd_identity_and_symlink_leaf_fail_closed(monkeypatch) -> None:
    directory_mode = gateway_adapters.stat.S_IFDIR | 0o700
    monkeypatch.setattr(gateway_adapters.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "getuid", lambda: 2000, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "getgid", lambda: 3000, raising=False)
    monkeypatch.setattr(
        gateway_adapters.os,
        "fstat",
        lambda _: SimpleNamespace(st_mode=directory_mode, st_dev=11, st_ino=22),
    )
    monkeypatch.setattr(
        gateway_adapters.os,
        "stat",
        lambda *_, **__: SimpleNamespace(st_mode=directory_mode, st_dev=11, st_ino=23),
    )
    with pytest.raises(OSError, match="workspace identity changed"):
        gateway_adapters._revalidate_workspace_fd(7, "/data/opensandbox/workspaces/scoped/workspace")

    unlinked = []

    def reject_symlink(name, flags, *, dir_fd):
        assert flags & gateway_adapters.os.O_NOFOLLOW
        assert flags & gateway_adapters.os.O_DIRECTORY
        assert dir_fd == 9
        raise NotADirectoryError(name)

    monkeypatch.setattr(gateway_adapters.os, "open", reject_symlink)
    monkeypatch.setattr(gateway_adapters.os, "unlink", lambda name, *, dir_fd: unlinked.append((name, dir_fd)))
    gateway_adapters._remove_tree_at(9, "swapped-link")
    assert unlinked == [("swapped-link", 9)]


def test_mailbox_protocol_rejects_wrong_owner_group_or_mode(monkeypatch) -> None:
    expected = SimpleNamespace(st_mode=stat.S_IFDIR | 0o2770, st_uid=1000, st_gid=4321)
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _: expected)
    gateway_adapters._require_directory(7, uid=1000, gid=4321, mode=0o2770)
    for changed in (
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o2770, st_uid=1001, st_gid=4321),
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o2770, st_uid=1000, st_gid=4322),
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o0770, st_uid=1000, st_gid=4321),
        SimpleNamespace(st_mode=stat.S_IFREG | 0o2770, st_uid=1000, st_gid=4321),
    ):
        monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _, value=changed: value)
        with pytest.raises(OSError, match="ownership protocol mismatch"):
            gateway_adapters._require_directory(7, uid=1000, gid=4321, mode=0o2770)


def test_mailbox_response_is_random_atomic_and_read_only(monkeypatch) -> None:
    opened: list[tuple[str, int]] = []
    replaced: list[tuple[str, str]] = []
    modes: list[int] = []
    tokens = iter(("a" * 32, "b" * 32))
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(gateway_adapters.secrets, "token_hex", lambda _: next(tokens))
    monkeypatch.setattr(gateway_adapters.os, "open", lambda name, flags, mode, *, dir_fd: opened.append((name, flags)) or 9)
    monkeypatch.setattr(gateway_adapters.os, "write", lambda _, data: len(data))
    monkeypatch.setattr(gateway_adapters.os, "fsync", lambda _: None)
    monkeypatch.setattr(gateway_adapters.os, "fchmod", lambda _, mode: modes.append(mode))
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _: None)
    monkeypatch.setattr(
        gateway_adapters.os,
        "replace",
        lambda source, target, **_: replaced.append((source, target)),
    )
    MailboxBroker._write_response(8, "1" * 32 + ".json", {"status": 200, "headers": {}, "body": ""})
    MailboxBroker._write_response(8, "2" * 32 + ".json", {"status": 200, "headers": {}, "body": ""})
    assert [item[0] for item in opened] == [f".{('a' * 32)}.tmp", f".{('b' * 32)}.tmp"]
    assert all(flags & os.O_EXCL and flags & os.O_NOFOLLOW for _, flags in opened)
    assert modes == [0o444, 0o444]
    assert replaced == [(opened[0][0], "1" * 32 + ".json"), (opened[1][0], "2" * 32 + ".json")]


def test_mailbox_request_inode_change_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "getgid", lambda: 4321, raising=False)
    raw = json.dumps({"method": "POST", "path": "/callback", "headers": {}, "body": ""}).encode()
    before = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o640,
        st_uid=1000,
        st_gid=4321,
        st_size=len(raw),
        st_dev=1,
        st_ino=2,
        st_mtime_ns=3,
        st_ctime_ns=4,
    )
    after = SimpleNamespace(**{**before.__dict__, "st_ino": 9})
    evidence = iter((before, after))
    chunks = iter((raw, b""))
    monkeypatch.setattr(gateway_adapters.os, "open", lambda *_, **__: 7)
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _: next(evidence))
    monkeypatch.setattr(gateway_adapters.os, "read", lambda *_: next(chunks))
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _: None)
    broker = MailboxBroker(SimpleNamespace(), SimpleNamespace(targets={}), 1.0, 1024)
    with pytest.raises(GatewayError, match="broker_request_changed"):
        broker._process(6, "0" * 32 + ".json")


def test_mailbox_forwards_callback_token_only_to_callback_and_context_targets(monkeypatch) -> None:
    captured: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        status = 200

        @staticmethod
        def read(_limit):
            return b"{}"

        @staticmethod
        def getheaders():
            return [("content-type", "application/json")]

    class FakeConnection:
        sock = None

        def __init__(self, *_args):
            pass

        def request(self, _method, path, *, body, headers):
            del body
            captured.append((path, dict(headers)))

        @staticmethod
        def getresponse():
            return FakeResponse()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr(gateway_adapters, "_PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr(gateway_adapters.os, "open", lambda *_, **__: 7)
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _fd: None)
    monkeypatch.setattr(gateway_adapters.os, "getgid", lambda: 4321, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    policy = SimpleNamespace(
        targets={
            "callback": ("https://api.internal.example", ("10.56.1.20",)),
            "openai": ("https://models.internal.example/openai/v1", ("10.56.1.21",)),
            "anthropic": ("https://models.internal.example/anthropic/v1", ("10.56.1.22",)),
        }
    )
    broker = MailboxBroker(SimpleNamespace(), policy, 1.0, 1024)

    def forward(path: str) -> dict[str, str]:
        raw = json.dumps(
            {
                "version": 1,
                "method": "POST",
                "path": path,
                "headers": {
                    "X-AI-Platform-Callback-Token": "attempt-bound-token",
                    "Content-Type": "application/json",
                },
                "body": "e30=",
                "created_at_unix_seconds": time.time(),
                "timeout_seconds": 3600.0,
            }
        ).encode()
        evidence = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o640,
            st_size=len(raw),
            st_uid=1000,
            st_gid=4321,
            st_dev=1,
            st_ino=2,
            st_mtime_ns=3,
            st_ctime_ns=4,
        )
        chunks = iter((raw, b""))
        monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: evidence)
        monkeypatch.setattr(gateway_adapters.os, "read", lambda *_: next(chunks))
        broker._process(6, "0" * 32 + ".json")
        return captured[-1][1]

    callback_headers = forward("/api/ai/runtime/callbacks/executor")
    context_headers = forward("/api/ai/runtime/callbacks/context-retrieval")
    model_headers = forward("/model/openai/chat/completions")

    assert callback_headers["x-ai-platform-callback-token"] == "attempt-bound-token"
    assert context_headers["x-ai-platform-callback-token"] == "attempt-bound-token"
    assert "x-ai-platform-callback-token" not in model_headers


def test_mailbox_model_and_callback_use_distinct_absolute_budgets_and_request_only_shortens(monkeypatch) -> None:
    observed: list[float] = []

    class Deadline:
        def __init__(self, timeout):
            self.timeout = timeout

        def remaining(self):
            return self.timeout

        def arm(self, _callback):
            return SimpleNamespace(cancel=lambda: None)

        def bind_socket(self, _socket):
            return None

    class FakeResponse:
        status = 200

        @staticmethod
        def read(_limit):
            return b"{}"

        @staticmethod
        def getheaders():
            return []

    class FakeConnection:
        sock = None

        def __init__(self, *_args):
            pass

        def request(self, *_args, **_kwargs):
            return None

        def getresponse(self):
            return FakeResponse()

        def close(self):
            return None

    def deadline(timeout):
        observed.append(timeout)
        return Deadline(timeout)

    monkeypatch.setattr(gateway_adapters, "operation_deadline", deadline)
    monkeypatch.setattr(gateway_adapters, "_PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr(gateway_adapters.os, "open", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _fd: None)
    monkeypatch.setattr(gateway_adapters.os, "getgid", lambda: 4321, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    policy = SimpleNamespace(
        targets={
            "callback": ("https://api.internal.example", ("10.56.1.20",)),
            "openai": ("https://models.internal.example/openai/v1", ("10.56.1.21",)),
            "anthropic": ("https://models.internal.example/anthropic/v1", ("10.56.1.22",)),
        }
    )
    broker = MailboxBroker(SimpleNamespace(), policy, 5.0, 1024, dispatch_timeout_seconds=3600.0)

    def process(path: str, requested_timeout: float) -> None:
        raw = json.dumps(
            {
                "version": 1,
                "method": "POST",
                "path": path,
                "headers": {"content-type": "application/json"},
                "body": "e30=",
                "created_at_unix_seconds": time.time(),
                "timeout_seconds": requested_timeout,
            }
        ).encode()
        evidence = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o640,
            st_size=len(raw),
            st_uid=1000,
            st_gid=4321,
            st_dev=1,
            st_ino=2,
            st_mtime_ns=3,
            st_ctime_ns=4,
        )
        chunks = iter((raw, b""))
        monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: evidence)
        monkeypatch.setattr(gateway_adapters.os, "read", lambda *_args: next(chunks))
        broker._process(6, "0" * 32 + ".json")

    process("/api/ai/runtime/callbacks/executor", 7200.0)
    process("/model/openai/chat/completions", 7200.0)
    process("/model/anthropic/v1/messages", 12.0)

    assert 4.5 <= observed[0] <= 5.0
    assert 3599.0 <= observed[1] <= 3600.0
    assert 11.5 <= observed[2] <= 12.0


def test_mailbox_poll_isolates_bad_requests_and_bounds_each_sandbox(monkeypatch) -> None:
    records = [
        SimpleNamespace(sandbox_id="sandbox-one", workspace_host_path="/data/opensandbox/workspaces/one"),
        SimpleNamespace(sandbox_id="sandbox-two", workspace_host_path="/data/opensandbox/workspaces/two"),
    ]
    denials: list[tuple[str, str]] = []
    store = active_mailbox_store(records, denials)
    monkeypatch.setattr(gateway_adapters.os, "name", "posix")
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_: None)
    first_names = [f"{value:032x}.json" for value in range(20)]
    second_names = ["f" * 32 + ".json"]
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 1.0, 1024)
    processed: list[str] = []
    responses: list[tuple[str, int]] = []
    snapshots = {
        records[0].workspace_host_path: (20, 200, [(first_names[0], 1, 1, 10, 1)]),
        records[1].workspace_host_path: (1, 10, [(second_names[0], 2, 2, 10, 2)]),
    }
    broker._snapshot_record = lambda record, _now: snapshots[record.workspace_host_path]
    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None
    broker._claim_request = lambda _request_fd, _claim_fd, entry: entry
    broker._prune_claims = lambda _claim_fd: None
    broker._request_identity_matches = lambda *_: True
    broker._unlink_request_if_identity = lambda *_: True

    def process(_descriptor, name, _identity):
        processed.append(name)
        if len(processed) == 1:
            raise RuntimeError("one malformed request")
        return {"status": 200, "headers": {}, "body": ""}

    broker._process = process
    broker._write_response = lambda _descriptor, name, value: responses.append((name, value["status"]))
    assert broker.poll_once() == 2
    assert processed == [first_names[0], second_names[0]]
    assert responses[0][1] == 500 and responses[-1] == (second_names[0], 200)
    assert ("mailbox-broker", "broker_internal_error") in denials

    processed.clear()
    responses.clear()
    assert broker.poll_once() == 2
    assert processed == [second_names[0], first_names[0]]


@pytest.mark.parametrize(
    "snapshot",
    (
        (MailboxBroker.PER_SANDBOX_PENDING_COUNT + 1, 1024),
        (1, MailboxBroker.PER_SANDBOX_PENDING_BYTES + 1),
    ),
)
def test_mailbox_per_sandbox_backlog_caps_fail_closed_without_outbound(monkeypatch, snapshot) -> None:
    record = SimpleNamespace(sandbox_id="sandbox-one", workspace_host_path="/data/opensandbox/workspaces/one")
    denials = []
    store = active_mailbox_store([record], denials)
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 1.0, 1024)
    entry = ("1" * 32 + ".json", 1, 2, 10, 3)
    monkeypatch.setattr(gateway_adapters.os, "name", "posix")
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_: None)
    broker._snapshot_record = lambda *_: (snapshot[0], snapshot[1], [entry])
    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None
    broker._claim_request = lambda _request_fd, _claim_fd, entry: entry
    broker._prune_claims = lambda _claim_fd: None
    broker._request_identity_matches = lambda *_: True
    broker._unlink_request_if_identity = lambda *_: True
    broker._process = lambda *_: (_ for _ in ()).throw(AssertionError("overflow must not reach upstream"))
    responses = []
    broker._write_response = lambda _fd, _name, response: responses.append(response)

    assert broker.poll_once() == 1
    assert [response["status"] for response in responses] == [429]
    assert ("mailbox-broker", "broker_backlog_exceeded") in denials


def test_mailbox_global_backlog_cap_fails_closed_for_every_sandbox(monkeypatch) -> None:
    records = [
        SimpleNamespace(sandbox_id=f"sandbox-{index}", workspace_host_path=f"/workspace/{index}")
        for index in range(5)
    ]
    denials = []
    store = active_mailbox_store(records, denials)
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 1.0, 1024)
    monkeypatch.setattr(gateway_adapters.os, "name", "posix")
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_: None)

    def snapshot(record, _now):
        index = int(record.workspace_host_path.rsplit("/", 1)[1])
        return 60, 600, [(f"{index:032x}.json", index + 1, index + 2, 10, index + 3)]

    broker._snapshot_record = snapshot
    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None
    broker._claim_request = lambda _request_fd, _claim_fd, entry: entry
    broker._prune_claims = lambda _claim_fd: None
    broker._request_identity_matches = lambda *_: True
    broker._unlink_request_if_identity = lambda *_: True
    broker._process = lambda *_: (_ for _ in ()).throw(AssertionError("global overflow must not reach upstream"))
    responses = []
    broker._write_response = lambda _fd, name, response: responses.append((name, response["status"]))

    assert broker.poll_once() == 5
    assert len(responses) == 5 and {status for _, status in responses} == {429}


def test_mailbox_expired_requests_are_reclaimed_with_bounded_identity_delete(monkeypatch) -> None:
    record = SimpleNamespace(workspace_host_path="/data/opensandbox/workspaces/one")
    denials = []
    store = SimpleNamespace(list=lambda _: [record], record_deny=lambda *value: denials.append(value))
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 1.0, 1024)
    names = [f"{index:032x}.json" for index in range(2)]
    old = time.time() - broker.REQUEST_TTL_SECONDS - 1
    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None
    broker._claim_request = lambda _request_fd, _claim_fd, entry: entry
    broker._prune_claims = lambda _claim_fd: None
    broker._prune_responses = lambda _fd: None
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_: None)
    monkeypatch.setattr(gateway_adapters.os, "listdir", lambda _fd: names)
    monkeypatch.setattr(
        gateway_adapters.os,
        "stat",
        lambda name, **_kwargs: SimpleNamespace(
            st_dev=1,
            st_ino=names.index(name) + 1,
            st_size=10,
            st_mtime=old,
            st_mtime_ns=int(old * 1_000_000_000),
        ),
    )
    removed = []
    broker._unlink_request_if_identity = lambda _fd, identity: removed.append(identity) or True

    assert broker._snapshot_record(record, time.time()) == (0, 0, [])
    assert [identity[0] for identity in removed] == names
    assert denials.count(("mailbox-broker", "broker_request_expired")) == 2


def test_mailbox_named_inode_swap_is_rejected_before_response_and_delete(monkeypatch) -> None:
    broker = MailboxBroker(SimpleNamespace(), SimpleNamespace(targets={}), 1.0, 1024)
    entry = ("1" * 32 + ".json", 1, 2, 10, 3)
    named = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_dev=1, st_ino=2)
    opened = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_dev=1, st_ino=9)
    monkeypatch.setattr(gateway_adapters.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(gateway_adapters.os, "stat", lambda *_args, **_kwargs: named)
    monkeypatch.setattr(gateway_adapters.os, "open", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(gateway_adapters.os, "fstat", lambda _fd: opened)
    monkeypatch.setattr(gateway_adapters.os, "close", lambda _fd: None)

    assert broker._request_identity_matches(3, entry) is False


def test_mailbox_claim_is_unpredictable_atomic_and_revalidates_moved_inode(monkeypatch) -> None:
    entry = ("1" * 32 + ".json", 11, 22, 33, 44)
    renames: list[tuple[str, str, int, int]] = []
    checks = iter((True, True))
    monkeypatch.setattr(gateway_adapters.secrets, "token_hex", lambda _size: "a" * 32)
    monkeypatch.setattr(
        MailboxBroker,
        "_request_identity_matches",
        classmethod(lambda _cls, _fd, _entry: next(checks)),
    )
    monkeypatch.setattr(
        gateway_adapters.os,
        "rename",
        lambda source, target, *, src_dir_fd, dst_dir_fd: renames.append(
            (source, target, src_dir_fd, dst_dir_fd)
        ),
    )

    claimed = MailboxBroker._claim_request(3, 4, entry)

    assert claimed == (f"{entry[0]}.{'a' * 32}.claim", *entry[1:])
    assert renames == [(entry[0], claimed[0], 3, 4)]

    checks = iter((True, False))
    cleaned: list[tuple[int, str]] = []
    monkeypatch.setattr(
        MailboxBroker,
        "_request_identity_matches",
        classmethod(lambda _cls, _fd, _entry: next(checks)),
    )
    monkeypatch.setattr(
        MailboxBroker,
        "_unlink_response_if_regular",
        lambda fd, name: cleaned.append((fd, name)),
    )
    assert MailboxBroker._claim_request(3, 4, entry) is None
    assert cleaned == [(4, f"{entry[0]}.{'a' * 32}.claim")]


def test_mailbox_claim_is_cleaned_when_processing_deadline_expires(monkeypatch) -> None:
    store = active_mailbox_store([], [])
    broker = MailboxBroker(store, SimpleNamespace(targets={}), 1.0, 1024)
    entry = ("1" * 32 + ".json", 1, 2, 10, 3)
    claimed = (entry[0] + "." + "a" * 32 + ".claim", *entry[1:])
    broker._open_record_mailbox = lambda _record: (1, 2, 3, 4, 5)
    broker._close_mailbox = lambda _descriptors: None
    broker._claim_request = lambda *_args: claimed
    broker._process = lambda *_args: (_ for _ in ()).throw(DeadlineExceeded("expired"))
    removed: list[tuple[int, tuple[str, int, int, int, int]]] = []
    broker._unlink_request_if_identity = lambda fd, identity: removed.append((fd, identity)) or True
    monkeypatch.setattr(gateway_adapters, "_revalidate_workspace_fd", lambda *_: None)

    with pytest.raises(DeadlineExceeded):
        broker._handle_entry(SimpleNamespace(sandbox_id="sandbox-one", workspace_host_path="/workspace"), entry, False)

    assert removed == [(4, claimed)]


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="real broker-owned claim permissions require a root-capable POSIX release gate",
)
def test_mailbox_claim_moves_request_into_real_broker_owned_posix_directory(tmp_path) -> None:
    workspace_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        gateway_adapters._prepare_mailbox(workspace_fd, str(tmp_path), os.getuid(), os.getgid())
    finally:
        os.close(workspace_fd)
    mailbox = tmp_path / ".opensandbox-gateway"
    request_path = mailbox / "requests" / ("1" * 32 + ".json")
    request_path.write_text("{}", encoding="utf-8")
    os.chown(request_path, 1000, os.getgid())
    os.chmod(request_path, 0o640)
    request_fd = os.open(mailbox / "requests", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    claim_fd = os.open(mailbox / "claims", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        evidence = os.stat(request_path, follow_symlinks=False)
        entry = (request_path.name, evidence.st_dev, evidence.st_ino, evidence.st_size, evidence.st_mtime_ns)
        claimed = MailboxBroker._claim_request(request_fd, claim_fd, entry)
        assert claimed is not None
        assert not request_path.exists()
        assert os.stat(claimed[0], dir_fd=claim_fd, follow_symlinks=False).st_ino == evidence.st_ino
        claim_dir = os.fstat(claim_fd)
        assert claim_dir.st_uid == os.getuid() and stat.S_IMODE(claim_dir.st_mode) == 0o700
    finally:
        os.close(claim_fd)
        os.close(request_fd)


def test_broker_loop_isolates_iteration_exception_and_continues() -> None:
    stop = threading.Event()
    calls = 0

    class Broker:
        def poll_once(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("one iteration failed")
            stop.set()
            return 0

    thread = threading.Thread(target=_broker_loop, args=(Broker(), stop))
    thread.start()
    thread.join(timeout=1)
    assert calls == 2 and not thread.is_alive()


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="real relay owner/mode and timeout cleanup requires a root-capable POSIX release gate",
)
def test_relay_timeout_removes_pending_request_on_real_posix(tmp_path) -> None:
    mailbox = tmp_path / "mailbox"
    requests = mailbox / "requests"
    responses = mailbox / "responses"
    requests.mkdir(parents=True)
    responses.mkdir()
    os.chmod(mailbox, 0o711)
    os.chown(mailbox, 0, 0)
    os.chmod(requests, 0o2770)
    os.chown(requests, 1000, 0)
    os.chmod(responses, 0o755)
    os.chown(responses, 0, 0)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    process = subprocess.Popen(
        [sys.executable, "-c", RELAY_SOURCE, str(mailbox), "0", "0", "0.05", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 2
        while True:
            try:
                client = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        with client:
            client.sendall(b"POST /callback HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
            client.settimeout(1)
            response = b""
            while chunk := client.recv(4096):
                response += chunk
        assert b"504 Gateway Timeout" in response
        assert not list(requests.glob("[0-9a-f]*.json"))
    finally:
        process.terminate()
        process.wait(timeout=2)


@pytest.mark.skipif(os.name != "posix", reason="POSIX dirfd/mode enforcement requires the s72 execution environment")
def test_mailbox_response_mode_is_enforced_by_real_posix_dirfd(tmp_path) -> None:
    response_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        name = "1" * 32 + ".json"
        MailboxBroker._write_response(response_fd, name, {"status": 200, "headers": {}, "body": ""})
        evidence = os.stat(name, dir_fd=response_fd, follow_symlinks=False)
        assert stat.S_ISREG(evidence.st_mode)
        assert stat.S_IMODE(evidence.st_mode) == 0o444
    finally:
        os.close(response_fd)


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
    env_example = (root / "deploy/opensandbox/gateway.env.example").read_text(encoding="utf-8")
    policy = json.loads((root / "deploy/opensandbox/egress-policy.v1.example.json").read_text(encoding="utf-8"))
    assert "docker.sock" not in public_unit and "SupplementaryGroups=docker" not in public_unit
    assert "docker.sock" in helper_unit and "SupplementaryGroups=docker" in helper_unit
    assert all(
        marker in install
        for marker in (
            "diff-index --quiet",
            "ls-files --others",
            "merge-base --is-ancestor",
            "git -C \"$SOURCE_REAL\" archive",
            "SOURCE_COMMIT",
            "is_commit",
            "require_root_tree",
            "verify_manifest",
            "workspaces.acl",
            "snapshot_state",
            "restore_snapshot",
            "previous-snapshot",
            "systemctl daemon-reload",
            "systemctl restart",
            "WorkingDirectory",
            "CURRENT_LINK.next",
        )
    )
    assert install.index("systemctl restart") < install.index("CURRENT_LINK.next")
    assert all(
        marker in rollback
        for marker in ("previous-snapshot", "config.present", "workspaces.acl", "verify_manifest", "validate_release", "daemon-reload", "systemctl restart", "CURRENT_LINK.next")
    )
    assert rollback.index("systemctl restart") < rollback.index("CURRENT_LINK.next")
    assert "InaccessiblePaths=/var/lib/opensandbox-gateway-deploy" in public_unit
    callback_base = next(line.split("=", 1)[1] for line in env_example.splitlines() if line.startswith("OPENSANDBOX_GATEWAY_CALLBACK_BASE="))
    assert callback_base == policy["targets"]["callback"]["base_url"]
    assert urllib.parse.urlsplit(callback_base).path == ""


def _run_gateway_bash_contract(script: pathlib.Path, root: pathlib.Path, body: str) -> subprocess.CompletedProcess[str]:
    bash = pathlib.Path("C:/Program Files/Git/bin/bash.exe")
    executable = str(bash) if bash.exists() else shutil.which("bash")
    if not executable:
        pytest.skip("Git Bash is required for executable deployment contracts")
    return subprocess.run(
        [executable, "-c", textwrap.dedent(body), "gateway-contract", str(script), str(root)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_installer_snapshot_restores_first_install_absence(tmp_path) -> None:
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/install-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        SYSTEMD_DIR=$ROOT/systemd; CONFIG_DIR=$ROOT/config; WORKSPACE_ROOT=$ROOT/workspaces
        CURRENT_LINK=$ROOT/current; RELEASES=$ROOT/releases; STATE=$ROOT/systemctl; ACTIONS=$ROOT/actions
        DEPLOY_STATE=$ROOT/deploy; AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence; mkdir -p "$DEPLOY_STATE"
        mkdir -p "$SYSTEMD_DIR" "$WORKSPACE_ROOT" "$RELEASES" "$STATE"
        printf 'acl-before\n' > "$ROOT/acl.current"
        require_root_tree() { test -d "$1" && test ! -L "$1"; }
        write_manifest() { : > "$1/MANIFEST.sha256"; }
        verify_manifest() { test -f "$1/MANIFEST.sha256"; }
        validate_release() { is_commit "$1"; }
        chown() { :; }
        stat() {
          test "$1" = -c && test "${@: -1}" = "$DEPLOY_STATE" && { echo 0:0:700; return; }
          test "$1" = -c && { echo 0; return; }
          command stat "$@"
        }
        install() {
          if test "$1" = -d; then mkdir -p "${@: -1}"; else cp "${@: -2:1}" "${@: -1}"; fi
        }
        getfacl() { cat "$ROOT/acl.current"; }
        setfacl() { cp "${1#--restore=}" "$ROOT/acl.current"; }
        systemctl() {
          action=$1; unit=${@: -1}; printf '%s:%s\n' "$action" "$unit" >> "$ACTIONS"
          case "$action" in
            is-active) test -f "$STATE/$unit.active" ;;
            is-enabled) test -f "$STATE/$unit.enabled" ;;
            enable) : > "$STATE/$unit.enabled" ;;
            disable) rm -f "$STATE/$unit.enabled" ;;
            restart) : > "$STATE/$unit.active" ;;
            stop) rm -f "$STATE/$unit.active" ;;
            daemon-reload) : ;;
          esac
        }
        snapshot_state "$ROOT/snapshot"
        printf 'new-public\n' > "$SYSTEMD_DIR/opensandbox-gateway.service"
        printf 'new-helper\n' > "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        mkdir "$CONFIG_DIR"; printf 'new-config\n' > "$CONFIG_DIR/gateway.env"
        printf 'acl-mutated\n' > "$ROOT/acl.current"
        : > "$STATE/opensandbox-gateway.service.active"; : > "$STATE/opensandbox-gateway.service.enabled"
        : > "$STATE/opensandbox-gateway-helper.service.active"; : > "$STATE/opensandbox-gateway-helper.service.enabled"
        restore_snapshot "$ROOT/snapshot"
        test ! -e "$SYSTEMD_DIR/opensandbox-gateway.service"
        test ! -e "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        test ! -e "$CONFIG_DIR" && test ! -e "$CURRENT_LINK"
        test ! -e "$AUTHORITY_SHA_STATE"
        test ! -e "$AUTHORITY_EVIDENCE_STATE"
        grep -qx acl-before "$ROOT/acl.current"
        test ! -e "$STATE/opensandbox-gateway.service.active" && test ! -e "$STATE/opensandbox-gateway.service.enabled"
        test ! -e "$STATE/opensandbox-gateway-helper.service.active" && test ! -e "$STATE/opensandbox-gateway-helper.service.enabled"
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_installer_snapshot_restores_upgrade_state_and_switches_last(tmp_path) -> None:
    old = "1" * 40
    new = "2" * 40
    releases = tmp_path / "releases"
    releases.mkdir()
    (releases / old).mkdir()
    (releases / new).mkdir()
    try:
        os.symlink(f"releases/{new}", tmp_path / "current", target_is_directory=True)
    except OSError:
        pytest.skip("native directory symlinks are required for the POSIX upgrade rollback contract")
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/install-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        SYSTEMD_DIR=$ROOT/systemd; CONFIG_DIR=$ROOT/config; WORKSPACE_ROOT=$ROOT/workspaces
        CURRENT_LINK=$ROOT/current; RELEASES=$ROOT/releases; STATE=$ROOT/systemctl; ACTIONS=$ROOT/actions
        DEPLOY_STATE=$ROOT/deploy; AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence; mkdir -p "$DEPLOY_STATE"
        mkdir -p "$SYSTEMD_DIR" "$CONFIG_DIR" "$WORKSPACE_ROOT" "$STATE"
        printf 'old-public\n' > "$SYSTEMD_DIR/opensandbox-gateway.service"
        printf 'old-helper\n' > "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        printf 'old-config\n' > "$CONFIG_DIR/gateway.env"; printf 'acl-old\n' > "$ROOT/acl.current"
        printf '{old}\n' > "$AUTHORITY_SHA_STATE"
        printf 'ls-remote-old\n' > "$AUTHORITY_EVIDENCE_STATE"
        rm -f "$CURRENT_LINK"; ln -s releases/{old} "$CURRENT_LINK"
        : > "$STATE/opensandbox-gateway.service.active"; : > "$STATE/opensandbox-gateway.service.enabled"
        : > "$STATE/opensandbox-gateway-helper.service.active"; : > "$STATE/opensandbox-gateway-helper.service.enabled"
        require_root_tree() {{ test -d "$1" && test ! -L "$1"; }}
        write_manifest() {{ : > "$1/MANIFEST.sha256"; }}
        verify_manifest() {{ test -f "$1/MANIFEST.sha256"; }}
        validate_release() {{ is_commit "$1"; }}
        chown() {{ :; }}
        stat() {{
          if test "$1" = -c; then
            case "${{@: -1}}" in
              "$DEPLOY_STATE") echo 0:0:700 ;;
              "$AUTHORITY_SHA_STATE"|"$AUTHORITY_EVIDENCE_STATE") echo 0:0:600 ;;
              *) echo 0 ;;
            esac
            return
          fi
          command stat "$@"
        }}
        install() {{ if test "$1" = -d; then mkdir -p "${{@: -1}}"; else cp "${{@: -2:1}}" "${{@: -1}}"; fi; }}
        getfacl() {{ cat "$ROOT/acl.current"; }}
        setfacl() {{ cp "${{1#--restore=}}" "$ROOT/acl.current"; }}
        systemctl() {{
          action=$1; unit=${{@: -1}}; printf '%s:%s:%s\n' "$action" "$unit" "$(readlink "$CURRENT_LINK" 2>/dev/null || true)" >> "$ACTIONS"
          case "$action" in is-active) test -f "$STATE/$unit.active";; is-enabled) test -f "$STATE/$unit.enabled";; enable) : > "$STATE/$unit.enabled";; disable) rm -f "$STATE/$unit.enabled";; restart) : > "$STATE/$unit.active";; stop) rm -f "$STATE/$unit.active";; daemon-reload) :;; esac
        }}
        snapshot_state "$ROOT/snapshot"
        printf 'new-public\n' > "$SYSTEMD_DIR/opensandbox-gateway.service"
        printf 'new-helper\n' > "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        printf 'new-config\n' > "$CONFIG_DIR/gateway.env"; printf 'acl-new\n' > "$ROOT/acl.current"
        rm "$CURRENT_LINK"; ln -s releases/{new} "$CURRENT_LINK"
        restore_snapshot "$ROOT/snapshot"
        grep -qx old-public "$SYSTEMD_DIR/opensandbox-gateway.service"
        grep -qx old-helper "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        grep -qx old-config "$CONFIG_DIR/gateway.env"; grep -qx acl-old "$ROOT/acl.current"
        test "$(readlink "$CURRENT_LINK")" = releases/{old}
        grep -qx '{old}' "$AUTHORITY_SHA_STATE"
        grep -qx 'ls-remote-old' "$AUTHORITY_EVIDENCE_STATE"
        grep '^restart:.*:releases/{new}$' "$ACTIONS" >/dev/null
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_release_validation_enforces_commit_owner_symlink_manifest_and_main(tmp_path) -> None:
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/install-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        test "$(is_commit 0123456789012345678901234567890123456789; echo $?)" = 0
        ! is_commit 012345678901234567890123456789012345678
        ! is_commit 01234567890123456789012345678901234567890
        ! is_commit z123456789012345678901234567890123456789
        mkdir "$ROOT/tree"
        OWNER=0; FIND_RESULT=
        stat() { echo "$OWNER"; }
        find() { test -z "$FIND_RESULT" || printf '%s\n' "$FIND_RESULT"; }
        require_root_tree "$ROOT/tree"
        OWNER=1000; ! require_root_tree "$ROOT/tree"
        OWNER=0; FIND_RESULT=symlink; ! require_root_tree "$ROOT/tree"
        unset -f stat find
        git init --bare "$ROOT/remote.git" >/dev/null
        git init "$ROOT/source" >/dev/null
        git -C "$ROOT/source" config user.email gateway@example.invalid
        git -C "$ROOT/source" config user.name gateway-test
        printf 'one\n' > "$ROOT/source/payload"; git -C "$ROOT/source" add payload; git -C "$ROOT/source" commit -m one >/dev/null
        git -C "$ROOT/source" branch -M main; git -C "$ROOT/source" remote add origin "$ROOT/remote.git"; git -C "$ROOT/source" push -u origin main >/dev/null
        COMMIT=$(git -C "$ROOT/source" rev-parse HEAD); RELEASES=$ROOT/releases; mkdir -p "$RELEASES/$COMMIT"
        EXPECTED_AUTHORITY_SHA=$COMMIT; AUTHORITY_EVIDENCE_ID=ls-remote-initial
        printf '%s\n' "$COMMIT" > "$RELEASES/$COMMIT/SOURCE_COMMIT"
        printf '%s\n' "$ROOT/source" > "$RELEASES/$COMMIT/SOURCE_ROOT"
        printf 'origin/main\n' > "$RELEASES/$COMMIT/AUTHORITY_REF"
        printf '%s\n' "$COMMIT" > "$RELEASES/$COMMIT/AUTHORITY_COMMIT"
        printf '%s\n' "$AUTHORITY_EVIDENCE_ID" > "$RELEASES/$COMMIT/AUTHORITY_EVIDENCE_ID"
        printf 'sealed\n' > "$RELEASES/$COMMIT/payload"
        chown() { :; }; require_root_tree() { test -d "$1" && test ! -L "$1"; }
        write_manifest "$RELEASES/$COMMIT"; validate_release "$COMMIT" exact
        test "$(require_exact_authority_head "$ROOT/source" origin/main "$COMMIT")" = "$COMMIT"
        printf 'tampered\n' >> "$RELEASES/$COMMIT/payload"; ! validate_release "$COMMIT"
        printf 'sealed\n' > "$RELEASES/$COMMIT/payload"; write_manifest "$RELEASES/$COMMIT"
        git -C "$ROOT/source" checkout -- payload
        printf 'two\n' >> "$ROOT/source/payload"; git -C "$ROOT/source" commit -am two >/dev/null
        git -C "$ROOT/source" push origin main >/dev/null
        git -C "$ROOT/source" fetch origin main >/dev/null
        validate_release "$COMMIT" rollback
        ! validate_release "$COMMIT" exact
        printf 'three\n' >> "$ROOT/source/payload"; git -C "$ROOT/source" commit -am three >/dev/null
        UNPUSHED=$(git -C "$ROOT/source" rev-parse HEAD); mkdir "$RELEASES/$UNPUSHED"
        printf '%s\n' "$UNPUSHED" > "$RELEASES/$UNPUSHED/SOURCE_COMMIT"
        printf '%s\n' "$ROOT/source" > "$RELEASES/$UNPUSHED/SOURCE_ROOT"
        printf 'origin/main\n' > "$RELEASES/$UNPUSHED/AUTHORITY_REF"
        printf '%s\n' "$UNPUSHED" > "$RELEASES/$UNPUSHED/AUTHORITY_COMMIT"
        printf 'ls-remote-unpushed\n' > "$RELEASES/$UNPUSHED/AUTHORITY_EVIDENCE_ID"
        printf 'sealed\n' > "$RELEASES/$UNPUSHED/payload"
        write_manifest "$RELEASES/$UNPUSHED"; ! validate_release "$UNPUSHED"
        CURRENT_AUTHORITY=$(git -C "$ROOT/source" rev-parse refs/remotes/origin/main)
        ! require_exact_authority_head "$ROOT/source" origin/main "$CURRENT_AUTHORITY"
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_installer_requires_fresh_expected_authority_and_records_evidence(tmp_path) -> None:
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/install-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        git init --bare "$ROOT/remote.git" >/dev/null
        git init "$ROOT/source" >/dev/null
        git -C "$ROOT/source" config user.email gateway@example.invalid
        git -C "$ROOT/source" config user.name gateway-test
        printf 'one\n' > "$ROOT/source/payload"; git -C "$ROOT/source" add payload
        git -C "$ROOT/source" commit -m one >/dev/null; git -C "$ROOT/source" branch -M main
        git -C "$ROOT/source" remote add origin "$ROOT/remote.git"; git -C "$ROOT/source" push -u origin main >/dev/null
        FIRST=$(git -C "$ROOT/source" rev-parse HEAD)
        test "$(require_exact_authority_head "$ROOT/source" origin/main "$FIRST")" = "$FIRST"

        git clone -b main "$ROOT/remote.git" "$ROOT/publisher" >/dev/null
        git -C "$ROOT/publisher" config user.email gateway@example.invalid
        git -C "$ROOT/publisher" config user.name gateway-test
        printf 'two\n' >> "$ROOT/publisher/payload"; git -C "$ROOT/publisher" commit -am two >/dev/null
        git -C "$ROOT/publisher" push origin main >/dev/null
        FRESH=$(git -C "$ROOT/source" ls-remote origin refs/heads/main | awk 'NR == 1 {print $1}')
        ! require_exact_authority_head "$ROOT/source" origin/main "$FRESH"
        git -C "$ROOT/source" fetch origin main >/dev/null
        ! require_exact_authority_head "$ROOT/source" origin/main "$FRESH"
        git -C "$ROOT/source" checkout --detach "$FRESH" >/dev/null
        test "$(require_exact_authority_head "$ROOT/source" origin/main "$FRESH")" = "$FRESH"

        DEPLOY_STATE=$ROOT/deploy; mkdir "$DEPLOY_STATE"
        AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
        chown() { :; }; chmod() { :; }
        record_authority_state "$FRESH" "ls-remote-20260724T010203Z-$FRESH"
        grep -qx "$FRESH" "$AUTHORITY_SHA_STATE"
        grep -qx "ls-remote-20260724T010203Z-$FRESH" "$AUTHORITY_EVIDENCE_STATE"
        ! record_authority_state "$FRESH" 'invalid evidence with spaces'
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_installer_restore_failure_preserves_backup_and_stops_partial_mutation(tmp_path) -> None:
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/install-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        BACKUP=$ROOT/unique-backup; STAGE=$ROOT/stage; RESTORE_FROM=$BACKUP; SUCCESS=0
        mkdir "$BACKUP" "$STAGE"
        restore_snapshot() { return 1; }
        set +e; ( cleanup_install ) >/dev/null 2>&1; STATUS=$?; set -e
        test "$STATUS" -eq 125
        test -d "$BACKUP" && test -d "$STAGE"

        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        SYSTEMD_DIR=$ROOT/systemd; CONFIG_DIR=$ROOT/config; WORKSPACE_ROOT=$ROOT/workspaces
        CURRENT_LINK=$ROOT/current; RELEASES=$ROOT/releases; DEPLOY_STATE=$ROOT/deploy
        AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
        mkdir -p "$SYSTEMD_DIR" "$WORKSPACE_ROOT" "$RELEASES" "$DEPLOY_STATE"
        ACTIONS=$ROOT/actions; : > "$ACTIONS"
        preflight_snapshot() { :; }
        rm() {
          printf 'rm:%s\n' "$*" >> "$ACTIONS"
          case "$*" in *opensandbox-gateway-helper.service*) return 1;; esac
        }
        setfacl() { printf 'setfacl\n' >> "$ACTIONS"; }
        systemctl() { printf 'systemctl:%s\n' "$*" >> "$ACTIONS"; }
        set +e; restore_snapshot "$BACKUP"; STATUS=$?; set -e
        test "$STATUS" -ne 0
        test "$(grep -c '^rm:' "$ACTIONS")" -eq 2
        ! grep -q '^setfacl\|^systemctl' "$ACTIONS"
        test -d "$BACKUP"
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_rollback_preflight_rejects_corrupt_historical_release_before_mutation(tmp_path) -> None:
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/rollback-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^rollback_main "\$@"$/d' "$SCRIPT")"
        SNAPSHOT=$ROOT/snapshot; mkdir "$SNAPSHOT"
        for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
          : > "$SNAPSHOT/$unit.absent"; : > "$SNAPSHOT/$unit.inactive"; : > "$SNAPSHOT/$unit.disabled"
        done
        : > "$SNAPSHOT/config.absent"; printf 'acl\n' > "$SNAPSHOT/workspaces.acl"
        printf 'releases/1111111111111111111111111111111111111111\n' > "$SNAPSHOT/current"
        printf '1111111111111111111111111111111111111111\n' > "$SNAPSHOT/authority-sha"
        printf 'ls-remote-corrupt\n' > "$SNAPSHOT/authority-evidence"
        : > "$SNAPSHOT/MANIFEST.sha256"
        require_root_tree() { :; }; verify_manifest() { :; }; validate_release() { return 1; }
        MUTATED=$ROOT/mutated
        if preflight_snapshot "$SNAPSHOT"; then : > "$MUTATED"; fi
        test ! -e "$MUTATED"
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_rollback_restores_first_install_absence_from_root_only_snapshot(tmp_path) -> None:
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/rollback-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^rollback_main "\$@"$/d' "$SCRIPT")"
        SYSTEMD_DIR=$ROOT/systemd; CONFIG_DIR=$ROOT/config; WORKSPACE_ROOT=$ROOT/workspaces
        CURRENT_LINK=$ROOT/current; RELEASES=$ROOT/releases; DEPLOY_STATE=$ROOT/deploy
        ROLLBACK_POINTER=$DEPLOY_STATE/previous-snapshot; STATE=$ROOT/systemctl
        AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
        SNAPSHOT_ID=.rollback.contract; SNAPSHOT=$DEPLOY_STATE/snapshots/$SNAPSHOT_ID
        mkdir -p "$SYSTEMD_DIR" "$CONFIG_DIR" "$WORKSPACE_ROOT" "$RELEASES" "$SNAPSHOT" "$STATE"
        EXPECTED_AUTHORITY_SHA=2222222222222222222222222222222222222222
        AUTHORITY_EVIDENCE_ID=ls-remote-current
        mkdir "$RELEASES/$EXPECTED_AUTHORITY_SHA"; : > "$CURRENT_LINK"
        printf '%s\n' "$SNAPSHOT_ID" > "$ROLLBACK_POINTER"
        : > "$SNAPSHOT/opensandbox-gateway.service.absent"
        : > "$SNAPSHOT/opensandbox-gateway-helper.service.absent"
        : > "$SNAPSHOT/opensandbox-gateway.service.inactive"
        : > "$SNAPSHOT/opensandbox-gateway-helper.service.inactive"
        : > "$SNAPSHOT/opensandbox-gateway.service.disabled"
        : > "$SNAPSHOT/opensandbox-gateway-helper.service.disabled"
        : > "$SNAPSHOT/config.absent"; : > "$SNAPSHOT/authority-sha.absent"
        : > "$SNAPSHOT/authority-evidence.absent"; printf 'acl-original\n' > "$SNAPSHOT/workspaces.acl"
        : > "$SNAPSHOT/current.absent"; : > "$SNAPSHOT/MANIFEST.sha256"
        printf 'new-public\n' > "$SYSTEMD_DIR/opensandbox-gateway.service"
        printf 'new-helper\n' > "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        printf 'new-config\n' > "$CONFIG_DIR/gateway.env"; printf 'acl-new\n' > "$ROOT/acl.current"
        printf '%040d\n' 1 > "$AUTHORITY_SHA_STATE"
        printf 'current-evidence\n' > "$AUTHORITY_EVIDENCE_STATE"
        id() { test "$1" = -u && echo 0; }
        test() {
          if builtin test "$#" -eq 2 && builtin test "$1" = -L && builtin test "$2" = "$CURRENT_LINK"; then return 0; fi
          builtin test "$@"
        }
        readlink() {
          if builtin test "$#" -eq 1 && builtin test "$1" = "$CURRENT_LINK"; then
            printf 'releases/%s\n' "$EXPECTED_AUTHORITY_SHA"
          else
            command readlink "$@"
          fi
        }
        stat() { target=${@: -1}; case "$target" in "$DEPLOY_STATE") echo 0:0:700;; "$ROLLBACK_POINTER") echo 0:0:600;; *) command stat "$@";; esac; }
        flock() { :; }; require_root_tree() { test -d "$1" && test ! -L "$1"; }; verify_manifest() { test -f "$1/MANIFEST.sha256"; }
        validate_release() { :; }
        install() { if test "$1" = -d; then mkdir -p "${@: -1}"; else cp "${@: -2:1}" "${@: -1}"; fi; }
        setfacl() { cp "${1#--restore=}" "$ROOT/acl.current"; }
        systemctl() { case "$1" in is-active) test "${@: -1}" = opensandbox.service;; disable|stop|daemon-reload) :;; *) :;; esac; }
        ss() { printf 'LISTEN 0 128 127.0.0.1:8080 0.0.0.0:*\n'; }
        rollback_main
        test ! -e "$SYSTEMD_DIR/opensandbox-gateway.service"
        test ! -e "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        test ! -e "$CONFIG_DIR" && test ! -e "$CURRENT_LINK"
        test ! -e "$AUTHORITY_SHA_STATE"
        test ! -e "$AUTHORITY_EVIDENCE_STATE"
        grep -qx acl-original "$ROOT/acl.current"
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_rollback_requires_fresh_authority_and_rejects_stale_or_rewritten_main(tmp_path) -> None:
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy/opensandbox/rollback-s72.sh"
    result = _run_gateway_bash_contract(
        script,
        tmp_path,
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1"); ROOT=$(cygpath -u "$2")
        eval "$(sed '/^rollback_main "\$@"$/d' "$SCRIPT")"
        git init "$ROOT/source" >/dev/null
        git -C "$ROOT/source" config user.email gateway@example.invalid
        git -C "$ROOT/source" config user.name gateway-test
        printf 'one\n' > "$ROOT/source/payload"; git -C "$ROOT/source" add payload
        git -C "$ROOT/source" commit -m one >/dev/null; FIRST=$(git -C "$ROOT/source" rev-parse HEAD)
        printf 'two\n' >> "$ROOT/source/payload"; git -C "$ROOT/source" commit -am two >/dev/null
        FRESH=$(git -C "$ROOT/source" rev-parse HEAD)
        git -C "$ROOT/source" update-ref refs/remotes/origin/main "$FRESH"
        RELEASES=$ROOT/releases; mkdir -p "$RELEASES/$FIRST" "$RELEASES/$FRESH"
        for COMMIT in "$FIRST" "$FRESH"; do
          printf '%s\n' "$COMMIT" > "$RELEASES/$COMMIT/SOURCE_COMMIT"
          printf '%s\n' "$ROOT/source" > "$RELEASES/$COMMIT/SOURCE_ROOT"
          printf 'origin/main\n' > "$RELEASES/$COMMIT/AUTHORITY_REF"
          printf '%s\n' "$COMMIT" > "$RELEASES/$COMMIT/AUTHORITY_COMMIT"
          printf 'original-release-evidence\n' > "$RELEASES/$COMMIT/AUTHORITY_EVIDENCE_ID"
          : > "$RELEASES/$COMMIT/MANIFEST.sha256"
        done
        require_root_tree() { test -d "$1" && test ! -L "$1"; }
        verify_manifest() { test -f "$1/MANIFEST.sha256"; }
        AUTHORITY_REF=origin/main; EXPECTED_AUTHORITY_SHA=$FRESH
        AUTHORITY_EVIDENCE_ID=ls-remote-fresh-$FRESH
        validate_release "$FRESH" rollback
        validate_release "$FIRST" rollback

        git -C "$ROOT/source" update-ref refs/remotes/origin/main "$FIRST"
        ! validate_release "$FIRST" rollback
        git -C "$ROOT/source" update-ref refs/remotes/origin/main "$FRESH"

        git -C "$ROOT/source" checkout --orphan rewritten >/dev/null
        git -C "$ROOT/source" rm -f payload >/dev/null
        printf 'rewritten\n' > "$ROOT/source/rewritten"; git -C "$ROOT/source" add rewritten
        git -C "$ROOT/source" commit -m rewritten >/dev/null
        REWRITTEN=$(git -C "$ROOT/source" rev-parse HEAD)
        git -C "$ROOT/source" update-ref refs/remotes/origin/main "$REWRITTEN"
        EXPECTED_AUTHORITY_SHA=$REWRITTEN; AUTHORITY_EVIDENCE_ID=ls-remote-rewritten-$REWRITTEN
        ! validate_release "$FIRST" rollback

        AUTHORITY_EVIDENCE_ID='invalid evidence with spaces'
        ! validate_release "$FRESH" rollback
        git -C "$ROOT/source" update-ref refs/remotes/origin/main "$FRESH"
        EXPECTED_AUTHORITY_SHA=$FRESH; AUTHORITY_EVIDENCE_ID=ls-remote-final-$FRESH
        DEPLOY_STATE=$ROOT/deploy; mkdir "$DEPLOY_STATE"
        AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
        chown() { :; }; chmod() { :; }
        record_authority_state "$FIRST" "$AUTHORITY_EVIDENCE_ID"
        grep -qx "$FIRST" "$AUTHORITY_SHA_STATE"
        grep -qx "$AUTHORITY_EVIDENCE_ID" "$AUTHORITY_EVIDENCE_STATE"
        ''',
    )
    assert result.returncode == 0, result.stderr or result.stdout
