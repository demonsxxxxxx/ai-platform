"""Minimal durable setup for real POST /api/ai/runs lifecycle acceptance."""

from __future__ import annotations

from psycopg import AsyncConnection

from app.execution.infrastructure.model_management import publish_models
from app.execution.infrastructure.model_security import api_key_fingerprint
from tests.support.run_lifecycle_executor import (
    SYNTHETIC_MODEL_API_KEY,
    SYNTHETIC_MODEL_NAME,
)


TENANT_ID = "default"
GENERAL_AGENT_ID = "general-agent"


async def seed_admission(
    conn: AsyncConnection,
    *,
    workspace_id: str,
    user_id: str,
    encryption_key: str,
) -> None:
    """Seed only tenant, workspace, user, chat agent, and model admission data."""

    if not workspace_id.strip() or not user_id.strip():
        raise ValueError("run_lifecycle_admission_identity_invalid")

    await conn.execute(
        """
        insert into tenants(id, name) values (%s, %s)
        on conflict (id) do nothing
        """,
        (TENANT_ID, "Default Tenant"),
    )
    await conn.execute(
        """
        insert into workspaces(id, tenant_id, name, status)
        values (%s, %s, %s, 'active')
        on conflict (id) do update
        set tenant_id = excluded.tenant_id, name = excluded.name, status = 'active'
        """,
        (workspace_id, TENANT_ID, workspace_id),
    )
    await conn.execute(
        """
        insert into users(id, tenant_id, display_name, status)
        values (%s, %s, %s, 'active')
        on conflict (id) do update
        set tenant_id = excluded.tenant_id,
            display_name = excluded.display_name,
            status = 'active'
        """,
        (user_id, TENANT_ID, "N01 lifecycle test user"),
    )
    await conn.execute(
        """
        insert into agents(id, tenant_id, name, agent_type, description,
                           default_skill_id, status)
        values (%s, %s, %s, 'chat', %s, null, 'active')
        on conflict (id) do update
        set tenant_id = excluded.tenant_id,
            name = excluded.name,
            agent_type = 'chat',
            description = excluded.description,
            default_skill_id = null,
            status = 'active'
        """,
        (
            GENERAL_AGENT_ID,
            TENANT_ID,
            "通用聊天 Agent",
            "Synthetic lifecycle acceptance agent.",
        ),
    )

    cursor = await conn.execute(
        "select revision from model_gateway_revisions where active = true limit 1"
    )
    active_gateway = await cursor.fetchone()
    expected_revision = (
        int(active_gateway["revision"]) if active_gateway is not None else None
    )
    await publish_models(
        conn,
        expected_revision=expected_revision,
        base_url="http://model.invalid",
        api_key=SYNTHETIC_MODEL_API_KEY,
        key_fingerprint=api_key_fingerprint(SYNTHETIC_MODEL_API_KEY),
        encryption_key=encryption_key,
        actor_user_id=user_id,
        upstream_model_ids=[SYNTHETIC_MODEL_NAME],
        models=[
            {
                "id": SYNTHETIC_MODEL_NAME,
                "value": SYNTHETIC_MODEL_NAME,
                "display_name": "N01 Synthetic Chat Model",
                "enabled": True,
                "is_default": True,
                "order": 1,
                "auto_compact_window": 100_000,
                "max_output_tokens": 4_096,
                "access_mode": "all",
                "allowed_user_ids": [],
            }
        ],
    )


__all__ = [
    "GENERAL_AGENT_ID",
    "TENANT_ID",
    "seed_admission",
]
