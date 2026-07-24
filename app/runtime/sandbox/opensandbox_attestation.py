from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


OPENSANDBOX_ATTESTATION_CONTRACT_VERSION = "ai-platform.opensandbox.topology-attestation.v1"
OPENSANDBOX_ATTESTATION_PATH = "/v1/sandboxes/{sandbox_id}/attestation"
OPENSANDBOX_API_KEY_HEADER = "OPEN-SANDBOX-API-KEY"

_ATTESTATION_MAX_RESPONSE_BYTES = 64 * 1024
_ATTESTATION_MIN_TIMEOUT_SECONDS = 0.1
_ATTESTATION_MAX_TIMEOUT_SECONDS = 5.0
_ATTESTATION_HOST_PATH_POLICY_SUBJECT = "scoped-workspace-only"
_ATTESTATION_PROFILE_VERSION = "v1"
_ATTESTATION_RUNTIME_IDENTITY = "runsc"
_ATTESTATION_NETWORK_MODE = "none"
_SUPPORTED_PATHS = frozenset({OPENSANDBOX_ATTESTATION_PATH})
_SHA256_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_AMBIGUOUS_NUMERIC_LABEL_PATTERN = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.IGNORECASE)
_PRIVATE_IP_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_SPECIAL_USE_IP_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/96"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("100::/64"),
    ipaddress.ip_network("2001::/23"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)


@dataclass(frozen=True)
class _TransportResponse:
    status_code: int
    url: str
    content: bytes


_AttestationTransport = Callable[[str, Mapping[str, str], float], _TransportResponse]


def _required_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("required text is missing")
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or len(normalized) > 4096
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in normalized)
    ):
        raise ValueError("required text is invalid")
    return normalized


def _configured_timeout(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("attestation timeout is invalid")
    timeout = float(value)
    if not _ATTESTATION_MIN_TIMEOUT_SECONDS <= timeout <= _ATTESTATION_MAX_TIMEOUT_SECONDS:
        raise ValueError("attestation timeout is invalid")
    return timeout


def _raw_authority_host(netloc: str, port: int | None) -> str:
    if netloc.startswith("["):
        closing_bracket = netloc.find("]")
        if closing_bracket <= 1:
            raise ValueError("lifecycle endpoint is invalid")
        raw_host = netloc[1:closing_bracket]
        suffix = netloc[closing_bracket + 1 :]
    elif port is None:
        raw_host = netloc
        suffix = ""
    else:
        port_suffix = f":{port}"
        if not netloc.endswith(port_suffix):
            raise ValueError("lifecycle endpoint is invalid")
        raw_host = netloc[: -len(port_suffix)]
        suffix = port_suffix
    expected_suffix = "" if port is None else f":{port}"
    if suffix != expected_suffix:
        raise ValueError("lifecycle endpoint is invalid")
    return raw_host


def _looks_like_ambiguous_numeric_host(host: str) -> bool:
    labels = host.split(".")
    return bool(
        _AMBIGUOUS_NUMERIC_LABEL_PATTERN.fullmatch(host)
        or (
            len(labels) > 1
            and all(_AMBIGUOUS_NUMERIC_LABEL_PATTERN.fullmatch(label) for label in labels)
        )
    )


def _canonical_endpoint_host(netloc: str, hostname: str, port: int | None) -> tuple[str, bool]:
    raw_host = _raw_authority_host(netloc, port)
    if not raw_host or not raw_host.isascii() or raw_host != hostname or hostname.endswith("."):
        raise ValueError("lifecycle endpoint is invalid")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if _looks_like_ambiguous_numeric_host(hostname):
            raise ValueError("lifecycle endpoint is invalid") from None
        labels = hostname.split(".")
        if (
            len(hostname) > 253
            or any(_DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels)
        ):
            raise ValueError("lifecycle endpoint is invalid") from None
        return hostname, False
    if raw_host != str(address):
        raise ValueError("lifecycle endpoint is invalid")
    explicitly_private = any(
        address.version == network.version and address in network
        for network in _PRIVATE_IP_NETWORKS
    )
    canonical_loopback = (
        isinstance(address, ipaddress.IPv4Address) and address.is_loopback
    ) or address == ipaddress.IPv6Address("::1")
    special_use = any(
        address.version == network.version and address in network
        for network in _SPECIAL_USE_IP_NETWORKS
    )
    if not canonical_loopback and (
        address.is_unspecified
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
        or special_use
        or not (explicitly_private or address.is_global)
    ):
        raise ValueError("lifecycle endpoint is invalid")
    return str(address), canonical_loopback


def _lifecycle_base_url(protocol: object, domain: object) -> str:
    scheme = _required_text(protocol)
    raw_domain = _required_text(domain)
    if scheme not in {"http", "https"}:
        raise ValueError("lifecycle scheme is invalid")
    try:
        parsed = urlsplit(f"{scheme}://{raw_domain}")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("lifecycle endpoint is invalid") from exc
    if (
        parsed.scheme != scheme
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("lifecycle endpoint is invalid")
    host, is_loopback = _canonical_endpoint_host(
        parsed.netloc,
        parsed.hostname,
        port,
    )
    if scheme == "http" and not is_loopback:
        raise ValueError("lifecycle endpoint is invalid")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return {"http": 80, "https": 443}.get(scheme)


def _same_endpoint(actual_url: str, expected_url: str) -> bool:
    try:
        actual = urlsplit(actual_url)
        expected = urlsplit(expected_url)
        actual_port = _effective_port(actual.scheme, actual.port)
        expected_port = _effective_port(expected.scheme, expected.port)
    except (TypeError, ValueError):
        return False
    return (
        actual.scheme == expected.scheme
        and (actual.hostname or "").lower() == (expected.hostname or "").lower()
        and actual_port == expected_port
        and actual.path == expected.path
        and actual.username is None
        and actual.password is None
        and not actual.query
        and not actual.fragment
    )


def _strict_json_loads(content: bytes) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    return json.loads(
        content,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )


def _strictly_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            return False
        return all(_strictly_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(_strictly_equal(item, expected_item) for item, expected_item in zip(actual, expected))
    return actual == expected


def _immutable_image(image: object, digest: object) -> tuple[str, str]:
    requested_image = _required_text(image)
    requested_digest = _required_text(digest)
    subject, separator, embedded_digest = requested_image.partition("@")
    last_path_segment = subject.rsplit("/", 1)[-1]
    if (
        not separator
        or not subject
        or not last_path_segment
        or subject.startswith("/")
        or subject.endswith("/")
        or "//" in subject
        or any(character in subject for character in "?#")
        or "@" in embedded_digest
        or ":" in last_path_segment
        or _SHA256_DIGEST_PATTERN.fullmatch(embedded_digest) is None
        or embedded_digest != requested_digest
    ):
        raise ValueError("immutable image is invalid")
    return requested_image, requested_digest


def _default_transport(url: str, headers: Mapping[str, str], timeout_seconds: float) -> _TransportResponse:
    timeout = httpx.Timeout(timeout=timeout_seconds, connect=min(timeout_seconds, 1.0))
    started_at = time.monotonic()
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", url, headers=dict(headers)) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() - started_at > timeout_seconds:
                    raise TimeoutError("attestation response deadline exceeded")
                content.extend(chunk)
                if len(content) > _ATTESTATION_MAX_RESPONSE_BYTES:
                    raise ValueError("attestation response is too large")
            return _TransportResponse(
                status_code=response.status_code,
                url=str(response.url),
                content=bytes(content),
            )


class _OpenSandboxAttestor:
    __slots__ = (
        "_api_key",
        "_base_url",
        "_callback_boundary_subject",
        "_contract_version",
        "_gateway_policy_subject",
        "_path_template",
        "_proof_key_id",
        "_proof_signing_key",
        "_runtime_subject",
        "_timeout_seconds",
        "_transport",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        path_template: str,
        contract_version: str,
        timeout_seconds: float,
        runtime_subject: str,
        gateway_policy_subject: str,
        callback_boundary_subject: str,
        proof_key_id: str,
        proof_signing_key: str,
        transport: _AttestationTransport,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._path_template = path_template
        self._contract_version = contract_version
        self._timeout_seconds = timeout_seconds
        self._runtime_subject = runtime_subject
        self._gateway_policy_subject = gateway_policy_subject
        self._callback_boundary_subject = callback_boundary_subject
        self._proof_key_id = proof_key_id
        if len(proof_signing_key.encode("utf-8")) < 32:
            raise ValueError("attestation proof signing key is invalid")
        self._proof_signing_key = proof_signing_key.encode("utf-8")
        self._transport = transport

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self._base_url!r}, "
            f"path_template={self._path_template!r}, api_key=<redacted>)"
        )

    def _expected_payload(self, capability: Any, request: Any, sandbox_id: str) -> dict[str, object]:
        runtime_identity = _required_text(getattr(capability, "runtime_identity", ""))
        runtime_subject = _required_text(getattr(capability, "runtime_subject", ""))
        gateway_policy_subject = _required_text(getattr(capability, "gateway_policy_subject", ""))
        callback_boundary_subject = _required_text(getattr(capability, "callback_boundary_subject", ""))
        if (
            runtime_identity != _ATTESTATION_RUNTIME_IDENTITY
            or runtime_subject != self._runtime_subject
            or gateway_policy_subject != self._gateway_policy_subject
            or callback_boundary_subject != self._callback_boundary_subject
        ):
            raise ValueError("configured attestation subjects drifted")
        image_subject, image_digest = _immutable_image(
            getattr(capability, "requested_image", ""),
            getattr(capability, "requested_image_digest", ""),
        )
        profile_id = _required_text(getattr(capability, "profile_id", ""))
        deny_audit_subject = _required_text(getattr(capability, "deny_audit_subject", ""))
        deny_counter_subject = _required_text(getattr(capability, "deny_counter_subject", ""))
        scope_labels = {
            key: _required_text(getattr(request, key, ""))
            for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id")
        }
        scope_labels["lease_id"] = (
            f"opensandbox:opensandbox-{scope_labels['run_id']}-{scope_labels['attempt_id']}:{sandbox_id}"
        )
        payload: dict[str, object] = {
            "contract_version": self._contract_version,
            "provider": "opensandbox",
            "sandbox_id": sandbox_id,
            "scope_labels": scope_labels,
            "runtime": {
                "identity": runtime_identity,
                "subject": runtime_subject,
            },
            "network": {
                "mode": _ATTESTATION_NETWORK_MODE,
                "default_deny": True,
            },
            "security": {
                "no_new_privileges": True,
                "user": "1000:1000",
                "uid": "1000",
                "gid": "1000",
            },
            "image": {
                "subject": image_subject,
                "digest": image_digest,
            },
            "host_path_policy": {
                "subject": _ATTESTATION_HOST_PATH_POLICY_SUBJECT,
                "unscoped_host_paths_allowed": False,
            },
            "subjects": {
                "gateway_policy": gateway_policy_subject,
                "callback_boundary": callback_boundary_subject,
                "capability": profile_id,
                "deny_audit": deny_audit_subject,
                "deny_counter": deny_counter_subject,
            },
            "signed_profile": {
                "id": profile_id,
                "version": _ATTESTATION_PROFILE_VERSION,
                "proof_key_id": self._proof_key_id,
            },
        }
        signed_profile = payload["signed_profile"]
        assert isinstance(signed_profile, dict)
        signed_profile["profile_signature"] = hmac.new(
            self._proof_signing_key,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return payload

    async def __call__(self, capability: Any, request: Any, sandbox_id: str, info: Any) -> bool:
        try:
            normalized_sandbox_id = _required_text(sandbox_id)
            info_id = info.get("id") if isinstance(info, dict) else getattr(info, "id", None)
            if info_id != normalized_sandbox_id:
                return False
            expected_payload = self._expected_payload(capability, request, normalized_sandbox_id)
            rendered_path = self._path_template.format(
                sandbox_id=quote(normalized_sandbox_id, safe="")
            )
            endpoint = f"{self._base_url}{rendered_path}"
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._transport,
                    endpoint,
                    {
                        "Accept": "application/json",
                        OPENSANDBOX_API_KEY_HEADER: self._api_key,
                    },
                    self._timeout_seconds,
                ),
                timeout=self._timeout_seconds,
            )
            if (
                type(response.status_code) is not int
                or not 200 <= response.status_code < 300
                or not _same_endpoint(response.url, endpoint)
                or not isinstance(response.content, bytes)
                or len(response.content) > _ATTESTATION_MAX_RESPONSE_BYTES
            ):
                return False
            payload = _strict_json_loads(response.content)
            return _strictly_equal(payload, expected_payload)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return False


def build_opensandbox_attestation_probe(
    settings: Any,
    *,
    transport: _AttestationTransport | None = None,
) -> _OpenSandboxAttestor | None:
    """Build the authenticated attestor only for a complete, allowlisted configuration."""

    try:
        path_template = _required_text(getattr(settings, "opensandbox_attestation_path", ""))
        contract_version = _required_text(
            getattr(settings, "opensandbox_attestation_contract_version", "")
        )
        if path_template not in _SUPPORTED_PATHS:
            raise ValueError("attestation path is unsupported")
        if contract_version != OPENSANDBOX_ATTESTATION_CONTRACT_VERSION:
            raise ValueError("attestation contract is unsupported")
        return _OpenSandboxAttestor(
            base_url=_lifecycle_base_url(
                getattr(settings, "opensandbox_protocol", ""),
                getattr(settings, "opensandbox_domain", ""),
            ),
            api_key=_required_text(getattr(settings, "opensandbox_api_key", "")),
            path_template=path_template,
            contract_version=contract_version,
            timeout_seconds=_configured_timeout(
                getattr(settings, "opensandbox_attestation_timeout_seconds", 0)
            ),
            runtime_subject=_required_text(getattr(settings, "sandbox_runtime_subject", "")),
            gateway_policy_subject=_required_text(
                getattr(settings, "opensandbox_external_egress_gateway_policy_subject", "")
            ),
            callback_boundary_subject=_required_text(
                getattr(settings, "opensandbox_external_egress_callback_boundary_subject", "")
            ),
            proof_key_id=_required_text(getattr(settings, "sandbox_egress_proof_key_id", "")),
            proof_signing_key=_required_text(getattr(settings, "sandbox_egress_proof_signing_key", "")),
            transport=transport or _default_transport,
        )
    except (TypeError, ValueError):
        return None
