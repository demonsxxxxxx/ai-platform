"""Identity-owned company employee directory, independent of local login records."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
from time import monotonic


class CompanyEmployeeDirectoryUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("company_employee_directory_unavailable")


@dataclass(frozen=True)
class CompanyEmployee:
    work_id: str
    display_name: str
    department_ids: tuple[str, ...] = ()


class CompanyEmployeeDirectoryService:
    def __init__(
        self, reader: Callable[[], Awaitable[list[CompanyEmployee]]], *, tenant_id: str,
    ) -> None:
        self._reader = reader
        self._tenant_id = tenant_id
        self._snapshot: tuple[str, list[CompanyEmployee]] | None = None
        self._snapshot_expires_at = 0.0
        self._snapshot_lock = asyncio.Lock()

    async def _listing_snapshot(self) -> tuple[str, list[CompanyEmployee]]:
        async with self._snapshot_lock:
            if self._snapshot is None or monotonic() >= self._snapshot_expires_at:
                employees = await self._reader()
                # Stable across API processes so a changed roster cannot silently mix pagination.
                revision = hashlib.sha256(json.dumps([
                    (item.work_id, item.display_name, item.department_ids) for item in employees
                ], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                self._snapshot = (revision, employees)
                self._snapshot_expires_at = monotonic() + 60
            # Pagination shares one validated roster; grant validation below always reads fresh authority.
            assert self._snapshot is not None
            return self._snapshot

    def _require_scope(self, tenant_id: str) -> None:
        if tenant_id != self._tenant_id:
            raise PermissionError("company_employee_directory_forbidden")

    async def list_employees(
        self, *, tenant_id: str, search: str = "", offset: int = 0, limit: int = 50,
        department_id: str = "",
    ) -> dict:
        self._require_scope(tenant_id)
        if (not 0 <= offset <= 50_000 or not 1 <= limit <= 100 or len(search) > 128
                or (department_id and (not department_id.isascii() or not department_id.isdecimal()
                                      or department_id.startswith("0") or len(department_id) > 10))):
            raise ValueError("company_employee_directory_query_invalid")
        revision, employees = await self._listing_snapshot()
        query = search.strip().casefold()
        matches = [employee for employee in employees
                   if (not department_id or department_id in employee.department_ids)
                   and (not query or query in employee.work_id.casefold()
                        or query in employee.display_name.casefold())]
        return {
            "employees": [{"work_id": item.work_id, "display_name": item.display_name,
                           "department_ids": list(item.department_ids)}
                          for item in matches[offset:offset + limit]],
            "total": len(matches), "offset": offset, "limit": limit, "snapshot_id": revision,
        }

    async def validate_grants(self, *, tenant_id: str, work_ids: set[str]) -> None:
        self._require_scope(tenant_id)
        if work_ids and not work_ids.issubset({item.work_id for item in await self._reader()}):
            raise ValueError("model_user_access_unknown_user")
