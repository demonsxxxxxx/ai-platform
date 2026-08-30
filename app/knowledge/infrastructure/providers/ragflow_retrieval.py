"""Bounded RAGFlow native-retrieval adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.knowledge.domain import (
    KnowledgeError,
    ProviderCallControl,
    ProviderChunkCandidate,
    ProviderRetrievalRequest,
    ProviderRetrievalResult,
    normalize_provider_result,
)

from .ragflow import RagFlowCatalogProvider, _PinnedOriginTransport


_TRANSIENT_HTTP_STATUSES = {429, 502, 503, 504}


class RagFlowKnowledgeProvider(RagFlowCatalogProvider):
    """RAGFlow provider implementing catalog and one-source retrieval."""

    async def _retrieval_payload(
        self,
        *,
        client: httpx.AsyncClient,
        credential: str,
        request: ProviderRetrievalRequest,
        control: ProviderCallControl,
    ) -> dict[str, Any]:
        try:
            async with client.stream(
                "POST",
                "/api/v1/retrieval",
                headers={"Authorization": f"Bearer {credential}"},
                json={
                    "question": request.question,
                    "dataset_ids": [request.provider_resource_id],
                    "page": 1,
                    "page_size": request.page_size,
                    "knn_top_k": request.candidate_pool_size,
                    "similarity_threshold": request.similarity_threshold,
                    "keyword": False,
                    "highlight": False,
                },
            ) as response:
                if response.status_code in {401, 403}:
                    raise KnowledgeError("knowledge_provider_rejected")
                if response.status_code in _TRANSIENT_HTTP_STATUSES:
                    raise KnowledgeError("knowledge_provider_transient")
                if 400 <= response.status_code < 500:
                    raise KnowledgeError("knowledge_provider_rejected")
                if response.status_code != 200:
                    raise KnowledgeError("knowledge_connection_unavailable")
                if "application/json" not in response.headers.get("content-type", "").lower():
                    raise KnowledgeError("knowledge_response_invalid")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        parsed_length = int(declared_length)
                    except ValueError as exc:
                        raise KnowledgeError("knowledge_response_invalid") from exc
                    if parsed_length > control.max_response_bytes:
                        raise KnowledgeError("knowledge_response_invalid")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > control.max_response_bytes:
                        raise KnowledgeError("knowledge_response_invalid")
        except httpx.TimeoutException as exc:
            raise KnowledgeError(control.timeout_failure_code) from exc
        except httpx.NetworkError as exc:
            raise KnowledgeError("knowledge_provider_transient") from exc
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgeError("knowledge_response_invalid") from exc
        if not isinstance(payload, dict):
            raise KnowledgeError("knowledge_response_invalid")
        if payload.get("code") not in {0, "0"}:
            raise KnowledgeError("knowledge_provider_rejected")
        return payload

    async def retrieve(
        self,
        *,
        base_url: str,
        credential: str,
        request: ProviderRetrievalRequest,
        control: ProviderCallControl,
    ) -> ProviderRetrievalResult:
        target = await self._validated_origin(base_url)
        try:
            async with asyncio.timeout(control.timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=target.origin,
                    timeout=httpx.Timeout(control.timeout_seconds),
                    follow_redirects=False,
                    trust_env=False,
                    transport=_PinnedOriginTransport(
                        target=target,
                        transport=self._transport,
                    ),
                ) as client:
                    payload = await self._retrieval_payload(
                        client=client,
                        credential=credential,
                        request=request,
                        control=control,
                    )
        except TimeoutError as exc:
            raise KnowledgeError(control.timeout_failure_code) from exc
        data = payload.get("data")
        if not isinstance(data, dict):
            raise KnowledgeError("knowledge_response_invalid")
        chunks = data.get("chunks")
        if not isinstance(chunks, list) or any(not isinstance(item, dict) for item in chunks):
            raise KnowledgeError("knowledge_response_invalid")
        return normalize_provider_result(
            (
                ProviderChunkCandidate(
                    provider_resource_id=item.get("dataset_id"),
                    provider_document_id=item.get("document_id"),
                    provider_chunk_id=item.get("id"),
                    content=item.get("content"),
                    title=item.get("document_keyword"),
                    provider_score=item.get("similarity"),
                    position=item.get("positions"),
                )
                for item in chunks
            ),
            expected_provider_resource_id=request.provider_resource_id,
            max_chunk_bytes=request.max_chunk_bytes,
            result_limit=request.page_size,
        )

