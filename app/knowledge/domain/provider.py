"""Provider-neutral retrieval values owned by External Knowledge."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .connection import KnowledgeError


_MAX_PROVIDER_ID_BYTES = 512
_MAX_RETRIEVAL_RESPONSE_BYTES = 2 * 1024 * 1024
_TIMEOUT_FAILURE_CODES = {
    "knowledge_provider_transient",
    "knowledge_retrieval_timeout",
}


def _canonical_provider_id(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > _MAX_PROVIDER_ID_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise KnowledgeError("knowledge_binding_invalid")
    return normalized


@dataclass(frozen=True)
class ProviderRetrievalRequest:
    """One bounded request for one server-resolved provider source."""

    question: str
    provider_resource_id: str
    page_size: int
    candidate_pool_size: int
    similarity_threshold: float
    max_query_bytes: int = 16_384
    max_chunk_bytes: int = 16_384

    def __post_init__(self) -> None:
        question = self.question.strip()
        if (
            not question
            or isinstance(self.max_query_bytes, bool)
            or not isinstance(self.max_query_bytes, int)
            or not 1 <= self.max_query_bytes <= 16_384
            or len(question.encode("utf-8")) > self.max_query_bytes
        ):
            raise KnowledgeError("knowledge_query_invalid")
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= 20
        ):
            raise KnowledgeError("knowledge_profile_invalid")
        if (
            isinstance(self.candidate_pool_size, bool)
            or not isinstance(self.candidate_pool_size, int)
            or not 20 <= self.candidate_pool_size <= 4096
        ):
            raise KnowledgeError("knowledge_profile_invalid")
        if self.candidate_pool_size < self.page_size:
            raise KnowledgeError("knowledge_profile_invalid")
        if (
            isinstance(self.similarity_threshold, bool)
            or not isinstance(self.similarity_threshold, (int, float))
            or not math.isfinite(float(self.similarity_threshold))
            or not 0 <= float(self.similarity_threshold) <= 1
        ):
            raise KnowledgeError("knowledge_profile_invalid")
        if (
            isinstance(self.max_chunk_bytes, bool)
            or not isinstance(self.max_chunk_bytes, int)
            or not 1 <= self.max_chunk_bytes <= 16_384
        ):
            raise KnowledgeError("knowledge_profile_invalid")
        object.__setattr__(self, "question", question)
        object.__setattr__(
            self,
            "provider_resource_id",
            _canonical_provider_id(self.provider_resource_id),
        )
        object.__setattr__(self, "similarity_threshold", float(self.similarity_threshold))


@dataclass(frozen=True)
class ProviderCallControl:
    """Per-call limits already capped by the orchestration deadline."""

    timeout_seconds: float
    timeout_failure_code: str = "knowledge_provider_transient"
    max_response_bytes: int = _MAX_RETRIEVAL_RESPONSE_BYTES

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or not 0 < float(self.timeout_seconds) <= 30
        ):
            raise KnowledgeError("knowledge_profile_invalid")
        if self.timeout_failure_code not in _TIMEOUT_FAILURE_CODES:
            raise KnowledgeError("knowledge_profile_invalid")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1024 <= self.max_response_bytes <= _MAX_RETRIEVAL_RESPONSE_BYTES
        ):
            raise KnowledgeError("knowledge_profile_invalid")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


@dataclass(frozen=True)
class ProviderChunkCandidate:
    """Provider fields projected by infrastructure before pure normalization."""

    provider_resource_id: Any
    provider_document_id: Any
    provider_chunk_id: Any
    content: Any
    title: Any
    provider_score: Any
    position: Any


@dataclass(frozen=True)
class ProviderRetrievalChunk:
    provider_document_id: str
    provider_chunk_id: str
    content: str
    title: str
    provider_score: float
    position_json: Any


@dataclass(frozen=True)
class ProviderRetrievalResult:
    chunks: tuple[ProviderRetrievalChunk, ...]
