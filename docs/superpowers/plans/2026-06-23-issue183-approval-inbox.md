# Issue 183 Approval Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Back the remaining #183 standalone approval inbox contract by exposing durable current-user tool permission requests outside the run detail route.

**Architecture:** Reuse `run_tool_permission_requests` as the approval source of truth. Add current-user inbox repository queries and route handlers under `/api/ai/tool-permissions/inbox`, while reusing the existing exact decision writer, public projection, run event, and audit behavior.

**Tech Stack:** FastAPI, async repository helpers, PostgreSQL schema/indexes, pytest route/repository/schema tests.

## Global Constraints

- Do not close #183 from this PR; link it with non-closing language only.
- Do not touch #164 closure or readiness state.
- Keep approval decisions tenant/user/run/request scoped; no broad latest-allow semantics.
- Public inbox responses must not expose raw request payloads, decision payloads, commands, local paths, URLs, request ids, or secrets.
- Local pytest commands must use `--basetemp .pytest-tmp\...`.

---

### Task 1: Current-User Approval Inbox API

**Files:**
- Modify: `app/routes/tool_permissions.py`
- Modify: `app/repositories.py`
- Modify: `app/schema.sql`
- Modify: `app/tool_permission_projection.py`
- Test: `tests/test_tool_permission_routes.py`
- Test: `tests/test_repositories.py`
- Test: `tests/test_schema.py`
- Modify: `docs/frontend/skills-marketplace-public-api.md`

**Interfaces:**
- Produces: `repositories.list_tool_permission_inbox(conn, tenant_id, user_id, status, limit) -> list[dict[str, Any]]`
- Produces: `repositories.get_tool_permission_request_by_id(conn, tenant_id, user_id, request_id) -> dict[str, Any] | None`
- Produces: `POST /api/ai/tool-permissions/inbox/{request_id}/decision`
- Produces: `GET /api/ai/tool-permissions/inbox?status=pending|decided|all&limit=...`

- [x] **Step 1: Write RED route tests**

Add route tests that assert:

- current-user inbox lists only sanitized permission projections;
- `status=pending|decided|all` is passed to the repository;
- inbox decision writes the same event and audit shape as run-scoped decisions;
- inbox decision returns `404 tool_permission_request_not_found` for another user's request and `409 tool_permission_request_not_pending` for already-decided requests.

- [x] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests\test_tool_permission_routes.py::test_tool_permission_inbox_lists_current_user_requests tests\test_tool_permission_routes.py::test_tool_permission_inbox_decision_writes_event_and_audit -q --basetemp .pytest-tmp\issue183-approval-inbox-red
```

Expected: fail because the routes do not exist.

- [x] **Step 3: Implement repository helpers and routes**

Add the repository query helpers and route handlers. Keep the route handlers thin and call the existing projection/decision functions.

- [x] **Step 4: Add schema index coverage**

Add an inbox-oriented index on `(tenant_id, user_id, status, created_at desc)` and test that `schema.sql` declares it.

- [x] **Step 5: Verify focused scope**

Run:

```powershell
python -m pytest tests\test_tool_permission_routes.py tests\test_repositories.py::test_list_tool_permission_inbox_filters_current_user_and_status tests\test_repositories.py::test_get_tool_permission_request_by_id_scopes_to_user_without_run tests\test_schema.py::test_schema_declares_tool_permission_inbox_index -q --basetemp .pytest-tmp\issue183-approval-inbox-focused
python -m compileall -q app tools scripts
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Review and PR**

Request independent review. Fix Critical/Important findings, then commit, push, and open a PR with `Refs #183` only.
