import hashlib
import hmac
import json
import tomllib
import time
import urllib.parse
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from app.runtime.sandbox import opensandbox_attestation
from app.runtime.sandbox.opensandbox_policy import (
    OpenSandboxProfileConfigurationError,
    governed_opensandbox_egress_bases,
)
from app.settings import Settings
import services.opensandbox_gateway.adapters as gateway_adapters
from services.opensandbox_gateway.adapters import BrokerPolicy, MailboxBroker
from services.opensandbox_gateway.gateway import GatewayError, MonotonicDeadline


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.yml"
OPENSANDBOX_COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.opensandbox.yml"
S72_COLOCATION_COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.s72-colocation.yml"
S72_BROKER_NGINX = ROOT / "deploy" / "ai-platform" / "s72-broker-nginx.conf.template"
S72_OPENSANDBOX_SERVICE = ROOT / "deploy" / "opensandbox" / "opensandbox-s72.service"
S72_SERVER_ENV = ROOT / "deploy" / "opensandbox" / "server-s72.env.example"
S72_SERVER_CONFIG = ROOT / "deploy" / "opensandbox" / "server-s72.toml.example"
ENV_EXAMPLE = ROOT / "deploy" / "ai-platform" / ".env.example"
IMAGE_DIGEST = "sha256:" + "a" * 64
IMAGE_SUBJECT = f"registry.example/team/ai-platform@{IMAGE_DIGEST}"
PROOF_SIGNING_KEY = "attestation-proof-signing-key-with-enough-entropy-2026"


def attestation_settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "opensandbox_protocol": "https",
        "opensandbox_domain": "opensandbox.internal:8080",
        "opensandbox_api_key": "lifecycle-api-key",
        "opensandbox_attestation_path": opensandbox_attestation.OPENSANDBOX_ATTESTATION_PATH,
        "opensandbox_attestation_contract_version": (
            opensandbox_attestation.OPENSANDBOX_ATTESTATION_CONTRACT_VERSION
        ),
        "opensandbox_attestation_timeout_seconds": 2.0,
        "sandbox_runtime_subject": "runtime-subject-a",
        "opensandbox_external_egress_gateway_policy_subject": "gateway-policy-subject-a",
        "opensandbox_external_egress_callback_boundary_subject": "callback-boundary-subject-a",
        "opensandbox_external_egress_callback_base_url": "https://bridge.internal.example:18443",
        "opensandbox_external_egress_openai_base_url": "https://bridge.internal.example:18443/openai/v1",
        "opensandbox_external_egress_anthropic_base_url": "https://bridge.internal.example:18443/anthropic",
        "sandbox_egress_proof_key_id": "proof-key-a",
        "sandbox_egress_proof_signing_key": PROOF_SIGNING_KEY,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_governed_bridge_accepts_only_one_exact_loopback_http_origin() -> None:
    settings = attestation_settings(
        opensandbox_external_egress_callback_base_url="http://127.0.0.1:18043",
        opensandbox_external_egress_openai_base_url="http://127.0.0.1:18043/openai/v1",
        opensandbox_external_egress_anthropic_base_url="http://127.0.0.1:18043/anthropic",
    )
    bases = governed_opensandbox_egress_bases(settings)
    assert bases.callback_base_url == "http://127.0.0.1:18043"

    settings.opensandbox_external_egress_openai_base_url = "https://127.0.0.1:18043/openai/v1"
    with pytest.raises(OpenSandboxProfileConfigurationError, match="bridge"):
        governed_opensandbox_egress_bases(settings)


@pytest.mark.parametrize("port", (80, 18042, 18044))
def test_governed_bridge_rejects_noncanonical_loopback_port(port: int) -> None:
    origin = f"http://127.0.0.1:{port}"
    settings = attestation_settings(
        opensandbox_external_egress_callback_base_url=origin,
        opensandbox_external_egress_openai_base_url=origin + "/openai/v1",
        opensandbox_external_egress_anthropic_base_url=origin + "/anthropic",
    )

    with pytest.raises(OpenSandboxProfileConfigurationError, match="bridge"):
        governed_opensandbox_egress_bases(settings)


def broker_policy(origin: str) -> BrokerPolicy:
    return BrokerPolicy(
        {
            "version": 1,
            "targets": {
                kind: {"base_url": value, "expected_ips": ["127.0.0.1"]}
                for kind, value in {
                    "callback": origin,
                    "openai": origin + "/openai/v1",
                    "anthropic": origin + "/anthropic",
                }.items()
            },
        }
    )


@pytest.mark.parametrize("port", (80, 18042, 18044))
def test_loopback_broker_policy_rejects_noncanonical_port(port: int) -> None:
    with pytest.raises(ValueError, match="broker"):
        broker_policy(f"http://127.0.0.1:{port}")


@pytest.mark.parametrize("port", (80, 18042, 18044))
def test_loopback_mailbox_rejects_connection_target_port_drift(monkeypatch, port: int) -> None:
    broker = MailboxBroker(SimpleNamespace(), broker_policy("http://127.0.0.1:18043"), 1.0, 1024)
    connections: list[tuple[str, int]] = []

    def connect(host: str, selected_port: int, timeout: float):
        del timeout
        connections.append((host, selected_port))
        return SimpleNamespace()

    monkeypatch.setattr(gateway_adapters.http.client, "HTTPConnection", connect)
    with pytest.raises(GatewayError, match="broker_policy_invalid"):
        broker._upstream_connection(
            urllib.parse.urlsplit(f"http://127.0.0.1:{port}"),
            ("127.0.0.1",),
            MonotonicDeadline.after(1.0),
        )
    assert connections == []


def capability(**overrides: Any) -> SimpleNamespace:
    values = {
        "profile_id": "profile-a",
        "runtime_identity": "runsc",
        "runtime_subject": "runtime-subject-a",
        "gateway_policy_subject": "gateway-policy-subject-a",
        "callback_boundary_subject": "callback-boundary-subject-a",
        "deny_audit_subject": "deny-audit-subject-a",
        "deny_counter_subject": "deny-counter-subject-a",
        "requested_image": IMAGE_SUBJECT,
        "requested_image_digest": IMAGE_DIGEST,
        "upstream_bridge_version": "v1",
        "callback_base_url": "https://bridge.internal.example:18443",
        "openai_base_url": "https://bridge.internal.example:18443/openai/v1",
        "anthropic_base_url": "https://bridge.internal.example:18443/anthropic",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def runtime_request(**overrides: Any) -> SimpleNamespace:
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "qat-attempt-a",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def attestation_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "contract_version": opensandbox_attestation.OPENSANDBOX_ATTESTATION_CONTRACT_VERSION,
        "provider": "opensandbox",
        "sandbox_id": "sandbox-a",
        "scope_labels": {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "attempt_id": "qat-attempt-a",
            "lease_id": "opensandbox:opensandbox-run-a-qat-attempt-a:sandbox-a",
        },
        "runtime": {
            "identity": "runsc",
            "subject": "runtime-subject-a",
        },
        "network": {
            "mode": "none",
            "default_deny": True,
        },
        "security": {
            "no_new_privileges": True,
            "user": "1000:1000",
            "uid": "1000",
            "gid": "1000",
        },
        "image": {
            "subject": IMAGE_SUBJECT,
            "digest": IMAGE_DIGEST,
        },
        "host_path_policy": {
            "subject": "scoped-workspace-only",
            "unscoped_host_paths_allowed": False,
        },
        "upstream_bridge": {
            "version": "v1",
            "callback_base_url": "https://bridge.internal.example:18443",
            "openai_base_url": "https://bridge.internal.example:18443/openai/v1",
            "anthropic_base_url": "https://bridge.internal.example:18443/anthropic",
        },
        "subjects": {
            "gateway_policy": "gateway-policy-subject-a",
            "callback_boundary": "callback-boundary-subject-a",
            "capability": "profile-a",
            "deny_audit": "deny-audit-subject-a",
            "deny_counter": "deny-counter-subject-a",
        },
        "signed_profile": {
            "id": "profile-a",
            "version": "v1",
            "proof_key_id": "proof-key-a",
        },
    }
    payload["signed_profile"]["profile_signature"] = hmac.new(
        PROOF_SIGNING_KEY.encode("utf-8"),
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    payload.update(overrides)
    return payload


def set_nested(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def response(
    payload: object,
    *,
    status_code: int = 200,
    url: str = "https://opensandbox.internal:8080/v1/sandboxes/sandbox-a/attestation",
) -> opensandbox_attestation._TransportResponse:
    content = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return opensandbox_attestation._TransportResponse(
        status_code=status_code,
        url=url,
        content=content,
    )


@pytest.mark.asyncio
async def test_authenticated_attestor_accepts_exact_topology_contract() -> None:
    calls: list[tuple[str, dict[str, str], float]] = []

    def transport(url: str, headers: Any, timeout_seconds: float):
        calls.append((url, dict(headers), timeout_seconds))
        return response(attestation_payload(), url=url)

    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(),
        transport=transport,
    )

    assert probe is not None
    assert await probe(capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}) is True
    assert calls == [
        (
            "https://opensandbox.internal:8080/v1/sandboxes/sandbox-a/attestation",
            {
                "Accept": "application/json",
                "OPEN-SANDBOX-API-KEY": "lifecycle-api-key",
            },
            2.0,
        )
    ]


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("opensandbox_api_key", ""),
        ("opensandbox_attestation_path", ""),
        ("opensandbox_attestation_path", "/v1/sandboxes/{sandbox_id}"),
        ("opensandbox_attestation_contract_version", "unknown.v1"),
        ("opensandbox_attestation_timeout_seconds", 0),
        ("opensandbox_attestation_timeout_seconds", 5.1),
        ("opensandbox_external_egress_gateway_policy_subject", ""),
        ("opensandbox_external_egress_callback_boundary_subject", ""),
        ("opensandbox_external_egress_callback_base_url", ""),
        ("opensandbox_external_egress_openai_base_url", ""),
        ("opensandbox_external_egress_anthropic_base_url", ""),
        ("sandbox_runtime_subject", ""),
        ("sandbox_egress_proof_key_id", ""),
    ],
)
def test_attestor_is_not_built_for_incomplete_or_non_allowlisted_configuration(
    setting: str,
    value: object,
) -> None:
    assert (
        opensandbox_attestation.build_opensandbox_attestation_probe(
            attestation_settings(**{setting: value})
        )
        is None
    )


@pytest.mark.parametrize(
    ("protocol", "domain", "expected_base_url"),
    [
        ("http", "127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("http", "[::1]:8080", "http://[::1]:8080"),
        ("https", "10.56.0.72:8080", "https://10.56.0.72:8080"),
        ("https", "opensandbox.internal:8080", "https://opensandbox.internal:8080"),
    ],
)
def test_attestor_factory_accepts_only_canonical_loopback_http_or_https_endpoints(
    protocol: str,
    domain: str,
    expected_base_url: str,
) -> None:
    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(opensandbox_protocol=protocol, opensandbox_domain=domain)
    )

    assert probe is not None
    assert expected_base_url in repr(probe)


@pytest.mark.parametrize(
    ("protocol", "domain"),
    [
        ("http", "opensandbox.internal:8080"),
        ("http", "10.56.0.72:8080"),
        ("http", "localhost:8080"),
        ("https", "169.254.169.254:8080"),
        ("https", "0.0.0.0:8080"),
        ("https", "[::]:8080"),
        ("https", "224.0.0.1:8080"),
        ("https", "240.0.0.1:8080"),
        ("https", "192.0.2.1:8080"),
        ("https", "198.18.0.1:8080"),
        ("https", "100.64.0.1:8080"),
        ("https", "[::ffff:127.0.0.1]:8080"),
        ("https", "[::ffff:8.8.8.8]:8080"),
        ("https", "[fec0::1]:8080"),
        ("http", "0x7f000001:8080"),
        ("http", "2130706433:8080"),
        ("http", "127.1:8080"),
        ("https", "OpenSandbox.internal:8080"),
        ("https", "tést.internal:8080"),
        ("https", "open_sandbox.internal:8080"),
    ],
)
def test_attestor_factory_rejects_unsafe_or_ambiguous_endpoint_before_transport(
    protocol: str,
    domain: str,
) -> None:
    calls = 0

    def transport(*_args: Any):
        nonlocal calls
        calls += 1
        return response(attestation_payload())

    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(opensandbox_protocol=protocol, opensandbox_domain=domain),
        transport=transport,
    )

    assert probe is None
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol", "domain"),
    [
        ("https", "opensandbox.internal:443"),
        ("http", "127.0.0.1:80"),
        ("https", "opensandbox.internal:8443"),
    ],
)
async def test_attestor_accepts_matching_effective_ports_after_httpx_url_normalization(
    protocol: str,
    domain: str,
) -> None:
    def transport(url: str, *_args: Any):
        return response(attestation_payload(), url=str(httpx.URL(url)))

    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(opensandbox_protocol=protocol, opensandbox_domain=domain),
        transport=transport,
    )

    assert probe is not None
    assert await probe(
        capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}
    ) is True


@pytest.mark.asyncio
async def test_attestor_rejects_changed_nondefault_port_after_httpx_url_normalization() -> None:
    def transport(url: str, *_args: Any):
        changed_port_url = url.replace(":8443/", ":9443/")
        return response(attestation_payload(), url=str(httpx.URL(changed_port_url)))

    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(opensandbox_domain="opensandbox.internal:8443"),
        transport=transport,
    )

    assert probe is not None
    assert await probe(
        capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "url"),
    [
        (302, "https://opensandbox.internal:8080/v1/sandboxes/sandbox-a/attestation"),
        (500, "https://opensandbox.internal:8080/v1/sandboxes/sandbox-a/attestation"),
        (200, "https://attacker.internal:8080/v1/sandboxes/sandbox-a/attestation"),
        (200, "http://opensandbox.internal:8080/v1/sandboxes/sandbox-a/attestation"),
        (200, "https://opensandbox.internal:8080/v1/sandboxes/other/attestation"),
    ],
)
async def test_attestor_rejects_redirect_non_success_and_endpoint_drift(
    status_code: int,
    url: str,
) -> None:
    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(),
        transport=lambda *_args: response(attestation_payload(), status_code=status_code, url=url),
    )

    assert probe is not None
    assert await probe(capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b'{"contract_version":"a","contract_version":"b"}',
        b'{"contract_version":NaN}',
    ],
)
async def test_attestor_rejects_non_json_duplicate_keys_and_non_finite_json(content: bytes) -> None:
    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(),
        transport=lambda *_args: response(content),
    )

    assert probe is not None
    assert await probe(capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}) is False


@pytest.mark.asyncio
async def test_attestor_enforces_bounded_transport_timeout_without_exposing_transport_detail() -> None:
    def timeout_transport(*_args: Any):
        time.sleep(0.2)
        raise TimeoutError("private lifecycle timeout detail")

    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(opensandbox_attestation_timeout_seconds=0.1),
        transport=timeout_transport,
    )

    assert probe is not None
    assert await probe(capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "mismatched_value"),
    [
        (("contract_version",), "unknown.v1"),
        (("sandbox_id",), "sandbox-b"),
        (("scope_labels", "tenant_id"), "tenant-b"),
        (("scope_labels", "workspace_id"), "workspace-b"),
        (("scope_labels", "user_id"), "user-b"),
        (("scope_labels", "session_id"), "session-b"),
        (("scope_labels", "run_id"), "run-b"),
        (("scope_labels", "lease_id"), "opensandbox:opensandbox-run-a:sandbox-b"),
        (("runtime", "identity"), "runc"),
        (("runtime", "subject"), "runtime-subject-b"),
        (("network", "mode"), "bridge"),
        (("network", "default_deny"), False),
        (("security", "no_new_privileges"), False),
        (("image", "subject"), "registry.example/team/other@" + IMAGE_DIGEST),
        (("image", "digest"), "sha256:" + "b" * 64),
        (("host_path_policy", "subject"), "unrestricted"),
        (("host_path_policy", "unscoped_host_paths_allowed"), True),
        (("subjects", "gateway_policy"), "gateway-policy-subject-b"),
        (("subjects", "callback_boundary"), "callback-boundary-subject-b"),
        (("subjects", "capability"), "profile-b"),
        (("subjects", "deny_audit"), "deny-audit-subject-b"),
        (("subjects", "deny_counter"), "deny-counter-subject-b"),
        (("signed_profile", "id"), "profile-b"),
        (("signed_profile", "version"), "v2"),
        (("signed_profile", "proof_key_id"), "proof-key-b"),
    ],
)
async def test_attestor_rejects_every_security_critical_mismatch(
    path: tuple[str, ...],
    mismatched_value: object,
) -> None:
    payload = attestation_payload()
    set_nested(payload, path, mismatched_value)
    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(),
        transport=lambda *_args: response(payload),
    )

    assert probe is not None
    assert await probe(capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["extra", "missing", "wrong_boolean_type"])
async def test_attestor_rejects_extra_missing_and_type_confused_fields(mutation: str) -> None:
    payload = deepcopy(attestation_payload())
    if mutation == "extra":
        payload["debug"] = {"private": True}
    elif mutation == "missing":
        del payload["network"]["default_deny"]
    else:
        payload["security"]["no_new_privileges"] = 1
    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(),
        transport=lambda *_args: response(payload),
    )

    assert probe is not None
    assert await probe(capability(), runtime_request(), "sandbox-a", {"id": "sandbox-a"}) is False


@pytest.mark.asyncio
async def test_attestor_rejects_sdk_info_or_configured_subject_drift_before_transport() -> None:
    calls = 0

    def transport(*_args: Any):
        nonlocal calls
        calls += 1
        return response(attestation_payload())

    probe = opensandbox_attestation.build_opensandbox_attestation_probe(
        attestation_settings(),
        transport=transport,
    )

    assert probe is not None
    assert await probe(capability(), runtime_request(), "sandbox-a", {"id": "sandbox-b"}) is False
    assert (
        await probe(
            capability(callback_boundary_subject="callback-boundary-subject-b"),
            runtime_request(),
            "sandbox-a",
            {"id": "sandbox-a"},
        )
        is False
    )
    assert calls == 0


def test_attestor_representation_redacts_api_key() -> None:
    probe = opensandbox_attestation.build_opensandbox_attestation_probe(attestation_settings())

    assert probe is not None
    assert "lifecycle-api-key" not in repr(probe)
    assert "<redacted>" in repr(probe)


def test_settings_and_compose_wire_complete_opensandbox_contract_for_api_and_worker() -> None:
    base_environment = {
        "SANDBOX_CONTAINER_PROVIDER",
        "SANDBOX_EGRESS_PROOF_KEY_ID",
        "SANDBOX_RUNTIME_SUBJECT",
        "OPENSANDBOX_DOMAIN",
        "OPENSANDBOX_PROTOCOL",
        "OPENSANDBOX_API_KEY",
        "OPENSANDBOX_USE_SERVER_PROXY",
        "OPENSANDBOX_EXECUTOR_IMAGE",
        "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST",
        "OPENSANDBOX_ATTESTATION_PATH",
        "OPENSANDBOX_ATTESTATION_CONTRACT_VERSION",
        "OPENSANDBOX_ATTESTATION_TIMEOUT_SECONDS",
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL",
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN",
        "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT",
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT",
    }
    bridge_environment = {
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BASE_URL",
        "OPENSANDBOX_EXTERNAL_EGRESS_OPENAI_BASE_URL",
        "OPENSANDBOX_EXTERNAL_EGRESS_ANTHROPIC_BASE_URL",
    }
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    opensandbox_compose = yaml.safe_load(OPENSANDBOX_COMPOSE.read_text(encoding="utf-8"))
    for service_name in ("api", "worker"):
        environment = compose["services"][service_name]["environment"]
        assert base_environment <= environment.keys()
        assert bridge_environment.isdisjoint(environment)
        assert environment["OPENSANDBOX_API_KEY"] == "${OPENSANDBOX_API_KEY:-}"
        assert environment["OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN"] == (
            "${OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN:-}"
        )
        assert environment["SANDBOX_CONTAINER_PROVIDER"] == "${SANDBOX_CONTAINER_PROVIDER:-fake}"
        overlay_environment = opensandbox_compose["services"][service_name]["environment"]
        assert overlay_environment["SANDBOX_CONTAINER_PROVIDER"] == "opensandbox"
        assert bridge_environment <= overlay_environment.keys()
        assert all(":?set " in overlay_environment[name] for name in bridge_environment)

    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    for name in base_environment:
        assert f"{name}=" in env_example
    expected_loopback = {
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BASE_URL": "http://127.0.0.1:18043",
        "OPENSANDBOX_EXTERNAL_EGRESS_OPENAI_BASE_URL": "http://127.0.0.1:18043/openai/v1",
        "OPENSANDBOX_EXTERNAL_EGRESS_ANTHROPIC_BASE_URL": "http://127.0.0.1:18043/anthropic",
    }
    for name, value in expected_loopback.items():
        assert f"{name}={value}" in env_example
    assert "AI_PLATFORM_S72_BRIDGE_" not in env_example
    assert "10.56." not in env_example and "211" not in env_example
    for safe_selection in (
        "WORKER_CLAUDE_AGENT_SDK_ENABLED=true",
        "CLAUDE_AGENT_PERMISSION_MODE=dontAsk",
        "CLAUDE_AGENT_DISALLOWED_TOOLS=Write,Edit,NotebookEdit",
        "SANDBOX_CONTAINER_PROVIDER=opensandbox",
        "AI_PLATFORM_MODEL_UPSTREAM=http://host.docker.internal:3002",
    ):
        assert safe_selection in env_example

    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_constructor(
        "!reset",
        lambda loader, node: loader.construct_sequence(node),
    )
    colocation = yaml.load(S72_COLOCATION_COMPOSE.read_text(encoding="utf-8"), Loader=ComposeLoader)
    for service_name in ("api", "worker"):
        environment = colocation["services"][service_name]["environment"]
        assert {name: environment[name] for name in bridge_environment} == expected_loopback
    assert "AI_PLATFORM_S72_BRIDGE" not in S72_COLOCATION_COMPOSE.read_text(encoding="utf-8")
    assert {
        "opensandbox_attestation_path",
        "opensandbox_attestation_contract_version",
        "opensandbox_attestation_timeout_seconds",
        "opensandbox_external_egress_callback_base_url",
        "opensandbox_external_egress_openai_base_url",
        "opensandbox_external_egress_anthropic_base_url",
    } <= Settings.model_fields.keys()


def test_s72_colocation_templates_keep_control_and_execution_domains_isolated() -> None:
    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_constructor(
        "!reset",
        lambda loader, node: loader.construct_sequence(node),
    )
    compose_text = S72_COLOCATION_COMPOSE.read_text(encoding="utf-8")
    compose = yaml.load(compose_text, Loader=ComposeLoader)
    broker = compose["services"]["s72-broker-entry"]
    assert broker["ports"] == ["127.0.0.1:18043:8080"]
    assert broker["read_only"] is True
    assert broker["user"] == "101:101"
    assert broker["cap_drop"] == ["ALL"]
    assert broker["security_opt"] == ["no-new-privileges:true"]
    assert "network_mode" not in broker
    assert all("docker.sock" not in value for value in broker.get("volumes", ()))
    assert compose["networks"]["s72_callback"]["internal"] is True
    for service_name in ("postgres", "redis", "minio", "api"):
        assert compose["services"][service_name]["ports"] == []

    nginx = S72_BROKER_NGINX.read_text(encoding="utf-8")
    assert nginx.count("listen 8080;") == 1
    assert "listen 0.0.0.0" not in nginx and "ssl" not in nginx.lower()
    assert "location ~ ^/api/ai/runtime/callbacks/" in nginx
    assert "location ^~ /openai/" in nginx and "location ^~ /anthropic/" in nginx
    assert "location / {\n        return 404;\n    }" in nginx

    service = S72_OPENSANDBOX_SERVICE.read_text(encoding="utf-8")
    server_env = S72_SERVER_ENV.read_text(encoding="utf-8")
    server_config_text = S72_SERVER_CONFIG.read_text(encoding="utf-8")
    server_config = tomllib.loads(server_config_text)
    assert "--network host" not in service
    assert "--publish 127.0.0.1:8080:8080" in service
    assert "--user ${OPENSANDBOX_SERVER_UID}:${OPENSANDBOX_SERVER_GID}" in service
    assert "--read-only" in service and "--cap-drop ALL" in service
    assert "--security-opt no-new-privileges" in service
    assert "REQUIRED_DEDICATED_NONROOT_UID" in server_env
    assert "REQUIRED_DEDICATED_NONROOT_GID" in server_env
    assert server_config["docker"]["network_mode"] == "none"
    assert server_config["docker"]["no_new_privileges"] is True
    assert "NET_RAW" in server_config["docker"]["drop_capabilities"]
    assert server_config["secure_runtime"] == {"type": "gvisor", "docker_runtime": "runsc"}
    assert "docker.sock" not in server_config_text

    templates = "\n".join((compose_text, nginx, service, server_env, server_config_text))
    for retired in ("10.56.", "211", "AI_PLATFORM_S72_BRIDGE", "REQUIRED_FIXED_EGRESS_HOSTNAME"):
        assert retired not in templates
    for datastore in ("postgres", "redis", "minio"):
        assert datastore not in service.lower()
        assert datastore not in server_config_text.lower()


def test_s72_opensandbox_service_fails_closed_on_container_name_collision() -> None:
    service = S72_OPENSANDBOX_SERVICE.read_text(encoding="utf-8")
    directives = tuple(line for line in service.splitlines() if line.startswith("Exec"))

    assert len(directives) == 1
    assert directives[0].startswith(
        "ExecStart=/usr/bin/docker run --rm --name ai-platform-opensandbox-server "
    )
    assert "ExecStartPre=" not in service
    assert "ExecStop=" not in service
    assert "/usr/bin/docker rm" not in service
    assert "/usr/bin/docker stop" not in service
    assert "KillMode=process" in service
    assert "TimeoutStopSec=30s" in service


def test_s72_lifecycle_network_is_internal_and_excludes_control_plane_datastores() -> None:
    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_constructor(
        "!reset",
        lambda loader, node: loader.construct_sequence(node),
    )
    compose = yaml.load(S72_COLOCATION_COMPOSE.read_text(encoding="utf-8"), Loader=ComposeLoader)
    lifecycle = compose["networks"]["opensandbox_lifecycle"]

    assert lifecycle["name"] == "ai-platform-opensandbox-lifecycle"
    assert lifecycle["internal"] is True
    for service_name in ("api", "worker"):
        assert "opensandbox_lifecycle" in compose["services"][service_name]["networks"]
    for service_name in ("postgres", "redis", "minio", "s72-broker-entry"):
        assert "opensandbox_lifecycle" not in compose["services"][service_name].get("networks", ())

    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "OPENSANDBOX_DOMAIN=ai-platform-opensandbox-server:8080" in env_example
    service = S72_OPENSANDBOX_SERVICE.read_text(encoding="utf-8")
    assert "--name ai-platform-opensandbox-server" in service
    assert "--network ai-platform-opensandbox-lifecycle" in service
