# Issue 183 Skill File Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back `PUT` and `DELETE /api/skills/{skill_name}/files/{file_path}` with tenant/user-scoped durable Skill file overlays while leaving import, direct Marketplace lifecycle, and MCP lifecycle out of scope.

**Architecture:** Add a `user_skill_files` table keyed by tenant, user, skill, and file path. Public Skills routes overlay those user files on top of released Skill snapshots; Marketplace routes continue to expose released snapshot files only. Writes and deletes audit their action and remain permission gated.

**Tech Stack:** FastAPI routes, async repository helpers, PostgreSQL schema SQL, pytest route/repository/schema tests.

## Global Constraints

- Work in an isolated backend worktree; do not edit the dirty frontend checkout.
- Link #183 with non-closing wording only; this slice is not `gate closable`.
- Do not change ZIP/GitHub import storage, direct Marketplace lifecycle, or MCP CRUD in this slice.
- Do not claim `211 verified` until merged main is deployed and route-smoked on 211.
- Every pytest command must use `--basetemp .pytest-tmp\...`.

---

### Task 1: Add RED tests for user Skill file overlays

**Files:**
- Modify: `tests/test_schema.py`
- Modify: `tests/test_repositories.py`
- Modify: `tests/test_skills_marketplace_routes.py`

**Interfaces:**
- Produces expected repository functions:
  - `list_user_skill_file_overlays(conn, tenant_id: str, user_id: str, skill_ids: list[str], include_content: bool = False) -> list[dict[str, Any]]`
  - `upsert_user_skill_file(conn, tenant_id: str, user_id: str, skill_id: str, file_path: str, content_base64: str, size_bytes: int) -> dict[str, Any]`
  - `delete_user_skill_file(conn, tenant_id: str, user_id: str, skill_id: str, file_path: str) -> dict[str, Any]`

- [x] Add schema test asserting `user_skill_files` exists with tenant/user/skill/path uniqueness and lookup index.
- [x] Add repository tests asserting list/upsert/delete SQL shape and params.
- [x] Add route test proving PUT stores a user overlay, GET reads the overlay, and Marketplace file preview still reads the released source file.
- [x] Add route test proving DELETE stores a tombstone and hides the public Skill file while preserving permission gates.
- [x] Run `python -m pytest tests\test_schema.py::test_schema_declares_user_skill_files tests\test_repositories.py::test_user_skill_file_overlay_repository_contracts tests\test_skills_marketplace_routes.py::test_public_skill_file_write_routes_persist_user_overlay tests\test_skills_marketplace_routes.py::test_public_skill_file_delete_marks_user_overlay_deleted -q --basetemp .pytest-tmp\issue183-red`.
- [x] Expected: tests fail because schema, repository helpers, and route behavior are not implemented.

### Task 2: Implement schema and repository helpers

**Files:**
- Modify: `app/schema.sql`
- Modify: `app/repositories.py`

**Interfaces:**
- Consumes test expectations from Task 1.
- Produces durable overlay persistence helpers for routes.

- [x] Add `user_skill_files` table with `id`, `tenant_id`, `user_id`, `skill_id`, `file_path`, `content_base64`, `size_bytes`, `status`, `created_at`, `updated_at`, `unique(tenant_id, user_id, skill_id, file_path)`.
- [x] Add index `idx_user_skill_files_user_skill` on `(tenant_id, user_id, skill_id, status, file_path)`.
- [x] Implement `list_user_skill_file_overlays`.
- [x] Implement `upsert_user_skill_file` with `on conflict` update to `status='active'`, new content, size, and `updated_at=now()`.
- [x] Implement `delete_user_skill_file` with `on conflict` tombstone update to `status='deleted'`.
- [x] Run the RED command again and confirm only route behavior remains failing if repository/schema are green.

### Task 3: Wire public Skills routes to overlays

**Files:**
- Modify: `app/models.py`
- Modify: `app/routes/skills_marketplace.py`
- Modify: `tests/test_skills_marketplace_routes.py`

**Interfaces:**
- Consumes repository helpers from Task 2.
- Produces public route responses:
  - PUT returns `{skill_name, file_path, message, size}`
  - DELETE returns `{skill_name, file_path, message}`

- [x] Add `PublicSkillFileMutationResponse`.
- [x] Teach `_project_files` to apply active/deleted overlay rows after released snapshot projection.
- [x] Fetch overlays for public Skills list/detail/file routes only.
- [x] Keep Marketplace list/detail/file routes on released snapshot files only.
- [x] Replace fail-closed PUT/DELETE with permission-gated upsert/delete plus audit logs.
- [x] Run the RED command and confirm it passes.

### Task 4: Verify changed scope and prepare PR

**Files:**
- Modify as above.
- Modify: `deploy/ai-platform/.env.example`
- Modify: `deploy/ai-platform/docker-compose.yml`

- [x] Run `python -m pytest tests\test_skills_marketplace_routes.py tests\test_repositories.py tests\test_schema.py -q --basetemp .pytest-tmp\issue183-skill-storage`.
- [x] Run `python -m compileall -q app tools scripts`.
- [x] Run `git diff --check`.
- [x] Run `python tools\foundation_alpha_readiness.py --format json` only as a boundary check; do not use it to claim #164 closure.
- [x] Address independent review finding by exposing `PUBLIC_SKILL_FILE_OVERLAY_MAX_BYTES` through deploy defaults and compose environment.
- [ ] Commit with message `feat: back user skill file storage overlay`.
- [ ] Push branch and open PR with `Refs #183`, no closing keyword.
