import base64
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import RepositoryNotFoundError
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
    overlays: dict[tuple[str, str, str, str], dict[str, object]] = {}

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

    async def fake_list_overlays(conn, *, tenant_id, user_id, skill_ids, include_content=False):
        calls.append(
            (
                "list_overlays",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "skill_ids": list(skill_ids),
                    "include_content": include_content,
                },
            )
        )
        return [
            dict(row)
            for (row_tenant, row_user, skill_id, _), row in overlays.items()
            if row_tenant == tenant_id and row_user == user_id and skill_id in set(skill_ids)
        ]

    async def fake_upsert_file(conn, *, tenant_id, user_id, skill_id, file_path, content_base64, size_bytes):
        calls.append(
            (
                "upsert_file",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "skill_id": skill_id,
                    "file_path": file_path,
                    "content_base64": content_base64,
                    "size_bytes": size_bytes,
                },
            )
        )
        row = {
            "skill_id": skill_id,
            "file_path": file_path,
            "content_base64": content_base64,
            "size_bytes": size_bytes,
            "status": "active",
        }
        overlays[(tenant_id, user_id, skill_id, file_path)] = row
        return dict(row)

    async def fake_delete_file(conn, *, tenant_id, user_id, skill_id, file_path):
        calls.append(
            (
                "delete_file",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "skill_id": skill_id,
                    "file_path": file_path,
                },
            )
        )
        row = {
            "skill_id": skill_id,
            "file_path": file_path,
            "content_base64": "",
            "size_bytes": 0,
            "status": "deleted",
        }
        overlays[(tenant_id, user_id, skill_id, file_path)] = row
        return dict(row)

    async def fake_set_status(conn, *, tenant_id, skill_id, status):
        calls.append(("set_status", {"tenant_id": tenant_id, "skill_id": skill_id, "status": status}))
        row = dict(_catalog_rows()[0])
        row["status"] = status
        return row

    async def fake_ensure_user(conn, *, tenant_id, user_id, display_name=None):
        calls.append(
            (
                "ensure_user",
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "display_name": display_name,
                },
            )
        )

    async def fake_audit(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-skill-contract"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(skills_marketplace, "transaction", fake_transaction)
    monkeypatch.setattr(skills_marketplace.repositories, "list_public_skill_catalog", fake_list)
    monkeypatch.setattr(skills_marketplace.repositories, "list_user_skill_file_overlays", fake_list_overlays)
    monkeypatch.setattr(skills_marketplace.repositories, "upsert_user_skill_file", fake_upsert_file)
    monkeypatch.setattr(skills_marketplace.repositories, "delete_user_skill_file", fake_delete_file)
    monkeypatch.setattr(skills_marketplace.repositories, "set_public_skill_enabled", fake_set_status)
    monkeypatch.setattr(skills_marketplace.repositories, "ensure_user", fake_ensure_user)
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

    bodyless_toggle_response = client.patch(
        "/api/skills/qa-file-reviewer/toggle",
        headers=headers("skill:read,marketplace:read"),
    )
    assert bodyless_toggle_response.status_code == 403
    assert bodyless_toggle_response.json()["detail"] == "missing_permission:skill:write"

    toggle_response = client.patch(
        "/api/skills/qa-file-reviewer/toggle",
        json={"enabled": False},
        headers=headers("skill:read,marketplace:read"),
    )
    assert toggle_response.status_code == 403
    assert toggle_response.json()["detail"] == "missing_permission:skill:write"

    bodyless_publish_response = client.post(
        "/api/skills/qa-file-reviewer/publish",
        headers=headers("skill:read,marketplace:read"),
    )
    assert bodyless_publish_response.status_code == 403
    assert bodyless_publish_response.json()["detail"] == "missing_permission:marketplace:publish"

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


def test_public_skill_write_routes_map_missing_skill_to_stable_json_404(monkeypatch):
    install_route_fakes(monkeypatch)

    async def missing_skill(conn, *, tenant_id, skill_id, status):
        raise RepositoryNotFoundError("workbench_skill_not_found")

    monkeypatch.setattr(
        "app.routes.skills_marketplace.repositories.set_public_skill_enabled",
        missing_skill,
    )
    client = TestClient(create_app())
    write_headers = headers("skill:write,skill:delete,marketplace:read")

    toggle_response = client.patch(
        "/api/skills/unknown-skill/toggle",
        json={"enabled": False},
        headers=write_headers,
    )
    assert toggle_response.status_code == 404
    assert toggle_response.json()["detail"] == "workbench_skill_not_found"

    delete_response = client.delete("/api/skills/unknown-skill", headers=write_headers)
    assert delete_response.status_code == 404
    assert delete_response.json()["detail"] == "workbench_skill_not_found"


def test_public_skill_file_write_routes_persist_user_overlay(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    bodyless_put_denied = client.put(
        "/api/skills/qa-file-reviewer/files/SKILL.md",
        headers=headers("skill:read"),
    )
    assert bodyless_put_denied.status_code == 403
    assert bodyless_put_denied.json()["detail"] == "missing_permission:skill:write"

    put_denied = client.put(
        "/api/skills/qa-file-reviewer/files/SKILL.md",
        json={"content": "updated"},
        headers=headers("skill:read"),
    )
    assert put_denied.status_code == 403
    assert put_denied.json()["detail"] == "missing_permission:skill:write"

    put_response = client.put(
        "/api/skills/qa-file-reviewer/files/SKILL.md",
        json={"content": "updated"},
        headers=headers("skill:write"),
    )
    assert put_response.status_code == 200
    assert put_response.json() == {
        "skill_name": "qa-file-reviewer",
        "file_path": "SKILL.md",
        "message": "Skill file saved",
        "size": len("updated"),
    }

    public_file_response = client.get(
        "/api/skills/qa-file-reviewer/files/SKILL.md",
        headers=headers(),
    )
    assert public_file_response.status_code == 200
    assert public_file_response.json()["content"] == "updated"

    marketplace_file_response = client.get(
        "/api/marketplace/qa-file-reviewer/files/SKILL.md",
        headers=headers(),
    )
    assert marketplace_file_response.status_code == 200
    assert "Review Word documents." in marketplace_file_response.json()["content"]

    assert any(name == "upsert_file" and payload["file_path"] == "SKILL.md" for name, payload in calls)
    assert calls.index(next(call for call in calls if call[0] == "ensure_user")) < calls.index(
        next(call for call in calls if call[0] == "upsert_file")
    )
    list_overlay_calls = [payload for name, payload in calls if name == "list_overlays"]
    assert any(payload["include_content"] is True for payload in list_overlay_calls)
    assert any(
        name == "audit" and payload["action"] == "skill.public.file_upsert" and payload["target_id"] == "qa-file-reviewer"
        for name, payload in calls
    )


def test_public_skill_file_delete_marks_user_overlay_deleted(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    delete_denied = client.delete(
        "/api/skills/qa-file-reviewer/files/SKILL.md",
        headers=headers("skill:write"),
    )
    assert delete_denied.status_code == 403
    assert delete_denied.json()["detail"] == "missing_permission:skill:delete"

    delete_response = client.delete("/api/skills/qa-file-reviewer/files/SKILL.md", headers=headers("skill:delete"))
    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "skill_name": "qa-file-reviewer",
        "file_path": "SKILL.md",
        "message": "Skill file deleted",
        "size": None,
    }

    detail_response = client.get("/api/skills/qa-file-reviewer", headers=headers())
    assert detail_response.status_code == 200
    assert detail_response.json()["files"] == ["references/guide.md"]

    public_file_response = client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=headers())
    assert public_file_response.status_code == 404
    assert public_file_response.json()["detail"] == "skill_file_not_found"

    marketplace_file_response = client.get(
        "/api/marketplace/qa-file-reviewer/files/SKILL.md",
        headers=headers(),
    )
    assert marketplace_file_response.status_code == 200
    assert "Review Word documents." in marketplace_file_response.json()["content"]

    assert any(name == "delete_file" and payload["file_path"] == "SKILL.md" for name, payload in calls)
    assert calls.index(next(call for call in calls if call[0] == "ensure_user")) < calls.index(
        next(call for call in calls if call[0] == "delete_file")
    )
    assert any(
        name == "audit" and payload["action"] == "skill.public.file_delete" and payload["target_id"] == "qa-file-reviewer"
        for name, payload in calls
    )


def test_public_skill_overlay_is_scoped_to_principal_user_and_tenant(monkeypatch):
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    write_response = client.put(
        "/api/skills/qa-file-reviewer/files/SKILL.md",
        json={"content": "user a overlay"},
        headers=headers("skill:write"),
    )
    assert write_response.status_code == 200

    same_user_response = client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=headers())
    assert same_user_response.status_code == 200
    assert same_user_response.json()["content"] == "user a overlay"

    other_user_headers = {
        **headers(),
        "X-AI-User-ID": "other-user",
    }
    other_user_response = client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=other_user_headers)
    assert other_user_response.status_code == 200
    assert "Review Word documents." in other_user_response.json()["content"]

    other_tenant_headers = {
        **headers(),
        "X-AI-Tenant-ID": "tenant-b",
    }
    other_tenant_response = client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=other_tenant_headers)
    assert other_tenant_response.status_code == 200
    assert "Review Word documents." in other_tenant_response.json()["content"]


def test_public_skill_overlay_keeps_fallback_skill_md_when_snapshot_has_no_files(monkeypatch):
    calls = install_route_fakes(monkeypatch)

    async def fake_list_without_files(conn, *, tenant_id, include_disabled=False):
        calls.append(("list_without_files", {"tenant_id": tenant_id, "include_disabled": include_disabled}))
        row = dict(_catalog_rows()[0])
        row["source"] = {"kind": "builtin", "tags": ["document"]}
        return [row]

    monkeypatch.setattr(
        "app.routes.skills_marketplace.repositories.list_public_skill_catalog",
        fake_list_without_files,
    )
    client = TestClient(create_app())

    put_response = client.put(
        "/api/skills/qa-file-reviewer/files/notes.md",
        json={"content": "user notes"},
        headers=headers("skill:write"),
    )
    assert put_response.status_code == 200

    detail_response = client.get("/api/skills/qa-file-reviewer", headers=headers())
    assert detail_response.status_code == 200
    assert detail_response.json()["files"] == ["SKILL.md", "notes.md"]

    fallback_response = client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=headers())
    assert fallback_response.status_code == 200
    assert "# qa-file-reviewer" in fallback_response.json()["content"]


def test_public_skill_file_write_rejects_oversized_overlay_before_persistence(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    from app.routes import skills_marketplace

    monkeypatch.setattr(
        skills_marketplace,
        "get_settings",
        lambda: Settings(frontend_poc_auth_enabled=True, public_skill_file_overlay_max_bytes=4),
    )
    client = TestClient(create_app())

    response = client.put(
        "/api/skills/qa-file-reviewer/files/SKILL.md",
        json={"content": "too-large"},
        headers=headers("skill:write"),
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "skill_file_too_large"
    assert not any(name == "upsert_file" for name, _ in calls)


def test_public_skill_batch_routes_map_to_tenant_availability(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())
    write_headers = headers("skill:write,skill:delete,marketplace:read")

    toggle_response = client.post(
        "/api/skills/batch/toggle",
        json={"names": ["qa-file-reviewer"], "enabled": False},
        headers=write_headers,
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json() == {"updated": ["qa-file-reviewer"], "errors": []}

    delete_response = client.post(
        "/api/skills/batch/delete",
        json={"names": ["qa-file-reviewer"]},
        headers=write_headers,
    )
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": ["qa-file-reviewer"], "errors": []}

    status_calls = [payload for name, payload in calls if name == "set_status"]
    assert status_calls == [
        {"tenant_id": "default", "skill_id": "qa-file-reviewer", "status": "disabled"},
        {"tenant_id": "default", "skill_id": "qa-file-reviewer", "status": "disabled"},
    ]


def test_public_skill_import_and_direct_marketplace_routes_are_permission_gated_then_fail_closed(monkeypatch):
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    zip_preview_denied = client.post(
        "/api/skills/upload/preview",
        headers=headers("skill:read,marketplace:read"),
    )
    assert zip_preview_denied.status_code == 403
    assert zip_preview_denied.json()["detail"] == "missing_permission:skill:write"

    zip_preview = client.post(
        "/api/skills/upload/preview",
        headers=headers("skill:write"),
    )
    assert zip_preview.status_code == 409
    assert zip_preview.json()["detail"] == "skill_import_contract_not_backed"

    zip_upload = client.post(
        "/api/skills/upload",
        headers=headers("skill:write"),
    )
    assert zip_upload.status_code == 409
    assert zip_upload.json()["detail"] == "skill_import_contract_not_backed"

    github_preview_denied = client.post(
        "/api/github/preview",
        json={"repo_url": "https://github.com/example/skills"},
        headers=headers("skill:read,marketplace:read"),
    )
    assert github_preview_denied.status_code == 403
    assert github_preview_denied.json()["detail"] == "missing_permission:skill:write"

    github_preview = client.post(
        "/api/github/preview",
        json={"repo_url": "https://github.com/example/skills", "branch": "main"},
        headers=headers("skill:write"),
    )
    assert github_preview.status_code == 409
    assert github_preview.json()["detail"] == "skill_import_contract_not_backed"

    github_install = client.post(
        "/api/github/install",
        json={"repo_url": "https://github.com/example/skills", "skill_names": ["qa-file-reviewer"]},
        headers=headers("skill:write"),
    )
    assert github_install.status_code == 409
    assert github_install.json()["detail"] == "skill_import_contract_not_backed"

    direct_marketplace_denied = client.post(
        "/api/marketplace/",
        json={"skill_name": "qa-file-reviewer"},
        headers=headers("marketplace:read"),
    )
    assert direct_marketplace_denied.status_code == 403
    assert direct_marketplace_denied.json()["detail"] == "missing_permission:marketplace:admin"

    direct_marketplace = client.post(
        "/api/marketplace/",
        json={"skill_name": "qa-file-reviewer"},
        headers=headers("marketplace:admin"),
    )
    assert direct_marketplace.status_code == 409
    assert direct_marketplace.json()["detail"] == "marketplace_direct_write_contract_not_backed"

    for method, path in [
        ("put", "/api/marketplace/qa-file-reviewer"),
        ("patch", "/api/marketplace/qa-file-reviewer/activate"),
        ("delete", "/api/marketplace/qa-file-reviewer"),
    ]:
        if method == "delete":
            response = client.delete(path, headers=headers("marketplace:admin"))
        else:
            response = getattr(client, method)(
                path,
                json={"skill_name": "qa-file-reviewer"},
                headers=headers("marketplace:admin"),
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "marketplace_direct_write_contract_not_backed"


def test_public_skill_batch_routes_are_permission_gated_and_report_item_errors(monkeypatch):
    install_route_fakes(monkeypatch)

    async def fail_missing(conn, *, tenant_id, skill_id, status):
        raise RepositoryNotFoundError(f"{skill_id}_not_found")

    monkeypatch.setattr(
        "app.routes.skills_marketplace.repositories.set_public_skill_enabled",
        fail_missing,
    )
    client = TestClient(create_app())

    delete_denied = client.post(
        "/api/skills/batch/delete",
        json={"names": ["qa-file-reviewer"]},
        headers=headers("skill:write"),
    )
    assert delete_denied.status_code == 403
    assert delete_denied.json()["detail"] == "missing_permission:skill:delete"

    toggle_denied = client.post(
        "/api/skills/batch/toggle",
        json={"names": ["qa-file-reviewer"], "enabled": False},
        headers=headers("skill:read"),
    )
    assert toggle_denied.status_code == 403
    assert toggle_denied.json()["detail"] == "missing_permission:skill:write"

    toggle_response = client.post(
        "/api/skills/batch/toggle",
        json={"names": ["missing-skill"], "enabled": False},
        headers=headers("skill:write"),
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json() == {
        "updated": [],
        "errors": [{"name": "missing-skill", "reason": "missing-skill_not_found"}],
    }
