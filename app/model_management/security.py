"""Security primitives for the platform-owned compatible model endpoint."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_MODEL_SECRET_AAD = b"ai-platform.model-connection.v1"
_BLOCKED_EXACT_IPS = {ipaddress.ip_address("169.254.169.254")}


class ModelConnectionSecurityError(ValueError):
    """Raised when model connection material crosses a security boundary."""


@dataclass(frozen=True)
class ValidatedEndpoint:
    base_url: str
    hostname: str
    port: int
    scheme: str
    ips: tuple[str, ...]


def encrypt_api_key(api_key: str, *, revision: int, encoded_key: str) -> bytes:
    key = _decode_encryption_key(encoded_key)
    value = api_key.strip()
    if not value or "\x00" in value or len(value.encode("utf-8")) > 4096:
        raise ModelConnectionSecurityError("model_connection_api_key_invalid")
    nonce = os.urandom(12)
    aad = _MODEL_SECRET_AAD + b"\0" + str(revision).encode("ascii")
    return nonce + AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad)


def decrypt_api_key(ciphertext: bytes, *, revision: int, encoded_key: str) -> str:
    key = _decode_encryption_key(encoded_key)
    if not isinstance(ciphertext, bytes) or len(ciphertext) < 29:
        raise ModelConnectionSecurityError("model_connection_secret_invalid")
    nonce, encrypted = ciphertext[:12], ciphertext[12:]
    aad = _MODEL_SECRET_AAD + b"\0" + str(revision).encode("ascii")
    try:
        return AESGCM(key).decrypt(nonce, encrypted, aad).decode("utf-8")
    except Exception as exc:
        raise ModelConnectionSecurityError("model_connection_secret_invalid") from exc


def api_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def validate_endpoint(base_url: str, *, allowed_internal_hosts: str = "") -> ValidatedEndpoint:
    normalized = base_url.strip().rstrip("/")
    if not normalized or len(normalized) > 2048 or any(char in normalized for char in "\r\n\x00"):
        raise ModelConnectionSecurityError("model_connection_endpoint_invalid")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ModelConnectionSecurityError("model_connection_endpoint_invalid")
    if parsed.path.rstrip("/") not in {"", "/v1"}:
        raise ModelConnectionSecurityError("model_connection_endpoint_must_be_origin")
    hostname = parsed.hostname.rstrip(".").lower()
    allowed_hosts = {
        item.strip().rstrip(".").lower()
        for item in allowed_internal_hosts.split(",")
        if item.strip()
    }
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ModelConnectionSecurityError("model_connection_endpoint_invalid") from exc
    if not 1 <= port <= 65535:
        raise ModelConnectionSecurityError("model_connection_endpoint_invalid")
    try:
        resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ModelConnectionSecurityError("model_connection_dns_unavailable") from exc
    ips = tuple(dict.fromkeys(item[4][0] for item in resolved))
    if not ips:
        raise ModelConnectionSecurityError("model_connection_dns_unavailable")
    internal_allowed = hostname in allowed_hosts
    for raw_ip in ips:
        ip = ipaddress.ip_address(raw_ip)
        if ip in _BLOCKED_EXACT_IPS or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ModelConnectionSecurityError("model_connection_endpoint_forbidden")
        if (ip.is_loopback or ip.is_private or ip.is_reserved) and not internal_allowed:
            raise ModelConnectionSecurityError("model_connection_endpoint_forbidden")
    if parsed.scheme != "https" and not internal_allowed:
        raise ModelConnectionSecurityError("model_connection_https_required")
    default_port = 443 if parsed.scheme == "https" else 80
    authority_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = authority_host if port == default_port else f"{authority_host}:{port}"
    return ValidatedEndpoint(
        base_url=f"{parsed.scheme}://{authority}",
        hostname=hostname,
        port=port,
        scheme=parsed.scheme,
        ips=ips,
    )


def tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def endpoint_parts(endpoint: ValidatedEndpoint) -> SplitResult:
    return urlsplit(endpoint.base_url)


def _decode_encryption_key(encoded_key: str) -> bytes:
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except (TypeError, ValueError) as exc:
        raise ModelConnectionSecurityError("model_connection_encryption_key_invalid") from exc
    if len(key) != 32:
        raise ModelConnectionSecurityError("model_connection_encryption_key_invalid")
    return key
