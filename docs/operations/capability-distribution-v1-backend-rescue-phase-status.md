# Capability Distribution V1 Backend Rescue Status

Status:
- [x] Phase 0: isolated rescue worktree created at `C:\aiwt\capability-distribution-v1-backend-rescue-20260710` on branch `codex/capability-distribution-v1-backend-rescue-20260710`; root dirty checkout remains untouched.
- [x] Phase 1: current source selected as `origin/main` commit `124a09c39290bb3bf39d9b13bd2fa1bd632a5040`; the user-provided `e43eae3` base is historical because `main` advanced through Agent Workspace V1.
- [x] Phase 2: historical range `097d839..6a77c37` audited; no commit is safe for whole cherry-pick, and extraction is limited to final Capability Distribution behavior and tests.
- [x] Phase 3: rescue design approved; delivery is one atomic, strictly focused backend PR with no 211 operation.
- [x] Phase 4: detailed TDD implementation plan written against current-main interfaces at `docs/superpowers/plans/2026-07-10-capability-distribution-v1-backend-rescue.md`.
- [x] Phase 5: schema, backfill, repository, and unified resolver implemented by TDD in `9e517ae` plus `a7393df`; focused result `18 passed, 158 deselected`, compile/diff checks passed, and task re-review found no Critical, Important, or Minor findings.
- [x] Phase 6: Admin API plus Skill, Marketplace, and MCP read/write cutover implemented by TDD; Admin API `9 passed`, Skill/Marketplace final `55 passed`, MCP final `160 passed`, and each task's final independent review found no Critical, Important, or Minor findings.
- [x] Phase 7: enqueue snapshot, worker Skill/MCP reauthorization, child-run inheritance, registration filtering, atomic allow-once handling, and sanitized audit implemented by TDD; final high-risk review found no Critical, Important, or Minor findings.
- [x] Phase 8: final local-source verification complete on `152ef289102dbb3673d139e1d35a67aa6c125811`: compile exited 0; foundation `214 passed`; routes `341 passed, 1 deselected` (known sandbox cancel excluded), worker `196 passed, 3 skipped`; migration/backfill, capability routes, discovery cutover, enqueue, and worker authorization are covered by those focused slices; `git diff --check origin/main...HEAD` exited 0 and the exact scope audit found no forbidden or unknown paths. This is current local CI evidence only, not deployment, runtime, browser, B1/B2/B3, 211, or rollout acceptance.
- [ ] Phase 9: independent sub-agent review complete; all Critical and Important findings fixed and re-reviewed.
- [ ] Phase 10: focused PR created and required GitHub checks pass on its final head.

Current source:
- `124a09c39290bb3bf39d9b13bd2fa1bd632a5040` (`origin/main`, current rescue base)

Historical references:
- `e43eae3e2ebf10ecf8b51eb6e31e51db889d8ef7` (user-provided main snapshot; superseded before worktree creation)
- `097d839..6a77c37` (stale Capability Distribution implementation reference; extraction source only)

Baseline evidence:
- `python -m compileall -q app tools scripts` -> passed on `124a09c`.
- Selected backend baseline -> `623 passed, 3 skipped, 1 failed`.
- The sole failure, `test_cancel_run_ignores_user_controlled_sandbox_container_payload`, also fails alone on `124a09c`; status is `historical/pre-existing`, scope is sandbox cancel, and this rescue does not modify it.

Phase 8 verification evidence:
- `python -m compileall -q app tools scripts` -> exit 0.
- Foundation slice -> `214 passed in 4.45s`.
- Route slice with only the pre-existing sandbox cancel test excluded -> `341 passed, 1 deselected, 1 warning in 45.58s`; the warning is the existing duplicate OpenAPI operation id for `active_notifications` in `app/routes/lambchat_compat.py`.
- Worker slice -> `196 passed, 3 skipped in 4.97s`.
- Aggregate focused pytest result -> `751 passed, 3 skipped, 1 deselected`; no Capability Distribution defect was reproduced, so no source or test repair was made in Task 7.

Phase 8 scope-audit evidence:
- Fresh command: `git diff --name-only origin/main...HEAD`.
- Exact count: `26` tracked paths. Every path in the complete output below is approved for this rescue; no scratch file is present.

```text
app/capability_distribution.py
app/main.py
app/models.py
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
tests/test_capability_distribution_routes.py
tests/test_chat_routes.py
tests/test_mcp_routes.py
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
