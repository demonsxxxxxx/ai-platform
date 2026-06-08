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
| #17 frontend source ownership | In progress. Source now lives under `frontend/web`, has local install/lint/build evidence, exposes release traceability plus a frontend projection audit, and now has a GitHub Actions frontend workflow with passing remote run evidence. `ci:verify` starts with the projection audit launcher; the active browser entry graph is currently clear of forbidden private/secret-like projection terms, and the Profile env-var surface is no longer active. Inactive legacy secret-like model/channel/envvar sources remain quarantined and must be remapped before G9 rollout. Full closure still needs later packaged image integration and release acceptance. |
| #20 G5 scheduling/admission gaps | Closed on 2026-06-06 by `f5da825` and `e203412`, with local full pytest and 211 smoke evidence recorded in the issue. |
| #21 capacity baseline | Open. Current default active worker execution is still about three runs, and load-test evidence is required before raising concurrency defaults. |
| #22 office UX/context continuity | Open future product issue. It should inform workbench design but is not implemented in this migration. |

Gate summary:

- G0 Source Authority is improved because frontend source is now in the same
  repository as backend and worker code. Frontend release traceability can now
  point to the same commit as backend/worker changes through
  `tools/frontend_release_traceability.py`; frontend `ci:verify` starts with
  the projection audit launcher and now records active-entry projection
  evidence plus quarantined legacy source gaps.
  `.github/workflows/ai-platform-frontend.yml` now enforces the frontend checks
  for source changes, and GitHub Actions run `27104398690` passed on commit
  `11ab56c660385f6790964af3d5bd60e3d4431ff2`. Packaged image trace still
  remains before this is a full release gate.
- G1 Security MVP remains dependent on company auth/session, RBAC, tenant
  isolation, redaction, and frontend projection audit/remap evidence.
- G2-G7 backend/control-plane foundations have substantial current coverage,
  with sandbox production Docker hardening still blocking high-risk expansion.
- G8/G10 Long Task and Multi-Agent work are not implemented by this migration
  and must not expand ordinary-user exposure until #16/#21 and
  frontend/user-loop gates pass.
- G9 Agent Frontend V1 is the active frontend gate. Source migration and
  active-entry projection audit move the gate forward, but quarantined legacy
  surfaces still need ai-platform projection remap, policy enforcement, and
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
- Admin Runtime capacity, governance readiness, and observability readiness
  projections.
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
- `tools/frontend_projection_audit.py` now provides a reproducible static
  audit with schema `ai-platform.frontend-projection-audit.v1`; frontend
  `projection:audit` is wired as the first step of `ci:verify` through a
  cross-platform Python launcher and fails closed when the active browser entry
  graph consumes forbidden private or secret-like projection terms.
- The audit now separates the active browser entry graph from quarantined
  legacy source files. `/channels` and `/models` render a fail-closed
  quarantine panel until those surfaces are remapped to ai-platform public or
  same-tenant admin projections.
- Ordinary model selectors now use `frontend/web/src/services/api/modelPublic.ts`,
  which exposes only safe model options, provider names, and per-user pinned
  model preferences. Legacy model administration code remains source-visible
  but is not part of the active browser entry graph.
- The audit now emits a machine-readable legacy route policy map for each
  scanned legacy route and a separate active-browser legacy route policy map.
  It records the required governance gate, ordinary-user fail-closed exposure,
  admin projection boundary, route scope, and required remap/hide action. This
  narrows the G6/G9 gap from missing route mapping to active route enforcement
  plus inactive legacy source remap.
- `frontend/web/src/services/api/runPlayback.ts`,
  `frontend/web/src/services/api/memory.ts`,
  `frontend/web/src/hooks/useAgent/eventProcessor.ts`, and artifact/reveal
  helpers define forbidden-key lists and strip private payload, storage key,
  work directory, runtime path, command fingerprint, resource limit, and raw
  payload fields before rendering.
- `frontend/web/src/services/api/config.ts` keeps browser API calls
  same-origin; Vite development proxy remains a dev-time adapter only.
- Admin Runtime source now includes capacity, G6 governance readiness, and G9
  observability readiness projections for operator visibility. Frontend UI work
  must consume these public/admin projections rather than rebuilding state from
  executor runtime payloads.
- Settings now includes an admin-only Admin Runtime Capacity section that calls
  only `GET /api/ai/admin/runtime/overview` and displays capacity,
  backpressure, governance gaps, and missing load-test evidence. This improves
  frontend operator visibility and has 211 frontend acceptance, but still does
  not close #21, G6, or G9 because load-test evidence, legacy route remap, and
  packaged frontend image delivery/release acceptance remain open.

Remaining audit risks:

- Imported legacy LambChat panels still include admin/model/MCP/envvar/channel
  surfaces that can handle or read user-entered credentials. The projection
  audit now reports inactive secret-like model/channel/envvar sources as
  quarantined legacy source gaps rather than active browser entry violations.
  The Profile env-var tab is hidden from the active browser entry graph until
  `/api/env-vars/*` is remapped to an ai-platform projection, masked,
  admin/policy-gated, or removed before ordinary-user Agent Frontend rollout.
- Legacy `/api/memory/*`, `/api/mcp/*`, `/api/env-vars/*`,
  `/api/agent/models/*`, and channel/admin endpoints now have audit-visible
  route policy mappings, but still need actual enforcement, hiding, or remap to
  ai-platform public/admin projections before G9 ordinary-user acceptance.

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

1. Add a project-owned packaged frontend release trace once frontend image
   delivery exists.
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
corepack pnpm run projection:audit
corepack pnpm run lint
corepack pnpm run build
```

Current local and CI-contract evidence on 2026-06-08:

- `corepack pnpm --version` returned `10.32.1`.
- `corepack pnpm install --frozen-lockfile` exited 0.
- `corepack pnpm run ci:verify` exited 0 at commit
  `22dc9e61605d406f10669e4f91f4cb1a87e2094d`; Vite reported the existing
  large chunk warnings.
- `frontend/web/package.json` now defines `projection:audit` as
  `node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json`
  and `ci:verify` as
  `node scripts/run-python-tool.mjs ../../tools/frontend_projection_audit.py --format json && eslint . && tsc -b && vite build && node scripts/write-build-provenance.mjs`,
  so the script works even when this Windows workstation can only start pnpm
  through Corepack and 211 needs `python3` instead of bare `python`.
- `python tools/frontend_release_traceability.py --format json` records the
  current git commit, dirty flag, package/lockfile hashes, CI commands, a
  deterministic static `dist/` manifest, and the packaged frontend image
  delivery status without printing local absolute paths, `.env` values, or
  secret-like data. The manifest includes file count, total bytes,
  `index.html` / service-worker entry hashes when present, and a manifest hash
  plus `dist/ai-platform-build-provenance.json`. The static `dist` artifact is
  considered same-commit only when build provenance matches the current git
  commit and frontend source hashes. If provenance is missing or stale, the
  traceability CLI reports `dist.status = built_unverified` with explicit
  blockers instead of binding an old ignored `dist` directory to the current
  backend/worker commit. Until `frontend/web/Dockerfile` and
  `deploy/ai-platform/docker-compose.frontend.yml` exist and are verified on a
  Docker-capable host, the packaged image section must remain
  `not_configured` with explicit blockers.
- `python tools/frontend_projection_audit.py --format json` records the
  current production-source route inventory, active browser entry graph,
  active-browser route inventory,
  quarantined legacy source findings, CI integration status, forbidden
  secret-like projection findings, and remaining legacy route policy gaps
  without printing local absolute paths or secret-like runtime configuration.
  The current status is `pass_with_policy_gaps`; this allows local
  `ci:verify` to exercise lint/build while preserving G6/G9 rollout blockers.
- `.github/workflows/ai-platform-frontend.yml` runs on frontend source,
  `docs/frontend/**`, `tests/test_frontend_*.py`, frontend audit/traceability
  tools, and workflow changes. It executes
  `corepack pnpm install --frozen-lockfile`,
  `corepack pnpm run ci:verify`, and
  `python tools/frontend_release_traceability.py --format json` without Docker,
  compose, `.env`, or secret-dependent steps. The release traceability CLI now
  records this workflow path and hash as part of the same-commit traceability
  manifest.
- GitHub Actions run `27104398690` passed on commit
  `11ab56c660385f6790964af3d5bd60e3d4431ff2`, providing remote CI evidence for
  the frontend workflow contract.
- At commit `22dc9e61605d406f10669e4f91f4cb1a87e2094d`,
  `python tools/frontend_release_traceability.py --format json` reported
  `dirty = false` before the later build-provenance hardening and kept packaged
  frontend image delivery
  `not_configured` with blockers
  `packaged_frontend_dockerfile_missing`,
  `packaged_frontend_compose_overlay_missing`, and
  `packaged_frontend_image_trace_missing`. At that time, the latest pushed
  change after the previous frontend workflow run touched only backend tests,
  so no newer frontend GitHub Actions run was required by the workflow
  filters.
- At commit `be03c953e60489f1d27b8e6d1a0a770f11e48fb8`,
  `corepack.cmd pnpm run ci:verify` exited 0 and wrote
  `dist/ai-platform-build-provenance.json`. A fresh
  `python tools/frontend_release_traceability.py --format json` run reported
  `git.dirty = false`, `dist.status = built`,
  `dist.build_provenance.status = verified`,
  `dist.build_provenance.verified_same_commit = true`, and no static `dist`
  blockers. The packaged frontend image section intentionally remained
  `not_configured` with blockers until a frontend Dockerfile, compose overlay,
  and image trace are added and verified on a Docker-capable host.
- GitHub Actions run `27114040908` passed on commit
  `be03c953e60489f1d27b8e6d1a0a770f11e48fb8`, covering the frontend workflow
  contract after the build-provenance hardening.

These warnings do not block the source migration, but they remain frontend
hardening work before broader Agent Frontend V1 rollout. Generated `dist/` is
not committed; release evidence should tie the built artifact or frontend image
back to the same git commit as API and worker through build provenance or a
packaged frontend image trace.

## Remaining Risks

- The imported frontend still contains legacy LambChat admin/model/MCP/persona
  and sandbox-related panels. Secret-like `/channels`, `/models`, and Profile
  env-var surfaces are now quarantined from the active browser entry graph; the
  remaining route inventory still needs ai-platform remap/policy acceptance
  before ordinary-user rollout.
- The source worktree on 211 was dirty at migration time; this import captures
  the hash-matched snapshot but does not clean the upstream LambChat POC repo.
- #21 capacity/load-test evidence remains open, so no production concurrency
  defaults should be raised from this migration.
- The Admin Runtime Capacity section has local source tests, build coverage,
  and 211 frontend acceptance at commit
  `f579155f3ec0ac7e37dd7b525f8eab27f7fd2e35`; the release traceability CLI now
  records a static `dist/` manifest for that same commit and reports packaged
  frontend image delivery blockers. Packaged frontend image delivery and
  release acceptance remain pending until the image definition and compose
  overlay exist and are verified on a Docker-capable host.
- #22 document-centric context/workbench UX remains future work and is not part
  of this source move.

## Verification Commands

Backend/source-authority focused verification:

```powershell
python -m pytest tests/test_source_authority_docs.py tests/test_serve_lambchat_thin_shell.py tests/test_lambchat_frontend_compat.py tests/test_lambchat_projection_contract.py -q --basetemp .pytest-tmp\frontend-migration
python tools/frontend_release_traceability.py --format json
python tools/frontend_projection_audit.py --format json
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
