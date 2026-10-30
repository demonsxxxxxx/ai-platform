"""Bind Execution effect evidence to authenticated callbacks and owner questions."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.execution.api import (
    RecoveryInvocation, RecoveryInvocationAuthority, RecoverySourceConfirmation, RecoveryOperationScope,
    RecoveryOperationsService, RecoveryOperationError, validate_recovery_operation_facts,
)
from app.execution.infrastructure.recovery_operations_postgres import PostgresRecoveryOperations
from app.platform.public_payload import sanitize_public_text
from app.runs.application.inputs import RunInputsService
from app.runs.infrastructure import inputs_postgres


def recovery_operation_sources(recovery: dict) -> list[dict]:
    """Frozen operation authority stays separate from the latest native restore epoch."""
    if recovery.get("state") != "recovery_candidate":
        return []
    sources = recovery.get("operation_sources", [recovery])
    keys = ("source_run_id", "source_attempt_id", "provider_epoch_id", "final_sequence")
    if (not isinstance(sources, list) or len(sources) > 128
        or any(not isinstance(source, dict) or any(source.get(key) is None for key in keys)
               for source in sources)):
        raise RecoveryOperationError("recovery_operation_source_invalid")
    return [{key: source[key] for key in keys} for source in sources]


async def _verified_native_input(conn: Any, *, invocation: RecoveryInvocation,
                                 recovery: dict, subjects: list, native_id: str):
    from app.bootstrap.claude_mcp import prepare_claude_mcp
    from app.context import api as context_api

    registration = prepare_claude_mcp({item["identity"]: item for item in subjects
                                      if isinstance(item, dict) and isinstance(item.get("identity"), str)}, {})
    native_tool_name = registration.sdk_names.get(invocation.tool_identity)
    if native_tool_name is None:
        raise context_api.ProviderSessionConflictError("provider_session_native_invocation_invalid")
    return await context_api.read_verified_native_invocation(
        conn, scope=invocation.scope.mapping(), epoch_id=recovery.get("provider_epoch_id"),
        source_run_id=recovery.get("source_run_id"), source_attempt_id=recovery.get("source_attempt_id"),
        final_sequence=recovery.get("final_sequence"), invocation_id=native_id,
        native_tool_name=native_tool_name,
    )


async def publish_recovery_source_questions(conn: Any, *, run: dict, attempt_id: str) -> dict:
    cursor = await conn.execute(
        """select owner_generation, execution_spec_json->'context_pack'->'conversation_context'->'recovery' as recovery
           from run_attempts where tenant_id=%s and run_id=%s and id=%s""",
        (run["tenant_id"], run["id"], attempt_id),
    )
    attempt = await cursor.fetchone()
    recovery = (attempt or {}).get("recovery") or {}
    if recovery.get("state") != "recovery_candidate":
        return {"recovery_confirmation_pending": False, "recovery_confirmation_reason": None}
    scope = RecoveryOperationScope(**{key: run[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id")})
    service, _ = build_recovery_operations()
    pending, reason, facts = False, None, []
    for source in recovery_operation_sources(recovery):
        cursor = await conn.execute(
            """select operation_key,tool_identity,verified_outcome from execution_recovery_operations
               where tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
                 and source_run_id=%s and source_attempt_id=%s
               order by operation_key""",
            (*scope.mapping().values(), source["source_run_id"], source["source_attempt_id"]),
        )
        for operation in await cursor.fetchall():
            confirmation = RecoverySourceConfirmation(scope, run["id"], attempt_id,
                attempt["owner_generation"], operation["operation_key"], operation["tool_identity"])
            if operation["verified_outcome"] != "unknown":
                fact = await service.source_confirmation_fact(conn, confirmation=confirmation)
                if fact is not None:
                    facts.append(fact.mapping())
                continue
            result = await service.publish_source_confirmation(
                conn, confirmation=confirmation, frozen_binding_ref=source["provider_epoch_id"],
            )
            pending = pending or result["decision"] == "wait"
            if result["decision"] == "deny":
                reason = result["reason_code"]
    validate_recovery_operation_facts(facts)
    return {"recovery_confirmation_pending": pending, "recovery_confirmation_reason": reason,
            "recovery_operation_facts": facts}


async def _current_authority(conn: Any, *, invocation: RecoveryInvocation):
    cursor = await conn.execute(
        """select attempt.execution_spec_json->'context_pack'->'conversation_context' as frozen,
                  attempt.execution_spec_json->'input'->'_runtime_tool_policy_subjects' as subjects
           from runs owner join run_attempts attempt on attempt.tenant_id=owner.tenant_id
             and attempt.run_id=owner.id and attempt.id=%s
           where owner.tenant_id=%s and owner.workspace_id=%s and owner.user_id=%s
             and owner.session_id=%s and owner.id=%s and owner.status='running'
             and owner.cancel_requested_at is null and attempt.status='running'
             and attempt.owner_generation=%s for update of owner,attempt""",
        (invocation.attempt_id, *invocation.scope.mapping().values(), invocation.run_id, invocation.owner_generation),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    frozen = row.get("frozen") or {}
    recovery = frozen.get("recovery") or {}
    native_id = invocation.invocation_id
    if isinstance(invocation, RecoverySourceConfirmation):
        operation = await PostgresRecoveryOperations().get_operation(
            conn, scope=invocation.scope, operation_key=invocation.invocation_id,
        )
        source = next((source for source in recovery_operation_sources(recovery)
                       if operation and source["source_run_id"] == operation["source_run_id"]
                       and source["source_attempt_id"] == operation["source_attempt_id"]), None)
        if source is None or operation["tool_identity"] != invocation.tool_identity:
            raise RecoveryOperationError("recovery_operation_source_invalid")
        native_id = operation["native_tool_use_id"]
        recovery = {**source, "state": recovery.get("state")}
    subject = next((item for item in (row.get("subjects") or [])
                    if isinstance(item, dict) and item.get("identity") == invocation.tool_identity), {})
    gated = recovery.get("state") == "recovery_candidate" and (
        isinstance(invocation, RecoverySourceConfirmation) or subject.get("write_capable") is not False)
    unavailable = RecoveryInvocationAuthority(gated, manual_confirmation_available=False)
    if isinstance(invocation, RecoverySourceConfirmation) and not all(
        subject.get(key) is True for key in ("registered", "declared", "active", "distributed")
    ):
        return replace(unavailable, public_summary_reason="recovery_operation_authority_stale")
    # Started evidence may arrive before the native tool_use append; read only on recovery.
    declaration = subject.get("public_operation_summary")
    if not gated or declaration is None:
        return unavailable
    cursor = await conn.execute(
        """select 1 from sandbox_leases where tenant_id=%s and workspace_id=%s and user_id=%s
           and session_id=%s and run_id=%s and attempt_id=%s and status='active'
           and released_at is null and expires_at>clock_timestamp()
           and lease_payload_json->>'owner_generation'=%s for share""",
        (*invocation.scope.mapping().values(), invocation.run_id, invocation.attempt_id, str(invocation.owner_generation)),
    )
    if await cursor.fetchone() is None:
        raise RecoveryOperationError("recovery_operation_authority_stale")
    from app.mcp import api as mcp_api
    from app.context import api as context_api
    from app.auth import is_ai_admin
    from app.principal_authority import resolve_current_principal, PrincipalAuthorityDenied
    from app.platform.postgres.errors import RepositoryAuthorizationError, RepositoryConflictError
    cursor = await conn.execute(
        """select 1 from mcp_servers server join tenant_capability_distributions distribution
             on distribution.tenant_id=server.tenant_id and distribution.capability_kind='mcp_server'
               and distribution.capability_id=server.name
           where server.tenant_id=%s and server.name=%s for share of server,distribution""",
        (invocation.scope.tenant_id, subject.get("mcp_server")),
    )
    if await cursor.fetchone() is None:
        return unavailable
    try:
        principal = await resolve_current_principal(user_id=invocation.scope.user_id, tenant_id=invocation.scope.tenant_id)
        tools = await mcp_api.authorize_selected_chat_mcp_tools(
            conn, tenant_id=invocation.scope.tenant_id, tool_ids=[subject["capability_id"]],
            principal_department_id=principal.department_id, principal_department_ids=principal.department_ids,
            principal_roles=principal.roles,
            is_admin=is_ai_admin(principal), permissions=principal.permissions,
        )
    except (PrincipalAuthorityDenied, RepositoryAuthorizationError, RepositoryConflictError):
        return replace(unavailable, public_summary_reason="recovery_operation_authority_stale")
    if len(tools) != 1 or tools[0].get("public_operation_summary") != declaration:
        return replace(unavailable, public_summary_reason="recovery_operation_public_object_metadata_changed")
    cursor = await conn.execute(
        """select execution_spec_json->'input'->'_runtime_tool_policy_subjects' as subjects
           from run_attempts where tenant_id=%s and run_id=%s and id=%s""",
        (invocation.scope.tenant_id, recovery.get("source_run_id"), recovery.get("source_attempt_id")),
    )
    source = await cursor.fetchone()
    all_source_subjects = (source or {}).get("subjects") or []
    source_subjects = [item for item in all_source_subjects
                       if isinstance(item, dict) and item.get("identity") == invocation.tool_identity]
    if len(source_subjects) != 1 or source_subjects[0].get("public_operation_summary") != declaration:
        return replace(unavailable, public_summary_reason="recovery_operation_public_object_metadata_changed")
    try:
        native_input = await _verified_native_input(
            conn, invocation=invocation, recovery=recovery,
            subjects=all_source_subjects, native_id=native_id,
        )
        summary = mcp_api.public_operation_summary(declaration, native_input)
    except context_api.ProviderSessionConflictError:
        return replace(unavailable, public_summary_reason="recovery_operation_native_evidence_invalid")
    except ValueError as exc:
        return replace(unavailable, public_summary_reason=str(exc))
    # Row locks do not stop lease expiry while external authority or ACK reads await.
    cursor = await conn.execute(
        """select 1 from runs owner join run_attempts attempt
             on attempt.tenant_id=owner.tenant_id and attempt.run_id=owner.id and attempt.id=%s
           join sandbox_leases lease on lease.tenant_id=owner.tenant_id and lease.run_id=owner.id
             and lease.attempt_id=attempt.id and lease.workspace_id=owner.workspace_id
             and lease.user_id=owner.user_id and lease.session_id=owner.session_id
           where owner.tenant_id=%s and owner.workspace_id=%s and owner.user_id=%s
             and owner.session_id=%s and owner.id=%s and owner.status='running'
             and owner.cancel_requested_at is null and attempt.status='running'
             and attempt.owner_generation=%s and lease.status='active' and lease.released_at is null
             and lease.expires_at>clock_timestamp() and lease.lease_payload_json->>'owner_generation'=%s
           for share of owner,attempt,lease""",
        (invocation.attempt_id, *invocation.scope.mapping().values(), invocation.run_id,
         invocation.owner_generation, str(invocation.owner_generation)),
    )
    if await cursor.fetchone() is None:
        raise RecoveryOperationError("recovery_operation_authority_stale")
    return RecoveryInvocationAuthority(True, public_summary=summary)



async def _completion_tail_authority(conn: Any, *, invocation: RecoveryInvocation) -> bool:
    from app.runs.domain.cancel_tail import cancel_tail_receipt_allowed
    from datetime import datetime, timezone

    cursor = await conn.execute(
        """select row_to_json(owner) as run, row_to_json(attempt) as attempt,
                  row_to_json(lease) as lease
           from runs owner join run_attempts attempt on attempt.tenant_id=owner.tenant_id
             and attempt.run_id=owner.id and attempt.id=%s
           join sandbox_leases lease on lease.tenant_id=owner.tenant_id and lease.run_id=owner.id
             and lease.attempt_id=attempt.id and lease.workspace_id=owner.workspace_id
             and lease.user_id=owner.user_id and lease.session_id=owner.session_id
           where owner.tenant_id=%s and owner.id=%s and lease.status='active'
             and lease.released_at is null and lease.expires_at>clock_timestamp()
           for update of owner,attempt,lease""",
        (invocation.attempt_id, invocation.scope.tenant_id, invocation.run_id),
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        return False
    row = rows[0]
    # JSON timestamps are converted back only for the pure Runs fence check.
    lease = dict(row["lease"])
    lease["expires_at"] = datetime.fromisoformat(lease["expires_at"])
    if any(row["run"].get(key) != value for key, value in invocation.scope.mapping().items()):
        return False
    return cancel_tail_receipt_allowed(
        run=row["run"], attempt=row["attempt"], lease=lease,
        tenant_id=invocation.scope.tenant_id, run_id=invocation.run_id,
        attempt_id=invocation.attempt_id, owner_generation=invocation.owner_generation,
        operation="tool_completion", operation_started=True, now=datetime.now(timezone.utc),
    )


async def _frozen_binding(conn: Any, *, invocation: RecoveryInvocation, operation: dict, frozen_binding_ref: str) -> bool:
    cursor = await conn.execute(
        """select execution_spec_json->'context_pack'->'conversation_context'->'recovery' as recovery
           from run_attempts where tenant_id=%s and run_id=%s and id=%s and owner_generation=%s""",
        (invocation.scope.tenant_id, invocation.run_id, invocation.attempt_id, invocation.owner_generation),
    )
    row = await cursor.fetchone()
    recovery = (row or {}).get("recovery") or {}
    if isinstance(invocation, RecoverySourceConfirmation):
        source = next((source for source in recovery_operation_sources(recovery)
                       if source["source_run_id"] == operation["source_run_id"]
                       and source["source_attempt_id"] == operation["source_attempt_id"]
                       and source["provider_epoch_id"] == frozen_binding_ref), None)
        return (source is not None and invocation.invocation_id == operation["operation_key"]
                and operation["tool_identity"] == invocation.tool_identity)
    # A resumed pending native call keeps its persisted tool_use_id; names alone never join.
    if not (frozen_binding_ref == recovery.get("provider_epoch_id")
            and operation["source_run_id"] == recovery.get("source_run_id")
            and operation["source_attempt_id"] == recovery.get("source_attempt_id")
            and operation["native_tool_use_id"] == invocation.invocation_id):
        return False
    cursor = await conn.execute(
        """select execution_spec_json->'input'->'_runtime_tool_policy_subjects' as subjects
           from run_attempts where tenant_id=%s and run_id=%s and id=%s""",
        (invocation.scope.tenant_id, recovery["source_run_id"], recovery["source_attempt_id"]),
    )
    source = await cursor.fetchone()
    from app.context import api as context_api
    try:
        await _verified_native_input(
            conn, invocation=invocation, recovery=recovery,
            subjects=(source or {}).get("subjects") or [], native_id=invocation.invocation_id,
        )
    except context_api.ProviderSessionConflictError:
        return False
    return True


def build_recovery_operations() -> tuple[RecoveryOperationsService, RunInputsService]:
    inputs = RunInputsService(inputs_postgres, sanitize_public_text)
    async def read_manual_answer(conn: Any, *, invocation: RecoveryInvocation, input_id: str):
        return await inputs_postgres.get_input(conn, tenant_id=invocation.scope.tenant_id,
            run_id=invocation.run_id, input_id=input_id)
    service = RecoveryOperationsService(
        PostgresRecoveryOperations(), _current_authority, _frozen_binding,
        inputs.publish_platform_question, sanitize_public_text,
        authorize_completion_tail=_completion_tail_authority, read_manual_answer=read_manual_answer,
    )
    async def answer(conn: Any, **kwargs: Any) -> None:
        await service.on_manual_answer(conn, **kwargs)
        binding = await service.persistence.find_question(
            conn, **{key: kwargs[key] for key in (
                "tenant_id", "user_id", "run_id", "attempt_id", "question_id",
            )},
        )
        if binding is not None:
            await inputs_postgres.acknowledge_inputs(
                conn, **{key: kwargs[key] for key in ("tenant_id", "run_id", "attempt_id")},
                input_ids=[kwargs["input_id"]],
            )
    from app.context.api import provider_session_verification_scope
    return service, replace(inputs, on_manual_answer=answer, prepare_verification=provider_session_verification_scope)
