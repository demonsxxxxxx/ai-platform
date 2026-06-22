import base64
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def headers(permissions: str = "skill:read,marketplace:read") -> dict[str, str]:
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": "user",
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": "qa",
        "X-AI-Permissions": permissions,
    }


def _source_with_files() -> dict[str, object]:
    skill_md = "---\nname: qa-file-reviewer\ndescription: Review Word documents.\n---\n\n# QA\n"
    guide = "Review guide"
    return {
        "kind": "builtin",
        "tags": ["document"],
        "files": [
            {
                "relative_path": "SKILL.md",
                "content_base64": base64.b64encode(skill_md.encode("utf-8")).decode("ascii"),
                "size_bytes": len(skill_md.encode("utf-8")),
            },
            {
                "relative_path": "references/guide.md",
                "content_base64": base64.b64encode(guide.encode("utf-8")).decode("ascii"),
                "size_bytes": len(guide.encode("utf-8")),
            },
        ],
    }


def _catalog_rows() -> list[dict[str, object]]:
    return [
        {
            "skill_id": "qa-file-reviewer",
            "name": "QA Word Review",
            "version": "hash-a",
            "description": "Review Word documents.",
            "status": "active",
            "visible_to_user": True,
            "source": _source_with_files(),
            "dependency_ids": ["minimax-docx"],
            "created_by": "dev-admin",
            "created_at": "2026-06-22T00:00:00Z",
            "updated_at": "2026-06-22T00:00:00Z",
        }
    ]


def install_route_fakes(monkeypatch) -> list[tuple[str, dict[str, object]]]:
    from app.routes import skills_marketplace

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_list(conn, *, tenant_id, include_disabled=False):
        calls.append(
            (
                "list",
                {
                    "tenant_id": tenant_id,
                    "include_disabled": include_disabled,
                    "conn_type": type(conn).__name__,
                },
            )
        )
        return _catalog_rows()

    async def fake_set_status(conn, *, tenant_id, skill_id, status):
        calls.append(("set_status", {"tenant_id": tenant_id, "skill_id": skill_id, "status": status}))
        row = dict(_catalog_rows()[0])
        row["status"] = status
        return row

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-skill-contract"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(skills_marketplace, "transaction", fake_transaction)
    monkeypatch.setattr(skills_marketplace.repositories, "list_public_skill_catalog", fake_list)
    monkeypatch.setattr(skills_marketplace.repositories, "set_public_skill_enabled", fake_set_status)
    monkeypatch.setattr(skills_marketplace.repositories, "append_audit_log", fake_audit)
    return calls


def test_skills_and_marketplace_read_contracts_project_catalog_and_files(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    skills_response = client.get(
        "/api/skills/?q=qa&tags=document&limit=10",
        headers=headers("skill:read,marketplace:read,skill:write,marketplace:publish"),
    )
    assert skills_response.status_code == 200
    skills_body = skills_response.json()
    assert skills_body["total"] == 1
    assert skills_body["skills"][0] == {
        "skill_name": "qa-file-reviewer",
        "description": "Review Word documents.",
        "tags": ["document"],
        "files": ["SKILL.md", "references/guide.md"],
        "enabled": True,
        "file_count": 2,
        "installed_from": "marketplace",
        "published_marketplace_name": "qa-file-reviewer",
        "created_at": "2026-06-22T00:00:00Z",
        "updated_at": "2026-06-22T00:00:00Z",
        "is_published": True,
        "marketplace_is_active": True,
    }
    assert skills_body["effective_permissions"] == [
        "skill:read",
        "skill:write",
        "marketplace:read",
        "marketplace:publish",
    ]

    detail_response = client.get("/api/skills/qa-file-reviewer", headers=headers())
    assert detail_response.status_code == 200
    assert detail_response.json()["files"] == ["SKILL.md", "references/guide.md"]

    file_response = client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=headers())
    assert file_response.status_code == 200
    assert "Review Word documents." in file_response.json()["content"]

    marketplace_response = client.get("/api/marketplace/", headers=headers())
    assert marketplace_response.status_code == 200
    assert marketplace_response.json()[0]["skill_name"] == "qa-file-reviewer"
    assert marketplace_response.json()[0]["file_count"] == 2

    tags_response = client.get("/api/marketplace/tags", headers=headers())
    assert tags_response.status_code == 200
    assert tags_response.json() == {"tags": ["document"]}

    marketplace_files_response = client.get("/api/marketplace/qa-file-reviewer/files", headers=headers())
    assert marketplace_files_response.status_code == 200
    assert marketplace_files_response.json() == {"files": ["SKILL.md", "references/guide.md"]}

    marketplace_file_response = client.get(
        "/api/marketplace/qa-file-reviewer/files/references%2Fguide.md",
        headers=headers(),
    )
    assert marketplace_file_response.status_code == 200
    assert marketplace_file_response.json()["content"] == "Review guide"

    assert any(name == "list" and payload["tenant_id"] == "default" for name, payload in calls)


def test_skill_and_marketplace_write_contracts_fail_closed_without_permissions(monkeypatch):
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    toggle_response = client.patch(
        "/api/skills/qa-file-reviewer/toggle",
        json={"enabled": False},
        headers=headers("skill:read,marketplace:read"),
    )
    assert toggle_response.status_code == 403
    assert toggle_response.json()["detail"] == "missing_permission:skill:write"

    publish_response = client.post(
        "/api/skills/qa-file-reviewer/publish",
        json={"skill_name": "qa-file-reviewer"},
        headers=headers("skill:read,marketplace:read"),
    )
    assert publish_response.status_code == 403
    assert publish_response.json()["detail"] == "missing_permission:marketplace:publish"

    install_response = client.post(
        "/api/marketplace/qa-file-reviewer/install",
        headers=headers("marketplace:read"),
    )
    assert install_response.status_code == 403
    assert install_response.json()["detail"] == "missing_permission:skill:write"


def test_skill_toggle_and_marketplace_install_update_tenant_availability(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())
    write_headers = headers("skill:read,skill:write,marketplace:read")

    toggle_response = client.patch(
        "/api/skills/qa-file-reviewer/toggle",
        json={"enabled": False},
        headers=write_headers,
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json() == {
        "skill_name": "qa-file-reviewer",
        "enabled": False,
        "message": "Skill disabled",
    }

    install_response = client.post("/api/marketplace/qa-file-reviewer/install", headers=write_headers)
    assert install_response.status_code == 200
    assert install_response.json() == {
        "message": "Skill installed",
        "skill_name": "qa-file-reviewer",
        "file_count": 2,
    }

    update_response = client.post("/api/marketplace/qa-file-reviewer/update", headers=write_headers)
    assert update_response.status_code == 200
    assert update_response.json() == {
        "message": "Skill updated",
        "skill_name": "qa-file-reviewer",
        "file_count": 2,
    }

    status_calls = [payload for name, payload in calls if name == "set_status"]
    assert status_calls == [
        {"tenant_id": "default", "skill_id": "qa-file-reviewer", "status": "disabled"},
        {"tenant_id": "default", "skill_id": "qa-file-reviewer", "status": "active"},
        {"tenant_id": "default", "skill_id": "qa-file-reviewer", "status": "active"},
    ]
