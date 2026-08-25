# Execution Specification And Attempt Lifecycle

Status: normative source-architecture decision; implementation is incremental

Current stacked foundation: the worker compiles an in-memory specification
before dispatch, and Runs owns the durable attempt schema plus create/transition
writers. Existing queue, worker, callback, and Sandbox paths are not yet cut over
to those writers.

Owner: `runs` bounded context

Parent contract: [`source-code-architecture.md`](source-code-architecture.md)

Terminalization contract: [`run-lifecycle-boundary.md`](run-lifecycle-boundary.md)

Streaming contract:
[`redis-streams-sse-execution-control.md`](redis-streams-sse-execution-control.md)

## 1. Decision

The completed migration gives every dispatch or redispatch one immutable
`ExecutionSpec` and one durable `RunAttempt` that references the exact
specification digest it is authorized to execute. A later attempt may compile a
new, narrower specification after current reauthorization; it cannot expand the
Run's admitted authority.

The two values answer different questions:

- `ExecutionSpec`: the exact admitted-and-reauthorized work for this attempt;
- `RunAttempt`: which bounded owner is currently authorized to try that work.

`runs` owns both durable facts. `execution` may claim and orchestrate an attempt,
but Redis queue metadata is a lease projection rather than the attempt ledger.
`sandbox` owns its provider lifecycle and binds its lease to the durable attempt.
`streaming` projects already committed attempt and Run facts.

This decision does not introduce a second Run status or terminal authority.
`runs.status` remains the compatibility Run-level projection until the attempt
migration is complete. Run terminalization continues to follow
`run-lifecycle-boundary.md`.

## 2. Immutable execution specification

### 2.1 Canonical contents

The specification freezes only admitted execution facts:

- tenant, workspace, principal, session, Run, and Agent identity;
- execution kind and the admitted Agent Profile revision/hash when present;
- selected Skill identity, locked version, release decision, and governed
  manifest references when the execution kind is `skill`;
- admitted input and file identities;
- the exact safe executor projection of the immutable context snapshot once the
  worker-owned context preparation step has completed;
- executor/model selection and the admitted execution-policy identity; and
- the specification schema version.

An executor projection may embed bounded safe Profile, Skill, input, or context
material required to make the attempt deterministic. The owning record identity
and integrity value remain in the specification, and the embedded projection
does not copy Profile, Skill, file, or context authority into Runs.

### 2.2 Explicit exclusions

The specification MUST NOT contain:

- provider API keys, authorization headers, callback tokens, raw secrets, or a
  credential value;
- a Sandbox provider handle, container name, executor URL, workspace path, or
  stop/release receipt;
- Redis message IDs, worker IDs, owner tokens, heartbeat timestamps, retry
  counters, or queue-private fields;
- an out-of-band runtime grant that is absent from the final bounded
  reauthorization projection; or
- terminal result, error, event, audit, or publication state.

Provider credentials remain behind the trusted host adapter. An attempt may
hold an opaque, short-lived credential-lease reference whose scope is fenced by
tenant, Run, attempt, provider, method/path, and expiry. Neither the credential
nor that lease reference is part of the reusable `ExecutionSpec`.

### 2.3 Canonical representation

The owning compiler produces:

- `schema_version`;
- exact canonical UTF-8 JSON bytes; and
- `spec_sha256`, the lowercase SHA-256 of those bytes.

Canonical JSON uses sorted object keys, no insignificant whitespace, UTF-8 text,
and rejects non-JSON values and non-finite numbers. Unknown top-level fields,
unsupported schema versions, identity mismatch, and supplied digest mismatch
fail closed. Callers receive copies/projections; they never receive mutable
references to the compiler's internal value.

Each attempt's specification is compiled only after admission and worker-side
reauthorization/context preparation have completed and immediately before the
first executor dispatch. Reauthorization may remove capability but MUST NOT add
an Agent, Skill, file, model, tool, or policy not present in the admitted facts.

## 3. Durable attempt state machine

`RunAttempt` is the sole durable execution-attempt authority. Its minimum state
graph is:

```text
created -> queued -> claimed -> running -> succeeded
    |         |         |          |-----> failed
    |         |         |          |-----> cancel_requested -> cancelled
    |         |         |---------> expired -> failed
    |         |-------------------> cancelled
    |-----------------------------> cancelled
```

The initial implementation MAY collapse `succeeded`, `failed`, and `cancelled`
into a typed terminal outcome, but the persisted transition and public
projection must remain unambiguous.

Every transition is a compare-and-set over the full fence:

```text
tenant_id + run_id + attempt_id + expected_state
+ expected_owner_kind + expected_owner_id + expected_owner_generation
```

An owning transition transaction updates the attempt, projects the compatible
Run status, records the safe lifecycle event/audit fact, and freezes any
terminal publication intent. Queue acknowledgement and Redis publication occur
after commit and cannot authorize a database transition.

### 3.1 Implemented foundation and remaining cutover

The current foundation adds the `run_attempts` relation, one-open-attempt
uniqueness, a single Runs-owned create writer, immutable specification/attempt
identity, exact legal-edge and generation-increment enforcement in PostgreSQL,
and one Runs-owned CAS writer that advances the attempt through the database
transition guard. That guard is the sole compatible Run projector in the same
statement, so a legal direct attempt update cannot bypass the invariant or
double-update the Run row.
PostgreSQL accepts only `created` as an initial attempt, locks and verifies a
queued parent Run plus its durable identity, retains the exact canonical UTF-8
JSON text, and verifies both its JSON projection and SHA-256 before accepting
the row.
`expired` remains an open arbitration state: a reconciler must fence the old
owner and finish that attempt before a later ordinal can be created.

This foundation is not the worker cutover. Existing queue, worker, callback,
Sandbox, event/audit, and terminalization writers do not yet create or advance
`run_attempts`; they remain on the legacy Run/Sandbox authorities until the
dual-write phase below. Real PostgreSQL DDL/readiness proof, event/audit
publication, Redis reclaim, callback/Sandbox binding, and mixed-version runtime
acceptance are therefore still required before the attempt migration can be
called complete.

Redis reclaim creates a new durable ordinal attempt before a new worker can
execute. It never overwrites the old attempt identity. Retry, resume, and copy
continue to create a new Run under the current product contract; they do not
copy an attempt, queue lease, Sandbox handle, callback token, credential lease,
or stream incarnation.

## 4. Sandbox and callback binding

A Sandbox lease references a non-null durable attempt. Sandbox provider phases
remain Sandbox-owned and use at least these durable meanings:

```text
provisioning -> ready -> executing -> releasing -> released
                                      |----------> cleanup_failed
```

These phases do not replace the attempt or Run state. They explain provider
resource truth and cleanup progress. A terminal Run with `releasing` or
`cleanup_failed` Sandbox state is valid and MUST NOT claim resource release.

Callback receipt requires the exact tenant/Run/attempt/runtime fence. Executor
terminal callbacks remain observations; they do not own Run terminalization.
Accepted and rejected callback receipts retain the attempt identity and a safe
reason without persisting callback secrets.

## 5. Ownership and dependency rules

| Concern | Canonical owner | Forbidden shortcut |
| --- | --- | --- |
| Specification value/compiler | `app.runs.domain` / `app.runs.application` | route-built canonical bytes or queue-owned digest |
| Specification persistence and attempt CAS | `app.runs.infrastructure.postgres` | new SQL in `app/repositories.py` |
| Queue claim and worker orchestration | `app.execution` | queue message as durable attempt truth |
| Sandbox phases and cleanup | `app.sandbox` | Runs calling a provider or marking a lease released |
| Live/terminal projection | `app.streaming` | Redis callback or stream entry as terminal authority |
| Provider credential injection | trusted host adapter | raw credential in Run, queue, spec, Sandbox request, event, or audit |

Routes may validate transport and request admission. They MUST NOT construct a
second specification schema. Workers may request a specification projection only
through the Runs public API and may not rebuild or override it from queue data.

## 6. Mixed-version migration

Migration uses additive expand/contract ordering:

1. Introduce the pure specification compiler, canonical codec, and owning tests.
   Keep existing `QueueRunPayload` and `RunPayload` behavior unchanged.
2. Add the `run_attempts` relation with the specification version, canonical
   JSON text, JSONB projection, and digest. Deploy schema before wiring runtime
   callers to the new writers.
3. Backfill and report legacy conflicts using Run columns, admitted input,
   Skill snapshots, context snapshots, queue/Sandbox lease facts, and stream
   authority. Never guess across conflicting identities.
4. Dual-write the specification and first attempt. New workers read the durable
   specification when present and use a bounded legacy compiler only for rows
   explicitly marked legacy. Queue wire shape remains compatible; do not add a
   producer field that old `extra="forbid"` workers reject.
5. Make every worker, callback, Sandbox, terminalization, and reconciler path
   use the durable attempt fence. Retain `runs.status` as a same-transaction
   projection.
6. Enforce non-null specification/attempt bindings and database constraints only
   after mixed-version telemetry and backfill prove the invariant.
7. Remove legacy compiler/read paths under the repository compatibility contract.

Rollback may stop new writers and return to dual-read while nullable fields and
new tables remain. It MUST NOT drop persisted specification or attempt facts
after a new writer has committed them.

## 7. Required evidence

The specification slice requires focused proof for:

- deterministic bytes/digest across mapping order and process restart;
- caller mutation after compile not changing the specification;
- exact schema/field/type validation, non-finite-number rejection, and digest
  mismatch rejection;
- Skill versus Harness-chat identity constraints;
- exclusion of credential and queue/Sandbox-private fields;
- legacy payload projection preserving current field names/defaults; and
- old producer/new worker compatibility without changing the queue wire shape.

The attempt slice additionally requires:

- PostgreSQL CAS, rollback, lock contention, unique active attempt, and exact
  terminal event/audit facts;
- rejection of non-`created` inserts, non-queued parent Runs, canonical
  JSON/JSONB drift, digest drift, and wrong same-named schema constraints;
- real Redis lease/reclaim races mapped to durable ordinal attempts;
- cancellation before dispatch, during execution, after provider stop failure,
  and cleanup retry;
- stale queue owner, callback, Sandbox handle, terminalizer, and stream
  incarnation all failing closed;
- retry/copy proving that attempt, credential, Sandbox, callback, and stream
  identities are not copied; and
- mixed-version deployment, backfill conflict, rollback, and recovery evidence.

Focused tests and green CI do not prove schema deployment, provider cleanup,
credential isolation, Redis convergence, or External Acceptance. Those evidence
levels must remain separately labelled.
