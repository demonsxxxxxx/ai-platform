from fastapi import APIRouter, Depends, HTTPException

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.models import AgentAppProjection, AgentAppsResponse

router = APIRouter()


def _projection_mode(agent_type: str) -> str:
    if agent_type == "chat":
        return "chat"
    if agent_type == "file":
        return "chat_file"
    return "chat_file"


@router.get("/agent-apps", response_model=AgentAppsResponse)
async def list_agent_apps(
    principal: AuthPrincipal = Depends(require_principal),
) -> AgentAppsResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")
    async with transaction() as conn:
        rows = await repositories.list_agent_app_projections(conn, tenant_id=principal.tenant_id)
    return AgentAppsResponse(
        agent_apps=[
            AgentAppProjection(
                app_id=row["app_id"],
                name=row["name"],
                mode=_projection_mode(row["agent_type"]),
                default_skill_id=row["default_skill_id"],
                allowed_input_types=row["input_modes"] or [],
                output_types=row["output_modes"] or [],
                status=row["status"],
            )
            for row in rows
        ]
    )
