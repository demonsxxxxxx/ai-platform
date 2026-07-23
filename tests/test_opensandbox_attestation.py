import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from app.runtime.sandbox import opensandbox_attestation
from app.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.yml"
ENV_EXAMPLE = ROOT / "deploy" / "ai-platform" / ".env.example"
IMAGE_DIGEST = "sha256:" + "a" * 64
IMAGE_SUBJECT = f"registry.example/team/ai-platform@{IMAGE_DIGEST}"


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
        "sandbox_egress_proof_key_id": "proof-key-a",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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
        },
        "image": {
            "subject": IMAGE_SUBJECT,
            "digest": IMAGE_DIGEST,
        },
        "host_path_policy": {
            "subject": "scoped-workspace-only",
            "unscoped_host_paths_allowed": False,
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
    expected_environment = {
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
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for service_name in ("api", "worker"):
        environment = compose["services"][service_name]["environment"]
        assert expected_environment <= environment.keys()
        assert environment["OPENSANDBOX_API_KEY"] == "${OPENSANDBOX_API_KEY:-}"
        assert environment["OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN"] == (
            "${OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN:-}"
        )
        assert environment["SANDBOX_CONTAINER_PROVIDER"] == "${SANDBOX_CONTAINER_PROVIDER:-fake}"

    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    for name in expected_environment:
        assert f"{name}=" in env_example
    assert {
        "opensandbox_attestation_path",
        "opensandbox_attestation_contract_version",
        "opensandbox_attestation_timeout_seconds",
    } <= Settings.model_fields.keys()
