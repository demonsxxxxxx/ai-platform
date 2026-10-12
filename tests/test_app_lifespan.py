import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.routes.admin_runs import (
    _require_run_cancellation_use_case as require_admin_use_case,
)
from app.routes.runs import _require_run_cancellation_use_case as require_owner_use_case
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.api import RunAttemptLifecycleService, RunCancellationUseCase


def test_create_app_owns_one_run_stream_runtime_and_closes_dependencies(monkeypatch):
    calls = []

    class Runtime:
        async def aclose(self):
            calls.append("run_stream_runtime")

    runtime = Runtime()
    cancellation_use_case = object.__new__(RunCancellationUseCase)
    attempt_lifecycle = object.__new__(RunAttemptLifecycleService)
    run_lifecycle = object.__new__(RunLifecycleService)

    async def fake_close_redis_client():
        calls.append("redis_client")

    async def fake_close_pool():
        calls.append("close_pool")

    def build_stream_runtime(transaction_factory, lifecycle):
        assert transaction_factory is main.transaction
        assert lifecycle is run_lifecycle
        calls.append("build_run_stream_runtime")
        return runtime

    monkeypatch.setattr(main, "build_run_stream_runtime", build_stream_runtime)

    def build_cancellation_use_case(
        *,
        attempt_lifecycle: RunAttemptLifecycleService,
        lifecycle: RunLifecycleService,
    ):
        assert attempt_lifecycle is app_attempt_lifecycle
        assert lifecycle is run_lifecycle
        return cancellation_use_case

    app_attempt_lifecycle = attempt_lifecycle
    monkeypatch.setattr(main, "build_run_lifecycle_service", lambda: run_lifecycle)
    monkeypatch.setattr(
        main, "build_run_cancellation_use_case", build_cancellation_use_case
    )
    monkeypatch.setattr(
        main,
        "build_run_attempt_lifecycle_service",
        lambda: attempt_lifecycle,
    )
    monkeypatch.setattr(main, "close_redis_client", fake_close_redis_client)
    monkeypatch.setattr(main, "close_pool", fake_close_pool)

    app = main.create_app()
    with TestClient(app):
        assert app.state.run_stream_runtime is runtime
        assert type(app.state.run_cancellation_use_case) is RunCancellationUseCase
        assert app.state.run_attempt_lifecycle is attempt_lifecycle

    assert calls == [
        "build_run_stream_runtime",
        "run_stream_runtime",
        "redis_client",
        "close_pool",
    ]


@pytest.mark.asyncio
async def test_stream_runtime_composes_worker_capabilities_and_closes_bridge(
    monkeypatch,
):
    from app.bootstrap import streaming

    closed = []

    class Bridge:
        async def aclose(self):
            closed.append(True)

    bridge = Bridge()

    async def is_cancel_requested(*_args, **_kwargs):
        return False

    lifecycle = SimpleNamespace(is_cancel_requested=is_cancel_requested)
    monkeypatch.setattr(streaming, "V4RedisStreamBridge", lambda: bridge)
    monkeypatch.setattr(
        streaming,
        "get_settings",
        lambda: SimpleNamespace(ai_session_secret="synthetic-test-secret"),
    )

    def transaction_factory():
        raise AssertionError("composition must not open a database transaction")

    runtime = streaming.build_run_stream_runtime(transaction_factory, lifecycle)
    assert runtime.bridge is bridge
    assert runtime.worker_capabilities.event_persistence is not None
    assert (
        runtime.worker_capabilities.event_persistence._is_cancel_requested
        is lifecycle.is_cancel_requested
    )
    assert (
        runtime.worker_capabilities.event_persistence._transaction_factory
        is transaction_factory
    )
    assert not hasattr(runtime, "hub")
    assert not hasattr(runtime, "rebuild_transport")
    await runtime.aclose()
    assert closed == [True]


@pytest.mark.parametrize("getter", [require_owner_use_case, require_admin_use_case])
@pytest.mark.parametrize("state_value", [None, object()])
def test_cancel_routes_fail_closed_without_exact_lifespan_owner(getter, state_value):
    state = SimpleNamespace()
    if state_value is not None:
        state.run_cancellation_use_case = state_value
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    with pytest.raises(RuntimeError, match="^run_cancellation_use_case_unavailable$"):
        getter(request)


class _CloseResource:
    def __init__(self, name, events, *, blocked=False, error=None):
        self.name, self.events, self.error = name, events, error
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.owner = None
        if not blocked:
            self.release.set()

    async def aclose(self):
        self.owner = asyncio.current_task()
        self.events.append(f"{self.name}:start")
        self.started.set()
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            self.events.append(f"{self.name}:end")
        except asyncio.CancelledError:
            self.events.append(f"{self.name}:cancelled")
            raise


def _owned_stream_runtime(monkeypatch, read, publish):
    from app.bootstrap.streaming import RunStreamRuntime
    from app.streaming import redis as stream_redis
    from app.streaming.infrastructure.v4 import V4RedisStreamBridge

    monkeypatch.setattr(
        stream_redis, "get_settings",
        lambda: SimpleNamespace(redis_url="redis://synthetic.invalid/0"),
    )
    monkeypatch.setattr(
        stream_redis.Redis, "from_url",
        lambda _url, **options: (
            read if options["max_connections"] == stream_redis.SSE_READ_MAX_CONNECTIONS
            else publish
        ),
    )
    return RunStreamRuntime(V4RedisStreamBridge(), object())


def _install_lifespan(monkeypatch, runtime, shared, database):
    monkeypatch.setattr(main, "build_run_lifecycle_service", object)
    monkeypatch.setattr(main, "build_run_stream_runtime", lambda *_args: runtime)
    monkeypatch.setattr(main, "build_run_cancellation_use_case", lambda **_kwargs: object())
    monkeypatch.setattr(main, "close_redis_client", shared)
    monkeypatch.setattr(main, "close_pool", database)
    return SimpleNamespace(state=SimpleNamespace(run_attempt_lifecycle=object()))


async def _run_lifespan(app, primary=None):
    async with main.lifespan(app):
        if primary is not None:
            raise primary


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_kind", ["none", "error", "cancellation"])
async def test_api_shutdown_owns_both_stream_pools_across_repeated_cancellation(
    monkeypatch, primary_kind,
):
    events = []
    read = _CloseResource("read", events, blocked=True)
    publish = _CloseResource("publish", events, blocked=True)
    shared = _CloseResource("shared", events)
    database = _CloseResource("database", events)
    runtime = _owned_stream_runtime(monkeypatch, read, publish)
    app = _install_lifespan(monkeypatch, runtime, shared.aclose, database.aclose)
    primary = {
        "none": None,
        "error": ValueError("original-body-error"),
        "cancellation": asyncio.CancelledError("original-body-cancellation"),
    }[primary_kind]
    task = asyncio.create_task(_run_lifespan(app, primary))
    try:
        await asyncio.wait_for(read.started.wait(), timeout=0.5)
        task.cancel("first-shutdown-cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated-read-cancellation")
        await asyncio.sleep(0)
        assert not task.done() and events == ["read:start"]
        read.release.set()
        await asyncio.wait_for(publish.started.wait(), timeout=0.5)
        task.cancel("repeated-publish-cancellation")
        await asyncio.sleep(0)
        assert not task.done() and events == ["read:start", "read:end", "publish:start"]
        publish.release.set()
        with pytest.raises(BaseException) as caught:
            await asyncio.wait_for(task, timeout=0.5)
        if primary is not None:
            assert caught.value is primary
        else:
            assert isinstance(caught.value, asyncio.CancelledError)
            assert caught.value.args == ("first-shutdown-cancellation",)
        assert events == [
            "read:start", "read:end", "publish:start", "publish:end",
            "shared:start", "shared:end", "database:start", "database:end",
        ]
        assert read.owner is publish.owner is shared.owner is database.owner
        assert read.owner.done()
    finally:
        read.release.set()
        publish.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_kind", ["none", "error", "cancellation"])
async def test_api_shutdown_observes_all_close_failures_without_replacing_primary(
    monkeypatch, caplog, primary_kind,
):
    events = []
    secret = "redis://user:synthetic-secret@synthetic.invalid/0"
    read_error = KeyError(secret)
    read = _CloseResource("read", events, error=read_error)
    publish = _CloseResource("publish", events, error=RuntimeError(secret))
    shared = _CloseResource("shared", events, error=LookupError(secret))
    database = _CloseResource("database", events, error=OSError(secret))
    runtime = _owned_stream_runtime(monkeypatch, read, publish)
    app = _install_lifespan(monkeypatch, runtime, shared.aclose, database.aclose)
    primary = {
        "none": None,
        "error": ValueError("original-body-error"),
        "cancellation": asyncio.CancelledError("original-body-cancellation"),
    }[primary_kind]
    with pytest.raises(BaseException) as caught:
        await _run_lifespan(app, primary)
    assert caught.value is (primary if primary is not None else read_error)
    assert events == ["read:start", "publish:start", "shared:start", "database:start"]
    assert [
        (record.cleanup_phase, record.cleanup_error_type) for record in caplog.records
    ] == [
        ("stream_read", "KeyError"), ("stream_publish", "RuntimeError"),
        ("stream_runtime", "KeyError"), ("redis_client", "LookupError"),
        ("database_pool", "OSError"),
    ]
    assert secret not in caplog.text
    assert all(record.exc_info is None and record.args == () for record in caplog.records)
    assert read.owner.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_kind", ["none", "error", "cancellation"])
@pytest.mark.parametrize("close_kind", ["error", "cancellation"])
async def test_api_shutdown_broken_logger_preserves_failures_and_later_closes(
    monkeypatch, primary_kind, close_kind,
):
    events = []
    close_error = {
        "error": ValueError("synthetic-close-error"),
        "cancellation": asyncio.CancelledError("synthetic-close-cancellation"),
    }[close_kind]
    stream = _CloseResource("stream", events, error=close_error)
    shared = _CloseResource("shared", events, error=RuntimeError("secondary-redis"))
    database = _CloseResource("database", events, error=OSError("secondary-database"))
    app = _install_lifespan(monkeypatch, stream, shared.aclose, database.aclose)
    warnings = []

    def broken_warning(_message, *, extra):
        warnings.append(extra["cleanup_phase"])
        raise RuntimeError("synthetic_logger_failure")

    monkeypatch.setattr(main.logger, "warning", broken_warning)
    primary = {
        "none": None,
        "error": LookupError("original-body-error"),
        "cancellation": asyncio.CancelledError("original-body-cancellation"),
    }[primary_kind]
    with pytest.raises(BaseException) as caught:
        await _run_lifespan(app, primary)
    expected_events = ["stream:start"]
    if close_kind == "cancellation":
        expected_events.append("stream:cancelled")
    assert events == expected_events + ["shared:start", "database:start"]
    assert caught.value is (primary if primary is not None else close_error)
    assert warnings == ["stream_runtime", "redis_client", "database_pool"]
    assert stream.owner.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["stream_runtime", "redis_client", "database_pool"])
@pytest.mark.parametrize("settlement", ["cancelled", "suppressed", "failed"])
async def test_api_close_budget_joins_finite_settlement_before_next_phase(
    monkeypatch, caplog, phase, settlement,
):
    events = []
    settling = asyncio.Event()
    release = asyncio.Event()
    owner = None
    late_error = RuntimeError("synthetic-private-close-error")

    async def close_timed_out_phase():
        nonlocal owner
        owner = asyncio.current_task()
        events.append(f"{phase}:start")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            settling.set()
            await release.wait()
            events.append(f"{phase}:settled")
            if settlement == "cancelled":
                raise
            if settlement == "failed":
                raise late_error

    phases = ("stream_runtime", "redis_client", "database_pool")
    resources = {name: _CloseResource(name, events).aclose for name in phases}
    resources[phase] = close_timed_out_phase
    app = _install_lifespan(
        monkeypatch, SimpleNamespace(aclose=resources["stream_runtime"]),
        resources["redis_client"], resources["database_pool"],
    )
    monkeypatch.setattr(main, "_API_RESOURCE_CLOSE_TIMEOUT_SECONDS", 0.01)
    task = asyncio.create_task(_run_lifespan(app))
    try:
        await asyncio.wait_for(settling.wait(), timeout=0.5)
        assert not task.done() and not owner.done()
        assert events[-1] == f"{phase}:start"
        release.set()
        with pytest.raises(RuntimeError if settlement == "failed" else TimeoutError) as caught:
            await asyncio.wait_for(task, timeout=0.5)
        if settlement == "failed":
            assert caught.value is late_error
        expected = []
        for name in phases:
            expected.extend([f"{name}:start", f"{name}:settled" if name == phase else f"{name}:end"])
        assert events == expected
        assert owner.done()
        assert [(r.cleanup_phase, r.cleanup_error_type) for r in caplog.records] == [
            (phase, "RuntimeError" if settlement == "failed" else "TimeoutError"),
        ]
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("self_cancel", [False, True])
async def test_api_cleanup_self_cancellation_is_observed_and_attempts_later_phases(
    monkeypatch, caplog, self_cancel,
):
    events = []
    cancellation = asyncio.CancelledError("synthetic-resource-cancellation")
    owner = None

    async def close_stream():
        nonlocal owner
        owner = asyncio.current_task()
        events.append("stream:start")
        if self_cancel:
            owner.cancel("synthetic-self-cancellation")
            await asyncio.sleep(0)
        raise cancellation

    shared = _CloseResource("shared", events)
    database = _CloseResource("database", events)
    app = _install_lifespan(
        monkeypatch, SimpleNamespace(aclose=close_stream), shared.aclose, database.aclose,
    )
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(_run_lifespan(app), timeout=0.5)
    if self_cancel:
        assert caught.value.args == ("synthetic-self-cancellation",)
    else:
        assert caught.value is cancellation
    assert events == ["stream:start", "shared:start", "shared:end", "database:start", "database:end"]
    assert owner.done() and owner.cancelled()
    assert [(r.cleanup_phase, r.cleanup_error_type) for r in caplog.records] == [
        ("stream_runtime", "CancelledError"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("body_failed", [False, True])
async def test_api_caller_cancel_at_cleanup_self_cancel_settlement_keeps_priority(
    monkeypatch, body_failed,
):
    events = []
    stream = _CloseResource("stream", events, error=asyncio.CancelledError("resource-cancel"))
    shared = _CloseResource("shared", events)
    primary = ValueError("body-failed") if body_failed else None

    async def close_database():
        events.append("database:end")
        caller.cancel("caller-cancel-at-settlement")

    app = _install_lifespan(monkeypatch, stream, shared.aclose, close_database)
    caller = asyncio.create_task(_run_lifespan(app, primary))
    with pytest.raises(ValueError if body_failed else asyncio.CancelledError) as caught:
        await asyncio.wait_for(caller, timeout=0.5)
    if body_failed:
        assert caught.value is primary
    else:
        assert caught.value.args == ("caller-cancel-at-settlement",)
    assert events == ["stream:start", "stream:cancelled", "shared:start", "shared:end", "database:end"]
    assert stream.owner.done() and stream.owner.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("already_handled", [False, True])
async def test_api_shutdown_distinguishes_pending_from_handled_caller_cancellation(
    monkeypatch, already_handled,
):
    events = []
    resource_cancel = asyncio.CancelledError("resource-cancel")
    stream = _CloseResource("stream", events, error=resource_cancel)
    shared = _CloseResource("shared", events)
    database = _CloseResource("database", events)
    app = _install_lifespan(monkeypatch, stream, shared.aclose, database.aclose)

    async def run():
        async with main.lifespan(app):
            asyncio.current_task().cancel("body-exit-cancellation")
            if already_handled:
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    pass

    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(run(), timeout=0.5)
    if already_handled:
        assert caught.value is resource_cancel
    else:
        assert caught.value.args == ("body-exit-cancellation",)
    assert events == [
        "stream:start", "stream:cancelled", "shared:start", "shared:end",
        "database:start", "database:end",
    ]
    assert stream.owner.done() and stream.owner.cancelled()


@pytest.mark.asyncio
async def test_api_lifespan_keeps_injected_stream_bridge_externally_owned(monkeypatch):
    from app.bootstrap.streaming import RunStreamRuntime
    from app.streaming.infrastructure.v4 import V4RedisStreamBridge

    events = []
    external = _CloseResource("external", events)
    shared = _CloseResource("shared", events)
    database = _CloseResource("database", events)
    runtime = RunStreamRuntime(V4RedisStreamBridge(bridge=external), object())
    app = _install_lifespan(monkeypatch, runtime, shared.aclose, database.aclose)
    await _run_lifespan(app)
    assert events == ["shared:start", "shared:end", "database:start", "database:end"]
    assert not external.started.is_set()


@pytest.mark.asyncio
async def test_api_shutdown_joins_real_shared_client_owner_after_caller_cancellation(monkeypatch):
    from app import redis_client

    events = []
    stream = _CloseResource("stream", events)
    shared = _CloseResource("shared", events, blocked=True)
    database = _CloseResource("database", events)
    app = _install_lifespan(monkeypatch, stream, redis_client.close_redis_client, database.aclose)
    monkeypatch.setattr(redis_client, "_clients", {})
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: shared)
    monkeypatch.setattr(
        redis_client, "get_settings",
        lambda: SimpleNamespace(redis_url="redis://synthetic.invalid/0", redis_max_connections=10),
    )
    redis_client.get_redis_client()
    entry = redis_client._clients[asyncio.get_running_loop()]
    task = asyncio.create_task(_run_lifespan(app))
    try:
        await asyncio.wait_for(shared.started.wait(), timeout=0.5)
        task.cancel("first-shared-close-cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated-shared-close-cancellation")
        await asyncio.sleep(0)
        assert not task.done() and entry.state == "closing"
        assert not database.started.is_set()
        with pytest.raises(redis_client.RedisClientUnavailable):
            redis_client.get_redis_client()
        shared.release.set()
        with pytest.raises(asyncio.CancelledError, match="^first-shared-close-cancellation$"):
            await asyncio.wait_for(task, timeout=0.5)
        assert redis_client._clients == {}
        assert events == ["stream:start", "stream:end", "shared:start", "shared:end", "database:start", "database:end"]
        assert shared.owner.done()
    finally:
        shared.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_logger", [False, True])
async def test_api_cancellation_during_database_close_wins_over_secondary_error(
    monkeypatch, broken_logger,
):
    events = []
    stream = _CloseResource("stream", events)
    shared = _CloseResource("shared", events)
    database = _CloseResource("database", events, blocked=True, error=RuntimeError("secondary"))

    def broken_warning(*_args, **_kwargs):
        raise RuntimeError("synthetic_logger_failure")

    if broken_logger:
        monkeypatch.setattr(main.logger, "warning", broken_warning)
    app = _install_lifespan(monkeypatch, stream, shared.aclose, database.aclose)
    task = asyncio.create_task(_run_lifespan(app))
    try:
        await asyncio.wait_for(database.started.wait(), timeout=0.5)
        task.cancel("first-database-close-cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated-database-close-cancellation")
        await asyncio.sleep(0)
        assert not task.done() and not database.owner.done()
        database.release.set()
        with pytest.raises(asyncio.CancelledError, match="^first-database-close-cancellation$"):
            await asyncio.wait_for(task, timeout=0.5)
        assert events == ["stream:start", "stream:end", "shared:start", "shared:end", "database:start"]
        assert database.owner.done()
    finally:
        database.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_api_cancellation_settles_real_redis_transport_closes_without_sockets(monkeypatch):
    from redis.asyncio import Redis
    from redis.asyncio.connection import Connection, ConnectionPool

    events = []

    class Writer:
        def __init__(self, name):
            self.name = name
            self.started, self.release = asyncio.Event(), asyncio.Event()

        def close(self):
            events.append(f"{self.name}:close")

        async def wait_closed(self):
            self.started.set()
            await self.release.wait()
            events.append(f"{self.name}:settled")

    def client(name):
        pool = ConnectionPool(protocol=2, socket_connect_timeout=1)
        connection = Connection(protocol=2, socket_connect_timeout=1)
        writer = Writer(name)
        connection._reader, connection._writer = object(), writer
        pool._available_connections.append(connection)
        return Redis.from_pool(pool), writer

    read, read_writer = client("read")
    publish, publish_writer = client("publish")
    runtime = _owned_stream_runtime(monkeypatch, read, publish)
    shared = _CloseResource("shared", events)
    database = _CloseResource("database", events)
    app = _install_lifespan(monkeypatch, runtime, shared.aclose, database.aclose)
    task = asyncio.create_task(_run_lifespan(app))
    try:
        await asyncio.wait_for(read_writer.started.wait(), timeout=0.5)
        task.cancel("first-transport-close-cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated-transport-close-cancellation")
        await asyncio.sleep(0)
        assert not task.done() and events == ["read:close"]
        read_writer.release.set()
        await asyncio.wait_for(publish_writer.started.wait(), timeout=0.5)
        task.cancel("publish-transport-close-cancellation")
        await asyncio.sleep(0)
        assert not task.done() and events == ["read:close", "read:settled", "publish:close"]
        publish_writer.release.set()
        with pytest.raises(asyncio.CancelledError, match="^first-transport-close-cancellation$"):
            await asyncio.wait_for(task, timeout=0.5)
        assert events == [
            "read:close", "read:settled", "publish:close", "publish:settled",
            "shared:start", "shared:end", "database:start", "database:end",
        ]
        assert read.connection_pool._available_connections[0]._writer is None
        assert publish.connection_pool._available_connections[0]._writer is None
    finally:
        read_writer.release.set()
        publish_writer.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await read.aclose()
        await publish.aclose()
