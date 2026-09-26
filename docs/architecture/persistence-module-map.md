# Persistence module ownership

Persistence code now enters through modules named for their owning domain or
explicit platform responsibility. The former top-level compatibility facades
(`app.repositories`, `app.agent_conversation_repository`,
`app.artifact_lifecycle_repository`, `app.session_continuity`,
`app.memory_redaction`, `app.persistence_limits`, and `app.run_event_repository`)
have been removed. Callers
use the canonical module that owns the operation. The caller supplies the
PostgreSQL connection and owns the transaction; adapters do not acquire a second
connection or commit independently.

| Owner | Current entry points | Responsibility |
| --- | --- | --- |
| `agent_apps` | `app/agent_apps/infrastructure/catalog_postgres.py`, `principal_catalog_postgres.py` | Agent catalog and principal-visible Agent queries |
| `artifacts` | `app/artifacts/infrastructure/records_postgres.py` | Artifact records and Run artifact reads |
| `context` | `app/context/infrastructure/postgres.py`, `snapshot_postgres.py`, `sources_postgres.py` | Memory, context snapshots, and scoped context sources |
| `conversations` | `app/conversations/infrastructure/postgres.py`, `session_queries_postgres.py` | Conversation persistence, authorized session reads, and session-run queries |
| `execution` | `app/execution/api.py` | Provider session dispatch contract |
| `files` | `app/files/infrastructure/run_bindings_postgres.py` | File records and scoped Run bindings |
| `identity` | `app/identity/infrastructure/postgres.py`, `audit_postgres.py`, `capability_distributions_postgres.py` | User identity, audit records, and capability distribution persistence |
| `mcp` | `app/mcp/infrastructure/postgres.py`, `registry_postgres.py`, `tool_policies_postgres.py`, `chat_access_postgres.py`; `app/mcp/repository.py` | Server CRUD delegates to the runtime registry implementation; tool policy and Chat access have separate adapters |
| `platform` | `app/platform/postgres/errors.py`, `limits.py`, `values.py`; `app/kernel/memory_redaction.py` | Shared PostgreSQL errors, value encoding, payload bounds, and memory redaction rules |
| `runs` | `app/runs/infrastructure/` adapters | Run creation, admission, control operations, lifecycle, steps, replay, and administrative queries |
| `sandbox` | `app/sandbox/infrastructure/leases_postgres.py` | Sandbox lease persistence |
| `skills` | `app/skills/infrastructure/` adapters | Skill catalog, resolution, versions, snapshots, overlays, and legacy workbench persistence |
| `streaming` | `app/streaming/infrastructure/run_events_postgres.py` | Durable Run event persistence |
| `persistence` | `app/persistence/` | Transitional artifact/file lifecycle adapters; object_lifecycle owns outbox orchestration and target domains own eligibility |

`app/routes/chat_sessions.py` reads generic sessions and authorized Agent
conversation pages through `app.conversations.infrastructure.postgres`. The
provider session dispatch contract is imported from `app.execution.api` by its
callers. Limits and redaction are imported from `app.platform.postgres.limits`
and `app.kernel.memory_redaction` respectively.

These paths identify current code ownership; they do not imply complete layer
separation. Some adapters and composition modules still call across domain
boundaries or retain older integration patterns. Those calls are known
architecture debt for later, bounded migrations. This module map records the
current owners and public entry points without claiming that every dependency
already follows an ideal domain boundary.

Ordinary Run transitions are orchestrated by
`app.runs.application.lifecycle.RunLifecycleService` with explicit persistence,
event, and audit ports. API and Worker composition inject the service;
committed-v4 and provider-lineage coordination lives in
`app.runs.application.terminalization_v4`. Stale-run recovery continues through
the execution application, including its queue fence and executor terminal
receipt checks. These application boundaries coexist with the cross-domain
adapter calls described above.

An implementation test must patch dependencies in the adapter that reads them.
A route test must patch the route's actual collaborator. The former compatibility
aliases no longer forward patches to canonical adapter globals.

Run event construction and size validation live in
`app.streaming.infrastructure.run_events_postgres`; durable ledger receipts and cursor
reads live in `app.streaming.infrastructure.event_ledger_postgres`. Cursor and public
projection rules live in `app.streaming.domain.run_events`. Active runtime lease queries live in
`app.sandbox.infrastructure.leases_postgres`.

Persistence tests are grouped by the same domain responsibilities under `tests/`.
Shared connection and cursor doubles live in `tests/support/repository_fixtures.py`;
CI selects the domain suites directly.
