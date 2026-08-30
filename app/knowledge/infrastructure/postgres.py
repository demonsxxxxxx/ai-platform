"""PostgreSQL authority for Knowledge connections and logical sources."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from app.knowledge.domain import (
    KnowledgeConnectionDefinition,
    KnowledgeError,
    ProviderSourceRecord,
    default_retrieval_profile_projection,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _canonical_digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _encode_cursor(*, label: str, item_id: str) -> str:
    payload = json.dumps(
        {"v": 1, "label": label, "id": item_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
    except Exception as exc:
        raise KnowledgeError("knowledge_cursor_invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or not isinstance(payload.get("label"), str)
        or not isinstance(payload.get("id"), str)
        or len(payload["label"]) > 240
        or len(payload["id"]) > 160
    ):
        raise KnowledgeError("knowledge_cursor_invalid")
    return payload["label"], payload["id"]


def _connection_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "provider_key": str(row["provider_key"]),
        "base_url": str(row.get("base_url") or ""),
        "status": str(row["status"]),
        "lifecycle_epoch": int(row.get("lifecycle_epoch") or 0),
        "credential_state": "configured",
        "credential_fingerprint": str(row.get("key_fingerprint") or ""),
        "candidate_revision_id": str(row.get("candidate_revision_id") or "") or None,
        "active_revision_id": str(row.get("active_revision_id") or "") or None,
        "active_catalog_sync_id": str(row.get("active_catalog_sync_id") or "") or None,
        "last_authenticated_check_at": _iso(row.get("last_authenticated_check_at")),
        "last_complete_sync_at": _iso(row.get("last_complete_sync_at")),
        "safe_failure_code": str(row.get("safe_failure_code") or "") or None,
        "source_count": int(row.get("source_count") or 0),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _source_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "connection_id": str(row["connection_id"]),
        "connection_name": str(row.get("connection_name") or ""),
        "name": str(row.get("display_name") or row["provider_name"]),
        "provider_name": str(row["provider_name"]),
        "description": str(row.get("description") or ""),
        "status": str(row["status"]),
        "authorization_version": int(row.get("authorization_version") or 1),
        "visibility": str(row.get("visibility") or "enterprise"),
        "allowed_department_ids": list(row.get("allowed_department_ids") or []),
        "allowed_roles": list(row.get("allowed_roles") or []),
        "allowed_user_ids": list(row.get("allowed_user_ids") or []),
        "first_seen_at": _iso(row.get("first_seen_at")),
        "last_seen_at": _iso(row.get("last_seen_at")),
        "last_complete_sync_at": _iso(row.get("last_complete_sync_at")),
        "connection_status": str(row.get("connection_status") or ""),
    }


def _builder_source_projection(row: dict[str, Any]) -> dict[str, Any]:
    source_status = str(row.get("status") or "")
    connection_status = str(row.get("connection_status") or "")
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "description": str(row.get("description") or ""),
        "authorization_version": int(row["authorization_version"]),
        "connection_name": str(row["connection_name"]),
        "last_seen_at": _iso(row.get("last_seen_at")),
        "available": source_status == "active" and connection_status == "active",
        "source_status": source_status,
        "connection_status": connection_status,
        "visibility": str(row.get("visibility") or "restricted"),
        "allowed_department_count": int(row.get("allowed_department_count") or 0),
        "allowed_department_ids": list(row.get("allowed_department_ids") or []),
        "allowed_roles": list(row.get("allowed_roles") or []),
        "allowed_user_ids": list(row.get("allowed_user_ids") or []),
    }


_CONNECTION_SELECT = """
select connections.id, connections.name, connections.provider_key, connections.status,
       connections.active_revision_id, connections.active_catalog_sync_id,
       connections.candidate_revision_id, connections.lifecycle_epoch,
       connections.last_authenticated_check_at, connections.last_complete_sync_at,
       connections.safe_failure_code, connections.create_request_hash,
       connections.created_at, connections.updated_at,
       coalesce(candidate.base_url, active.base_url, '') as base_url,
       coalesce(candidate_secret.fingerprint, active_secret.fingerprint, '') as key_fingerprint,
       (
         select count(*) from knowledge_sources sources
         where sources.tenant_id = connections.tenant_id
           and sources.connection_id = connections.id
           and sources.status <> 'missing'
       ) as source_count
from knowledge_connections connections
left join knowledge_connection_revisions candidate
  on candidate.tenant_id = connections.tenant_id
 and candidate.id = connections.candidate_revision_id
left join platform_secret_records candidate_secret
  on candidate_secret.tenant_id = connections.tenant_id
 and candidate_secret.id = candidate.secret_ref
left join knowledge_connection_revisions active
  on active.tenant_id = connections.tenant_id
 and active.id = connections.active_revision_id
left join platform_secret_records active_secret
  on active_secret.tenant_id = connections.tenant_id
 and active_secret.id = active.secret_ref
"""

_SYNC_SELECT = """
select id, connection_id, connection_revision_id, status, purpose,
       lease_owner, lease_generation, lease_expires_at,
       observed_count, page_count, safe_failure_code,
       requested_at, started_at, completed_at
from knowledge_catalog_syncs
"""


class PostgresKnowledgeRepository:
    async def get_connection_by_create_operation(
        self,
        conn: Any,
        *,
        tenant_id: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            f"{_CONNECTION_SELECT} where connections.tenant_id = %s "
            "and connections.create_operation_id = %s",
            (tenant_id, operation_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result = _connection_projection(row)
        result["_create_request_hash"] = str(row["create_request_hash"])
        return result

    async def create_connection(
        self,
        conn: Any,
        *,
        tenant_id: str,
        name: str,
        definition: KnowledgeConnectionDefinition,
        actor_id: str,
        operation_id: str,
        request_hash: str,
    ) -> dict[str, Any]:
        connection_id = _new_id("knc")
        revision_id = _new_id("knr")
        try:
            await conn.execute(
                """
                insert into knowledge_connections(
                  id, tenant_id, name, provider_key, status, lifecycle_epoch,
                  create_operation_id, create_request_hash, created_by
                ) values (%s, %s, %s, %s, 'draft', 0, %s, %s, %s)
                """,
                (
                    connection_id,
                    tenant_id,
                    name,
                    definition.provider_key,
                    operation_id,
                    request_hash,
                    actor_id,
                ),
            )
            await conn.execute(
                """
                insert into knowledge_connection_revisions(
                  id, tenant_id, connection_id, revision, provider_key, base_url,
                  secret_ref, operation_id, transport_policy_json, content_hash,
                  check_status, created_by
                ) values (%s, %s, %s, 1, %s, %s, %s, %s, %s::jsonb, %s, 'pending', %s)
                """,
                (
                    revision_id,
                    tenant_id,
                    connection_id,
                    definition.provider_key,
                    definition.base_url,
                    definition.secret_ref,
                    operation_id,
                    json.dumps(definition.transport_policy, separators=(",", ":")),
                    definition.content_hash(),
                    actor_id,
                ),
            )
            await conn.execute(
                """
                update knowledge_connections
                set candidate_revision_id = %s, updated_at = now()
                where tenant_id = %s and id = %s
                """,
                (revision_id, tenant_id, connection_id),
            )
        except Exception as exc:
            if getattr(exc, "sqlstate", "") == "23505":
                raise KnowledgeError("knowledge_connection_conflict") from exc
            raise
        return await self.get_connection(conn, tenant_id=tenant_id, connection_id=connection_id)

    async def lock_connection_for_rotation(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        operation_id: str,
        credential_fingerprint: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select connections.provider_key,
                   coalesce(candidate.base_url, active.base_url, '') as base_url,
                   replay.id is not null as replayed,
                   replay_secret.fingerprint as replayed_fingerprint
            from knowledge_connections connections
            left join knowledge_connection_revisions candidate
              on candidate.tenant_id = connections.tenant_id
             and candidate.id = connections.candidate_revision_id
            left join knowledge_connection_revisions active
              on active.tenant_id = connections.tenant_id
             and active.id = connections.active_revision_id
            left join knowledge_connection_revisions replay
              on replay.tenant_id = connections.tenant_id
             and replay.connection_id = connections.id
             and replay.operation_id = %s
            left join platform_secret_records replay_secret
              on replay_secret.tenant_id = replay.tenant_id
             and replay_secret.id = replay.secret_ref
            where connections.tenant_id = %s and connections.id = %s
            for update of connections
            """,
            (operation_id, tenant_id, connection_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if bool(row["replayed"]) and str(row.get("replayed_fingerprint") or "") != (
            credential_fingerprint
        ):
            raise KnowledgeError("knowledge_operation_identity_reused")
        return {
            "provider_key": str(row["provider_key"]),
            "base_url": str(row["base_url"]),
            "replayed": bool(row["replayed"]),
        }

    async def rotate_candidate(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        definition: KnowledgeConnectionDefinition,
        actor_id: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select provider_key
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        connection = await cursor.fetchone()
        if connection is None:
            return None
        if str(connection["provider_key"]) != definition.provider_key:
            raise KnowledgeError("knowledge_connection_provider_change_forbidden")
        cursor = await conn.execute(
            """
            select coalesce(max(revision), 0) + 1 as revision
            from knowledge_connection_revisions
            where tenant_id = %s and connection_id = %s
            """,
            (tenant_id, connection_id),
        )
        revision = int((await cursor.fetchone())["revision"])
        revision_id = _new_id("knr")
        await conn.execute(
            """
            insert into knowledge_connection_revisions(
              id, tenant_id, connection_id, revision, provider_key, base_url,
              secret_ref, operation_id, transport_policy_json, content_hash,
              check_status, created_by
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'pending', %s)
            """,
            (
                revision_id,
                tenant_id,
                connection_id,
                revision,
                definition.provider_key,
                definition.base_url,
                definition.secret_ref,
                operation_id,
                json.dumps(definition.transport_policy, separators=(",", ":")),
                definition.content_hash(),
                actor_id,
            ),
        )
        await conn.execute(
            """
            update knowledge_connections
            set candidate_revision_id = %s, safe_failure_code = null, updated_at = now(),
                status = case when active_revision_id is null then 'draft' else status end
            where tenant_id = %s and id = %s
            """,
            (revision_id, tenant_id, connection_id),
        )
        return await self.get_connection(conn, tenant_id=tenant_id, connection_id=connection_id)

    async def get_connection(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            f"{_CONNECTION_SELECT} where connections.tenant_id = %s and connections.id = %s",
            (tenant_id, connection_id),
        )
        row = await cursor.fetchone()
        return _connection_projection(row) if row is not None else None

    async def list_connections(
        self,
        conn: Any,
        *,
        tenant_id: str,
        limit: int,
        cursor: str | None,
        query: str,
    ) -> dict[str, Any]:
        after = _decode_cursor(cursor)
        clauses = ["connections.tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if query:
            clauses.append("connections.name ilike %s")
            params.append(f"%{query}%")
        if after:
            clauses.append("(lower(connections.name), connections.id) > (%s, %s)")
            params.extend(after)
        params.append(limit + 1)
        cursor_result = await conn.execute(
            f"{_CONNECTION_SELECT} where {' and '.join(clauses)} "
            "order by lower(connections.name), connections.id limit %s",
            tuple(params),
        )
        rows = list(await cursor_result.fetchall())
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(label=str(last["name"]).lower(), item_id=str(last["id"]))
        return {
            "items": [_connection_projection(row) for row in visible],
            "next_cursor": next_cursor,
            "limit": limit,
        }

    async def record_check(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        revision_id: str,
        passed: bool,
        failure_code: str | None,
        cataloging: bool = False,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select candidate_revision_id, active_revision_id
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        row = await cursor.fetchone()
        if row is None or str(row.get("candidate_revision_id") or "") != revision_id:
            raise KnowledgeError("knowledge_connection_candidate_stale")
        await conn.execute(
            """
            update knowledge_connection_revisions
            set checked_at = now(), check_status = %s
            where tenant_id = %s and connection_id = %s and id = %s
            """,
            ("passed" if passed else "failed", tenant_id, connection_id, revision_id),
        )
        await conn.execute(
            """
            update knowledge_connections
            set last_authenticated_check_at = now(), safe_failure_code = %s,
                status = case
                  when active_revision_id is not null then status
                  when %s and %s then 'cataloging'
                  when %s then 'draft'
                  else 'unavailable'
                end,
                updated_at = now()
            where tenant_id = %s and id = %s
            """,
            (failure_code, passed, cataloging, passed, tenant_id, connection_id),
        )
        return await self.get_connection(conn, tenant_id=tenant_id, connection_id=connection_id)

    async def claim_connection_check(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        operation_id: str,
        actor_id: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        cursor = await conn.execute(
            """
            select candidate_revision_id
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        connection = await cursor.fetchone()
        if connection is None:
            raise KnowledgeError("knowledge_connection_not_found")
        await conn.execute(
            """
            update knowledge_connection_check_receipts
            set status = 'reconcile_required', lease_owner = null,
                lease_expires_at = null,
                safe_failure_code = 'knowledge_check_lease_expired',
                completed_at = now()
            where tenant_id = %s and connection_id = %s and status = 'checking'
              and lease_expires_at <= now()
            """,
            (tenant_id, connection_id),
        )
        existing_cursor = await conn.execute(
            """
            select connection_revision_id, status, safe_failure_code
            from knowledge_connection_check_receipts
            where tenant_id = %s and connection_id = %s and operation_id = %s
            """,
            (tenant_id, connection_id, operation_id),
        )
        existing = await existing_cursor.fetchone()
        if existing is not None:
            return {
                "claimed": False,
                "status": str(existing["status"]),
                "safe_failure_code": str(existing.get("safe_failure_code") or "") or None,
            }
        active_check = await conn.execute(
            """
            select 1 from knowledge_connection_check_receipts
            where tenant_id = %s and connection_id = %s and status = 'checking'
            limit 1
            """,
            (tenant_id, connection_id),
        )
        if await active_check.fetchone() is not None:
            raise KnowledgeError("knowledge_check_in_progress")
        revision_id = str(connection.get("candidate_revision_id") or "")
        if not revision_id:
            raise KnowledgeError("knowledge_connection_candidate_not_found")
        revision_cursor = await conn.execute(
            """
            select id as revision_id, provider_key, base_url, secret_ref, revision, check_status
            from knowledge_connection_revisions
            where tenant_id = %s and connection_id = %s and id = %s
            """,
            (tenant_id, connection_id, revision_id),
        )
        revision = await revision_cursor.fetchone()
        if revision is None:
            raise KnowledgeError("knowledge_connection_revision_stale")
        await conn.execute(
            """
            insert into knowledge_connection_check_receipts(
              tenant_id, connection_id, connection_revision_id, operation_id,
              status, lease_owner, lease_generation, lease_expires_at, requested_by
            ) values (
              %s, %s, %s, %s, 'checking', %s, 1,
              now() + (%s * interval '1 second'), %s
            )
            """,
            (
                tenant_id,
                connection_id,
                revision_id,
                operation_id,
                lease_owner,
                lease_seconds,
                actor_id,
            ),
        )
        return {
            "claimed": True,
            "revision": revision,
            "lease_owner": lease_owner,
            "lease_generation": 1,
        }

    async def finish_connection_check(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        operation_id: str,
        revision_id: str,
        lease_owner: str,
        lease_generation: int,
        passed: bool,
        failure_code: str | None,
    ) -> dict[str, Any]:
        # Every transaction that touches both records takes the connection lock
        # before the child receipt lock.  Keep this order aligned with claim and
        # reconciliation paths so concurrent completion cannot deadlock them.
        connection_cursor = await conn.execute(
            """
            select 1
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        if await connection_cursor.fetchone() is None:
            raise KnowledgeError("knowledge_check_lease_stale")
        cursor = await conn.execute(
            """
            select receipts.status, receipts.connection_revision_id,
                   receipts.lease_owner, receipts.lease_generation,
                   receipts.lease_expires_at > now() as lease_valid
            from knowledge_connection_check_receipts receipts
            where receipts.tenant_id = %s and receipts.connection_id = %s
              and receipts.operation_id = %s
            for update
            """,
            (tenant_id, connection_id, operation_id),
        )
        receipt = await cursor.fetchone()
        if (
            receipt is None
            or str(receipt["status"]) != "checking"
            or str(receipt["connection_revision_id"]) != revision_id
            or str(receipt.get("lease_owner") or "") != lease_owner
            or int(receipt.get("lease_generation") or 0) != lease_generation
            or receipt.get("lease_valid") is not True
        ):
            raise KnowledgeError("knowledge_check_lease_stale")
        connection = await self.record_check(
            conn,
            tenant_id=tenant_id,
            connection_id=connection_id,
            revision_id=revision_id,
            passed=passed,
            failure_code=failure_code,
        )
        await conn.execute(
            """
            update knowledge_connection_check_receipts
            set status = %s, safe_failure_code = %s, lease_owner = null,
                lease_expires_at = null, completed_at = now()
            where tenant_id = %s and connection_id = %s and operation_id = %s
            """,
            (
                "passed" if passed else "failed",
                failure_code,
                tenant_id,
                connection_id,
                operation_id,
            ),
        )
        return {"status": "passed" if passed else "failed", "connection": connection}

    async def claim_catalog_sync(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        purpose: str,
        operation_id: str,
        actor_id: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        cursor = await conn.execute(
            """
            select status, lifecycle_epoch, active_revision_id, candidate_revision_id
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        connection = await cursor.fetchone()
        if connection is None:
            raise KnowledgeError("knowledge_connection_not_found")
        if purpose not in {"manual_active_refresh", "candidate_activation"}:
            raise KnowledgeError("knowledge_sync_purpose_invalid")
        await conn.execute(
            """
            update knowledge_catalog_syncs
            set status = 'reconcile_required', lease_owner = null,
                lease_expires_at = null,
                safe_failure_code = 'knowledge_sync_lease_expired',
                completed_at = now()
            where tenant_id = %s and connection_id = %s
              and status in ('requested', 'enumerating', 'committing')
              and lease_expires_at <= now()
            """,
            (tenant_id, connection_id),
        )
        existing_cursor = await conn.execute(
            f"{_SYNC_SELECT} where tenant_id = %s and connection_id = %s and operation_id = %s",
            (tenant_id, connection_id, operation_id),
        )
        existing = await existing_cursor.fetchone()
        if existing is not None:
            if str(existing.get("purpose") or "") != purpose:
                raise KnowledgeError("knowledge_operation_identity_reused")
            return {
                "claimed": False,
                "sync": self._sync_projection(existing),
            }
        active_cursor = await conn.execute(
            """
            select id
            from knowledge_catalog_syncs
            where tenant_id = %s and connection_id = %s
              and status in ('requested', 'enumerating', 'committing')
            limit 1
            """,
            (tenant_id, connection_id),
        )
        if await active_cursor.fetchone() is not None:
            raise KnowledgeError("knowledge_sync_in_progress")
        if purpose == "manual_active_refresh":
            if str(connection["status"]) != "active" or not connection.get(
                "active_revision_id"
            ):
                raise KnowledgeError("knowledge_connection_not_active")
            revision_id = str(connection["active_revision_id"])
        else:
            if not connection.get("candidate_revision_id"):
                raise KnowledgeError("knowledge_connection_candidate_not_found")
            revision_id = str(connection["candidate_revision_id"])
        revision_cursor = await conn.execute(
            """
            select id as revision_id, provider_key, base_url, secret_ref, revision, check_status
            from knowledge_connection_revisions
            where tenant_id = %s and connection_id = %s and id = %s
            """,
            (tenant_id, connection_id, revision_id),
        )
        revision = await revision_cursor.fetchone()
        if revision is None:
            raise KnowledgeError("knowledge_connection_revision_stale")
        sync_id = _new_id("kns")
        await conn.execute(
            """
            insert into knowledge_catalog_syncs(
              id, tenant_id, connection_id, connection_revision_id, operation_id,
              requested_by, purpose, status, lease_owner, lease_generation,
              lease_expires_at, requested_at, started_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, 'enumerating', %s, 1,
              now() + (%s * interval '1 second'), now(), now()
            )
            """,
            (
                sync_id,
                tenant_id,
                connection_id,
                revision_id,
                operation_id,
                actor_id,
                purpose,
                lease_owner,
                lease_seconds,
            ),
        )
        sync_cursor = await conn.execute(
            f"{_SYNC_SELECT} where tenant_id = %s and id = %s",
            (tenant_id, sync_id),
        )
        return {
            "claimed": True,
            "sync": self._sync_projection(await sync_cursor.fetchone()),
            "revision": revision,
            "lease_owner": lease_owner,
            "lease_generation": 1,
        }

    async def commit_catalog(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        revision_id: str,
        purpose: str,
        operation_id: str,
        sync_id: str,
        lease_owner: str,
        lease_generation: int,
        actor_id: str,
        records: tuple[ProviderSourceRecord, ...],
        page_count: int,
    ) -> dict[str, Any]:
        # Source -> connection -> sync is the repository-wide authority lock
        # order.  Catalog replacement can mark any existing source missing, so
        # it locks the complete current catalog by stable source ID before the
        # connection epoch.  Separate selects keep the order explicit; a
        # multi-table FOR UPDATE does not guarantee which row PostgreSQL locks
        # first.
        source_cursor = await conn.execute(
            """
            select id
            from knowledge_sources
            where tenant_id = %s and connection_id = %s
            order by id
            for update
            """,
            (tenant_id, connection_id),
        )
        await source_cursor.fetchall()
        connection_cursor = await conn.execute(
            """
            select active_revision_id, candidate_revision_id, status, lifecycle_epoch
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        connection = await connection_cursor.fetchone()
        sync_cursor = await conn.execute(
            """
            select status as sync_status, connection_revision_id, purpose,
                   lease_owner, lease_generation,
                   lease_expires_at > now() as lease_valid
            from knowledge_catalog_syncs
            where tenant_id = %s and connection_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id, sync_id),
        )
        sync = await sync_cursor.fetchone()
        if (
            connection is None
            or sync is None
            or str(sync["sync_status"]) != "enumerating"
            or str(sync.get("connection_revision_id") or "") != revision_id
            or str(sync.get("purpose") or "") != purpose
            or str(sync.get("lease_owner") or "") != lease_owner
            or int(sync.get("lease_generation") or 0) != lease_generation
            or sync.get("lease_valid") is not True
        ):
            raise KnowledgeError("knowledge_sync_lease_stale")
        expected_pointer = (
            connection.get("candidate_revision_id")
            if purpose == "candidate_activation"
            else connection.get("active_revision_id")
        )
        if str(expected_pointer or "") != revision_id:
            raise KnowledgeError("knowledge_connection_revision_stale")
        candidate_digest = hashlib.sha256(
            "\n".join(sorted(record.digest() for record in records)).encode("ascii")
        ).hexdigest()
        await conn.execute(
            """
            update knowledge_catalog_syncs
            set status = 'committing', observed_count = %s, page_count = %s,
                candidate_digest = %s
            where tenant_id = %s and id = %s
            """,
            (len(records), page_count, candidate_digest, tenant_id, sync_id),
        )
        observed_ids: list[str] = []
        for record in records:
            observed_ids.append(record.provider_resource_id)
            await conn.execute(
                """
                insert into knowledge_catalog_sync_observations(
                  tenant_id, sync_id, lease_generation, provider_resource_id,
                  provider_name, provider_metadata_json, record_digest
                ) values (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    tenant_id,
                    sync_id,
                    lease_generation,
                    record.provider_resource_id,
                    record.provider_name,
                    json.dumps(record.provider_metadata, ensure_ascii=False, separators=(",", ":")),
                    record.digest(),
                ),
            )
            source_id = _new_id("ksrc")
            cursor = await conn.execute(
                """
                insert into knowledge_sources(
                  id, tenant_id, connection_id, provider_resource_id, provider_name,
                  provider_metadata_json, status, authorization_version,
                  first_seen_at, last_seen_at, last_complete_sync_id,
                  last_seen_connection_revision_id
                ) values (%s, %s, %s, %s, %s, %s::jsonb, 'pending_review', 1,
                          now(), now(), %s, %s)
                on conflict (tenant_id, connection_id, provider_resource_id) do update
                set provider_name = excluded.provider_name,
                    provider_metadata_json = excluded.provider_metadata_json,
                    status = case
                      when knowledge_sources.status = 'missing' then 'pending_review'
                      else knowledge_sources.status
                    end,
                    last_seen_at = now(),
                    last_complete_sync_id = excluded.last_complete_sync_id,
                    last_seen_connection_revision_id = excluded.last_seen_connection_revision_id,
                    updated_at = now()
                returning id
                """,
                (
                    source_id,
                    tenant_id,
                    connection_id,
                    record.provider_resource_id,
                    record.provider_name,
                    json.dumps(record.provider_metadata, ensure_ascii=False, separators=(",", ":")),
                    sync_id,
                    revision_id,
                ),
            )
            persisted = await cursor.fetchone()
            await conn.execute(
                """
                insert into knowledge_source_acl_versions(
                  tenant_id, source_id, authorization_version, visibility,
                  operation_id, content_hash, created_by
                ) values (%s, %s, 1, 'restricted', %s, %s, %s)
                on conflict (tenant_id, source_id, authorization_version) do nothing
                """,
                (
                    tenant_id,
                    str(persisted["id"]),
                    sync_id,
                    _canonical_digest(
                        {
                            "department_ids": [],
                            "roles": [],
                            "user_ids": [],
                            "visibility": "restricted",
                        }
                    ),
                    actor_id,
                ),
            )
        if observed_ids:
            await conn.execute(
                """
                update knowledge_sources
                set status = 'missing', updated_at = now()
                where tenant_id = %s and connection_id = %s
                  and provider_resource_id <> all(%s)
                  and status <> 'missing'
                """,
                (tenant_id, connection_id, observed_ids),
            )
        else:
            await conn.execute(
                """
                update knowledge_sources
                set status = 'missing', updated_at = now()
                where tenant_id = %s and connection_id = %s and status <> 'missing'
                """,
                (tenant_id, connection_id),
            )
        await conn.execute(
            """
            update knowledge_catalog_syncs
            set status = 'succeeded', lease_owner = null, lease_expires_at = null,
                completed_at = now()
            where tenant_id = %s and id = %s
            """,
            (tenant_id, sync_id),
        )
        await conn.execute(
            """
            delete from knowledge_catalog_sync_observations
            where tenant_id = %s and sync_id = %s and lease_generation = %s
            """,
            (tenant_id, sync_id, lease_generation),
        )
        next_epoch = int(connection.get("lifecycle_epoch") or 0) + 1
        await conn.execute(
            """
            update knowledge_connection_revisions
            set checked_at = now(), check_status = 'passed'
            where tenant_id = %s and id = %s
            """,
            (tenant_id, revision_id),
        )
        await conn.execute(
            """
            update knowledge_connections
            set status = 'active', active_revision_id = %s, active_catalog_sync_id = %s,
                candidate_revision_id = case when %s = 'candidate_activation' then null
                                             else candidate_revision_id end,
                lifecycle_epoch = %s, last_authenticated_check_at = now(),
                last_complete_sync_at = now(), safe_failure_code = null, updated_at = now()
            where tenant_id = %s and id = %s
            """,
            (revision_id, sync_id, purpose, next_epoch, tenant_id, connection_id),
        )
        await conn.execute(
            """
            insert into knowledge_connection_lifecycle_receipts(
              tenant_id, connection_id, lifecycle_epoch, state, active_revision_id,
              active_catalog_sync_id, operation_id, requested_by
            ) values (%s, %s, %s, 'active', %s, %s, %s, %s)
            """,
            (
                tenant_id,
                connection_id,
                next_epoch,
                revision_id,
                sync_id,
                operation_id,
                actor_id,
            ),
        )
        cursor = await conn.execute(
            f"{_SYNC_SELECT} where tenant_id = %s and id = %s",
            (tenant_id, sync_id),
        )
        return self._sync_projection(await cursor.fetchone())

    async def fail_catalog_sync(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        sync_id: str,
        lease_owner: str,
        lease_generation: int,
        failure_code: str,
    ) -> dict[str, Any]:
        connection_cursor = await conn.execute(
            """
            select active_revision_id
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        connection = await connection_cursor.fetchone()
        cursor = await conn.execute(
            """
            select status, lease_owner, lease_generation,
                   lease_expires_at > now() as lease_valid
            from knowledge_catalog_syncs
            where tenant_id = %s and connection_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id, sync_id),
        )
        row = await cursor.fetchone()
        if (
            connection is None
            or row is None
            or str(row["status"]) not in {"requested", "enumerating"}
            or str(row.get("lease_owner") or "") != lease_owner
            or int(row.get("lease_generation") or 0) != lease_generation
            or row.get("lease_valid") is not True
        ):
            raise KnowledgeError("knowledge_sync_lease_stale")
        await conn.execute(
            """
            update knowledge_catalog_syncs
            set status = 'failed', safe_failure_code = %s, lease_owner = null,
                lease_expires_at = null, completed_at = now()
            where tenant_id = %s and id = %s
            """,
            (failure_code, tenant_id, sync_id),
        )
        await conn.execute(
            """
            update knowledge_connections
            set safe_failure_code = %s,
                status = case when active_revision_id is null then 'unavailable' else status end,
                updated_at = now()
            where tenant_id = %s and id = %s
            """,
            (failure_code, tenant_id, connection_id),
        )
        result = await conn.execute(
            f"{_SYNC_SELECT} where tenant_id = %s and id = %s",
            (tenant_id, sync_id),
        )
        return self._sync_projection(await result.fetchone())

    async def get_sync(
        self,
        conn: Any,
        *,
        tenant_id: str,
        sync_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            f"{_SYNC_SELECT} where tenant_id = %s and id = %s",
            (tenant_id, sync_id),
        )
        row = await cursor.fetchone()
        return self._sync_projection(row) if row is not None else None

    @staticmethod
    def _sync_projection(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "connection_id": str(row.get("connection_id") or ""),
            "connection_revision_id": str(row.get("connection_revision_id") or ""),
            "status": str(row["status"]),
            "purpose": str(row.get("purpose") or ""),
            "observed_count": int(row.get("observed_count") or 0),
            "page_count": int(row.get("page_count") or 0),
            "safe_failure_code": str(row.get("safe_failure_code") or "") or None,
            "requested_at": _iso(row.get("requested_at")),
            "started_at": _iso(row.get("started_at")),
            "completed_at": _iso(row.get("completed_at")),
        }

    async def disable_connection(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        operation_id: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select status, lifecycle_epoch
            from knowledge_connections
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, connection_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        existing = await conn.execute(
            """
            select 1 from knowledge_connection_lifecycle_receipts
            where tenant_id = %s and connection_id = %s and operation_id = %s
            """,
            (tenant_id, connection_id, operation_id),
        )
        if await existing.fetchone() is not None or str(row["status"]) == "disabled":
            return await self.get_connection(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
            )
        next_epoch = int(row.get("lifecycle_epoch") or 0) + 1
        await conn.execute(
            """
            update knowledge_connections
            set status = 'disabled', active_revision_id = null,
                active_catalog_sync_id = null, lifecycle_epoch = %s, updated_at = now()
            where tenant_id = %s and id = %s
            """,
            (next_epoch, tenant_id, connection_id),
        )
        await conn.execute(
            """
            insert into knowledge_connection_lifecycle_receipts(
              tenant_id, connection_id, lifecycle_epoch, state, active_revision_id,
              active_catalog_sync_id, operation_id, requested_by
            ) values (%s, %s, %s, 'disabled', null, null, %s, %s)
            """,
            (tenant_id, connection_id, next_epoch, operation_id, actor_id),
        )
        return await self.get_connection(conn, tenant_id=tenant_id, connection_id=connection_id)

    async def list_sources(
        self,
        conn: Any,
        *,
        tenant_id: str,
        limit: int,
        cursor: str | None,
        query: str,
        connection_id: str | None,
        status: str | None,
    ) -> dict[str, Any]:
        after = _decode_cursor(cursor)
        clauses = ["sources.tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if query:
            clauses.append("coalesce(sources.display_name, sources.provider_name) ilike %s")
            params.append(f"%{query}%")
        if connection_id:
            clauses.append("sources.connection_id = %s")
            params.append(connection_id)
        if status:
            clauses.append("sources.status = %s")
            params.append(status)
        if after:
            clauses.append(
                "(lower(coalesce(sources.display_name, sources.provider_name)), sources.id) > (%s, %s)"
            )
            params.extend(after)
        params.append(limit + 1)
        cursor_result = await conn.execute(
            f"""
            select sources.*, connections.name as connection_name,
                   connections.status as connection_status,
                   connections.last_complete_sync_at,
                   acl.visibility,
                   coalesce(array(
                     select department_id from knowledge_source_acl_departments departments
                     where departments.tenant_id = sources.tenant_id
                       and departments.source_id = sources.id
                       and departments.authorization_version = sources.authorization_version
                     order by department_id
                   ), array[]::text[]) as allowed_department_ids,
                   coalesce(array(
                     select role_id from knowledge_source_acl_roles roles
                     where roles.tenant_id = sources.tenant_id
                       and roles.source_id = sources.id
                       and roles.authorization_version = sources.authorization_version
                     order by role_id
                   ), array[]::text[]) as allowed_roles,
                   coalesce(array(
                     select user_id from knowledge_source_acl_users users_acl
                     where users_acl.tenant_id = sources.tenant_id
                       and users_acl.source_id = sources.id
                       and users_acl.authorization_version = sources.authorization_version
                     order by user_id
                   ), array[]::text[]) as allowed_user_ids
            from knowledge_sources sources
            join knowledge_connections connections
              on connections.tenant_id = sources.tenant_id
             and connections.id = sources.connection_id
            join knowledge_source_acl_versions acl
              on acl.tenant_id = sources.tenant_id
             and acl.source_id = sources.id
             and acl.authorization_version = sources.authorization_version
            where {' and '.join(clauses)}
            order by lower(coalesce(sources.display_name, sources.provider_name)), sources.id
            limit %s
            """,
            tuple(params),
        )
        rows = list(await cursor_result.fetchall())
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            label = str(last.get("display_name") or last["provider_name"]).lower()
            next_cursor = _encode_cursor(label=label, item_id=str(last["id"]))
        return {
            "items": [_source_projection(row) for row in visible],
            "next_cursor": next_cursor,
            "limit": limit,
        }

    async def list_builder_catalog(
        self,
        conn: Any,
        *,
        tenant_id: str,
        limit: int,
        cursor: str | None,
        query: str,
        selected_source_ids: list[str],
    ) -> dict[str, Any]:
        after = _decode_cursor(cursor)
        clauses = [
            "sources.tenant_id = %s",
            "sources.status = 'active'",
            "connections.status = 'active'",
        ]
        params: list[Any] = [tenant_id]
        if query:
            clauses.append(
                "(coalesce(sources.display_name, sources.provider_name) ilike %s "
                "or coalesce(sources.description, '') ilike %s "
                "or connections.name ilike %s)"
            )
            pattern = f"%{query}%"
            params.extend((pattern, pattern, pattern))
        if after:
            clauses.append(
                "(lower(coalesce(sources.display_name, sources.provider_name)), sources.id) "
                "> (%s, %s)"
            )
            params.extend(after)
        params.append(limit + 1)
        page_cursor = await conn.execute(
            f"""
            select sources.id, coalesce(sources.display_name, sources.provider_name) as name,
                   coalesce(sources.description, '') as description,
                   sources.authorization_version, sources.last_seen_at,
                   sources.status, connections.status as connection_status,
                   connections.name as connection_name, acl.visibility,
                   coalesce(array(
                     select department_id from knowledge_source_acl_departments departments
                     where departments.tenant_id = sources.tenant_id
                       and departments.source_id = sources.id
                       and departments.authorization_version = sources.authorization_version
                     order by department_id
                   ), array[]::text[]) as allowed_department_ids,
                   coalesce(array(
                     select role_id from knowledge_source_acl_roles roles
                     where roles.tenant_id = sources.tenant_id
                       and roles.source_id = sources.id
                       and roles.authorization_version = sources.authorization_version
                     order by role_id
                   ), array[]::text[]) as allowed_roles,
                   coalesce(array(
                     select user_id from knowledge_source_acl_users users_acl
                     where users_acl.tenant_id = sources.tenant_id
                       and users_acl.source_id = sources.id
                       and users_acl.authorization_version = sources.authorization_version
                     order by user_id
                   ), array[]::text[]) as allowed_user_ids,
                   (
                     select count(*) from knowledge_source_acl_departments departments
                     where departments.tenant_id = sources.tenant_id
                       and departments.source_id = sources.id
                       and departments.authorization_version = sources.authorization_version
                   ) as allowed_department_count
            from knowledge_sources sources
            join knowledge_connections connections
              on connections.tenant_id = sources.tenant_id
             and connections.id = sources.connection_id
            join knowledge_source_acl_versions acl
              on acl.tenant_id = sources.tenant_id
             and acl.source_id = sources.id
             and acl.authorization_version = sources.authorization_version
            where {' and '.join(clauses)}
            order by lower(coalesce(sources.display_name, sources.provider_name)), sources.id
            limit %s
            """,
            tuple(params),
        )
        page_rows = list(await page_cursor.fetchall())
        has_more = len(page_rows) > limit
        visible_rows = page_rows[:limit]
        next_cursor = None
        if has_more and visible_rows:
            last = visible_rows[-1]
            next_cursor = _encode_cursor(
                label=str(last["name"]).lower(),
                item_id=str(last["id"]),
            )

        selected_rows: list[dict[str, Any]] = []
        if selected_source_ids:
            selected_cursor = await conn.execute(
                """
                select sources.id,
                       coalesce(sources.display_name, sources.provider_name) as name,
                       coalesce(sources.description, '') as description,
                       sources.authorization_version, sources.last_seen_at,
                       sources.status, connections.status as connection_status,
                       connections.name as connection_name, acl.visibility,
                       coalesce(array(
                         select department_id from knowledge_source_acl_departments departments
                         where departments.tenant_id = sources.tenant_id
                           and departments.source_id = sources.id
                           and departments.authorization_version = sources.authorization_version
                         order by department_id
                       ), array[]::text[]) as allowed_department_ids,
                       coalesce(array(
                         select role_id from knowledge_source_acl_roles roles
                         where roles.tenant_id = sources.tenant_id
                           and roles.source_id = sources.id
                           and roles.authorization_version = sources.authorization_version
                         order by role_id
                       ), array[]::text[]) as allowed_roles,
                       coalesce(array(
                         select user_id from knowledge_source_acl_users users_acl
                         where users_acl.tenant_id = sources.tenant_id
                           and users_acl.source_id = sources.id
                           and users_acl.authorization_version = sources.authorization_version
                         order by user_id
                       ), array[]::text[]) as allowed_user_ids,
                       (
                         select count(*) from knowledge_source_acl_departments departments
                         where departments.tenant_id = sources.tenant_id
                           and departments.source_id = sources.id
                           and departments.authorization_version = sources.authorization_version
                       ) as allowed_department_count
                from knowledge_sources sources
                join knowledge_connections connections
                  on connections.tenant_id = sources.tenant_id
                 and connections.id = sources.connection_id
                join knowledge_source_acl_versions acl
                  on acl.tenant_id = sources.tenant_id
                 and acl.source_id = sources.id
                 and acl.authorization_version = sources.authorization_version
                where sources.tenant_id = %s and sources.id = any(%s)
                """,
                (tenant_id, selected_source_ids),
            )
            selected_rows = list(await selected_cursor.fetchall())

        projected_by_id = {
            str(row["id"]): _builder_source_projection(row)
            for row in visible_rows
        }
        for row in selected_rows:
            projected_by_id[str(row["id"])] = _builder_source_projection(row)
        return {
            "sources": list(projected_by_id.values()),
            "next_cursor": next_cursor,
            "limit": limit,
            "retrieval_profiles": [default_retrieval_profile_projection()],
        }

    async def get_source(
        self,
        conn: Any,
        *,
        tenant_id: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select sources.*, connections.name as connection_name,
                   connections.status as connection_status,
                   connections.last_complete_sync_at, acl.visibility,
                   coalesce(array(
                     select department_id from knowledge_source_acl_departments departments
                     where departments.tenant_id = sources.tenant_id
                       and departments.source_id = sources.id
                       and departments.authorization_version = sources.authorization_version
                     order by department_id
                   ), array[]::text[]) as allowed_department_ids,
                   coalesce(array(
                     select role_id from knowledge_source_acl_roles roles
                     where roles.tenant_id = sources.tenant_id
                       and roles.source_id = sources.id
                       and roles.authorization_version = sources.authorization_version
                     order by role_id
                   ), array[]::text[]) as allowed_roles,
                   coalesce(array(
                     select user_id from knowledge_source_acl_users users_acl
                     where users_acl.tenant_id = sources.tenant_id
                       and users_acl.source_id = sources.id
                       and users_acl.authorization_version = sources.authorization_version
                     order by user_id
                   ), array[]::text[]) as allowed_user_ids
            from knowledge_sources sources
            join knowledge_connections connections
              on connections.tenant_id = sources.tenant_id
             and connections.id = sources.connection_id
            join knowledge_source_acl_versions acl
              on acl.tenant_id = sources.tenant_id
             and acl.source_id = sources.id
             and acl.authorization_version = sources.authorization_version
            where sources.tenant_id = %s and sources.id = %s
            """,
            (tenant_id, source_id),
        )
        row = await cursor.fetchone()
        return _source_projection(row) if row is not None else None

    async def update_source(
        self,
        conn: Any,
        *,
        tenant_id: str,
        source_id: str,
        display_name_present: bool,
        display_name: str | None,
        description_present: bool,
        description: str | None,
        status: str | None,
        operation_id: str,
        request_hash: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select sources.status, connections.status as connection_status,
                   acl.visibility,
                   exists (
                     select 1 from knowledge_source_acl_departments departments
                     where departments.tenant_id = sources.tenant_id
                       and departments.source_id = sources.id
                       and departments.authorization_version = sources.authorization_version
                   ) or exists (
                     select 1 from knowledge_source_acl_roles roles
                     where roles.tenant_id = sources.tenant_id
                       and roles.source_id = sources.id
                       and roles.authorization_version = sources.authorization_version
                   ) or exists (
                     select 1 from knowledge_source_acl_users users_acl
                     where users_acl.tenant_id = sources.tenant_id
                       and users_acl.source_id = sources.id
                       and users_acl.authorization_version = sources.authorization_version
                   ) as has_restricted_authority
            from knowledge_sources sources
            join knowledge_connections connections
              on connections.tenant_id = sources.tenant_id
             and connections.id = sources.connection_id
            join knowledge_source_acl_versions acl
              on acl.tenant_id = sources.tenant_id
             and acl.source_id = sources.id
             and acl.authorization_version = sources.authorization_version
            where sources.tenant_id = %s and sources.id = %s
            for update of sources
            """,
            (tenant_id, source_id),
        )
        current = await cursor.fetchone()
        if current is None:
            return None
        receipt_cursor = await conn.execute(
            """
            select request_hash from knowledge_source_update_receipts
            where tenant_id = %s and source_id = %s and operation_id = %s
            """,
            (tenant_id, source_id, operation_id),
        )
        receipt = await receipt_cursor.fetchone()
        if receipt is not None:
            if str(receipt["request_hash"]) != request_hash:
                raise KnowledgeError("knowledge_operation_identity_reused")
            return await self.get_source(conn, tenant_id=tenant_id, source_id=source_id)
        if status == "active":
            if str(current["status"]) == "missing":
                raise KnowledgeError("knowledge_source_missing")
            if str(current["connection_status"]) != "active":
                raise KnowledgeError("knowledge_source_connection_inactive")
            if (
                str(current["visibility"]) == "restricted"
                and not bool(current["has_restricted_authority"])
            ):
                raise KnowledgeError("knowledge_source_acl_invalid")
        cursor = await conn.execute(
            """
            update knowledge_sources
            set display_name = case when %s::boolean then %s else display_name end,
                description = case when %s::boolean then %s else description end,
                status = coalesce(%s, status), updated_at = now()
            where tenant_id = %s and id = %s
            returning id
            """,
            (
                display_name_present,
                display_name,
                description_present,
                description,
                status,
                tenant_id,
                source_id,
            ),
        )
        if await cursor.fetchone() is None:
            return None
        await conn.execute(
            """
            insert into knowledge_source_update_receipts(
              tenant_id, source_id, operation_id, request_hash, requested_by
            ) values (%s, %s, %s, %s, %s)
            """,
            (tenant_id, source_id, operation_id, request_hash, actor_id),
        )
        return await self.get_source(conn, tenant_id=tenant_id, source_id=source_id)

    async def replace_source_acl(
        self,
        conn: Any,
        *,
        tenant_id: str,
        source_id: str,
        expected_version: int,
        visibility: str,
        department_ids: tuple[str, ...],
        roles: tuple[str, ...],
        user_ids: tuple[str, ...],
        actor_id: str,
        operation_id: str,
        content_hash: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select authorization_version
            from knowledge_sources
            where tenant_id = %s and id = %s
            for update
            """,
            (tenant_id, source_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        receipt_cursor = await conn.execute(
            """
            select content_hash
            from knowledge_source_acl_versions
            where tenant_id = %s and source_id = %s and operation_id = %s
            """,
            (tenant_id, source_id, operation_id),
        )
        receipt = await receipt_cursor.fetchone()
        if receipt is not None:
            if str(receipt["content_hash"]) != content_hash:
                raise KnowledgeError("knowledge_operation_identity_reused")
            return await self.get_source(conn, tenant_id=tenant_id, source_id=source_id)
        if int(row["authorization_version"]) != expected_version:
            raise KnowledgeError("knowledge_source_acl_version_stale")
        version = expected_version + 1
        await conn.execute(
            """
            insert into knowledge_source_acl_versions(
              tenant_id, source_id, authorization_version, visibility,
              operation_id, content_hash, created_by
            ) values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                source_id,
                version,
                visibility,
                operation_id,
                content_hash,
                actor_id,
            ),
        )
        for table, column, values in (
            ("knowledge_source_acl_departments", "department_id", department_ids),
            ("knowledge_source_acl_roles", "role_id", roles),
            ("knowledge_source_acl_users", "user_id", user_ids),
        ):
            for value in values:
                await conn.execute(
                    f"""
                    insert into {table}(
                      tenant_id, source_id, authorization_version, {column}
                    ) values (%s, %s, %s, %s)
                    """,
                    (tenant_id, source_id, version, value),
                )
        await conn.execute(
            """
            update knowledge_sources
            set authorization_version = %s, updated_at = now()
            where tenant_id = %s and id = %s
            """,
            (version, tenant_id, source_id),
        )
        return await self.get_source(conn, tenant_id=tenant_id, source_id=source_id)

    async def get_source_acl_by_operation(
        self,
        conn: Any,
        *,
        tenant_id: str,
        source_id: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """
            select content_hash
            from knowledge_source_acl_versions
            where tenant_id = %s and source_id = %s and operation_id = %s
            """,
            (tenant_id, source_id, operation_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "content_hash": str(row["content_hash"]),
            "source": await self.get_source(
                conn,
                tenant_id=tenant_id,
                source_id=source_id,
            ),
        }
