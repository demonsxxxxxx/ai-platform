# Capability Distribution V1 Backend Rescue Status

Status:
- [x] Phase 0: isolated rescue worktree created at `C:\aiwt\capability-distribution-v1-backend-rescue-20260710` on branch `codex/capability-distribution-v1-backend-rescue-20260710`; root dirty checkout remains untouched.
- [x] Phase 1: current source selected as `origin/main` commit `289897087ce3b88724401f78b936f96fa7b68562`; the rescue branch was rebased without conflicts after the S1A/S2A merges, and the earlier `e43eae3` and `124a09c` bases are historical.
- [x] Phase 2: historical range `097d839..6a77c37` audited; no commit is safe for whole cherry-pick, and extraction is limited to final Capability Distribution behavior and tests.
- [x] Phase 3: rescue design approved; delivery is one atomic, strictly focused backend PR with no 211 operation.
- [x] Phase 4: detailed TDD implementation plan written against current-main interfaces at `docs/superpowers/plans/2026-07-10-capability-distribution-v1-backend-rescue.md`.
- [x] Phase 5: schema, backfill, repository, and unified resolver implemented by TDD in `9e517ae` plus `a7393df`; focused result `18 passed, 158 deselected`, compile/diff checks passed, and task re-review found no Critical, Important, or Minor findings.
- [x] Phase 6: Admin API plus Skill, Marketplace, and MCP read/write cutover implemented by TDD; Admin API `9 passed`, Skill/Marketplace final `55 passed`, MCP final `160 passed`, and each task's final independent review found no Critical, Important, or Minor findings.
- [x] Phase 7: enqueue snapshot, worker Skill/MCP reauthorization, child-run inheritance, registration filtering, atomic allow-once handling, and sanitized audit implemented by TDD; final high-risk review found no Critical, Important, or Minor findings.
- [x] Phase 8: post-rebase local-source verification complete on `c87a6ce5574ae413ce6bb6a79711398a69199e25`: compile exited 0; affected pytest slices total `872 passed, 3 skipped, 1 deselected`; migration/backfill, exact-role handling, department allow/deny, disabled-capability denial, Admin API, Skill/MCP read/write cutover, enqueue snapshot, worker reauthorization, allow-once deduplication, dispatcher identity, audit sanitization, and discovery privacy are covered; `git diff --check origin/main...HEAD` exited 0 and the exact 31-path scope audit found no forbidden or unknown paths. This is current local source evidence only, not deployment, runtime, browser, B1/B2/B3, 211, or rollout acceptance.
- [ ] Phase 9: independent sub-agent review complete; all Critical and Important findings fixed and re-reviewed.
- [ ] Phase 10: focused PR created and required GitHub checks pass on its final head.

Current source:
- `289897087ce3b88724401f78b936f96fa7b68562` (`origin/main`, current rescue base)
- `c87a6ce5574ae413ce6bb6a79711398a69199e25` (verified rebased implementation head before this status update)

Historical references:
- `e43eae3e2ebf10ecf8b51eb6e31e51db889d8ef7` (user-provided main snapshot; superseded before worktree creation)
- `124a09c39290bb3bf39d9b13bd2fa1bd632a5040` (pre-S1A/S2A rescue base; superseded by the required final rebase)
- `152ef289102dbb3673d139e1d35a67aa6c125811` (pre-final-fix verification head; superseded)
- `097d839..6a77c37` (stale Capability Distribution implementation reference; extraction source only)

Baseline evidence:
- `python -m compileall -q app tools scripts` -> passed on `124a09c`.
- Selected backend baseline -> `623 passed, 3 skipped, 1 failed`.
- The sole failure, `test_cancel_run_ignores_user_controlled_sandbox_container_payload`, also fails alone on `124a09c`; status is `historical/pre-existing`, scope is sandbox cancel, and this rescue does not modify it.

Phase 8 verification evidence:
- `python -m compileall -q app tools scripts` -> exit 0.
- Capability/schema/repository/Admin slice -> `226 passed`.
- Skill/Marketplace/role-governance/auth slice -> `71 passed`, with the existing duplicate OpenAPI operation-id warning for `active_notifications` in `app/routes/lambchat_compat.py`.
- MCP route slice -> `22 passed`.
- Chat/run/requeue/control slice with only the pre-existing sandbox cancel test excluded -> `271 passed, 1 deselected`.
- Worker/adapter slice -> `201 passed, 3 skipped`.
- Multi-agent dispatcher slice -> `10 passed`.
- Impacted capability/admin caller slice -> `71 passed`.
- Aggregate focused pytest result -> `872 passed, 3 skipped, 1 deselected`; all commands used fresh children under `.pytest-tmp`.

Phase 8 scope-audit evidence:
- Fresh command: `git diff --name-only origin/main...HEAD`.
- Exact count: `31` tracked paths. Every path in the complete output below is approved for this rescue; no scratch file is present.

```text
app/auth.py
app/capability_distribution.py
app/main.py
app/models.py
app/multi_agent_dispatcher.py
app/repositories.py
app/routes/capability_distributions.py
app/routes/chat.py
app/routes/mcp.py
app/routes/role_governance.py
app/routes/runs.py
app/routes/skills_marketplace.py
app/schema.sql
app/worker.py
docs/operations/capability-distribution-v1-backend-rescue-phase-status.md
docs/superpowers/plans/2026-07-10-capability-distribution-v1-backend-rescue.md
docs/superpowers/specs/2026-07-10-capability-distribution-v1-backend-rescue-design.md
tests/test_capability_distribution.py
tests/test_agent_apps.py
tests/test_auth_principal.py
tests/test_capability_distribution_routes.py
tests/test_chat_routes.py
tests/test_mcp_routes.py
tests/test_multi_agent_dispatcher.py
tests/test_repositories.py
tests/test_role_governance_routes.py
tests/test_routes.py
tests/test_run_control_routes.py
tests/test_schema.py
tests/test_skills_marketplace_routes.py
tests/test_worker.py
```

Boundaries:
- No unknown dirty file has been cleaned, reset, copied, or overwritten.
- No old branch has been merged or broadly cherry-picked.
- No sandbox, Release Authority, B1, B2 readiness, B3, frontend, deploy, compose, image, Ruleset, or 211 operation is in scope.
- No deployment, runtime parity, browser acceptance, or department rollout claim is authorized by this task.
