from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.routes.admin_runs import _require_run_cancellation_use_case as require_admin_use_case
from app.routes.runs import _require_run_cancellation_use_case as require_owner_use_case
from app.runs.api import RunCancellationUseCase


def test_create_app_owns_one_run_stream_runtime_and_closes_dependencies(monkeypatch):
    calls = []

    class Runtime:
        async def aclose(self):
            calls.append("run_stream_runtime")

    runtime = Runtime()
    cancellation_use_case = object.__new__(RunCancellationUseCase)

    async def fake_close_redis_client():
        calls.append("redis_client")

    async def fake_close_pool():
        calls.append("close_pool")

    monkeypatch.setattr(main, "build_run_stream_runtime", lambda _transaction: runtime)
    monkeypatch.setattr(main, "build_run_cancellation_use_case", lambda: cancellation_use_case)
    monkeypatch.setattr(main, "close_redis_client", fake_close_redis_client)
    monkeypatch.setattr(main, "close_pool", fake_close_pool)

    app = main.create_app()
    with TestClient(app):
        assert app.state.run_stream_runtime is runtime
        assert type(app.state.run_cancellation_use_case) is RunCancellationUseCase

    assert calls == ["run_stream_runtime", "redis_client", "close_pool"]


@pytest.mark.parametrize("getter", [require_owner_use_case, require_admin_use_case])
@pytest.mark.parametrize("state_value", [None, object()])
def test_cancel_routes_fail_closed_without_exact_lifespan_owner(getter, state_value):
    state = SimpleNamespace()
    if state_value is not None:
        state.run_cancellation_use_case = state_value
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    with pytest.raises(RuntimeError, match="^run_cancellation_use_case_unavailable$"):
        getter(request)
