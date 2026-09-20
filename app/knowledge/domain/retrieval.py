"""Versioned built-in retrieval policy identities exposed to Agent Builder."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .connection import KnowledgeError


DEFAULT_RETRIEVAL_PROFILE_ID = "krp_default"
DEFAULT_RETRIEVAL_PROFILE_REVISION = 1

_DEFAULT_POLICY: dict[str, Any] = {
    "candidate_pool_size": 1024,
    "cancellation_grace_ms": 250,
    "final_top_k": 8,
    "fusion_strategy": "rrf",
    "max_chunk_bytes": 16_384,
    "max_parallel_sources": 4,
    "max_query_bytes": 16_384,
    "max_retries_per_source": 1,
    "max_total_evidence_bytes": 131_072,
    "mode": "deterministic",
    "overall_timeout_ms": 12_000,
    "per_source_timeout_ms": 8_000,
    "retry_backoff_base_ms": 100,
    "retry_backoff_cap_ms": 1_000,
    "retry_jitter_ratio": 0.2,
    "rrf_constant": 60,
    "score_threshold": 0.45,
    "top_k_per_source": 8,
}


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalPolicy:
    """Validated immutable policy loaded from an admitted profile revision."""

    profile_id: str
    revision: int
    mode: str
    top_k_per_source: int
    candidate_pool_size: int
    score_threshold: float
    fusion_strategy: str
    rrf_constant: int
    final_top_k: int
    per_source_timeout_ms: int
    overall_timeout_ms: int
    cancellation_grace_ms: int
    max_retries_per_source: int
    retry_backoff_base_ms: int
    retry_backoff_cap_ms: int
    retry_jitter_ratio: float
    max_parallel_sources: int
    max_query_bytes: int
    max_chunk_bytes: int
    max_total_evidence_bytes: int

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "KnowledgeRetrievalPolicy":
        integer_fields = (
            "revision",
            "top_k_per_source",
            "candidate_pool_size",
            "rrf_constant",
            "final_top_k",
            "per_source_timeout_ms",
            "overall_timeout_ms",
            "cancellation_grace_ms",
            "max_retries_per_source",
            "retry_backoff_base_ms",
            "retry_backoff_cap_ms",
            "max_parallel_sources",
            "max_query_bytes",
            "max_chunk_bytes",
            "max_total_evidence_bytes",
        )
        try:
            if any(
                isinstance(row[field], bool) or not isinstance(row[field], int)
                for field in integer_fields
            ):
                raise TypeError
            if any(
                isinstance(row[field], bool)
                or not isinstance(row[field], (int, float))
                for field in ("score_threshold", "retry_jitter_ratio")
            ):
                raise TypeError
            values = {
                "profile_id": str(row["id"]),
                "revision": row["revision"],
                "mode": str(row["mode"]),
                "top_k_per_source": row["top_k_per_source"],
                "candidate_pool_size": row["candidate_pool_size"],
                "score_threshold": float(row["score_threshold"]),
                "fusion_strategy": str(row["fusion_strategy"]),
                "rrf_constant": row["rrf_constant"],
                "final_top_k": row["final_top_k"],
                "per_source_timeout_ms": row["per_source_timeout_ms"],
                "overall_timeout_ms": row["overall_timeout_ms"],
                "cancellation_grace_ms": row["cancellation_grace_ms"],
                "max_retries_per_source": row["max_retries_per_source"],
                "retry_backoff_base_ms": row["retry_backoff_base_ms"],
                "retry_backoff_cap_ms": row["retry_backoff_cap_ms"],
                "retry_jitter_ratio": float(row["retry_jitter_ratio"]),
                "max_parallel_sources": row["max_parallel_sources"],
                "max_query_bytes": row["max_query_bytes"],
                "max_chunk_bytes": row["max_chunk_bytes"],
                "max_total_evidence_bytes": row["max_total_evidence_bytes"],
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise KnowledgeError("knowledge_profile_invalid") from exc
        if (
            not values["profile_id"]
            or values["profile_id"] != values["profile_id"].strip()
            or len(values["profile_id"].encode("utf-8")) > 160
            or values["revision"] < 1
            or values["mode"] != "deterministic"
            or not 1 <= values["top_k_per_source"] <= 20
            or not values["top_k_per_source"] <= values["candidate_pool_size"] <= 4096
            or values["candidate_pool_size"] < 20
            or not math.isfinite(values["score_threshold"])
            or not 0 <= values["score_threshold"] <= 1
            or values["fusion_strategy"] != "rrf"
            or values["rrf_constant"] <= 0
            or not 1 <= values["final_top_k"] <= 20
            or not 100 <= values["per_source_timeout_ms"] <= 30_000
            or not 100 <= values["overall_timeout_ms"] <= 60_000
            or not 0 <= values["cancellation_grace_ms"] <= 2_000
            or not 0 <= values["max_retries_per_source"] <= 3
            or not 10 <= values["retry_backoff_base_ms"] <= 1_000
            or not values["retry_backoff_base_ms"]
            <= values["retry_backoff_cap_ms"]
            <= 5_000
            or not math.isfinite(values["retry_jitter_ratio"])
            or not 0 <= values["retry_jitter_ratio"] <= 0.5
            or not 1 <= values["max_parallel_sources"] <= 8
            or not 1 <= values["max_query_bytes"] <= 16_384
            or not 1 <= values["max_chunk_bytes"] <= 16_384
            or not 1 <= values["max_total_evidence_bytes"] <= 131_072
            or str(row.get("status") or "") != "active"
        ):
            raise KnowledgeError("knowledge_profile_invalid")
        return cls(**values)


def default_retrieval_profile_projection() -> dict[str, Any]:
    canonical = json.dumps(
        _DEFAULT_POLICY,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "id": DEFAULT_RETRIEVAL_PROFILE_ID,
        "revision": DEFAULT_RETRIEVAL_PROFILE_REVISION,
        "name": "平台标准检索",
        "description": "确定性多知识源检索、排序与证据预算策略。",
        "status": "active",
        "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
