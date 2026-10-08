"""PostgreSQL persistence for Runs-owned interaction inputs."""

from __future__ import annotations

import json
from typing import Any, cast

from psycopg import AsyncConnection

from app.runs.domain.inputs import RunInputClosed, RunInputConflict


_INPUT_COLUMNS = (
    "input_id, tenant_id, run_id, attempt_id, kind, text, question_id, answers, "
    "status, created_at"
)
_QUESTION_COLUMNS = "tenant_id, run_id, attempt_id, question_id, questions, status, created_at"


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def get_owner_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = "for update" if for_update else ""
    cursor = await conn.execute(
        f"""
        select id, tenant_id, status, cancel_requested_at
        from runs
        where tenant_id = %s and id = %s and user_id = %s
        {lock_clause}
        """,
        (tenant_id, run_id, user_id),
    )
    return await cursor.fetchone()


async def get_current_attempt_id(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> str | None:
    cursor = await conn.execute(
        """
        select id
        from run_attempts
        where tenant_id = %s
          and run_id = %s
          and status in ('claimed', 'running')
        order by ordinal desc
        limit 1
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return str(row["id"]) if row is not None else None


async def get_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select tenant_id, run_id, attempt_id, state, created_at, updated_at, sealed_at
        from run_input_sessions
        where tenant_id = %s and run_id = %s and attempt_id = %s
        """,
        (tenant_id, run_id, attempt_id),
    )
    return await cursor.fetchone()


async def open_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    await conn.execute(
        """
        insert into run_input_sessions(tenant_id, run_id, attempt_id, state)
        values (%s, %s, %s, 'open')
        on conflict (tenant_id, run_id, attempt_id) do nothing
        """,
        (tenant_id, run_id, attempt_id),
    )
    session = await get_session(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    if session is None:
        raise RunInputClosed()
    return session


async def get_input(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    input_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        f"""
        select {_INPUT_COLUMNS}
        from run_inputs
        where tenant_id = %s and run_id = %s and input_id = %s::uuid
        """,
        (tenant_id, run_id, input_id),
    )
    return await cursor.fetchone()


async def insert_input(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    input_id: str,
    kind: str,
    text: str | None,
    question_id: str | None,
    answers: dict[str, Any],
) -> dict[str, Any]:
    cursor = await conn.execute(
        f"""
        insert into run_inputs(
          input_id, tenant_id, run_id, attempt_id, kind, text, question_id,
          answers, status
        ) values (%s::uuid, %s, %s, %s, %s, %s, %s, %s::jsonb, 'queued')
        returning {_INPUT_COLUMNS}
        """,
        (
            input_id,
            tenant_id,
            run_id,
            attempt_id,
            kind,
            text,
            question_id,
            _json_dumps(answers),
        ),
    )
    # The owning Run lock serializes submission and its idempotency lookup.
    return cast(dict[str, Any], await cursor.fetchone())


async def get_question(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    question_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        f"""
        select {_QUESTION_COLUMNS}
        from run_input_questions
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and question_id = %s
        """,
        (tenant_id, run_id, attempt_id, question_id),
    )
    return await cursor.fetchone()


async def publish_question(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    question_id: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    cursor = await conn.execute(
        f"""
        insert into run_input_questions(
          tenant_id, run_id, attempt_id, question_id, questions, status
        ) values (%s, %s, %s, %s, %s::jsonb, 'pending')
        on conflict (tenant_id, run_id, attempt_id, question_id) do nothing
        returning {_QUESTION_COLUMNS}
        """,
        (tenant_id, run_id, attempt_id, question_id, _json_dumps(questions)),
    )
    row = await cursor.fetchone()
    if row is not None:
        return row
    existing = await get_question(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        question_id=question_id,
    )
    if existing is None:
        raise RunInputConflict("run_input_question_id_conflict")
    return existing


async def set_question_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    question_id: str,
    status: str,
) -> bool:
    cursor = await conn.execute(
        """
        update run_input_questions
        set status = %s
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and question_id = %s and status = 'pending'
        returning question_id
        """,
        (status, tenant_id, run_id, attempt_id, question_id),
    )
    return await cursor.fetchone() is not None


async def list_inputs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        f"""
        select {_INPUT_COLUMNS}
        from run_inputs
        where tenant_id = %s and run_id = %s
        order by created_at, input_id
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_questions(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        f"""
        select {_QUESTION_COLUMNS}
        from run_input_questions
        where tenant_id = %s and run_id = %s
        order by created_at, attempt_id, question_id
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_queued_inputs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    question_id: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    filters = [
        "tenant_id = %s",
        "run_id = %s",
        "attempt_id = %s",
        "status = 'queued'",
    ]
    params: list[Any] = [tenant_id, run_id, attempt_id]
    if question_id is not None:
        filters.append("question_id = %s")
        params.append(question_id)
    if kind is not None:
        filters.append("kind = %s")
        params.append(kind)
    limit_sql = ""
    if limit is not None:
        limit_sql = "limit %s"
        params.append(limit)
    cursor = await conn.execute(
        f"""
        select {_INPUT_COLUMNS}
        from run_inputs
        where {' and '.join(filters)}
        order by created_at, input_id
        {limit_sql}
        for update
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())


async def acknowledge_inputs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    input_ids: list[str],
) -> None:
    if not input_ids:
        raise RunInputConflict("run_input_ack_mismatch")
    cursor = await conn.execute(
        """
        select input_id::text as input_id, question_id
        from run_inputs
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and input_id = any(%s::uuid[])
        order by input_id
        for update
        """,
        (tenant_id, run_id, attempt_id, input_ids),
    )
    rows = list(await cursor.fetchall())
    if {str(row["input_id"]) for row in rows} != set(input_ids):
        raise RunInputConflict("run_input_ack_mismatch")
    await conn.execute(
        """
        update run_inputs
        set status = 'applied'
        where tenant_id = %s and run_id = %s and attempt_id = %s
          and input_id = any(%s::uuid[])
        """,
        (tenant_id, run_id, attempt_id, input_ids),
    )
    question_ids = sorted(
        {str(row["question_id"]) for row in rows if row.get("question_id") is not None}
    )
    for question_id in question_ids:
        await conn.execute(
            """
            update run_input_questions
            set status = 'resolved'
            where tenant_id = %s and run_id = %s and attempt_id = %s
              and question_id = %s and status = 'answered'
            """,
            (tenant_id, run_id, attempt_id, question_id),
        )


async def seal_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        update run_input_sessions
        set state = 'sealed', sealed_at = coalesce(sealed_at, now()), updated_at = now()
        where tenant_id = %s and run_id = %s and attempt_id = %s and state = 'open'
        returning tenant_id, run_id, attempt_id, state, created_at, updated_at, sealed_at
        """,
        (tenant_id, run_id, attempt_id),
    )
    row = await cursor.fetchone()
    if row is not None:
        return row
    session = await get_session(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    if session is None:
        raise RunInputClosed()
    return session
