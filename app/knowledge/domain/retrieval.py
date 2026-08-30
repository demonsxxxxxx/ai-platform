"""Versioned built-in retrieval policy identities exposed to Agent Builder."""

from __future__ import annotations

import hashlib
import json
from typing import Any


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
