"""Canonical governed OpenSandbox policy and metadata helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.execution_boundary import (
    GOVERNED_EGRESS_PROOF_LABEL,
    governed_egress_previous_signing_keys,
    is_governed_egress_identity_proof,
    is_governed_egress_proof,
)
from app.platform.sandbox.docker_governed_network import governed_egress_proof_key_id
from app.runtime.sandbox.contracts import (
    ContainerLease,
    ContainerStatus,
    build_trusted_callback_target,
)
from app.runtime.sandbox.providers.opensandbox import metadata as opensandbox_metadata
from app.runtime.sandbox.workspace_permissions import RUNTIME_GID, RUNTIME_UID

SANDBOX_SECURITY_PROFILE_GOVERNED = "governed"
SANDBOX_SECURITY_PROFILE_INTERNAL_TEST = "internal-test"
SANDBOX_SECURITY_PROFILE_LABEL = "ai-platform.security_profile"
DIRECT_OPENSANDBOX_PROFILE_ID = "direct-opensandbox"
DIRECT_OPENSANDBOX_NETWORK_NAME = "ai-platform-opensandbox-egress-internal-v1"
DIRECT_OPENSANDBOX_POLICY_SUBJECT = "stateless-nginx-egress"
DIRECT_OPENSANDBOX_CALLBACK_SUBJECT = "api-callback-token-validation"
DIRECT_OPENSANDBOX_DENIAL_SUBJECT = "ai-platform-sandbox-runtime"
INTERNAL_TEST_OPENSANDBOX_PROFILE = "official-opensandbox-direct-v1"
_ORPHAN_SCOPE_KEYS = (
    "tenant_id",
    "workspace_id",
    "user_id",
    "session_id",
    "run_id",
    "attempt_id",
    "sandbox_mode",
)

_GOVERNED_EGRESS_PROOF_LABEL = "ai-platform.governed_egress.proof"
_GOVERNED_BRIDGE_PATHS = {
    "callback": "",
    "openai": "/openai/v1",
    "anthropic": "/anthropic",
}
_PROXY_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}\Z")


class OpenSandboxProfileConfigurationError(ValueError):
    """Report a bounded OpenSandbox egress configuration failure."""


@dataclass(frozen=True)
class ExecutorEgressBases:
    """Hold the three validated executor egress bases."""

    callback_base_url: str
    openai_base_url: str
    anthropic_base_url: str

    def callback_target(self):
        """Resolve the callback URL from the canonical egress proxy base."""

        parsed = urlsplit(self.callback_base_url)
        return build_trusted_callback_target(
            self.callback_base_url,
            extra_hosts=[parsed.hostname or ""],
        )


def _canonical_proxy_base(value: object) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise OpenSandboxProfileConfigurationError("OpenSandbox egress proxy base is invalid") from None
    host = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or not _PROXY_HOST.fullmatch(host)
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
        or parsed.netloc != f"{host}:{port}"
    ):
        raise OpenSandboxProfileConfigurationError("OpenSandbox egress proxy base is invalid") from None
    return urlunsplit((parsed.scheme, f"{host}:{port}", "", "", ""))


def governed_opensandbox_egress_bases(settings: Any) -> ExecutorEgressBases:
    """Return direct-mode bases for the one configured stateless egress proxy."""

    proxy = _canonical_proxy_base(getattr(settings, "opensandbox_egress_proxy_url", ""))
    return ExecutorEgressBases(
        callback_base_url=proxy,
        openai_base_url=f"{proxy}/openai/v1",
        anthropic_base_url=f"{proxy}/anthropic",
    )


def validate_opensandbox_image_reference(
    image: str,
    configured_digest: str,
    *,
    allow_local_image_id: bool,
) -> tuple[str, str]:
    """Validate an immutable repository digest or a legacy cleanup image ID."""

    if allow_local_image_id and re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        digest = image
    else:
        subject, separator, digest = image.partition("@")
        last_path_segment = subject.rsplit("/", 1)[-1]
        if (
            not separator
            or not subject
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in subject)
            or "@" in digest
            or ":" in last_path_segment
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            raise OpenSandboxProfileConfigurationError(
                "OpenSandbox executor image must be an immutable sha256 reference"
            ) from None
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", configured_digest):
        raise OpenSandboxProfileConfigurationError("OpenSandbox configured executor digest is invalid") from None
    if configured_digest != digest:
        raise OpenSandboxProfileConfigurationError(
            "OpenSandbox configured executor digest does not match image reference"
        ) from None
    return image, digest


def requested_opensandbox_image(
    settings: Any,
    *,
    allow_local_image_id: bool | None = None,
) -> tuple[str, str]:
    """Return only a validated configured image reference and matching digest."""

    if allow_local_image_id is None:
        allow_local_image_id = (
            str(getattr(settings, "deployment_environment", "") or "") == "test"
            and str(getattr(settings, "sandbox_container_provider", "") or "").strip().lower()
            == "opensandbox"
            and str(getattr(settings, "sandbox_security_profile", "") or "")
            == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST
            and str(getattr(settings, "opensandbox_expected_network_mode", "") or "") == "bridge"
        )
    image = str(getattr(settings, "opensandbox_executor_image", "") or "")
    if not image:
        image = str(getattr(settings, "sandbox_executor_image", "") or "")
    configured_digest = str(getattr(settings, "opensandbox_executor_image_digest", "") or "")
    return validate_opensandbox_image_reference(
        image,
        configured_digest,
        allow_local_image_id=allow_local_image_id,
    )


def runtime_scope_labels(request: Any) -> dict[str, str]:
    """Return the exact server-owned runtime scope labels for one request."""

    return {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": request.tenant_id,
        "ai-platform.workspace_id": request.workspace_id,
        "ai-platform.user_id": request.user_id,
        "ai-platform.session_id": request.session_id,
        "ai-platform.run_id": request.run_id,
        "ai-platform.attempt_id": request.attempt_id,
        "ai-platform.sandbox_mode": request.sandbox_mode,
        "ai-platform.browser_enabled": "true" if request.browser_enabled else "false",
    }


def opensandbox_container_name(run_id: str, attempt_id: str) -> str:
    """Return the local name bound to one exact OpenSandbox attempt."""

    return f"opensandbox-{run_id}-{attempt_id}"


def opensandbox_status_from_state(state: object) -> str:
    """Normalize the bounded OpenSandbox lifecycle states used by runtime cleanup."""

    normalized = str(state or "unknown").strip().lower()
    if normalized in {"running", "ready"}:
        return "running"
    if normalized in {"pending", "creating", "starting"}:
        return "created"
    if normalized in {"terminated", "killed", "deleted"}:
        return "removed"
    if normalized in {"failed", "error"}:
        return "exited"
    if normalized in {"paused", "suspended"}:
        return "paused"
    return normalized or "unknown"


def opensandbox_metadata_from_info(info: Any) -> dict[str, str]:
    """Return exact string metadata from an OpenSandbox SDK readback."""

    metadata = getattr(info, "metadata", None)
    if metadata is None and isinstance(info, dict):
        metadata = info.get("metadata")
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in metadata.items()
    ):
        return {}
    return dict(metadata)


def opensandbox_id(info: Any) -> str:
    """Return the normalized OpenSandbox identifier from an SDK readback."""

    value = getattr(info, "id", None)
    if value is None and isinstance(info, dict):
        value = info.get("id")
    return str(value or "")


def opensandbox_state(info: Any) -> str:
    """Return the raw bounded lifecycle state from an SDK readback."""

    status = getattr(info, "status", None)
    if isinstance(info, dict):
        status = info.get("status")
    state = getattr(status, "state", None)
    if state is None and isinstance(status, dict):
        state = status.get("state")
    if state is None:
        state = getattr(info, "state", None)
    return str(state or "unknown")


def opensandbox_status_from_info(info: Any) -> ContainerStatus | None:
    """Project authoritative OpenSandbox readback into the shared safe status model."""

    metadata = opensandbox_metadata_from_info(info)
    if metadata.get("ai-platform.owner") != "sandbox-runtime":
        return None
    sandbox_mode = metadata.get("ai-platform.sandbox_mode")
    if sandbox_mode not in {"ephemeral", "persistent"}:
        sandbox_mode = None
    sandbox_id = opensandbox_id(info)
    run_id = metadata.get("ai-platform.run_id")
    attempt_id = metadata.get("ai-platform.attempt_id")
    return ContainerStatus(
        container_id=sandbox_id,
        container_name=(
            opensandbox_container_name(run_id, attempt_id)
            if run_id and attempt_id
            else f"opensandbox-{sandbox_id}"
        ),
        provider="opensandbox",
        status=opensandbox_status_from_state(opensandbox_state(info)),
        tenant_id=metadata.get("ai-platform.tenant_id"),
        workspace_id=metadata.get("ai-platform.workspace_id"),
        user_id=metadata.get("ai-platform.user_id"),
        session_id=metadata.get("ai-platform.session_id"),
        run_id=run_id,
        sandbox_mode=sandbox_mode,
        browser_enabled=metadata.get("ai-platform.browser_enabled", "false").lower() == "true",
        executor_url=None,
        detail={"labels": metadata},
    )


def governed_opensandbox_lease_labels(
    request: Any,
    capability: Any,
    *,
    executor_identity_labels: Mapping[str, str],
    skill_mount_labels: Mapping[str, str],
    governed_proof_label: str | None,
) -> dict[str, str]:
    """Build governed OpenSandbox metadata from the admitted capability."""

    labels = runtime_scope_labels(request)
    labels.update(
        {
            "ai-platform.provider_backend": "opensandbox",
            "ai-platform.executor.requested_image": capability.requested_image,
            "ai-platform.executor.requested_image_digest": capability.requested_image_digest,
            "ai-platform.external_egress.profile_version": "v1",
            "ai-platform.external_egress.profile_id": capability.profile_id,
            "ai-platform.external_egress.endpoint_sha256": hashlib.sha256(
                capability.endpoint.encode("utf-8")
            ).hexdigest(),
            "ai-platform.external_egress.runtime_identity": capability.runtime_identity,
            "ai-platform.external_egress.network_mode": capability.network_mode,
            "ai-platform.runtime_subject": capability.runtime_subject,
            "ai-platform.external_egress.gateway_policy_subject": capability.gateway_policy_subject,
            "ai-platform.external_egress.callback_boundary_subject": capability.callback_boundary_subject,
            "ai-platform.external_egress.deny_audit_subject": capability.deny_audit_subject,
            "ai-platform.external_egress.deny_counter_subject": capability.deny_counter_subject,
            "ai-platform.external_egress.profile_requested_image": capability.requested_image,
            "ai-platform.external_egress.profile_requested_image_digest": capability.requested_image_digest,
            "ai-platform.external_egress.upstream_bridge_version": capability.upstream_bridge_version,
            "ai-platform.external_egress.callback_base_sha256": hashlib.sha256(
                capability.callback_base_url.encode("utf-8")
            ).hexdigest(),
            "ai-platform.external_egress.openai_base_sha256": hashlib.sha256(
                capability.openai_base_url.encode("utf-8")
            ).hexdigest(),
            "ai-platform.external_egress.anthropic_base_sha256": hashlib.sha256(
                capability.anthropic_base_url.encode("utf-8")
            ).hexdigest(),
            "ai-platform.external_egress.profile_expires_at": capability.expires_at,
        }
    )
    if governed_proof_label is not None:
        labels[_GOVERNED_EGRESS_PROOF_LABEL] = governed_proof_label
    labels.update(executor_identity_labels)
    labels.update(skill_mount_labels)
    return labels


def internal_test_opensandbox_lease_labels(
    request: Any,
    settings: Any,
    *,
    executor_identity_labels: Mapping[str, str],
    skill_mount_labels: Mapping[str, str],
) -> dict[str, str]:
    """Build explicit non-production metadata for direct official OpenSandbox acceptance."""

    requested_image, requested_digest = requested_opensandbox_image(settings)
    labels = runtime_scope_labels(request)
    labels.update(
        {
            "ai-platform.provider_backend": "opensandbox",
            SANDBOX_SECURITY_PROFILE_LABEL: SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
            "ai-platform.internal_test.profile": INTERNAL_TEST_OPENSANDBOX_PROFILE,
            "ai-platform.internal_test.network_mode": str(
                getattr(settings, "opensandbox_expected_network_mode", "") or ""
            ),
            "ai-platform.internal_test.runtime_identity": "runsc",
            "ai-platform.internal_test.risk": "bridge-non-production",
            "ai-platform.executor.requested_image": requested_image,
            "ai-platform.executor.requested_image_digest": requested_digest,
            "ai-platform.runtime_subject": str(getattr(settings, "sandbox_runtime_subject", "") or ""),
        }
    )
    labels.update(executor_identity_labels)
    labels.update(skill_mount_labels)
    return labels


def internal_test_orphan_cleanup_metadata_filter(filters: Mapping[str, str]) -> dict[str, str] | None:
    """Return an exact direct-mode inventory filter only for one complete runtime scope."""

    if filters.get("security_profile") != SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
        return None
    if any(not str(filters.get(key) or "").strip() for key in _ORPHAN_SCOPE_KEYS):
        return None
    metadata = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.provider_backend": "opensandbox",
        SANDBOX_SECURITY_PROFILE_LABEL: SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
    }
    metadata.update({f"ai-platform.{key}": str(filters[key]) for key in _ORPHAN_SCOPE_KEYS})
    return metadata


def internal_test_orphan_cleanup_expected_labels(
    filters: Mapping[str, str],
    settings: Any,
) -> dict[str, str] | None:
    """Return all immutable direct-mode evidence required before orphan deletion."""

    metadata = internal_test_orphan_cleanup_metadata_filter(filters)
    if metadata is None:
        return None
    requested_image, requested_digest = requested_opensandbox_image(settings)
    metadata.update(
        {
            "ai-platform.internal_test.profile": INTERNAL_TEST_OPENSANDBOX_PROFILE,
            "ai-platform.internal_test.network_mode": "bridge",
            "ai-platform.internal_test.runtime_identity": "runsc",
            "ai-platform.internal_test.risk": "bridge-non-production",
            "ai-platform.executor.requested_image": requested_image,
            "ai-platform.executor.requested_image_digest": requested_digest,
            "ai-platform.runtime_subject": str(getattr(settings, "sandbox_runtime_subject", "") or ""),
        }
    )
    return metadata


_OPENSANDBOX_EXTERNAL_EGRESS_RUNTIME_IDENTITY = "runsc"


def _required_remote_string(labels: object, key: str) -> str | None:
    if not isinstance(labels, dict):
        return None
    value = labels.get(key)
    return value if isinstance(value, str) and value else None


def _governed_cleanup_expected_binding(
    status: ContainerStatus,
    lease: ContainerLease,
) -> dict[str, object] | None:
    labels = status.detail.get("labels")
    attempt_id = _required_remote_string(lease.labels, "ai-platform.attempt_id")
    if not isinstance(labels, dict) or attempt_id is None:
        return None

    expected_remote = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.provider_backend": "opensandbox",
        "ai-platform.tenant_id": lease.tenant_id,
        "ai-platform.workspace_id": lease.workspace_id,
        "ai-platform.user_id": lease.user_id,
        "ai-platform.session_id": lease.session_id,
        "ai-platform.run_id": lease.run_id,
        "ai-platform.attempt_id": attempt_id,
        "ai-platform.sandbox_mode": lease.sandbox_mode,
    }
    if not opensandbox_metadata.opensandbox_metadata_matches(labels, expected_remote):
        return None

    required_remote_keys = (
        SANDBOX_SECURITY_PROFILE_LABEL,
        "ai-platform.external_egress.runtime_identity",
        "ai-platform.external_egress.network_mode",
        "ai-platform.runtime_subject",
        "ai-platform.external_egress.gateway_policy_subject",
        "ai-platform.external_egress.callback_boundary_subject",
        "ai-platform.external_egress.deny_audit_subject",
        "ai-platform.external_egress.deny_counter_subject",
        "ai-platform.external_egress.profile_id",
        "ai-platform.external_egress.profile_version",
        "ai-platform.external_egress.endpoint_sha256",
        "ai-platform.external_egress.executor_image",
        "ai-platform.external_egress.executor_image_digest",
        "ai-platform.external_egress.upstream_bridge_version",
        "ai-platform.external_egress.callback_base_url_sha256",
        "ai-platform.external_egress.openai_base_url_sha256",
        "ai-platform.external_egress.anthropic_base_url_sha256",
    )
    if any(_required_remote_string(labels, key) is None for key in required_remote_keys):
        return None
    if (
        _required_remote_string(labels, SANDBOX_SECURITY_PROFILE_LABEL)
        != SANDBOX_SECURITY_PROFILE_GOVERNED
        or _required_remote_string(labels, "ai-platform.external_egress.runtime_identity")
        != _OPENSANDBOX_EXTERNAL_EGRESS_RUNTIME_IDENTITY
        or _required_remote_string(labels, "ai-platform.external_egress.network_mode")
        != DIRECT_OPENSANDBOX_NETWORK_NAME
        or _required_remote_string(labels, "ai-platform.external_egress.profile_version") != "v1"
        or _required_remote_string(labels, "ai-platform.external_egress.upstream_bridge_version") != "v1"
    ):
        return None

    return {
        "tenant_id": lease.tenant_id,
        "workspace_id": lease.workspace_id,
        "user_id": lease.user_id,
        "session_id": lease.session_id,
        "run_id": lease.run_id,
        "attempt_id": attempt_id,
        "lease_identity": f"opensandbox:{lease.container_name}:{lease.container_id}",
    }


def _is_internal_test_opensandbox(settings: Any) -> bool:
    return bool(
        str(getattr(settings, "sandbox_security_profile", "") or "")
        == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST
        and str(getattr(settings, "deployment_environment", "") or "") == "test"
        and str(getattr(settings, "sandbox_container_provider", "") or "").strip().lower()
        == "opensandbox"
        and str(getattr(settings, "opensandbox_expected_network_mode", "") or "") == "bridge"
    )


def opensandbox_cleanup_identity_is_authorized(
    status: ContainerStatus,
    lease: ContainerLease,
    settings: Any,
    *,
    now: datetime,
) -> bool:
    """Authorize cleanup only from exact provider-owned remote identity."""

    status_labels = status.detail.get("labels")
    if not isinstance(status_labels, dict):
        return False
    lease_profile = str(
        lease.labels.get(SANDBOX_SECURITY_PROFILE_LABEL) or SANDBOX_SECURITY_PROFILE_GOVERNED
    )
    if lease_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
        try:
            if not _is_internal_test_opensandbox(settings):
                return False
            requested_image, requested_digest = requested_opensandbox_image(settings)
            normalized_image = opensandbox_metadata.normalize_opensandbox_metadata(
                {
                    "ai-platform.executor.requested_image": requested_image,
                    "ai-platform.executor.requested_image_digest": requested_digest,
                }
            )
        except (OpenSandboxProfileConfigurationError, opensandbox_metadata.OpenSandboxMetadataError):
            return False
        return bool(
            opensandbox_metadata.opensandbox_status_matches_lease(status_labels, lease.labels)
            and status_labels.get(SANDBOX_SECURITY_PROFILE_LABEL)
            == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST
            and status_labels.get("ai-platform.internal_test.profile")
            == INTERNAL_TEST_OPENSANDBOX_PROFILE
            and status_labels.get("ai-platform.internal_test.network_mode") == "bridge"
            and status_labels.get("ai-platform.internal_test.runtime_identity") == "runsc"
            and status_labels.get("ai-platform.executor.requested_image")
            == normalized_image["ai-platform.executor.requested_image"]
            and status_labels.get("ai-platform.executor.requested_image_digest")
            == normalized_image["ai-platform.executor.requested_image_digest"]
            and lease.labels.get("ai-platform.runtime_subject")
            == str(getattr(settings, "sandbox_runtime_subject", "") or "")
        )
    if lease_profile != SANDBOX_SECURITY_PROFILE_GOVERNED:
        return False
    encoded_proof = lease.labels.get(GOVERNED_EGRESS_PROOF_LABEL)
    if not isinstance(encoded_proof, str):
        return False
    try:
        proof = json.loads(encoded_proof)
    except (TypeError, ValueError):
        return False
    if not isinstance(proof, dict):
        return False
    expected_binding = _governed_cleanup_expected_binding(status, lease)
    if expected_binding is None:
        return False
    return is_governed_egress_proof(
        proof,
        provider="opensandbox",
        signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
        signing_key_id=governed_egress_proof_key_id(settings),
        previous_signing_keys=governed_egress_previous_signing_keys(
            getattr(settings, "sandbox_egress_proof_previous_keys_json", "")
        ),
        allow_previous_keys=True,
        expected_binding=expected_binding,
        now=now,
        require_fresh=False,
    )


def opensandbox_renewal_identity_is_authorized(
    status: ContainerStatus,
    lease: ContainerLease,
    settings: Any,
    *,
    now: datetime,
) -> bool:
    """Authorize renewal from an active DB fence and immutable remote identity."""

    labels = status.detail.get("labels")
    if not isinstance(labels, dict):
        return False
    expected_executor_identity = {
        "ai-platform.executor.user": f"{RUNTIME_UID}:{RUNTIME_GID}",
        "ai-platform.executor.uid": str(RUNTIME_UID),
        "ai-platform.executor.gid": str(RUNTIME_GID),
        "ai-platform.executor.identity_evidence": "authenticated-runtime-endpoint",
    }
    if not opensandbox_metadata.opensandbox_metadata_matches(labels, expected_executor_identity):
        return False
    if not opensandbox_cleanup_identity_is_authorized(status, lease, settings, now=now):
        return False
    lease_profile = str(
        lease.labels.get(SANDBOX_SECURITY_PROFILE_LABEL) or SANDBOX_SECURITY_PROFILE_GOVERNED
    )
    if lease_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
        return True
    encoded_proof = lease.labels.get(GOVERNED_EGRESS_PROOF_LABEL)
    if not isinstance(encoded_proof, str):
        return False
    try:
        proof = json.loads(encoded_proof)
    except (TypeError, ValueError):
        return False
    expected_binding = _governed_cleanup_expected_binding(status, lease)
    return bool(
        expected_binding is not None
        and is_governed_egress_identity_proof(
            proof,
            provider="opensandbox",
            signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
            signing_key_id=governed_egress_proof_key_id(settings),
            expected_binding=expected_binding,
            now=now,
        )
    )
