import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_stale_run_reconciliation_settings_accept_environment_overrides(monkeypatch):
    monkeypatch.setenv("STALE_RUN_RECONCILIATION_SECONDS", "1800")
    monkeypatch.setenv("STALE_RUN_RECONCILIATION_LIMIT", "7")
    monkeypatch.setenv("STALE_RUN_RECONCILIATION_FENCE_TTL_SECONDS", "420")

    settings = Settings(_env_file=None)

    assert settings.stale_run_reconciliation_seconds == 1800
    assert settings.stale_run_reconciliation_limit == 7
    assert settings.stale_run_reconciliation_fence_ttl_seconds == 420


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stale_run_reconciliation_seconds", 59),
        ("stale_run_reconciliation_limit", 0),
        ("stale_run_reconciliation_fence_ttl_seconds", 29),
    ],
)
def test_stale_run_reconciliation_settings_reject_unsafe_bounds(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_sandbox_security_profile_defaults_governed_and_rejects_retired_profile(monkeypatch):
    assert Settings(_env_file=None).sandbox_security_profile == "governed"

    monkeypatch.setenv("SANDBOX_SECURITY_PROFILE", "trusted_internal")
    monkeypatch.setenv("SANDBOX_CONTAINER_PROVIDER", "opensandbox")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_sandbox_security_profile_rejects_unknown_values():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, sandbox_security_profile="permissive")


def test_internal_test_opensandbox_profile_requires_explicit_test_bridge_selection():
    settings = Settings(
        _env_file=None,
        deployment_environment="test",
        sandbox_container_provider="opensandbox",
        sandbox_security_profile="internal-test",
        opensandbox_expected_network_mode="bridge",
    )

    assert settings.sandbox_security_profile == "internal-test"
    assert settings.opensandbox_internal_test_forward_model_credentials is False


def test_internal_test_opensandbox_model_credentials_require_explicit_bounded_opt_in():
    settings = Settings(
        _env_file=None,
        deployment_environment="test",
        sandbox_container_provider="opensandbox",
        sandbox_security_profile="internal-test",
        opensandbox_expected_network_mode="bridge",
        opensandbox_internal_test_forward_model_credentials=True,
        openai_api_key="openai-test-credential",
        anthropic_auth_token="anthropic-test-credential",
    )

    assert settings.opensandbox_internal_test_forward_model_credentials is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"sandbox_security_profile": "governed"},
        {"deployment_environment": "development"},
        {"sandbox_container_provider": "docker"},
        {"opensandbox_expected_network_mode": "none"},
        {"openai_api_key": ""},
        {"anthropic_auth_token": ""},
    ],
)
def test_internal_test_opensandbox_model_credentials_reject_unbounded_or_incomplete_opt_in(overrides):
    values = {
        "deployment_environment": "test",
        "sandbox_container_provider": "opensandbox",
        "sandbox_security_profile": "internal-test",
        "opensandbox_expected_network_mode": "bridge",
        "opensandbox_internal_test_forward_model_credentials": True,
        "openai_api_key": "openai-test-credential",
        "anthropic_auth_token": "anthropic-test-credential",
        **overrides,
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"deployment_environment": "production"},
        {"deployment_environment": "development"},
        {"sandbox_container_provider": "fake"},
        {"sandbox_container_provider": "docker"},
        {"opensandbox_expected_network_mode": "none"},
    ],
)
def test_internal_test_opensandbox_profile_rejects_single_sided_or_non_test_selection(overrides):
    values = {
        "deployment_environment": "test",
        "sandbox_container_provider": "opensandbox",
        "sandbox_security_profile": "internal-test",
        "opensandbox_expected_network_mode": "bridge",
        **overrides,
    }
    if values["deployment_environment"] == "production":
        values["trusted_principal_secret"] = "test-only-principal-secret"
    with pytest.raises(ValidationError, match="internal_test_opensandbox_profile_invalid"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("provider", ["fake", "docker", "opensandbox"])
def test_retired_security_profile_is_rejected_for_every_provider(provider):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            sandbox_container_provider=provider,
            sandbox_security_profile="trusted_internal",
        )


def test_retired_runtime_authority_settings_are_not_configurable():
    retired_fields = {
        "multi_agent_dispatch_worker_enabled",
        "multi_agent_dispatch_worker_interval_seconds",
        "multi_agent_dispatch_worker_limit",
        "multi_agent_dispatch_worker_user_id",
        "multi_agent_dispatch_lease_ttl_seconds",
        "enable_legacy_runtime211_fallback",
        "ragflow_api_url",
        "ragflow_api_key",
        "ragflow_default_dataset_id",
        "ragflow_timeout_seconds",
        "ragflow_top_k",
        "ragflow_similarity_threshold",
    }

    assert retired_fields.isdisjoint(Settings.model_fields)


def test_capacity_and_redis_pool_defaults_are_bounded_independently():
    settings = Settings(_env_file=None)

    assert settings.worker_concurrency == 10
    assert settings.max_active_worker_runs == 10
    assert settings.max_active_runs_per_user == 3
    assert settings.redis_max_connections == 10
    assert settings.database_pool_max_size == 10


def test_redis_max_connections_rejects_non_positive_values():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, redis_max_connections=0)


def test_production_identity_boundary_requires_gateway_secret_and_forbids_poc():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deployment_environment="production")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            deployment_environment="production",
            trusted_principal_secret="secret",
            frontend_poc_auth_enabled=True,
        )


def test_default_tenant_is_fixed_deployment_scope():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, default_tenant_id="customer-a")


def test_object_delete_retry_cap_cannot_be_lower_than_base():
    with pytest.raises(ValidationError, match="artifact_object_delete_retry_cap_below_base"):
        Settings(
            _env_file=None,
            artifact_object_delete_retry_base_seconds=120,
            artifact_object_delete_retry_cap_seconds=60,
        )


@pytest.mark.parametrize(
    "field",
    [
        "run_event_retention_days",
        "context_snapshot_retention_days",
        "audit_retention_days",
        "message_retention_days",
        "file_retention_days",
    ],
)
def test_production_rejects_unimplemented_nonzero_retention_policies(field):
    with pytest.raises(ValidationError, match="unsupported_retention_policy_in_production"):
        Settings(
            _env_file=None,
            deployment_environment="production",
            trusted_principal_secret="gateway-secret",
            **{field: 7},
        )


def test_nonproduction_retention_projection_can_report_unsupported_configuration():
    settings = Settings(
        _env_file=None,
        deployment_environment="test",
        run_event_retention_days=7,
    )

    assert settings.run_event_retention_days == 7
