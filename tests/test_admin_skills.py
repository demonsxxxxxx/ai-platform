from contextlib import asynccontextmanager
from dataclasses import dataclass
import io
from pathlib import Path
import zipfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import AuthPrincipal
from app.main import create_app
from app.models import AdminSkillDetailResponse
from app.repositories import RepositoryConflictError
from app.routes.admin_skills import admin_upload_skill_package
from app.skills import dependencies as skill_dependencies
from app.skills.dependencies import SkillDependencyPolicyError, skill_dependency_ids, skill_dependency_policy
from app.settings import Settings
from app.storage import StoredObject


def admin_headers():
    return {
        "X-AI-User-ID": "dev-admin",
        "X-AI-Roles": "admin",
        "X-AI-Tenant-ID": "default",
    }


def user_headers():
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
    }


def skill_admin_headers():
    return {
        "X-AI-User-ID": "skill-admin",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Permissions": "skill:admin",
    }


def skill_package_zip(*, name: str = "qa-file-reviewer", description: str = "Review Word documents.") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        )
        archive.writestr("references/guide.md", "review guide")
    return buffer.getvalue()


def snapshot_files(content_base64: str = "c2tpbGw=", size_bytes: int = 5):
    return [{"relative_path": "SKILL.md", "content_base64": content_base64, "size_bytes": size_bytes}]


def minimax_dependency_manifest(version: str = "hash-minimax"):
    return {
        "skill_id": "minimax-docx",
        "version": version,
        "content_hash": version,
        "description": "DOCX parser",
        "source": {"kind": "builtin", "asset_dir": "minimax-docx", "version": version},
        "files": snapshot_files("ZGVw", 3),
        "dependency_ids": [],
    }


def builtin_qa_source(version: str):
    return {
        "kind": "builtin",
        "asset_dir": "qa-file-reviewer",
        "version": version,
        "files": snapshot_files(),
        "dependency_manifests": [minimax_dependency_manifest()],
    }


def materializable_builtin_qa_version(version: str, *, description: str = "QA review"):
    return {
        "skill_id": "qa-file-reviewer",
        "version": version,
        "content_hash": version,
        "description": description,
        "source": builtin_qa_source(version),
        "dependency_ids": ["minimax-docx"],
        "status": "active",
        "created_by": "dev-admin",
        "created_at": None,
    }


def uploaded_qa_source(version: str):
    return {
        "kind": "uploaded",
        "storage_key": f"tenants/default/skills/qa-file-reviewer/versions/{version}/package.zip",
        "files": snapshot_files(),
        "dependency_manifests": [minimax_dependency_manifest()],
    }


def materializable_uploaded_qa_version(version: str, *, description: str = "Uploaded QA review"):
    return {
        "skill_id": "qa-file-reviewer",
        "version": version,
        "content_hash": version,
        "description": description,
        "source": uploaded_qa_source(version),
        "dependency_ids": ["minimax-docx"],
        "status": "active",
        "created_by": "dev-admin",
        "created_at": None,
    }


def reviewed_skill_version_release(version):
    return {
        "schema_version": "ai-platform.skill-version-release-review.v1",
        "status": "passed",
        "blockers": [],
        "package_evidence": {"sbom_files": ["sbom.json"]},
        "release_review": {
            "sbom_reviewed": True,
            "license_policy_reviewed": True,
            "vulnerability_reviewed": True,
        },
    }


def blocked_skill_version_release(version):
    return {
        "status": "blocked",
        "blockers": ["dependency_license_policy_review_not_verified"],
    }


async def fake_update_skill_version_status(conn, **kwargs):
    return {
        **materializable_uploaded_qa_version(kwargs["version"]),
        "status": kwargs["status"],
    }


def test_admin_skill_detail_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/skills/qa-file-reviewer", headers=user_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_skill_detail_returns_skill_versions_and_snapshots(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_detail(conn, *, tenant_id, skill_id):
        assert isinstance(conn, FakeConnection)
        assert tenant_id == "default"
        assert skill_id == "qa-file-reviewer"
        return {
            "skill": {"skill_id": "qa-file-reviewer", "name": "QA File Reviewer"},
            "release_policy": {
                "skill_id": "qa-file-reviewer",
                "channel": "stable",
                "current_version": "hash-a",
                "previous_version": "0.1.0",
                "rollout_percent": 100,
                "status": "active",
                "promoted_by": "dev-admin",
                "promoted_at": None,
            },
            "versions": [
                {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-a",
                    "content_hash": "hash-a",
                    "description": "QA review",
                    "source": {"kind": "builtin"},
                    "dependency_ids": ["minimax-docx"],
                    "status": "active",
                    "created_by": "dev-admin",
                    "created_at": None,
                }
            ],
            "recent_snapshots": [{"run_id": "run-a", "skill_id": "qa-file-reviewer"}],
        }

    async def fake_list_skill_ids(conn):
        assert isinstance(conn, FakeConnection)
        return ["baoyu-translate", "minimax-docx", "qa-file-reviewer"]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_admin_skill_detail", fake_detail)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/skills/qa-file-reviewer", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["skill"]["skill_id"] == "qa-file-reviewer"
    assert body["release_policy"]["current_version"] == "hash-a"
    assert body["release_policy"]["previous_version"] == "0.1.0"
    assert body["versions"][0]["content_hash"] == "hash-a"
    assert body["versions"][0]["dependency_ids"] == ["minimax-docx"]
    assert body["recent_snapshots"][0]["run_id"] == "run-a"
    assert body["dependency_policy"] == {
        "skill_id": "qa-file-reviewer",
        "public": True,
        "internal_dependency": False,
        "dependency_ids": ["minimax-docx"],
        "dependency_details": [
            {
                "skill_id": "minimax-docx",
                "status": "allowed",
                "reason": "declared_internal_dependency",
                "public": False,
                "internal_dependency": True,
                "available": True,
            }
        ],
    }


def test_admin_skill_detail_response_rejects_extra_dependency_policy_fields():
    payload = {
        "skill": {"skill_id": "qa-file-reviewer", "name": "QA File Reviewer"},
        "dependency_policy": {
            "skill_id": "qa-file-reviewer",
            "public": True,
            "internal_dependency": False,
            "dependency_ids": ["minimax-docx"],
            "unexpected_internal": "storage-key",
            "dependency_details": [
                {
                    "skill_id": "minimax-docx",
                    "status": "allowed",
                    "reason": "declared_internal_dependency",
                    "public": False,
                    "internal_dependency": True,
                    "available": True,
                    "unexpected_internal": "package.zip",
                }
            ],
        },
    }

    with pytest.raises(ValidationError, match="unexpected_internal"):
        AdminSkillDetailResponse.model_validate(payload)


def test_dependency_policy_reports_missing_internal_dependency_for_admin_audit():
    policy = skill_dependency_policy("qa-file-reviewer", {"qa-file-reviewer"})

    assert policy == {
        "skill_id": "qa-file-reviewer",
        "public": True,
        "internal_dependency": False,
        "dependency_ids": ["minimax-docx"],
        "dependency_details": [
            {
                "skill_id": "minimax-docx",
                "status": "blocked",
                "reason": "skill_dependency_missing",
                "public": False,
                "internal_dependency": True,
                "available": False,
            }
        ],
    }
    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_missing: minimax-docx"):
        skill_dependency_ids("qa-file-reviewer", {"qa-file-reviewer"})


def test_dependency_policy_allows_ctd_stability_reference_dependency():
    policy = skill_dependency_policy(
        "ctd-32s73-stability-template-fill",
        {"ctd-32s73-stability-template-fill", "reference-fact-extraction"},
    )

    assert policy == {
        "skill_id": "ctd-32s73-stability-template-fill",
        "public": True,
        "internal_dependency": False,
        "dependency_ids": ["reference-fact-extraction"],
        "dependency_details": [
            {
                "skill_id": "reference-fact-extraction",
                "status": "allowed",
                "reason": "declared_internal_dependency",
                "public": False,
                "internal_dependency": True,
                "available": True,
            }
        ],
    }
    assert skill_dependency_ids(
        "ctd-32s73-stability-template-fill",
        {"ctd-32s73-stability-template-fill", "reference-fact-extraction"},
    ) == ["reference-fact-extraction"]


def test_dependency_policy_reports_public_dependency_without_allowing_it(monkeypatch):
    monkeypatch.setitem(skill_dependencies.SKILL_DEPENDENCIES, "qa-file-reviewer", ["baoyu-translate"])

    policy = skill_dependency_policy(
        "qa-file-reviewer",
        {"baoyu-translate", "minimax-docx", "qa-file-reviewer"},
    )

    assert policy["dependency_ids"] == ["baoyu-translate"]
    assert policy["dependency_details"] == [
        {
            "skill_id": "baoyu-translate",
            "status": "blocked",
            "reason": "skill_dependency_not_internal",
            "public": True,
            "internal_dependency": False,
            "available": True,
        }
    ]
    with pytest.raises(SkillDependencyPolicyError, match="skill_dependency_not_internal: baoyu-translate"):
        skill_dependency_ids(
            "qa-file-reviewer",
            {"baoyu-translate", "minimax-docx", "qa-file-reviewer"},
        )


def test_admin_skill_detail_returns_blocked_dependency_policy_for_admin_audit(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_detail(conn, *, tenant_id, skill_id):
        return {
            "skill": {"skill_id": skill_id, "name": "QA File Reviewer"},
            "versions": [],
            "recent_snapshots": [],
        }

    async def fake_list_skill_ids(conn):
        return ["qa-file-reviewer"]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_admin_skill_detail", fake_detail)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/skills/qa-file-reviewer", headers=admin_headers())

    assert response.status_code == 200
    assert response.json()["dependency_policy"] == {
        "skill_id": "qa-file-reviewer",
        "public": True,
        "internal_dependency": False,
        "dependency_ids": ["minimax-docx"],
        "dependency_details": [
            {
                "skill_id": "minimax-docx",
                "status": "blocked",
                "reason": "skill_dependency_missing",
                "public": False,
                "internal_dependency": True,
                "available": False,
            }
        ],
    }


def test_admin_sync_builtin_skills_records_registry_versions_dependencies_and_snapshots(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    minimax_dir = skills_root / "minimax-docx"
    qa_dir = skills_root / "qa-file-reviewer"
    ragflow_dir = skills_root / "ragflow-knowledge-search"
    minimax_dir.mkdir(parents=True)
    qa_dir.mkdir(parents=True)
    ragflow_dir.mkdir(parents=True)
    (minimax_dir / "SKILL.md").write_text(
        "---\nname: minimax-docx\ndescription: Word document generation\n---\n\n# minimax-docx\n",
        encoding="utf-8",
    )
    (qa_dir / "SKILL.md").write_text(
        "---\nname: qa-file-reviewer\ndescription: QA review\n---\n\n# qa-file-reviewer\n",
        encoding="utf-8",
    )
    (ragflow_dir / "SKILL.md").write_text(
        "---\nname: ragflow-knowledge-search\ndescription: Read-only SOP knowledge retrieval\n---\n\n# ragflow\n",
        encoding="utf-8",
    )

    @dataclass(frozen=True)
    class FakeBuiltinSkill:
        name: str
        description: str
        path: Path
        version: str
        source: dict
        entry: dict

    class FakeRegistry:
        def __init__(self, skills_root):
            assert str(skills_root) == str(skills_root_path)

        def list_builtin_skills(self):
            return [
                FakeBuiltinSkill(
                    name="minimax-docx",
                    description="Word document generation",
                    path=minimax_dir,
                    version="hash-mini",
                    source={"kind": "builtin", "asset_dir": "minimax-docx", "version": "hash-mini"},
                    entry={"kind": "filesystem", "path": str(minimax_dir)},
                ),
                FakeBuiltinSkill(
                    name="qa-file-reviewer",
                    description="QA review",
                    path=qa_dir,
                    version="hash-qa",
                    source={"kind": "builtin", "asset_dir": "qa-file-reviewer", "version": "hash-qa"},
                    entry={"kind": "filesystem", "path": str(qa_dir)},
                ),
                FakeBuiltinSkill(
                    name="ragflow-knowledge-search",
                    description="Read-only SOP knowledge retrieval",
                    path=ragflow_dir,
                    version="hash-ragflow",
                    source={"kind": "builtin", "asset_dir": "ragflow-knowledge-search", "version": "hash-ragflow"},
                    entry={"kind": "filesystem", "path": str(ragflow_dir)},
                ),
            ]

    class FakeConnection:
        pass

    synced = []
    catalog_updates = []
    snapshot_backfills = []

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_upsert(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        synced.append(kwargs)

    async def fake_update_catalog(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        catalog_updates.append(kwargs)

    async def fake_backfill_snapshot(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        snapshot_backfills.append(kwargs)

    skills_root_path = str(skills_root)
    settings = Settings(frontend_poc_auth_enabled=True, platform_skills_root=skills_root_path)
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.admin_skills.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.admin_skills.BuiltinSkillRegistry", FakeRegistry)
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fake_upsert)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.backfill_builtin_skill_version_snapshot",
        fake_backfill_snapshot,
    )
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.update_skill_catalog_version",
        fake_update_catalog,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/skills/sync-builtin", headers=admin_headers())

    assert response.status_code == 200
    assert [item["skill_id"] for item in response.json()["synced"]] == [
        "minimax-docx",
        "qa-file-reviewer",
        "ragflow-knowledge-search",
    ]
    assert [item["skill_id"] for item in synced] == [
        "minimax-docx",
        "qa-file-reviewer",
        "ragflow-knowledge-search",
    ]
    assert synced[0]["version"] == "hash-mini"
    assert synced[0]["content_hash"] == "hash-mini"
    assert synced[0]["dependency_ids"] == []
    assert synced[0]["status"] == "draft"
    assert synced[0]["source_json"]["kind"] == "builtin"
    assert synced[0]["source_json"]["files"][0]["relative_path"] == "SKILL.md"
    assert synced[1]["version"] == "hash-qa"
    assert synced[1]["content_hash"] == "hash-qa"
    assert synced[1]["dependency_ids"] == ["minimax-docx"]
    assert synced[1]["status"] == "draft"
    assert synced[1]["source_json"]["kind"] == "builtin"
    assert synced[1]["source_json"]["files"][0]["relative_path"] == "SKILL.md"
    assert synced[1]["source_json"]["dependency_manifests"][0]["skill_id"] == "minimax-docx"
    assert synced[1]["source_json"]["dependency_manifests"][0]["files"][0]["relative_path"] == "SKILL.md"
    assert synced[2]["version"] == "hash-ragflow"
    assert synced[2]["content_hash"] == "hash-ragflow"
    assert synced[2]["dependency_ids"] == []
    assert synced[2]["status"] == "draft"
    assert synced[2]["source_json"]["kind"] == "builtin"
    assert synced[2]["source_json"]["files"][0]["relative_path"] == "SKILL.md"
    assert [item["status"] for item in response.json()["synced"]] == ["draft", "draft", "draft"]
    assert [(item["skill_id"], item["version"]) for item in catalog_updates] == [
        ("minimax-docx", "hash-mini"),
        ("qa-file-reviewer", "hash-qa"),
        ("ragflow-knowledge-search", "hash-ragflow"),
    ]
    assert [(item["skill_id"], item["version"]) for item in snapshot_backfills] == [
        ("minimax-docx", "hash-mini"),
        ("qa-file-reviewer", "hash-qa"),
        ("ragflow-knowledge-search", "hash-ragflow"),
    ]


def test_admin_sync_builtin_skills_rejects_dependency_policy_violation(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    qa_dir = skills_root / "qa-file-reviewer"
    minimax_dir = skills_root / "minimax-docx"
    translate_dir = skills_root / "baoyu-translate"
    qa_dir.mkdir(parents=True)
    minimax_dir.mkdir(parents=True)
    translate_dir.mkdir(parents=True)
    (qa_dir / "SKILL.md").write_text(
        "---\nname: qa-file-reviewer\ndescription: QA review\n---\n\n# qa-file-reviewer\n",
        encoding="utf-8",
    )
    (minimax_dir / "SKILL.md").write_text(
        "---\nname: minimax-docx\ndescription: Word document generation\n---\n\n# minimax-docx\n",
        encoding="utf-8",
    )
    (translate_dir / "SKILL.md").write_text(
        "---\nname: baoyu-translate\ndescription: Translate documents\n---\n\n# baoyu-translate\n",
        encoding="utf-8",
    )

    settings = Settings(frontend_poc_auth_enabled=True, platform_skills_root=str(skills_root))

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_upsert(conn, **kwargs):
        assert isinstance(conn, FakeConnection)

    async def fake_update_catalog(conn, **kwargs):
        assert isinstance(conn, FakeConnection)

    async def fake_backfill_snapshot(conn, **kwargs):
        assert isinstance(conn, FakeConnection)

    def disallowed_dependency(skill_id, available_skill_ids):
        if skill_id == "qa-file-reviewer":
            raise SkillDependencyPolicyError("skill_dependency_not_internal: baoyu-translate")
        return []

    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.admin_skills.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.skill_dependency_ids", disallowed_dependency)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fake_upsert)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.backfill_builtin_skill_version_snapshot",
        fake_backfill_snapshot,
    )
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.update_skill_catalog_version",
        fake_update_catalog,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/skills/sync-builtin", headers=admin_headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_dependency_policy_violation"


def test_admin_upload_skill_package_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", skill_package_zip(), "application/zip")},
        headers=user_headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_skill_admin_upload_existing_catalog_skill_is_denied_before_storage(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_skill(conn, *, skill_id):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "shared-research-skill"
        return {"skill_id": skill_id, "version": "builtin-shared-version", "status": "active"}

    async def fake_list_skill_ids(conn):
        raise AssertionError("existing-skill denial must happen before dependency lookup")

    class FakeObjectStorage:
        def put_bytes(self, **kwargs):
            raise AssertionError("existing-skill denial must happen before package storage")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FakeObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/shared-research-skill/versions/upload",
        files={
            "package": (
                "shared-research-skill.zip",
                skill_package_zip(name="shared-research-skill"),
                "application/zip",
            )
        },
        headers=skill_admin_headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_admin_upload_skill_package_rejects_missing_internal_dependency(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_skill(conn, *, skill_id):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "qa-file-reviewer"
        return {"skill_id": skill_id, "status": "active"}

    async def fake_list_skill_ids(conn):
        assert isinstance(conn, FakeConnection)
        return ["qa-file-reviewer"]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", skill_package_zip(), "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_dependency_policy_violation"


def test_admin_upload_skill_package_stores_object_and_upserts_skill_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    stored_objects = []
    upserts = []
    audits = []

    class FakeObjectStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            assert content == package_content
            assert content_type == "application/zip"
            stored_objects.append({"storage_key": storage_key, "content": content, "content_type": content_type})
            return StoredObject(storage_key=storage_key, sha256="zip-sha256", size_bytes=len(content))

    async def fake_upsert(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        upserts.append(kwargs)

    async def fake_get_skill(conn, *, skill_id):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "qa-file-reviewer"
        return {"skill_id": skill_id, "status": "active"}

    async def fake_list_skill_ids(conn):
        assert isinstance(conn, FakeConnection)
        return ["qa-file-reviewer", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        assert isinstance(conn, FakeConnection)
        return None

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        assert isinstance(conn, FakeConnection)
        assert tenant_id == "default"
        assert skill_id == "qa-file-reviewer"
        assert channel == "stable"
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-active",
            "previous_version": None,
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "previous-admin",
            "promoted_at": None,
        }

    async def fake_audit(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        audits.append(kwargs)
        return "aud-upload"

    package_content = skill_package_zip()
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FakeObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fake_upsert)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", package_content, "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    uploaded = body["uploaded"]
    expected_key = (
        "skills/qa-file-reviewer/versions/"
        f"{uploaded['content_hash']}/package.zip"
    )
    assert uploaded["skill_id"] == "qa-file-reviewer"
    assert uploaded["version"] == uploaded["content_hash"]
    assert uploaded["source"]["kind"] == "uploaded"
    assert uploaded["source"]["package_sha256"] == "zip-sha256"
    assert uploaded["source"]["size_bytes"] == len(package_content)
    assert uploaded["source"]["package_contract"]["schema_version"] == "ai-platform.skill-package-contract.v1"
    assert uploaded["source"]["package_contract"]["skill_id"] == "qa-file-reviewer"
    assert uploaded["source"]["package_contract"]["version"] == uploaded["content_hash"]
    assert uploaded["source"]["package_contract"]["content_hash"] == uploaded["content_hash"]
    assert uploaded["source"]["package_contract"]["package_sha256"] == "zip-sha256"
    assert uploaded["source"]["package_contract"]["storage_key"] == expected_key
    assert uploaded["source"]["package_contract"]["uploaded_by"] == "dev-admin"
    assert uploaded["source"]["dependency_evidence"] == {
        "schema_version": "ai-platform.skill-dependency-evidence.v1",
        "status": "review_required",
        "dependency_count": 1,
        "dependency_ids": ["minimax-docx"],
        "manifest_snapshot_present": True,
        "package_evidence_present": False,
        "evidence_files": {
            "sbom_or_signed_package": [],
            "license_policy": [],
            "vulnerability_scan": [],
        },
    }
    assert [item["relative_path"] for item in uploaded["source"]["files"]] == ["SKILL.md", "references/guide.md"]
    assert uploaded["source"]["dependency_manifests"][0]["skill_id"] == "minimax-docx"
    dependency_paths = [item["relative_path"] for item in uploaded["source"]["dependency_manifests"][0]["files"]]
    assert "SKILL.md" in dependency_paths

    assert len(stored_objects) == 1
    assert stored_objects[0]["storage_key"] == expected_key

    assert len(upserts) == 1
    upsert = upserts[0]
    assert upsert["skill_id"] == "qa-file-reviewer"
    assert upsert["version"] == uploaded["content_hash"]
    assert upsert["content_hash"] == uploaded["content_hash"]
    assert upsert["description"] == "Review Word documents."
    assert upsert["source_json"] == uploaded["source"]
    assert upsert["dependency_ids"] == ["minimax-docx"]
    assert upsert["status"] == "draft"
    assert upsert["created_by"] == "dev-admin"

    assert len(audits) == 1
    audit = audits[0]
    assert audit["tenant_id"] == "default"
    assert audit["user_id"] == "dev-admin"
    assert audit["action"] == "skill_version_uploaded"
    assert audit["target_type"] == "skill"
    assert audit["target_id"] == "qa-file-reviewer"
    assert audit["payload_json"]["skill_id"] == "qa-file-reviewer"
    assert audit["payload_json"]["version"] == uploaded["content_hash"]
    assert audit["payload_json"]["storage_key"] == expected_key
    assert audit["payload_json"]["package_sha256"] == "zip-sha256"


def test_skill_admin_upload_new_skill_package_creates_draft_without_release_or_visibility(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    package_content = skill_package_zip(
        name="new-research-skill",
        description="Summarize research briefs.",
    )
    stored_objects = []
    catalog_creates = []
    version_upserts = []
    release_policies = []
    visibility_updates = []
    audits = []

    class FakeObjectStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            assert content == package_content
            assert content_type == "application/zip"
            stored_objects.append({"storage_key": storage_key, "content": content, "content_type": content_type})
            return StoredObject(storage_key=storage_key, sha256="new-zip-sha256", size_bytes=len(content))

    async def fake_get_skill(conn, *, skill_id):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "new-research-skill"
        return None

    async def fake_list_skill_ids(conn):
        assert isinstance(conn, FakeConnection)
        return ["general-chat", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "new-research-skill"
        return None

    async def fake_create_catalog(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        catalog_creates.append(kwargs)

    async def fake_upsert_version(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        version_upserts.append(kwargs)

    async def fake_set_policy(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        release_policies.append(kwargs)

    async def fake_set_uploaded_workbench_status(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        visibility_updates.append(kwargs)
        return {"skill_id": kwargs["skill_id"], "status": kwargs["status"], "visible_to_user": True}

    async def fake_audit(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        audits.append(kwargs)
        return "aud-new-upload"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FakeObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.create_skill_catalog", fake_create_catalog, raising=False)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fake_upsert_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.set_uploaded_workbench_skill_status",
        fake_set_uploaded_workbench_status,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/new-research-skill/versions/upload",
        files={"package": ("new-research-skill.zip", package_content, "application/zip")},
        headers=skill_admin_headers(),
    )

    assert response.status_code == 200
    uploaded = response.json()["uploaded"]
    assert uploaded["skill_id"] == "new-research-skill"
    assert uploaded["version"] == uploaded["content_hash"]
    assert uploaded["description"] == "Summarize research briefs."
    assert uploaded["status"] == "draft"
    assert uploaded["source"]["kind"] == "uploaded"
    assert uploaded["source"]["storage_key"] == (
        f"skills/new-research-skill/versions/{uploaded['content_hash']}/package.zip"
    )

    assert len(stored_objects) == 1
    assert catalog_creates == [
        {
            "skill_id": "new-research-skill",
            "name": "new-research-skill",
            "version": uploaded["content_hash"],
            "description": "Summarize research briefs.",
            "input_modes": ["chat"],
            "output_modes": ["answer"],
            "executor_type": "claude-agent-worker",
            "status": "active",
        }
    ]
    assert len(version_upserts) == 1
    assert version_upserts[0]["skill_id"] == "new-research-skill"
    assert version_upserts[0]["dependency_ids"] == []
    assert version_upserts[0]["status"] == "draft"
    assert release_policies == []
    assert visibility_updates == []
    assert [item["action"] for item in audits] == [
        "skill_catalog_created_from_upload",
        "skill_version_uploaded",
    ]


@pytest.mark.asyncio
async def test_publish_uploaded_skill_to_tenant_rejects_delegated_skill_admin_before_writes(monkeypatch):
    from app.routes import admin_skills as route_module

    calls = []

    async def record_policy(*args, **kwargs):
        calls.append(("policy", kwargs))

    async def record_visibility(*args, **kwargs):
        calls.append(("visibility", kwargs))

    async def record_audit(*args, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(route_module.repositories, "set_skill_release_policy", record_policy)
    monkeypatch.setattr(route_module.repositories, "set_uploaded_workbench_skill_status", record_visibility)
    monkeypatch.setattr(route_module.repositories, "append_audit_log", record_audit)

    with pytest.raises(HTTPException) as exc_info:
        await route_module._publish_uploaded_skill_to_tenant(
            object(),
            principal=AuthPrincipal(
                user_id="skill-admin",
                display_name="Skill Admin",
                tenant_id="default",
                roles=["user"],
                permissions=["skill:admin"],
            ),
            skill_id="new-research-skill",
            version="hash-draft",
            previous_version=None,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "not_ai_admin"
    assert calls == []


def test_admin_upload_new_skill_catalog_conflict_fails_without_global_overwrite(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    package_content = skill_package_zip(
        name="raced-research-skill",
        description="Summarize raced research briefs.",
    )

    class FakeObjectStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            raise AssertionError("catalog conflict must stop before package object storage")

    async def fake_get_skill(conn, *, skill_id):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "raced-research-skill"
        return None

    async def fake_list_skill_ids(conn):
        assert isinstance(conn, FakeConnection)
        return ["general-chat"]

    async def fake_get_version(conn, *, skill_id, version):
        raise AssertionError("catalog conflict must stop before existing-version reuse")

    async def fake_create_catalog(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        assert kwargs["skill_id"] == "raced-research-skill"
        raise RepositoryConflictError("skill_catalog_already_exists")

    async def fail_upsert_catalog(conn, **kwargs):
        raise AssertionError("new skill uploads must use insert-only catalog creation")

    async def fail_after_catalog_conflict(conn, **kwargs):
        raise AssertionError("catalog conflict must stop before version, policy, or audit writes")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FakeObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.create_skill_catalog", fake_create_catalog, raising=False)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_catalog", fail_upsert_catalog, raising=False)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fail_after_catalog_conflict)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fail_after_catalog_conflict)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.set_uploaded_workbench_skill_status",
        fail_after_catalog_conflict,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fail_after_catalog_conflict)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/raced-research-skill/versions/upload",
        files={"package": ("raced-research-skill.zip", package_content, "application/zip")},
        headers=skill_admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_catalog_already_exists"


def test_admin_preview_skill_package_uses_global_catalog_existence(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_list_skill_ids(conn):
        assert isinstance(conn, FakeConnection)
        return ["tenant-invisible-skill", "general-chat"]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/upload/preview",
        files={
            "file": (
                "tenant-invisible-skill.zip",
                skill_package_zip(name="tenant-invisible-skill", description="Tenant invisible."),
                "application/zip",
            )
        },
        headers=skill_admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "skill_count": 1,
        "skills": [
            {
                "name": "tenant-invisible-skill",
                "description": "Tenant invisible.",
                "file_count": 2,
                "files": ["SKILL.md", "references/guide.md"],
                "already_exists": True,
            }
        ],
    }


def test_admin_upload_existing_catalog_skill_without_tenant_policy_publishes_to_tenant(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    package_content = skill_package_zip(
        name="shared-research-skill",
        description="Summarize shared research briefs.",
    )
    version_upserts = []
    release_policies = []
    visibility_updates = []
    audits = []

    class FakeObjectStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            assert content == package_content
            assert content_type == "application/zip"
            return StoredObject(storage_key=storage_key, sha256="shared-zip-sha256", size_bytes=len(content))

    async def fake_get_skill(conn, *, skill_id):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "shared-research-skill"
        return {
            "skill_id": skill_id,
            "id": skill_id,
            "name": skill_id,
            "version": "builtin-shared-version",
            "status": "active",
        }

    async def fake_list_skill_ids(conn):
        assert isinstance(conn, FakeConnection)
        return ["shared-research-skill", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "shared-research-skill"
        return None

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        assert isinstance(conn, FakeConnection)
        assert tenant_id == "default"
        assert skill_id == "shared-research-skill"
        assert channel == "stable"
        return None

    async def fail_upsert_catalog(conn, **kwargs):
        raise AssertionError("existing global skill catalog row must not be recreated")

    async def fake_upsert_version(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        version_upserts.append(kwargs)

    async def fake_set_policy(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        release_policies.append(kwargs)

    async def fake_set_uploaded_workbench_status(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        visibility_updates.append(kwargs)
        return {"skill_id": kwargs["skill_id"], "status": kwargs["status"], "visible_to_user": True}

    async def fake_audit(conn, **kwargs):
        assert isinstance(conn, FakeConnection)
        audits.append(kwargs)
        return "aud-existing-tenant-upload"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FakeObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_catalog", fail_upsert_catalog, raising=False)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fake_upsert_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.set_uploaded_workbench_skill_status",
        fake_set_uploaded_workbench_status,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/shared-research-skill/versions/upload",
        files={"package": ("shared-research-skill.zip", package_content, "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    uploaded = response.json()["uploaded"]
    assert uploaded["skill_id"] == "shared-research-skill"
    assert uploaded["status"] == "released"
    assert len(version_upserts) == 1
    assert version_upserts[0]["status"] == "released"
    assert release_policies == [
        {
            "tenant_id": "default",
            "skill_id": "shared-research-skill",
            "version": uploaded["content_hash"],
            "previous_version": "builtin-shared-version",
            "promoted_by": "dev-admin",
        }
    ]
    assert visibility_updates == [
        {
            "tenant_id": "default",
            "skill_id": "shared-research-skill",
            "status": "active",
        }
    ]
    assert [item["action"] for item in audits] == [
        "skill_version_uploaded",
        "skill_release_promoted_from_upload",
    ]


def test_admin_publishing_upload_rolls_back_when_response_model_build_fails(monkeypatch):
    calls = []
    package_content = skill_package_zip(
        name="response-failure-skill",
        description="Exercise response construction rollback.",
    )

    @asynccontextmanager
    async def recording_transaction():
        calls.append(("tx_enter", {}))
        try:
            yield object()
        except Exception:
            calls.append(("tx_rollback", {}))
            raise
        else:
            calls.append(("tx_commit", {}))

    class FakeObjectStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            assert content == package_content
            assert content_type == "application/zip"
            return StoredObject(storage_key=storage_key, sha256="response-failure-sha256", size_bytes=len(content))

    async def fake_get_skill(conn, *, skill_id):
        assert skill_id == "response-failure-skill"
        return None

    async def fake_list_skill_ids(conn):
        return []

    async def fake_create_skill_catalog(conn, **kwargs):
        calls.append(("create_catalog", kwargs))

    async def fake_upsert_skill_version(conn, **kwargs):
        calls.append(("upsert_version", kwargs))
        return True

    async def fake_set_skill_release_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_set_uploaded_workbench_skill_status(conn, **kwargs):
        calls.append(("set_visibility", kwargs))
        return {"skill_id": kwargs["skill_id"], "status": kwargs["status"]}

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return f"aud-{kwargs['action']}"

    def fail_response_build(**kwargs):
        calls.append(("response_build_failed", kwargs))
        raise RuntimeError("response_build_failed")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", recording_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FakeObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.create_skill_catalog",
        fake_create_skill_catalog,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fake_upsert_skill_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_skill_release_policy)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.set_uploaded_workbench_skill_status",
        fake_set_uploaded_workbench_skill_status,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.routes.admin_skills.AdminSkillUploadResponse", fail_response_build)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/ai/admin/skills/response-failure-skill/versions/upload",
        files={"package": ("response-failure-skill.zip", package_content, "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 500
    names = [name for name, _ in calls]
    assert names.count("tx_enter") == 1
    assert "tx_rollback" in names
    assert "tx_commit" not in names
    assert names.index("response_build_failed") < names.index("tx_rollback")
    audit_actions = [payload["action"] for name, payload in calls if name == "audit"]
    assert audit_actions == [
        "skill_catalog_created_from_upload",
        "skill_version_uploaded",
        "skill_release_promoted_from_upload",
    ]


def test_admin_upload_skill_package_rejects_name_mismatch_before_storage(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        raise AssertionError("database must not be called for a package name mismatch")
        yield FakeConnection()

    class FailingObjectStorage:
        def put_bytes(self, **kwargs):
            raise AssertionError("storage must not be called for a package name mismatch")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FailingObjectStorage)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/ai/admin/skills/unknown-skill/versions/upload",
        files={"package": ("other-skill.zip", skill_package_zip(name="other-skill"), "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "skill_package_name_mismatch"


@pytest.mark.asyncio
async def test_admin_upload_skill_package_rejects_unsafe_skill_id_before_read(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    class UnreadableUpload:
        async def read(self, *args, **kwargs):
            raise AssertionError("upload body must not be read before skill id validation")

    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)

    with pytest.raises(Exception) as exc_info:
        await admin_upload_skill_package(
            "../unknown-skill",
            UnreadableUpload(),
            principal=AuthPrincipal(
                user_id="dev-admin",
                display_name="Dev Admin",
                tenant_id="default",
                roles=["admin"],
            ),
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert getattr(exc_info.value, "detail", None) == "skill_id contains unsupported characters"


def test_admin_upload_skill_package_reuses_existing_version_without_storage_overwrite(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    class FailingObjectStorage:
        def put_bytes(self, **kwargs):
            raise AssertionError("existing immutable skill version must not overwrite object storage")

    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id, "status": "active"}

    async def fake_list_skill_ids(conn):
        return ["qa-file-reviewer", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Existing upload",
            "source": {
                "kind": "uploaded",
                "storage_key": f"skills/{skill_id}/versions/{version}/package.zip",
                "package_sha256": "existing-sha",
                "size_bytes": 123,
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_manifests": [minimax_dependency_manifest()],
            },
            "dependency_ids": ["minimax-docx"],
            "status": "active",
            "created_by": "first-admin",
            "created_at": None,
        }

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        assert tenant_id == "default"
        assert skill_id == "qa-file-reviewer"
        assert channel == "stable"
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-active",
            "previous_version": None,
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "first-admin",
            "promoted_at": None,
        }

    async def fail_upsert(conn, **kwargs):
        raise AssertionError("existing immutable skill version must not be upserted again")

    async def fake_audit(conn, **kwargs):
        calls.append(kwargs)
        return "aud-reused"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FailingObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fail_upsert)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", skill_package_zip(), "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["uploaded"]["source"]["package_sha256"] == "existing-sha"
    assert calls[0]["action"] == "skill_version_upload_reused"


def test_admin_upload_existing_version_without_tenant_policy_publishes_reused_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []
    policies = []
    visibility_updates = []
    status_updates = []

    class FailingObjectStorage:
        def put_bytes(self, **kwargs):
            raise AssertionError("existing immutable skill version must not overwrite object storage")

    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id, "version": "0.1.0", "status": "active"}

    async def fake_list_skill_ids(conn):
        return ["qa-file-reviewer", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        storage_key = f"skills/{skill_id}/versions/{version}/package.zip"
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Existing upload",
            "source": {
                "kind": "uploaded",
                "storage_key": storage_key,
                "package_sha256": "existing-sha",
                "size_bytes": 123,
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
                "dependency_manifests": [minimax_dependency_manifest()],
                "package_contract": {
                    "schema_version": "ai-platform.skill-package-contract.v1",
                    "skill_id": skill_id,
                    "version": version,
                    "content_hash": version,
                    "package_sha256": "existing-sha",
                    "storage_key": storage_key,
                    "uploaded_by": "first-admin",
                    "file_count": 2,
                    "size_bytes": 123,
                    "evidence_files": {
                        "sbom_or_signed_package": [],
                        "license_policy": [],
                        "vulnerability_scan": [],
                    },
                },
            },
            "dependency_ids": ["minimax-docx"],
            "status": "draft",
            "created_by": "first-admin",
            "created_at": None,
        }

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        assert tenant_id == "default"
        assert skill_id == "qa-file-reviewer"
        assert channel == "stable"
        return None

    async def fail_upsert(conn, **kwargs):
        raise AssertionError("existing immutable skill version must not be upserted again")

    async def fake_update_status(conn, **kwargs):
        status_updates.append(kwargs)
        return {**(await fake_get_version(conn, skill_id=kwargs["skill_id"], version=kwargs["version"])), "status": kwargs["status"]}

    async def fake_set_policy(conn, **kwargs):
        policies.append(kwargs)

    async def fake_set_uploaded_workbench_status(conn, **kwargs):
        visibility_updates.append(kwargs)
        return {"skill_id": kwargs["skill_id"], "status": kwargs["status"], "visible_to_user": True}

    async def fake_audit(conn, **kwargs):
        calls.append(kwargs)
        return "aud-reused"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FailingObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fail_upsert)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.set_uploaded_workbench_skill_status",
        fake_set_uploaded_workbench_status,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", skill_package_zip(), "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    uploaded = response.json()["uploaded"]
    assert uploaded["source"]["package_sha256"] == "existing-sha"
    assert uploaded["status"] == "released"
    assert status_updates == [
        {
            "skill_id": "qa-file-reviewer",
            "version": uploaded["content_hash"],
            "status": "released",
        }
    ]
    assert policies == [
        {
            "tenant_id": "default",
            "skill_id": "qa-file-reviewer",
            "version": uploaded["content_hash"],
            "previous_version": "0.1.0",
            "promoted_by": "dev-admin",
        }
    ]
    assert visibility_updates == [
        {
            "tenant_id": "default",
            "skill_id": "qa-file-reviewer",
            "status": "active",
        }
    ]
    assert [item["action"] for item in calls] == [
        "skill_version_upload_reused",
        "skill_release_promoted_from_upload",
    ]


def test_admin_upload_skill_package_reuse_rejects_stale_dependency_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    class FailingObjectStorage:
        def put_bytes(self, **kwargs):
            raise AssertionError("stale existing skill version must reject before object storage")

    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id, "status": "active"}

    async def fake_list_skill_ids(conn):
        return ["qa-file-reviewer", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Stale existing upload",
            "source": {
                "kind": "uploaded",
                "storage_key": f"skills/{skill_id}/versions/{version}/package.zip",
                "package_sha256": "existing-sha",
                "size_bytes": 123,
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "first-admin",
            "created_at": None,
        }

    async def fail_audit(conn, **kwargs):
        calls.append(kwargs)
        raise AssertionError("stale existing skill version must reject before audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FailingObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fail_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", skill_package_zip(), "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"
    assert calls == []


def test_admin_upload_skill_package_reuse_rejects_non_uploaded_existing_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    class FailingObjectStorage:
        def put_bytes(self, **kwargs):
            raise AssertionError("non-uploaded existing skill version must reject before object storage")

    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id, "status": "active"}

    async def fake_list_skill_ids(conn):
        return ["qa-file-reviewer", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Builtin version with colliding version id",
            "source": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "status": "active",
            "created_by": "system",
            "created_at": None,
        }

    async def fail_audit(conn, **kwargs):
        raise AssertionError("non-uploaded existing skill version must reject before audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FailingObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fail_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", skill_package_zip(), "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_upload_skill_package_rejects_concurrent_version_conflict_before_publish(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    stored_objects = []

    class FakeObjectStorage:
        def put_bytes(self, *, storage_key, content, content_type):
            stored_objects.append({"storage_key": storage_key, "content_type": content_type})
            return StoredObject(storage_key=storage_key, sha256="zip-sha256", size_bytes=len(content))

    async def fake_get_skill(conn, *, skill_id):
        return {"skill_id": skill_id, "status": "active"}

    async def fake_list_skill_ids(conn):
        return ["qa-file-reviewer", "minimax-docx"]

    async def fake_get_version(conn, *, skill_id, version):
        return None

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return None

    async def fake_upsert_version(conn, **kwargs):
        return False

    async def fail_set_policy(conn, **kwargs):
        raise AssertionError("conflicting skill version must not be published")

    async def fail_set_uploaded_workbench_status(conn, **kwargs):
        raise AssertionError("conflicting skill version must not become active")

    async def fail_audit(conn, **kwargs):
        raise AssertionError("conflicting skill version must not write upload audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.ObjectStorage", FakeObjectStorage)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.list_skill_ids", fake_list_skill_ids)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.upsert_skill_version", fake_upsert_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fail_set_policy)
    monkeypatch.setattr(
        "app.routes.admin_skills.repositories.set_uploaded_workbench_skill_status",
        fail_set_uploaded_workbench_status,
        raising=False,
    )
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fail_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/upload",
        files={"package": ("qa-file-reviewer.zip", skill_package_zip(), "application/zip")},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_already_exists"
    assert len(stored_objects) == 1


def test_admin_skill_release_routes_require_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    responses = [
        client.get(
            "/api/ai/admin/skills/qa-file-reviewer/versions/diff?from_version=hash-a&to_version=hash-b",
            headers=user_headers(),
        ),
        client.post(
            "/api/ai/admin/skills/qa-file-reviewer/promote",
            json={"version": "hash-b"},
            headers=user_headers(),
        ),
        client.post(
            "/api/ai/admin/skills/qa-file-reviewer/rollback",
            json={"version": "hash-a"},
            headers=user_headers(),
        ),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert [response.json()["detail"] for response in responses] == ["not_ai_admin", "not_ai_admin", "not_ai_admin"]


def test_admin_skill_version_diff_returns_manifest_changes(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_diff(conn, *, skill_id, from_version, to_version):
        assert isinstance(conn, FakeConnection)
        assert skill_id == "qa-file-reviewer"
        assert from_version == "hash-a"
        assert to_version == "hash-b"
        return {
            "skill_id": skill_id,
            "from_version": from_version,
            "to_version": to_version,
            "content_hash_changed": True,
            "description_changed": False,
            "source_changed": True,
            "dependency_added": ["term-checker"],
            "dependency_removed": ["minimax-docx"],
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.diff_skill_versions", fake_diff)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/skills/qa-file-reviewer/versions/diff?from_version=hash-a&to_version=hash-b",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "skill_id": "qa-file-reviewer",
        "from_version": "hash-a",
        "to_version": "hash-b",
        "content_hash_changed": True,
        "description_changed": False,
        "source_changed": True,
        "dependency_added": ["term-checker"],
        "dependency_removed": ["minimax-docx"],
    }


def test_admin_skill_version_status_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-a/status",
        json={"status": "reviewed"},
        headers=user_headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"


def test_skill_admin_permission_cannot_mutate_global_skill_lifecycle(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    status_response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-a/status",
        json={"status": "disabled"},
        headers=skill_admin_headers(),
    )
    promote_response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-a"},
        headers=skill_admin_headers(),
    )
    rollback_response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=skill_admin_headers(),
    )
    diff_response = client.get(
        "/api/ai/admin/skills/qa-file-reviewer/versions/diff?from_version=hash-a&to_version=hash-b",
        headers=skill_admin_headers(),
    )
    detail_response = client.get(
        "/api/ai/admin/skills/qa-file-reviewer",
        headers=skill_admin_headers(),
    )

    assert status_response.status_code == 403
    assert promote_response.status_code == 403
    assert rollback_response.status_code == 403
    assert diff_response.status_code == 403
    assert detail_response.status_code == 403
    assert status_response.json()["detail"] == "not_ai_admin"
    assert promote_response.json()["detail"] == "not_ai_admin"
    assert rollback_response.json()["detail"] == "not_ai_admin"
    assert diff_response.json()["detail"] == "not_ai_admin"
    assert detail_response.json()["detail"] == "not_ai_admin"


def test_admin_skill_version_status_reviewed_requires_release_review(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            **materializable_uploaded_qa_version(version),
            "status": "draft",
        }

    async def fail_update_status(conn, **kwargs):
        raise AssertionError("blocked release review must not update status")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fail_update_status, raising=False)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", blocked_skill_version_release)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-draft/status",
        json={"status": "reviewed"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_release_review_not_verified"


def test_admin_skill_version_status_marks_reviewed_and_audits(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        calls.append(("get_version", skill_id, version))
        return {
            **materializable_uploaded_qa_version(version),
            "status": "draft",
        }

    async def fake_update_status(conn, **kwargs):
        calls.append(("update_status", kwargs))
        return {
            **materializable_uploaded_qa_version(kwargs["version"]),
            "status": kwargs["status"],
        }

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-status-reviewed"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_status, raising=False)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-draft/status",
        json={"status": "reviewed"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reviewed"
    update_call = [item for item in calls if item[0] == "update_status"][0][1]
    assert update_call == {
        "skill_id": "qa-file-reviewer",
        "version": "hash-draft",
        "status": "reviewed",
    }
    audit_call = [item for item in calls if item[0] == "audit"][0][1]
    assert audit_call["action"] == "skill_version_status_changed"
    assert audit_call["target_id"] == "qa-file-reviewer"
    assert audit_call["payload_json"]["skill_id"] == "qa-file-reviewer"
    assert audit_call["payload_json"]["version"] == "hash-draft"
    assert audit_call["payload_json"]["from_status"] == "draft"
    assert audit_call["payload_json"]["to_status"] == "reviewed"
    assert audit_call["payload_json"]["review_status"] == "passed"


@pytest.mark.parametrize("target_status", ["disabled", "deprecated"])
def test_admin_skill_version_status_can_disable_or_deprecate_without_release_review(monkeypatch, target_status):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_uploaded_qa_version(version)

    async def fake_update_status(conn, **kwargs):
        calls.append(("update_status", kwargs))
        return {
            **materializable_uploaded_qa_version(kwargs["version"]),
            "status": kwargs["status"],
        }

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-status"

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        calls.append(("get_policy", {"tenant_id": tenant_id, "skill_id": skill_id, "channel": channel}))
        return {"current_version": "hash-current", "previous_version": "hash-a"}

    def fail_review(version):
        raise AssertionError("disable/deprecate must not require release review")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_status, raising=False)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", fail_review)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-a/status",
        json={"status": target_status},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == target_status
    update_call = [item for item in calls if item[0] == "update_status"][0][1]
    assert update_call["status"] == target_status
    audit_call = [item for item in calls if item[0] == "audit"][0][1]
    assert audit_call["payload_json"]["to_status"] == target_status


@pytest.mark.parametrize("target_status", ["disabled", "deprecated"])
def test_admin_skill_version_status_rejects_disabling_current_release_policy(monkeypatch, target_status):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_uploaded_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {"current_version": "hash-current", "previous_version": "hash-a"}

    async def fail_update_status(conn, **kwargs):
        raise AssertionError("current release policy version must not be disabled or deprecated directly")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fail_update_status, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-current/status",
        json={"status": target_status},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_has_active_release_policy"


@pytest.mark.parametrize("target_status", ["disabled", "deprecated"])
@pytest.mark.parametrize("rollout_percent", [0, 50])
def test_admin_skill_version_status_rejects_disabling_gray_rollout_previous_policy(
    monkeypatch,
    target_status,
    rollout_percent,
):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_uploaded_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "current_version": "hash-current",
            "previous_version": "hash-a",
            "rollout_percent": rollout_percent,
        }

    async def fail_update_status(conn, **kwargs):
        raise AssertionError("gray rollout previous version must not be disabled or deprecated directly")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fail_update_status, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-a/status",
        json={"status": target_status},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_has_active_release_policy"


def test_admin_skill_version_status_rejects_invalid_status(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/versions/hash-a/status",
        json={"status": "released"},
        headers=admin_headers(),
    )

    assert response.status_code == 422


def test_admin_promote_skill_version_sets_release_policy_and_audit(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        assert isinstance(conn, FakeConnection)
        calls.append(("get_version", skill_id, version))
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        calls.append(("get_policy", tenant_id, skill_id, channel))
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-a",
            "previous_version": "0.1.0",
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "old-admin",
            "promoted_at": None,
        }

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_update_status(conn, **kwargs):
        calls.append(("update_status", kwargs))
        return {
            **materializable_builtin_qa_version(kwargs["version"]),
            "status": kwargs["status"],
        }

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-b")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-b"
    assert response.json()["previous_version"] == "hash-a"
    set_call = [item for item in calls if item[0] == "set_policy"][0][1]
    assert set_call["tenant_id"] == "default"
    assert set_call["skill_id"] == "qa-file-reviewer"
    assert set_call["version"] == "hash-b"
    assert set_call["previous_version"] == "hash-a"
    assert ("update_status", {"skill_id": "qa-file-reviewer", "version": "hash-b", "status": "released"}) in calls
    assert ("update_status", {"skill_id": "qa-file-reviewer", "version": "hash-a", "status": "deprecated"}) in calls
    audit_call = [item for item in calls if item[0] == "audit"][0][1]
    assert audit_call["action"] == "skill_version_promoted"
    assert audit_call["target_id"] == "qa-file-reviewer"
    assert audit_call["payload_json"]["to_version"] == "hash-b"
    assert audit_call["payload_json"]["schema_version"] == "ai-platform.skill-version-release-audit.v1"
    assert audit_call["payload_json"]["lifecycle"] == {
        "released_version": "hash-b",
        "deprecated_version": "hash-a",
        "target_status": "released",
    }
    assert audit_call["payload_json"]["release_review"]["status"] == "passed"
    assert audit_call["payload_json"]["release_review"]["blocker_count"] == 0
    assert audit_call["payload_json"]["release_review"]["dependency_evidence_present"] is True
    assert "storage_key" not in audit_call["payload_json"]
    assert "content_base64" not in str(audit_call["payload_json"])


def test_admin_promote_rejects_unreviewed_release_evidence_before_policy_lookup(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        raise AssertionError("promote must fail before policy lookup")

    def blocked_release_review(version):
        return {
            "status": "blocked",
            "blockers": ["dependency_license_policy_review_not_verified"],
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", blocked_release_review)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-b")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_release_review_not_verified"


def test_admin_promote_rejects_draft_skill_version_before_release_review(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            **materializable_uploaded_qa_version(version),
            "status": "draft",
        }

    def fail_review(version):
        raise AssertionError("draft version must reject before release review")

    async def fail_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        raise AssertionError("draft version must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", fail_review)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-draft"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_reviewed"


def test_admin_promote_accepts_gray_rollout_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        if version == "hash-a":
            return materializable_uploaded_qa_version(version, description="Previous QA review")
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-a",
            "previous_version": "0.1.0",
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "old-admin",
            "promoted_at": None,
        }

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-gray-promote"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_skill_version_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-b")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b", "rollout_percent": 50},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-b"
    assert response.json()["previous_version"] == "hash-a"
    assert response.json()["rollout_percent"] == 50
    set_call = [item for item in calls if item[0] == "set_policy"][0][1]
    assert set_call["version"] == "hash-b"
    assert set_call["previous_version"] == "hash-a"
    assert set_call["rollout_percent"] == 50
    audit_call = [item for item in calls if item[0] == "audit"][0][1]
    assert audit_call["payload_json"]["rollout_percent"] == 50


def test_admin_promote_gray_rejects_unmaterializable_existing_policy_current_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        calls.append(("get_version", skill_id, version))
        if version == "hash-b":
            return materializable_builtin_qa_version(version)
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-a",
            "previous_version": "0.1.0",
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "old-admin",
            "promoted_at": None,
        }

    async def fail_set_policy(conn, **kwargs):
        raise AssertionError("gray promote must reject before writing an unmaterializable previous cohort")

    async def fail_audit(conn, **kwargs):
        raise AssertionError("gray promote must reject before audit")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fail_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fail_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-b")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b", "rollout_percent": 50},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"
    assert ("get_version", "qa-file-reviewer", "hash-a") in calls


def test_admin_promote_gray_without_policy_uses_catalog_version_as_previous(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        calls.append(("get_version", skill_id, version))
        if version == "hash-uploaded":
            return materializable_uploaded_qa_version(version)
        return materializable_builtin_qa_version(version, description="Current QA review")

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        calls.append(("get_policy", tenant_id, skill_id, channel))
        return None

    async def fake_get_skill(conn, *, skill_id):
        calls.append(("get_skill", skill_id))
        return {"skill_id": skill_id, "status": "active", "version": "hash-a"}

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-first-gray-promote"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_skill_version_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-a")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-uploaded", "rollout_percent": 50},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-uploaded"
    assert response.json()["previous_version"] == "hash-a"
    assert response.json()["rollout_percent"] == 50
    assert ("get_version", "qa-file-reviewer", "hash-a") in calls
    set_call = [item for item in calls if item[0] == "set_policy"][0][1]
    assert set_call["version"] == "hash-uploaded"
    assert set_call["previous_version"] == "hash-a"
    assert set_call["rollout_percent"] == 50


def test_admin_promote_full_without_policy_allows_unmaterializable_catalog_previous(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        calls.append(("get_version", skill_id, version))
        if version == "hash-uploaded":
            return materializable_uploaded_qa_version(version)
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Stale QA review",
            "source": {"kind": "uploaded", "files": snapshot_files()},
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        calls.append(("get_policy", tenant_id, skill_id, channel))
        return None

    async def fake_get_skill(conn, *, skill_id):
        calls.append(("get_skill", skill_id))
        return {"skill_id": skill_id, "status": "active", "version": "hash-stale"}

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_update_status(conn, **kwargs):
        calls.append(("update_status", kwargs))
        return {
            **materializable_uploaded_qa_version(kwargs["version"]),
            "status": kwargs["status"],
        }

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-first-full-promote"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill", fake_get_skill)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-uploaded", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-uploaded"
    assert response.json()["previous_version"] == "hash-stale"
    assert ("get_version", "qa-file-reviewer", "hash-stale") in calls
    set_call = [item for item in calls if item[0] == "set_policy"][0][1]
    assert set_call["version"] == "hash-uploaded"
    assert set_call["previous_version"] == "hash-stale"
    assert (
        "update_status",
        {"skill_id": "qa-file-reviewer", "version": "hash-stale", "status": "deprecated"},
    ) in calls
    audit_call = [item for item in calls if item[0] == "audit"][0][1]
    assert audit_call["payload_json"]["from_version"] == "hash-stale"


def test_admin_promote_rejects_inactive_skill_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "disabled",
            "created_by": "dev-admin",
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_inactive"


def test_admin_promote_rejects_builtin_version_that_cannot_be_materialized(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "current-hash")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "old-hash"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_promote_rejects_builtin_snapshot_materialization_failure(monkeypatch):
    class FakeConnection:
        pass

    class FakeRegistry:
        def __init__(self, root):
            self.root = root

        def list_builtin_skills(self):
            @dataclass
            class FakeSkill:
                name: str

            return [FakeSkill("qa-file-reviewer")]

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    def fail_build_skill_manifest_pins(**kwargs):
        raise ValueError("skill snapshot too large")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.BuiltinSkillRegistry", FakeRegistry)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_manifest_pins", fail_build_skill_manifest_pins)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_promote_accepts_uploaded_version_with_snapshot_files(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_uploaded_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-a",
            "previous_version": "0.1.0",
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "old-admin",
            "promoted_at": None,
        }

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-uploaded-promote"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_skill_version_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-uploaded", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-uploaded"
    assert [item for item in calls if item[0] == "set_policy"][0][1]["version"] == "hash-uploaded"


def test_admin_promote_rejects_uploaded_version_with_stale_dependency_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Uploaded QA review",
            "source": {
                "kind": "uploaded",
                "storage_key": "tenants/default/skills/qa-file-reviewer/versions/hash-uploaded/package.zip",
                "files": snapshot_files(),
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("stale uploaded dependency metadata must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-uploaded", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_promote_rejects_uploaded_version_without_snapshot_files(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Uploaded QA review",
            "source": {
                "kind": "uploaded",
                "storage_key": "tenants/default/skills/qa-file-reviewer/versions/hash-uploaded/package.zip",
                "files": [],
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-uploaded", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_promote_rejects_uploaded_version_with_missing_dependency_snapshots(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Uploaded QA review",
            "source": {
                "kind": "uploaded",
                "storage_key": "tenants/default/skills/qa-file-reviewer/versions/hash-uploaded/package.zip",
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            },
            "dependency_ids": ["minimax-docx"],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("unmaterializable dependency snapshots must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-uploaded", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_promote_rejects_fileless_builtin_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("fileless builtin policy versions must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-b")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_promote_rejects_builtin_snapshot_with_stale_dependency_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {
                "kind": "builtin",
                "asset_dir": "qa-file-reviewer",
                "version": version,
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("stale builtin dependency metadata must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b", "rollout_percent": 100},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_promote_missing_version_returns_404(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "missing-version"},
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "skill_version_not_found"


def test_admin_rollback_skill_version_sets_release_policy_and_audit(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        if version == "hash-a":
            return {
                **materializable_builtin_qa_version(version),
                "status": "deprecated",
            }
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-b",
            "previous_version": "hash-a",
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "dev-admin",
            "promoted_at": None,
        }

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_update_status(conn, **kwargs):
        calls.append(("update_status", kwargs))
        return {
            **materializable_builtin_qa_version(kwargs["version"]),
            "status": kwargs["status"],
        }

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-a"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-a")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-a"
    assert response.json()["previous_version"] == "hash-b"
    set_call = [item for item in calls if item[0] == "set_policy"][0][1]
    assert set_call["version"] == "hash-a"
    assert set_call["previous_version"] == "hash-b"
    assert ("update_status", {"skill_id": "qa-file-reviewer", "version": "hash-a", "status": "released"}) in calls
    assert ("update_status", {"skill_id": "qa-file-reviewer", "version": "hash-b", "status": "deprecated"}) in calls
    audit_call = [item for item in calls if item[0] == "audit"][0][1]
    assert audit_call["action"] == "skill_version_rolled_back"
    assert audit_call["payload_json"]["from_version"] == "hash-b"
    assert audit_call["payload_json"]["to_version"] == "hash-a"
    assert audit_call["payload_json"]["rollout_percent"] == 100
    assert audit_call["payload_json"]["schema_version"] == "ai-platform.skill-version-release-audit.v1"
    assert audit_call["payload_json"]["lifecycle"] == {
        "released_version": "hash-a",
        "deprecated_version": "hash-b",
        "target_status": "released",
    }
    assert audit_call["payload_json"]["release_review"]["status"] == "passed"
    assert audit_call["payload_json"]["release_review"]["dependency_evidence_present"] is True
    assert "storage_key" not in audit_call["payload_json"]
    assert "content_base64" not in str(audit_call["payload_json"])


def test_admin_rollback_missing_version_returns_404(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "missing-version"},
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "skill_version_not_found"


def test_admin_rollback_rejects_inactive_skill_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "disabled",
            "created_by": "dev-admin",
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_inactive"


def test_admin_rollback_rejects_builtin_version_that_cannot_be_materialized(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "current-hash")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "old-hash"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_rollback_accepts_uploaded_version_with_snapshot_files(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_uploaded_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-current",
            "previous_version": "hash-uploaded",
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "dev-admin",
            "promoted_at": None,
        }

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-uploaded-rollback"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_skill_version_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-uploaded"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-uploaded"
    assert [item for item in calls if item[0] == "set_policy"][0][1]["version"] == "hash-uploaded"


def test_admin_rollback_rejects_uploaded_version_with_stale_dependency_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Uploaded QA review",
            "source": {
                "kind": "uploaded",
                "storage_key": "tenants/default/skills/qa-file-reviewer/versions/hash-uploaded/package.zip",
                "files": snapshot_files(),
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("stale uploaded dependency metadata must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-uploaded"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_rollback_rejects_uploaded_version_without_snapshot_files(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Uploaded QA review",
            "source": {
                "kind": "uploaded",
                "storage_key": "tenants/default/skills/qa-file-reviewer/versions/hash-uploaded/package.zip",
                "files": [],
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-uploaded"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_rollback_rejects_uploaded_version_with_missing_dependency_snapshots(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "Uploaded QA review",
            "source": {
                "kind": "uploaded",
                "storage_key": "tenants/default/skills/qa-file-reviewer/versions/hash-uploaded/package.zip",
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            },
            "dependency_ids": ["minimax-docx"],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("unmaterializable dependency snapshots must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-uploaded"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_rollback_rejects_fileless_builtin_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("fileless builtin policy versions must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-a")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_rollback_rejects_builtin_snapshot_with_stale_dependency_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return {
            "skill_id": skill_id,
            "version": version,
            "content_hash": version,
            "description": "QA review",
            "source": {
                "kind": "builtin",
                "asset_dir": "qa-file-reviewer",
                "version": version,
                "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            },
            "dependency_ids": [],
            "status": "active",
            "created_by": "dev-admin",
            "created_at": None,
        }

    async def fail_get_policy(*args, **kwargs):
        raise AssertionError("stale builtin dependency metadata must reject before policy lookup")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fail_get_policy)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_version_not_materializable"


def test_admin_rollback_requires_existing_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return None

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-a")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "rollback_policy_not_available"


def test_admin_rollback_accepts_existing_gray_release_policy(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-b",
            "previous_version": "hash-a",
            "rollout_percent": 50,
            "status": "active",
            "promoted_by": "dev-admin",
            "promoted_at": None,
        }

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-gray-rollback"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_skill_version_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-a")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-a"
    assert response.json()["previous_version"] == "hash-b"
    assert response.json()["rollout_percent"] == 100
    set_call = [item for item in calls if item[0] == "set_policy"][0][1]
    assert set_call["version"] == "hash-a"
    assert set_call["previous_version"] == "hash-b"
    assert set_call["rollout_percent"] == 100


def test_admin_rollback_converges_gray_policy_without_previous_version(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-b",
            "previous_version": None,
            "rollout_percent": 50,
            "status": "active",
            "promoted_by": "dev-admin",
            "promoted_at": None,
        }

    async def fake_set_policy(conn, **kwargs):
        calls.append(("set_policy", kwargs))

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-gray-converge"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.set_skill_release_policy", fake_set_policy)
    monkeypatch.setattr("app.routes.admin_skills.repositories.update_skill_version_status", fake_update_skill_version_status)
    monkeypatch.setattr("app.routes.admin_skills.repositories.append_audit_log", fake_audit)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-b")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-b"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == "hash-b"
    assert response.json()["previous_version"] is None
    assert response.json()["rollout_percent"] == 100
    set_call = [item for item in calls if item[0] == "set_policy"][0][1]
    assert set_call["version"] == "hash-b"
    assert set_call["previous_version"] is None
    assert set_call["rollout_percent"] == 100


def test_admin_rollback_requires_previous_version_target(monkeypatch):
    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    async def fake_get_version(conn, *, skill_id, version):
        return materializable_builtin_qa_version(version)

    async def fake_get_policy(conn, *, tenant_id, skill_id, channel="stable"):
        return {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": "hash-c",
            "previous_version": "hash-b",
            "rollout_percent": 100,
            "status": "active",
            "promoted_by": "dev-admin",
            "promoted_at": None,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr("app.routes.admin_skills._current_builtin_skill_version", lambda skill_id: "hash-a")
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/rollback",
        json={"version": "hash-a"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "rollback_target_not_previous_version"
