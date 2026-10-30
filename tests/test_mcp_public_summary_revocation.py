"""Omitted registration fields must preserve disclosure authority under its lock."""
import json
from types import SimpleNamespace

import pytest

from app.mcp.infrastructure import postgres as owner
from tests.test_mcp_public_operation_summary import DECLARATION


@pytest.mark.asyncio
@pytest.mark.parametrize("current,incoming,expected", [
    ({"create_project": DECLARATION}, {}, {"create_project": DECLARATION}),
    ({}, {}, {}),
    ({"create_project": DECLARATION}, {"public_operation_summaries": {}}, {}),
])
async def test_distribution_merges_public_declarations_from_locked_current_metadata(current, incoming, expected):
    calls = []
    class Conn:
        async def execute(self, query, params):
            calls.append(query)
            async def row():
                if "from mcp_servers" in query:
                    return {"name": "vendor"}
                if "select metadata_json" in query:
                    assert any("pg_advisory_xact_lock" in previous for previous in calls[:-1])
                    assert "for update" in query
                    return {"metadata_json": {"public_operation_summaries": current}}
                if "insert into tenant_capability_distributions" in query:
                    metadata = json.loads(params[9])
                    assert metadata == {"public_operation_summaries": expected}
                    return {"metadata_json": metadata}
                return None
            return SimpleNamespace(fetchone=row)
    result = await owner.upsert_mcp_server_distribution(Conn(), tenant_id="tenant", server_name="vendor",
        status="active", visible_to_user=True, scope_mode="allowlist", department_ids=[], allowed_roles=[],
        metadata_json=incoming, updated_by="admin-a")
    assert result["metadata_json"]["public_operation_summaries"] == expected


def test_omitted_api_declaration_never_forwards_lock_before_snapshot(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_mcp_routes import install_mcp_route_fakes, headers
    calls = install_mcp_route_fakes(monkeypatch)
    client = TestClient(create_app())
    assert client.post("/api/mcp", headers=headers(roles="admin"), json={"name": "public-summary", "public_operation_summaries": {"create_project": DECLARATION}}).status_code == 200
    assert client.put("/api/mcp/public-summary", headers=headers(roles="admin"), json={}).status_code == 200
    write = [payload for name, payload in calls if name == "upsert_distribution"][-1]
    assert "public_operation_summaries" not in write["metadata_json"]
