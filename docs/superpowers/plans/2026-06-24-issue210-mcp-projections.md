# Issue 210 MCP Projections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify governed MCP frontend surfaces as ai-platform public/admin projections so `/api/mcp` and `/api/admin/mcp` no longer appear as active legacy route gaps.

**Architecture:** Keep the existing MCP backend route contract. The MCP router already provides an ordinary-user catalog projection, admin-gated lifecycle writes, tenant/department filtering, credential redaction, and audit logging. This slice updates projection-audit source-of-truth lists and regression tests so frontend governance recognizes those routes as safe public/admin projection surfaces.

**Tech Stack:** Python, pytest, FastAPI route tests, `tools/frontend_projection_audit.py`, governance readiness tests.

## Global Constraints

- Refs #210 only; do not auto-close #164 or claim Foundation Alpha POC readiness.
- Do not expose MCP credentials, headers, env values, raw runtime config, DB URLs, Redis URLs, storage keys, or bearer tokens.
- Preserve ordinary-user safe catalog/degraded projection behavior for `/api/mcp`.
- Preserve admin-only lifecycle and policy behavior for `/api/admin/mcp`.
- Local pytest commands must use `--basetemp .pytest-tmp\...`.
- No database schema change is required for this slice.

---

### Task 1: Projection Audit Classification

**Files:**
- Modify: `tests/test_frontend_projection_audit.py`
- Modify: `tests/test_governance_readiness.py`
- Modify: `tools/frontend_projection_audit.py`
- Test: `tests/test_mcp_routes.py`

**Interfaces:**
- Consumes: existing `app.routes.mcp` public catalog and admin lifecycle contract.
- Produces: `SAFE_PUBLIC_ROUTE_PREFIXES` containing `/api/mcp` and `SAFE_ADMIN_ROUTE_PREFIXES` containing `/api/admin/mcp`.

- [ ] **Step 1: Write failing audit tests**

Change projection-audit expectations so `/api/mcp` is in safe public routes, `/api/admin/mcp` is in safe admin routes, and neither route appears in active legacy policy gaps.

- [ ] **Step 2: Run RED**

Run:

```powershell
python -m pytest tests\test_frontend_projection_audit.py::test_frontend_projection_audit_reports_current_public_admin_boundary tests\test_governance_readiness.py::test_governance_readiness_frontend_projection_evidence_clears_mcp_active_legacy_gap -q --basetemp .pytest-tmp\issue210-red
```

Expected: fail because MCP routes are still classified as legacy policy routes.

- [ ] **Step 3: Update audit route inventories**

Add `/api/mcp` to safe public route prefixes, add `/api/admin/mcp` to safe admin route prefixes, and remove MCP entries from legacy-policy-required route prefixes and policy map.

- [ ] **Step 4: Run GREEN and affected tests**

Run:

```powershell
python -m pytest tests\test_frontend_projection_audit.py tests\test_governance_readiness.py::test_governance_readiness_frontend_projection_evidence_clears_mcp_active_legacy_gap tests\test_mcp_routes.py -q --basetemp .pytest-tmp\issue210-green
python tools\frontend_projection_audit.py --format json
python -m compileall -q app tools scripts
git diff --check
```

Expected: tests pass; projection audit exits 0 with MCP routes absent from active legacy route gap details.

- [ ] **Step 5: Review, PR, and evidence**

Request read-only review, post substitute review evidence if GitHub formal review is unavailable, then open a PR with `Refs #210`. Only after merge and required 211 smoke/evidence may #210 be closed.

## Self-Review

- Spec coverage: #210 public/admin MCP projection classification and route-test evidence are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: route prefix constants and test assertions use exact existing names.
