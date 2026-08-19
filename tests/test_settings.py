from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import OBJECT_DELETE_LEGACY_ENV_SUPPORTED_UNTIL, Settings


def test_claude_agent_sdk_timeout_defaults_to_document_workflow_budget(monkeypatch):
    monkeypatch.delenv("CLAUDE_AGENT_SDK_TIMEOUT_SECONDS", raising=False)

    assert Settings(_env_file=None).claude_agent_sdk_timeout_seconds == 1200.0
    assert (
        Settings(
            _env_file=None,
            claude_agent_sdk_timeout_seconds=120.0,
        ).claude_agent_sdk_timeout_seconds
        == 120.0
    )


def test_browser_authentication_windows_default_to_twenty_four_hours():
    settings = Settings(_env_file=None)

    assert settings.ai_session_max_age_seconds == 24 * 60 * 60
    assert settings.auth_context_max_age_seconds == 24 * 60 * 60
    assert settings.company_authority_freshness_seconds == 24 * 60 * 60


@pytest.mark.parametrize(
    "field",
    [
        "ai_session_max_age_seconds",
        "auth_context_max_age_seconds",
        "company_authority_freshness_seconds",
    ],
)
@pytest.mark.parametrize("value", [86399, 86401])
def test_browser_authentication_windows_reject_non_twenty_four_hour_values(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_browser_public_launchpad_urls_default_unavailable_and_accept_explicit_env(
    monkeypatch,
):
    defaults = Settings(_env_file=None)
    assert defaults.browser_public_launchpad_lingxi_url is None
    assert defaults.browser_public_launchpad_sop_url is None
    assert defaults.browser_public_launchpad_word_translate_url is None
    assert defaults.browser_public_launchpad_word_review_url is None

    monkeypatch.setenv(
        "BROWSER_PUBLIC_LAUNCHPAD_LINGXI_URL",
        "http://10.56.0.25:8189/#/TaskManagement/indexSpace/",
    )
    monkeypatch.setenv(
        "BROWSER_PUBLIC_LAUNCHPAD_WORD_TRANSLATE_URL",
        "https://word-tools.example.test/translate",
    )

    settings = Settings(_env_file=None)

    assert (
        settings.browser_public_launchpad_lingxi_url
        == "http://10.56.0.25:8189/#/TaskManagement/indexSpace/"
    )
    assert settings.browser_public_launchpad_word_translate_url == (
        "https://word-tools.example.test/translate"
    )


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "/relative/path",
        "https://user:password@example.test/path",
        "https://example.test/path?token=secret",
        "https://example.test/#access_token=secret",
        "https://example.test/path with space",
        123,
    ],
)
def test_browser_public_launchpad_urls_reject_unsafe_values(value):
    with pytest.raises(ValidationError, match="browser_public_launchpad_url"):
        Settings(_env_file=None, browser_public_launchpad_lingxi_url=value)


@pytest.mark.parametrize(
    "value",
    [
        "auth.internal.example",
        "ftp://auth.internal.example",
        "https://user:password@auth.internal.example",
        "https://auth.internal.example?token=secret",
        "https://auth.internal.example/#fragment",
    ],
)
def test_private_upstream_urls_reject_invalid_base_urls(value):
    with pytest.raises(ValidationError, match="private_upstream_url_invalid"):
        Settings(_env_file=None, existing_auth_base_url=value)


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


def test_sandbox_security_profile_defaults_governed_and_rejects_retired_profile(
    monkeypatch,
):
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


def test_internal_test_model_credential_forwarding_requires_exact_profile_and_credentials():
    settings = Settings(
        _env_file=None,
        deployment_environment="test",
        sandbox_container_provider="opensandbox",
        sandbox_security_profile="internal-test",
        opensandbox_expected_network_mode="bridge",
        opensandbox_internal_test_forward_model_credentials=True,
        openai_api_key="test-openai-key",
        anthropic_auth_token="test-anthropic-token",
    )

    assert settings.opensandbox_internal_test_forward_model_credentials is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"deployment_environment": "development"},
        {"sandbox_container_provider": "docker"},
        {"sandbox_security_profile": "governed"},
        {"opensandbox_expected_network_mode": "none"},
    ],
)
def test_internal_test_model_credential_forwarding_rejects_other_profiles(overrides):
    values = {
        "deployment_environment": "test",
        "sandbox_container_provider": "opensandbox",
        "sandbox_security_profile": "internal-test",
        "opensandbox_expected_network_mode": "bridge",
        "opensandbox_internal_test_forward_model_credentials": True,
        "openai_api_key": "test-openai-key",
        "anthropic_auth_token": "test-anthropic-token",
        **overrides,
    }

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "missing_credential",
    ["openai_api_key", "anthropic_auth_token"],
)
def test_internal_test_model_credential_forwarding_requires_both_credentials(
    missing_credential,
):
    values = {
        "deployment_environment": "test",
        "sandbox_container_provider": "opensandbox",
        "sandbox_security_profile": "internal-test",
        "opensandbox_expected_network_mode": "bridge",
        "opensandbox_internal_test_forward_model_credentials": True,
        "openai_api_key": "test-openai-key",
        "anthropic_auth_token": "test-anthropic-token",
        missing_credential: "",
    }

    with pytest.raises(ValidationError, match="internal_test_model_credentials_required"):
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
def test_internal_test_opensandbox_profile_rejects_single_sided_or_non_test_selection(
    overrides,
):
    values = {
        "deployment_environment": "test",
        "sandbox_container_provider": "opensandbox",
        "sandbox_security_profile": "internal-test",
        "opensandbox_expected_network_mode": "bridge",
        **overrides,
    }
    if values["deployment_environment"] == "production":
        values["trusted_principal_secret"] = "test-only-principal-secret"
    with pytest.raises(
        ValidationError, match="internal_test_opensandbox_profile_invalid"
    ):
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
    assert settings.redis_max_connections == 64
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


def test_production_requires_explicit_private_upstream_urls():
    with pytest.raises(
        ValidationError, match="private_upstream_url_required_in_production"
    ):
        Settings(
            _env_file=None,
            deployment_environment="production",
            trusted_principal_secret="gateway-secret",
        )

    settings = Settings(
        _env_file=None,
        deployment_environment="production",
        trusted_principal_secret="gateway-secret",
        existing_auth_base_url="https://auth.internal.example",
        existing_user_info_base_url="https://directory.internal.example",
    )

    assert settings.existing_auth_base_url == "https://auth.internal.example"
    assert settings.existing_user_info_base_url == "https://directory.internal.example"


def test_default_tenant_is_fixed_deployment_scope():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, default_tenant_id="customer-a")


def test_object_delete_settings_use_generic_names_and_keep_python_aliases():
    settings = Settings(_env_file=None)

    assert settings.object_delete_batch_limit == 50
    assert settings.object_delete_max_attempts == 5
    assert settings.object_delete_retry_base_seconds == 60
    assert settings.object_delete_retry_cap_seconds == 3600
    assert settings.artifact_object_delete_max_attempts == 5
    assert settings.artifact_object_delete_retry_base_seconds == 60
    assert settings.artifact_object_delete_retry_cap_seconds == 3600
    assert OBJECT_DELETE_LEGACY_ENV_SUPPORTED_UNTIL == "2026-10-31"

    settings.artifact_object_delete_max_attempts = 8
    settings.artifact_object_delete_retry_base_seconds = 80
    settings.artifact_object_delete_retry_cap_seconds = 800

    assert settings.object_delete_max_attempts == 8
    assert settings.object_delete_retry_base_seconds == 80
    assert settings.object_delete_retry_cap_seconds == 800


def test_legacy_object_delete_environment_names_remain_fallbacks(monkeypatch):
    monkeypatch.setenv("ARTIFACT_RETENTION_CLEANUP_LIMIT", "17")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_RETRY_BASE_SECONDS", "90")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_RETRY_CAP_SECONDS", "900")

    settings = Settings(_env_file=None)

    assert settings.artifact_retention_cleanup_limit == 17
    assert settings.object_delete_batch_limit == 17
    assert settings.object_delete_max_attempts == 7
    assert settings.object_delete_retry_base_seconds == 90
    assert settings.object_delete_retry_cap_seconds == 900


def test_canonical_object_delete_environment_names_win_over_legacy(monkeypatch):
    monkeypatch.setenv("ARTIFACT_RETENTION_CLEANUP_LIMIT", "17")
    monkeypatch.setenv("OBJECT_DELETE_BATCH_LIMIT", "23")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("OBJECT_DELETE_MAX_ATTEMPTS", "9")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_RETRY_BASE_SECONDS", "90")
    monkeypatch.setenv("OBJECT_DELETE_RETRY_BASE_SECONDS", "120")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_RETRY_CAP_SECONDS", "900")
    monkeypatch.setenv("OBJECT_DELETE_RETRY_CAP_SECONDS", "1200")

    settings = Settings(_env_file=None)

    assert settings.artifact_retention_cleanup_limit == 17
    assert settings.object_delete_batch_limit == 23
    assert settings.object_delete_max_attempts == 9
    assert settings.object_delete_retry_base_seconds == 120
    assert settings.object_delete_retry_cap_seconds == 1200


def test_compose_projects_canonical_object_delete_settings_with_legacy_fallbacks():
    compose = Path("deploy/ai-platform/docker-compose.yml").read_text(encoding="utf-8")
    expected = (
        'OBJECT_DELETE_BATCH_LIMIT: "${OBJECT_DELETE_BATCH_LIMIT:-${ARTIFACT_RETENTION_CLEANUP_LIMIT:-50}}"',
        'OBJECT_DELETE_MAX_ATTEMPTS: "${OBJECT_DELETE_MAX_ATTEMPTS:-${ARTIFACT_OBJECT_DELETE_MAX_ATTEMPTS:-5}}"',
        'OBJECT_DELETE_RETRY_BASE_SECONDS: "${OBJECT_DELETE_RETRY_BASE_SECONDS:-${ARTIFACT_OBJECT_DELETE_RETRY_BASE_SECONDS:-60}}"',
        'OBJECT_DELETE_RETRY_CAP_SECONDS: "${OBJECT_DELETE_RETRY_CAP_SECONDS:-${ARTIFACT_OBJECT_DELETE_RETRY_CAP_SECONDS:-3600}}"',
    )

    for mapping in expected:
        assert compose.count(mapping) == 2


def test_environment_example_prefers_canonical_object_delete_names():
    lines = (
        Path("deploy/ai-platform/.env.example").read_text(encoding="utf-8").splitlines()
    )
    active = {line for line in lines if line and not line.startswith("#")}

    assert {
        "OBJECT_DELETE_BATCH_LIMIT=50",
        "OBJECT_DELETE_MAX_ATTEMPTS=5",
        "OBJECT_DELETE_RETRY_BASE_SECONDS=60",
        "OBJECT_DELETE_RETRY_CAP_SECONDS=3600",
    }.issubset(active)
    assert not any(line.startswith("ARTIFACT_OBJECT_DELETE_") for line in active)
    assert "# Deprecated migration aliases remain accepted through 2026-10-31." in lines


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "object_delete_retry_base_seconds": 120,
            "object_delete_retry_cap_seconds": 60,
        },
        {
            "artifact_object_delete_retry_base_seconds": 120,
            "artifact_object_delete_retry_cap_seconds": 60,
        },
    ],
)
def test_object_delete_retry_cap_cannot_be_lower_than_base(overrides):
    with pytest.raises(ValidationError, match="object_delete_retry_cap_below_base"):
        Settings(
            _env_file=None,
            **overrides,
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
    with pytest.raises(
        ValidationError, match="unsupported_retention_policy_in_production"
    ):
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
