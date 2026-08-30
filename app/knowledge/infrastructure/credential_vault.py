"""Knowledge-owned adapter for the shared encrypted credential vault."""

from __future__ import annotations

from typing import Any

from app.knowledge.domain import KnowledgeError
from app.platform.credentials.vault import PlatformCredentialError


class KnowledgeCredentialVault:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    async def store(self, conn: Any, **kwargs: Any) -> Any:
        try:
            return await self._delegate.store(conn, **kwargs)
        except PlatformCredentialError as exc:
            raise KnowledgeError(str(exc)) from exc

    async def resolve(self, conn: Any, **kwargs: Any) -> str:
        try:
            return await self._delegate.resolve(conn, **kwargs)
        except PlatformCredentialError as exc:
            raise KnowledgeError(str(exc)) from exc


__all__ = ["KnowledgeCredentialVault"]
