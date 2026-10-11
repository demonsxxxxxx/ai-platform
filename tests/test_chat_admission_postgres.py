"""Real-PostgreSQL durability behind Chat admission failure recovery."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.models import ChatStreamRequest
from app.persistence import chat_submissions
from app.routes.chat import (
    _canonical_pre_persistence_rejection_fingerprint,
    _persist_pre_persistence_rejection,
)
from tests.support.db_transactions import event_loop_policy as event_loop_policy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_mode",
    ["admission_commit_ack_lost", "admission_rollback", "rejection_commit_ack_lost"],
)
async def test_chat_rejection_recovery_uses_committed_postgres_outcome(monkeypatch, failure_mode):
    """Use separate connections to observe the exact claim/COMMIT recovery contract."""

    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema_name = f"chat_admission_{uuid.uuid4().hex}"
    principal = AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a")
    request = ChatStreamRequest(
        message="synthetic commit recovery",
        workspace_id="workspace-a",
        submission_id=str(uuid.uuid4()),
    )
    submission_id = str(request.submission_id)
    fingerprint = _canonical_pre_persistence_rejection_fingerprint(
        request=request, principal=principal, query_agent_id=None,
    )
    observer = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    writer = None
    recovery = None
    try:
        await observer.execute(sql.SQL("create schema {}").format(sql.Identifier(schema_name)))
        await observer.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))
        await observer.execute(Path("app/schema.sql").read_text(encoding="utf-8"))
        await observer.execute("insert into tenants(id, name) values ('tenant-a', 'Tenant A')")
        await observer.execute("insert into workspaces(id, tenant_id, name) values ('workspace-a', 'tenant-a', 'A')")
        await observer.execute("insert into users(id, tenant_id, display_name) values ('user-a', 'tenant-a', 'User A')")
        await observer.execute(
            "insert into agents(id, tenant_id, name, agent_type) values ('agent-a', 'tenant-a', 'Agent A', 'chat')"
        )
        await observer.execute("insert into skills(id, name, version, executor_type) values ('skill-a', 'Skill A', '1.0.0', 'fake')")
        writer = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
        recovery = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
        for conn in (writer, recovery):
            await conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema_name)))

        # Mirror the route's admission transaction, with real writes and a real
        # COMMIT before the synthetic acknowledgement error reaches its caller.
        with pytest.raises(RuntimeError, match="synthetic admission failure"):
            async with writer.transaction():
                _, created = await chat_submissions.claim_chat_submission(
                    writer,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    submission_id=submission_id,
                    workspace_id=request.workspace_id,
                    request_fingerprint_sha256=fingerprint,
                )
                assert created
                await writer.execute(
                    """
                    insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title)
                    values ('session-a', 'tenant-a', 'workspace-a', 'user-a', 'agent-a', 'A')
                    """
                )
                await writer.execute(
                    """
                    insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status)
                    values ('run-a', 'tenant-a', 'workspace-a', 'session-a', 'user-a', 'agent-a', 'skill-a', 'queued')
                    """
                )
                await writer.execute(
                    """
                    insert into messages(id, tenant_id, session_id, run_id, role, content)
                    values ('message-a', 'tenant-a', 'session-a', 'run-a', 'user', 'synthetic')
                    """
                )
                await chat_submissions.finalize_chat_submission(
                    writer,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    submission_id=submission_id,
                    state="accepted_pending_enqueue",
                    workspace_id=request.workspace_id,
                    session_id="session-a",
                    run_id="run-a",
                    outcome_json={
                        "session_id": "session-a", "run_id": "run-a",
                        "status": "accepted_pending_enqueue", "submission_id": submission_id,
                    },
                )
                if failure_mode != "admission_commit_ack_lost":
                    raise RuntimeError("synthetic admission failure before COMMIT")
            raise RuntimeError("synthetic admission failure after COMMIT")

        @asynccontextmanager
        async def recovery_transaction():
            async with recovery.transaction():
                yield recovery
            if failure_mode == "rejection_commit_ack_lost":
                raise RuntimeError("synthetic rejection COMMIT acknowledgement lost")

        monkeypatch.setattr("app.routes.chat.transaction", recovery_transaction)
        recovery_kwargs = {
            "request": request, "principal": principal, "submission_id": submission_id,
            "query_agent_id": None, "workspace_id": request.workspace_id, "session_id": None,
            "code": "chat_submission_internal_error",
        }
        if failure_mode == "rejection_commit_ack_lost":
            with pytest.raises(HTTPException) as raised:
                await _persist_pre_persistence_rejection(**recovery_kwargs)
            assert raised.value.status_code == 500
            assert raised.value.detail["code"] == "chat_submission_internal_error"
            assert "submission_disposition" not in raised.value.detail
        else:
            response = await _persist_pre_persistence_rejection(**recovery_kwargs)
            if failure_mode == "admission_commit_ack_lost":
                assert response.status == "accepted_pending_enqueue"
                assert response.run_id == "run-a"
                assert response.session_id == "session-a"
                assert await _persist_pre_persistence_rejection(**recovery_kwargs) == response
            else:
                assert response is None

        durable = await chat_submissions.get_chat_submission(
            observer, tenant_id=principal.tenant_id, user_id=principal.user_id,
            submission_id=submission_id,
        )
        assert durable["request_fingerprint_sha256"] == fingerprint
        accepted = failure_mode == "admission_commit_ack_lost"
        assert durable["state"] == ("accepted_pending_enqueue" if accepted else "rejected_before_persist")
        assert durable["submission_disposition"] == (None if accepted else "rejected_before_persist")
        for table in ("sessions", "runs", "messages"):
            cursor = await observer.execute(sql.SQL("select count(*) as count from {}").format(sql.Identifier(table)))
            assert (await cursor.fetchone())["count"] == (1 if accepted else 0)
        cursor = await observer.execute("select count(*) as count from chat_submissions")
        assert (await cursor.fetchone())["count"] == 1
    finally:
        for conn in (writer, recovery):
            if conn is not None:
                await conn.close()
        try:
            await observer.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name)))
        finally:
            await observer.close()
