from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.context.domain.provider_sessions import (
    PROVIDER_SESSION_ENGINE_CLAUDE,
    ProviderSessionConflictError,
)


class ProviderSessionRepository(Protocol):
    async def ensure_binding(self, conn: Any, **scope: Any) -> dict[str, Any]: ...

    async def claim_writer(self, conn: Any, **scope: Any) -> dict[str, Any]: ...

    async def append_entries(self, conn: Any, **scope: Any) -> list[dict[str, Any]]: ...

    async def list_entries(self, conn: Any, **scope: Any) -> list[dict[str, Any]]: ...

    async def list_subpaths(self, conn: Any, **scope: Any) -> list[str]: ...

    async def has_main_transcript(self, conn: Any, **scope: Any) -> bool: ...


@dataclass(frozen=True)
class ProviderSessionOperationResult:
    action: str
    accepted: bool = True
    entries: tuple[dict[str, Any], ...] = ()
    subpaths: tuple[str, ...] = ()
    accepted_entry_count: int = 0

    @property
    def entry_count(self) -> int:
        return self.accepted_entry_count or len(self.entries)


class ProviderSessionUseCases:
    """Context-owned provider transcript operations over an injected repository."""

    def __init__(self, repository: ProviderSessionRepository) -> None:
        self._repository = repository

    async def execute_callback(
        self,
        conn: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        agent_id: str,
        run_id: str,
        attempt_id: str,
        provider_session_id: str,
        action: str,
        entries: list[dict[str, Any]],
        subpath: str | None,
    ) -> ProviderSessionOperationResult:
        base_scope = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "engine": PROVIDER_SESSION_ENGINE_CLAUDE,
        }
        provider_scope = {**base_scope, "provider_session_id": provider_session_id}
        await self._repository.ensure_binding(conn, **base_scope)
        await self._repository.claim_writer(
            conn,
            **provider_scope,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        if action == "append":
            rows = await self._repository.append_entries(
                conn,
                **provider_scope,
                run_id=run_id,
                attempt_id=attempt_id,
                entries=entries,
                subpath=subpath,
            )
            return ProviderSessionOperationResult(
                action=action,
                accepted_entry_count=len(rows),
            )
        if action == "list_subkeys":
            subpaths = await self._repository.list_subpaths(conn, **provider_scope)
            return ProviderSessionOperationResult(
                action=action,
                subpaths=tuple(subpaths),
            )
        if action != "load":
            raise ProviderSessionConflictError("provider_session_action_invalid")
        rows = await self._repository.list_entries(
            conn,
            **provider_scope,
            subpath=subpath,
        )
        loaded: list[dict[str, Any]] = []
        for row in rows:
            entry = row.get("entry_json") if isinstance(row, Mapping) else None
            if not isinstance(entry, Mapping):
                raise ProviderSessionConflictError("provider_session_entry_shape_invalid")
            loaded.append(dict(entry))
        return ProviderSessionOperationResult(action=action, entries=tuple(loaded))

    async def has_main_transcript(
        self,
        conn: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        agent_id: str,
        engine: str,
    ) -> bool:
        if engine != PROVIDER_SESSION_ENGINE_CLAUDE:
            return False
        return await self._repository.has_main_transcript(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            engine=engine,
        )


_use_cases: ProviderSessionUseCases | None = None


def configure_provider_session_use_cases(use_cases: ProviderSessionUseCases) -> None:
    global _use_cases
    _use_cases = use_cases


def configured_provider_session_use_cases() -> ProviderSessionUseCases:
    if _use_cases is None:
        raise ProviderSessionConflictError("provider_session_use_cases_not_configured")
    return _use_cases


async def execute_provider_session_callback(conn: Any, **kwargs: Any) -> ProviderSessionOperationResult:
    return await configured_provider_session_use_cases().execute_callback(conn, **kwargs)


async def provider_session_has_main_transcript(conn: Any, **kwargs: Any) -> bool:
    return await configured_provider_session_use_cases().has_main_transcript(conn, **kwargs)


__all__ = [
    "ProviderSessionOperationResult",
    "ProviderSessionRepository",
    "ProviderSessionUseCases",
    "configure_provider_session_use_cases",
    "configured_provider_session_use_cases",
    "execute_provider_session_callback",
    "provider_session_has_main_transcript",
]
