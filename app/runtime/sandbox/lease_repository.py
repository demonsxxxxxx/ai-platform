"""Persistence operations for real-sandbox lease creation and release fencing."""

from __future__ import annotations

import json
import uuid
from typing import Any


class SandboxLeaseReleaseScopeMismatchError(RuntimeError):
    """Raised when a release fence collides with another lease scope."""


def new_lease_id() -> str:
    return f"lease_{uuid.uuid4().hex}"


async def create_sandbox_lease(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    attempt_id: str | None = None,
    trace_id: str,
    sandbox_mode: str,
    provider: str,
    browser_enabled: bool,
    ttl_seconds: int,
    resource_limits_json: dict[str, Any],
    user_visible_payload_json: dict[str, Any],
    lease_payload_json: dict[str, Any],
    runtime_container_id: str | None = None,
    runtime_container_name: str | None = None,
    runtime_executor_url: str | None = None,
    runtime_workspace_container_path: str | None = None,
    lease_id: str | None = None,
) -> dict[str, Any]:
    lease_id = lease_id or new_lease_id()
    if provider == "fake" and not runtime_container_id:
        runtime_container_id = f"exec-{run_id}"
        runtime_container_name = f"executor-{runtime_container_id}"
        runtime_executor_url = "http://sandbox-runtime.invalid"
        runtime_workspace_container_path = "/workspace"
    runtime_handle_verified = all(
        (
            runtime_container_id,
            runtime_container_name,
            runtime_executor_url,
            runtime_workspace_container_path,
        )
    )
    if (attempt_id and lease_payload_json.get("attempt_id") != attempt_id) or (
        provider in {"docker", "opensandbox"}
        and (not attempt_id or not runtime_handle_verified)
    ):
        raise ValueError("sandbox_runtime_handle_required")
    cursor = await connection.execute(
        """
        insert into sandbox_leases(
          id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id, trace_id,
          sandbox_mode, provider, browser_enabled, resource_limits_json,
          user_visible_payload_json, lease_payload_json,
          runtime_container_id, runtime_container_name, runtime_executor_url,
          runtime_workspace_container_path, runtime_handle_verified_at,
          heartbeat_at, expires_at
        )
        values (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s,
          case when %s then now() else null end,
          now(), now() + (%s * interval '1 second')
        )
        returning *
        """,
        (
            lease_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            attempt_id,
            trace_id,
            sandbox_mode,
            provider,
            browser_enabled,
            json.dumps(resource_limits_json, ensure_ascii=False),
            json.dumps(user_visible_payload_json, ensure_ascii=False),
            json.dumps(lease_payload_json, ensure_ascii=False),
            runtime_container_id,
            runtime_container_name,
            runtime_executor_url,
            runtime_workspace_container_path,
            runtime_handle_verified,
            int(ttl_seconds),
        ),
    )
    row = await cursor.fetchone()
    return row or {
        "id": lease_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "trace_id": trace_id,
        "sandbox_mode": sandbox_mode,
        "provider": provider,
        "status": "active",
        "browser_enabled": browser_enabled,
        "resource_limits_json": resource_limits_json,
        "user_visible_payload_json": user_visible_payload_json,
        "lease_payload_json": lease_payload_json,
        "runtime_container_id": runtime_container_id,
        "runtime_container_name": runtime_container_name,
        "runtime_executor_url": runtime_executor_url,
        "runtime_workspace_container_path": runtime_workspace_container_path,
        "runtime_handle_verified_at": (
            "platform-verified" if runtime_handle_verified else None
        ),
    }


async def fence_sandbox_lease_release(
    connection: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    attempt_id: str | None,
    lease_id: str,
    sandbox_mode: str,
    provider: str,
    browser_enabled: bool,
    reason: str,
) -> dict[str, Any]:
    """Persist a monotonic release fence after an uncertain lease insertion."""

    cursor = await connection.execute(
        """
        insert into sandbox_leases(
          id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
          sandbox_mode, provider, status, browser_enabled, lease_payload_json,
          heartbeat_at, expires_at, released_at, release_reason
        )
        values (
          %s, %s, %s, %s, %s, %s, %s,
          %s, %s, 'released', %s,
          '{"source":"sandbox_runtime_release_fence"}'::jsonb,
          now(), now(), now(), %s
        )
        on conflict (id) do update
        set status = 'released',
            released_at = coalesce(sandbox_leases.released_at, now()),
            release_reason = excluded.release_reason,
            updated_at = now()
        where sandbox_leases.tenant_id = excluded.tenant_id
          and sandbox_leases.workspace_id = excluded.workspace_id
          and sandbox_leases.user_id = excluded.user_id
          and sandbox_leases.session_id = excluded.session_id
          and sandbox_leases.run_id = excluded.run_id
          and sandbox_leases.sandbox_mode = excluded.sandbox_mode
          and sandbox_leases.provider = excluded.provider
          and coalesce(sandbox_leases.attempt_id, '') = coalesce(excluded.attempt_id, '')
        returning *
        """,
        (
            lease_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            attempt_id,
            sandbox_mode,
            provider,
            browser_enabled,
            reason,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SandboxLeaseReleaseScopeMismatchError(
            "sandbox_lease_release_scope_mismatch"
        )
    return row
