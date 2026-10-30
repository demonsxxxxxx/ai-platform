"""Route adapters for authorized Session continuation projections."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

from app.auth import AuthPrincipal
from app.runs import api as runs_api


async def _load_continuation(
    loader: Callable[..., Any],
    principal: AuthPrincipal,
    conn: Any,
    session_id: str,
) -> Any:
    return await loader(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=session_id,
    )


def bind(get_session_continuation: Callable[..., Any], principal: AuthPrincipal):
    """Bind the route's replaceable Context API seam to one principal."""

    return partial(_load_continuation, get_session_continuation, principal)


def cursor_valid(run: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """Compare terminal history cursors through Runs-owned status normalization."""

    status = str(snapshot.get("status") or "")
    if not runs_api.is_terminal_run_status(status):
        return True
    return runs_api.normalize_run_status(
        str(run.get("status") or "")
    ) == runs_api.normalize_run_status(status)


def lambchat_status(status: str) -> str:
    status = runs_api.normalize_run_status(status)
    return {
        "succeeded": "completed",
        "failed": "error",
        "queued": "pending",
        "running": "running",
    }.get(status, status)
