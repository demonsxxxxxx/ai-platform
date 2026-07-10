# Capability Distribution V1 Backend Rescue Status

Status:
- [x] Phase 0: isolated rescue worktree created at `C:\aiwt\capability-distribution-v1-backend-rescue-20260710` on branch `codex/capability-distribution-v1-backend-rescue-20260710`; root dirty checkout remains untouched.
- [x] Phase 1: current source selected as `origin/main` commit `db8bef4add20fb457786f1d58031963687ec9e9f`; the rescue branch was rebased without conflicts after S1A/S2A and S0B, and all earlier bases are historical.
- [x] Phase 2: historical range `097d839..6a77c37` audited; no commit is safe for whole cherry-pick, and extraction is limited to final Capability Distribution behavior and tests.
- [x] Phase 3: rescue design approved; delivery is one atomic, strictly focused backend PR with no 211 operation.
- [x] Phase 4: detailed TDD implementation plan written against current-main interfaces at `docs/superpowers/plans/2026-07-10-capability-distribution-v1-backend-rescue.md`.
- [x] Phase 5: schema, backfill, repository, and unified resolver implemented by TDD; initial focused result `18 passed, 158 deselected`, compile/diff checks passed, and task re-review found no Critical, Important, or Minor findings.
- [x] Phase 6: Admin API plus Skill, Marketplace, and MCP read/write cutover implemented by TDD; Admin API `9 passed`, Skill/Marketplace final `55 passed`, MCP final `160 passed`, and each task's final independent review found no Critical, Important, or Minor findings.
- [x] Phase 7: enqueue snapshot, worker Skill/MCP reauthorization, child-run inheritance, registration filtering, atomic allow-once handling, and sanitized audit implemented by TDD; final high-risk review found no Critical, Important, or Minor findings.
- [x] Phase 8: final post-S0B-rebase local-source verification complete on implementation head `75278e4b81b6d7386cb670163cbebd576f88581d`: compile exited 0; affected pytest slices total `1003 passed, 4 skipped, 1 deselected`; migration/backfill, exact department and role handling, malformed/blank-scope denial, rollout-selected Skill/Marketplace/Agent discovery, atomic Marketplace authority responses, empty-authority projection short-circuiting, enqueue/requeue snapshot authority, rollback-safe route/worker/dispatcher denial audit, dispatcher conflict rollback, database-authoritative child reconciliation, worker reauthorization, allow-once deduplication, selector-oracle privacy, and audit sanitization are covered; working-tree and base diff checks exited 0 and the exact 38-path scope audit found no forbidden or unknown paths. This is current local source evidence only, not deployment, runtime, browser, B1/B2/B3, 211, or rollout acceptance.
- [ ] Phase 9: fresh independent re-review of `db8bef4..b2ff9fc` completed with `0 Critical, 1 Important, 0 Minor`; the finding is fixed on `75278e4` by building the non-rollout admin response inside the activation write transaction, and all affected gates were repeated, but the required fresh independent re-review is still pending, so the branch is not merge-ready.
- [ ] Phase 10: focused PR created and required GitHub checks pass on its final head.

Current source:
- `db8bef4add20fb457786f1d58031963687ec9e9f` (`origin/main`, current rescue base)
- `75278e4b81b6d7386cb670163cbebd576f88581d` (current verified implementation head; final independent re-review pending)

Historical references:
- `e43eae3e2ebf10ecf8b51eb6e31e51db889d8ef7` (user-provided main snapshot; superseded before worktree creation)
- `124a09c39290bb3bf39d9b13bd2fa1bd632a5040` (pre-S1A/S2A rescue base; superseded by the required final rebase)
- `289897087ce3b88724401f78b936f96fa7b68562` (pre-S0B rescue base; superseded)
- `152ef289102dbb3673d139e1d35a67aa6c125811` (pre-final-fix verification head; superseded)
- `c87a6ce5574ae413ce6bb6a79711398a69199e25` (pre-second-broad-review implementation head; superseded)
- `097d839..6a77c37` (stale Capability Distribution implementation reference; extraction source only)

Baseline evidence:
- `python -m compileall -q app tools scripts` -> passed on `124a09c`.
- Selected backend baseline -> `623 passed, 3 skipped, 1 failed`.
- The sole failure, `test_cancel_run_ignores_user_controlled_sandbox_container_payload`, also fails alone on `124a09c`; status is `historical/pre-existing`, scope is sandbox cancel, and this rescue does not modify it.

Phase 8 verification evidence:
- `python -m compileall -q app tools scripts` -> exit 0.
- Capability/schema/repository plus environment-gated PostgreSQL slice -> `239 passed, 1 skipped`; the skip is the real PostgreSQL gate because `AI_PLATFORM_CAPABILITY_DISTRIBUTION_TEST_DSN` is not configured locally and `psql` is unavailable.
- Admin Skill, Agent Workspace, LambChat compatibility, and chat projection slice -> `166 passed`.
- MCP, Skill/Marketplace, role-governance, and auth slice -> `98 passed`, with the existing duplicate OpenAPI operation-id warning for `active_notifications` in `app/routes/lambchat_compat.py`.
- Run and run-control slice with only the pre-existing sandbox cancel test excluded -> `250 passed, 1 deselected`.
- Worker, adapter, and multi-agent dispatcher slice -> `217 passed, 3 skipped`. An earlier run with a long basetemp name produced four Windows `WinError 206` path-length failures before assertions; final verification used short fresh children under `.pytest-tmp`.
- Impacted Agent Apps module -> `8 passed`; stale direct-resolver assertions were migrated to the unified authorizer boundary without removing disabled MCP fail-closed coverage.
- Aggregate focused pytest result -> `978 passed, 4 skipped, 1 deselected`; all commands used fresh children under `.pytest-tmp`.
- The first post-S0B route run exposed four stale generic test fixtures for the new file/artifact permission and bounded-read contract. Only `tests/test_routes.py` fixtures were aligned; `app/routes/files.py` and S0B dedicated tests were not modified. The focused four-test rerun and complete run/control rerun passed.
- Final broad-review fixes were developed with focused RED/GREEN evidence: empty Agent projection `1 failed` then `1 passed`; malformed legacy roles/worker denial `4 failed, 1 skipped` then `4 passed, 1 skipped`, followed by empty-role `2 failed, 2 passed` then `4 passed`; role-governance bypass audit `1 failed` then `1 passed`; dispatcher rollback audit `2 failed, 10 passed` then `12 passed`; real runs/chat authorizer coverage `5 passed` per route.
- Fresh independent broad re-review on `6fee253` verified those five closures but found `4 Important, 2 Minor`; its verdict was `not merge-ready`.
- Follow-up finding fixes on `e9a099d`: release-aware Skill/Agent discovery RED `4 + 5 failed` then GREEN `4 + 5 passed`; copy/retry/resume selector-oracle/audit RED `15 failed` then GREEN `15 passed`; dispatcher post-claim rollback RED `2 failed` then GREEN `2 passed`; malformed locked child reconciliation RED `1 failed` then GREEN `6 passed`; blank-role integrity RED `2 failed, 3 passed` then GREEN `5 passed`. The real PostgreSQL test now coordinates two transactions against one incomplete marker and asserts the second waits for the locked recheck, but local execution remains skipped without the dedicated DSN.
- Follow-up independent review on `097fe3e` closed four of those six findings and returned `0 Critical, 2 Important, 0 Minor`; DB-authoritative child reconciliation and observable single-backfill PostgreSQL contention remain open.
- Final follow-up fixes on `1d71ab0`: worker terminal reconciliation no longer trusts queue input and always delegates relationship validation to the repository's locked database state; the ordinary-run and queue-marker-free child-run comparison passed `2 passed`, and the complete worker module passed `125 passed`. The PostgreSQL gate now observes the second backend blocked by the first through `pg_stat_activity` and `pg_blocking_pids`, inserts a committed late legacy row while blocked, and verifies the completed-marker recheck prevents a second backfill; local execution remains `1 skipped` because the dedicated DSN is absent.
- Final follow-up affected verification repeated the established six groups: core `239 passed, 1 skipped`; Admin/Agent projection/chat `166 passed`; MCP/Skill/role/auth `98 passed`; run/control `250 passed, 1 deselected`; worker/adapter/dispatcher `217 passed, 3 skipped`; Agent Apps `8 passed`. Aggregate: `978 passed, 4 skipped, 1 deselected`. Compile, working-tree/base diff checks, exact 38-path scope, secret-value/path, personal-path, and forbidden-operation scans passed.
- Fresh independent broad review on `57d99ff` returned `0 Critical, 2 Important, 1 Minor`: it closed both final follow-up findings and confirmed the 38-path/S0B/PostgreSQL skip boundaries, then identified blank MCP write roles, rollout-selected discovery lifecycle, and malformed legacy department migration gaps. No PR or remote write is authorized until the Important findings are fixed and a fresh re-review returns zero Critical/Important.
- Final review-fix RED/GREEN on `187fda9`: MCP blank create/update roles failed `2` cases with `200/409` before GREEN `2 passed`; rollout-selected Agent and Skill/Marketplace discovery plus route identity/SQL coverage failed `15` cases before GREEN `10 + 5 passed`; department schema/backfill tests failed before GREEN schema `1 passed` and repository `4 passed`, while the real PostgreSQL test remains environment-gated at `1 skipped`.
- Final review-fix affected verification: core `252 passed, 1 skipped`; Admin/Agent projection/chat `166 passed`; MCP/Skill/role/auth `100 passed`; run/control `250 passed, 1 deselected`; worker/adapter/dispatcher `217 passed, 3 skipped`; Agent Apps `8 passed`. Aggregate: `993 passed, 4 skipped, 1 deselected`; compile, diff, exact 38-path scope, secret, personal-path, and forbidden-operation scans passed.
- Fresh independent re-review on `b2ff9fc` returned `0 Critical, 1 Important, 0 Minor`: all three preceding findings and the 38-path/S0B/PostgreSQL boundaries are closed, but Marketplace activation must build its admin response inside the write transaction from a non-rollout projection to prevent committed authority changes followed by a false 404.
- Atomic Marketplace activation RED/GREEN on `75278e4`: missing and draft/reviewed/disabled/deprecated previous-track states across enable and disable produced `10 failed` with post-commit 404 before GREEN `10 passed`; the tests require `toggle -> audit -> non-rollout admin list -> commit` in one transaction.
- Atomic activation affected verification: core `252 passed, 1 skipped`; Admin/Agent projection/chat `166 passed`; MCP/Skill/role/auth `110 passed`; run/control `250 passed, 1 deselected`; worker/adapter/dispatcher `217 passed, 3 skipped`; Agent Apps `8 passed`. Aggregate: `1003 passed, 4 skipped, 1 deselected`; compile, diff, exact 38-path scope, secret, personal-path, and forbidden-operation scans passed.

Phase 8 scope-audit evidence:
- Fresh command: `git diff --name-only origin/main...HEAD`.
- Exact count: `38` tracked paths. Every path in the complete output below is approved for this rescue; no scratch file is present.

```text
app/auth.py
app/capability_distribution.py
app/main.py
app/models.py
app/multi_agent_dispatcher.py
app/repositories.py
app/routes/admin_skills.py
app/routes/capability_distributions.py
app/routes/chat.py
app/routes/frontend_projections.py
app/routes/lambchat_compat.py
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
tests/test_admin_skills.py
tests/test_agent_workspace_projection.py
tests/test_auth_principal.py
tests/test_capability_distribution_routes.py
tests/test_capability_distribution_postgres.py
tests/test_chat_routes.py
tests/test_lambchat_frontend_compat.py
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
- S0B production source and dedicated tests were preserved; only stale generic fixtures in the already-owned `tests/test_routes.py` were aligned after the disjoint main rebase.
- No sandbox, Release Authority, B1, B2 readiness, B3, frontend, deploy, compose, image, Ruleset, or 211 operation is in scope.
- No deployment, runtime parity, browser acceptance, or department rollout claim is authorized by this task.
