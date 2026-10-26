# Execution Specification And Attempt Lifecycle

Status: normative source-architecture decision; implementation is incremental

Current stacked foundation: the worker compiles and persists the immutable
specification before dispatch, Runs owns the durable attempt schema and
transitions, and ordinary queue workers bind the initial Redis lease plus every
successful heartbeat to the exact attempt owner fence. Callback, Redis reclaim,
and remaining Sandbox paths are not yet fully cut over to that fence.

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

A rejected dispatch compilation keeps the generic public `execution_spec_invalid`
outcome. Its typed `ExecutionSpecError.code` is retained only as a private
predispatch diagnostic reason through the existing Runs failure owner. Input,
canonical specification bytes and arbitrary exception text are not copied
into that reason. Unknown `ValueError` failures remain generic; MCP retains
its existing typed error mapping. This observation does not alter admission,
Attempt fencing or terminalization.

Each attempt's specification is compiled only after admission and worker-side
reauthorization/context preparation have completed and immediately before the
first executor dispatch. Reauthorization may remove capability but MUST NOT add
an Agent, Skill, file, model, tool, or policy not present in the admitted facts.

### 2.3 Model-capacity expansion

The codec accepts historical `ai-platform.execution-spec.v1` canonical bytes,
the frozen `ai-platform.execution-spec.v2` mapping, and an exact-field
`ai-platform.execution-spec.v3` mapping. Both model-bearing versions freeze the
`model_gateway_revision` and `model_max_output_tokens`; v3 freezes
`model_auto_compact_window` while v2 keeps its original
`model_max_input_tokens`. The dispatcher can compile v3 only from an
independently locked Runs-owned model snapshot matching its model identity, not
from Redis queue fields. Worker dispatch writes v3 and rejects a missing or
mismatched durable model snapshot. The sandbox transport carries the paired
`model_token_limits` projection; the Run/Attempt-bound proxy verifies the
binding and output limit. The compaction window configures SDK-owned automatic
compaction, not an additional proxy input recount. The detailed enforcement
boundary is owned by [Model credentials](opensandbox-ephemeral-model-credentials.md).

The Runs-domain codec owns the frozen v3 wire range (100000–1000000) without
importing live Execution policy; changing that schema range requires a new
schema version, not a model-catalog setting change.

Frozen v2 attempts stay byte-identical and recoverable: every reader that needs
the engine window maps their `model_max_input_tokens` through the clamp the
runner applied (`min(1M, max(100k, value))`). That mapping lives with the model
catalog policy and has no new writers; the Runs application-layer projection
calls it through Execution's public API, while the domain codec preserves the
original v2 values. v3 is the only version the dispatcher compiles.

Every other Run/Attempt-bound reader that gates on the specification version
accepts both model-bearing versions rather than v2 alone, so a v3 Attempt keeps
the same Context and terminal authority. Provider-session epoch, lineage and
turn-commit lookups therefore match `v2` and `v3` while keeping their existing
tenant, workspace, user, Run, Attempt, owner-generation and lease checks. The
stored `execution_spec_schema_version` is never rewritten to imitate v2.

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
Run status, and records the safe lifecycle event/audit fact in the same
transaction. Under [ADR 0013](../adr/0013-redis-stream-only-sse.md), terminal
publication derives from committed Run/event facts; independent terminal intents
are retired. Queue acknowledgement and direct Redis publication occur after
commit and cannot authorize a database transition. Publication ordering belongs
to [SSE execution control](redis-streams-sse-execution-control.md).

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

The first worker cutover slice derives a Runs-owned `rat_*` identity from the
opaque Redis `qat_*` lease identity without changing `QueueRunPayload`, compiles
the immutable specification immediately before dispatch, advances the durable
attempt through `created -> queued -> claimed -> running`, and uses the durable
identity for executor, Sandbox, callback, stream, cancellation, and terminal
projection paths. Pre-dispatch failures that never produce a valid specification
remain compatible Run-only terminal paths.

`lock_queued_run_for_attempt` is a read/lock port: it selects the queued Run with
`FOR UPDATE OF runs`, requiring matching tenant, workspace, user, session, and
Agent identity. It retains the admitted authorization snapshot and distinct Run
and session Profile pins without changing Run status. There is no separate
Run-only running writer in the lifecycle service or PostgreSQL lifecycle adapter;
only the durable Attempt dispatch transition projects queued to running.

This is not the complete attempt migration. The ordinary worker records the
initial queue-message identity and lease window during `created -> queued`, then
persists each Redis-fenced heartbeat with the exact attempt identity, queue
identity, worker owner, and current `owner_generation`. A Redis heartbeat whose
PostgreSQL write or commit fails stops worker execution and is not treated as
durable success: the worker does not acknowledge or fail the queue message, and
the Redis-only extension remains bounded by one visibility-window grace deadline
from worker processing start. That deadline covers the sleep, Redis renewal,
PostgreSQL write, and transaction commit rather than only the interval between
heartbeat cycles. If no durable attempt exists by that deadline, the worker stops
renewing and cancels local execution. Redis and PostgreSQL share the exact
heartbeat value returned by the Redis authority.

That queue heartbeat is maintained only while the worker owns and processes the
queue item. After an asynchronous dispatch is accepted and the item is
acknowledged, the Attempt's queue heartbeat/expiry remain historical queue
ownership facts; Sandbox and Executor continue their own heartbeats. A stale
queue timestamp alone cannot establish loss of current execution ownership.
Admin runtime health uses the latest durable Attempt, an active same-tenant/Run/
Attempt Sandbox lease, and its current owner-generation binding. It never
refreshes the queue lease to make monitoring appear healthy. Missing or
ambiguous current-runtime evidence remains unknown; an executor terminal receipt
while the Run is still running is shown as awaiting platform reconciliation.

Lease acquisition, heartbeat refresh, and the final reclaim decision use Redis
server time. A heartbeat normalizes future-skewed Redis lease and worker activity
timestamps. Reclaim independently normalizes an abandoned future-dated lease in
both matching metadata hashes, grants one fresh visibility window, and only
requeues or dead-letters it on a later expired pass. The reclaim mutation
atomically rechecks queue lease identity and visibility expiry against that same
Redis clock before changing queue state; the caller clock is only a scan hint.
Business stale-Run adjudication uses the Execution PostgreSQL claim path and is
not part of Redis queue lease mutation.

Protocol-v2 leases store their owner credential in `owner_token_v2` with
`lease_protocol_version = 2` and omit the legacy `owner_token` projection. A v1
reclaimer therefore fails its existing owner check before mutation, while the
current heartbeat, acknowledgement, failure, verification, and reclaim scripts
accept both legacy and protocol-v2 leases. This provides a bounded forward
mixed-version cutover on the shared queue namespace. Rollback to a v1-only fleet
requires all protocol-v2 processing leases to be drained or explicitly recovered
by a current reclaimer first.

PostgreSQL continues to reject durable timestamp regression. Its heartbeat writer
stores the exact Redis-authoritative window only when the existing row is not
ahead, and the worker verifies the returned row before treating persistence as
successful. Schema activation fails closed when an open attempt heartbeat is more
than five seconds ahead of the PostgreSQL clock, so operators must remediate that
state before installing the monotonic guard. Stale-run recovery moves the exact
open attempt into reconciler-owned `expired` or `cancel_requested` before terminal
completion, and ordinary worker/executor terminalization mirrors the exact
terminal attempt in the same transaction. Schema `2026.09.26.1` settles retired
platform multi-agent attempts once; no permission or parent-child maintenance
writer remains. New Sandbox leases carry the
bound Attempt's `owner_generation` and a generation-specific callback token ID.
Callback receipt and model-proxy connection reads require the lease generation
to equal the current Attempt generation; previous-generation callbacks cannot
write through a newly owned Attempt. Leases created before this field was
recorded fail this check, so an upgrade must drain active legacy leases or let
their reconciliation settle before treating callback continuity as available.
Redis reclaim still lacks end-to-end expected `owner_generation` fencing and
recoverable cross-store effects. The heartbeat path has real PostgreSQL/Redis
rollback-and-convergence coverage; production Sandbox acceptance and rollback
evidence remain required before the migration can be called complete.

### 3.2 Attempt settlement detail (N30/N31)

Schema `2026.10.25.5` adds `run_attempt_settlements`, one Runs-owned detail per
existing `(tenant_id, run_id, attempt_id)`. Its composite foreign key binds the
detail to that exact `RunAttempt`; it is not a second execution Attempt. The
detail stores the legacy terminal receipt identity, canonical receipt content
and digest, accepted reconciliation context, settlement phase/claim/retry
facts, final Run result mapping, and snapshots of the Run/Attempt status,
cancellation time and owner generation used for comparison.

| Field group | `run_attempt_settlements` | Authority retained |
|---|---|---|
| Receipt identity/content/digest | `receipt_identity`, `receipt_json`, `receipt_sha256`, source lease ID and captured generation | First terminal content remains in `sandbox_leases`; later root diagnostics are excluded from the detail digest |
| Dispatch intent recovery | `intent_recovery_context_json` | The original Sandbox intent context is retained separately from acceptance |
| Dispatch acceptance/context | `dispatch_state`, `accepted_context_json` | Dispatch acceptance is observed from its durable dispatch row; accepted context remains Sandbox/Execution-owned |
| Settlement progress | `settlement_phase`, settlement claim, retry clock/count | Runs detail projection; no execution transition |
| Business result | Run result status/JSON/digest, error code and settled time | Run remains the committed business result owner |
| Comparison fence | Run/Attempt status, owner generation and cancellation-time snapshots | Read-only observations of current authority |
| Stop/release/cleanup | Not copied into the settlement detail | Sandbox lease retains resource and cleanup ownership |

The receipt identity is tied to the original Sandbox lease and Attempt. Because
the legacy terminal receipt is JSONB, its digest is over canonical JSON; the
mutable root `diagnostics` field is excluded. The source does not retain the
original callback bytes or an independent receipt timestamp, so the projection
does not claim either. The dispatch intent's recovery context is immutable and
separate from accepted semantics; `dispatch_timings` added at acceptance cannot
rewrite it. An executor probe claim without a terminal receipt remains a
Sandbox probe and does not populate the settlement claim token or phase. A
terminal receipt with `waiting_terminal` and no accepted dispatch remains
explicitly `receipt_before_acceptance` / `awaiting_context`.

This detail has no Attempt transition trigger. Writing it does not update
`run_attempts`, `runs.status`, `owner_generation`, lifecycle events, or audit
facts. It does not change `commit_provider_turn` requirements: Attempt
`running`, no cancellation, and the exact writer generation remain mandatory.
The stored status/generation/cancellation values are comparison snapshots, not
execution authority.

Resource identity, stop/release evidence, cleanup phase, cleanup retry and
cleanup claim stay Sandbox-owned on `sandbox_leases`. The shared legacy
reconciliation claim columns are projected as a settlement claim only while
the legacy phase is `claimed`; in `cleanup_pending` or `failed`, they remain
cleanup claims and are reported separately by comparison. A released lease can
therefore remain in the settlement comparison denominator while cleanup owns
its own outstanding work.

The Runs API exposes bounded keyset backfill and page-scoped read-only
comparison, with a maximum page of 800 Attempts. Missing rows, unknown
bindings and conflicts remain in each page's counts. A page result or a stable
snapshot's row count and maximum key cannot authorize a reader cutover.

Schema `2026.10.25.11` adds the persistent reader, writer and retirement markers.
Reader and writer cutovers remain separate operator actions. New and upgraded
databases initialize both markers to `sandbox_lease.v1`; schema reapplication
preserves their current versions and retirement state. `tools/attempt_settlement_storage.py`
provides `prepare`/`activate` for the reader and `prepare-writer`/`activate-writer`
for the writer. Preparation performs bounded backfill and comparison without
changing either marker. Activation requires operator confirmations for drained
writers and in-flight operations plus real SDK, Sandbox and fourteen-scenario
acceptance. The program records those declarations and reports that it did not
verify them.

Reader activation fences `runs`, `sandbox_leases`, `run_attempts`, and
`sandbox_execution_dispatches`, compares the full Attempt denominator in a
`REPEATABLE READ` transaction, and updates only the reader marker when missing,
unknown, conflict and mismatch counts are all zero and the budget is satisfied.
While a source writer transaction is active, its N40 path obtains compatible
locks on those same source tables before reading the marker and maintains the
detail in the same transaction. Writer activation has its own marker and
preflight; it fences the same lifecycle sources and changes legacy-column write
behavior only after the required drain and comparison. Neither marker is
implicitly switched by schema application.

When the marker is `attempt.v1`, the executor reconciler selects due receipts
from `run_attempt_settlements` and uses the detail's exact source lease only for
the Sandbox claim CAS. Callback recovery receives the accepted context and
receipt from the same detail mapping; Runs finalization checks its claim token
there while Sandbox still locks the exact resource identity and generation.
Admin Run lease projections and runtime health/diagnostics use this mapping as
well. Missing details raise an error. Unknown/conflict mappings remain visible in
comparison, admin diagnostics and retirement readiness, but are isolated from
the due-claim batch so one unverifiable receipt cannot block mapped Runs. The
existing `claim_sandbox_executor_suspects` probe owner also claims the narrow
unknown-receipt case when its dispatch row, immutable intent context, source
lease and Attempt generation match. A provider status proving the same
Run/Attempt/resource/generation permits the existing Sandbox lease port to
record the frozen context as acceptance and the dispatch CAS in one transaction;
provider timeout/unavailability releases the probe claim for durable retry, and
a provider-confirmed absence uses the existing
`quarantine_sandbox_executor_reconciliation` owner. No reader
falls back to Sandbox settlement columns. Source fields
remain dual-written in N40 to preserve the identified legacy
reader rollback condition while a fresh full comparison remains zero.

Resource stop/release state, cleanup status/claims, executor heartbeat, and
probe claims remain Sandbox-owned. N41's writer cutover stops compatibility
writes to the eight legacy settlement fields while retaining them for the
explicit N43 disposition. `executor_terminal_received_at`, Sandbox cleanup and
probe fields, heartbeat, and resource lifecycle facts remain in place. Rollback
to the legacy reader is compatible only while its fields are maintained and a
fresh fenced comparison confirms equality; after writer cutover, recovery needs
a validated reconstruction or forward repair.

The Redis stale-run business fence has been retired. PostgreSQL owns the stale
Run claim path through `app/execution/application/stale_run_reconciliation.py`;
queue delivery `owner_token`, ACK/requeue and capacity controls remain transport
ownership, and SSE keeps its independent stream contract. The PostgreSQL stale
Run owner is marker-aware: its pending-terminal-receipt guard reads either the
legacy Sandbox settlement columns or the `run_attempt_settlements` detail based
on the persisted reader marker, so the default `sandbox_lease.v1` and activated
`attempt.v1` modes both retain stale Run recovery. Schema readiness remains a
separate fail-closed prerequisite; an unsupported schema/layout or missing
marker cannot enter the owner. Production always enables the PostgreSQL Run
delivery-intent outbox. API startup maps historical queued Runs in bounded,
serialized pages from persisted immutable facts and fails closed when that
history cannot be proven. A delivered intent retries with its stored queue
message identity, allowing recovery of the original queued message.

N43's schema retirement is an explicit, one-way operator command. The `.11`
retirement marker and core schema readiness must be current; preflight accounts
for incomplete Attempt mapping/history and every unresolved source/resource
claim even when its TTL has expired. The command records the declared minimum
compatible application version and requires old-binary stop, Run/resource
drain, and backup/restore declarations. It removes only the inventoried legacy
settlement fields, indexes and constraint, without `CASCADE`. Schema readiness
fails closed on marker/layout mismatch, there is no automatic down migration,
and post-retirement legacy history mapping is rejected. Production stop/drain,
backup/restore, real SDK/Sandbox acceptance and the fourteen scenarios remain
operator verification items.

Queue leasing now scans candidates in oldest-first order across successive
`queue_lease_scan_limit` batches, skipping quota-blocked entries until it finds
the oldest eligible Run. This removes the fixed tail horizon that could starve
an older eligible Run while arrivals continued. A dispatch with a long blocked
prefix can read the full queued list; capacity testing must include that case.

Redis reclaim transfers an expired lease only when the same atomic operation
consumes its processing-list entry. Retained retry metadata preserves the
delivery budget; it cannot authorize a second enqueue after transfer. Concurrent
reclaimers and repeated calls must leave one queued copy and one admission
ordinal. Missing processing metadata may still recover through exact retry
metadata when the processing entry remains. This does not repair historical
duplicate entries or make an older reclaimer implementation replay-safe.

Redis reclaim must create a new durable ordinal attempt before a new worker can
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
For a first accepted terminal receipt, the private failure observation and the
immutable Sandbox receipt commit in the same PostgreSQL transaction. Duplicate
receipts keep the existing receipt and diagnostic revision. Worker terminal paths
append private diagnostics only after the current Attempt lifecycle fence and
before the same transaction projects the Run terminal state. These observations
do not authorize an Attempt transition or replace the Run result.

## 5. Ownership and dependency rules

| Concern | Canonical owner | Forbidden shortcut |
| --- | --- | --- |
| Specification value/compiler | `app.runs.domain` / `app.runs.application` | route-built canonical bytes or queue-owned digest |
| Specification persistence and attempt CAS | `app.runs.infrastructure.postgres` | new SQL in a global repository module or route |
| Queue claim and worker orchestration | `app.execution` | queue message as durable attempt truth |
| Sandbox phases and cleanup | `app.sandbox` | Runs calling a provider or marking a lease released |
| Live/terminal projection | `app.streaming` | Redis callback or stream entry as terminal authority |
| Private Run diagnostics | `app.runs.application` / `app.runs.infrastructure` | `runs.result_json` or a lease receipt as the administrator query store |
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
- real Redis forward mixed-version proof that a v1 reclaimer cannot mutate a
  protocol-v2 lease, plus future-clock normalization on retry and dead-letter
  branches;
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

### Frozen terminal receipt recovery

A persisted terminal receipt settles the original Attempt; it is not another
execution admission. Recovery retains current profile/Skill/capability
reauthorization, which may reject settlement, but never uses that authorization
to rewrite the frozen ExecutionSpec. The ready dispatch fence is still required;
an invalid candidate/fence cannot restore a spec or terminalize a different Run.

Runs restores the canonical JSON and digest from the exact Attempt, validates
its JSONB projection and identity/context/model pins, and projects that original
spec. It does not materialize Context again, compile a new spec, attach execution
MCP configuration, start an Attempt/runtime or append another `worker_started`.
The historical canonical codec remains the read owner; recovery never repairs
or re-hashes old rows.

Locks are acquired in the same transaction in Run, Sandbox receipt, Attempt
order. The scoped Run and canonical Attempt are Runs-owned; the Sandbox public
receipt authority locks and checks its exact scope, claim token, terminal receipt
and captured lease generation. Runs does not interpret Sandbox claim state.
The captured generation remains on worker authority through final result CAS.
Only the original queue-worker Attempt at that generation, or its explicit
`cancel_requested` state exactly one generation later with a Run cancellation,
is eligible. A later owner/generation is never adopted by rereading current rows.
Cancellation retains the existing terminal policy and native append-tail limits.

The result-commit service still checks the claim before writing result facts,
then rechecks Attempt authority at terminalization; a failed terminal CAS rolls
back assistant, artifacts, coverage and terminal events. Publication remains
after commit. Unknown scope, stale owner or invalid frozen authority quarantines
the receipt without manufacturing a terminal fact for the referenced Run.

Retirement disposition: the old reconciliation materialize/compile/bind branch
and unused worker lifecycle read ports are removed. New Claude reconciliation
contexts no longer write the redundant `adapter_context.run_payload`; the outer
`run_payload` remains the actual recovery consumer. Historical inner keys remain
ignored by finalization, with no data migration. Native transcripts, append/turn
receipts, old snapshot readers and legacy canonical spec fields are preserved.

Source regressions cover pure frozen restoration, invalid/stale fences, current
revocation, exact scope/claim/owner, cancellation generation, canonical drift,
transaction rollback and after-commit worker publication. Real PostgreSQL
coverage is in `test_frozen_receipt_spec_postgres.py`, alongside the existing
native transcript integrity/cancellation suite. Local skips are not database
acceptance; the schema-migrations CI shard supplies real PostgreSQL.
