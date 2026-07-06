# B4 Skill Release Evidence Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent admin promote from activating a Skill release policy unless the target immutable Skill version has reviewed SBOM-or-signed-package, license, and vulnerability evidence.

**Architecture:** Reuse the existing B4 release readiness validators, but apply them to a target `skill_versions` row. Add a version-scoped helper that reads package files plus external release evidence, fails closed on missing/pending/mismatched evidence, then call it from promote after materialization checks and before policy lookup.

**Tech Stack:** FastAPI route handlers, existing repository functions, `app.skills.release_readiness.build_skill_release_readiness`, pytest route tests.

## Global Constraints

- B4 remains `local partial`; this slice does not close B4, G6, dashboard visual acceptance, dashboard 211 acceptance, or reviewed runtime Skill run on 211.
- Do not add database schema or environment changes in this slice.
- Do not expose raw package bytes, storage keys, `content_base64`, raw `release_decision`, raw version/hash internals beyond existing admin-only surfaces, or executor-private payloads.
- Every local pytest command must include `--basetemp .pytest-tmp`.
- No full-repository pytest.

---

### Task 1: Version-Scoped Release Evidence Helper

**Files:**
- Modify: `app/skills/release_readiness.py`
- Modify: `tests/test_skill_release_readiness.py`

**Interfaces:**
- Produces: `build_skill_version_release_review(skill_version: dict[str, Any], *, skill_release_evidence_root: str | Path | None = None) -> dict[str, Any]`
- Consumes: target `skill_version` rows with `skill_id`, `version`, `content_hash`, and `source.files`.

- [ ] **Step 1: Write failing tests**

Add tests showing the helper rejects missing/pending evidence, rejects mismatched target content hashes, and accepts reviewed external evidence for an uploaded immutable version.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
python -m pytest tests/test_skill_release_readiness.py::test_skill_version_release_review_rejects_missing_or_pending_evidence tests/test_skill_release_readiness.py::test_skill_version_release_review_requires_matching_target_content_hash tests/test_skill_release_readiness.py::test_skill_version_release_review_accepts_reviewed_uploaded_source_files_with_external_evidence -q --basetemp .pytest-tmp
```

Expected: fail because `build_skill_version_release_review` does not exist.

- [ ] **Step 3: Implement minimal helper**

Add a helper that:

- collects `source.files[*].relative_path` from the version row;
- reads external evidence from `docs/release-evidence/skill-release/<skill_id>`;
- validates release review manifest schema, `skill_id`, `skill_content_hash`, review flags, and reviewed evidence contents;
- returns safe blockers without raw file contents or storage keys.

- [ ] **Step 4: Run helper tests**

Run:

```powershell
python -m pytest tests/test_skill_release_readiness.py -q --basetemp .pytest-tmp
```

Expected: all skill release readiness tests pass.

### Task 2: Promote Release Gate

**Files:**
- Modify: `app/routes/admin_skills.py`
- Modify: `tests/test_admin_skills.py`

**Interfaces:**
- Consumes: `build_skill_version_release_review(...)`
- Produces: promote rejection `409 skill_release_review_not_verified`

- [ ] **Step 1: Write failing tests**

Add a route test showing promote returns HTTP 409 with `skill_release_review_not_verified` before release policy lookup when the target version has evidence blockers.

```python
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

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_skills.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_version", fake_get_version)
    monkeypatch.setattr("app.routes.admin_skills.repositories.get_skill_release_policy", fake_get_policy)
    monkeypatch.setattr(
        "app.routes.admin_skills.build_skill_version_release_review",
        lambda version: {
            "status": "blocked",
            "blockers": ["dependency_license_policy_review_not_verified"],
        },
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/admin/skills/qa-file-reviewer/promote",
        json={"version": "hash-b"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill_release_review_not_verified"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
python -m pytest tests/test_admin_skills.py::test_admin_promote_rejects_unreviewed_release_evidence_before_policy_lookup -q --basetemp .pytest-tmp
```

Expected: fails because the route does not yet check version-scoped release readiness.

- [ ] **Step 3: Implement minimal helper and route calls**

In `app/routes/admin_skills.py` import `build_skill_version_release_review` and add:

```python
def _require_reviewed_skill_version_release(skill_version: dict[str, object]) -> None:
    review = build_skill_version_release_review(skill_version)
    if review.get("status") != "passed" or review.get("blockers"):
        raise HTTPException(status_code=409, detail="skill_release_review_not_verified")
```

Call `_require_reviewed_skill_version_release(version)` after `_require_materializable_skill_version(...)` in `admin_promote_skill_version`, before reading or writing release policy state.

- [ ] **Step 4: Make existing positive promote tests explicit**

Patch positive promote tests and policy-specific negative promote tests that should exercise policy behavior rather than evidence failure:

```python
monkeypatch.setattr("app.routes.admin_skills.build_skill_version_release_review", reviewed_skill_version_release)
```

Add helper:

```python
def reviewed_skill_version_release(version):
    return {"status": "passed", "blockers": []}
```

- [ ] **Step 5: Run focused route tests**

Run:

```powershell
python -m pytest tests/test_admin_skills.py -q --basetemp .pytest-tmp
```

Expected: all admin skills tests pass.

### Task 3: Final Verification And Review

**Files:**
- Modify: `docs/operations/b4-skills-management-continuation-status.md`

**Interfaces:**
- Consumes: test evidence from Tasks 1-2.
- Produces: updated phase status.

- [ ] **Step 1: Run changed-scope verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_admin_skills.py tests/test_skill_release_readiness.py tests/test_governance_readiness.py tests/test_verify_governance_runtime_smoke.py -q --basetemp .pytest-tmp
python tools\skill_release_readiness.py --format json
git diff --check
```

Expected: compile and pytest exit 0; readiness still reports top-level `partial_blocked` because actual reviewed evidence, runtime acceptance, and dashboard acceptance remain open.

- [ ] **Step 2: Update phase status**

Update the status document with exact verification evidence and keep the current B4 label as `local partial`.

- [ ] **Step 3: Request sub-agent review substitute**

Dispatch a code reviewer for the branch diff and fix all Critical or Important findings before PR.
