from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Protocol

from app import repositories
from app.control_plane_contracts import sanitize_public_payload
from app.path_safety import ensure_creatable_inside


class ContextRetrievalDenied(PermissionError):
    pass


def _token_count(value: str) -> int:
    return len(str(value or "").split())


def _bounded_text(value: object, *, max_bytes: int | None = None, max_tokens: int | None = None) -> tuple[str, bool]:
    text = str(value or "")
    sanitized = sanitize_public_payload(text)
    text = sanitized if isinstance(sanitized, str) else ""
    truncated = False
    if max_bytes is not None:
        encoded = text.encode("utf-8")
        if len(encoded) > max_bytes:
            text = encoded[: max(0, int(max_bytes))].decode("utf-8", errors="ignore")
            truncated = True
    if max_tokens is not None:
        words = text.split()
        if len(words) > max_tokens:
            text = " ".join(words[: max(0, int(max_tokens))])
            truncated = True
    return text, truncated


def _scope_matches(row: dict[str, Any], *, tenant_id: str, workspace_id: str, user_id: str, session_id: str | None = None, run_id: str | None = None) -> bool:
    if str(row.get("tenant_id") or "") != tenant_id:
        return False
    if str(row.get("workspace_id") or "") != workspace_id:
        return False
    if str(row.get("user_id") or "") != user_id:
        return False
    if session_id is not None and str(row.get("session_id") or "") != session_id:
        return False
    if run_id is not None and str(row.get("run_id") or "") != run_id:
        return False
    return True


class InMemoryContextRetrievalRepository:
    """Small in-memory adapter used by focused contract tests."""

    def __init__(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        memory_records: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages = list(messages or [])
        self.files = list(files or [])
        self.artifacts = list(artifacts or [])
        self.memory_records = list(memory_records or [])


class ContextRetrievalRepository(Protocol):
    async def list_messages(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        ...

    async def get_file(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        file_id: str,
    ) -> dict[str, Any] | None:
        ...

    async def get_artifact(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        artifact_id: str,
    ) -> dict[str, Any] | None:
        ...

    async def list_memory_records(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        ...

    def read_storage_bytes(self, row: dict[str, Any]) -> bytes:
        ...


class RepositoryContextRetrievalRepository:
    """Repository/storage adapter for scoped context retrieval tools."""

    def __init__(self, conn: Any, *, storage: Any) -> None:
        self._conn = conn
        self._storage = storage

    async def list_messages(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        rows = await repositories.list_scoped_context_messages(
            self._conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )
        return [dict(row) for row in rows]

    async def get_file(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        file_id: str,
    ) -> dict[str, Any] | None:
        row = await repositories.get_scoped_context_file(
            self._conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            file_id=file_id,
        )
        return dict(row) if row is not None else None

    async def get_artifact(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        artifact_id: str,
    ) -> dict[str, Any] | None:
        row = await repositories.get_scoped_context_artifact(
            self._conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            artifact_id=artifact_id,
        )
        return dict(row) if row is not None else None

    async def list_memory_records(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = await repositories.list_scoped_context_memory_records(
            self._conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            query=query,
            limit=limit,
        )
        return [dict(row) for row in rows]

    def read_storage_bytes(self, row: dict[str, Any]) -> bytes:
        storage_key = str(row.get("storage_key") or "")
        if not storage_key:
            return b""
        return self._storage.get_bytes(storage_key=storage_key)


class TransactionalContextRetrievalRepository:
    """Repository adapter that opens a fresh DB transaction per retrieval call."""

    def __init__(
        self,
        transaction_factory: Callable[[], AbstractAsyncContextManager[Any]],
        *,
        storage: Any,
    ) -> None:
        self._transaction_factory = transaction_factory
        self._storage = storage

    async def list_messages(self, **kwargs: Any) -> list[dict[str, Any]]:
        async with self._transaction_factory() as conn:
            rows = await repositories.list_scoped_context_messages(conn, **kwargs)
        return [dict(row) for row in rows]

    async def get_file(self, **kwargs: Any) -> dict[str, Any] | None:
        async with self._transaction_factory() as conn:
            row = await repositories.get_scoped_context_file(conn, **kwargs)
        return dict(row) if row is not None else None

    async def get_artifact(self, **kwargs: Any) -> dict[str, Any] | None:
        async with self._transaction_factory() as conn:
            row = await repositories.get_scoped_context_artifact(conn, **kwargs)
        return dict(row) if row is not None else None

    async def list_memory_records(self, **kwargs: Any) -> list[dict[str, Any]]:
        async with self._transaction_factory() as conn:
            rows = await repositories.list_scoped_context_memory_records(conn, **kwargs)
        return [dict(row) for row in rows]

    def read_storage_bytes(self, row: dict[str, Any]) -> bytes:
        storage_key = str(row.get("storage_key") or "")
        if not storage_key:
            return b""
        return self._storage.get_bytes(storage_key=storage_key)


class ContextRetrieval:
    """Tenant/user/workspace scoped retrieval tools for bounded context manifests."""

    def __init__(self, repository: InMemoryContextRetrievalRepository | ContextRetrievalRepository) -> None:
        self._repository = repository

    async def read_session_messages(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        limit: int = 20,
        offset: int = 0,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        if isinstance(self._repository, InMemoryContextRetrievalRepository):
            rows = [
                row
                for row in self._repository.messages
                if str(row.get("tenant_id") or "") == tenant_id
                and str(row.get("session_id") or "") == session_id
                and str(row.get("run_id") or "") == run_id
            ]
            if rows and not any(
                _scope_matches(
                    row,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                )
                for row in rows
            ):
                raise ContextRetrievalDenied("context_scope_denied")
            scoped_rows = [
                row
                for row in rows
                if _scope_matches(
                    row,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                )
            ]
        else:
            scoped_rows = await self._repository.list_messages(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                limit=max(1, int(limit)) + 1,
                offset=offset,
            )
        if not scoped_rows:
            raise ContextRetrievalDenied("context_scope_denied")
        selected: list[dict[str, Any]] = []
        spent_tokens = 0
        page_limit = max(0, int(limit))
        rows_page = (
            scoped_rows[:page_limit]
            if not isinstance(self._repository, InMemoryContextRetrievalRepository)
            else scoped_rows[max(0, offset) : max(0, offset) + page_limit]
        )
        for row in rows_page:
            content, truncated = _bounded_text(row.get("content"), max_tokens=max(1, max_tokens - spent_tokens))
            tokens = _token_count(content)
            if selected and spent_tokens + tokens > max_tokens:
                break
            if tokens == 0 and row.get("content"):
                break
            selected.append(
                {
                    "message_id": str(row.get("message_id") or row.get("id") or ""),
                    "run_id": str(row.get("run_id") or ""),
                    "role": str(row.get("role") or ""),
                    "content": content,
                    "truncated": truncated,
                }
            )
            spent_tokens += tokens
            if spent_tokens >= max_tokens:
                break
        next_offset = max(0, offset) + len(selected)
        has_more = (
            len(scoped_rows) > page_limit
            if not isinstance(self._repository, InMemoryContextRetrievalRepository)
            else next_offset < len(scoped_rows)
        )
        if (
            isinstance(self._repository, InMemoryContextRetrievalRepository)
            and not has_more
            and len(selected) < len(rows_page)
        ):
            has_more = True
        return self._envelope(
            "context_retrieval.read_session_messages",
            items=selected,
            next_offset=next_offset if has_more else None,
        )

    async def read_context_file(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        file_id: str,
        max_bytes: int = 65536,
    ) -> dict[str, Any]:
        row = await self._get_file_row(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            file_id=file_id,
        )
        content, truncated = self._bounded_content_from_row(row, max_bytes=max_bytes)
        return self._envelope(
            "context_retrieval.read_context_file",
            file_id=file_id,
            name=self._safe_name(row),
            content=content,
            truncated=truncated,
        )

    async def read_run_artifact(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        artifact_id: str,
        max_bytes: int = 65536,
    ) -> dict[str, Any]:
        row = await self._get_artifact_row(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            artifact_id=artifact_id,
        )
        content, truncated = self._bounded_content_from_row(row, max_bytes=max_bytes)
        return self._envelope(
            "context_retrieval.read_run_artifact",
            artifact_id=artifact_id,
            artifact_type=str(row.get("artifact_type") or ""),
            label=self._safe_name(row),
            content=content,
            truncated=truncated,
        )

    async def stage_context_file_to_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        file_id: str,
        workspace_root: str,
    ) -> dict[str, Any]:
        row = await self._get_file_row(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            file_id=file_id,
        )
        name = self._safe_name(row)
        file_segment = self._safe_id_segment(file_id)
        raw_bytes = self._raw_content_bytes(row)
        target_path = Path(workspace_root) / "context" / file_segment / name
        ensure_creatable_inside(workspace_root, target_path, "context_file_workspace_escape")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(raw_bytes)
        return self._envelope(
            "context_retrieval.stage_context_file_to_workspace",
            file_id=file_id,
            workspace_path=f"context/{file_segment}/{name}",
            bytes_staged=len(raw_bytes),
        )

    async def search_memory(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        query: str,
        limit: int = 10,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        if isinstance(self._repository, InMemoryContextRetrievalRepository):
            terms = [term.casefold() for term in str(query or "").split() if term.strip()]
            rows = [
                row
                for row in self._repository.memory_records
                if str(row.get("agent_id") or "") == agent_id
                and _scope_matches(
                    row,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                )
                and str(row.get("status") or "active") == "active"
                and not row.get("deleted_at")
            ]
            matching = [
                row
                for row in rows
                if not terms or any(term in str(row.get("content") or "").casefold() for term in terms)
            ]
        else:
            matching = await self._repository.list_memory_records(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                query=query,
                limit=limit,
            )
        items = []
        spent_tokens = 0
        for row in matching[: max(0, limit)]:
            content, truncated = _bounded_text(row.get("content"), max_tokens=max(1, max_tokens - spent_tokens))
            tokens = _token_count(content)
            if items and spent_tokens + tokens > max_tokens:
                break
            items.append(
                {
                    "memory_record_id": str(row.get("memory_record_id") or row.get("id") or ""),
                    "record_type": str(row.get("record_type") or ""),
                    "content": content,
                    "truncated": truncated,
                }
            )
            spent_tokens += tokens
            if spent_tokens >= max_tokens:
                break
        return self._envelope("context_retrieval.search_memory", items=items)

    def _find_scoped(
        self,
        rows: list[dict[str, Any]],
        id_key: str,
        id_value: str,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        candidates = [row for row in rows if str(row.get(id_key) or row.get("id") or "") == id_value]
        for row in candidates:
            if _scope_matches(
                row,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
            ):
                return row
        raise ContextRetrievalDenied("context_scope_denied")

    async def _get_file_row(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        file_id: str,
    ) -> dict[str, Any]:
        if isinstance(self._repository, InMemoryContextRetrievalRepository):
            return self._find_scoped(
                self._repository.files,
                "file_id",
                file_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
            )
        row = await self._repository.get_file(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            file_id=file_id,
        )
        if row is None:
            raise ContextRetrievalDenied("context_scope_denied")
        return row

    async def _get_artifact_row(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        if isinstance(self._repository, InMemoryContextRetrievalRepository):
            return self._find_scoped(
                self._repository.artifacts,
                "artifact_id",
                artifact_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
            )
        row = await self._repository.get_artifact(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            artifact_id=artifact_id,
        )
        if row is None:
            raise ContextRetrievalDenied("context_scope_denied")
        return row

    def _raw_content_bytes(self, row: dict[str, Any]) -> bytes:
        if isinstance(self._repository, InMemoryContextRetrievalRepository):
            return str(row.get("content") or "").encode("utf-8")
        return self._repository.read_storage_bytes(row)

    def _bounded_content_from_row(self, row: dict[str, Any], *, max_bytes: int) -> tuple[str, bool]:
        if isinstance(self._repository, InMemoryContextRetrievalRepository):
            return _bounded_text(row.get("content"), max_bytes=max_bytes)
        raw = self._raw_content_bytes(row)
        truncated = len(raw) > max_bytes
        bounded = raw[: max(0, int(max_bytes))] if truncated else raw
        text, text_truncated = _bounded_text(bounded.decode("utf-8", errors="ignore"))
        return text, truncated or text_truncated

    def _safe_name(self, row: dict[str, Any]) -> str:
        name = str(row.get("original_name") or row.get("label") or row.get("name") or row.get("file_id") or row.get("artifact_id") or "context.bin")
        return PurePosixPath(name).name or "context.bin"

    def _safe_id_segment(self, value: object) -> str:
        text = str(value or "").strip()
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in text)
        return safe or "context-file"

    def _envelope(self, action: str, **payload: Any) -> dict[str, Any]:
        return {
            **payload,
            "audit": {"action": action},
            "redaction": {"object_locator_refs_removed": True},
        }
