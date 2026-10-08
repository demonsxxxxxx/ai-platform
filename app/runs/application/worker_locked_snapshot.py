"""Resolve the locked Run snapshot before Worker capability admission."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerLockedSnapshot:
    locked_run: Any
    run_identity: dict[str, str]
    trace_id: str
    payload: Any | None
    mismatch_fields: list[str]
    valid: bool


class WorkerLockedSnapshotService:
    def __init__(
        self, *, identity: Callable[..., dict[str, str]],
        mismatch_fields: Callable[..., list[str]],
        load_model: Callable[..., Awaitable[dict[str, Any]]],
        model_loader: Callable[..., Awaitable[Any]],
        trace_id: Callable[..., str], parse_payload: Callable[..., Any],
        profile_identity_valid: Callable[..., bool],
        reconciliation_profile_matches: Callable[..., bool],
    ) -> None:
        self._identity = identity
        self._mismatch_fields = mismatch_fields
        self._load_model = load_model
        self._model_loader = model_loader
        self._trace_id = trace_id
        self._parse_payload = parse_payload
        self._profile_identity_valid = profile_identity_valid
        self._reconciliation_profile_matches = reconciliation_profile_matches

    async def resolve(
        self, conn: Any, *, payload: Any, locked_run: Any,
        trace_id: str, reconciliation: bool,
    ) -> WorkerLockedSnapshot:
        run_identity = self._identity(payload, locked_run)
        mismatch_fields = self._mismatch_fields(payload, run_identity)
        if mismatch_fields:
            return WorkerLockedSnapshot(
                locked_run, run_identity, trace_id, None, mismatch_fields, False,
            )
        locked_run = await self._load_model(
            locked_run, conn=conn, run_identity=run_identity,
            load_run_model_snapshot=self._model_loader,
        )
        trace_id = self._trace_id(payload, locked_run)
        locked_payload = self._parse_payload(locked_run, run_identity=run_identity)
        valid = locked_payload is not None and (
            self._profile_identity_valid(locked_payload.agent_profile or {}, locked_run)
            and (not reconciliation or self._reconciliation_profile_matches(
                payload.input, locked_payload.agent_profile or {},
            ))
        )
        return WorkerLockedSnapshot(
            locked_run, run_identity, trace_id, locked_payload, [], valid,
        )
