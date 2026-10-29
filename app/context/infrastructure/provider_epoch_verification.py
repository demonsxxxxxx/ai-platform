"""Private two-stage native media proof for atomic cross-owner transactions.

Only the original full append receipts authenticate image-bearing entries. The
short-lived digest binds that completed check to the unchanged structured ledger
and image metadata when the owning transaction reacquires its locks.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any

from app.context.domain.provider_media import ProviderMediaTransfer
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_ENTRIES, MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
    ProviderSessionConflictError, provider_entry_json_bytes,
)
from app.context.infrastructure.provider_epoch_ledger import read_provider_ledger, verify_provider_ledger
from app.context.infrastructure.provider_images import read_epoch_images

_SCOPE = ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")
_COUNTERS = ("id", "provider_session_id", "next_sequence", "entry_count", "transcript_bytes", "structured_bytes")


@dataclass(frozen=True)
class ProviderEpochProof:
    ledger_sha256: str
    prefix_bytes: tuple[int, ...]
    native_calls: tuple[tuple[int, dict], ...]


# Task-local, bounded to one public verification context; never a persistent cache.
_active_proofs: ContextVar[dict[str, Any] | None] = ContextVar("provider_epoch_media_proofs", default=None)


async def authorized_epoch_read_plan(conn, *, tenant_id: str, run_id: str, user_id: str | None = None) -> tuple[dict, list[str]] | None:
    row = await (await conn.execute(
        """select owner.tenant_id,owner.workspace_id,owner.user_id,owner.session_id,owner.agent_id,
                  attempt.id as attempt_id,attempt.owner_generation,attempt.execution_spec_sha256,
                  attempt.execution_spec_json->'context_pack'->'conversation_context' as frozen,
                  head.current_epoch_id
           from runs owner join sessions session on session.tenant_id=owner.tenant_id
             and session.id=owner.session_id and session.workspace_id=owner.workspace_id
             and session.user_id=owner.user_id and session.agent_id=owner.agent_id and session.status='active'
           left join lateral (select id,owner_generation,execution_spec_sha256,execution_spec_json
             from run_attempts where tenant_id=owner.tenant_id and run_id=owner.id
             order by ordinal desc limit 1) attempt on true
           left join provider_session_heads head on head.tenant_id=owner.tenant_id
             and head.workspace_id=owner.workspace_id and head.user_id=owner.user_id
             and head.session_id=owner.session_id and head.agent_id=owner.agent_id and head.engine='claude'
           where owner.tenant_id=%s and owner.id=%s and (%s::text is null or owner.user_id=%s) for share of owner,session""",
        (tenant_id, run_id, user_id, user_id),
    )).fetchone()
    if row is None:
        return None
    frozen = row.get("frozen") or {}
    recovery = frozen.get("recovery") or {}
    sources = recovery.get("operation_sources", [recovery]) if recovery else []
    if not isinstance(sources, list) or len(sources) > 128 or any(not isinstance(source, dict) for source in sources):
        raise ProviderSessionConflictError("provider_session_spec_mismatch")
    epoch_ids = {row.get("current_epoch_id"), frozen.get("provider_epoch_id"), frozen.get("source_epoch_id")}
    epoch_ids.update(source.get("provider_epoch_id") for source in sources)
    epoch_ids.discard(None)
    if any(not isinstance(value, str) or not value for value in epoch_ids):
        raise ProviderSessionConflictError("provider_session_spec_mismatch")
    binding = {key: row.get(key) for key in (*_SCOPE, "attempt_id", "owner_generation", "execution_spec_sha256")}
    return binding, sorted(epoch_ids)


async def snapshot_epoch_ledger(conn, *, binding: dict, epoch_id: str):
    epoch = await (await conn.execute(
        """select * from provider_session_epochs where tenant_id=%s and workspace_id=%s
             and user_id=%s and session_id=%s and agent_id=%s and engine='claude' and id=%s for share""",
        (*(binding[key] for key in _SCOPE), epoch_id),
    )).fetchone()
    if epoch is None:
        raise ProviderSessionConflictError("provider_session_scope_mismatch")
    if (type(epoch.get("entry_count")) is not int
        or not 0 <= epoch["entry_count"] <= MAX_PROVIDER_SESSION_ENTRIES
        or type(epoch.get("transcript_bytes")) is not int
        or not 0 <= epoch["transcript_bytes"] <= MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES
        or type(epoch.get("structured_bytes")) is not int
        or epoch["structured_bytes"] < 0):
        raise ProviderSessionConflictError("provider_session_transcript_too_large")
    rows, receipts = await read_provider_ledger(conn, epoch=epoch)
    objects = await read_epoch_images(conn, epoch=epoch) if any(row.get("image_references") for row in rows) else []
    return dict(epoch), rows, receipts, objects


def ledger_fingerprint(epoch, rows, receipts, objects) -> str:
    # Required integrity stamp for the lock-free storage gap; it never replaces an ACK.
    receipt_metadata = [{key: value.isoformat() if isinstance(value, datetime) else value
                         for key, value in receipt.items()} for receipt in receipts]
    value = [{key: str(epoch[key]) if key == "provider_session_id" else epoch[key]
              for key in (*_SCOPE, *_COUNTERS)}, rows, receipt_metadata, objects]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def finish_epoch_proof(epoch, rows, receipts, media: ProviderMediaTransfer) -> ProviderEpochProof:
    if media.completed is not True or media.epoch_id != epoch["id"] or media.next_sequence != epoch["next_sequence"]:
        raise ProviderSessionConflictError("provider_session_image_snapshot_conflict")
    stamp = ledger_fingerprint(epoch, rows, receipts, media.objects)
    verified = verify_provider_ledger(epoch=epoch, rows=deepcopy(rows), receipts=receipts, media=media)
    sizes = [0]
    calls = []
    for row in verified:
        sizes.append(sizes[-1] + provider_entry_json_bytes(row["entry_json"]))
        message = row["entry_json"].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((row["sequence"], deepcopy(block)))
    return ProviderEpochProof(stamp, tuple(sizes), tuple(calls))


async def checked_epoch_proof(conn, *, epoch, rows, receipts) -> ProviderEpochProof:
    active = _active_proofs.get()
    if active is None or epoch["id"] not in active["proofs"]:
        raise ProviderSessionConflictError("provider_session_image_verification_required")
    plan = await authorized_epoch_read_plan(conn, tenant_id=active["tenant_id"], run_id=active["run_id"])
    if plan is None or plan[0] != active["binding"] or epoch["id"] not in plan[1]:
        raise ProviderSessionConflictError("provider_session_image_snapshot_conflict")
    objects = await read_epoch_images(conn, epoch=epoch)
    proof = active["proofs"][epoch["id"]]
    if proof.ledger_sha256 != ledger_fingerprint(epoch, rows, receipts, objects):
        raise ProviderSessionConflictError("provider_session_image_snapshot_conflict")
    return proof


def native_call_blocks(epoch_id: str, *, final_sequence: int) -> list[dict] | None:
    active = _active_proofs.get()
    proof = active["proofs"].get(epoch_id) if active else None
    return [deepcopy(block) for sequence, block in proof.native_calls if sequence <= final_sequence] if proof else None


@asynccontextmanager
async def provider_epoch_verification_scope(transaction_factory, *, tenant_id: str, run_id: str, transfer, user_id: str | None = None):
    inherited = _active_proofs.get()
    if inherited is not None:
        if inherited["tenant_id"] != tenant_id or inherited["run_id"] != run_id or (user_id is not None and inherited["binding"] and inherited["binding"]["user_id"] != user_id):
            raise ProviderSessionConflictError("provider_session_image_snapshot_conflict")
        yield
        return
    async with transaction_factory() as conn:
        plan = await authorized_epoch_read_plan(conn, tenant_id=tenant_id, run_id=run_id, user_id=user_id)
    proofs = {}
    if plan is not None:
        binding, epoch_ids = plan
        for epoch_id in epoch_ids:
            async with transaction_factory() as conn:
                current = await authorized_epoch_read_plan(conn, tenant_id=tenant_id, run_id=run_id, user_id=user_id)
                if current != plan:
                    raise ProviderSessionConflictError("provider_session_image_snapshot_conflict")
                epoch, rows, receipts, objects = await snapshot_epoch_ledger(conn, binding=binding, epoch_id=epoch_id)
            if not any(row.get("image_references") for row in rows):
                continue
            media = ProviderMediaTransfer(epoch_id, epoch["next_sequence"], binding["owner_generation"] or 0, objects)
            await transfer(media)
            proofs[epoch_id] = finish_epoch_proof(epoch, rows, receipts, media)
            # No image bytes or reconstructed transcript outlive this bounded transfer.
            del media, rows, receipts, objects
    token = _active_proofs.set({"tenant_id": tenant_id, "run_id": run_id,
                                "binding": plan[0] if plan else None, "proofs": proofs})
    try:
        yield
    finally:
        _active_proofs.reset(token)
