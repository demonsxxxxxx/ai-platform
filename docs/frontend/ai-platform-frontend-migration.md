# ai-platform Frontend Source Migration

Date: 2026-06-06

This document records the GitHub issue #17 source-ownership migration step. It
keeps the current PRD, foundation roadmap, guardrails, code, GitHub issues, and
211 runtime evidence aligned without changing backend scheduling, sandbox,
auth/session, DB schema, or compose delivery behavior.

## Current Gate Judgment

| Area | Current judgment |
| --- | --- |
| #15 roadmap governance | Open. The roadmap has a gate-based sync, but release evidence and execution history are still mixed in older sections. |
| #16 tenant-aware concurrency | Open. #20 closed the current G5 scheduling/admission gaps, but #21 still blocks capacity claims and production default increases. |
| #17 frontend source ownership | In progress. Source now lives under `frontend/web`, has local install/lint/build evidence, and exposes a reusable `ci:verify` plus release traceability CLI; full closure still needs CI enforcement and later image integration. |
| #20 G5 scheduling/admission gaps | Closed on 2026-06-06 by `f5da825` and `e203412`, with local full pytest and 211 smoke evidence recorded in the issue. |
| #21 capacity baseline | Open. Current default active worker execution is still about three runs, and load-test evidence is required before raising concurrency defaults. |
| #22 office UX/context continuity | Open future product issue. It should inform workbench design but is not implemented in this migration. |

Gate summary:

- G0 Source Authority is improved because frontend source is now in the same
  repository as backend and worker code. Frontend release traceability can now
  point to the same commit as backend/worker changes through
  `tools/frontend_release_traceability.py`, but CI still needs to enforce
  frontend checks before this is a full release gate.
- G1 Security MVP remains dependent on company auth/session, RBAC, tenant
  isolation, redaction, and frontend projection audit.
- G2-G7 backend/control-plane foundations have substantial current coverage,
  with sandbox production Docker hardening still blocking high-risk expansion.
- G8/G10 Long Task and Multi-Agent work are not implemented by this migration
  and must not expand ordinary-user exposure until #16/#21 and
  frontend/user-loop gates pass.
- G9 Agent Frontend V1 is the active frontend gate. Source migration moves the
  gate forward, but imported legacy surfaces still need projection audit and
  product acceptance before broad trial.

## Source And Runtime Evidence

Current 211 frontend runtime:

```text
python3 tools/serve_lambchat_thin_shell.py --host 0.0.0.0 --port 18001 --root /home/xinlin.jiang/lambchat-poc/frontend-dist-ai-platform --api-base http://127.0.0.1:8020
```

211 health evidence gathered during this migration:

- `http://127.0.0.1:18001/` returned HTTP 200 with the built SPA.
- `http://127.0.0.1:18001/api/ai/health` returned `{"status":"ok"}` through
  the frontend proxy.

The 211 LambChat frontend source worktree is dirty and contains ai-platform
customizations plus safety tests. The local import source key files matched the
211 source for `package.json`, `pnpm-lock.yaml`, `vite.config.ts`, and
`src/App.tsx` by SHA-256 before migration.

## Repository Shape

```text
ai-platform/
  app/                  # backend API, repositories, worker support
  deploy/ai-platform/   # current backend/worker compose target
  frontend/web/         # React/Vite frontend source imported for #17
  tools/                # thin-shell serving and verification helpers
  docs/
```

The import excludes:

- `node_modules/`
- `dist/`
- `.git/`
- `.env` and `.env.*`
- TypeScript build-info files

## API Contract Inventory

The frontend must keep browser calls on same-origin `/api/*`. In local Vite
development, `/api/*` is proxied to `VITE_AI_PLATFORM_API_TARGET`, defaulting
to `http://127.0.0.1:8020`; in 211, the thin shell proxies to ai-platform. The
browser-facing API base remains same-origin; split frontend/backend browser API
origins are not part of the current contract.

Required public/user contracts:

- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/refresh`
- `POST /api/chat/stream`
- `GET /api/chat/sessions/{session_id}/stream`
- `/api/sessions/*`
- `/api/upload/*`
- `GET /api/ai/runs/{run_id}/playback`
- `POST /api/ai/runs/{run_id}/tool-permissions/{request_id}/decision`
- `GET /api/ai/artifacts/{artifact_id}/download`
- `GET /api/ai/artifacts/{artifact_id}/preview`
- `/api/ai/memory/*`

Required admin/operator contracts must stay admin-only and same-tenant:

- `/api/admin/*`
- `/api/ai/admin/*`
- Admin Runtime overview and backpressure projections.
- Admin Runtime capacity and governance readiness projections.
- Admin memory policy/inventory/retention cleanup projections.
- Admin tool policy projections.

Projection boundary:

- Ordinary users consume public projections only.
- Admin users consume same-tenant operational projections only.
- The frontend must not consume executor private payload, raw storage keys,
  sandbox work directories, command fingerprints, raw runtime paths, secret-like
  values, raw request payloads, raw decision payloads, or raw skill staging
  paths.

## Public/Admin Projection Audit

Static audit on 2026-06-07:

- Core ai-platform playback, memory, event, artifact, and reveal-preview code
  consumes same-origin `/api/*` and ai-platform `/api/ai/*` projections.
- `frontend/web/src/services/api/runPlayback.ts`,
  `frontend/web/src/services/api/memory.ts`,
  `frontend/web/src/hooks/useAgent/eventProcessor.ts`, and artifact/reveal
  helpers define forbidden-key lists and strip private payload, storage key,
  work directory, runtime path, command fingerprint, resource limit, and raw
  payload fields before rendering.
- `frontend/web/src/services/api/config.ts` keeps browser API calls
  same-origin; Vite development proxy remains a dev-time adapter only.
- Admin Runtime now includes capacity and G6 governance readiness projections
  for operator visibility. Frontend UI work must consume these public/admin
  projections rather than rebuilding state from executor runtime payloads.

Remaining audit risks:

- Imported legacy LambChat panels still include admin/model/MCP/envvar/channel
  surfaces that can handle user-entered credentials such as model API keys or
  channel app secrets. These are not executor private payload reads, but they
  must remain admin/policy-gated or hidden before ordinary-user Agent Frontend
  rollout.
- Legacy `/api/memory/*`, `/api/mcp/*`, `/api/env-vars/*`,
  `/api/agent/models/*`, and channel/admin endpoints need route-by-route
  policy mapping to ai-platform public/admin projections before G9 ordinary-user
  acceptance.

## Multi-Image Delivery Plan

Current state:

- API and worker still use the existing Python source/image path.
- The 211 frontend remains a thin-shell static dist served by
  `tools/serve_lambchat_thin_shell.py`.
- `deploy/ai-platform/docker-compose.yml` is not changed by this migration.

Future packaged direction:

| Image | Responsibility |
| --- | --- |
| `ai-platform-api` | FastAPI control plane and public/admin routes. |
| `ai-platform-worker` | Queue leasing, run execution, maintenance ticks, sandbox cleanup, dispatcher maintenance. |
| `ai-platform-frontend` | Static `frontend/web/dist` served by nginx or equivalent, with `/api/*` proxying to the API. |

Future integration steps:

1. Add frontend CI steps for install, lint, tests, and build.
2. Add a project-owned frontend Dockerfile or static-server image definition.
3. Add a compose overlay for frontend image validation on a Docker-capable host.
4. Record release evidence tying API, worker, and frontend image artifacts to
   the same git commit.

## Build And Release Traceability

Local verification should use the package-manager version pinned in
`frontend/web/package.json`. On machines where `pnpm` is not on `PATH`, use
Corepack:

```powershell
cd frontend/web
corepack pnpm --version
corepack pnpm install --frozen-lockfile
corepack pnpm run ci:verify
```

Current local evidence on 2026-06-07:

- `corepack pnpm --version` returned `10.32.1`.
- `corepack pnpm install --frozen-lockfile` exited 0.
- `corepack pnpm lint` exited 0 with 1 warning in
  `src/components/chat/ChatMessage/sessionImageGallery.tsx`.
- `corepack pnpm build` exited 0; Vite reported large chunk warnings.
- `frontend/web/package.json` now defines `ci:verify` as
  `eslint . && tsc -b && vite build`, so the script works even when this
  Windows workstation can only start pnpm through Corepack.
- `python tools/frontend_release_traceability.py --format json` records the
  current git commit, dirty flag, package/lockfile hashes, CI commands, and
  `dist/` status without printing local absolute paths, `.env` values, or
  secret-like data.

These warnings do not block the source migration, but they remain frontend
hardening work before broader Agent Frontend V1 rollout. Generated `dist/` is
not committed; release evidence should tie the built artifact or frontend image
back to the same git commit as API and worker.

## Remaining Risks

- The imported frontend still contains legacy LambChat admin/model/MCP/persona
  and sandbox-related panels. They must be audited or hidden behind ai-platform
  policy before ordinary-user rollout.
- The source worktree on 211 was dirty at migration time; this import captures
  the hash-matched snapshot but does not clean the upstream LambChat POC repo.
- #21 capacity/load-test evidence remains open, so no production concurrency
  defaults should be raised from this migration.
- #22 document-centric context/workbench UX remains future work and is not part
  of this source move.

## Verification Commands

Backend/source-authority focused verification:

```powershell
python -m pytest tests/test_source_authority_docs.py tests/test_serve_lambchat_thin_shell.py tests/test_lambchat_frontend_compat.py tests/test_lambchat_projection_contract.py -q --basetemp .pytest-tmp\frontend-migration
python tools/frontend_release_traceability.py --format json
python tools/governance_readiness.py --format json
```

Frontend verification:

```powershell
cd frontend/web
pnpm install --frozen-lockfile
pnpm lint
pnpm build
```

211 smoke evidence should remain limited to current thin-shell health and
same-origin `/api/*` behavior until a frontend image is explicitly introduced.
