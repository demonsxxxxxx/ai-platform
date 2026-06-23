# Issue183 GitHub Import Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back public GitHub Skill import preview/install for existing public Skills by storing imported package files as tenant/user-scoped overlays.

**Architecture:** Keep the slice narrow and reuse the existing ZIP package parser plus `user_skill_files` overlay storage. The routes accept only public `https://github.com/{owner}/{repo}` URLs, download the GitHub archive for the requested branch, find one-package Skill roots inside the archive, preview metadata without persistence, and install selected existing Skills into the current user's overlay.

**Tech Stack:** FastAPI, Pydantic, httpx, existing `app.skills.packages` parser, existing public Skills repositories, pytest route tests.

## Global Constraints

- Link to #183 with `Refs #183`; do not use `Closes` or `Fixes`.
- Do not close #164 and do not claim #164 `211 verified`.
- Do not create global built-in Skills, direct Marketplace records, admin Skill versions, or release-policy promotions.
- Do not support private GitHub tokens or arbitrary non-GitHub URLs in this slice.
- Permission checks must run before URL validation and network fetch.
- Marketplace file previews must continue to read released Skill snapshots and exclude current-user overlays.
- Local pytest commands must use workspace-local `--basetemp .pytest-tmp\...`.

---

### Task 1: GitHub Import Parser And Route Contract

**Files:**
- Modify: `app/routes/skills_marketplace.py`
- Modify: `tests/test_skills_marketplace_routes.py`
- Modify: `docs/frontend/skills-marketplace-public-api.md`

**Interfaces:**
- Consumes: existing `parse_skill_package_zip(content) -> ParsedSkillPackage`.
- Produces: `preview_github_skills()` returning `{"repo_url", "branch", "skills"}` and `install_github_skills()` returning `{"message", "installed", "errors"}`.

- [ ] **Step 1: Add failing route tests**

Add tests proving:
- preview denies missing `skill:write` before validating URL;
- preview rejects non-GitHub URLs after permission passes;
- preview uses a monkeypatched downloader and returns one discovered Skill from a GitHub archive without persistence;
- install persists only selected existing Skills as user overlays, enables tenant availability, writes audit evidence, and leaves Marketplace previews on released snapshots;
- install reports a selected unknown Skill as an item error without persisting it.

- [ ] **Step 2: Run the new tests and verify RED**

Run:
`python -m pytest tests\test_skills_marketplace_routes.py::test_public_skill_github_preview_uses_archive_without_persistence tests\test_skills_marketplace_routes.py::test_public_skill_github_install_persists_selected_existing_skill_overlay tests\test_skills_marketplace_routes.py::test_public_skill_github_install_reports_unknown_selected_skill_without_persistence tests\test_skills_marketplace_routes.py::test_public_skill_github_import_validates_permission_before_url -q --basetemp .pytest-tmp\issue183-github-import-red`

Expected: fail because the GitHub routes still return `skill_import_contract_not_backed`.

- [ ] **Step 3: Implement minimal route support**

Implement:
- URL validation for `https://github.com/{owner}/{repo}` only;
- archive URL construction as `https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip`;
- bounded async download using `httpx.AsyncClient`;
- archive extraction that strips the GitHub archive top directory and parses any directory containing `SKILL.md`;
- preview metadata with `path`;
- install of selected packages to current-user overlays using existing repository helpers.

- [ ] **Step 4: Run focused tests and docs checks**

Run:
`python -m pytest tests\test_skills_marketplace_routes.py::test_public_skill_github_preview_uses_archive_without_persistence tests\test_skills_marketplace_routes.py::test_public_skill_github_install_persists_selected_existing_skill_overlay tests\test_skills_marketplace_routes.py::test_public_skill_github_install_reports_unknown_selected_skill_without_persistence tests\test_skills_marketplace_routes.py::test_public_skill_github_import_validates_permission_before_url -q --basetemp .pytest-tmp\issue183-github-import-green`

Then run:
`python -m pytest tests\test_skills_marketplace_routes.py tests\test_skill_packages.py tests\test_repositories.py tests\test_schema.py -q --basetemp .pytest-tmp\issue183-github-import-final`

- [ ] **Step 5: Run compile and diff hygiene**

Run:
`python -m compileall -q app tools scripts`

Run:
`git diff --check`
