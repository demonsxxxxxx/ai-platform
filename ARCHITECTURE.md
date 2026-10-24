# Architecture navigation

Status: navigation to accepted source contracts; no independent architecture rules.

Start with the [documentation authority index](docs/README.md). It distinguishes
active contracts, proposed migrations and dated observations. Read source and tests
at the same revision; deployed state is established through release evidence.

## Processes and ownership

The [system architecture](docs/architecture/system-architecture.md) maps the API,
Worker, Sandbox Executor, reconciler, browser, PostgreSQL, Redis and object storage.
The [runtime authorities](docs/architecture/runtime-authorities.md) assign product
facts and lifecycle decisions. The [source architecture](docs/architecture/source-code-architecture.md)
and [ADR 0006](docs/adr/0006-domain-first-modular-monolith.md) own package placement,
public `api`/`events` boundaries, adapters, composition and compatibility retirement.
Target packages are created only when needed; their presence in a diagram does not
establish implemented or deployed behavior.

| Concern | Detailed owner |
| --- | --- |
| Product language and Expert Agent experience | [CONTEXT](CONTEXT.md), [Workbench](docs/architecture/expert-agent-service-workbench.md) |
| Profile publication and authorization | [Profile boundary](docs/architecture/agent-profile-persistence-boundary.md) |
| Conversation scope and provider continuity | [Conversation context](docs/architecture/agent-conversation-context.md), [provider continuity](docs/architecture/claude-provider-session-continuity.md) |
| Run outcomes and transaction ordering | [Run lifecycle](docs/architecture/run-lifecycle-boundary.md) |
| Immutable execution input and attempt ownership | [ExecutionSpec and RunAttempt](docs/architecture/execution-spec-and-attempt-lifecycle.md) |
| Provider lifecycle, callbacks and resource cleanup | [Sandbox Runtime](docs/architecture/sandbox-runtime-control-layer.md) |
| SSE transport and client acceptance | [SSE index](docs/architecture/redis-streams-sse-event-channel.md) |
| Public messages and terminal errors | [Chat projection](docs/architecture/chat-run-lifecycle-and-public-error-projection.md) |
| Persistence, schema and deletion | [Data lifecycle](docs/architecture/single-enterprise-data-lifecycle.md) |
| Verification evidence and retirement | [Evidence governance](docs/architecture/ci-test-readiness-governance.md) |
| Builds and immutable release packages | [Packaging](docs/architecture/docker-packaging.md), [release operations](docs/operations/release-operations-runbook.md) |

## Repository improvement

[Core beliefs](docs/design-docs/core-beliefs.md) links the existing engineering
principles. Source measurements (import edges, hotspots) come from running
`scripts/analysis/source_quality_inventory.py` at the revision in question. The
[debt inventory](docs/exec-plans/tech-debt-tracker.md) lists known architecture
debt; it does not supersede these contracts. The stopped Harness migration, its blueprint and
its 2026-10-13 quality baseline are archived under `docs/exec-plans/completed/`.
