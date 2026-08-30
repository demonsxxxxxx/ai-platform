"""Pure provider-result validation and bounded projection."""

from __future__ import annotations

import json
import math
from typing import Iterable

from .connection import KnowledgeError
from .provider import (
    ProviderChunkCandidate,
    ProviderRetrievalChunk,
    ProviderRetrievalResult,
)


_MAX_PROVIDER_ID_BYTES = 512
_MAX_TITLE_BYTES = 512
_MAX_POSITION_BYTES = 8192


def _bounded_identifier(value: object) -> str:
    if not isinstance(value, str):
        raise KnowledgeError("knowledge_response_invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > _MAX_PROVIDER_ID_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise KnowledgeError("knowledge_response_invalid")
    return normalized


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _safe_title(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    return _truncate_utf8(normalized, _MAX_TITLE_BYTES)


def _safe_position(value: object) -> object | None:
    if value is None:
        return None
    if not isinstance(value, (dict, list)):
        raise KnowledgeError("knowledge_response_invalid")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise KnowledgeError("knowledge_response_invalid") from exc
    if len(encoded) > _MAX_POSITION_BYTES:
        raise KnowledgeError("knowledge_response_invalid")
    return json.loads(encoded)


def normalize_provider_chunk(
    candidate: ProviderChunkCandidate,
    *,
    expected_provider_resource_id: str,
    max_chunk_bytes: int,
) -> ProviderRetrievalChunk:
    provider_resource_id = _bounded_identifier(candidate.provider_resource_id)
    if provider_resource_id != expected_provider_resource_id:
        raise KnowledgeError("knowledge_response_invalid")
    if not isinstance(candidate.content, str):
        raise KnowledgeError("knowledge_response_invalid")
    content = _truncate_utf8(candidate.content, max_chunk_bytes)
    if not content.strip():
        raise KnowledgeError("knowledge_response_invalid")
    if (
        isinstance(candidate.provider_score, bool)
        or not isinstance(candidate.provider_score, (int, float))
        or not math.isfinite(float(candidate.provider_score))
    ):
        raise KnowledgeError("knowledge_response_invalid")
    return ProviderRetrievalChunk(
        provider_document_id=_bounded_identifier(candidate.provider_document_id),
        provider_chunk_id=_bounded_identifier(candidate.provider_chunk_id),
        content=content,
        title=_safe_title(candidate.title),
        provider_score=float(candidate.provider_score),
        position_json=_safe_position(candidate.position),
    )


def normalize_provider_result(
    candidates: Iterable[ProviderChunkCandidate],
    *,
    expected_provider_resource_id: str,
    max_chunk_bytes: int,
    result_limit: int,
) -> ProviderRetrievalResult:
    rows = tuple(candidates)
    if len(rows) > result_limit:
        raise KnowledgeError("knowledge_response_invalid")
    return ProviderRetrievalResult(
        chunks=tuple(
            normalize_provider_chunk(
                candidate,
                expected_provider_resource_id=expected_provider_resource_id,
                max_chunk_bytes=max_chunk_bytes,
            )
            for candidate in rows
        )
    )

