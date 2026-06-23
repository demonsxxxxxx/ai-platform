# Issue 183 ZIP Import Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back public Skills ZIP preview/upload with tenant/user-scoped file overlays while keeping GitHub import, direct Marketplace lifecycle, and MCP lifecycle out of scope.

**Architecture:** Reuse the existing Skill package ZIP parser and `user_skill_files` overlay table from the previous #183 slice. `POST /api/skills/upload/preview` parses a multipart package and returns bounded package metadata without persistence. `POST /api/skills/upload` parses the same package, verifies the Skill exists in the public catalog, writes each package file as a current-user overlay, enables tenant availability, and records one audit log.

**Tech Stack:** FastAPI multipart upload, Pydantic response models, existing repository functions, existing `app.skills.packages` ZIP validation.

## Global Constraints

- Do not close #183 in this slice.
- Do not touch GitHub network import storage.
- Do not implement direct Marketplace publish/edit/admin lifecycle.
- Do not implement MCP server CRUD, credential lifecycle, department enablement, or approval inbox.
- Marketplace file previews continue to read released Skill snapshots, not user overlays.
- All pytest runs must use a workspace-local `--basetemp .pytest-tmp\...`.
- No full-repository pytest.

---

### Task 1: Public ZIP Import Preview And Upload

**Files:**
- Modify: `app/skills/packages.py`
- Modify: `app/models.py`
- Modify: `app/routes/skills_marketplace.py`
- Modify: `tests/test_skill_packages.py`
- Modify: `tests/test_skills_marketplace_routes.py`
- Modify: `docs/frontend/skills-marketplace-public-api.md`

**Interfaces:**
- Consumes: `parse_skill_package_zip(content: bytes, expected_skill_id: str | None = None) -> ParsedSkillPackage`
- Produces: `PublicSkillImportPreviewResponse`, `PublicSkillImportUploadResponse`

- [x] **Step 1: Write failing parser and route tests**

Add tests proving:
- parser can infer `skill_id` from `SKILL.md` when preview does not have a path parameter;
- `/api/skills/upload/preview` returns `skill_count=1` and package file metadata without persistence;
- `/api/skills/upload` persists package files as user overlays and leaves Marketplace preview on released snapshots;
- invalid or unknown package Skill returns stable errors.
- upload routes check `skill:write` before missing multipart-file validation.

- [x] **Step 2: Verify RED**

Run:

```powershell
New-Item -ItemType Directory -Force .pytest-tmp | Out-Null
python -m pytest tests\test_skill_packages.py::test_parse_skill_package_zip_can_infer_skill_name tests\test_skills_marketplace_routes.py::test_public_skill_zip_preview_projects_package_without_persistence tests\test_skills_marketplace_routes.py::test_public_skill_zip_upload_persists_package_as_user_overlay -q --basetemp .pytest-tmp\issue183-zip-import-red
```

Expected: FAIL because the parser requires `expected_skill_id` and routes still return `skill_import_contract_not_backed`.

Observed RED:

- Initial ZIP import tests failed because the parser required `expected_skill_id`
  and upload routes still returned `skill_import_contract_not_backed`.
- Review follow-up test
  `test_public_skill_zip_import_checks_permission_before_missing_file_validation`
  failed with `422` before permission validation, proving the auth-ordering bug.

- [x] **Step 3: Implement minimal parser and route support**

Implementation details:
- Make `expected_skill_id` optional in `parse_skill_package_zip`.
- Validate inferred Skill ids with `assert_safe_id`.
- Add response models for frontend-compatible preview/upload shapes.
- Add upload reader bounded by `MAX_SKILL_PACKAGE_TOTAL_BYTES`.
- Keep permission gates before parsing.
- Ensure uploaded package Skill exists in `list_public_skill_catalog`.
- Persist every parsed file with `upsert_user_skill_file`.
- Enable tenant availability via `set_public_skill_enabled(..., status="active")`.
- Audit `skill.public.zip_imported` with `skill_id`, `content_hash`, `file_count`, `size_bytes`, and department id.
- Keep multipart file optional at the FastAPI validation layer so `skill:write`
  is checked before `skill_package_required`.

- [x] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests\test_skill_packages.py tests\test_skills_marketplace_routes.py tests\test_repositories.py tests\test_schema.py -q --basetemp .pytest-tmp\issue183-zip-import-green
python -m compileall -q app tools scripts
git diff --check
```

Expected: all pass.

Observed GREEN:

```powershell
python -m pytest tests\test_skills_marketplace_routes.py::test_public_skill_zip_import_checks_permission_before_missing_file_validation tests\test_skills_marketplace_routes.py::test_public_skill_zip_preview_projects_package_without_persistence tests\test_skills_marketplace_routes.py::test_public_skill_zip_upload_persists_package_as_user_overlay tests\test_skills_marketplace_routes.py::test_public_skill_zip_upload_rejects_unknown_skill_without_persistence -q --basetemp .pytest-tmp\issue183-zip-import-permission-green
# 4 passed

python -m pytest tests\test_skill_packages.py tests\test_skills_marketplace_routes.py tests\test_repositories.py tests\test_schema.py -q --basetemp .pytest-tmp\issue183-zip-import-final-after-review
# 156 passed

python -m compileall -q app tools scripts
# exit 0

git diff --check
# exit 0
```

- [x] **Step 5: Update docs and PR evidence**

Update `docs/frontend/skills-marketplace-public-api.md` so ZIP preview/upload are listed as backed and GitHub import remains fail-closed.
