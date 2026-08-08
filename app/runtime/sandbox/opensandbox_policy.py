"""Canonical governed OpenSandbox policy and metadata helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.runtime.sandbox.contracts import (
    ContainerStatus,
    build_trusted_callback_target,
)

SANDBOX_SECURITY_PROFILE_GOVERNED = "governed"
SANDBOX_SECURITY_PROFILE_LABEL = "ai-platform.security_profile"

_GOVERNED_EGRESS_PROOF_LABEL = "ai-platform.governed_egress.proof"
_GOVERNED_BRIDGE_PATHS = {
    "callback": "",
    "openai": "/openai/v1",
    "anthropic": "/anthropic",
}
_DNS_HOSTNAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


class OpenSandboxProfileConfigurationError(ValueError):
    """Report a bounded OpenSandbox governed-policy configuration failure."""


@dataclass(frozen=True)
class ExecutorEgressBases:
    """Hold the three validated executor egress bases."""

    callback_base_url: str
    openai_base_url: str
    anthropic_base_url: str

    def callback_target(self):
        """Resolve the callback URL from the canonical governed base."""

        parsed = urlsplit(self.callback_base_url)
        return build_trusted_callback_target(
            self.callback_base_url,
            extra_hosts=[parsed.hostname or ""],
        )


def _canonical_governed_bridge_base(value: object, *, kind: str) -> str:
    raw = str(value or "")
    expected_path = _GOVERNED_BRIDGE_PATHS[kind]
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise OpenSandboxProfileConfigurationError("OpenSandbox upstream bridge base is invalid") from None
    host = parsed.hostname or ""
    pinned_https = parsed.scheme == "https" and bool(_DNS_HOSTNAME.fullmatch(host))
    loopback_http = parsed.scheme == "http" and host == "127.0.0.1"
    if (
        not (pinned_https or loopback_http)
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
    canonical = urlunsplit((parsed.scheme, f"{host}:{port}", expected_path, "", ""))
    if raw != canonical:
        raise OpenSandboxProfileConfigurationError("OpenSandbox upstream bridge base is invalid") from None
    return canonical


def governed_opensandbox_egress_bases(settings: Any) -> ExecutorEgressBases:
    """Return the governed profile's canonical, same-origin bridge bases."""

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
        (urlsplit(value).scheme, urlsplit(value).hostname, urlsplit(value).port)
        for value in (
            bases.callback_base_url,
            bases.openai_base_url,
            bases.anthropic_base_url,
        )
    }
    if len(origins) != 1:
        raise OpenSandboxProfileConfigurationError("OpenSandbox upstream bridge origin drift detected")
    return bases


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
