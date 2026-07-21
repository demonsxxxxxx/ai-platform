# ai-platform Guardrails

## Authority

This file defines repository-level product and engineering guardrails for the
current `ai-platform` control plane.

Use these sources together, in this order, before implementation work:

1. Current user instruction in the active session.
2. `docs/superpowers/specs/2026-06-10-ai-platform-product-prd-v2.md`.
3. `docs/superpowers/specs/2026-06-11-ai-platform-tech-acceptance.md`.
4. `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`.
5. This guardrails file.
6. Current code, tests, and fresh 211 runtime evidence.
7. GitHub issues explicitly named by the active goal and confirmed current from
   fresh GitHub state.

If these sources disagree, stop broad implementation and narrow the work to
source-authority repair first.

## Current Source Boundaries

- Local source is the current `ai-platform` repository root.
- 211 backend source is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`.
- 211 deploy composition target is `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform` so the committed compose build context stays repo-local.
- If live container labels still point to `/home/xinlin.jiang/ai-platform-phaseb/deploy/ai-platform`, treat that as stale runtime evidence to reconcile before claiming source-authority closure.
- 211 frontend entry is `http://10.56.0.211:18001/`.
- 211 frontend normal runtime is the Docker Compose `frontend` service with
  container `ai-platform-frontend` serving the built `frontend/web` artifact on
  port `18001`; Python static preview processes are legacy rollback/comparison
  only.
- 211 backend API is `ai-platform-api:8020`.
- Active platform containers are `ai-platform-api`, `ai-platform-worker`,
  `ai-platform-frontend`, `ai-platform-postgres`, `ai-platform-redis`, and
  `ai-platform-minio`.

Do not make product or implementation decisions from directories, ports, or
services outside the current PRD, roadmap, current code, and current 211
runtime evidence.

## P0 Gate Order And Current Gate Sequence

Current P0 work must move these gates toward closure:

1. Memory / Context.
2. MCP / Tool Permission.
3. Event / Playback Contract.
4. Sandbox Lease / Workspace.
5. Agent Frontend V1 verification for the above public projections.

Long Task / Platform Multi-Run Orchestration / SDK Subagent expansion must wait
until these gates have current code, focused tests, review, and 211 smoke
evidence.

The current roadmap gate sequence is stricter than the old P0-only list:

1. G0-G1 Source Authority / Security Baseline, including company AD/auth/session,
   RBAC, tenant/workspace/user isolation, redaction, repo-local deploy
   composition, and runtime label parity.
2. G2-G4 Control Plane MVP contracts for session, run, file, artifact, skill,
   tool, memory, event, and audit; executors consume platform payloads and do
   not define platform schema.
3. G5 Run Lifecycle / Worker Runtime V1, including queue, lease, heartbeat,
   retry, dead-letter, cancel, resume, checkpoint, and idempotency.
4. G6 Tool / Skill / Memory Governance, including allow/deny/ask policy,
   retention, redaction, delete, dependency, and release-policy flows.
5. G7 Sandbox / Resource Hardening, including Docker provider validation,
   egress policy, runtime quota, orphan cleanup, and container security options.
6. G8 Deferred Platform Multi-Run Gate remains a deferred parking-lot for
   platform-owned parent/child multi-run orchestration. Historical evidence and
   appendices may mention the old title "G8 Multi-Agent Controlled Beta"; do
   not use that title for current status. SDK agent/subagent behavior stays
   inside one governed platform run; the current evidence work is B3 SDK
   subagent fanout capacity, not ordinary-user platform-level multi-run
   exposure and not a beta route.
7. G9 Observability / Quality / Ops, including Admin Runtime, cost/token/latency
   metrics, error taxonomy, trace/audit export, and alerts.
8. G10 Internal Beta / Department Rollout with explicit internal workflow owner.

Compose one-command startup, packaged delivery, and public Docker convenience
are later milestones. They must not displace intranet AD/auth/session,
tenant-aware isolation, fair scheduling, operational visibility, or frontend
source/version ownership as the current platform gates.

## Implementation Guardrails

- Read the relevant current code and tests before changing a slice.
- Add or update focused tests for every changed contract.
- Treat auth/session, tenant isolation, queue, worker maintenance, run lifecycle,
  sandbox, schema, shared contracts, platform multi-run / SDK subagent
  expansion, and frontend-backend auth/session contracts as high-verification
  areas.
- Keep tenant/workspace/user boundaries explicit in queue, quota, worker
  maintenance, memory cleanup, dispatcher, and Admin operational projections.
- Do not let AD/company auth stand in for per-tenant quota, fair scheduling, or
  noisy-neighbor backpressure.
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
- Do not add local-only frontend or compose assumptions that replace the
  current 211 intranet entry. Frontend source is maintained in `frontend/web`;
  its build and image provenance must remain traceable to the exact Git commit.
  It must consume only ai-platform public/admin projections and never executor
  private payload.
- Do not copy, export, commit, or quote real `.env` files. Use committed
  `.env.example` templates and redacted runtime evidence only.
- Keep root `.dockerignore` exclusions for real env files aligned with the
  repo-local Docker build context; `.gitignore` is not a Docker build-context
  boundary.

## Review And Deployment Guardrails

- The standard 211 source rollout path is `tools/release_authority.py
  deploy-main-commit` with an explicit full commit fetched from authoritative
  `origin/main`. The tool is the preferred implementation of the release
  invariants, not an independent product-acceptance gate.
- If the standard tool cannot safely execute for a tool-specific reason, do not
  silently bypass it. A break-glass release requires explicit user approval of
  the exact fallback plan and one project-bound persistent release owner. The
  fallback must prove the same fetched-main ancestry, clean isolated checkout,
  immutable image provenance, single lease, rollback subject, and final parity.
  Never use a local source archive, hand-copied frontend dist, or dirty
  coordination checkout.
- Git-native release preparation must fail closed when the commit is not
  reachable from fetched main, the versioned checkout is dirty, contains
  ignored worktree files, or is mismatched, an interrupted staging directory
  remains, a release path escapes through a symlink or traversal, or canonical
  and compatibility image provenance labels disagree. A release-owner
  post-merge 211 rollout is still required before claiming `211 verified` or
  source-authority closure.
- Goal-sized work and gate closures should follow
  `docs/agent-rules/github-issue-pr-workflow.md`: issue -> PR -> review ->
  merge -> deploy/smoke -> close issue. Do not close or auto-close an issue
  until the linked PR has merged and required local, review, docs, and 211
  evidence are recorded.
- Stage or high-risk completion requires independent multi-agent review. If the
  delegation tool exposes per-agent model and reasoning controls, set them
  deliberately for task complexity. If it does not expose those fields, record
  the inherited/default configuration and do not claim a model-specific or
  reasoning-specific gate.
- Only fix review feedback after validating it against current PRD, roadmap,
  guardrails, code, and tests.
- Use layered verification: targeted tests for small/local changes, related
  module plus key-path tests for medium changes, and higher verification for
  the high-risk areas named above.
- Run targeted tests for the changed or affected modules plus the relevant
  integration or smoke checks before PR, deployment, merge, or stage-gate
  closure. Do not require full-repository pytest as a routine gate.
- Run Docker compose, image build, container restart, and sandbox Docker smoke
  only on 211 or another Docker-capable host.
- 211 verification must prove the current deployed containers, image identity,
  API health, and relevant contract behavior after deployment.
