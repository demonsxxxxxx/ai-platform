"""Prepare one source-bound conversation checkpoint before an Attempt exists."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from app.context.domain.checkpoint_turns import CompleteSourceTurnStream, checkpoint_source_text
from app.context.domain.conversation_authority import (
    ConversationSourceChain, extend_source_digest, source_digest_scope,
    validate_authority_receipt,
)
from app.context.domain.provider_sessions import ProviderSessionScope
from app.context.application.provider_sessions import matching_ready_provider_epoch


class CheckpointBuildRepository(Protocol):
    async def load_source(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...
    async def find(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...
    async def claim(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...
    async def assert_lease(self, conn: Any, **kwargs: Any) -> None: ...
    async def save_progress(self, conn: Any, **kwargs: Any) -> None: ...
    async def complete(self, conn: Any, **kwargs: Any) -> None: ...


SourcePageLoader = Callable[..., Awaitable[list[dict[str, Any]]]]
CheckpointLoader = Callable[..., Awaitable[dict[str, Any] | None]]
CountProviderTokens = Callable[..., Awaitable[int]]
SummarizeSource = Callable[..., Awaitable[dict[str, Any]]]


def _boundary(row: Mapping[str, Any]) -> dict[str, str]:
    value = row["created_at"]
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("conversation_checkpoint_range_invalid")
    return {"created_at": parsed.astimezone(UTC).isoformat(), "id": row["id"]}


def _future_input(summary: str, turns: list[list[dict[str, Any]]], current: str) -> str:
    return json.dumps({
        "checkpoint_summary": summary,
        "recent_turns": [[{"role": row["role"], "content": row["content"]}
                          for row in turn] for turn in turns],
        "current_user": current,
    }, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class ConversationCheckpointBuilder:
    def __init__(self, *, repository: CheckpointBuildRepository,
                 page_loader: SourcePageLoader, checkpoint_loader: CheckpointLoader,
                 count_tokens: CountProviderTokens, summarize: SummarizeSource) -> None:
        self._repository = repository
        self._page = page_loader
        self._checkpoint = checkpoint_loader
        self._count = count_tokens
        self._summarize = summarize

    async def prepare(self, *, transaction_factory: Any, tenant_id: str,
                      run_id: str, context_snapshot_id: str) -> str | None:
        async with transaction_factory() as conn:
            source = await self._repository.load_source(
                conn, tenant_id=tenant_id, run_id=run_id,
                context_snapshot_id=context_snapshot_id,
            )
        if source is None:
            raise ValueError("conversation_checkpoint_source_unavailable")
        scope = {key: source[key] for key in
                 ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")}
        receipt = validate_authority_receipt(source["conversation_authority_json"])
        if (receipt["scope"] != scope
            or receipt["through_session_generation"] != source["session_generation"]
            or (receipt["current_message_id"] is not None and (
                receipt["current_message_id"] not in source["included_message_ids"]
                or not isinstance(source["current_user_text"], str)))
            or type(source["max_input_tokens"]) is not int
            or type(source["max_output_tokens"]) is not int):
            raise ValueError("conversation_checkpoint_source_invalid")
        async with transaction_factory() as conn:
            base = await self._checkpoint(
                conn, scope=scope, run_id=run_id,
                checkpoint_id=receipt["base_checkpoint_id"],
            ) if receipt["base_checkpoint_id"] else None
        if base and (base["source_sha256"] != receipt["base_checkpoint_source_sha256"]
                     or base["summary_sha256"] != receipt["base_checkpoint_summary_sha256"]
                     or base["message_count"] != receipt["message_count"] - receipt["tail_message_count"]):
            raise ValueError("conversation_checkpoint_base_invalid")
        if receipt["tail_message_count"] == 0:
            return base["id"] if base else None

        def new_chain() -> ConversationSourceChain:
            return ConversationSourceChain(
                scope, receipt["through_session_generation"], run_id,
                receipt["current_message_id"],
                predecessor_digest=base["source_sha256"] if base else None,
                base_checkpoint_id=base["id"] if base else None,
                base_checkpoint_summary_sha256=base["summary_sha256"] if base else None,
                predecessor_message_count=base["message_count"] if base else 0,
                predecessor_range_start=base["range_start"] if base else None,
                predecessor_range_end=base["range_end"] if base else None,
            )

        async def pages(chain: ConversationSourceChain):
            while True:
                async with transaction_factory() as conn:
                    page = await self._page(
                        conn, tenant_id=scope["tenant_id"], workspace_id=scope["workspace_id"],
                        user_id=scope["user_id"], session_id=scope["session_id"],
                        run_id=run_id, limit=4, oldest_first=True,
                        after_created_at=chain.range_end["created_at"] if chain.range_end else None,
                        after_id=chain.range_end["id"] if chain.range_end else None,
                    )
                if (not isinstance(page, list) or len(page) > 4
                    or sum(len(row["content"].encode("utf-8")) for row in page) > 1024 * 1024):
                    raise ValueError("conversation_checkpoint_source_page_invalid")
                chain.add_page(page)
                yield page
                if len(page) < 4:
                    break

        first = new_chain()
        turns = CompleteSourceTurnStream()
        turn_count = 0
        async for page in pages(first):
            turn_count += len(turns.add_page(page))
        turn_count += int(turns.finish() is not None)
        verified = first.receipt()
        if any(verified[key] != receipt[key] for key in
               ("message_count", "source_sha256", "range_start", "range_end",
                "tail_message_count", "tail_sha256")):
            raise ValueError("conversation_checkpoint_source_changed")
        if turn_count <= 1:
            return base["id"] if base else None
        reserve = min(8, turn_count - 1)
        build_key = hashlib.sha256(json.dumps([
            scope, run_id, context_snapshot_id, receipt["source_sha256"],
            base["id"] if base else None,
            source["model_id"], source["model_value"], source["model_gateway_revision"],
            source["max_input_tokens"], source["max_output_tokens"],
            "ai-platform.context-checkpoint.v1", "ai-platform.checkpoint-prompt.v1",
        ], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        predecessor = (base["source_sha256"] if base else
                       hashlib.sha256(source_digest_scope(**scope)).hexdigest())
        deadline = time.monotonic() + 125
        while True:
            async with transaction_factory() as conn:
                claimed = await self._repository.claim(
                    conn, scope=scope, run_id=run_id, source_snapshot_id=context_snapshot_id,
                    build_key_sha256=build_key, source_sha256=receipt["source_sha256"],
                    predecessor_checkpoint_id=base["id"] if base else None,
                    predecessor_sha256=predecessor,
                )
                if claimed is not None:
                    break
                existing = await self._repository.find(conn, scope=scope, build_key_sha256=build_key)
                if existing and existing["state"] == "ready":
                    ready = await self._checkpoint(conn, scope=scope, run_id=run_id,
                                                   checkpoint_id=existing["id"])
                    if (ready and ready["owner_run_id"] == run_id
                        and ready["source_snapshot_id"] == context_snapshot_id
                        and ready["source_sha256"] == receipt["source_sha256"]):
                        return ready["id"]
                    raise ValueError("conversation_checkpoint_ready_identity_invalid")
            if time.monotonic() >= deadline:
                raise ValueError("conversation_checkpoint_builder_busy")
            await asyncio.sleep(1)
        checkpoint_id, lease_id = claimed["id"], claimed["builder_lease_id"]
        recovered = claimed["covered_message_count"] > 0
        previous_summary = claimed["summary_text"] if recovered else base["summary_text"] if base else None
        if recovered and (not isinstance(previous_summary, str) or not previous_summary
                          or hashlib.sha256(previous_summary.encode()).hexdigest() != claimed["summary_sha256"]):
            raise ValueError("conversation_checkpoint_recovery_invalid")
        digest = predecessor
        covered_messages = base["message_count"] if base else 0
        covered_turns = 0
        start = None
        end = None
        replayed = not recovered
        pending: list[list[dict[str, Any]]] = []
        recent: deque[list[dict[str, Any]]] = deque()
        second = new_chain()
        stream = CompleteSourceTurnStream()
        current_user = (source["current_user_text"] if receipt["current_message_id"] else
                        source["input_message"] or "")

        async def assert_lease() -> None:
            async with transaction_factory() as conn:
                await self._repository.assert_lease(
                    conn, checkpoint_id=checkpoint_id, lease_id=lease_id, run_id=run_id,
                )

        async def summarize_pending() -> None:
            nonlocal previous_summary, digest, covered_messages, covered_turns, start, end, pending
            while pending:
                part_size = len(pending)
                while True:
                    part = pending[:part_size]
                    text = checkpoint_source_text(previous_summary, part)
                    if len(text.encode("utf-8")) <= 1024 * 1024:
                        await assert_lease()
                        count = await self._count(run_id=run_id, source_text=text)
                        if count <= source["max_input_tokens"]:
                            break
                    if part_size == 1:
                        raise ValueError("context_compaction_chunk_too_large")
                    part_size = max(1, part_size // 2)
                result = await self._summarize(run_id=run_id, source_text=text)
                summary = result["summary"]
                if (not isinstance(summary, str) or not summary
                    or type(result["input_tokens"]) is not int
                    or type(result["output_tokens"]) is not int):
                    raise ValueError("conversation_checkpoint_summary_invalid")
                await assert_lease()
                compressed = await self._count(run_id=run_id, source_text=summary)
                if compressed >= count:
                    raise ValueError("context_compaction_no_progress")
                flattened = [row for turn in part for row in turn]
                next_digest = extend_source_digest(digest, flattened)
                next_messages = covered_messages + len(flattened)
                next_turns = covered_turns + len(part)
                next_start = start or _boundary(flattened[0])
                next_end = _boundary(flattened[-1])
                async with transaction_factory() as conn:
                    await self._repository.save_progress(
                        conn, checkpoint_id=checkpoint_id, lease_id=lease_id, run_id=run_id,
                        source_sha256=next_digest,
                        covered_message_count=next_messages - (base["message_count"] if base else 0),
                        covered_turn_count=next_turns, range_start=next_start, range_end=next_end,
                        summary=summary, summary_sha256=hashlib.sha256(summary.encode()).hexdigest(),
                        input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
                    )
                digest, covered_messages, covered_turns = next_digest, next_messages, next_turns
                start, end, previous_summary = next_start, next_end, summary
                pending = pending[part_size:]

        async def accept_old_turn(turn: list[dict[str, Any]]) -> None:
            nonlocal digest, covered_messages, covered_turns, start, end, replayed
            if not replayed:
                digest = extend_source_digest(digest, turn)
                covered_messages += len(turn)
                covered_turns += 1
                start = start or _boundary(turn[0])
                end = _boundary(turn[-1])
                if covered_messages - (base["message_count"] if base else 0) == claimed["covered_message_count"]:
                    if (digest != claimed["source_sha256"] or end["id"] != claimed["range_end_id"]
                        or covered_turns != claimed["covered_turn_count"]):
                        raise ValueError("conversation_checkpoint_recovery_invalid")
                    replayed = True
                elif covered_messages - (base["message_count"] if base else 0) > claimed["covered_message_count"]:
                    raise ValueError("conversation_checkpoint_recovery_invalid")
                return
            pending.append(turn)
            if len(checkpoint_source_text(previous_summary, pending).encode("utf-8")) > 256 * 1024:
                await summarize_pending()

        async for page in pages(second):
            for turn in stream.add_page(page):
                recent.append(turn)
                if len(recent) > reserve:
                    await accept_old_turn(recent.popleft())
        last = stream.finish()
        if last:
            recent.append(last)
            if len(recent) > reserve:
                await accept_old_turn(recent.popleft())
        if any(second.receipt()[key] != receipt[key] for key in
               ("message_count", "source_sha256", "range_start", "range_end",
                "tail_message_count", "tail_sha256")):
            raise ValueError("conversation_checkpoint_source_changed")
        if not replayed:
            raise ValueError("conversation_checkpoint_recovery_invalid")
        await summarize_pending()
        while True:
            if previous_summary is None:
                raise ValueError("conversation_checkpoint_summary_missing")
            projected = _future_input(previous_summary, list(recent), current_user)
            if len(projected.encode("utf-8")) <= 1024 * 1024:
                await assert_lease()
                projected_tokens = await self._count(run_id=run_id, source_text=projected)
                if projected_tokens <= source["max_input_tokens"] * 9 // 10:
                    break
            if len(recent) <= 1:
                raise ValueError("context_input_too_large")
            pending.append(recent.popleft())
            await summarize_pending()
        async with transaction_factory() as conn:
            await self._repository.complete(
                conn, checkpoint_id=checkpoint_id, lease_id=lease_id,
                run_id=run_id, source_sha256=receipt["source_sha256"],
            )
            ready = await self._checkpoint(conn, scope=scope, run_id=run_id,
                                           checkpoint_id=checkpoint_id)
            if ready is None or ready["source_sha256"] != digest:
                raise ValueError("conversation_checkpoint_commit_invalid")
        return checkpoint_id


_builder: ConversationCheckpointBuilder | None = None


def configure_checkpoint_builder(builder: ConversationCheckpointBuilder) -> None:
    global _builder
    _builder = builder


async def prepare_checkpoint_for_run(*, transaction_factory: Any, tenant_id: str,
                                     run_id: str, context_snapshot_id: str,
                                     executor_type: str, reconciliation: bool,
                                     queue_identity: Mapping[str, str] | None = None) -> str | None:
    if executor_type != "claude-agent-worker" or reconciliation or not context_snapshot_id:
        return None
    if _builder is None:
        raise ValueError("conversation_checkpoint_builder_unavailable")
    async with transaction_factory() as conn:
        source = await _builder._repository.load_source(
            conn, tenant_id=tenant_id, run_id=run_id,
            context_snapshot_id=context_snapshot_id,
        )
        if source is None or not isinstance(source.get("conversation_authority_json"), dict):
            return None
        if queue_identity is not None and any(
            source[key] != queue_identity[key] for key in
            ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")
        ):
            raise ValueError("conversation_checkpoint_queue_identity_mismatch")
        receipt = validate_authority_receipt(source["conversation_authority_json"])
        scope = ProviderSessionScope(*[source[key] for key in
                                      ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")])
        ready = await matching_ready_provider_epoch(
            conn, scope=scope, run_id=run_id, source_sha256=receipt["source_sha256"],
            message_count=receipt["message_count"],
        )
    if ready:
        return None
    return await _builder.prepare(
        transaction_factory=transaction_factory, tenant_id=tenant_id,
        run_id=run_id, context_snapshot_id=context_snapshot_id,
    )
