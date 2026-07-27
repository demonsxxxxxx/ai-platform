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


def test_sandbox_security_profile_defaults_governed_and_accepts_explicit_trusted_internal(monkeypatch):
    assert Settings(_env_file=None).sandbox_security_profile == "governed"

    monkeypatch.setenv("SANDBOX_SECURITY_PROFILE", "trusted_internal")
    monkeypatch.setenv("SANDBOX_CONTAINER_PROVIDER", "opensandbox")

    assert Settings(_env_file=None).sandbox_security_profile == "trusted_internal"


def test_sandbox_security_profile_rejects_unknown_values():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, sandbox_security_profile="permissive")


@pytest.mark.parametrize("provider", ["fake", "docker"])
def test_trusted_internal_security_profile_rejects_non_opensandbox_provider(provider):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            sandbox_container_provider=provider,
            sandbox_security_profile="trusted_internal",
        )
