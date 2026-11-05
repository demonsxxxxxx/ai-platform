"""PostgreSQL storage for Execution-owned effect evidence and exact recovery joins."""

from __future__ import annotations

import json
from typing import Any

from app.execution.domain.recovery_operations import RecoveryInvocation, RecoveryOperationScope


_OPERATION_SCOPE = "tenant_id=%(tenant_id)s and workspace_id=%(workspace_id)s and user_id=%(user_id)s and session_id=%(session_id)s"
_INVOCATION_SCOPE = _OPERATION_SCOPE + " and run_id=%(run_id)s and attempt_id=%(attempt_id)s and invocation_id=%(invocation_id)s and owner_generation=%(owner_generation)s and tool_identity=%(tool_identity)s and binding_kind=%(binding_kind)s"


class PostgresRecoveryOperations:
    async def record_started(
        self, conn: Any, *, invocation: RecoveryInvocation, operation_key: str,
        tool_facts: dict[str, Any], public_summary: str,
    ) -> dict[str, Any]:
        cursor = await conn.execute(
            """insert into execution_recovery_operations (
                 operation_key, tenant_id, workspace_id, user_id, session_id,
                 source_run_id, source_attempt_id, source_generation, native_tool_use_id,
                 tool_identity, tool_facts_json, public_summary
               ) values (%(operation_key)s,%(tenant_id)s,%(workspace_id)s,%(user_id)s,%(session_id)s,
                 %(run_id)s,%(attempt_id)s,%(owner_generation)s,%(invocation_id)s,%(tool_identity)s,
                 %(facts)s::jsonb,%(summary)s)
               on conflict (tenant_id,source_run_id,source_attempt_id,native_tool_use_id) do nothing""",
            {**invocation.mapping(), "operation_key": operation_key, "facts": json.dumps(tool_facts), "summary": public_summary},
        )
        cursor = await conn.execute(
            "select * from execution_recovery_operations where " + _OPERATION_SCOPE +
            " and source_run_id=%(run_id)s and source_attempt_id=%(attempt_id)s and native_tool_use_id=%(invocation_id)s for update",
            invocation.mapping(),
        )
        return await cursor.fetchone()

    async def get_operation(self, conn: Any, *, scope: RecoveryOperationScope, operation_key: str) -> dict[str, Any] | None:
        cursor = await conn.execute(
            "select * from execution_recovery_operations where " + _OPERATION_SCOPE + " and operation_key=%(operation_key)s for update",
            {**scope.mapping(), "operation_key": operation_key},
        )
        return await cursor.fetchone()

    async def record_reported(self, conn: Any, *, scope: RecoveryOperationScope, operation_key: str, status: str) -> None:
        await conn.execute(
            "update execution_recovery_operations set reported_status=%(status)s where " + _OPERATION_SCOPE + " and operation_key=%(operation_key)s",
            {**scope.mapping(), "operation_key": operation_key, "status": status},
        )

    async def verify(
        self, conn: Any, *, scope: RecoveryOperationScope, operation_key: str,
        outcome: str, kind: str, reference: str,
    ) -> dict[str, Any]:
        cursor = await conn.execute(
            """update execution_recovery_operations set verified_outcome=%(outcome)s,
                 verified_kind=%(kind)s, verified_reference=%(reference)s, verified_at=now()
               where """ + _OPERATION_SCOPE + " and operation_key=%(operation_key)s and verified_outcome <> 'succeeded' returning *",
            {**scope.mapping(), "operation_key": operation_key, "outcome": outcome, "kind": kind, "reference": reference},
        )
        row = await cursor.fetchone()
        return row if row is not None else await self.get_operation(conn, scope=scope, operation_key=operation_key)

    async def insert_binding(
        self, conn: Any, *, invocation: RecoveryInvocation, operation_key: str,
        frozen_binding_ref: str, public_summary: str,
    ) -> None:
        await conn.execute(
            """insert into execution_recovery_invocations (
                 tenant_id,workspace_id,user_id,session_id,run_id,attempt_id,owner_generation,
                 invocation_id,binding_kind,tool_identity,operation_key,frozen_binding_ref,public_summary)
               values (%(tenant_id)s,%(workspace_id)s,%(user_id)s,%(session_id)s,%(run_id)s,
                 %(attempt_id)s,%(owner_generation)s,%(invocation_id)s,%(binding_kind)s,%(tool_identity)s,
                 %(operation_key)s,%(frozen)s,%(summary)s)
               on conflict (tenant_id,run_id,attempt_id,invocation_id) do nothing""",
            {**invocation.mapping(), "operation_key": operation_key, "frozen": frozen_binding_ref, "summary": public_summary},
        )

    async def get_binding(self, conn: Any, *, invocation: RecoveryInvocation, for_update: bool = False) -> dict[str, Any] | None:
        cursor = await conn.execute(
            "select * from execution_recovery_invocations where " + _INVOCATION_SCOPE + (" for update" if for_update else ""),
            invocation.mapping(),
        )
        return await cursor.fetchone()

    async def attach_question(self, conn: Any, *, invocation: RecoveryInvocation, question_id: str, expires_at: Any) -> None:
        await conn.execute(
            "update execution_recovery_invocations set question_id=%(question)s, question_expires_at=%(expires)s where " + _INVOCATION_SCOPE + " and question_id is null",
            {**invocation.mapping(), "question": question_id, "expires": expires_at},
        )

    async def find_question(self, conn: Any, *, tenant_id: str, user_id: str, run_id: str, attempt_id: str, question_id: str) -> dict[str, Any] | None:
        cursor = await conn.execute(
            """select * from execution_recovery_invocations where tenant_id=%s and user_id=%s
               and run_id=%s and attempt_id=%s and question_id=%s""",
            (tenant_id, user_id, run_id, attempt_id, question_id),
        )
        return await cursor.fetchone()

    async def authorize_answer(self, conn: Any, *, invocation: RecoveryInvocation, input_id: str, authorization: str) -> None:
        await conn.execute(
            'update execution_recovery_invocations set "authorization"=%(authorization)s, answer_input_id=%(input_id)s::uuid where ' + _INVOCATION_SCOPE + ' and "authorization"=\'pending\'',
            {**invocation.mapping(), "authorization": authorization, "input_id": input_id},
        )

    async def close_binding(self, conn: Any, *, invocation: RecoveryInvocation, reason: str) -> None:
        await conn.execute(
            "update execution_recovery_invocations set closed=true, close_reason=%(reason)s where " + _INVOCATION_SCOPE,
            {**invocation.mapping(), "reason": reason},
        )

    async def mark_started(self, conn: Any, *, invocation: RecoveryInvocation) -> None:
        await conn.execute(
            "update execution_recovery_invocations set started=true where " + _INVOCATION_SCOPE,
            invocation.mapping(),
        )
