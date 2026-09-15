"""Context repository double. Production authority has no test-specific branches.

Fixtures must include real metadata (especially size_bytes). This double does
not synthesize missing metadata or claim to emulate PostgreSQL locking/ACL SQL.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.storage import ObjectStorageSizeLimitError


class InMemoryContextRetrievalRepository:
    def __init__(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        memory_records: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages = deepcopy(messages or [])
        self.files = deepcopy(files or [])
        self.artifacts = deepcopy(artifacts or [])
        self.memory_records = deepcopy(memory_records or [])

    @staticmethod
    def _matches(row: dict[str, Any], scope: dict[str, str]) -> bool:
        return all(row.get(key) == value for key, value in scope.items())

    async def list_messages(
        self, *, tenant_id: str, workspace_id: str, user_id: str,
        session_id: str, run_id: str, limit: int, offset: int,
    ) -> list[dict[str, Any]]:
        scope = dict(tenant_id=tenant_id, workspace_id=workspace_id,
                     user_id=user_id, session_id=session_id, run_id=run_id)
        # Input fixtures declare durable order. SQL ordering has its own real-DB tests.
        rows = [row for row in self.messages if self._matches(row, scope)]
        start = max(0, offset)
        return deepcopy(rows[start:start + max(0, limit)])

    async def get_file(
        self, *, tenant_id: str, workspace_id: str, user_id: str,
        session_id: str, run_id: str, file_id: str,
    ) -> dict[str, Any] | None:
        scope = dict(tenant_id=tenant_id, workspace_id=workspace_id,
                     user_id=user_id, session_id=session_id, run_id=run_id)
        return self._get(self.files, scope, 'file_id', file_id)

    async def get_artifact(
        self, *, tenant_id: str, workspace_id: str, user_id: str,
        session_id: str, run_id: str, artifact_id: str,
    ) -> dict[str, Any] | None:
        scope = dict(tenant_id=tenant_id, workspace_id=workspace_id,
                     user_id=user_id, session_id=session_id, run_id=run_id)
        return self._get(self.artifacts, scope, 'artifact_id', artifact_id)

    def _get(
        self, rows: list[dict[str, Any]], scope: dict[str, str],
        key: str, identity: str,
    ) -> dict[str, Any] | None:
        return next((deepcopy(row) for row in rows
                     if self._matches(row, scope)
                     and (row.get(key) or row.get('id')) == identity), None)

    async def list_memory_records(
        self, *, tenant_id: str, workspace_id: str, user_id: str,
        agent_id: str, session_id: str, query: str, limit: int,
    ) -> list[dict[str, Any]]:
        scope = dict(tenant_id=tenant_id, workspace_id=workspace_id,
                     user_id=user_id, session_id=session_id, agent_id=agent_id)
        terms = query.casefold().split()
        rows = [row for row in self.memory_records
                if self._matches(row, scope)
                and row.get('status', 'active') == 'active'
                and not row.get('deleted_at')
                and (not terms or any(term in str(row.get('content', '')).casefold()
                                      for term in terms))]
        return deepcopy(rows[:max(0, limit)])

    def read_storage_bytes(
        self, row: dict[str, Any], *, max_bytes: int | None = None,
    ) -> bytes:
        content = row.get('content', b'')
        raw = content if isinstance(content, bytes) else str(content).encode('utf-8')
        if max_bytes is not None and len(raw) > max_bytes:
            raise ObjectStorageSizeLimitError('object_size_limit_exceeded')
        return raw
