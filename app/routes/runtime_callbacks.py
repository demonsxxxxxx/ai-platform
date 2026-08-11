from dataclasses import asdict
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from app import repositories
from app.context_manifest import available_context_retrieval_tools
from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalDenied,
    ContextRetrievalInputError,
)
from app.db import transaction
from app.public_execution import (
    PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
    PUBLIC_EXECUTION_EVENT_TYPES,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_matches_binding,
    callback_token_matches,
)
from app.runtime.sandbox.contracts import (
    ExecutorCallbackEvent,
    ExecutorContextRetrievalRequest,
)
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events
from app.required_tool_contract import (
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    declaration_from_payload,
)
from app.settings import get_settings
from app.streaming.redis import (
    RedisStreamBridge,
    StreamContractError,
    StreamTransportUnavailable,
    canonical_assistant_delta_event,
    get_stream_authority,
    new_envelope,
    stable_event_id,
)
from app.storage import ObjectStorage

router = APIRouter()


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "canceled"}
_TERMINAL_EXECUTOR_CALLBACK_STATUSES = {"completed", "failed", "cancelled"}
_CAPABILITY_EVIDENCE_BINDING_FIELDS = (
    "tenant_id",
    "workspace_id",
    "user_id",
    "session_id",
    "run_id",
    "attempt_id",
)
_CAPABILITY_AUTHORITY_SCHEMA_VERSION = "ai-platform.capability-authority.v1"
_MAX_CAPABILITY_AUTHORITY_DECLARATIONS = 256


def _coalesce_public_deltas(items: list[tuple[int, str]]) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for index, text in items:
        if result and len((result[-1][1] + text).encode()) <= 8192:
            result[-1] = (result[-1][0], result[-1][1] + text)
        else:
            result.append((index, text))
    return result


def _capability_authority_declarations(
    lease: dict[str, Any],
    *,
    attempt_id: str,
) -> dict[tuple[str, str], RequiredCapabilityDeclaration]:
    lease_payload = lease.get("lease_payload_json")
    authority = (
        lease_payload.get("capability_authority")
        if isinstance(lease_payload, dict)
        else None
    )
    if (
        not isinstance(authority, dict)
        or set(authority) != {"schema_version", "attempt_id", "declarations"}
        or authority.get("schema_version") != _CAPABILITY_AUTHORITY_SCHEMA_VERSION
        or authority.get("attempt_id") != attempt_id
        or not isinstance(authority.get("declarations"), list)
        or len(authority["declarations"]) > _MAX_CAPABILITY_AUTHORITY_DECLARATIONS
    ):
        raise HTTPException(status_code=409, detail="capability_authority_unavailable")
    declarations: dict[tuple[str, str], RequiredCapabilityDeclaration] = {}
    try:
        for raw in authority["declarations"]:
            declaration = declaration_from_payload(raw)
            if declaration is None or declaration.capability_kind not in {"skill", "mcp"}:
                raise RequiredToolContractError("required_tool_declaration_mismatch")
            key = (declaration.capability_kind, declaration.canonical_identity)
            if key in declarations:
                raise RequiredToolContractError("required_tool_declaration_mismatch")
            declarations[key] = declaration
    except RequiredToolContractError as exc:
        raise HTTPException(status_code=409, detail="capability_authority_unavailable") from exc
    return declarations


def _validated_capability_evidence(
    raw: object,
    *,
    expected_binding: dict[str, str],
    declarations: dict[tuple[str, str], RequiredCapabilityDeclaration],
) -> RequiredCapabilityEvidence:
    try:
        evidence = RequiredCapabilityEvidence.from_payload(raw)
    except RequiredToolContractError as exc:
        raise HTTPException(status_code=409, detail="capability_evidence_invalid") from exc
    if any(
        getattr(evidence, field) != expected_binding[field]
        for field in _CAPABILITY_EVIDENCE_BINDING_FIELDS
    ):
        raise HTTPException(status_code=409, detail="capability_evidence_scope_mismatch")
    declaration = declarations.get(
        (evidence.capability_kind, evidence.canonical_identity)
    )
    if declaration is None:
        raise HTTPException(status_code=409, detail="capability_evidence_not_authorized")
    if evidence.declaration_sha256 != declaration.declaration_sha256:
        raise HTTPException(status_code=409, detail="capability_evidence_declaration_mismatch")
    return evidence


def _private_capability_evidence_event(
    callback: ExecutorCallbackEvent,
    *,
    run_identity: dict[str, Any],
    declarations: dict[tuple[str, str], RequiredCapabilityDeclaration],
) -> dict[str, Any] | None:
    raw = callback.capability_evidence
    if raw is None:
        return None
    if not callback.batch_id:
        raise HTTPException(status_code=409, detail="callback_batch_id_required")
    expected_binding = {
        "tenant_id": str(run_identity.get("tenant_id") or ""),
        "workspace_id": str(run_identity.get("workspace_id") or ""),
        "user_id": str(run_identity.get("user_id") or ""),
        "session_id": callback.session_id,
        "run_id": callback.run_id,
        "attempt_id": callback.attempt_id,
    }
    evidence = _validated_capability_evidence(
        raw,
        expected_binding=expected_binding,
        declarations=declarations,
    )
    return {
        "event_type": "capability_invocation_evidence",
        "stage": "capability_evidence",
        "message": "Capability invocation evidence recorded",
        "visible_to_user": False,
        "payload": asdict(evidence),
    }


def _validate_capability_evidence_lifecycle(
    rows: list[dict[str, Any]],
    *,
    expected_binding: dict[str, str],
    declarations: dict[tuple[str, str], RequiredCapabilityDeclaration],
) -> None:
    states: dict[tuple[str, str, str], str] = {}
    for row in rows:
        evidence = _validated_capability_evidence(
            row.get("payload_json") if isinstance(row, dict) else None,
            expected_binding=expected_binding,
            declarations=declarations,
        )
        key = (
            evidence.capability_kind,
            evidence.canonical_identity,
            str(evidence.tool_call_id or ""),
        )
        current = states.get(key, "")
        if evidence.lifecycle_phase == "invocation_requested":
            if current:
                raise HTTPException(
                    status_code=409,
                    detail="capability_evidence_lifecycle_invalid",
                )
            states[key] = "invocation_requested"
        elif current != "invocation_requested":
            raise HTTPException(
                status_code=409,
                detail="capability_evidence_lifecycle_invalid",
            )
        else:
            states[key] = evidence.lifecycle_phase


async def record_executor_callback(
    callback: ExecutorCallbackEvent,
) -> dict[str, object]:
    """Persist only non-terminal sandbox observations; worker owns run terminal facts."""

    if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
        raise HTTPException(
            status_code=409, detail="executor_terminal_callback_not_allowed"
        )
    callback_for_events = callback
    if callback.status == "running" and callback.new_message is not None:
        raw_delta = (
            callback.new_message["delta"]
            if "delta" in callback.new_message
            else callback.new_message.get("text")
        )
        if (
            canonical_assistant_delta_event(
                stage="message", payload={"delta": raw_delta}
            )
            is None
        ):
            callback_for_events = callback.model_copy(update={"new_message": None})
    events = callback_event_to_run_events(callback_for_events)
    public_deltas: list[tuple[int, str]] = []
    authority = None
    callback_emitted_at: str | None = None
    tenant_id = ""
    async with transaction() as conn:
        run_identity = await repositories.get_run_identity(
            conn, run_id=callback.run_id, for_update=True
        )
        if run_identity is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        if str(run_identity.get("session_id") or "") != callback.session_id:
            raise HTTPException(status_code=409, detail="callback_session_mismatch")
        if str(run_identity.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
            raise HTTPException(status_code=409, detail="run_already_terminal")
        tenant_id = str(run_identity["tenant_id"])
        runtime_lease = await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
        )
        capability_declarations = (
            _capability_authority_declarations(
                runtime_lease,
                attempt_id=callback.attempt_id,
            )
            if callback.capability_evidence is not None
            else {}
        )
        source_digest = hashlib.sha256(
            json.dumps(
                callback_for_events.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        event_batch: list[dict[str, Any]] = [
            {
                "event_type": "executor_callback",
                "stage": "executor",
                "message": f"Executor callback: {callback.status}",
                "payload": {
                    "callback_status": callback.status,
                    "attempt_id": callback.attempt_id,
                    "batch_id": callback.batch_id,
                    "progress": callback.progress,
                    "source_digest": source_digest,
                    "visible_to_user": False,
                },
            }
        ]
        capability_evidence_event = _private_capability_evidence_event(
            callback,
            run_identity=run_identity,
            declarations=capability_declarations,
        )
        if capability_evidence_event is not None:
            event_batch.append(capability_evidence_event)
        for item_index, event in enumerate(events):
            executor_event = agent_event_to_executor_event(event)
            executor_event_type = str(executor_event["event_type"])
            executor_payload = dict(executor_event["payload"])
            if executor_event_type == "assistant_delta":
                canonical_delta = canonical_assistant_delta_event(
                    stage=str(executor_event["stage"]),
                    payload=executor_payload,
                )
                if canonical_delta is None:
                    continue
                event_stage, event_message, event_payload = canonical_delta
                public_deltas.append((item_index, str(event_payload["delta"])))
                continue
            else:
                event_stage = str(executor_event["stage"])
                event_message = str(executor_event["message"])
                event_payload = executor_payload
            if executor_event_type in PUBLIC_EXECUTION_EVENT_TYPES:
                event_payload = {
                    "source": "executor_callback",
                    "source_event_type": executor_event_type,
                    "visible_to_user": False,
                }
                executor_event_type = "executor_private_event"
                event_stage = "executor"
                event_message = "Executor event withheld from public projection"
            if (
                executor_event_type not in PUBLIC_EXECUTION_EVENT_TYPES
                and executor_event_type
                not in {"assistant_delta", PUBLIC_AGENT_PROGRESS_EVENT_TYPE}
            ):
                event_payload["source"] = "executor_callback"
            event_batch.append(
                {
                    "event_type": executor_event_type,
                    "stage": event_stage,
                    "message": event_message,
                    "payload": event_payload,
                }
            )
        if public_deltas:
            authority = await get_stream_authority(
                conn, tenant_id=tenant_id, run_id=callback.run_id
            )
            if (
                authority is None
                or authority.attempt_id != callback.attempt_id
                or authority.state != "confirmed"
            ):
                raise HTTPException(
                    status_code=409, detail="sse_stream_attempt_inactive"
                )
            if not callback.batch_id:
                raise HTTPException(
                    status_code=409, detail="callback_batch_id_required"
                )
        if callback.batch_id:
            receipt = await repositories.append_event_batch(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                batch_id=callback.batch_id,
                events=event_batch,
            )
            if capability_evidence_event is not None:
                evidence_rows = await repositories.list_run_capability_evidence(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                )
                _validate_capability_evidence_lifecycle(
                    evidence_rows,
                    expected_binding={
                        "tenant_id": str(run_identity.get("tenant_id") or ""),
                        "workspace_id": str(run_identity.get("workspace_id") or ""),
                        "user_id": str(run_identity.get("user_id") or ""),
                        "session_id": callback.session_id,
                        "run_id": callback.run_id,
                        "attempt_id": callback.attempt_id,
                    },
                    declarations=capability_declarations,
                )
            if public_deltas:
                candidate = (
                    receipt.get("callback_received_at")
                    if isinstance(receipt, dict)
                    else None
                )
                if not isinstance(candidate, str) or not candidate:
                    raise HTTPException(
                        status_code=503,
                        detail="callback_batch_receipt_timestamp_unavailable",
                    )
                callback_emitted_at = candidate
        else:
            for event in event_batch:
                await repositories.append_event(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    **event,
                )
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
        )
    if public_deltas:
        # The callback receipt is already durable. Re-check the run and stream
        # authority after that commit so a concurrently committed terminal run
        # suppresses a late live delta without coupling Redis I/O to PG locks.
        async with transaction() as conn:
            current_run = await repositories.get_run_identity(
                conn,
                run_id=callback.run_id,
            )
            if (
                current_run is None
                or str(current_run.get("tenant_id") or "") != tenant_id
                or str(current_run.get("session_id") or "") != callback.session_id
            ):
                raise HTTPException(
                    status_code=409, detail="callback_run_authority_changed"
                )
            if str(current_run.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
                return {"accepted": True, "event_count": len(event_batch)}
            await _require_current_runtime_attempt(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
            )
            authority = await get_stream_authority(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
            )
        if (
            authority is None
            or authority.attempt_id != callback.attempt_id
            or authority.state != "confirmed"
            or callback_emitted_at is None
        ):
            raise HTTPException(status_code=409, detail="sse_stream_attempt_inactive")
        bridge = RedisStreamBridge()
        try:
            for item_index, delta in _coalesce_public_deltas(public_deltas):
                await bridge.append(
                    new_envelope(
                        event_id=stable_event_id(
                            tenant_scope_value=authority.tenant_scope,
                            run_id=callback.run_id,
                            attempt_id=callback.attempt_id,
                            batch_id=str(callback.batch_id),
                            item_index=item_index,
                        ),
                        tenant_scope_value=authority.tenant_scope,
                        run_id=callback.run_id,
                        attempt_id=callback.attempt_id,
                        stream_incarnation=authority.stream_incarnation,
                        event_type="assistant_text_delta",
                        payload={"delta": delta},
                        emitted_at=callback_emitted_at,
                    )
                )
        except StreamContractError as exc:
            if str(exc) != "stream_terminal_closed":
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StreamTransportUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="sse_stream_unavailable"
            ) from exc
        finally:
            await bridge.aclose()
    return {"accepted": True, "event_count": len(event_batch)}


async def _require_current_runtime_attempt(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    leases = await repositories.list_current_sandbox_runtime_leases_for_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    if len(leases) != 1:
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_inactive")
    lease = leases[0]
    payload = lease.get("lease_payload_json") if isinstance(lease, dict) else None
    persisted_attempt_id = str(lease.get("attempt_id") or "") if isinstance(lease, dict) else ""
    payload_attempt_id = str(payload.get("attempt_id") or "") if isinstance(payload, dict) else ""
    if (persisted_attempt_id or payload_attempt_id) != attempt_id or (
        persisted_attempt_id and payload_attempt_id and persisted_attempt_id != payload_attempt_id
    ):
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_mismatch")
    return lease


def _require_valid_callback_token(
    provided_token: str | None,
    callback_token_id: str,
    *,
    run_id: str,
    attempt_id: str,
) -> None:
    expected_token = get_settings().sandbox_callback_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="callback_token_not_configured",
        )
    binding = CallbackTokenBinding(run_id=run_id, attempt_id=attempt_id)
    if not callback_token_id_matches_binding(callback_token_id, binding):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_callback_token",
        )
    if not callback_token_matches(
        secret=expected_token,
        token_id=callback_token_id,
        provided_token=provided_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_callback_token",
        )


@router.post("/runtime/callbacks/executor")
async def executor_callback(
    callback: ExecutorCallbackEvent,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> dict[str, object]:
    _require_valid_callback_token(
        callback_token,
        callback.callback_token_id,
        run_id=callback.run_id,
        attempt_id=callback.attempt_id,
    )
    return await record_executor_callback(callback)


@router.post("/runtime/callbacks/context-retrieval")
async def executor_context_retrieval_callback(
    request: ExecutorContextRetrievalRequest,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> dict[str, object]:
    """Broker one exact snapshot-authorized retrieval without exposing backend credentials."""

    _require_valid_callback_token(
        callback_token,
        request.callback_token_id,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
    )
    async with transaction() as conn:
        run_identity = await repositories.get_run_identity(conn, run_id=request.run_id, for_update=True)
        if run_identity is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        if str(run_identity.get("session_id") or "") != request.session_id:
            raise HTTPException(status_code=409, detail="callback_session_mismatch")
        if str(run_identity.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
            raise HTTPException(status_code=409, detail="run_already_terminal")
        tenant_id = str(run_identity.get("tenant_id") or "")
        workspace_id = str(run_identity.get("workspace_id") or "")
        user_id = str(run_identity.get("user_id") or "")
        agent_id = str(run_identity.get("agent_id") or "")
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
        )
        snapshot = await repositories.get_bound_executor_context_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=request.session_id,
            run_id=request.run_id,
        )
        if snapshot is None:
            raise HTTPException(status_code=409, detail="context_snapshot_unavailable")
        payload = snapshot.get("payload_json") if isinstance(snapshot, dict) else None
        manifest = payload.get("context_manifest") if isinstance(payload, dict) else None
        if request.action not in available_context_retrieval_tools(manifest):
            raise HTTPException(status_code=403, detail="context_retrieval_not_authorized")
        identity = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "session_id": request.session_id,
            "run_id": request.run_id,
            "agent_id": agent_id,
        }
        retrieval = ContextRetrievalAuthority.for_broker_connection(conn, ObjectStorage())
        try:
            result = await retrieval.execute(request.action, identity, request.arguments)
        except ContextRetrievalInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ContextRetrievalDenied as exc:
            reason = str(exc)
            if reason in {
                "context_file_too_large",
                "context_artifact_too_large",
                "context_file_size_required",
                "context_artifact_size_required",
            }:
                raise HTTPException(status_code=413, detail=reason) from exc
            raise HTTPException(status_code=403, detail="context_scope_denied") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="context_retrieval_failed") from exc
        await repositories.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=request.run_id,
            event_type="context_retrieved",
            stage="context",
            message="Sandbox context retrieval completed",
            payload={
                "action": request.action,
                "result": "allowed",
                "visible_to_user": False,
            },
        )
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
        )
    return {"result": result}


@router.post("/runtime/callbacks/tool-permission")
async def executor_tool_permission_callback(
) -> dict[str, object]:
    """Retired callback endpoint: never deserialize or resolve a permission request."""

    raise HTTPException(status_code=status.HTTP_410_GONE, detail="tool_permission_runtime_approval_removed")
