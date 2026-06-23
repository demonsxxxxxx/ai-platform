# Issue 205 Workbench Projections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe backend projections for governed post-login `/users`, `/settings`, `/feedback`, and `/notifications` workbench routes.

**Architecture:** Implement one focused FastAPI router for these legacy frontend paths under `/api`, with explicit public/read and admin/write projections. Keep the slice projection-only: write endpoints queue audited lifecycle requests and never persist or project secrets, raw system settings, gateway keys, or private payloads.

**Tech Stack:** FastAPI, Pydantic v2 models, existing `AuthPrincipal` trusted-header auth, existing audit-log repository helper, pytest `TestClient`.

## Global Constraints

- Link to #205 with `Refs #205`; do not auto-close until merged, reviewed, verified, and evidence is posted.
- Do not claim `211 verified`, `gate closable`, or #164 closure from local/PR evidence.
- Ordinary-user routes must return safe public/degraded projections, not legacy admin payloads and not 422 surprises.
- Admin write routes must check permissions before request-body validation and return `403` for unauthorized users.
- Validation errors for write payloads must not echo submitted secret values.
- Local pytest commands must use `--basetemp .pytest-tmp\...`.

---

### Task 1: Projection Route Contract

**Files:**
- Create: `tests/test_workbench_projection_routes.py`
- Create: `app/routes/workbench_projections.py`
- Modify: `app/models.py`
- Modify: `app/main.py`
- Modify: `app/routes/auth.py`
- Modify: `app/routes/lambchat_compat.py`
- Modify: `tools/frontend_projection_audit.py`
- Modify: `tests/test_frontend_projection_audit.py`
- Modify: `tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `AuthPrincipal`, `require_principal`, `is_ai_admin`, `transaction`, `repositories.append_audit_log`, `assert_safe_id`.
- Produces: FastAPI routes under `/api/users`, `/api/settings`, `/api/feedback`, and `/api/notifications` with safe read/admin write behavior.

- [x] **Step 1: Write failing tests**

Cover:
- ordinary `/api/users/`, `/api/settings/`, `/api/feedback/`, `/api/notifications/active`;
- admin writes for user lifecycle, settings updates/reset, feedback assignment/closure/labels, notification create/update/delete/replay;
- 403 before validation for ordinary users;
- no secret value echo in responses, audit payloads, or validation errors;
- projection audit remaps these prefixes out of legacy policy-required routes.

- [x] **Step 2: Run RED**

Run: `python -m pytest tests\test_workbench_projection_routes.py -q --basetemp .pytest-tmp\issue205-red`

Expected: fail because `app.routes.workbench_projections` is not registered and/or route behavior is missing.

Observed: `6 failed` because `app.routes.workbench_projections` was not available yet.

- [x] **Step 3: Implement minimal projection router and models**

Add explicit response/request models and route handlers. Use static safe projections plus redacted audit log entries. Do not add database schema or persistence.

- [x] **Step 4: Run focused GREEN**

Run: `python -m pytest tests\test_workbench_projection_routes.py -q --basetemp .pytest-tmp\issue205-green`

Expected: pass.

Observed final: `python -m pytest tests\test_workbench_projection_routes.py -q --basetemp .pytest-tmp\issue205-routes-final` -> `6 passed`.

- [x] **Step 5: Run affected tests and integration checks**

Run:
- `python -m pytest tests\test_workbench_projection_routes.py tests\test_auth_routes.py tests\test_frontend_projection_audit.py -q --basetemp .pytest-tmp\issue205-targeted`
- `python -m compileall -q app tools scripts`
- `git diff --check`
- `python tools\frontend_projection_audit.py --format json`

Expected: targeted tests pass; compile/diff checks exit 0; frontend projection audit still reports no ordinary-user reachable legacy routes and these route prefixes are safe/admin projected.

Observed final after review fixes:
- `python -m pytest tests\test_workbench_projection_routes.py tests\test_auth_routes.py tests\test_frontend_projection_audit.py -q --basetemp .pytest-tmp\issue205-targeted-reviewfix-final2` -> `48 passed, 2 warnings`.
- `python -m compileall -q app tools scripts` -> exit 0.
- `git diff --check` -> exit 0.
- `python tools\frontend_projection_audit.py --format json` -> exit 0; status `pass_with_policy_gaps`; active ordinary-user reachable legacy routes `0`; `/api/users`, `/api/settings`, and `/api/notifications/active` in safe public route inventory; `/api/notifications/admin` in safe admin route inventory.

- [x] **Step 6: Review, PR, and evidence**

Run read-only sub-agent review. Fix or explicitly reject findings with evidence. Open a PR with `Refs #205`, local verification, review substitute status, and no 211/#164 closure claims.

Observed review:
- Read-only sub-agent review found one critical settings write response leak for nested secret-bearing values under non-secret-looking keys.
- Fixed by returning `[redacted]` for every settings write response and adding a regression test for `PUT /api/settings/ui.locale` with nested `token_secret`.
- Also split frontend projection audit inventory into safe public and safe admin route buckets so `/api/notifications/admin` is not labelled as public.

## Self-Review

- Spec coverage: `/users`, `/settings`, `/feedback`, and `/notifications` each have public/degraded read and admin/write coverage.
- Placeholder scan: no TBD/TODO placeholders; each step has command and expected result.
- Type consistency: route names and model responsibilities are defined in Task 1 and reused consistently.
