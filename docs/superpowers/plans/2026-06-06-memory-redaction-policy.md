# Memory Redaction Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe configurable memory redaction mode that preserves current behavior by default and adds stricter write-time redaction when policy selects it.

**Architecture:** Store `redaction_mode` on `memory_policies`, project it through public/admin policy responses, and pass it into memory write-time redaction. The redaction helper supports `standard` and `strict`; repository and API validation reject all other modes.

**Tech Stack:** FastAPI, Pydantic v2, async repository layer, PostgreSQL schema SQL, pytest.

---

### Task 1: Add Redaction Mode Contract Tests

**Files:**
- Modify: `tests/test_schema.py`
- Modify: `tests/test_repositories.py`
- Modify: `tests/test_context_routes.py`

- [ ] **Step 1: Add failing schema assertions**

Add assertions to `test_schema_declares_memory_context_tables` requiring:

```python
assert "redaction_mode text not null default 'standard'" in schema
assert "alter table memory_policies add column if not exists redaction_mode" in schema
assert "chk_memory_policies_redaction_mode" in schema
assert "redaction_mode in ('standard', 'strict')" in schema
```

- [ ] **Step 2: Add failing repository policy tests**

Extend memory policy tests so default policies include:

```python
assert policy["redaction_mode"] == "standard"
```

Add an upsert expectation that a `repositories.set_memory_policy` call using the existing policy scope arguments plus `redaction_mode="strict"` persists and returns strict mode. Add invalid-mode and blank-mode tests expecting `RepositoryConflictError("memory_redaction_mode_invalid")`. Add stored dirty-mode tests proving invalid or blank persisted modes project as `strict`.

- [ ] **Step 3: Add failing strict write-time redaction test**

Add a repository test that calls `create_memory_record` with the existing required memory scope arguments plus `redaction_mode="strict"` and raw `sk-strict1234567890abcdef`, `ghp_strict1234567890abcdef`, and a JWT-like string in content/metadata. Assert inserted content and metadata do not contain the raw markers and contain `[redacted-secret]`.

- [ ] **Step 4: Add failing route projection/audit tests**

Extend user/admin policy route tests to send `redaction_mode: "strict"`, assert repository call receives it, response projects it, audit payload includes only `"redaction_mode": "strict"`, and strict policy reasons redact raw provider/JWT markers. Add invalid-mode route tests proving `"redaction_mode": "off"` is rejected before repository writes.

- [ ] **Step 5: Run focused tests and confirm RED**

Run:

```powershell
python -m pytest tests/test_schema.py tests/test_repositories.py tests/test_context_routes.py -q --basetemp .pytest-tmp
```

Expected: FAIL because schema/model/repository/route code does not yet support `redaction_mode`.

### Task 2: Implement Redaction Mode

**Files:**
- Modify: `app/memory_redaction.py`
- Modify: `app/schema.sql`
- Modify: `app/models.py`
- Modify: `app/repositories.py`
- Modify: `app/routes/context.py`

- [ ] **Step 1: Implement helper mode validation**

Add constants for `standard` and `strict`, a strict `normalize_memory_redaction_mode(value)` helper, and strict regexes for raw provider/JWT-like credentials. Keep `redact_memory_text(value)` defaulting to standard through its explicit default argument and add an optional `mode` keyword.

- [ ] **Step 2: Persist policy field**

Add `redaction_mode` to `memory_policies` create table, idempotent alter, cleanup update, and check constraint.

- [ ] **Step 3: Thread policy through repository layer**

Add `redaction_mode` to default policy, row projection, effective select, upsert insert/update/returning, admin list select, and `create_memory_record`. Treat invalid or blank stored modes as `strict`.

- [ ] **Step 4: Thread policy through API layer**

Add `redaction_mode` to `MemoryPolicyRequest`, `_memory_policy_response`, user/admin policy update calls, policy audit payloads, and memory record creation. Redact policy update reasons using the requested policy mode.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run:

```powershell
python -m pytest tests/test_schema.py tests/test_repositories.py tests/test_context_routes.py -q --basetemp .pytest-tmp
```

Expected: PASS.

### Task 3: Verification, Review, and Deployment

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Run local verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp
git diff --check
```

Expected: compileall exit 0, full pytest exit 0, diff check exit 0.

- [ ] **Step 2: Request inherited-configuration review**

Dispatch code review if a capable subagent tool is available. If model/reasoning fields are not exposed, record review as inherited-configuration and do not claim an explicit model gate.

- [ ] **Step 3: Apply accepted review feedback and rerun verification**

Fix Critical and Important findings, then rerun focused tests and the relevant full verification.

- [ ] **Step 4: Commit and push**

Commit with:

```powershell
git add app tests docs
git commit -m "feat: add memory redaction policy mode"
git push origin main
```

- [ ] **Step 5: Deploy and smoke on 211**

Sync source to the repo-level 211 backend target defined by `AGENTS.md`, build or runtime-rebase on Docker-capable 211, recreate API/worker with repo-local compose, verify `/api/ai/health` through API and frontend proxy, set strict smoke policy, create strict smoke memory record, verify no raw marker leakage, then delete smoke rows and update roadmap evidence.
