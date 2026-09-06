# Documentation Authority

This index separates active contracts, migration targets, and historical records.
It does not establish deployed runtime state. Read code, schemas, and tests at the
same Git revision; the release procedure binds deployed evidence separately.

## Start here

| Question | Owner |
| --- | --- |
| What runs, and where are the trust and data boundaries? | [System architecture](architecture/system-architecture.md) |
| Which context owns a business decision? | [Runtime authorities](architecture/runtime-authorities.md) |
| Where does code belong, and how can old code be removed? | [Source architecture](architecture/source-code-architecture.md) |
| Which cross-process changes are proposed, and in what order? | [Runtime convergence](architecture/runtime-convergence.md) |
| What observable results must a slice prove? | [System acceptance matrix](acceptance/system-architecture-matrix.md) |
| What does a test, build, or deployment result actually prove? | [Evidence governance](architecture/ci-test-readiness-governance.md) |

The convergence document and matrix are proposed implementation/acceptance
criteria. They do not silently override an accepted protocol or authorize a
runtime change. A behavior-changing slice must update its detailed owner and
pass the existing review process before activation. Progress, named people,
current SHAs, exceptions, and evidence results belong in the issue/PR.

## Detailed contracts

| Concern | Single detailed owner |
| --- | --- |
| Product vocabulary and Expert Agent UX | [Workbench](architecture/expert-agent-service-workbench.md); root [CONTEXT](../CONTEXT.md) |
| Profile revisions, visibility, and publication | [Profile boundary](architecture/agent-profile-persistence-boundary.md) |
| Conversation selection and executor-private history | [Conversation context](architecture/agent-conversation-context.md) |
| Run terminalization and transaction ordering | [Run lifecycle](architecture/run-lifecycle-boundary.md) |
| Immutable execution input and attempt ownership | [ExecutionSpec and RunAttempt](architecture/execution-spec-and-attempt-lifecycle.md) |
| Provider resource lifecycle and callback authority | [Sandbox Runtime](architecture/sandbox-runtime-control-layer.md) |
| Model credentials and governed egress | [Credential boundary](architecture/opensandbox-ephemeral-model-credentials.md) |
| Data ownership, schema changes, and deletion | [Data lifecycle](architecture/single-enterprise-data-lifecycle.md) |
| SSE navigation and supersession | [SSE index](architecture/redis-streams-sse-event-channel.md) |
| SSE bytes, identity, replay, and client acceptance | [SSE wire](architecture/redis-streams-sse-wire-protocol.md) |
| Stream admission, claims, leases, and recovery | [SSE execution control](architecture/redis-streams-sse-execution-control.md) |
| Chat display and public terminal errors | [Chat projection](architecture/chat-run-lifecycle-and-public-error-projection.md) |
| Dependency locks, image builds, and publication | [Packaging](architecture/docker-packaging.md) |

Do not duplicate these rules in an overview. When two current documents conflict,
record the conflict and reconcile the owning contract against the accepted ADR,
code, schemas, and tests. A document's later edit date does not make it a new
authority. Code proves implementation; it does not make an implementation defect
a desirable requirement.

## Decisions and historical material

Accepted rationale is recorded in [ADR 0001](adr/0001-agent-app-revision-authorization-lifecycle.md),
[ADR 0005](adr/0005-harness-chat-is-not-a-skill.md),
[ADR 0006](adr/0006-domain-first-modular-monolith.md),
[ADR 0007](adr/0007-fixed-browser-authentication-day.md),
[ADR 0008](adr/0008-agent-sdk-autonomous-skill-dispatch.md), and
[ADR 0012](adr/0012-recoverable-agent-kernel-event-stream-v4.md).
ADR 0012 owns the active v4 decision. Earlier streaming ADRs are history, not
selectable fallback runtimes. Keep historical ADRs and release evidence intact.

[SDK 0.2.130 upgrade](architecture/claude-agent-sdk-0.2.130-upgrade.md) is a
historical upgrade record. It is not the current dependency or capacity table;
use the repository locks, effective configuration, and packaging authority.
Its public-projection failure constraints remain binding through the current
Chat and SSE contracts until explicitly replaced.

## Delivery and operations

[AGENTS](../AGENTS.md), [PR workflow](agent-rules/github-issue-pr-workflow.md),
[local test execution](agent-rules/local-test-execution.md), and
[multi-agent workflow](agent-rules/multi-agent-context-workflow.md) own repository
work, verification, and handoff. This cleanup does not add another approval system.

[Release operations](operations/release-operations-runbook.md) remains the only
executable release procedure. [SSE cutover acceptance](operations/redis-streams-sse-cutover-acceptance.md)
and [ordinary-user acceptance](acceptance/agent-app/ordinary-user-matrix.md)
remain the detailed deployed checks. The system matrix complements them with
cross-component failure scenarios; it does not replace them.
[Release evidence](release-evidence/README.md) is historical unless its exact
subject has been freshly verified.

## Document maintenance

A retained document must have one purpose and identify whether it describes an
active contract, a proposed migration, or a dated observation. Preserve public
anchors or update their consumers when moving material. Keep one detailed owner
per requirement, and link to it from indexes. Do not copy per-PR approval claims,
current work status, or repeated implementation checklists into durable policy.
