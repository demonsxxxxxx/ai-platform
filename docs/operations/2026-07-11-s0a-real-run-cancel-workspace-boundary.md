# S0A Real Run Cancel And Tenant-Scoped Workspace Boundary

Status:
- [x] Phase 0 fetch/readback: `origin/main` read back as `5b4b6ef6536c0cf8b53528b55520e5005caeb508` after fetch; branch `codex/s0a-real-run-cancel-workspace-boundary-20260711` was created from that ref.
- [x] Phase 1 source review: root `AGENTS.md`, PRD v2, tech acceptance, guardrails, foundation roadmap, GitHub workflow, and current cancel/workspace/lease paths reviewed. The requested `docs/operations/2026-07-10-security-release-blocker-triage.md` is absent on current main and is not treated as read evidence.
- [x] Phase 1 issue: GitHub issue #383 created for this lane.
- [x] Phase 2 TDD RED: added failing coverage for owner cancel audit, persisted runtime-handle cleanup, missing forged lease handle, workspace route/repository/schema guards, worker cancel-vs-executor exception race, frontend canonical run cancel, and DSN-gated PostgreSQL schema application. RED evidence: `python -m pytest tests/test_run_control_routes.py tests/test_repositories.py tests/test_routes.py tests/test_schema.py tests/test_worker.py -q --basetemp .pytest-tmp\run-s0a-red-20260711-001` failed on the expected S0A assertions; frontend initially failed because local `node_modules` was absent.
- [x] Phase 3 GREEN implementation: implemented repository/route workspace guard, additive schema constraints and runtime handle columns, owner cancel audit, fail-closed legacy session cancel, persisted-handle sandbox cleanup, worker exception-after-cancel handling, frontend `cancelRun(run_id)` stop-generation path, and the approved minimal runtime producer extension so `SandboxRuntime` passes trusted provider `ContainerLease` handles into `runtime_*` columns.
- [x] Phase 4 verification: targeted backend affected slice passed after the runtime producer and worker stale-terminal fixes (`642 passed, 1 skipped` with `.pytest-tmp\run-s0a-affected-backend-20260711-013`); focused frontend node tests passed (`12 passed`); `corepack pnpm exec tsc -b` and `python -m compileall -q app tools scripts` passed. PostgreSQL gate is real but not passed: `tests/test_s0a_schema_postgres.py` clean-skipped because `AI_PLATFORM_S0A_SCHEMA_TEST_DSN` is not configured.
- [ ] Phase 5 review/PR/CI: first independent review found blockers #1-#4; #1 was fixed after main-control approved minimal scope expansion to `app/runtime/sandbox/runtime.py` and `tests/test_sandbox_runtime.py`; #2-#4 fixed locally. A later broad review found worker stale-terminal event leakage after repository terminal no-op; that has been fixed and covered in `tests/test_worker.py`. Final exact-head broad re-review, ready PR, and observed required CI are pending.

Scope:
- Frontend stop generation must call the platform run cancel endpoint with the current trusted `run_id`; it must fail closed when no trusted run id exists.
- Owner and admin cancel must stay tenant-scoped, authorization-scoped, idempotent for accepted cancel states, and auditable.
- Sandbox cleanup must only stop runtime handles that were persisted and validated by the platform, not user-controlled request data, executor callback data, or arbitrary payload fields.
- Worker cancellation races must preserve accepted cancel terminal semantics and must not overwrite a cancelled run with `executor_failure`.
- Run creation must validate workspace tenant ownership at route and repository boundaries before any session or run insert.

Out of scope:
- No 211 deployment or smoke in this lane.
- No changes to `app/routes/files.py`, S0B tests, capability distribution, Skill/MCP selected execution contract, S2B-FE auth/session files, executor provider internals, queue fencing/S3, multi-agent product routes, deploy/compose/CI, or unrelated refactors.
- Main-control approved a minimal exception for `app/runtime/sandbox/runtime.py` and direct runtime tests only to persist trusted provider-returned runtime handles; container provider, executor auth/provider internals, queue, deploy, and 211 remain out of scope.
