"""Authorize a locked Run candidate without importing other contexts' persistence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.runs.application.worker_capability_admission import WorkerCapabilityAuthorization
from app.runs.application.worker_dispatch_admission import (
    WorkerAdmissionOutcome,
    WorkerAuthorizedDispatchCandidate,
)


class WorkerLockedAuthorizationService:
    def __init__(
        self, *, snapshot: Any,
        skill_materialize: Callable[..., Awaitable[list[dict[str, Any]] | None]],
        profile_authorize: Callable[..., Awaitable[tuple[Any, str | None]]],
        capability_authority: Any,
        audit_authority: Any,
        distribution_authority: Any,
        failures: Any,
    ) -> None:
        self._snapshot = snapshot
        self._skill_materialize = skill_materialize
        self._profile_authorize = profile_authorize
        self._capabilities = capability_authority
        self._audits = audit_authority
        self._distribution = distribution_authority
        self._failures = failures

    async def authorize(
        self,
        conn: Any,
        *,
        payload: Any,
        locked_run: dict[str, Any],
        current_principal: Any,
        attempt_authority: Any,
        capabilities: Any,
        attempt_id: str,
        trace_id: str,
        reconciliation: bool,
    ) -> WorkerAuthorizedDispatchCandidate:
        snapshot = await self._snapshot.resolve(
            conn, payload=payload, locked_run=locked_run,
            trace_id=trace_id, reconciliation=reconciliation,
        )
        run_identity = snapshot.run_identity
        locked_run = snapshot.locked_run
        trace_id = snapshot.trace_id

        def denied(terminal: Any) -> WorkerAuthorizedDispatchCandidate:
            return WorkerAuthorizedDispatchCandidate(
                payload=terminal.payload, locked_run=locked_run,
                run_identity=run_identity, trace_id=trace_id,
                outcome=WorkerAdmissionOutcome(
                    terminal.outcome.status, terminal.outcome.error_code,
                    terminal.outcome.error_message,
                ),
                publish_after_commit=True,
            )

        if snapshot.mismatch_fields:
            return denied(await self._failures.pre_dispatch_error(
                conn, payload=payload, run_identity=run_identity,
                v4_capabilities=capabilities, attempt_lifecycle=attempt_authority,
                error_code="queue_payload_identity_mismatch",
                error_message="Queue payload identity does not match run record",
                event_stage="worker",
                event_payload={
                    "visible_to_user": False, "severity": "error", "mismatch_fields": snapshot.mismatch_fields,
                },
            ))
        locked_payload = snapshot.payload
        if not snapshot.valid:
            return denied(await self._failures.invalid_snapshot(
                conn, payload=locked_payload or payload, locked_run=locked_run,
                run_identity=run_identity, trace_id=trace_id,
                v4_capabilities=capabilities, attempt_lifecycle=attempt_authority,
            ))
        skill_manifests = await self._skill_materialize(
            conn, tenant_id=run_identity["tenant_id"],
            run_id=run_identity["run_id"], skill_manifest_refs=locked_payload.skill_manifests,
        )
        if skill_manifests is None:
            return denied(await self._failures.invalid_snapshot(
                conn, payload=locked_payload, locked_run=locked_run,
                run_identity=run_identity, trace_id=trace_id,
                v4_capabilities=capabilities, attempt_lifecycle=attempt_authority,
            ))
        locked_payload = locked_payload.model_copy(update={"skill_manifests": skill_manifests})
        locked_payload, denial_reason = await self._profile_authorize(
            conn, payload=locked_payload, principal=current_principal,
            run_identity=run_identity,
        )
        if denial_reason is not None:
            return denied(await self._failures.capability_denial(
                conn, payload=locked_payload,
                authorization=WorkerCapabilityAuthorization(
                    locked_payload, current_principal, (),
                    self._distribution.denied(
                        "agent_profile", run_identity["agent_id"], denial_reason,
                    ),
                ),
                run_identity=run_identity, trace_id=trace_id,
                v4_capabilities=capabilities, attempt_lifecycle=attempt_authority,
                policy="agent_profile_authority",
            ))
        authorization = await self._capabilities.authorize(
            conn, payload=locked_payload, run_identity=run_identity,
            attempt_id=attempt_id, current_principal=current_principal,
        )
        await self._audits.record_admission(
            conn, authorization=authorization, run_identity=run_identity, trace_id=trace_id,
        )
        if authorization.denial is not None:
            return denied(await self._failures.capability_denial(
                conn, payload=locked_payload, authorization=authorization,
                run_identity=run_identity, trace_id=trace_id,
                v4_capabilities=capabilities, attempt_lifecycle=attempt_authority,
            ))
        return WorkerAuthorizedDispatchCandidate(
            payload=authorization.payload, locked_run=locked_run,
            run_identity=run_identity, trace_id=trace_id, authorization=authorization,
        )
