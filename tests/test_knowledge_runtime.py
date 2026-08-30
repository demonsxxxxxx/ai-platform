from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.knowledge.domain import (
    KnowledgeError,
    KnowledgeEvidence,
    RunKnowledgeSnapshot,
    RunKnowledgeSourceSnapshot,
)
from app.knowledge.infrastructure.runtime_postgres import (
    PostgresKnowledgeRuntimeRepository,
    _preempting_terminal_outcome,
)


POSTGRES_DSN_ENV = "AI_PLATFORM_AGENT_PROFILE_TEST_DSN"


def _source(**overrides: object) -> RunKnowledgeSourceSnapshot:
    values: dict[str, object] = {
        "source_id": "ksrc_policy",
        "source_authorization_version": 1,
        "connection_id": "kconn_policy",
        "connection_revision_id": "krev_policy_1",
        "connection_revision": 1,
        "connection_catalog_sync_id": "ksync_policy_1",
        "connection_lifecycle_epoch": 1,
        "provider_resource_id": "dataset-policy",
        "ordinal": 0,
        "required": True,
    }
    values.update(overrides)
    return RunKnowledgeSourceSnapshot(**values)  # type: ignore[arg-type]


def _snapshot(**overrides: object) -> RunKnowledgeSnapshot:
    values: dict[str, object] = {
        "tenant_id": "tenant-knowledge-runtime",
        "run_id": "run-knowledge-runtime",
        "agent_id": "agent-knowledge-runtime",
        "profile_revision": 1,
        "profile_content_hash": "a" * 64,
        "retrieval_profile_id": "krp_default",
        "retrieval_profile_revision": 1,
        "sources": (_source(),),
        "principal_policy_version": 1,
    }
    values.update(overrides)
    return RunKnowledgeSnapshot(**values)  # type: ignore[arg-type]


def test_run_knowledge_snapshot_is_canonical_bounded_and_credential_free() -> None:
    first = _snapshot()
    second = _snapshot()

    assert first.content_hash() == second.content_hash()
    assert len(first.sources_canonical_json().encode("utf-8")) < 16_384
    source_payload = first.sources_projection()[0]
    assert tuple(source_payload) == (
        "connection_catalog_sync_id",
        "connection_id",
        "connection_lifecycle_epoch",
        "connection_revision",
        "connection_revision_id",
        "ordinal",
        "provider_resource_id",
        "required",
        "source_authorization_version",
        "source_id",
    )
    assert not (
        {"base_url", "credential", "query", "secret_ref", "content"}
        & source_payload.keys()
    )


def test_run_knowledge_snapshot_rejects_order_duplicates_and_ninth_source() -> None:
    with pytest.raises(KnowledgeError, match="knowledge_snapshot_invalid"):
        _snapshot(sources=(_source(ordinal=1),))
    with pytest.raises(KnowledgeError, match="knowledge_snapshot_invalid"):
        _snapshot(sources=(_source(), _source(ordinal=1)))
    with pytest.raises(KnowledgeError, match="knowledge_snapshot_invalid"):
        _snapshot(
            sources=tuple(
                _source(
                    source_id=f"ksrc_{index}",
                    provider_resource_id=f"dataset-{index}",
                    ordinal=index,
                )
                for index in range(9)
            )
        )


def test_knowledge_evidence_enforces_bytes_digest_score_and_position() -> None:
    evidence = KnowledgeEvidence(
        evidence_id="kev_1",
        source_id="ksrc_policy",
        provider_document_id="document-1",
        provider_chunk_id="chunk-1",
        title="制度说明",
        content="这是经过归一化的证据。",
        provider_score=0.87,
        fused_rank=1,
        position_json={"page": 2},
    )

    assert (
        evidence.content_sha256()
        == hashlib.sha256(evidence.content.encode("utf-8")).hexdigest()
    )
    assert json.loads(evidence.position_canonical_json()) == {"page": 2}
    with pytest.raises(KnowledgeError, match="knowledge_evidence_invalid"):
        KnowledgeEvidence(
            evidence_id="kev_2",
            source_id="ksrc_policy",
            provider_document_id="document-2",
            provider_chunk_id=None,
            title="",
            content="evidence",
            provider_score=math.inf,
            fused_rank=1,
            position_json={},
        )


def test_knowledge_evidence_rejects_malformed_identity_and_position_metadata() -> None:
    with pytest.raises(KnowledgeError, match="knowledge_runtime_identity_invalid"):
        KnowledgeEvidence(
            evidence_id=None,  # type: ignore[arg-type]
            source_id="ksrc_policy",
            provider_document_id="document-1",
            provider_chunk_id=None,
            title="Policy",
            content="Evidence",
            provider_score=0.8,
            fused_rank=1,
            position_json={},
        )
    with pytest.raises(KnowledgeError, match="knowledge_evidence_invalid"):
        KnowledgeEvidence(
            evidence_id="kev_2",
            source_id="ksrc_policy",
            provider_document_id="document-1",
            provider_chunk_id=None,
            title="Policy",
            content="Evidence",
            provider_score=0.8,
            fused_rank=1,
            position_json={"secret": "must-not-be-persisted"},
        )


def test_preempting_terminal_outcome_uses_persisted_cancel_before_deadline() -> None:
    deadline = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)
    server_now = deadline + timedelta(milliseconds=1)

    assert _preempting_terminal_outcome(
        {
            "cancel_requested_at": deadline - timedelta(milliseconds=1),
            "deadline_at": deadline,
            "server_now": server_now,
        }
    ) == ("cancelled", None)
    assert _preempting_terminal_outcome(
        {
            "cancel_requested_at": deadline + timedelta(milliseconds=1),
            "deadline_at": deadline,
            "server_now": server_now,
        }
    ) == ("failed", "knowledge_retrieval_timeout")


@pytest.mark.asyncio
async def test_terminalize_rejects_unknown_failure_code_before_database_io() -> None:
    repository = PostgresKnowledgeRuntimeRepository()

    with pytest.raises(KnowledgeError, match="knowledge_retrieval_terminal_invalid"):
        await repository.terminalize_retrieval_attempt(
            object(),
            tenant_id="tenant-knowledge-runtime",
            run_id="run-knowledge-runtime",
            attempt_id="attempt-knowledge-runtime",
            generation=4,
            snapshot_hash="a" * 64,
            status="failed",
            source_count=1,
            result_count=0,
            provider_retry_count=0,
            duration_ms=1,
            safe_failure_code="provider_internal_stacktrace",
        )


def _postgres_dsn() -> str:
    dsn = os.getenv(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} is not configured")
    return dsn


async def _set_search_path(conn: psycopg.AsyncConnection, schema_name: str) -> None:
    await conn.execute(
        sql.SQL("set search_path to {}, public").format(sql.Identifier(schema_name))
    )


def _execution_spec() -> tuple[str, str]:
    value = {
        "agent_id": "agent-knowledge-runtime",
        "execution_kind": "skill",
        "run_id": "run-knowledge-runtime",
        "schema_version": "ai-platform.execution-spec.v1",
        "session_id": "session-knowledge-runtime",
        "skill_id": "skill-knowledge-runtime",
        "tenant_id": "tenant-knowledge-runtime",
        "user_id": "user-knowledge-runtime",
        "workspace_id": "workspace-knowledge-runtime",
    }
    canonical = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _seed_runtime_authority(conn: psycopg.AsyncConnection) -> None:
    tenant_id = "tenant-knowledge-runtime"
    await conn.execute(
        "insert into tenants(id, name) values (%s, 'Knowledge Runtime')",
        (tenant_id,),
    )
    await conn.execute(
        "insert into workspaces(id, tenant_id, name) values "
        "('workspace-knowledge-runtime', %s, 'Knowledge Runtime')",
        (tenant_id,),
    )
    await conn.execute(
        "insert into users(id, tenant_id, display_name) values "
        "('user-knowledge-runtime', %s, 'Knowledge Runtime')",
        (tenant_id,),
    )
    await conn.execute(
        "insert into skills(id, name, version, executor_type) values "
        "('skill-knowledge-runtime', 'Knowledge Runtime', '1.0.0', 'claude_agent_sdk')"
    )
    await conn.execute(
        """
        insert into agents(id, tenant_id, name, agent_type, default_skill_id)
        values ('agent-knowledge-runtime', %s, 'Knowledge Runtime',
                'claude_agent_sdk', 'skill-knowledge-runtime')
        """,
        (tenant_id,),
    )
    bindings = [
        {
            "source_id": "ksrc_policy",
            "source_authorization_version": 1,
            "ordinal": 0,
            "required": True,
            "retrieval_profile_id": "krp_default",
            "retrieval_profile_revision": 1,
        }
    ]
    await conn.execute(
        """
        insert into agent_profile_revisions(
          tenant_id, agent_id, revision, status, revision_status, name,
          instructions, model_id, skill_id, skill_version, skill_set,
          knowledge_source_ids,
          retrieval_profile_id, knowledge_bindings, content_hash, avatar_ref,
          category, visibility, allowed_department_ids, allowed_roles,
          allowed_user_ids, created_by
        ) values (
          %s, 'agent-knowledge-runtime', 1, 'published', 'published',
          'Knowledge Runtime', 'Use admitted evidence.', 'profile-managed',
          'skill-knowledge-runtime', '1.0.0',
          '[{"skill_id":"skill-knowledge-runtime","expected_version":"1.0.0"}]'::jsonb,
          '["ksrc_policy"]'::jsonb,
          'krp_default', %s::jsonb, %s, 'builtin:agent', 'research', 'tenant',
          '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'user-knowledge-runtime'
        )
        """,
        (tenant_id, json.dumps(bindings), "a" * 64),
    )
    await conn.execute(
        """
        insert into sessions(
          id, tenant_id, workspace_id, user_id, agent_id,
          admitted_agent_profile_revision, admitted_agent_profile_hash
        ) values (
          'session-knowledge-runtime', %s, 'workspace-knowledge-runtime',
          'user-knowledge-runtime', 'agent-knowledge-runtime', 1, %s
        )
        """,
        (tenant_id, "a" * 64),
    )
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id,
          execution_kind, skill_id, status, authz_policy_version,
          admitted_agent_profile_revision, admitted_agent_profile_hash
        ) values (
          'run-knowledge-runtime', %s, 'workspace-knowledge-runtime',
          'session-knowledge-runtime', 'user-knowledge-runtime',
          'agent-knowledge-runtime', 'skill', 'skill-knowledge-runtime',
          'queued', 1, 1, %s
        )
        """,
        (tenant_id, "a" * 64),
    )
    await conn.execute(
        """
        insert into platform_secret_records(
          id, tenant_id, purpose, ciphertext, key_version, fingerprint, created_by
        ) values (
          'secret-knowledge-runtime', %s, 'knowledge_provider', decode('00', 'hex'),
          'v1', '0123456789abcdef', 'user-knowledge-runtime'
        )
        """,
        (tenant_id,),
    )
    await conn.execute(
        """
        insert into knowledge_connections(
          id, tenant_id, name, provider_key, status, lifecycle_epoch,
          create_operation_id, create_request_hash, created_by
        ) values (
          'kconn_policy', %s, 'Policy', 'ragflow', 'draft', 0,
          'operation-create-policy', %s, 'user-knowledge-runtime'
        )
        """,
        (tenant_id, "b" * 64),
    )
    await conn.execute(
        """
        insert into knowledge_connection_revisions(
          id, tenant_id, connection_id, revision, provider_key, base_url,
          secret_ref, operation_id, content_hash, check_status, created_by
        ) values (
          'krev_policy_1', %s, 'kconn_policy', 1, 'ragflow',
          'https://ragflow.internal', 'secret-knowledge-runtime',
          'operation-revision-policy', %s, 'passed', 'user-knowledge-runtime'
        )
        """,
        (tenant_id, "c" * 64),
    )
    await conn.execute(
        """
        insert into knowledge_catalog_syncs(
          id, tenant_id, connection_id, connection_revision_id, operation_id,
          requested_by, purpose, status, lease_generation, observed_count,
          page_count, candidate_digest, started_at, completed_at
        ) values (
          'ksync_policy_1', %s, 'kconn_policy', 'krev_policy_1',
          'operation-sync-policy', 'user-knowledge-runtime',
          'candidate_activation', 'succeeded', 1, 1, 1, %s, now(), now()
        )
        """,
        (tenant_id, "d" * 64),
    )
    await conn.execute(
        """
        update knowledge_connections
        set status = 'active', active_revision_id = 'krev_policy_1',
            active_catalog_sync_id = 'ksync_policy_1', lifecycle_epoch = 1
        where tenant_id = %s and id = 'kconn_policy'
        """,
        (tenant_id,),
    )
    await conn.execute(
        """
        insert into knowledge_sources(
          id, tenant_id, connection_id, provider_resource_id, provider_name,
          status, authorization_version, last_complete_sync_id,
          last_seen_connection_revision_id
        ) values (
          'ksrc_policy', %s, 'kconn_policy', 'dataset-policy', 'Policy',
          'active', 1, 'ksync_policy_1', 'krev_policy_1'
        )
        """,
        (tenant_id,),
    )
    canonical_spec, spec_sha256 = _execution_spec()
    await conn.execute(
        """
        insert into run_attempts(
          id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
          queue_attempt_id, execution_spec_schema_version, execution_spec_json,
          execution_spec_canonical_json, execution_spec_sha256
        ) values (
          'attempt-knowledge-runtime', %s, 'run-knowledge-runtime', 1, 'created',
          'queue_worker', 'worker-runtime', 'queue-attempt-runtime',
          'ai-platform.execution-spec.v1', %s::jsonb, %s, %s
        )
        """,
        (tenant_id, canonical_spec, canonical_spec, spec_sha256),
    )
    await conn.execute(
        """
        update run_attempts
        set status = 'queued', owner_generation = 2,
            queue_message_id = 'queue-message-runtime'
        where tenant_id = %s and id = 'attempt-knowledge-runtime'
        """,
        (tenant_id,),
    )
    await conn.execute(
        """
        update run_attempts
        set status = 'claimed', owner_generation = 3,
            lease_expires_at = now() + interval '30 seconds',
            last_heartbeat_at = now()
        where tenant_id = %s and id = 'attempt-knowledge-runtime'
        """,
        (tenant_id,),
    )
    await conn.execute(
        """
        update run_attempts
        set status = 'running', owner_generation = 4, started_at = now()
        where tenant_id = %s and id = 'attempt-knowledge-runtime'
        """,
        (tenant_id,),
    )


@pytest.mark.asyncio
async def test_runtime_repository_pins_snapshot_fences_generation_and_commits_evidence() -> (
    None
):
    dsn = _postgres_dsn()
    schema_name = f"knowledge_runtime_{uuid.uuid4().hex}"
    schema_text = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row
    )
    repository = PostgresKnowledgeRuntimeRepository()
    try:
        await conn.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_text)
        await _seed_runtime_authority(conn)

        snapshot = _snapshot()
        async with conn.transaction():
            stored = await repository.create_run_snapshot(conn, snapshot=snapshot)
            replay = await repository.create_run_snapshot(conn, snapshot=snapshot)
        assert stored["content_hash"] == snapshot.content_hash()
        assert replay["content_hash"] == snapshot.content_hash()

        invalid_sources = snapshot.sources_projection()
        invalid_sources[0]["secret_ref"] = "must-not-enter-run-authority"
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """
                    insert into run_knowledge_snapshots(
                      tenant_id, run_id, agent_id, profile_revision,
                      profile_content_hash, retrieval_profile_id,
                      retrieval_profile_revision, sources_json,
                      principal_policy_version, authorized_at, content_hash
                    ) values (
                      %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now(), %s
                    )
                    on conflict (tenant_id, run_id) do nothing
                    """,
                    (
                        snapshot.tenant_id,
                        snapshot.run_id,
                        snapshot.agent_id,
                        snapshot.profile_revision,
                        snapshot.profile_content_hash,
                        snapshot.retrieval_profile_id,
                        snapshot.retrieval_profile_revision,
                        json.dumps(invalid_sources),
                        snapshot.principal_policy_version,
                        snapshot.content_hash(),
                    ),
                )

        with pytest.raises(KnowledgeError, match="knowledge_snapshot_conflict"):
            async with conn.transaction():
                await repository.create_run_snapshot(
                    conn,
                    snapshot=_snapshot(
                        sources=(_source(connection_lifecycle_epoch=2),)
                    ),
                )

        async with conn.transaction():
            claimed = await repository.claim_retrieval_attempt(
                conn,
                tenant_id=snapshot.tenant_id,
                run_id=snapshot.run_id,
                attempt_id="attempt-knowledge-runtime",
                generation=4,
                snapshot_hash=snapshot.content_hash(),
                source_count=1,
                overall_timeout_ms=12_000,
            )
            replayed_claim = await repository.claim_retrieval_attempt(
                conn,
                tenant_id=snapshot.tenant_id,
                run_id=snapshot.run_id,
                attempt_id="attempt-knowledge-runtime",
                generation=4,
                snapshot_hash=snapshot.content_hash(),
                source_count=1,
                overall_timeout_ms=12_000,
            )
        assert claimed["id"] == replayed_claim["id"]
        assert claimed["deadline_at"] == replayed_claim["deadline_at"]

        with pytest.raises(KnowledgeError, match="knowledge_retrieval_fence_stale"):
            async with conn.transaction():
                await repository.claim_retrieval_attempt(
                    conn,
                    tenant_id=snapshot.tenant_id,
                    run_id=snapshot.run_id,
                    attempt_id="attempt-knowledge-runtime",
                    generation=3,
                    snapshot_hash=snapshot.content_hash(),
                    source_count=1,
                    overall_timeout_ms=12_000,
                )

        evidence = KnowledgeEvidence(
            evidence_id="kev_policy_1",
            source_id="ksrc_policy",
            provider_document_id="document-policy",
            provider_chunk_id="chunk-policy",
            title="Policy",
            content="Employees must follow the approved policy.",
            provider_score=0.91,
            fused_rank=1,
            position_json={"page": 3},
        )
        async with conn.transaction():
            await conn.execute(
                """
                update run_attempts
                set lease_expires_at = now() - interval '1 millisecond'
                where tenant_id = %s and id = 'attempt-knowledge-runtime'
                """,
                (snapshot.tenant_id,),
            )
        with pytest.raises(KnowledgeError, match="knowledge_retrieval_fence_stale"):
            async with conn.transaction():
                await repository.commit_successful_retrieval(
                    conn,
                    tenant_id=snapshot.tenant_id,
                    run_id=snapshot.run_id,
                    attempt_id="attempt-knowledge-runtime",
                    generation=4,
                    snapshot_hash=snapshot.content_hash(),
                    source_count=1,
                    result_count=1,
                    provider_retry_count=0,
                    duration_ms=23,
                    evidence=(evidence,),
                )
        async with conn.transaction():
            await conn.execute(
                """
                update run_attempts
                set lease_expires_at = now() + interval '30 seconds'
                where tenant_id = %s and id = 'attempt-knowledge-runtime'
                """,
                (snapshot.tenant_id,),
            )
        async with conn.transaction():
            terminal = await repository.commit_successful_retrieval(
                conn,
                tenant_id=snapshot.tenant_id,
                run_id=snapshot.run_id,
                attempt_id="attempt-knowledge-runtime",
                generation=4,
                snapshot_hash=snapshot.content_hash(),
                source_count=1,
                result_count=1,
                provider_retry_count=0,
                duration_ms=24,
                evidence=(evidence,),
            )
        assert terminal["status"] == "succeeded"
        assert terminal["evidence_count"] == 1

        async with conn.transaction():
            loaded = await repository.load_successful_evidence(
                conn,
                tenant_id=snapshot.tenant_id,
                run_id=snapshot.run_id,
                attempt_id="attempt-knowledge-runtime",
                generation=4,
                snapshot_hash=snapshot.content_hash(),
            )
        assert [item["evidence_id"] for item in loaded] == ["kev_policy_1"]

        async with conn.transaction():
            late_terminal = await repository.terminalize_retrieval_attempt(
                conn,
                tenant_id=snapshot.tenant_id,
                run_id=snapshot.run_id,
                attempt_id="attempt-knowledge-runtime",
                generation=4,
                snapshot_hash=snapshot.content_hash(),
                status="failed",
                source_count=1,
                result_count=0,
                provider_retry_count=0,
                duration_ms=25,
                safe_failure_code="knowledge_provider_transient",
            )
        assert late_terminal["status"] == "succeeded"
        assert late_terminal["terminal_digest"] == terminal["terminal_digest"]

        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """
                    update knowledge_evidence set title = 'mutated'
                    where tenant_id = %s and run_id = %s and evidence_id = %s
                    """,
                    (snapshot.tenant_id, snapshot.run_id, "kev_policy_1"),
                )

        async with conn.transaction():
            await conn.execute(
                """
                update knowledge_retrieval_profiles
                set status = 'disabled'
                where id = 'krp_default' and revision = 1
                """
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(schema_text)
    finally:
        await conn.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("winner", "expected_status", "expected_failure_code"),
    [
        ("cancelled", "cancelled", None),
        ("timeout", "failed", "knowledge_retrieval_timeout"),
    ],
)
async def test_runtime_repository_persists_cancel_or_deadline_before_late_success(
    winner: str,
    expected_status: str,
    expected_failure_code: str | None,
) -> None:
    dsn = _postgres_dsn()
    schema_name = f"knowledge_runtime_race_{uuid.uuid4().hex}"
    schema_text = Path("app/schema.sql").read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, row_factory=dict_row
    )
    repository = PostgresKnowledgeRuntimeRepository()
    try:
        await conn.execute(
            sql.SQL("create schema {}").format(sql.Identifier(schema_name))
        )
        await _set_search_path(conn, schema_name)
        await conn.execute(schema_text)
        await _seed_runtime_authority(conn)

        snapshot = _snapshot()
        async with conn.transaction():
            await repository.create_run_snapshot(conn, snapshot=snapshot)
            await repository.claim_retrieval_attempt(
                conn,
                tenant_id=snapshot.tenant_id,
                run_id=snapshot.run_id,
                attempt_id="attempt-knowledge-runtime",
                generation=4,
                snapshot_hash=snapshot.content_hash(),
                source_count=1,
                overall_timeout_ms=100,
            )

        if winner == "cancelled":
            async with conn.transaction():
                await repository.request_cancellation(
                    conn,
                    tenant_id=snapshot.tenant_id,
                    run_id=snapshot.run_id,
                    attempt_id="attempt-knowledge-runtime",
                    generation=4,
                    snapshot_hash=snapshot.content_hash(),
                )
        else:
            await asyncio.sleep(0.25)

        evidence = KnowledgeEvidence(
            evidence_id="kev_late",
            source_id="ksrc_policy",
            provider_document_id="document-late",
            provider_chunk_id="chunk-late",
            title="Late evidence",
            content="This evidence arrived after the authoritative outcome.",
            provider_score=0.9,
            fused_rank=1,
            position_json={"page": 1},
        )
        async with conn.transaction():
            terminal = await repository.commit_successful_retrieval(
                conn,
                tenant_id=snapshot.tenant_id,
                run_id=snapshot.run_id,
                attempt_id="attempt-knowledge-runtime",
                generation=4,
                snapshot_hash=snapshot.content_hash(),
                source_count=1,
                result_count=1,
                provider_retry_count=0,
                duration_ms=250,
                evidence=(evidence,),
            )

        assert terminal["status"] == expected_status
        assert terminal["safe_failure_code"] == expected_failure_code
        evidence_cursor = await conn.execute(
            """
            select count(*) as evidence_count
            from knowledge_evidence
            where tenant_id = %s and run_id = %s
            """,
            (snapshot.tenant_id, snapshot.run_id),
        )
        assert int((await evidence_cursor.fetchone())["evidence_count"]) == 0
    finally:
        await conn.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(schema_name)
            )
        )
        await conn.close()
