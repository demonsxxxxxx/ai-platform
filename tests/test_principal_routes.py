from fastapi.testclient import TestClient

from app.main import create_app


def test_create_run_rejects_missing_principal(monkeypatch):
    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/api/ai/runs",
        json={
            "tenant_id": "fake",
            "workspace_id": "default",
            "user_id": "forged",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "input": {},
            "file_ids": [],
        },
    )
    assert response.status_code in {401, 403}
