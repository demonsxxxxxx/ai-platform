"""PostgreSQL actual-model ledger. The fenced callback owns the transaction."""
from __future__ import annotations

import json
from dataclasses import asdict
from app.runs.domain.model_token_usage import TOKEN_FIELDS, cumulative_deltas, token_totals

_SCOPE = "tenant_id=%s and run_id=%s and attempt_id=%s and scope_id=%s"


class PostgresModelTokenUsage:
    def __init__(self, *, provider_session):
        self.provider_session = provider_session

    async def record(self, conn, *, tenant_id, run_id, attempt_id, snapshot, receipt_id, recorded_at):
        key = (tenant_id, run_id, attempt_id, snapshot.scope_id)
        payload = asdict(snapshot)
        provider_id = await self.provider_session(conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id)
        if provider_id:
            await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))",
                (json.dumps([tenant_id, provider_id]),))
        await conn.execute("""insert into model_token_usage_scopes
            (tenant_id,run_id,attempt_id,scope_id,baseline,baseline_json,provider_session_id,coverage,created_at,updated_at)
            values (%s,%s,%s,%s,%s,%s::jsonb,%s,'unavailable',%s,%s) on conflict do nothing""",
            (*key, snapshot.baseline, json.dumps(payload["baseline_models"]), provider_id, recorded_at, recorded_at))
        state = await (await conn.execute(f"select * from model_token_usage_scopes where {_SCOPE} for update", key)).fetchone()
        old = await (await conn.execute(f"select payload_json from model_token_usage_observations where {_SCOPE} and observation_seq=%s",
            (*key, snapshot.observation_seq))).fetchone()
        if old is not None:
            if old["payload_json"] == payload:
                return "duplicate"
            await self._mark_partial(conn, key, "observation_conflict", recorded_at)
            await self._coverage_receipt(conn, key, receipt_id, recorded_at)
            return "partial"
        await conn.execute("""insert into model_token_usage_observations
            (tenant_id,run_id,attempt_id,scope_id,observation_seq,payload_json,recorded_at,source_receipt_id)
            values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            (*key, snapshot.observation_seq, json.dumps(payload), recorded_at, receipt_id))
        if snapshot.observation_seq <= state["latest_observation_seq"]:
            return "stale"
        reason = None
        if (snapshot.baseline != state["baseline"] or payload["baseline_models"] != state["baseline_json"]
            or (state["provider_session_id"] is not None and state["provider_session_id"] != provider_id)):
            reason = "scope_changed"
        if payload["baseline_models"] is None or snapshot.baseline == "unknown":
            reason = reason or "baseline_unknown"
        if not provider_id or (snapshot.provider_session_id is not None and snapshot.provider_session_id != provider_id):
            reason = reason or "provider_identity_unknown"
        deltas = cumulative_deltas(state["cumulative_json"] or {}, payload["models"])
        if deltas is None:
            reason = reason or "counter_regressed"
        # Already accepted ranges may settle after a successor scope has reported.
        if not reason and deltas:
            others = await (await conn.execute("""select baseline_json,cumulative_json from model_token_usage_scopes
                where tenant_id=%s and provider_session_id=%s
                  and not (run_id=%s and attempt_id=%s and scope_id=%s)
                  and cumulative_json <> '{}'::jsonb""", (tenant_id, provider_id, run_id, attempt_id, snapshot.scope_id))).fetchall()
            for other in others:
                absolute = {model: {f: counts[f] + (other["baseline_json"] or {}).get(model, {}).get(f,0)
                    for f in TOKEN_FIELDS} for model, counts in other["cumulative_json"].items()}
                if cumulative_deltas(absolute, payload["baseline_models"]) is None:
                    reason = "scope_overlap"
                    break
        if reason:
            await self._mark_partial(conn, key, reason, recorded_at)
            await conn.execute(f"update model_token_usage_scopes set latest_observation_seq=%s where {_SCOPE}",
                (snapshot.observation_seq, *key))
            await self._coverage_receipt(conn, key, receipt_id, recorded_at)
            return "partial"
        for model, counts in deltas.items():
            await conn.execute("""insert into model_token_usage_deltas
                (tenant_id,run_id,attempt_id,scope_id,observation_seq,actual_model,
                 input_tokens,cache_read_input_tokens,cache_creation_input_tokens,output_tokens,recorded_at,source_receipt_id)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (*key, snapshot.observation_seq, model, *(counts[f] for f in TOKEN_FIELDS), recorded_at, receipt_id))
        reason = state["coverage_reason"] or snapshot.reason
        coverage = "partial" if reason and (state["cumulative_json"] or payload["models"]) else snapshot.coverage
        await conn.execute(f"""update model_token_usage_scopes set latest_observation_seq=%s,cumulative_json=%s::jsonb,
            coverage=%s,coverage_reason=%s,updated_at=%s,provider_session_id=%s where {_SCOPE}""",
            (snapshot.observation_seq, json.dumps(payload["models"]), coverage, reason, recorded_at, provider_id, *key))
        await self._coverage_receipt(conn, key, receipt_id, recorded_at)
        return "accepted"

    async def _coverage_receipt(self, conn, key, receipt_id, recorded_at):
        await conn.execute(f"""insert into model_token_usage_coverage
            (tenant_id,run_id,attempt_id,scope_id,source_receipt_id,recorded_at,coverage,coverage_reason)
            select tenant_id,run_id,attempt_id,scope_id,%s,%s,coverage,coverage_reason
            from model_token_usage_scopes where {_SCOPE} on conflict do nothing""", (receipt_id, recorded_at, *key))

    async def _mark_partial(self, conn, key, reason, recorded_at):
        await conn.execute(f"""update model_token_usage_scopes set coverage='partial',
            coverage_reason=coalesce(coverage_reason,%s),updated_at=%s where {_SCOPE}""", (reason, recorded_at, *key))

    async def query(self, conn, *, tenant_id, start_at, end_at, user_id, q, limit, offset):
        # Search is literal substring matching, not user-supplied SQL wildcard syntax.
        search = q.strip().lower() if q and q.strip() else None
        filters = (tenant_id, start_at, end_at, user_id, user_id, search, search, search)
        cte = """with selected as (
            select x.*, r.user_id, r.auth_source, u.display_name
            from model_token_usage_deltas x join runs r on r.tenant_id=x.tenant_id and r.id=x.run_id
            left join users u on u.tenant_id=r.tenant_id and u.id=r.user_id
            where x.tenant_id=%s and x.recorded_at >= %s and x.recorded_at < %s
              and (%s::text is null or r.user_id=%s)
              and (%s::text is null or position(%s in lower(coalesce(r.user_id,'')))>0
                   or position(%s in lower(coalesce(u.display_name,'')))>0)
        ), totals as (select """
        if user_id is None:
            grouping = "user_id, max(display_name) display_name, bool_and(auth_source='company-login') company_identity"
            group_key = "user_id"
        else:
            grouping = "actual_model as model"
            group_key = "actual_model"
        cte += grouping + "," + ",".join(f"sum({f}) as {f}" for f in TOKEN_FIELDS)
        cte += f" from selected group by {group_key}) "
        total = await (await conn.execute(cte + "select count(*) as total from totals", filters)).fetchone()
        query = cte + "select * from totals order by (input_tokens+cache_read_input_tokens+cache_creation_input_tokens+output_tokens) desc, "
        query += "user_id asc nulls last" if user_id is None else "model asc"
        args = filters
        if user_id is None:
            query += " limit %s offset %s"
            args += (limit, offset)
        rows = await (await conn.execute(query, args)).fetchall()
        data = []
        for row in rows:
            identity = {"user_id": row["user_id"], "work_id": row["user_id"] if row["company_identity"] else None,
                        "display_name": row["display_name"]} if user_id is None else {"model": row["model"]}
            data.append({**identity, **token_totals(row)})
        coverage = await (await conn.execute("""with window_coverage as (
            select distinct on (tenant_id,run_id,attempt_id,scope_id) *
            from model_token_usage_coverage
            where tenant_id=%s and recorded_at >= %s and recorded_at < %s
            order by tenant_id,run_id,attempt_id,scope_id,recorded_at desc,id desc
        ) select count(*) as scopes,
            count(*) filter(where s.coverage<>'reported_final') as partial_scopes,
            count(*) filter(where s.coverage_reason='baseline_unknown') as unknown_scopes
            from window_coverage s join runs r on r.tenant_id=s.tenant_id and r.id=s.run_id
            left join users u on u.tenant_id=r.tenant_id and u.id=r.user_id
            where (%s::text is null or r.user_id=%s)
              and (%s::text is null or position(%s in lower(coalesce(r.user_id,'')))>0
                   or position(%s in lower(coalesce(u.display_name,'')))>0)""", filters)).fetchone()
        missing = await (await conn.execute("""select count(*) as missing from runs r
            left join users u on u.tenant_id=r.tenant_id and u.id=r.user_id
            where r.tenant_id=%s and r.created_at >= %s and r.created_at < %s
              and (%s::text is null or r.user_id=%s)
              and (%s::text is null or position(%s in lower(coalesce(r.user_id,'')))>0
                   or position(%s in lower(coalesce(u.display_name,'')))>0)
              and not exists(select 1 from model_token_usage_scopes s where s.tenant_id=r.tenant_id and s.run_id=r.id and s.created_at < %s)""", (*filters, end_at))).fetchone()
        started = await (await conn.execute("select applied_at from schema_migrations where version='2026.10.20.2'")).fetchone()
        collected_at = started["applied_at"] if started else None
        partial = int(coverage["partial_scopes"])
        incomplete = partial or missing["missing"] or collected_at is None or start_at < collected_at
        known = int(total["total"]) > 0
        reason = ("partial_report" if known and incomplete else
                  "baseline_unknown" if coverage["unknown_scopes"] else
                  "report_missing" if coverage["scopes"] else "not_collected")
        return {"data": data, "total": int(total["total"]), "limit": limit if user_id is None else None,
            "offset": offset if user_id is None else 0,
            "meta": {"collection_started_at": collected_at,
                "coverage": {"status": ("partial" if incomplete else "reported_final") if known else "unavailable",
                    "reason": reason if incomplete or not known else None,
                    "scopes": int(coverage["scopes"]), "partial_scopes": partial}}}
