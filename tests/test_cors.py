import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def _settings(origins):
    return type("S", (), {"cors_allow_origins": origins})()


def test_cors_preflight_allows_cookie_credentials(monkeypatch):
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: _settings("http://localhost:9527"),
    )

    response = TestClient(create_app()).options(
        "/api/ai/auth/me",
        headers={
            "Origin": "http://localhost:9527",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:9527"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_unlisted_origin(monkeypatch):
    monkeypatch.setattr("app.main.get_settings", lambda: _settings("http://10.56.0.211:8080"))

    response = TestClient(create_app()).options(
        "/api/ai/auth/me",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_rejects_wildcard_origins_with_credentials(monkeypatch):
    monkeypatch.setattr("app.main.get_settings", lambda: _settings("*"))

    with pytest.raises(RuntimeError, match="cors_wildcard_not_allowed_with_credentials"):
        create_app()
