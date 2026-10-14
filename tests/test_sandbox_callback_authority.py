"""The assembled Sandbox fence uses the caller's connection and durable scope."""

from copy import deepcopy

import pytest

from app.bootstrap.sandbox_callbacks import build_sandbox_callback_authority
from app.runs.infrastructure import postgres as runs_postgres
from app.sandbox.api import SandboxCallbackAuthorityError
from app.sandbox.infrastructure import leases_postgres


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["application", "http"])
@pytest.mark.parametrize(
    ("failure", "reason", "expected_calls"),
    [
        (None, None, ["read", "run_lock", "attempt_lock"]),
        ("missing", "run_not_found", ["read"]),
        ("session", "callback_session_mismatch", ["read"]),
        ("terminal", "run_already_terminal", ["read"]),
        ("locked_missing", "sandbox_runtime_attempt_inactive", ["read", "run_lock"]),
        ("locked_tenant", "sandbox_runtime_attempt_inactive", ["read", "run_lock"]),
        ("locked_session", "callback_session_mismatch", ["read", "run_lock"]),
        ("locked_terminal", "run_already_terminal", ["read", "run_lock"]),
        ("no_lease", "sandbox_runtime_attempt_inactive", ["read", "run_lock", "attempt_lock"]),
        ("duplicate", "sandbox_runtime_attempt_inactive", ["read", "run_lock", "attempt_lock"]),
        ("attempt", "sandbox_runtime_attempt_mismatch", ["read", "run_lock", "attempt_lock"]),
        ("payload_attempt", "sandbox_runtime_attempt_mismatch", ["read", "run_lock", "attempt_lock"]),
        ("generation", "sandbox_runtime_owner_generation_stale", ["read", "run_lock", "attempt_lock"]),
    ],
)
async def test_assembled_callback_authority_preserves_durable_fences(monkeypatch, entry, failure, reason, expected_calls):
    authority = build_sandbox_callback_authority()
    connection = object()
    calls = []
    run = {"tenant_id": "durable-tenant", "session_id": "session", "status": "running"}
    lease = {"attempt_id": "attempt", "lease_payload_json": {
        "attempt_id": "attempt", "callback_token_id": "generation-token",
    }}

    async def identity(conn, *, run_id, for_update):
        assert conn is connection and run_id == "run"
        calls.append("run_lock" if for_update else "read")
        row = dict(run)
        selected = (failure or "").removeprefix("locked_") if for_update else failure
        if for_update or not (failure or "").startswith("locked_"):
            if selected == "missing":
                return None
            if selected == "tenant":
                row["tenant_id"] = "foreign-tenant"
            if selected == "session":
                row["session_id"] = "foreign-session"
            if selected == "terminal":
                row["status"] = "CANCELLED"
        return row

    async def current(conn, **scope):
        assert conn is connection
        assert scope == {"tenant_id": "durable-tenant", "run_id": "run", "attempt_id": "attempt"}
        calls.append("attempt_lock")
        row = deepcopy(lease)
        if failure == "no_lease":
            return []
        if failure == "duplicate":
            return [row, row]
        if failure == "attempt":
            row["attempt_id"] = "other-attempt"
        if failure == "payload_attempt":
            row["lease_payload_json"]["attempt_id"] = "other-attempt"
        if failure == "generation":
            row["lease_payload_json"]["callback_token_id"] = "new-generation"
        return [row]

    async def forbidden_tail(*args, **kwargs):
        pytest.fail("ordinary callback must never acquire provider-tail authority")

    monkeypatch.setattr(runs_postgres, "get_run_identity", identity)
    monkeypatch.setattr(leases_postgres, "list_current_sandbox_runtime_leases_for_attempt", current)
    monkeypatch.setattr(leases_postgres, "list_cancelling_provider_tail_leases_for_attempt", forbidden_tail)
    from fastapi import HTTPException
    from app.routes import runtime_callbacks

    lock = authority.lock_run_then_attempt if entry == "application" else runtime_callbacks._lock_callback_authority
    kwargs = dict(run_id="run", attempt_id="attempt", callback_token_id="generation-token", session_id="session")
    if reason:
        error_type = SandboxCallbackAuthorityError if entry == "application" else HTTPException
        with pytest.raises(error_type) as caught:
            await lock(connection, **kwargs)
        if entry == "application":
            assert str(caught.value) == reason
        else:
            assert caught.value.detail == reason
            assert caught.value.status_code == (404 if reason == "run_not_found" else 409)
    else:
        assert await lock(connection, **kwargs) == (run, lease)
    assert calls == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_tail", [False, True])
@pytest.mark.parametrize("active", [False, True])
async def test_provider_tail_is_explicit_fallback_only(monkeypatch, allow_tail, active):
    from unittest.mock import AsyncMock

    lease = {"attempt_id": "attempt", "lease_payload_json": {"attempt_id": "attempt"}}
    current = AsyncMock(return_value=[lease] if active else [])
    tail = AsyncMock(return_value=[lease])
    monkeypatch.setattr(leases_postgres, "list_current_sandbox_runtime_leases_for_attempt", current)
    monkeypatch.setattr(leases_postgres, "list_cancelling_provider_tail_leases_for_attempt", tail)
    authority = build_sandbox_callback_authority()
    conn = object()
    scope = dict(tenant_id="tenant", run_id="run", attempt_id="attempt")
    if not active and not allow_tail:
        with pytest.raises(SandboxCallbackAuthorityError, match="sandbox_runtime_attempt_inactive"):
            await authority.recheck_attempt(conn, **scope, callback_token_id="token", allow_provider_tail=allow_tail)
    else:
        assert await authority.recheck_attempt(conn, **scope, callback_token_id="token", allow_provider_tail=allow_tail) is lease
    current.assert_awaited_once_with(conn, **scope)
    if allow_tail and not active:
        tail.assert_awaited_once_with(conn, **scope)
    else:
        tail.assert_not_awaited()
