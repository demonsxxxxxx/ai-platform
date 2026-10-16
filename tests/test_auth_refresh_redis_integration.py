"""Refresh completion fencing against real Lua on independent Redis clients."""
import asyncio
import os
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from redis.asyncio import Redis

from app import auth, auth_sessions
from app.mcp.infrastructure import runtime
from test_auth_session_operations import install_refresh_grant


@pytest.mark.asyncio
@pytest.mark.parametrize("lifetime", [86400, 300])
async def test_real_redis_refresh_completed_before_second_connection_acquires_lease(monkeypatch, lifetime):
    url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    prefix = f"test-auth-refresh:{uuid.uuid4().hex}:"
    for owner, name in ((auth_sessions, "_context_key"), (auth_sessions, "_authority_key"),
                        (runtime, "_principal_jwt_key")):
        original = getattr(owner, name)
        monkeypatch.setattr(owner, name, lambda value, original=original: prefix + original(value))
    fake, _settings, cookie, _principal, _store, record = await install_refresh_grant(
        monkeypatch, lifetime=lifetime,
    )
    first_client = Redis.from_url(url, decode_responses=True)
    second_client = Redis.from_url(url, decode_responses=True)
    delayed_task = None
    paused, resume = asyncio.Event(), asyncio.Event()
    begin = auth.begin_auth_operation_for_cookie
    calls = 0
    keys = list(fake.values)

    async def delayed_begin(*args, **kwargs):
        if asyncio.current_task() is delayed_task:
            paused.set()
            await resume.wait()
        return await begin(*args, **kwargs)

    async def user_info(_work_id, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise httpx.ConnectError("synthetic duplicate authority lookup")
        return record

    try:
        async with first_client.pipeline(transaction=True) as pipeline:
            for key, (value, deadline) in fake.values.items():
                pipeline.set(key, value, pxat=int(deadline * 1000))
            await pipeline.execute()
        monkeypatch.setattr(auth_sessions, "get_redis", lambda: (
            second_client if asyncio.current_task() is delayed_task else first_client
        ))
        monkeypatch.setattr(runtime, "get_redis_client", lambda: first_client)
        monkeypatch.setattr("app.principal_authority.authority_checked_at_now", lambda: "2026-10-14T00:00:00+00:00")
        if lifetime == 300:
            await auth.refresh_company_session(cookie)
        initial = await auth_sessions.browser_auth_grant(cookie)
        deadlines = {key: await first_client.pexpiretime(key) for key in keys}
        monkeypatch.setattr(auth, "begin_auth_operation_for_cookie", delayed_begin)
        monkeypatch.setattr("app.principal_authority.fetch_company_user_info", user_info)
        delayed_task = asyncio.create_task(auth.refresh_company_session(cookie))
        await asyncio.wait_for(paused.wait(), 2)
        winner = await auth.refresh_company_session(cookie)
        if lifetime == 300:
            assert (await auth_sessions.browser_auth_grant(cookie))["principal"] == initial["principal"]
        resume.set()
        assert await asyncio.wait_for(delayed_task, 2) == winner
        assert calls == 1
        current = await auth_sessions.browser_auth_grant(cookie)
        assert current["subject_epoch"] == initial["subject_epoch"]
        assert current["expires_at"] <= initial["expires_at"]
        assert current["principal"]["company_jwt_expires_at"] > int(time.time())
        for key, deadline in deadlines.items():
            assert 0 < await first_client.pexpiretime(key) <= deadline
    finally:
        resume.set()
        if delayed_task is not None:
            await asyncio.gather(delayed_task, return_exceptions=True)
        await first_client.delete(*keys)
        await first_client.aclose()
        await second_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining_ms", [1750, 1250])
async def test_real_redis_grant_projection_uses_absolute_deadline_across_second_boundary(monkeypatch, remaining_ms):
    url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    prefix = f"test-auth-deadline:{uuid.uuid4().hex}:"
    for owner, name in ((auth_sessions, "_context_key"), (auth_sessions, "_authority_key"),
                        (runtime, "_principal_jwt_key")):
        original = getattr(owner, name)
        monkeypatch.setattr(owner, name, lambda value, original=original: prefix + original(value))
    fake, _settings, cookie, _principal, _store, _record = await install_refresh_grant(monkeypatch)
    client = Redis.from_url(url, decode_responses=True)
    keys = list(fake.values)
    try:
        deadline_ms = (await client.time())[0] * 1000 + 86400000 + 500
        async with client.pipeline(transaction=True) as pipeline:
            for key, (value, _deadline) in fake.values.items():
                pipeline.set(key, value, pxat=deadline_ms)
            await pipeline.execute()
        seconds, milliseconds = divmod(deadline_ms - remaining_ms, 1000)
        # Exercise the production Lua with a controlled clock; actual key deadlines stay in Redis.
        clock = f"""
local actual_call = redis.call
local redis = {{call = function(command, ...)
  if command == "TIME" then return {{{seconds}, {milliseconds * 1000}}} end
  if command == "PTTL" then return {remaining_ms} end
  return actual_call(command, ...)
end}}
"""

        async def eval_at_boundary(script, numkeys, *args):
            assert script == auth_sessions.V2_PRINCIPAL_SNAPSHOT_SCRIPT
            return await client.eval(clock + script, numkeys, *args)

        monkeypatch.setattr(auth_sessions, "get_redis", lambda: SimpleNamespace(eval=eval_at_boundary))
        grant = await auth_sessions.browser_auth_grant(cookie)
        assert grant["expires_at"] == deadline_ms // 1000
        for key in keys:
            assert await client.pexpiretime(key) == deadline_ms
    finally:
        await client.delete(*keys)
        await client.aclose()
