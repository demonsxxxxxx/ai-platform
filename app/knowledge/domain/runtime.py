"""Immutable values for admitted Knowledge runs and durable evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from .connection import KnowledgeError


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_SOURCE_SNAPSHOT_BYTES = 16_384
_MAX_POSITION_BYTES = 8_192
_POSITION_KEYS = frozenset(
    {
        "bbox",
        "bottom",
        "end",
        "height",
        "left",
        "page",
        "page_id",
        "page_no",
        "page_number",
        "polygon",
        "right",
        "start",
        "tokens",
        "top",
        "width",
        "x",
        "y",
    }
)
_PROFILE_BINDING_KEYS = frozenset(
    {
        "ordinal",
        "required",
        "retrieval_profile_id",
        "retrieval_profile_revision",
        "source_authorization_version",
        "source_id",
    }
)


def _bounded_identifier(value: str, *, max_bytes: int = 512) -> str:
    if not isinstance(value, str):
        raise KnowledgeError("knowledge_runtime_identity_invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > max_bytes
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise KnowledgeError("knowledge_runtime_identity_invalid")
    return normalized


def _require_positive_integer(value: int, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise KnowledgeError(code)
    return value


def _canonical_json(value: Any, *, max_bytes: int, root_types: tuple[type, ...]) -> str:
    if not isinstance(value, root_types):
        raise KnowledgeError("knowledge_runtime_payload_invalid")
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise KnowledgeError("knowledge_runtime_payload_invalid") from exc
    if len(canonical.encode("utf-8")) > max_bytes:
        raise KnowledgeError("knowledge_runtime_payload_invalid")
    return canonical


def _validate_position_projection(value: Any, *, depth: int = 0) -> None:
    if depth > 4:
        raise KnowledgeError("knowledge_evidence_invalid")
    if isinstance(value, dict):
        if len(value) > 32:
            raise KnowledgeError("knowledge_evidence_invalid")
        for key, item in value.items():
            if not isinstance(key, str) or key not in _POSITION_KEYS:
                raise KnowledgeError("knowledge_evidence_invalid")
            _validate_position_projection(item, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise KnowledgeError("knowledge_evidence_invalid")
        for item in value:
            _validate_position_projection(item, depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise KnowledgeError("knowledge_evidence_invalid")
        return
    if (
        isinstance(value, str)
        and len(value.encode("utf-8")) <= 160
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return
    raise KnowledgeError("knowledge_evidence_invalid")


def canonical_run_knowledge_bindings(
    *,
    source_ids: Any,
    retrieval_profile_id: Any,
    bindings: Any,
) -> tuple[dict[str, Any], ...]:
    """Validate the exact credential-free Agent-to-Run Knowledge projection."""

    if (
        not isinstance(source_ids, (list, tuple))
        or not 1 <= len(source_ids) <= 8
        or not isinstance(retrieval_profile_id, str)
        or not isinstance(bindings, (list, tuple))
        or len(bindings) != len(source_ids)
    ):
        raise KnowledgeError("knowledge_snapshot_profile_mismatch")
    try:
        normalized_profile_id = _bounded_identifier(
            retrieval_profile_id,
            max_bytes=160,
        )
    except KnowledgeError as exc:
        raise KnowledgeError("knowledge_snapshot_profile_mismatch") from exc
    if normalized_profile_id != retrieval_profile_id:
        raise KnowledgeError("knowledge_snapshot_profile_mismatch")
    normalized_source_ids: list[str] = []
    for source_id in source_ids:
        if not isinstance(source_id, str):
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")
        try:
            normalized_source_id = _bounded_identifier(source_id, max_bytes=160)
        except KnowledgeError as exc:
            raise KnowledgeError("knowledge_snapshot_profile_mismatch") from exc
        if normalized_source_id != source_id:
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")
        normalized_source_ids.append(normalized_source_id)
    if len(normalized_source_ids) != len(set(normalized_source_ids)):
        raise KnowledgeError("knowledge_snapshot_profile_mismatch")

    normalized_bindings: list[dict[str, Any]] = []
    retrieval_profile_revision: int | None = None
    for ordinal, (source_id, binding) in enumerate(
        zip(normalized_source_ids, bindings, strict=True)
    ):
        if not isinstance(binding, dict) or set(binding) != _PROFILE_BINDING_KEYS:
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")
        source_authorization_version = binding.get("source_authorization_version")
        binding_ordinal = binding.get("ordinal")
        binding_profile_revision = binding.get("retrieval_profile_revision")
        if (
            binding.get("source_id") != source_id
            or isinstance(source_authorization_version, bool)
            or not isinstance(source_authorization_version, int)
            or source_authorization_version <= 0
            or isinstance(binding_ordinal, bool)
            or not isinstance(binding_ordinal, int)
            or binding_ordinal != ordinal
            or binding.get("required") is not True
            or binding.get("retrieval_profile_id") != normalized_profile_id
            or isinstance(binding_profile_revision, bool)
            or not isinstance(binding_profile_revision, int)
            or binding_profile_revision <= 0
        ):
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")
        if retrieval_profile_revision is None:
            retrieval_profile_revision = binding_profile_revision
        elif retrieval_profile_revision != binding_profile_revision:
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")
        normalized_bindings.append(
            {
                "source_id": source_id,
                "source_authorization_version": source_authorization_version,
                "ordinal": ordinal,
                "required": True,
                "retrieval_profile_id": normalized_profile_id,
                "retrieval_profile_revision": binding_profile_revision,
            }
        )
    return tuple(normalized_bindings)


@dataclass(frozen=True)
class RunKnowledgeSourceSnapshot:
    """One server-resolved source authority captured during Run admission."""

    source_id: str
    source_authorization_version: int
    connection_id: str
    connection_revision_id: str
    connection_revision: int
    connection_catalog_sync_id: str
    connection_lifecycle_epoch: int
    provider_resource_id: str
    ordinal: int
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_id", _bounded_identifier(self.source_id, max_bytes=160)
        )
        object.__setattr__(
            self,
            "source_authorization_version",
            _require_positive_integer(
                self.source_authorization_version,
                code="knowledge_snapshot_invalid",
            ),
        )
        object.__setattr__(
            self,
            "connection_id",
            _bounded_identifier(self.connection_id, max_bytes=160),
        )
        object.__setattr__(
            self,
            "connection_revision_id",
            _bounded_identifier(self.connection_revision_id, max_bytes=160),
        )
        object.__setattr__(
            self,
            "connection_revision",
            _require_positive_integer(
                self.connection_revision,
                code="knowledge_snapshot_invalid",
            ),
        )
        object.__setattr__(
            self,
            "connection_catalog_sync_id",
            _bounded_identifier(self.connection_catalog_sync_id, max_bytes=160),
        )
        object.__setattr__(
            self,
            "connection_lifecycle_epoch",
            _require_positive_integer(
                self.connection_lifecycle_epoch,
                code="knowledge_snapshot_invalid",
            ),
        )
        object.__setattr__(
            self,
            "provider_resource_id",
            _bounded_identifier(self.provider_resource_id),
        )
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or self.ordinal < 0
        ):
            raise KnowledgeError("knowledge_snapshot_invalid")
        if self.required is not True:
            raise KnowledgeError("knowledge_snapshot_invalid")

    def projection(self) -> dict[str, Any]:
        return {
            "connection_catalog_sync_id": self.connection_catalog_sync_id,
            "connection_id": self.connection_id,
            "connection_lifecycle_epoch": self.connection_lifecycle_epoch,
            "connection_revision": self.connection_revision,
            "connection_revision_id": self.connection_revision_id,
            "ordinal": self.ordinal,
            "provider_resource_id": self.provider_resource_id,
            "required": self.required,
            "source_authorization_version": self.source_authorization_version,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class RunKnowledgeSnapshot:
    """Credential-free immutable Knowledge authority for one admitted Run."""

    tenant_id: str
    run_id: str
    agent_id: str
    profile_revision: int
    profile_content_hash: str
    retrieval_profile_id: str
    retrieval_profile_revision: int
    sources: tuple[RunKnowledgeSourceSnapshot, ...]
    principal_policy_version: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "tenant_id", _bounded_identifier(self.tenant_id, max_bytes=160)
        )
        object.__setattr__(
            self, "run_id", _bounded_identifier(self.run_id, max_bytes=160)
        )
        object.__setattr__(
            self, "agent_id", _bounded_identifier(self.agent_id, max_bytes=160)
        )
        object.__setattr__(
            self,
            "profile_revision",
            _require_positive_integer(
                self.profile_revision, code="knowledge_snapshot_invalid"
            ),
        )
        if not isinstance(
            self.profile_content_hash, str
        ) or not _SHA256_PATTERN.fullmatch(self.profile_content_hash):
            raise KnowledgeError("knowledge_snapshot_invalid")
        object.__setattr__(
            self,
            "retrieval_profile_id",
            _bounded_identifier(self.retrieval_profile_id, max_bytes=160),
        )
        object.__setattr__(
            self,
            "retrieval_profile_revision",
            _require_positive_integer(
                self.retrieval_profile_revision,
                code="knowledge_snapshot_invalid",
            ),
        )
        object.__setattr__(
            self,
            "principal_policy_version",
            _require_positive_integer(
                self.principal_policy_version,
                code="knowledge_snapshot_invalid",
            ),
        )
        if not isinstance(self.sources, tuple) or not 1 <= len(self.sources) <= 8:
            raise KnowledgeError("knowledge_snapshot_invalid")
        if tuple(source.ordinal for source in self.sources) != tuple(
            range(len(self.sources))
        ):
            raise KnowledgeError("knowledge_snapshot_invalid")
        source_ids = tuple(source.source_id for source in self.sources)
        if len(source_ids) != len(set(source_ids)):
            raise KnowledgeError("knowledge_snapshot_invalid")
        _canonical_json(
            self.sources_projection(),
            max_bytes=_MAX_SOURCE_SNAPSHOT_BYTES,
            root_types=(list,),
        )

    def sources_projection(self) -> list[dict[str, Any]]:
        return [source.projection() for source in self.sources]

    def sources_canonical_json(self) -> str:
        return _canonical_json(
            self.sources_projection(),
            max_bytes=_MAX_SOURCE_SNAPSHOT_BYTES,
            root_types=(list,),
        )

    def content_hash(self) -> str:
        canonical = _canonical_json(
            {
                "agent_id": self.agent_id,
                "principal_policy_version": self.principal_policy_version,
                "profile_content_hash": self.profile_content_hash,
                "profile_revision": self.profile_revision,
                "retrieval_profile_id": self.retrieval_profile_id,
                "retrieval_profile_revision": self.retrieval_profile_revision,
                "run_id": self.run_id,
                "sources": self.sources_projection(),
                "tenant_id": self.tenant_id,
            },
            max_bytes=32_768,
            root_types=(dict,),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KnowledgeEvidence:
    """One bounded evidence passage accepted for a successful retrieval."""

    evidence_id: str
    source_id: str
    provider_document_id: str
    provider_chunk_id: str | None
    title: str
    content: str
    provider_score: float
    fused_rank: int
    position_json: Any

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            _bounded_identifier(self.evidence_id, max_bytes=160),
        )
        object.__setattr__(
            self, "source_id", _bounded_identifier(self.source_id, max_bytes=160)
        )
        object.__setattr__(
            self,
            "provider_document_id",
            _bounded_identifier(self.provider_document_id),
        )
        if self.provider_chunk_id is not None:
            object.__setattr__(
                self,
                "provider_chunk_id",
                _bounded_identifier(self.provider_chunk_id),
            )
        if (
            not isinstance(self.title, str)
            or len(self.title.encode("utf-8")) > 512
            or any(ord(character) == 0 for character in self.title)
        ):
            raise KnowledgeError("knowledge_evidence_invalid")
        if (
            not isinstance(self.content, str)
            or not self.content.strip()
            or len(self.content.encode("utf-8")) > 16_384
        ):
            raise KnowledgeError("knowledge_evidence_invalid")
        if (
            isinstance(self.provider_score, bool)
            or not isinstance(self.provider_score, (int, float))
            or not math.isfinite(float(self.provider_score))
        ):
            raise KnowledgeError("knowledge_evidence_invalid")
        object.__setattr__(self, "provider_score", float(self.provider_score))
        if (
            isinstance(self.fused_rank, bool)
            or not isinstance(self.fused_rank, int)
            or not 1 <= self.fused_rank <= 20
        ):
            raise KnowledgeError("knowledge_evidence_invalid")
        position_value = self.position_json if self.position_json is not None else {}
        _validate_position_projection(position_value)
        position_canonical = _canonical_json(
            position_value,
            max_bytes=_MAX_POSITION_BYTES,
            root_types=(dict, list),
        )
        object.__setattr__(self, "position_json", json.loads(position_canonical))

    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def position_canonical_json(self) -> str:
        return _canonical_json(
            self.position_json,
            max_bytes=_MAX_POSITION_BYTES,
            root_types=(dict, list),
        )
