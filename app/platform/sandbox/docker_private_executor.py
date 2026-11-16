"""Resolve a Docker executor's private endpoint from its exact governed bridge."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from typing import Any

from app.platform.sandbox.docker_governed_network import (
    container_labels,
    docker_callback_endpoint_subject,
    docker_complete_governed_network_members,
    docker_governed_callback_target,
    docker_network_attachment,
    docker_network_authoritative_attrs,
    docker_owned_governed_network,
    governed_docker_network_name,
    runtime_release_commit,
)
from app.platform.sandbox.errors import GovernedEgressAdmissionError


def private_docker_executor_ipv4(value: object) -> str:
    raw = str(value or "").strip()
    try:
        address = ipaddress.ip_interface(raw).ip
    except ValueError as exc:
        raise GovernedEgressAdmissionError() from exc
    if (
        not isinstance(address, ipaddress.IPv4Address)
        or not address.is_private
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ):
        raise GovernedEgressAdmissionError() from None
    return str(address)


def governed_private_executor_ipv4(
    client: Any,
    container: Any,
    lease: Any,
    request: Any,
    settings: Any,
    *,
    identity_proof_matches: Callable[[Mapping[str, str], Mapping[str, object]], bool],
) -> str:
    """Resolve a private executor IP only from the exact governed bridge."""
    if (
        lease.provider != "docker"
        or lease.tenant_id != request.tenant_id
        or lease.workspace_id != request.workspace_id
        or lease.user_id != request.user_id
        or lease.session_id != request.session_id
        or lease.run_id != request.run_id
        or lease.sandbox_mode != request.sandbox_mode
        or lease.browser_enabled != request.browser_enabled
        or lease.labels.get("ai-platform.attempt_id") != request.attempt_id
    ):
        raise GovernedEgressAdmissionError() from None
    if hasattr(container, "reload"):
        try:
            container.reload()
        except Exception:
            raise GovernedEgressAdmissionError() from None
    if getattr(container, "status", None) != "running":
        raise GovernedEgressAdmissionError() from None
    container_id = str(getattr(container, "id", "") or "").strip()
    attrs = getattr(container, "attrs", None)
    if not isinstance(attrs, dict):
        raise GovernedEgressAdmissionError() from None
    inspected_id = str(attrs.get("Id") or "").strip()
    if not container_id or container_id != lease.container_id or container_id != inspected_id:
        raise GovernedEgressAdmissionError() from None

    callback = docker_governed_callback_target(settings)
    runtime_commit = runtime_release_commit(settings)
    network_name = governed_docker_network_name(lease)
    try:
        network = client.networks.get(network_name)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    network_id, verified_name = docker_owned_governed_network(network, lease)
    if verified_name != network_name:
        raise GovernedEgressAdmissionError() from None
    primary, _api = docker_complete_governed_network_members(
        client,
        network=network,
        lease=lease,
        network_id=network_id,
        network_name=network_name,
        callback_base_url=callback.base_url,
        expected_source=runtime_commit,
    )
    primary_id, attachment = docker_network_attachment(primary, network_name, network_id)
    if primary_id != lease.container_id or primary_id != container_id:
        raise GovernedEgressAdmissionError() from None

    network_attrs = docker_network_authoritative_attrs(network)
    members = network_attrs.get("Containers")
    member = members.get(primary_id) if isinstance(members, dict) else None
    if not isinstance(member, dict):
        raise GovernedEgressAdmissionError() from None
    attachment_ip = private_docker_executor_ipv4(attachment.get("IPAddress"))
    member_ip = private_docker_executor_ipv4(member.get("IPv4Address"))
    if attachment_ip != member_ip:
        raise GovernedEgressAdmissionError() from None

    callback_subject = docker_callback_endpoint_subject(
        client,
        network_name=network_name,
        network_id=network_id,
        callback_base_url=callback.base_url,
        expected_source=runtime_commit,
    )
    inspected_labels = container_labels(primary)
    image_subject = inspected_labels.get("ai-platform.executor.requested_image", "")
    image_digest = inspected_labels.get("ai-platform.executor.requested_image_digest", "")
    if not image_subject or not image_digest:
        raise GovernedEgressAdmissionError() from None
    expected_binding = {
        "tenant_id": request.tenant_id,
        "workspace_id": request.workspace_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "run_id": request.run_id,
        "attempt_id": request.attempt_id,
        "image_subject": image_subject,
        "image_digest": image_digest,
        "lease_identity": f"docker:{lease.container_name}:{lease.container_id}",
        "runtime_subject": "docker-internal-bridge",
        "policy_subject": f"{network_id}:{network_name}:internal",
        "callback_subject": callback_subject,
        "denial_subject": f"{network_id}:internal-default-deny",
        "network_id": network_id,
        "network_name": network_name,
    }
    # Reconciliation snapshots do not contain every launch-time capability
    # input. The proof signature preserves those frozen scope digests; this
    # path only resolves an existing executor after ownership is rechecked.
    if not identity_proof_matches(lease.labels, expected_binding):
        raise GovernedEgressAdmissionError() from None
    return attachment_ip
