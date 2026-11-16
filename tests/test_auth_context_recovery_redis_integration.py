"""Explicit-login orphaned Cookie recovery against real Redis Lua."""
import json
import os
import uuid

import httpx
import pytest
from redis.asyncio import Redis

from app import auth_sessions
from app.main import create_app
from test_auth_session_operations import FakeAuthRedis, install_auth_context_dependencies


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_local_identity", [False, True])
async def test_real_redis_login_recovers_only_after_retiring_an_orphaned_cookie(monkeypatch, fresh_local_identity):
    url = os.getenv("AI_PLATFORM_SSE_REDIS_TEST_URL", "").strip()
    if not url:
        pytest.skip("AI_PLATFORM_SSE_REDIS_TEST_URL is not configured")
    prefix = f"test-auth-orphan:{uuid.uuid4().hex}:"
    for name in ("_context_key", "_authority_key"):
        original = getattr(auth_sessions, name)
        monkeypatch.setattr(auth_sessions, name, lambda value, original=original: prefix + original(value))
    install_auth_context_dependencies(monkeypatch, FakeAuthRedis())
    redis = Redis.from_url(url, decode_responses=True)
    monkeypatch.setattr(auth_sessions, "get_redis", lambda: redis)
    request = {"nonce": "A" * 43, "protocol_version": 2,
               "browser_incarnation": "I" * 43, "generation": 1}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
            assert (await client.post("/api/ai/auth/bootstrap", json=request)).status_code == 200
            cookie = client.cookies["ai_platform_auth_context"]
            keys = await redis.keys(prefix + "*")
            assert len(keys) == 2
            await redis.delete(*keys)  # Task-owned keys only; simulate auth-store replacement.
            current = ({**request, "nonce": "B" * 43, "browser_incarnation": "J" * 43}
                       if fresh_local_identity else request)
            rejected = await client.post("/api/ai/auth/bootstrap", json=current)
            assert rejected.status_code == 409
            assert "set-cookie" not in rejected.headers
            missing = await client.post("/api/ai/auth/bootstrap", json={**current, "recovery_only": True})
            assert missing.status_code == 401 and missing.json()["detail"] == "auth_context_missing"
            assert "Max-Age=0" in missing.headers["set-cookie"]
            assert "ai_platform_auth_context" not in client.cookies
            assert await redis.keys(prefix + "*") == []
            fresh = {**request, "nonce": "C" * 43, "browser_incarnation": "K" * 43}
            assert (await client.post("/api/ai/auth/bootstrap", json=fresh)).status_code == 200
            assert client.cookies["ai_platform_auth_context"] != cookie
            keys = await redis.keys(prefix + "*")
            values = [json.loads(await redis.get(key)) for key in keys if ":auth-context:" in key]
            assert len(values) == 1 and values[0]["principal"] is None
    finally:
        keys = await redis.keys(prefix + "*")
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
