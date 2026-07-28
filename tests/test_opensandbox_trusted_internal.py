import importlib

import pytest

from app.runtime.sandbox.opensandbox_trusted_internal import (
    OpenSandboxProfileConfigurationError,
    trusted_internal_cleanup_identity_is_authorized,
    trusted_internal_orphan_cleanup_metadata_filter,
    validate_opensandbox_image_reference,
)
from app.runtime.sandbox.providers.opensandbox.metadata import normalize_opensandbox_metadata
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
    opensandbox_trusted_internal_callback_base_url = "http://10.56.0.211:8020"
    opensandbox_trusted_internal_openai_base_url = "http://10.56.0.211:3002/v1"
    opensandbox_trusted_internal_anthropic_base_url = "http://10.56.0.211:3002"
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
        pytest.param("opensandbox_api_key", "", id="api-key"),
        pytest.param("opensandbox_domain", "opensandbox.internal:8080", id="literal-endpoint"),
        pytest.param("opensandbox_domain", "8.8.8.8:8080", id="private-endpoint"),
        pytest.param("opensandbox_domain", "10.56.1.72", id="endpoint-port"),
        pytest.param("opensandbox_use_server_proxy", True, id="server-proxy"),
        pytest.param("opensandbox_executor_image", "", id="explicit-image"),
        pytest.param("opensandbox_executor_image", "registry.example/ai-platform:latest", id="immutable-image"),
        pytest.param("opensandbox_executor_image_digest", "sha256:" + "b" * 64, id="matching-digest"),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "https://bridge.internal.example:18443",
            id="governed-bridge-callback",
        ),
        pytest.param(
            "opensandbox_trusted_internal_openai_base_url",
            "https://bridge.internal.example:18443/openai/v1",
            id="governed-bridge-openai",
        ),
        pytest.param(
            "opensandbox_trusted_internal_anthropic_base_url",
            "https://bridge.internal.example:18443/anthropic",
            id="governed-bridge-anthropic",
        ),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "http://10.56.0.212:8020",
            id="callback-host-drift",
        ),
        pytest.param(
            "opensandbox_trusted_internal_openai_base_url",
            "http://10.56.0.212:3002/v1",
            id="model-host-drift",
        ),
        pytest.param(
            "opensandbox_trusted_internal_anthropic_base_url",
            "http://10.56.0.211:3003",
            id="model-port-drift",
        ),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "http://10.56.0.211:8020/callback",
            id="callback-path",
        ),
        pytest.param(
            "opensandbox_trusted_internal_openai_base_url",
            "http://10.56.0.211:3002/openai/v1",
            id="openai-path",
        ),
        pytest.param(
            "opensandbox_trusted_internal_anthropic_base_url",
            "http://10.56.0.211:3002/anthropic",
            id="anthropic-path",
        ),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "http://platform.internal:8020",
            id="hostname",
        ),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "http://127.0.0.1:8020",
            id="unsafe-loopback",
        ),
        pytest.param(
            "opensandbox_trusted_internal_openai_base_url",
            "http://8.8.8.8:3002/v1",
            id="unsafe-public-ip",
        ),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "http://0.0.0.0:8020",
            id="unsafe-unspecified-ip",
        ),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "http://240.0.0.1:8020",
            id="unsafe-reserved-ip",
        ),
        pytest.param(
            "opensandbox_trusted_internal_callback_base_url",
            "http://user@10.56.0.211:8020",
            id="credentials",
        ),
        pytest.param(
            "opensandbox_trusted_internal_openai_base_url",
            "http://10.56.0.211:3002/v1?model=test",
            id="query",
        ),
        pytest.param(
            "opensandbox_trusted_internal_anthropic_base_url",
            "http://10.56.0.211:3002#fragment",
            id="fragment",
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
    assert created["env"]["AI_PLATFORM_CALLBACK_BASE_URL"] == settings.opensandbox_trusted_internal_callback_base_url
    assert created["env"]["OPENAI_BASE_URL"] == settings.opensandbox_trusted_internal_openai_base_url
    assert created["env"]["ANTHROPIC_BASE_URL"] == settings.opensandbox_trusted_internal_anthropic_base_url
    callback_target = container_provider.executor_callback_target(settings, "opensandbox")
    assert callback_target.base_url == "http://10.56.0.211:8020"
    assert callback_target.callback_url == "http://10.56.0.211:8020/api/ai/runtime/callbacks/executor"
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
    provider = opensandbox_provider()
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


def _trusted_orphan_filters(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "qat-test-attempt",
        "sandbox_mode": "ephemeral",
        "security_profile": "trusted_internal",
    }
    values.update(overrides)
    return values


def _trusted_orphan_metadata(**overrides):
    values = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.tenant_id": "tenant-a",
        "ai-platform.workspace_id": "workspace-a",
        "ai-platform.user_id": "user-a",
        "ai-platform.session_id": "session-a",
        "ai-platform.run_id": "run-a",
        "ai-platform.attempt_id": "qat-test-attempt",
        "ai-platform.sandbox_mode": "ephemeral",
        "ai-platform.security_profile": "trusted_internal",
        "ai-platform.browser_enabled": "false",
    }
    values.update(overrides)
    return values


def test_trusted_internal_cleanup_authorizes_only_the_exact_normalized_canonical_scope():
    raw_run_id = "run-" + "r" * 64
    lease_labels = _trusted_orphan_metadata(
        **{
            "ai-platform.run_id": raw_run_id,
            "ai-platform.executor.requested_image": "registry.example/ai-platform@sha256:" + "a" * 64,
            "ai-platform.executor.requested_image_digest": "sha256:" + "a" * 64,
        }
    )
    remote_labels = normalize_opensandbox_metadata(lease_labels)
    other_scope = {**lease_labels, "ai-platform.run_id": "run-" + "s" * 64}

    assert trusted_internal_cleanup_identity_is_authorized(remote_labels, lease_labels)
    assert not trusted_internal_cleanup_identity_is_authorized(remote_labels, other_scope)
    assert raw_run_id not in repr(remote_labels)


@pytest.mark.asyncio
async def test_opensandbox_tenant_only_orphan_cleanup_is_safe_noop_before_manager(monkeypatch):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    provider = opensandbox_provider()

    async def unexpected_manager(_connection_config):
        pytest.fail("tenant-only cleanup must not create an OpenSandbox manager")

    monkeypatch.setattr(provider, "_manager", unexpected_manager)
    monkeypatch.setattr(container_provider, "get_settings", lambda: pytest.fail("settings must not be read"))

    assert await provider.cleanup_orphan_containers({"tenant_id": "tenant-a"}, reason="admin_runtime") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata_override", "expected_killed"),
    [
        pytest.param({}, ["osb-orphan-a"], id="exact-scope"),
        pytest.param({"ai-platform.run_id": "run-stale"}, [], id="run-mismatch"),
        pytest.param({"ai-platform.security_profile": "governed"}, [], id="profile-mismatch"),
    ],
)
async def test_opensandbox_orphan_cleanup_requires_exact_trusted_scope_and_readback(
    monkeypatch,
    metadata_override,
    expected_killed,
):
    container_provider = importlib.import_module("app.runtime.sandbox.container_provider")
    settings = TrustedInternalOpenSandboxSettings()
    monkeypatch.setattr(container_provider, "get_settings", lambda: settings)
    orphan = FakeOpenSandbox(
        sandbox_id="osb-orphan-a",
        metadata=_trusted_orphan_metadata(**metadata_override),
        state="FAILED",
    )

    class RecordingManager:
        def __init__(self):
            self.list_calls = []
            self.killed = []
            self.closed = False

        async def list_sandboxes(self, **kwargs):
            self.list_calls.append(kwargs)
            return [orphan]

        async def kill_sandbox(self, sandbox_id):
            self.killed.append(sandbox_id)

        async def close(self):
            self.closed = True

    manager = RecordingManager()
    provider = opensandbox_provider()

    async def manager_factory(_connection_config):
        return manager

    monkeypatch.setattr(provider, "_manager", manager_factory)
    filters = _trusted_orphan_filters()
    expected_filter = trusted_internal_orphan_cleanup_metadata_filter(filters)

    results = await provider.cleanup_orphan_containers(filters, reason="orphan_reconciliation")

    assert manager.list_calls == [{"metadata": expected_filter}]
    assert manager.killed == expected_killed
    assert [result.container_id for result in results] == expected_killed
    assert manager.closed is True
