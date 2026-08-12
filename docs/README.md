# Documentation Authority

This index names durable documentation. It is not a project status report and
does not represent deployed runtime state.

## Governance

- `../AGENTS.md` defines repository-local operating constraints.
- `agent-rules/multi-agent-context-workflow.md` defines ownership, leases, and
  handoff.
- `agent-rules/github-issue-pr-workflow.md` defines issue, PR, review, and
  closure evidence.
- `architecture/runtime-authorities.md` maps each runtime capability to its
  single business authority and defines the Harness replacement seam.
- `architecture/single-enterprise-data-lifecycle.md` defines the fixed
  single-enterprise identity scope, datastore ownership, versioned schema
  lifecycle, bounded reads, retention workflow, and PostgreSQL payload limits.
- `architecture/sandbox-runtime-control-layer.md` defines the Sandbox Runtime
  application authority, target lifecycle, ownership fences, provider port, and
  staged recovery model.
- `adr/0001-agent-app-revision-authorization-lifecycle.md` records the Agent App
  decision to pin conversations, reauthorize every run, and retain withdrawn
  history as read-only.
- `adr/0005-harness-chat-is-not-a-skill.md` records the execution-identity
  boundary between ordinary Harness chat and explicitly authorized Skills.
- `architecture/opensandbox-ephemeral-model-credentials.md` defines the
  attempt-bound model-route admission and trusted provider-secret boundary.
- `architecture/redis-streams-sse-event-channel.md` indexes the current v2.1
  single-runtime Redis SSE contract. Its wire-protocol, execution-control, and
  cutover/acceptance links are the single detailed authorities; implementation
  reuses existing attempt/runtime/worker fences and does not assume a parallel
  A2 execution ledger.
- `adr/0004-redis-streams-sse-event-channel-v2-1-correction.md` records the
  accepted v2.1 decision. ADR 0003 (v2) and ADR 0002 (v1) are superseded audit
  history, not runnable fallbacks.
- `architecture/docker-packaging.md` defines reproducible dependency authority,
  immutable image bases, CI image acceptance, and digest-bound GHCR supply-chain
  publication without deployment or runtime authority.

The repository-root `../CONTEXT.md` defines the compressed Agent App ubiquitous
language used by source, product, and acceptance contracts.

## Operations

`operations/release-operations-runbook.md` is the sole executable release
procedure for a controlled Docker host. It requires a read-only readiness packet and one release
owner with one mutation lease. No document here authorizes a manual deployment
or substitutes for current host evidence.

`operations/s72-opensandbox-gateway-runbook.md` is the separate root-owned s72
gateway install and rollback authority. It does not replace the application
release procedure or establish application runtime acceptance.

`acceptance/agent-app/ordinary-user-matrix.md` defines the source/runtime
evidence boundary and post-merge ordinary-user matrix for Agent Apps. It grants
no deployment or runtime mutation authority.

## Contracts And Evidence

Source contracts live in their owning code and focused tests. Public frontend
contracts live in `frontend/`. Reviewed, redacted evidence is stored under
`release-evidence/` and indexed by `release-evidence/README.md`. Evidence is
historical unless its exact subject is freshly verified under the applicable
release or acceptance procedure.
