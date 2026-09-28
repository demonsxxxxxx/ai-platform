"""Scoped coverage receipts for Claude's native provider session."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = "ai-platform.conversation-authority.v2"
_DIGEST_DOMAIN = b"ai-platform.conversation-source.v2\0"
_ALLOWED_ROLES = frozenset({"user", "assistant"})
_SAFE_MEMBER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SCOPE_FIELDS = ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def source_digest_scope(*, tenant_id: str, workspace_id: str, user_id: str, session_id: str, agent_id: str) -> bytes:
    return _DIGEST_DOMAIN + _canonical([tenant_id, workspace_id, user_id, session_id, agent_id])


def initial_source_digest(scope: Mapping[str, str]) -> str:
    """Return the stable zero-message digest for one authorized session scope."""
    if set(scope) != set(_SCOPE_FIELDS) or any(not isinstance(scope[key], str) or not scope[key] for key in _SCOPE_FIELDS):
        raise ValueError("conversation_authority_scope_invalid")
    return hashlib.sha256(source_digest_scope(**{key: scope[key] for key in _SCOPE_FIELDS})).hexdigest()


def canonical_message(row: Mapping[str, Any]) -> bytes:
    role = row.get("role")
    content = row.get("content")
    if (
        role not in _ALLOWED_ROLES
        or not isinstance(content, str)
        or not isinstance(row.get("id"), str)
        or not row["id"]
        or not isinstance(row.get("run_id"), str)
        or not row["run_id"]
    ):
        raise ValueError("conversation_authority_message_invalid")
    return _canonical([row["id"], row["run_id"], role, content])


def extend_source_digest(predecessor: str, rows: Sequence[Mapping[str, Any]]) -> str:
    try:
        digest = bytes.fromhex(predecessor)
    except (TypeError, ValueError) as exc:
        raise ValueError("conversation_authority_digest_invalid") from exc
    if len(digest) != 32:
        raise ValueError("conversation_authority_digest_invalid")
    for row in rows:
        message = canonical_message(row)
        digest = hashlib.sha256(digest + len(message).to_bytes(8, "big") + message).digest()
    return digest.hex()


def make_authority_receipt(
    *,
    scope: Mapping[str, str],
    through_session_generation: int,
    message_count: int,
    source_sha256: str,
    current_message_id: str | None,
) -> dict[str, Any]:
    return validate_authority_receipt({
        "schema_version": SCHEMA_VERSION,
        "scope": dict(scope),
        "through_session_generation": through_session_generation,
        "message_count": message_count,
        "source_sha256": source_sha256,
        "current_message_id": current_message_id,
    })


def validate_authority_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("conversation_authority_schema_invalid")
    scope = receipt.get("scope")
    if not isinstance(scope, dict) or set(scope) != set(_SCOPE_FIELDS):
        raise ValueError("conversation_authority_scope_invalid")
    if any(not isinstance(scope[key], str) or not scope[key] for key in _SCOPE_FIELDS):
        raise ValueError("conversation_authority_scope_invalid")
    generation = receipt.get("through_session_generation")
    if type(generation) is not int or generation < 1:
        raise ValueError("conversation_authority_generation_invalid")
    count = receipt.get("message_count")
    if type(count) is not int or count < 0:
        raise ValueError("conversation_authority_count_invalid")
    current_message_id = receipt.get("current_message_id")
    if current_message_id is not None and (
        not isinstance(current_message_id, str)
        or _SAFE_MEMBER_ID.fullmatch(current_message_id) is None
    ):
        raise ValueError("conversation_authority_current_message_invalid")
    digest = receipt.get("source_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("conversation_authority_digest_invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {key: scope[key] for key in _SCOPE_FIELDS},
        "through_session_generation": generation,
        "message_count": count,
        "source_sha256": digest,
        "current_message_id": current_message_id,
    }
