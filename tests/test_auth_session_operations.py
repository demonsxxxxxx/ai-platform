import asyncio
from contextlib import asynccontextmanager
import json
import math
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.auth import AuthPrincipal
from app.main import create_app


AUTH_CONTEXT_MAX_EPOCH = (2**53) - 1


class FakeAuthRedis:
    """Small Redis/Lua model for auth-context operation tests."""

    def __init__(self) -> None:
        self.values: dict[str, tuple[str, float]] = {}
        self.available = True
        self.lock = threading.Lock()
        self.now = time.time()

    def _require_available(self) -> None:
        if not self.available:
            raise ConnectionError("redis unavailable")

    def _get(self, key: str, now: float) -> str | None:
        value = self.values.get(key)
        if value is None:
            return None
        raw, expires_at = value
        if expires_at <= now:
            self.values.pop(key, None)
            return None
        return raw

    def _set(self, key: str, raw: str, expires_at: float) -> None:
        self.values[key] = (raw, expires_at)

    @staticmethod
    def _is_valid_epoch(value: object) -> bool:
        if type(value) is int:
            return 0 <= value <= AUTH_CONTEXT_MAX_EPOCH
        if type(value) is float:
            return (
                math.isfinite(value)
                and 0 <= value <= AUTH_CONTEXT_MAX_EPOCH
                and value.is_integer()
            )
        return False

    @staticmethod
    def _is_valid_lease(value: object) -> bool:
        if type(value) is int:
            return value >= 0
        if type(value) is float:
            return math.isfinite(value) and value >= 0
        return False

    @classmethod
    def _is_valid_context_record(cls, record: object) -> bool:
        if not isinstance(record, dict):
            return False
        return (
            cls._is_valid_epoch(record.get("schema_version"))
            and record.get("schema_version") == 1
            and isinstance(record.get("nonce_binding"), str)
            and cls._is_valid_epoch(record.get("operation_epoch"))
            and cls._is_valid_epoch(record.get("tenant_user_subject_epoch"))
            and isinstance(record.get("operation_token"), str)
            and isinstance(record.get("operation_kind"), str)
            and cls._is_valid_lease(record.get("lease_until"))
            and (record.get("principal") is None or isinstance(record.get("principal"), dict))
        )

    @staticmethod
    def _is_pristine_anonymous(record: dict[str, object]) -> bool:
        return (
            record["principal"] is None
            and record["tenant_user_subject_epoch"] == 0
            and record["operation_epoch"] == 0
            and record["operation_token"] == ""
            and record["operation_kind"] == ""
            and record["lease_until"] == 0
        )

    async def eval(self, script: str, _numkeys: int, *args: object) -> str:
        self._require_available()
        with self.lock:
            if "ai-platform:auth-context-bootstrap:v1" in script:
                key, expected_binding, ttl_seconds, request_has_matching_context = args
                now_value = self.now
                if not self._is_valid_epoch(ttl_seconds) or ttl_seconds < 1:
                    return json.dumps({"status": "corrupt"})
                existing = self._get(str(key), now_value)
                if existing is None:
                    self._set(
                        str(key),
                        json.dumps(
                            {
                                "schema_version": 1,
                                "nonce_binding": str(expected_binding),
                                "principal": None,
                                "tenant_user_subject_epoch": 0,
                                "operation_epoch": 0,
                                "operation_token": "",
                                "operation_kind": "",
                                "lease_until": 0,
                            }
                        ),
                        now_value + int(ttl_seconds),
                    )
                    return json.dumps({"status": "created"})
                try:
                    record = json.loads(existing)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._is_valid_context_record(record)
                    or record.get("nonce_binding") != expected_binding
                ):
                    return json.dumps({"status": "corrupt"})
                if (
                    str(request_has_matching_context) == "1"
                    or self._is_pristine_anonymous(record)
                ):
                    return json.dumps({"status": "existing"})
                return json.dumps({"status": "rebootstrap_required"})

            if "ai-platform:auth-context-begin:v1" in script:
                key, lease_seconds, operation_token, operation_kind = args
                now_value = self.now
                if (
                    not self._is_valid_epoch(lease_seconds)
                    or lease_seconds < 1
                    or not isinstance(operation_token, str)
                    or not operation_token
                    or not isinstance(operation_kind, str)
                    or not operation_kind
                ):
                    return json.dumps({"status": "corrupt"})
                existing = self._get(str(key), now_value)
                if existing is None:
                    return json.dumps({"status": "missing"})
                try:
                    record = json.loads(existing)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if not self._is_valid_context_record(record):
                    return json.dumps({"status": "corrupt"})
                if record["operation_epoch"] >= AUTH_CONTEXT_MAX_EPOCH:
                    return json.dumps({"status": "corrupt"})
                operation_epoch = record["operation_epoch"] + 1
                record["operation_epoch"] = operation_epoch
                record["operation_token"] = operation_token
                record["operation_kind"] = operation_kind
                record["lease_until"] = now_value + lease_seconds
                _, expires_at = self.values[str(key)]
                self._set(str(key), json.dumps(record), expires_at)
                return json.dumps(
                    {
                        "status": "begun",
                        "operation_epoch": operation_epoch,
                    }
                )

            if "ai-platform:auth-context-commit:v1" in script:
                key, operation_epoch, operation_token, principal_json = args
                now_value = self.now
                existing = self._get(str(key), now_value)
                if existing is None:
                    return json.dumps({"status": "missing"})
                try:
                    record = json.loads(existing)
                    principal = json.loads(str(principal_json))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return json.dumps({"status": "corrupt"})
                if (
                    not self._is_valid_context_record(record)
                    or not self._is_valid_epoch(operation_epoch)
                    or operation_epoch < 1
                    or not isinstance(operation_token, str)
                    or not operation_token
                    or (principal is not None and not isinstance(principal, dict))
                ):
                    return json.dumps({"status": "corrupt"})
                if (
                    record.get("operation_epoch") != operation_epoch
                    or record.get("operation_token") != operation_token
                ):
                    return json.dumps({"status": "superseded"})
                if record["tenant_user_subject_epoch"] >= AUTH_CONTEXT_MAX_EPOCH:
                    return json.dumps({"status": "corrupt"})
                if record.get("lease_until") <= now_value:
                    return json.dumps({"status": "expired"})
                record["principal"] = principal
                record["tenant_user_subject_epoch"] += 1
                record["operation_token"] = ""
                record["operation_kind"] = ""
                record["lease_until"] = 0
                _, expires_at = self.values[str(key)]
                self._set(str(key), json.dumps(record), expires_at)
                return json.dumps({"status": "committed"})

            if "ai-platform:auth-oauth-state-consume:v1" in script:
                state_key, expected_context, expected_provider = args
                now_value = self.now
                existing = self._get(str(state_key), now_value)
                if existing is None:
                    return json.dumps({"status": "missing"})
                self.values.pop(str(state_key), None)
                try:
                    record = json.loads(existing)
                except json.JSONDecodeError:
                    return json.dumps({"status": "corrupt"})
                if (
                    not isinstance(record, dict)
                    or record.get("context_handle") != expected_context
                    or record.get("provider") != expected_provider
                ):
                    return json.dumps({"status": "invalid"})
                if (
                    not self._is_valid_epoch(record.get("operation_epoch"))
                    or record["operation_epoch"] < 1
                    or not isinstance(record.get("operation_token"), str)
                    or not record["operation_token"]
                ):
                    return json.dumps({"status": "corrupt"})
                return json.dumps(
                    {
                        "status": "consumed",
                        "operation_epoch": record["operation_epoch"],
                        "operation_token": record["operation_token"],
                    }
                )

            raise AssertionError(f"unexpected script: {script[:80]}")

    async def get(self, key: str) -> str | None:
        self._require_available()
        with self.lock:
            return self._get(key, self.now)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._require_available()
        with self.lock:
            self._set(key, value, self.now + int(ex or 60))
        return True

    async def aclose(self) -> None:
        return None


@asynccontextmanager
async def fake_transaction():
    yield object()


def auth_settings(**overrides):
    values = {
        "ai_session_secret": "test-session-secret-with-at-least-32-bytes",
        "ai_session_max_age_seconds": 3600,
        "ai_session_cookie_name": "ai_platform_session",
        "ai_session_cookie_secure": False,
        "auth_context_cookie_name": "ai_platform_auth_context",
        "auth_context_cookie_secure": False,
        "auth_context_lease_seconds": 30,
        "auth_context_secret": "",
        "trusted_principal_secret": "gateway-secret",
        "frontend_poc_auth_enabled": False,
        "default_tenant_id": "default",
        "existing_auth_timeout_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def install_auth_context_dependencies(monkeypatch, redis: FakeAuthRedis, settings=None):
    settings = settings or auth_settings()

    async def fake_ensure_user(*_args, **_kwargs):
        return None

    async def fake_append_audit_log(*_args, **_kwargs):
        return "audit-id"

    monkeypatch.setattr("app.auth_sessions.get_redis", lambda: redis)
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.lambchat_compat.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.auth.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", fake_ensure_user)
    monkeypatch.setattr("app.routes.auth.append_audit_log", fake_append_audit_log)
    return settings


def bootstrap(client: TestClient, nonce: str = "A" * 43) -> str:
    response = client.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert response.status_code == 200, response.text
    cookie = response.cookies.get("ai_platform_auth_context")
    assert cookie
    return cookie


def install_company_login(monkeypatch, *, gate_a: threading.Event | None = None, release_a: threading.Event | None = None):
    async def fake_login(username: str, _password: str):
        if username == "user-a" and gate_a is not None and release_a is not None:
            gate_a.set()
            await asyncio.to_thread(release_a.wait, 5)
        return {
            "workId": username,
            "userName": username,
            "cnName": username.title(),
        }

    async def fake_user_info(_work_id: str):
        return {"roles": ["user"]}

    monkeypatch.setattr("app.routes.auth.call_existing_login", fake_login)
    monkeypatch.setattr("app.routes.auth.call_existing_user_info", fake_user_info)


def test_bootstrap_concurrent_and_late_requests_set_one_stable_context_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    clients = [TestClient(create_app()), TestClient(create_app())]
    responses = []

    def bootstrap_client(client: TestClient):
        responses.append(client.post("/api/ai/auth/bootstrap", json={"nonce": "A" * 43}))

    threads = [threading.Thread(target=bootstrap_client, args=(client,)) for client in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    late_response = clients[0].post("/api/ai/auth/bootstrap", json={"nonce": "A" * 43})
    assert all(response.status_code == 200 for response in responses)
    cookies = [response.cookies["ai_platform_auth_context"] for response in [*responses, late_response]]
    assert len(set(cookies)) == 1
    assert len(redis.values) == 1


def test_nonce_only_bootstrap_cannot_reissue_an_authenticated_context(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    install_company_login(monkeypatch)
    nonce = "N" * 43
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a, nonce)

    login = client_a.post(
        "/api/ai/auth/login",
        json={"username": "user-a", "password": "safe-password"},
    )
    assert login.status_code == 200

    client_b = TestClient(create_app())
    replay = client_b.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert replay.status_code == 409
    assert replay.json()["detail"] == "auth_context_rebootstrap_required"
    assert "set-cookie" not in replay.headers
    assert client_b.get("/api/ai/auth/me").status_code == 401

    reload = client_a.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert reload.status_code == 200
    assert reload.cookies["ai_platform_auth_context"] == context_cookie
    assert client_a.get("/api/ai/auth/me").json()["user_id"] == "user-a"

    fresh = client_b.post("/api/ai/auth/bootstrap", json={"nonce": "F" * 43})
    assert fresh.status_code == 200
    assert fresh.cookies["ai_platform_auth_context"] != context_cookie
    assert client_b.get("/api/ai/auth/me").status_code == 401


def test_nonce_only_bootstrap_cannot_reissue_a_previously_operated_anonymous_context(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    nonce = "O" * 43
    client_a = TestClient(create_app())
    bootstrap(client_a, nonce)
    assert client_a.post("/api/ai/auth/logout").status_code == 200

    client_b = TestClient(create_app())
    replay = client_b.post("/api/ai/auth/bootstrap", json={"nonce": nonce})
    assert replay.status_code == 409
    assert replay.json()["detail"] == "auth_context_rebootstrap_required"
    assert "set-cookie" not in replay.headers


def test_browser_auth_mutations_without_a_context_fail_closed_without_cookie_mutation(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())

    responses = [
        client.post(
            "/api/ai/auth/login",
            json={"username": "user-a", "password": "safe-password"},
        ),
        client.post("/api/ai/auth/logout"),
        client.post("/api/ai/auth/oauth/github/begin"),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert all(response.json()["detail"] == "auth_context_missing" for response in responses)
    assert all("set-cookie" not in response.headers for response in responses)


def test_oauth_mutation_responses_do_not_mutate_the_context_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    bootstrap(client)

    begin = client.post("/api/ai/auth/oauth/github/begin")
    assert begin.status_code == 200
    assert begin.json()["state"]
    assert "set-cookie" not in begin.headers

    callback = client.post(
        "/api/ai/auth/oauth/github/callback",
        json={"code": "provider-code", "state": begin.json()["state"]},
    )
    assert callback.status_code == 503
    assert callback.json()["detail"] == "oauth_provider_unavailable"
    assert "set-cookie" not in callback.headers


def test_late_login_a_cannot_overwrite_newer_login_b_or_set_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    started_a = threading.Event()
    release_a = threading.Event()
    install_company_login(monkeypatch, gate_a=started_a, release_a=release_a)
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a)
    client_b = TestClient(create_app())
    client_b.cookies.set("ai_platform_auth_context", context_cookie)
    responses: dict[str, object] = {}

    def login_a():
        responses["a"] = client_a.post(
            "/api/ai/auth/login",
            json={"username": "user-a", "password": "safe-password"},
        )

    thread = threading.Thread(target=login_a)
    thread.start()
    assert started_a.wait(timeout=5)
    response_b = client_b.post(
        "/api/ai/auth/login",
        json={"username": "user-b", "password": "safe-password"},
    )
    release_a.set()
    thread.join(timeout=5)
    response_a = responses["a"]

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert response_a.json()["detail"] == "auth_operation_superseded"
    assert "set-cookie" not in response_a.headers
    assert "set-cookie" not in response_b.headers
    assert client_b.get("/api/ai/auth/me").json()["user_id"] == "user-b"


def test_superseded_login_rolls_back_user_and_audit_side_effects(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    started_a = threading.Event()
    release_a = threading.Event()
    install_company_login(monkeypatch, gate_a=started_a, release_a=release_a)
    committed_effects: list[tuple[str, str]] = []
    rolled_back_effects: list[list[tuple[str, str]]] = []

    @asynccontextmanager
    async def tracked_transaction():
        staged_effects: list[tuple[str, str]] = []
        try:
            yield staged_effects
        except Exception:
            rolled_back_effects.append(staged_effects)
            raise
        else:
            committed_effects.extend(staged_effects)

    async def tracked_ensure_user(conn, *, user_id, **_kwargs):
        conn.append(("user", user_id))

    async def tracked_append_audit_log(conn, *, user_id, **_kwargs):
        conn.append(("audit", user_id))
        return "audit-id"

    monkeypatch.setattr("app.routes.auth.transaction", tracked_transaction)
    monkeypatch.setattr("app.routes.auth.ensure_user", tracked_ensure_user)
    monkeypatch.setattr("app.routes.auth.append_audit_log", tracked_append_audit_log)
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a)
    client_b = TestClient(create_app())
    client_b.cookies.set("ai_platform_auth_context", context_cookie)
    responses: dict[str, object] = {}

    def login_a():
        responses["a"] = client_a.post(
            "/api/ai/auth/login",
            json={"username": "user-a", "password": "safe-password"},
        )

    thread = threading.Thread(target=login_a)
    thread.start()
    assert started_a.wait(timeout=5)
    response_b = client_b.post(
        "/api/ai/auth/login",
        json={"username": "user-b", "password": "safe-password"},
    )
    release_a.set()
    thread.join(timeout=5)
    response_a = responses["a"]

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert committed_effects == [("user", "user-b"), ("audit", "user-b")]
    assert rolled_back_effects == [[("user", "user-a"), ("audit", "user-a")]]


def test_late_logout_cannot_clear_newer_login_or_context_cookie(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    install_company_login(monkeypatch)
    client_a = TestClient(create_app())
    context_cookie = bootstrap(client_a)
    assert client_a.post(
        "/api/ai/auth/login",
        json={"username": "user-a", "password": "safe-password"},
    ).status_code == 200

    started_logout = threading.Event()
    release_logout = threading.Event()
    from app import routes

    original_commit = routes.auth.commit_auth_operation

    async def deferred_logout_commit(operation, principal):
        if operation.kind == "logout":
            started_logout.set()
            await asyncio.to_thread(release_logout.wait, 5)
        return await original_commit(operation, principal)

    monkeypatch.setattr("app.routes.auth.commit_auth_operation", deferred_logout_commit)
    client_b = TestClient(create_app())
    client_b.cookies.set("ai_platform_auth_context", context_cookie)
    responses: dict[str, object] = {}

    def logout_a():
        responses["logout"] = client_a.post("/api/ai/auth/logout")

    thread = threading.Thread(target=logout_a)
    thread.start()
    assert started_logout.wait(timeout=5)
    response_b = client_b.post(
        "/api/ai/auth/login",
        json={"username": "user-b", "password": "safe-password"},
    )
    release_logout.set()
    thread.join(timeout=5)
    response_a = responses["logout"]

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert response_a.json()["detail"] == "auth_operation_superseded"
    assert "set-cookie" not in response_a.headers
    assert "set-cookie" not in response_b.headers
    assert client_b.get("/api/ai/auth/me").json()["user_id"] == "user-b"


@pytest.mark.asyncio
async def test_oauth_callback_inversion_only_commits_newest_context_operation(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    nonce = "B" * 43
    handle = auth_sessions.auth_context_handle_for_nonce(nonce, settings)
    assert await auth_sessions.bootstrap_auth_context(handle, nonce, settings) == "created"
    operation_a = await auth_sessions.begin_auth_operation(handle, "oauth:github", settings)
    state_a = await auth_sessions.issue_oauth_state(handle, "github", operation_a, settings)
    operation_b = await auth_sessions.begin_auth_operation(handle, "oauth:github", settings)
    assert (
        await auth_sessions.commit_auth_operation(
            operation_b,
            auth_sessions.principal_snapshot(
                AuthPrincipal("oauth-b", "OAuth B", "tenant-b", source="company-login")
            ),
        )
    ) == "committed"

    callback_a = await auth_sessions.consume_oauth_state(handle, "github", state_a, settings)
    assert (
        await auth_sessions.commit_auth_operation(
            callback_a,
            auth_sessions.principal_snapshot(
                AuthPrincipal("oauth-a", "OAuth A", "tenant-a", source="company-login")
            ),
        )
    ) == "superseded"
    assert (await auth_sessions.principal_for_context(handle, settings))["user_id"] == "oauth-b"


@pytest.mark.asyncio
async def test_expired_lease_allows_new_epoch_and_permanently_rejects_old_commit(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(
        monkeypatch,
        redis,
        auth_settings(auth_context_lease_seconds=5),
    )
    redis.now = 1000.0
    nonce = "C" * 43
    handle = auth_sessions.auth_context_handle_for_nonce(nonce, settings)
    await auth_sessions.bootstrap_auth_context(handle, nonce, settings)
    operation_a = await auth_sessions.begin_auth_operation(handle, "login", settings)
    redis.now += 5
    assert (
        await auth_sessions.commit_auth_operation(
            operation_a,
            auth_sessions.principal_snapshot(AuthPrincipal("user-a", "A", "tenant-a")),
        )
    ) == "expired"
    operation_b = await auth_sessions.begin_auth_operation(handle, "login", settings)
    assert (
        await auth_sessions.commit_auth_operation(
            operation_b,
            auth_sessions.principal_snapshot(AuthPrincipal("user-b", "B", "tenant-b")),
        )
    ) == "committed"
    assert (
        await auth_sessions.commit_auth_operation(
            operation_a,
            auth_sessions.principal_snapshot(AuthPrincipal("user-a", "A", "tenant-a")),
        )
    ) == "superseded"


@pytest.mark.asyncio
async def test_context_and_operation_token_substitution_are_rejected(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    handle_a = auth_sessions.auth_context_handle_for_nonce("D" * 43, settings)
    handle_b = auth_sessions.auth_context_handle_for_nonce("E" * 43, settings)
    await auth_sessions.bootstrap_auth_context(handle_a, "D" * 43, settings)
    await auth_sessions.bootstrap_auth_context(handle_b, "E" * 43, settings)
    operation_a = await auth_sessions.begin_auth_operation(handle_a, "login", settings)

    substituted_context = auth_sessions.AuthOperation(
        context_handle=handle_b,
        epoch=operation_a.epoch,
        token=operation_a.token,
        kind=operation_a.kind,
    )
    substituted_token = auth_sessions.AuthOperation(
        context_handle=handle_a,
        epoch=operation_a.epoch,
        token="wrong-operation-token",
        kind=operation_a.kind,
    )
    principal = auth_sessions.principal_snapshot(AuthPrincipal("user-a", "A", "tenant-a"))

    assert await auth_sessions.commit_auth_operation(substituted_context, principal) == "superseded"
    assert await auth_sessions.commit_auth_operation(substituted_token, principal) == "superseded"
    assert await auth_sessions.principal_for_context(handle_a, settings) is None


def test_redis_unavailable_lost_or_corrupt_context_fails_closed_without_cookie_mutation(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    redis.available = False
    unavailable = client.post("/api/ai/auth/bootstrap", json={"nonce": "F" * 43})
    assert unavailable.status_code == 503
    assert "set-cookie" not in unavailable.headers

    redis.available = True
    context_cookie = bootstrap(client, "G" * 43)
    redis.values.clear()
    client.cookies.set("ai_platform_auth_context", context_cookie)
    lost = client.get("/api/ai/auth/me")
    assert lost.status_code == 401
    assert "set-cookie" not in lost.headers
    rebootstrap = client.post("/api/ai/auth/bootstrap", json={"nonce": "G" * 43})
    assert rebootstrap.status_code == 200
    assert rebootstrap.cookies["ai_platform_auth_context"] == context_cookie

    context_key = next(iter(redis.values))
    _, expiry = redis.values[context_key]
    redis.values[context_key] = ("not-json", expiry)
    corrupt = client.get("/api/ai/auth/me")
    assert corrupt.status_code == 503
    assert corrupt.json()["detail"] == "auth_context_unavailable"
    corrupt_bootstrap = client.post("/api/ai/auth/bootstrap", json={"nonce": "G" * 43})
    assert corrupt_bootstrap.status_code == 503
    assert "set-cookie" not in corrupt_bootstrap.headers


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.5),
        ("operation_epoch", True),
        ("operation_epoch", -1),
        ("operation_epoch", 1.5),
        ("tenant_user_subject_epoch", True),
        ("tenant_user_subject_epoch", -1),
        ("tenant_user_subject_epoch", 1.5),
        ("lease_until", True),
        ("lease_until", -1),
        ("lease_until", float("nan")),
        ("lease_until", float("inf")),
    ],
)
def test_me_rejects_corrupt_numeric_auth_context_state(monkeypatch, field, value):
    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    context_cookie = bootstrap(client, "Q" * 43)
    context_key = next(iter(redis.values))
    raw, expiry = redis.values[context_key]
    record = json.loads(raw)
    record["principal"] = {
        "user_id": "numeric-user",
        "display_name": "Numeric User",
        "tenant_id": settings.default_tenant_id,
        "department_id": "",
        "roles": ["user"],
        "permissions": [],
        "source": "company-login",
    }
    record[field] = value
    redis.values[context_key] = (json.dumps(record), expiry)
    client.cookies.set("ai_platform_auth_context", context_cookie)

    response = client.get("/api/ai/auth/me")
    assert response.status_code == 503
    assert response.json()["detail"] == "auth_context_unavailable"


@pytest.mark.asyncio
async def test_fake_auth_redis_rejects_corrupt_records_without_numeric_coercion(monkeypatch):
    from app import auth_sessions

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    handle = auth_sessions.auth_context_handle_for_nonce("R" * 43, settings)

    await auth_sessions.bootstrap_auth_context(handle, "R" * 43, settings)
    context_key = next(iter(redis.values))
    raw, expiry = redis.values[context_key]
    record = json.loads(raw)
    record["operation_epoch"] = True
    redis.values[context_key] = (json.dumps(record), expiry)

    with pytest.raises(auth_sessions.AuthContextError) as bootstrap_error:
        await auth_sessions.bootstrap_auth_context(handle, "R" * 43, settings)
    assert bootstrap_error.value.code == "auth_context_unavailable"

    with pytest.raises(auth_sessions.AuthContextError) as begin_error:
        await auth_sessions.begin_auth_operation(handle, "login", settings)
    assert begin_error.value.code == "auth_context_unavailable"

    commit_handle = auth_sessions.auth_context_handle_for_nonce("S" * 43, settings)
    await auth_sessions.bootstrap_auth_context(commit_handle, "S" * 43, settings)
    operation = await auth_sessions.begin_auth_operation(commit_handle, "login", settings)
    commit_key = next(key for key in redis.values if commit_handle in key)
    raw, expiry = redis.values[commit_key]
    record = json.loads(raw)
    record["lease_until"] = float("inf")
    redis.values[commit_key] = (json.dumps(record), expiry)

    with pytest.raises(auth_sessions.AuthContextError) as commit_error:
        await auth_sessions.commit_auth_operation(
            operation,
            auth_sessions.principal_snapshot(
                AuthPrincipal("user-a", "User A", settings.default_tenant_id)
            ),
        )
    assert commit_error.value.code == "auth_context_unavailable"


def test_weak_context_secret_fails_closed_without_cookie_mutation(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(
        monkeypatch,
        redis,
        auth_settings(ai_session_secret="too-short", auth_context_secret=""),
    )
    response = TestClient(create_app()).post(
        "/api/ai/auth/bootstrap",
        json={"nonce": "H" * 43},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "auth_context_unavailable"
    assert "set-cookie" not in response.headers


def test_old_principal_cookie_forces_relogin_while_trusted_headers_and_bearer_stay_compatible(monkeypatch):
    from app.auth import sign_principal_session

    redis = FakeAuthRedis()
    settings = install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    legacy_token = sign_principal_session(
        AuthPrincipal("legacy-user", "Legacy User", "default", source="company-login")
    )

    client.cookies.set(settings.ai_session_cookie_name, legacy_token)
    legacy = client.get("/api/ai/auth/me")
    assert legacy.status_code == 401
    assert "set-cookie" not in legacy.headers

    bearer = client.get("/api/ai/auth/me", headers={"Authorization": f"Bearer {legacy_token}"})
    assert bearer.status_code == 200
    assert bearer.json()["user_id"] == "legacy-user"

    trusted = client.get(
        "/api/ai/auth/me",
        headers={"X-AI-User-ID": "trusted-user", "X-AI-Gateway-Secret": "gateway-secret"},
    )
    assert trusted.status_code == 200
    assert trusted.json()["user_id"] == "trusted-user"


def test_browser_login_rejects_untrusted_tenant_substitution(monkeypatch):
    redis = FakeAuthRedis()
    install_auth_context_dependencies(monkeypatch, redis)
    client = TestClient(create_app())
    bootstrap(client)

    response = client.post(
        "/api/ai/auth/login",
        json={
            "username": "user-a",
            "password": "safe-password",
            "tenant_id": "attacker-tenant",
            "operation_epoch": 999,
        },
    )
    assert response.status_code == 422
    assert "set-cookie" not in response.headers
