from fastapi.testclient import TestClient

import app.main as main
from app.runs.api import RunCancellationUseCase


def test_create_app_owns_one_run_stream_runtime_and_closes_dependencies(monkeypatch):
    calls = []

    class Runtime:
        async def aclose(self):
            calls.append("run_stream_runtime")

    runtime = Runtime()

    async def fake_close_redis_client():
        calls.append("redis_client")

    async def fake_close_pool():
        calls.append("close_pool")

    monkeypatch.setattr(main, "build_run_stream_runtime", lambda: runtime)
    monkeypatch.setattr(main, "close_redis_client", fake_close_redis_client)
    monkeypatch.setattr(main, "close_pool", fake_close_pool)

    app = main.create_app()
    with TestClient(app):
        assert app.state.run_stream_runtime is runtime
        assert type(app.state.run_cancellation_use_case) is RunCancellationUseCase

    assert calls == ["run_stream_runtime", "redis_client", "close_pool"]
