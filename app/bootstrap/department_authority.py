"""Compose the company department client with the existing pure-tree adapter."""

from app.department_directory import department_parent_ids, fetch_department_directory
from app.identity.api import DepartmentAuthority
from app.identity.infrastructure.dingtalk_departments import fetch_dingtalk_department_authority
from app.settings import get_settings


async def resolve_department_authority(work_id: str, *, settings=None, fresh: bool = False) -> DepartmentAuthority:
    current = settings or get_settings()
    base_url = str(getattr(current, "dingtalk_department_authority_base_url", "") or "")
    timeout_seconds = float(getattr(current, "dingtalk_department_authority_timeout_seconds", 5.0))

    async def directory_reader() -> dict[str, str | None]:
        return department_parent_ids(await fetch_department_directory(
            base_url=base_url, timeout_seconds=timeout_seconds,
        ))

    return await fetch_dingtalk_department_authority(
        work_id,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        directory_reader=directory_reader,
        fresh=fresh,
    )
