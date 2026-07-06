# B4 Skill Upload Version Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the minimal B4 source/API slice for Skill package upload/import, immutable Skill versions, lifecycle transitions, ordinary-user denial, release/rollback audit evidence, and dependency-evidence metadata without repeating PR #329 or PR #331.

**Architecture:** Keep the current `skills`, `skill_versions`, and `skill_release_policies` schema. Store lifecycle status in `skill_versions.status`, immutable package/evidence contract metadata in `skill_versions.source_json`, and release authority in tenant-scoped `skill_release_policies`. Public/ordinary-user routes consume only released or legacy-active versions; admin routes can inspect all versions and drive controlled transitions.

**Tech Stack:** FastAPI routes, Pydantic models in `app/models.py`, repository helpers in `app/repositories.py`, Skill package parsing in `app/skills/packages.py`, release review in `app/skills/release_readiness.py`, pytest with `--basetemp .pytest-tmp`.

## Global Constraints

- B4 remains `local partial`; do not claim B4 complete, `211 verified`, or `gate closable`.
- Do not touch B1, B3, G7, deployment, Docker, or 211 runtime Skill execution.
- Every local pytest command must include `--basetemp .pytest-tmp`.
- Do not run full-repository pytest.
- Do not expose raw package bytes, object storage keys, `content_base64`, raw release decisions, sandbox working directories, or executor-private payloads in public/ordinary-user projections.
- Preserve legacy `active` seed versions as allowed compatibility, but new upload/import versions must enter as `draft`.

---

### Task 1: Lifecycle Contract Helpers

**Files:**
- Create: `app/skills/lifecycle.py`
- Modify: `app/skills/pinning.py`
- Test: `tests/test_skill_lifecycle.py`

**Interfaces:**
- Produces: `SKILL_VERSION_DRAFT`, `SKILL_VERSION_REVIEWED`, `SKILL_VERSION_RELEASED`, `SKILL_VERSION_DISABLED`, `SKILL_VERSION_DEPRECATED`, `SKILL_VERSION_LEGACY_ACTIVE`.
- Produces: `is_admin_materializable_status(status: object) -> bool`.
- Produces: `is_releasable_status(status: object) -> bool`.
- Produces: `is_user_runnable_status(status: object) -> bool`.
- Produces: `normalize_skill_version_status(status: object) -> str`.
- Consumes: no new repository APIs.

- [ ] **Step 1: Write failing lifecycle tests**

Add tests that assert:

```python
from app.skills.lifecycle import (
    is_admin_materializable_status,
    is_releasable_status,
    is_user_runnable_status,
    normalize_skill_version_status,
)
from app.skills.pinning import SkillVersionMaterializationError, build_skill_version_manifest_pin


def test_skill_version_lifecycle_status_helpers_keep_legacy_active_compatible():
    assert normalize_skill_version_status("") == "draft"
    assert is_admin_materializable_status("reviewed") is True
    assert is_admin_materializable_status("released") is True
    assert is_admin_materializable_status("active") is True
    assert is_releasable_status("reviewed") is True
    assert is_releasable_status("released") is True
    assert is_releasable_status("active") is True
    assert is_user_runnable_status("released") is True
    assert is_user_runnable_status("active") is True
    assert is_user_runnable_status("draft") is False
    assert is_user_runnable_status("reviewed") is False
    assert is_user_runnable_status("disabled") is False
    assert is_user_runnable_status("deprecated") is False


def test_skill_version_manifest_pin_accepts_released_and_rejects_draft_disabled_deprecated():
    base = {
        "skill_id": "qa-file-reviewer",
        "version": "hash-reviewed",
        "content_hash": "hash-reviewed",
        "description": "Reviewed Skill",
        "source": {
            "kind": "uploaded",
            "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
        },
        "dependency_ids": [],
    }
    assert build_skill_version_manifest_pin({**base, "status": "released"})["version"] == "hash-reviewed"
    for status in ("draft", "disabled", "deprecated"):
        with pytest.raises(SkillVersionMaterializationError):
            build_skill_version_manifest_pin({**base, "status": status})
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_skill_lifecycle.py -q --basetemp .pytest-tmp`

Expected: FAIL because `app.skills.lifecycle` does not exist and pinning still only recognizes `active`.

- [ ] **Step 3: Implement lifecycle helper and pinning status use**

Create `app/skills/lifecycle.py` with explicit status constants and helper predicates. Update `app/skills/pinning.py` so `_build_skill_version_manifest_pin()` uses `is_admin_materializable_status()` rather than hard-coded `status == "active"`.

- [ ] **Step 4: Run green tests**

Run: `python -m pytest tests/test_skill_lifecycle.py -q --basetemp .pytest-tmp`

Expected: PASS.

### Task 2: Immutable Upload/Import Contract Metadata

**Files:**
- Modify: `app/skills/packages.py`
- Modify: `app/routes/admin_skills.py`
- Modify: `app/models.py`
- Test: `tests/test_skill_packages.py`
- Test: `tests/test_admin_skills.py`

**Interfaces:**
- Produces: `build_skill_package_contract(parsed: ParsedSkillPackage, package_sha256: str, storage_key: str, *, uploaded_by: str) -> dict[str, Any]`.
- Produces: `validate_skill_package_contract(contract: dict[str, Any], *, skill_id: str, content_hash: str) -> dict[str, Any]`.
- Admin upload returns `uploaded.status == "draft"` for new versions.
- Admin upload stores `source.package_contract` and `source.dependency_evidence`.

- [ ] **Step 1: Write failing package contract tests**

Add tests that assert package contract validation rejects mismatched `skill_id`, mismatched `content_hash`, missing `package_sha256`, and unsafe `storage_key`; and that admin upload upserts a `draft` immutable version with `package_contract` and safe `dependency_evidence`.

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_skill_packages.py tests/test_admin_skills.py::test_admin_upload_skill_package_stores_object_and_upserts_skill_version -q --basetemp .pytest-tmp`

Expected: FAIL because contract helpers and draft upload status are not present.

- [ ] **Step 3: Implement contract helpers and upload wiring**

Add package contract helpers to `app/skills/packages.py`. In admin upload, set new uploaded versions to `draft`, store `source_json["package_contract"]`, store `source_json["dependency_evidence"]` with safe booleans/relative evidence file refs, and keep duplicate content-hash uploads insert-only.

- [ ] **Step 4: Run green tests**

Run: `python -m pytest tests/test_skill_packages.py tests/test_admin_skills.py -q --basetemp .pytest-tmp`

Expected: PASS.

### Task 3: Admin Version Lifecycle Transitions

**Files:**
- Modify: `app/models.py`
- Modify: `app/repositories.py`
- Modify: `app/routes/admin_skills.py`
- Test: `tests/test_admin_skills.py`

**Interfaces:**
- Produces repository helper: `update_skill_version_status(conn, *, skill_id: str, version: str, status: str) -> dict[str, Any]`.
- Produces admin route: `POST /api/ai/admin/skills/{skill_id}/versions/{version}/status`.
- Produces request model: `AdminSkillVersionStatusRequest(status: Literal["reviewed", "disabled", "deprecated"])`.
- Status `reviewed` requires `build_skill_version_release_review()` to pass.
- Status `disabled` and `deprecated` are audit-only lifecycle changes and never delete evidence.

- [ ] **Step 1: Write failing admin lifecycle tests**

Add tests for:
- ordinary user cannot call the status route;
- marking a draft version `reviewed` fails with `skill_release_review_not_verified` when review verdict is blocked;
- marking a draft version `reviewed` succeeds when review verdict passes and audit payload includes `from_status`, `to_status`, `version`, and `review_status`;
- marking a version `disabled` succeeds and returns status `disabled`;
- invalid status returns Pydantic 422 or route 400.

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_admin_skills.py -q --basetemp .pytest-tmp`

Expected: FAIL because the status route and repository helper do not exist.

- [ ] **Step 3: Implement repository helper and route**

Add `update_skill_version_status()` with a single `update ... returning` query. Add admin route with `_require_admin`, `_safe_skill_id`, `_safe_version`, review verification for `reviewed`, and audit action `skill_version_status_changed`.

- [ ] **Step 4: Run green tests**

Run: `python -m pytest tests/test_admin_skills.py -q --basetemp .pytest-tmp`

Expected: PASS.

### Task 4: Release/Rollback Lifecycle And Audit Evidence

**Files:**
- Modify: `app/routes/admin_skills.py`
- Modify: `app/repositories.py`
- Test: `tests/test_admin_skills.py`

**Interfaces:**
- Promote requires status `reviewed`, `released`, or legacy `active`, plus existing release review pass.
- Promote updates selected version to `released` and superseded previous current version to `deprecated` unless it is the same version or already disabled.
- Rollback updates rollback target to `released` and superseded current version to `deprecated`.
- Audit payloads include `schema_version`, `lifecycle`, and `release_review`.

- [ ] **Step 1: Write failing release/rollback lifecycle tests**

Add tests that promote rejects `draft` with `skill_version_not_reviewed`, promote marks target `released`, promotes previous current to `deprecated`, rollback marks target `released`, rollback marks superseded current as `deprecated`, and both audit payloads include safe review/evidence summaries without `storage_key` or `content_base64`.

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_admin_skills.py -q --basetemp .pytest-tmp`

Expected: FAIL because promote/rollback currently accept `active` only and do not update version lifecycle status.

- [ ] **Step 3: Implement release/rollback lifecycle status updates**

Replace `_require_active_skill_version()` usage with lifecycle-aware helpers. Add compact audit summary helpers that include review status, blocker count, and dependency evidence presence, but no raw package storage or file bytes. Call `update_skill_version_status()` in promote/rollback transaction after release policy update.

- [ ] **Step 4: Run green tests**

Run: `python -m pytest tests/test_admin_skills.py -q --basetemp .pytest-tmp`

Expected: PASS.

### Task 5: Ordinary-User Denial For Unreviewed And Disabled Skills

**Files:**
- Modify: `app/repositories.py`
- Modify: `app/routes/skills_marketplace.py`
- Modify: `app/routes/chat.py`
- Modify: `app/routes/runs.py`
- Test: `tests/test_skills_marketplace_routes.py`
- Test: `tests/test_chat_routes.py`

**Interfaces:**
- Public catalog/detail/file paths hide or deny disabled tenant availability and selected versions whose lifecycle status is not released or legacy active.
- Chat/run creation propagates repository conflicts for unreviewed/disabled selected versions as 409.
- Admin and write paths that need to re-enable a disabled skill may still query with `include_disabled=True`.

- [ ] **Step 1: Write failing public denial tests**

Add tests that a disabled skill is absent from list and returns `404 skill_not_found` for public detail/file, and that a selected version with status `draft`, `reviewed`, `disabled`, or `deprecated` is hidden from ordinary-user catalog projections.

- [ ] **Step 2: Write failing run/chat denial tests**

Add tests where `repositories.resolve_agent_skill()` sees release policy current version with status `draft` or `disabled` and raises `RepositoryConflictError("skill_version_not_released")`; route assertions expect HTTP 409 with that detail.

- [ ] **Step 3: Run red tests**

Run: `python -m pytest tests/test_skills_marketplace_routes.py tests/test_chat_routes.py -q --basetemp .pytest-tmp`

Expected: FAIL because public routes still use `include_disabled=True` for ordinary reads and repository resolution does not check selected version status.

- [ ] **Step 4: Implement public filters and run resolution status checks**

Update `list_public_skill_catalog()` to join selected `skill_versions.status` and filter selected lifecycle status for ordinary projections. Keep `include_disabled=True` only for write/admin helper paths. Update `resolve_agent_skill()` to select the effective version status and raise `skill_version_not_released` unless it is released or legacy active.

- [ ] **Step 5: Run green tests**

Run: `python -m pytest tests/test_skills_marketplace_routes.py tests/test_chat_routes.py -q --basetemp .pytest-tmp`

Expected: PASS.

### Task 6: Readiness And Closeout

**Files:**
- Modify: `docs/operations/b4-skill-upload-version-lifecycle-status.md`
- Optional modify: `app/skills/release_readiness.py`
- Test: focused test bundle.

**Interfaces:**
- The status document records source/API slice progress and remaining B4 gaps.
- `skill_release_readiness` remains `partial_blocked`; no 211 runtime gap is closed.

- [ ] **Step 1: Update status document**

Mark completed source/API tasks and record exact verification commands.

- [ ] **Step 2: Run final focused verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_skill_lifecycle.py tests/test_skill_packages.py tests/test_admin_skills.py tests/test_skills_marketplace_routes.py tests/test_chat_routes.py tests/test_skill_release_readiness.py tests/test_verify_governance_runtime_smoke.py -q --basetemp .pytest-tmp
python tools\skill_release_readiness.py --format json
git diff --check origin/main..HEAD
```

Expected:
- compile exits 0;
- focused pytest exits 0;
- readiness exits 0 and remains `partial_blocked`;
- diff check exits 0.
