"""Purpose-bound encrypted credential storage.

Product contexts retain only an opaque reference. Plaintext exists only while a
bounded infrastructure adapter resolves a reference for an authorized call.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_AAD_PREFIX = b"ai-platform.platform-credential.v1"
_ALLOWED_PURPOSES = frozenset({"knowledge_provider"})


class PlatformCredentialError(ValueError):
    """A safe failure at the shared credential boundary."""


@dataclass(frozen=True)
class StoredCredential:
    secret_ref: str
    fingerprint: str


def credential_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _key(encoded_key: str) -> bytes:
    try:
        decoded = base64.b64decode(encoded_key, validate=True)
    except (TypeError, ValueError) as exc:
        raise PlatformCredentialError("platform_credential_key_invalid") from exc
    if len(decoded) != 32:
        raise PlatformCredentialError("platform_credential_key_invalid")
    return decoded


def _purpose(value: str) -> str:
    if value not in _ALLOWED_PURPOSES:
        raise PlatformCredentialError("platform_credential_purpose_invalid")
    return value


def _aad(*, secret_ref: str, purpose: str) -> bytes:
    return _AAD_PREFIX + b"\0" + _purpose(purpose).encode("ascii") + b"\0" + secret_ref.encode("ascii")


def encrypt_credential(
    value: str,
    *,
    secret_ref: str,
    purpose: str,
    encoded_key: str,
) -> bytes:
    normalized = value.strip()
    if not normalized or "\x00" in normalized or len(normalized.encode("utf-8")) > 4096:
        raise PlatformCredentialError("platform_credential_value_invalid")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key(encoded_key)).encrypt(
        nonce,
        normalized.encode("utf-8"),
        _aad(secret_ref=secret_ref, purpose=purpose),
    )
    return nonce + ciphertext


def decrypt_credential(
    ciphertext: bytes,
    *,
    secret_ref: str,
    purpose: str,
    encoded_key: str,
) -> str:
    if not isinstance(ciphertext, bytes) or len(ciphertext) < 29:
        raise PlatformCredentialError("platform_credential_invalid")
    nonce, encrypted = ciphertext[:12], ciphertext[12:]
    try:
        return AESGCM(_key(encoded_key)).decrypt(
            nonce,
            encrypted,
            _aad(secret_ref=secret_ref, purpose=purpose),
        ).decode("utf-8")
    except PlatformCredentialError:
        raise
    except Exception as exc:
        raise PlatformCredentialError("platform_credential_invalid") from exc


class PlatformCredentialVault:
    """PostgreSQL-backed technical vault with no product projection."""

    def __init__(self, *, settings_provider: Any) -> None:
        self._settings_provider = settings_provider

    def _encryption_key(self) -> str:
        value = str(self._settings_provider().platform_credentials_encryption_key or "")
        if not value:
            raise PlatformCredentialError("platform_credential_key_invalid")
        return value

    async def store(
        self,
        conn: Any,
        *,
        tenant_id: str,
        purpose: str,
        value: str,
        actor_id: str,
    ) -> StoredCredential:
        secret_ref = f"sec_{uuid.uuid4().hex}"
        fingerprint = credential_fingerprint(value.strip())
        ciphertext = encrypt_credential(
            value,
            secret_ref=secret_ref,
            purpose=purpose,
            encoded_key=self._encryption_key(),
        )
        await conn.execute(
            """
            insert into platform_secret_records(
              id, tenant_id, purpose, ciphertext, key_version, fingerprint,
              status, created_by
            ) values (%s, %s, %s, %s, 'v1', %s, 'active', %s)
            """,
            (secret_ref, tenant_id, purpose, ciphertext, fingerprint, actor_id),
        )
        return StoredCredential(secret_ref=secret_ref, fingerprint=fingerprint)

    async def resolve(
        self,
        conn: Any,
        *,
        tenant_id: str,
        secret_ref: str,
        purpose: str,
    ) -> str:
        cursor = await conn.execute(
            """
            select ciphertext
            from platform_secret_records
            where id = %s and tenant_id = %s and purpose = %s and status = 'active'
            """,
            (secret_ref, tenant_id, _purpose(purpose)),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PlatformCredentialError("platform_credential_not_found")
        return decrypt_credential(
            bytes(row["ciphertext"]),
            secret_ref=secret_ref,
            purpose=purpose,
            encoded_key=self._encryption_key(),
        )

    async def revoke(self, conn: Any, *, tenant_id: str, secret_ref: str) -> None:
        await conn.execute(
            """
            update platform_secret_records
            set status = 'revoked', updated_at = now()
            where id = %s and tenant_id = %s and status = 'active'
            """,
            (secret_ref, tenant_id),
        )
