from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.knowledge.domain import (
    KnowledgeConnectionDefinition,
    KnowledgeError,
    ProviderSourceRecord,
    default_retrieval_profile_projection,
)
from app.knowledge.infrastructure.postgres import PostgresKnowledgeRepository
from app.platform.credentials import PlatformCredentialVault


POSTGRES_DSN_ENV = "AI_PLATFORM_AGENT_PROFILE_TEST_DSN"


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(
        sql.SQL("set search_path to {}, public").format(sql.Identifier(schema_name))
    )


@pytest.mark.asyncio
async def test_catalog_commit_locks_sources_before_connection() -> None:
    class StopAfterLockOrderObserved(Exception):
        pass

    class SourceCursor:
        async def fetchall(self) -> list[dict[str, object]]:
            return []

    class Connection:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def execute(self, query: str, _params: object) -> SourceCursor:
            normalized = " ".join(query.split())
            self.queries.append(normalized)
            if len(self.queries) == 1:
                return SourceCursor()
            raise StopAfterLockOrderObserved

    conn = Connection()
    with pytest.raises(StopAfterLockOrderObserved):
        await PostgresKnowledgeRepository().commit_catalog(
            conn,
            tenant_id="tenant-a",
            connection_id="kconn-a",
            revision_id="krev-a",
            purpose="candidate_activation",
            operation_id="operation-a",
            sync_id="ksync-a",
            lease_owner="worker-a",
            lease_generation=1,
            actor_id="admin-a",
            records=(),
            page_count=1,
        )

    assert len(conn.queries) == 2
    assert "from knowledge_sources" in conn.queries[0]
    assert "order by id for update" in conn.queries[0]
    assert "from knowledge_connections" in conn.queries[1]
    assert "for update" in conn.queries[1]


@pytest.mark.asyncio
async def test_knowledge_catalog_commit_is_fenced_idempotent_and_defaults_restricted() -> None:
    dsn = _postgres_dsn()
    schema_name = f"knowledge_catalog_{uuid.uuid4().hex}"
    schema_text = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    repository = PostgresKnowledgeRepository()
    vault = PlatformCredentialVault(
        settings_provider=lambda: SimpleNamespace(
            platform_credentials_encryption_key=base64.b64encode(b"k" * 32).decode("ascii")
        )
    )
    try:
        await conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_text)
        await conn.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await conn.execute(
            "insert into users(id, tenant_id, display_name) values ('admin-a', 'tenant-a', 'Admin A')"
        )

        create_operation = str(uuid.uuid4())
        async with conn.transaction():
            stored = await vault.store(
                conn,
                tenant_id="tenant-a",
                purpose="knowledge_provider",
                value="provider-secret",
                actor_id="admin-a",
            )
            connection = await repository.create_connection(
                conn,
                tenant_id="tenant-a",
                name="企业制度库",
                definition=KnowledgeConnectionDefinition(
                    provider_key="ragflow",
                    base_url="https://ragflow.example",
                    secret_ref=stored.secret_ref,
                    transport_policy={"follow_redirects": False, "timeout_seconds": 3},
                ),
                actor_id="admin-a",
                operation_id=create_operation,
                request_hash="0" * 64,
            )
        assert connection["status"] == "draft"
        assert "provider-secret" not in str(connection)

        check_operation = str(uuid.uuid4())
        async with conn.transaction():
            check_claim = await repository.claim_connection_check(
                conn,
                tenant_id="tenant-a",
                connection_id=connection["id"],
                operation_id=check_operation,
                actor_id="admin-a",
                lease_owner="check-worker-a",
                lease_seconds=30,
            )
            await conn.execute(
                """
                update knowledge_connection_check_receipts
                set lease_expires_at = now() - interval '1 second'
                where tenant_id = %s and connection_id = %s and operation_id = %s
                """,
                ("tenant-a", connection["id"], check_operation),
            )
        with pytest.raises(KnowledgeError, match="knowledge_check_lease_stale"):
            async with conn.transaction():
                await repository.finish_connection_check(
                    conn,
                    tenant_id="tenant-a",
                    connection_id=connection["id"],
                    operation_id=check_operation,
                    revision_id=check_claim["revision"]["revision_id"],
                    lease_owner="check-worker-a",
                    lease_generation=1,
                    passed=True,
                    failure_code=None,
                )

        activation_operation = str(uuid.uuid4())
        async with conn.transaction():
            claim = await repository.claim_catalog_sync(
                conn,
                tenant_id="tenant-a",
                connection_id=connection["id"],
                purpose="candidate_activation",
                operation_id=activation_operation,
                actor_id="admin-a",
                lease_owner="worker-a",
                lease_seconds=30,
            )
        assert claim["claimed"] is True
        assert claim["sync"]["status"] == "enumerating"

        with pytest.raises(KnowledgeError, match="knowledge_sync_in_progress"):
            async with conn.transaction():
                await repository.claim_catalog_sync(
                    conn,
                    tenant_id="tenant-a",
                    connection_id=connection["id"],
                    purpose="candidate_activation",
                    operation_id=str(uuid.uuid4()),
                    actor_id="admin-a",
                    lease_owner="worker-b",
                    lease_seconds=30,
                )

        revision_id = claim["revision"]["revision_id"]
        async with conn.transaction():
            await conn.execute(
                """
                update knowledge_catalog_syncs
                set lease_expires_at = now() - interval '1 second'
                where tenant_id = %s and id = %s
                """,
                ("tenant-a", claim["sync"]["id"]),
            )
        with pytest.raises(KnowledgeError, match="knowledge_sync_lease_stale"):
            async with conn.transaction():
                await repository.commit_catalog(
                    conn,
                    tenant_id="tenant-a",
                    connection_id=connection["id"],
                    revision_id=revision_id,
                    purpose="candidate_activation",
                    operation_id=activation_operation,
                    sync_id=claim["sync"]["id"],
                    lease_owner="worker-a",
                    lease_generation=1,
                    actor_id="admin-a",
                    records=(),
                    page_count=1,
                )
        async with conn.transaction():
            await conn.execute(
                """
                update knowledge_catalog_syncs
                set lease_expires_at = now() + interval '30 seconds'
                where tenant_id = %s and id = %s
                """,
                ("tenant-a", claim["sync"]["id"]),
            )
        async with conn.transaction():
            await repository.record_check(
                conn,
                tenant_id="tenant-a",
                connection_id=connection["id"],
                revision_id=revision_id,
                passed=True,
                failure_code=None,
                cataloging=True,
            )
        async with conn.transaction():
            sync = await repository.commit_catalog(
                conn,
                tenant_id="tenant-a",
                connection_id=connection["id"],
                revision_id=revision_id,
                purpose="candidate_activation",
                operation_id=activation_operation,
                sync_id=claim["sync"]["id"],
                lease_owner="worker-a",
                lease_generation=1,
                actor_id="admin-a",
                records=(
                    ProviderSourceRecord("dataset-a", "制度库", {"document_count": 4}),
                    ProviderSourceRecord("dataset-b", "产品库", {"document_count": 2}),
                ),
                page_count=1,
            )
        assert sync["status"] == "succeeded"
        assert sync["observed_count"] == 2

        async with conn.transaction():
            replay = await repository.claim_catalog_sync(
                conn,
                tenant_id="tenant-a",
                connection_id=connection["id"],
                purpose="candidate_activation",
                operation_id=activation_operation,
                actor_id="admin-a",
                lease_owner="worker-replay",
                lease_seconds=30,
            )
            source_page = await repository.list_sources(
                conn,
                tenant_id="tenant-a",
                limit=20,
                cursor=None,
                query="",
                connection_id=connection["id"],
                status=None,
            )
        assert replay["claimed"] is False
        assert replay["sync"]["id"] == sync["id"]
        with pytest.raises(KnowledgeError, match="knowledge_operation_identity_reused"):
            async with conn.transaction():
                await repository.claim_catalog_sync(
                    conn,
                    tenant_id="tenant-a",
                    connection_id=connection["id"],
                    purpose="manual_active_refresh",
                    operation_id=activation_operation,
                    actor_id="admin-a",
                    lease_owner="worker-reused-operation",
                    lease_seconds=30,
                )
        assert {source["status"] for source in source_page["items"]} == {"pending_review"}
        assert {source["visibility"] for source in source_page["items"]} == {"restricted"}
        assert {source["connection_status"] for source in source_page["items"]} == {"active"}
        assert all(source["last_complete_sync_at"] for source in source_page["items"])

        source = source_page["items"][0]
        with pytest.raises(KnowledgeError, match="knowledge_source_acl_invalid"):
            async with conn.transaction():
                await repository.update_source(
                    conn,
                    tenant_id="tenant-a",
                    source_id=source["id"],
                    display_name_present=False,
                    display_name=None,
                    description_present=False,
                    description=None,
                    status="active",
                    operation_id=str(uuid.uuid4()),
                    request_hash="1" * 64,
                    actor_id="admin-a",
                )

        acl_operation = str(uuid.uuid4())
        async with conn.transaction():
            governed = await repository.replace_source_acl(
                conn,
                tenant_id="tenant-a",
                source_id=source["id"],
                expected_version=1,
                visibility="enterprise",
                department_ids=(),
                roles=(),
                user_ids=(),
                actor_id="admin-a",
                operation_id=acl_operation,
                content_hash="2" * 64,
            )
        assert governed["authorization_version"] == 2

        async with conn.transaction():
            replayed_acl = await repository.replace_source_acl(
                conn,
                tenant_id="tenant-a",
                source_id=source["id"],
                expected_version=1,
                visibility="enterprise",
                department_ids=(),
                roles=(),
                user_ids=(),
                actor_id="admin-a",
                operation_id=acl_operation,
                content_hash="2" * 64,
            )
            active = await repository.update_source(
                conn,
                tenant_id="tenant-a",
                source_id=source["id"],
                display_name_present=False,
                display_name=None,
                description_present=False,
                description=None,
                status="active",
                operation_id=str(uuid.uuid4()),
                request_hash="3" * 64,
                actor_id="admin-a",
            )
        assert replayed_acl["authorization_version"] == 2
        assert active["status"] == "active"

        retained_source = next(
            item for item in source_page["items"] if item["id"] != source["id"]
        )
        async with conn.transaction():
            await conn.execute(
                """
                update knowledge_retrieval_profiles
                set revision = 2, content_hash = repeat('2', 64)
                where id = 'krp_default' and revision = 1
                """
            )
            builder_catalog = await repository.list_builder_catalog(
                conn,
                tenant_id="tenant-a",
                limit=1,
                cursor=None,
                query="",
                selected_source_ids=[retained_source["id"]],
            )
        builder_sources = {item["id"]: item for item in builder_catalog["sources"]}
        assert builder_sources[source["id"]]["available"] is True
        assert builder_sources[retained_source["id"]]["available"] is False
        assert "provider_resource_id" not in builder_sources[source["id"]]
        assert builder_sources[source["id"]]["allowed_department_ids"] == []
        assert builder_sources[source["id"]]["allowed_roles"] == []
        assert builder_sources[source["id"]]["allowed_user_ids"] == []
        assert builder_catalog["limit"] == 1
        assert builder_catalog["retrieval_profiles"] == [
            {
                **default_retrieval_profile_projection(),
                "revision": 2,
                "content_hash": "2" * 64,
            }
        ]

        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """
                    insert into knowledge_source_acl_departments(
                      tenant_id, source_id, authorization_version, department_id
                    ) values (%s, %s, %s, '')
                    """,
                    ("tenant-a", source["id"], active["authorization_version"]),
                )

        await conn.execute(
            """
            insert into skills(id, name, version, executor_type)
            values ('knowledge-test-skill', 'Knowledge test', '1.0.0', 'claude_agent_sdk')
            on conflict (id) do nothing
            """
        )
        await conn.execute(
            """
            insert into agents(id, tenant_id, name, agent_type, default_skill_id)
            values ('agt_knowledge_constraint', 'tenant-a', 'Knowledge constraint',
                    'claude_agent_sdk', 'knowledge-test-skill')
            """
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """
                    insert into agent_profile_revisions(
                      tenant_id, agent_id, revision, status, revision_status,
                      name, instructions, model_id, skill_id, skill_version,
                      knowledge_source_ids, retrieval_profile_id, knowledge_bindings,
                      content_hash, avatar_ref, category, visibility,
                      allowed_department_ids, allowed_roles, allowed_user_ids, created_by
                    ) values (
                      'tenant-a', 'agt_knowledge_constraint', 1, 'draft', 'draft',
                      'Knowledge constraint', 'Use governed knowledge.', 'profile-managed',
                      'knowledge-test-skill', '1.0.0', %s::jsonb, 'krp_default', %s::jsonb,
                      %s, 'builtin:agent', 'general', 'tenant',
                      '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'admin-a'
                    )
                    """,
                    (
                        json.dumps([source["id"]]),
                        json.dumps(
                            [
                                {
                                    "source_id": "ksrc_wrong",
                                    "source_authorization_version": 2,
                                    "ordinal": 0,
                                    "required": True,
                                    "retrieval_profile_id": "krp_default",
                                    "retrieval_profile_revision": 1,
                                }
                            ]
                        ),
                        "4" * 64,
                    ),
                )
    finally:
        await conn.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name))
        )
        await conn.close()
