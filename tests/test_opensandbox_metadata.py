import pytest

from app.runtime.sandbox.opensandbox_policy import opensandbox_metadata_from_info
from app.runtime.sandbox.providers.opensandbox.metadata import (
    OpenSandboxMetadataError,
    normalize_opensandbox_metadata,
    opensandbox_metadata_matches,
)


def _generated_governed_metadata() -> dict[str, str]:
    """Mirror the server-owned OpenSandbox create metadata shape without runtime values."""

    return {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": "tenant-a",
        "ai-platform.workspace_id": "workspace-a",
        "ai-platform.user_id": "user-a",
        "ai-platform.session_id": "session-a",
        "ai-platform.run_id": "run-a",
        "ai-platform.attempt_id": "attempt-a",
        "ai-platform.sandbox_mode": "ephemeral",
        "ai-platform.browser_enabled": "false",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.runtime_subject": "r" * 68,
        "ai-platform.executor.requested_image_digest": "sha256:" + "a" * 64,
    }


def test_real_generated_metadata_shape_normalizes_the_68_character_runtime_subject():
    raw = _generated_governed_metadata()

    normalized = normalize_opensandbox_metadata(raw)

    # This names the failing server-owned category without exposing its runtime value.
    assert raw["ai-platform.runtime_subject"] != normalized["ai-platform.runtime_subject"]
    assert len(normalized["ai-platform.runtime_subject"]) <= 63
    assert normalized["ai-platform.executor.requested_image_digest"] != raw[
        "ai-platform.executor.requested_image_digest"
    ]


def test_metadata_tokens_are_deterministic_and_domain_separated():
    first = normalize_opensandbox_metadata({"ai-platform.runtime_subject": "r" * 68})
    repeated = normalize_opensandbox_metadata({"ai-platform.runtime_subject": "r" * 68})
    changed = normalize_opensandbox_metadata({"ai-platform.runtime_subject": "s" * 68})
    other_key = normalize_opensandbox_metadata({"ai-platform.run_id": "r" * 68})

    assert first == repeated
    assert first["ai-platform.runtime_subject"] != changed["ai-platform.runtime_subject"]
    assert first["ai-platform.runtime_subject"] != other_key["ai-platform.run_id"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"ai-platform.owner": 1},
        {1: "sandbox-runtime"},
        [("ai-platform.owner", "sandbox-runtime")],
    ],
)
def test_authoritative_metadata_readback_rejects_non_string_mappings(metadata):
    assert opensandbox_metadata_from_info({"metadata": metadata}) == {}


@pytest.mark.parametrize(
    "observed",
    [
        {"ai-platform.owner": 1},
        {1: "sandbox-runtime"},
    ],
)
def test_metadata_match_rejects_non_string_remote_entries(observed):
    assert not opensandbox_metadata_matches(
        observed,
        {"ai-platform.owner": "sandbox-runtime"},
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"ai-platform.owner": "sandbox-runtime", "bad key": "value"},
        {"ai-platform.owner": "sandbox-runtime", "ai-platform.runtime_subject": "\x01"},
        {"ai-platform.owner": "sandbox-runtime", "ai-platform.runtime_subject": "\x00"},
    ],
)
def test_metadata_normalizer_rejects_provider_invalid_entries_before_create(metadata):
    with pytest.raises(OpenSandboxMetadataError):
        normalize_opensandbox_metadata(metadata)
