# P2 Multi-Agent Controlled Child Run Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an admin-only handoff from a claimed multi-agent dispatch step
to one queued child run.

**Architecture:** Keep the handoff bounded to the existing run-control route,
repository, context snapshot, queue, run-step, event, and audit contracts. The
repository validates and creates the child run; the route prepares the queue
payload with the child owner identity and enqueues after the DB transaction.

**Tech Stack:** FastAPI, async PostgreSQL repository helpers, Redis-backed
queue payloads, Pydantic response models, pytest.

---

## File Structure

- Modify `app/models.py`: add handoff response model.
- Modify `app/repositories.py`: add a repository helper to validate a claimed
  dispatch step, create a queued child run, update parent step handoff state,
  and write event/audit rows.
- Modify `app/routes/runs.py`: add route constant, owner-principal queue helper
  support, and admin-only handoff route.
- Modify `tests/test_run_control_routes.py`: add route and repository focused
  tests.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`:
  record final evidence after review and 211 smoke.

## Tasks

- [x] Add RED route tests for admin-only handoff, successful queue payload
  creation with parent owner identity, duplicate handoff rejection, and expired
  lease rejection without enqueue.
- [x] Add RED repository tests for child run insert, parent step update,
  parent/child events, audit payload, dependency resume materialization, and
  unsafe user-controlled resume stripping.
- [x] Add `MultiAgentDispatchHandoffResponse` and route contract constant.
- [x] Add an optional owner identity parameter to
  `prepare_copied_run_for_queue` so admin-triggered child runs use the child
  run owner for context snapshot and queue payload identity.
- [x] Implement `create_multi_agent_dispatch_child_run` in
  `app/repositories.py` with active-parent, claimed-step, lease, duplicate, and
  dependency validation.
- [x] Add `POST /runs/{run_id}/multi-agent/dispatch/claims/{dispatch_id}/handoff`
  in `app/routes/runs.py`, enqueue after transaction, and return queue insight.
- [x] Run focused tests for route/repository/source-authority coverage.
- [x] Run inherited-configuration multi-agent review; validate and fix only
  feedback grounded in PRD, roadmap, guardrails, code, and tests.
- [x] Run compile, full pytest, and `git diff --check`.
- [ ] Open PR, merge after review, deploy to 211, smoke the handoff contract,
  then append final roadmap evidence.

## Focused Commands

```powershell
python -m pytest tests/test_run_control_routes.py::test_multi_agent_dispatch_handoff_requires_admin tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_handoff_creates_owner_child_run_and_enqueues tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_handoff_rejects_duplicate_without_enqueue tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_handoff_rejects_expired_claim_without_enqueue -q --basetemp .pytest-tmp\p2-handoff-red-route
python -m pytest tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_records_parent_child_events_and_audit tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_builds_single_step_resume_input tests/test_run_control_routes.py::test_create_multi_agent_dispatch_child_run_rejects_malformed_claim_lease_without_insert -q --basetemp .pytest-tmp\p2-handoff-red-repo
python -m pytest tests/test_run_control_routes.py tests/test_repositories.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-handoff-focused
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\p2-handoff-full
git diff --check
```

## Local Verification Evidence

- RED route tests: `4 failed` before the handoff route existed.
- RED repository tests: `3 failed` before
  `create_multi_agent_dispatch_child_run` existed.
- Focused TDD verification:
  - route handoff tests: `4 passed`
  - repository handoff tests: `3 passed`
- Focused route/repository/source-authority verification:
  `177 passed`.
- Wider focused verification including create/copy/retry/resume routes:
  `243 passed`.
- Full local verification:
  `949 passed, 6 skipped, 2 warnings`.
- Static checks:
  `python -m compileall -q app tools scripts` exited 0.
  `git diff --check` exited 0.
- Review:
  Boole inherited-configuration review found no Critical or Important issues.
  One Minor documentation progress issue was fixed by marking completed plan
  tasks while leaving PR/deploy/211 evidence unchecked.

## 211 Smoke Expectations

- `/api/ai/health` returns OK after deployment.
- OpenAPI exposes the handoff route.
- Ordinary user handoff returns `403`.
- Admin claim followed by admin handoff returns `200` with
  `contract_version = ai-platform.multi-agent-dispatch-handoff.v1`.
- Child run is `queued`, has `copied_from_run_id = parent_run_id`, and queue
  payload/context source is `multi_agent_dispatch_handoff`.
- Parent step payload has `dispatch_state = handed_off`,
  `dispatch_child_run_id`, and `dispatch_handed_off_at`.
- Audit contains `run.multi_agent.dispatch.handoff`.
- Smoke DB/Redis cleanup leaves no seeded rows or queue payloads.
