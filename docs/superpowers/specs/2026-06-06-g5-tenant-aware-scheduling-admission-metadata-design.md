# G5 Tenant-Aware Scheduling, Admission, And Queue Metadata Design

## Purpose

Close the remaining multi-tenant high-concurrency risks tracked by GitHub issue
#20 without moving the product toward Docker compose packaged delivery or
ordinary-user platform-level multi-run orchestration exposure.

This design uses the current PRD, foundation roadmap, guardrails, `AGENTS.md`,
current `main`, and issues #15/#16/#17/#20 as the source of truth. It keeps the
current company-internal backend platform direction: source authority, tenant
isolation, bounded runtime behavior, admin-only operations, and layered
verification come before broader multi-agent rollout.

## Current Gaps

1. `app.queue._lease_run_with_quota()` scans only the queue tail window. If that
   window contains only quota-blocked tenant or user candidates, a worker can
   return idle while an older runnable tenant exists outside that tail window.
2. `app.repositories.create_multi_agent_dispatch_child_run()` inserts queued
   child runs without participating in the active-run admission policy used by
   public create/copy/retry/resume paths.
3. `app.queue.remove_queued_run()` and `app.queue.get_run_queue_position()` still
   transfer the full Redis queued list with `LRANGE 0 -1`. Admin run enrichment
   then calls queue position per queued run.
4. `AGENTS.md` needs to make explicit that high-risk queue/admission/runtime
   work requires independent review, and that inherited/default review cannot
   be reported as an explicit model or reasoning gate.

## Recommended Approach

Use three bounded sub-slices under one G5 gate:

1. Queue fair bounded lease plus indexed queue metadata.
2. Multi-agent child-run admission and backpressure.
3. Project rule and roadmap sync for issue #20.

This avoids a large queue-topology migration while still closing the review
findings that block the multi-tenant high-concurrency gate.

## Alternatives Considered

### Option A: Per-tenant Redis queues

Create one queued list per tenant and make workers rotate across tenants.

This is the strongest long-term scheduler shape, but it changes the queue
topology, worker leasing model, cancel behavior, and admin projection at once.
It is too broad for the current #20 closure because it increases deployment risk
and would make rollback harder.

### Option B: Multi-window bounded scan only

Keep the current single list and only scan additional bounded windows when the
tail window is quota-blocked.

This fixes the starvation symptom, but it leaves queue position, queued-run
removal, and admin enrichment tied to full-list transfer. It does not close all
#20 acceptance criteria.

### Option C: Current list plus queue metadata indexes

Keep the current Redis queued list as the worker source, but add metadata
indexes for run lookup, order, and bounded operational projections. Use a
multi-window bounded lease path for fairness within a configured horizon.

This is the recommended option. It preserves existing queue behavior and tests
where possible, adds bounded lookup paths, and keeps the future per-tenant queue
migration available as a later architecture change.

## Queue Metadata Contract

Add derived Redis keys under the configured queue prefix:

- `queued-meta`: hash keyed by `message_id`, containing sanitized queue metadata
  such as `run_id`, `tenant_id`, `user_id`, `enqueued_at`, `sequence`, and `raw`.
- `queued-run-index`: hash keyed by `tenant_id + ":" + run_id`, containing a
  JSON list of current `message_id` values for that run. This preserves
  cancellation semantics if the same run is accidentally queued more than once
  with different payload JSON.
- `queued-order`: sorted set keyed by `message_id`, scored by a monotonic Redis
  sequence.
- `queued-sequence`: Redis integer counter used to assign stable order.

`enqueue_run()` writes the existing queued list item and all metadata in one
Redis script or atomic pipeline. The existing return value remains a one-based
position estimate so public route contracts stay compatible.

`get_run_queue_position()` no longer performs `LRANGE 0 -1`. It resolves the
run index to one or more `message_id` values, checks metadata tenant/run
matches, and uses the lowest `ZRANK queued-order message_id` to return the
existing one-based queue position.
If metadata is absent or stale, it returns `None` instead of scanning the full
list. Existing response fields keep `queue_position: int | null`.

`remove_queued_run()` no longer performs `LRANGE 0 -1`. It resolves the run
index to metadata, removes all queued raw payloads for that run, and deletes the
associated metadata. Existing unindexed queue entries may use a bounded fallback
scan only within a configured maintenance limit; normal operation uses the
index.

When a worker leases, dead-letters, acks, fails, or reclaims a queued item, the
queue metadata for that `message_id` is removed. Processing metadata remains the
source for active worker quota accounting.

## Fair Bounded Lease Contract

Quota mode keeps the current global queue list, but leasing changes from
"tail-window only" to "bounded fairness horizon".

The scheduler scans newest-first because current worker leasing already treats
the queue tail as the active end. It scans at most the configured fairness
horizon, split into bounded windows so one window of quota-blocked work cannot
make a worker idle prematurely.

For each candidate:

1. Invalid payloads are dead-lettered atomically and the scan continues.
2. `capacity_full` stops the lease attempt because global worker capacity is
   exhausted.
3. `quota_blocked` skips only that candidate and continues within the horizon.
4. `leased` returns the payload and moves the item into processing atomically.
5. `conflict` continues scanning because another worker may have changed the
   list.

If every candidate within the horizon is invalid, conflicting, or quota-blocked,
the worker returns idle with bounded evidence. The gate only claims fairness
within that configured horizon; it does not claim strict global fairness across
an unbounded queue.

## Multi-Agent Child-Run Admission Contract

Multi-agent child runs are server-created work, but they still consume tenant
and owner capacity before the dispatcher can be enabled broadly.

`create_multi_agent_dispatch_child_run()` accepts an explicit active-run
admission limit from the caller. Before inserting the child run, it applies the
same advisory-lock-backed user active-run admission policy used by public
create/copy/retry/resume paths, scoped to the parent run owner:

- `tenant_id`: parent tenant.
- `user_id`: parent owner.
- `limit`: current `max_active_runs_per_user` setting.

If admission rejects the handoff, no child run is inserted and no queue payload
is enqueued. The admin handoff/tick routes return `409`; the worker dispatcher
records a skipped result rather than enqueueing. This preserves fail-closed
behavior while the dispatcher remains disabled by default.

This design uses the existing owner quota policy rather than a new
system-owned quota. A separate system quota can be added later only if the PRD
explicitly decides that server fanout should have different capacity semantics.

## API And Projection Behavior

Public and admin response models remain backward-compatible:

- Existing `queue_position` fields remain `int | null`.
- A `null` position means the run is not queued, metadata is missing/stale, or
  the bounded lookup cannot prove the position.
- Existing `queue_insight.queue_sample` is extended with safe metadata fields
  such as indexed lookup availability and fallback scan limits.
- No projection exposes raw Redis keys, raw queue payloads, runtime private
  payload, storage keys, sandbox paths, command fingerprints, secrets, or
  executor-private data.

Admin projections may include same-tenant operational counts. Ordinary-user
projections remain scoped to the current user and do not expose other users in
the tenant.

## Error Handling

- Redis metadata write failure during enqueue fails the enqueue operation rather
  than creating an unindexed normal queued item.
- Metadata mismatch between run index and metadata returns unknown position or
  no removal, then removes stale index entries where safe.
- Queue removal never scans the full list in application code. Bounded fallback
  is only for old unindexed items and must report that it was bounded.
- Multi-agent admission rejection maps to `RepositoryConflictError` with a
  stable reason such as `user_active_run_limit_exceeded`.
- Worker dispatcher treats that conflict as a skipped dispatch, not as a child
  enqueue failure.

## Testing Plan

Use TDD for each behavior change:

1. Queue starvation regression: a quota-blocked tail tenant cannot make the
   worker idle when a runnable candidate exists inside the fairness horizon.
2. Bounded horizon regression: the worker may return idle when all candidates
   inside the configured horizon are quota-blocked, without scanning beyond it.
3. Queue metadata regression: enqueue writes metadata, queue position uses
   indexed lookup, and position does not call `LRANGE 0 -1`.
4. Queue removal regression: cancellation/removal uses indexed metadata and does
   not call `LRANGE 0 -1`; stale metadata is cleaned or treated as unknown.
5. Admin enrichment regression: queued admin runs can request queue positions
   without unbounded list transfer.
6. Multi-agent admission regression: admin handoff, admin tick, and worker
   dispatcher cannot create/enqueue child runs when the owner is at the active
   run limit.
7. Projection regression: public queue insight remains redacted; admin queue
   insight stays same-tenant and operational.

Focused tests run first for `tests/test_queue.py`,
`tests/test_run_control_routes.py`, `tests/test_admin_run_detail.py`, and
`tests/test_multi_agent_dispatcher.py`. Before PR, merge, deployment, or gate
closure, run compile, full local pytest with workspace-local `--basetemp`, git
diff checks, independent review, and 211 smoke.

## Documentation And Rule Sync

Update `AGENTS.md` to state:

- High-risk queue, admission, tenant isolation, worker, sandbox, schema,
  multi-agent runtime, and deployment work requires independent review.
- If the delegation tool exposes model and reasoning fields, set them
  deliberately for the task.
- If the tool does not expose those fields, record the review as
  inherited/default configuration and do not claim an explicit model or
  reasoning gate.
- If a user or goal explicitly requires an explicit model/reasoning gate and no
  available tool can satisfy it, the stage gate is not closed until that
  limitation is resolved or the requirement is revised.

Update the foundation roadmap only with the #20 gate state and links to the
execution plan or release evidence. Do not append long smoke logs or PR-by-PR
execution narrative to the product roadmap.

## Non-Goals

- Do not migrate to per-tenant Redis queues in this slice.
- Do not enable multi-agent dispatcher by default.
- Do not add a new public frontend entry.
- Do not change AD/company authentication.
- Do not make Docker compose one-command startup a current acceptance gate.
- Do not mount Docker socket in the default stack.
- Do not submit secrets, real `.env` values, runtime private payloads, or raw
  executor payloads.

## Acceptance Criteria

- A quota-blocked tail tenant or user cannot make a worker idle while runnable
  work exists within the configured fairness horizon.
- Multi-agent child-run creation cannot bypass the active-run quota and
  backpressure policy.
- Queue position, queued-run removal, and admin queue enrichment avoid
  application-level unbounded Redis queued-list scans under normal operation.
- Tests cover starvation, bounded horizon, fanout quota, queue metadata,
  indexed removal, and projection redaction behavior.
- `AGENTS.md` clearly distinguishes required high-risk review from explicit
  model/reasoning claims.
- Roadmap updates record the #20 gate without turning the roadmap back into a
  release evidence log.

## Spec Self-Review

- Placeholder scan: no unresolved marker or unspecified implementation slot is
  left in the design.
- Consistency check: the queue contract preserves the current list-backed worker
  source while adding metadata indexes for bounded lookup.
- Scope check: the work is split into three related sub-slices under the #20 G5
  gate and does not include per-tenant queue migration or packaged delivery.
- Ambiguity check: child-run fanout uses the parent owner active-run quota by
  default; a separate system-owned quota is explicitly deferred.
