# ai-platform Guardrails

## Authority

This file defines repository-level product and engineering guardrails for the
current `ai-platform` control plane.

Use these sources together, in this order, before implementation work:

1. Current user instruction in the active session.
2. This guardrails file.
3. Current code, tests, and fresh s72 runtime evidence when runtime evidence is
   authorized and available.
4. GitHub issues explicitly named by the active goal and confirmed current from
   fresh GitHub state.

If these sources disagree, stop broad implementation and narrow the work to
source-authority repair first.

## Current Source Boundaries

- Local source is the current `ai-platform` repository root.
- s72 is the only future release and runtime target. The canonical managed
  platform root is `/opt/ai-platform`, with exact detached checkouts below
  `/opt/ai-platform/releases`; the canonical OpenSandbox gateway releases remain
  below `/opt/opensandbox-gateway/releases`.
- The only future Compose selection is the base file plus
  `deploy/ai-platform/docker-compose.s72-colocation.yml`, invoked through
  `tools/s72_colocation_authority.py`.
- Platform API, worker, frontend, PostgreSQL, Redis, and MinIO are the trusted
  control plane. OpenSandbox/runsc containers are the untrusted execution plane
  even though both planes share the s72 host.
- Fresh s72 preflight and runtime parity are required before claiming runtime
  state. Committed paths and historical evidence do not prove s72 readiness.
- 211 paths, ports, data, and secrets are legacy-only. Do not access, copy,
  migrate, stop, or use 211 as a prerequisite for a future release.

Do not make product or implementation decisions from directories, ports, or
services outside these guardrails, current code, and current s72 runtime
evidence.

## Implementation Guardrails

- Read the relevant current code and tests before changing a slice.
- Add or update focused tests for every changed contract.
- Treat auth/session, tenant isolation, queue, worker maintenance, run lifecycle,
  sandbox, schema, shared contracts, platform multi-run / SDK subagent
  expansion, and frontend-backend auth/session contracts as high-verification
  areas.
- Keep Agent/Harness orchestration behind the Engine adapter boundary. The
  platform owns admission, authorization, context binding, queueing, sandbox
  policy, persistence, and public projections; the selected Engine SDK owns
  model/tool loops and any internal subagent coordination.
- Platform-owned multi-run admission is retired. Client input containing
  `execution_mode=multi_agent`, `multi_agent_steps`, or
  `multi_agent_dispatch` must fail closed instead of creating platform child
  runs. Generic run steps and SDK-originated semantic subagent events remain
  valid public projection inputs and must not imply a platform dispatcher.
- Engine-specific SDK types and callbacks must terminate inside the Engine
  adapter. Routes, repositories, queue contracts, and public SSE contracts
  must remain stable when replacing the current Claude Agent SDK adapter with
  another Harness such as Pi.
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
  requires the Docker-capable s72 authority and a real ordinary-user smoke.
- Do not mount Docker socket in the default compose file. Docker provider checks
  must use a controlled runtime environment. On s72, only the managed
  OpenSandbox lifecycle controller may access the socket; untrusted executors,
  the broker entry, and platform containers must not mount it.
- Frontend source is maintained in `frontend/web`;
  its build and image provenance must remain traceable to the exact Git commit.
  It must consume only ai-platform public/admin projections and never executor
  private payload.
- Do not copy, export, commit, or quote real `.env` files. Use committed
  `.env.example` templates and redacted runtime evidence only.
- Keep root `.dockerignore` exclusions for real env files aligned with the
  repo-local Docker build context; `.gitignore` is not a Docker build-context
  boundary.

## Review And Deployment Guardrails

- The only future runtime rollout path is `tools/s72_colocation_authority.py
  deploy-main-commit` with an explicit full commit fetched from authoritative
  `origin/main`. It composes the immutable OpenSandbox controller, gateway,
  repository release authority, runtime acceptance, parity, and rollback under
  one host mutation lease.
- Git-native release preparation must fail closed when the commit is not
  reachable from fetched main, the versioned checkout is dirty, contains
  ignored worktree files, or is mismatched, an interrupted staging directory
  remains, a release path escapes through a symlink or traversal, or canonical
  and compatibility image provenance labels disagree.
- Never release from a local source archive, copied frontend distribution, dirty
  coordination checkout, or patched live container. Release readiness,
  persistent ownership, mutation leases, and break-glass authority are defined
  only in `docs/agent-rules/multi-agent-context-workflow.md`; future s72 host
  commands and terminal parity evidence are defined only in
  `docs/operations/s72-colocated-platform-runbook.md`. The 211 runbook is a
  legacy recovery reference, not a deployment prerequisite.
- Issue, PR, review, verification, status, and closure rules are defined only in
  `docs/agent-rules/github-issue-pr-workflow.md`.
- Durable docs describe a contract or an executable procedure. Current status,
  temporary phase plans, and release journals belong in the active GitHub record
  or controller checkpoint, not repository Markdown.
