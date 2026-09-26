import json
import pytest
import app.platform.postgres.limits as _repo_owner_app_platform_postgres_limits
import app.runs.infrastructure.replay_postgres as _repo_owner_app_runs_infrastructure_replay_postgres
import app.skills.infrastructure.postgres as _repo_owner_app_skills_infrastructure_postgres
import app.skills.infrastructure.run_snapshots_postgres as _repo_owner_app_skills_infrastructure_run_snapshots_postgres
import app.skills.pinning as _repo_owner_app_skills_pinning
from app.platform.postgres.errors import RepositoryConflictError
from tests.support.repository_fixtures import RecordingConnection


def test_run_skill_snapshot_source_recomputes_file_and_release_identity():
    manifest = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded", "storage_key": "private/package.zip"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
        "mcp_tool_ids": [],
        "snapshot_governance": {"selected_files": [{"sha256": "caller-controlled"}]},
    }

    locked = _repo_owner_app_skills_infrastructure_postgres.run_skill_snapshot_source_json(
        manifest,
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )
    changed_file = _repo_owner_app_skills_infrastructure_postgres.run_skill_snapshot_source_json(
        {
            **manifest,
            "files": [{"relative_path": "SKILL.md", "content_base64": "ZHJpZnQ=", "size_bytes": 5}],
        },
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )
    changed_release = _repo_owner_app_skills_infrastructure_postgres.run_skill_snapshot_source_json(
        manifest,
        release_decision={"selected_version": "hash-v1", "selected_track": "previous"},
    )

    assert locked["snapshot_governance"]["selected_files"][0]["sha256"] != "caller-controlled"
    assert locked != changed_file
    assert locked["release_decision_sha256"] != changed_release["release_decision_sha256"]
    assert "files" not in locked
    assert "storage_key" not in locked


@pytest.mark.asyncio
async def test_upsert_run_skill_snapshot_is_tenant_and_run_scoped():
    conn = RecordingConnection()

    await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.upsert_run_skill_snapshot(
        conn,
        tenant_id="default",
        run_id="run-a",
        skill_id="qa-file-reviewer",
        skill_version="hash-a",
        content_hash="hash-a",
        source_json={"kind": "builtin"},
        dependency_ids=["minimax-docx"],
        allowed=True,
        staged=True,
        used=True,
        used_skills_source="executor_hook",
        inferred_used=False,
    )

    sql, params = conn.calls[0]
    assert "insert into run_skill_snapshots" in sql
    assert "on conflict (tenant_id, run_id, skill_id)" in sql
    assert params[0].startswith("rss_")
    assert params[1:5] == ("default", "run-a", "qa-file-reviewer", "hash-a")
    assert any('"kind": "builtin"' in str(item) for item in params)
    assert any("minimax-docx" in str(item) for item in params)
    assert "used_skills_source" in sql
    assert "inferred_used" in sql
    assert "executor_hook" in params
    assert False in params


@pytest.mark.asyncio
async def test_upsert_run_skill_snapshot_preserves_immutable_provenance_identity():
    conn = RecordingConnection()

    await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.upsert_run_skill_snapshot(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        skill_id="department-review",
        skill_version="hash-v1",
        content_hash="hash-v1",
        source_json={"kind": "uploaded", "snapshot_governance": {"schema_version": "v1"}},
        dependency_ids=["dependency-a"],
        allowed=True,
        staged=True,
        used=False,
    )

    sql, _params = conn.calls[0]
    update_clause = sql.split("do update set", 1)[1].split("where", 1)[0]
    assert "skill_version" not in update_clause
    assert "content_hash" not in update_clause
    assert "source_json" not in update_clause
    assert "dependency_ids" not in update_clause
    assert "run_skill_snapshots.skill_version = excluded.skill_version" in sql
    assert "run_skill_snapshots.content_hash = excluded.content_hash" in sql
    assert "run_skill_snapshots.source_json = excluded.source_json" in sql
    assert "run_skill_snapshots.dependency_ids = excluded.dependency_ids" in sql
    assert "returning id" in sql


@pytest.mark.asyncio
async def test_upsert_run_skill_snapshot_fails_closed_on_immutable_identity_mismatch():
    class ConflictCursor:
        async def fetchone(self):
            return None

    class ConflictConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ConflictCursor()

    with pytest.raises(RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
        await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.upsert_run_skill_snapshot(
            ConflictConnection(),
            tenant_id="tenant-a",
            run_id="run-a",
            skill_id="department-review",
            skill_version="hash-v2",
            content_hash="hash-v2",
            source_json={"kind": "uploaded"},
            dependency_ids=[],
            allowed=True,
            staged=True,
            used=False,
        )


@pytest.mark.asyncio
async def test_insert_run_skill_snapshots_at_creation_is_insert_only_and_exact():
    conn = RecordingConnection()
    manifests = [
        {
            "skill_id": "department-review",
            "version": "hash-v1",
            "content_hash": "hash-v1",
            "source": {"kind": "uploaded", "storage_key": "must-not-persist"},
            "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            "dependency_ids": ["dependency-a"],
            "mcp_tool_ids": [],
            "snapshot_governance": {
                "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                "selected_files": [{"relative_path": "SKILL.md", "size_bytes": 5, "sha256": "hash-file"}],
            },
            "allowed": True,
            "staged": False,
            "used": False,
        }
    ]

    await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.insert_run_skill_snapshots_at_creation(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        skill_manifests=manifests,
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )

    sql, params = conn.calls[0]
    assert "insert into run_skill_snapshots" in sql
    assert "on conflict (tenant_id, run_id, skill_id) do nothing" in sql
    assert "returning id" in sql
    assert params[1:6] == ("tenant-a", "run-a", "department-review", "hash-v1", "hash-v1")
    serialized_params = str(params)
    assert "dependency-a" in serialized_params
    assert "snapshot_governance" in serialized_params
    assert "content_base64" not in serialized_params
    assert "storage_key" not in serialized_params
    materialization_sql, materialization_params = conn.calls[1]
    assert "insert into run_skill_materializations" in materialization_sql
    assert materialization_params[:3] == ("tenant-a", "run-a", "department-review")
    assert len(materialization_params[3]) == 64
    assert "content_base64" in materialization_params[4]


@pytest.mark.asyncio
async def test_insert_run_skill_snapshots_allows_dependency_manifest_without_execution_mcp_pin():
    conn = RecordingConnection()
    primary = {
        "skill_id": "department-review",
        "version": "hash-v1",
        "content_hash": "hash-v1",
        "source": {"kind": "uploaded"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": ["document-helper"],
        "mcp_tool_ids": [],
    }
    dependency = {
        "skill_id": "document-helper",
        "version": "hash-helper",
        "content_hash": "hash-helper",
        "source": {"kind": "builtin", "asset_dir": "document-helper"},
        "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        "dependency_ids": [],
    }

    await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.insert_run_skill_snapshots_at_creation(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        skill_manifests=[primary, dependency],
        release_decision={"selected_version": "hash-v1", "selected_track": "current"},
    )

    assert len(conn.calls) == 4
    dependency_source = json.loads(conn.calls[2][1][6])
    assert dependency_source["mcp_tool_ids"] == []


@pytest.mark.asyncio
async def test_insert_run_skill_snapshots_preserves_each_root_release_decision():
    conn = RecordingConnection()
    manifests = [
        {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "source": {"kind": "builtin"},
            "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            "dependency_ids": [],
            "mcp_tool_ids": [],
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "selected_version": version,
                "selected_track": track,
            },
        }
        for skill_id, version, track in (
            ("skill-a", "hash-a", "current"),
            ("skill-b", "hash-b", "previous"),
        )
    ]

    await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.insert_run_skill_snapshots_at_creation(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        skill_manifests=manifests,
        release_decision=manifests[0]["release_decision"],
    )

    first_source = json.loads(conn.calls[0][1][6])
    second_source = json.loads(conn.calls[2][1][6])
    assert first_source["release_decision_sha256"] != second_source["release_decision_sha256"]


@pytest.mark.asyncio
async def test_insert_run_skill_snapshots_at_creation_rejects_non_materializable_identity():
    with pytest.raises(RepositoryConflictError, match="run_skill_snapshot_identity_mismatch"):
        await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.insert_run_skill_snapshots_at_creation(
            RecordingConnection(),
            tenant_id="tenant-a",
            run_id="run-a",
            skill_manifests=[
                {
                    "skill_id": "department-review",
                    "version": "hash-v1",
                    "content_hash": "different-hash",
                    "source": {"kind": "uploaded"},
                    "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                    "dependency_ids": [],
                    "mcp_tool_ids": [],
                }
            ],
            release_decision={"selected_version": "hash-v1", "selected_track": "current"},
        )


@pytest.mark.asyncio
async def test_materialize_run_skill_manifests_orders_by_reference_and_rejects_drift(monkeypatch):
    manifests = [
        {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "source": {"kind": "builtin"},
            "files": [
                {"relative_path": "SKILL.md", "content_base64": encoded, "size_bytes": 5}
            ],
            "dependency_ids": [],
        }
        for skill_id, version, encoded in (
            ("primary", "hash-primary", "c2tpbGw="),
            ("dependency", "hash-dependency", "aGVscGU="),
        )
    ]
    refs = _repo_owner_app_skills_infrastructure_run_snapshots_postgres.skill_manifest_refs(manifests)
    stored_rows = [
        {
            "skill_id": item["skill_id"],
            "materialization_sha256": _repo_owner_app_skills_pinning.skill_manifest_materialization_sha256(item),
            "manifest_json": item,
        }
        for item in reversed(manifests)
    ]

    class Cursor:
        async def fetchall(self):
            return stored_rows

    class Connection:
        async def execute(self, sql, params):
            assert "from run_skill_materializations" in sql
            assert params == ("tenant-a", "run-a")
            return Cursor()

    from app.skills import pinning

    hash_manifest = pinning.skill_manifest_materialization_sha256
    hash_count = 0

    def counted(manifest):
        nonlocal hash_count
        hash_count += 1
        return hash_manifest(manifest)

    monkeypatch.setattr(pinning, "skill_manifest_materialization_sha256", counted)
    loaded = await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.materialize_run_skill_manifests(
        Connection(),
        tenant_id="tenant-a",
        run_id="run-a",
        skill_manifest_refs=refs,
    )

    assert [item["skill_id"] for item in loaded] == ["primary", "dependency"]
    assert hash_count == len(manifests)
    with pytest.raises(
        RepositoryConflictError,
        match="run_skill_materialization_identity_mismatch",
    ):
        await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.materialize_run_skill_manifests(
            Connection(),
            tenant_id="tenant-a",
            run_id="run-a",
            skill_manifest_refs=[{**refs[0], "materialization_sha256": "0" * 64}, refs[1]],
        )
    for invalid in (
        [manifests[0]],
        [refs[0], manifests[1]],
        [refs[0], "unexpected"],
        [refs[0], None],
        "not-a-list",
        None,
    ):
        with pytest.raises(
            RepositoryConflictError,
            match="run_skill_materialization_identity_mismatch",
        ):
            await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.materialize_run_skill_manifests(
                Connection(),
                tenant_id="tenant-a",
                run_id="run-a",
                skill_manifest_refs=invalid,
            )


def test_skill_manifest_transport_is_always_reference_only():
    manifest = {
        "skill_id": "qa-file-reviewer",
        "version": "hash-primary",
        "content_hash": "hash-primary",
        "source": {"kind": "builtin"},
        "files": [
            {
                "relative_path": "SKILL.md",
                "content_base64": "c2tpbGw=",
                "size_bytes": 5,
            }
        ],
        "dependency_ids": [],
    }

    references = _repo_owner_app_skills_infrastructure_run_snapshots_postgres.skill_manifest_refs([manifest])

    assert references == _repo_owner_app_skills_infrastructure_run_snapshots_postgres.skill_manifest_refs([manifest])
    assert "files" not in references[0]
    assert "content_base64" not in json.dumps(references)


@pytest.mark.asyncio
async def test_list_run_skill_snapshots_projects_persisted_telemetry():
    class SnapshotCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "skill_version": "hash-a",
                    "content_hash": "hash-a",
                    "source_json": {
                        "kind": "builtin",
                        "version": "hash-a",
                        "snapshot_governance": {
                            "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                            "snapshot_source": "platform_release_lock",
                            "release_lock": {
                                "mode": "manifest_pin",
                                "release_decision": {"selected_version": "hash-a"},
                                "selected_version": "hash-a",
                                "track": "manifest_pin",
                                "rollout": 100,
                            },
                            "manifest": {
                                "digest": "hash-a",
                                "source_kind": "builtin",
                                "selected_file_count": 1,
                                "content_hash": "hash-a",
                            },
                            "selected_files": [
                                {
                                    "relative_path": "SKILL.md",
                                    "size_bytes": 5,
                                    "sha256": "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
                                    "content_base64": "c2tpbGw=",
                                }
                            ],
                            "dependency_evidence": {
                                "status": "review_required",
                                "ref": "skill_dependency_policy",
                                "dependency_count": 1,
                            },
                            "does_not_close_b4_or_deployed_runtime_acceptance": True,
                            "storage_key": "tenants/default/private/package.zip",
                        },
                    },
                    "dependency_ids": ["minimax-docx"],
                    "allowed": True,
                    "staged": True,
                    "used": False,
                    "used_skills_source": "inferred",
                    "inferred_used": True,
                    "created_at": None,
                }
            ]

    class SnapshotConnection:
        async def execute(self, sql, params):
            assert "used_skills_source" in sql
            assert "inferred_used" in sql
            assert params == ("default", "run-a")
            return SnapshotCursor()

    snapshots = await _repo_owner_app_skills_infrastructure_run_snapshots_postgres.list_run_skill_snapshots(
        SnapshotConnection(),
        tenant_id="default",
        run_id="run-a",
    )

    assert snapshots == [
        {
            "skill_id": "qa-file-reviewer",
            "skill_version": "hash-a",
            "content_hash": "hash-a",
            "source": {
                "kind": "builtin",
                "snapshot_governance": {
                    "schema_version": "ai-platform.skill-pinned-snapshot-governance.v1",
                    "snapshot_source": "platform_release_lock",
                    "release_lock": {"mode": "manifest_pin"},
                    "manifest": {
                        "source_kind": "builtin",
                        "selected_file_count": 1,
                    },
                    "selected_files": [
                        {
                            "relative_path": "SKILL.md",
                            "size_bytes": 5,
                            "sha256": "9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
                        }
                    ],
                    "dependency_evidence": {
                        "status": "review_required",
                        "ref": "skill_dependency_policy",
                        "dependency_count": 1,
                    },
                    "does_not_close_b4_or_deployed_runtime_acceptance": True,
                },
            },
            "dependency_ids": ["minimax-docx"],
            "allowed": True,
            "staged": True,
            "used": False,
            "created_at": None,
            "usage": {
                "used_skills_source": "inferred",
                "inferred_used": True,
                "inferred_used_skills": ["qa-file-reviewer"],
            },
        }
    ]
    serialized = json.dumps(snapshots, ensure_ascii=False)
    assert snapshots[0]["skill_version"] == "hash-a"
    assert snapshots[0]["content_hash"] == "hash-a"
    assert "content_base64" not in serialized
    assert "storage_key" not in serialized
    assert "hash-a" not in json.dumps(snapshots[0]["source"], ensure_ascii=False)
    assert "version" not in snapshots[0]["source"]
    assert "track" not in serialized
    assert "rollout" not in serialized


@pytest.mark.asyncio
async def test_update_run_input_execution_snapshot_atomically_replaces_canonical_fields():
    conn = RecordingConnection()
    execution_snapshot = _repo_owner_app_runs_infrastructure_replay_postgres.copied_run_execution_snapshot(
        {
            "tenant_id": "must-not-project",
            "file_ids": ["file-a"],
            "input": {"message": "review"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-current",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-current",
                "selected_track": "manifest_pin",
            },
            "skill_manifests": [
                {
                    "skill_id": "qa-file-reviewer",
                    "content_hash": "hash-current",
                    "source": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                }
            ],
            "context_snapshot_id": "ctx-current",
            "context_snapshot": {"context_snapshot_id": "ctx-current", "source": "copy_run"},
            "model_id": "model-catalog-a",
            "model_value": "provider-model-a",
            "schema_version": "ai-platform.run-payload.v1",
        }
    )

    await _repo_owner_app_runs_infrastructure_replay_postgres.update_run_input_execution_snapshot(
        conn,
        tenant_id="default",
        run_id="run-a",
        execution_snapshot=execution_snapshot,
    )

    sql, params = conn.calls[0]
    assert "select id, input_json" in sql
    assert sql.endswith("for update")
    assert "tenant_id = %s and id = %s" in sql
    assert params == (
        "default",
        "run-a",
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
    )
    update_sql, update_params = conn.calls[1]
    assert update_sql.startswith("update runs set input_json = %s::jsonb")
    assert update_params == (
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
        "default",
        "run-a",
    )


@pytest.mark.asyncio
async def test_update_run_input_execution_snapshot_explicitly_replaces_null_and_empty_values():
    conn = RecordingConnection()
    execution_snapshot = _repo_owner_app_runs_infrastructure_replay_postgres.copied_run_execution_snapshot(
        {
            "input": {},
            "executor_type": "claude-agent-worker",
            "skill_version": None,
            "release_decision": {},
            "skill_manifests": [],
            "context_snapshot_id": None,
            "context_snapshot": {},
            "model_id": None,
            "model_value": None,
        }
    )

    await _repo_owner_app_runs_infrastructure_replay_postgres.update_run_input_execution_snapshot(
        conn,
        tenant_id="tenant-a",
        run_id="run-empty",
        execution_snapshot=execution_snapshot,
    )

    assert len(conn.calls) == 2
    _, params = conn.calls[0]
    assert params == (
        "tenant-a",
        "run-empty",
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
        _repo_owner_app_platform_postgres_limits.compact_json_dumps(execution_snapshot),
    )


def test_copied_run_execution_snapshot_audits_all_queue_non_identity_fields():
    snapshot = _repo_owner_app_runs_infrastructure_replay_postgres.copied_run_execution_snapshot(
        {
            "tenant_id": "must-not-project",
            "run_id": "must-not-project",
            "file_ids": ["file-a"],
            "input": {"message": "copy"},
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-a",
            "release_decision": {"selected_version": "hash-a"},
            "skill_manifests": [{"skill_id": "general-chat", "content_hash": "hash-a"}],
            "context_snapshot_id": "ctx-a",
            "context_snapshot": {"context_snapshot_id": "ctx-a"},
            "model_id": "model-catalog-a",
            "model_value": "provider-model-a",
            "schema_version": "ai-platform.run-payload.v1",
            "unrelated": "preserve-outside-projection",
        }
    )

    assert snapshot == {
        "file_ids": ["file-a"],
        "input": {"message": "copy"},
        "executor_type": "claude-agent-worker",
        "skill_version": "hash-a",
        "release_decision": {"selected_version": "hash-a"},
        "skill_manifests": [{"skill_id": "general-chat", "content_hash": "hash-a"}],
        "context_snapshot_id": "ctx-a",
        "context_snapshot": {"context_snapshot_id": "ctx-a"},
        "model_id": "model-catalog-a",
        "model_value": "provider-model-a",
        "schema_version": "ai-platform.run-payload.v1",
        "execution_kind": "skill",
    }


@pytest.mark.parametrize(
    "invalid_manifests",
    ([{"skill_id": "general-chat"}, "unexpected"], [{"skill_id": "general-chat"}, None], "not-a-list", None),
)
def test_copied_run_execution_snapshot_preserves_invalid_manifest_transport_for_strict_validation(
    invalid_manifests,
):
    snapshot = _repo_owner_app_runs_infrastructure_replay_postgres.copied_run_execution_snapshot(
        {"skill_manifests": invalid_manifests}
    )

    assert snapshot["skill_manifests"] == invalid_manifests
