from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def _ordinary_headers() -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": "",
    }


def _install_runtime_config_settings(monkeypatch, settings: Settings) -> None:
    from app.routes import browser_runtime_config

    monkeypatch.setattr(
        "app.auth.get_settings",
        lambda: Settings(_env_file=None, frontend_poc_auth_enabled=True),
    )
    monkeypatch.setattr(browser_runtime_config, "get_settings", lambda: settings)


def test_browser_runtime_config_is_authenticated_and_defaults_unavailable(monkeypatch):
    _install_runtime_config_settings(monkeypatch, Settings(_env_file=None))
    client = TestClient(create_app())

    unauthenticated = client.get("/api/runtime-config/browser")
    authenticated = client.get(
        "/api/runtime-config/browser",
        headers=_ordinary_headers(),
    )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.headers["cache-control"] == "no-store"
    assert authenticated.json() == {
        "launchpad_urls": {
            "lingxi": None,
            "sop_assistant": None,
            "word_translate": None,
            "word_review": None,
        }
    }


def test_browser_runtime_config_projects_only_explicit_browser_public_values(monkeypatch):
    settings = Settings(
        _env_file=None,
        browser_public_launchpad_lingxi_url=(
            "http://10.56.0.25:8189/#/TaskManagement/indexSpace/"
        ),
        browser_public_launchpad_sop_url="https://apps.example.test/#/AI/RAGFlowSOP",
        browser_public_launchpad_word_translate_url="https://docs.example.test/translate",
        browser_public_launchpad_word_review_url="https://apps.example.test/#/AI/WordReview",
        existing_auth_base_url="https://backend-auth.internal.example",
        existing_user_info_base_url="https://backend-directory.internal.example",
        database_url="postgresql://private-user:private-password@db.internal/private",
        redis_url="redis://redis.internal:6379/0",
        openai_base_url="https://model-gateway.internal.example/v1",
        openai_api_key="private-openai-key",
        anthropic_auth_token="private-anthropic-token",
    )
    _install_runtime_config_settings(monkeypatch, settings)

    response = TestClient(create_app()).get(
        "/api/runtime-config/browser",
        headers=_ordinary_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "launchpad_urls": {
            "lingxi": "http://10.56.0.25:8189/#/TaskManagement/indexSpace/",
            "sop_assistant": "https://apps.example.test/#/AI/RAGFlowSOP",
            "word_translate": "https://docs.example.test/translate",
            "word_review": "https://apps.example.test/#/AI/WordReview",
        }
    }
    serialized = response.text
    for private_value in (
        "backend-auth.internal.example",
        "backend-directory.internal.example",
        "private-user",
        "private-password",
        "redis.internal",
        "model-gateway.internal.example",
        "private-openai-key",
        "private-anthropic-token",
    ):
        assert private_value not in serialized
