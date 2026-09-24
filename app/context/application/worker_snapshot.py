from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.context.application.checkpoints import load_ready_checkpoint
from app.context.domain.conversation import (
    ConversationContextError,
    build_executor_conversation_context,
    build_executor_conversation_context_v2,
    empty_executor_conversation_context,
)
from app.context.domain.conversation_authority import (
    ConversationSourceChain,
    validate_authority_receipt,
)
from app.context.domain.provider_sessions import (
    PROVIDER_SESSION_RESUME_CONTEXT_KEY,
    ProviderSessionContinuityError,
    ProviderSessionScope,
)

_SAFE_SNAPSHOT_MEMBER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

SnapshotLoader = Callable[..., Awaitable[dict[str, Any] | None]]
MessageLoader = Callable[..., Awaitable[list[dict[str, Any]]]]
HistoryPageLoader = Callable[..., Awaitable[list[dict[str, Any]]]]
CheckpointLoader = Callable[..., Awaitable[dict[str, Any] | None]]
ProviderEpochMatcher = Callable[..., Awaitable[bool]]
ContextProjector = Callable[[dict[str, Any]], dict[str, Any]]


async def _materialize_authorized_history(
    conn: Any, *, identity: dict[str, str], receipt: dict[str, Any],
    selected_message_ids: list[str], history_page_loader: HistoryPageLoader,
    checkpoint_loader: CheckpointLoader, native_mode: bool,
) -> dict[str, Any]:
    authority = validate_authority_receipt(receipt)
    scope = {field: identity.get(field, "") for field in
             ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")}
    if (authority["scope"] != scope
        or authority["current_message_id"] not in (None, *selected_message_ids)):
        raise ConversationContextError("conversation_authority_scope_invalid")
    frozen_base = None
    if not native_mode and authority["base_checkpoint_id"] is not None:
        frozen_base = await checkpoint_loader(
            conn, scope=scope, run_id=identity["run_id"],
            checkpoint_id=authority["base_checkpoint_id"],
        )
        if (frozen_base is None
            or frozen_base["source_sha256"] != authority["base_checkpoint_source_sha256"]
            or frozen_base["summary_sha256"] != authority["base_checkpoint_summary_sha256"]
            or frozen_base["message_count"] != authority["message_count"] - authority["tail_message_count"]
            or frozen_base["through_session_generation"] > authority["through_session_generation"]):
            raise ConversationContextError("conversation_checkpoint_receipt_invalid")
    base = frozen_base
    chain = ConversationSourceChain(
        scope=scope, through_session_generation=authority["through_session_generation"],
        current_run_id=identity["run_id"], current_message_id=authority["current_message_id"],
        predecessor_digest=base["source_sha256"] if base else None,
        base_checkpoint_id=base["id"] if base else None,
        base_checkpoint_summary_sha256=base["summary_sha256"] if base else None,
        predecessor_message_count=base["message_count"] if base else 0,
        predecessor_range_start=base["range_start"] if base else None,
        predecessor_range_end=base["range_end"] if base else None,
    )
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    while True:
        page = await history_page_loader(
            conn, tenant_id=scope["tenant_id"], workspace_id=scope["workspace_id"],
            user_id=scope["user_id"], session_id=scope["session_id"], run_id=identity["run_id"],
            limit=4, oldest_first=True,
            after_created_at=chain.range_end["created_at"] if chain.range_end else None,
            after_id=chain.range_end["id"] if chain.range_end else None,
        )
        if not isinstance(page, list) or len(page) > 4:
            raise ConversationContextError("conversation_authority_page_invalid")
        total_bytes += sum(len(row["content"].encode("utf-8")) for row in page)
        if total_bytes > 16 * 1024 * 1024 and not native_mode:
            raise ConversationContextError("conversation_source_requires_checkpoint")
        chain.add_page(page)
        if not native_mode:
            rows.extend(page)
        if len(page) < 4:
            break
    verified = chain.receipt()
    compare = (
        ("message_count", "source_sha256", "range_start", "range_end")
        if native_mode
        else (
            "message_count", "source_sha256", "range_start", "range_end",
            "tail_sha256", "tail_message_count",
        )
    )
    if any(verified[field] != authority[field] for field in compare):
        raise ConversationContextError("conversation_authority_range_invalid")
    return {
        **build_executor_conversation_context_v2(rows),
        "source_sha256": authority["source_sha256"],
        "through_session_generation": authority["through_session_generation"],
        "current_message_id": authority["current_message_id"],
        "message_count": authority["message_count"],
        "checkpoint_id": None if native_mode else (base["id"] if base else None),
        "checkpoint_source_sha256": (
            None if native_mode else (base["source_sha256"] if base else None)
        ),
        "checkpoint_summary": None if native_mode else (base["summary_text"] if base else None),
        "tail_message_count": verified["tail_message_count"],
        "native_source_verified": native_mode,
    }


async def materialize_worker_context_snapshot(
    conn: Any,
    *,
    identity: dict[str, str],
    context_snapshot_id: str,
    snapshot_loader: SnapshotLoader,
    message_loader: MessageLoader,
    context_projector: ContextProjector,
    history_page_loader: HistoryPageLoader | None = None,
    checkpoint_loader: CheckpointLoader = load_ready_checkpoint,
    provider_epoch_matcher: ProviderEpochMatcher | None = None,
) -> dict[str, Any] | None:
    scoped_snapshot = await snapshot_loader(
        conn,
        tenant_id=identity["tenant_id"],
        workspace_id=identity["workspace_id"],
        user_id=identity["user_id"],
        session_id=identity["session_id"],
        run_id=identity["run_id"],
        context_snapshot_id=context_snapshot_id,
    )
    if scoped_snapshot is None:
        return None

    raw_message_ids = scoped_snapshot.get("included_message_ids")
    raw_file_ids = scoped_snapshot.get("included_file_ids")
    if not isinstance(raw_message_ids, list) or not isinstance(raw_file_ids, list):
        return None
    if (
        any(
            not isinstance(member_id, str)
            or not _SAFE_SNAPSHOT_MEMBER_ID.fullmatch(member_id)
            for member_id in (*raw_message_ids, *raw_file_ids)
        )
    ):
        return None
    selected_message_ids = list(raw_message_ids)
    selected_file_ids = list(raw_file_ids)
    if len(selected_message_ids) != len(set(selected_message_ids)) or len(
        selected_file_ids
    ) != len(set(selected_file_ids)):
        return None
    conversation_authority = scoped_snapshot.get("conversation_authority_json")
    if conversation_authority is not None:
        if not isinstance(conversation_authority, dict):
            return None
        try:
            if identity.get("engine") == "claude":
                if provider_epoch_matcher is None or history_page_loader is None:
                    raise ProviderSessionContinuityError(
                        "provider_session_authority_verifier_unavailable"
                    )
                conversation_context = await _materialize_authorized_history(
                    conn,
                    identity=identity,
                    receipt=conversation_authority,
                    selected_message_ids=selected_message_ids,
                    history_page_loader=history_page_loader,
                    checkpoint_loader=checkpoint_loader,
                    native_mode=True,
                )
                native_mode = await provider_epoch_matcher(
                    conn, scope=ProviderSessionScope(**{key: identity[key] for key in
                                 ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")}),
                    run_id=identity["run_id"], source_sha256=conversation_context["source_sha256"],
                    message_count=conversation_context["message_count"],
                )
                conversation_context["native_source_verified"] = native_mode
            else:
                if history_page_loader is None:
                    return None
                conversation_context = await _materialize_authorized_history(
                    conn, identity=identity, receipt=conversation_authority,
                    selected_message_ids=selected_message_ids, history_page_loader=history_page_loader,
                    checkpoint_loader=checkpoint_loader, native_mode=False,
                )
        except ProviderSessionContinuityError:
            raise
        except (ConversationContextError, ValueError, KeyError, TypeError, AttributeError):
            return None
    elif selected_message_ids:
        materialized_messages = await message_loader(
            conn,
            tenant_id=identity["tenant_id"],
            workspace_id=identity["workspace_id"],
            user_id=identity["user_id"],
            session_id=identity["session_id"],
            run_id=identity["run_id"],
            limit=len(selected_message_ids),
        )
        try:
            conversation_context = build_executor_conversation_context(
                materialized_messages,
                selected_message_ids=selected_message_ids,
                current_run_id=identity["run_id"],
            )
        except ConversationContextError:
            return None
    else:
        conversation_context = empty_executor_conversation_context()

    context_ref = context_projector(scoped_snapshot)
    return {
        "context_snapshot_id": str(context_ref["context_snapshot_id"]),
        "context_snapshot": context_ref,
        "conversation_context": {
            **conversation_context,
            PROVIDER_SESSION_RESUME_CONTEXT_KEY: False,
        },
        "file_ids": selected_file_ids,
    }


def _included_count(
    row: dict[str, Any],
    field: str,
    payload: dict[str, Any],
    payload_field: str,
) -> int:
    raw = row.get(field)
    if isinstance(raw, list):
        return len(raw)
    try:
        return int(payload.get(payload_field) or 0)
    except (TypeError, ValueError):
        return 0


def _safe_context_memory_policy(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "default").strip()
    if source not in {"default", "stored", "not_recorded"}:
        source = "stored"
    try:
        retention_days = int(raw.get("retention_days") or 90)
    except (TypeError, ValueError):
        retention_days = 90
    if retention_days <= 0:
        retention_days = 90
    return {
        "source": source,
        "memory_enabled": bool(raw.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": retention_days,
    }


def context_snapshot_ref_from_row(row: dict[str, Any]) -> dict[str, Any]:
    from app.context_builder import ensure_public_context_provenance
    from app.context_manifest import (
        CONTEXT_MANIFEST_SCHEMA_VERSION,
        sanitize_context_manifest_payload,
    )
    from app.control_plane_contracts import CONTEXT_SNAPSHOT_SCHEMA_VERSION

    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    public_payload = ensure_public_context_provenance(
        payload,
        source="stored_context_snapshot",
        message_count=_included_count(
            row, "included_message_ids", payload, "message_count"
        ),
        file_count=_included_count(row, "included_file_ids", payload, "file_count"),
        artifact_count=_included_count(
            row, "included_artifact_ids", payload, "artifact_count"
        ),
        memory_record_count=_included_count(
            row,
            "included_memory_record_ids",
            payload,
            "memory_record_count",
        ),
        memory_policy_source="not_recorded",
        long_term_memory_read=False,
        preserve_stored_input_keys=True,
    )
    context_ref: dict[str, Any] = {
        "schema_version": str(
            row.get("schema_version")
            or payload.get("schema_version")
            or CONTEXT_SNAPSHOT_SCHEMA_VERSION
        ),
        "context_snapshot_id": str(row["id"]),
        "source": public_payload["used_context_summary"]["source"],
        "message_count": public_payload["referenced_materials"]["message_count"],
        "file_count": public_payload["referenced_materials"]["file_count"],
        "memory_record_count": public_payload["referenced_materials"][
            "memory_record_count"
        ],
        "referenced_materials": public_payload["referenced_materials"],
        "used_context_summary": public_payload["used_context_summary"],
        "latest_artifact_version": public_payload["latest_artifact_version"],
        "execution_tier": public_payload["execution_tier"],
        "context_pack_version": public_payload["context_pack_version"],
        "context_pack_generated_at": public_payload["context_pack_generated_at"],
    }
    memory_policy = _safe_context_memory_policy(payload.get("memory_policy"))
    if memory_policy is not None:
        context_ref["memory_policy"] = memory_policy
    context_manifest = payload.get("context_manifest")
    if (
        isinstance(context_manifest, dict)
        and context_manifest.get("schema_version")
        == CONTEXT_MANIFEST_SCHEMA_VERSION
    ):
        context_ref["context_manifest"] = sanitize_context_manifest_payload(
            context_manifest
        )
    return context_ref
