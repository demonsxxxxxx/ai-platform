# ai-platform Frontend Web

Status: source ownership migration for GitHub issue #17.

This directory contains the React/Vite frontend source used as the current
ai-platform authenticated workbench. The operator-configured frontend entry is served through
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

Run from `frontend/web`:

```powershell
pnpm install --frozen-lockfile
pnpm run projection:audit
pnpm run test:model-admin
pnpm run lint
pnpm run build
```

`pnpm run ci:verify` starts with `pnpm run projection:audit` and is the frontend
CI gate. Read the current audit output for source violations and route-policy
gaps; this README does not establish a current passing result. The audit emits
an active-browser route inventory so ordinary-user review can distinguish live
legacy routes from inactive imported source. A `pass_with_policy_gaps` result
allows the remaining CI checks to continue while preserving the reported
G6/G9 rollout gaps. Neither that status nor a passing frontend CI run closes the
full Agent Frontend V1 rollout gate.

`pnpm run test:model-admin` runs the mounted model administration and catalog
context regressions, including stale probes, credential reuse, cancellation and
successive publication revisions. It is included in `pnpm run ci:verify` and uses
synthetic endpoints and credentials without contacting a model provider.

From the repository root, `python tools/frontend_release_traceability.py
--format json` records the frontend package hashes, workflow contract, static
`dist/` manifest, and packaged frontend image status. The packaged image status
is expected to be `configured` only when `frontend/web/Dockerfile`,
`frontend/web/nginx.conf.template`, and
`deploy/ai-platform/docker-compose.yml` are present, pass the
secret/private-payload denylist scan, and keep the required build provenance,
upload, proxy-timeout, and compose argument contract. `configured` describes
source configuration only, not an image build, deployment, or runtime acceptance.
Static `dist/` is same-commit evidence only when provenance records a known
commit, clean dirty state, and matching frontend source hashes. Local Windows development does not
build Docker images. GitHub Actions performs a non-push packaged-image
build/provenance check for relevant frontend changes, but release acceptance
still requires image smoke on the exact controlled Docker-capable host.

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

- Auth/session: `/api/ai/auth/bootstrap`, `/api/ai/auth/login`,
  `/api/ai/auth/me`, `/api/ai/auth/logout`.
- Chat/session stream: `/api/chat/stream`, `/api/chat/sessions/{id}/stream`,
  `/api/sessions/*`.
- Uploads: `/api/ai/files` and `/api/ai/files/uploads/*`.
- Playback: `/api/ai/runs/{run_id}/playback`.
- Artifacts:
  `/api/ai/artifacts/{artifact_id}/download` and
  `/api/ai/artifacts/{artifact_id}/preview`.
- Memory/context management:
  `/api/ai/memory/*` and `/api/ai/admin/memory/*` through public/admin
  projections.

## Migration Boundaries

The 2026-06-06 import was source-first; its provenance hashes above are a
historical snapshot. Current delivery uses the packaged frontend described below:

- Backend scheduling, sandbox, auth/session, DB schema, and compose behavior
  are unchanged by the frontend import.
- The frontend image is now configured in the main Compose file; actual
  deployment and acceptance still require exact-package evidence.
- Historical admin/model/MCP/persona/sandbox source remains under ai-platform
  projection and policy audit before ordinary-user rollout.
- `pnpm run projection:audit` runs the repository-owned static projection audit
  and is the first step in `pnpm run ci:verify`; `pass_with_policy_gaps`
  continues to lint, type-check, and build while preserving G6/G9 rollout
  blockers.
- Issue #22 office-user context continuity and sandbox cold-start UX is not
  implemented here; it should shape later workbench design.
- G8 platform-level multi-run orchestration and G10 workflow-owner rollout work
  are not implemented by this migration.

## Packaged Delivery

The main [Compose file](../../deploy/ai-platform/docker-compose.yml) defines
separate backend and frontend application images:

- `AI_PLATFORM_IMAGE` supplies API, Worker, migration, and backend utility roles.
- `AI_PLATFORM_FRONTEND_IMAGE` is required for the `frontend` service, serving
  static files with same-origin `/api/*` proxying.

There is no separate frontend Compose overlay. The frontend service does not
receive backend, model-gateway, or sandbox secrets. Postgres, Redis, MinIO, and
sandbox executor images remain separate infrastructure services.

Use the immutable package and exact-image procedure in the
[release runbook](../../docs/operations/release-operations-runbook.md).
From the extracted, verified package, its supported deployment entry is
`python3 deploy.py --env-file /absolute/path/to/.env`; run it only with deployment
authorization and the runbook's preflight and migration requirements satisfied.
Do not substitute a source-checkout Compose invocation for that procedure.

The CI image gate validates the image can be built and that
`ai-platform-build-provenance.json` inside the image matches the workflow commit;
runtime proxy smoke remains a separate release gate. Compose configuration or
one-command startup alone does not establish release acceptance.
