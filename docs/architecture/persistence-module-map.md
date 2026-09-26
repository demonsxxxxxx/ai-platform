# Persistence module ownership

`app/repositories.py` preserves extracted persistence names as identity aliases.
Persistence changes belong in the adapters below. The caller supplies the
PostgreSQL connection and owns the transaction; adapters do not acquire a second
connection or commit independently.

| Owner | Adapter under `app/<owner>/infrastructure/` | Responsibility |
| --- | --- | --- |
| `agent_apps` | `principal_catalog_postgres.py` | Principal-visible Agent catalog |
| `skills` | `resolution_postgres.py`, `catalog_postgres.py` | Executable Skill resolution and tenant catalog |
| `skills` | `versions_postgres.py` | Versions, release policy, and administrative Skill queries |
| `skills` | `run_snapshots_postgres.py` | Immutable run Skill packages and usage projections |
| `skills` | `file_overlays_postgres.py` | User Skill file overlays |
| `identity` | `capability_distributions_postgres.py` | Tenant capability distribution and lifecycle locks |
| `identity` | `audit_postgres.py` | Audit writes, denial records, and role audit history |
| `mcp` | `tool_policies_postgres.py`, `chat_access_postgres.py` | Tool policy queries and Chat access checks |
| `conversations` | `session_queries_postgres.py` | Scoped session and session-run reads |
| `runs` | `creation_postgres.py`, `capability_admission_postgres.py` | Run creation, identity snapshots, and capability admission |
| `runs` | `control_operations_postgres.py`, `replay_postgres.py` | Idempotent control operations, copy, retry, and resume |
| `runs` | `steps_postgres.py` | Step persistence and terminal updates |
| `runs` | `lifecycle_postgres.py` | Run state, terminalization intent, and bounded stale candidates |
| `runs` | `admin_queries_postgres.py` | Administrative Run detail and runtime summaries |
| `files` | `run_bindings_postgres.py` | File creation, reads, and scoped Run binding |
| `artifacts` | `records_postgres.py` | Artifact record creation and Run artifact reads |
| `sandbox` | `leases_postgres.py` | Lease queries, renewal, and cleanup outcome recording |
| `streaming` | `run_events_postgres.py` | Event ledger compatibility and error translation |

Technical value encoding, size-error conversion, and scalar coercion live in
`app/platform/postgres/values.py`. Previously extracted adapters retain their
existing ownership.

## Dependency and compatibility boundaries

The source move preserves existing function bodies, SQL, lock order, error
codes, and return values. Cross-domain SQL orchestration and inherited legacy imports remain
explicit migration debt. `app/mcp/repository.py` still contains an older lazy
facade dependency; this move does not claim to remove that pre-existing coupling.

`app/repositories.py` contains no local functions. Ordinary Run transitions are
orchestrated by `app.runs.application.lifecycle.RunLifecycleService` with
explicit persistence, event, and audit ports. API and Worker composition inject
the service; committed-v4 and provider-lineage coordination lives in
`app.runs.application.terminalization_v4`. The caller owns the transaction.
Stale-run recovery continues through the execution application, including its
queue fence and executor terminal receipt checks.

Human tool approval requests, expiration/drain maintenance, callback and UI
surfaces, and platform multi-Agent parent/child recovery have been removed.
Schema `2026.09.26.1` settles affected open historical Runs and Attempts, drops
the approval relation, and renames the five ordinary terminalization columns.
The [Run lifecycle boundary](run-lifecycle-boundary.md) records the stopped-fleet
upgrade and rollback boundary. SDK subagents and ordinary Run copy lineage
remain active. Historical event filtering does not authorize old producers.

Current compatibility consumers include `app/routes/runs.py`,
`app/routes/chat.py`, `app/routes/admin_runs.py`, `app/routes/admin_skills.py`,
`app/worker.py`, `app/worker_main.py`, `app/agent_apps/authority.py`,
`app/skills/catalog.py`, and `app/mcp/repository.py`. They continue to use the
existing names; caller migration is a separate application/API cutover.
An implementation test must patch dependencies in the
adapter that reads them. A route test must patch the route's actual collaborator.
No dynamic forwarding is used to make patches to the old facade affect adapter
globals.

The architecture policy names each compatibility bridge. Migrate supported
callers to owner APIs before retiring aliases; extraction is not proof that
external consumers have stopped importing them. New behavior should follow the
[source architecture](source-code-architecture.md) API and application-port
rules, rather than extending inherited cross-domain adapter dependencies.
