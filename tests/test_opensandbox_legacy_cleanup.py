from copy import deepcopy

from app.runtime.sandbox.opensandbox_legacy_cleanup import (
    trusted_internal_cleanup_identity_is_authorized,
    trusted_internal_cleanup_labels_from_persisted_row,
    trusted_internal_orphan_cleanup_identity_is_authorized,
    trusted_internal_orphan_cleanup_metadata_filter,
)
from app.runtime.sandbox.providers.opensandbox.metadata import normalize_opensandbox_metadata


def _legacy_metadata(**overrides: str) -> dict[str, str]:
    values = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.tenant_id": "tenant-a",
        "ai-platform.workspace_id": "workspace-a",
        "ai-platform.user_id": "user-a",
        "ai-platform.session_id": "session-a",
        "ai-platform.run_id": "run-a",
        "ai-platform.attempt_id": "attempt-a",
        "ai-platform.sandbox_mode": "ephemeral",
        "ai-platform.security_profile": "trusted_internal",
        "ai-platform.browser_enabled": "false",
    }
    values.update(overrides)
    return values


def _legacy_filters(**overrides: str) -> dict[str, str]:
    values = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "sandbox_mode": "ephemeral",
        "security_profile": "trusted_internal",
    }
    values.update(overrides)
    return values


def test_legacy_cleanup_rebuilds_only_an_exact_persisted_terminal_identity():
    row = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "sandbox_mode": "ephemeral",
        "browser_enabled": False,
        "runtime_container_id": "osb-run-a",
        "runtime_container_name": "opensandbox-run-a",
        "runtime_executor_url": "http://opensandbox.test:18000",
        "runtime_workspace_container_path": "/workspace",
    }
    payload = {
        "attempt_id": "attempt-a",
        "container_id": "osb-run-a",
        "container_name": "opensandbox-run-a",
        "executor_url": "http://opensandbox.test:18000",
        "workspace_container_path": "/workspace",
        "security_profile": "trusted_internal",
        "labels": _legacy_metadata(),
    }

    cleanup_labels = trusted_internal_cleanup_labels_from_persisted_row(row, payload)
    remote_labels = normalize_opensandbox_metadata(payload["labels"])

    assert cleanup_labels == payload["labels"]
    assert trusted_internal_cleanup_identity_is_authorized(remote_labels, cleanup_labels)

    for mutation in (
        {"security_profile": "governed"},
        {"attempt_id": "other-attempt"},
        {"governed_egress_proof": {}},
    ):
        changed = deepcopy(payload)
        changed.update(mutation)
        assert trusted_internal_cleanup_labels_from_persisted_row(row, changed) is None


def test_legacy_orphan_cleanup_requires_complete_scope_and_rejects_governed_projection():
    filters = _legacy_filters()
    expected = trusted_internal_orphan_cleanup_metadata_filter(filters)

    assert expected is not None
    assert trusted_internal_orphan_cleanup_identity_is_authorized(
        normalize_opensandbox_metadata(_legacy_metadata()),
        filters,
    )
    assert trusted_internal_orphan_cleanup_metadata_filter({"tenant_id": "tenant-a"}) is None
    assert not trusted_internal_orphan_cleanup_identity_is_authorized(
        normalize_opensandbox_metadata(
            _legacy_metadata(**{"ai-platform.governed_egress.proof": "unexpected"})
        ),
        filters,
    )
