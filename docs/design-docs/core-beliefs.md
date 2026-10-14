# Core beliefs

Status: principle navigation; detailed rules remain with the linked owners.

- **One owner per business fact.** Product decisions belong to bounded contexts;
  technical adapters and Bootstrap composition remain separate.
  [Source architecture](../architecture/source-code-architecture.md)
- **Admitted identity survives every handoff.** Tenant, workspace, principal,
  Profile, Run, Attempt and lease bindings remain explicit through replay and
  execution. [Runtime authorities](../architecture/runtime-authorities.md)
- **Persist before projecting.** PostgreSQL owns durable outcomes; Redis and the
  browser project committed, authorized facts.
  [Run lifecycle](../architecture/run-lifecycle-boundary.md)
- **Public views reveal only authorized facts.** Private executor material,
  credentials and storage/runtime identities retain their own boundaries.
  [Chat projection](../architecture/chat-run-lifecycle-and-public-error-projection.md)
- **Recovery preserves ownership and the first failure.** Cancellation and partial
  cleanup need bounded, observable handling; source configuration cannot establish
  that a real resource was released. [Sandbox Runtime](../architecture/sandbox-runtime-control-layer.md)
- **The smallest complete change retires what it replaces.** Reuse owning
  implementations, update consumers and regressions, and inventory compatibility
  exits. [AGENTS](../../AGENTS.md), [delivery workflow](../agent-rules/github-issue-pr-workflow.md)
- **A claim is no stronger than its evidence.** Source, unit tests, real-dependency
  integration, image provenance, deployment and user acceptance are distinct.
  [Evidence governance](../architecture/ci-test-readiness-governance.md)
