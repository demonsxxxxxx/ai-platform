from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.context.domain.provider_sessions import (
    ProviderSessionConflictError,
    ProviderSessionScope,
)


class ProviderSessionRepository(Protocol):
    async def matching_ready_epoch(self, conn: Any, *, scope: ProviderSessionScope,
                                   run_id: str, source_sha256: str, message_count: int) -> bool: ...

    async def callback_epoch(self, conn: Any, **scope: Any) -> dict[str, Any]: ...

    async def claim_lineage(self, conn: Any, *, scope: ProviderSessionScope, run_id: str) -> None: ...

    async def release_lineage(self, conn: Any, *, tenant_id: str, run_id: str) -> None: ...

    async def prepare_epoch(self, conn: Any, *, scope: ProviderSessionScope,
                            run_id: str, conversation_context: Mapping[str, Any]) -> dict[str, Any]: ...

    async def commit_turn(self, conn: Any, *, tenant_id: str, run_id: str,
                          attempt_id: str, assistant_message_id: str, final_sequence: int) -> None: ...


@dataclass(frozen=True)
class ProviderSessionOperationResult:
    action: str
    accepted: bool = True
    entries: tuple[dict[str, Any], ...] = ()
    subpaths: tuple[str, ...] = ()
    accepted_entry_count: int = 0
    next_sequence: int = 1
    last_sequence: int | None = None

    @property
    def entry_count(self) -> int:
        return self.accepted_entry_count or len(self.entries)


class ProviderSessionUseCases:
    """Context-owned provider transcript operations over an injected repository."""

    def __init__(self, repository: ProviderSessionRepository) -> None:
        self._repository = repository

    async def matching_ready_epoch(self, conn: Any, *, scope: ProviderSessionScope,
                                   run_id: str, source_sha256: str, message_count: int) -> bool:
        return await self._repository.matching_ready_epoch(
            conn, scope=scope, run_id=run_id,
            source_sha256=source_sha256, message_count=message_count,
        )

    async def claim_lineage(self, conn: Any, *, scope: ProviderSessionScope, run_id: str) -> None:
        await self._repository.claim_lineage(conn, scope=scope, run_id=run_id)

    async def release_lineage(self, conn: Any, *, tenant_id: str, run_id: str) -> None:
        await self._repository.release_lineage(conn, tenant_id=tenant_id, run_id=run_id)

    async def prepare_epoch(self, conn: Any, *, scope: ProviderSessionScope,
                            run_id: str, conversation_context: Mapping[str, Any]) -> dict[str, Any]:
        return await self._repository.prepare_epoch(
            conn, scope=scope, run_id=run_id, conversation_context=conversation_context,
        )

    async def commit_turn(self, conn: Any, *, tenant_id: str, run_id: str,
                          attempt_id: str, assistant_message_id: str, final_sequence: int) -> None:
        await self._repository.commit_turn(
            conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            assistant_message_id=assistant_message_id, final_sequence=final_sequence,
        )

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
        expected_sequence: int | None,
    ) -> ProviderSessionOperationResult:
        receipt = await self._repository.callback_epoch(
            conn, tenant_id=tenant_id, workspace_id=workspace_id,
            user_id=user_id, session_id=session_id, agent_id=agent_id,
            run_id=run_id, attempt_id=attempt_id,
            provider_session_id=provider_session_id, action=action,
            subpath=subpath, entries=entries, expected_sequence=expected_sequence,
        )
        return ProviderSessionOperationResult(
            action=action,
            entries=tuple(receipt.get("entries", ())),
            subpaths=tuple(receipt.get("subpaths", ())),
            accepted_entry_count=receipt.get("entry_count", 0),
            next_sequence=receipt["next_sequence"],
            last_sequence=receipt.get("last_sequence"),
        )


_use_cases: ProviderSessionUseCases | None = None


def configure_provider_session_use_cases(use_cases: ProviderSessionUseCases) -> None:
    global _use_cases
    _use_cases = use_cases


def configured_provider_session_use_cases() -> ProviderSessionUseCases:
    if _use_cases is None:
        raise ProviderSessionConflictError("provider_session_use_cases_not_configured")
    return _use_cases


async def matching_ready_provider_epoch(conn: Any, *, scope: ProviderSessionScope,
                                        run_id: str, source_sha256: str, message_count: int) -> bool:
    return await configured_provider_session_use_cases().matching_ready_epoch(
        conn, scope=scope, run_id=run_id,
        source_sha256=source_sha256, message_count=message_count,
    )


async def claim_provider_lineage(conn: Any, *, scope: ProviderSessionScope, run_id: str) -> None:
    await configured_provider_session_use_cases().claim_lineage(conn, scope=scope, run_id=run_id)


async def release_provider_lineage(conn: Any, *, tenant_id: str, run_id: str) -> None:
    await configured_provider_session_use_cases().release_lineage(conn, tenant_id=tenant_id, run_id=run_id)


async def prepare_provider_epoch(conn: Any, *, scope: ProviderSessionScope, run_id: str,
                                 conversation_context: Mapping[str, Any]) -> dict[str, Any]:
    return await configured_provider_session_use_cases().prepare_epoch(
        conn, scope=scope, run_id=run_id, conversation_context=conversation_context,
    )


async def commit_provider_turn(conn: Any, *, tenant_id: str, run_id: str,
                               attempt_id: str, assistant_message_id: str,
                               final_sequence: int) -> None:
    await configured_provider_session_use_cases().commit_turn(
        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
        assistant_message_id=assistant_message_id, final_sequence=final_sequence,
    )


async def execute_provider_session_callback(conn: Any, **kwargs: Any) -> ProviderSessionOperationResult:
    return await configured_provider_session_use_cases().execute_callback(conn, **kwargs)


__all__ = [
    "ProviderSessionOperationResult",
    "ProviderSessionRepository",
    "ProviderSessionUseCases",
    "matching_ready_provider_epoch",
    "claim_provider_lineage",
    "release_provider_lineage",
    "prepare_provider_epoch",
    "commit_provider_turn",
    "configure_provider_session_use_cases",
    "configured_provider_session_use_cases",
    "execute_provider_session_callback",
]
