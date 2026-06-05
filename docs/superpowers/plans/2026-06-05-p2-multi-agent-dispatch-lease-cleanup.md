# P2 Multi-Agent Dispatch Lease Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add lease metadata and admin cleanup for expired multi-agent dispatch claims.

**Architecture:** Keep lease state in `run_steps.payload_json` so no DB migration is needed. Claim writes a bounded lease; Admin Runtime cleanup reclaims expired same-tenant claims back to `pending` and writes audit evidence. No queue enqueue or scheduler starts in this slice.

**Tech Stack:** FastAPI, async PostgreSQL repository helpers, existing run-step and audit contracts, pytest.

---

## File Structure

- Modify `app/settings.py`: add `multi_agent_dispatch_lease_ttl_seconds`.
- Modify `app/repositories.py`: add lease metadata on claim and a cleanup helper.
- Modify `app/routes/runs.py`: pass configured TTL to the claim helper.
- Modify `app/routes/admin_runtime.py`: add admin-only cleanup route.
- Modify `tests/test_run_control_routes.py`: update claim route/helper assertions.
- Modify `tests/test_admin_runtime_routes.py`: add cleanup route tests.
- Modify `tests/test_repositories.py`: add repository cleanup helper test.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: append final evidence after deploy.

## Tasks

- [x] Add RED tests for claim lease metadata and cleanup route/repository behavior.
- [x] Add TTL setting and claim payload lease fields.
- [x] Add repository cleanup helper that reclaims expired claimed steps and writes audit.
- [x] Add admin runtime cleanup route with admin-only same-tenant boundary.
- [x] Run focused tests and full verification.
- [x] Run inherited-configuration review and fix validated feedback.
- [ ] Deploy to 211, smoke, and record roadmap evidence.

## Focused Commands

```powershell
python -m pytest tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_claim_records_ledger_event_and_audit tests/test_run_control_routes.py::test_claim_multi_agent_dispatch_step_writes_step_event_and_audit tests/test_admin_runtime_routes.py::test_admin_runtime_multi_agent_dispatch_cleanup_requires_admin tests/test_admin_runtime_routes.py::test_admin_runtime_multi_agent_dispatch_cleanup_returns_same_tenant_expired_claims tests/test_repositories.py::test_cleanup_expired_multi_agent_dispatch_claims_reclaims_steps_and_writes_audit -q --basetemp .pytest-tmp\p2-dispatch-cleanup-red
python -m pytest tests/test_run_control_routes.py tests/test_admin_runtime_routes.py tests/test_repositories.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-dispatch-cleanup-focused
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\p2-dispatch-cleanup-full
git diff --check
```

## Local Verification Evidence

- RED review tests:
  `3 failed` before the safe timestamp parsing and stale marker cleanup fix.
- RED second-review tests:
  `3 failed` before batch scanning and `skip locked`.
- Focused verification:
  `194 passed` for `tests/test_run_control_routes.py`,
  `tests/test_admin_runtime_routes.py`, `tests/test_repositories.py`, and
  `tests/test_source_authority_docs.py`.
- Full verification:
  `942 passed, 6 skipped, 2 warnings`.
- Static checks:
  `python -m compileall -q app tools scripts` exited 0.
  `git diff --check` exited 0 with only an `app/settings.py` CRLF/LF warning.
- Review:
  Faraday inherited-configuration review found the malformed timestamp,
  parent-run-status, stale `dispatch_expired_at`, candidate-window, and
  `skip locked` issues. Final review reported no Critical, Important, or
  Minor findings.
