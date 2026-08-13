# Backend Source Code Architecture

This document is the normative placement, dependency, naming, compatibility,
deletion, and migration contract for backend source under `app/`. It is not a
project status report and does not establish deployed runtime state.

The keywords **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.
Existing paths that predate this contract are migration exceptions, not
precedent for new code.

## 1. Architectural objective

AI Platform is a domain-first modular monolith with several explicit process
entrypoints. Product policy and durable business facts belong to bounded
contexts. FastAPI, PostgreSQL, Redis, object storage, Harness SDKs, and Sandbox
providers are delivery or infrastructure adapters around those contexts.

The design optimizes for:

- one owner for each business decision and persisted fact;
- local reasoning inside a domain and explicit cross-domain contracts;
- behavior-preserving, reversible migration from the current tree;
- removal of dormant, duplicate, generated, or test-only production code after
  proof;
- preserving API, authorization, persistence, queue, and runtime behavior while
  source ownership changes.

It does not optimize for the fewest directories, the most services, or visual
similarity to another repository.

## 2. Target package tree

New product code MUST be placed in the following shape. A domain creates only
the subpackages it actually needs; empty ceremonial layers are forbidden.

```text
app/
  bootstrap/                 # process entrypoints and dependency wiring only
    api.py
    worker.py
    executor.py
    maintenance.py
    settings.py
  kernel/                    # tiny framework-neutral shared vocabulary
  platform/                  # shared technical clients; no product decisions
    postgres/
      migrations/
    redis/
    object_storage/
    observability/
  identity/
  agent_apps/
  skills/
  conversations/
  runs/
  context/
  files/
  artifacts/
  object_lifecycle/
  streaming/
  mcp/
  execution/
  sandbox/
  compat/                    # named legacy wire/import boundaries only
```

A bounded context MAY contain:

```text
<domain>/
  api.py                     # stable in-process contract exposed to peers
  events.py                  # versioned integration-event contracts
  registry.py                # optional typed extension registry contract
  domain/                    # entities, value objects, policies, domain errors
  application/               # commands, queries, use cases, and ports
  infrastructure/            # PostgreSQL/Redis/S3/SDK/provider adapters
  transport/                 # HTTP, SSE, callback, or CLI translation
```

`api.py` and `events.py` are boundary modules, not re-export lists for every
internal symbol. They MUST expose the smallest contract peers need and MUST NOT
expose concrete infrastructure adapters.

### 2.1 Bounded-context ownership

| Context | Owns | Does not own |
| --- | --- | --- |
| `identity` | authenticated principal, company identity projection, session identity, workspace/user scope, role/access facts | Agent App ACL policy, run admission, frontend-only state |
| `agent_apps` | immutable Agent Profile revisions, publication, visibility/ACL, Agent App admission definition | conversation history, Skill release, executor behavior |
| `skills` | Skill catalog, version/release lifecycle, distribution, governed material identity | Harness chat, SDK execution loop, arbitrary uploaded data |
| `conversations` | conversation/session ownership, messages, history, builder-test purpose, conversation projections | run state machine, executor dispatch, profile publication |
| `runs` | run identity, admission result, attempt/generation, retry/resume/copy/cancel policy, tool-permission facts | queue transport, Harness-private events, conversation ownership |
| `context` | immutable context snapshots, memory selection, authorized context continuity | file byte storage, model loop |
| `files` | upload authorization, file record and binding lifecycle, authorized byte access | generated artifact truth, parser-specific Skill policy |
| `artifacts` | generated artifact record, lineage, lifecycle, authenticated download projection | temporary sandbox paths, model text claims |
| `object_lifecycle` | typed file/artifact deletion outbox, claim lease, retry/dead-letter/requeue, physical-delete receipt orchestration | file/artifact eligibility, retention policy, arbitrary object-store cleanup |
| `streaming` | transformation of committed safe facts, Redis live/replay contract, SSE cursor/gap/terminal transport | callback-batch receipt, run terminal truth, raw SDK events |
| `mcp` | MCP server/tool catalog and authorization | independent chat execution, generic provider secrets |
| `execution` | queue/worker orchestration, Harness adapter ports, model/executor selection, admitted capability execution | profile/Skill authorization, durable run authority, Sandbox lifecycle |
| `sandbox` | Sandbox Runtime lifecycle, attempt binding, callback-batch receipt, provider port, staging/recovery fences | provider SDK state as business truth, run admission |

Admin and Workbench views are projections of the owning contexts. They MUST NOT
become a second write authority or a generic `admin` domain. Model-selection
policy belongs to `execution`; provider credentials remain configuration at the
trusted adapter boundary. General Harness chat and specialized Skills remain
separate identities under
[`../adr/0005-harness-chat-is-not-a-skill.md`](../adr/0005-harness-chat-is-not-a-skill.md).

### 2.2 Kernel and platform limits

`kernel` is a dependency leaf for framework-neutral primitives used by at least
three contexts, such as typed identifiers, clocks, and base error vocabulary.
It MUST NOT contain product workflows, repository interfaces, HTTP types,
database records, provider clients, or a generic utilities collection.

`platform` owns reusable technical clients and resource lifecycle only. It MAY
wrap connection pools, transactions, Redis clients, object-storage clients, and
structured logging. It MUST NOT decide authorization, lifecycle, admission,
retention eligibility, release, or public projection. Domain-specific SQL and
storage decisions remain in that domain's `infrastructure` package.

`bootstrap` is the composition root. It MAY import every layer to create the API,
worker, executor, and maintenance processes. It MUST NOT contain business rules
or be imported by a domain.

## 3. Dependency direction

Within a domain, the normal dependency direction is:

```text
transport -> application -> domain -> kernel
                   ^
                   |
infrastructure ----+       infrastructure -> platform

bootstrap -> transport + application + infrastructure + platform
compat -> canonical api/transport
```

The rules are:

1. `domain` MUST use only the Python standard library and approved `kernel`
   primitives. It MUST NOT import FastAPI, Pydantic transport models, psycopg,
   Redis, boto/S3 clients, queue clients, Harness SDKs, or Sandbox providers.
2. `application` MAY import its own `domain`, define ports with `Protocol` or
   abstract interfaces, and call another context only through that context's
   `api.py`. It MUST NOT import a route or concrete infrastructure adapter.
3. `infrastructure` implements application-owned ports. It MAY import its own
   domain/application contracts, `platform`, and third-party clients. It MUST
   NOT import transport or decide product policy.
4. `transport` validates and translates one protocol, obtains an authenticated
   principal, invokes an application use case, and maps typed results/errors.
   It MUST NOT contain SQL, queue payload construction, capability admission,
   retention decisions, or provider calls.
5. `compat` MAY normalize a legacy name or wire shape and delegate. It MUST NOT
   perform a database write, queue dispatch, admission, authorization, release,
   lifecycle, or execution decision.
6. `platform` and `kernel` MUST NOT import a product context.
7. Only `bootstrap` may construct concrete adapters and register them with an
   application service or runtime registry.

### 3.1 Cross-domain calls

A context MUST NOT import another context's `domain/`, `application/`,
`infrastructure/`, or `transport/` internals. It may:

- call a narrow typed operation exposed by `<other>.api`;
- consume a versioned fact defined by `<other>.events`;
- reference a stable identifier/value type re-exported by the owner's
  `api.py`; or
- use a read-only projection whose owner and source facts are explicit.

One context MUST NOT mutate another context's tables. Cross-context writes are
orchestrated by an application use case that calls each owner. A read model MAY
join tables across contexts only when it is explicitly named as a projection,
is read-only, and does not become an alternative authorization or lifecycle
authority.

Shared object deletion is one explicit orchestration boundary, not permission
for generic cross-domain writes. `object_lifecycle` owns the outbox protocol and
calls file/artifact-owned eligibility and target-transition ports inside the
application-owned transaction. Only those target owners update their lifecycle
facts; only `object_lifecycle` claims, receipts, fails, dead-letters, or requeues
the outbox. This preserves the atomic target/outbox receipt without creating a
second file or artifact authority.

That orchestration MUST create one database Unit of Work and propagate its exact
transaction context through every target-transition and outbox port. A called
port MUST NOT start or commit an independent transaction. Target state and the
outbox receipt commit or roll back together under the required row locks. A
PostgreSQL integration test MUST prove atomic commit, rollback, lock scope, and
stale-lease fencing.

At most one outbox row exists for the typed target identity
`(tenant_id, target_type, target_id)`. Only `object_lifecycle` inserts or upserts
that row. A duplicate enqueue/delete request is idempotent only when target
identity, storage identity, target lifecycle, and outbox state agree; disagreement
fails closed and becomes reconciliation work.

Database foreign keys may protect cross-context referential integrity. Their
existence does not grant the consumer permission to update the referenced
owner's state.

### 3.2 Transactions and concurrency

The application use case owns its transaction boundary and concurrency
invariants. Repositories expose domain-specific operations through ports; a
route MUST NOT coordinate unrelated SQL calls directly. Locks, generation
fences, idempotency keys, queue receipts, and terminal transitions MUST remain
with the business operation whose race they protect.

Moving code MUST preserve lock acquisition order, transaction scope, identity
binding, and side-effect ordering. A source move that changes one of those is a
behavior change and requires a separate design and concurrency evidence.

## 4. Runtime and data ownership

This section maps source placement; the business authority remains
[`runtime-authorities.md`](runtime-authorities.md).

| Runtime/resource | Source owner | Contract |
| --- | --- | --- |
| API process | `bootstrap.api` plus domain `transport/http` | HTTP/SSE translation only; no SDK execution |
| Worker process | `bootstrap.worker`, `execution.application`, and admitted domain APIs | consumes run queue, restores authority, invokes adapters, persists through owners |
| Executor process | `bootstrap.executor`, `execution.infrastructure`, `sandbox` | engine/provider-private loop behind platform contracts |
| Maintenance process | `bootstrap.maintenance` calling `object_lifecycle`, `artifacts`, and `context` APIs | bounded scheduled cleanup/reconciliation; not a second worker or retention authority |
| PostgreSQL | domain `infrastructure/postgres` adapters; migration runner in `platform.postgres` | durable business truth; each table has one write owner |
| Redis queue | `execution.infrastructure` using `platform.redis` | bounded transport, leases, and delivery; not run truth |
| Redis Streams | `streaming.infrastructure` using `platform.redis` | safe bounded live/replay plane; not terminal truth |
| Object storage | `files` and `artifacts` adapters using `platform.object_storage` | bytes only; PostgreSQL records authorize identity/lifecycle |
| Object deletion loop | `object_lifecycle.application` and its target-owned ports | one typed outbox claim/receipt/retry protocol; eligibility remains with target owners |
| Harness SDK | `execution.infrastructure/harness/<provider>` | private model/tool loop; cannot mint platform authority |
| Sandbox provider | `sandbox.infrastructure/providers/<provider>` | translates governed lifecycle; provider state is not business truth |

The attempt-bound callback-batch receipt remains part of the Sandbox Runtime
control contract, and run terminal intent remains `runs`-owned. `streaming`
projects only already-authorized or committed safe facts and MUST NOT create,
reinterpret, or independently receipt either authority.

Dynamic provider, Harness, parser, and plugin selection MUST use an explicit,
typed registry owned by the relevant context and populated by `bootstrap`.
`<domain>/registry.py` defines the immutable capability key, descriptor, adapter
port, and `register`/`resolve` contract. Bootstrap registers concrete adapters;
duplicate keys and an unknown configured key fail startup. Configuration may
select only a registered capability key. It MUST NOT contain a Python module
path, class name, unrestricted command, or arbitrary import target.

Process entrypoints are a separate static inventory in `bootstrap`: API, worker,
executor, and maintenance. Docker, Compose, packaging, and operator commands
bind to one of those known entrypoints. Import-time registration, unrestricted
command parsing, and hidden `getattr` dispatch are forbidden for new code. A
registry row names a supported production capability; test doubles do not enter
the production registry. Removing a registry row or process requires enumerating
the key, bootstrap registration, settings selector, image command, Compose/CI,
operator docs, persisted identities, and observed runtime selection.

The baseline still runs maintenance in the shared worker loop. That is an
explicit migration exception, not evidence that `bootstrap.maintenance` is a
deployed process. Splitting the entrypoint requires separate packaging,
deployment, rollback, and runtime acceptance evidence; source movement alone
does not create a new service.

## 5. Naming and coding rules

### 5.1 Packages and modules

- Package and module names MUST be `snake_case` and describe one business
  concept or adapter responsibility.
- New global dumping modules named `models.py`, `repositories.py`, `service.py`,
  `services.py`, `utils.py`, `helpers.py`, `common.py`, or `manager.py` are
  forbidden at `app/` root and discouraged inside a context. Use the concept,
  such as `run_admission.py`, `profile_revision.py`, or
  `postgres_run_repository.py`.
- Delivery-phase, ticket, and evidence labels such as `b1`, `phase2`, `211`,
  `poc`, `readiness`, or `acceptance` MUST NOT name production modules unless
  that term is a lasting product concept.
- A module SHOULD have one reason to change. Adding a second business domain to
  an existing hot module is forbidden even when splitting that module is not in
  the current PR.

### 5.2 Types and operations

| Meaning | Naming rule |
| --- | --- |
| Domain concept | noun, for example `RunAttempt`, `ProfileRevision` |
| Transport input/output | `...Request`, `...Response` |
| Application intent | `...Command`; read intent is `...Query` |
| Handler/use case | explicit verb, for example `AdmitRun`, `PublishProfile` |
| Persisted row shape | `...Record` |
| Safe read model | `...Projection` |
| Application port | capability noun, for example `RunRepository`, `EventPublisher`, `Clock` |
| Concrete adapter | technology plus port, for example `PostgresRunRepository`, `RedisEventPublisher`, `S3ArtifactStore` |
| Business decision owner | `...Authority`, only when it is the single authoritative policy decision |
| Compatibility boundary | `<subject>_compat.py` or a module under `compat/<subject>/` |
| Stable error | typed error plus lower-snake-case `code` owned by the boundary |

Functions MUST use a precise verb. Names such as `process`, `handle`, `manage`,
`execute`, `data`, `payload`, or `item` require a boundary-specific qualifier.
Async functions do not add `_async`; the return type and implementation express
that property.

Identifiers end in `_id`; SHA-256 digests end in `_sha256`; byte counts end in
`_bytes`; durations include their unit such as `_seconds`; timestamps end in
`_at`. A field whose unit or identity is ambiguous MUST NOT cross a boundary.

### 5.3 Configuration, constants, and hard-coded values

- Environment parsing and process defaults belong to `bootstrap.settings`.
  Domain code receives typed configuration and MUST NOT read environment
  variables directly.
- Endpoint URLs, credentials, ports, model/provider selection, retention
  windows, and deployment-specific workspace values MUST NOT be embedded in
  routes or business logic.
- Stable protocol identifiers and business constants MAY be code constants only
  in their owning context. Other contexts import the public constant or derive
  a projection; they MUST NOT duplicate its literal.
- Every protocol/business literal that crosses a context boundary MUST have one
  named canonical symbol exported by the owner's `api.py` or `events.py`. The
  architecture policy keeps an explicit governed-symbol/owner inventory and
  rejects a definition of that governed literal outside its owner; it does not
  attempt to ban harmless repeated prose strings.
- A default that changes authorization, tenant/workspace scope, provider, model,
  or persistence target MUST be explicit at admission/configuration and fail
  closed in production. Compatibility defaults require a named legacy
  consumer.
- Provider, parser, Skill, model, and executor mappings MUST have one canonical
  registry and a consistency test. Parallel hand-built dictionaries are
  forbidden.

### 5.4 Errors and projections

Domain/application errors carry a stable machine code and safe structured
facts. HTTP status, SSE shape, and compatibility wording are mapped in
transport. Raw database errors, SDK exceptions, commands, secrets, storage keys,
and private paths MUST NOT cross a public projection.

An ordinary-user projection MUST be intentionally smaller than its admin or
internal record. Serialization of a persistence record is not a projection
strategy.

## 6. What belongs in the repository

The repository retains:

- supported production source and explicit process entrypoints;
- source-level tests and reusable test support under `tests/`;
- schema migrations and compatibility readers required by persisted facts;
- operator and governance tools that are part of a documented authority;
- durable architecture, contract, and operations documents; and
- reviewed, redacted evidence only under the existing evidence authority.

The following MUST move out of `app/` or be deleted after the applicable proof:

- test doubles, fake providers, deterministic runners, and fixtures that are not
  supported production capabilities: move to `tests/support/`;
- one-off analysis, migration preparation, source audit, evidence generation,
  and release verification: move to `tools/` or `scripts/` when they remain a
  maintained operator/governance capability;
- generated logs, reports, screenshots, caches, and local runtime artifacts: do
  not commit them;
- superseded experiments and POCs with no supported runtime or external
  consumer: delete them instead of keeping a dormant adapter in production;
- duplicate business implementations: keep one canonical owner and, only when
  proven necessary, one logic-free compatibility facade.

Zero production registration is strong evidence that an adapter is not a
supported runtime, but deletion still requires checking configured entrypoints,
packaging, deploy manifests, scripts, docs, and external imports.

## 7. Compatibility contract

Compatibility is exceptional and evidence-based. It is not created "just in
case" and it is not justified solely by a date.

Every compatibility boundary MUST record in its issue/PR:

1. canonical owner and replacement contract;
2. named consumers that cannot migrate atomically;
3. exact legacy input/output or import surface;
4. whether persisted historical records require dual read;
5. observable usage or a bounded consumer inventory;
6. removal condition and rollback constraint; and
7. a contract test proving delegation and fail-closed behavior.

For a deployed route, configuration key, import, or entrypoint, observable exit
evidence also records the exact source/image/config subject, telemetry or
inventory source and reproducible query, observation start/end, tenant and
principal/consumer scope, predeclared minimum observation window, release or
normal-use cycle covered, owner attestation, and known blind spots. The window
MUST be chosen before collection and cover at least one normal rollout/use cycle.
If telemetry or a complete consumer inventory is unavailable, deletion remains
blocked.

An internal alias with no independently deployed consumer SHOULD be migrated
and removed in the same PR or one bounded dependent sequence. Public routes,
environment variables, deployment entrypoints, and external Python imports may
need a compatibility interval, but the exit gate is consumer migration plus
observed zero use, not elapsed time. A sunset date MAY set an operational
deadline; it MUST NOT manufacture a consumer or keep a dead alias alive.

A compatibility module MUST remain thinner than the canonical owner. It may
rename fields, translate a retired wire shape, or delegate an import. It MUST
NOT contain SQL, object-store operations, queueing, a provider call, a second
registry, or an independent authorization/lifecycle decision.

Persisted-state compatibility follows the owning ADR and migration contract.
Historical rows are not bulk-rewritten merely to make source cleanup easier.

## 8. Deletion proof

The deletion issue/PR MUST state the exact baseline and provide the proof tier
matching the real surface. Negative `rg` output is only one input.

| Surface removed | Minimum proof before deletion |
| --- | --- |
| Private function/class | AST and text reference scan; decorator/registration/reflection scan; affected tests; history check when intent is unclear |
| Python module/package | all private-symbol proof plus imports, `__all__`, dynamic imports, package data, CLI/module execution, configuration and test fixtures |
| CLI/script/entrypoint | module proof plus Dockerfile, Compose, CI, cron/systemd, operator docs, shell/PowerShell callers, packaging metadata, and supported external automation |
| Provider/executor/parser/plugin | explicit registry and settings defaults, deploy configuration, image entrypoint, runtime selection evidence, persisted identities, and rollback behavior |
| Public HTTP/SSE/callback route | server route inventory, generated/open API clients, frontend and external consumer inventory, deprecation/fail-closed contract, observed no-call window when deployed, and stable migration path |
| Environment/configuration key | every Compose/CI/operator/secret owner, canonical precedence, deployed inventory, rollback plan, and zero-use evidence; no alias solely for calendar compatibility |
| Database column/table/state/event | forward migration, old/new reader and writer matrix, queue drain or dual-read proof, backfill bounds, rollback, retention, audit/history, and referential-integrity checks |
| Import compatibility facade | canonical identity/delegation test, internal and external import inventory, usage evidence, and satisfied removal condition |

If an external or runtime consumer cannot be observed, deletion is **blocked**;
it is not made safe by adding a broad exception. Material deletion evidence is
recorded in the issue/PR under the repository workflow, not in a mutable status
document.

Each broad cleanup issue carries an item disposition table with `path/symbol`,
current invocation surface, target owner or `tests/support`/`tools`/delete
disposition, proof tier, evidence, and blocked dependencies. Notebooks,
fixtures, ad-hoc docs, and maintained scripts must appear in that inventory
rather than being silently classified as production.

After deletion, run the smallest complete proof of absence and the affected
contract/integration tests. Deleting material persisted data or an active
runtime target requires a separately authorized destructive operation; source
cleanup does not grant that authority.

## 9. Migration and behavior replay

Broad restructuring uses the following sequence per bounded slice:

1. **Classify.** Name the current business fact, canonical target context,
   consumers, side effects, locks, persisted identities, and runtime surfaces.
2. **Freeze behavior.** Add or identify focused contract tests at the old public
   boundary, including denial and concurrency behavior where relevant.
3. **Create the owner.** Extract the implementation into the target domain
   without changing route shape, schema, error code, queue identity, lock order,
   or public projection.
4. **Replay.** Run the same input/output and side-effect corpus through the
   canonical owner and the old boundary. For persistence code, compare SQL
   intent, transaction/lock scope, receipts, and failure behavior rather than
   only return values.
5. **Delegate.** Replace the old implementation with a thin facade only if a
   proven consumer still needs it. There MUST NOT be two writable authorities.
6. **Migrate callers.** Move callers domain by domain to `api.py` or the owning
   adapter. Do not use a repository-wide rename that hides dependency changes.
7. **Prove absence.** Apply the deletion proof for the old surface, remove the
   facade, and keep the canonical contract test.
8. **Change behavior separately.** Any policy, schema, wire, rollout, or runtime
   change follows its own issue/ADR and evidence after ownership is stable.

Moves SHOULD preserve Git history with focused renames when practical. Copying
then independently editing two implementations is forbidden. Dual write is
forbidden unless a persistence ADR specifies reconciliation, idempotency,
cutover, and rollback.

The replay corpus MUST be committed as deterministic focused contract or
integration tests with fixed clocks/identities where those affect output. The
PR records the exact base/head, test paths, command, result, and which observable
dimensions were compared: response/error, persisted state, SQL/lock/transaction
scope, queue/event/receipt identity, and failure side effects. A generated
replay report is not committed; the versioned tests plus exact PR evidence are
the reproducible record. Manual return-value comparison alone is insufficient.

## 10. Current-to-target mapping

This table classifies source at the decision baseline; it is not a progress
ledger. It names the target owner for future bounded migrations.

| Current surface | Target owner |
| --- | --- |
| `app/main.py`, global settings and resource construction | `bootstrap.api`, `bootstrap.settings`, and `platform` clients |
| `app/auth.py`, `app/auth_sessions.py`, role governance | `identity` |
| `app/agent_apps/**`, `app/agent_profiles.py`, Agent Profile routes | `agent_apps`; old import/route surfaces become explicit `compat` only when needed |
| `app/skills/**`, Skill marketplace/distribution/release code | `skills` |
| Chat/session routes, `app/agent_conversation_repository.py`, message/session persistence | `conversations` |
| Run routes, retry/resume/copy/cancel, tool-permission and run lifecycle persistence | `runs` |
| Queue consumers, `app/worker.py`, `app/worker_main.py`, model/executor selection | `execution` plus `bootstrap.worker` |
| `app/executors/**` and Harness SDK translation | `execution.infrastructure.harness` |
| `app/context/**`, memory selection and context continuity | `context` |
| `app/attachments/**` and file parser contracts | byte classification belongs to `files`; Skill requirements belong to `skills`; run attachment admission is orchestrated by `runs` through both public APIs |
| Upload/file authorization and file lifecycle persistence | `files` |
| Artifact records, lineage, and expiry eligibility | `artifacts`; shared byte client remains `platform.object_storage` |
| `app/persistence/object_deletions.py` and shared file/artifact outbox transitions | `object_lifecycle` |
| `app/data_retention.py` and maintenance scheduling | `bootstrap.maintenance` orchestrating `object_lifecycle`, `artifacts`, and `context` public APIs |
| `app/streaming/**`, run-event repositories and SSE projection | `streaming` |
| `app/mcp/**` and MCP sections of the global repository | `mcp` |
| `app/runtime/sandbox/**` and Sandbox Runtime routes | `sandbox` |
| `app/db.py`, `app/storage.py`, and connection/client construction | `platform.postgres`, `platform.object_storage`, and `bootstrap` wiring; no business repository logic |
| `app/schema.sql` and `app/schema_migrations.py` | versioned `platform.postgres.migrations`; every business table/change still names its bounded-context owner |
| `app/routes/lambchat_compat.py` and retired wire aliases | `compat/lambchat` delegating to domain transports/APIs |
| Other `app/persistence/**` modules | split into each owning domain's `infrastructure/postgres`; temporary facades may delegate |
| `app/repositories.py` | dissolved into domain repository adapters; old module is a temporary facade, never a new owner |
| `app/models.py` and `app/validation.py` | domain values, application contracts, transport DTOs, and persistence records in their owner |
| `app/routes/**` | each context's `transport/http`; shared root router assembly only in `bootstrap.api` |
| readiness, audit, acceptance, baseline and evidence generators in `app/` | supported runtime health belongs to its domain; source/release/evidence tools move to `tools/` or `scripts/`; obsolete POCs are deleted |
| fake providers, deterministic adapters, and executor stubs | `tests/support` unless the capability is explicitly supported and registered in production |

The existing `app.persistence` and Agent Profile facades demonstrate the desired
intermediate shape only when they are logic-free and identity-bound to one
canonical owner. Parallel SQL or policy in both old and new modules is a defect.

An approved legacy migration bridge is narrower than a compatibility facade.
It MAY let one frozen legacy source module import one exact bounded-context
`infrastructure` module solely to preserve an existing Python symbol as a
top-level identity alias while its implementation moves. Every source path,
target module, module alias, and symbol MUST be listed by immutable architecture
authority before the move. The legacy source MUST shrink in the activating
change, the target MUST define every declared symbol, and the bridge MUST reject
prefixes, wildcards, dynamic imports, rebinding, new executable source logic,
renames, and exceptions. A bridge grants import compatibility only; it does not
make the legacy module a persistence owner or a public cross-domain API.
Each authority entry MUST also state its observable removal condition. Bridge
retirement is two bounded changes: first remove the authority entry after its
condition is proven while keeping the aliases stable; then remove the aliases
under the next authority. Candidate policy edits never authorize either step.

A legacy public-API cutover is a different, one-shot authority. It MAY let one
frozen legacy source delete an exact set of locally defined symbols and replace
every use with one declared bounded-context `api.py` or `events.py` symbol. The
authority records a one-to-one old/new symbol map, one exact static module
alias, and the exact owning domain/application module. The public boundary may
only expose those symbols as explicit same-name static re-exports from that
owner; it cannot implement or replace policy locally. The authority may also
inventory exact now-unused
standard-library imports removed with those definitions. The checker canonicalizes only those declared attribute
replacements and requires the rest of the source AST to equal the baseline
after the declared definitions and imports are removed. It rejects source deletion or rename, partial or extra
rewrites, retained or rebound legacy symbols, wildcards, dynamic imports,
private or infrastructure targets, new SQL/control flow/state/functions, and
exceptions. A cutover creates no compatibility alias and grants no general
permission to edit the frozen source. After activation the source remains
frozen until an authority-only change removes the consumed cutover entry.

## 11. Test architecture

The target test tree mirrors ownership:

```text
tests/
  unit/<domain>/
  contract/<domain>/
  integration/<domain>/
  architecture/
  support/
```

- Unit tests exercise pure domain and application policy without real external
  infrastructure.
- Contract tests freeze `api.py`, events, transport projections, ports, and
  compatibility delegation.
- Integration tests exercise PostgreSQL, Redis, object storage, SDK/provider
  adapters, and concurrency where those semantics cannot be mocked truthfully.
- Architecture tests enforce imports, placement, registries, facade shape, and
  legacy-surface no-growth.
- Test doubles and fixture builders live under `tests/support`; production code
  MUST NOT expose a fake capability solely to make tests convenient.

Existing flat tests migrate with the production slice they protect. A cleanup
PR MUST NOT move hundreds of unrelated tests for visual consistency.

Source and local tests prove source contracts only. CI builds prove their named
build/test subject. Deployment and runtime claims require the exact external
acceptance procedure in the repository authority documents.

## 12. Executable governance

The architecture decision is followed by a separate, independently reviewed
gate. The gate MUST use the trusted exact-base/exact-head mechanism already
used by repository governance and initially enforce new changes rather than
pretend the legacy tree is clean.

The first gate version MUST check:

1. forbidden dependency edges among domain, application, infrastructure,
   transport, platform, bootstrap, and compat layers;
2. cross-domain imports limited to `api.py`, `events.py`, and `kernel` types;
3. no new unapproved `app/` root modules or generic dumping modules;
4. no new domain responsibility or unexplained growth in frozen hot files;
5. compatibility facades contain no SQL, provider call, queue dispatch, or
   independent business branch and remain bounded in size;
6. production registries exclude test doubles and arbitrary dynamic imports;
7. governed protocol constants and registry keys have one declared owner;
8. moved/deleted public, dynamic, or persisted surfaces name their proof tier;
9. architecture exceptions bind exact paths and candidate scope, state a
   reason/owner/removal condition, expire, and cannot exempt security or
   authority violations.

Machine configuration for the gate is a policy input, not a manual progress
ledger. The follow-up owns the root `architecture-policy.json` plus
`schemas/architecture-policy.v1.schema.json`. The policy enumerates target
packages/layers, allowed public cross-domain modules, approved root modules,
governed symbol owners, canonical registry module/key/settings-selector owners,
and frozen hot files. A candidate-only
`.architecture-governance-exception.json` binds an exception to exact base/head,
paths, owner, reason, and removal condition. The gate itself MUST be introduced
in a later PR so the candidate that defines it cannot certify its own
correctness.

## 13. Review checklist for every backend PR

Before approving a change under `app/`, reviewers answer:

1. Which bounded context owns the business fact?
2. Is the code in the correct layer, and do imports point inward?
3. Does another module already own this policy, registry, constant, or SQL?
4. Is the transport thin and is the public projection intentionally safe?
5. Are units, identifiers, versions, and error codes explicit in names?
6. Did the change add to a frozen global module or create a generic bucket?
7. Is compatibility tied to a real consumer and measurable exit condition?
8. If code was moved, was behavior replayed before behavior changed?
9. If code was deleted, was the correct deletion proof completed?
10. Which evidence is source, CI/build, deployment, and runtime, and which has
    actually been observed?

Failure to name an owner is a design blocker, not a reason to place code in a
shared module.
