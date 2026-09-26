import json
import pytest
import app.mcp.infrastructure.chat_access_postgres as _repo_owner_app_mcp_infrastructure_chat_access_postgres
import app.mcp.infrastructure.registry_postgres as _repo_owner_app_mcp_infrastructure_registry_postgres
import app.mcp.infrastructure.tool_policies_postgres as _repo_owner_app_mcp_infrastructure_tool_policies_postgres
import app.mcp.repository as _repo_owner_app_mcp_repository
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
import app.runs.infrastructure.capability_admission_postgres as _repo_owner_app_runs_infrastructure_capability_admission_postgres
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError
from tests.support.repository_fixtures import FakeCursor


def test_extract_run_mcp_tool_ids_covers_only_top_level_aliases():
    extracted = _repo_owner_app_runs_infrastructure_capability_admission_postgres.extract_run_mcp_tool_ids(
        {
            "mcp_tool_ids": ["tool-a", "tool-shared"],
            "mcpToolIds": ["tool-b", "tool-shared"],
            "multi_agent_steps": [
                {"step_key": "inspect", "mcp_tool_ids": ["tool-c"]},
                {"stepKey": "execute", "mcpToolIds": ["tool-d"]},
            ],
            "metadata": {"mcp_tool_ids": ["unrelated-tool"]},
            "message": "do not parse mcp_tool_ids=unrelated-string",
        }
    )

    assert extracted == ["tool-a", "tool-shared", "tool-b"]


@pytest.mark.parametrize("redact_public", [False, True])
def test_normalize_run_input_preserves_top_level_mcp_tool_selector(redact_public):
    normalized = _repo_owner_app_runs_infrastructure_capability_admission_postgres.normalize_run_input_for_enqueue(
        {
            "message": "run scoped tools",
            "mcpToolIds": ["tool-global"],
            "multi_agent_steps": [
                {"step_key": "plan", "mcp_tool_ids": ["tool-plan"]},
                {"step_key": "code", "mcpToolIds": ["tool-code"]},
            ],
        },
        redact_public=redact_public,
    )

    assert normalized["mcp_tool_ids"] == ["tool-global"]
    if redact_public:
        assert "mcpToolIds" not in normalized
    else:
        assert normalized["mcpToolIds"] == ["tool-global"]
    assert _repo_owner_app_runs_infrastructure_capability_admission_postgres.extract_run_mcp_tool_ids(normalized) == ["tool-global"]

    nested_only = _repo_owner_app_runs_infrastructure_capability_admission_postgres.normalize_run_input_for_enqueue(
        {
            "multi_agent_steps": [
                {"step_key": "plan", "mcpToolIds": ["tool-plan"]},
                {"step_key": "code", "mcp_tool_ids": ["tool-code"]},
            ]
        },
        redact_public=redact_public,
    )
    assert "mcp_tool_ids" not in nested_only
    assert _repo_owner_app_runs_infrastructure_capability_admission_postgres.extract_run_mcp_tool_ids(nested_only) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"mcp_tool_ids": "tool-a"},
        {"mcpToolIds": {"tool": "tool-a"}},
    ],
)
def test_extract_run_mcp_tool_ids_rejects_invalid_typed_forms_fail_closed(payload):
    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        _repo_owner_app_runs_infrastructure_capability_admission_postgres.extract_run_mcp_tool_ids(payload)


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_applies_tenant_tool_policy_fail_closed():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "registry_status": "active",
                "policy_status": "disabled",
                "registry_write_capable": False,
                "policy_write_capable": False,
                "registry_risk_level": "low",
                "policy_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
            }

    class ToolConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolCursor()

    conn = ToolConnection()

    with pytest.raises(RepositoryConflictError, match="mcp_tool_disabled"):
        await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.ensure_mcp_tool_active(
            conn,
            tenant_id="tenant-a",
            tool_id="ragflow-knowledge-search",
        )

    sql, params = conn.calls[0]
    assert "left join tool_policies" in sql
    assert "tool_policies.tenant_id = %s" in sql
    assert params == ("tenant-a", "ragflow-knowledge-search", "tenant-a")


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_requires_tenant_tool_policy_row():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "registry_status": "active",
                "policy_status": None,
                "registry_write_capable": False,
                "policy_write_capable": None,
                "registry_risk_level": "low",
                "policy_risk_level": None,
                "registry_visible_to_user": True,
                "policy_visible_to_user": None,
            }

    class ToolConnection:
        async def execute(self, sql, params):
            return ToolCursor()

    with pytest.raises(RepositoryConflictError, match="mcp_tool_disabled"):
        await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.ensure_mcp_tool_active(
            ToolConnection(),
            tenant_id="tenant-a",
            tool_id="ragflow-knowledge-search",
        )


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_cannot_lower_registry_write_or_risk():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "dangerous-writer",
                "server_id": "business",
                "registry_status": "active",
                "policy_status": "active",
                "registry_write_capable": True,
                "policy_write_capable": False,
                "registry_risk_level": "high",
                "policy_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
            }

    class ToolConnection:
        async def execute(self, sql, params):
            return ToolCursor()

    row = await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.ensure_mcp_tool_active(
        ToolConnection(),
        tenant_id="tenant-a",
        tool_id="dangerous-writer",
    )

    assert row["id"] == "dangerous-writer"
    assert row["status"] == "active"
    assert row["write_capable"] is True
    assert row["risk_level"] == "high"
    assert row["visible_to_user"] is True


@pytest.mark.asyncio
async def test_list_admin_tool_policies_returns_missing_tenant_policy_as_disabled_inventory():
    class ToolPolicyCursor:
        async def fetchall(self):
            return [
                {
                    "tenant_id": "tenant-a",
                    "tool_id": "ragflow-knowledge-search",
                    "server_id": "ragflow",
                    "name": "RAGFlow",
                    "description": "Read-only search",
                    "registry_status": "active",
                    "policy_status": None,
                    "registry_write_capable": False,
                    "policy_write_capable": None,
                    "registry_risk_level": "low",
                    "policy_risk_level": None,
                    "registry_visible_to_user": True,
                    "policy_visible_to_user": None,
                    "reason": None,
                    "updated_by": None,
                    "updated_at": None,
                    "endpoint": "https://internal.example",
                    "auth_mode": "api-key",
                }
            ]

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    rows = await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.list_admin_tool_policies(
        conn,
        tenant_id="tenant-a",
        include_disabled=True,
        limit=999,
    )

    sql, params = conn.calls[0]
    assert "from mcp_tools" in sql
    assert "left join tool_policies" in sql
    assert "tool_policies.tenant_id = %s" in sql
    assert params == ("tenant-a", "tenant-a", True, 500)
    assert "endpoint" not in rows[0]
    assert "auth_mode" not in rows[0]
    assert rows == [
        {
            "tenant_id": "tenant-a",
            "tool_id": "ragflow-knowledge-search",
            "id": "ragflow-knowledge-search",
            "server_id": "ragflow",
            "name": "RAGFlow",
            "description": "Read-only search",
            "registry_status": "active",
            "policy_status": "disabled",
            "effective_status": "disabled",
            "status": "disabled",
            "registry_write_capable": False,
            "policy_write_capable": False,
            "write_capable": False,
            "registry_risk_level": "low",
            "policy_risk_level": "low",
            "risk_level": "low",
            "registry_visible_to_user": True,
            "policy_visible_to_user": False,
            "visible_to_user": False,
            "source": "registry",
            "reason": "",
            "updated_by": None,
            "updated_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_list_admin_tool_policies_filters_hidden_when_disabled_excluded():
    class ToolPolicyCursor:
        async def fetchall(self):
            return []

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    rows = await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.list_admin_tool_policies(
        conn,
        tenant_id="tenant-a",
        include_disabled=False,
        limit=50,
    )

    assert rows == []
    sql, params = conn.calls[0]
    assert "coalesce(mcp_tools.visible_to_user, false) = true" in sql
    assert "tool_policies.status = 'active'" in sql
    assert "tool_policies.visible_to_user = true" in sql
    assert params == ("tenant-a", "tenant-a", False, 50)


@pytest.mark.asyncio
async def test_upsert_admin_tool_policy_writes_tenant_policy_and_returns_effective_row():
    class ToolPolicyCursor:
        async def fetchone(self):
            return {
                "tenant_id": "tenant-a",
                "tool_id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "name": "RAGFlow",
                "description": "Read-only search",
                "registry_status": "active",
                "policy_status": "active",
                "registry_write_capable": False,
                "policy_write_capable": True,
                "registry_risk_level": "low",
                "policy_risk_level": "high",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
                "reason": "controlled write",
                "updated_by": "tool-admin",
                "updated_at": "2026-06-05T00:00:00Z",
            }

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    row = await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.upsert_admin_tool_policy(
        conn,
        tenant_id="tenant-a",
        tool_id="ragflow-knowledge-search",
        status="active",
        risk_level="high",
        write_capable=True,
        visible_to_user=True,
        reason="controlled write",
        updated_by="tool-admin",
    )

    sql, params = conn.calls[0]
    assert "insert into tool_policies" in sql
    assert "on conflict (tenant_id, tool_id) do update" in sql
    assert params == (
        "tenant-a",
        "active",
        True,
        "high",
        True,
        "controlled write",
        "tool-admin",
        "ragflow-knowledge-search",
        "tenant-a",
    )
    assert row["source"] == "tenant"
    assert row["effective_status"] == "active"
    assert row["write_capable"] is True
    assert row["risk_level"] == "high"


@pytest.mark.asyncio
async def test_upsert_admin_tool_policy_raises_for_missing_tool():
    class MissingCursor:
        async def fetchone(self):
            return None

    class MissingConnection:
        async def execute(self, sql, params):
            return MissingCursor()

    with pytest.raises(RepositoryNotFoundError, match="mcp_tool_not_found"):
        await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.upsert_admin_tool_policy(
            MissingConnection(),
            tenant_id="tenant-a",
            tool_id="missing-tool",
            status="disabled",
            risk_level="low",
            write_capable=False,
            visible_to_user=False,
            reason="missing",
            updated_by="tool-admin",
        )


@pytest.mark.asyncio
async def test_get_mcp_tool_registry_entry_scopes_tool_through_parent_server_tenant():
    class RegistryCursor:
        async def fetchone(self):
            return {
                "name": "qa-mcp",
                "transport": "streamable_http",
                "status": "active",
            }

    class RegistryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RegistryCursor()

    conn = RegistryConnection()

    row = await _repo_owner_app_mcp_repository.get_mcp_tool_registry_entry(
        conn,
        tenant_id="tenant-a",
        tool_id="qa-mcp::qa.search",
    )

    sql, params = conn.calls[0]
    assert "from mcp_servers" in sql
    assert "mcp_tools" not in sql
    assert "mcp_tool_catalog_entries" not in sql
    assert params == ("tenant-a", "qa-mcp")
    assert row is not None
    assert {
        key: row[key]
        for key in (
            "tool_id",
            "server_id",
            "name",
            "description",
            "registry_status",
            "server_status",
            "write_capable",
            "risk_level",
            "visible_to_user",
            "effective_status",
        )
    } == {
        "tool_id": "qa-mcp::qa.search",
        "server_id": "qa-mcp",
        "name": "qa.search",
        "description": "",
        "registry_status": "active",
        "server_status": "active",
        "write_capable": True,
        "risk_level": "high",
        "visible_to_user": True,
        "effective_status": "active",
    }
    assert _repo_owner_app_mcp_repository.mcp_runtime_metadata_usable(row)


@pytest.mark.asyncio
async def test_chat_catalog_query_accepts_only_the_known_builtin_as_local_compatibility():
    class Cursor:
        async def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = ()

        async def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return Cursor()

    conn = Connection()
    assert await _repo_owner_app_mcp_infrastructure_chat_access_postgres.list_chat_mcp_tool_catalog_entries(conn, tenant_id="tenant-a") == []

    assert "mcp_tools.id = 'ragflow-knowledge-search'" in conn.sql
    assert "mcp_tools.server_id = 'ragflow'" in conn.sql
    assert "catalog_entry.tenant_id = %s" not in conn.sql
    assert conn.params == ("tenant-a", "tenant-a")


@pytest.mark.asyncio
async def test_record_mcp_server_credential_keeps_hash_not_secret_material():
    class CredentialConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FakeCursor()

    conn = CredentialConnection()

    await _repo_owner_app_mcp_infrastructure_registry_postgres.record_mcp_server_credential(
        conn,
        tenant_id="tenant-a",
        server_name="qa-mcp",
        credential_fingerprint="credential-sha",
        metadata={"header_names": ["Authorization"]},
        updated_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into mcp_server_credentials" in sql
    assert "credential_fingerprint" in sql
    assert params == (
        "tenant-a",
        "qa-mcp",
        "credential-sha",
        json.dumps({"header_names": ["Authorization"]}, ensure_ascii=False),
        "admin-a",
    )
    assert "raw-secret" not in str(params)
