import pytest
import app.agent_apps.infrastructure.principal_catalog_postgres as _repo_owner_app_agent_apps_infrastructure_principal_catalog_postgres
import app.identity.infrastructure.capability_distributions_postgres as _repo_owner_app_identity_infrastructure_capability_distributions_postgres
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
import app.runs.infrastructure.capability_admission_postgres as _repo_owner_app_runs_infrastructure_capability_admission_postgres
import app.skills.dependencies as _repo_owner_app_skills_dependencies
import app.agent_apps.infrastructure.principal_catalog_postgres as principal_catalog_persistence
import app.identity.infrastructure.capability_distributions_postgres as distribution_persistence
import app.runs.infrastructure.capability_admission_postgres as capability_admission_persistence
from app.agent_apps.infrastructure import postgres as agent_profile_persistence
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError
from tests.support.repository_fixtures import SingleRowCursor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "status",
        "published_by",
        "published_from_revision",
        "expected_published_at",
    ),
    [
        ("draft", None, None, None),
        ("published", "publisher-a", 7, "database-timestamp"),
        ("withdrawn", None, None, None),
    ],
)
async def test_create_agent_profile_revision_preserves_typed_publication_bindings(
    status,
    published_by,
    published_from_revision,
    expected_published_at,
):
    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, statement, params):
            normalized = " ".join(statement.split())
            self.calls.append((normalized, params))
            if "select coalesce(max(revision), 0) as current_revision" in normalized:
                return SingleRowCursor({"current_revision": 7})
            if "insert into agent_profile_revisions" in normalized:
                return SingleRowCursor(
                    {"published_at": None if params[20] is None else "database-timestamp"}
                )
            return SingleRowCursor(None)

    conn = Connection()
    saved = await agent_profile_persistence.create_agent_profile_revision(
        conn,
        tenant_id="tenant-a",
        agent_id="agt_support",
        status=status,
        name="Support assistant",
        description="Approved support helper.",
        starter_prompts=[],
        instructions="Private instruction",
        skill_set=[{"skill_id": "general-chat"}],
        mcp_tool_ids=["mcp-a", "mcp-b"],
        avatar_ref="builtin:agent",
        avatar_seed="support-assistant",
        market_tags=["support"],
        content_hash="a" * 64,
        created_by="creator-a",
        published_by=published_by,
        expected_previous_revision=7,
        published_from_revision=published_from_revision,
    )

    insert_sql, params = conn.calls[2]
    assert "revision_status" in insert_sql
    assert "starter_prompts" in insert_sql
    assert "skill_set" in insert_sql
    assert "market_tags" in insert_sql
    for retired_column in (
        " welcome_message",
        " capability_summary",
        " recommended_tasks",
        " supported_input_types",
        " supported_file_types",
        " expected_outputs",
        " permissions_and_data_access_notice",
        " model_id",
        " skill_id",
        " skill_version",
        " avatar_style_ref",
        " avatar_asset_id",
        " category",
        " market_tag,",
    ):
        assert retired_column not in insert_sql
    assert len(params) == insert_sql.count("%s") == 23
    assert params[0:6] == (
        "tenant-a", "agt_support", 8, status, "Support assistant", "Approved support helper."
    )
    assert params[7] == "Private instruction"
    assert params[8] == '[{"skill_id": "general-chat"}]'
    assert params[9] == '["mcp-a", "mcp-b"]'
    assert params[11:15] == ("builtin:agent", "support-assistant", '["support"]', "tenant")
    assert params[19:23] == (published_by, published_by, published_from_revision, None)
    assert saved["published_at"] == expected_published_at


@pytest.mark.asyncio
async def test_list_published_agent_profiles_searches_safe_public_use_fields():
    class RowsCursor:
        async def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, statement, params):
            self.sql = " ".join(statement.split())
            self.params = params
            return RowsCursor()

    conn = Connection()
    rows = await agent_profile_persistence.list_current_published_agent_profiles(
        conn,
        tenant_id="company-default",
        query="内部通知润色",
        limit=500,
    )

    assert rows == []
    assert "normalize(agent_profile_revisions.name, NFKC) ilike %s" in conn.sql
    assert "normalize(agent_profile_revisions.description, NFKC) ilike %s" in conn.sql
    assert "jsonb_array_elements_text" in conn.sql
    assert "normalize(tag.value, NFKC) ilike %s" in conn.sql
    assert "select count(*)" in conn.sql
    assert "runs.tenant_id = agent_profile_revisions.tenant_id" in conn.sql
    assert "runs.agent_id = agent_profile_revisions.agent_id" in conn.sql
    assert "runs.status = 'succeeded'" in conn.sql
    assert conn.sql.count("escape E'\\\\'") == 3
    assert conn.params == (
        "company-default",
        "%内部通知润色%",
        "%内部通知润色%",
        "%内部通知润色%",
        200,
    )


@pytest.mark.asyncio
async def test_list_published_agent_profiles_escapes_like_metacharacters():
    class RowsCursor:
        async def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.params = None

        async def execute(self, _statement, params):
            self.params = params
            return RowsCursor()

    conn = Connection()
    await agent_profile_persistence.list_current_published_agent_profiles(
        conn,
        tenant_id="company-default",
        query="%_\\",
    )

    assert conn.params == (
        "company-default",
        "%\\%\\_\\\\%",
        "%\\%\\_\\\\%",
        "%\\%\\_\\\\%",
        200,
    )


@pytest.mark.asyncio
async def test_capability_distribution_backfill_marks_completion_and_never_recreates_after_rerun():
    backfill = _repo_owner_app_identity_infrastructure_capability_distributions_postgres.ensure_tenant_capability_distribution_backfill
    assert callable(backfill), "ensure_tenant_capability_distribution_backfill missing"

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.calls = []
            self.completed = False

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("select completed_at from tenant_capability_distribution_backfills"):
                return Cursor({"completed_at": "now" if self.completed else None})
            if compact.startswith("update tenant_capability_distribution_backfills set completed_at = now()"):
                self.completed = True
            return Cursor()

    conn = Connection()
    await backfill(conn, tenant_id="tenant-a")
    await backfill(conn, tenant_id="tenant-a")

    assert len(conn.calls) == 7
    initial_check, marker_insert, marker_lock, skill_call, mcp_call, completion_update, rerun_check = conn.calls
    skill_sql, skill_params = skill_call
    mcp_sql, mcp_params = mcp_call
    assert "for update" not in initial_check[0]
    assert initial_check[1] == ("tenant-a",)
    assert "insert into tenant_capability_distribution_backfills" in marker_insert[0]
    assert marker_insert[1] == ("tenant-a",)
    assert "for update" in marker_lock[0]
    assert marker_lock[1] == ("tenant-a",)
    assert completion_update[1] == ("tenant-a",)
    assert rerun_check == initial_check
    assert "from tenant_workbench_skills" in skill_sql
    assert "from skills" in skill_sql
    assert skill_sql.count("skills.status = 'active'") == 2
    assert "on conflict (tenant_id, capability_kind, capability_id) do nothing" in skill_sql
    assert "do update" not in skill_sql
    assert "from mcp_servers" in mcp_sql
    assert "department_ids" in mcp_sql
    assert "allowed_roles" in mcp_sql
    assert "jsonb_typeof(mcp_servers.allowed_roles) is distinct from 'array'" in mcp_sql
    assert "jsonb_array_elements" in mcp_sql
    assert "jsonb_typeof(role_value) is distinct from 'string'" in mcp_sql
    assert "select distinct lower(btrim(role_value #>> '{}'))" in mcp_sql
    assert "unnest(mcp_servers.department_ids)" in mcp_sql
    assert "department_validation.scope_valid" in mcp_sql
    assert "not role_validation.scope_valid or not department_validation.scope_valid" in mcp_sql
    assert "else 'disabled'" in mcp_sql
    assert "on conflict (tenant_id, capability_kind, capability_id) do nothing" in mcp_sql
    assert "do update" not in mcp_sql
    assert skill_sql.count("%s") == len(skill_params)
    assert mcp_sql.count("%s") == len(mcp_params)
    assert skill_params == (
        "tenant-a",
        "tenant-a",
        "tenant-a",
        "tenant-a",
        sorted(_repo_owner_app_skills_dependencies.PUBLIC_WORKBENCH_SKILL_IDS),
    )
    assert sum("from tenant_workbench_skills" in sql for sql, _ in conn.calls) == 1
    assert sum("from mcp_servers" in sql for sql, _ in conn.calls) == 1


@pytest.mark.asyncio
async def test_capability_distribution_backfill_lock_recheck_observes_concurrent_completion():
    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("select completed_at from tenant_capability_distribution_backfills"):
                if "for update" in compact:
                    return Cursor({"completed_at": "completed-by-concurrent-transaction"})
                return Cursor({"completed_at": None})
            return Cursor()

    conn = Connection()
    await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.ensure_tenant_capability_distribution_backfill(conn, tenant_id="tenant-a")

    assert len(conn.calls) == 3
    initial_check, marker_insert, locked_recheck = conn.calls
    assert "for update" not in initial_check[0]
    assert "insert into tenant_capability_distribution_backfills" in marker_insert[0]
    assert "for update" in locked_recheck[0]
    assert not any("from tenant_workbench_skills" in sql for sql, _ in conn.calls)
    assert not any("from mcp_servers" in sql for sql, _ in conn.calls)
    assert not any("set completed_at = now()" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_capability_distribution_list_and_get_normalize_array_and_json_projections():
    list_rows = _repo_owner_app_identity_infrastructure_capability_distributions_postgres.list_capability_distribution_rows
    get_row = _repo_owner_app_identity_infrastructure_capability_distributions_postgres.get_capability_distribution_row
    assert callable(list_rows), "list_capability_distribution_rows missing"
    assert callable(get_row), "get_capability_distribution_row missing"

    row = {
        "id": "capdist_a",
        "tenant_id": "tenant-a",
        "capability_kind": "mcp_server",
        "capability_id": "qa-mcp",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": ("qa",),
        "allowed_roles": '["qa_operator"]',
        "metadata_json": '{"legacy_source":"mcp_servers"}',
        "updated_by": "admin-a",
        "created_at": None,
        "updated_at": None,
    }

    class Cursor:
        async def fetchone(self):
            return row

        async def fetchall(self):
            return [row]

    class Connection:
        async def execute(self, sql, params=()):
            return Cursor()

    conn = Connection()
    listed = await list_rows(conn, tenant_id="tenant-a", capability_kind="mcp_server", include_disabled=False)
    fetched = await get_row(conn, tenant_id="tenant-a", capability_kind="mcp_server", capability_id="qa-mcp")

    assert listed == [fetched]
    assert fetched["department_ids"] == ["qa"]
    assert fetched["allowed_roles"] == ["qa_operator"]
    assert fetched["metadata_json"] == {"legacy_source": "mcp_servers"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed_roles",
    ['{"unexpected":"object"}', '[1,"qa"]', '[""]', '["   "]'],
)
async def test_capability_distribution_projection_rejects_malformed_allowed_roles(allowed_roles):
    row = {
        "id": "capdist-malformed",
        "tenant_id": "tenant-a",
        "capability_kind": "mcp_server",
        "capability_id": "unsafe-mcp",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": ("QA",),
        "allowed_roles": allowed_roles,
        "metadata_json": '{}',
        "updated_by": "admin-a",
        "created_at": None,
        "updated_at": None,
    }

    class Cursor:
        async def fetchone(self):
            return row

    class Connection:
        async def execute(self, sql, params=()):
            return Cursor()

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryConflictError, match="capability_distribution_scope_invalid"):
        await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.get_capability_distribution_row(
            Connection(),
            tenant_id="tenant-a",
            capability_kind="mcp_server",
            capability_id="unsafe-mcp",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("department_ids", [("QA", ""), ("QA", "   "), ("QA", None)])
async def test_capability_distribution_projection_rejects_malformed_department_ids(department_ids):
    row = {
        "id": "capdist-malformed-department",
        "tenant_id": "tenant-a",
        "capability_kind": "mcp_server",
        "capability_id": "unsafe-mcp",
        "status": "active",
        "visible_to_user": True,
        "scope_mode": "allowlist",
        "department_ids": department_ids,
        "allowed_roles": [],
        "metadata_json": {},
        "updated_by": "admin-a",
        "created_at": None,
        "updated_at": None,
    }

    class Cursor:
        async def fetchone(self):
            return row

    class Connection:
        async def execute(self, sql, params=()):
            return Cursor()

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryConflictError, match="capability_distribution_scope_invalid"):
        await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.get_capability_distribution_row(
            Connection(),
            tenant_id="tenant-a",
            capability_kind="mcp_server",
            capability_id="unsafe-mcp",
        )


@pytest.mark.asyncio
async def test_principal_agent_projection_keeps_skillless_chat_without_skill_distribution(
    monkeypatch,
):
    async def fake_list_agents(conn, *, tenant_id):
        assert tenant_id == "tenant-a"
        return [
            {
                "id": "general-agent",
                "agent_type": "chat",
                "default_skill_id": None,
                "status": "active",
                "skill_version": None,
            }
        ]

    async def fake_list_distributions(conn, **kwargs):
        return []

    async def fail_audit(*args, **kwargs):
        raise AssertionError(
            "skillless Harness discovery must not audit a Skill bypass"
        )

    monkeypatch.setattr(principal_catalog_persistence, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(principal_catalog_persistence, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(principal_catalog_persistence, "append_audit_log", fail_audit)

    rows = await _repo_owner_app_agent_apps_infrastructure_principal_catalog_postgres.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="user-a",
        department_id="",
        roles=[],
        is_admin=False,
        permissions=["chat:read"],
    )

    assert len(rows) == 1
    assert rows[0]["id"] == "general-agent"
    assert rows[0]["default_skill_id"] is None
    assert rows[0]["skill_version"] is None
    assert rows[0]["skill_version_status"] is None
    assert rows[0]["input_modes"] == ["chat"]
    assert rows[0]["output_modes"] == ["answer"]


@pytest.mark.asyncio
async def test_principal_agent_projection_filters_exact_scope_and_audits_admin_bypass(
    monkeypatch,
):
    rows = [
        {
            "id": "general-agent",
            "default_skill_id": "general-chat",
            "status": "active",
        },
        {
            "id": "qa-word-review",
            "default_skill_id": "qa-file-reviewer",
            "status": "active",
        },
        {
            "id": "retired-agent",
            "default_skill_id": "retired-skill",
            "status": "active",
        },
    ]
    distributions = [
        {
            "capability_kind": "skill",
            "capability_id": "general-chat",
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["QA"],
            "allowed_roles": ["qa-operator"],
        },
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa-operator"],
        },
        {
            "capability_kind": "skill",
            "capability_id": "retired-skill",
            "status": "disabled",
            "visible_to_user": False,
            "scope_mode": "allowlist",
            "department_ids": ["translation"],
            "allowed_roles": ["translator"],
        },
    ]
    audits = []

    async def fake_list_agents(conn, *, tenant_id):
        assert tenant_id == "tenant-a"
        return rows

    async def fake_list_distributions(conn, *, tenant_id, capability_kind, include_disabled):
        assert (tenant_id, capability_kind, include_disabled) == ("tenant-a", "skill", True)
        return distributions

    async def fake_append_audit(conn, **kwargs):
        audits.append(kwargs)
        return f"audit-{len(audits)}"

    monkeypatch.setattr(principal_catalog_persistence, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(principal_catalog_persistence, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(principal_catalog_persistence, "append_audit_log", fake_append_audit)

    authorized = await _repo_owner_app_agent_apps_infrastructure_principal_catalog_postgres.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="qa-user",
        department_id="QA",
        roles=[" QA-OPERATOR "],
        is_admin=False,
        permissions=["chat:read"],
    )
    admin_rows = await _repo_owner_app_agent_apps_infrastructure_principal_catalog_postgres.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="admin-a",
        department_id="platform",
        roles=["admin"],
        is_admin=True,
        permissions=["chat:read"],
    )

    assert [row["id"] for row in authorized] == ["general-agent"]
    assert [row["id"] for row in admin_rows] == [
        "general-agent",
        "qa-word-review",
        "retired-agent",
    ]
    assert len(audits) == 3
    assert {audit["target_id"] for audit in audits} == {
        "general-chat",
        "qa-file-reviewer",
        "retired-skill",
    }
    for audit in audits:
        assert audit["action"] == "capability_distribution.admin_bypass"
        assert audit["target_type"] == "skill"
        assert audit["payload_json"]["admin_bypass"] is True
        assert audit["payload_json"]["decision_reason"] == "admin_bypass"


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [False, True])
async def test_principal_agent_projection_hides_archived_default_skill_for_every_principal(monkeypatch, is_admin):
    async def fake_list_agents(conn, *, tenant_id):
        return [
            {"id": "general-agent", "default_skill_id": "general-chat", "status": "active"},
            {"id": "qa-word-review", "default_skill_id": "qa-file-reviewer", "status": "active"},
        ]

    async def fake_list_distributions(conn, **kwargs):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "general-chat",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {},
            },
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "disabled",
                "visible_to_user": False,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata": {"archived_at": "2026-07-15T00:00:00.000Z"},
            },
        ]

    audits = []

    async def fake_append_audit(conn, **kwargs):
        audits.append(kwargs)
        return "audit"

    monkeypatch.setattr(principal_catalog_persistence, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(principal_catalog_persistence, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(principal_catalog_persistence, "append_audit_log", fake_append_audit)

    rows = await _repo_owner_app_agent_apps_infrastructure_principal_catalog_postgres.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="admin-a" if is_admin else "user-a",
        department_id="platform" if is_admin else "qa",
        roles=["admin"] if is_admin else ["qa_operator"],
        is_admin=is_admin,
        permissions=["chat:read"],
    )

    assert [row["id"] for row in rows] == ["general-agent"]
    assert [audit["target_id"] for audit in audits] == (["general-chat"] if is_admin else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["draft", "reviewed", "disabled", "deprecated"])
async def test_principal_agent_projection_hides_non_runnable_rollout_selected_previous_version(
    monkeypatch,
    previous_status,
):
    async def fake_list_agents(conn, *, tenant_id):
        return [
            {
                "id": "qa-word-review",
                "default_skill_id": "qa-file-reviewer",
                "status": "active",
                "skill_version": "hash-new",
                "skill_version_status": "released",
                "release_policy_version": "hash-new",
                "release_policy_previous_version": "hash-old",
                "release_policy_rollout_percent": 0,
                "release_policy_previous_version_status": previous_status,
            }
        ]

    async def fake_list_distributions(conn, **kwargs):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
            }
        ]

    monkeypatch.setattr(principal_catalog_persistence, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(principal_catalog_persistence, "list_capability_distribution_rows", fake_list_distributions)

    rows = await _repo_owner_app_agent_apps_infrastructure_principal_catalog_postgres.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="previous-track-user",
        department_id="QA",
        roles=["user"],
        is_admin=False,
        permissions=["chat:read"],
    )

    assert rows == []


@pytest.mark.asyncio
async def test_principal_agent_projection_projects_runnable_rollout_selected_previous_version(monkeypatch):
    async def fake_list_agents(conn, *, tenant_id):
        return [
            {
                "id": "qa-word-review",
                "default_skill_id": "qa-file-reviewer",
                "status": "active",
                "skill_version": "hash-new",
                "skill_version_status": "released",
                "release_policy_version": "hash-new",
                "release_policy_previous_version": "hash-old",
                "release_policy_rollout_percent": 0,
                "release_policy_previous_version_status": "released",
            }
        ]

    async def fake_list_distributions(conn, **kwargs):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
            }
        ]

    monkeypatch.setattr(principal_catalog_persistence, "list_lambchat_agents", fake_list_agents)
    monkeypatch.setattr(principal_catalog_persistence, "list_capability_distribution_rows", fake_list_distributions)

    rows = await _repo_owner_app_agent_apps_infrastructure_principal_catalog_postgres.list_principal_lambchat_agents(
        object(),
        tenant_id="tenant-a",
        actor_user_id="previous-track-user",
        department_id="QA",
        roles=["user"],
        is_admin=False,
        permissions=["chat:read"],
    )

    assert rows[0]["skill_version"] == "hash-old"
    assert rows[0]["skill_version_status"] == "released"
    assert "release_policy_previous_version_status" not in rows[0]


@pytest.mark.asyncio
async def test_capability_distribution_upsert_and_toggle_raise_controlled_not_found_errors():
    upsert = _repo_owner_app_identity_infrastructure_capability_distributions_postgres.upsert_capability_distribution_row
    toggle = _repo_owner_app_identity_infrastructure_capability_distributions_postgres.toggle_capability_distribution_row
    assert callable(upsert), "upsert_capability_distribution_row missing"
    assert callable(toggle), "toggle_capability_distribution_row missing"

    class MissingCursor:
        async def fetchone(self):
            return None

    class Connection:
        async def execute(self, sql, params=()):
            return MissingCursor()

    conn = Connection()
    kwargs = {
        "tenant_id": "tenant-a",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
    }
    with pytest.raises(RepositoryNotFoundError, match="capability_distribution_not_found"):
        await upsert(
            conn,
            **kwargs,
            status="active",
            visible_to_user=True,
            scope_mode="allowlist",
            department_ids=["qa"],
            allowed_roles=["qa_operator"],
            metadata_json={},
            updated_by="admin-a",
        )
    with pytest.raises(RepositoryNotFoundError, match="capability_distribution_not_found"):
        await toggle(conn, **kwargs, enabled=False, updated_by="admin-a")


@pytest.mark.asyncio
async def test_archive_capability_distribution_is_tenant_scoped_and_idempotent(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []
            self.rows = {
                ("tenant-a", "skill", "qa-file-reviewer"): {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "active",
                    "visible_to_user": True,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {},
                    "updated_by": None,
                },
                ("tenant-b", "skill", "qa-file-reviewer"): {
                    "id": "capdist-b",
                    "tenant_id": "tenant-b",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "active",
                    "visible_to_user": True,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {},
                    "updated_by": None,
                },
            }

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if "pg_advisory_xact_lock" in compact:
                return Cursor(None)
            if compact.startswith("select metadata_json"):
                tenant_id, capability_kind, capability_id = params
                row = self.rows.get((tenant_id, capability_kind, capability_id))
                return Cursor({"metadata_json": row["metadata_json"]} if row is not None else None)
            assert compact.startswith("update tenant_capability_distributions")
            preserve_existing_evidence, _, archived_by, updated_by, tenant_id, capability_kind, capability_id = params
            row = self.rows.get((tenant_id, capability_kind, capability_id))
            if row is None:
                return Cursor(None)
            metadata_json = row["metadata_json"]
            if not preserve_existing_evidence:
                metadata_json["archived_at"] = "2026-07-15T00:00:00.000Z"
                metadata_json["archived_by"] = archived_by[:255]
            row["status"] = "disabled"
            row["visible_to_user"] = False
            row["updated_by"] = updated_by
            return Cursor(dict(row))

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()

    first = await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.archive_capability_distribution_row(
        conn,
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        archived_by="admin-a",
    )
    second = await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.archive_capability_distribution_row(
        conn,
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        archived_by="admin-b",
    )

    assert first["status"] == second["status"] == "disabled"
    assert first["visible_to_user"] is second["visible_to_user"] is False
    assert second["metadata_json"] == {
        "archived_at": "2026-07-15T00:00:00.000Z",
        "archived_by": "admin-a",
    }
    assert conn.rows[("tenant-b", "skill", "qa-file-reviewer")]["status"] == "active"
    assert conn.rows[("tenant-b", "skill", "qa-file-reviewer")]["metadata_json"] == {}
    assert all("delete from" not in sql for sql, _ in conn.calls)
    assert all(
        forbidden not in " ".join(sql for sql, _ in conn.calls)
        for forbidden in ("skill_versions", "package_objects", "run_skill_snapshots")
    )


@pytest.mark.parametrize(
    "archive_marker",
    [None, "", "invalid", "2026-02-30T00:00:00.000Z", [], {}, False],
)
def test_repository_archive_predicate_matches_strict_shared_timestamp_semantics(archive_marker):
    assert _repo_owner_app_identity_infrastructure_capability_distributions_postgres.is_capability_distribution_archived(
        {"metadata_json": {"archived_at": archive_marker}}
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_metadata", "preserve_evidence"),
    [
        (
            {"archived_at": "2026-07-15T00:00:00.000Z", "archived_by": "admin-a"},
            True,
        ),
        (
            {"archived_at": "invalid", "archived_by": ["admin-a"]},
            False,
        ),
        (
            {"archived_at": "2026-07-15T00:00:00.000Z", "archived_by": ["admin-a"]},
            False,
        ),
    ],
)
async def test_archive_distribution_preserves_only_valid_first_evidence(
    monkeypatch,
    existing_metadata,
    preserve_evidence,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            if "pg_advisory_xact_lock" in compact:
                return Cursor(None)
            if compact.startswith("select metadata_json"):
                return Cursor({"metadata_json": existing_metadata})
            assert compact.startswith("update tenant_capability_distributions")
            assert "case when %s::boolean" in compact
            assert params[0:2] == (preserve_evidence, preserve_evidence)
            metadata_json = (
                existing_metadata
                if preserve_evidence
                else {"archived_at": "2026-07-15T01:02:03.004Z", "archived_by": "admin-b"}
            )
            return Cursor(
                {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "disabled",
                    "visible_to_user": False,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": metadata_json,
                    "updated_by": "admin-b",
                }
            )

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    archived = await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.archive_capability_distribution_row(
        Connection(),
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        archived_by="admin-b",
    )

    assert archived["metadata_json"]["archived_at"] != "invalid"
    assert archived["metadata_json"]["archived_by"] == ("admin-a" if preserve_evidence else "admin-b")


@pytest.mark.asyncio
async def test_invalid_archive_marker_does_not_block_distribution_status_update(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            self.calls.append((sql, params))
            compact = " ".join(sql.split())
            if compact.startswith("select metadata_json"):
                return Cursor({"metadata_json": {"archived_at": "invalid"}})
            assert "metadata_json ? 'archived_at'" not in compact
            if compact.startswith("update tenant_capability_distributions"):
                assert compact.count("%s") == len(params)
                assert params == (
                    True,
                    True,
                    "admin-a",
                    "tenant-a",
                    "skill",
                    "qa-file-reviewer",
                )
                assert "catalog_generation" not in compact
                assert "catalog_status" not in compact
            return Cursor(
                {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "active",
                    "visible_to_user": True,
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {"archived_at": "invalid"},
                }
            )

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()
    row = await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.toggle_capability_distribution_row(
        conn,
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_id="qa-file-reviewer",
        enabled=True,
        updated_by="admin-a",
    )

    assert row["status"] == "active"
    update_params = next(params for sql, params in conn.calls if "update tenant_capability_distributions" in sql)
    assert update_params == (True, True, "admin-a", "tenant-a", "skill", "qa-file-reviewer")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "distribution_status"),
    [(True, "active"), (False, "disabled")],
)
async def test_mcp_distribution_toggle_locks_existing_server(
    monkeypatch,
    enabled,
    distribution_status,
):
    async def no_backfill(conn, *, tenant_id):
        assert tenant_id == "tenant-a"

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if "pg_advisory_xact_lock" in compact:
                return Cursor(None)
            if compact.startswith("select metadata_json"):
                return Cursor({"metadata_json": {}})
            if compact.startswith("update tenant_capability_distributions"):
                assert "catalog_status" not in compact
                assert params == (
                    enabled,
                    enabled,
                    "admin-a",
                    "tenant-a",
                    "mcp_server",
                    "qa-mcp",
                )
                return Cursor(
                    {
                        "id": "capdist-mcp",
                        "tenant_id": "tenant-a",
                        "capability_kind": "mcp_server",
                        "capability_id": "qa-mcp",
                        "status": distribution_status,
                        "visible_to_user": True,
                        "scope_mode": "allowlist",
                        "department_ids": [],
                        "allowed_roles": [],
                        "metadata_json": {},
                    }
                )
            if compact.startswith("select 1 as present from mcp_servers"):
                assert "status <> 'deleted'" in compact
                assert "for update" in compact
                assert params == ("tenant-a", "qa-mcp")
                return Cursor({"name": "qa-mcp"})
            raise AssertionError(compact)

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    row = await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.toggle_capability_distribution_row(
        Connection(),
        tenant_id="tenant-a",
        capability_kind="mcp_server",
        capability_id="qa-mcp",
        enabled=enabled,
        updated_by="admin-a",
    )

    assert row["status"] == distribution_status


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, "", "   ", [], "x" * 256])
async def test_archive_distribution_rejects_invalid_actor_before_database_write(monkeypatch, actor):
    async def fail_backfill(*args, **kwargs):
        raise AssertionError("invalid archive actor must fail before database access")

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", fail_backfill)

    with pytest.raises(RepositoryConflictError, match="capability_distribution_archive_actor_invalid"):
        await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.archive_capability_distribution_row(
            object(),
            tenant_id="tenant-a",
            capability_kind="skill",
            capability_id="qa-file-reviewer",
            archived_by=actor,
        )


@pytest.mark.asyncio
async def test_batch_lifecycle_locks_use_canonical_order_without_duplicates(monkeypatch):
    class Cursor:
        async def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            events.append(("lock", params[0]))
            return Cursor()

    conn = Connection()
    events = []

    async def completed_backfill(conn, *, tenant_id):
        events.append(("ensure", tenant_id))

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", completed_backfill)
    await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.acquire_capability_distribution_lifecycle_locks(
        conn,
        tenant_id="tenant-a",
        capability_kind="skill",
        capability_ids=["skill-b", "skill-a", "skill-b"],
    )

    assert events == [
        ("ensure", "tenant-a"),
        ("lock", '{"capability_id":"skill-a","capability_kind":"skill","tenant_id":"tenant-a"}'),
        ("lock", '{"capability_id":"skill-b","capability_kind":"skill","tenant_id":"tenant-a"}'),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["archive", "upsert", "toggle", "set_status"])
async def test_capability_distribution_lifecycle_lock_precedes_row_lock_and_write(monkeypatch, operation):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if "pg_advisory_xact_lock" in compact:
                return Cursor(None)
            if compact.startswith("select metadata_json"):
                return Cursor(
                    {"metadata_json": {}}
                    if operation in {"archive", "toggle"}
                    else None
                )
            return Cursor(
                {
                    "id": "capdist-a",
                    "tenant_id": "tenant-a",
                    "capability_kind": "skill",
                    "capability_id": "qa-file-reviewer",
                    "status": "disabled" if operation == "archive" else "active",
                    "visible_to_user": operation != "archive",
                    "scope_mode": "allowlist",
                    "department_ids": [],
                    "allowed_roles": [],
                    "metadata_json": {},
                }
            )

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()
    kwargs = {
        "tenant_id": "tenant-a",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
    }

    if operation == "archive":
        await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.archive_capability_distribution_row(conn, **kwargs, archived_by="admin-a")
    elif operation == "upsert":
        await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.upsert_capability_distribution_row(
            conn,
            **kwargs,
            status="active",
            visible_to_user=True,
            scope_mode="allowlist",
            department_ids=[],
            allowed_roles=[],
            metadata_json={},
            updated_by="admin-a",
        )
    elif operation == "toggle":
        await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.toggle_capability_distribution_row(conn, **kwargs, enabled=True, updated_by="admin-a")
    else:
        await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.set_capability_distribution_status(conn, **kwargs, status="active", updated_by="admin-a")

    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == ('{"capability_id":"qa-file-reviewer","capability_kind":"skill","tenant_id":"tenant-a"}',)
    assert conn.calls[1][0].startswith("select metadata_json")
    assert conn.calls[1][0].endswith("for update")
    assert conn.calls[2][0].startswith(("insert into tenant_capability_distributions", "update tenant_capability_distributions"))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["toggle", "set_status", "upsert"])
async def test_archived_capability_distribution_rejects_reactivation(monkeypatch, operation):
    async def no_backfill(conn, *, tenant_id):
        return None

    class Cursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params=()):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("select metadata_json"):
                return Cursor({"metadata_json": {"archived_at": "2026-07-15T00:00:00.000Z"}})
            return Cursor(None)

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    conn = Connection()
    kwargs = {
        "tenant_id": "tenant-a",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
    }

    with pytest.raises(RepositoryConflictError, match="capability_distribution_archived"):
        if operation == "toggle":
            await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.toggle_capability_distribution_row(conn, **kwargs, enabled=True, updated_by="admin-a")
        elif operation == "set_status":
            await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.set_capability_distribution_status(conn, **kwargs, status="active", updated_by="admin-a")
        else:
            await _repo_owner_app_identity_infrastructure_capability_distributions_postgres.upsert_capability_distribution_row(
                conn,
                **kwargs,
                status="active",
                visible_to_user=True,
                scope_mode="allowlist",
                department_ids=[],
                allowed_roles=[],
                metadata_json={},
                updated_by="admin-a",
            )

    assert any(sql.startswith("select metadata_json") and sql.endswith("for update") for sql, _ in conn.calls)
    assert not any("metadata_json ? 'archived_at'" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_authorize_selected_run_capabilities_fails_closed_for_archived_distribution(monkeypatch):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v1",
            "skill_content_hash": "hash-v1",
            "skill_version_status": "active",
            "release_policy_version": None,
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def archived_distribution(conn, **kwargs):
        return {
            "status": "disabled",
            "visible_to_user": False,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
            "metadata_json": {"archived_at": "2026-07-15T00:00:00.000Z"},
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_selected_skill", resolve_selected)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", archived_distribution)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_selected_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="qa-file-reviewer",
            expected_version="hash-v1",
            rollout_key="admin-a",
            normalized_input={},
            principal_department_id="platform",
            principal_roles=["admin"],
            is_admin=True,
            permissions=["skill:write"],
        )


@pytest.mark.asyncio
async def test_capability_distribution_authorization_allows_same_department_skill_and_mcp_tool(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        calls.append(("skill", tenant_id, agent_id, skill_id))
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(("distribution", capability_kind, capability_id))
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa-operator"],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        calls.append(("tool", tenant_id, tool_id))
        return {
            "tool_id": tool_id,
            "server_id": "qa-mcp",
            "effective_status": "active",
            "server_status": "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(capability_admission_persistence, "get_mcp_tool_registry_entry", fake_get_tool)

    skill = await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="general-chat",
        normalized_input={"mcp_tool_ids": ["qa-search"]},
        principal_department_id="qa",
        principal_roles=[" QA-Operator "],
        is_admin=False,
        permissions=[],
    )

    assert skill["skill_id"] == "general-chat"
    assert calls == [
        ("skill", "tenant-a", "general-agent", "general-chat"),
        ("distribution", "skill", "general-chat"),
        ("tool", "tenant-a", "qa-search"),
        ("distribution", "mcp_server", "qa-mcp"),
    ]


@pytest.mark.asyncio
async def test_harness_skill_authorization_derives_canonical_backing_tool_without_explicit_selector(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": "tenant-search",
        }

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(("distribution", capability_kind, capability_id))
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        calls.append(("tool", tool_id))
        return {
            "tool_id": tool_id,
            "server_id": "tenant-search-server",
            "effective_status": "active",
            "server_status": "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(capability_admission_persistence, "get_mcp_tool_registry_entry", fake_get_tool)

    await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="sop-assistant",
        skill_id="knowledge-skill",
        normalized_input={},
        principal_department_id="qa",
        principal_roles=["qa_operator"],
        is_admin=False,
        permissions=[],
    )

    assert calls == [
        ("distribution", "skill", "knowledge-skill"),
        ("tool", "tenant-search"),
        ("distribution", "mcp_server", "tenant-search-server"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denial",
    ["backing_missing", "tool_missing", "hidden", "disabled", "department", "role", "parent_disabled"],
)
async def test_harness_backed_ragflow_skill_authorization_fails_closed_for_current_parent_and_tool_state(
    monkeypatch,
    denial,
):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": None if denial == "backing_missing" else "tenant-search",
        }

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        row = {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }
        if capability_kind == "mcp_server":
            if denial == "hidden":
                row["visible_to_user"] = False
            elif denial == "disabled":
                row["status"] = "disabled"
            elif denial == "department":
                row["department_ids"] = ["finance"]
            elif denial == "role":
                row["allowed_roles"] = ["reviewer"]
        return row

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        if denial == "tool_missing":
            return None
        return {
            "tool_id": tool_id,
            "server_id": "tenant-search-server",
            "effective_status": "active",
            "server_status": "disabled" if denial == "parent_disabled" else "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(capability_admission_persistence, "get_mcp_tool_registry_entry", fake_get_tool)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="sop-assistant",
            skill_id="ragflow-knowledge-search",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["missing", "hidden", "disabled", "department", "role"])
async def test_capability_distribution_authorization_denies_skill_before_enqueue(monkeypatch, denial):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        if denial == "missing":
            raise RepositoryNotFoundError("agent_or_skill_not_found")
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        row = {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }
        if denial == "hidden":
            row["visible_to_user"] = False
        elif denial == "disabled":
            row["status"] = "disabled"
        elif denial == "department":
            row["department_ids"] = ["finance"]
        elif denial == "role":
            row["allowed_roles"] = ["reviewer"]
        return row

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", fake_get_distribution)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_capability_distribution_denial_carries_sanitized_immutable_audit_record(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {
            "skill_id": skill_id,
            "skill_status": "active",
            "executor_type": "claude-agent-worker",
        }

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["finance"],
            "allowed_roles": ["reviewer"],
            "metadata_json": {"secret": "must-not-be-audited"},
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", fake_get_distribution)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError) as exc_info:
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={},
            principal_department_id="QA",
            principal_roles=[" QA-Operator ", "qa-operator"],
            is_admin=False,
            permissions=[],
        )

    denial = exc_info.value.denial
    assert denial is not None
    assert denial.audit_payload() == {
        "capability_kind": "skill",
        "capability_id": "general-chat",
        "actor_department_id": "QA",
        "actor_roles": ["qa-operator"],
        "department_scope_ids": ["finance"],
        "role_scope_ids": ["reviewer"],
        "scope_mode": "allowlist",
        "decision_reason": "department_not_allowed",
        "admin_bypass": False,
    }
    with pytest.raises((AttributeError, TypeError)):
        denial.actor_roles += ("admin",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector_state",
    [
        "agent_inactive",
        "skill_inactive",
        "skill_version_not_released",
        "executor_type_not_allowed",
        "agent_skill_mismatch",
        "mcp_tool_disabled",
    ],
)
async def test_capability_distribution_authorization_hides_pre_authorization_selector_state(
    monkeypatch,
    selector_state,
):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        raise _repo_owner_app_platform_postgres_errors.RepositoryConflictError(selector_state)

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={},
            principal_department_id="QA",
            principal_roles=["qa-operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["missing", "disabled", "server_disabled", "department"])
async def test_capability_distribution_authorization_denies_explicit_mcp_tool_before_enqueue(monkeypatch, denial):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        if capability_kind == "skill":
            return {
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": ["qa"],
                "allowed_roles": [],
            }
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["finance"] if denial == "department" else ["qa"],
            "allowed_roles": [],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        if denial == "missing":
            return None
        return {
            "tool_id": tool_id,
            "server_id": "qa-mcp",
            "effective_status": "disabled" if denial == "disabled" else "active",
            "server_status": "disabled" if denial == "server_disabled" else "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(capability_admission_persistence, "get_mcp_tool_registry_entry", fake_get_tool)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={"mcp_tool_ids": ["qa-search"]},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_capability_distribution_skill_revocation_after_original_run_denies_requeue(monkeypatch):
    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def revoked_skill_distribution(conn, *, tenant_id, capability_kind, capability_id):
        assert (capability_kind, capability_id) == ("skill", "general-chat")
        return {
            "status": "disabled",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }

    async def fail_tool_lookup(*args, **kwargs):
        raise AssertionError("revoked Skill must deny before MCP lookup")

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", revoked_skill_distribution)
    monkeypatch.setattr(capability_admission_persistence, "get_mcp_tool_registry_entry", fail_tool_lookup)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={"mcp_tool_ids": ["tool-a"]},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_capability_distribution_mcp_revocation_after_original_run_denies_requeue(monkeypatch):
    calls = []

    async def fake_resolve_agent_skill(conn, *, tenant_id, agent_id, skill_id):
        return {"skill_id": skill_id, "skill_status": "active", "executor_type": "claude-agent-worker"}

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append((capability_kind, capability_id))
        return {
            "status": "active" if capability_kind == "skill" else "disabled",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
        }

    async def fake_get_tool(conn, *, tenant_id, tool_id):
        calls.append(("tool", tool_id))
        return {
            "tool_id": tool_id,
            "server_id": "qa-mcp",
            "effective_status": "active",
            "server_status": "active",
            "visible_to_user": True,
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_agent_skill", fake_resolve_agent_skill)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(capability_admission_persistence, "get_mcp_tool_registry_entry", fake_get_tool)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="general-chat",
            normalized_input={"mcpToolIds": ["revoked-tool"]},
            principal_department_id="qa",
            principal_roles=["qa_operator"],
            is_admin=False,
            permissions=[],
        )

    assert calls == [
        ("skill", "general-chat"),
        ("tool", "revoked-tool"),
        ("mcp_server", "qa-mcp"),
    ]
