"""Run input ownership, receipts and admission races against real PostgreSQL."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row

from app.bootstrap.run_inputs import build_run_inputs_service
from app.runs.domain.inputs import RunInputClosed, RunInputConflict


QUESTIONS = [{
    "question": "Which sections?", "header": "Sections", "multiSelect": True,
    "options": [{"label": "Intro", "description": "Opening"},
                {"label": "Results", "description": "Findings"}],
}]


@pytest.fixture
async def inputs_db():
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN")
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    schema = f"run_inputs_{uuid4().hex}"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    await admin.execute(f"create schema {schema}")

    @asynccontextmanager
    async def connect():
        conn = await psycopg.AsyncConnection.connect(
            dsn, options=f"-c search_path={schema}", row_factory=dict_row,
        )
        try:
            yield conn
        finally:
            await conn.close()

    try:
        async with connect() as conn:
            await conn.execute("""
                create table runs (
                  tenant_id text, id text, user_id text, status text,
                  cancel_requested_at timestamptz, workspace_id text, session_id text,
                  agent_id text, context_snapshot_id text, created_at timestamptz not null default now(),
                  primary key(tenant_id,id));
                create table sessions (id text primary key, tenant_id text, user_id text, status text);
                insert into sessions values ('session','tenant','owner','active');
                create table run_attempts (
                  id text primary key, tenant_id text, run_id text, status text, ordinal int);
                insert into runs values ('tenant','run','owner','running',null,
                  'workspace','session','agent','context',now());
                insert into run_attempts values ('attempt','tenant','run','running',1);
            """)
            source = (Path(__file__).resolve().parents[1] / "app/schema.sql").read_text()
            start = source.index("create table if not exists run_input_sessions")
            end = source.index("create or replace function", start)
            await conn.execute(source[start:end])
            await conn.commit()
        yield connect, build_run_inputs_service()
    finally:
        await admin.execute(f"drop schema {schema} cascade")
        await admin.close()


async def callback(service, conn, operation, *, attempt="attempt", **fields):
    # The runtime callback authority takes the owning Run lock before calling
    # the service. Use the same order here to test contention with user input.
    run = await service.persistence.get_owner_run(
        conn, tenant_id="tenant", user_id="owner", run_id="run", for_update=True,
    )
    return await service.callback(
        conn, tenant_id="tenant", run=run, run_id="run", attempt_id=attempt,
        operation=operation, **fields,
    )


async def submit(service, conn, **fields):
    return await service.submit(
        conn, tenant_id="tenant", user_id="owner", run_id="run", **fields,
    )


async def test_inputs_owner_idempotency_ack_and_sealed_admission(inputs_db):
    connect, service = inputs_db
    input_id = str(uuid4())
    async with connect() as conn, conn.transaction():
        assert await service.get_projection(conn, tenant_id="tenant", user_id="other", run_id="run") is None
        assert await service.get_projection(conn, tenant_id="other", user_id="owner", run_id="run") is None
        await callback(service, conn, "open")
        accepted = await submit(service, conn, input_id=input_id, text="Add a Chinese summary")
        assert await submit(service, conn, input_id=input_id, text="Add a Chinese summary") == accepted
        with pytest.raises(RunInputConflict):
            await submit(service, conn, input_id=input_id, text="Different instruction")
        pending = await callback(service, conn, "settle")
        assert pending["state"] == "open" and pending["inputs"][0]["input_id"] == input_id
        # Lost receipts repeat the command; ACK is the only operation to retry
        # after the SDK accepted a query.
        assert await callback(service, conn, "settle") == pending
        await callback(service, conn, "ack", input_ids=[input_id])
        await callback(service, conn, "ack", input_ids=[input_id])
        assert await callback(service, conn, "settle") == {"state": "sealed", "inputs": []}
        with pytest.raises(RunInputClosed):
            await submit(service, conn, input_id=str(uuid4()), text="Too late")
        assert (await submit(service, conn, input_id=input_id, text="Add a Chinese summary"))["status"] == "applied"


async def test_question_restore_answer_and_terminal_projection(inputs_db):
    connect, service = inputs_db
    async with connect() as conn, conn.transaction():
        await callback(service, conn, "open")
        await callback(service, conn, "question", question_id="question", questions=QUESTIONS)
    async with connect() as conn, conn.transaction():
        restored = await service.get_projection(conn, tenant_id="tenant", user_id="owner", run_id="run")
        assert restored["questions"][0]["status"] == "pending"
        answer_id = str(uuid4())
        await submit(service, conn, input_id=answer_id, question_id="question", answers={"q0": ["o0", "o1"]})
        result = await callback(service, conn, "poll", question_id="question")
        assert result["inputs"][0]["answers"] == {"q0": ["o0", "o1"]}
        await conn.execute("update runs set status='cancelled'")
        closed = await service.get_projection(conn, tenant_id="tenant", user_id="owner", run_id="run")
        assert closed["state"] == "inactive"
        assert closed["questions"][0]["status"] == "closed"
        assert closed["inputs"][0]["status"] == "closed"


async def test_answer_ack_resolves_question_and_stale_attempt_cannot_operate(inputs_db):
    connect, service = inputs_db
    async with connect() as conn, conn.transaction():
        await callback(service, conn, "open")
        await callback(service, conn, "question", question_id="question", questions=QUESTIONS)
        input_id = str(uuid4())
        await submit(service, conn, input_id=input_id, question_id="question", answers={"q0": {"text": "Custom answer"}})
        await callback(service, conn, "ack", input_ids=[input_id])
        projection = await service.get_projection(conn, tenant_id="tenant", user_id="owner", run_id="run")
        assert projection["questions"][0]["status"] == "resolved"
        with pytest.raises(RunInputClosed):
            await callback(service, conn, "poll", attempt="old-attempt")
        await conn.execute("update runs set cancel_requested_at=now()")
        with pytest.raises(RunInputClosed):
            await callback(service, conn, "question", question_id="late", questions=QUESTIONS)
        await conn.execute("delete from runs")
        assert (await (await conn.execute("select count(*) n from run_inputs")).fetchone())["n"] == 0


@pytest.mark.parametrize("submit_first", [True, False])
async def test_submission_and_sealing_serialize_on_the_owning_run(inputs_db, submit_first):
    connect, service = inputs_db
    async with connect() as conn, conn.transaction():
        await callback(service, conn, "open")
    async with connect() as first, connect() as second:
        input_id = str(uuid4())
        async with first.transaction():
            if submit_first:
                await submit(service, first, input_id=input_id, text="Accepted before seal")
                operation = callback(service, second, "settle")
            else:
                await callback(service, first, "settle")
                operation = submit(service, second, input_id=input_id, text="Arrived after seal")
            task = asyncio.create_task(operation)
            try:
                # Observe a real PostgreSQL lock wait, not only task scheduling.
                for _ in range(100):
                    row = await (await first.execute(
                        "select wait_event_type from pg_stat_activity where pid=%s",
                        (second.info.backend_pid,),
                    )).fetchone()
                    if row and row["wait_event_type"] == "Lock":
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("input operation did not wait for the owning Run lock")
                assert not task.done()
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        if submit_first:
            result = await asyncio.wait_for(task, 2)
            assert result["state"] == "open" and result["inputs"][0]["input_id"] == input_id
        else:
            with pytest.raises(RunInputClosed):
                await asyncio.wait_for(task, 2)
        await second.rollback()


@pytest.mark.parametrize(
    "question_text, question_count",
    [("Which sections?", 1), ("Q" * 4_001, 1), ("𠀀" * 15_998, 4)],
    ids=["short", "long", "four-long-unicode"],
)
async def test_http_question_answer_and_supplementary_text_reach_the_same_actor(
    inputs_db, monkeypatch, question_text, question_count,
):
    from app.auth import require_principal
    from app.execution.infrastructure.harness.claude.interaction import ClaudeRunInteractionActor
    from app.routes import runs, runtime_callbacks
    from app.runtime.sandbox.callback_tokens import derive_callback_token
    from app.execution.infrastructure.run_inputs_http import RunInputCallbackClient

    connect, service = inputs_db
    questions = [
        {**QUESTIONS[0], "question": question_text if question_count == 1 else f"{index}:{question_text}"}
        for index in range(question_count)
    ]
    app = FastAPI()
    app.state.run_inputs_service = service
    app.include_router(runs.router, prefix="/api/ai")
    app.include_router(runtime_callbacks.router, prefix="/api/ai")
    principal = SimpleNamespace(tenant_id="tenant", user_id="owner", roles=[])
    app.dependency_overrides[require_principal] = lambda: principal

    @asynccontextmanager
    async def transaction():
        async with connect() as conn, conn.transaction():
            yield conn

    token_id = "cbt:run:attempt"
    secret = "synthetic-input-callback-secret"

    async def current_lease(_conn, **scope):
        assert scope == {"tenant_id": "tenant", "run_id": "run", "attempt_id": "attempt"}
        return [{"attempt_id": "attempt", "lease_payload_json": {
            "attempt_id": "attempt", "callback_token_id": token_id,
        }}]

    monkeypatch.setattr(runs, "transaction", transaction)
    monkeypatch.setattr(runtime_callbacks, "transaction", transaction)
    monkeypatch.setattr(runtime_callbacks, "get_settings", lambda: SimpleNamespace(sandbox_callback_token=secret))
    # The real callback route, token validation and Run lock remain in use.
    # Only the already-tested sandbox lease lookup uses a controlled fixture.
    monkeypatch.setattr(
        runtime_callbacks.sandbox_leases,
        "list_current_sandbox_runtime_leases_for_attempt", current_lease,
    )
    transport = httpx.ASGITransport(app=app)
    port = RunInputCallbackClient(
        callback_url="http://input.test/api/ai/runtime/callbacks/inputs",
        callback_token=derive_callback_token(secret, token_id),
        callback_token_id=token_id, run_id="run", attempt_id="attempt",
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
    )
    actor = ClaudeRunInteractionActor(port, run_id="run", attempt_id="attempt")
    question_task = None
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://input.test") as browser:
            bad_token = await browser.post("/api/ai/runtime/callbacks/inputs", json={
                "operation": "open", "run_id": "run", "attempt_id": "attempt",
                "callback_token_id": token_id,
            }, headers={"X-AI-Platform-Callback-Token": "wrong"})
            assert bad_token.status_code == 401
            async with connect() as conn, conn.transaction():
                await conn.execute("update runs set cancel_requested_at=now()")
            cancelled = await browser.post("/api/ai/runtime/callbacks/inputs", json={
                "operation": "open", "run_id": "run", "attempt_id": "attempt",
                "callback_token_id": token_id,
            }, headers={"X-AI-Platform-Callback-Token": derive_callback_token(secret, token_id)})
            assert cancelled.status_code == 409 and cancelled.json()["detail"] == "run_input_closed"
            async with connect() as conn, conn.transaction():
                await conn.execute("update runs set cancel_requested_at=null")

            await actor.open()
            question_task = asyncio.create_task(actor.resolve_native_question(
                tool_call_id="native-question", tool_input={"questions": questions},
            ))
            async with asyncio.timeout(2):
                while True:
                    restored = (await browser.get("/api/ai/runs/run/inputs")).json()
                    if restored["questions"]:
                        break
                    await asyncio.sleep(0.01)
            history = await browser.get("/api/ai/sessions/session/run-inputs?limit=1")
            assert history.status_code == 200
            assert [item["run_id"] for item in history.json()["runs"]] == ["run"]
            assert history.json()["has_more"] is False
            question_id = restored["questions"][0]["question_id"]
            assert restored["questions"][0]["status"] == "pending"
            assert not question_task.done()
            submission = {"input_id": str(uuid4()), "question_id": question_id,
                          "answers": {f"q{index}": ["o0", "o1"] for index, item in enumerate(questions)}}
            submitted = await browser.post("/api/ai/runs/run/inputs", json=submission)
            assert submitted.status_code == 200 and submitted.json()["status"] == "queued"
            updated = await asyncio.wait_for(question_task, 2)
            assert updated == {"questions": questions, "answers": {item["question"]: ["Intro", "Results"] for item in questions}}
            received = (await browser.get("/api/ai/runs/run/inputs")).json()
            assert received["questions"][0]["status"] == "answered"
            await actor.acknowledge_native_question("native-question")
            resolved = (await browser.get("/api/ai/runs/run/inputs")).json()
            assert resolved["questions"][0]["status"] == "resolved"

            text_submission = {"input_id": str(uuid4()), "text": "Add a Chinese summary"}
            accepted = await browser.post("/api/ai/runs/run/inputs", json=text_submission)
            assert accepted.status_code == 200
            assert (await browser.post("/api/ai/runs/run/inputs", json=text_submission)).json() == accepted.json()
            command = await actor.settle_at_result()
            queries = []

            class Client:
                async def query(self, text, session_id):
                    queries.append((text, session_id))

            await actor.apply_text_at_result(Client(), command, session_id="native-session")
            assert queries == [(text_submission["text"], "native-session")]
            assert await actor.settle_at_result() is None
            late = await browser.post("/api/ai/runs/run/inputs", json={"input_id": str(uuid4()), "text": "Too late"})
            assert late.status_code == 409 and late.json()["detail"] == "run_input_closed"
            principal.user_id = "other"
            assert (await browser.get("/api/ai/sessions/session/run-inputs")).status_code == 404
            assert (await browser.get("/api/ai/runs/run/inputs")).status_code == 404
            assert (await browser.post("/api/ai/runs/run/inputs", json=text_submission)).status_code == 404
    finally:
        await actor.cancel()
        if question_task is not None:
            await asyncio.gather(question_task, return_exceptions=True)


@pytest.mark.parametrize("admin", [False, True])
async def test_supplementary_input_retains_existing_role_redaction_policy(inputs_db, admin):
    connect, service = inputs_db
    text = "Contact alice@example.test"
    async with connect() as conn, conn.transaction():
        await callback(service, conn, "open")
        input_id = str(uuid4())
        accepted = await submit(service, conn, input_id=input_id, text=text, redact_public=not admin)
        assert await submit(service, conn, input_id=input_id, text=text, redact_public=not admin) == accepted
        execution = await callback(service, conn, "settle")
        assert (text == execution["inputs"][0]["text"]) is admin
        public = await service.get_projection(conn, tenant_id="tenant", user_id="owner", run_id="run")
        assert "alice@example.test" not in public["inputs"][0]["text"]
        owner = await service.get_projection(conn, tenant_id="tenant", user_id="owner", run_id="run", redact_public=not admin)
        assert (text == owner["inputs"][0]["text"]) is admin
        await callback(service, conn, "question", question_id="admin-answer", questions=QUESTIONS)
        await submit(service, conn, input_id=str(uuid4()), question_id="admin-answer",
                     answers={"q0": {"text": text}}, redact_public=not admin)
        answer = (await callback(service, conn, "poll", question_id="admin-answer"))["inputs"][0]["answers"]["q0"]["text"]
        assert (answer == text) is admin


@pytest.mark.parametrize("multi", [False, True])
async def test_redacted_question_option_collisions_have_distinct_execution_keys(inputs_db, multi):
    connect, service = inputs_db
    questions = [{"question": f"Send to {email}?", "header": "Contact", "multiSelect": multi,
                  "options": [{"label": "alice@example.test", "description": ""},
                              {"label": "bob@example.test", "description": ""}]}
                 for email in ("alice@example.test", "bob@example.test")]
    async with connect() as conn, conn.transaction():
        await callback(service, conn, "open")
        from app.runs.domain.inputs import canonicalize_questions
        public_questions = canonicalize_questions(questions, sanitize_text=service.sanitize_text)
        await callback(service, conn, "question", question_id="collision", questions=public_questions)
        public = await service.get_projection(conn, tenant_id="tenant", user_id="owner", run_id="run")
        batch = public["questions"][0]["questions"]
        assert batch[0]["question"] == batch[1]["question"]
        assert batch[0]["key"] != batch[1]["key"]
        assert batch[0]["options"][0]["label"] == batch[0]["options"][1]["label"]
        assert batch[0]["options"][0]["key"] != batch[0]["options"][1]["key"]
        assert "example.test" not in str(batch)
        answers = {"q0": ["o1", "o0"] if multi else "o1", "q1": {"text": "o0"}}
        input_id = str(uuid4())
        await submit(service, conn, input_id=input_id, question_id="collision", answers=answers)
        assert (await callback(service, conn, "poll", question_id="collision"))["inputs"][0]["answers"] == answers
        assert (await submit(service, conn, input_id=input_id, question_id="collision", answers=answers))["status"] == "queued"


async def test_session_input_history_paginates_owned_runs_and_rejects_foreign_cursors(inputs_db):
    connect, service = inputs_db
    async with connect() as conn, conn.transaction():
        await callback(service, conn, "open")
        await submit(service, conn, input_id=str(uuid4()), text="old round")
        await conn.execute("update runs set status='completed', created_at=now()-interval '1 day'")
        await conn.execute("""insert into runs (tenant_id,id,user_id,status,session_id,created_at) values
            ('tenant','next','owner','completed','session',now()),
            ('tenant','foreign','other','completed','session',now()),
            ('other','foreign-tenant','owner','completed','session',now()),
            ('tenant','other-session','owner','completed','another',now())""")
        page = await service.get_session_history(conn, tenant_id="tenant", user_id="owner", session_id="session", limit=1)
        assert [item["run_id"] for item in page["runs"]] == ["next"]
        assert page["has_more"] and page["next_before_run_id"] == "next"
        older = await service.get_session_history(conn, tenant_id="tenant", user_id="owner", session_id="session", limit=1, before_run_id="next")
        assert [item["run_id"] for item in older["runs"]] == ["run"]
        assert older["runs"][0]["inputs"][0]["text"] == "old round"
        assert not older["has_more"]
        for foreign in ("foreign", "foreign-tenant", "other-session", "missing"):
            assert (await service.get_session_history(conn, tenant_id="tenant", user_id="owner", session_id="session", before_run_id=foreign))["runs"] == []
        assert await service.get_session_history(conn, tenant_id="tenant", user_id="other", session_id="session") is None
        assert await service.get_session_history(conn, tenant_id="other", user_id="owner", session_id="session") is None


@pytest.mark.parametrize("answers", [{"q0": ["Intro"]}, {"q0": ["o8"]}, {"q0": ["o0", "o0"]},
                                     {"q9": ["o0"]}, {"q0": "o0"}, {"q0": {"text": "  "}}, {}])
async def test_question_answers_require_exact_opaque_keys_and_complete_valid_values(inputs_db, answers):
    from app.runs.domain.inputs import RunInputError
    connect, service = inputs_db
    async with connect() as conn, conn.transaction():
        await callback(service, conn, "open")
        await callback(service, conn, "question", question_id="invalid-answer", questions=QUESTIONS)
        with pytest.raises(RunInputError):
            await submit(service, conn, input_id=str(uuid4()), question_id="invalid-answer", answers=answers)
        assert (await callback(service, conn, "poll", question_id="invalid-answer"))["inputs"] == []
