"""Synthetic upstream evidence for model-generated follow-up suggestions."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import AuthPrincipal
from app.conversations.application.follow_up_suggestions import (
    FOLLOW_UP_CACHE_TTL_SECONDS,
    FOLLOW_UP_CLAIM_TTL_SECONDS,
    FollowUpTargetNotFound,
    build_follow_up_suggestions,
    follow_up_cache_key,
)
from app.conversations.infrastructure.follow_up_projection import (
    public_follow_up_turns,
)
from app.conversations.transport.follow_up_suggestions import (
    build_follow_up_suggestion_router,
)
from app.execution.application.follow_up_completion import (
    FollowUpSuggestionResult,
    FollowUpSuggestionService,
)
from app.execution.domain.follow_up_suggestions import (
    FOLLOW_UP_SUGGESTION_LIMIT,
    FollowUpSuggestionError,
    build_follow_up_messages,
    parse_follow_up_suggestions,
)

TENANT = "tenant-a"
USER = "user-a"
SESSION = "session-a"
RUN = "run-a"


@asynccontextmanager
async def fake_transaction():
    yield object()


def model_answer(*questions: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(list(questions), ensure_ascii=False)}}]},
        ensure_ascii=False,
    ).encode("utf-8")


class FakeUpstream:
    """The async transport port the completion now uses."""

    def __init__(self, *, body: bytes | None = None, status: int = 200, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._body = body if body is not None else model_answer("还能补充验收标准吗？")
        self._status = status
        self._error = error

    async def request_async(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(status=self._status, body=self._body, content_type="application/json")


class FakeModelRepository:
    """Records the exact owner-scoped Run binding asked of the model plane."""

    def __init__(self, connection=None) -> None:
        self.bindings: list[dict[str, str]] = []
        self._connection = connection
        self._error: Exception | None = None

    def fails(self, error: Exception) -> "FakeModelRepository":
        self._error = error
        return self

    async def run_model_completion_connection(self, _conn, **kwargs):
        if self._error is not None:
            raise self._error
        self.bindings.append(dict(kwargs))
        return self._connection


def connection(**changes):
    values = {
        "revision": 7,
        "model_value": "gateway-model-a",
        "base_url": "https://gateway.example",
        "api_key": "synthetic-provider-key",
        "max_input_tokens": 128_000,
        "max_output_tokens": 4096,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class FakeMemoryCache:
    """Stands in for Redis so claim/TTL/store semantics stay testable."""

    def __init__(self) -> None:
        self.values: dict[str, list[str]] = {}
        self.claims: set[str] = set()
        self.claim_ttls: list[int] = []
        self.store_ttls: list[int] = []

    async def read(self, key: str):
        return self.values.get(key)

    async def claim(self, key: str, *, ttl_seconds: int) -> bool:
        self.claim_ttls.append(ttl_seconds)
        if key in self.claims:
            return False
        self.claims.add(key)
        return True

    async def release(self, key: str) -> None:
        self.claims.discard(key)

    async def store(self, key, suggestions, *, ttl_seconds: int) -> None:
        self.store_ttls.append(ttl_seconds)
        self.values[key] = list(suggestions)
        self.claims.discard(key)


TURNS = [("user", "帮我把需求整理成任务清单"), ("assistant", "已整理：1. 明确目标 2. 拆解步骤")]


class FakeSuggestionRepository:
    def __init__(self, *, target=True, turns=None) -> None:
        self.target_queries: list[dict[str, object]] = []
        self.turn_queries: list[dict[str, object]] = []
        self._target = {"id": RUN, "status": "succeeded"} if target else None
        self._turns = TURNS if turns is None else turns

    async def authorized_target_run(self, _conn, **kwargs):
        self.target_queries.append(kwargs)
        if kwargs["tenant_id"] != TENANT or kwargs["user_id"] != USER:
            return None
        if kwargs["session_id"] != SESSION or kwargs["run_id"] != RUN:
            return None
        return self._target

    async def authorized_run_turns(self, _conn, **kwargs):
        self.turn_queries.append(kwargs)
        return self._turns


class FakeGenerator:
    def __init__(self, suggestions=("还能补充验收标准吗？",), result=None) -> None:
        self.calls: list[dict[str, object]] = []
        self._suggestions = suggestions
        self._result = result

    async def suggest(self, **kwargs):
        self.calls.append(kwargs)
        return self._result or FollowUpSuggestionResult(suggestions=tuple(self._suggestions))


def service(upstream: FakeUpstream, repository: FakeModelRepository):
    return FollowUpSuggestionService(
        transaction_factory=fake_transaction,
        repository=repository,
        upstream=upstream,
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key="synthetic-encryption-key",
            model_connection_allowed_internal_hosts="",
        ),
    )


async def completion(upstream: FakeUpstream, repository: FakeModelRepository, *, run_id: str = RUN):
    return await service(upstream, repository).suggest(
        tenant_id=TENANT,
        user_id=USER,
        session_id=SESSION,
        run_id=run_id,
        turns=TURNS,
    )


def test_parsing_keeps_only_short_distinct_questions():
    raw = json.dumps(
        ["  第一条问题？ ", "第一条问题？", "", "1. 第二条问题？", "第三", "第四条不应出现？"],
        ensure_ascii=False,
    )
    parsed = parse_follow_up_suggestions(raw)

    assert parsed == ["第一条问题？", "第二条问题？", "第三"]
    assert len(parsed) <= FOLLOW_UP_SUGGESTION_LIMIT
    # Fenced JSON and bare line lists are the tolerated non-JSON shapes.
    assert parse_follow_up_suggestions("```json\n[\"甲？\"]\n```") == ["甲？"]
    assert parse_follow_up_suggestions("甲？\n乙？") == ["甲？", "乙？"]


def test_parsing_rejects_overlong_or_non_string_answers():
    for raw in (
        json.dumps(["长" * 61], ensure_ascii=False),
        json.dumps([{"prompt": "对象形式"}], ensure_ascii=False),
        json.dumps({"suggestions": ["对象根"]}, ensure_ascii=False),
        "",
    ):
        with pytest.raises(FollowUpSuggestionError):
            parse_follow_up_suggestions(raw)


def test_transcript_uses_only_bounded_public_turns():
    messages = build_follow_up_messages((("user", "问题"), ("assistant", "回答" * 900)))

    assert messages[0]["role"] == "system"
    assert "用户：问题" in messages[1]["content"]
    assert len(messages[1]["content"]) < 4200
    with pytest.raises(FollowUpSuggestionError):
        build_follow_up_messages((("system", "私密指令"),))


def test_public_turn_projection_drops_private_roles_and_redacts_secrets():
    turns = public_follow_up_turns(
        [
            {"role": "system", "content": "私有系统指令"},
            {"role": "user", "content": "  我的问题是这个  "},
            {"role": "assistant", "content": ""},
            {"role": "tool", "content": "raw tool payload"},
            {"role": "assistant", "content": "Authorization: Bearer sk-live-secret\n答案在这里"},
        ]
    )

    assert [role for role, _text in turns] == ["user", "assistant"]
    assert turns[0] == ("user", "我的问题是这个")
    assert "sk-live-secret" not in turns[1][1]
    assert "答案在这里" in turns[1][1]


@pytest.mark.asyncio
async def test_completion_uses_the_target_run_model_without_tools_or_secret_leak():
    upstream = FakeUpstream(body=model_answer("怎么补充验收标准？", "能给出时间表吗？"))
    repository = FakeModelRepository(connection())

    result = await completion(upstream, repository)

    assert result.suggestions == ("怎么补充验收标准？", "能给出时间表吗？")
    assert repository.bindings == [
        {
            "tenant_id": TENANT,
            "user_id": USER,
            "session_id": SESSION,
            "run_id": RUN,
            "encryption_key": "synthetic-encryption-key",
        }
    ]
    assert len(upstream.calls) == 1
    call = upstream.calls[0]
    assert call["path"] == "/v1/chat/completions"
    assert call["provider"] == "openai"
    assert call["method"] == "POST"
    assert call["api_key"] == "synthetic-provider-key"
    body = json.loads(call["body"])
    assert body["model"] == "gateway-model-a"
    assert body["tools"] == []
    assert body["stream"] is False
    assert 1 <= body["max_tokens"] <= 256
    assert all(message["role"] in {"system", "user"} for message in body["messages"])
    assert "synthetic-provider-key" not in call["body"].decode("utf-8")


@pytest.mark.asyncio
async def test_disabled_pinned_model_omits_the_completion_instead_of_falling_back():
    upstream = FakeUpstream()
    repository = FakeModelRepository(None)

    result = await completion(upstream, repository)

    assert result.suggestions == ()
    assert result.reason == "model_unavailable"
    assert upstream.calls == []
    assert len(repository.bindings) == 1


@pytest.mark.asyncio
async def test_provider_timeout_stops_waiting_and_returns_empty(monkeypatch):
    import app.execution.application.follow_up_completion as completion_module

    monkeypatch.setattr(completion_module, "FOLLOW_UP_COMPLETION_TIMEOUT_SECONDS", 0.05)

    class SlowUpstream:
        async def request_async(self, **_kwargs):
            await asyncio.sleep(0.4)
            return SimpleNamespace(status=200, body=model_answer("不应返回"), content_type="application/json")

    result = await FollowUpSuggestionService(
        transaction_factory=fake_transaction,
        repository=FakeModelRepository(connection()),
        upstream=SlowUpstream(),
        settings_provider=lambda: SimpleNamespace(
            model_connection_encryption_key="synthetic-encryption-key",
            model_connection_allowed_internal_hosts="",
        ),
    ).suggest(
        tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN, turns=TURNS
    )

    assert result.suggestions == ()
    assert result.reason == "completion_invalid"


@pytest.mark.asyncio
async def test_provider_and_binding_failures_stay_empty_without_leaking_copy():
    failing = FakeUpstream(error=RuntimeError("upstream refused credential sk-live-secret"))
    failed = await completion(failing, FakeModelRepository(connection()))
    assert failed.suggestions == ()
    assert failed.reason == "completion_failed"
    assert "sk-live-secret" not in json.dumps(failed.reason)

    rejecting = await completion(FakeUpstream(status=429), FakeModelRepository(connection()))
    assert rejecting.suggestions == ()
    assert rejecting.reason == "completion_invalid"

    binding_error = await completion(
        FakeUpstream(), FakeModelRepository().fails(RuntimeError("decrypt failed"))
    )
    assert binding_error.suggestions == ()
    assert binding_error.reason == "connection_failed"


@pytest.mark.asyncio
async def test_unusable_output_never_reaches_the_user():
    for body in (
        b"not json",
        json.dumps({"choices": []}).encode(),
        json.dumps({"choices": [{"message": {"content": ""}}]}).encode(),
        model_answer("长" * 80),
    ):
        result = await completion(FakeUpstream(body=body), FakeModelRepository(connection()))
        assert result.suggestions == ()


def resolve(*, repository, cache, generator, sleep=None):
    kwargs = {
        "repository": repository,
        "cache": cache,
        "generator": generator,
        "transaction_factory": fake_transaction,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    return build_follow_up_suggestions(**kwargs)


@pytest.mark.asyncio
async def test_unauthorized_target_returns_not_found_without_any_model_call_or_cache_write():
    repository = FakeSuggestionRepository(target=False)
    cache = FakeMemoryCache()
    generator = FakeGenerator()
    use_case = resolve(repository=repository, cache=cache, generator=generator)

    with pytest.raises(FollowUpTargetNotFound):
        await use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN)

    assert generator.calls == []
    assert repository.target_queries[0] == {
        "tenant_id": TENANT,
        "user_id": USER,
        "session_id": SESSION,
        "run_id": RUN,
    }
    assert repository.turn_queries == []
    assert cache.values == {} and cache.claim_ttls == []


@pytest.mark.asyncio
async def test_answer_without_assistant_text_is_not_generated():
    generator = FakeGenerator()
    with pytest.raises(FollowUpTargetNotFound):
        await resolve(
            repository=FakeSuggestionRepository(turns=[("user", "只有提问")]),
            cache=FakeMemoryCache(),
            generator=generator,
        )(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN)
    assert generator.calls == []


@pytest.mark.asyncio
async def test_one_model_request_per_run_and_replay_serves_the_cached_answer():
    cache = FakeMemoryCache()
    generator = FakeGenerator()
    use_case = resolve(repository=FakeSuggestionRepository(), cache=cache, generator=generator)

    first = await use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN)
    second = await use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN)

    assert first == second == ["还能补充验收标准吗？"]
    assert len(generator.calls) == 1
    assert generator.calls[0] == {
        "tenant_id": TENANT,
        "user_id": USER,
        "session_id": SESSION,
        "run_id": RUN,
        "turns": TURNS,
    }
    key = follow_up_cache_key(
        tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN
    )
    assert cache.values == {key: ["还能补充验收标准吗？"]}
    assert cache.claim_ttls == [FOLLOW_UP_CLAIM_TTL_SECONDS]
    assert cache.store_ttls == [FOLLOW_UP_CACHE_TTL_SECONDS]
    assert cache.claims == set()


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_claim_and_one_model_request():
    cache = FakeMemoryCache()
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    started = asyncio.Event()

    class SlowGenerator(FakeGenerator):
        async def suggest(self, **kwargs):
            started.set()
            await asyncio.sleep(0.05)
            return await super().suggest(**kwargs)

    generator = SlowGenerator(suggestions=("第一条？",))
    use_case = resolve(
        repository=FakeSuggestionRepository(), cache=cache, generator=generator, sleep=record_sleep
    )
    owner = asyncio.create_task(use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN))
    await started.wait()
    follower = asyncio.create_task(use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN))
    results = await asyncio.gather(owner, follower)

    assert len(generator.calls) == 1
    assert results[0] == ["第一条？"]
    # The follower never generates, and never invents suggestions it did not receive.
    assert results[1] == []
    assert sleeps and all(0.2 == seconds for seconds in sleeps)


@pytest.mark.asyncio
async def test_follower_serves_the_owner_answer_once_it_lands():
    cache = FakeMemoryCache()
    key = follow_up_cache_key(
        tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN
    )
    cache.claims.add(key)

    async def cache_fills_later(seconds: float) -> None:
        assert seconds == 0.2
        cache.values.setdefault(key, ["共享中的建议？"])

    use_case = resolve(
        repository=FakeSuggestionRepository(),
        cache=cache,
        generator=FakeGenerator(),
        sleep=cache_fills_later,
    )

    assert await use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN) == [
        "共享中的建议？"
    ]


@pytest.mark.asyncio
async def test_failed_generation_does_not_pin_an_empty_cache_entry():
    cache = FakeMemoryCache()
    generator = FakeGenerator(result=FollowUpSuggestionResult(suggestions=()))
    use_case = resolve(repository=FakeSuggestionRepository(), cache=cache, generator=generator)

    assert await use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN) == []
    assert cache.values == {}
    assert cache.claims == set()
    assert await use_case(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN) == []
    assert len(generator.calls) == 2


@pytest.mark.asyncio
async def test_cache_identity_is_owner_scoped_and_not_a_raw_identifier():
    first = follow_up_cache_key(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id=RUN)
    other_user = follow_up_cache_key(tenant_id=TENANT, user_id="user-b", session_id=SESSION, run_id=RUN)
    other_run = follow_up_cache_key(tenant_id=TENANT, user_id=USER, session_id=SESSION, run_id="run-b")

    assert len({first, other_user, other_run}) == 3
    for key in (first, other_user, other_run):
        assert RUN not in key and SESSION not in key and USER not in key


def client_for(*, target=True, turns=None, suggestions=("还能补充验收标准吗？",), generator=None):
    generator = generator or FakeGenerator(suggestions=suggestions)
    resolve_suggestions = resolve(
        repository=FakeSuggestionRepository(target=target, turns=turns),
        cache=FakeMemoryCache(),
        generator=generator,
    )
    app = FastAPI()
    app.include_router(
        build_follow_up_suggestion_router(
            resolve_suggestions=resolve_suggestions,
            principal_dependency=lambda: AuthPrincipal(
                user_id=USER, display_name="User A", tenant_id=TENANT
            ),
        )
    )
    return TestClient(app), generator


def test_route_returns_owner_scoped_suggestions_without_storeable_cache_headers():
    client, generator = client_for()

    response = client.post(
        "/chat/follow-up-suggestions", json={"session_id": SESSION, "run_id": RUN}
    )

    assert response.status_code == 200
    assert response.json() == {"suggestions": ["还能补充验收标准吗？"]}
    assert response.headers["cache-control"] == "private, no-store"
    assert len(generator.calls) == 1
    # The browser's principal, never its body, decides the owner scope.
    assert generator.calls[0]["tenant_id"] == TENANT
    assert generator.calls[0]["user_id"] == USER


def test_route_rejects_an_unauthorized_or_malformed_target():
    not_found, generator = client_for(target=False)
    missing = not_found.post("/chat/follow-up-suggestions", json={"session_id": SESSION, "run_id": RUN})
    assert missing.status_code == 404
    assert generator.calls == []

    malformed, malformed_generator = client_for()
    for payload in (
        {"session_id": SESSION},
        {"session_id": SESSION, "run_id": "bad id"},
        {"session_id": SESSION, "run_id": RUN, "model": "browser-chosen"},
    ):
        assert malformed.post("/chat/follow-up-suggestions", json=payload).status_code == 422
    assert malformed_generator.calls == []
