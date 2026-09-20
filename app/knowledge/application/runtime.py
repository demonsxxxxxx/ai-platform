"""Application orchestration for bounded pre-Engine Knowledge retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from app.knowledge.application.provider import KnowledgeProvider
from app.knowledge.domain import (
    KnowledgeError,
    KnowledgeEvidence,
    KnowledgeRetrievalPolicy,
    ProviderCallControl,
    ProviderRetrievalRequest,
    ProviderRetrievalResult,
    RunKnowledgeSnapshot,
    RunKnowledgeSourceSnapshot,
    canonical_engine_evidence,
)


TransactionFactory = Callable[[], Any]
CancellationCheck = Callable[[], Awaitable[bool]]
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
RandomValue = Callable[[], float]


class KnowledgeRuntimeRepository(Protocol):
    async def load_run_knowledge_snapshot(
        self, conn: Any, *, tenant_id: str, run_id: str
    ) -> dict[str, Any] | None: ...

    async def load_run_attempt_generation(
        self, conn: Any, *, tenant_id: str, run_id: str, attempt_id: str
    ) -> dict[str, Any] | None: ...

    async def load_connection_revision(
        self,
        conn: Any,
        *,
        tenant_id: str,
        connection_id: str,
        revision_id: str,
        revision: int,
        catalog_sync_id: str,
        lifecycle_epoch: int,
    ) -> dict[str, Any] | None: ...

    async def load_retrieval_profile(
        self, conn: Any, *, profile_id: str, revision: int
    ) -> dict[str, Any] | None: ...

    async def load_retrieval_attempt(
        self, conn: Any, **kwargs: Any
    ) -> dict[str, Any] | None: ...

    async def retrieval_deadline_is_live(
        self, conn: Any, **kwargs: Any
    ) -> bool: ...

    async def claim_retrieval_attempt(
        self, conn: Any, **kwargs: Any
    ) -> dict[str, Any]: ...

    async def request_cancellation(
        self, conn: Any, **kwargs: Any
    ) -> dict[str, Any]: ...

    async def terminalize_retrieval_attempt(
        self, conn: Any, **kwargs: Any
    ) -> dict[str, Any]: ...

    async def commit_successful_retrieval(
        self, conn: Any, **kwargs: Any
    ) -> dict[str, Any]: ...

    async def load_successful_evidence(
        self, conn: Any, **kwargs: Any
    ) -> tuple[dict[str, Any], ...]: ...

    async def resolve_citation_evidence_ids(
        self, conn: Any, **kwargs: Any
    ) -> tuple[str, ...]: ...

    async def finalize_citations(
        self, conn: Any, **kwargs: Any
    ) -> tuple[dict[str, Any], ...]: ...


class KnowledgeCredentialResolver(Protocol):
    async def resolve(self, conn: Any, **kwargs: Any) -> str: ...


class KnowledgeAuditWriter(Protocol):
    async def append(self, conn: Any, **kwargs: Any) -> str: ...


@dataclass(frozen=True, slots=True)
class KnowledgeRuntimeResult:
    """Provider-private-free result consumed by the Worker."""

    status: str
    evidence: tuple[dict[str, Any], ...] = ()
    error_code: str | None = None
    error_message: str | None = None


class KnowledgeRuntimeFailure(RuntimeError):
    """Typed safe failure consumed by the Worker's normal terminal path."""

    _MESSAGES = {
        "knowledge_access_denied": "Knowledge access is unavailable",
        "knowledge_binding_invalid": "Knowledge binding is invalid",
        "knowledge_connection_invalid": "Knowledge connection is invalid",
        "knowledge_connection_unavailable": "Knowledge connection is unavailable",
        "knowledge_no_evidence": "未在当前已授权知识库中找到可支持回答的内容。请补充关键词或换一种问法。",
        "knowledge_profile_invalid": "Knowledge retrieval policy is invalid",
        "knowledge_provider_rejected": "Knowledge provider rejected the request",
        "knowledge_provider_transient": "Knowledge provider is temporarily unavailable",
        "knowledge_query_invalid": "Knowledge query is invalid",
        "knowledge_response_invalid": "Knowledge provider response is invalid",
        "knowledge_retrieval_cancelled": "Knowledge retrieval was cancelled",
        "knowledge_retrieval_timeout": "Knowledge retrieval timed out",
        "knowledge_source_disabled": "Knowledge source is unavailable",
        "knowledge_source_missing": "Knowledge source is unavailable",
    }

    def __init__(self, code: str) -> None:
        self.code = _safe_failure_code(code)
        self.public_message = self._MESSAGES[self.code]
        super().__init__(self.public_message)


class KnowledgeProviderPermitPool:
    """Process-local outbound bound shared by Runs using one connection."""

    def __init__(self, permits_per_connection: int) -> None:
        if (
            isinstance(permits_per_connection, bool)
            or not isinstance(permits_per_connection, int)
            or not 1 <= permits_per_connection <= 64
        ):
            raise ValueError("knowledge_provider_permit_limit_invalid")
        self._limit = permits_per_connection
        self._permits: dict[tuple[str, str], asyncio.Semaphore] = {}

    @asynccontextmanager
    async def acquire(self, tenant_id: str, connection_id: str):
        key = (tenant_id, connection_id)
        permit = self._permits.setdefault(key, asyncio.Semaphore(self._limit))
        await permit.acquire()
        try:
            yield
        finally:
            permit.release()


@dataclass(frozen=True, slots=True)
class _SourceAuthority:
    source: RunKnowledgeSourceSnapshot
    revision: dict[str, Any]
    provider: KnowledgeProvider
    credential: str


@dataclass(frozen=True, slots=True)
class _RunKnowledgeContext:
    snapshot: RunKnowledgeSnapshot
    generation: int


@dataclass(frozen=True, slots=True)
class _RunKnowledgeAuthority:
    snapshot: RunKnowledgeSnapshot
    sources: tuple[_SourceAuthority, ...]
    policy: KnowledgeRetrievalPolicy
    generation: int


@dataclass(frozen=True, slots=True)
class _SourceRetrieval:
    authority: _SourceAuthority
    result: ProviderRetrievalResult


class _KnowledgeCancelled(Exception):
    pass


_TERMINAL_STATUSES = frozenset({"succeeded", "no_evidence", "failed", "cancelled"})
_PERSISTED_FAILURE_CODES = frozenset(
    {
        "knowledge_access_denied",
        "knowledge_binding_invalid",
        "knowledge_connection_invalid",
        "knowledge_connection_unavailable",
        "knowledge_no_evidence",
        "knowledge_profile_invalid",
        "knowledge_provider_rejected",
        "knowledge_provider_transient",
        "knowledge_query_invalid",
        "knowledge_response_invalid",
        "knowledge_retrieval_timeout",
        "knowledge_source_disabled",
        "knowledge_source_missing",
    }
)


def _safe_failure_code(value: str) -> str:
    return (
        value
        if value in KnowledgeRuntimeFailure._MESSAGES
        else "knowledge_connection_unavailable"
    )


def _persisted_failure_code(value: str) -> str:
    code = _safe_failure_code(value)
    return (
        code if code in _PERSISTED_FAILURE_CODES else "knowledge_connection_unavailable"
    )


def _snapshot_from_projection(
    tenant_id: str,
    run_id: str,
    row: Mapping[str, Any],
) -> RunKnowledgeSnapshot:
    try:
        raw_sources = row["sources"]
        if not isinstance(raw_sources, list):
            raise TypeError
        sources = tuple(
            RunKnowledgeSourceSnapshot(**dict(source))
            for source in raw_sources
            if isinstance(source, Mapping)
        )
        if len(sources) != len(raw_sources):
            raise TypeError
        return RunKnowledgeSnapshot(
            tenant_id=tenant_id,
            run_id=run_id,
            agent_id=str(row["agent_id"]),
            profile_revision=int(row["profile_revision"]),
            profile_content_hash=str(row["profile_content_hash"]),
            retrieval_profile_id=str(row["retrieval_profile_id"]),
            retrieval_profile_revision=int(row["retrieval_profile_revision"]),
            sources=sources,
            principal_policy_version=int(row["principal_policy_version"]),
        )
    except (KeyError, TypeError, ValueError, KnowledgeError) as exc:
        raise KnowledgeError("knowledge_binding_invalid") from exc


def _engine_evidence_from_rows(
    rows: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    evidence: list[dict[str, Any]] = []
    for row in rows:
        try:
            evidence.append(
                KnowledgeEvidence(
                    evidence_id=str(row["evidence_id"]),
                    source_id=str(row["source_id"]),
                    provider_document_id=str(row["provider_document_id"]),
                    provider_chunk_id=(
                        str(row["provider_chunk_id"])
                        if row.get("provider_chunk_id") is not None
                        else None
                    ),
                    title=str(row.get("title") or ""),
                    content=str(row["content"]),
                    provider_score=float(row["provider_score"]),
                    fused_rank=int(row["fused_rank"]),
                    position_json=row.get("position_json"),
                ).engine_projection()
            )
        except (KeyError, TypeError, ValueError, KnowledgeError) as exc:
            raise KnowledgeError("knowledge_response_invalid") from exc
    return canonical_engine_evidence(evidence)


def _evidence_id(
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    provider_key: str,
    source_id: str,
    document_id: str,
    chunk_id: str,
) -> str:
    identity = "\x1f".join(
        (
            tenant_id,
            run_id,
            attempt_id,
            provider_key,
            source_id,
            document_id,
            chunk_id,
        )
    )
    return f"kev_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _fuse_results(
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    results: tuple[_SourceRetrieval, ...],
    policy: KnowledgeRetrievalPolicy,
) -> tuple[KnowledgeEvidence, ...]:
    """Apply threshold, composite dedupe, deterministic RRF, and final bounds."""

    ranked: list[tuple[float, int, str, str, _SourceAuthority, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for source_result in sorted(
        results,
        key=lambda item: item.authority.source.ordinal,
    ):
        provider_key = source_result.authority.provider.provider_key
        source = source_result.authority.source
        for source_rank, chunk in enumerate(source_result.result.chunks, start=1):
            if chunk.provider_score < policy.score_threshold:
                continue
            identity = (
                provider_key,
                source.source_id,
                chunk.provider_document_id,
                chunk.provider_chunk_id,
            )
            if identity in seen:
                continue
            seen.add(identity)
            ranked.append(
                (
                    1.0 / (policy.rrf_constant + source_rank),
                    source.ordinal,
                    chunk.provider_document_id,
                    chunk.provider_chunk_id,
                    source_result.authority,
                    chunk,
                )
            )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))

    selected: list[KnowledgeEvidence] = []
    used_bytes = 0
    for _, _, _, _, authority, chunk in ranked:
        if len(selected) >= policy.final_top_k:
            break
        item_bytes = len(chunk.title.encode("utf-8")) + len(
            chunk.content.encode("utf-8")
        )
        if used_bytes + item_bytes > policy.max_total_evidence_bytes:
            continue
        rank = len(selected) + 1
        selected.append(
            KnowledgeEvidence(
                evidence_id=_evidence_id(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    provider_key=authority.provider.provider_key,
                    source_id=authority.source.source_id,
                    document_id=chunk.provider_document_id,
                    chunk_id=chunk.provider_chunk_id,
                ),
                source_id=authority.source.source_id,
                provider_document_id=chunk.provider_document_id,
                provider_chunk_id=chunk.provider_chunk_id,
                title=chunk.title,
                content=chunk.content,
                provider_score=chunk.provider_score,
                fused_rank=rank,
                position_json=chunk.position_json,
            )
        )
        used_bytes += item_bytes
    return tuple(selected)


async def _watch_cancellation(check: CancellationCheck) -> bool:
    while True:
        if await check():
            return True
        await asyncio.sleep(0.1)


async def _cancel_tasks(
    tasks: tuple[asyncio.Task[Any], ...],
    *,
    grace_seconds: float,
) -> None:
    pending = tuple(task for task in tasks if not task.done())
    for task in pending:
        task.cancel()
    exceeded_grace = False
    if pending:
        await asyncio.sleep(0)
        _, still_pending = await asyncio.wait(pending, timeout=max(grace_seconds, 0.0))
        exceeded_grace = bool(still_pending)
    await asyncio.gather(*tasks, return_exceptions=True)
    if exceeded_grace:
        raise KnowledgeError("knowledge_connection_unavailable")


async def _shielded_cancel_tasks(
    tasks: tuple[asyncio.Task[Any], ...],
    *,
    grace_seconds: float,
) -> None:
    cleanup = asyncio.create_task(
        _cancel_tasks(tasks, grace_seconds=grace_seconds),
        name="knowledge-retrieval-cleanup",
    )
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
    cleanup.result()
    if interrupted:
        raise asyncio.CancelledError


class KnowledgeRuntimeService:
    """Own one generation-fenced multi-source retrieval before Engine dispatch."""

    def __init__(
        self,
        *,
        transaction_factory: TransactionFactory,
        repository: KnowledgeRuntimeRepository,
        credential_vault: KnowledgeCredentialResolver,
        audit_writer: KnowledgeAuditWriter,
        providers: tuple[KnowledgeProvider, ...],
        permit_pool: KnowledgeProviderPermitPool,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        random_value: RandomValue = random.random,
    ) -> None:
        self._transaction = transaction_factory
        self._repository = repository
        self._credential_vault = credential_vault
        self._audit_writer = audit_writer
        self._providers = {provider.provider_key: provider for provider in providers}
        self._permit_pool = permit_pool
        self._clock = clock
        self._sleep = sleep
        self._random_value = random_value

    async def retrieve(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        actor_id: str,
        question: str,
        cancel_requested: CancellationCheck | None = None,
    ) -> KnowledgeRuntimeResult:
        try:
            context = await self._load_context(
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
            )
        except KnowledgeError as exc:
            return self._failed_result(exc.code)
        if context is None:
            return KnowledgeRuntimeResult(status="skipped")

        try:
            attempt = await self._load_retrieval_attempt(
                context,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
            )
            policy: KnowledgeRetrievalPolicy | None = None
            if attempt is None:
                policy = await self._load_policy(context)
                attempt = await self._claim(
                    context,
                    policy=policy,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                )
        except KnowledgeError as exc:
            return self._failed_result(exc.code)

        attempt_status = str(attempt.get("status") or "")
        if attempt_status in _TERMINAL_STATUSES:
            return await self._terminal_result(
                context,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                attempt=attempt,
            )
        remaining_ms = attempt.get("remaining_ms")
        if (
            attempt_status != "retrieving"
            or isinstance(remaining_ms, bool)
            or not isinstance(remaining_ms, int)
            or not 0 <= remaining_ms <= 60_000
        ):
            return self._failed_result("knowledge_connection_unavailable")

        started = self._clock()
        if remaining_ms == 0:
            return await self._terminalize(
                context,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                actor_id=actor_id,
                status="failed",
                result_count=0,
                retry_count=0,
                duration_ms=0,
                safe_failure_code="knowledge_retrieval_timeout",
            )

        try:
            policy = policy or await self._load_policy(context)
            authority = await self._load_authority(
                context,
                policy=policy,
                attempt_id=attempt_id,
            )
        except KnowledgeError as exc:
            return await self._terminalize(
                context,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                actor_id=actor_id,
                status="failed",
                result_count=0,
                retry_count=0,
                duration_ms=self._duration_ms(started),
                safe_failure_code=_persisted_failure_code(exc.code),
            )

        deadline = started + remaining_ms / 1000
        retry_counts = {item.source.source_id: 0 for item in authority.sources}
        result_counts = {item.source.source_id: 0 for item in authority.sources}
        try:
            source_results = await self._retrieve_sources(
                authority,
                tenant_id=tenant_id,
                question=question,
                deadline=deadline,
                retry_counts=retry_counts,
                result_counts=result_counts,
                cancel_requested=cancel_requested,
            )
            evidence = _fuse_results(
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                results=source_results,
                policy=authority.policy,
            )
            duration_ms = self._duration_ms(started)
            if not evidence:
                return await self._terminalize(
                    authority,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    actor_id=actor_id,
                    status="no_evidence",
                    result_count=sum(result_counts.values()),
                    retry_count=sum(retry_counts.values()),
                    duration_ms=duration_ms,
                    safe_failure_code="knowledge_no_evidence",
                )
            async with self._transaction() as conn:
                terminal = await self._repository.commit_successful_retrieval(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    generation=authority.generation,
                    snapshot_hash=authority.snapshot.content_hash(),
                    source_count=len(authority.snapshot.sources),
                    result_count=sum(result_counts.values()),
                    provider_retry_count=sum(retry_counts.values()),
                    duration_ms=duration_ms,
                    evidence=evidence,
                )
                await self._append_audit(
                    conn,
                    authority=authority,
                    actor_id=actor_id,
                    attempt=terminal,
                )
            return await self._terminal_result(
                authority,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                attempt=terminal,
            )
        except _KnowledgeCancelled:
            return await self._terminalize(
                authority,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                actor_id=actor_id,
                status="cancelled",
                result_count=sum(result_counts.values()),
                retry_count=sum(retry_counts.values()),
                duration_ms=self._duration_ms(started),
                safe_failure_code=None,
            )
        except KnowledgeError as exc:
            return await self._terminalize(
                authority,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                actor_id=actor_id,
                status="failed",
                result_count=sum(result_counts.values()),
                retry_count=sum(retry_counts.values()),
                duration_ms=self._duration_ms(started),
                safe_failure_code=_persisted_failure_code(exc.code),
            )
        except Exception:
            return await self._terminalize(
                authority,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                actor_id=actor_id,
                status="failed",
                result_count=sum(result_counts.values()),
                retry_count=sum(retry_counts.values()),
                duration_ms=self._duration_ms(started),
                safe_failure_code="knowledge_connection_unavailable",
            )

    async def resolve_citation_evidence_ids(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        answer: str,
    ) -> tuple[str, ...]:
        return await self._repository.resolve_citation_evidence_ids(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            answer=answer,
        )

    async def finalize_citations(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        message_id: str,
        answer: str,
        operation_id: str,
    ) -> tuple[dict[str, Any], ...]:
        return await self._repository.finalize_citations(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            message_id=message_id,
            answer=answer,
            operation_id=operation_id,
        )

    def _duration_ms(self, started: float) -> int:
        return min(max(int((self._clock() - started) * 1000), 0), 120_000)

    @staticmethod
    def _failed_result(code: str) -> KnowledgeRuntimeResult:
        failure = KnowledgeRuntimeFailure(code)
        return KnowledgeRuntimeResult(
            status="failed",
            error_code=failure.code,
            error_message=failure.public_message,
        )

    async def _load_context(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> _RunKnowledgeContext | None:
        async with self._transaction() as conn:
            snapshot_row = await self._repository.load_run_knowledge_snapshot(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
            )
            if snapshot_row is None:
                return None
            snapshot = _snapshot_from_projection(tenant_id, run_id, snapshot_row)
            generation_row = await self._repository.load_run_attempt_generation(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
            )
            if generation_row is None:
                raise KnowledgeError("knowledge_binding_invalid")
            generation = generation_row.get("generation")
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation <= 0
                or str(generation_row.get("status") or "") not in {"claimed", "running"}
                or generation_row.get("lease_valid") is not True
            ):
                raise KnowledgeError("knowledge_connection_unavailable")
            return _RunKnowledgeContext(snapshot=snapshot, generation=generation)

    async def _load_retrieval_attempt(
        self,
        context: _RunKnowledgeContext,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            return await self._repository.load_retrieval_attempt(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                generation=context.generation,
                snapshot_hash=context.snapshot.content_hash(),
            )

    async def _load_policy(
        self,
        context: _RunKnowledgeContext,
    ) -> KnowledgeRetrievalPolicy:
        async with self._transaction() as conn:
            profile_row = await self._repository.load_retrieval_profile(
                conn,
                profile_id=context.snapshot.retrieval_profile_id,
                revision=context.snapshot.retrieval_profile_revision,
            )
        if profile_row is None:
            raise KnowledgeError("knowledge_profile_invalid")
        return KnowledgeRetrievalPolicy.from_row(profile_row)

    async def _load_authority(
        self,
        context: _RunKnowledgeContext,
        *,
        policy: KnowledgeRetrievalPolicy,
        attempt_id: str,
    ) -> _RunKnowledgeAuthority:
        async with self._transaction() as conn:
            credentials: dict[str, str] = {}
            sources: list[_SourceAuthority] = []
            for source in context.snapshot.sources:
                revision = await self._repository.load_connection_revision(
                    conn,
                    tenant_id=context.snapshot.tenant_id,
                    connection_id=source.connection_id,
                    revision_id=source.connection_revision_id,
                    revision=source.connection_revision,
                    catalog_sync_id=source.connection_catalog_sync_id,
                    lifecycle_epoch=source.connection_lifecycle_epoch,
                )
                if revision is None:
                    raise KnowledgeError("knowledge_connection_unavailable")
                provider_key = str(revision.get("provider_key") or "")
                provider = self._providers.get(provider_key)
                secret_ref = str(revision.get("secret_ref") or "")
                if (
                    str(revision.get("connection_id") or "") != source.connection_id
                    or int(revision.get("revision") or 0) != source.connection_revision
                    or str(revision.get("check_status") or "") != "passed"
                    or str(revision.get("activation_state") or "") != "active"
                    or not str(revision.get("base_url") or "")
                    or not secret_ref
                    or provider is None
                ):
                    raise KnowledgeError("knowledge_connection_invalid")
                credential = credentials.get(secret_ref)
                if credential is None:
                    deadline_live = await self._repository.retrieval_deadline_is_live(
                        conn,
                        tenant_id=context.snapshot.tenant_id,
                        run_id=context.snapshot.run_id,
                        attempt_id=attempt_id,
                        generation=context.generation,
                        snapshot_hash=context.snapshot.content_hash(),
                    )
                    if not deadline_live:
                        raise KnowledgeError("knowledge_retrieval_timeout")
                    credential = await self._credential_vault.resolve(
                        conn,
                        tenant_id=context.snapshot.tenant_id,
                        secret_ref=secret_ref,
                        purpose="knowledge_provider",
                    )
                    if not isinstance(credential, str) or not credential:
                        raise KnowledgeError("knowledge_connection_invalid")
                    credentials[secret_ref] = credential
                sources.append(
                    _SourceAuthority(
                        source=source,
                        revision=revision,
                        provider=provider,
                        credential=credential,
                    )
                )
        return _RunKnowledgeAuthority(
            snapshot=context.snapshot,
            sources=tuple(sources),
            policy=policy,
            generation=context.generation,
        )

    async def _claim(
        self,
        context: _RunKnowledgeContext,
        *,
        policy: KnowledgeRetrievalPolicy,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.claim_retrieval_attempt(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                generation=context.generation,
                snapshot_hash=context.snapshot.content_hash(),
                source_count=len(context.snapshot.sources),
                overall_timeout_ms=policy.overall_timeout_ms,
            )

    async def _retrieve_sources(
        self,
        authority: _RunKnowledgeAuthority,
        *,
        tenant_id: str,
        question: str,
        deadline: float,
        retry_counts: dict[str, int],
        result_counts: dict[str, int],
        cancel_requested: CancellationCheck | None,
    ) -> tuple[_SourceRetrieval, ...]:
        concurrency = asyncio.Semaphore(authority.policy.max_parallel_sources)
        tasks = tuple(
            asyncio.create_task(
                self._retrieve_source(
                    item,
                    policy=authority.policy,
                    tenant_id=tenant_id,
                    question=question,
                    deadline=deadline,
                    concurrency=concurrency,
                    retry_counts=retry_counts,
                    result_counts=result_counts,
                ),
                name=f"knowledge-source-{item.source.source_id}",
            )
            for item in authority.sources
        )
        cancellation_task = (
            asyncio.create_task(
                _watch_cancellation(cancel_requested),
                name="knowledge-cancellation-watch",
            )
            if cancel_requested is not None
            else None
        )
        pending = set(tasks)
        results: dict[int, _SourceRetrieval] = {}
        try:
            while pending:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise KnowledgeError("knowledge_retrieval_timeout")
                wait_for = set(pending)
                if cancellation_task is not None:
                    wait_for.add(cancellation_task)
                done, _ = await asyncio.wait(
                    wait_for,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise KnowledgeError("knowledge_retrieval_timeout")
                if cancellation_task is not None and cancellation_task in done:
                    if cancellation_task.result():
                        raise _KnowledgeCancelled
                for task in done & pending:
                    pending.remove(task)
                    result = task.result()
                    results[result.authority.source.ordinal] = result
            return tuple(results[index] for index in range(len(authority.sources)))
        finally:
            cleanup = tasks + (
                (cancellation_task,) if cancellation_task is not None else ()
            )
            await _shielded_cancel_tasks(
                cleanup,
                grace_seconds=authority.policy.cancellation_grace_ms / 1000,
            )

    async def _retrieve_source(
        self,
        authority: _SourceAuthority,
        *,
        policy: KnowledgeRetrievalPolicy,
        tenant_id: str,
        question: str,
        deadline: float,
        concurrency: asyncio.Semaphore,
        retry_counts: dict[str, int],
        result_counts: dict[str, int],
    ) -> _SourceRetrieval:
        request = ProviderRetrievalRequest(
            question=question,
            provider_resource_id=authority.source.provider_resource_id,
            page_size=policy.top_k_per_source,
            candidate_pool_size=policy.candidate_pool_size,
            similarity_threshold=policy.score_threshold,
            max_query_bytes=policy.max_query_bytes,
            max_chunk_bytes=policy.max_chunk_bytes,
        )
        async with concurrency:
            for retry_index in range(policy.max_retries_per_source + 1):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise KnowledgeError("knowledge_retrieval_timeout")
                per_source_seconds = policy.per_source_timeout_ms / 1000
                control = ProviderCallControl(
                    timeout_seconds=min(per_source_seconds, remaining),
                    timeout_failure_code=(
                        "knowledge_provider_transient"
                        if remaining >= per_source_seconds
                        else "knowledge_retrieval_timeout"
                    ),
                    max_response_bytes=max(
                        1024,
                        min(2 * 1024 * 1024, policy.max_total_evidence_bytes * 4),
                    ),
                )
                try:
                    async with self._permit_pool.acquire(
                        tenant_id,
                        authority.source.connection_id,
                    ):
                        result = await authority.provider.retrieve(
                            base_url=str(authority.revision["base_url"]),
                            credential=authority.credential,
                            request=request,
                            control=control,
                        )
                    if not isinstance(result, ProviderRetrievalResult):
                        raise KnowledgeError("knowledge_response_invalid")
                    result_counts[authority.source.source_id] = len(result.chunks)
                    return _SourceRetrieval(authority=authority, result=result)
                except asyncio.CancelledError:
                    raise
                except KnowledgeError as exc:
                    if (
                        exc.code != "knowledge_provider_transient"
                        or retry_index >= policy.max_retries_per_source
                    ):
                        raise
                    retry_counts[authority.source.source_id] += 1
                    await self._sleep(self._retry_delay(policy, retry_index, deadline))
                except Exception as exc:
                    raise KnowledgeError("knowledge_connection_unavailable") from exc
        raise KnowledgeError("knowledge_retrieval_timeout")

    def _retry_delay(
        self,
        policy: KnowledgeRetrievalPolicy,
        retry_index: int,
        deadline: float,
    ) -> float:
        base = min(
            policy.retry_backoff_cap_ms / 1000,
            policy.retry_backoff_base_ms / 1000 * (2**retry_index),
        )
        random_value = min(max(float(self._random_value()), 0.0), 1.0)
        delay = min(
            policy.retry_backoff_cap_ms / 1000,
            base
            * (
                1
                - policy.retry_jitter_ratio
                + 2 * policy.retry_jitter_ratio * random_value
            ),
        )
        if self._clock() + delay >= deadline:
            raise KnowledgeError("knowledge_retrieval_timeout")
        return delay

    async def _terminalize(
        self,
        authority: _RunKnowledgeContext | _RunKnowledgeAuthority,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        actor_id: str,
        status: str,
        result_count: int,
        retry_count: int,
        duration_ms: int,
        safe_failure_code: str | None,
    ) -> KnowledgeRuntimeResult:
        try:
            async with self._transaction() as conn:
                if status == "cancelled":
                    await self._repository.request_cancellation(
                        conn,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        generation=authority.generation,
                        snapshot_hash=authority.snapshot.content_hash(),
                    )
                terminal = await self._repository.terminalize_retrieval_attempt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    generation=authority.generation,
                    snapshot_hash=authority.snapshot.content_hash(),
                    status=status,
                    source_count=len(authority.snapshot.sources),
                    result_count=result_count,
                    provider_retry_count=retry_count,
                    duration_ms=duration_ms,
                    safe_failure_code=safe_failure_code,
                )
                await self._append_audit(
                    conn,
                    authority=authority,
                    actor_id=actor_id,
                    attempt=terminal,
                )
        except Exception:
            return self._failed_result(
                safe_failure_code or "knowledge_retrieval_cancelled"
            )
        return await self._terminal_result(
            authority,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            attempt=terminal,
        )

    async def _append_audit(
        self,
        conn: Any,
        *,
        authority: _RunKnowledgeContext | _RunKnowledgeAuthority,
        actor_id: str,
        attempt: Mapping[str, Any],
    ) -> None:
        status = str(attempt.get("status") or "failed")
        await self._audit_writer.append(
            conn,
            tenant_id=authority.snapshot.tenant_id,
            actor_id=actor_id,
            action=f"knowledge.retrieval.{status}",
            target_type="run",
            target_id=authority.snapshot.run_id,
            operation_id=(
                f"knowledge-retrieval:{authority.snapshot.run_id}:"
                f"{authority.generation}"
            ),
            payload={
                "attempt_id": str(attempt.get("attempt_id") or ""),
                "duration_ms": int(attempt.get("duration_ms") or 0),
                "evidence_count": int(attempt.get("evidence_count") or 0),
                "provider_retry_count": int(attempt.get("provider_retry_count") or 0),
                "result_count": int(attempt.get("result_count") or 0),
                "safe_failure_code": (
                    str(attempt.get("safe_failure_code") or "") or None
                ),
                "source_count": int(attempt.get("source_count") or 0),
                "status": status,
            },
        )

    async def _terminal_result(
        self,
        authority: _RunKnowledgeContext | _RunKnowledgeAuthority,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        attempt: Mapping[str, Any],
    ) -> KnowledgeRuntimeResult:
        status = str(attempt.get("status") or "failed")
        if status == "succeeded":
            async with self._transaction() as conn:
                rows = await self._repository.load_successful_evidence(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    generation=authority.generation,
                    snapshot_hash=authority.snapshot.content_hash(),
                )
            try:
                evidence = _engine_evidence_from_rows(rows)
            except KnowledgeError as exc:
                return self._failed_result(exc.code)
            if not evidence:
                return self._failed_result("knowledge_response_invalid")
            return KnowledgeRuntimeResult(status="succeeded", evidence=evidence)
        if status == "no_evidence":
            return self._failed_result("knowledge_no_evidence")
        if status == "cancelled":
            return KnowledgeRuntimeResult(
                status="cancelled",
                error_code="knowledge_retrieval_cancelled",
                error_message=KnowledgeRuntimeFailure(
                    "knowledge_retrieval_cancelled"
                ).public_message,
            )
        return self._failed_result(str(attempt.get("safe_failure_code") or ""))


_service: KnowledgeRuntimeService | None = None


def configure_knowledge_runtime(service: KnowledgeRuntimeService) -> None:
    global _service
    _service = service


def _configured_service() -> KnowledgeRuntimeService:
    if _service is None:
        raise RuntimeError("knowledge_runtime_not_configured")
    return _service


async def retrieve_run_knowledge(**kwargs: Any) -> KnowledgeRuntimeResult:
    return await _configured_service().retrieve(**kwargs)


async def resolve_run_citation_evidence_ids(
    conn: Any, **kwargs: Any
) -> tuple[str, ...]:
    return await _configured_service().resolve_citation_evidence_ids(conn, **kwargs)


async def finalize_run_citations(conn: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
    return await _configured_service().finalize_citations(conn, **kwargs)


__all__ = [
    "KnowledgeProviderPermitPool",
    "KnowledgeRuntimeFailure",
    "KnowledgeRuntimeResult",
    "KnowledgeRuntimeService",
    "configure_knowledge_runtime",
    "finalize_run_citations",
    "resolve_run_citation_evidence_ids",
    "retrieve_run_knowledge",
]
