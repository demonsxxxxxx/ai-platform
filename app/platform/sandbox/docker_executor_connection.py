"""Resolve Docker executor endpoints from published ports or proved bridges."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from app.platform.sandbox.docker_governed_network import (
    container_labels,
    docker_api_callback_witness,
    docker_callback_endpoint_subject,
    docker_governed_callback_target,
    docker_network_attachment,
    docker_network_authoritative_attrs,
    docker_owned_governed_network,
    governed_docker_network_name,
    governed_egress_proof_key_id,
    runtime_release_commit,
)
from app.platform.sandbox.errors import (
    ContainerStartFailedError,
    GovernedEgressAdmissionError,
)


@dataclass(frozen=True)
class DockerExecutorPublishedEndpoint:
    published_host: str
    bind_ip: str


def resolve_executor_published_endpoint(raw_host: str) -> DockerExecutorPublishedEndpoint:
    host = str(raw_host or "").strip()
    if not host or host in {"0.0.0.0", "::"}:
        raise ContainerStartFailedError("executor published endpoint is invalid")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if literal.version != 4 or literal.is_unspecified:
            raise ContainerStartFailedError("executor published endpoint is invalid")
        if not literal.is_loopback and not literal.is_private:
            raise ContainerStartFailedError("executor published endpoint is invalid")
        return DockerExecutorPublishedEndpoint(published_host=host, bind_ip=str(literal))
    try:
        addresses = {
            str(ipaddress.IPv4Address(result[4][0]))
            for result in socket.getaddrinfo(
                host,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, ValueError, IndexError, TypeError) as exc:
        raise ContainerStartFailedError("executor published endpoint is invalid") from exc
    if len(addresses) != 1:
        raise ContainerStartFailedError("executor published endpoint is invalid")
    bind_ip = next(iter(addresses))
    resolved_ip = ipaddress.IPv4Address(bind_ip)
    if not resolved_ip.is_loopback and not resolved_ip.is_private:
        raise ContainerStartFailedError("executor published endpoint is invalid")
    if resolved_ip.is_loopback and host.lower() != "localhost":
        raise ContainerStartFailedError("executor published endpoint is invalid")
    return DockerExecutorPublishedEndpoint(published_host=host, bind_ip=bind_ip)


def published_executor_url_from_container(
    container: Any,
    endpoint: DockerExecutorPublishedEndpoint | None = None,
) -> str | None:
    ports = getattr(container, "attrs", {}).get("NetworkSettings", {}).get("Ports", {})
    bindings = ports.get("18000/tcp") or []
    if len(bindings) != 1:
        if endpoint is not None and bindings:
            raise ContainerStartFailedError("executor published endpoint mismatch")
        return None
    binding = bindings[0]
    host_port = str(binding.get("HostPort") or "").strip()
    try:
        port_number = int(host_port)
    except ValueError:
        port_number = 0
    if not 1 <= port_number <= 65535:
        if endpoint is not None:
            raise ContainerStartFailedError("executor published endpoint mismatch")
        return None
    host = str(binding.get("HostIp") or "").strip()
    if endpoint is not None:
        if host != endpoint.bind_ip:
            raise ContainerStartFailedError("executor published endpoint mismatch")
        return f"http://{endpoint.published_host}:{port_number}"
    if host in {"", "0.0.0.0", "::"}:
        return None
    return f"http://{host}:{port_number}"


def executor_connect_base_url(
    executor_url: str,
    endpoint: DockerExecutorPublishedEndpoint,
) -> str:
    parsed = urlsplit(executor_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ContainerStartFailedError("executor published endpoint mismatch") from exc
    if parsed.scheme != "http" or not port:
        raise ContainerStartFailedError("executor published endpoint mismatch")
    return f"http://{endpoint.bind_ip}:{port}"


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


def is_exact_owned_remote_container(
    container: Any,
    lease: Any,
    *,
    require_lease_identity: bool = False,
) -> bool:
    labels = container_labels(container)
    if not all(
        labels.get(key) == value
        for key, value in {
            "ai-platform.owner": "sandbox-runtime",
            "ai-platform.tenant_id": lease.tenant_id,
            "ai-platform.workspace_id": lease.workspace_id,
            "ai-platform.user_id": lease.user_id,
            "ai-platform.session_id": lease.session_id,
            "ai-platform.run_id": lease.run_id,
            "ai-platform.attempt_id": lease.labels.get("ai-platform.attempt_id"),
        }.items()
    ):
        return False
    container_id = str(getattr(container, "id", "") or "").strip()
    attrs = getattr(container, "attrs", {})
    inspected_id = str(attrs.get("Id") or "").strip() if isinstance(attrs, dict) else ""
    return bool(container_id and container_id == inspected_id) and (
        not require_lease_identity or container_id == lease.container_id
    )


def complete_governed_network_members(
    client: Any,
    *,
    network: Any,
    lease: Any,
    network_id: str,
    network_name: str,
    callback_base_url: str,
    expected_source: str,
) -> tuple[Any, Any]:
    """Prove a governed bridge has its current API witness and primary lease."""
    docker_owned_governed_network(network, lease)
    api_container, api_id, _api_source = docker_api_callback_witness(client, expected_source)
    try:
        primary = client.containers.get(lease.container_name)
    except Exception:
        raise GovernedEgressAdmissionError() from None
    if not is_exact_owned_remote_container(
        primary,
        lease,
        require_lease_identity=True,
    ):
        raise GovernedEgressAdmissionError() from None
    primary_id, _attachment = docker_network_attachment(primary, network_name, network_id)
    attrs = docker_network_authoritative_attrs(network)
    members = attrs.get("Containers")
    if not isinstance(members, dict) or {str(member_id) for member_id in members} != {api_id, primary_id}:
        raise GovernedEgressAdmissionError() from None
    docker_callback_endpoint_subject(
        client,
        network_name=network_name,
        network_id=network_id,
        callback_base_url=callback_base_url,
        expected_source=expected_source,
    )
    return primary, api_container


def resolve_private_docker_executor_ipv4(
    container: Any,
    lease: Any,
    request: Any,
    settings: Any,
    client: Any,
    *,
    proof_identity_resolver: Callable[..., object],
) -> str:
    """Resolve an executor address only after exact governed bridge and proof checks."""
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
    primary, _api = complete_governed_network_members(
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
    # Reconciliation snapshots omit launch-time capabilities; the signed proof
    # preserves their scope digests while current container bindings are checked.
    proof = proof_identity_resolver(
        "docker",
        lease.labels,
        signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
        signing_key_id=governed_egress_proof_key_id(settings),
        expected_binding=expected_binding,
    )
    if proof is None:
        raise GovernedEgressAdmissionError() from None
    return attachment_ip


def resolve_executor_connection(
    container: Any,
    endpoint: DockerExecutorPublishedEndpoint,
    *,
    lease: Any,
    request: Any,
    settings: Any,
    client: Any,
    proof_identity_resolver: Callable[..., object],
) -> tuple[str, str]:
    executor_url = published_executor_url_from_container(container, endpoint)
    if executor_url is not None:
        return executor_url, executor_connect_base_url(executor_url, endpoint)
    private_ip = resolve_private_docker_executor_ipv4(
        container,
        lease,
        request,
        settings,
        client,
        proof_identity_resolver=proof_identity_resolver,
    )
    private_url = f"http://{private_ip}:18000"
    return private_url, private_url
