"""Application service for Run-owned model snapshots."""

from __future__ import annotations

from typing import Any, Protocol


class RunModelSnapshotRepository(Protocol):
    async def load(self, conn: Any, *, tenant_id: str, run_id: str) -> dict[str, Any]: ...

    async def bind(
        self, conn: Any, *, tenant_id: str, run_id: str, model_id: str, model_value: str,
        connection_revision: int, auto_compact_window: int, max_output_tokens: int,
        group_id: str | None = None, group_code: str | None = None,
        group_type: str | None = None, group_revision: int | None = None,
        selection_mode: str = "auto", usage_scene: str = "independent",
        policy_version: str | None = None,
    ) -> None: ...

    async def replace(self, conn: Any, **kwargs: Any) -> None: ...


class RunModelSnapshotService:
    def __init__(self, repository: RunModelSnapshotRepository) -> None:
        self._repository = repository

    async def load(self, conn: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._repository.load(conn, **kwargs)

    async def bind(self, conn: Any, **kwargs: Any) -> None:
        await self._repository.bind(conn, **kwargs)

    async def replace(self, conn: Any, **kwargs: Any) -> None:
        await self._repository.replace(conn, **kwargs)


_service: RunModelSnapshotService | None = None


def configure_run_model_snapshots(service: RunModelSnapshotService) -> None:
    global _service
    _service = service


def _configured_service() -> RunModelSnapshotService:
    if _service is None:
        raise RuntimeError("run_model_snapshot_service_not_configured")
    return _service


async def load_run_model_snapshot(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await _configured_service().load(conn, **kwargs)


async def bind_run_model(conn: Any, **kwargs: Any) -> None:
    await _configured_service().bind(conn, **kwargs)


async def replace_run_model_snapshot(conn: Any, **kwargs: Any) -> None:
    await _configured_service().replace(conn, **kwargs)
