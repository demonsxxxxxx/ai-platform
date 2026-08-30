from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import httpx
import pytest

from app.knowledge.domain import (
    KnowledgeError,
    ProviderCallControl,
    ProviderRetrievalRequest,
)
from app.knowledge.infrastructure.providers import RagFlowKnowledgeProvider


def _provider(monkeypatch, handler) -> RagFlowKnowledgeProvider:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 9380))
        ],
    )
    return RagFlowKnowledgeProvider(
        settings_provider=lambda: SimpleNamespace(
            knowledge_connection_allowed_hosts="ragflow.internal",
            knowledge_provider_timeout_seconds=3,
        ),
        transport=httpx.MockTransport(handler),
    )


def _request(**overrides) -> ProviderRetrievalRequest:
    values = {
        "question": "公司的差旅报销标准是什么？",
        "provider_resource_id": "dataset-authoritative",
        "page_size": 2,
        "candidate_pool_size": 128,
        "similarity_threshold": 0.45,
        "max_chunk_bytes": 16,
    }
    values.update(overrides)
    return ProviderRetrievalRequest(**values)


@pytest.mark.asyncio
async def test_ragflow_retrieval_uses_exact_server_values_and_safe_projection(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "chunks": [
                        {
                            "id": "chunk-a",
                            "content": "差旅报销按制度执行，超长内容会被截断。",
                            "dataset_id": "dataset-authoritative",
                            "document_id": "document-a",
                            "document_keyword": "差旅制度",
                            "similarity": 0.92,
                            "positions": {"page": 3},
                            "provider_private_field": "discarded",
                        }
                    ],
                    "provider_private_summary": "discarded",
                },
            },
            headers={"content-type": "application/json"},
        )

    provider = _provider(monkeypatch, handler)
    result = await provider.retrieve(
        base_url="http://ragflow.internal:9380",
        credential="provider-key",
        request=_request(),
        control=ProviderCallControl(timeout_seconds=2),
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].provider_document_id == "document-a"
    assert result.chunks[0].provider_chunk_id == "chunk-a"
    assert len(result.chunks[0].content.encode("utf-8")) <= 16
    assert result.chunks[0].position_json == {"page": 3}
    assert not hasattr(result.chunks[0], "provider_private_field")
    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "POST"
    assert sent.url.path == "/api/v1/retrieval"
    assert sent.url.host == "10.20.30.40"
    assert sent.headers["host"] == "ragflow.internal:9380"
    assert sent.headers["authorization"] == "Bearer provider-key"
    assert json.loads(sent.content) == {
        "question": "公司的差旅报销标准是什么？",
        "dataset_ids": ["dataset-authoritative"],
        "page": 1,
        "page_size": 2,
        "knn_top_k": 128,
        "similarity_threshold": 0.45,
        "keyword": False,
        "highlight": False,
    }
    assert "top_k" not in json.loads(sent.content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (401, "knowledge_provider_rejected"),
        (403, "knowledge_provider_rejected"),
        (429, "knowledge_provider_transient"),
        (502, "knowledge_provider_transient"),
        (503, "knowledge_provider_transient"),
        (504, "knowledge_provider_transient"),
        (400, "knowledge_provider_rejected"),
        (500, "knowledge_connection_unavailable"),
    ],
)
async def test_ragflow_retrieval_maps_http_failures_without_response_body(
    monkeypatch,
    status,
    expected_code,
) -> None:
    marker = "provider-private-error-body"
    provider = _provider(
        monkeypatch,
        lambda _request: httpx.Response(
            status,
            content=marker.encode(),
            headers={"content-type": "text/plain"},
        ),
    )

    with pytest.raises(KnowledgeError, match=expected_code) as caught:
        await provider.retrieve(
            base_url="http://ragflow.internal:9380",
            credential="provider-key",
            request=_request(),
            control=ProviderCallControl(timeout_seconds=2),
        )

    assert marker not in str(caught.value)


@pytest.mark.asyncio
async def test_ragflow_retrieval_rejects_mismatched_dataset_identity(monkeypatch) -> None:
    provider = _provider(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "chunks": [
                        {
                            "id": "chunk-a",
                            "content": "content",
                            "dataset_id": "dataset-browser-selected",
                            "document_id": "document-a",
                            "similarity": 0.8,
                        }
                    ]
                },
            },
            headers={"content-type": "application/json"},
        ),
    )

    with pytest.raises(KnowledgeError, match="knowledge_response_invalid"):
        await provider.retrieve(
            base_url="http://ragflow.internal:9380",
            credential="provider-key",
            request=_request(),
            control=ProviderCallControl(timeout_seconds=2),
        )


@pytest.mark.asyncio
async def test_ragflow_retrieval_maps_transport_timeout_to_caller_selected_safe_code(
    monkeypatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("query-and-secret-canary")

    provider = _provider(monkeypatch, handler)
    with pytest.raises(KnowledgeError, match="knowledge_retrieval_timeout") as caught:
        await provider.retrieve(
            base_url="http://ragflow.internal:9380",
            credential="provider-key",
            request=_request(),
            control=ProviderCallControl(
                timeout_seconds=2,
                timeout_failure_code="knowledge_retrieval_timeout",
            ),
        )

    assert "query-and-secret-canary" not in str(caught.value)


@pytest.mark.asyncio
async def test_ragflow_retrieval_rejects_oversized_success_body_without_echo(monkeypatch) -> None:
    marker = "provider-private-chunk-canary"
    provider = _provider(
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            content=(marker + ("x" * 2048)).encode(),
            headers={"content-type": "application/json"},
        ),
    )

    with pytest.raises(KnowledgeError, match="knowledge_response_invalid") as caught:
        await provider.retrieve(
            base_url="http://ragflow.internal:9380",
            credential="provider-key",
            request=_request(),
            control=ProviderCallControl(timeout_seconds=2, max_response_bytes=1024),
        )

    assert marker not in str(caught.value)

