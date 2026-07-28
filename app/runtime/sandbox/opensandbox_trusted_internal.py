from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.runtime.sandbox.contracts import (
    CallbackTargetValidationError,
    ContainerStatus,
    build_trusted_callback_target,
)
from app.runtime.sandbox.providers.opensandbox.metadata import (
    OpenSandboxMetadataError,
    normalize_opensandbox_metadata,
    opensandbox_metadata_matches,
)

SANDBOX_SECURITY_PROFILE_GOVERNED = "governed"
SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL = "trusted_internal"
SANDBOX_SECURITY_PROFILE_LABEL = "ai-platform.security_profile"

_GOVERNED_EGRESS_PROOF_LABEL = "ai-platform.governed_egress.proof"
_GOVERNED_BRIDGE_PATHS = {
    "callback": "",
    "openai": "/openai/v1",
    "anthropic": "/anthropic",
}
_TRUSTED_DIRECT_PATHS = {
    "callback": "",
    "openai": "/v1",
    "anthropic": "",
}
_TRUSTED_ORPHAN_CLEANUP_FILTER_LABELS = {
    "tenant_id": "ai-platform.tenant_id",
    "workspace_id": "ai-platform.workspace_id",
    "user_id": "ai-platform.user_id",
    "session_id": "ai-platform.session_id",
    "run_id": "ai-platform.run_id",
    "attempt_id": "ai-platform.attempt_id",
    "sandbox_mode": "ai-platform.sandbox_mode",
    "security_profile": SANDBOX_SECURITY_PROFILE_LABEL,
}
_DNS_HOSTNAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


class OpenSandboxProfileConfigurationError(ValueError):
    """Report a bounded OpenSandbox security-profile configuration failure."""


@dataclass(frozen=True)
class ExecutorEgressBases:
    """Hold the three validated executor egress bases for one profile."""

    callback_base_url: str
    openai_base_url: str
    anthropic_base_url: str

    def callback_target(self):
        """Resolve the callback URL from the same canonical base used in executor env."""

        parsed = urlsplit(self.callback_base_url)
        return build_trusted_callback_target(
            self.callback_base_url,
            extra_hosts=[parsed.hostname or ""],
        )


@dataclass(frozen=True)
class OpenSandboxSecurityProfile:
    """Represent one canonical OpenSandbox profile without retaining credentials."""

    name: str
    egress_bases: ExecutorEgressBases
    requested_image: str
    requested_image_digest: str

    @property
    def governed(self) -> bool:
        """Return whether the strict governed profile was selected."""

        return self.name == SANDBOX_SECURITY_PROFILE_GOVERNED


def configured_security_profile(settings: Any) -> str:
    """Return the configured profile name with the governed compatibility default."""

    return str(
        getattr(settings, "sandbox_security_profile", SANDBOX_SECURITY_PROFILE_GOVERNED)
        or SANDBOX_SECURITY_PROFILE_GOVERNED
    )


def require_provider_profile_compatibility(settings: Any, provider_name: object) -> str:
    """Reject the relaxed profile unless OpenSandbox is the selected provider."""

    selected_provider = str(provider_name or "").strip().lower()
    if (
        configured_security_profile(settings) == SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        and selected_provider != "opensandbox"
    ):
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal sandbox security profile requires the OpenSandbox provider"
        )
    return selected_provider


def _canonical_governed_bridge_base(value: object, *, kind: str) -> str:
    raw = str(value or "")
    expected_path = _GOVERNED_BRIDGE_PATHS[kind]
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise OpenSandboxProfileConfigurationError("OpenSandbox upstream bridge base is invalid") from None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not _DNS_HOSTNAME.fullmatch(host)
        or host != host.lower()
        or parsed.username
        or parsed.password
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
        or parsed.netloc != f"{host}:{port}"
        or host in {"api.sandbox.internal", "host.docker.internal"}
    ):
        raise OpenSandboxProfileConfigurationError("OpenSandbox upstream bridge base is invalid") from None
    canonical = urlunsplit(("https", f"{host}:{port}", expected_path, "", ""))
    if raw != canonical:
        raise OpenSandboxProfileConfigurationError("OpenSandbox upstream bridge base is invalid") from None
    return canonical


def _governed_egress_bases(settings: Any) -> ExecutorEgressBases:
    bases = ExecutorEgressBases(
        callback_base_url=_canonical_governed_bridge_base(
            getattr(settings, "opensandbox_external_egress_callback_base_url", ""),
            kind="callback",
        ),
        openai_base_url=_canonical_governed_bridge_base(
            getattr(settings, "opensandbox_external_egress_openai_base_url", ""),
            kind="openai",
        ),
        anthropic_base_url=_canonical_governed_bridge_base(
            getattr(settings, "opensandbox_external_egress_anthropic_base_url", ""),
            kind="anthropic",
        ),
    )
    origins = {
        (urlsplit(value).hostname, urlsplit(value).port)
        for value in (
            bases.callback_base_url,
            bases.openai_base_url,
            bases.anthropic_base_url,
        )
    }
    if len(origins) != 1:
        raise OpenSandboxProfileConfigurationError("OpenSandbox upstream bridge origin drift detected")
    return bases


def governed_opensandbox_egress_bases(settings: Any) -> ExecutorEgressBases:
    """Return the strict profile's canonical, same-origin upstream bridge bases."""

    return _governed_egress_bases(settings)


def validate_opensandbox_image_reference(
    image: str,
    configured_digest: str,
    *,
    allow_local_image_id: bool,
) -> tuple[str, str]:
    """Validate an immutable repository digest or an explicitly allowed local image ID."""

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
    allow_local_image_id: bool = False,
) -> tuple[str, str]:
    """Return only a validated configured image reference and matching digest."""

    image = str(getattr(settings, "opensandbox_executor_image", "") or "")
    if not image:
        image = str(getattr(settings, "sandbox_executor_image", "") or "")
    configured_digest = str(getattr(settings, "opensandbox_executor_image_digest", "") or "")
    return validate_opensandbox_image_reference(
        image,
        configured_digest,
        allow_local_image_id=allow_local_image_id,
    )


def _private_ipv4(value: object) -> str | None:
    try:
        parsed = ipaddress.ip_address(str(value or ""))
    except ValueError:
        return None
    if (
        parsed.version != 4
        or not parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_unspecified
        or parsed.is_reserved
    ):
        return None
    return str(parsed)


def _validate_trusted_endpoint(settings: Any) -> None:
    protocol = str(getattr(settings, "opensandbox_protocol", "") or "")
    domain = str(getattr(settings, "opensandbox_domain", "") or "")
    try:
        parsed = urlsplit(f"{protocol}://{domain}")
        port = parsed.port
    except ValueError:
        raise OpenSandboxProfileConfigurationError("trusted_internal OpenSandbox endpoint is invalid") from None
    host = _private_ipv4(parsed.hostname)
    if (
        protocol not in {"http", "https"}
        or host is None
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
        or domain != f"{host}:{port}"
        or bool(getattr(settings, "opensandbox_use_server_proxy", False))
    ):
        raise OpenSandboxProfileConfigurationError("trusted_internal OpenSandbox endpoint is invalid") from None


def _canonical_trusted_direct_base(value: object, *, kind: str) -> str:
    raw = str(value or "")
    expected_path = _TRUSTED_DIRECT_PATHS[kind]
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox dedicated base is invalid"
        ) from None
    host = _private_ipv4(parsed.hostname)
    if (
        parsed.scheme not in {"http", "https"}
        or host is None
        or parsed.username
        or parsed.password
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
        or parsed.netloc != f"{host}:{port}"
    ):
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox dedicated base is invalid"
        ) from None
    canonical = urlunsplit((parsed.scheme, f"{host}:{port}", expected_path, "", ""))
    if raw != canonical:
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox dedicated base is invalid"
        ) from None
    return canonical


def _trusted_egress_bases(settings: Any) -> ExecutorEgressBases:
    bases = ExecutorEgressBases(
        callback_base_url=_canonical_trusted_direct_base(
            getattr(settings, "opensandbox_trusted_internal_callback_base_url", ""),
            kind="callback",
        ),
        openai_base_url=_canonical_trusted_direct_base(
            getattr(settings, "opensandbox_trusted_internal_openai_base_url", ""),
            kind="openai",
        ),
        anthropic_base_url=_canonical_trusted_direct_base(
            getattr(settings, "opensandbox_trusted_internal_anthropic_base_url", ""),
            kind="anthropic",
        ),
    )
    parsed_bases = {
        "callback": urlsplit(bases.callback_base_url),
        "openai": urlsplit(bases.openai_base_url),
        "anthropic": urlsplit(bases.anthropic_base_url),
    }
    if len({parsed.hostname for parsed in parsed_bases.values()}) != 1:
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox dedicated base host drift detected"
        )
    openai = parsed_bases["openai"]
    anthropic = parsed_bases["anthropic"]
    if (openai.scheme, openai.hostname, openai.port) != (
        anthropic.scheme,
        anthropic.hostname,
        anthropic.port,
    ):
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox model base origin drift detected"
        )
    try:
        bases.callback_target()
    except CallbackTargetValidationError:
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox dedicated base is invalid"
        ) from None
    return bases


def resolve_opensandbox_security_profile(settings: Any) -> OpenSandboxSecurityProfile:
    """Canonicalize the configured OpenSandbox profile before any SDK side effect."""

    profile_name = configured_security_profile(settings)
    if profile_name == SANDBOX_SECURITY_PROFILE_GOVERNED:
        return OpenSandboxSecurityProfile(
            name=profile_name,
            egress_bases=_governed_egress_bases(settings),
            requested_image="",
            requested_image_digest="",
        )
    if profile_name != SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL:
        raise OpenSandboxProfileConfigurationError("OpenSandbox sandbox security profile is invalid")
    api_key = getattr(settings, "opensandbox_api_key", "")
    if (
        not isinstance(api_key, str)
        or not api_key
        or api_key != api_key.strip()
        or len(api_key) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in api_key)
    ):
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox API credential is unavailable"
        )
    _validate_trusted_endpoint(settings)
    if not str(getattr(settings, "opensandbox_executor_image", "") or ""):
        raise OpenSandboxProfileConfigurationError(
            "trusted_internal OpenSandbox executor image is unavailable"
        )
    requested_image, requested_image_digest = requested_opensandbox_image(
        settings,
        allow_local_image_id=True,
    )
    return OpenSandboxSecurityProfile(
        name=profile_name,
        egress_bases=_trusted_egress_bases(settings),
        requested_image=requested_image,
        requested_image_digest=requested_image_digest,
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
    """Return only string metadata from an OpenSandbox SDK readback."""

    metadata = getattr(info, "metadata", None)
    if metadata is None and isinstance(info, dict):
        metadata = info.get("metadata")
    return {str(key): str(value) for key, value in (metadata or {}).items()}


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


def trusted_internal_lease_labels(
    request: Any,
    profile: OpenSandboxSecurityProfile,
    *,
    executor_identity_labels: Mapping[str, str],
    skill_mount_labels: Mapping[str, str],
) -> dict[str, str]:
    """Build safe trusted-internal lease labels without credentials or governed proof."""

    if profile.name != SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL:
        raise OpenSandboxProfileConfigurationError("trusted_internal OpenSandbox profile is unavailable")
    labels = runtime_scope_labels(request)
    labels.update(
        {
            "ai-platform.provider_backend": "opensandbox",
            SANDBOX_SECURITY_PROFILE_LABEL: SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL,
            "ai-platform.executor.requested_image": profile.requested_image,
            "ai-platform.executor.requested_image_digest": profile.requested_image_digest,
        }
    )
    labels.update(executor_identity_labels)
    labels.update(skill_mount_labels)
    return labels


def governed_opensandbox_lease_labels(
    request: Any,
    capability: Any,
    *,
    executor_identity_labels: Mapping[str, str],
    skill_mount_labels: Mapping[str, str],
    governed_proof_label: str | None,
) -> dict[str, str]:
    """Build the unchanged governed OpenSandbox metadata alongside the relaxed profile."""

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


def _has_governed_projection(labels: Mapping[object, object]) -> bool:
    return _GOVERNED_EGRESS_PROOF_LABEL in labels or any(
        str(key).startswith(("ai-platform.external_egress.", "ai-platform.governed_egress."))
        or str(key) == "ai-platform.runtime_subject"
        for key in labels
    )


def trusted_internal_orphan_cleanup_metadata_filter(
    filters: Mapping[str, object],
) -> dict[str, str] | None:
    """Return an exact remote filter only for a complete trusted lease scope."""

    if set(filters) != set(_TRUSTED_ORPHAN_CLEANUP_FILTER_LABELS):
        return None
    values: dict[str, str] = {}
    for field, label in _TRUSTED_ORPHAN_CLEANUP_FILTER_LABELS.items():
        value = filters.get(field)
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return None
        values[label] = value
    if (
        values["ai-platform.sandbox_mode"] not in {"ephemeral", "persistent"}
        or values[SANDBOX_SECURITY_PROFILE_LABEL] != SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
    ):
        return None
    raw_metadata = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.provider_backend": "opensandbox",
        **values,
    }
    try:
        return normalize_opensandbox_metadata(raw_metadata)
    except OpenSandboxMetadataError:
        return None


def trusted_internal_orphan_cleanup_identity_is_authorized(
    status_labels: object,
    filters: Mapping[str, object],
) -> bool:
    """Match an authoritative readback to one complete trusted cleanup scope."""

    expected = trusted_internal_orphan_cleanup_metadata_filter(filters)
    return (
        expected is not None
        and isinstance(status_labels, dict)
        and not _has_governed_projection(status_labels)
        and all(status_labels.get(key) == value for key, value in expected.items())
    )


def trusted_internal_cleanup_identity_is_authorized(
    status_labels: object,
    lease_labels: Mapping[str, str],
) -> bool:
    """Verify exact safe trusted-internal identity before destructive cleanup."""

    if not isinstance(status_labels, dict):
        return False
    image = str(lease_labels.get("ai-platform.executor.requested_image") or "")
    digest = str(lease_labels.get("ai-platform.executor.requested_image_digest") or "")
    if bool(image) != bool(digest):
        return False
    try:
        image_valid = bool(image) and validate_opensandbox_image_reference(
            image,
            digest,
            allow_local_image_id=True,
        ) == (image, digest)
    except OpenSandboxProfileConfigurationError:
        image_valid = False
    persisted_scope = {
        "ai-platform.owner",
        "ai-platform.tenant_id",
        "ai-platform.workspace_id",
        "ai-platform.user_id",
        "ai-platform.session_id",
        "ai-platform.run_id",
        "ai-platform.attempt_id",
        "ai-platform.sandbox_mode",
        "ai-platform.browser_enabled",
        "ai-platform.provider_backend",
        SANDBOX_SECURITY_PROFILE_LABEL,
    }
    scope_valid = (
        set(lease_labels) == persisted_scope
        and all(isinstance(value, str) and value for value in lease_labels.values())
        and lease_labels.get("ai-platform.owner") == "sandbox-runtime"
        and lease_labels.get("ai-platform.provider_backend") == "opensandbox"
        and lease_labels.get(SANDBOX_SECURITY_PROFILE_LABEL) == SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        and lease_labels.get("ai-platform.sandbox_mode") in {"ephemeral", "persistent"}
        and lease_labels.get("ai-platform.browser_enabled") in {"true", "false"}
    )
    return (
        (image_valid or scope_valid)
        and status_labels.get(SANDBOX_SECURITY_PROFILE_LABEL) == SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        and status_labels.get("ai-platform.provider_backend") == "opensandbox"
        and opensandbox_metadata_matches(status_labels, lease_labels)
        and not _has_governed_projection(lease_labels)
        and not _has_governed_projection(status_labels)
    )


def trusted_internal_persisted_runtime_labels(
    lease: Any,
    request: Any,
    workspace: Any,
    *,
    configured_profile: str,
    has_executor_auth: bool,
) -> dict[str, str]:
    """Validate one live trusted lease and return its bounded persistence labels."""

    expected_labels = {
        **runtime_scope_labels(request),
        "ai-platform.provider_backend": "opensandbox",
        SANDBOX_SECURITY_PROFILE_LABEL: SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL,
    }
    if (
        configured_profile != SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        or lease.provider != "opensandbox"
        or any(str(lease.labels.get(key) or "") != expected for key, expected in expected_labels.items())
        or lease.tenant_id != request.tenant_id == workspace.tenant_id
        or lease.workspace_id != request.workspace_id == workspace.workspace_id
        or lease.user_id != request.user_id == workspace.user_id
        or lease.session_id != request.session_id == workspace.session_id
        or lease.run_id != request.run_id == workspace.run_id
        or lease.sandbox_mode != request.sandbox_mode
        or lease.browser_enabled != request.browser_enabled
        or lease.workspace_host_path != workspace.workspace_host_path
        or lease.workspace_container_path != workspace.workspace_container_path
        or not has_executor_auth
        or not str(lease.labels.get("ai-platform.executor.requested_image") or "")
        or not str(lease.labels.get("ai-platform.executor.requested_image_digest") or "")
        or _has_governed_projection(lease.labels)
    ):
        raise ValueError("trusted_internal_runtime_lease_invalid")
    return {key: str(lease.labels[key]) for key in expected_labels}


def trusted_internal_runtime_lease_payload_matches_row(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    """Verify exact stored row, payload, scope, attempt and runtime bindings."""

    labels = payload.get("labels")
    if not isinstance(labels, dict):
        return False
    required_row_strings = {
        "tenant_id": row.get("tenant_id"),
        "workspace_id": row.get("workspace_id"),
        "user_id": row.get("user_id"),
        "session_id": row.get("session_id"),
        "run_id": row.get("run_id"),
        "sandbox_mode": row.get("sandbox_mode"),
        "runtime_container_id": row.get("runtime_container_id"),
        "runtime_container_name": row.get("runtime_container_name"),
        "runtime_executor_url": row.get("runtime_executor_url"),
        "runtime_workspace_container_path": row.get("runtime_workspace_container_path"),
    }
    if any(not isinstance(value, str) or not value for value in required_row_strings.values()):
        return False
    if required_row_strings["sandbox_mode"] not in {"ephemeral", "persistent"}:
        return False
    browser_enabled = row.get("browser_enabled")
    attempt_id = payload.get("attempt_id")
    if not isinstance(browser_enabled, bool) or not isinstance(attempt_id, str) or not attempt_id:
        return False
    if any(
        payload.get(payload_key) != required_row_strings[row_key]
        for payload_key, row_key in (
            ("container_id", "runtime_container_id"),
            ("container_name", "runtime_container_name"),
            ("executor_url", "runtime_executor_url"),
            ("workspace_container_path", "runtime_workspace_container_path"),
        )
    ):
        return False
    expected_labels = {
        "ai-platform.owner": "sandbox-runtime",
        "ai-platform.tenant_id": required_row_strings["tenant_id"],
        "ai-platform.workspace_id": required_row_strings["workspace_id"],
        "ai-platform.user_id": required_row_strings["user_id"],
        "ai-platform.session_id": required_row_strings["session_id"],
        "ai-platform.run_id": required_row_strings["run_id"],
        "ai-platform.attempt_id": attempt_id,
        "ai-platform.sandbox_mode": required_row_strings["sandbox_mode"],
        "ai-platform.browser_enabled": "true" if browser_enabled else "false",
        "ai-platform.provider_backend": "opensandbox",
        SANDBOX_SECURITY_PROFILE_LABEL: SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL,
    }
    if set(labels) != set(expected_labels) or any(
        str(labels.get(key) or "") != expected for key, expected in expected_labels.items()
    ):
        return False
    if _has_governed_projection(labels):
        return False
    return not any(
        key == "governed_egress_proof" or str(key).startswith("governed_egress_")
        for key in payload
    )


def trusted_internal_cleanup_labels_from_persisted_row(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, str] | None:
    """Rebuild only a complete persisted trusted scope for provider-side cleanup."""

    if (
        payload.get("security_profile") != SANDBOX_SECURITY_PROFILE_TRUSTED_INTERNAL
        or not trusted_internal_runtime_lease_payload_matches_row(row, payload)
    ):
        return None
    labels = payload.get("labels")
    return {str(key): str(value) for key, value in labels.items()} if isinstance(labels, dict) else None
