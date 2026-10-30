"""Host evidence production and consumption for one recovery invocation.

Only authenticated owner answers and registered authoritative queries verify effects.
Executor started/completed facts establish identity, never a verified outcome.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from app.execution.domain.recovery_operations import (
    RECOVERY_CONFIRMATION_TIMEOUT_SECONDS,
    RecoveryInvocation, RecoverySourceConfirmation, RecoveryOperationError, RecoveryOperationScope, RecoveryOperationFact,
    operation_permission, operation_question, source_operation_question,
)


@dataclass(frozen=True)
class RecoveryInvocationAuthority:
    requires_recovery_gate: bool
    manual_confirmation_available: bool = True
    public_summary: str | None = None
    public_summary_reason: str = "recovery_operation_public_object_metadata_missing"

    def __post_init__(self) -> None:
        if type(self.requires_recovery_gate) is not bool:
            raise RecoveryOperationError("recovery_operation_authority_invalid")


@dataclass(frozen=True)
class AuthoritativeOperationStatus:
    outcome: str
    reference: str
    conclusive: bool


class RecoveryOperationsPersistence(Protocol):
    async def record_started(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...
    async def get_operation(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...
    async def record_reported(self, conn: Any, **kwargs: Any) -> None: ...
    async def verify(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...
    async def insert_binding(self, conn: Any, **kwargs: Any) -> None: ...
    async def get_binding(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...
    async def attach_question(self, conn: Any, **kwargs: Any) -> None: ...
    async def find_question(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...
    async def authorize_answer(self, conn: Any, **kwargs: Any) -> None: ...
    async def mark_started(self, conn: Any, **kwargs: Any) -> None: ...
    async def close_binding(self, conn: Any, **kwargs: Any) -> None: ...


AsyncPort = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class RecoveryOperationsService:
    persistence: RecoveryOperationsPersistence
    authorize_current: AsyncPort
    authorize_frozen_binding: AsyncPort
    publish_question: AsyncPort
    sanitize_text: Callable[[object], str]
    authorize_completion_tail: AsyncPort | None = None
    authoritative_queries: Mapping[str, AsyncPort] = field(default_factory=dict)
    read_manual_answer: AsyncPort | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def _summary(self, text: str) -> str:
        safe = self.sanitize_text(text)
        if not isinstance(safe, str) or not safe.strip() or len(safe) > 1000:
            raise RecoveryOperationError("recovery_operation_summary_invalid")
        return safe

    async def _authority(self, conn: Any, invocation: RecoveryInvocation) -> RecoveryInvocationAuthority:
        authority = await self.authorize_current(conn, invocation=invocation)
        if not isinstance(authority, RecoveryInvocationAuthority):
            raise RecoveryOperationError("recovery_operation_authority_stale")
        return authority

    async def record_started(
        self, conn: Any, *, invocation: RecoveryInvocation,
        tool_facts: dict[str, Any], public_summary: str,
    ) -> dict[str, Any]:
        if isinstance(invocation, RecoverySourceConfirmation):
            raise RecoveryOperationError("recovery_operation_not_authorized")
        authority = await self._authority(conn, invocation)
        if not isinstance(tool_facts, dict) or len(json.dumps(tool_facts, allow_nan=False).encode("utf-8")) > 8192:
            raise RecoveryOperationError("recovery_operation_facts_invalid")
        summary = self._summary(public_summary)
        if authority.requires_recovery_gate:
            if not authority.manual_confirmation_available and invocation.tool_identity not in self.authoritative_queries:
                raise RecoveryOperationError(authority.public_summary_reason)
            operation, binding = await self._locked_binding(conn, invocation)
            if not binding["started"]:
                if operation_permission(operation, binding)[0] != "allow":
                    raise RecoveryOperationError("recovery_operation_not_authorized")
                await self.persistence.mark_started(conn, invocation=invocation)
        row = await self.persistence.record_started(
            conn, invocation=invocation, operation_key="rop_" + uuid4().hex,
            tool_facts=tool_facts, public_summary=summary,
        )
        if row is None or (row["source_generation"], row["tool_identity"], row["tool_facts_json"], row["public_summary"]) != (
            invocation.owner_generation, invocation.tool_identity, tool_facts, summary,
        ):
            raise RecoveryOperationError("recovery_operation_started_conflict")
        return {"operation_key": row["operation_key"], "verified_outcome": row["verified_outcome"]}

    async def record_reported(self, conn: Any, *, invocation: RecoveryInvocation, operation_key: str, status: str) -> None:
        try:
            await self._authority(conn, invocation)
        except RecoveryOperationError:
            if (self.authorize_completion_tail is None or status not in {"completed", "failed"}
                or await self.authorize_completion_tail(conn, invocation=invocation) is not True):
                raise RecoveryOperationError("recovery_operation_authority_stale") from None
        row = await self.persistence.get_operation(conn, scope=invocation.scope, operation_key=operation_key)
        if row is None or any(row[key] != value for key, value in {
            "source_run_id": invocation.run_id, "source_attempt_id": invocation.attempt_id,
            "source_generation": invocation.owner_generation, "native_tool_use_id": invocation.invocation_id,
            "tool_identity": invocation.tool_identity,
        }.items()) or status not in {"completed", "failed", "unknown"}:
            raise RecoveryOperationError("recovery_operation_report_invalid")
        if row.get("reported_status") is not None:
            if row["reported_status"] == status:
                return
            raise RecoveryOperationError("recovery_operation_report_conflict")
        await self.persistence.record_reported(conn, scope=invocation.scope, operation_key=operation_key, status=status)

    async def bind_invocation(
        self, conn: Any, *, invocation: RecoveryInvocation, operation_key: str,
        frozen_binding_ref: str, public_summary: str,
    ) -> None:
        authority = await self._authority(conn, invocation)
        if not authority.requires_recovery_gate or not isinstance(frozen_binding_ref, str) or not 0 < len(frozen_binding_ref) <= 512:
            raise RecoveryOperationError("recovery_operation_binding_invalid")
        operation = await self.persistence.get_operation(conn, scope=invocation.scope, operation_key=operation_key)
        if operation is None or operation["tool_identity"] != invocation.tool_identity or (
            operation["source_run_id"], operation["source_attempt_id"]
        ) == (invocation.run_id, invocation.attempt_id):
            raise RecoveryOperationError("recovery_operation_source_invalid")
        # This port must read a host frozen recovery join or a host question identity;
        # a tool-name/input match is not a source/current-operation association.
        if await self.authorize_frozen_binding(
            conn, invocation=invocation, operation=operation, frozen_binding_ref=frozen_binding_ref,
        ) is not True:
            raise RecoveryOperationError("recovery_operation_binding_unverified")
        summary = self._summary(public_summary)
        await self.persistence.insert_binding(
            conn, invocation=invocation, operation_key=operation_key,
            frozen_binding_ref=frozen_binding_ref, public_summary=summary,
        )
        binding = await self.persistence.get_binding(conn, invocation=invocation, for_update=True)
        if binding is None or (binding["operation_key"], binding["frozen_binding_ref"], binding["public_summary"]) != (
            operation_key, frozen_binding_ref, summary,
        ):
            raise RecoveryOperationError("recovery_operation_binding_conflict")

    async def _locked_binding(self, conn: Any, invocation: RecoveryInvocation) -> tuple[dict[str, Any], dict[str, Any]]:
        binding = await self.persistence.get_binding(conn, invocation=invocation)
        if binding is None:
            raise RecoveryOperationError("recovery_operation_binding_missing")
        # Run authority -> source operation -> current binding is the common lock order.
        operation = await self.persistence.get_operation(conn, scope=invocation.scope, operation_key=binding["operation_key"])
        binding = await self.persistence.get_binding(conn, invocation=invocation, for_update=True)
        if operation is None or binding is None or operation["tool_identity"] != invocation.tool_identity:
            raise RecoveryOperationError("recovery_operation_binding_invalid")
        return operation, binding

    async def query(self, conn: Any, *, invocation: RecoveryInvocation) -> dict[str, Any]:
        authority = await self._authority(conn, invocation)
        if not authority.manual_confirmation_available and invocation.tool_identity not in self.authoritative_queries:
            return {
                "schema_version": "ai-platform.recovery-operation-evidence.v1",
                "run_id": invocation.run_id, "attempt_id": invocation.attempt_id,
                "tool_identity": invocation.tool_identity, "invocation_id": invocation.invocation_id,
                "decision": "deny", "reason_code": authority.public_summary_reason,
            }
        operation, binding = await self._locked_binding(conn, invocation)
        decision, reason = operation_permission(operation, binding)
        result = {"schema_version": "ai-platform.recovery-operation-evidence.v1",
                  "run_id": invocation.run_id, "attempt_id": invocation.attempt_id,
                  "tool_identity": invocation.tool_identity, "invocation_id": invocation.invocation_id,
                  "operation_key": operation["operation_key"], "decision": decision, "reason_code": reason}
        if decision == "allow":
            return {**result, "evidence_kind": "platform_receipt", "permission_basis": reason,
                    "verified_outcome": operation["verified_outcome"]}
        if decision == "deny":
            return result
        if not authority.manual_confirmation_available:
            return {**result, "decision": "deny", "reason_code": authority.public_summary_reason}
        if authority.public_summary is not None and binding["public_summary"] != authority.public_summary:
            raise RecoveryOperationError("recovery_operation_public_object_metadata_changed")
        if binding["question_id"] is None:
            question_id = str(uuid4())
            await self.publish_question(
                conn, tenant_id=invocation.scope.tenant_id, user_id=invocation.scope.user_id,
                run_id=invocation.run_id, attempt_id=invocation.attempt_id, question_id=question_id,
                questions=(source_operation_question(binding["public_summary"])
                           if isinstance(invocation, RecoverySourceConfirmation) else
                           operation_question(authority.public_summary or operation["public_summary"], binding["public_summary"])),
            )
            await self.persistence.attach_question(
                conn, invocation=invocation, question_id=question_id,
                expires_at=self.clock() + timedelta(seconds=RECOVERY_CONFIRMATION_TIMEOUT_SECONDS),
            )
        else:
            question_id = binding["question_id"]
            if binding["question_expires_at"] <= self.clock():
                return {**result, "decision": "deny", "reason_code": "operation_confirmation_expired"}
        return {**result, "question_id": question_id}

    async def publish_source_confirmation(
        self, conn: Any, *, confirmation: RecoverySourceConfirmation, frozen_binding_ref: str,
    ) -> dict[str, Any]:
        authority = await self._authority(conn, confirmation)
        if not authority.requires_recovery_gate or not authority.manual_confirmation_available:
            return {"decision": "deny", "reason_code": authority.public_summary_reason}
        await self.bind_invocation(
            conn, invocation=confirmation, operation_key=confirmation.invocation_id,
            frozen_binding_ref=frozen_binding_ref, public_summary=authority.public_summary,
        )
        return await self.query(conn, invocation=confirmation)

    async def source_confirmation_fact(
        self, conn: Any, *, confirmation: RecoverySourceConfirmation,
    ) -> RecoveryOperationFact | None:
        authority = await self._authority(conn, confirmation)
        if not authority.manual_confirmation_available or self.read_manual_answer is None:
            return None
        if await self.persistence.get_binding(conn, invocation=confirmation) is None:
            return None
        operation, binding = await self._locked_binding(conn, confirmation)
        if (operation["verified_outcome"] not in {"succeeded", "not_effective"}
            or operation["verified_kind"] != "manual_answer" or binding["answer_input_id"] is None
            or operation["verified_reference"] != str(binding["answer_input_id"])
            or binding["closed"] or binding["started"] or binding["authorization"] != "hold"
            or authority.public_summary != binding["public_summary"]):
            return None
        answer = await self.read_manual_answer(conn, invocation=confirmation, input_id=str(binding["answer_input_id"]))
        if (not answer or answer.get("attempt_id") != confirmation.attempt_id
            or answer.get("question_id") != binding["question_id"] or answer.get("source") != "manual"
            or answer.get("kind") != "answer" or answer.get("status") != "applied"
            or answer.get("answers") != {"q0": "o1" if operation["verified_outcome"] == "succeeded" else "o2"}):
            return None
        current = await self._authority(conn, confirmation)
        if not current.manual_confirmation_available or current.public_summary != authority.public_summary:
            return None
        return RecoveryOperationFact(current.public_summary, operation["verified_outcome"])

    async def close_invocation(self, conn: Any, *, invocation: RecoveryInvocation, reason: str) -> None:
        if reason not in {"cancelled", "timeout", "denied", "query_failed"}:
            raise RecoveryOperationError("recovery_operation_close_reason_invalid")
        # Closing only removes authority on this exact previously issued binding.
        # The callback adapter authenticates the original writer's close receipt;
        # it cannot grant a newer generation or close a different Attempt.
        await self._locked_binding(conn, invocation)
        await self.persistence.close_binding(conn, invocation=invocation, reason=reason)

    async def on_manual_answer(
        self, conn: Any, *, tenant_id: str, user_id: str, run_id: str, attempt_id: str,
        input_id: str, question_id: str, answers: dict[str, Any], source: str,
    ) -> None:
        if source != "manual":
            return
        binding = await self.persistence.find_question(
            conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id,
            attempt_id=attempt_id, question_id=question_id,
        )
        if binding is None:
            return  # Ordinary SDK questions never become recovery evidence.
        scope = RecoveryOperationScope(**{key: binding[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id")})
        binding_type = RecoverySourceConfirmation if binding.get("binding_kind") == "source_operation" else RecoveryInvocation
        invocation = binding_type(scope, run_id, attempt_id, binding["owner_generation"], binding["invocation_id"], binding["tool_identity"])
        authority = await self._authority(conn, invocation)
        if not authority.manual_confirmation_available:
            raise RecoveryOperationError(authority.public_summary_reason)
        operation, binding = await self._locked_binding(conn, invocation)
        if authority.public_summary is not None and binding["public_summary"] != authority.public_summary:
            raise RecoveryOperationError("recovery_operation_public_object_metadata_changed")
        if binding["question_id"] != question_id or binding["question_expires_at"] <= self.clock() or binding["started"] or binding["closed"]:
            raise RecoveryOperationError("recovery_operation_question_closed")
        if binding["answer_input_id"] is not None:
            if str(binding["answer_input_id"]) != input_id:
                raise RecoveryOperationError("recovery_operation_answer_conflict")
            return
        option = answers.get("q0") if set(answers) == {"q0"} else None
        options = {"o0", "o1", "o2"} if isinstance(invocation, RecoverySourceConfirmation) else {"o0", "o1", "o2", "o3"}
        if not isinstance(option, str) or option not in options:
            raise RecoveryOperationError("recovery_operation_explicit_answer_required")
        if option in {"o1", "o2"}:
            operation = await self.persistence.verify(
                conn, scope=scope, operation_key=operation["operation_key"],
                outcome="succeeded" if option == "o1" else "not_effective",
                kind="manual_answer", reference=input_id,
            )
        authorization = {"o0": "hold", "o1": "hold", "o2": "retry", "o3": "current_invocation"}[option]
        if operation["verified_outcome"] == "succeeded" or isinstance(invocation, RecoverySourceConfirmation):
            authorization = "hold"
        await self.persistence.authorize_answer(conn, invocation=invocation, input_id=input_id, authorization=authorization)

    async def probe_authority(self, transaction_factory: Callable[..., Any], *, scope: RecoveryOperationScope, operation_key: str) -> bool:
        async with transaction_factory() as conn:
            operation = await self.persistence.get_operation(conn, scope=scope, operation_key=operation_key)
        if operation is None:
            raise RecoveryOperationError("recovery_operation_source_invalid")
        query = self.authoritative_queries.get(operation["tool_identity"])
        if query is None:
            return False
        # No DB transaction spans external IO; only this registered tool's adapter
        # may interpret its terminal/idempotency state, never arbitrary MCP input.
        status = await query(operation=dict(operation))
        if not isinstance(status, AuthoritativeOperationStatus) or status.conclusive is not True or status.outcome not in {"succeeded", "not_effective"}:
            return False
        if not isinstance(status.reference, str) or not 0 < len(status.reference) <= 512:
            raise RecoveryOperationError("recovery_operation_authority_reference_invalid")
        async with transaction_factory() as conn:
            await self.persistence.get_operation(conn, scope=scope, operation_key=operation_key)
            await self.persistence.verify(conn, scope=scope, operation_key=operation_key,
                                          outcome=status.outcome, kind="authoritative_query", reference=status.reference)
        return True
