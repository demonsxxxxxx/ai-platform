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

The codec accepts historical `ai-platform.execution-spec.v1` canonical bytes
and the frozen v2/v3 mappings. Grouped executions use the exact-field
`ai-platform.execution-spec.v4` mapping. All model-bearing versions freeze
`model_gateway_revision` and `model_max_output_tokens`; v3/v4 freeze
`model_auto_compact_window` while v2 keeps its original `model_max_input_tokens`.
The dispatcher compiles from an independently locked Runs-owned model snapshot,
not Redis queue fields. It writes v4 for grouped Runs and v3 for historical
ungrouped Runs, rejecting missing or mismatched durable model facts. The sandbox transport carries the paired
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
original v2 values.

v4 additionally freezes group identity/type/revision, selection mode, trusted
usage scene and policy version under the [model-group contract](model-group-management.md).
Reconciliation snapshots and their restored queue payloads retain these fields;
they do not authorize a new choice. Gateway access compares them with the Run.

Run/Attempt-bound readers accept all model-bearing versions. Provider-session
epoch, lineage and turn-commit lookups match `v2`, `v3` and `v4` while retaining
their tenant, workspace, user, Run, Attempt, owner-generation and lease checks.
Stored specifications and their schema versions are never rewritten.

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

### 3.2 Attempt is the single execution-settlement authority

`run_attempt_settlements` is the Runs-owned canonical settlement detail for the exact `(tenant_id, run_id, attempt_id)`, bound by a composite foreign key. It is not another execution Attempt. All runtime readers and writers use this detail; there is no legacy reader/writer, shadow dual-write, storage marker, cutover flag or separate retirement command.

| Fact | Owner and persistence |
|---|---|
| Terminal receipt, identity and canonical digest | Attempt detail; first content wins, conflicting duplicates fail closed; mutable root diagnostics do not change the identity digest |
| Original dispatch intent and accepted context | Separate immutable Attempt fields; acceptance timings cannot rewrite the original intent |
| Settlement claim, retry deadline/count and committed result mapping | Attempt detail; ordinary progress never advances execution `owner_generation` |
| Run outcome | Runs lifecycle same-transaction business projection; a pre-dispatch failure may still have no Attempt |
| Provider transcript/coverage and continuation | Context validates the exact writer, sequence, append receipts and workspace proof in the terminal transaction |
| Resource identity, heartbeat, probe, stop/release and cleanup | Sandbox lease; independent claims and retry clocks, never settlement authority |

Callbacks and probes record observations through the composed Sandbox port into Runs' Attempt authority on the caller's transaction. An early terminal receipt remains `receipt_before_acceptance` / `awaiting_context`; accepted context makes it eligible for settlement without replacing the receipt. A probe claim cannot become an Attempt settlement claim. Missing detail raises an error; unknown or conflicting bindings are excluded from due work and remain visible to exact probe/operator recovery and administrator diagnostics. No runtime falls back to Sandbox settlement columns.

Settlement claims lock Run, exact leases in ID order, then Attempt and detail. Due selection skips locked Runs so another replica can make progress. Retry preserves its own `next_retry_at` independently of lease updates and does not re-execute the Agent. An old claim cannot write diagnostics, quarantine a replacement owner's resource, commit a result or release a checkpoint.

For native continuation, the reconciler stops the exact verified producer and saves the host workspace before recording a durable checkpoint. Its final transaction validates the current claim, commits Context continuation, assistant and artifact facts, Run/Attempt outcome and events, records the settled claim, and finalizes that exact stopped resource. Any failed terminal CAS, claim, scope, generation or checkpoint check rolls back the entire transaction. A committed checkpoint is not claimable again; a durable partial handoff can be recovered without contacting its stopped producer. Operator repair retains the one-shot authorization, immutable image identity, verified media and workspace, SDK/receipt agreement and exact cancellation-generation checks.

Executor success still wins a late cancellation request. Context accepts the original completed writer only while the Run remains `running`, the exact Attempt is `cancel_requested`, cancellation is recorded, and its generation is exactly writer generation plus one. Takeover, later generations, incomplete mirrors, unknown append ACKs or external effects never grant normal continuation or replay authority. Context snapshots remain immutable execution inputs and stop/save checkpoints remain evidence, not alternate settlement owners.

Administrator debug exposes Attempt phase/count and only allowlisted numeric `dispatch_timings` in `0..86_400_000`; bools, private context and raw payloads are excluded. Missing accepted context cannot fall back to old timings.

### 3.3 One-time historical upgrade and compatibility disposition

Schema application is the only old-data upgrade entrance. Existing legacy settlement columns require `--confirm-settlement-upgrade` after old binaries are stopped, all Runs/Attempts/resources and unresolved claims are drained, and a recoverable backup/restore drill is verified. Expired claim TTLs do not prove external I/O stopped. The upgrader locks lifecycle sources, maps every Attempt in bounded pages, compares the complete historical denominator, and rejects missing bindings, ambiguous generations, conflicting receipts or unmapped history. Reapplication is idempotent. It preserves original receipt, diagnostics, accepted/intent context, Run/Attempt facts and resource evidence.

Only after successful mapping and comparison does that same transaction remove the eight old Sandbox settlement columns, three old indexes, old status constraint, old write-fence trigger/function and the reader/writer/retirement marker tables. It does not use `CASCADE`. New schemas contain only Attempt settlement plus Sandbox resource fields. Runtime readiness requires that layout; it cannot select an older authority. No automatic down migration exists. Recovery uses a schema-compatible corrected package or an explicitly authorized restore, not an old binary against the contracted schema. See the [release runbook](../operations/release-operations-runbook.md).

The Redis stale-Run business fence is retired. PostgreSQL binds stale recovery to the observed Run, Attempt/generation and claim, and reads pending receipts only from Attempt detail. Queue `owner_token`, ACK/requeue, capacity and SSE retain their separate transport contracts. Production requires the durable Run delivery outbox; historical queued recovery preserves its original identity and fails closed on unprovable input. Execution supervision's independent domain modes and drain claims remain; removing settlement compatibility does not change fleet scheduling ownership.

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
immutable Attempt receipt commit in the same PostgreSQL transaction. Duplicate
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

## 6. Upgrade ordering

ExecutionSpec historical readers remain governed by their versioned input
contract; they do not select settlement storage. Attempt settlement upgrade is
a stopped-writer, drained, one-time transition described in §3.3. Preserve
immutable specification bytes, original Attempt/lease identities, Run results,
Context history and authorization. Do not reinterpret migration checks as
permission to run old and new settlement binaries concurrently or replay a task.

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
Reconciliation with cancellation intent continues through the frozen binding into
result commit; the ordinary queued predispatch cancellation shortcut and its
cancel-intent fence apply only to new execution. The result transaction settles
Context before its actual terminal CAS, preserving the distinct original writer
and current cancellation generations. An exact persisted stop/save checkpoint
also completes lease finalization without reconnecting the destroyed Sandbox.
Unknown or mismatched checkpoint scope, generation or container identity cannot
supply stop proof.
A complete stopped/saved checkpoint is validated against the frozen scope,
lease generation and persisted container before restoring its handle. For this
read-only consumption only, immutable historical OpenSandbox identity labels may
outlive the currently configured executor image; the existing static label/image
binding checks still apply. This grants no spawn, probe, stop or credential
capability. Without that checkpoint, current-image live-handle authorization
remains required. Quarantined receipts are not automatically reactivated.

An explicitly authorized operator may invoke
`repair_quarantined_executor_checkpoint` for one exact scope, Run, Attempt,
lease, original owner generation and invocation ID. Eligibility is restricted to
this stopped/saved failed-receipt case, quarantined solely for
`executor_reconciliation_runtime_handle_invalid`, with a running Run whose
cancellation target remains latched and no later Attempt or other writer. The
operation checks canonical frozen authority, original SDK/checkpoint agreement,
immutable handle labels, the actual saved workspace manifest and the complete
native ACK prefix before writing a fresh repair claim. Empty saved workspaces
are valid. It records one bounded `checkpoint_repair` mark retaining the original
quarantine reason/time and the new invocation/claim authority; it never rewrites
the original terminal or checkpoint receipt.

That claim, Context/diagnostics settlement, legal cancellation terminal CAS and
lease finalization share one outer transaction. Failure rolls back the repair
mark and leaves quarantine intact. Publication follows the outer commit; no
producer, model or MCP operation runs. Replay is rejected. This is an internal
operator invocation, not an automatic scan, UI/HTTP endpoint, generic retry or
permission to repair any other historical Run.

A failed executor receipt may complete the cancelled lifecycle only when the
Run's cancellation target is already latched and the current claimed
reconciliation supplies stopped/not-found evidence with a saved workspace.
Context validates that receipt before the unchanged terminal CAS. The cancelled
Run retains the original executor failure in its result and private diagnostics;
SDK settlement error and external-effect uncertainty remain private facts. An
unknown external effect still produces `recovery_candidate` and the next native
Attempt's inspection gate, never an automatic resumable/unlocked state. Ordinary
failures without cancellation and successful-result arbitration keep their
existing policies.

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
The reconciliation-only early cancellation shortcut and cancel-intent dispatch
rejection are retired; ordinary queued dispatch keeps both checks. Owning
regressions include `test_runs_worker_dispatch_admission.py`,
`test_execution_spec.py` and `test_reconciled_cancel_commit*.py`.
The old runtime-status-only selection for a latched, stopped failed receipt is
replaced at the shared result-commit owner. Existing runtime-cancelled receipts
and ordinary predispatch/failed-result behavior remain compatible; no claim,
Attempt generation or Context verification path is retired or weakened.
Checkpoint consumption no longer requires the destroyed producer to match the
new deployment's executor image. Live-provider and new-dispatch current-image
checks retain their original owners and are not compatibility bypasses.

Source regressions cover pure frozen restoration, invalid/stale fences, current
revocation, exact scope/claim/owner, cancellation generation, canonical drift,
transaction rollback and after-commit worker publication. Real PostgreSQL
coverage is in `test_frozen_receipt_spec_postgres.py`, alongside the existing
native transcript integrity/cancellation suite. Local skips are not database
acceptance; the schema-migrations CI shard supplies real PostgreSQL.
