"""Immutable conversation-range authorization receipts and source digests."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "ai-platform.conversation-authority.v2"
_DIGEST_DOMAIN = b"ai-platform.conversation-source.v2\0"
_ALLOWED_ROLES = frozenset({"user", "assistant"})
_SAFE_MEMBER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def source_digest_scope(*, tenant_id: str, workspace_id: str, user_id: str, session_id: str, agent_id: str) -> bytes:
    return _DIGEST_DOMAIN + _canonical([tenant_id, workspace_id, user_id, session_id, agent_id])


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


def source_chain_digest(*, scope: bytes, rows: Sequence[Mapping[str, Any]]) -> str:
    return extend_source_digest(hashlib.sha256(scope).hexdigest(), rows)


@dataclass
class ConversationSourceChain:
    scope: dict[str, str]
    through_session_generation: int
    current_run_id: str
    current_message_id: str | None
    predecessor_digest: str | None = None
    base_checkpoint_id: str | None = None
    base_checkpoint_summary_sha256: str | None = None
    predecessor_message_count: int = 0
    predecessor_range_start: dict[str, str] | None = None
    predecessor_range_end: dict[str, str] | None = None
    message_count: int = 0
    range_start: dict[str, str] | None = None
    range_end: dict[str, str] | None = None
    _digest: bytes = field(init=False, repr=False)
    _tail_digest: Any = field(default_factory=hashlib.sha256, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.through_session_generation) is not int or self.through_session_generation < 1:
            raise ValueError("conversation_authority_generation_invalid")
        if (self.predecessor_digest is None) != (self.base_checkpoint_id is None):
            raise ValueError("conversation_authority_checkpoint_invalid")
        if self.base_checkpoint_id is not None:
            if (type(self.predecessor_message_count) is not int or self.predecessor_message_count < 1
                or self.predecessor_range_start is None or self.predecessor_range_end is None
                or not isinstance(self.base_checkpoint_summary_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.base_checkpoint_summary_sha256)
                or not isinstance(self.predecessor_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.predecessor_digest)):
                raise ValueError("conversation_authority_checkpoint_invalid")
            self.range_start = self.predecessor_range_start
            self.range_end = self.predecessor_range_end
        try:
            self._digest = (
                bytes.fromhex(self.predecessor_digest)
                if self.predecessor_digest is not None
                else hashlib.sha256(source_digest_scope(**self.scope)).digest()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("conversation_authority_digest_invalid") from exc
        if len(self._digest) != 32:
            raise ValueError("conversation_authority_digest_invalid")

    def add_page(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            created_at = row.get("created_at")
            try:
                parsed = (created_at if isinstance(created_at, datetime)
                          else datetime.fromisoformat(created_at))
            except (TypeError, ValueError) as exc:
                raise ValueError("conversation_authority_range_invalid") from exc
            if parsed.tzinfo is None:
                raise ValueError("conversation_authority_range_invalid")
            boundary = {"created_at": parsed.astimezone(UTC).isoformat(),
                        "id": str(row.get("id") or "")}
            if (
                not boundary["id"]
                or (self.range_end is not None and (boundary["created_at"], boundary["id"])
                    <= (self.range_end["created_at"], self.range_end["id"]))
                or row.get("run_id") == self.current_run_id
                or type(row.get("session_generation")) is not int
                or not 1 <= row["session_generation"] < self.through_session_generation
            ):
                raise ValueError("conversation_authority_range_invalid")
            message = canonical_message(row)
            frame = len(message).to_bytes(8, "big") + message
            self._digest = hashlib.sha256(self._digest + frame).digest()
            self._tail_digest.update(frame)
            self.range_start = self.range_start or boundary
            self.range_end = boundary
            self.message_count += 1

    def receipt(self) -> dict[str, Any]:
        source = self._digest.hex()
        total = self.predecessor_message_count + self.message_count
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": self.scope,
            "through_session_generation": self.through_session_generation,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "message_count": total,
            "source_sha256": source,
            "base_checkpoint_id": self.base_checkpoint_id,
            "base_checkpoint_source_sha256": self.predecessor_digest,
            "base_checkpoint_summary_sha256": self.base_checkpoint_summary_sha256,
            "tail_message_count": self.message_count,
            "tail_sha256": self._tail_digest.hexdigest(),
            "current_message_id": self.current_message_id,
        }


def validate_authority_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("conversation_authority_schema_invalid")
    required = {"scope", "through_session_generation", "range_start", "range_end", "message_count", "source_sha256", "base_checkpoint_id", "base_checkpoint_source_sha256", "base_checkpoint_summary_sha256", "tail_message_count", "tail_sha256", "current_message_id"}
    if not required.issubset(receipt):
        raise ValueError("conversation_authority_fields_invalid")
    if not isinstance(receipt["scope"], dict) or set(receipt["scope"]) != {"tenant_id", "workspace_id", "user_id", "session_id", "agent_id"}:
        raise ValueError("conversation_authority_scope_invalid")
    if any(not isinstance(value, str) or not value for value in receipt["scope"].values()):
        raise ValueError("conversation_authority_scope_invalid")
    if type(receipt["through_session_generation"]) is not int or receipt["through_session_generation"] < 1:
        raise ValueError("conversation_authority_generation_invalid")
    if (type(receipt["message_count"]) is not int or receipt["message_count"] < 0
        or type(receipt["tail_message_count"]) is not int or receipt["tail_message_count"] < 0
        or receipt["tail_message_count"] > receipt["message_count"]):
        raise ValueError("conversation_authority_count_invalid")
    if (receipt["range_start"] is None) != (receipt["range_end"] is None) or (receipt["range_start"] is None) != (receipt["message_count"] == 0):
        raise ValueError("conversation_authority_range_invalid")
    for boundary in (receipt["range_start"], receipt["range_end"]):
        if boundary is None:
            continue
        if (not isinstance(boundary, dict) or set(boundary) != {"created_at", "id"}
            or not isinstance(boundary["id"], str) or _SAFE_MEMBER_ID.fullmatch(boundary["id"]) is None
            or not isinstance(boundary["created_at"], str)):
            raise ValueError("conversation_authority_range_invalid")
        try:
            parsed = datetime.fromisoformat(boundary["created_at"])
        except ValueError as exc:
            raise ValueError("conversation_authority_range_invalid") from exc
        if parsed.tzinfo is None:
            raise ValueError("conversation_authority_range_invalid")
    if (receipt["range_start"] is not None
        and (datetime.fromisoformat(receipt["range_start"]["created_at"]).astimezone(UTC), receipt["range_start"]["id"])
            > (datetime.fromisoformat(receipt["range_end"]["created_at"]).astimezone(UTC), receipt["range_end"]["id"])):
        raise ValueError("conversation_authority_range_invalid")
    if (receipt["current_message_id"] is not None
        and (not isinstance(receipt["current_message_id"], str)
             or _SAFE_MEMBER_ID.fullmatch(receipt["current_message_id"]) is None)):
        raise ValueError("conversation_authority_current_message_invalid")
    base_id = receipt.get("base_checkpoint_id")
    if base_id is None:
        if (receipt.get("base_checkpoint_source_sha256") is not None
            or receipt.get("base_checkpoint_summary_sha256") is not None
            or receipt["tail_message_count"] != receipt["message_count"]):
            raise ValueError("conversation_authority_checkpoint_invalid")
    else:
        if (not isinstance(base_id, str) or _SAFE_MEMBER_ID.fullmatch(base_id) is None
            or receipt["tail_message_count"] >= receipt["message_count"]):
            raise ValueError("conversation_authority_checkpoint_invalid")
        for field in ("base_checkpoint_source_sha256", "base_checkpoint_summary_sha256"):
            digest = receipt.get(field)
            if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
                raise ValueError("conversation_authority_checkpoint_invalid")
    for field in ("source_sha256", "tail_sha256"):
        value = receipt[field]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("conversation_authority_digest_invalid")
    return dict(receipt)
