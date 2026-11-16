"""PostgreSQL adapter for the Runs-owned private diagnostic record."""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any

from psycopg import AsyncConnection

from app.streaming.api import read_admin_detail_events
from app.files.api import read_admin_run_input_files

from app.runs.domain.cost_observability import projected_estimated_cost_minor
from app.runs.infrastructure.model_call_evidence_postgres import read_model_call_evidence
from app.runs.domain.model_call_evidence import project_model_call

from app.runs.infrastructure.admin_queries_postgres import get_admin_run_monitor_report
from app.runs.domain.run_performance import merge_browser_latency
from app.runs.domain.diagnostics import (
    RUN_DIAGNOSTICS_SCHEMA_VERSION,
    merge_run_diagnostics,
    serialize_run_diagnostics_payload,
)


class PostgresRunDiagnosticsRepository:
    def __init__(self, *, write_timeout_seconds: float = 10.0) -> None:
        self._write_timeout_ms = max(
            1,
            min(int(write_timeout_seconds * 1_000), 60_000),
        )

    async def append_browser_latency(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        measurement: dict[str, Any],
    ) -> dict[str, Any]:
        cursor = await conn.execute(
            """
            select 1 from run_events
            where tenant_id = %s and run_id = %s and visible_to_user is true
              and event_type in ('message.delta', 'message.part.delta')
              and jsonb_typeof(payload_json->'delta') = 'string'
              and payload_json->>'delta' ~ '[^[:space:]]'
            limit 1
            """, (tenant_id, run_id),
        )
        if await cursor.fetchone() is None:
            raise ValueError("browser_text_evidence_unavailable")
        return await self._capture(
            conn, tenant_id=tenant_id, run_id=run_id,
            observation=None, browser_latency=measurement,
        )

    async def append_observation(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._capture(
            conn, tenant_id=tenant_id, run_id=run_id,
            observation=observation,
        )

    async def _capture(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        observation: dict[str, Any] | None,
        browser_latency: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        savepoint = "run_diagnostics_capture"
        await conn.execute(f"savepoint {savepoint}")
        try:
            cursor = await conn.execute(
                "select current_setting('lock_timeout') as lock_timeout, "
                "current_setting('statement_timeout') as statement_timeout"
            )
            previous_timeouts = await cursor.fetchone()
            timeout = f"{self._write_timeout_ms}ms"
            await conn.execute(
                "select set_config('lock_timeout', %s, true), "
                "set_config('statement_timeout', %s, true)",
                (timeout, timeout),
            )
            result = await self._append_observation(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                observation=observation,
                browser_latency=browser_latency,
            )
            await conn.execute(
                "select set_config('lock_timeout', %s, true), "
                "set_config('statement_timeout', %s, true)",
                (
                    previous_timeouts["lock_timeout"],
                    previous_timeouts["statement_timeout"],
                ),
            )
        except BaseException:
            try:
                await conn.execute(f"rollback to savepoint {savepoint}")
            finally:
                await conn.execute(f"release savepoint {savepoint}")
            raise
        await conn.execute(f"release savepoint {savepoint}")
        return result

    async def _append_observation(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
        observation: dict[str, Any] | None,
        browser_latency: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merge = merge_browser_latency if browser_latency is not None else merge_run_diagnostics
        value = browser_latency if browser_latency is not None else observation
        payload, _ = merge(None, value)
        diagnostic_id = _diagnostic_id(tenant_id=tenant_id, run_id=run_id)
        cursor = await conn.execute(
            """
            insert into run_diagnostics(
              diagnostic_id, tenant_id, run_id, schema_version, revision, payload_json
            ) values (%s, %s, %s, %s, 1, %s::jsonb)
            on conflict (tenant_id, run_id) do nothing
            returning diagnostic_id, schema_version, revision, payload_json,
                      created_at, updated_at
            """,
            (
                diagnostic_id,
                tenant_id,
                run_id,
                RUN_DIAGNOSTICS_SCHEMA_VERSION,
                serialize_run_diagnostics_payload(payload),
            ),
        )
        if row := await cursor.fetchone():
            return dict(row)
        cursor = await conn.execute(
            """
            select diagnostic_id, schema_version, revision, payload_json,
                   created_at, updated_at
            from run_diagnostics
            where tenant_id = %s and run_id = %s
            for update
            """,
            (tenant_id, run_id),
        )
        current = await cursor.fetchone()
        if current is None:
            raise RuntimeError("run_diagnostics_write_conflict")
        if current.get("schema_version") != RUN_DIAGNOSTICS_SCHEMA_VERSION:
            raise RuntimeError("run_diagnostics_schema_unsupported")
        payload, changed = merge(current.get("payload_json"), value)
        if not changed:
            return dict(current)
        cursor = await conn.execute(
            """
            update run_diagnostics
            set payload_json = %s::jsonb, revision = revision + 1, updated_at = now()
            where tenant_id = %s and run_id = %s
            returning diagnostic_id, schema_version, revision, payload_json,
                      created_at, updated_at
            """,
            (serialize_run_diagnostics_payload(payload), tenant_id, run_id),
        )
        updated = await cursor.fetchone()
        if updated is None:
            raise RuntimeError("run_diagnostics_update_conflict")
        return dict(updated)

    async def get_admin_snapshot(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select r.id as run_id, r.session_id, r.user_id, r.workspace_id,
                   r.status, r.trace_id, r.created_at, r.queued_at, r.started_at,
                   r.finished_at, r.error_code, r.result_json, r.agent_id,
                   r.execution_kind, r.model_id, r.model_value,
                   r.model_gateway_revision, r.admitted_agent_profile_revision,
                   r.admitted_agent_profile_hash, r.auto_compact_window, r.max_output_tokens,
                   r.input_token_count, r.output_token_count, r.total_token_count,
                   r.estimated_cost_minor, r.copied_from_run_id,
                   d.diagnostic_id, d.schema_version as diagnostic_schema_version,
                   d.revision, d.payload_json, d.created_at as diagnostic_created_at,
                   d.updated_at as diagnostic_updated_at
            from runs r
            left join run_diagnostics d
              on d.tenant_id = r.tenant_id and d.run_id = r.id
            where r.tenant_id = %s and r.id = %s
            """,
            (tenant_id, run_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        attempt_cursor = await conn.execute(
            """
            select id as attempt_id, ordinal, status, owner_kind, started_at,
                   finished_at, terminal_reason, error_code
            from run_attempts
            where tenant_id = %s and run_id = %s
            order by ordinal asc
            """,
            (tenant_id, run_id),
        )
        attempts = [dict(item) for item in await attempt_cursor.fetchall()]
        diagnostic = None
        if row.get("diagnostic_id"):
            diagnostic = {
                "diagnostic_id": row["diagnostic_id"],
                "schema_version": row["diagnostic_schema_version"],
                "revision": row["revision"],
                "payload_json": row["payload_json"],
                "created_at": row["diagnostic_created_at"],
                "updated_at": row["diagnostic_updated_at"],
            }
        return {
            "run": {
                key: row.get(key)
                for key in (
                    "run_id",
                    "session_id",
                    "user_id",
                    "workspace_id",
                    "status",
                    "trace_id",
                    "created_at",
                    "queued_at",
                    "started_at",
                    "finished_at",
                    "error_code",
                    "agent_id",
                    "execution_kind",
                    "model_id",
                    "model_value",
                    "model_gateway_revision",
                    "admitted_agent_profile_revision",
                    "admitted_agent_profile_hash", "auto_compact_window", "max_output_tokens", "copied_from_run_id",
                )
            },
            "result_json": row.get("result_json"),
            "diagnostic": diagnostic,
            "attempts": attempts,
            "usage": {
                "input_tokens": row.get("input_token_count"),
                "output_tokens": row.get("output_token_count"),
                "total_tokens": row.get("total_token_count"),
                "estimated_cost_minor": projected_estimated_cost_minor(row.get("estimated_cost_minor"), row.get("result_json")),
            },
        }

    async def get_admin_monitor_metadata(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        run_ids: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        cursor = await conn.execute(
            """
            select
              runs.id as run_id,
              sessions.title as session_title,
              left(runs.input_json->>'message', 240) as task_summary,
              users.display_name as user_display_name,
              workspaces.name as workspace_name,
              coalesce(nullif(admitted_profile.name, ''), nullif(agents.name, ''))
                as agent_name,
              skills.name as skill_name,
              runs.latency_ms,
              runs.input_token_count,
              runs.output_token_count,
              runs.total_token_count,
              case
                when runs.result_json #>> '{runtime_private_payload,sdk_usage,cost_coverage}' in ('partial', 'unavailable') then null
                when runs.result_json #>> '{runtime_private_payload,sdk_usage,cost_coverage}' = 'complete' then
                  case when jsonb_typeof(runs.result_json #> '{cost,estimated_cost_minor}') = 'number'
                    and runs.result_json #>> '{cost,estimated_cost_minor}' ~ '^[0-9]{1,10}$'
                  then case when (runs.result_json #>> '{cost,estimated_cost_minor}')::numeric <= 2147483647
                    then (runs.result_json #>> '{cost,estimated_cost_minor}')::integer else null end
                  else null end
                else runs.estimated_cost_minor
              end as estimated_cost_minor,
              runs.model_value,
              runs.copied_from_run_id,
              runs.trace_id is not null as trace_id_recorded
            from runs
            left join sessions
              on sessions.tenant_id = runs.tenant_id
             and sessions.id = runs.session_id
             and sessions.workspace_id = runs.workspace_id
             and sessions.user_id is not distinct from runs.user_id
             and sessions.agent_id = runs.agent_id
            left join users
              on users.tenant_id = runs.tenant_id
             and users.id = runs.user_id
            left join workspaces
              on workspaces.tenant_id = runs.tenant_id
             and workspaces.id = runs.workspace_id
            left join agents
              on agents.tenant_id = runs.tenant_id
             and agents.id = runs.agent_id
            left join agent_profile_revisions admitted_profile
              on admitted_profile.tenant_id = runs.tenant_id
             and admitted_profile.agent_id = runs.agent_id
             and admitted_profile.revision = runs.admitted_agent_profile_revision
            left join skills
              on skills.id = runs.skill_id
            where runs.tenant_id = %s and runs.id = any(%s::text[])
            """,
            (tenant_id, list(run_ids)),
        )
        rows = await cursor.fetchall()
        return {
            str(row["run_id"]): {
                key: value for key, value in dict(row).items() if key != "run_id"
            }
            for row in rows
        }

    async def get_admin_monitor_report(
        self, conn: AsyncConnection, *, tenant_id: str, from_at: datetime, to_at: datetime,
        user_id: str | None = None, agent_id: str | None = None,
        model_id: str | None = None, model_gateway_revision: int | None = None,
        status: str | None = None,
        order: str = "slow", limit: int = 50,
    ) -> dict[str, Any]:
        return await get_admin_run_monitor_report(
            conn, tenant_id=tenant_id, from_at=from_at, to_at=to_at,
            user_id=user_id, agent_id=agent_id, model_id=model_id,
            model_gateway_revision=model_gateway_revision, status=status,
            order=order, limit=limit,
        )

    async def get_execution_debug_binding(self, conn: AsyncConnection, *, run_id: str, attempt_id: str) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """select r.tenant_id, lease.id as lease_id
               from runs r join run_attempts a on a.tenant_id=r.tenant_id and a.run_id=r.id
               join sandbox_leases lease on lease.tenant_id=r.tenant_id and lease.run_id=r.id and lease.attempt_id=a.id
               where r.id=%s and a.id=%s and r.status in ('queued','running') and a.status in ('running','cancel_requested')
                 and lease.lease_payload_json->>'attempt_id'=a.id
                 and lease.lease_payload_json->>'owner_generation'=a.owner_generation::text
                 and lease.status='active' and lease.released_at is null and lease.expires_at>now()
               order by lease.created_at desc limit 1""", (run_id, attempt_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_admin_evidence_page(self, conn: AsyncConnection, *, tenant_id: str, run_id: str, after_sequence: int, limit: int) -> list[dict[str, Any]]:
        cursor = await conn.execute("""
            with page as (
              select id, sequence, event_type, stage, visible_to_user, created_at, payload_json,
                case when visible_to_user then octet_length(payload_json::text) else 0 end as payload_bytes
              from run_events where tenant_id = %s and run_id = %s and sequence > %s
              order by sequence asc limit %s
            ), budgeted as (
              select page.*, sum(case when payload_bytes <= 65536 then payload_bytes else 0 end)
                over (order by sequence) as retained_bytes from page
            )
            select id, sequence, event_type, stage, visible_to_user, created_at,
              case when visible_to_user and payload_bytes <= 65536 and retained_bytes <= 524288
                then payload_json else '{}'::jsonb end as payload_json,
              (visible_to_user and (payload_bytes > 65536 or retained_bytes > 524288)) as payload_omitted
            from budgeted order by sequence
            """, (tenant_id, run_id, after_sequence, limit))
        return list(await cursor.fetchall())

    async def get_admin_inspection_facts(self, conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        cursor = await conn.execute(
            """select r.input_json, (select e.payload_json from run_events e
               where e.tenant_id=r.tenant_id and e.run_id=r.id and e.visible_to_user is false
                 and e.event_type='run_trigger_recorded' order by e.sequence limit 1) as trusted_trigger
               from runs r where r.tenant_id = %s and r.id = %s""",
            (tenant_id, run_id),
        )
        row = await cursor.fetchone() or {}
        related = await conn.execute(
            """
            select source.id as source_run_id, child.id as child_run_id, child.status,
                   child.created_at, child.user_id, coalesce(operation.payload_json->>'action', case when exists (select 1 from run_events t where t.tenant_id=child.tenant_id and t.run_id=child.id and t.event_type='run_trigger_recorded' and t.visible_to_user is false and t.payload_json->>'source'='copy') then 'copy' end) as action
            from runs current
            join runs child on child.tenant_id = current.tenant_id
              and (child.copied_from_run_id = current.id or child.id = current.id)
            join runs source on source.tenant_id = child.tenant_id and source.id = child.copied_from_run_id
              and source.workspace_id = child.workspace_id and source.user_id is not distinct from child.user_id
            left join lateral (
              select e.payload_json from run_events e
              where e.tenant_id = source.tenant_id and e.run_id = source.id
                and e.event_type = 'run_control_operation_committed'
                and e.payload_json->>'source_run_id' = source.id
                and e.payload_json->>'child_run_id' = child.id
                and e.payload_json->>'action' in ('retry', 'resume')
              order by e.sequence desc limit 1
            ) operation on true
            where current.tenant_id = %s and current.id = %s
              and current.workspace_id = child.workspace_id
              and current.user_id is not distinct from child.user_id
            order by child.created_at desc, child.id desc limit 51
            """, (tenant_id, run_id),
        )
        artifacts = await conn.execute("select id as artifact_id, label, content_type, size_bytes from artifacts where tenant_id = %s and run_id = %s order by created_at, id limit 101", (tenant_id, run_id))
        calls = await read_model_call_evidence(conn, tenant_id=tenant_id, run_id=run_id)
        observed = {}
        for entry in calls[:200]:
            payload = entry.get("payload_json") or {}
            safe = project_model_call(payload)
            if safe and safe["call_id"] not in observed:
                observed[safe["call_id"]] = {**safe, "attempt_id": payload.get("attempt_id")}
        attachments = await read_admin_run_input_files(conn, tenant_id=tenant_id, run_id=run_id)
        safe_input = dict(row["input_json"]) if isinstance(row.get("input_json"), dict) else {}
        safe_input.pop("_trusted_run_source", None)
        if row.get("trusted_trigger"):
            safe_input["_trusted_run_source"] = row["trusted_trigger"]
        return {"input": safe_input, "attachments": attachments, "relationships": list(await related.fetchall()),
                "artifacts": list(await artifacts.fetchall()), "model_calls": list(reversed(observed.values())),
                "model_calls_truncated": len(calls) > 200}

    async def get_admin_public_result(self, conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        from app.runs.application.admin_public_result import build_admin_public_result
        evidence = await read_admin_detail_events(conn, tenant_id=tenant_id, run_id=run_id)
        return build_admin_public_result(evidence, tenant_id=tenant_id, run_id=run_id)

    async def get_admin_debug_skills(self, conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        cursor = await conn.execute(
            """select skill_id, skill_version, content_hash, dependency_ids
               from run_skill_snapshots where tenant_id = %s and run_id = %s
               order by skill_id limit 101""", (tenant_id, run_id),
        )
        return list(await cursor.fetchall())

    async def get_admin_investigation_events(
        self, conn: AsyncConnection, *, tenant_id: str, run_id: str,
    ) -> dict[str, Any]:
        cursor = await conn.execute(
            """
            select id, id as event_id, event_type, event_type as type, stage, sequence, created_at,
                   visible_to_user, payload_json, payload_json as payload
            from run_events
            where tenant_id = %s and run_id = %s and visible_to_user is true
              and event_type in ('tool.started', 'tool.completed', 'tool.failed', 'tool.denied', 'model.completed', 'agent.progress', 'run.failed', 'message.completed', 'subagent.started', 'subagent.progress', 'subagent.completed', 'subagent.failed', 'subagent.cancelled')
            order by sequence desc limit 501
            """, (tenant_id, run_id),
        )
        rows = list(await cursor.fetchall())
        text_cursor = await conn.execute(
            """
            select min(e.created_at) as first_public_text_at
            from run_events e join runs r on r.tenant_id = e.tenant_id and r.id = e.run_id
            where e.tenant_id = %s and e.run_id = %s and e.visible_to_user is true
              and e.event_type in ('message.delta', 'message.part.delta')
              and jsonb_typeof(e.payload_json->'delta') = 'string'
              and e.payload_json->>'delta' ~ '[^[:space:]]'
              and e.created_at >= r.created_at and e.created_at <= now()
              and (r.finished_at is null or e.created_at <= r.finished_at)
            """, (tenant_id, run_id),
        )
        text_row = await text_cursor.fetchone() or {}
        return {
            "events": list(reversed(rows[:500])),
            "events_truncated": len(rows) > 500,
            "first_public_text_at": text_row.get("first_public_text_at"),
        }


def _diagnostic_id(*, tenant_id: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\0{run_id}".encode()).hexdigest()
    return f"rdiag_{digest[:32]}"
