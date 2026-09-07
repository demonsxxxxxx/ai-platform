# System Architecture

Status: consolidated source map and technical direction. It is not deployment
evidence. Target changes below remain proposals until their detailed owner is
updated and the associated acceptance scenarios pass.

## Scope and technical direction

Keep a domain-first modular monolith with separate API, Worker and Sandbox
Executor process boundaries. Keep PostgreSQL for durable facts, Redis for
bounded queue/live coordination, and object storage for bytes. Do not introduce
a new message broker, workflow engine, universal manager, or microservice solely
to reduce the size of a source file.

[Runtime authorities](runtime-authorities.md) owns the business map;
[source architecture](source-code-architecture.md) owns package/import direction.
The detailed Profile, Skill, Context, Runs, Sandbox, SSE, data and packaging
contracts remain authoritative. This overview links rather than reproduces them.

## Source process map

| Process/resource | Observed entry or boundary | Responsibility | Current limitation to verify |
| --- | --- | --- | --- |
| API | `app/main.py`, `app/routes/chat.py` | Authenticated admission, queries and public transport | Submission orchestration remains in route code |
| Worker | `app/worker_main.py`, `app/worker.py` | Queue lease, reauthorization, execution preparation and dispatch | Single/pool supervisor paths and maintenance coupling remain |
| Sandbox controller | `app/runtime/sandbox/runtime.py` | Resource acquire, stage, validate, dispatch and cleanup | Provider calls also exist in routes/reconciler |
| Executor | `app/runtime/sandbox/executor_app.py` | One scoped Engine execution and callback delivery | Callback admission and durable acknowledgement need clear internal semantics |
| Engine | `app/executors/claude_agent_sdk_runner.py` | SDK-specific model/tool loop and event normalization | SDK types must not become public protocol authority |
| Reconciler | `app/executor_reconciler.py` | Collect asynchronous results, finalize Runs and release resources | Eligible backlog must not wait for a new notification |
| Maintenance | Worker-owned scheduling | Retry, reclaim, cleanup and retention scheduling | Exception isolation alone does not bound phase duration |
| Model/callback egress | Governed OpenSandbox proxy plus API | Scope-bound proxying without long-lived model keys in governed Executor | Test-profile exceptions are not production permissions |
| Browser | `useAgent`, SSE adapter, message projection | Submit, observe and display authorized state | Message and accepted cursor ownership is still dispersed |

These are source entrypoints, not a claim that every target package or an
independent maintenance service already exists in the deployed image.

## End-to-end lifecycle

An explicit user submission binds the authorized Session/Profile/Skill/model/file
facts and a stable submission identity. API commits admission before queue
publication; response loss is resolved through the same submission identity.
Worker restores admitted facts, reauthorizes, materializes Context, compiles an
immutable ExecutionSpec and binds the exact RunAttempt before dispatch.

Sandbox controller acquires a verified provider resource, stages authorized
material and validates the exact dispatch. An `accepted` executor response is
an asynchronous handoff, not business success. Callbacks persist observations
and receipts. Runs owns the final outcome; reconciler collects authorized
artifacts and settles through the same Runs/Sandbox owners. Cleanup may remain
pending after business terminalization.

See [ExecutionSpec and RunAttempt](execution-spec-and-attempt-lifecycle.md),
[Run lifecycle](run-lifecycle-boundary.md), and
[Sandbox Runtime](sandbox-runtime-control-layer.md) for exact state transitions.

## Trust and data boundaries

Principal attributes come from the existing identity authority, not arbitrary
browser fields. Profile revision/hash, Skill material, model connection revision
and Context selection are pinned facts. Historical context is data and cannot
restore current tools or replace system instructions.

Executor-private input, admin diagnostics, and public messages are different
projections. Governed Executor processes do not receive long-lived model
credentials. Public SSE never carries raw tool arguments/results, credentials,
provider handles, runtime paths or hidden provider reasoning. The SDK result is
an observation; it cannot mint a Run outcome or an artifact record.

Stored messages and their authorized executor-private materialization are
legitimate bounded business data. Unclassified raw SDK transcripts, arbitrary
log/prompt dumps and file bytes do not belong in Run events or public manifests.
See [data lifecycle](single-enterprise-data-lifecycle.md) and
[conversation context](agent-conversation-context.md).

## Failure and resource model

Treat received, locally queued, durably committed, published, client-applied and
painted as separate milestones. Record the milestone actually measured. A lost
response does not prove that a write failed; retry and reconciliation reuse its
identity. Queue visibility, attempt ownership, Sandbox resource state and stream
incarnation have distinct fences.

Bound resources independently: dispatch slots, active Sandboxes, model requests,
callback batches, provider calls, database connections, object I/O, reconciler
work and per-browser buffers. Increasing worker count alone is not a capacity
plan. Derive a release-specific profile from measured load and effective config.

Synchronous storage/parse work must not block API progress. External I/O must
not extend transaction/lock lifetime without an explicitly documented temporary
exception. Removing that exception requires the replacement claim, side-effect
and receipt protocol; moving a call alone does not preserve concurrency safety.
Detailed proposed changes are in [runtime convergence](runtime-convergence.md).

## Stability boundary

Retain current wire and callback versions, exact identities, terminal hydration,
redaction, permission enforcement, immutable revisions, migration safeguards and
compatibility exit conditions while restructuring. Active stream successor
creation, transport replacement, provider pooling, image splitting and relaxed
revocation are separate decisions. A code move that changes locks, retries,
bytes, authority or side effects is a behavior change.

## Acceptance and rollout

Use [system acceptance](../acceptance/system-architecture-matrix.md) to select
cross-component scenarios, plus each detailed owner's tests. Record exact source,
configuration/profile, dependency versions, fixture scale, timestamps, assertions,
resource cleanup and evidence level. Unmeasured percentile targets remain unset;
local timings are not service-level commitments.

A slice ends when one owner is active, old callers/implementations are removed
or have a bounded compatibility exit, and negative/fault tests pass. Code moved
into additional files without retiring a path does not satisfy that outcome.
Deployment and rollback remain in the existing release runbook.
