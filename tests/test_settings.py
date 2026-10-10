from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import Settings


# Stable synthetic keys, never production credentials.
_TEST_TRUSTED_PRINCIPAL_SECRET = "0123456789abcdef" * 4
_TEST_AI_SESSION_SECRET = "abcdef0123456789" * 4


def test_claude_agent_sdk_timeout_defaults_to_unbounded(monkeypatch):
    monkeypatch.delenv("CLAUDE_AGENT_SDK_TIMEOUT_SECONDS", raising=False)

    assert Settings(_env_file=None).claude_agent_sdk_timeout_seconds == 0.0
    assert (
        Settings(
            _env_file=None,
            claude_agent_sdk_timeout_seconds=120.0,
        ).claude_agent_sdk_timeout_seconds
        == 120.0
    )


def test_claude_agent_sdk_max_turns_defaults_to_256(monkeypatch):
    monkeypatch.delenv("CLAUDE_AGENT_SDK_MAX_TURNS", raising=False)

    assert Settings(_env_file=None).claude_agent_sdk_max_turns == 256


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


def test_profile_drive_transfer_upstream_requires_https():
    with pytest.raises(ValidationError, match="profile_drive_transfer_https_required"):
        Settings(
            _env_file=None,
            profile_drive_transfer_upstream="http://profile-drive.internal",
        )

    settings = Settings(
        _env_file=None,
        profile_drive_transfer_upstream="https://profile-drive.internal",
    )
    assert settings.profile_drive_transfer_upstream == "https://profile-drive.internal"


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


def test_internal_test_bridge_profile_is_scoped_to_test_opensandbox():
    values = {
        "deployment_environment": "test",
        "sandbox_container_provider": "opensandbox",
        "sandbox_security_profile": "internal-test",
        "opensandbox_expected_network_mode": "bridge",
    }
    settings = Settings(_env_file=None, **values)
    assert settings.sandbox_security_profile == "internal-test"
    assert settings.opensandbox_expected_network_mode == "bridge"

    for field, value, error in (
        ("deployment_environment", "production", "internal_test_opensandbox_profile_invalid"),
        ("sandbox_container_provider", "docker", "internal_test_opensandbox_profile_invalid"),
        ("opensandbox_expected_network_mode", "host", "opensandbox_expected_network_mode_invalid"),
    ):
        with pytest.raises(ValidationError, match=error):
            Settings(_env_file=None, **{**values, field: value})
    with pytest.raises(ValidationError, match="opensandbox_expected_network_mode_invalid"):
        Settings(_env_file=None, deployment_environment="production", opensandbox_expected_network_mode="bridge")


def test_production_opensandbox_accepts_a_configured_named_egress_network():
    values = {
        "deployment_environment": "production",
        "trusted_principal_secret": _TEST_TRUSTED_PRINCIPAL_SECRET,
        "ai_session_secret": _TEST_AI_SESSION_SECRET,
        "existing_auth_base_url": "https://auth.internal.example",
        "existing_user_info_base_url": "https://directory.internal.example",
        "sandbox_container_provider": "opensandbox",
        "opensandbox_expected_network_mode": "customer-public-egress-2026",
        "opensandbox_use_server_proxy": True,
        "sandbox_egress_policy_enabled": True,
        "opensandbox_api_key": "opensandbox-secret",
        "opensandbox_base_url": "http://10.56.1.75:8080",
        "opensandbox_egress_proxy_url": "http://egress.opensandbox.internal:8080",
    }

    assert Settings(_env_file=None, **values).opensandbox_expected_network_mode == (
        "customer-public-egress-2026"
    )

    for network_name in (
        "none",
        "bridge",
        "host",
        "ai-platform-opensandbox-egress-internal-v1",
        "bad network name",
        "customer-egress-",
        "customer-egress.",
        "customer-egress_",
        "n" * 64,
    ):
        values["opensandbox_expected_network_mode"] = network_name
        with pytest.raises(ValidationError, match="opensandbox_expected_network_mode_invalid"):
            Settings(_env_file=None, **values)


@pytest.mark.parametrize("name", ["n", "n" * 63, "customer-egress_2026"])
def test_configured_network_name_is_preserved_by_remote_metadata(name):
    from app.settings import is_valid_opensandbox_network_name
    from app.runtime.sandbox.providers.opensandbox.metadata import normalize_opensandbox_metadata

    assert is_valid_opensandbox_network_name(name)
    labels = {"ai-platform.external_egress.network_mode": name}
    assert normalize_opensandbox_metadata(labels) == labels


@pytest.mark.parametrize("provider", ["fake", "docker", "opensandbox"])
def test_unknown_security_profile_fails_closed(provider):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            sandbox_container_provider=provider,
            sandbox_security_profile="trusted_internal",
        )


def test_retired_runtime_authority_settings_are_not_configurable(monkeypatch, tmp_path):
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
        "sandbox_executor_browser_image",
        "sandbox_egress_network_name",
        "opensandbox_workspace_mount_enabled",
        "opensandbox_startup_io_probe_enabled",
        "opensandbox_allowed_egress_hosts",
        "run_event_stream_max_heartbeats",
        "default_workspace_id",
        "ai_session_cookie_name",
        "artifact_default_retention_days",
        "model_gateway_request_concurrency_limit",
    }

    assert retired_fields.isdisjoint(Settings.model_fields)

    # Old deployment files remain readable while active settings still apply.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{name.upper()}=obsolete\n" for name in sorted(retired_fields))
        + "WORKER_CONCURRENCY=7\n",
        encoding="utf-8",
    )
    for name in retired_fields:
        monkeypatch.setenv(name.upper(), "obsolete")
    monkeypatch.delenv("WORKER_CONCURRENCY", raising=False)
    settings = Settings(_env_file=env_file)
    assert settings.worker_concurrency == 7
    assert retired_fields.isdisjoint(settings.model_dump())

    for path in (
        "deploy/ai-platform/.env.example",
        "deploy/ai-platform/docker-compose.yml",
    ):
        text = Path(path).read_text(encoding="utf-8")
        assert all(name.upper() not in text for name in retired_fields)


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


def test_queue_lease_visibility_timeout_rejects_non_positive_values():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, queue_lease_visibility_timeout_seconds=0)


def test_production_identity_boundary_requires_gateway_secret_and_forbids_poc():
    with pytest.raises(
        ValidationError, match="trusted_principal_secret_required_in_production"
    ):
        Settings(
            _env_file=None,
            deployment_environment="production",
            trusted_principal_secret="",
        )
    with pytest.raises(ValidationError, match="frontend_poc_auth_forbidden_in_production"):
        Settings(
            _env_file=None,
            deployment_environment="production",
            trusted_principal_secret=_TEST_TRUSTED_PRINCIPAL_SECRET,
            ai_session_secret=_TEST_AI_SESSION_SECRET,
            frontend_poc_auth_enabled=True,
        )


@pytest.mark.parametrize("field", ["trusted_principal_secret", "ai_session_secret"])
@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("", "required"),
        (" \t\n", "required"),
        ("a" * 31, "too_short"),
        (" " + "a" * 31 + " ", "too_short"),
        ("change_me" + "a" * 32, "placeholder_forbidden"),
        ("changeme" + "a" * 32, "placeholder_forbidden"),
        ("example" + "a" * 32, "placeholder_forbidden"),
        ("replace" + "a" * 32, "placeholder_forbidden"),
        ("  ChAnGe_Me" + "a" * 32 + "  ", "placeholder_forbidden"),
        ("  ChAnGeMe" + "a" * 32 + "  ", "placeholder_forbidden"),
        ("  ExAmPlE" + "a" * 32 + "  ", "placeholder_forbidden"),
        ("  RePlAcE" + "a" * 32 + "  ", "placeholder_forbidden"),
    ],
)
def test_production_rejects_missing_weak_or_placeholder_auth_secrets(field, value, error):
    values = {
        "deployment_environment": "production",
        "trusted_principal_secret": _TEST_TRUSTED_PRINCIPAL_SECRET,
        "ai_session_secret": _TEST_AI_SESSION_SECRET,
        "existing_auth_base_url": "http://auth.internal.example",
        "existing_user_info_base_url": "http://directory.internal.example",
        field: value,
    }

    with pytest.raises(ValidationError, match=f"{field}_{error}_in_production"):
        Settings(_env_file=None, **values)


def test_production_accepts_32_character_auth_secrets_and_intranet_http():
    trusted_secret = _TEST_TRUSTED_PRINCIPAL_SECRET[:32]
    session_secret = _TEST_AI_SESSION_SECRET[:32]
    settings = Settings(
        _env_file=None,
        deployment_environment="production",
        trusted_principal_secret=trusted_secret,
        ai_session_secret=session_secret,
        existing_auth_base_url="http://auth.internal.example",
        existing_user_info_base_url="http://directory.internal.example",
        ai_session_cookie_secure=False,
    )

    assert settings.trusted_principal_secret == trusted_secret
    assert settings.ai_session_secret == session_secret
    assert settings.existing_auth_base_url == "http://auth.internal.example"
    assert settings.existing_user_info_base_url == "http://directory.internal.example"
    assert settings.ai_session_cookie_secure is False


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("value", ["", "short", "change_me" + "a" * 32])
def test_nonproduction_auth_secret_configuration_remains_compatible(environment, value):
    settings = Settings(
        _env_file=None,
        deployment_environment=environment,
        trusted_principal_secret=value,
        ai_session_secret=value,
    )

    assert settings.trusted_principal_secret == value
    assert settings.ai_session_secret == value


def test_production_requires_explicit_private_upstream_urls():
    with pytest.raises(
        ValidationError, match="private_upstream_url_required_in_production"
    ):
        Settings(
            _env_file=None,
            deployment_environment="production",
            trusted_principal_secret=_TEST_TRUSTED_PRINCIPAL_SECRET,
            ai_session_secret=_TEST_AI_SESSION_SECRET,
        )

    settings = Settings(
        _env_file=None,
        deployment_environment="production",
        trusted_principal_secret=_TEST_TRUSTED_PRINCIPAL_SECRET,
        ai_session_secret=_TEST_AI_SESSION_SECRET,
        existing_auth_base_url="https://auth.internal.example",
        existing_user_info_base_url="https://directory.internal.example",
    )

    assert settings.existing_auth_base_url == "https://auth.internal.example"
    assert settings.existing_user_info_base_url == "https://directory.internal.example"


def test_default_tenant_is_fixed_deployment_scope():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, default_tenant_id="customer-a")


def test_object_delete_settings_use_only_generic_names():
    settings = Settings(_env_file=None)

    assert settings.object_delete_batch_limit == 50
    assert settings.object_delete_max_attempts == 5
    assert settings.object_delete_retry_base_seconds == 60
    assert settings.object_delete_retry_cap_seconds == 3600
    assert not hasattr(settings, "artifact_object_delete_max_attempts")
    assert not hasattr(settings, "artifact_object_delete_retry_base_seconds")
    assert not hasattr(settings, "artifact_object_delete_retry_cap_seconds")


def test_legacy_object_delete_environment_names_are_ignored(monkeypatch):
    monkeypatch.setenv("ARTIFACT_RETENTION_CLEANUP_LIMIT", "17")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_RETRY_BASE_SECONDS", "90")
    monkeypatch.setenv("ARTIFACT_OBJECT_DELETE_RETRY_CAP_SECONDS", "900")

    settings = Settings(_env_file=None)

    assert settings.artifact_retention_cleanup_limit == 17
    assert settings.object_delete_batch_limit == 50
    assert settings.object_delete_max_attempts == 5
    assert settings.object_delete_retry_base_seconds == 60
    assert settings.object_delete_retry_cap_seconds == 3600


def test_canonical_object_delete_environment_names_are_loaded(monkeypatch):
    monkeypatch.setenv("OBJECT_DELETE_BATCH_LIMIT", "23")
    monkeypatch.setenv("OBJECT_DELETE_MAX_ATTEMPTS", "9")
    monkeypatch.setenv("OBJECT_DELETE_RETRY_BASE_SECONDS", "120")
    monkeypatch.setenv("OBJECT_DELETE_RETRY_CAP_SECONDS", "1200")

    settings = Settings(_env_file=None)

    assert settings.object_delete_batch_limit == 23
    assert settings.object_delete_max_attempts == 9
    assert settings.object_delete_retry_base_seconds == 120
    assert settings.object_delete_retry_cap_seconds == 1200


def test_compose_projects_only_canonical_object_delete_settings():
    compose = Path("deploy/ai-platform/docker-compose.yml").read_text(encoding="utf-8")
    expected = (
        "OBJECT_DELETE_BATCH_LIMIT: ${OBJECT_DELETE_BATCH_LIMIT:-50}",
        "OBJECT_DELETE_MAX_ATTEMPTS: ${OBJECT_DELETE_MAX_ATTEMPTS:-5}",
        "OBJECT_DELETE_RETRY_BASE_SECONDS: ${OBJECT_DELETE_RETRY_BASE_SECONDS:-60}",
        "OBJECT_DELETE_RETRY_CAP_SECONDS: ${OBJECT_DELETE_RETRY_CAP_SECONDS:-3600}",
    )

    for mapping in expected:
        assert compose.count(mapping) == 2
    assert "ARTIFACT_OBJECT_DELETE_" not in compose
    assert "OBJECT_DELETE_BATCH_LIMIT:-${ARTIFACT_RETENTION_CLEANUP_LIMIT" not in compose


def test_environment_example_uses_only_canonical_object_delete_names():
    source = Path("deploy/ai-platform/.env.example").read_text(encoding="utf-8")
    active = {line for line in source.splitlines() if line and not line.startswith("#")}

    assert {
        "OBJECT_DELETE_BATCH_LIMIT=50",
        "OBJECT_DELETE_MAX_ATTEMPTS=5",
        "OBJECT_DELETE_RETRY_BASE_SECONDS=60",
        "OBJECT_DELETE_RETRY_CAP_SECONDS=3600",
    }.issubset(active)
    assert "ARTIFACT_OBJECT_DELETE_" not in source


def test_object_delete_retry_cap_cannot_be_lower_than_base():
    with pytest.raises(ValidationError, match="object_delete_retry_cap_below_base"):
        Settings(
            _env_file=None,
            object_delete_retry_base_seconds=120,
            object_delete_retry_cap_seconds=60,
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
            trusted_principal_secret=_TEST_TRUSTED_PRINCIPAL_SECRET,
            ai_session_secret=_TEST_AI_SESSION_SECRET,
            **{field: 7},
        )


def test_nonproduction_retention_projection_can_report_unsupported_configuration():
    settings = Settings(
        _env_file=None,
        deployment_environment="test",
        run_event_retention_days=7,
    )

    assert settings.run_event_retention_days == 7
