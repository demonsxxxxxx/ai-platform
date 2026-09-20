from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.platform.postgres.errors import RepositoryNotFoundError


async def list_workbench_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_admin_fields: bool = False,
) -> list[dict[str, Any]]:
    """Reject the retired mixed Agent/Skill Workbench catalog."""

    del conn, tenant_id, include_admin_fields
    raise RepositoryNotFoundError("workbench_capability_catalog_retired")


async def list_workbench_skills(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    """Reject the retired fixed-list Workbench Skill catalog."""

    del conn, tenant_id, include_disabled
    raise RepositoryNotFoundError("workbench_skill_catalog_retired")
