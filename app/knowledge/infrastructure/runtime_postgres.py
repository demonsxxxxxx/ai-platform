"""Generation-fenced PostgreSQL primitives for Knowledge Run execution."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Iterable

from app.knowledge.domain import (
    KnowledgeError,
    KnowledgeEvidence,
    RunKnowledgeSnapshot,
    RunKnowledgeSourceSnapshot,
    canonical_run_knowledge_bindings,
    ordered_citation_evidence_ids,
)


_TERMINAL_STATUSES = frozenset({"succeeded", "no_evidence", "failed", "cancelled"})
_SAFE_RETRIEVAL_FAILURE_CODES = frozenset(
    {
        "knowledge_access_denied",
        "knowledge_binding_invalid",
        "knowledge_connection_invalid",
        "knowledge_connection_unavailable",
        "knowledge_no_evidence",
        "knowledge_profile_invalid",
        "knowledge_provider_rejected",
        "knowledge_provider_transient",
        "knowledge_query_invalid",
        "knowledge_response_invalid",
        "knowledge_retrieval_timeout",
        "knowledge_source_disabled",
        "knowledge_source_missing",
    }
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _attempt_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "attempt_id": str(row["attempt_id"]),
        "generation": int(row["generation"]),
        "snapshot_hash": str(row["snapshot_hash"]),
        "status": str(row["status"]),
        "source_count": int(row.get("source_count") or 0),
        "result_count": int(row.get("result_count") or 0),
        "evidence_count": int(row.get("evidence_count") or 0),
        "provider_retry_count": int(row.get("provider_retry_count") or 0),
        "duration_ms": (
            int(row["duration_ms"]) if row.get("duration_ms") is not None else None
        ),
        "safe_failure_code": str(row.get("safe_failure_code") or "") or None,
        "cancel_requested_at": _iso(row.get("cancel_requested_at")),
        "terminal_digest": str(row.get("terminal_digest") or "") or None,
        "started_at": _iso(row.get("started_at")),
        "deadline_at": _iso(row.get("deadline_at")),
        "remaining_ms": (
            int(row["remaining_ms"])
            if row.get("remaining_ms") is not None
            else None
        ),
        "completed_at": _iso(row.get("completed_at")),
    }


def _snapshot_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(row["run_id"]),
        "agent_id": str(row["agent_id"]),
        "profile_revision": int(row["profile_revision"]),
        "profile_content_hash": str(row["profile_content_hash"]),
        "retrieval_profile_id": str(row["retrieval_profile_id"]),
        "retrieval_profile_revision": int(row["retrieval_profile_revision"]),
        "sources": list(row.get("sources_json") or []),
        "principal_policy_version": int(row["principal_policy_version"]),
        "authorized_at": _iso(row.get("authorized_at")),
        "content_hash": str(row["content_hash"]),
        "created_at": _iso(row.get("created_at")),
    }


def _canonical_digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    return value if len(encoded) <= max_bytes else encoded[:max_bytes].decode("utf-8", "ignore")


def _citation_position(value: Any) -> Any:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return value if len(canonical.encode("utf-8")) <= 2048 else {}


def _citation_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "message_id": str(row["message_id"]),
        "run_id": str(row["run_id"]),
        "evidence_id": str(row["evidence_id"]),
        "ordinal": int(row["ordinal"]),
        "title": str(row.get("title") or ""),
        "excerpt": str(row["excerpt"]),
        "score": float(row["score"]),
        "position": row.get("position_json") or {},
    }


def _preempting_terminal_outcome(
    attempt: dict[str, Any],
) -> tuple[str, str | None] | None:
    deadline_at = attempt.get("deadline_at")
    cancel_requested_at = attempt.get("cancel_requested_at")
    server_now = attempt.get("server_now")
    if not isinstance(deadline_at, datetime) or not isinstance(server_now, datetime):
        raise KnowledgeError("knowledge_retrieval_fence_stale")
    if isinstance(cancel_requested_at, datetime) and cancel_requested_at <= deadline_at:
        return "cancelled", None
    if server_now >= deadline_at:
        return "failed", "knowledge_retrieval_timeout"
    return None


def _validate_profile_bindings(snapshot: RunKnowledgeSnapshot, value: Any) -> None:
    if not isinstance(value, list) or len(value) != len(snapshot.sources):
        raise KnowledgeError("knowledge_snapshot_profile_mismatch")
    for expected, actual in zip(snapshot.sources, value, strict=True):
        if not isinstance(actual, dict):
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")
        source_authorization_version = actual.get("source_authorization_version")
        ordinal = actual.get("ordinal")
        retrieval_profile_revision = actual.get("retrieval_profile_revision")
        if (
            str(actual.get("source_id") or "") != expected.source_id
            or isinstance(source_authorization_version, bool)
            or not isinstance(source_authorization_version, int)
            or source_authorization_version != expected.source_authorization_version
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal != expected.ordinal
            or actual.get("required") is not True
            or str(actual.get("retrieval_profile_id") or "")
            != snapshot.retrieval_profile_id
            or isinstance(retrieval_profile_revision, bool)
            or not isinstance(retrieval_profile_revision, int)
            or retrieval_profile_revision != snapshot.retrieval_profile_revision
        ):
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")


def _validate_terminal_counts(
    *,
    status: str,
    source_count: int,
    result_count: int,
    evidence_count: int,
    provider_retry_count: int,
    duration_ms: int,
    safe_failure_code: str | None,
) -> None:
    integer_values = (
        source_count,
        result_count,
        evidence_count,
        provider_retry_count,
        duration_ms,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in integer_values
    ):
        raise KnowledgeError("knowledge_retrieval_terminal_invalid")
    if (
        not 1 <= source_count <= 8
        or not 0 <= result_count <= 160
        or not 0 <= evidence_count <= 20
        or evidence_count > result_count
        or not 0 <= provider_retry_count <= 24
        or not 0 <= duration_ms <= 120_000
        or (status == "succeeded" and evidence_count == 0)
        or (status == "no_evidence" and evidence_count != 0)
    ):
        raise KnowledgeError("knowledge_retrieval_terminal_invalid")
    if (
        safe_failure_code is not None
        and safe_failure_code not in _SAFE_RETRIEVAL_FAILURE_CODES
    ):
        raise KnowledgeError("knowledge_retrieval_terminal_invalid")
    if status == "succeeded" and safe_failure_code is not None:
        raise KnowledgeError("knowledge_retrieval_terminal_invalid")
    if status == "no_evidence" and safe_failure_code != "knowledge_no_evidence":
        raise KnowledgeError("knowledge_retrieval_terminal_invalid")
    if status == "failed" and safe_failure_code is None:
        raise KnowledgeError("knowledge_retrieval_terminal_invalid")
    if status == "cancelled" and safe_failure_code is not None:
        raise KnowledgeError("knowledge_retrieval_terminal_invalid")


class PostgresKnowledgeRuntimeRepository:
    """Persistence boundary; every mutating method requires the caller transaction."""

    async def create_run_snapshot_from_bindings(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        agent_id: str,
        profile_revision: int,
        profile_content_hash: str,
        principal_policy_version: int,
        knowledge_source_ids: tuple[str, ...],
        retrieval_profile_id: str,
        knowledge_bindings: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        canonical_bindings = canonical_run_knowledge_bindings(
            source_ids=knowledge_source_ids,
            retrieval_profile_id=retrieval_profile_id,
            bindings=knowledge_bindings,
        )
        source_ids = [str(binding["source_id"]) for binding in canonical_bindings]
        retrieval_profile_revision = int(
            canonical_bindings[0]["retrieval_profile_revision"]
        )

        source_cursor = await conn.execute(
            """
            select sources.id as source_id, sources.connection_id,
                   sources.provider_resource_id, sources.authorization_version,
                   sources.last_complete_sync_id as connection_catalog_sync_id,
                   sources.last_seen_connection_revision_id as connection_revision_id,
                   connections.lifecycle_epoch as connection_lifecycle_epoch,
                   revisions.revision as connection_revision
            from knowledge_sources sources
            join knowledge_connections connections
              on connections.tenant_id = sources.tenant_id
             and connections.id = sources.connection_id
            left join knowledge_connection_revisions revisions
              on revisions.tenant_id = sources.tenant_id
             and revisions.connection_id = sources.connection_id
             and revisions.id = sources.last_seen_connection_revision_id
            where sources.tenant_id = %s and sources.id = any(%s)
            order by sources.id
            """,
            (tenant_id, sorted(source_ids)),
        )
        source_rows = {
            str(row["source_id"]): dict(row) for row in await source_cursor.fetchall()
        }
        if set(source_rows) != set(source_ids):
            raise KnowledgeError("knowledge_snapshot_source_unavailable")

        sources: list[RunKnowledgeSourceSnapshot] = []
        for binding in canonical_bindings:
            source_id = str(binding["source_id"])
            row = source_rows[source_id]
            if row.get("connection_revision") is None:
                raise KnowledgeError("knowledge_snapshot_authority_stale")
            try:
                sources.append(
                    RunKnowledgeSourceSnapshot(
                        source_id=source_id,
                        source_authorization_version=int(
                            binding["source_authorization_version"]
                        ),
                        connection_id=row["connection_id"],
                        connection_revision_id=row["connection_revision_id"],
                        connection_revision=int(row["connection_revision"]),
                        connection_catalog_sync_id=row["connection_catalog_sync_id"],
                        connection_lifecycle_epoch=int(
                            row["connection_lifecycle_epoch"]
                        ),
                        provider_resource_id=row["provider_resource_id"],
                        ordinal=int(binding["ordinal"]),
                        required=True,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise KnowledgeError("knowledge_snapshot_authority_stale") from exc
        snapshot = RunKnowledgeSnapshot(
            tenant_id=tenant_id,
            run_id=run_id,
            agent_id=agent_id,
            profile_revision=profile_revision,
            profile_content_hash=profile_content_hash,
            retrieval_profile_id=retrieval_profile_id,
            retrieval_profile_revision=retrieval_profile_revision,
            sources=tuple(sources),
            principal_policy_version=principal_policy_version,
        )
        return await self.create_run_snapshot(conn, snapshot=snapshot)

    async def create_run_snapshot(
        self,
        conn: Any,
        *,
        snapshot: RunKnowledgeSnapshot,
    ) -> dict[str, Any]:
        existing_cursor = await conn.execute(
            """
            select * from run_knowledge_snapshots
            where tenant_id = %s and run_id = %s
            """,
            (snapshot.tenant_id, snapshot.run_id),
        )
        existing = await existing_cursor.fetchone()
        if existing is not None:
            if str(existing["content_hash"]) != snapshot.content_hash():
                raise KnowledgeError("knowledge_snapshot_conflict")
            return _snapshot_projection(existing)

        run_cursor = await conn.execute(
            """
            select runs.agent_id, runs.admitted_agent_profile_revision,
                   runs.admitted_agent_profile_hash, runs.authz_policy_version,
                   profiles.content_hash as profile_content_hash,
                   profiles.knowledge_bindings
            from runs
            join agent_profile_revisions profiles
              on profiles.tenant_id = runs.tenant_id
             and profiles.agent_id = runs.agent_id
             and profiles.revision = runs.admitted_agent_profile_revision
            where runs.tenant_id = %s and runs.id = %s
            for update of runs
            """,
            (snapshot.tenant_id, snapshot.run_id),
        )
        run = await run_cursor.fetchone()
        if (
            run is None
            or str(run["agent_id"]) != snapshot.agent_id
            or int(run.get("admitted_agent_profile_revision") or 0)
            != snapshot.profile_revision
            or str(run.get("admitted_agent_profile_hash") or "")
            != snapshot.profile_content_hash
            or str(run.get("profile_content_hash") or "")
            != snapshot.profile_content_hash
            or int(run.get("authz_policy_version") or 0)
            != snapshot.principal_policy_version
        ):
            raise KnowledgeError("knowledge_snapshot_run_mismatch")
        _validate_profile_bindings(snapshot, run.get("knowledge_bindings"))

        # Source -> connection is the global authority lock order shared with
        # catalog commits and lifecycle writers.  IDs are sorted independently
        # of Agent binding order so concurrent multi-source admission converges.
        source_ids = sorted(source.source_id for source in snapshot.sources)
        source_cursor = await conn.execute(
            """
            select id, connection_id, provider_resource_id, status,
                   authorization_version, last_complete_sync_id,
                   last_seen_connection_revision_id
            from knowledge_sources
            where tenant_id = %s and id = any(%s)
            order by id
            for update
            """,
            (snapshot.tenant_id, source_ids),
        )
        source_rows = await source_cursor.fetchall()
        sources_by_id = {str(row["id"]): row for row in source_rows}
        if set(sources_by_id) != set(source_ids):
            raise KnowledgeError("knowledge_snapshot_source_unavailable")

        connection_ids = sorted({source.connection_id for source in snapshot.sources})
        connection_cursor = await conn.execute(
            """
            select id, status, lifecycle_epoch, active_revision_id,
                   active_catalog_sync_id
            from knowledge_connections
            where tenant_id = %s and id = any(%s)
            order by id
            for update
            """,
            (snapshot.tenant_id, connection_ids),
        )
        connection_rows = await connection_cursor.fetchall()
        connections_by_id = {str(row["id"]): row for row in connection_rows}
        if set(connections_by_id) != set(connection_ids):
            raise KnowledgeError("knowledge_snapshot_connection_unavailable")

        profile_cursor = await conn.execute(
            """
            select 1
            from knowledge_retrieval_profiles
            where id = %s and revision = %s and status = 'active'
            for share
            """,
            (snapshot.retrieval_profile_id, snapshot.retrieval_profile_revision),
        )
        if await profile_cursor.fetchone() is None:
            raise KnowledgeError("knowledge_retrieval_profile_unavailable")

        revision_ids = sorted(
            {source.connection_revision_id for source in snapshot.sources}
        )
        revision_cursor = await conn.execute(
            """
            select id, connection_id, revision, check_status
            from knowledge_connection_revisions
            where tenant_id = %s and id = any(%s)
            """,
            (snapshot.tenant_id, revision_ids),
        )
        revisions_by_id = {
            str(row["id"]): row for row in await revision_cursor.fetchall()
        }

        sync_ids = sorted(
            {source.connection_catalog_sync_id for source in snapshot.sources}
        )
        sync_cursor = await conn.execute(
            """
            select id, connection_id, connection_revision_id, status
            from knowledge_catalog_syncs
            where tenant_id = %s and id = any(%s)
            """,
            (snapshot.tenant_id, sync_ids),
        )
        syncs_by_id = {str(row["id"]): row for row in await sync_cursor.fetchall()}

        for source in snapshot.sources:
            source_row = sources_by_id[source.source_id]
            connection = connections_by_id[source.connection_id]
            revision = revisions_by_id.get(source.connection_revision_id)
            sync = syncs_by_id.get(source.connection_catalog_sync_id)
            if (
                str(source_row["status"]) != "active"
                or str(source_row["connection_id"]) != source.connection_id
                or str(source_row["provider_resource_id"])
                != source.provider_resource_id
                or int(source_row["authorization_version"])
                != source.source_authorization_version
                or str(source_row.get("last_complete_sync_id") or "")
                != source.connection_catalog_sync_id
                or str(source_row.get("last_seen_connection_revision_id") or "")
                != source.connection_revision_id
                or str(connection["status"]) != "active"
                or int(connection["lifecycle_epoch"])
                != source.connection_lifecycle_epoch
                or str(connection.get("active_revision_id") or "")
                != source.connection_revision_id
                or str(connection.get("active_catalog_sync_id") or "")
                != source.connection_catalog_sync_id
                or revision is None
                or str(revision["connection_id"]) != source.connection_id
                or int(revision["revision"]) != source.connection_revision
                or str(revision["check_status"]) != "passed"
                or sync is None
                or str(sync["connection_id"]) != source.connection_id
                or str(sync["connection_revision_id"]) != source.connection_revision_id
                or str(sync["status"]) != "succeeded"
            ):
                raise KnowledgeError("knowledge_snapshot_authority_stale")

        insert_cursor = await conn.execute(
            """
            insert into run_knowledge_snapshots(
              tenant_id, run_id, agent_id, profile_revision, profile_content_hash,
              retrieval_profile_id, retrieval_profile_revision, sources_json,
              principal_policy_version, authorized_at, content_hash
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now(), %s
            )
            on conflict (tenant_id, run_id) do nothing
            returning *
            """,
            (
                snapshot.tenant_id,
                snapshot.run_id,
                snapshot.agent_id,
                snapshot.profile_revision,
                snapshot.profile_content_hash,
                snapshot.retrieval_profile_id,
                snapshot.retrieval_profile_revision,
                snapshot.sources_canonical_json(),
                snapshot.principal_policy_version,
                snapshot.content_hash(),
            ),
        )
        inserted = await insert_cursor.fetchone()
        if inserted is not None:
            return _snapshot_projection(inserted)
        replay_cursor = await conn.execute(
            """
            select * from run_knowledge_snapshots
            where tenant_id = %s and run_id = %s
            """,
            (snapshot.tenant_id, snapshot.run_id),
        )
        replay = await replay_cursor.fetchone()
        if replay is None or str(replay["content_hash"]) != snapshot.content_hash():
            raise KnowledgeError("knowledge_snapshot_conflict")
        return _snapshot_projection(replay)

    async def load_run_knowledge_snapshot(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select *
            from run_knowledge_snapshots
            where tenant_id = %s and run_id = %s
            """,
            (tenant_id, run_id),
        )
        row = await cursor.fetchone()
        return None if row is None else _snapshot_projection(dict(row))

    async def load_run_attempt_generation(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select owner_generation, status,
                   lease_expires_at > clock_timestamp() as lease_valid
            from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
            """,
            (tenant_id, run_id, attempt_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        generation = row.get("owner_generation")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise KnowledgeError("knowledge_binding_invalid")
        return {
            "generation": generation,
            "status": str(row.get("status") or ""),
            "lease_valid": row.get("lease_valid") is True,
        }

    async def load_connection_revision(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        revision_id: str,
        revision: int,
        catalog_sync_id: str,
        lifecycle_epoch: int,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select revisions.id as revision_id, revisions.connection_id,
                   revisions.revision, revisions.provider_key, revisions.base_url,
                   revisions.secret_ref, revisions.transport_policy_json,
                   revisions.check_status, receipts.state as activation_state
            from knowledge_connection_revisions revisions
            join knowledge_connection_lifecycle_receipts receipts
              on receipts.tenant_id = revisions.tenant_id
             and receipts.connection_id = revisions.connection_id
             and receipts.lifecycle_epoch = %s
             and receipts.active_revision_id = revisions.id
             and receipts.active_catalog_sync_id = %s
            where revisions.tenant_id = %s and revisions.connection_id = %s
              and revisions.id = %s and revisions.revision = %s
            """,
            (
                lifecycle_epoch,
                catalog_sync_id,
                tenant_id,
                connection_id,
                revision_id,
                revision,
            ),
        )
        row = await cursor.fetchone()
        return None if row is None else dict(row)

    async def load_retrieval_profile(
        self,
        conn: Any,
        *,
        profile_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select *
            from knowledge_retrieval_profiles
            where id = %s and revision = %s
            """,
            (profile_id, revision),
        )
        row = await cursor.fetchone()
        return None if row is None else dict(row)

    async def load_retrieval_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select attempts.*,
                   greatest(
                     0,
                     floor(extract(epoch from (
                       attempts.deadline_at - clock_timestamp()
                     )) * 1000)
                   )::bigint as remaining_ms
            from knowledge_retrieval_attempts attempts
            where attempts.tenant_id = %s and attempts.run_id = %s
              and attempts.attempt_id = %s and attempts.generation = %s
              and attempts.snapshot_hash = %s
            """,
            (tenant_id, run_id, attempt_id, generation, snapshot_hash),
        )
        row = await cursor.fetchone()
        return None if row is None else _attempt_projection(dict(row))

    async def retrieval_deadline_is_live(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
    ) -> bool:
        cursor = await conn.execute(
            """
            select exists (
              select 1
              from knowledge_retrieval_attempts attempts
              where attempts.tenant_id = %s and attempts.run_id = %s
                and attempts.attempt_id = %s and attempts.generation = %s
                and attempts.snapshot_hash = %s
                and attempts.status = 'retrieving'
                and attempts.deadline_at > clock_timestamp()
            ) as deadline_live
            """,
            (tenant_id, run_id, attempt_id, generation, snapshot_hash),
        )
        row = await cursor.fetchone()
        return row is not None and row.get("deadline_live") is True

    async def claim_retrieval_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
        source_count: int,
        overall_timeout_ms: int,
    ) -> dict[str, Any]:
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation <= 0
            or isinstance(source_count, bool)
            or not isinstance(source_count, int)
            or not 1 <= source_count <= 8
            or isinstance(overall_timeout_ms, bool)
            or not isinstance(overall_timeout_ms, int)
            or not 100 <= overall_timeout_ms <= 60_000
        ):
            raise KnowledgeError("knowledge_retrieval_claim_invalid")
        await self._lock_run_attempt_fence(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            generation=generation,
            allow_cancel_requested=False,
            require_live_lease=True,
        )
        snapshot_cursor = await conn.execute(
            """
            select content_hash, jsonb_array_length(sources_json) as source_count
            from run_knowledge_snapshots
            where tenant_id = %s and run_id = %s
            """,
            (tenant_id, run_id),
        )
        snapshot = await snapshot_cursor.fetchone()
        if (
            snapshot is None
            or str(snapshot["content_hash"]) != snapshot_hash
            or int(snapshot["source_count"]) != source_count
        ):
            raise KnowledgeError("knowledge_retrieval_snapshot_mismatch")

        cursor = await conn.execute(
            """
            insert into knowledge_retrieval_attempts(
              id, tenant_id, run_id, attempt_id, generation, snapshot_hash,
              status, source_count, started_at, deadline_at
            ) values (
              %s, %s, %s, %s, %s, %s, 'retrieving', %s, clock_timestamp(),
              clock_timestamp() + (%s * interval '1 millisecond')
            )
            on conflict (tenant_id, run_id, attempt_id, generation) do nothing
            returning *,
              greatest(
                0,
                floor(extract(epoch from (
                  deadline_at - clock_timestamp()
                )) * 1000)
              )::bigint as remaining_ms
            """,
            (
                _new_id("kret"),
                tenant_id,
                run_id,
                attempt_id,
                generation,
                snapshot_hash,
                source_count,
                overall_timeout_ms,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            existing_cursor = await conn.execute(
                """
                select attempts.*,
                       greatest(
                         0,
                         floor(extract(epoch from (
                           attempts.deadline_at - clock_timestamp()
                         )) * 1000)
                       )::bigint as remaining_ms
                from knowledge_retrieval_attempts attempts
                where attempts.tenant_id = %s and attempts.run_id = %s
                  and attempts.attempt_id = %s and attempts.generation = %s
                for update
                """,
                (tenant_id, run_id, attempt_id, generation),
            )
            row = await existing_cursor.fetchone()
        if (
            row is None
            or str(row["snapshot_hash"]) != snapshot_hash
            or int(row["source_count"]) != source_count
            or str(row["status"]) not in {"retrieving", *_TERMINAL_STATUSES}
        ):
            raise KnowledgeError("knowledge_retrieval_claim_conflict")
        return _attempt_projection(row)

    async def request_cancellation(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
    ) -> dict[str, Any]:
        await self._lock_run_attempt_fence(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            generation=generation,
            allow_cancel_requested=True,
            allow_cancel_successor=True,
            require_live_lease=False,
        )
        cursor = await conn.execute(
            """
            update knowledge_retrieval_attempts
            set cancel_requested_at = coalesce(cancel_requested_at, clock_timestamp()),
                updated_at = clock_timestamp()
            where tenant_id = %s and run_id = %s and attempt_id = %s
              and generation = %s and snapshot_hash = %s
              and status = 'retrieving'
            returning *
            """,
            (tenant_id, run_id, attempt_id, generation, snapshot_hash),
        )
        row = await cursor.fetchone()
        if row is None:
            row = await self._load_retrieval_attempt_for_update(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                generation=generation,
                snapshot_hash=snapshot_hash,
            )
        if row is None:
            raise KnowledgeError("knowledge_retrieval_fence_stale")
        return _attempt_projection(row)

    async def terminalize_retrieval_attempt(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
        status: str,
        source_count: int,
        result_count: int,
        provider_retry_count: int,
        duration_ms: int,
        safe_failure_code: str | None,
    ) -> dict[str, Any]:
        if status not in {"no_evidence", "failed", "cancelled"}:
            raise KnowledgeError("knowledge_retrieval_terminal_invalid")
        _validate_terminal_counts(
            status=status,
            source_count=source_count,
            result_count=result_count,
            evidence_count=0,
            provider_retry_count=provider_retry_count,
            duration_ms=duration_ms,
            safe_failure_code=safe_failure_code,
        )
        digest = _canonical_digest(
            {
                "duration_ms": duration_ms,
                "evidence_count": 0,
                "provider_retry_count": provider_retry_count,
                "result_count": result_count,
                "safe_failure_code": safe_failure_code,
                "source_count": source_count,
                "status": status,
            }
        )
        return await self._commit_terminal(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            generation=generation,
            snapshot_hash=snapshot_hash,
            status=status,
            source_count=source_count,
            result_count=result_count,
            evidence_count=0,
            provider_retry_count=provider_retry_count,
            duration_ms=duration_ms,
            safe_failure_code=safe_failure_code,
            terminal_digest=digest,
        )

    async def commit_successful_retrieval(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
        source_count: int,
        result_count: int,
        provider_retry_count: int,
        duration_ms: int,
        evidence: Iterable[KnowledgeEvidence],
    ) -> dict[str, Any]:
        evidence_rows = tuple(evidence)
        _validate_terminal_counts(
            status="succeeded",
            source_count=source_count,
            result_count=result_count,
            evidence_count=len(evidence_rows),
            provider_retry_count=provider_retry_count,
            duration_ms=duration_ms,
            safe_failure_code=None,
        )
        if tuple(item.fused_rank for item in evidence_rows) != tuple(
            range(1, len(evidence_rows) + 1)
        ):
            raise KnowledgeError("knowledge_evidence_invalid")
        evidence_ids = tuple(item.evidence_id for item in evidence_rows)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise KnowledgeError("knowledge_evidence_invalid")
        digest = _canonical_digest(
            {
                "duration_ms": duration_ms,
                "evidence": [
                    {
                        "content_sha256": item.content_sha256(),
                        "evidence_id": item.evidence_id,
                        "fused_rank": item.fused_rank,
                        "source_id": item.source_id,
                    }
                    for item in evidence_rows
                ],
                "provider_retry_count": provider_retry_count,
                "result_count": result_count,
                "source_count": source_count,
                "status": "succeeded",
            }
        )
        await self._lock_run_attempt_fence(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            generation=generation,
            allow_cancel_requested=False,
            allow_cancel_successor=False,
            require_live_lease=True,
        )
        attempt = await self._load_retrieval_attempt_for_update(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            generation=generation,
            snapshot_hash=snapshot_hash,
        )
        if attempt is None:
            raise KnowledgeError("knowledge_retrieval_fence_stale")
        if str(attempt["status"]) in _TERMINAL_STATUSES:
            return _attempt_projection(attempt)
        if str(attempt["status"]) != "retrieving":
            raise KnowledgeError("knowledge_retrieval_fence_stale")

        preempted = await self._commit_preempting_terminal(
            conn,
            attempt=attempt,
            result_count=result_count,
            provider_retry_count=provider_retry_count,
            duration_ms=duration_ms,
        )
        if preempted is not None:
            return _attempt_projection(preempted)

        snapshot_cursor = await conn.execute(
            """
            select sources_json
            from run_knowledge_snapshots
            where tenant_id = %s and run_id = %s and content_hash = %s
            """,
            (tenant_id, run_id, snapshot_hash),
        )
        snapshot = await snapshot_cursor.fetchone()
        admitted_source_ids = {
            str(item.get("source_id") or "")
            for item in ((snapshot or {}).get("sources_json") or [])
            if isinstance(item, dict)
        }
        if not admitted_source_ids or any(
            item.source_id not in admitted_source_ids for item in evidence_rows
        ):
            raise KnowledgeError("knowledge_evidence_source_mismatch")

        retrieval_attempt_id = str(attempt["id"])
        for item in evidence_rows:
            await conn.execute(
                """
                insert into knowledge_evidence(
                  tenant_id, run_id, retrieval_attempt_id, evidence_id, source_id,
                  provider_document_id, provider_chunk_id, title, content,
                  content_sha256, provider_score, fused_rank, position_json
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    tenant_id,
                    run_id,
                    retrieval_attempt_id,
                    item.evidence_id,
                    item.source_id,
                    item.provider_document_id,
                    item.provider_chunk_id,
                    item.title,
                    item.content,
                    item.content_sha256(),
                    item.provider_score,
                    item.fused_rank,
                    item.position_canonical_json(),
                ),
            )
        terminal_cursor = await conn.execute(
            """
            update knowledge_retrieval_attempts
            set status = 'succeeded', result_count = %s, evidence_count = %s,
                provider_retry_count = %s, duration_ms = %s,
                safe_failure_code = null, terminal_digest = %s,
                completed_at = clock_timestamp(), updated_at = clock_timestamp()
            where tenant_id = %s and run_id = %s and attempt_id = %s
              and generation = %s and snapshot_hash = %s
              and status = 'retrieving' and source_count = %s
            returning *
            """,
            (
                result_count,
                len(evidence_rows),
                provider_retry_count,
                duration_ms,
                digest,
                tenant_id,
                run_id,
                attempt_id,
                generation,
                snapshot_hash,
                source_count,
            ),
        )
        terminal = await terminal_cursor.fetchone()
        if terminal is None:
            raise KnowledgeError("knowledge_retrieval_terminal_conflict")
        return _attempt_projection(terminal)

    async def load_successful_evidence(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
    ) -> tuple[dict[str, Any], ...]:
        cursor = await conn.execute(
            """
            select evidence.evidence_id, evidence.source_id,
                   evidence.provider_document_id, evidence.provider_chunk_id,
                   evidence.title, evidence.content, evidence.content_sha256,
                   evidence.provider_score, evidence.fused_rank, evidence.position_json
            from knowledge_retrieval_attempts attempts
            join knowledge_evidence evidence
              on evidence.tenant_id = attempts.tenant_id
             and evidence.run_id = attempts.run_id
             and evidence.retrieval_attempt_id = attempts.id
            where attempts.tenant_id = %s and attempts.run_id = %s
              and attempts.attempt_id = %s and attempts.generation = %s
              and attempts.snapshot_hash = %s and attempts.status = 'succeeded'
            order by evidence.fused_rank, evidence.evidence_id
            """,
            (tenant_id, run_id, attempt_id, generation, snapshot_hash),
        )
        return tuple(dict(row) for row in await cursor.fetchall())

    async def resolve_citation_evidence_ids(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        answer: str,
    ) -> tuple[str, ...]:
        _, evidence = await self._load_citation_authority(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        return ordered_citation_evidence_ids(
            answer,
            tuple(str(row["evidence_id"]) for row in evidence),
        )

    async def finalize_citations(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        message_id: str,
        answer: str,
        operation_id: str,
    ) -> tuple[dict[str, Any], ...]:
        values = (tenant_id, run_id, attempt_id, message_id, operation_id)
        if (
            any(
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
                for value in values
            )
            or len(operation_id.encode("utf-8")) > 256
        ):
            raise KnowledgeError("knowledge_citation_invalid")
        message_cursor = await conn.execute(
            """
            select id from messages
            where tenant_id = %s and run_id = %s and id = %s and role = 'assistant'
            for update
            """,
            (tenant_id, run_id, message_id),
        )
        if await message_cursor.fetchone() is None:
            raise KnowledgeError("knowledge_citation_message_invalid")
        attempt, evidence = await self._load_citation_authority(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        evidence_ids = ordered_citation_evidence_ids(
            answer,
            tuple(str(row["evidence_id"]) for row in evidence),
        )
        evidence_by_id = {str(row["evidence_id"]): row for row in evidence}
        evidence = tuple(evidence_by_id[evidence_id] for evidence_id in evidence_ids)
        digest = _canonical_digest({"evidence_ids": list(evidence_ids)})
        existing_cursor = await conn.execute(
            """
            select * from knowledge_citations
            where tenant_id = %s and run_id = %s and message_id = %s
            order by ordinal
            """,
            (tenant_id, run_id, message_id),
        )
        existing = tuple(dict(row) for row in await existing_cursor.fetchall())
        if existing:
            if (
                tuple(str(row["evidence_id"]) for row in existing) != evidence_ids
                or any(str(row["operation_id"]) != operation_id for row in existing)
                or any(str(row["citation_set_hash"]) != digest for row in existing)
                or any(str(row["retrieval_attempt_id"]) != attempt_id for row in existing)
            ):
                raise KnowledgeError("knowledge_citation_conflict")
            return tuple(_citation_projection(row) for row in existing)
        citations: list[dict[str, Any]] = []
        for ordinal, row in enumerate(evidence, start=1):
            citation_id = "kct_" + hashlib.sha256(
                f"{tenant_id}\x1f{run_id}\x1f{message_id}\x1f{ordinal}\x1f{row['evidence_id']}".encode(
                    "utf-8"
                )
            ).hexdigest()[:32]
            position = _citation_position(row.get("position_json") or {})
            cursor = await conn.execute(
                """
                insert into knowledge_citations(
                  id, tenant_id, run_id, message_id, retrieval_attempt_id,
                  retrieval_generation, evidence_id, ordinal, source_id,
                  document_ref, chunk_ref, title, excerpt, content_sha256,
                  score, position_json, operation_id, citation_set_hash
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                ) returning *
                """,
                (
                    citation_id,
                    tenant_id,
                    run_id,
                    message_id,
                    attempt["id"],
                    int(attempt["generation"]),
                    row["evidence_id"],
                    ordinal,
                    row["source_id"],
                    row["provider_document_id"],
                    row.get("provider_chunk_id"),
                    row.get("title") or "",
                    _truncate_utf8(str(row["content"]), 2048),
                    row["content_sha256"],
                    row["provider_score"],
                    json.dumps(position, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    operation_id,
                    digest,
                ),
            )
            citations.append(dict(await cursor.fetchone()))
        return tuple(_citation_projection(row) for row in citations)

    async def _load_citation_authority(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        attempt_cursor = await conn.execute(
            """
            select attempts.id, attempts.generation
            from knowledge_retrieval_attempts attempts
            join run_attempts execution_attempt
              on execution_attempt.tenant_id = attempts.tenant_id
             and execution_attempt.run_id = attempts.run_id
             and execution_attempt.id = attempts.attempt_id
             and execution_attempt.owner_generation = attempts.generation
            where attempts.tenant_id = %s and attempts.run_id = %s
              and attempts.attempt_id = %s and attempts.status = 'succeeded'
              and execution_attempt.status in ('claimed', 'running')
            for key share of attempts, execution_attempt
            """,
            (tenant_id, run_id, attempt_id),
        )
        attempt = await attempt_cursor.fetchone()
        if attempt is None:
            raise KnowledgeError("knowledge_citation_fence_stale")
        evidence_cursor = await conn.execute(
            """
            select evidence_id, source_id, provider_document_id, provider_chunk_id,
                   title, content, content_sha256, provider_score,
                   fused_rank, position_json
            from knowledge_evidence
            where tenant_id = %s and run_id = %s and retrieval_attempt_id = %s
            order by fused_rank, evidence_id
            for key share
            """,
            (tenant_id, run_id, attempt["id"]),
        )
        evidence = tuple(dict(row) for row in await evidence_cursor.fetchall())
        if not evidence:
            raise KnowledgeError("knowledge_citation_invalid")
        return dict(attempt), evidence

    async def _commit_terminal(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
        status: str,
        source_count: int,
        result_count: int,
        evidence_count: int,
        provider_retry_count: int,
        duration_ms: int,
        safe_failure_code: str | None,
        terminal_digest: str,
    ) -> dict[str, Any]:
        await self._lock_run_attempt_fence(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            generation=generation,
            allow_cancel_requested=status == "cancelled",
            allow_cancel_successor=status == "cancelled",
            require_live_lease=status != "cancelled",
        )
        existing = await self._load_retrieval_attempt_for_update(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            generation=generation,
            snapshot_hash=snapshot_hash,
        )
        if existing is None:
            raise KnowledgeError("knowledge_retrieval_fence_stale")
        if str(existing["status"]) in _TERMINAL_STATUSES:
            return _attempt_projection(existing)
        preempted = await self._commit_preempting_terminal(
            conn,
            attempt=existing,
            result_count=result_count,
            provider_retry_count=provider_retry_count,
            duration_ms=duration_ms,
        )
        if preempted is not None:
            return _attempt_projection(preempted)
        if status == "cancelled":
            raise KnowledgeError("knowledge_retrieval_terminal_invalid")
        cursor = await conn.execute(
            """
            update knowledge_retrieval_attempts
            set status = %s, result_count = %s, evidence_count = %s,
                provider_retry_count = %s, duration_ms = %s,
                safe_failure_code = %s, terminal_digest = %s,
                completed_at = clock_timestamp(), updated_at = clock_timestamp()
            where tenant_id = %s and run_id = %s and attempt_id = %s
              and generation = %s and snapshot_hash = %s
              and status = 'retrieving' and source_count = %s
            returning *
            """,
            (
                status,
                result_count,
                evidence_count,
                provider_retry_count,
                duration_ms,
                safe_failure_code,
                terminal_digest,
                tenant_id,
                run_id,
                attempt_id,
                generation,
                snapshot_hash,
                source_count,
            ),
        )
        terminal = await cursor.fetchone()
        if terminal is None:
            raise KnowledgeError("knowledge_retrieval_terminal_conflict")
        return _attempt_projection(terminal)

    async def _lock_run_attempt_fence(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        allow_cancel_requested: bool,
        allow_cancel_successor: bool = False,
        require_live_lease: bool,
    ) -> dict[str, Any]:
        cursor = await conn.execute(
            """
            select id, status, owner_generation,
                   lease_expires_at > clock_timestamp() as lease_valid
            from run_attempts
            where tenant_id = %s and run_id = %s and id = %s
            for update
            """,
            (tenant_id, run_id, attempt_id),
        )
        row = await cursor.fetchone()
        allowed_statuses = {"claimed", "running"}
        if allow_cancel_requested:
            allowed_statuses.add("cancel_requested")
        status = str(row.get("status") or "") if row is not None else ""
        owner_generation = int(row.get("owner_generation") or 0) if row is not None else 0
        generation_matches = owner_generation == generation or (
            allow_cancel_successor
            and status == "cancel_requested"
            and owner_generation == generation + 1
        )
        if (
            row is None
            or not generation_matches
            or status not in allowed_statuses
            or (require_live_lease and row.get("lease_valid") is not True)
        ):
            raise KnowledgeError("knowledge_retrieval_fence_stale")
        return row

    async def _load_retrieval_attempt_for_update(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        generation: int,
        snapshot_hash: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select knowledge_retrieval_attempts.*,
                   clock_timestamp() as server_now
            from knowledge_retrieval_attempts
            where tenant_id = %s and run_id = %s and attempt_id = %s
              and generation = %s and snapshot_hash = %s
            for update
            """,
            (tenant_id, run_id, attempt_id, generation, snapshot_hash),
        )
        return await cursor.fetchone()

    async def _commit_preempting_terminal(
        self,
        conn: Any,
        *,
        attempt: dict[str, Any],
        result_count: int,
        provider_retry_count: int,
        duration_ms: int,
    ) -> dict[str, Any] | None:
        outcome = _preempting_terminal_outcome(attempt)
        if outcome is None:
            return None
        status, safe_failure_code = outcome
        terminal_digest = _canonical_digest(
            {
                "duration_ms": duration_ms,
                "evidence_count": 0,
                "provider_retry_count": provider_retry_count,
                "result_count": result_count,
                "safe_failure_code": safe_failure_code,
                "source_count": int(attempt["source_count"]),
                "status": status,
            }
        )
        cursor = await conn.execute(
            """
            update knowledge_retrieval_attempts
            set status = %s, result_count = %s, evidence_count = 0,
                provider_retry_count = %s, duration_ms = %s,
                safe_failure_code = %s, terminal_digest = %s,
                completed_at = clock_timestamp(), updated_at = clock_timestamp()
            where id = %s and tenant_id = %s and run_id = %s
              and generation = %s and snapshot_hash = %s
              and status = 'retrieving'
            returning *
            """,
            (
                status,
                result_count,
                provider_retry_count,
                duration_ms,
                safe_failure_code,
                terminal_digest,
                attempt["id"],
                attempt["tenant_id"],
                attempt["run_id"],
                attempt["generation"],
                attempt["snapshot_hash"],
            ),
        )
        terminal = await cursor.fetchone()
        if terminal is None:
            raise KnowledgeError("knowledge_retrieval_terminal_conflict")
        return terminal
