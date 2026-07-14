import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_legacy_product_surface_routes_are_absent_from_openapi() -> None:
    paths = set(create_app().openapi()["paths"])

    banned_paths = {
        "/api/agents",
        "/api/agent-workspace",
        "/api/channels/catalog",
        "/api/admin/channels",
        "/api/admin/channels/{channel_id}/test",
        "/api/admin/channels/{channel_id}/enable",
        "/api/admin/channels/{channel_id}/disable",
        "/api/admin/channels/{channel_id}/credentials",
        "/api/admin/channels/{channel_id}/retention",
    }
    assert paths.isdisjoint(banned_paths)
    assert not any(path.startswith("/api/persona-presets") for path in paths)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/agents", None),
        ("get", "/api/agent-workspace", None),
        ("get", "/api/channels/catalog", None),
        ("get", "/api/persona-presets/", None),
        ("post", "/api/admin/channels", {"channel_id": "legacy"}),
        ("post", "/api/persona-presets/legacy/use", None),
        ("patch", "/api/persona-presets/legacy/preference", {"is_favorite": True}),
    ],
)
def test_legacy_product_surface_requests_return_404(
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    client = TestClient(create_app())

    request = getattr(client, method)
    response = request(path) if payload is None else request(path, json=payload)

    assert response.status_code == 404
