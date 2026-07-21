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
