"""Runs-owned callback consumption and administrator usage query capability."""
from __future__ import annotations

from typing import Protocol, Any
from app.runs.domain.model_token_usage import normalize_model_usage_snapshot, window_utc


class ModelTokenUsagePersistence(Protocol):
    async def record(self, conn, **kwargs) -> str: ...
    async def query(self, conn, **kwargs) -> dict: ...


class ModelTokenUsageService:
    def __init__(self, persistence: ModelTokenUsagePersistence):
        self.persistence = persistence

    async def record(self, conn, *, tenant_id, run_id, attempt_id, snapshot, receipt_id, recorded_at) -> str:
        value = normalize_model_usage_snapshot(snapshot)
        return await self.persistence.record(conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
            snapshot=value, receipt_id=receipt_id, recorded_at=recorded_at)

    async def query(self, conn, *, tenant_id: str, start_date=None, end_date=None,
                    user_id=None, q=None, limit=50, offset=0) -> dict[str, Any]:
        start, end, start_at, end_at = window_utc(start_date, end_date)
        result = await self.persistence.query(conn, tenant_id=tenant_id, start_at=start_at, end_at=end_at,
            user_id=user_id, q=q, limit=limit, offset=offset)
        result["meta"].update(start_date=start.isoformat(), end_date=end.isoformat(), timezone="Asia/Shanghai")
        return result
