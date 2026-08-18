import json

import pytest

from app.mcp.infrastructure import postgres as mcp_postgres


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    async def fetchall(self):
        return list(self._rows)


class _MigrationConnection:
    def __init__(self, *, servers, tools=(), credentials=(), events=None):
        self.servers = list(servers)
        self.tools = list(tools)
        self.credentials = list(credentials)
        self.events = events if events is not None else []

    async def execute(self, statement, params=None):
        normalized = " ".join(str(statement).split()).lower()
        self.events.append((normalized, params))
        if normalized.startswith("select pg_advisory_xact_lock"):
            return _Cursor()
        if normalized.startswith("select tenant_id, name, endpoint_redacted"):
            return _Cursor(self.servers)
        if normalized.startswith("select id, server_id, endpoint, status"):
            return _Cursor(self.tools)
        if normalized.startswith("select tenant_id, server_name, credential_fingerprint"):
            return _Cursor(self.credentials)
        if normalized.startswith("update mcp_tools set endpoint"):
            return _Cursor({"id": row["id"]} for row in self.tools)
        if normalized.startswith(("update ", "insert ", "alter table ")):
            return _Cursor()
        raise AssertionError(normalized)


def _server(**overrides):
    return {
        "tenant_id": "tenant-a",
        "name": "gateway",
        "endpoint_redacted": "https://mcp.example/mcp",
        "credential_fingerprint": "legacy-fingerprint",
        "credential_metadata_json": {
            "header_names": ["X-Legacy-Key"],
            "endpoint_configured": True,
        },
        "updated_by": "admin-a",
        **overrides,
    }


@pytest.mark.asyncio
async def test_legacy_mcp_migration_seals_before_clearing_and_scrubs_header_names():
    events = []
    conn = _MigrationConnection(
        servers=[_server()],
        tools=[
            {
                "id": "tool-a",
                "server_id": "gateway",
                "endpoint": "https://mcp.example/mcp?token=legacy-secret",
                "status": "active",
            }
        ],
        credentials=[
            {
                "tenant_id": "tenant-a",
                "server_name": "gateway",
                "credential_fingerprint": "legacy-fingerprint",
                "metadata_json": {
                    "header_names": ["X-Legacy-Key"],
                    "endpoint_configured": True,
                },
                "credential_envelope": "",
                "updated_by": "admin-a",
            }
        ],
        events=events,
    )

    def seal(**kwargs):
        events.append(("seal", kwargs))
        return "sealed-envelope"

    result = await mcp_postgres.migrate_legacy_mcp_credentials(
        conn,
        seal_credentials=seal,
    )

    assert result == {
        "sealed_credentials": 1,
        "scrubbed_records": 2,
        "cleared_tool_endpoints": 1,
    }
    seal_event = next(event for event in events if event[0] == "seal")
    assert seal_event[1] == {
        "tenant_id": "tenant-a",
        "server_id": "gateway",
        "endpoint": "https://mcp.example/mcp?token=legacy-secret",
        "static_headers": {},
    }
    server_update = next(event for event in events if event[0].startswith("update mcp_servers"))
    credential_update = next(
        event for event in events if event[0].startswith("update mcp_server_credentials")
    )
    tool_update = next(event for event in events if event[0].startswith("update mcp_tools"))
    assert json.loads(server_update[1][0]) == {"endpoint_configured": True}
    assert json.loads(credential_update[1][0]) == {"endpoint_configured": True}
    assert credential_update[1][1] == "sealed-envelope"
    assert events.index(seal_event) < events.index(server_update) < events.index(tool_update)
    assert events[-2][0].startswith("alter table mcp_servers validate constraint")
    assert events[-1][0].startswith("alter table mcp_tools validate constraint")


@pytest.mark.asyncio
async def test_legacy_mcp_migration_does_not_clear_plaintext_when_sealing_fails():
    events = []
    conn = _MigrationConnection(
        servers=[_server()],
        tools=[],
        credentials=[],
        events=events,
    )

    def fail_seal(**_kwargs):
        raise RuntimeError("encryption unavailable")

    with pytest.raises(RuntimeError, match="encryption unavailable"):
        await mcp_postgres.migrate_legacy_mcp_credentials(
            conn,
            seal_credentials=fail_seal,
        )

    assert not any(
        statement.startswith(("update ", "insert ", "alter table "))
        for statement, _ in events
    )


@pytest.mark.asyncio
async def test_legacy_mcp_migration_fails_closed_on_conflicting_active_endpoints():
    events = []
    conn = _MigrationConnection(
        servers=[_server()],
        tools=[
            {
                "id": "tool-a",
                "server_id": "gateway",
                "endpoint": "https://mcp.example/mcp?token=first",
                "status": "active",
            },
            {
                "id": "tool-b",
                "server_id": "gateway",
                "endpoint": "https://mcp.example/mcp?token=second",
                "status": "active",
            },
        ],
        credentials=[],
        events=events,
    )

    with pytest.raises(
        mcp_postgres.McpLegacyCredentialMigrationError,
        match="mcp_legacy_endpoint_conflict",
    ):
        await mcp_postgres.migrate_legacy_mcp_credentials(
            conn,
            seal_credentials=lambda **_kwargs: "unreachable",
        )

    assert not any(
        statement.startswith(("update ", "insert ", "alter table "))
        for statement, _ in events
    )


@pytest.mark.asyncio
async def test_legacy_mcp_migration_rejects_ambiguous_cross_tenant_tool_owner():
    events = []
    conn = _MigrationConnection(
        servers=[
            _server(),
            _server(tenant_id="tenant-b", updated_by="admin-b"),
        ],
        tools=[
            {
                "id": "tool-a",
                "server_id": "gateway",
                "endpoint": "https://mcp.example/mcp",
                "status": "active",
            }
        ],
        credentials=[],
        events=events,
    )

    with pytest.raises(
        mcp_postgres.McpLegacyCredentialMigrationError,
        match="mcp_legacy_endpoint_ambiguous",
    ):
        await mcp_postgres.migrate_legacy_mcp_credentials(
            conn,
            seal_credentials=lambda **_kwargs: "unreachable",
        )

    assert not any(
        statement.startswith(("update ", "insert ", "alter table "))
        for statement, _ in events
    )
