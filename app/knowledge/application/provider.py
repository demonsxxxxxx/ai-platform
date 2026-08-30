"""Provider-neutral application port for External Knowledge."""

from __future__ import annotations

from typing import Protocol

from app.knowledge.domain import (
    ProviderCallControl,
    ProviderCatalogSnapshot,
    ProviderRetrievalRequest,
    ProviderRetrievalResult,
)


class KnowledgeProvider(Protocol):
    provider_key: str

    async def check(self, *, base_url: str, credential: str) -> None: ...

    async def list_sources(
        self,
        *,
        base_url: str,
        credential: str,
    ) -> ProviderCatalogSnapshot: ...

    async def retrieve(
        self,
        *,
        base_url: str,
        credential: str,
        request: ProviderRetrievalRequest,
        control: ProviderCallControl,
    ) -> ProviderRetrievalResult: ...
