"""Isolated real-dependency processes for Run lifecycle control-chain tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import psycopg
from psycopg import conninfo, sql
from psycopg.rows import dict_row
from redis.asyncio import Redis

from app import schema_migrations
from app.bootstrap.run_lifecycle import build_run_lifecycle_service
from app.runs.infrastructure.postgres import load_current_terminal_event_fact
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
)
from app.streaming.application.worker_publication_v4 import (
    WorkerV4Capabilities,
    admit_v4_stream,
)
from app.streaming.domain.live import stream_key
from app.streaming.infrastructure.worker_v4 import RedisV4PublicationTransport
from app.streaming.infrastructure.worker_v4 import PostgresV4PendingAdmissions
from app.streaming.infrastructure.worker_v4 import PostgresWorkerEventPersistence
from app.streaming.infrastructure.run_events_postgres import append_event
from app.streaming.infrastructure.v4 import V4RedisStreamBridge
from app.streaming.redis import RedisStreamBridge
from tests.support.run_lifecycle_redis import IsolatedRedis


ROOT = Path(__file__).resolve().parents[2]
POSTGRES_DSN_ENV = "AI_PLATFORM_S0A_SCHEMA_TEST_DSN"
CALLBACK_SECRET = "lifecycle-control-test-callback-secret"
SESSION_SECRET = "lifecycle-control-test-session-secret"
EXECUTOR_SECRET = "lifecycle-control-test-executor-secret"
_TRUSTED_API_PORTS = (8000, 8020, 18043, 18443)


def required_test_url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required integration dependency is missing: {name}")
    return value


def _schema_dsn(base_dsn: str, schema_name: str) -> str:
    return conninfo.make_conninfo(
        base_dsn,
        options=f"-c search_path={schema_name}",
    )


@asynccontextmanager
async def _connection_factory(dsn: str, schema_name: str):
    conn = await psycopg.AsyncConnection.connect(
        _schema_dsn(dsn, schema_name),
        row_factory=dict_row,
    )
    try:
        async with conn.transaction():
            yield conn
    finally:
        await conn.close()


async def _index_connection(dsn: str, schema_name: str):
    return await psycopg.AsyncConnection.connect(
        _schema_dsn(dsn, schema_name),
        autocommit=True,
        row_factory=dict_row,
    )


class RunLifecycleTestStack:
    """One test's schema, run identity, Redis namespace, and admission anchor."""

    def __init__(self, *, dsn: str, schema_name: str) -> None:
        self.dsn = dsn
        self.redis_url = ""
        self.redis_instance = IsolatedRedis()
        self.schema_name = schema_name
        self.admin: psycopg.AsyncConnection | None = None
        self.redis: Redis | None = None
        self.tenant_id = f"t_{uuid.uuid4().hex}"
        self.workspace_id = f"w_{uuid.uuid4().hex}"
        self.user_id = f"u_{uuid.uuid4().hex}"
        self.agent_id = f"a_{uuid.uuid4().hex}"
        self.skill_id = f"sk_{uuid.uuid4().hex}"
        self.session_id = f"s_{uuid.uuid4().hex}"
        self.run_id = f"r_{uuid.uuid4().hex}"
        self.attempt_id = f"att_{uuid.uuid4().hex}"
        self.owner_generation = 4
        self.callback_token_id = callback_token_id_for_binding(
            CallbackTokenBinding(
                run_id=self.run_id,
                attempt_id=self.attempt_id,
                owner_generation=self.owner_generation,
            )
        )
        self.tenant_scope = ""
        self.redis_stream_key = ""
        self.queue_prefix = f"lifecycle-test:{uuid.uuid4().hex}"

    @classmethod
    def create(cls) -> RunLifecycleTestStack:
        return cls(
            dsn=required_test_url(POSTGRES_DSN_ENV),
            schema_name=f"run_lifecycle_{uuid.uuid4().hex}",
        )

    async def __aenter__(self) -> RunLifecycleTestStack:
        self.redis_url = self.redis_instance.start()
        self.admin = await psycopg.AsyncConnection.connect(
            self.dsn,
            autocommit=True,
            row_factory=dict_row,
        )
        await self.admin.execute(
            sql.SQL("create schema {}").format(sql.Identifier(self.schema_name))
        )
        factory = self.connection_factory
        await schema_migrations.apply_migrations(
            transaction_factory=factory,
            index_connection_factory=lambda: _index_connection(self.dsn, self.schema_name),
        )
        await self._seed_running_attempt()

        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        bridge = RedisStreamBridge(publish_client=self.redis)
        capabilities = WorkerV4Capabilities(
            pending_admissions=PostgresV4PendingAdmissions(
                factory,
                authority_secret=SESSION_SECRET,
            ),
            event_persistence=PostgresWorkerEventPersistence(
                factory, append_event=append_event,
                is_cancel_requested=build_run_lifecycle_service().is_cancel_requested,
                load_terminal_event_fact=load_current_terminal_event_fact,
            ),
            publication_transport=RedisV4PublicationTransport(
                V4RedisStreamBridge(bridge)
            ),
        )
        authority = await admit_v4_stream(
            capabilities,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
        )
        self.tenant_scope = authority.tenant_scope
        self.redis_stream_key = stream_key(
            tenant_scope_value=authority.tenant_scope,
            run_id=self.run_id,
            stream_incarnation=authority.stream_incarnation,
        )
        return self

    @asynccontextmanager
    async def connection_factory(self):
        async with _connection_factory(self.dsn, self.schema_name) as conn:
            yield conn

    async def _seed_running_attempt(self) -> None:
        spec = {
            "schema_version": "ai-platform.execution-spec.v1",
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "skill_id": self.skill_id,
            "execution_kind": "skill",
        }
        canonical_spec = json.dumps(spec, sort_keys=True, separators=(",", ":"))
        async with self.connection_factory() as conn:
            await conn.execute(
                "insert into tenants(id, name) values (%s, %s)",
                (self.tenant_id, self.tenant_id),
            )
            await conn.execute(
                "insert into workspaces(id, tenant_id, name) values (%s, %s, %s)",
                (self.workspace_id, self.tenant_id, self.workspace_id),
            )
            await conn.execute(
                "insert into users(id, tenant_id, display_name) values (%s, %s, %s)",
                (self.user_id, self.tenant_id, self.user_id),
            )
            await conn.execute(
                "insert into agents(id, tenant_id, name, agent_type) values (%s, %s, %s, 'chat')",
                (self.agent_id, self.tenant_id, self.agent_id),
            )
            await conn.execute(
                "insert into skills(id, name, version, executor_type) values (%s, %s, '1', 'fake')",
                (self.skill_id, self.skill_id),
            )
            await conn.execute(
                "insert into sessions(id, tenant_id, workspace_id, user_id, agent_id, title) values (%s, %s, %s, %s, %s, %s)",
                (
                    self.session_id,
                    self.tenant_id,
                    self.workspace_id,
                    self.user_id,
                    self.agent_id,
                    self.session_id,
                ),
            )
            await conn.execute(
                "insert into runs(id, tenant_id, workspace_id, session_id, user_id, agent_id, skill_id, status) values (%s, %s, %s, %s, %s, %s, %s, 'queued')",
                (
                    self.run_id,
                    self.tenant_id,
                    self.workspace_id,
                    self.session_id,
                    self.user_id,
                    self.agent_id,
                    self.skill_id,
                ),
            )
            await conn.execute(
                """
                insert into run_attempts(
                  id, tenant_id, run_id, ordinal, status, owner_kind, owner_id,
                  queue_attempt_id,
                  execution_spec_schema_version, execution_spec_json,
                  execution_spec_canonical_json, execution_spec_sha256
                ) values (%s, %s, %s, 1, 'created', 'queue_worker', 'lifecycle-test-worker',
                          %s, 'ai-platform.execution-spec.v1', %s::jsonb, %s, %s)
                """,
                (
                    self.attempt_id,
                    self.tenant_id,
                    self.run_id,
                    f"queue_{self.attempt_id}",
                    canonical_spec,
                    canonical_spec,
                    hashlib.sha256(canonical_spec.encode("utf-8")).hexdigest(),
                ),
            )
            for status in ("queued", "claimed", "running"):
                await conn.execute(
                    """
                    update run_attempts
                    set status = %s,
                        owner_generation = owner_generation + 1,
                        queue_message_id = case when status = 'created'
                                                then %s else queue_message_id end
                    where tenant_id = %s and id = %s
                    """,
                    (
                        status,
                        f"message_{self.attempt_id}",
                        self.tenant_id,
                        self.attempt_id,
                    ),
                )
            await conn.execute(
                """
                insert into sandbox_leases(
                  id, tenant_id, workspace_id, user_id, session_id, run_id, attempt_id,
                  trace_id, sandbox_mode, provider, status, expires_at, lease_payload_json
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, 'ephemeral', 'fake', 'active',
                          now() + interval '15 minutes', %s::jsonb)
                """,
                (
                    f"lease_{self.attempt_id}",
                    self.tenant_id,
                    self.workspace_id,
                    self.user_id,
                    self.session_id,
                    self.run_id,
                    self.attempt_id,
                    f"trace_{self.run_id}",
                    json.dumps(
                        {
                            "attempt_id": self.attempt_id,
                            "owner_generation": self.owner_generation,
                            "callback_token_id": self.callback_token_id,
                        }
                    ),
                ),
            )

    def api_environment(self) -> dict[str, str]:
        return _base_subprocess_environment() | {
            "DATABASE_URL": _schema_dsn(self.dsn, self.schema_name),
            "REDIS_URL": self.redis_url,
            "QUEUE_KEY_PREFIX": self.queue_prefix,
            "SANDBOX_CALLBACK_TOKEN": CALLBACK_SECRET,
            "AI_SESSION_SECRET": SESSION_SECRET,
            "DEPLOYMENT_ENVIRONMENT": "test",
            "DATABASE_POOL_MIN_SIZE": "0",
            "DATABASE_POOL_MAX_SIZE": "4",
        }


def _base_subprocess_environment() -> dict[str, str]:
    path = os.environ.get("PATH")
    if not path:
        raise RuntimeError("PATH is unavailable for managed process")
    return {
        "PATH": path,
        "PYTHONPATH": str(ROOT),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


class ManagedProcess:
    """Own one exact subprocess and require a completed stop during teardown."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
    ) -> None:
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.pid = self.process.pid
        self.exit_code: int | None = None

    def stop(self, *, timeout_seconds: float = 10.0) -> int:
        if self.process.pid != self.pid:
            raise RuntimeError("managed process identity changed")
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.exit_code = self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.exit_code = self.process.wait(timeout=timeout_seconds)
        if self.process.poll() is None or self.exit_code is None:
            raise RuntimeError("managed process did not settle")
        return self.exit_code

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.exit_code is None:
            self.stop()


def select_trusted_loopback_port() -> int:
    for port in _TRUSTED_API_PORTS:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no trusted loopback API port is available")


def select_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for_http(url: str, *, process: ManagedProcess, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status: int | None = None
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise RuntimeError(f"managed HTTP process exited: {process.process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                last_status = response.status
                if 200 <= response.status < 300:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
    raise RuntimeError(f"managed HTTP process did not become ready (status={last_status})")


def wait_for_file(path: Path, *, process: ManagedProcess, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise RuntimeError(f"managed executor exited: {process.process.returncode}")
        if path.is_file():
            return
        time.sleep(0.05)
    raise RuntimeError(f"managed test executor did not reach its callback barrier: {path.name}")


async def close_stack(stack: RunLifecycleTestStack) -> None:
    first_error: BaseException | None = None
    if stack.redis is not None:
        if stack.redis_stream_key:
            try:
                await stack.redis.delete(
                    stack.redis_stream_key,
                    f"{stack.redis_stream_key}:state",
                )
            except BaseException as exc:
                first_error = exc
        try:
            await stack.redis.aclose()
        except BaseException as exc:
            first_error = first_error or exc
    if stack.admin is not None:
        try:
            await stack.admin.execute(
                sql.SQL("drop schema if exists {} cascade").format(
                    sql.Identifier(stack.schema_name)
                )
            )
        except BaseException as exc:
            first_error = first_error or exc
        try:
            await stack.admin.close()
        except BaseException as exc:
            first_error = first_error or exc
    try:
        stack.redis_instance.close()
    except BaseException as exc:
        first_error = first_error or exc
    if first_error is not None:
        raise first_error


@asynccontextmanager
async def isolated_run_lifecycle_stack():
    stack = RunLifecycleTestStack.create()
    try:
        await stack.__aenter__()
        yield stack
    finally:
        await close_stack(stack)
