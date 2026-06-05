# P2 Multi-Agent Dispatch Tick Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only bounded dispatch tick that claims, hands off, and enqueues one safe ready multi-agent step.

**Architecture:** Reuse the existing run-control readiness helper, dispatch claim repository function, child handoff repository function, copied-run context snapshot path, and queue enqueue path. Keep the tick as a route-level orchestration primitive, not a background scheduler.

**Tech Stack:** FastAPI, Pydantic, psycopg async transactions, existing ai-platform queue helpers, pytest.

---

### Task 1: Tick Contract Tests

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [x] **Step 1: Add RED tests**

Add route tests for:

```python
def test_multi_agent_dispatch_tick_requires_admin(monkeypatch): ...
def test_multi_agent_dispatch_tick_rejects_when_no_ready_step(monkeypatch): ...
def test_multi_agent_dispatch_tick_rejects_when_only_ready_step_is_unsafe(monkeypatch): ...
def test_multi_agent_dispatch_tick_claims_handoffs_and_enqueues_next_ready_step(monkeypatch): ...
```

- [x] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_multi_agent_dispatch_tick_requires_admin tests/test_run_control_routes.py::test_multi_agent_dispatch_tick_rejects_when_no_ready_step tests/test_run_control_routes.py::test_multi_agent_dispatch_tick_claims_handoffs_and_enqueues_next_ready_step -q --basetemp .pytest-tmp\p2-dispatch-tick-red
```

Expected before implementation: failures with `404 Not Found` because the route does not exist.

### Task 2: Route Implementation

**Files:**
- Modify: `app/models.py`
- Modify: `app/routes/runs.py`

- [x] **Step 1: Add response model**

Add `MultiAgentDispatchTickResponse` with contract version, parent id, selected step id/key, dispatch id, child run id, session id, queue position, queue insight, claim event/audit ids, and handoff event/audit ids.

- [x] **Step 2: Add candidate helper**

Add `_dispatch_tick_candidate(run, steps, principal)` that validates active multi-agent status, reads `multi_agent_readiness_snapshot`, selects the first ready safe step, and fails closed with `no_ready_steps` or `no_safe_ready_steps`.

- [x] **Step 3: Add route**

Add `POST /runs/{run_id}/multi-agent/dispatch/tick` in `app/routes/runs.py`. The route must:

```python
if not is_ai_admin(principal):
    raise HTTPException(status_code=403, detail="admin_required")
async with transaction() as conn:
    run = await repositories.get_run(..., for_update=True)
    steps = await repositories.list_run_steps(...)
    candidate = _dispatch_tick_candidate(...)
    claim = await repositories.claim_multi_agent_dispatch_step(...)
    copied = await repositories.create_multi_agent_dispatch_child_run(...)
    queue_payload = await prepare_copied_run_for_queue(..., source="multi_agent_dispatch_tick")
queue_position = await enqueue_run(queue_payload)
return MultiAgentDispatchTickResponse(...)
```

- [x] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py -q -k "multi_agent_dispatch_tick or multi_agent_dispatch_claim or multi_agent_dispatch_handoff" --basetemp .pytest-tmp\p2-dispatch-tick-focused
```

Result after review fixes:

- `python -m pytest tests/test_run_control_routes.py::test_multi_agent_dispatch_tick_rejects_when_only_ready_step_is_unsafe tests/test_run_control_routes.py::test_claim_multi_agent_dispatch_step_writes_step_event_and_audit tests/test_run_control_routes.py::test_claim_multi_agent_dispatch_step_rejects_stale_non_pending_race -q --basetemp .pytest-tmp\p2-dispatch-tick-review-green2`: `6 passed`.
- `python -m pytest tests/test_run_control_routes.py -q -k "multi_agent_dispatch_tick or multi_agent_dispatch_claim or multi_agent_dispatch_handoff" --basetemp .pytest-tmp\p2-dispatch-tick-focused2`: `15 passed, 104 deselected`.

Review-driven fixes:

- Unsafe configured ready step keys now fail closed for forbidden aliases,
  hash-like values, invalid safe ids, and raw/private projection terms before
  returning tick response metadata.
- Dispatch claim now uses a conditional insert/update and maps stale non-pending
  races to `409 dispatch_step_not_pending` instead of overwriting a concurrent
  running or terminal step.
- Tick candidate scanning skips already completed/running/blocked configured
  steps and continues to the next safe ready step instead of stopping on the
  first non-ready dependency step.

### Task 3: Review, Docs, And Deployment

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Create: `docs/superpowers/specs/2026-06-06-p2-multi-agent-dispatch-tick-design.md`
- Create: `docs/superpowers/plans/2026-06-06-p2-multi-agent-dispatch-tick.md`

- [x] **Step 1: Run affected verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_run_control_routes.py tests/test_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-dispatch-tick-affected
git diff --check
```

Result:

- `python -m compileall -q app tools scripts`: exit 0.
- `git diff --check`: exit 0.
- `python -m pytest tests/test_run_control_routes.py tests/test_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-dispatch-tick-affected2`: `194 passed`.
- `python -m pytest -q --basetemp .pytest-tmp\p2-dispatch-tick-full2`: `991 passed, 6 skipped, 2 warnings`.

- [x] **Step 2: Request inherited-configuration review**

Use available subagent review if the tool exposes inherited configuration. Do
not claim an explicit model or reasoning-effort gate unless the dispatch tool
actually exposes those fields.

Result: inherited-configuration reviewer reported no Critical or Important
findings. The only Minor item was to make sure the two new feature docs are
tracked before commit; they are intended deliverables for this slice.

- [x] **Step 3: Commit and deploy**

Commit the implementation and docs to `main`, push, sync the current source to
211, build or runtime-rebase according to `AGENTS.md`, recreate API/worker, and
verify image labels plus health.

Result:

- Commit: `c35c7f0a891062e8f636ba9a834a16ca9e3830f6`
  (`feat: add multi-agent dispatch tick`), pushed to `origin/main`.
- 211 image: `ai-platform:c35c7f0a8910`,
  `sha256:93d40379aadf0276a6690eaf010541a6da78b6c889a7329a12b6d82d825d99a1`.
- 211 labels:
  `ai-platform.source-revision=c35c7f0a891062e8f636ba9a834a16ca9e3830f6`,
  `ai-platform.source_note=p2-multi-agent-dispatch-tick`.
- 211 containers: `ai-platform-api` and `ai-platform-worker` both running
  `ai-platform:c35c7f0a8910`.
- 211 API health: `GET /api/ai/health` returned `{"status":"ok"}`.
- 211 OpenAPI route exposure:
  `/api/ai/runs/{run_id}/multi-agent/dispatch/tick` present with `POST`.

- [x] **Step 4: 211 smoke**

In the live container, create a smoke parent with a succeeded dependency and a
ready child step, call the tick route as admin, verify child enqueue and
claim/handoff evidence, verify ordinary-user projection redaction, then clean
all smoke DB/queue rows.

Result:

- Admin HTTP smoke returned
  `contract_version=ai-platform.multi-agent-dispatch-tick.v1`,
  `step_key=code`, `status=queued`, and `queue_position=1`.
- Live DB evidence showed child run status `queued`, child copied from the
  parent run, parent `code` step status `running`, dispatch state `handed_off`,
  and `dispatch_child_run_id` matching the child run id.
- Redis queue evidence showed exactly one queued child payload before cleanup.
- Run event evidence included `agent_step_started`,
  `multi_agent_dispatch_handoff`, `run_multi_agent_child_created`,
  `skill_release_decision`, `context_snapshot_created`, `queued`, and
  `skill_selected`.
- Audit evidence included `run.multi_agent.dispatch.claim` and
  `run.multi_agent.dispatch.handoff`.
- Ordinary-user event projection returned no hidden `agent_step_started` or
  `multi_agent_dispatch_handoff` events.
- Cleanup verification after smoke reported zero remaining smoke rows in
  `users`, `sessions`, `runs`, `run_steps`, `run_events`,
  `run_context_snapshots`, `audit_logs`, and zero smoke Redis queue payloads.
- Recent API and worker logs showed no `error`, `traceback`, or `exception`
  lines for the smoke window.
