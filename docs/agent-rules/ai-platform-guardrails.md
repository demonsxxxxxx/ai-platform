# ai-platform Guardrails

## Authority

This file defines repository-level product and engineering guardrails for the
current `ai-platform` control plane.

Use these sources together, in this order, before implementation work:

1. Current user instruction in the active session.
2. `docs/superpowers/specs/2026-05-29-ai-platform-final-product-prd.md`.
3. `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`.
4. This guardrails file.
5. Current code, tests, and fresh 211 runtime evidence.

If these sources disagree, stop broad implementation and narrow the work to
source-authority repair first.

## Current Source Boundaries

- Local source is the current `ai-platform` repository root.
- 211 backend source is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`.
- 211 deploy composition target is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform` so the committed compose build context stays repo-local.
- If live container labels still point to `/home/xinlin.jiang/ai-platform-phaseb/deploy/ai-platform`, treat that as stale runtime evidence to reconcile before claiming source-authority closure.
- 211 frontend entry is `http://10.56.0.211:18001/`.
- 211 frontend is the LambChat thin shell served by `tools/serve_lambchat_thin_shell.py`.
- 211 backend API is `ai-platform-api:8020`.
- Active platform containers are `ai-platform-api`, `ai-platform-worker`,
  `ai-platform-postgres`, `ai-platform-redis`, and `ai-platform-minio`.

Do not make product or implementation decisions from directories, ports, or
services outside the current PRD, roadmap, current code, and current 211
runtime evidence.

## P0 Gate Order

Current P0 work must move these gates toward closure:

1. Memory / Context.
2. MCP / Tool Permission.
3. Event / Playback Contract.
4. Sandbox Lease / Workspace.
5. Agent Frontend V1 verification for the above public projections.

Long Task / Multi-Agent Runtime work must wait until these gates have current
code, focused tests, review, and 211 smoke evidence.

## Implementation Guardrails

- Read the relevant current code and tests before changing a slice.
- Add or update focused tests for every changed contract.
- Keep ordinary-user projections free of raw skill ids, storage keys, runtime
  paths, command fingerprints, executor private payloads, and secret-like data.
- Keep Admin projections same-tenant and operational; do not expose user secret
  payload or executor private payload.
- Keep long-term cross-session memory fail-closed until policy, retention,
  redaction, delete, and approval paths are complete.
- Keep write-capable or risky tools fail-closed unless a current platform
  permission decision permits the exact call.
- Keep sandbox fake provider as test-only evidence. Production sandbox evidence
  requires Docker-capable 211 or another controlled Docker host.
- Do not mount Docker socket in the default compose file. Docker provider checks
  must use the sandbox compose overlay or a controlled runtime environment.
- Do not copy, export, commit, or quote real `.env` files. Use committed
  `.env.example` templates and redacted runtime evidence only.
- Keep root `.dockerignore` exclusions for real env files aligned with the
  repo-local Docker build context; `.gitignore` is not a Docker build-context
  boundary.

## Review And Deployment Guardrails

- Stage or high-risk P0 completion requires independent multi-agent review with
  `gpt-5.5` and `reasoning_effort=xhigh`.
- Only fix review feedback after validating it against current PRD, roadmap,
  guardrails, code, and tests.
- Run local focused tests before full local verification.
- Run Docker compose, image build, container restart, and sandbox Docker smoke
  only on 211 or another Docker-capable host.
- 211 verification must prove the current deployed containers, image identity,
  API health, and relevant contract behavior after deployment.
