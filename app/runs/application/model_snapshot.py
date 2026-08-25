"""Application service for Run-owned model snapshots."""

from __future__ import annotations

from typing import Any, Protocol


class RunModelSnapshotRepository(Protocol):
    async def bind(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        model_id: str,
        model_value: str,
        connection_revision: int | None,
    ) -> None: ...

    async def inherit(
        self,
        conn: Any,
        *,
        tenant_id: str,
        source_run_id: str,
        child_run_id: str,
    ) -> None: ...


class RunModelSnapshotService:
    def __init__(self, repository: RunModelSnapshotRepository) -> None:
        self._repository = repository

    async def bind(self, conn: Any, **kwargs: Any) -> None:
        await self._repository.bind(conn, **kwargs)

    async def inherit(self, conn: Any, **kwargs: Any) -> None:
        await self._repository.inherit(conn, **kwargs)


_service: RunModelSnapshotService | None = None


def configure_run_model_snapshots(service: RunModelSnapshotService) -> None:
    global _service
    _service = service


def _configured_service() -> RunModelSnapshotService:
    if _service is None:
        raise RuntimeError("run_model_snapshot_service_not_configured")
    return _service


async def bind_run_model(conn: Any, **kwargs: Any) -> None:
    await _configured_service().bind(conn, **kwargs)


async def inherit_run_model(conn: Any, **kwargs: Any) -> None:
    await _configured_service().inherit(conn, **kwargs)
