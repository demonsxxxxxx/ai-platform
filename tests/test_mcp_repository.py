import pytest

from app.mcp import repository as mcp_repository


@pytest.mark.asyncio
async def test_expired_sync_takeover_claims_new_attempt_and_fences_old_attempt(monkeypatch):
    """A reclaimed lease increments the attempt, preventing the old owner from publishing."""

    class Cursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            if "for update" in sql:
                return Cursor(
                    {
                        "tenant_id": "tenant-a",
                        "name": "knowledge",
                        "status": "active",
                        "catalog_generation": 7,
                        "catalog_sync_attempt": 2,
                        "catalog_status": "syncing",
                        "catalog_unavailable_reason": "refresh_required",
                        "catalog_revision": 3,
                        "catalog_discovered_count": 1,
                        "catalog_selectable_count": 1,
                    }
                )
            return Cursor(
                {
                    "catalog_generation": 7,
                    "catalog_sync_attempt": 3,
                    "catalog_status": "syncing",
                    "catalog_unavailable_reason": "refresh_required",
                    "catalog_revision": 3,
                    "catalog_discovered_count": 1,
                    "catalog_selectable_count": 1,
                }
            )

    conn = Connection()
    started = await mcp_repository.begin_mcp_catalog_sync(
        conn,
        tenant_id="tenant-a",
        server_name="knowledge",
        observed_generation=7,
        actor_id="admin-a",
    )

    assert started["started"] is True
    assert started["catalog_sync_attempt"] == 3
    assert "catalog_sync_lease_expires_at <= now()" in conn.calls[1][0]

    async def current_server(conn, **kwargs):
        return {
            "status": "active",
            "catalog_generation": 7,
            "catalog_sync_attempt": 3,
            "catalog_status": "syncing",
            "catalog_revision": 3,
            "catalog_discovered_count": 1,
            "catalog_selectable_count": 1,
        }

    monkeypatch.setattr(mcp_repository, "_locked_server", current_server)
    stale = await mcp_repository.publish_mcp_tool_catalog(
        object(),
        tenant_id="tenant-a",
        server_name="knowledge",
        observed_generation=7,
        observed_attempt=2,
        endpoint="https://mcp.example/tools",
        tools=(),
        actor_id="admin-a",
    )

    assert stale["catalog_unavailable_reason"] == "stale_generation"
    assert stale["published"] is False
