import pytest
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
import app.runs.infrastructure.capability_admission_postgres as _repo_owner_app_runs_infrastructure_capability_admission_postgres
import app.skills.infrastructure.resolution_postgres as _repo_owner_app_skills_infrastructure_resolution_postgres
import app.skills.infrastructure.run_snapshots_postgres as _repo_owner_app_skills_infrastructure_run_snapshots_postgres
import app.runs.infrastructure.capability_admission_postgres as capability_admission_persistence
from app.platform.postgres.errors import RepositoryConflictError
from tests.support.repository_fixtures import SingleRowConnection


@pytest.mark.asyncio
async def test_resolve_agent_skill_uses_global_skill_lifecycle_and_canonical_backing_tool():
    conn = SingleRowConnection(
        {
            "agent_id": "sop-assistant",
            "agent_status": "active",
            "default_skill_id": "ragflow-knowledge-search",
            "skill_id": "ragflow-knowledge-search",
            "skill_status": "active",
            "skill_version": "0.1.0",
            "skill_version_status": "active",
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": "ragflow-knowledge-search",
            "input_modes": ["chat"],
        }
    )

    row = await _repo_owner_app_skills_infrastructure_resolution_postgres.resolve_agent_skill(
        conn,
        tenant_id="tenant-a",
        agent_id="sop-assistant",
        skill_id="ragflow-knowledge-search",
    )

    assert row["backing_mcp_tool_id"] == "ragflow-knowledge-search"
    assert "skills.status as skill_status" in conn.sql
    assert "tenant_workbench_skills" not in conn.sql


@pytest.mark.asyncio
async def test_resolve_selected_skill_allows_active_non_default_skill():
    conn = SingleRowConnection(
        {
            "agent_id": "general-agent",
            "agent_status": "active",
            "default_skill_id": "general-chat",
            "skill_id": "department-review",
            "skill_status": "active",
            "skill_version": "hash-review-v1",
            "skill_content_hash": "hash-review-v1",
            "skill_version_status": "active",
            "release_policy_version": "hash-review-v1",
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": None,
            "input_modes": ["docx"],
        }
    )

    row = await _repo_owner_app_skills_infrastructure_resolution_postgres.resolve_selected_skill(
        conn,
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="department-review",
    )

    assert row["skill_id"] == "department-review"
    assert row["default_skill_id"] == "general-chat"


@pytest.mark.asyncio
async def test_resolve_selected_skill_rejects_non_materializable_version_identity():
    conn = SingleRowConnection(
        {
            "agent_id": "general-agent",
            "agent_status": "active",
            "default_skill_id": "general-chat",
            "skill_id": "department-review",
            "skill_status": "active",
            "skill_version": "hash-review-v1",
            "skill_content_hash": "different-content-hash",
            "skill_version_status": "active",
            "release_policy_version": "hash-review-v1",
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "backing_mcp_tool_id": None,
            "input_modes": ["docx"],
        }
    )

    with pytest.raises(RepositoryConflictError, match="skill_version_not_materializable"):
        await _repo_owner_app_skills_infrastructure_resolution_postgres.resolve_selected_skill(
            conn,
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
        )


@pytest.mark.asyncio
async def test_authorize_selected_run_capabilities_returns_stable_stale_conflict_without_current_version(
    monkeypatch,
):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v2",
            "skill_content_hash": "hash-v2",
            "skill_version_status": "active",
            "release_policy_version": "hash-v2",
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def distribution(conn, **kwargs):
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["reviewer"],
        }

    async def exact_version(conn, *, skill_id, version):
        return {"skill_id": skill_id, "version": version, "content_hash": version, "status": "active"}

    monkeypatch.setattr(capability_admission_persistence, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", distribution)
    monkeypatch.setattr(capability_admission_persistence, "get_effective_skill_version_for_policy", exact_version)

    with pytest.raises(RepositoryConflictError) as exc_info:
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_selected_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
            expected_version="hash-v1",
            rollout_key="user-a",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["reviewer"],
            is_admin=False,
            permissions=[],
        )

    assert str(exc_info.value) == "skill_selection_stale"
    assert "hash-v2" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_authorize_selected_run_capabilities_rejects_version_content_hash_invariant(
    monkeypatch,
):
    async def resolve_selected(conn, *, tenant_id, agent_id, skill_id):
        return {
            "agent_id": agent_id,
            "agent_status": "active",
            "skill_id": skill_id,
            "skill_status": "active",
            "skill_version": "hash-v1",
            "skill_content_hash": "different-hash",
            "skill_version_status": "active",
            "release_policy_version": None,
            "release_policy_previous_version": None,
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def distribution(conn, **kwargs):
        return {
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
        }

    monkeypatch.setattr(capability_admission_persistence, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", distribution)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_selected_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
            expected_version="hash-v1",
            rollout_key="user-a",
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["reviewer"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_resolve_agent_skill_uses_tenant_stable_release_policy():
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "qa-word-review",
                "agent_status": "active",
                "default_skill_id": "qa-file-reviewer",
                "skill_id": "qa-file-reviewer",
                "skill_status": "active",
                "skill_version": "hash-release",
                "release_policy_version": "hash-release",
                "release_policy_previous_version": "hash-previous",
                "release_policy_rollout_percent": 50,
                "executor_type": "claude-agent-worker",
                "mcp_tool_status": None,
                "input_modes": ["docx"],
            }

    class ResolveConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ResolveCursor()

    conn = ResolveConnection()

    row = await _repo_owner_app_skills_infrastructure_resolution_postgres.resolve_agent_skill(
        conn,
        tenant_id="default",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
    )

    assert "left join skill_release_policies" in conn.sql
    assert "coalesce(skill_release_policies.current_version, skills.version) as skill_version" in conn.sql
    assert "skill_release_policies.current_version as release_policy_version" in conn.sql
    assert "skill_release_policies.previous_version as release_policy_previous_version" in conn.sql
    assert "skill_release_policies.rollout_percent as release_policy_rollout_percent" in conn.sql
    assert "skill_release_policies.channel = 'stable'" in conn.sql
    assert conn.params == ("qa-file-reviewer", "default", "qa-word-review")
    assert row["skill_version"] == "hash-release"
    assert row["release_policy_version"] == "hash-release"
    assert row["release_policy_previous_version"] == "hash-previous"
    assert row["release_policy_rollout_percent"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("version_status", ["draft", "reviewed", "disabled", "deprecated"])
async def test_resolve_agent_skill_rejects_unreleased_policy_version(version_status):
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "qa-word-review",
                "agent_status": "active",
                "default_skill_id": "qa-file-reviewer",
                "skill_id": "qa-file-reviewer",
                "skill_status": "active",
                "skill_version": "hash-release",
                "skill_version_status": version_status,
                "release_policy_version": "hash-release",
                "release_policy_previous_version": None,
                "release_policy_rollout_percent": 100,
                "executor_type": "claude-agent-worker",
                "mcp_tool_status": None,
                "input_modes": ["docx"],
            }

    class ResolveConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ResolveCursor()

    conn = ResolveConnection()

    with pytest.raises(RepositoryConflictError, match="skill_version_not_released"):
        await _repo_owner_app_skills_infrastructure_resolution_postgres.resolve_agent_skill(
            conn,
            tenant_id="default",
            agent_id="qa-word-review",
            skill_id="qa-file-reviewer",
        )

    assert "coalesce(skill_versions.status, 'active') as skill_version_status" in conn.sql


@pytest.mark.asyncio
async def test_resolve_agent_skill_rejects_embedded_poco_executor_fact_source():
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "general-agent",
                "agent_status": "active",
                "default_skill_id": "general-chat",
                "skill_id": "general-chat",
                "skill_status": "active",
                "skill_version": "0.1.0",
                "release_policy_version": None,
                "executor_type": "embedded-poco-kernel",
                "mcp_tool_status": None,
                "input_modes": ["chat"],
            }

    class ResolveConnection:
        async def execute(self, sql, params):
            return ResolveCursor()

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryConflictError, match="executor_type_not_allowed"):
        await _repo_owner_app_skills_infrastructure_resolution_postgres.resolve_agent_skill(
            ResolveConnection(),
            tenant_id="default",
            agent_id="general-agent",
            skill_id="general-chat",
        )


def test_skill_snapshot_reader_normalizes_legacy_governance_boundary_marker():
    source = _repo_owner_app_skills_infrastructure_run_snapshots_postgres._sanitize_skill_snapshot_source(
        {
            "kind": "builtin",
            "snapshot_governance": {
                "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                "snapshot_source": "platform_release_lock",
                "does_not_close_b4_or_211": True,
            },
        }
    )

    governance = source["snapshot_governance"]
    assert governance["schema_version"] == "ai-platform.skill-pinned-snapshot-governance.v1"
    assert governance["does_not_close_b4_or_deployed_runtime_acceptance"] is True
    assert "does_not_close_b4_or_211" not in governance
