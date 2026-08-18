from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from app.auth import AuthPrincipal
from app.control_plane_contracts import RUN_EXECUTION_KIND_SKILL
from app.models import QueueRunPayload
from app.principal_authority import PrincipalAuthorityDenied


RUN_IDENTITY_FIELDS = (
    "tenant_id",
    "workspace_id",
    "user_id",
    "session_id",
    "run_id",
    "agent_id",
    "execution_kind",
    "skill_id",
)


def _payload_identity(payload: QueueRunPayload) -> dict[str, str]:
    return {field: str(getattr(payload, field) or "") for field in RUN_IDENTITY_FIELDS}


def _locked_run_identity(payload: QueueRunPayload, locked_run: object) -> dict[str, str]:
    if not isinstance(locked_run, dict):
        return _payload_identity(payload)
    identity: dict[str, str] = {}
    for field in RUN_IDENTITY_FIELDS:
        value = locked_run.get("id") if field == "run_id" else locked_run.get(field)
        if field == "execution_kind" and value is None:
            value = RUN_EXECUTION_KIND_SKILL
        identity[field] = str(value) if value else ""
    return identity


def _identity_mismatch_fields(payload: QueueRunPayload, identity: dict[str, str]) -> list[str]:
    payload_identity = _payload_identity(payload)
    return [field for field in RUN_IDENTITY_FIELDS if payload_identity[field] != str(identity[field])]


async def _resolve_current_principal_before_dispatch(
    payload: QueueRunPayload,
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]],
    run_loader: Callable[..., Awaitable[dict[str, Any] | None]],
    principal_resolver: Callable[..., Awaitable[AuthPrincipal]],
) -> AuthPrincipal | None:
    """Resolve current authority after binding the payload to an active run."""

    async with transaction_factory() as conn:
        queued_run = await run_loader(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
        )
    if queued_run is None or str(queued_run.get("status") or "") not in {
        "queued",
        "running",
    }:
        return None
    run_identity = _locked_run_identity(payload, queued_run)
    if _identity_mismatch_fields(payload, run_identity):
        return None
    try:
        return await principal_resolver(
            user_id=run_identity["user_id"],
            tenant_id=run_identity["tenant_id"],
        )
    except PrincipalAuthorityDenied:
        return None
