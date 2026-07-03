from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class SessionContinuityRef:
    sdk_session_id: str
    lock_key: str
    forked: bool = False


class InMemorySessionContinuityStore:
    """In-process continuity adapter for tests and single-worker development."""

    def __init__(self) -> None:
        self._resume_keys: dict[str, str] = {}
        self._fork_counter = 0

    async def get_or_create(self, key: str) -> str:
        if key not in self._resume_keys:
            self._resume_keys[key] = _stable_sdk_session_uuid(key)
        return self._resume_keys[key]

    async def fork(self, key: str, reason: str) -> str:
        self._fork_counter += 1
        return _stable_sdk_session_uuid(f"{key}\x1f{reason or 'fork'}\x1f{self._fork_counter}")


class SessionContinuity:
    """Resolve SDK resume keys and serialize writes for a platform session scope."""

    def __init__(self, store: InMemorySessionContinuityStore | None = None) -> None:
        self._store = store or InMemorySessionContinuityStore()
        self._locks: dict[str, asyncio.Lock] = {}

    async def resolve(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        agent_id: str,
        skill_id: str,
        model_key: str,
        fork_reason: str | None = None,
    ) -> SessionContinuityRef:
        key = self._key(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            skill_id=skill_id,
            model_key=model_key,
        )
        if fork_reason:
            sdk_session_id = await self._store.fork(key, fork_reason)
            safe_reason = _safe_lock_segment(fork_reason)
            lock_key = f"{key}:fork:{safe_reason}:{_digest(sdk_session_id)}"
            return SessionContinuityRef(sdk_session_id=sdk_session_id, lock_key=lock_key, forked=True)
        sdk_session_id = await self._store.get_or_create(key)
        return SessionContinuityRef(sdk_session_id=sdk_session_id, lock_key=key, forked=False)

    @asynccontextmanager
    async def sdk_session_lock(self, lock_key: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            yield

    def _key(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        agent_id: str,
        skill_id: str,
        model_key: str,
    ) -> str:
        return "\x1f".join(
            [
                tenant_id,
                workspace_id,
                user_id,
                session_id,
                agent_id,
                skill_id,
                model_key or "default-model",
            ]
        )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _stable_sdk_session_uuid(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-platform-sdk-session:{value}"))


def _safe_lock_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value or "fork")
