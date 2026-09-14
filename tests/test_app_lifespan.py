from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.routes.admin_runs import _require_run_cancellation_use_case as require_admin_use_case
from app.routes.runs import _require_run_cancellation_use_case as require_owner_use_case
from app.runs.api import RunAttemptLifecycleService, RunCancellationUseCase


def test_create_app_owns_one_run_stream_runtime_and_closes_dependencies(monkeypatch):
    calls = []

    class Runtime:
        async def aclose(self):
            calls.append("run_stream_runtime")

    runtime = Runtime()
    cancellation_use_case = object.__new__(RunCancellationUseCase)
    attempt_lifecycle = object.__new__(RunAttemptLifecycleService)

    async def fake_close_redis_client():
        calls.append("redis_client")

    async def fake_close_pool():
        calls.append("close_pool")

    monkeypatch.setattr(main, "build_run_stream_runtime", lambda _transaction: runtime)
    def build_cancellation_use_case(*, attempt_lifecycle: RunAttemptLifecycleService):
        assert attempt_lifecycle is app_attempt_lifecycle
        return cancellation_use_case

    app_attempt_lifecycle = attempt_lifecycle
    monkeypatch.setattr(main, "build_run_cancellation_use_case", build_cancellation_use_case)
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

    assert calls == ["run_stream_runtime", "redis_client", "close_pool"]


@pytest.mark.asyncio
async def test_stream_runtime_composes_worker_capabilities_and_closes_bridge(monkeypatch):
    from app.bootstrap import streaming

    closed = []

    class Bridge:
        async def aclose(self):
            closed.append(True)

    bridge = Bridge()
    monkeypatch.setattr(streaming, "V4RedisStreamBridge", lambda: bridge)
    monkeypatch.setattr(
        streaming, "get_settings", lambda: SimpleNamespace(ai_session_secret="synthetic-test-secret")
    )

    def transaction_factory():
        raise AssertionError("composition must not open a database transaction")

    runtime = streaming.build_run_stream_runtime(transaction_factory)
    assert runtime.bridge is bridge
    assert runtime.worker_capabilities.event_persistence is not None
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
