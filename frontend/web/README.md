# ai-platform Frontend Web

Status: source ownership migration for GitHub issue #17.

This directory contains the React/Vite frontend source used as the current
ai-platform authenticated workbench. The 211 entry remains
`http://10.56.0.211:18001/`, served through
the repository static frontend service with same-origin `/api/*` proxying to the
ai-platform API. Keeping this source in the repository does not create a new
public frontend entry and does not close the full Agent Frontend V1 rollout
gate by itself.

## Provenance

The source was originally imported from the historical 211 proof-of-concept
frontend. On 2026-06-06, these key files matched between that source and the
local import snapshot:

| File | SHA-256 |
| --- | --- |
| `package.json` | `f8ce35deb23391f84a9d48204b886dcc2ca47804a54cdcc96ed9c633d9a1c916` |
| `pnpm-lock.yaml` | `1ae5ae064dec4bae4e0b58668a957b363da29c9f843c4ec0765f0a9eabbc177d` |
| `vite.config.ts` | `eb655979deb801da7d500a3b4363e6706d8a751a3084e27ead0873f16f2db22b` |
| `src/App.tsx` | `03d63757c23f4fe7455b53b8e3609a15fa52e2ac97dd3191969bf7631bfefefd` |

Generated output, local dependencies, real env files, and TypeScript build-info
files are intentionally excluded from the repository import.

## Development

```powershell
pnpm install --frozen-lockfile
pnpm run projection:audit
pnpm run lint
pnpm run build
```

`pnpm run ci:verify` starts with `pnpm run projection:audit` and is the release
gate. The current audit status is `pass_with_policy_gaps`: active browser
entry files are blocked if they consume executor-private or secret-like
projection terms, while quarantined legacy source and route-policy gaps stay
visible for G6/G9 follow-up. The audit also emits an active-browser route
inventory so ordinary-user review can distinguish live legacy routes from
inactive imported source. This status lets `ci:verify` continue to lint,
type-check, and build, but it does not close the Agent Frontend V1 rollout gate.

From the repository root, `python tools/frontend_release_traceability.py
--format json` records the frontend package hashes, workflow contract, static
`dist/` manifest, and packaged frontend image status. The packaged image status
is expected to be `configured` only when `frontend/web/Dockerfile`,
`frontend/web/nginx.conf.template`, and
`deploy/ai-platform/docker-compose.frontend.yml` are present, pass the
secret/private-payload denylist scan, and keep the required build provenance,
upload, proxy-timeout, and compose argument contract. Static `dist/` is
same-commit evidence only when provenance records a known commit, clean dirty
state, and matching frontend source hashes. Local Windows development does not
build Docker images. GitHub Actions performs a non-push packaged-image
build/provenance check for relevant frontend changes, but release acceptance
still requires image smoke on 211 or another Docker-capable host.

For local development, Vite proxies `/api/*` to `VITE_AI_PLATFORM_API_TARGET`,
defaulting to `http://127.0.0.1:8020`. For the intranet deployment, keep the
browser using same-origin `/api/*`; the deployed thin shell proxies those calls
to ai-platform. Split frontend/backend browser API origins are not part of the
current ai-platform contract.

## API Boundary

The frontend must consume ai-platform public/admin projections only. It must not
read, persist, or render executor private payload, raw storage keys, sandbox
work directories, command fingerprints, secret-like values, or raw runtime
paths.

Current platform-facing contract areas:

- Auth/session: `/api/auth/login`, `/api/auth/me`, `/api/auth/refresh`.
- Chat/session stream: `/api/chat/stream`, `/api/chat/sessions/{id}/stream`,
  `/api/sessions/*`.
- Uploads: `/api/upload/*`.
- Playback: `/api/ai/runs/{run_id}/playback`.
- Tool permission decisions:
  `/api/ai/runs/{run_id}/tool-permissions/{request_id}/decision`.
- Artifacts:
  `/api/ai/artifacts/{artifact_id}/download` and
  `/api/ai/artifacts/{artifact_id}/preview`.
- Memory/context management:
  `/api/ai/memory/*` and `/api/ai/admin/memory/*` through public/admin
  projections.

## Migration Boundaries

This import is intentionally source-first:

- Backend scheduling, sandbox, auth/session, DB schema, and compose behavior
  are unchanged by the frontend import.
- The current 211 static frontend deployment remains the active runtime entry
  until a frontend image is explicitly added and verified.
- Historical admin/model/MCP/persona/sandbox source remains under ai-platform
  projection and policy audit before ordinary-user rollout.
- `pnpm run projection:audit` runs the repository-owned static projection audit
  and is the first step in `pnpm run ci:verify`; `pass_with_policy_gaps`
  continues to lint, type-check, and build while preserving G6/G9 rollout
  blockers.
- Issue #22 office-user context continuity and sandbox cold-start UX is not
  implemented here; it should shape later workbench design.
- G8/G10 Long Task and Multi-Agent work are not implemented by this migration.

## Future Multi-Image Direction

Later packaged delivery should use three project-owned images:

- `ai-platform-api` for FastAPI control-plane routes.
- `ai-platform-worker` for queue, sandbox cleanup, memory cleanup, and
  dispatcher maintenance.
- `ai-platform-frontend` for static files built from `frontend/web/dist`,
  served by nginx or an equivalent static server with `/api/*` proxying.

The optional `deploy/ai-platform/docker-compose.frontend.yml` overlay now
defines the frontend image boundary for Docker-capable validation. It is not a
compose one-command acceptance gate and does not pass backend, model-gateway, or
sandbox secrets into the frontend service. The CI image gate validates the
image can be built and that `ai-platform-build-provenance.json` inside the image
matches the workflow commit; runtime proxy smoke remains a separate release
gate.

Postgres, Redis, MinIO, and sandbox executor images remain separate
infrastructure services. Docker compose one-command startup is not a current
acceptance gate for this migration.
