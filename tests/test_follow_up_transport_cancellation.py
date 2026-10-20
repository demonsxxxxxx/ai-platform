"""Deterministic evidence that a timed-out completion cannot overlap a second one.

The transport is a real `httpx.AsyncClient` over a stub async transport that holds
the request open until released, so a second request can only proceed if the first
has genuinely stopped.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.execution.application import follow_up_completion as completion_module
from app.execution.infrastructure import model_upstream

TENANT = "tenant-a"
USER = "user-a"
SESSION = "session-a"
RUN = "run-a"
TURNS = [("user", "整理需求"), ("assistant", "已整理")]


@asynccontextmanager
async def fake_transaction():
    yield object()


class FollowUpCompletionRepository:
    async def run_model_completion_connection(self, _conn, **_kwargs):
        return SimpleNamespace(
            revision=7,
            model_value="gateway-model-a",
            base_url="https://gateway.example",
            api_key="synthetic-provider-key",
            max_input_tokens=128_000,
            max_output_tokens=4096,
        )


class BlockingEndpoint:
    hostname = "gateway.example"
    port = 443
    scheme = "https"
    base_url = "https://gateway.example"
    ips = ("192.0.2.10",)


class RequestLog:
    """Counts genuinely in-flight paid requests."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.active = 0
        self.max_active = 0
        self.hold = asyncio.Event()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        label = f"request-{len(self.started) + 1}"
        self.started.append(label)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self.hold.wait()
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(["补充验收标准？"], ensure_ascii=False)}}]},
            )
        finally:
            self.active -= 1
            self.finished.append(label)


def _install(monkeypatch, log: RequestLog) -> None:
    def new_async_client(timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(log.handle),
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_seconds,
        )

    monkeypatch.setattr(model_upstream, "validate_endpoint", lambda *_a, **_k: BlockingEndpoint())
    monkeypatch.setattr(model_upstream, "_new_async_client", new_async_client)
    monkeypatch.setattr(
        model_upstream,
        "_outbound_headers",
        lambda **_kwargs: {
            "content-type": "application/json",
            "accept-encoding": "identity",
            "host": "gateway.example",
            "authorization": "Bearer synthetic-key",
        },
    )


async def wait_until(predicate, *, attempts: int = 200) -> None:
    """Let the request reach the stub without a fixed sleep."""

    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition_not_reached")


def _adapter():
    return model_upstream.ModelUpstreamAdapter()


async def _request(adapter):
    return await adapter.request_async(
        base_url="https://gateway.example",
        allowed_internal_hosts="",
        api_key="synthetic-key",
        method="POST",
        path="/v1/chat/completions",
        provider="openai",
        body=b"{}",
        headers={"content-type": "application/json"},
    )


@pytest.mark.asyncio
async def test_cancelled_async_transport_closes_before_returning(monkeypatch) -> None:
    log = RequestLog()
    _install(monkeypatch, log)

    task = asyncio.ensure_future(_request(_adapter()))
    await wait_until(lambda: log.active == 1)
    assert log.started == ["request-1"]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The socket is closed and the request counted as stopped before cancellation
    # returns to the caller.
    assert log.active == 0, "the transport released its in-flight request"
    assert log.finished == ["request-1"]


@pytest.mark.asyncio
async def test_timed_out_completion_stops_the_paid_request_before_returning(
    monkeypatch,
) -> None:
    log = RequestLog()
    _install(monkeypatch, log)
    monkeypatch.setattr(completion_module, "FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(completion_module, "FOLLOW_UP_COMPLETION_CLOSE_GRACE_SECONDS", 1.0)

    service = completion_module.FollowUpSuggestionService(
        transaction_factory=fake_transaction,
        repository=FollowUpCompletionRepository(),
        upstream=_adapter(),
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key="synthetic-encryption-key",
            model_connection_allowed_internal_hosts="",
        ),
    )

    result = await service.suggest(
        tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN, turns=TURNS
    )

    assert result.suggestions == ()
    assert result.reason == "completion_invalid"
    assert log.started == ["request-1"], "one paid request for one timed-out attempt"
    assert log.active == 0, "the timed-out request stopped before suggest returned"
    assert log.finished == ["request-1"]


@pytest.mark.asyncio
async def test_a_second_completion_never_overlaps_the_timed_out_one(monkeypatch) -> None:
    log = RequestLog()
    _install(monkeypatch, log)
    monkeypatch.setattr(completion_module, "FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(completion_module, "FOLLOW_UP_COMPLETION_CLOSE_GRACE_SECONDS", 1.0)

    service = completion_module.FollowUpSuggestionService(
        transaction_factory=fake_transaction,
        repository=FollowUpCompletionRepository(),
        upstream=_adapter(),
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key="synthetic-encryption-key",
            model_connection_allowed_internal_hosts="",
        ),
    )

    first = await service.suggest(
        tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN, turns=TURNS
    )
    second = await service.suggest(
        tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN, turns=TURNS
    )

    assert first.suggestions == second.suggestions == ()
    # Two sequential attempts are two requests, but never concurrent ones.
    assert len(log.started) == 2
    assert log.max_active == 1, "no paid request overlapped another"


def test_synchronous_transport_remains_for_its_existing_consumers() -> None:
    # ModelUpstreamAdapter.request / open_stream keep serving the proxy and Worker.
    assert callable(model_upstream.ModelUpstreamAdapter.request)
    assert callable(model_upstream.ModelUpstreamAdapter.open_stream)
    assert callable(model_upstream.ModelUpstreamAdapter.parse_model_ids)
    assert callable(model_upstream.ModelUpstreamAdapter.request_async)
