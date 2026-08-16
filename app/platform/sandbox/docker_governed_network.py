"""Docker governed-network identity, ownership, and callback witness checks."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.execution_boundary import GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID
from app.runtime.sandbox.contracts import (
    CallbackTargetValidationError,
    ContainerLease,
    build_trusted_callback_target,
)
from app.platform.sandbox.errors import GovernedEgressAdmissionError

GOVERNED_DOCKER_CALLBACK_ALIAS = "api.sandbox.internal"
GOVERNED_DOCKER_API_RELEASE_OWNER = "repo-local-compose"
GOVERNED_DOCKER_NETWORK_OWNER = "sandbox-runtime-governed-egress-v2"

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DockerGovernedEgressAdmission:
    create_kwargs: dict[str, Any]
    lease_labels: dict[str, str]
    network_id: str
    network_name: str
    callback_base_url: str
    runtime_commit: str


def container_labels(container: Any) -> dict[str, str]:
    labels = getattr(container, "labels", None)
    if labels is None:
        labels = getattr(container, "attrs", {}).get("Config", {}).get("Labels", {})
    return {str(key): str(value) for key, value in (labels or {}).items()}


def docker_network_options(network: Any) -> dict[str, str]:
    if isinstance(network, dict):
        attrs = network.get("attrs")
        raw_options = (
            (attrs.get("Options") if isinstance(attrs, dict) else None)
            or network.get("Options")
            or network.get("options")
            or {}
        )
    else:
        attrs = getattr(network, "attrs", {})
        raw_options = attrs.get("Options") if isinstance(attrs, dict) else {}
    if not isinstance(raw_options, dict):
        return {}
    return {str(key): str(value).lower() for key, value in raw_options.items()}


def docker_network_authoritative_attrs(network: Any) -> dict[str, Any]:
    attrs = network.get("attrs") if isinstance(network, dict) else getattr(network, "attrs", None)
    return dict(attrs) if isinstance(attrs, dict) else {}


def docker_governed_network_identity(
    network: Any,
    configured_name: str,
) -> tuple[str, str]:
    if hasattr(network, "reload"):
        try:
            network.reload()
        except Exception:
            raise GovernedEgressAdmissionError() from None
    attrs = docker_network_authoritative_attrs(network)
    network_id = str(attrs.get("Id") or "").strip()
    network_name = str(attrs.get("Name") or "").strip()
    if (
        not network_id
        or len(network_id) > 512
        or network_name != configured_name
        or attrs.get("Driver") != "bridge"
        or attrs.get("Internal") is not True
        or docker_network_options(network)
        != {"com.docker.network.bridge.enable_ip_masquerade": "false"}
    ):
        raise GovernedEgressAdmissionError() from None
    return network_id, network_name


def runtime_release_commit(settings: Any) -> str:
    commit = str(getattr(settings, "ai_platform_runtime_commit", "") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise GovernedEgressAdmissionError() from None
    return commit


def governed_egress_proof_key_id(settings: Any) -> str:
    key_id = str(
        getattr(
            settings,
            "sandbox_egress_proof_key_id",
            GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
        )
        or ""
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key_id):
        raise GovernedEgressAdmissionError() from None
    return key_id


def docker_governed_callback_target(settings: Any) -> Any:
    callback_url = str(getattr(settings, "sandbox_callback_base_url", "") or "")
    try:
        callback = build_trusted_callback_target(callback_url, extra_hosts=[])
    except CallbackTargetValidationError:
        raise GovernedEgressAdmissionError() from None
    if callback.host != GOVERNED_DOCKER_CALLBACK_ALIAS:
        raise GovernedEgressAdmissionError() from None
    return callback


def docker_image_subjects(settings: Any) -> tuple[str, str]:
    image = str(getattr(settings, "sandbox_executor_image", "") or "").strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        return image, image
    image_name, separator, digest = image.rpartition("@")
    if (
        not image_name
        or separator != "@"
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or len(image) > 2048
    ):
        raise GovernedEgressAdmissionError() from None
    return image, digest


def governed_docker_network_name(lease: ContainerLease) -> str:
    attempt_id = str(lease.labels.get("ai-platform.attempt_id") or "").strip()
    if not attempt_id:
        raise GovernedEgressAdmissionError() from None
    scope = "\x00".join(
        (
            lease.tenant_id,
            lease.workspace_id,
            lease.user_id,
            lease.session_id,
            lease.run_id,
            attempt_id,
            lease.container_name,
        )
    )
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:32]
    return f"ai-platform-sandbox-egress-v2-{digest}"


def governed_docker_network_labels(lease: ContainerLease) -> dict[str, str]:
    attempt_id = str(lease.labels.get("ai-platform.attempt_id") or "").strip()
    if not attempt_id:
        raise GovernedEgressAdmissionError() from None
    return {
        "ai-platform.owner": GOVERNED_DOCKER_NETWORK_OWNER,
        "ai-platform.tenant_id": lease.tenant_id,
        "ai-platform.workspace_id": lease.workspace_id,
        "ai-platform.user_id": lease.user_id,
        "ai-platform.session_id": lease.session_id,
        "ai-platform.run_id": lease.run_id,
        "ai-platform.attempt_id": attempt_id,
        "ai-platform.container_name": lease.container_name,
    }


def docker_api_callback_witness(
    client: Any,
    expected_source: str,
) -> tuple[Any, str, str]:
    try:
        containers = client.containers.list(all=True)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    witnesses: list[tuple[Any, str, str]] = []
    for container in containers:
        if hasattr(container, "reload"):
            try:
                container.reload()
            except Exception:
                continue
        labels = container_labels(container)
        if (
            labels.get("ai-platform.release-role") != "api"
            or labels.get("ai-platform.release-owner")
            != GOVERNED_DOCKER_API_RELEASE_OWNER
        ):
            continue
        source = str(labels.get("ai-platform.source-commit") or "")
        attrs = getattr(container, "attrs", {})
        state = attrs.get("State") if isinstance(attrs, dict) else None
        health = state.get("Health") if isinstance(state, dict) else None
        container_id = str(getattr(container, "id", "") or "").strip()
        inspected_id = str(attrs.get("Id") or "").strip() if isinstance(attrs, dict) else ""
        if (
            source != expected_source
            or getattr(container, "status", "") != "running"
            or not isinstance(health, dict)
            or health.get("Status") != "healthy"
            or not container_id
            or container_id != inspected_id
        ):
            continue
        witnesses.append((container, container_id, source))
    if len(witnesses) != 1:
        raise GovernedEgressAdmissionError() from None
    return witnesses[0]


def docker_owned_api_callback_container(
    client: Any,
    expected_source: str,
) -> tuple[Any, str]:
    try:
        containers = client.containers.list(all=True)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    candidates: list[tuple[Any, str]] = []
    for container in containers:
        if hasattr(container, "reload"):
            try:
                container.reload()
            except Exception:
                continue
        labels = container_labels(container)
        attrs = getattr(container, "attrs", {})
        container_id = str(getattr(container, "id", "") or "").strip()
        inspected_id = str(attrs.get("Id") or "").strip() if isinstance(attrs, dict) else ""
        if (
            labels.get("ai-platform.release-role") == "api"
            and labels.get("ai-platform.release-owner")
            == GOVERNED_DOCKER_API_RELEASE_OWNER
            and labels.get("ai-platform.source-commit") == expected_source
            and container_id
            and container_id == inspected_id
        ):
            candidates.append((container, container_id))
    if len(candidates) != 1:
        raise GovernedEgressAdmissionError() from None
    return candidates[0]


def docker_owned_governed_network(
    network: Any,
    lease: ContainerLease,
) -> tuple[str, str]:
    name = governed_docker_network_name(lease)
    network_id, network_name = docker_governed_network_identity(network, name)
    attrs = docker_network_authoritative_attrs(network)
    labels = attrs.get("Labels")
    if not isinstance(labels, dict) or any(
        str(labels.get(key) or "") != value
        for key, value in governed_docker_network_labels(lease).items()
    ):
        raise GovernedEgressAdmissionError() from None
    return network_id, network_name


def get_or_create_governed_docker_network(
    client: Any,
    lease: ContainerLease,
) -> tuple[Any, str, str]:
    name = governed_docker_network_name(lease)
    networks = getattr(client, "networks", None)
    if networks is None:
        raise GovernedEgressAdmissionError() from None
    try:
        network = networks.get(name)
    except Exception:
        try:
            network = networks.create(
                name,
                driver="bridge",
                internal=True,
                options={
                    "com.docker.network.bridge.enable_ip_masquerade": "false"
                },
                labels=governed_docker_network_labels(lease),
            )
        except Exception:
            try:
                network = networks.get(name)
            except Exception:
                raise GovernedEgressAdmissionError() from None
    network_id, network_name = docker_owned_governed_network(network, lease)
    return network, network_id, network_name


def attach_api_callback_witness(network: Any, api_container: Any) -> None:
    connect = getattr(network, "connect", None)
    if not callable(connect):
        raise GovernedEgressAdmissionError() from None
    try:
        connect(api_container, aliases=[GOVERNED_DOCKER_CALLBACK_ALIAS])
    except Exception as exc:
        _logger.debug(
            "Docker API callback witness is already attached or unavailable",
            extra={"error_type": type(exc).__name__},
        )


def docker_network_attachment(
    container: Any,
    network_name: str,
    network_id: str,
) -> tuple[str, dict[str, Any]]:
    if hasattr(container, "reload"):
        try:
            container.reload()
        except Exception:
            raise GovernedEgressAdmissionError() from None
    attrs = getattr(container, "attrs", {})
    if not isinstance(attrs, dict):
        raise GovernedEgressAdmissionError() from None
    container_id = str(getattr(container, "id", "") or "").strip()
    inspected_id = str(attrs.get("Id") or "").strip()
    network_settings = attrs.get("NetworkSettings")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    host_config = attrs.get("HostConfig")
    extra_hosts = host_config.get("ExtraHosts") if isinstance(host_config, dict) else None
    if (
        not container_id
        or container_id != inspected_id
        or not isinstance(networks, dict)
        or set(networks) != {network_name}
        or extra_hosts not in (None, [])
    ):
        raise GovernedEgressAdmissionError() from None
    attachment = networks.get(network_name)
    attachment_id = str(
        attachment.get("NetworkID") if isinstance(attachment, dict) else ""
    ).strip()
    if attachment_id != network_id:
        raise GovernedEgressAdmissionError() from None
    return container_id, dict(attachment)


def docker_callback_endpoint_subject(
    client: Any,
    *,
    network_name: str,
    network_id: str,
    callback_base_url: str,
    expected_source: str,
) -> str:
    container, container_id, source = docker_api_callback_witness(
        client,
        expected_source,
    )
    attrs = getattr(container, "attrs", {})
    network_settings = attrs.get("NetworkSettings") if isinstance(attrs, dict) else None
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    attachment = networks.get(network_name) if isinstance(networks, dict) else None
    aliases = attachment.get("Aliases") if isinstance(attachment, dict) else None
    if (
        not isinstance(attachment, dict)
        or str(attachment.get("NetworkID") or "") != network_id
        or not isinstance(aliases, list)
        or aliases.count(GOVERNED_DOCKER_CALLBACK_ALIAS) != 1
    ):
        raise GovernedEgressAdmissionError() from None
    return (
        f"{callback_base_url}|{container_id}|{source}|{network_id}|"
        f"{GOVERNED_DOCKER_CALLBACK_ALIAS}"
    )
