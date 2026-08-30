# Documentation Authority

This index names durable documentation. It is not a project status report and
does not represent deployed runtime state.

## Governance

- `../AGENTS.md` defines repository-local operating constraints.
- `agent-rules/multi-agent-context-workflow.md` defines ownership, leases, and
  handoff.
- `agent-rules/github-issue-pr-workflow.md` defines risk-scaled PR scope,
  review, verification, and evidence language.
- `agent-rules/local-test-execution.md` defines the bounded, observable,
  worktree-safe procedure for local pytest stages and their failure taxonomy.
- `architecture/runtime-authorities.md` maps each runtime capability to its
  single business authority and defines the Harness replacement seam.
- `architecture/expert-agent-service-workbench.md` defines the Agent-first
  authenticated product, Agent.md Builder language, progressive configuration,
  task-oriented Market/Workspace UX, and truthful Skill archive semantics.
- `architecture/agent-conversation-context.md` defines platform-owned conversation
  continuity, snapshot-authorized executor-private messages, complete-turn
  context budgeting, and the separation from public context projections.
- `architecture/source-code-architecture.md` defines the normative backend
  package tree, dependency direction, naming, compatibility, deletion proof,
  and strangler migration contract. ADR 0006 records the decision and rejected
  alternatives.
- `architecture/ci-test-readiness-governance.md` defines evidence levels,
  required-test ownership, runtime/offline readiness boundaries, and retirement
  rules for obsolete checks.
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
- `adr/0006-domain-first-modular-monolith.md` records the decision to organize
  backend source by bounded context rather than global technical layers.
- `adr/0007-fixed-browser-authentication-day.md` records the fixed, non-sliding
  24-hour browser authentication lifetime and its revocation tradeoff.
- `adr/0008-agent-sdk-autonomous-skill-dispatch.md` records that an Agent owns a
  governed Skill Set while its Agent SDK autonomously decides whether and which
  registered Skill to invoke; Skill file capability is not a per-run upload
  requirement.
- `adr/0013-external-knowledge-authority.md` establishes the Knowledge bounded
  context, RAGFlow provider boundary, cross-context transaction owners, and
  credential isolation used by Agent Apps, Runs, Conversations, and citations.
- `architecture/opensandbox-ephemeral-model-credentials.md` defines the
  attempt-bound model-route admission, Run-pinned compatible-endpoint revision,
  and trusted encrypted credential/proxy boundary.
- `architecture/redis-streams-sse-event-channel.md` indexes the implemented v4
  single-runtime Redis SSE contract. Its wire-protocol, execution-control, and
  cutover/acceptance links are the single detailed authorities; implementation
  reuses existing Run/attempt/runtime/worker fences, durable PostgreSQL event
  ownership, Redis replay plus Pub/Sub fan-out, and successor-incarnation
  recovery.
- `architecture/chat-run-lifecycle-and-public-error-projection.md` records the
  repository-wide module disposition for queued-to-processing Chat state and
  disclosure-safe terminal-error presentation. It identifies the current
  authorities, retired browser compatibility, retained queue and safety
  fallbacks, owning tests, and deployment evidence ceiling without redefining
  the SSE wire or Run lifecycle.
- `adr/0012-recoverable-agent-kernel-event-stream-v4.md` records the accepted,
  active v4 Agent-kernel event and recovery decision. ADR 0009 remains
  historical context for the Redis replay/live transport mechanics; ADR 0004
  (v2.1), ADR 0003 (v2), and ADR 0002 (v1) are superseded audit history, not
  runnable fallbacks.
- `architecture/docker-packaging.md` defines reproducible dependency authority,
  immutable image bases, CI image acceptance, and digest-bound GHCR supply-chain
  publication without deployment or runtime authority.

The repository-root `../CONTEXT.md` defines the compressed Agent App ubiquitous
language used by source, product, and acceptance contracts.

## Product Requirements

- `product/README.md` indexes durable user-facing product requirement
  contracts. It is not a roadmap or implementation-status board.
- `product/external-knowledge/README.md` indexes the external Knowledge
  product contract for governed connection to the company's existing RAGFlow
  datasets, Agent revision binding, retrieval admission, evidence and citations.

## Operations

`operations/release-operations-runbook.md` is the sole executable release
procedure for a controlled Docker host. It requires a read-only readiness packet and one release
owner with one mutation lease. No document here authorizes a manual deployment
or substitutes for current host evidence.

The production OpenSandbox direct contour is documented in the release
runbook and uses the official SDK adapter plus the stateless Nginx egress entry.

`acceptance/agent-app/ordinary-user-matrix.md` defines the source/runtime
evidence boundary and post-merge ordinary-user matrix for Agent Apps. It grants
no deployment or runtime mutation authority.

## Contracts And Evidence

Source contracts live in their owning code and focused tests. Public frontend
contracts live in `frontend/`. Reviewed, redacted evidence is stored under
`release-evidence/` and indexed by `release-evidence/README.md`. Evidence is
historical unless its exact subject is freshly verified under the applicable
release or acceptance procedure.
