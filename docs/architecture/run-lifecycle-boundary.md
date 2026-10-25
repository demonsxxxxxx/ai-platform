# Run Lifecycle Boundary

Status: normative source-architecture decision

Owner: `runs` bounded context

Parent contract: [`source-code-architecture.md`](source-code-architecture.md)

Runtime authority: [`runtime-authorities.md`](runtime-authorities.md)

Execution specification and attempt authority:
[`execution-spec-and-attempt-lifecycle.md`](execution-spec-and-attempt-lifecycle.md)

## 1. Decision

Run terminalization is one Runs application capability. It is not a generic
repository utility, a transport concern, a Redis authority, a Sandbox lifecycle,
or a compatibility feature.

The target follows a domain-first modular-monolith/ports-and-adapters shape.
Names in this diagram describe logical roles, not the current Python port names:

```text
HTTP transport ─┐
                ├─> RunLifecycleService ─> Run domain decisions
Worker loop  ───┘            │
                             ├─> RunRepository (PostgreSQL)
                             ├─> StreamingEventLedgerWriter
                             ├─> AuditLedgerWriter
                             └─> SandboxRuntimeClient

bootstrap.api / bootstrap.worker construct and inject every concrete adapter.
```

The application service owns the transaction and orchestration. PostgreSQL is
the durable Run truth. Redis terminal events project an already committed,
authorized transition; they cannot create, replace or reinterpret it. Independent
SSE terminal intents are retired by ADR 0013.

### 1.1 Source and migration status

The original decision-baseline notes are historical observations, not a current
implementation inventory. Existing RunAttempt transitions, Runs-owned public
terminal projection and direct committed-v4 publication must be inspected in
their owning modules. Publication queues, terminal intents and successor recovery
are retired; [SSE execution control](redis-streams-sse-execution-control.md) owns
the current transport behavior.

`RunLifecycleService` now owns ordinary complete/fail/cancel/enqueue-failure
transitions through explicit ports. `bootstrap.run_lifecycle` constructs it for
API and Worker composition; `lifecycle_postgres` owns its SQL and
`run_terminalization` coordinates provider lineage and committed event rows.
The current [application ports](../../app/runs/application/lifecycle.py) are
`RunLifecyclePersistence`, `RunEventWriter`, and `RunAuditWriter`.
[Lifecycle composition](../../app/bootstrap/run_lifecycle.py) injects
`PostgresRunLifecyclePersistence`, Streaming `append_event`, and Identity
`append_audit_log`; target ledger-role names above do not introduce extra owners.
Legacy repository lifecycle exports and human approval/parent-child recovery
are retired. The lifecycle service and its persistence port have no Run-only
running writer. Worker dispatch locks the session-consistent queued Run through
`RunAttemptLifecycleService`; the durable Attempt transition projects running in
PostgreSQL. Pre-dispatch failures without a valid specification retain their
Run-only terminal paths. Source, tests, image and runtime acceptance remain separate.

## 2. Source ownership

| Concern | Canonical owner | Allowed contents | Forbidden contents |
| --- | --- | --- | --- |
| Terminal state and decision rules | `app.runs.domain` | framework-neutral values, status classification, typed decisions, safe result policy | psycopg, FastAPI, Redis, repository calls, process settings |
| Terminalization use cases | `app.runs.application` | commands, results, ports, transaction-scoped orchestration, cancellation/reconciliation policy | concrete adapters, `ContextVar`, service locators, routes, environment reads |
| PostgreSQL adapter | [`app.runs.infrastructure.lifecycle_postgres`](../../app/runs/infrastructure/lifecycle_postgres.py) | SQL, row locks, CAS/fencing, bounded selectors, record mapping | user-visible wording, HTTP errors, Redis calls, event/audit policy, Sandbox provider calls |
| Durable Run-event ledger | [`app.streaming.infrastructure.run_events_postgres`](../../app/streaming/infrastructure/run_events_postgres.py) | validate and receipt safe event records supplied by an owning application, write `run_events` on the caller's connection, serve authorized history | Run terminal decisions, event wording, independent transaction/commit, Redis terminal authority |
| Durable audit ledger | [`app.identity.infrastructure.audit_postgres`](../../app/identity/infrastructure/audit_postgres.py) | validate a generic bounded audit envelope and write `audit_logs` on the caller's connection | Runs policy, target authorization, independent transaction/commit, public projection |
| HTTP transport | `app.runs.transport.http` | auth/input validation, command mapping, safe response/error mapping | SQL, queue/terminal decisions, concrete infrastructure, `app.bootstrap` imports |
| Public in-process contract | `app.runs.api` | stable application commands/results/service protocol and public Run constants | adapter construction, mutable global registration, compatibility behavior |
| API/worker composition | `app.bootstrap.api`, `app.bootstrap.worker` | construct concrete adapters once and inject a complete service graph | product rules, SQL, request handling, implicit global lookup |
| Legacy import surface | temporary, explicitly inventoried compatibility only | logic-free name delegation when separately authorized | lifecycle writes, orchestration, hidden defaults, indefinite aliases |

New Runs lifecycle code belongs to `app.runs`, and internal callers use
`app.runs.api`; it is never added to a global repository module. An external
compatibility claim requires named consumers and evidence, not an assumption.

## 3. Operation classification

The legacy mixed closure is split by responsibility, not copied wholesale.

### 3.1 PostgreSQL primitives

The Runs PostgreSQL adapter owns operations equivalent to:

- lock and read one Run identity/status;
- stage the first business terminalization target with compare-and-set semantics;
- acquire an owner/admin cancellation row lock and persist the cancellation fact;
- list bounded stale-run candidates with `FOR UPDATE SKIP LOCKED`;
- transition open Run steps to cancelled or failed;
- persist one terminal Run state and its observability counters;
- test whether the terminal Run/event/audit facts already exist.

Each primitive accepts the caller's transaction connection. It MUST NOT start,
commit, or roll back an independent transaction and MUST NOT publish Redis,
append user-visible events, select error wording, or call another context.

### 3.2 Application use cases

`RunLifecycleService` owns operations equivalent to:

- request owner cancellation;
- request administrator cancellation;
- fail, cancel, or complete one Run;
- compensate a committed queue-admission failure;
- reconcile a stale ownerless Run;
- finish a staged terminal transition and its exact Attempt.

These operations coordinate PostgreSQL primitives and explicit event-ledger,
audit-ledger and Sandbox Runtime application ports, then invoke direct Streaming
publication after commit.
They receive their dependencies by constructor or explicit argument. A missing
dependency fails during bootstrap, not during a terminal transition.

### 3.3 Domain decisions

Pure Runs policy owns:

- legal terminal targets and precedence (`cancel_requested` may advance to
  `cancelled`; a settled terminal Run cannot be overwritten);
- classification of a blocked success commit;
- typed `RunTerminalizationProgress` semantics;
- safe terminal result/error projection rules.

`RunTerminalizationProgress` exposes `completed`, `status`, and `did_transition`
as typed fields; mapping-style `get` compatibility is retired. The Runs API
continues to export this value. `is_terminal(requested_status)` and truthiness
retain terminal-status checks; `did_transition` remains the separate fact that
the current attempt won the durable transition. This is an in-process value,
not a persisted-record or HTTP response schema.

Public sanitization uses an explicit stable public contract. Raw child result,
error, dispatch metadata, storage identity, command, secret, or private path
never becomes a Run event merely because it was present in a database record.

## 4. Transaction and ordering invariants

The migration MUST preserve these observable semantics:

1. **One Unit of Work.** A protected Run/step transition and its durable
   Run-event/audit facts use the same
   PostgreSQL connection and transaction. The current `RunEventWriter` port uses
   Streaming `append_event` for `run_events`; `RunAuditWriter` uses Identity
   `append_audit_log` for `audit_logs`. Neither adapter starts or commits
   a transaction supplied by Runs.
2. **Owning row first.** Acquire the owning Run row before dependent step or
   Attempt locks. Do not introduce a reverse lock order.
3. **First business terminalization target wins.** The first nonterminal target is retained;
   only the defined `cancel_requested` to `cancelled` advancement is allowed.
4. **Terminal rows are immutable.** Success, failure, and cancellation CAS
   predicates exclude already terminal Runs.
5. **Bounded maintenance.** Candidate scans retain their limit and
   `FOR UPDATE SKIP LOCKED` behavior. One blocked candidate cannot serialize the
   entire maintenance loop.
6. **Attempt fencing.** A worker transition retains its exact Run, Attempt,
   queue owner, and reconciliation claim checks.
7. **Exactly-once durable facts.** Only the transaction that changes a
   nonterminal Run appends its terminal event/audit facts. Repeated completion
   returns the existing terminal status without duplicating those facts.
8. **Side-effect order.** Publish only after the Run transition and its facts
   commit. Derive stable terminal/end identities from the committed Run fact;
   Redis receipts enforce idempotency and callback-prefix ordering. There is no
   publication state, pending-intent drain or successor recovery. Redis failure
   cannot undo Run finalization or hide its authoritative final answer.
9. **Sandbox cleanup remains Sandbox-owned.** Owner and administrator cancellation
   retain separate authorization commands, then use one Runs-owned post-cancellation
   operation after the transaction commits. That operation admits and publishes
   the exact Attempt stream when present, removes queued payloads only for a
   cancelled Run, and always attempts Sandbox cleanup before reporting a queue
   failure. Publication failures do not prevent cleanup. Bootstrap injects these
   collaborators; neither HTTP route owns a separate cleanup implementation.
   The routes retain their existing 404/502/503 mapping and actor/role/reason.
   After the Run cancellation fact commits, orchestration calls the public Sandbox Runtime release/reconcile
   application API through a separate Sandbox-owned operation boundary. That authority
   owns the scoped lease and operation fence and records stop/release or cleanup
   failure. The existing stop-under-lock compatibility mechanism must remain
   until the Sandbox owner activates its separately proven claim/I/O/receipt
   replacement. Runs never calls a provider or marks
   a Sandbox lease released. A cancelled Run with cleanup pending does not claim
   that the Sandbox was released.
10. **Rollback is atomic.** Any exception before transaction completion rolls
    back the state transition and its event/audit facts together.

### 4.1 Asynchronous terminal consumption and elapsed metrics

The terminal reconciler drains ready PostgreSQL receipts before starting idle
maintenance. Failed-environment cleanup and suspect-executor probing each run
as a separately supervised, non-overlapping phase; neither is awaited on the
terminal scan/signal path. Probe progress also wakes the consumer locally when
Redis is unavailable. Redis errors retain bounded PostgreSQL polling. Shutdown
cancels and joins signal waiters and maintenance operations, including an
operation that has not yet finished after cancellation; claim/release and
stop/finalize fences remain in their owning paths.

After Worker reconciliation, only a terminal `WorkerOutcome` for the receipt's
exact Run permits stop/release and receipt finalization. Nonterminal outcomes,
including `skipped` with `stale_terminal_state`, retain settlement responsibility
through the existing claim-fenced receipt retry. Cancellation can make a dispatch
fence stale while the Run and its original Attempt are still nonterminal. The
next claim must settle that Attempt or observe the already-terminal Run before
finalizing; a stale error label is not a terminal fact. This does not replace
the independent stale-Run maintenance fallback or relax receipt validation.

For an asynchronously dispatched result, `latency_ms` measures elapsed time
from the persisted, timezone-aware `runs.started_at` to terminal result
projection. Execution's application projection samples the UTC observation time;
the Worker supplies the persisted start and does not own a new clock.
Reconciliation does not start a fresh execution stopwatch. Missing,
invalid, naive or future start timestamps yield `null`, rather than a fabricated
zero. Synchronous adapter timing retains its existing monotonic stopwatch.
SDK `model.completed.duration_ms` and private SDK-stage timings remain separate
metrics. This change does not rewrite historical terminal results or alter token
and cache counters.

### 4.2 Executor identity metadata and retained results

Execution's adapter result envelope owns `executor_type`; the terminal projection
preserves it in `runs.result_json.executor.executor_type`. New Claude adapter
results and private executor payloads omit `worker_boundary` and `delegate_used`:
the former duplicated that identity and the latter was a constant, not verified
SDK delegation evidence. Their removal does not change SDK subagents, execution
selection, capability evidence, or Run/Attempt authority.

These optional JSON keys are not required by `ai-platform.executor-result.v1`
and have no database columns or schema constraints. Existing result JSON remains
readable without migration, backfill, or rewriting; absence is valid and does not
need a replacement default. `app/platform/public_payload.py` retains both keys
and their normalized aliases in its forbidden-public-key contract for historical
records. Remove that compatibility protection only after proving no retained
record or supported input can contain them, under the
[public-projection contract](chat-run-lifecycle-and-public-error-projection.md).

### 4.3 Terminal-tail stage observations

API, Worker, and sandbox-executor entrypoints configure one dedicated standard
INFO logger, `app.platform.terminal_timing`, with an idempotent stderr handler and
no ancestor propagation. The handler renders safe fields explicitly; ordinary
application logger levels remain unchanged. Private standard logs emit
`run_terminal_stage` once per measured operation,
with Run/Attempt IDs, a fixed `terminal_phase`, monotonic
`terminal_phase_duration_ms`, and `terminal_phase_outcome` (`ok`, `error`, or
`cancelled`). `terminal_status` is the normalized result classification observed
at that stage, or `unknown`; it is not another durable Run authority. An `ok`
operation returned normally, including a normal failed/cancelled terminal result.
Exceptions and cancellation propagate unchanged; logging failure cannot replace
an operation's result. No prompt, answer, selected path, storage key, credential,
callback payload, or exception text is included. There are no per-token/file
observations, new schema fields, public payload fields, or new telemetry backend.

The barriers remain separate:

- `sdk_final_flush`: accepted final SDK Result through public EOF and callback /
  provider-store settlement. Intermediate/background Results are excluded.
  The SDK flushes pending mirror entries **before** delivering public Result;
  that earlier wait is excluded even when this span is near zero.
  `run_session_store_append_summary` reports full-run append calls (including
  retries), failed calls, cumulative/max duration and `store_active_calls` at runner exit (all store
  operations, including load/list).
  It includes pre-Result store calls; cumulative work can overlap model execution
  and is not terminal tail latency or additive with the other spans. Failed calls
  may later succeed on retry. The aggregate has no per-entry payloads and is
  emitted at most once; active store calls indicate an incomplete observation.
  `latest_public_result_gap_ms` is the interval from the preceding SDK-public
  message to the latest observed public Result, not a raw-Result mirror-flush
  span or necessarily the final Run Result. It is absent without both anchors.
  `append_await_in_latest_result_gap_ms` sums append-call intervals clipped to
  that window (including in-flight calls). Concurrent calls can overlap; it is
  not their union. It excludes SDK lock/backoff waits outside append, and neither
  field observes native CLI JSON write/fsync/close. Non-store model/tool work
  can occupy the remainder. Later non-Result messages do not erase the snapshot.
  `sdk_disconnect_flush` measures the existing combined disconnect/store fallback
  on early exit or cancellation. Neither measures post-settlement resource teardown.
- `executor_heartbeat_drain`: join the supervisor heartbeat and active progress.
  After a normal runner return (not timeout) with no observed stream failure,
  retire the pure supervisor lease heartbeat by cancellation and join the sender's
  actual completion; it carries no answer or capability receipt. A late host
  renewal is fenced by exact Run/Attempt/owner and the terminal receipt. A sender
  that resists cancellation still delays this join. API cancellation, timeout,
  runner exceptions and already-observed delivery failure retain the conservative
  drain/error path; retirement does not clear an existing failure;
  `executor_public_stream_drain`: close the buffered public callback stream;
  `executor_final_phase_callback`: the preceding final phase send, including
  its public callback flush/receipt barrier. A small close span does not exclude
  backlog in this earlier send;
  `executor_terminal_callback_ack`: the complete terminal-callback retry/ack wait.
- `terminal_receipt_commit`: the callback receipt transaction through its exit;
  `terminal_receipt_signal` and `terminal_receipt_publication`: the subsequent
  reconciliation wake and callback-row publication. Receipt commit does not mean
  the final assistant, artifact records, or Run result have committed.
- `reconciler_workspace_snapshot`: selected-file immutable snapshot collection,
  including the remote OpenSandbox identity check before local snapshot I/O;
  this span alone cannot identify disk latency;
  `reconciler_result_conversion`: the existing storage bridge await, including
  thread admission/queueing, selected-file validation/read/hash, provisional
  cleanup reservation, object-storage writes and adapter result projection.
  Failed-result conversion may write no files. This aggregate is not a pure
  object-store upload measurement, and neither span is the artifact-record
  database commit. The existing executor
  `artifact_upload_latency_ms` may measure an `artifact_created` callback, so it
  is not evidence of object-storage upload time.
- `result_business_commit`: the Runs infrastructure timing decorator observes
  the unchanged application commit service through transaction exit, including
  existing stale/CAS classification; bootstrap selects the decorator without
  changing its public constructor or contract. A `skipped` status is not a newly
  written result. Existing SDK-placeholder lease bookkeeping remains inside the
  commit; later physical reconciler sandbox stop/release is outside it.

`result_business_publication` measures Worker publication after the unchanged
commit and release-flag update. The separate `terminal_receipt_publication` span
observes callback-row publication only. Neither proves frontend receipt/history
or artifact download visibility. `reconciler_provider_stop` measures provider stop
between claim verification and lease finalization, after business terminality;
it excludes those two transactions and can delay the next serial reconciliation.
Existing `reconciler_result_conversion` remains the artifact-path screening span;
no per-file payload, upload-only claim or new tracing system is introduced.

These spans may overlap across processes (especially callback ack versus receipt
commit/publication), and retries produce another operation observation. Do not
sum them into an end-to-end duration or treat a stage's `ok` as a later-stage
receipt. They preserve all existing completion/durability barriers and do not
measure receipt queue residence or browser rendering. Deployed latency attribution
requires correlated logs from the exact release, a known status and attachment
case, and real PostgreSQL/Redis/provider/storage observations. Synthetic-delay
source tests prove attribution and ordering only.

## 5. Composition and call rules

### API process

`bootstrap.api` constructs one `RunLifecycleService` graph and supplies it to
Runs/Chat/Admin HTTP transports. It also supplies the Streaming-owned
transaction-scoped event-ledger adapter, the Identity-owned audit-ledger adapter, the
direct post-commit publisher, and the public Sandbox Runtime
client. Transport helpers receive the service explicitly or through framework
dependency injection whose provider reads only the application instance
installed by bootstrap.

A route, transport helper, or admission helper MUST NOT import
`app.bootstrap.api`, construct persistence, or fetch a process-global service.

### Worker process

`bootstrap.worker` constructs the worker's `RunLifecycleService` and passes it
through the worker entrypoint. Worker processing and maintenance functions
receive that service explicitly. After the PostgreSQL transaction commits, the
worker/API terminal coordinator invokes the Streaming post-commit publisher.
Business reconciliation remains durable; no maintenance loop scans SSE delivery
work. Final hydration converges even when Redis publication fails. Test doubles implement
application ports and are injected by tests; they are not production registries
or global monkeypatch requirements.

Execution supervision is composed through `bootstrap.execution_supervision`,
which injects the PostgreSQL store and transaction boundary into the
application-level domain scheduler. Existing delivery, probe, settlement and
Sandbox cleanup action owners remain the only executors. Their existing loops
keep independent scan limits, polling cadence, I/O budget and domain capacity.
The persisted domain mode selects which owner may claim: `legacy` admits only
the legacy path, `unified` invokes the same action owner through a unified claim,
and `draining` admits neither path. A mode/version change fences new claims
across replicas. An active action keeps its claim until its operation completes;
cancellation waits for that completion before releasing the slot.

Supervision and shadow observation default to disabled. Enabling the feature does
not change domain ownership by itself; the operator must request drain and
activate each domain separately. An expired claim is not proof that its process
or remote I/O stopped. Claim recovery requires a human operator to supply the
exact domain and owner versions, claim token, worker identity, operator identity
and stop evidence; the application records that evidence but does not verify it.
The claim TTL is not an external exactly-once guarantee.

### Forbidden dependency mechanisms

The Runs lifecycle MUST NOT use:

- `ContextVar`, thread-local, request-local, or connection attributes to locate
  ports;
- module-level mutable service registration;
- fallback construction inside application/domain/transport code;
- dynamic import, `getattr` dispatch, or a string-selected class/module;
- `app.repositories` as the service passed by bootstrap.

## 6. Naming contract

- Application boundary: `RunLifecycleService`.
- Current persistence port: `RunLifecyclePersistence`; concrete adapter:
  `PostgresRunLifecyclePersistence`.
- Current event/audit ports: `RunEventWriter` and `RunAuditWriter`.
- Target cross-owner roles use capability nouns such as
  `StreamingEventLedgerWriter`, `AuditLedgerWriter`, `RunEventPublisher`, and
  `SandboxRuntimeClient`; these role names do not claim existing Python classes.
- Input intents: explicit commands such as `CancelRunCommand` and
  `FailRunCommand` when a structured input is required.
- Persisted shapes end in `Record`; safe reads end in `Projection`; outcomes
  end in `Result` or a precise domain noun.

Permission-specific names such as `ToolPermissionTerminalizationProgress` and
`permission_terminalization_*` are retired with the human approval boundary.
They MUST NOT become canonical Runs names or permanent aliases.

## 7. Compatibility and deletion

Issue #1013 is a hard cutover: retired human approval decisions never become
synchronous authorization grants, and no approval writer remains.

Schema `2026.09.26.1` completes the persisted-state cutover. Stop old API and
Worker processes and their in-flight dispatch before applying this migration;
old binaries query the removed approval table and permission-prefixed columns
and cannot share this schema with new binaries. The transactional migration
settles only open Runs carrying retired platform orchestration controls and
their open Attempts through legal transitions. Run/Attempt history, immutable
specifications, ordinary staged terminal results, SDK subagents, and generic
copy lineage remain. Sandbox leases retain their existing cleanup owner.

The migration drops `run_tool_permission_requests` and renames
`permission_terminalization_*` to `terminalization_*`. Readiness checks require
the new contract and absence of the retired relation. A rollback to old binaries
requires restoring the pre-upgrade database backup with the fleet stopped;
renaming columns back cannot restore deleted approval records. Source merge
does not execute this deployment operation.

The inactive `app/repositories.py -> app.runs.infrastructure.lifecycle`
migration bridge is intentionally removed before activation. It incorrectly
classified application orchestration as infrastructure persistence. Its
removal does not delete a main-branch implementation or a supported runtime
surface.

The behavior migration MUST:

1. inventory every internal and supported external caller of the legacy Run
   lifecycle repository symbols;
2. migrate internal callers to the Runs application API in the same bounded
   cutover;
3. keep only independently justified persistence aliases already covered by a
   narrow authority entry;
4. delete obsolete orchestration exports rather than retaining a facade that
   performs lifecycle writes;
5. record historical persisted-record handling separately from Python import
   compatibility.

## 8. Migration slices

1. **Decision and authority.** Merge this document and retire the inactive
   mixed lifecycle bridge without production changes.
2. **Pure policy.** Move terminal decision/projection helpers into Runs domain
   and prove them with unit tests.
3. **PostgreSQL primitives.** Extract SQL/lock/CAS operations with replay tests
   for SQL, rows, transaction scope, and failure side effects.
4. **Application service.** Implement explicit ports and orchestration; preserve
   committed Run/event/audit identity and transaction-external direct publication.
   Do not restore `sse_terminal_publication_intents`, a publication queue, an
   ambient locator or a concrete adapter import.
5. **API composition.** Inject the service into Runs/Chat/Admin transports and
   retire transport-to-bootstrap imports.
6. **Worker composition.** Inject the same application contract into worker
   processing and maintenance entrypoints.
7. **Legacy deletion.** Remove obsolete repository lifecycle exports after
   caller and external-import inventory.

One slice MUST NOT combine a policy rewrite with an unproven lock/transaction
rewrite. A source move that changes a response, persisted row, SQL/lock order,
event/audit identity, terminal-event identity, or failure side effect is a
behavior change and needs separate evidence.

## 9. Required evidence

The terminal behavior change is not ready until focused evidence covers:

- pure decision unit tests with fixed inputs;
- PostgreSQL integration tests for commit, rollback, CAS loss, lock contention,
  `SKIP LOCKED`, stale lease/generation fencing, and exact-once event/audit facts;
- terminal publication tests for deterministic IDs/bytes, PostgreSQL rollback,
  commit then Redis failure, callback/terminal ordering, exact partial-end retry,
  missing Stream without reconstruction, and final hydrate convergence;
- Sandbox Runtime tests proving cancellation calls its public release authority,
  provider stop is fenced/receipted there, stop failure remains reconcilable,
  and Runs never mutates the lease or claims cleanup success;
- route tests with an explicitly injected fake application service;
- worker tests with an explicitly injected fake application service;
- replay of success, failure, cancellation, queue compensation, stale-owner
  recovery, and idempotent historical schema settlement;
- architecture tests rejecting transport-to-bootstrap imports, service-locator
  patterns, and lifecycle orchestration under infrastructure;
- exact base/head, immutable governance, and independent fixed-SHA review.

Source and CI evidence do not prove a stopped deployment, production schema
migration, Redis convergence, Sandbox cleanup, or mixed-version runtime safety.
Those remain deployment/runtime acceptance.

## Actual-model usage accounting

[Actual model token usage](actual-model-token-usage.md) owns the administrator
usage ledger. It consumes private callback snapshots in the existing fenced
receipt transaction, independently of Run terminal state and legacy counters.
