from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import jwt
from jwt import PyJWTError


class JwtValidationError(ValueError):
    """Collapse third-party JWT failures at the platform adapter boundary."""


def encode_hs256_jwt(claims: dict[str, Any], *, secret: str) -> str:
    """Sign server-owned claims using the receiver's existing HS256 contract."""
    try:
        return jwt.encode(claims, secret, algorithm="HS256")
    except (PyJWTError, TypeError, ValueError) as exc:
        raise JwtValidationError() from exc


def decode_hs256_jwt(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
    required_claims: Sequence[str],
    verify_exp: bool = True,
) -> dict[str, Any]:
    """Decode one HS256 JWT with exact issuer, audience, and required claims."""

    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            options={
                "require": list(required_claims),
                "verify_exp": verify_exp,
            },
        )
    except (PyJWTError, TypeError, ValueError) as exc:
        raise JwtValidationError() from exc
