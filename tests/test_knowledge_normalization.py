from __future__ import annotations

import math

import pytest

from app.knowledge.domain import (
    KnowledgeError,
    ProviderChunkCandidate,
    normalize_provider_chunk,
    normalize_provider_result,
)


def _candidate(**overrides):
    values = {
        "provider_resource_id": "dataset-a",
        "provider_document_id": "document-a",
        "provider_chunk_id": "chunk-a",
        "content": "受控知识内容",
        "title": "企业 制度",
        "provider_score": 0.91,
        "position": {"page": 2},
    }
    values.update(overrides)
    return ProviderChunkCandidate(**values)


def test_provider_chunk_normalization_is_bounded_and_provider_neutral() -> None:
    normalized = normalize_provider_chunk(
        _candidate(
            content="你你你",
            title="  企业   制度  ",
            position={"page": 2, "rect": [1, 2, 3, 4]},
        ),
        expected_provider_resource_id="dataset-a",
        max_chunk_bytes=7,
    )

    assert normalized.content == "你你"
    assert normalized.title == "企业 制度"
    assert normalized.provider_document_id == "document-a"
    assert normalized.provider_chunk_id == "chunk-a"
    assert normalized.provider_score == 0.91
    assert normalized.position_json == {"page": 2, "rect": [1, 2, 3, 4]}


@pytest.mark.parametrize(
    ("overrides", "max_chunk_bytes"),
    [
        ({"provider_resource_id": "dataset-b"}, 1024),
        ({"provider_document_id": None}, 1024),
        ({"provider_chunk_id": ""}, 1024),
        ({"content": None}, 1024),
        ({"content": "   "}, 1024),
        ({"provider_score": True}, 1024),
        ({"provider_score": math.nan}, 1024),
        ({"provider_score": math.inf}, 1024),
        ({"position": "provider-private-position"}, 1024),
        ({"position": {"raw": "x" * 9000}}, 1024),
    ],
)
def test_provider_chunk_normalization_rejects_each_unsafe_field(
    overrides,
    max_chunk_bytes,
) -> None:
    with pytest.raises(KnowledgeError, match="knowledge_response_invalid"):
        normalize_provider_chunk(
            _candidate(**overrides),
            expected_provider_resource_id="dataset-a",
            max_chunk_bytes=max_chunk_bytes,
        )


def test_provider_result_rejects_more_rows_than_the_admitted_page() -> None:
    with pytest.raises(KnowledgeError, match="knowledge_response_invalid"):
        normalize_provider_result(
            (_candidate(provider_chunk_id="chunk-a"), _candidate(provider_chunk_id="chunk-b")),
            expected_provider_resource_id="dataset-a",
            max_chunk_bytes=1024,
            result_limit=1,
        )

