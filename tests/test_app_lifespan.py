from fastapi.testclient import TestClient

import app.main as main


def test_create_app_closes_database_pool_on_shutdown(monkeypatch):
    calls = []

    async def fake_close_pool():
        calls.append("close_pool")

    monkeypatch.setattr(main, "close_pool", fake_close_pool, raising=False)

    with TestClient(main.create_app()):
        pass

    assert calls == ["close_pool"]
