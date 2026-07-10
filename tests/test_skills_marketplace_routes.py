import base64
from contextlib import asynccontextmanager
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories import RepositoryNotFoundError
from app.settings import Settings


def headers(
    permissions: str = "skill:read,marketplace:read",
    *,
    roles: str | None = None,
    department_id: str = "qa",
) -> dict[str, str]:
    effective_roles = roles if roles is not None else ("admin" if "marketplace:admin" in permissions else "user")
    return {
        "X-AI-User-ID": "ordinary",
        "X-AI-Roles": effective_roles,
        "X-AI-Tenant-ID": "default",
        "X-AI-Department-ID": department_id,
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


def _package_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _skill_package_zip(description: str = "Imported review skill.") -> bytes:
    return _package_zip(
        {
            "SKILL.md": (
                "---\n"
                "name: qa-file-reviewer\n"
                f"description: {description}\n"
                "---\n\n"
                "# Imported QA\n"
            ),
            "references/imported.md": "Imported guide",
        }
    )


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


def install_route_fakes(
    monkeypatch,
    *,
    distribution_rows: list[dict[str, object]] | None = None,
) -> list[tuple[str, dict[str, object]]]:
    from app.routes import skills_marketplace

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls: list[tuple[str, dict[str, object]]] = []
    catalog_rows = _catalog_rows()
    skill_versions: dict[tuple[str, str], dict[str, object]] = {
        (
            "qa-file-reviewer",
            "hash-a",
        ): {
            "description": "Review Word documents.",
            "source": _source_with_files(),
            "dependency_ids": ["minimax-docx"],
            "created_by": "dev-admin",
            "created_at": "2026-06-22T00:00:00Z",
        }
    }
    release_policy: dict[str, object] | None = {
        "skill_id": "qa-file-reviewer",
        "channel": "stable",
        "current_version": "hash-a",
        "previous_version": None,
        "rollout_percent": 100,
        "status": "active",
        "promoted_by": "dev-admin",
        "promoted_at": None,
    }
    overlays: dict[tuple[str, str, str, str], dict[str, object]] = {}
    distributions = distribution_rows if distribution_rows is not None else [
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
            "metadata_json": {},
        }
    ]

    async def fake_list(conn, *, tenant_id, include_disabled=False, rollout_key=None):
        calls.append(
            (
                "list",
                {
                    "tenant_id": tenant_id,
                    "include_disabled": include_disabled,
                    "rollout_key": rollout_key,
                    "conn_type": type(conn).__name__,
                },
            )
        )
        rows = []
        for row in catalog_rows:
            projected = dict(row)
            if release_policy and release_policy["skill_id"] == row["skill_id"]:
                version = str(release_policy["current_version"])
                version_row = skill_versions.get((str(row["skill_id"]), version))
                if version_row is not None:
                    projected["version"] = version
                    projected["description"] = version_row["description"]
                    projected["source"] = version_row["source"]
                    projected["dependency_ids"] = version_row["dependency_ids"]
                    projected["created_by"] = version_row["created_by"]
                    projected["created_at"] = version_row["created_at"]
            rows.append(projected)
        return rows

    async def fake_list_distributions(conn, *, tenant_id, capability_kind=None, include_disabled=True):
        calls.append(
            (
                "list_distributions",
                {
                    "tenant_id": tenant_id,
                    "capability_kind": capability_kind,
                    "include_disabled": include_disabled,
                },
            )
        )
        return [
            dict(row)
            for row in distributions
            if capability_kind is None or row.get("capability_kind") == capability_kind
        ]

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        calls.append(
            (
                "get_distribution",
                {
                    "tenant_id": tenant_id,
                    "capability_kind": capability_kind,
                    "capability_id": capability_id,
                },
            )
        )
        for row in distributions:
            if row.get("capability_kind") == capability_kind and row.get("capability_id") == capability_id:
                return dict(row)
        return None

    async def fake_toggle_distribution(conn, *, tenant_id, capability_kind, capability_id, enabled, updated_by):
        calls.append(
            (
                "toggle_distribution",
                {
                    "tenant_id": tenant_id,
                    "capability_kind": capability_kind,
                    "capability_id": capability_id,
                    "enabled": enabled,
                    "updated_by": updated_by,
                },
            )
        )
        for row in distributions:
            if row.get("capability_kind") == capability_kind and row.get("capability_id") == capability_id:
                current = str(row.get("status") or "disabled")
                row["status"] = "active" if (enabled if enabled is not None else current != "active") else "disabled"
                return dict(row)
        raise RepositoryNotFoundError("capability_distribution_not_found")

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
        for catalog_row in catalog_rows:
            if catalog_row["skill_id"] == skill_id:
                catalog_row["status"] = status
                catalog_row["visible_to_user"] = True
                break
        return {
            "skill_id": skill_id,
            "name": "QA Word Review",
            "version": "hash-a",
            "description": "Review Word documents.",
            "status": status,
            "visible_to_user": True,
        }

    async def fake_upsert_skill_version(
        conn,
        *,
        skill_id,
        version,
        content_hash,
        description,
        source_json,
        dependency_ids,
        status="active",
        created_by=None,
    ):
        calls.append(
            (
                "upsert_skill_version",
                {
                    "skill_id": skill_id,
                    "version": version,
                    "content_hash": content_hash,
                    "description": description,
                    "source_json": source_json,
                    "dependency_ids": dependency_ids,
                    "status": status,
                    "created_by": created_by,
                },
            )
        )
        skill_versions[(skill_id, version)] = {
            "description": description,
            "source": source_json,
            "dependency_ids": dependency_ids,
            "created_by": created_by,
            "created_at": "2026-06-23T00:00:00Z",
        }

    async def fake_get_release_policy(conn, *, tenant_id, skill_id, channel="stable"):
        calls.append(
            (
                "get_release_policy",
                {"tenant_id": tenant_id, "skill_id": skill_id, "channel": channel},
            )
        )
        if release_policy and release_policy["skill_id"] == skill_id:
            return dict(release_policy)
        return None

    async def fake_set_release_policy(
        conn,
        *,
        tenant_id,
        skill_id,
        version,
        previous_version,
        promoted_by,
        channel="stable",
        rollout_percent=100,
        status="active",
    ):
        nonlocal release_policy
        calls.append(
            (
                "set_release_policy",
                {
                    "tenant_id": tenant_id,
                    "skill_id": skill_id,
                    "version": version,
                    "previous_version": previous_version,
                    "promoted_by": promoted_by,
                    "channel": channel,
                    "rollout_percent": rollout_percent,
                    "status": status,
                },
            )
        )
        release_policy = {
            "skill_id": skill_id,
            "channel": channel,
            "current_version": version,
            "previous_version": previous_version,
            "rollout_percent": rollout_percent,
            "status": status,
            "promoted_by": promoted_by,
            "promoted_at": None,
        }

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
    monkeypatch.setattr(skills_marketplace.repositories, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(skills_marketplace.repositories, "get_capability_distribution_row", fake_get_distribution)
    monkeypatch.setattr(skills_marketplace.repositories, "toggle_capability_distribution_row", fake_toggle_distribution)
    monkeypatch.setattr(skills_marketplace.repositories, "list_user_skill_file_overlays", fake_list_overlays)
    monkeypatch.setattr(skills_marketplace.repositories, "upsert_user_skill_file", fake_upsert_file)
    monkeypatch.setattr(skills_marketplace.repositories, "delete_user_skill_file", fake_delete_file)
    monkeypatch.setattr(skills_marketplace.repositories, "set_public_skill_enabled", fake_set_status)
    monkeypatch.setattr(skills_marketplace.repositories, "upsert_skill_version", fake_upsert_skill_version)
    monkeypatch.setattr(skills_marketplace.repositories, "get_skill_release_policy", fake_get_release_policy)
    monkeypatch.setattr(skills_marketplace.repositories, "set_skill_release_policy", fake_set_release_policy)
    monkeypatch.setattr(skills_marketplace.repositories, "ensure_user", fake_ensure_user)
    monkeypatch.setattr(skills_marketplace.repositories, "append_audit_log", fake_audit)
    return calls


def admin_bypass_distribution_rows() -> list[dict[str, object]]:
    return [
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "disabled",
            "visible_to_user": False,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
            "metadata_json": {},
        }
    ]


def assert_admin_bypass_audit(
    calls: list[tuple[str, dict[str, object]]],
    *,
    action: str,
) -> dict[str, object]:
    audit = next(
        payload
        for name, payload in calls
        if name == "audit" and payload["action"] == action
    )
    payload_json = audit["payload_json"]
    assert isinstance(payload_json, dict)
    assert {
        key: payload_json[key]
        for key in (
            "capability_kind",
            "capability_id",
            "actor_department_id",
            "department_scope_ids",
            "role_scope_ids",
            "scope_mode",
            "decision_reason",
            "admin_bypass",
        )
    } == {
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
        "actor_department_id": "platform",
        "department_scope_ids": ["qa"],
        "role_scope_ids": ["qa_operator"],
        "scope_mode": "allowlist",
        "decision_reason": "admin_bypass",
        "admin_bypass": True,
    }
    return payload_json


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
    marketplace_body = marketplace_response.json()
    assert marketplace_body["total"] == 1
    assert marketplace_body["skip"] == 0
    assert marketplace_body["limit"] == 50
    assert marketplace_body["available_tags"] == ["document"]
    assert marketplace_body["effective_permissions"] == ["skill:read", "marketplace:read"]
    assert marketplace_body["skills"][0]["skill_name"] == "qa-file-reviewer"
    assert marketplace_body["skills"][0]["file_count"] == 2

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
    assert not any(
        name == "audit" and payload["action"] == "capability_distribution.admin_bypass"
        for name, payload in calls
    )


def test_public_skill_reads_hide_disabled_tenant_availability(monkeypatch):
    from app.routes import skills_marketplace

    class FakeConnection:
        pass

    @asynccontextmanager
    async def fake_transaction():
        yield FakeConnection()

    calls = []

    async def fake_list(conn, *, tenant_id, include_disabled=False, rollout_key=None):
        calls.append({"tenant_id": tenant_id, "include_disabled": include_disabled})
        if not include_disabled:
            return []
        row = dict(_catalog_rows()[0])
        row["status"] = "disabled"
        return [row]

    async def fake_list_overlays(conn, *, tenant_id, user_id, skill_ids, include_content=False):
        return []

    async def fake_list_distributions(conn, *, tenant_id, capability_kind=None, include_disabled=True):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "disabled",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {},
            }
        ]

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return (await fake_list_distributions(conn, tenant_id=tenant_id))[0]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(skills_marketplace, "transaction", fake_transaction)
    monkeypatch.setattr(skills_marketplace.repositories, "list_public_skill_catalog", fake_list)
    monkeypatch.setattr(skills_marketplace.repositories, "list_user_skill_file_overlays", fake_list_overlays)
    monkeypatch.setattr(skills_marketplace.repositories, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(skills_marketplace.repositories, "get_capability_distribution_row", fake_get_distribution)
    client = TestClient(create_app())

    list_response = client.get("/api/skills/", headers=headers())
    assert list_response.status_code == 200
    assert list_response.json()["skills"] == []

    detail_response = client.get("/api/skills/qa-file-reviewer", headers=headers())
    assert detail_response.status_code == 404
    assert detail_response.json()["detail"] == "skill_not_found"

    file_response = client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=headers())
    assert file_response.status_code == 404
    assert file_response.json()["detail"] == "skill_not_found"

    marketplace_response = client.get("/api/marketplace/", headers=headers())
    assert marketplace_response.status_code == 200
    assert marketplace_response.json()["skills"] == []

    marketplace_file_response = client.get(
        "/api/marketplace/qa-file-reviewer/files/SKILL.md",
        headers=headers(),
    )
    assert marketplace_file_response.status_code == 404
    assert marketplace_file_response.json()["detail"] == "skill_not_found"

    assert calls
    assert all(call["include_disabled"] is False for call in calls)


@pytest.mark.parametrize("version_status", ["draft", "reviewed", "disabled", "deprecated"])
def test_public_skill_reads_hide_non_runnable_versions(monkeypatch, version_status):
    from app.routes import skills_marketplace

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    calls = []
    row = dict(_catalog_rows()[0])
    row.update(lifecycle_status="active", version_status=version_status)

    async def fake_list(conn, *, tenant_id, include_disabled=False, rollout_key):
        calls.append((include_disabled, rollout_key))
        return [dict(row)]

    async def fake_list_overlays(conn, *, tenant_id, user_id, skill_ids, include_content=False):
        return []

    async def fake_list_distributions(conn, *, tenant_id, capability_kind=None, include_disabled=True):
        return [
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": [],
                "allowed_roles": [],
                "metadata_json": {},
            }
        ]

    async def fake_get_distribution(conn, *, tenant_id, capability_kind, capability_id):
        return (await fake_list_distributions(conn, tenant_id=tenant_id))[0]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr(skills_marketplace, "transaction", fake_transaction)
    monkeypatch.setattr(skills_marketplace.repositories, "list_public_skill_catalog", fake_list)
    monkeypatch.setattr(skills_marketplace.repositories, "list_user_skill_file_overlays", fake_list_overlays)
    monkeypatch.setattr(skills_marketplace.repositories, "list_capability_distribution_rows", fake_list_distributions)
    monkeypatch.setattr(skills_marketplace.repositories, "get_capability_distribution_row", fake_get_distribution)
    client = TestClient(create_app())

    assert client.get("/api/skills/", headers=headers()).json()["skills"] == []
    assert client.get("/api/skills/qa-file-reviewer", headers=headers()).status_code == 404
    assert client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=headers()).status_code == 404
    assert client.get("/api/marketplace/", headers=headers()).json()["skills"] == []
    assert client.get("/api/marketplace/qa-file-reviewer", headers=headers()).status_code == 404
    assert client.get("/api/marketplace/qa-file-reviewer/files", headers=headers()).status_code == 404
    assert client.get(
        "/api/marketplace/qa-file-reviewer/files/SKILL.md",
        headers=headers(),
    ).status_code == 404
    assert calls and all(include_disabled is False for include_disabled, _ in calls)
    assert all(rollout_key == "ordinary" for _, rollout_key in calls)


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


def test_publish_distribution_allows_authorized_skill(monkeypatch):
    calls = install_route_fakes(
        monkeypatch,
        distribution_rows=[
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": ["qa"],
                "allowed_roles": [],
                "metadata_json": {},
            }
        ],
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/skills/qa-file-reviewer/publish",
        json={"description": "Approved publish preview."},
        headers=headers("marketplace:publish"),
    )

    assert response.status_code == 200
    assert response.json()["description"] == "Approved publish preview."
    assert any(
        name == "audit" and payload["action"] == "skill.public.publish_requested"
        for name, payload in calls
    )


def test_publish_distribution_hides_cross_department_skill(monkeypatch):
    calls = install_route_fakes(
        monkeypatch,
        distribution_rows=[
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": ["qa"],
                "allowed_roles": [],
                "metadata_json": {},
            }
        ],
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/skills/qa-file-reviewer/publish",
        json={"description": "Unauthorized publish preview."},
        headers=headers("marketplace:publish", department_id="rd"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "skill_not_found"
    assert not any(name == "audit" for name, _ in calls)


def test_publish_distribution_hides_disabled_and_missing_rows(monkeypatch):
    distributions = [
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "disabled",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
            "metadata_json": {},
        }
    ]
    calls = install_route_fakes(monkeypatch, distribution_rows=distributions)
    client = TestClient(create_app())
    publish_headers = headers("marketplace:publish")

    disabled = client.post("/api/skills/qa-file-reviewer/publish", json={}, headers=publish_headers)
    distributions.clear()
    missing = client.post("/api/skills/qa-file-reviewer/publish", json={}, headers=publish_headers)

    assert disabled.status_code == 404
    assert disabled.json()["detail"] == "skill_not_found"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "skill_not_found"
    assert not any(name == "audit" for name, _ in calls)


def test_publish_admin_bypass_audit_preserves_distribution_decision(monkeypatch):
    calls = install_route_fakes(monkeypatch, distribution_rows=admin_bypass_distribution_rows())
    client = TestClient(create_app())

    response = client.post(
        "/api/skills/qa-file-reviewer/publish",
        json={"description": "Admin publish preview."},
        headers=headers("marketplace:publish", roles="admin", department_id="platform"),
    )

    assert response.status_code == 200
    payload_json = assert_admin_bypass_audit(calls, action="skill.public.publish_requested")
    assert payload_json["marketplace_skill_name"] == "qa-file-reviewer"


def test_zip_import_admin_bypass_audit_preserves_distribution_decision(monkeypatch):
    calls = install_route_fakes(monkeypatch, distribution_rows=admin_bypass_distribution_rows())
    client = TestClient(create_app())

    response = client.post(
        "/api/skills/upload",
        files={"file": ("qa-file-reviewer.zip", _skill_package_zip(), "application/zip")},
        headers=headers("skill:write", roles="admin", department_id="platform"),
    )

    assert response.status_code == 200
    payload_json = assert_admin_bypass_audit(calls, action="skill.public.zip_imported")
    assert payload_json["file_count"] == 2
    assert not any(name == "toggle_distribution" for name, _ in calls)


def test_github_import_admin_bypass_audit_preserves_distribution_decision(monkeypatch):
    calls = install_route_fakes(monkeypatch, distribution_rows=admin_bypass_distribution_rows())

    async def fake_download(url: str) -> bytes:
        return _github_skill_archive()

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/install",
        json={"repo_url": "https://github.com/example/skills", "skill_names": ["qa-file-reviewer"]},
        headers=headers("skill:write", roles="admin", department_id="platform"),
    )

    assert response.status_code == 200
    payload_json = assert_admin_bypass_audit(calls, action="skill.public.github_imported")
    assert payload_json["repo_url"] == "https://github.com/example/skills"
    assert not any(name == "toggle_distribution" for name, _ in calls)


def test_user_file_overlay_admin_bypass_audits_preserve_distribution_decision(monkeypatch):
    calls = install_route_fakes(monkeypatch, distribution_rows=admin_bypass_distribution_rows())
    client = TestClient(create_app())
    admin_headers = headers("skill:write,skill:delete", roles="admin", department_id="platform")

    update = client.put(
        "/api/skills/qa-file-reviewer/files/notes.md",
        json={"content": "admin notes"},
        headers=admin_headers,
    )
    delete = client.delete(
        "/api/skills/qa-file-reviewer/files/notes.md",
        headers=admin_headers,
    )

    assert update.status_code == 200
    assert delete.status_code == 200
    upsert_payload = assert_admin_bypass_audit(calls, action="skill.public.file_upsert")
    delete_payload = assert_admin_bypass_audit(calls, action="skill.public.file_delete")
    assert upsert_payload["file_path"] == "notes.md"
    assert delete_payload["file_path"] == "notes.md"
    assert not any(name == "toggle_distribution" for name, _ in calls)


def test_marketplace_list_fails_closed_and_projects_openapi_object_shape(monkeypatch):
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    denied_response = client.get("/api/marketplace/", headers=headers("skill:read"))
    assert denied_response.status_code == 403
    assert denied_response.json()["detail"] == "missing_permission:marketplace:read"

    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200
    operation = openapi_response.json()["paths"]["/api/marketplace/"]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/MarketplaceListResponse"}


def test_skill_and_marketplace_reads_resolve_unified_distribution(monkeypatch):
    distributions = [
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa-operator"],
            "metadata_json": {},
        }
    ]
    calls = install_route_fakes(monkeypatch, distribution_rows=distributions)
    client = TestClient(create_app())
    qa_headers = headers(roles="QA-OPERATOR", department_id="qa")

    same_department = client.get("/api/skills/", headers=qa_headers)
    marketplace_same_department = client.get("/api/marketplace/", headers=qa_headers)
    assert same_department.json()["total"] == 1
    assert marketplace_same_department.json()["total"] == 1

    distributions[0]["department_ids"] = []
    tenant_wide = client.get("/api/marketplace/", headers=headers(roles="other", department_id="rd"))
    assert tenant_wide.json()["total"] == 0

    distributions[0]["department_ids"] = ["qa"]
    distributions[0]["allowed_roles"] = []
    cross_department_headers = headers(department_id="rd")
    assert client.get("/api/skills/", headers=cross_department_headers).json()["skills"] == []
    assert client.get("/api/marketplace/qa-file-reviewer", headers=cross_department_headers).status_code == 404
    assert client.get("/api/skills/qa-file-reviewer/files/SKILL.md", headers=cross_department_headers).status_code == 404

    distributions[0]["department_ids"] = []
    distributions[0]["allowed_roles"] = ["qa-operator"]
    assert client.get("/api/marketplace/", headers=headers(roles="viewer")).json()["skills"] == []

    distributions[0]["visible_to_user"] = False
    assert client.get("/api/marketplace/", headers=qa_headers).json()["skills"] == []
    distributions[0]["visible_to_user"] = True
    distributions[0]["status"] = "disabled"
    assert client.get("/api/marketplace/", headers=qa_headers).json()["skills"] == []
    distributions.clear()
    assert client.get("/api/marketplace/", headers=qa_headers).json()["skills"] == []

    distributions.append(
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "active",
            "visible_to_user": False,
            "scope_mode": "allowlist",
            "department_ids": ["qa"],
            "allowed_roles": ["qa_operator"],
            "metadata_json": {},
        }
    )
    admin_response = client.get("/api/marketplace/", headers=headers(roles="admin", department_id="rd"))
    assert admin_response.json()["total"] == 1
    assert any(name == "list_distributions" for name, _ in calls)


def test_skill_and_marketplace_admin_bypass_reads_are_audited(monkeypatch):
    cases = [
        (
            {
                "status": "active",
                "visible_to_user": False,
                "department_ids": [],
                "allowed_roles": [],
            },
            "/api/skills/",
        ),
        (
            {
                "status": "disabled",
                "visible_to_user": True,
                "department_ids": [],
                "allowed_roles": [],
            },
            "/api/marketplace/",
        ),
        (
            {
                "status": "active",
                "visible_to_user": True,
                "department_ids": ["qa"],
                "allowed_roles": [],
            },
            "/api/skills/qa-file-reviewer",
        ),
        (
            {
                "status": "active",
                "visible_to_user": True,
                "department_ids": [],
                "allowed_roles": ["qa-operator"],
            },
            "/api/marketplace/qa-file-reviewer/files/SKILL.md",
        ),
    ]

    for distribution_overrides, path in cases:
        distribution = {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "scope_mode": "allowlist",
            "metadata_json": {},
            **distribution_overrides,
        }
        calls = install_route_fakes(monkeypatch, distribution_rows=[distribution])
        response = TestClient(create_app()).get(
            path,
            headers=headers(roles="admin", department_id="platform"),
        )

        assert response.status_code == 200
        audit = next(
            payload
            for name, payload in calls
            if name == "audit" and payload["action"] == "capability_distribution.admin_bypass"
        )
        assert audit["target_type"] == "skill"
        assert audit["target_id"] == "qa-file-reviewer"
        assert audit["payload_json"]["decision_reason"] == "admin_bypass"
        assert audit["payload_json"]["admin_bypass"] is True


def test_shared_skill_lifecycle_requires_admin_and_marketplace_install_stays_user_local(monkeypatch):
    distributions = [
        {
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "status": "active",
            "visible_to_user": True,
            "scope_mode": "allowlist",
            "department_ids": [],
            "allowed_roles": [],
            "metadata_json": {},
        }
    ]
    calls = install_route_fakes(monkeypatch, distribution_rows=distributions)
    client = TestClient(create_app())

    ordinary = headers("skill:write,skill:delete,marketplace:read")
    assert client.patch("/api/skills/qa-file-reviewer/toggle", json={"enabled": False}, headers=ordinary).status_code == 403
    assert client.delete("/api/skills/qa-file-reviewer", headers=ordinary).status_code == 403
    assert client.post(
        "/api/skills/batch/toggle",
        json={"names": ["qa-file-reviewer"], "enabled": False},
        headers=ordinary,
    ).status_code == 403
    assert client.post("/api/skills/batch/delete", json={"names": ["qa-file-reviewer"]}, headers=ordinary).status_code == 403

    install_response = client.post("/api/marketplace/qa-file-reviewer/install", headers=ordinary)
    update_response = client.post("/api/marketplace/qa-file-reviewer/update", headers=ordinary)
    assert install_response.status_code == 200
    assert update_response.status_code == 200
    assert not any(name == "toggle_distribution" for name, _ in calls)

    distributions[0]["visible_to_user"] = False
    assert client.post("/api/marketplace/qa-file-reviewer/install", headers=ordinary).status_code == 404

    admin = headers("skill:write,skill:delete,marketplace:read", roles="admin")
    assert client.patch("/api/skills/qa-file-reviewer/toggle", json={"enabled": False}, headers=admin).status_code == 200
    assert any(name == "toggle_distribution" for name, _ in calls)


def test_marketplace_install_audit_includes_allowed_distribution_decision(monkeypatch):
    calls = install_route_fakes(
        monkeypatch,
        distribution_rows=[
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "active",
                "visible_to_user": True,
                "scope_mode": "allowlist",
                "department_ids": ["qa"],
                "allowed_roles": ["qa-operator"],
                "metadata_json": {},
            }
        ],
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/marketplace/qa-file-reviewer/install",
        headers=headers("skill:write,marketplace:read", roles="QA-OPERATOR"),
    )

    assert response.status_code == 200
    audit = next(
        payload
        for name, payload in calls
        if name == "audit" and payload["action"] == "marketplace.skill.installed"
    )
    assert audit["payload_json"] == {
        "department_id": "qa",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
        "actor_department_id": "qa",
        "actor_roles": ["qa-operator"],
        "department_scope_ids": ["qa"],
        "role_scope_ids": ["qa-operator"],
        "scope_mode": "allowlist",
        "decision_reason": "allowed",
        "admin_bypass": False,
    }
    assert not any(name == "toggle_distribution" for name, _ in calls)


def test_marketplace_update_audit_includes_admin_bypass_distribution_decision(monkeypatch):
    calls = install_route_fakes(
        monkeypatch,
        distribution_rows=[
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "status": "disabled",
                "visible_to_user": False,
                "scope_mode": "allowlist",
                "department_ids": ["qa"],
                "allowed_roles": ["qa_operator"],
                "metadata_json": {},
            }
        ],
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/marketplace/qa-file-reviewer/update",
        headers=headers("skill:write,marketplace:read", roles="admin", department_id="platform"),
    )

    assert response.status_code == 200
    audit = next(
        payload
        for name, payload in calls
        if name == "audit" and payload["action"] == "marketplace.skill.updated"
    )
    assert audit["payload_json"] == {
        "department_id": "platform",
        "capability_kind": "skill",
        "capability_id": "qa-file-reviewer",
        "actor_department_id": "platform",
        "actor_roles": ["admin"],
        "department_scope_ids": ["qa"],
        "role_scope_ids": ["qa_operator"],
        "scope_mode": "allowlist",
        "decision_reason": "admin_bypass",
        "admin_bypass": True,
    }
    assert not any(name == "toggle_distribution" for name, _ in calls)


def test_skill_toggle_and_marketplace_install_update_tenant_availability(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())
    admin_write_headers = headers("skill:read,skill:write,marketplace:read", roles="admin")
    ordinary_write_headers = headers("skill:read,skill:write,marketplace:read")

    toggle_response = client.patch(
        "/api/skills/qa-file-reviewer/toggle",
        json={"enabled": False},
        headers=admin_write_headers,
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json() == {
        "skill_name": "qa-file-reviewer",
        "enabled": False,
        "message": "Skill disabled",
    }

    restore_response = client.patch(
        "/api/skills/qa-file-reviewer/toggle",
        json={"enabled": True},
        headers=admin_write_headers,
    )
    assert restore_response.status_code == 200

    install_response = client.post("/api/marketplace/qa-file-reviewer/install", headers=ordinary_write_headers)
    assert install_response.status_code == 200
    assert install_response.json() == {
        "message": "Skill installed",
        "skill_name": "qa-file-reviewer",
        "file_count": 2,
    }

    update_response = client.post("/api/marketplace/qa-file-reviewer/update", headers=ordinary_write_headers)
    assert update_response.status_code == 200
    assert update_response.json() == {
        "message": "Skill updated",
        "skill_name": "qa-file-reviewer",
        "file_count": 2,
    }

    distribution_calls = [payload for name, payload in calls if name == "toggle_distribution"]
    assert distribution_calls == [
        {
            "tenant_id": "default",
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "enabled": False,
            "updated_by": "ordinary",
        },
        {
            "tenant_id": "default",
            "capability_kind": "skill",
            "capability_id": "qa-file-reviewer",
            "enabled": True,
            "updated_by": "ordinary",
        },
    ]


def test_public_skill_write_routes_map_missing_skill_to_stable_json_404(monkeypatch):
    install_route_fakes(monkeypatch)

    async def missing_distribution(conn, *, tenant_id, capability_kind, capability_id, enabled, updated_by):
        raise RepositoryNotFoundError("capability_distribution_not_found")

    monkeypatch.setattr(
        "app.routes.skills_marketplace.repositories.toggle_capability_distribution_row",
        missing_distribution,
    )
    client = TestClient(create_app())
    write_headers = headers("skill:write,skill:delete,marketplace:read", roles="admin")

    toggle_response = client.patch(
        "/api/skills/unknown-skill/toggle",
        json={"enabled": False},
        headers=write_headers,
    )
    assert toggle_response.status_code == 404
    assert toggle_response.json()["detail"] == "capability_distribution_not_found"

    delete_response = client.delete("/api/skills/unknown-skill", headers=write_headers)
    assert delete_response.status_code == 404
    assert delete_response.json()["detail"] == "capability_distribution_not_found"


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

    async def fake_list_without_files(conn, *, tenant_id, include_disabled=False, rollout_key=None):
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
    write_headers = headers("skill:write,skill:delete,marketplace:read", roles="admin")

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

    assert [payload["enabled"] for name, payload in calls if name == "toggle_distribution"] == [False, False]


def test_public_skill_zip_preview_projects_package_without_persistence(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    denied = client.post(
        "/api/skills/upload/preview",
        files={"file": ("qa-file-reviewer.zip", _skill_package_zip(), "application/zip")},
        headers=headers("skill:read,marketplace:read"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "missing_permission:skill:write"

    response = client.post(
        "/api/skills/upload/preview",
        files={"file": ("qa-file-reviewer.zip", _skill_package_zip(), "application/zip")},
        headers=headers("skill:write"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["skill_count"] == 1
    assert body["skills"] == [
        {
            "name": "qa-file-reviewer",
            "description": "Imported review skill.",
            "file_count": 2,
            "files": ["SKILL.md", "references/imported.md"],
            "already_exists": True,
        }
    ]
    assert not any(name == "upsert_file" for name, _ in calls)
    assert not any(name == "audit" for name, _ in calls)


def test_public_skill_zip_preview_hides_unauthorized_catalog_existence(monkeypatch):
    distributions: list[dict[str, object]] = []
    calls = install_route_fakes(monkeypatch, distribution_rows=distributions)
    client = TestClient(create_app())
    restricted_rows = [
        {
            "status": "active",
            "visible_to_user": False,
            "department_ids": [],
            "allowed_roles": [],
        },
        {
            "status": "disabled",
            "visible_to_user": True,
            "department_ids": [],
            "allowed_roles": [],
        },
        {
            "status": "active",
            "visible_to_user": True,
            "department_ids": ["qa"],
            "allowed_roles": [],
        },
        {
            "status": "active",
            "visible_to_user": True,
            "department_ids": [],
            "allowed_roles": ["qa-operator"],
        },
    ]

    for overrides in restricted_rows:
        distributions[:] = [
            {
                "capability_kind": "skill",
                "capability_id": "qa-file-reviewer",
                "scope_mode": "allowlist",
                "metadata_json": {},
                **overrides,
            }
        ]
        response = client.post(
            "/api/skills/upload/preview",
            files={"file": ("qa-file-reviewer.zip", _skill_package_zip(), "application/zip")},
            headers=headers("skill:write", roles="viewer", department_id="rd"),
        )
        assert response.status_code == 200
        assert response.json()["skills"][0]["already_exists"] is False

    distributions.clear()
    missing = client.post(
        "/api/skills/upload/preview",
        files={"file": ("qa-file-reviewer.zip", _skill_package_zip(), "application/zip")},
        headers=headers("skill:write", roles="viewer", department_id="rd"),
    )
    assert missing.status_code == 200
    assert missing.json()["skills"][0]["already_exists"] is False
    assert not any(
        name == "audit" and payload["action"] == "capability_distribution.admin_bypass"
        for name, payload in calls
    )


def test_public_skill_zip_preview_admin_bypass_is_audited(monkeypatch):
    calls = install_route_fakes(monkeypatch, distribution_rows=admin_bypass_distribution_rows())
    client = TestClient(create_app())

    response = client.post(
        "/api/skills/upload/preview",
        files={"file": ("qa-file-reviewer.zip", _skill_package_zip(), "application/zip")},
        headers=headers("skill:write", roles="admin", department_id="platform"),
    )

    assert response.status_code == 200
    assert response.json()["skills"][0]["already_exists"] is True
    assert_admin_bypass_audit(calls, action="capability_distribution.admin_bypass")


def test_public_skill_zip_import_checks_permission_before_missing_file_validation(monkeypatch):
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    preview_denied = client.post(
        "/api/skills/upload/preview",
        headers=headers("skill:read,marketplace:read"),
    )
    assert preview_denied.status_code == 403
    assert preview_denied.json()["detail"] == "missing_permission:skill:write"

    upload_denied = client.post(
        "/api/skills/upload",
        headers=headers("skill:read,marketplace:read"),
    )
    assert upload_denied.status_code == 403
    assert upload_denied.json()["detail"] == "missing_permission:skill:write"

    preview_missing = client.post(
        "/api/skills/upload/preview",
        headers=headers("skill:write"),
    )
    assert preview_missing.status_code == 400
    assert preview_missing.json()["detail"] == "skill_package_required"

    upload_missing = client.post(
        "/api/skills/upload",
        headers=headers("skill:write"),
    )
    assert upload_missing.status_code == 400
    assert upload_missing.json()["detail"] == "skill_package_required"


def test_public_skill_zip_upload_persists_package_as_user_overlay(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/skills/upload",
        files={"file": ("qa-file-reviewer.zip", _skill_package_zip(), "application/zip")},
        headers=headers("skill:write,marketplace:read"),
    )
    assert response.status_code == 200
    assert response.json() == {
        "message": "Skills imported",
        "created": [{"name": "qa-file-reviewer", "file_count": 2}],
        "errors": [],
        "skill_count": 1,
    }

    public_file_response = client.get(
        "/api/skills/qa-file-reviewer/files/references/imported.md",
        headers=headers(),
    )
    assert public_file_response.status_code == 200
    assert public_file_response.json()["content"] == "Imported guide"

    marketplace_files_response = client.get("/api/marketplace/qa-file-reviewer/files", headers=headers())
    assert marketplace_files_response.status_code == 200
    assert marketplace_files_response.json() == {"files": ["SKILL.md", "references/guide.md"]}

    upsert_paths = [payload["file_path"] for name, payload in calls if name == "upsert_file"]
    assert upsert_paths == ["SKILL.md", "references/imported.md"]
    assert not any(name == "set_status" for name, _ in calls)
    assert any(
        name == "audit"
        and payload["action"] == "skill.public.zip_imported"
        and payload["target_id"] == "qa-file-reviewer"
        and payload["payload_json"]["file_count"] == 2
        for name, payload in calls
    )


def test_public_skill_zip_upload_rejects_unknown_skill_without_persistence(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    unknown_package = _package_zip(
        {
            "SKILL.md": "---\nname: unknown-skill\ndescription: Unknown skill.\n---\n\n# Unknown\n",
        }
    )

    response = client.post(
        "/api/skills/upload",
        files={"file": ("unknown-skill.zip", unknown_package, "application/zip")},
        headers=headers("skill:write"),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "skill_not_found"
    assert not any(name == "upsert_file" for name, _ in calls)


def _github_archive_zip(files: dict[str, str | bytes]) -> bytes:
    return _package_zip({f"repo-main/{path}": content for path, content in files.items()})


def _github_skill_archive(description: str = "Imported from GitHub.") -> bytes:
    return _github_archive_zip(
        {
            "skills/qa-file-reviewer/SKILL.md": (
                "---\n"
                "name: qa-file-reviewer\n"
                f"description: {description}\n"
                "---\n\n"
                "# GitHub QA\n"
            ),
            "skills/qa-file-reviewer/references/github.md": "GitHub guide",
        }
    )


def test_public_skill_github_import_validates_permission_before_url(monkeypatch):
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    github_preview_denied = client.post(
        "/api/github/preview",
        json={"repo_url": "not-a-url"},
        headers=headers("skill:read,marketplace:read"),
    )
    assert github_preview_denied.status_code == 403
    assert github_preview_denied.json()["detail"] == "missing_permission:skill:write"

    github_install_denied = client.post(
        "/api/github/install",
        json={"repo_url": "not-a-url", "skill_names": ["qa-file-reviewer"]},
        headers=headers("skill:read,marketplace:read"),
    )
    assert github_install_denied.status_code == 403
    assert github_install_denied.json()["detail"] == "missing_permission:skill:write"

    github_preview = client.post(
        "/api/github/preview",
        json={"repo_url": "https://example.com/example/skills", "branch": "main"},
        headers=headers("skill:write"),
    )
    assert github_preview.status_code == 400
    assert github_preview.json()["detail"] == "github_import_repo_url_unsupported"

    github_install = client.post(
        "/api/github/install",
        json={"repo_url": "https://example.com/example/skills", "skill_names": ["qa-file-reviewer"]},
        headers=headers("skill:write"),
    )
    assert github_install.status_code == 400
    assert github_install.json()["detail"] == "github_import_repo_url_unsupported"


def test_public_skill_github_preview_uses_archive_without_persistence(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    downloads: list[str] = []

    async def fake_download(url: str) -> bytes:
        downloads.append(url)
        return _github_skill_archive()

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/preview",
        json={"repo_url": "https://github.com/example/skills", "branch": "feature-branch"},
        headers=headers("skill:write"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "repo_url": "https://github.com/example/skills",
        "branch": "feature-branch",
        "skills": [
            {
                "name": "qa-file-reviewer",
                "path": "skills/qa-file-reviewer",
                "description": "Imported from GitHub.",
            }
        ],
    }
    assert downloads == ["https://codeload.github.com/example/skills/zip/refs/heads/feature-branch"]
    assert not any(name == "upsert_file" for name, _ in calls)
    assert not any(name == "audit" for name, _ in calls)


def test_public_skill_github_preview_falls_back_to_api_when_archive_unavailable(monkeypatch):
    from app.skills.github_import import GitHubImportError

    calls = install_route_fakes(monkeypatch)
    archive_attempts: list[str] = []
    api_attempts: list[tuple[str, str]] = []

    async def fake_download(url: str) -> bytes:
        archive_attempts.append(url)
        raise GitHubImportError(502, "github_import_archive_unavailable")

    async def fake_api_download(repo_url: str, branch: str) -> bytes:
        api_attempts.append((repo_url, branch))
        return _github_skill_archive("Imported through API.")

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    monkeypatch.setattr(
        "app.routes.skills_marketplace._download_github_archive_from_api",
        fake_api_download,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/github/preview",
        json={"repo_url": "https://github.com/example/skills", "branch": "feature-branch"},
        headers=headers("skill:write"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "repo_url": "https://github.com/example/skills",
        "branch": "feature-branch",
        "skills": [
            {
                "name": "qa-file-reviewer",
                "path": "skills/qa-file-reviewer",
                "description": "Imported through API.",
            }
        ],
    }
    assert archive_attempts == ["https://codeload.github.com/example/skills/zip/refs/heads/feature-branch"]
    assert api_attempts == [("https://github.com/example/skills", "feature-branch")]
    assert not any(name == "upsert_file" for name, _ in calls)
    assert not any(name == "audit" for name, _ in calls)


def test_public_skill_github_preview_does_not_fallback_when_archive_is_not_found(monkeypatch):
    from app.skills.github_import import GitHubImportError

    install_route_fakes(monkeypatch)
    api_attempts: list[tuple[str, str]] = []

    async def fake_download(url: str) -> bytes:
        raise GitHubImportError(404, "github_import_archive_not_found")

    async def fake_api_download(repo_url: str, branch: str) -> bytes:
        api_attempts.append((repo_url, branch))
        return _github_skill_archive()

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    monkeypatch.setattr(
        "app.routes.skills_marketplace._download_github_archive_from_api",
        fake_api_download,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/github/preview",
        json={"repo_url": "https://github.com/example/skills", "branch": "missing-branch"},
        headers=headers("skill:write"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "github_import_archive_not_found"
    assert api_attempts == []


def test_public_skill_github_preview_keeps_files_before_skill_md_in_archive(monkeypatch):
    async def fake_download(url: str) -> bytes:
        return _github_archive_zip(
            {
                "skills/qa-file-reviewer/references/first.md": "first file",
                "skills/qa-file-reviewer/SKILL.md": (
                    "---\nname: qa-file-reviewer\ndescription: Ordered package.\n---\n\n# QA\n"
                ),
            }
        )

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/install",
        json={"repo_url": "https://github.com/example/skills", "skill_names": ["qa-file-reviewer"]},
        headers=headers("skill:write"),
    )
    assert response.status_code == 200

    public_file_response = client.get(
        "/api/skills/qa-file-reviewer/files/references/first.md",
        headers=headers(),
    )
    assert public_file_response.status_code == 200
    assert public_file_response.json()["content"] == "first file"


def test_public_skill_github_preview_does_not_absorb_sibling_prefix_paths(monkeypatch):
    async def fake_download(url: str) -> bytes:
        return _github_archive_zip(
            {
                "skills/qa-file-reviewer/SKILL.md": (
                    "---\nname: qa-file-reviewer\ndescription: Primary package.\n---\n\n# QA\n"
                ),
                "skills/qa-file-reviewer-extra/private.md": "must not join primary package",
            }
        )

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/install",
        json={"repo_url": "https://github.com/example/skills", "skill_names": ["qa-file-reviewer"]},
        headers=headers("skill:write"),
    )
    assert response.status_code == 200

    sibling_response = client.get(
        "/api/skills/qa-file-reviewer/files/-extra/private.md",
        headers=headers(),
    )
    assert sibling_response.status_code == 404


def test_public_skill_github_install_persists_selected_existing_skill_overlay(monkeypatch):
    calls = install_route_fakes(monkeypatch)

    async def fake_download(url: str) -> bytes:
        return _github_skill_archive()

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/install",
        json={
            "repo_url": "https://github.com/example/skills",
            "branch": "main",
            "skill_names": ["qa-file-reviewer"],
        },
        headers=headers("skill:write,marketplace:read"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Skills installed",
        "installed": ["qa-file-reviewer"],
        "errors": [],
    }

    public_file_response = client.get(
        "/api/skills/qa-file-reviewer/files/references/github.md",
        headers=headers(),
    )
    assert public_file_response.status_code == 200
    assert public_file_response.json()["content"] == "GitHub guide"

    marketplace_files_response = client.get("/api/marketplace/qa-file-reviewer/files", headers=headers())
    assert marketplace_files_response.status_code == 200
    assert marketplace_files_response.json() == {"files": ["SKILL.md", "references/guide.md"]}

    upsert_paths = [payload["file_path"] for name, payload in calls if name == "upsert_file"]
    assert upsert_paths == ["SKILL.md", "references/github.md"]
    assert not any(name == "set_status" for name, _ in calls)
    assert any(
        name == "audit"
        and payload["action"] == "skill.public.github_imported"
        and payload["target_id"] == "qa-file-reviewer"
        and payload["payload_json"]["repo_url"] == "https://github.com/example/skills"
        and payload["payload_json"]["branch"] == "main"
        for name, payload in calls
    )


def test_public_skill_github_install_reports_unknown_selected_skill_without_persistence(monkeypatch):
    calls = install_route_fakes(monkeypatch)

    async def fake_download(url: str) -> bytes:
        return _github_archive_zip(
            {
                "skills/unknown-skill/SKILL.md": (
                    "---\nname: unknown-skill\ndescription: Unknown GitHub skill.\n---\n\n# Unknown\n"
                ),
            }
        )

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/install",
        json={
            "repo_url": "https://github.com/example/skills",
            "skill_names": ["unknown-skill"],
        },
        headers=headers("skill:write"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Skills installed",
        "installed": [],
        "errors": ["unknown-skill:skill_not_found"],
    }
    assert not any(name == "upsert_file" for name, _ in calls)


def test_public_skill_github_install_rejects_duplicate_selected_names(monkeypatch):
    calls = install_route_fakes(monkeypatch)

    async def fake_download(url: str) -> bytes:
        return _github_skill_archive()

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/install",
        json={
            "repo_url": "https://github.com/example/skills",
            "skill_names": ["qa-file-reviewer", "qa-file-reviewer"],
        },
        headers=headers("skill:write"),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "duplicate_skill_names"
    assert not any(name == "upsert_file" for name, _ in calls)


def test_public_skill_github_preview_rejects_duplicate_discovered_skill_ids(monkeypatch):
    calls = install_route_fakes(monkeypatch)

    async def fake_download(url: str) -> bytes:
        return _github_archive_zip(
            {
                "skills/a/SKILL.md": "---\nname: qa-file-reviewer\ndescription: First.\n---\n\n# First\n",
                "skills/b/SKILL.md": "---\nname: qa-file-reviewer\ndescription: Second.\n---\n\n# Second\n",
            }
        )

    monkeypatch.setattr("app.routes.skills_marketplace._download_github_archive", fake_download)
    client = TestClient(create_app())

    response = client.post(
        "/api/github/preview",
        json={"repo_url": "https://github.com/example/skills"},
        headers=headers("skill:write"),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "github_import_duplicate_skill_id"
    assert not any(name == "upsert_file" for name, _ in calls)


def test_public_skill_direct_marketplace_lifecycle_updates_catalog_release_policy_and_availability(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    direct_marketplace_denied = client.post(
        "/api/marketplace/",
        json={"skill_name": "qa-file-reviewer"},
        headers=headers("marketplace:read"),
    )
    assert direct_marketplace_denied.status_code == 403
    assert direct_marketplace_denied.json()["detail"] == "missing_permission:marketplace:admin"

    direct_marketplace = client.post(
        "/api/marketplace/",
        json={
            "skill_name": "qa-file-reviewer",
            "description": "Published from marketplace admin.",
            "version": "hash-marketplace",
            "tags": ["document", "admin"],
        },
        headers=headers("marketplace:admin"),
    )
    assert direct_marketplace.status_code == 200
    direct_marketplace_body = direct_marketplace.json()
    assert direct_marketplace_body["skill_name"] == "qa-file-reviewer"
    assert direct_marketplace_body["description"] == "Published from marketplace admin."
    assert direct_marketplace_body["tags"] == ["document", "admin"]

    update_response = client.put(
        "/api/marketplace/qa-file-reviewer",
        json={
            "description": "Edited marketplace description.",
            "tags": ["edited"],
        },
        headers=headers("marketplace:admin"),
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Edited marketplace description."
    assert update_response.json()["tags"] == ["edited"]
    assert update_response.json()["version"].startswith("marketplace.")

    deactivate_response = client.patch(
        "/api/marketplace/qa-file-reviewer/activate",
        json={"active": False},
        headers=headers("marketplace:admin"),
    )
    assert deactivate_response.status_code == 200
    assert deactivate_response.json()["is_active"] is False
    assert deactivate_response.json()["file_count"] == 2

    activate_response = client.patch(
        "/api/marketplace/qa-file-reviewer/activate",
        json={"active": True},
        headers=headers("marketplace:admin"),
    )
    assert activate_response.status_code == 200
    assert activate_response.json()["is_active"] is True
    assert activate_response.json()["file_count"] == 2

    delete_response = client.delete("/api/marketplace/qa-file-reviewer", headers=headers("marketplace:admin"))
    assert delete_response.status_code == 200
    assert delete_response.json() == {"message": "Marketplace skill disabled", "skill_name": "qa-file-reviewer"}

    assert any(
        name == "upsert_skill_version"
        and payload["skill_id"] == "qa-file-reviewer"
        and payload["version"] == "hash-marketplace"
        and payload["source_json"]["tags"] == ["document", "admin"]
        for name, payload in calls
    )
    assert not any(name == "update_catalog_version" for name, _ in calls)
    assert any(
        name == "set_release_policy"
        and payload["skill_id"] == "qa-file-reviewer"
        and str(payload["version"]).startswith("marketplace.")
        and payload["previous_version"] == "hash-marketplace"
        for name, payload in calls
    )
    assert [payload["enabled"] for name, payload in calls if name == "toggle_distribution"] == [False, True, False]
    audit_actions = [payload["action"] for name, payload in calls if name == "audit"]
    assert "marketplace.skill.created" in audit_actions
    assert "marketplace.skill.updated" in audit_actions
    assert "marketplace.skill.activation_changed" in audit_actions
    assert "marketplace.skill.disabled" in audit_actions
    update_audit = next(
        payload
        for name, payload in calls
        if name == "audit" and payload["action"] == "marketplace.skill.updated"
    )
    assert update_audit["payload_json"]["previous_version"] == "hash-marketplace"
    assert str(update_audit["payload_json"]["version"]).startswith("marketplace.")
    assert update_audit["payload_json"]["previous_description"] == "Published from marketplace admin."
    assert update_audit["payload_json"]["description"] == "Edited marketplace description."
    assert update_audit["payload_json"]["previous_tags"] == ["document", "admin"]
    assert update_audit["payload_json"]["tags"] == ["edited"]

    read_after_write = client.get("/api/marketplace/qa-file-reviewer", headers=headers("marketplace:read"))
    assert read_after_write.status_code == 404
    assert read_after_write.json()["detail"] == "skill_not_found"


def test_public_skill_direct_marketplace_lifecycle_rejects_mismatch_and_missing_skill(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    mismatch_response = client.put(
        "/api/marketplace/qa-file-reviewer",
        json={"skill_name": "other-skill", "version": "hash-other"},
        headers=headers("marketplace:admin"),
    )
    assert mismatch_response.status_code == 400
    assert mismatch_response.json()["detail"] == "marketplace_skill_name_mismatch"

    missing_response = client.put(
        "/api/marketplace/missing-skill",
        json={"version": "hash-missing"},
        headers=headers("marketplace:admin"),
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "skill_not_found"

    denied_response = client.put(
        "/api/marketplace/missing-skill",
        json={"version": "hash-missing"},
        headers=headers("marketplace:read"),
    )
    assert denied_response.status_code == 403
    assert denied_response.json()["detail"] == "missing_permission:marketplace:admin"
    assert not any(name == "upsert_skill_version" for name, _ in calls)


def test_public_skill_direct_marketplace_lifecycle_rejects_same_version_metadata_conflict(monkeypatch):
    calls = install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.put(
        "/api/marketplace/qa-file-reviewer",
        json={"version": "hash-a", "description": "Cannot overwrite hash-a in place."},
        headers=headers("marketplace:admin"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "marketplace_version_conflict"
    assert not any(name == "upsert_skill_version" for name, _ in calls)


def test_public_skill_direct_marketplace_activation_accepts_frontend_is_active_payload(monkeypatch):
    install_route_fakes(monkeypatch)
    client = TestClient(create_app())

    response = client.patch(
        "/api/marketplace/qa-file-reviewer/activate",
        json={"is_active": False},
        headers=headers("marketplace:admin"),
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False


@pytest.mark.parametrize("selected_previous_status", [None, "draft", "reviewed", "disabled", "deprecated"])
@pytest.mark.parametrize("active", [False, True])
def test_marketplace_activation_uses_non_rollout_admin_response_inside_write_transaction(
    monkeypatch,
    selected_previous_status,
    active,
):
    calls = install_route_fakes(monkeypatch)
    transaction_id = 0

    @asynccontextmanager
    async def recording_transaction():
        nonlocal transaction_id
        transaction_id += 1
        current_id = transaction_id
        calls.append(("tx_enter", {"id": current_id}))
        try:
            yield object()
        except Exception:
            calls.append(("tx_rollback", {"id": current_id}))
            raise
        else:
            calls.append(("tx_commit", {"id": current_id}))

    async def fake_list(conn, *, tenant_id, include_disabled=False, rollout_key=None):
        calls.append(
            (
                "activation_catalog",
                {
                    "include_disabled": include_disabled,
                    "rollout_key": rollout_key,
                    "selected_previous_status": selected_previous_status,
                },
            )
        )
        if rollout_key is not None:
            return []
        return [dict(_catalog_rows()[0])]

    monkeypatch.setattr("app.routes.skills_marketplace.transaction", recording_transaction)
    monkeypatch.setattr("app.routes.skills_marketplace.repositories.list_public_skill_catalog", fake_list)
    client = TestClient(create_app())

    response = client.patch(
        "/api/marketplace/qa-file-reviewer/activate",
        json={"active": active},
        headers=headers("marketplace:admin"),
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is active
    assert [payload for name, payload in calls if name == "activation_catalog"] == [
        {
            "include_disabled": True,
            "rollout_key": None,
            "selected_previous_status": selected_previous_status,
        }
    ]
    assert len([1 for name, _ in calls if name == "tx_enter"]) == 1
    toggle_index = next(index for index, (name, _) in enumerate(calls) if name == "toggle_distribution")
    audit_index = next(
        index
        for index, (name, payload) in enumerate(calls)
        if name == "audit" and payload["action"] == "marketplace.skill.activation_changed"
    )
    list_index = next(index for index, (name, _) in enumerate(calls) if name == "activation_catalog")
    commit_index = next(index for index, (name, _) in enumerate(calls) if name == "tx_commit")
    assert toggle_index < audit_index < list_index < commit_index


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("patch", "/api/marketplace/qa-file-reviewer/activate", {"active": False}),
        ("patch", "/api/marketplace/qa-file-reviewer/activate", {"active": True}),
        (
            "post",
            "/api/marketplace/",
            {"skill_name": "qa-file-reviewer", "description": "Created response failure"},
        ),
        ("put", "/api/marketplace/qa-file-reviewer", {"description": "Updated response failure"}),
    ],
    ids=["deactivate", "activate", "create", "update"],
)
def test_marketplace_writes_roll_back_when_response_model_build_fails(monkeypatch, method, path, payload):
    calls = install_route_fakes(monkeypatch)
    transaction_id = 0

    @asynccontextmanager
    async def recording_transaction():
        nonlocal transaction_id
        transaction_id += 1
        current_id = transaction_id
        calls.append(("tx_enter", {"id": current_id}))
        try:
            yield object()
        except Exception:
            calls.append(("tx_rollback", {"id": current_id}))
            raise
        else:
            calls.append(("tx_commit", {"id": current_id}))

    def fail_response_build(row, principal):
        calls.append(("response_build_failed", {"skill_id": row.get("skill_id")}))
        raise RuntimeError("response_build_failed")

    monkeypatch.setattr("app.routes.skills_marketplace.transaction", recording_transaction)
    monkeypatch.setattr("app.routes.skills_marketplace._marketplace_item", fail_response_build)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = getattr(client, method)(path, json=payload, headers=headers("marketplace:admin"))

    assert response.status_code == 500
    names = [name for name, _ in calls]
    assert names.count("tx_enter") == 1
    assert "tx_rollback" in names
    assert "tx_commit" not in names
    assert names.index("response_build_failed") < names.index("tx_rollback")
    assert sum(name == "audit" for name, _ in calls) == 1


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("patch", "/api/marketplace/qa-file-reviewer/activate", {"active": False}),
        ("patch", "/api/marketplace/qa-file-reviewer/activate", {"active": True}),
        (
            "post",
            "/api/marketplace/",
            {"skill_name": "qa-file-reviewer", "description": "Created missing response"},
        ),
        ("put", "/api/marketplace/qa-file-reviewer", {"description": "Updated missing response"}),
    ],
    ids=["deactivate", "activate", "create", "update"],
)
def test_marketplace_writes_roll_back_when_response_catalog_row_is_missing(monkeypatch, method, path, payload):
    calls = install_route_fakes(monkeypatch)
    transaction_id = 0
    catalog_call_count = 0
    activation_path = path.endswith("/activate")

    @asynccontextmanager
    async def recording_transaction():
        nonlocal transaction_id
        transaction_id += 1
        current_id = transaction_id
        calls.append(("tx_enter", {"id": current_id}))
        try:
            yield object()
        except Exception:
            calls.append(("tx_rollback", {"id": current_id}))
            raise
        else:
            calls.append(("tx_commit", {"id": current_id}))

    async def missing_response_catalog(conn, *, tenant_id, include_disabled=False, rollout_key=None):
        nonlocal catalog_call_count
        catalog_call_count += 1
        calls.append(
            (
                "response_catalog",
                {
                    "call": catalog_call_count,
                    "include_disabled": include_disabled,
                    "rollout_key": rollout_key,
                },
            )
        )
        if not activation_path and catalog_call_count == 1:
            return [dict(_catalog_rows()[0])]
        return []

    monkeypatch.setattr("app.routes.skills_marketplace.transaction", recording_transaction)
    monkeypatch.setattr(
        "app.routes.skills_marketplace.repositories.list_public_skill_catalog",
        missing_response_catalog,
    )
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = getattr(client, method)(path, json=payload, headers=headers("marketplace:admin"))

    assert response.status_code == 404
    names = [name for name, _ in calls]
    assert names.count("tx_enter") == 1
    assert "tx_rollback" in names
    assert "tx_commit" not in names
    assert sum(name == "audit" for name, _ in calls) == 1


def test_public_skill_batch_routes_are_permission_gated_and_report_item_errors(monkeypatch):
    install_route_fakes(monkeypatch)

    async def fail_missing(conn, *, tenant_id, capability_kind, capability_id, enabled, updated_by):
        raise RepositoryNotFoundError(f"{capability_id}_not_found")

    monkeypatch.setattr(
        "app.routes.skills_marketplace.repositories.toggle_capability_distribution_row",
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
        headers=headers("skill:write", roles="admin"),
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json() == {
        "updated": [],
        "errors": [{"name": "missing-skill", "reason": "missing-skill_not_found"}],
    }
