# Issue 156 Formal Frontend Runtime Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close GitHub issue #156 by replacing the 211 Python static preview as the normal frontend serving path with a formal `ai-platform-frontend` Docker Compose service on port `18001`.

**Architecture:** Reuse the existing `frontend/web/Dockerfile` nginx static image and make it part of the repo-local `deploy/ai-platform/docker-compose.yml` runtime. Keep the old Python static release helper only as a legacy preview/rollback artifact path, while runtime evidence and smoke checks prove that Compose owns `18001`.

**Tech Stack:** Docker Compose v2, nginx static frontend image, React/Vite `frontend/web`, Python traceability and smoke-contract tools, GitHub PR checks, 211 SSH runtime verification.

## Global Constraints

- The normal 211 frontend runtime must be a Docker Compose service named `frontend` with container name `ai-platform-frontend`.
- Host port `18001` must map to the frontend container's nginx port `8080`.
- `/api/*` must proxy to the API service through `AI_PLATFORM_API_UPSTREAM`, defaulting to `http://api:8020`.
- The frontend image must carry build provenance through `AI_PLATFORM_BUILD_COMMIT`, `AI_PLATFORM_BUILD_DIRTY`, `org.opencontainers.image.revision`, `ai-platform.source-revision`, and `ai-platform-build-provenance.json`.
- No committed file may contain real `.env` values, credentials, API keys, passwords, or account passwords.
- 211 deployment evidence must distinguish `PR ready`, `merged`, `211 verified`, and `gate closable`.
- Issue #156 can close only after code is merged, 211 `docker compose ps` shows the formal frontend service on `18001`, Python preview is not the normal serving owner, login/chat/API health smoke passes, rollback is documented, and review evidence is recorded.

---

### Task 1: Main Compose Runtime

**Files:**
- Modify: `deploy/ai-platform/docker-compose.yml`
- Modify: `deploy/ai-platform/.env.example`
- Test: `tests/test_frontend_release_traceability.py`

**Interfaces:**
- Consumes: existing `frontend/web/Dockerfile` and `frontend/web/nginx.conf.template`.
- Produces: `frontend` service in the default Compose runtime with `container_name: ai-platform-frontend`.

- [ ] Add the `frontend` service to the default Compose file.
- [ ] Add non-secret frontend runtime variables to `.env.example`.
- [ ] Assert the service name, container name, build provenance args, API upstream, and `18001:8080` mapping in tests.

### Task 2: Traceability And CI Contract

**Files:**
- Modify: `tools/frontend_release_traceability.py`
- Modify: `tests/test_frontend_release_traceability.py`
- Modify: `tests/test_frontend_ci_workflow.py`
- Modify: `.github/workflows/ai-platform-frontend.yml`

**Interfaces:**
- Consumes: main Compose frontend service from Task 1.
- Produces: traceability output that fails closed when the formal frontend Compose service is missing or has policy gaps.

- [ ] Add a formal frontend service section to the release traceability output.
- [ ] Require the default Compose file in frontend workflow path filters.
- [ ] Keep packaged image scanning secret-safe by scanning the frontend-only overlay and using targeted checks for the main Compose file.

### Task 3: Operator Docs And Rollback

**Files:**
- Modify: `docs/operations/frontend-static-release-deploy.md`
- Modify: `docs/frontend/prd-frontend-closure-matrix.md`

**Interfaces:**
- Consumes: Task 1 runtime and Task 2 traceability.
- Produces: operator guidance for formal deploy, health checks, rollback, and legacy Python preview boundaries.

- [ ] Rewrite the operations doc so the normal path is Docker/nginx Compose, not Python static preview.
- [ ] Document build, deploy, smoke, provenance, and rollback commands.
- [ ] Mark Python static service as legacy preview/rollback only.

### Task 4: Local Verification And PR Update

**Files:**
- Modify only files changed by Tasks 1-3.

**Interfaces:**
- Consumes: completed code/docs changes.
- Produces: committed and pushed PR update.

- [ ] Run Python compile check.
- [ ] Run targeted pytest for frontend deploy/traceability/workflow checks.
- [ ] Run frontend `corepack pnpm run ci:verify`.
- [ ] Run traceability and packaged runtime smoke contract tools.
- [ ] Run `git diff --check`.
- [ ] Commit and push the PR branch.

### Task 5: Review And 211 Closure Evidence

**Files:**
- No required source edits unless review finds issues.

**Interfaces:**
- Consumes: pushed PR update.
- Produces: subagent review evidence, 211 deployment evidence, and issue closure evidence.

- [ ] Run subagent code review for the branch diff and fix Critical/Important findings.
- [ ] Deploy the formal frontend runtime to 211.
- [ ] Verify `docker compose ps` shows `ai-platform-frontend` bound to `18001`.
- [ ] Verify no standalone Python preview process is required for normal `18001` serving.
- [ ] Verify `/auth/login`, logged-in `/chat` composer, `/api/ai/health`, `/healthz`, and build provenance.
- [ ] Merge PR and close #156 only after all acceptance evidence is current.
