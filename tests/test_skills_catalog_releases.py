import json
import pytest
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
import app.skills.infrastructure.catalog_postgres as _repo_owner_app_skills_infrastructure_catalog_postgres
import app.skills.infrastructure.file_overlays_postgres as _repo_owner_app_skills_infrastructure_file_overlays_postgres
import app.skills.infrastructure.versions_postgres as _repo_owner_app_skills_infrastructure_versions_postgres
import app.identity.infrastructure.capability_distributions_postgres as distribution_persistence
import app.skills.infrastructure.catalog_postgres as skill_catalog_persistence
import app.skills.infrastructure.versions_postgres as skill_versions_persistence
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError
from tests.support.repository_fixtures import FakeCursor, RecordingConnection, SingleRowCursor


@pytest.mark.asyncio
async def test_list_public_skill_catalog_hides_archived_but_keeps_disabled_distribution(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    def row(skill_id, *, status, metadata_json):
        return {
            "skill_id": skill_id,
            "name": skill_id,
            "version": f"{skill_id}-hash",
            "expected_version": f"{skill_id}-hash",
            "description": skill_id,
            "input_modes": [],
            "lifecycle_status": "active",
            "status": status,
            "visible_to_user": status == "active",
            "department_ids": [],
            "allowed_roles": [],
            "distribution_metadata_json": metadata_json,
            "version_status": "active",
            "source_json": {"kind": "builtin", "files": []},
            "dependency_ids": [],
            "created_by": "admin-a",
            "created_at": None,
            "updated_at": None,
        }

    class Cursor:
        async def fetchall(self):
            return [
                row("archived-skill", status="disabled", metadata_json={"archived_at": "2026-07-15T00:00:00.000Z"}),
                row("disabled-skill", status="disabled", metadata_json={}),
            ]

    class Connection:
        async def execute(self, sql, params):
            return Cursor()

    monkeypatch.setattr(skill_catalog_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    rows = await _repo_owner_app_skills_infrastructure_catalog_postgres.list_public_skill_catalog(
        Connection(),
        tenant_id="tenant-a",
        include_disabled=True,
    )

    assert [row["skill_id"] for row in rows] == ["disabled-skill"]


@pytest.mark.asyncio
async def test_list_public_skill_catalog_projects_public_source_without_internal_dependencies(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(skill_catalog_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": "hash-a",
                    "expected_version": "hash-a",
                    "description": "Review Word documents.",
                    "input_modes": ["docx"],
                    "status": "active",
                    "visible_to_user": True,
                    "source_json": {
                        "kind": "builtin",
                        "tags": ["document"],
                        "files": [{"relative_path": "SKILL.md", "content_base64": "IyBRQQ=="}],
                    },
                    "dependency_ids": ["minimax-docx"],
                    "created_by": "dev-admin",
                    "created_at": None,
                    "updated_at": None,
                    "release_policy_version": "hash-a",
                    "release_policy_previous_version": "hash-old",
                    "release_policy_rollout_percent": 100,
                    "release_policy_previous_version_status": "released",
                    "release_policy_previous_content_hash": "hash-old",
                }
            ]

    class CatalogConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return CatalogCursor()

    conn = CatalogConnection()

    rows = await _repo_owner_app_skills_infrastructure_catalog_postgres.list_public_skill_catalog(
        conn,
        tenant_id="default",
        include_disabled=True,
        rollout_key="current-track-user",
    )

    assert rows[0]["source"]["tags"] == ["document"]
    assert rows[0]["source"]["files"][0]["relative_path"] == "SKILL.md"
    assert rows[0]["dependency_ids"] == ["minimax-docx"]
    assert rows[0]["expected_version"] == "hash-a"
    assert rows[0]["input_modes"] == ["docx"]
    assert "skill_versions.content_hash as expected_version" in conn.sql
    assert "skills.input_modes" in conn.sql
    assert "tenant_capability_distributions.capability_id is not null" in conn.sql
    assert "tenant_workbench_skills" not in conn.sql
    assert "skills.status = 'active'" in conn.sql
    assert conn.params[0:2] == ("default", "default")
    assert "qa-file-reviewer" in conn.params[2]
    assert "minimax-docx" not in conn.params[2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "content_hash"),
    [
        (None, "hash-a"),
        ("", "hash-a"),
        ("hash-a", None),
        ("hash-a", ""),
        ("hash-a", "hash-other"),
    ],
)
async def test_list_public_skill_catalog_hides_non_materializable_current_versions(
    monkeypatch,
    version,
    content_hash,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": version,
                    "expected_version": content_hash,
                    "description": "Review Word documents.",
                    "input_modes": ["docx"],
                    "status": "active",
                    "visible_to_user": True,
                    "version_status": "released",
                    "source_json": {"kind": "builtin", "files": []},
                    "dependency_ids": [],
                    "created_by": "dev-admin",
                    "created_at": None,
                    "updated_at": None,
                }
            ]

    class CatalogConnection:
        async def execute(self, sql, params):
            return CatalogCursor()

    monkeypatch.setattr(skill_catalog_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    rows = await _repo_owner_app_skills_infrastructure_catalog_postgres.list_public_skill_catalog(
        CatalogConnection(),
        tenant_id="default",
        include_disabled=True,
    )

    assert rows == []


@pytest.mark.asyncio
async def test_list_public_skill_catalog_hides_unreleased_selected_versions_by_default(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(skill_catalog_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)
    def catalog_row(skill_id: str, version_status: str) -> dict[str, object]:
        version = f"{skill_id}-version"
        return {
            "skill_id": skill_id,
            "name": skill_id,
            "version": version,
            "expected_version": version,
            "description": f"{skill_id} description",
            "input_modes": ["chat"],
            "status": "active",
            "visible_to_user": True,
            "version_status": version_status,
            "source_json": {"kind": "builtin", "files": []},
            "dependency_ids": [],
            "created_by": "dev-admin",
            "created_at": None,
            "updated_at": None,
        }

    class CatalogCursor:
        async def fetchall(self):
            return [
                catalog_row("general-chat", "active"),
                catalog_row("qa-file-reviewer", "released"),
                catalog_row("retired-skill", "draft"),
                catalog_row("ragflow-knowledge-search", "reviewed"),
                catalog_row("ctd-32s73-stability-template-fill", "disabled"),
                catalog_row("custom-deprecated-skill", "deprecated"),
            ]

    class CatalogConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return CatalogCursor()

    conn = CatalogConnection()

    rows = await _repo_owner_app_skills_infrastructure_catalog_postgres.list_public_skill_catalog(
        conn,
        tenant_id="default",
        include_disabled=False,
    )

    assert [row["skill_id"] for row in rows] == ["general-chat", "qa-file-reviewer"]
    assert "coalesce(skill_versions.status, 'active') as version_status" in conn.sql
    assert "previous_skill_versions.status as release_policy_previous_version_status" in conn.sql


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["draft", "reviewed", "disabled", "deprecated"])
async def test_public_skill_catalog_hides_non_runnable_rollout_selected_previous_version(
    monkeypatch,
    previous_status,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": "hash-new",
                    "expected_version": "hash-new",
                    "description": "New description",
                    "input_modes": ["docx"],
                    "lifecycle_status": "active",
                    "status": "active",
                    "visible_to_user": True,
                    "version_status": "released",
                    "source_json": {"kind": "builtin", "tags": ["new"]},
                    "dependency_ids": ["new-dependency"],
                    "created_by": "admin-new",
                    "created_at": None,
                    "updated_at": None,
                    "release_policy_version": "hash-new",
                    "release_policy_previous_version": "hash-old",
                    "release_policy_previous_content_hash": "hash-old",
                    "release_policy_rollout_percent": 0,
                    "release_policy_previous_version_status": previous_status,
                    "release_policy_previous_description": "Old description",
                    "release_policy_previous_source_json": {"kind": "builtin", "tags": ["old"]},
                    "release_policy_previous_dependency_ids": ["old-dependency"],
                    "release_policy_previous_created_by": "admin-old",
                    "release_policy_previous_created_at": None,
                }
            ]

    class CatalogConnection:
        def __init__(self):
            self.sql = ""

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            return CatalogCursor()

    monkeypatch.setattr(skill_catalog_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    rows = await _repo_owner_app_skills_infrastructure_catalog_postgres.list_public_skill_catalog(
        CatalogConnection(),
        tenant_id="default",
        include_disabled=False,
        rollout_key="previous-track-user",
    )

    assert rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_content_hash", ["hash-old", None, "", "hash-other"])
async def test_public_skill_catalog_projects_only_materializable_rollout_selected_previous_version(
    monkeypatch,
    previous_content_hash,
):
    async def no_backfill(conn, *, tenant_id):
        return None

    class CatalogCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "name": "QA Word Review",
                    "version": "hash-new",
                    "expected_version": "hash-new",
                    "description": "New description",
                    "input_modes": ["docx"],
                    "lifecycle_status": "active",
                    "status": "active",
                    "visible_to_user": True,
                    "version_status": "released",
                    "source_json": {"kind": "builtin", "tags": ["new"]},
                    "dependency_ids": ["new-dependency"],
                    "created_by": "admin-new",
                    "created_at": None,
                    "updated_at": None,
                    "release_policy_version": "hash-new",
                    "release_policy_previous_version": "hash-old",
                    "release_policy_previous_content_hash": previous_content_hash,
                    "release_policy_rollout_percent": 0,
                    "release_policy_previous_version_status": "released",
                    "release_policy_previous_description": "Old description",
                    "release_policy_previous_source_json": {"kind": "builtin", "tags": ["old"]},
                    "release_policy_previous_dependency_ids": ["old-dependency"],
                    "release_policy_previous_created_by": "admin-old",
                    "release_policy_previous_created_at": None,
                }
            ]

    class CatalogConnection:
        async def execute(self, sql, params):
            return CatalogCursor()

    monkeypatch.setattr(skill_catalog_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    rows = await _repo_owner_app_skills_infrastructure_catalog_postgres.list_public_skill_catalog(
        CatalogConnection(),
        tenant_id="default",
        include_disabled=False,
        rollout_key="previous-track-user",
    )

    if previous_content_hash != "hash-old":
        assert rows == []
        return

    assert rows[0]["version"] == "hash-old"
    assert rows[0]["expected_version"] == "hash-old"
    assert rows[0]["input_modes"] == ["docx"]
    assert rows[0]["version_status"] == "released"
    assert rows[0]["description"] == "Old description"
    assert rows[0]["source"]["tags"] == ["old"]
    assert rows[0]["dependency_ids"] == ["old-dependency"]
    assert rows[0]["created_by"] == "admin-old"
    assert not any(key.startswith("release_policy_") for key in rows[0])


@pytest.mark.asyncio
async def test_upsert_skill_version_records_immutable_catalog_version():
    conn = RecordingConnection()

    await _repo_owner_app_skills_infrastructure_versions_postgres.upsert_skill_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-a",
        content_hash="hash-a",
        description="QA review",
        source_json={"kind": "builtin"},
        dependency_ids=["minimax-docx"],
        status="active",
        created_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into skill_versions" in sql
    assert "on conflict (skill_id, version)" in sql
    assert "do nothing" in sql
    assert "returning skill_id" in sql
    assert "do update set" not in sql
    assert "content_hash = excluded.content_hash" not in sql
    assert params[0].startswith("skv_")
    assert params[1:4] == ("qa-file-reviewer", "hash-a", "hash-a")
    assert '"kind": "builtin"' in str(params)
    assert "minimax-docx" in str(params)


@pytest.mark.asyncio
async def test_upsert_skill_version_reports_conflict_when_insert_skipped():
    class ConflictCursor:
        async def fetchone(self):
            return None

    class ConflictConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ConflictCursor()

    conn = ConflictConnection()

    inserted = await _repo_owner_app_skills_infrastructure_versions_postgres.upsert_skill_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-a",
        content_hash="hash-a",
        description="QA review",
        source_json={"kind": "uploaded"},
        dependency_ids=["minimax-docx"],
        status="draft",
        created_by="admin-a",
    )

    assert inserted is False


@pytest.mark.asyncio
async def test_create_skill_catalog_is_insert_only_and_reports_conflict():
    class ConflictCursor:
        async def fetchone(self):
            return None

    class ConflictConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ConflictCursor()

    conn = ConflictConnection()

    with pytest.raises(RepositoryConflictError) as exc_info:
        await _repo_owner_app_skills_infrastructure_versions_postgres.create_skill_catalog(
            conn,
            skill_id="new-research-skill",
            name="New Research Skill",
            version="hash-new",
            description="Summarize research briefs.",
            input_modes=["chat"],
            output_modes=["answer"],
            executor_type="claude-agent-worker",
            status="active",
        )

    sql, params = conn.calls[0]
    assert str(exc_info.value) == "skill_catalog_already_exists"
    assert "insert into skills" in sql
    assert "on conflict (id) do nothing" in sql
    assert "do update" not in sql
    assert "returning id" in sql
    assert params[0:5] == (
        "new-research-skill",
        "New Research Skill",
        "hash-new",
        "Summarize research briefs.",
        '["chat"]',
    )
    assert params[5:8] == ('["answer"]', "claude-agent-worker", "active")


@pytest.mark.asyncio
async def test_update_skill_catalog_version_updates_current_skill_pointer():
    conn = RecordingConnection()

    await _repo_owner_app_skills_infrastructure_versions_postgres.update_skill_catalog_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-current",
        description="Current QA review",
    )

    sql, params = conn.calls[0]
    assert "update skills" in sql
    assert "set version = %s" in sql
    assert "where id = %s" in sql
    assert params == ("hash-current", "Current QA review", "qa-file-reviewer")


@pytest.mark.asyncio
async def test_user_skill_file_overlay_repository_contracts():
    class OverlayCursor:
        def __init__(self, *, row=None, rows=None):
            self.row = row or {
                "skill_id": "qa-file-reviewer",
                "file_path": "SKILL.md",
                "content_base64": "dXBkYXRlZA==",
                "size_bytes": 7,
                "status": "active",
            }
            self.rows = rows or [self.row]

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class OverlayConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            return OverlayCursor()

    conn = OverlayConnection()

    overlays = await _repo_owner_app_skills_infrastructure_file_overlays_postgres.list_user_skill_file_overlays(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_ids=["qa-file-reviewer"],
        include_content=True,
    )
    upserted = await _repo_owner_app_skills_infrastructure_file_overlays_postgres.upsert_user_skill_file(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_id="qa-file-reviewer",
        file_path="SKILL.md",
        content_base64="dXBkYXRlZA==",
        size_bytes=7,
    )
    deleted = await _repo_owner_app_skills_infrastructure_file_overlays_postgres.delete_user_skill_file(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_id="qa-file-reviewer",
        file_path="SKILL.md",
    )

    list_sql, list_params = conn.calls[0]
    assert "from user_skill_files" in list_sql
    assert "content_base64" in list_sql
    assert "skill_id = any(%s)" in list_sql
    assert "status in ('active', 'deleted')" in list_sql
    assert list_params == ("default", "ordinary", ["qa-file-reviewer"])
    assert overlays[0]["file_path"] == "SKILL.md"

    upsert_sql, upsert_params = conn.calls[1]
    assert "insert into user_skill_files" in upsert_sql
    assert "on conflict (tenant_id, user_id, skill_id, file_path)" in upsert_sql
    assert "status = 'active'" in upsert_sql
    assert upsert_params[0].startswith("usf_")
    assert upsert_params[1:7] == (
        "default",
        "ordinary",
        "qa-file-reviewer",
        "SKILL.md",
        "dXBkYXRlZA==",
        7,
    )
    assert upserted["status"] == "active"

    delete_sql, delete_params = conn.calls[2]
    assert "insert into user_skill_files" in delete_sql
    assert "status = 'deleted'" in delete_sql
    assert "content_base64 = ''" in delete_sql
    assert delete_params[0].startswith("usf_")
    assert delete_params[1:5] == ("default", "ordinary", "qa-file-reviewer", "SKILL.md")
    assert deleted["file_path"] == "SKILL.md"


@pytest.mark.asyncio
async def test_user_skill_file_overlay_list_can_omit_content_for_catalog_projection():
    class OverlayCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "file_path": "SKILL.md",
                    "content_base64": "",
                    "size_bytes": 7,
                    "status": "active",
                }
            ]

    class OverlayConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return OverlayCursor()

    conn = OverlayConnection()

    overlays = await _repo_owner_app_skills_infrastructure_file_overlays_postgres.list_user_skill_file_overlays(
        conn,
        tenant_id="default",
        user_id="ordinary",
        skill_ids=["qa-file-reviewer"],
    )

    assert "'' as content_base64" in conn.sql
    assert "content_base64," not in conn.sql
    assert conn.params == ("default", "ordinary", ["qa-file-reviewer"])
    assert overlays[0]["content_base64"] == ""


@pytest.mark.asyncio
async def test_backfill_builtin_skill_version_snapshot_only_updates_incomplete_builtin_rows():
    conn = RecordingConnection()

    await _repo_owner_app_skills_infrastructure_versions_postgres.backfill_builtin_skill_version_snapshot(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-current",
        source_json={
            "kind": "builtin",
            "asset_dir": "qa-file-reviewer",
            "version": "hash-current",
            "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            "dependency_manifests": [
                {
                    "skill_id": "minimax-docx",
                    "version": "hash-dep",
                    "content_hash": "hash-dep",
                    "source": {"kind": "builtin", "asset_dir": "minimax-docx"},
                    "files": [{"relative_path": "SKILL.md", "content_base64": "ZGVw", "size_bytes": 3}],
                }
            ],
        },
        dependency_ids=["minimax-docx"],
        description="Current QA review",
    )

    sql, params = conn.calls[0]
    assert "update skill_versions" in sql
    assert "source_json->>'kind' = 'builtin'" in sql
    assert "not (source_json ? 'files')" in sql
    assert "source_json->'files' is distinct from (%s::jsonb->'files')" in sql
    assert "dependency_ids <> %s::jsonb" in sql
    assert "source_json->'dependency_manifests' is distinct from (%s::jsonb->'dependency_manifests')" in sql
    assert params[3:] == (
        "qa-file-reviewer",
        "hash-current",
        params[0],
        params[1],
        params[0],
    )
    assert '"files"' in params[0]
    assert '"dependency_manifests"' in params[0]
    assert "minimax-docx" in params[1]


@pytest.mark.asyncio
async def test_list_skill_versions_projects_source_and_dependencies():
    class VersionCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-a",
                    "content_hash": "hash-a",
                    "description": "QA review",
                    "source_json": {"kind": "builtin"},
                    "dependency_ids": ["minimax-docx"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                }
            ]

    class VersionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return VersionCursor()

    conn = VersionConnection()

    versions = await _repo_owner_app_skills_infrastructure_versions_postgres.list_skill_versions(conn, skill_id="qa-file-reviewer")

    assert "from skill_versions" in conn.sql
    assert conn.params == ("qa-file-reviewer",)
    assert versions == [
        {
            "skill_id": "qa-file-reviewer",
            "version": "hash-a",
            "content_hash": "hash-a",
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "status": "active",
            "created_by": "admin-a",
            "created_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_get_effective_skill_version_for_policy_returns_uploaded_source():
    class VersionCursor:
        async def fetchone(self):
            return {
                "skill_id": "qa-file-reviewer",
                "version": "hash-uploaded",
                "content_hash": "hash-uploaded",
                "description": "QA review",
                "source_json": {"kind": "uploaded", "files": [{"relative_path": "SKILL.md"}]},
                "dependency_ids": [],
                "status": "active",
                "created_by": "admin-a",
                "created_at": None,
            }

    class VersionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return VersionCursor()

    conn = VersionConnection()

    version = await _repo_owner_app_skills_infrastructure_versions_postgres.get_effective_skill_version_for_policy(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-uploaded",
    )

    assert "from skill_versions" in conn.sql
    assert conn.params == ("qa-file-reviewer", "hash-uploaded")
    assert version["source"]["kind"] == "uploaded"
    assert version["version"] == "hash-uploaded"


@pytest.mark.asyncio
async def test_get_skill_projects_status_for_upload_preflight():
    class SkillCursor:
        async def fetchone(self):
            return {"skill_id": "qa-file-reviewer", "version": "0.1.0", "status": "active"}

    class SkillConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return SkillCursor()

    conn = SkillConnection()

    row = await _repo_owner_app_skills_infrastructure_catalog_postgres.get_skill(conn, skill_id="qa-file-reviewer")

    assert "from skills" in conn.sql
    assert "version" in conn.sql
    assert conn.params == ("qa-file-reviewer",)
    assert row == {"skill_id": "qa-file-reviewer", "version": "0.1.0", "status": "active"}


@pytest.mark.asyncio
async def test_list_skill_ids_returns_all_catalog_ids_for_dependency_policy():
    class SkillCursor:
        async def fetchall(self):
            return [{"id": "qa-file-reviewer"}, {"id": "minimax-docx"}]

    class SkillConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params=()):
            self.sql = " ".join(sql.split())
            self.params = params
            return SkillCursor()

    conn = SkillConnection()

    skill_ids = await _repo_owner_app_skills_infrastructure_catalog_postgres.list_skill_ids(conn)

    assert "from skills" in conn.sql
    assert conn.params == ()
    assert skill_ids == ["qa-file-reviewer", "minimax-docx"]


@pytest.mark.asyncio
async def test_get_skill_release_policy_projects_current_version():
    class ReleaseCursor:
        async def fetchone(self):
            return {
                "skill_id": "qa-file-reviewer",
                "channel": "stable",
                "current_version": "hash-b",
                "previous_version": "hash-a",
                "rollout_percent": 100,
                "status": "active",
                "promoted_by": "dev-admin",
                "promoted_at": None,
            }

    class ReleaseConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ReleaseCursor()

    conn = ReleaseConnection()

    policy = await _repo_owner_app_skills_infrastructure_versions_postgres.get_skill_release_policy(
        conn,
        tenant_id="default",
        skill_id="qa-file-reviewer",
    )

    assert "status = 'active'" in conn.sql
    assert policy == {
        "skill_id": "qa-file-reviewer",
        "channel": "stable",
        "current_version": "hash-b",
        "previous_version": "hash-a",
        "rollout_percent": 100,
        "status": "active",
        "promoted_by": "dev-admin",
        "promoted_at": None,
    }


@pytest.mark.asyncio
async def test_set_skill_release_policy_is_tenant_scoped_and_preserves_previous_version():
    conn = RecordingConnection()

    await _repo_owner_app_skills_infrastructure_versions_postgres.set_skill_release_policy(
        conn,
        tenant_id="default",
        skill_id="qa-file-reviewer",
        version="hash-b",
        previous_version="hash-a",
        promoted_by="dev-admin",
        channel="stable",
        rollout_percent=100,
    )

    sql, params = conn.calls[0]
    assert "insert into skill_release_policies" in sql
    assert "on conflict (tenant_id, skill_id, channel)" in sql
    assert "current_version = excluded.current_version" in sql
    assert params[0].startswith("skr_")
    assert params[1:6] == ("default", "qa-file-reviewer", "stable", "hash-b", "hash-a")
    assert params[6:9] == (100, "active", "dev-admin")


@pytest.mark.asyncio
async def test_diff_skill_versions_reports_manifest_and_dependency_changes():
    class DiffCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class DiffConnection:
        async def execute(self, sql, params):
            assert "from skill_versions" in " ".join(sql.split())
            version = params[1]
            rows = {
                "hash-a": {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-a",
                    "content_hash": "hash-a",
                    "description": "old QA",
                    "source_json": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                    "dependency_ids": ["minimax-docx"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                },
                "hash-b": {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-b",
                    "content_hash": "hash-b",
                    "description": "new QA",
                    "source_json": {"kind": "builtin", "asset_dir": "qa-file-reviewer-v2"},
                    "dependency_ids": ["minimax-docx", "term-checker"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                },
            }
            return DiffCursor(rows.get(version))

    diff = await _repo_owner_app_skills_infrastructure_versions_postgres.diff_skill_versions(
        DiffConnection(),
        skill_id="qa-file-reviewer",
        from_version="hash-a",
        to_version="hash-b",
    )

    assert diff == {
        "skill_id": "qa-file-reviewer",
        "from_version": "hash-a",
        "to_version": "hash-b",
        "content_hash_changed": True,
        "description_changed": True,
        "source_changed": True,
        "dependency_added": ["term-checker"],
        "dependency_removed": [],
    }


@pytest.mark.asyncio
async def test_diff_skill_versions_raises_when_version_missing():
    class MissingCursor:
        async def fetchone(self):
            return None

    class MissingConnection:
        async def execute(self, sql, params):
            return MissingCursor()

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryNotFoundError, match="skill_version_not_found"):
        await _repo_owner_app_skills_infrastructure_versions_postgres.diff_skill_versions(
            MissingConnection(),
            skill_id="qa-file-reviewer",
            from_version="hash-a",
            to_version="hash-b",
        )


@pytest.mark.asyncio
async def test_admin_skill_detail_projects_versions_and_recent_snapshots(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        assert tenant_id == "tenant-a"

    monkeypatch.setattr(skill_versions_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class DetailCursor:
        def __init__(self, *, one=None, many=None):
            self.one = one
            self.many = many or []

        async def fetchone(self):
            return self.one

        async def fetchall(self):
            return self.many

    class SkillDetailConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            if "from skills" in compact:
                assert "tenant_workbench_skills" not in compact
                assert "join tenant_capability_distributions" in compact
                assert "skills.status as lifecycle_status" in compact
                assert params == ("tenant-a", "qa-file-reviewer")
                return DetailCursor(
                    one={
                        "skill_id": "qa-file-reviewer",
                        "name": "qa-file-reviewer",
                        "version": "0.1.0",
                        "description": "QA review",
                        "input_modes": ["docx"],
                        "output_modes": ["reviewed_docx"],
                        "executor_type": "claude_agent",
                        "status": "active",
                        "visible_to_user": True,
                        "distribution_metadata_json": {},
                    }
                )
            if "from skill_versions" in compact:
                assert params == ("qa-file-reviewer",)
                return DetailCursor(
                    many=[
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-a",
                            "content_hash": "hash-a",
                            "description": "QA review",
                            "source_json": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "status": "active",
                            "created_by": "admin-a",
                            "created_at": None,
                        }
                    ]
                )
            if "from skill_release_policies" in compact:
                assert params == ("tenant-a", "qa-file-reviewer", "stable")
                return DetailCursor(
                    one={
                        "skill_id": "qa-file-reviewer",
                        "channel": "stable",
                        "current_version": "hash-a",
                        "previous_version": "0.1.0",
                        "rollout_percent": 100,
                        "status": "active",
                        "promoted_by": "admin-a",
                        "promoted_at": None,
                    }
                )
            if "from run_skill_snapshots" in compact:
                assert params == ("tenant-a", "qa-file-reviewer")
                return DetailCursor(
                    many=[
                        {
                            "run_id": "run-a",
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
                            "used": True,
                            "created_at": None,
                        }
                    ]
                )
            raise AssertionError(compact)

    detail = await _repo_owner_app_skills_infrastructure_versions_postgres.get_admin_skill_detail(
        SkillDetailConnection(),
        tenant_id="tenant-a",
        skill_id="qa-file-reviewer",
    )

    assert detail["skill"]["skill_id"] == "qa-file-reviewer"
    assert detail["versions"][0]["content_hash"] == "hash-a"
    assert detail["versions"][0]["source"] == {"kind": "builtin"}
    assert detail["versions"][0]["dependency_ids"] == ["minimax-docx"]
    assert detail["release_policy"]["current_version"] == "hash-a"
    assert detail["release_policy"]["previous_version"] == "0.1.0"
    assert detail["recent_snapshots"][0]["run_id"] == "run-a"
    assert detail["recent_snapshots"][0]["dependency_ids"] == ["minimax-docx"]
    assert detail["recent_snapshots"][0]["source"] == {
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
    }
    serialized_snapshots = json.dumps(detail["recent_snapshots"], ensure_ascii=False)
    assert "skill_version" not in serialized_snapshots
    assert "content_hash" not in serialized_snapshots
    assert "content_base64" not in serialized_snapshots
    assert "storage_key" not in serialized_snapshots
    assert "hash-a" not in serialized_snapshots
    assert "version" not in detail["recent_snapshots"][0]["source"]
    assert "track" not in serialized_snapshots
    assert "rollout" not in serialized_snapshots


@pytest.mark.asyncio
async def test_admin_skill_detail_hides_archived_distribution(monkeypatch):
    async def no_backfill(_conn, *, tenant_id):
        assert tenant_id == "tenant-a"

    monkeypatch.setattr(skill_versions_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class Cursor:
        async def fetchone(self):
            return {
                "skill_id": "archived-demo",
                "name": "archived-demo",
                "version": "hash-a",
                "description": "Archived demo",
                "input_modes": ["chat"],
                "output_modes": ["answer"],
                "executor_type": "claude-agent-worker",
                "lifecycle_status": "active",
                "status": "disabled",
                "visible_to_user": False,
                "distribution_metadata_json": {
                    "archived_at": "2026-08-10T00:00:00.000Z",
                    "archived_by": "admin-a",
                },
            }

    class Connection:
        def __init__(self):
            self.calls = 0

        async def execute(self, sql, params):
            self.calls += 1
            assert self.calls == 1
            compact = " ".join(sql.split())
            assert "distribution_metadata_json" in compact
            assert params == ("tenant-a", "archived-demo")
            return Cursor()

    detail = await _repo_owner_app_skills_infrastructure_versions_postgres.get_admin_skill_detail(
        Connection(),
        tenant_id="tenant-a",
        skill_id="archived-demo",
    )

    assert detail is None


@pytest.mark.asyncio
async def test_list_admin_skill_summaries_excludes_package_source(monkeypatch):
    async def no_backfill(_conn, *, tenant_id):
        assert tenant_id == "tenant-a"

    monkeypatch.setattr(skill_versions_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class SummaryCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "native-demo",
                    "name": "native-demo",
                    "description": "Native demo",
                    "lifecycle_status": "active",
                    "distribution_status": "disabled",
                    "visible_to_user": False,
                    "distribution_metadata_json": {},
                    "latest_version": "hash-a",
                    "latest_version_status": "draft",
                    "current_version": None,
                    "rollout_percent": None,
                },
                {
                    "skill_id": "archived-demo",
                    "name": "archived-demo",
                    "description": "Archived demo",
                    "lifecycle_status": "active",
                    "distribution_status": "disabled",
                    "visible_to_user": False,
                    "distribution_metadata_json": {
                        "archived_at": "2026-08-10T00:00:00.000Z",
                        "archived_by": "admin-a",
                    },
                    "latest_version": "hash-archived",
                    "latest_version_status": "released",
                    "current_version": "hash-archived",
                    "rollout_percent": 100,
                }
            ]

    class SummaryConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            assert params == ("tenant-a", "tenant-a")
            assert "source_json" not in compact
            assert "storage_key" not in compact
            assert "distribution_metadata_json" in compact
            assert "left join lateral" in compact
            return SummaryCursor()

    rows = await _repo_owner_app_skills_infrastructure_versions_postgres.list_admin_skill_summaries(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert rows == [
        {
            "skill_id": "native-demo",
            "name": "native-demo",
            "description": "Native demo",
            "lifecycle_status": "active",
            "distribution_status": "disabled",
            "visible_to_user": False,
            "latest_version": "hash-a",
            "latest_version_status": "draft",
            "current_version": None,
            "rollout_percent": None,
        }
    ]


@pytest.mark.asyncio
async def test_set_workbench_skill_status_rejects_internal_dependency_skill():
    conn = RecordingConnection()

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryNotFoundError, match="workbench_skill_not_found"):
        await _repo_owner_app_skills_infrastructure_catalog_postgres.set_workbench_skill_status(
            conn,
            tenant_id="default",
            skill_id="minimax-docx",
            status="active",
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_set_uploaded_workbench_skill_status_creates_authoritative_distribution(monkeypatch):
    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class UploadedSkillConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("insert into tenant_capability_distributions"):
                return SingleRowCursor(
                    {
                        "id": "capdist-new",
                        "tenant_id": "default",
                        "capability_kind": "skill",
                        "capability_id": "new-research-skill",
                        "status": "active",
                        "visible_to_user": True,
                        "scope_mode": "allowlist",
                        "department_ids": [],
                        "allowed_roles": [],
                        "metadata_json": {},
                    }
                )
            if compact.startswith("select skills.id as skill_id"):
                return SingleRowCursor(
                    {
                        "skill_id": "new-research-skill",
                        "name": "new-research-skill",
                        "version": "hash-new",
                        "description": "Summarize research briefs.",
                        "input_modes": ["chat"],
                        "output_modes": ["answer"],
                        "executor_type": "claude-agent-worker",
                        "status": "active",
                        "visible_to_user": True,
                    }
                )
            return FakeCursor()

    conn = UploadedSkillConnection()

    row = await _repo_owner_app_skills_infrastructure_catalog_postgres.set_uploaded_workbench_skill_status(
        conn,
        tenant_id="default",
        skill_id="new-research-skill",
        status="active",
    )

    assert row["skill_id"] == "new-research-skill"
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert "select metadata_json" in conn.calls[1][0]
    assert "insert into tenant_capability_distributions" in conn.calls[2][0]
    assert "tenant_workbench_skills" not in " ".join(sql for sql, _ in conn.calls)
    assert conn.calls[2][1][1:5] == ("default", "skill", "new-research-skill", "active")
    assert conn.calls[3][1] == ("default", "new-research-skill")


@pytest.mark.asyncio
async def test_set_public_skill_enabled_updates_existing_authoritative_distribution(monkeypatch):
    async def existing_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return {
            "tenant_id": tenant_id,
            "capability_kind": capability_kind,
            "capability_id": capability_id,
            "status": "disabled",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
            "metadata_json": {"source": "shared"},
        }

    async def no_backfill(conn, *, tenant_id):
        return None

    monkeypatch.setattr(skill_catalog_persistence, "get_capability_distribution_row", existing_distribution)
    monkeypatch.setattr(distribution_persistence, "ensure_tenant_capability_distribution_backfill", no_backfill)

    class UploadedSkillConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            if compact.startswith("insert into tenant_capability_distributions"):
                return SingleRowCursor(
                    {
                        "id": "capdist-existing",
                        "tenant_id": "default",
                        "capability_kind": "skill",
                        "capability_id": "new-research-skill",
                        "status": "active",
                        "visible_to_user": True,
                        "scope_mode": "allowlist",
                        "department_ids": ["qa"],
                        "allowed_roles": ["qa_operator"],
                        "metadata_json": {"source": "shared"},
                    }
                )
            if compact.startswith("select skills.id as skill_id"):
                return SingleRowCursor(
                    {
                        "skill_id": "new-research-skill",
                        "name": "new-research-skill",
                        "version": "hash-new",
                        "description": "Summarize research briefs.",
                        "input_modes": ["chat"],
                        "output_modes": ["answer"],
                        "executor_type": "claude-agent-worker",
                        "status": "active",
                        "visible_to_user": True,
                    }
                )
            return FakeCursor()

    conn = UploadedSkillConnection()

    row = await _repo_owner_app_skills_infrastructure_catalog_postgres.set_public_skill_enabled(
        conn,
        tenant_id="default",
        skill_id="new-research-skill",
        status="active",
    )

    assert row["skill_id"] == "new-research-skill"
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert "select metadata_json" in conn.calls[1][0]
    assert "insert into tenant_capability_distributions" in conn.calls[2][0]
    assert "tenant_workbench_skills" not in " ".join(sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_set_public_skill_enabled_rejects_non_public_skill_without_distribution(monkeypatch):
    async def missing_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return None

    monkeypatch.setattr(skill_catalog_persistence, "get_capability_distribution_row", missing_distribution)

    class MissingUploadedSkillConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            self.calls.append((compact, params))
            return SingleRowCursor(None)

    conn = MissingUploadedSkillConnection()

    with pytest.raises(RepositoryNotFoundError, match="workbench_skill_not_found"):
        await _repo_owner_app_skills_infrastructure_catalog_postgres.set_public_skill_enabled(
            conn,
            tenant_id="default",
            skill_id="minimax-docx",
            status="active",
        )

    assert conn.calls == []
