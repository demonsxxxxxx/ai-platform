from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime.sandbox.opensandbox_policy import governed_opensandbox_egress_bases
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
    callback_token_id_matches_binding,
    callback_token_matches,
    derive_callback_token,
)
from services.opensandbox_gateway.adapters import (
    BrokerPolicy,
    InMemoryLifecycleTransport,
    InMemoryRuntimeAdapter,
    InMemoryStateStore,
)
from services.opensandbox_gateway.gateway import (
    GatewayApplication,
    GatewayConfig,
    GatewayError,
    LeaseRecord,
    RuntimeEvidence,
)
from services.opensandbox_gateway.model_credentials import model_id_sha256


IMAGE = "registry.example/executor@sha256:" + "1" * 64
LOOPBACK_ORIGIN = "http://127.0.0.1:18043"


def _gateway_config() -> GatewayConfig:
    return GatewayConfig(
        lifecycle_api_key="lifecycle-" + "a" * 32,
        capability_bearer_token="capability-" + "b" * 32,
        record_signing_key=b"signing-" + b"c" * 40,
        proof_key_id="proof-v1",
        profile_id="s72-runsc-none-v1",
        public_authority="10.56.0.72:8443",
        lifecycle_endpoint="http://127.0.0.1:8080",
        executor_image=IMAGE,
        runtime_subject="s72/runsc/v1",
        gateway_policy_subject="s72/gateway/loopback-v1",
        callback_boundary_subject="ai-platform/callbacks/v1",
        deny_audit_subject="s72/deny-audit/v1",
        deny_counter_subject="s72/deny-counter/v1",
        callback_upstream_base=LOOPBACK_ORIGIN,
        openai_upstream_base=f"{LOOPBACK_ORIGIN}/openai/v1",
        anthropic_upstream_base=f"{LOOPBACK_ORIGIN}/anthropic",
        upstream_transport="loopback_http",
    )


def test_loopback_broker_preserves_exact_origin_and_rejects_control_plane_targets() -> None:
    config = _gateway_config()
    config.validate()
    policy = BrokerPolicy(
        {
            "version": 1,
            "targets": {
                "callback": {"base_url": config.callback_upstream_base, "expected_ips": ["127.0.0.1"]},
                "openai": {"base_url": config.openai_upstream_base, "expected_ips": ["127.0.0.1"]},
                "anthropic": {"base_url": config.anthropic_upstream_base, "expected_ips": ["127.0.0.1"]},
            },
        }
    )
    assert policy.transport == "loopback_http"
    for target in ("postgres:5432", "redis:6379", "minio:9000", "host.docker.internal:18043"):
        with pytest.raises((ValueError, GatewayError)):
            replace(config, callback_upstream_base=f"http://{target}").validate()


def test_platform_policy_accepts_only_the_same_loopback_origin() -> None:
    settings = SimpleNamespace(
        opensandbox_external_egress_callback_base_url=LOOPBACK_ORIGIN,
        opensandbox_external_egress_openai_base_url=f"{LOOPBACK_ORIGIN}/openai/v1",
        opensandbox_external_egress_anthropic_base_url=f"{LOOPBACK_ORIGIN}/anthropic",
    )
    bases = governed_opensandbox_egress_bases(settings)
    assert bases.callback_base_url == LOOPBACK_ORIGIN
    settings.opensandbox_external_egress_openai_base_url = "http://127.0.0.1:5432/openai/v1"
    with pytest.raises(ValueError, match="origin drift"):
        governed_opensandbox_egress_bases(settings)


def test_callback_token_is_attempt_bound_and_cross_attempt_replay_fails() -> None:
    secret = "callback-secret"
    first = CallbackTokenBinding(run_id="run-a", attempt_id="attempt-a")
    second = CallbackTokenBinding(run_id="run-a", attempt_id="attempt-b")
    token_id = callback_token_id_for_binding(first)
    token = derive_callback_token(secret, token_id)
    assert callback_token_matches(secret=secret, token_id=token_id, provided_token=token)
    assert callback_token_id_matches_binding(token_id, first)
    assert not callback_token_id_matches_binding(token_id, second)


def _lease(attempt_id: str) -> LeaseRecord:
    return LeaseRecord(
        sandbox_id="sandbox-a",
        scope={"attempt_id": attempt_id},
        metadata={
            "ai-platform.model_id_sha256": model_id_sha256("model-a"),
            "ai-platform.skill_mount.fingerprint": "",
        },
        image=IMAGE,
        image_digest=IMAGE.rsplit("@", 1)[1],
        workspace_host_path="/data/opensandbox/workspaces/attempt-a",
        mounts=[],
        canonical_request_hash=hashlib.sha256(b"request").hexdigest(),
        executor_token_hash=hashlib.sha256(b"token").hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(),
        state="active",
        signature="signature",
    )


def test_model_route_receipt_rejects_cross_attempt_and_same_attempt_replay() -> None:
    store = InMemoryStateStore()
    store.save(_lease("attempt-a"))
    request = {
        "sandbox_id": "sandbox-a",
        "request_id": "a" * 32,
        "provider": "openai",
        "method": "POST",
        "path": "/responses",
        "model": "model-a",
        "created_at": 10.0,
        "now": 11.0,
        "ttl_seconds": 15.0,
        "request_limit": 8,
        "attempt_id": "attempt-a",
    }
    store.consume_model_route(**request)
    with pytest.raises(GatewayError, match="model_route_replayed"):
        store.consume_model_route(**request)
    with pytest.raises(GatewayError, match="model_route_attempt_mismatch"):
        store.consume_model_route(**{**request, "request_id": "b" * 32, "attempt_id": "attempt-b"})


@pytest.mark.parametrize(
    "host_path",
    [
        "/var/run/docker.sock",
        "/etc/ai-platform/model-secrets/openai-api-key",
        "/var/lib/docker/volumes/ai_platform_postgres/_data",
        "/var/lib/docker/volumes/ai_platform_redis/_data",
        "/var/lib/docker/volumes/ai_platform_minio/_data",
    ],
)
def test_hostile_sandbox_mounts_never_reach_the_lifecycle_upstream(host_path: str) -> None:
    app = GatewayApplication(
        _gateway_config(),
        InMemoryLifecycleTransport(),
        InMemoryRuntimeAdapter(),
        InMemoryStateStore(),
    )
    scope = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
    }
    with pytest.raises(GatewayError, match="host_path_not_scoped"):
        app._accept_volumes(
            [{"name": "ai-platform-workspace", "mountPath": "/workspace", "host": host_path}],
            scope,
            {"ai-platform.skill_mount.required": "false", "ai-platform.skill_mount.fingerprint": ""},
        )


def test_host_network_attestation_fails_closed_before_dispatch() -> None:
    app = GatewayApplication(
        _gateway_config(),
        InMemoryLifecycleTransport(),
        InMemoryRuntimeAdapter(),
        InMemoryStateStore(),
    )
    record = _lease("attempt-a")
    evidence = RuntimeEvidence(
        sandbox_id=record.sandbox_id,
        runtime="runsc",
        network_mode="host",
        no_new_privileges=True,
        user="1000:1000",
        uid="1000",
        gid="1000",
        image=record.image,
        image_digest=record.image_digest,
        mounts=(),
        labels=record.metadata,
        skill_mount_fingerprint="",
    )
    with pytest.raises(GatewayError, match="runtime_attestation_drift"):
        app._validate_evidence(record, evidence)


def test_runsc_attestation_contract_excludes_host_network_socket_and_control_plane_volumes() -> None:
    source = (Path(__file__).resolve().parents[1] / "services/opensandbox_gateway/gateway.py").read_text(
        encoding="utf-8"
    )
    assert 'runtime != "runsc"' in source
    assert 'network_mode != "none"' in source
    assert "no_new_privileges" in source
    assert '"/var/run/docker.sock"' not in source
    assert '"ai_platform_postgres"' not in source
    assert '"ai_platform_redis"' not in source
    assert '"ai_platform_minio"' not in source
    root = Path(__file__).resolve().parents[1]
    server_unit = (root / "deploy/opensandbox/opensandbox-s72.service").read_text(encoding="utf-8")
    server_config = (root / "deploy/opensandbox/server-s72.toml.example").read_text(encoding="utf-8")
    overlay = (root / "deploy/ai-platform/docker-compose.s72-colocation.yml").read_text(encoding="utf-8")
    assert "/var/run/docker.sock" in server_unit
    assert "release-role=opensandbox-server" in server_unit
    assert "/var/run/docker.sock" not in overlay
    assert 'network_mode = "none"' in server_config
    assert 'docker_runtime = "runsc"' in server_config
