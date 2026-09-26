"""Admin queries persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.artifacts.infrastructure.records_postgres import list_run_artifacts
from app.control_plane_contracts import ARTIFACT_MANIFEST_SCHEMA_VERSION
from app.control_plane_contracts import AUDIT_EVENT_SCHEMA_VERSION
from app.streaming.events import EVENT_ENVELOPE_SCHEMA_VERSION
from app.control_plane_contracts import EXECUTOR_RESULT_SCHEMA_VERSION
from app.control_plane_contracts import RUN_CONTRACT_VERSION
from app.control_plane_contracts import RUN_EXECUTION_KIND_SKILL
from app.control_plane_contracts import artifact_manifest_contract
from app.control_plane_contracts import standard_trace_id
from app.error_taxonomy import summarize_error_categories
from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.values import _coerce_int
from app.platform.public_payload import sanitize_public_payload
from app.platform.public_payload import sanitize_public_text
from app.runs.infrastructure.creation_postgres import ACTIVE_RUN_STATUSES
from app.runs.infrastructure.postgres import get_run
from app.runs.infrastructure.steps_postgres import list_run_steps
from app.sandbox.infrastructure.leases_postgres import list_sandbox_leases_for_run
from app.skills.infrastructure.run_snapshots_postgres import _attach_skill_usage
from app.skills.infrastructure.run_snapshots_postgres import _sanitize_skill_snapshot
from app.skills.infrastructure.run_snapshots_postgres import list_run_skill_snapshots
from app.streaming.infrastructure.run_events_postgres import list_run_events
from psycopg import AsyncConnection
from typing import Any
import app.runs.domain.terminalization as runs_api


def _required_schema_version(row: dict[str, Any], field: str, expected: str, error_code: str) -> str:
    value = row.get(field)
    if value != expected:
        raise RepositoryConflictError(error_code)
    return str(value)


async def list_admin_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id as run_id,
          session_id,
          user_id,
          workspace_id,
          status,
          agent_id,
          execution_kind,
          skill_id,
          created_at,
          queued_at,
          started_at,
          finished_at,
          cancel_requested_at,
          cancel_requested_by,
          error_code,
          error_message
        from runs
        where tenant_id = %s
          and (%s::text is null or user_id = %s)
          and (%s::text is null or status = %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, user_id, user_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def get_admin_runtime_run_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Return same-tenant run status and redacted recent failure aggregates for Admin Runtime."""
    status_cursor = await conn.execute(
        """
        select status, count(*) as count
        from runs
        where tenant_id = %s
        group by status
        """,
        (tenant_id,),
    )
    status_rows = list(await status_cursor.fetchall())
    by_status = {
        str(row["status"]): _coerce_int(row["count"])
        for row in status_rows
        if row.get("status") is not None
    }
    failure_cursor = await conn.execute(
        """
        select id, user_id, agent_id, error_code, error_message, created_at
        from runs
        where tenant_id = %s
          and status = 'failed'
        order by created_at desc
        limit %s
        """,
        (tenant_id, limit),
    )
    failure_rows = list(await failure_cursor.fetchall())
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "active": sum(by_status.get(status, 0) for status in ACTIVE_RUN_STATUSES),
        "terminal": sum(by_status.get(status, 0) for status in runs_api.TERMINAL_RUN_STATUSES),
        "recent_failures": [
            {
                "run_id": row["id"],
                "user_id": row.get("user_id"),
                "agent_id": row.get("agent_id"),
                "error_code": sanitize_public_text(row.get("error_code")) or None,
                "error_message": sanitize_public_text(row.get("error_message")),
                "created_at": row.get("created_at"),
            }
            for row in failure_rows
        ],
    }


async def get_admin_runtime_admission_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int,
    top_user_limit: int = 10,
) -> dict[str, Any]:
    """Return same-tenant active-run admission pressure for Admin Runtime."""
    active_limit = max(int(limit), 0)
    top_limit = max(min(int(top_user_limit), 50), 1)
    totals_cursor = await conn.execute(
        """
        with grouped as (
          select user_id, count(*) as active
          from runs
          where tenant_id = %s
            and status in ('queued', 'running')
          group by user_id
        )
        select
          coalesce(sum(active), 0) as active_runs,
          count(*) filter (where user_id is not null) as active_users,
          count(*) filter (where user_id is not null and %s > 0 and active >= %s) as saturated_users
        from grouped
        """,
        (tenant_id, active_limit, active_limit),
    )
    totals = await totals_cursor.fetchone() or {}
    top_cursor = await conn.execute(
        """
        select user_id, count(*) as active
        from runs
        where tenant_id = %s
          and status in ('queued', 'running')
          and user_id is not null
        group by user_id
        order by count(*) desc, user_id asc
        limit %s
        """,
        (tenant_id, top_limit),
    )
    top_rows = list(await top_cursor.fetchall())
    top_users = [
        {
            "user_id": str(row["user_id"]),
            "active": _coerce_int(row["active"]),
            "saturated": active_limit > 0 and _coerce_int(row["active"]) >= active_limit,
        }
        for row in top_rows
        if row.get("user_id")
    ]
    return {
        "policy_active": active_limit > 0,
        "max_active_runs_per_user": active_limit,
        "active_runs": _coerce_int(totals.get("active_runs")),
        "active_users": _coerce_int(totals.get("active_users")),
        "saturated_users": _coerce_int(totals.get("saturated_users")),
        "top_users": top_users,
    }


async def get_admin_runtime_observability_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Return same-tenant observability seed metrics for the Admin Runtime overview."""
    cursor = await conn.execute(
        """
        with event_summary as (
          select
            count(*) as event_count,
            count(*) filter (where error_code is not null and error_code <> '') as event_error_count,
            avg(latency_ms) filter (where latency_ms is not null) as avg_latency_ms,
            max(latency_ms) as max_latency_ms,
            percentile_cont(0.5) within group (order by latency_ms)
              filter (where latency_ms is not null) as p50_latency_ms,
            percentile_cont(0.95) within group (order by latency_ms)
              filter (where latency_ms is not null) as p95_latency_ms,
            percentile_cont(0.99) within group (order by latency_ms)
              filter (where latency_ms is not null) as p99_latency_ms
          from run_events
          where tenant_id = %s
        ),
        artifact_summary as (
          select count(*) as artifact_count
          from artifacts
          where tenant_id = %s
        ),
        run_totals as (
          select
            count(*) filter (where error_code is not null and error_code <> '') as run_error_count,
            coalesce(sum(input_token_count), 0) as run_input_token_count,
            coalesce(sum(output_token_count), 0) as run_output_token_count,
            coalesce(sum(total_token_count), 0) as run_total_token_count,
            coalesce(sum(estimated_cost_minor), 0) as run_estimated_cost_minor
          from runs
          where tenant_id = %s
        ),
        error_types as (
          select coalesce(jsonb_object_agg(error_code, error_count), '{}'::jsonb) as error_types
          from (
            select error_code, count(*) as error_count
            from (
              select error_code
              from runs
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
              union all
              select error_code
              from run_events
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
            ) all_errors
            group by error_code
          ) errors
        )
        select
          event_summary.event_count,
          artifact_summary.artifact_count,
          run_totals.run_error_count + event_summary.event_error_count as error_count,
          error_types.error_types,
          event_summary.avg_latency_ms,
          event_summary.max_latency_ms,
          event_summary.p50_latency_ms,
          event_summary.p95_latency_ms,
          event_summary.p99_latency_ms,
          run_totals.run_input_token_count as input_token_count,
          run_totals.run_output_token_count as output_token_count,
          run_totals.run_total_token_count as total_token_count,
          run_totals.run_estimated_cost_minor as estimated_cost_minor
        from event_summary, artifact_summary, run_totals, error_types
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
    )
    row = await cursor.fetchone() or {}
    raw_error_types = row.get("error_types") if isinstance(row.get("error_types"), dict) else {}
    error_types = {
        sanitized_key: _coerce_int(value)
        for key, value in raw_error_types.items()
        if (sanitized_key := sanitize_public_text(key))
    }
    avg_latency = row.get("avg_latency_ms")
    max_latency = row.get("max_latency_ms")
    p50_latency = row.get("p50_latency_ms")
    p95_latency = row.get("p95_latency_ms")
    p99_latency = row.get("p99_latency_ms")
    return {
        "event_count": _coerce_int(row.get("event_count")),
        "artifact_count": _coerce_int(row.get("artifact_count")),
        "error_count": _coerce_int(row.get("error_count")),
        "error_types": error_types,
        "error_categories": summarize_error_categories(error_types),
        "latency_ms": {
            "avg": _coerce_int(avg_latency) if avg_latency is not None else None,
            "max": _coerce_int(max_latency) if max_latency is not None else None,
            "p50": _coerce_int(p50_latency) if p50_latency is not None else None,
            "p95": _coerce_int(p95_latency) if p95_latency is not None else None,
            "p99": _coerce_int(p99_latency) if p99_latency is not None else None,
        },
        "token_counts": {
            "input": _coerce_int(row.get("input_token_count")),
            "output": _coerce_int(row.get("output_token_count")),
            "total": _coerce_int(row.get("total_token_count")),
        },
        "estimated_cost_minor": _coerce_int(row.get("estimated_cost_minor")),
    }


_SANDBOX_LEASE_RUNTIME_HANDLE_PROJECTION_KEYS = {
    "container_id",
    "container_name",
    "executor_url",
    "workspace_host_path",
    "workspace_container_path",
    "labels",
    "runtime_container_id",
    "runtime_container_name",
    "runtime_executor_url",
    "runtime_workspace_container_path",
    "runtime_handle_verified_at",
}


def _sandbox_lease_payload_projection(row: dict[str, Any]) -> dict[str, Any]:
    payload = sanitize_public_payload(
        row.get("lease_payload_json") if isinstance(row.get("lease_payload_json"), dict) else {}
    )
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if str(key) not in _SANDBOX_LEASE_RUNTIME_HANDLE_PROJECTION_KEYS
    }


def _sandbox_lease_admin_projection(row: dict[str, Any]) -> dict[str, Any]:
    resource_limits = sanitize_public_payload(
        row.get("resource_limits_json") if isinstance(row.get("resource_limits_json"), dict) else {}
    )
    user_visible_payload = sanitize_public_payload(
        row.get("user_visible_payload_json") if isinstance(row.get("user_visible_payload_json"), dict) else {}
    )
    lease_payload = _sandbox_lease_payload_projection(row)
    return {
        "lease_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "run_id": str(row["run_id"]),
        "trace_id": str(row.get("trace_id") or standard_trace_id(str(row["run_id"]))),
        "sandbox_mode": str(row["sandbox_mode"]),
        "provider": str(row.get("provider") or "fake"),
        "status": str(row.get("status") or "active"),
        "browser_enabled": bool(row.get("browser_enabled")),
        "resource_limits": resource_limits if isinstance(resource_limits, dict) else {},
        "workspace": user_visible_payload if isinstance(user_visible_payload, dict) else {},
        "lease_payload": lease_payload,
        "heartbeat_at": row.get("heartbeat_at"),
        "expires_at": row.get("expires_at"),
        "released_at": row.get("released_at"),
        "release_reason": str(row.get("release_reason") or ""),
        "created_at": row.get("created_at"),
    }


async def get_admin_run_detail(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    run = await get_run(conn, tenant_id=tenant_id, run_id=run_id)
    if run is None:
        return None
    run_contract_version = _required_schema_version(run, "schema_version", RUN_CONTRACT_VERSION, "invalid_run_contract")
    executor_schema_version = _required_schema_version(
        run,
        "executor_schema_version",
        EXECUTOR_RESULT_SCHEMA_VERSION,
        "invalid_executor_result_schema_version",
    )
    events = await list_run_events(conn, tenant_id=tenant_id, run_id=run_id)
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    artifacts = await list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id)
    sandbox_leases = await list_sandbox_leases_for_run(conn, tenant_id=tenant_id, run_id=run_id)
    skill_snapshots = _attach_skill_usage(
        await list_run_skill_snapshots(conn, tenant_id=tenant_id, run_id=run_id),
        events,
    )
    skill_snapshots = [_sanitize_skill_snapshot(snapshot) for snapshot in skill_snapshots]
    cursor = await conn.execute(
        """
        select id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json, created_at
        from audit_logs
        where tenant_id = %s
          and (
            target_id = %s
            or payload_json->>'run_id' = %s
          )
        order by created_at asc
        """,
        (tenant_id, run_id, run_id),
    )
    audit_rows = list(await cursor.fetchall())
    run_input = sanitize_public_payload(run["input_json"] if isinstance(run.get("input_json"), dict) else {})
    if not isinstance(run_input, dict):
        run_input = {}
    run_result = sanitize_public_payload(run["result_json"] if isinstance(run.get("result_json"), dict) else {})
    if not isinstance(run_result, dict):
        run_result = {}
    return {
        "run": {
            "run_id": run["id"],
            "session_id": run["session_id"],
            "user_id": run["user_id"],
            "workspace_id": run["workspace_id"],
            "status": run["status"],
            "agent_id": run["agent_id"],
            "execution_kind": run.get("execution_kind") or RUN_EXECUTION_KIND_SKILL,
            "skill_id": run["skill_id"],
            "created_at": run["created_at"],
            "queued_at": run.get("queued_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "cancel_requested_at": run.get("cancel_requested_at"),
            "cancel_requested_by": run.get("cancel_requested_by"),
            "input": run_input,
            "result": run_result,
            "error_code": sanitize_public_text(run.get("error_code")) or None,
            "error_message": sanitize_public_text(run.get("error_message")),
            "trace_id": run.get("trace_id") or standard_trace_id(str(run["id"])),
            "contract_version": run_contract_version,
            "executor_schema_version": executor_schema_version,
        },
        "events": [
            {
                "event_id": item["id"],
                "schema_version": _required_schema_version(
                    item,
                    "schema_version",
                    EVENT_ENVELOPE_SCHEMA_VERSION,
                    "invalid_event_schema_version",
                ),
                "sequence": int(item.get("sequence") or 0),
                "trace_id": item.get("trace_id") or standard_trace_id(str(run["id"])),
                "type": item["event_type"],
                "stage": item["stage"],
                "message": sanitize_public_text(item.get("message")),
                "severity": item.get("severity") or "info",
                "visible_to_user": bool(item.get("visible_to_user", True)),
                "error_code": sanitize_public_text(item.get("error_code")) or None,
                "latency_ms": item.get("latency_ms"),
                "token_counts": {
                    "input": int(item.get("input_token_count") or 0),
                    "output": int(item.get("output_token_count") or 0),
                    "total": int(item.get("total_token_count") or 0),
                },
                "cost": {"estimated_cost_minor": int(item.get("estimated_cost_minor") or 0)},
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "created_at": item["created_at"],
            }
            for item in events
        ],
        "steps": [
            {
                "step_id": item["id"],
                "run_id": item["run_id"],
                "step_key": item["step_key"],
                "step_kind": item["step_kind"],
                "status": item["status"],
                "title": sanitize_public_text(item.get("title")),
                "role": sanitize_public_text(item.get("role")) if item.get("role") is not None else None,
                "sequence": item["sequence"],
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "started_at": item["started_at"],
                "finished_at": item["finished_at"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            for item in steps
        ],
        "artifacts": [
            {
                "artifact_id": item["id"],
                "trace_id": item.get("trace_id") or standard_trace_id(str(run["id"])),
                "artifact_type": item["artifact_type"],
                "label": sanitize_public_text(item.get("label")) or str(item["artifact_type"]),
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "manifest": artifact_manifest_contract(
                    artifact_type=str(item["artifact_type"]),
                    manifest=item.get("manifest_json") if isinstance(item.get("manifest_json"), dict) else {},
                    schema_version=_required_schema_version(
                        item,
                        "manifest_version",
                        ARTIFACT_MANIFEST_SCHEMA_VERSION,
                        "invalid_artifact_manifest_schema_version",
                    ),
                ),
                "created_at": item["created_at"],
            }
            for item in artifacts
        ],
        "sandbox_leases": [_sandbox_lease_admin_projection(item) for item in sandbox_leases],
        "skill_snapshots": skill_snapshots,
        "audit": [
            {
                "audit_id": item["id"],
                "schema_version": _required_schema_version(
                    item,
                    "schema_version",
                    AUDIT_EVENT_SCHEMA_VERSION,
                    "invalid_audit_event_schema_version",
                ),
                "trace_id": item.get("trace_id"),
                "user_id": item["user_id"],
                "action": item["action"],
                "target_type": item["target_type"],
                "target_id": item["target_id"],
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "created_at": item["created_at"],
            }
            for item in audit_rows
        ],
    }
