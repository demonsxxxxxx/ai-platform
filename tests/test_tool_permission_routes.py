from fastapi.testclient import TestClient

from app.main import create_app


def test_retired_tool_permission_routes_are_absent():
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    assert "/api/ai/tool-permissions/inbox" not in paths
    assert "/api/ai/runs/{run_id}/tool-permissions/{request_id}" not in paths
    assert "/api/ai/runs/{run_id}/tool-permissions/request" not in paths
    assert "/api/ai/runs/{run_id}/tool-permissions/{request_id}/decision" not in paths
    assert "/api/ai/tool-permissions/inbox/{request_id}/decision" not in paths
