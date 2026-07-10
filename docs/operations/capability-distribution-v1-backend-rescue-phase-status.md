# Capability Distribution V1 Backend Rescue Status

Status:
- [x] Phase 0: isolated rescue worktree created at `C:\aiwt\capability-distribution-v1-backend-rescue-20260710` on branch `codex/capability-distribution-v1-backend-rescue-20260710`; root dirty checkout remains untouched.
- [x] Phase 1: current source selected as `origin/main` commit `124a09c39290bb3bf39d9b13bd2fa1bd632a5040`; the user-provided `e43eae3` base is historical because `main` advanced through Agent Workspace V1.
- [x] Phase 2: historical range `097d839..6a77c37` audited; no commit is safe for whole cherry-pick, and extraction is limited to final Capability Distribution behavior and tests.
- [x] Phase 3: rescue design approved; delivery is one atomic, strictly focused backend PR with no 211 operation.
- [x] Phase 4: detailed TDD implementation plan written against current-main interfaces at `docs/superpowers/plans/2026-07-10-capability-distribution-v1-backend-rescue.md`.
- [x] Phase 5: schema, backfill, repository, and unified resolver implemented by TDD in `9e517ae` plus `a7393df`; focused result `18 passed, 158 deselected`, compile/diff checks passed, and task re-review found no Critical, Important, or Minor findings.
- [x] Phase 6: Admin API plus Skill, Marketplace, and MCP read/write cutover implemented by TDD; Admin API `9 passed`, Skill/Marketplace final `55 passed`, MCP final `160 passed`, and each task's final independent review found no Critical, Important, or Minor findings.
- [ ] Phase 7: enqueue snapshot, worker Skill/MCP reauthorization, child-run inheritance, registration filtering, and audit implemented by TDD.
- [ ] Phase 8: compile, focused pytest, migration/backfill, integration, and diff verification complete on final source.
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

Boundaries:
- No unknown dirty file has been cleaned, reset, copied, or overwritten.
- No old branch has been merged or broadly cherry-picked.
- No sandbox, Release Authority, B1, B2 readiness, B3, frontend, deploy, compose, image, Ruleset, or 211 operation is in scope.
- No deployment, runtime parity, browser acceptance, or department rollout claim is authorized by this task.
