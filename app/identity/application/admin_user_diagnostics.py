from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol


ADMIN_USER_DIAGNOSTICS_SCHEMA_VERSION = "ai-platform.admin-user-diagnostics.v1"


class AdminUserDiagnosticsStore(Protocol):
    async def list_users(
        self,
        *,
        tenant_id: str,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Mapping[str, Any]], int]: ...

    async def get_diagnostics(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_limit: int,
        run_limit: int,
        audit_limit: int,
    ) -> Mapping[str, Any] | None: ...


def _safe_text(value: object, sanitizer: Callable[[object], str]) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    sanitized = sanitizer(value)
    return sanitized.strip() if isinstance(sanitized, str) else ""


def _count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _user_summary(
    row: Mapping[str, Any],
    *,
    sanitizer: Callable[[object], str],
) -> dict[str, Any]:
    return {
        "user_id": str(row.get("user_id") or ""),
        "display_name": _safe_text(row.get("display_name"), sanitizer),
        "status": str(row.get("status") or "unknown"),
        "created_at": row.get("created_at"),
        "session_count": _count(row.get("session_count")),
        "run_count": _count(row.get("run_count")),
        "queued_run_count": _count(row.get("queued_run_count")),
        "running_run_count": _count(row.get("running_run_count")),
        "succeeded_run_count": _count(row.get("succeeded_run_count")),
        "failed_run_count": _count(row.get("failed_run_count")),
        "cancelled_run_count": _count(row.get("cancelled_run_count")),
        "last_activity_at": row.get("last_activity_at"),
    }


class AdminUserDiagnosticsService:
    """Build a bounded, redacted administrator projection around one user."""

    def __init__(
        self,
        store: AdminUserDiagnosticsStore,
        *,
        sanitize_text: Callable[[object], str],
    ) -> None:
        self._store = store
        self._sanitize_text = sanitize_text

    async def list_users(
        self,
        *,
        tenant_id: str,
        search: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        rows, total = await self._store.list_users(
            tenant_id=tenant_id,
            search=search,
            offset=offset,
            limit=limit,
        )
        return {
            "schema_version": ADMIN_USER_DIAGNOSTICS_SCHEMA_VERSION,
            "users": [
                _user_summary(row, sanitizer=self._sanitize_text)
                for row in rows
            ],
            "total": _count(total),
            "offset": offset,
            "limit": limit,
        }

    async def get_diagnostics(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_limit: int,
        run_limit: int,
        audit_limit: int,
    ) -> dict[str, Any] | None:
        projection = await self._store.get_diagnostics(
            tenant_id=tenant_id,
            user_id=user_id,
            session_limit=session_limit,
            run_limit=run_limit,
            audit_limit=audit_limit,
        )
        if projection is None:
            return None

        sessions = []
        for row in projection.get("sessions", []):
            sessions.append(
                {
                    "session_id": str(row.get("session_id") or ""),
                    "workspace_id": str(row.get("workspace_id") or ""),
                    "agent_id": str(row.get("agent_id") or ""),
                    "status": str(row.get("status") or "unknown"),
                    "purpose": str(row.get("purpose") or "conversation"),
                    "run_count": _count(row.get("run_count")),
                    "failed_run_count": _count(row.get("failed_run_count")),
                    "last_run_at": row.get("last_run_at"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )

        runs = []
        for row in projection.get("runs", []):
            runs.append(
                {
                    "run_id": str(row.get("run_id") or ""),
                    "session_id": str(row.get("session_id") or ""),
                    "workspace_id": str(row.get("workspace_id") or ""),
                    "status": str(row.get("status") or "unknown"),
                    "agent_id": str(row.get("agent_id") or ""),
                    "execution_kind": str(row.get("execution_kind") or ""),
                    "skill_id": row.get("skill_id"),
                    "error_code": _safe_text(row.get("error_code"), self._sanitize_text) or None,
                    "error_message": _safe_text(row.get("error_message"), self._sanitize_text) or None,
                    "created_at": row.get("created_at"),
                    "queued_at": row.get("queued_at"),
                    "started_at": row.get("started_at"),
                    "finished_at": row.get("finished_at"),
                }
            )

        audit = []
        for row in projection.get("audit", []):
            relations: list[str] = []
            if row.get("actor_user_id") == user_id:
                relations.append("actor")
            if row.get("target_type") == "user" and row.get("target_id") == user_id:
                relations.append("subject")
            if row.get("run_id"):
                relations.append("run")
            if row.get("session_id"):
                relations.append("session")
            if not relations:
                continue
            audit.append(
                {
                    "audit_id": str(row.get("audit_id") or ""),
                    "actor_user_id": _safe_text(
                        row.get("actor_user_id"), self._sanitize_text
                    )
                    or None,
                    "action": _safe_text(row.get("action"), self._sanitize_text),
                    "target_type": str(row.get("target_type") or ""),
                    "target_id": _safe_text(
                        row.get("target_id"), self._sanitize_text
                    ),
                    "relations": relations,
                    "run_id": row.get("run_id"),
                    "session_id": row.get("session_id"),
                    "trace_id": _safe_text(
                        row.get("trace_id"), self._sanitize_text
                    )
                    or None,
                    "created_at": row.get("created_at"),
                }
            )

        return {
            "schema_version": ADMIN_USER_DIAGNOSTICS_SCHEMA_VERSION,
            "user": _user_summary(
                projection["user"],
                sanitizer=self._sanitize_text,
            ),
            "sessions": sessions,
            "runs": runs,
            "audit": audit,
            "limits": {
                "sessions": session_limit,
                "runs": run_limit,
                "audit": audit_limit,
            },
        }


__all__ = [
    "ADMIN_USER_DIAGNOSTICS_SCHEMA_VERSION",
    "AdminUserDiagnosticsService",
    "AdminUserDiagnosticsStore",
]
