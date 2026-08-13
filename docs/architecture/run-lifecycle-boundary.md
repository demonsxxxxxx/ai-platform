# Run Lifecycle Boundary

Status: normative source-architecture decision

Owner: `runs` bounded context

Parent contract: [`source-code-architecture.md`](source-code-architecture.md)

Runtime authority: [`runtime-authorities.md`](runtime-authorities.md)

## 1. Decision

Run terminalization is one Runs application capability. It is not a generic
repository utility, a transport concern, a Redis authority, a Sandbox lifecycle,
or a compatibility feature.

The target follows a domain-first modular-monolith/ports-and-adapters shape:

```text
HTTP transport ─┐
                ├─> RunLifecycleService ─> Run domain decisions
Worker loop  ───┘            │
                             ├─> RunRepository (PostgreSQL)
                             ├─> StreamingEventLedgerWriter
                             ├─> AuditLedgerWriter
                             ├─> TerminalIntentRecorder
                             └─> SandboxRuntimeClient

bootstrap.api / bootstrap.worker construct and inject every concrete adapter.
```

The application service owns the transaction and orchestration. PostgreSQL is
the durable Run truth. Redis terminal intent is a projection of an already
authorized terminal transition; it cannot create, replace, or reinterpret that
transition.

### 1.1 Decision-baseline gaps

This document defines the target and the required migration evidence. It does
not claim that the decision baseline already implements the boundary. At that
baseline:

- Run terminalization policy, SQL, event/audit writes, and Redis intent calls
  remain mixed in `app/repositories.py`;
- `app.streaming.infrastructure.postgres` and `app.platform.postgres.audit` do
  not yet exist as the canonical transaction-scoped ledger adapters;
- API/Admin cancellation still coordinates Sandbox provider stop and lease
  release outside a public Sandbox Runtime application authority;
- the durable terminal-intent path supports pending state and exact-ID retry,
  but the terminal reconciler does not yet allocate a successor stream
  incarnation when continuity is unprovable; and
- routes/workers have not yet received an explicitly injected
  `RunLifecycleService`.

These are open behavior-migration blockers under Issue #1027/#1018. Merging the
document or retiring the inactive bridge closes none of them. No PR may claim
the Runs lifecycle boundary, Redis convergence, Sandbox cleanup authority, or
runtime acceptance until the corresponding slices and evidence in section 9
are terminal.

## 2. Source ownership

| Concern | Canonical owner | Allowed contents | Forbidden contents |
| --- | --- | --- | --- |
| Terminal state and decision rules | `app.runs.domain` | framework-neutral values, status classification, typed decisions, safe result policy | psycopg, FastAPI, Redis, repository calls, process settings |
| Terminalization use cases | `app.runs.application` | commands, results, ports, transaction-scoped orchestration, cancellation/reconciliation policy | concrete adapters, `ContextVar`, service locators, routes, environment reads |
| PostgreSQL adapter | `app.runs.infrastructure.postgres` | SQL, row locks, CAS/fencing, bounded selectors, record mapping | user-visible wording, HTTP errors, Redis calls, event/audit policy, Sandbox provider calls |
| Durable Run-event ledger | `app.streaming.infrastructure.postgres` | validate and receipt safe event records supplied by an owning application, write `run_events` on the caller's connection, serve ordered replay | Run terminal decisions, event wording, independent transaction/commit, Redis terminal authority |
| Durable audit ledger | `app.platform.postgres.audit` | validate a generic bounded audit envelope and write `audit_logs` on the caller's connection | Runs policy, target authorization, independent transaction/commit, public projection |
| HTTP transport | `app.runs.transport.http` | auth/input validation, command mapping, safe response/error mapping | SQL, queue/terminal decisions, concrete infrastructure, `app.bootstrap` imports |
| Public in-process contract | `app.runs.api` | stable application commands/results/service protocol and public Run constants | adapter construction, mutable global registration, compatibility behavior |
| API/worker composition | `app.bootstrap.api`, `app.bootstrap.worker` | construct concrete adapters once and inject a complete service graph | product rules, SQL, request handling, implicit global lookup |
| Legacy import surface | temporary, explicitly inventoried compatibility only | logic-free name delegation when separately authorized | lifecycle writes, orchestration, hidden defaults, indefinite aliases |

`app/repositories.py` is not a Run lifecycle owner. New Runs lifecycle code MUST
NOT be added there. Internal callers migrate to `app.runs.api`; an external
compatibility claim requires named consumers and evidence, not an assumption.

## 3. Operation classification

The legacy mixed closure is split by responsibility, not copied wholesale.

### 3.1 PostgreSQL primitives

The Runs PostgreSQL adapter owns operations equivalent to:

- lock and read one Run identity/status;
- stage the first terminal intent with compare-and-set semantics;
- acquire an owner/admin cancellation row lock and persist the cancellation fact;
- list bounded terminalization, child-reconciliation, and parent-finalization
  candidates with `FOR UPDATE SKIP LOCKED`;
- transition open Run steps to cancelled or failed;
- persist one terminal Run state and its observability counters;
- lock and update a multi-agent parent step using dispatch identity fences;
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
- drain bounded pending terminalization work;
- reconcile one terminal multi-agent child into its parent step;
- finalize a ready multi-agent parent exactly once.

These operations coordinate PostgreSQL primitives and explicit event-ledger,
audit-ledger, terminal-intent-recorder, and Sandbox Runtime application ports.
They receive their dependencies by constructor or explicit argument. A missing
dependency fails during bootstrap, not during a terminal transition.

### 3.3 Domain decisions

Pure Runs policy owns:

- legal terminal targets and precedence (`cancel_requested` may advance to
  `cancelled`; a settled terminal Run cannot be overwritten);
- classification of a blocked success commit;
- typed `RunTerminalizationProgress` semantics;
- parent status/count/message decisions from already-authorized child facts;
- safe terminal result/error projection rules.

Public sanitization uses an explicit stable public contract. Raw child result,
error, dispatch metadata, storage identity, command, secret, or private path
never becomes a Run event merely because it was present in a database record.

## 4. Transaction and ordering invariants

The migration MUST preserve these observable semantics:

1. **One Unit of Work.** A protected Run/step transition, its frozen terminal
   publication intent, and its durable Run-event/audit facts use the same
   PostgreSQL connection and transaction. `StreamingEventLedgerWriter` owns
   `run_events`; `AuditLedgerWriter` owns `audit_logs`; neither starts or commits
   a transaction supplied by Runs.
2. **Owning row first.** Acquire the owning Run row before dependent step or
   child reconciliation locks. Do not introduce a reverse lock order.
3. **First terminal intent wins.** The first nonterminal target is retained;
   only the defined `cancel_requested` to `cancelled` advancement is allowed.
4. **Terminal rows are immutable.** Success, failure, and cancellation CAS
   predicates exclude already terminal Runs.
5. **Bounded maintenance.** Candidate scans retain their limit and
   `FOR UPDATE SKIP LOCKED` behavior. One blocked candidate cannot serialize the
   entire maintenance loop.
6. **Dispatch fencing.** A child may update a parent step only when parent,
   step, dispatch identity, child Run identity, and handed-off state still
   match.
7. **Exactly-once durable facts.** Parent finalization and child reconciliation
   keep their event/audit existence checks or an equivalent unique receipt.
8. **Side-effect order.** Freeze and persist terminal/end semantic IDs,
   canonical bytes, sizes, digests, stream incarnation, projection version,
   publication state, and retry metadata with the Run transition. Publish to
   Redis only after commit. Unknown publication retries the same IDs/bytes;
   pending work is recovered by the existing fenced terminal publisher or
   reconciler. A Redis failure does not authorize a second PostgreSQL terminal
   state or leave the Run permanently nonterminal.
9. **Sandbox cleanup remains Sandbox-owned.** After the Run cancellation fact
   commits, orchestration calls the public Sandbox Runtime release/reconcile
   application API in a separate Sandbox-owned transaction. That authority
   locks the scoped lease, fences the attempt/generation, calls provider stop,
   and records release or cleanup failure. Runs never calls a provider or marks
   a Sandbox lease released. A cancelled Run with cleanup pending does not claim
   that the Sandbox was released.
10. **Rollback is atomic.** Any exception before transaction completion rolls
    back the state transition and its event/audit facts together.

## 5. Composition and call rules

### API process

`bootstrap.api` constructs one `RunLifecycleService` graph and supplies it to
Runs/Chat/Admin HTTP transports. It also supplies the Streaming-owned
transaction-scoped event-ledger adapter, the platform audit-ledger adapter, the
terminal-intent recorder/post-commit publisher, and the public Sandbox Runtime
client. Transport helpers receive the service explicitly or through framework
dependency injection whose provider reads only the application instance
installed by bootstrap.

A route, transport helper, or admission helper MUST NOT import
`app.bootstrap.api`, construct persistence, or fetch a process-global service.

### Worker process

`bootstrap.worker` constructs the worker's `RunLifecycleService` and passes it
through the worker entrypoint. Worker processing and maintenance functions
receive that service explicitly. After the PostgreSQL transaction commits, the
worker/API terminal coordinator invokes the Streaming post-commit publisher;
maintenance retries durable pending terminal intents using the same frozen
semantic identity and current attempt/incarnation fence. Test doubles implement
application ports and are injected by tests; they are not production registries
or global monkeypatch requirements.

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
- Persistence port: `RunRepository` or a narrower capability name.
- Concrete adapter: `PostgresRunRepository` (or narrowly named PostgreSQL
  adapters when the repository is split).
- Cross-owner ports: capability nouns such as `StreamingEventLedgerWriter`,
  `AuditLedgerWriter`, `TerminalIntentRecorder`, `TerminalIntentPublisher`, and
  `SandboxRuntimeClient`.
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
   the existing durable `sse_terminal_publication_intents` identity/digest/state
   contract and post-commit pending-intent recovery; no ambient locator or
   concrete adapter import.
5. **API composition.** Inject the service into Runs/Chat/Admin transports and
   retire transport-to-bootstrap imports.
6. **Worker composition.** Inject the same application contract into worker
   processing and maintenance entrypoints.
7. **Legacy deletion.** Remove obsolete repository lifecycle exports after
   caller and external-import inventory.

One slice MUST NOT combine a policy rewrite with an unproven lock/transaction
rewrite. A source move that changes a response, persisted row, SQL/lock order,
event/audit identity, terminal-intent identity, or failure side effect is a
behavior change and needs separate evidence.

## 9. Required evidence

The terminal behavior change is not ready until focused evidence covers:

- pure decision unit tests with fixed inputs;
- PostgreSQL integration tests for commit, rollback, CAS loss, lock contention,
  `SKIP LOCKED`, stale lease/generation fencing, and exact-once event/audit facts;
- terminal publication tests for frozen semantic IDs/bytes/digests, PostgreSQL
  rollback, commit then Redis failure/unknown outcome, exact retry, pending
  reconciler recovery, successor incarnation, and final hydrate convergence;
- Sandbox Runtime tests proving cancellation calls its public release authority,
  provider stop is fenced/receipted there, stop failure remains reconcilable,
  and Runs never mutates the lease or claims cleanup success;
- route tests with an explicitly injected fake application service;
- worker tests with an explicitly injected fake application service;
- replay of success, failure, cancellation, queue compensation, stale-owner
  recovery, child reconciliation, and parent finalization;
- architecture tests rejecting transport-to-bootstrap imports, service-locator
  patterns, and lifecycle orchestration under infrastructure;
- exact base/head, immutable governance, and independent fixed-SHA review.

Source and CI evidence do not prove a stopped deployment, production schema
migration, Redis convergence, Sandbox cleanup, or mixed-version runtime safety.
Those remain deployment/runtime acceptance.
