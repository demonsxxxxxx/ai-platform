import json
import pytest
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
import app.runs.infrastructure.capability_admission_postgres as _repo_owner_app_runs_infrastructure_capability_admission_postgres
import app.runs.infrastructure.replay_postgres as _repo_owner_app_runs_infrastructure_replay_postgres
import app.skills.infrastructure.postgres as _repo_owner_app_skills_infrastructure_postgres
import app.skills.infrastructure.run_snapshots_postgres as _repo_owner_app_skills_infrastructure_run_snapshots_postgres
import app.runs.infrastructure.capability_admission_postgres as capability_admission_persistence
import app.runs.infrastructure.replay_postgres as replay_persistence
from app.platform.postgres.limits import RUN_INPUT_MAX_BYTES
from app.skills.infrastructure import postgres as skill_persistence
from app.platform.postgres.errors import RepositoryConflictError
from tests.support.repository_fixtures import RecordingConnection


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_status", ["active", "released", "deprecated"])
async def test_authorize_replay_run_capabilities_keeps_exact_v1_after_current_v2(
    monkeypatch,
    historical_status,
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
            "release_policy_previous_version": "hash-v1",
            "release_policy_rollout_percent": 100,
            "executor_type": "claude-agent-worker",
            "input_modes": [],
        }

    async def distribution(conn, **kwargs):
        return {"status": "active", "visible_to_user": True, "department_ids": [], "allowed_roles": []}

    async def historical_version(conn, *, skill_id, version):
        assert version == "hash-v1"
        return {"skill_id": skill_id, "version": "hash-v1", "content_hash": "hash-v1", "status": historical_status}

    monkeypatch.setattr(capability_admission_persistence, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", distribution)
    monkeypatch.setattr(skill_persistence, "get_skill_version", historical_version)

    skill = await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_replay_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="department-review",
        pinned_version="hash-v1",
        pinned_executor_type="claude-agent-worker",
        skill_manifests=[
            {
                "skill_id": "department-review",
                "version": "hash-v1",
                "content_hash": "hash-v1",
                "source": {"kind": "uploaded"},
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_ids": [],
                "mcp_tool_ids": [],
            }
        ],
        normalized_input={},
        principal_department_id="qa",
        principal_roles=["reviewer"],
        is_admin=False,
        permissions=[],
    )

    assert skill["skill_version"] == "hash-v2"


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_status", ["disabled", "security_revoked"])
async def test_authorize_replay_run_capabilities_blocks_revoked_historical_pin(
    monkeypatch,
    historical_status,
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
        return {"status": "active", "visible_to_user": True, "department_ids": [], "allowed_roles": []}

    async def historical_version(conn, *, skill_id, version):
        return {"skill_id": skill_id, "version": version, "content_hash": version, "status": historical_status}

    monkeypatch.setattr(capability_admission_persistence, "resolve_selected_skill", resolve_selected, raising=False)
    monkeypatch.setattr(capability_admission_persistence, "get_capability_distribution_row", distribution)
    monkeypatch.setattr(skill_persistence, "get_skill_version", historical_version)

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_replay_run_capabilities(
            object(),
            tenant_id="tenant-a",
            agent_id="general-agent",
            skill_id="department-review",
            pinned_version="hash-v1",
            pinned_executor_type="claude-agent-worker",
            skill_manifests=[
                {
                    "skill_id": "department-review",
                    "version": "hash-v1",
                    "content_hash": "hash-v1",
                    "source": {"kind": "uploaded"},
                    "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                    "dependency_ids": [],
                    "mcp_tool_ids": [],
                }
            ],
            normalized_input={},
            principal_department_id="qa",
            principal_roles=["reviewer"],
            is_admin=False,
            permissions=[],
        )


@pytest.mark.asyncio
async def test_authorize_replay_run_capabilities_reauthorizes_harness_pinned_mcp(monkeypatch):
    calls = []

    async def shared_authorizer(conn, **kwargs):
        calls.append(kwargs["normalized_input"])
        return {
            "skill_id": kwargs["skill_id"],
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-v2",
        }

    async def historical_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "status": "active",
        }

    monkeypatch.setattr(capability_admission_persistence, "_authorize_run_capabilities", shared_authorizer)
    monkeypatch.setattr(skill_persistence, "get_skill_version", historical_version)

    await _repo_owner_app_runs_infrastructure_capability_admission_postgres.authorize_replay_run_capabilities(
        object(),
        tenant_id="tenant-a",
        agent_id="general-agent",
        skill_id="knowledge-v1",
        pinned_version="hash-v1",
        pinned_executor_type="claude-agent-worker",
        skill_manifests=[
            {
                "skill_id": "knowledge-v1",
                "version": "hash-v1",
                "content_hash": "hash-v1",
                "source": {"kind": "uploaded"},
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_ids": [],
                "mcp_tool_ids": ["historical-search"],
            }
        ],
        normalized_input={"message": "search"},
        principal_department_id="qa",
        principal_roles=["reviewer"],
        is_admin=False,
        permissions=[],
    )

    assert calls == [{"message": "search", "mcp_tool_ids": ["historical-search"]}]


def test_historical_direct_ragflow_replay_fails_closed():
    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        _repo_owner_app_runs_infrastructure_capability_admission_postgres.pinned_replay_mcp_tool_ids(
            skill_id="knowledge-v1",
            pinned_version="hash-v1",
            pinned_executor_type="ragflow",
            skill_manifests=[
                {
                    "skill_id": "knowledge-v1",
                    "version": "hash-v1",
                    "mcp_tool_ids": ["historical-search"],
                }
            ],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_refs",
    [
        pytest.param("mixed-string", id="mixed-string"),
        pytest.param("mixed-null", id="mixed-null"),
        pytest.param("not-a-list", id="not-a-list"),
        pytest.param(None, id="null"),
    ],
)
async def test_copy_run_as_new_task_rejects_malformed_skill_manifest_transport_before_writes(
    monkeypatch,
    malformed_refs,
):
    source_manifest = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": [],
    }
    source_ref = _repo_owner_app_skills_infrastructure_run_snapshots_postgres.skill_manifest_refs([source_manifest])[0]
    transported_refs = {
        "mixed-string": [source_ref, "unexpected"],
        "mixed-null": [source_ref, None],
        "not-a-list": "unexpected",
        None: None,
    }[malformed_refs]

    async def source_run(conn, **kwargs):
        return {
            "id": "run-source",
            "workspace_id": "default",
            "session_id": "ses-source",
            "agent_id": "general-agent",
            "skill_id": "department-review",
            "principal_roles": ["reviewer"],
            "principal_department_id": "qa",
            "input_json": {
                "input": {"message": "review"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-v1",
                "release_decision": {"selected_version": "hash-v1", "selected_track": "current"},
                "skill_manifests": transported_refs,
            },
        }

    monkeypatch.setattr(replay_persistence, "get_authorized_run", source_run)

    with pytest.raises(RepositoryConflictError, match="run_skill_materialization_identity_mismatch"):
        await _repo_owner_app_runs_infrastructure_replay_postgres.copy_run_as_new_task(
            object(),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-source",
        )


@pytest.mark.asyncio
async def test_copy_run_as_new_task_rejects_source_snapshot_mismatch_before_writes(monkeypatch):
    source_manifest = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": [],
    }
    source_ref = _repo_owner_app_skills_infrastructure_run_snapshots_postgres.skill_manifest_refs([source_manifest])[0]

    async def source_run(conn, **kwargs):
        return {
            "id": "run-source",
            "workspace_id": "default",
            "session_id": "ses-source",
            "agent_id": "general-agent",
            "skill_id": "department-review",
            "principal_roles": ["reviewer"],
            "principal_department_id": "qa",
            "input_json": {
                "input": {"message": "review"},
                "executor_type": "claude-agent-worker",
                "skill_version": "hash-v1",
                "release_decision": {"selected_version": "hash-v1", "selected_track": "current"},
                "skill_manifests": [source_ref],
            },
        }

    async def materialize(*args, **kwargs):
        return [source_manifest]

    async def mismatch(*args, **kwargs):
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")

    async def forbidden_replay(*args, **kwargs):
        raise AssertionError("source snapshot mismatch must deny before replay authorization or writes")

    monkeypatch.setattr(replay_persistence, "get_authorized_run", source_run)
    monkeypatch.setattr(replay_persistence, "materialize_run_skill_manifests", materialize)
    monkeypatch.setattr(replay_persistence, "validate_run_skill_snapshots_for_dispatch", mismatch)
    monkeypatch.setattr(replay_persistence, "authorize_replay_run_capabilities", forbidden_replay)

    with pytest.raises(RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
        await _repo_owner_app_runs_infrastructure_replay_postgres.copy_run_as_new_task(
            object(),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-source",
        )


@pytest.mark.asyncio
async def test_copy_run_as_new_task_reauthorizes_but_persists_source_v1_provenance(monkeypatch):
    source_manifest = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }
    source_release = {
        "schema_version": "ai-platform.skill-release-decision.v1",
        "policy_active": True,
        "selected_version": "hash-v1",
        "selected_track": "current",
    }
    source_ref = _repo_owner_app_skills_infrastructure_run_snapshots_postgres.skill_manifest_refs([source_manifest])[0]
    source = {
        "id": "run-source",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "session_id": "session-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "skill_id": "department-review",
        "principal_roles": ["reviewer"],
        "principal_department_id": "qa",
        "auth_source": "session-token",
        "input_json": {
            "input": {"message": "retry"},
            "file_ids": ["file-prior"],
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-v1",
            "release_decision": source_release,
            "skill_manifests": [source_ref],
        },
    }
    calls = {}

    async def get_source(*args, **kwargs):
        return source

    async def authorize_replay(conn, **kwargs):
        calls["authorize"] = kwargs
        return {"executor_type": "claude-agent-worker", "skill_version": "hash-v2"}

    async def validate_source(conn, **kwargs):
        calls["source_snapshot"] = kwargs

    async def no_completed(*args, **kwargs):
        return {}, {}

    async def no_write(*args, **kwargs):
        return None

    async def insert_snapshots(conn, **kwargs):
        calls["snapshots"] = kwargs

    monkeypatch.setattr(replay_persistence, "get_authorized_run", get_source)
    monkeypatch.setattr(replay_persistence, "authorize_replay_run_capabilities", authorize_replay)
    monkeypatch.setattr(replay_persistence, "validate_run_skill_snapshots_for_dispatch", validate_source)
    monkeypatch.setattr(replay_persistence, "_completed_steps_for_resume", no_completed)
    monkeypatch.setattr(replay_persistence, "append_event", no_write)
    monkeypatch.setattr(replay_persistence, "append_message", no_write)
    monkeypatch.setattr(replay_persistence, "insert_run_skill_snapshots_at_creation", insert_snapshots)

    class MaterializationCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": source_manifest["skill_id"],
                    "materialization_sha256": source_ref[
                        "materialization_sha256"
                    ],
                    "manifest_json": source_manifest,
                }
            ]

    class MaterializationConnection(RecordingConnection):
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if "from run_skill_materializations" in normalized:
                self.calls.append((normalized, params))
                assert params == ("tenant-a", "run-source")
                return MaterializationCursor()
            return await super().execute(sql, params)

    conn = MaterializationConnection()
    copied = await _repo_owner_app_runs_infrastructure_replay_postgres.copy_run_as_new_task(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-source",
    )

    assert calls["authorize"]["pinned_version"] == "hash-v1"
    assert calls["authorize"]["pinned_executor_type"] == "claude-agent-worker"
    assert calls["authorize"]["skill_manifests"] == [source_manifest]
    assert calls["source_snapshot"]["run_id"] == "run-source"
    assert calls["source_snapshot"]["release_decision"] == source_release
    assert calls["snapshots"]["skill_manifests"] == [source_manifest]
    assert copied["skill_version"] == "hash-v1"
    assert copied["release_decision"] == source_release
    assert copied["skill_manifests"] == _repo_owner_app_skills_infrastructure_run_snapshots_postgres.skill_manifest_refs([source_manifest])
    assert "files" not in copied["skill_manifests"][0]
    assert "content_base64" not in json.dumps(copied["skill_manifests"])
    assert copied["file_ids"] == ["file-prior"]
    assert not any(sql.startswith("update files") for sql, _params in conn.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["copy", "retry", "resume"])
async def test_copy_retry_resume_legacy_general_chat_upgrades_child_to_skillless_harness(
    monkeypatch,
    operation,
):
    source = {
        "id": "run-legacy",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "session_id": "session-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "execution_kind": "skill",
        "skill_id": "general-chat",
        "status": "failed",
        "principal_roles": ["user"],
        "principal_department_id": "qa",
        "auth_source": "session-token",
        "input_json": {
            "input": {"message": "continue the historical chat"},
            "file_ids": ["file-prior"],
            "executor_type": "claude-agent-worker",
            "schema_version": "ai-platform.run-payload.v1",
        },
    }
    calls = {}

    async def get_source(*args, **kwargs):
        return source

    async def authorize_mcp(conn, **kwargs):
        calls["mcp"] = kwargs
        return []

    async def completed(*args, **kwargs):
        return {"step-a": "done"}, {}

    async def no_active_child(*args, **kwargs):
        return None

    async def no_write(*args, **kwargs):
        return None

    async def forbid_skill_authority(*args, **kwargs):
        raise AssertionError("legacy base chat must not re-enter Skill replay authority")

    async def record_message(conn, **kwargs):
        calls["message"] = kwargs

    monkeypatch.setattr(replay_persistence, "get_authorized_run", get_source)
    monkeypatch.setattr(replay_persistence, "authorize_selected_chat_mcp_tools", authorize_mcp)
    monkeypatch.setattr(replay_persistence, "authorize_replay_run_capabilities", forbid_skill_authority)
    monkeypatch.setattr(replay_persistence, "validate_run_skill_snapshots_for_dispatch", forbid_skill_authority)
    monkeypatch.setattr(replay_persistence, "insert_run_skill_snapshots_at_creation", forbid_skill_authority)
    monkeypatch.setattr(replay_persistence, "_completed_steps_for_resume", completed)
    monkeypatch.setattr(replay_persistence, "get_active_retry_for_source_run", no_active_child)
    monkeypatch.setattr(replay_persistence, "get_active_resume_for_source_run", no_active_child)
    monkeypatch.setattr(replay_persistence, "append_event", no_write)
    monkeypatch.setattr(replay_persistence, "append_message", record_message)
    monkeypatch.setattr(replay_persistence, "append_audit_log", no_write)

    conn = RecordingConnection()
    operation_function = {
        "copy": _repo_owner_app_runs_infrastructure_replay_postgres.copy_run_as_new_task,
        "retry": _repo_owner_app_runs_infrastructure_replay_postgres.retry_run_as_new_task,
        "resume": _repo_owner_app_runs_infrastructure_replay_postgres.resume_run_as_new_task,
    }[operation]
    copied = await operation_function(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-legacy",
    )

    assert calls["mcp"]["tool_ids"] == []
    assert copied["execution_kind"] == "harness_chat"
    assert copied["skill_id"] is None
    assert copied["schema_version"] == "ai-platform.run-payload.v2"
    assert copied["skill_version"] is None
    assert copied["release_decision"] == {}
    assert copied["skill_manifests"] == []
    assert copied["file_ids"] == ["file-prior"]
    assert calls["message"]["metadata_json"]["skill_id"] is None

    insert_sql, insert_params = next(
        (sql, params) for sql, params in conn.calls if sql.startswith("insert into runs")
    )
    assert "execution_kind, skill_id" in insert_sql
    assert insert_params[6:8] == ("harness_chat", None)
    persisted_input = json.loads(insert_params[19])
    assert persisted_input["schema_version"] == "ai-platform.run-payload.v2"
    assert persisted_input["skill_manifests"] == []


@pytest.mark.asyncio
async def test_copy_run_rejects_expanded_resume_input_before_generation_write(monkeypatch):
    async def get_source(*_args, **_kwargs):
        return {
            "id": "run-source",
            "workspace_id": "workspace-a",
            "session_id": "session-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "principal_roles": ["user"],
            "principal_department_id": "qa",
            "auth_source": "company-login",
            "input_json": {"input": {"message": "retry"}},
        }

    async def allow(*_args, **_kwargs):
        return None

    async def oversized_resume(*_args, **_kwargs):
        return {"step-a": {"value": "x" * RUN_INPUT_MAX_BYTES}}, {}

    async def forbidden_generation(*_args, **_kwargs):
        raise AssertionError("oversized copied input must fail before session generation allocation")

    monkeypatch.setattr(replay_persistence, "get_authorized_run", get_source)
    monkeypatch.setattr(replay_persistence, "require_replay_source_identity", lambda **_kwargs: None)
    monkeypatch.setattr(replay_persistence, "validate_run_skill_snapshots_for_dispatch", allow)
    monkeypatch.setattr(replay_persistence, "authorize_replay_run_capabilities", allow)
    monkeypatch.setattr(replay_persistence, "_completed_steps_for_resume", oversized_resume)
    monkeypatch.setattr(replay_persistence, "allocate_session_run_generation", forbidden_generation)

    with pytest.raises(RepositoryConflictError, match="run_input_too_large"):
        await _repo_owner_app_runs_infrastructure_replay_postgres.copy_run_as_new_task(
            object(),
            tenant_id="tenant-a",
            user_id="user-a",
            run_id="run-source",
        )


@pytest.mark.asyncio
async def test_validate_replay_skill_manifests_aggregates_root_skill_mcp_pins(monkeypatch):
    async def get_version(_conn, *, skill_id, version):
        return {"version": version, "content_hash": version, "status": "released"}

    monkeypatch.setattr(skill_persistence, "get_skill_version", get_version)
    manifests = [
        {
            "skill_id": "skill-a",
            "version": "hash-a",
            "content_hash": "hash-a",
            "files": [{}],
            "dependency_ids": [],
            "mcp_tool_ids": ["mcp:a"],
        },
        {
            "skill_id": "skill-b",
            "version": "hash-b",
            "content_hash": "hash-b",
            "files": [{}],
            "dependency_ids": [],
            "mcp_tool_ids": ["mcp:b"],
        },
    ]

    assert await _repo_owner_app_skills_infrastructure_postgres.validate_replay_skill_manifests(
        object(),
        skill_id="skill-a",
        pinned_version="hash-a",
        pinned_executor_type="claude-agent-worker",
        skill_manifests=manifests,
        skill_set=[
            {"skill_id": "skill-a", "expected_version": "hash-a"},
            {"skill_id": "skill-b", "expected_version": "hash-b"},
        ],
    ) == ["mcp:a", "mcp:b"]

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_skills_infrastructure_postgres.validate_replay_skill_manifests(
            object(),
            skill_id="skill-a",
            pinned_version="hash-a",
            pinned_executor_type="claude-agent-worker",
            skill_manifests=manifests,
            skill_set=[],
        )

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_skills_infrastructure_postgres.validate_replay_skill_manifests(
            object(),
            skill_id="skill-a",
            pinned_version="hash-a",
            pinned_executor_type="claude-agent-worker",
            skill_manifests=[manifests[0], dict(manifests[0])],
            skill_set=[{"skill_id": "skill-a", "expected_version": "hash-a"}],
        )

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_skills_infrastructure_postgres.validate_replay_skill_manifests(
            object(),
            skill_id="skill-a",
            pinned_version="hash-a",
            pinned_executor_type="claude-agent-worker",
            skill_manifests=manifests,
            skill_set=[{"skill_id": "skill-a", "expected_version": "hash-a"}],
        )

    dependency_manifests = [
        {**manifests[0], "dependency_ids": ["skill-b"]},
        manifests[1],
    ]
    assert await _repo_owner_app_skills_infrastructure_postgres.validate_replay_skill_manifests(
        object(),
        skill_id="skill-a",
        pinned_version="hash-a",
        pinned_executor_type="claude-agent-worker",
        skill_manifests=dependency_manifests,
        skill_set=[{"skill_id": "skill-a", "expected_version": "hash-a"}],
    ) == ["mcp:a"]

    cyclic_manifests = [
        {**manifests[0], "dependency_ids": ["skill-b"]},
        {**manifests[1], "dependency_ids": ["skill-a"]},
    ]
    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryAuthorizationError, match="capability_not_authorized"):
        await _repo_owner_app_skills_infrastructure_postgres.validate_replay_skill_manifests(
            object(),
            skill_id="skill-a",
            pinned_version="hash-a",
            pinned_executor_type="claude-agent-worker",
            skill_manifests=cyclic_manifests,
            skill_set=[{"skill_id": "skill-a", "expected_version": "hash-a"}],
        )
