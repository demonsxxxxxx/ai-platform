import importlib

import pytest

from app.runtime.sandbox.opensandbox_trusted_internal import (
    OpenSandboxProfileConfigurationError,
    validate_opensandbox_image_reference,
)
from test_sandbox_container_provider import (
    FakeOpenSandbox,
    OpenSandboxSettings,
    opensandbox_provider,
    request,
    workspace,
)


class TrustedInternalOpenSandboxSettings(OpenSandboxSettings):
    """Source-test settings for the explicit internal-beta OpenSandbox profile."""

    sandbox_security_profile = "trusted_internal"
    opensandbox_domain = "10.56.1.72:8080"
    opensandbox_api_key = "test-stock-opensandbox-key"
    opensandbox_executor_image = "registry.example/ai-platform@sha256:" + "a" * 64
    opensandbox_external_egress_callback_base_url = "http://10.56.0.211:18443"
    opensandbox_external_egress_openai_base_url = "http://10.56.0.211:18443/openai/v1"
    opensandbox_external_egress_anthropic_base_url = "http://10.56.0.211:18443/anthropic"
    openai_api_key = "test-model-api-key"
    anthropic_auth_token = "test-model-auth-token"


@pytest.mark.parametrize(
    ("image", "digest", "allow_local_image_id"),
    [
        ("registry.example/ai-platform:latest", "sha256:" + "a" * 64, True),
        ("sha256:" + "a" * 64, "sha256:" + "b" * 64, True),
        ("sha256:" + "a" * 64, "sha256:" + "a" * 64, False),
    ],
)
def test_immutable_image_validation_rejects_mutable_mismatch_and_governed_local_id(
    image,
    digest,
    allow_local_image_id,
):
    with pytest.raises(OpenSandboxProfileConfigurationError):
        validate_opensandbox_image_reference(
            image,
            digest,
            allow_local_image_id=allow_local_image_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("sandbox_security_profile", "governed", id="profile"),
        pytest.param("opensandbox_api_key", "", id="api-key"),
        pytest.param("opensandbox_domain", "opensandbox.internal:8080", id="literal-endpoint"),
        pytest.param("opensandbox_domain", "8.8.8.8:8080", id="private-endpoint"),
        pytest.param("opensandbox_domain", "10.56.1.72", id="endpoint-port"),
        pytest.param("opensandbox_use_server_proxy", True, id="server-proxy"),
        pytest.param("opensandbox_executor_image", "", id="explicit-image"),
        pytest.param("opensandbox_executor_image", "registry.example/ai-platform:latest", id="immutable-image"),
        pytest.param("opensandbox_executor_image_digest", "sha256:" + "b" * 64, id="matching-digest"),
        pytest.param(
            "opensandbox_external_egress_callback_base_url",
            "http://bridge.internal:18443",
            id="callback-literal",
        ),
        pytest.param(
            "opensandbox_external_egress_openai_base_url",
            "http://10.56.0.211:18443/v1",
            id="openai-dedicated-base",
        ),
        pytest.param(
            "opensandbox_external_egress_anthropic_base_url",
            "http://8.8.8.8:18443/anthropic",
            id="anthropic-private-base",
        ),
    ],
)
async def test_trusted_internal_opensandbox_rejects_incomplete_profile_before_create(monkeypatch, field, value):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    setattr(settings, field, value)
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider(
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability")
    )

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError):
        await provider.create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.created == []


@pytest.mark.asyncio
async def test_trusted_internal_opensandbox_uses_profile_bases_without_governed_proof(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider(
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability")
    )
    provider._authoritative_attestation_probe = lambda *_args: pytest.fail(
        "trusted_internal must not mint governed attestation evidence"
    )
    sandbox_request = request()
    leased_workspace = workspace()

    lease = await provider.create_or_reuse(sandbox_request, leased_workspace)
    await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)

    created = FakeOpenSandbox.created[0]
    assert created["network_policy"] is None
    assert created["env"]["AI_PLATFORM_CALLBACK_BASE_URL"] == settings.opensandbox_external_egress_callback_base_url
    assert created["env"]["OPENAI_BASE_URL"] == settings.opensandbox_external_egress_openai_base_url
    assert created["env"]["ANTHROPIC_BASE_URL"] == settings.opensandbox_external_egress_anthropic_base_url
    assert lease.labels["ai-platform.security_profile"] == "trusted_internal"
    assert lease.labels["ai-platform.provider_backend"] == "opensandbox"
    assert all("governed_egress" not in key and "external_egress" not in key for key in lease.labels)
    assert "default_deny" not in repr(lease.labels)
    assert "policy_bound" not in repr(lease.labels)
    for secret in (
        settings.opensandbox_api_key,
        settings.openai_api_key,
        settings.anthropic_auth_token,
    ):
        assert secret not in repr(created["metadata"])
        assert secret not in repr(lease.labels)

    stopped = await provider.stop(lease, reason="test_complete")
    assert stopped.status == "stopped"
    assert FakeOpenSandbox.instances[lease.container_id].killed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("health_probe", "identity_probe", "error_type"),
    [
        pytest.param(lambda *_args: False, None, "health", id="health"),
        pytest.param(None, lambda *_args: {"uid": 0, "gid": 0}, "identity", id="identity"),
    ],
)
async def test_trusted_internal_opensandbox_cleans_failed_health_or_identity(
    monkeypatch,
    health_probe,
    identity_probe,
    error_type,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider(
        health_probe=health_probe,
        identity_probe=identity_probe,
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability"),
    )
    expected_error = (
        container_provider.ExecutorHealthTimeoutError
        if error_type == "health"
        else container_provider.ContainerStartFailedError
    )

    with pytest.raises(expected_error):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.killed is True
    assert sandbox.closed is True
    assert sandbox.kill_calls == 1
    assert provider._leases == {}
    assert provider._sandboxes == {}


@pytest.mark.asyncio
async def test_trusted_internal_opensandbox_cleanup_uncertainty_remains_fail_closed(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    def fail_health_after_making_cleanup_uncertain(*_args):
        FakeOpenSandbox.instances["osb-run-a"].kill_error = RuntimeError("test cleanup transport failure")
        return False

    provider = opensandbox_provider(
        health_probe=fail_health_after_making_cleanup_uncertain,
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability"),
    )

    with pytest.raises(container_provider.ContainerCleanupFailedError):
        await provider.create_or_reuse(request(), workspace())

    sandbox = FakeOpenSandbox.instances["osb-run-a"]
    assert sandbox.kill_calls == 1
    assert sandbox.closed is True
    assert provider._leases[("run-a", "qat-test-attempt")].container_id == sandbox.id
    assert provider._sandboxes[sandbox.id] is sandbox


@pytest.mark.asyncio
async def test_trusted_internal_dispatch_metadata_drift_surfaces_cleanup_uncertainty(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider(
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability")
    )
    sandbox_request = request()
    leased_workspace = workspace()
    lease = await provider.create_or_reuse(sandbox_request, leased_workspace)
    sandbox = FakeOpenSandbox.instances[lease.container_id]
    sandbox.metadata["ai-platform.attempt_id"] = "qat-stale-attempt"

    with pytest.raises(container_provider.ContainerCleanupFailedError, match="cleanup could not be confirmed"):
        await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)

    assert sandbox.killed is False
    assert sandbox.kill_calls == 0
    assert provider._leases[(lease.run_id, sandbox_request.attempt_id)] is lease
    assert provider._sandboxes[lease.container_id] is sandbox


@pytest.mark.asyncio
async def test_trusted_internal_dispatch_rejects_configured_profile_drift_and_cleans_once(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider(
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability")
    )
    sandbox_request = request()
    leased_workspace = workspace()
    lease = await provider.create_or_reuse(sandbox_request, leased_workspace)
    sandbox = FakeOpenSandbox.instances[lease.container_id]
    settings.sandbox_security_profile = "governed"

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError):
        await provider.validate_for_dispatch(lease, sandbox_request, leased_workspace)

    assert sandbox.killed is True
    assert sandbox.closed is True
    assert sandbox.kill_calls == 1
    assert provider._leases == {}
    assert provider._sandboxes == {}


@pytest.mark.asyncio
async def test_trusted_internal_cached_reuse_cleans_before_rejecting_static_config_drift(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    provider = opensandbox_provider(
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability")
    )
    lease = await provider.create_or_reuse(request(), workspace())
    sandbox = FakeOpenSandbox.instances[lease.container_id]
    settings.opensandbox_api_key = ""

    with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="credential is unavailable"):
        await provider.create_or_reuse(request(), workspace())

    assert sandbox.killed is True
    assert sandbox.closed is True
    assert sandbox.kill_calls == 1
    assert provider._leases == {}
    assert provider._sandboxes == {}


def test_trusted_internal_profile_is_rejected_for_non_opensandbox_provider(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    container_provider.reset_container_provider_cache()
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    for provider_name in ("fake", "docker"):
        with pytest.raises(container_provider.OpenSandboxCapabilityAdmissionError, match="requires the OpenSandbox"):
            container_provider.create_container_provider(provider_name)

    container_provider.reset_container_provider_cache()


@pytest.mark.asyncio
async def test_trusted_internal_opensandbox_accepts_exact_preloaded_local_image_id(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    FakeOpenSandbox.reset()
    settings = TrustedInternalOpenSandboxSettings()
    settings.opensandbox_executor_image = "sha256:" + "c" * 64
    settings.opensandbox_executor_image_digest = settings.opensandbox_executor_image
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)

    lease = await opensandbox_provider(
        capability_profile_fetcher=lambda *_args: pytest.fail("trusted_internal must not fetch governed capability")
    ).create_or_reuse(request(), workspace())

    assert FakeOpenSandbox.created[0]["image"] == settings.opensandbox_executor_image
    assert lease.labels["ai-platform.executor.requested_image"] == settings.opensandbox_executor_image
    assert lease.labels["ai-platform.executor.requested_image_digest"] == settings.opensandbox_executor_image
